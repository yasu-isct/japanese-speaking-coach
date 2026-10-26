"""Rebuild materials off the UI thread, then atomically select the new snapshot."""
from __future__ import annotations
from pathlib import Path
import threading
from .settings import remember_materials, remember_semantic, load_settings


class MaterialsController:
    def __init__(self, client, *, settings_path: Path | None = None, model_directory: Path | None = None,
                 model_home: Path | None = None):
        from .model_setup import default_model_root

        self.client = client
        self.settings_path = settings_path
        self._reload_lock = threading.Lock()
        self.model_home = model_home if model_home is not None else default_model_root()
        active = getattr(getattr(client.materials, "encoder", None), "directory", None)
        saved = load_settings(settings_path).get("semantic_model_directory")
        saved = Path(saved) if isinstance(saved, str) and Path(saved).is_absolute() else None
        self.model_directory = Path(model_directory or active or saved or self.model_home)

    def model_info(self) -> dict:
        from .semantic_materials import ASSETS

        store = self.client.materials
        enabled = bool(store.diagnostics.get("semantic_enabled"))
        failed = enabled and store.semantic_failed.is_set()
        present = all((self.model_directory / name).is_file() for name in ASSETS)
        message = ("已开启本地多语言查找。相关片段可能没有所问答案，请结合出处使用。" if enabled and not failed else
                   "本地查找暂不可用，当前已回到关键词；可以重新开启。" if failed else
                   "当前使用关键词查找；开启后可查找不同说法和跨语言资料。")
        return {"enabled": enabled and not failed, "directory": str(self.model_directory),
                "files_present": present, "message": message}

    def set_semantic(self, enabled: bool, directory: Path | None = None, *, progress=None, cancel=None) -> dict:
        """Run on a worker; publish the new mode only after its complete index exists."""
        from .materials import MaterialsStore
        from .model_setup import _cancelled
        from .semantic_materials import SemanticEncoder, SemanticMaterialsStore, SemanticUnavailable

        notify = progress or (lambda message: None)
        with self._reload_lock:
            _cancelled(cancel)
            current = self.client.materials
            selected = Path(directory).expanduser().absolute() if directory is not None else self.model_directory
            if enabled:
                notify("正在本机加载模型和建立资料索引，当前对话继续…")
                try:
                    active = getattr(current, "encoder", None)
                    reuse = (getattr(active, "directory", None) == selected.resolve()
                             and not current.semantic_failed.is_set()) if active is not None else False
                    encoder = active if reuse else SemanticEncoder(selected)
                    _cancelled(cancel)
                    store = SemanticMaterialsStore(current.root, encoder=encoder, limits=current.limits,
                                                   tokenizer=current._tokenizer)
                except SemanticUnavailable as exc:
                    raise ValueError(str(exc) + " 当前资料和查找方式已保留。") from None
            else:
                notify("正在切回关键词查找…")
                store = MaterialsStore(current.root, limits=current.limits, tokenizer=current._tokenizer)
            _cancelled(cancel)
            if (any(store.diagnostics.get(key) for key in ("unsafe_root", "root_read_error", "directory_read_error"))
                    or (store.diagnostics.get("missing_root") and current.chunks)):
                raise ValueError("资料目录暂时无法读取，当前资料和查找方式已保留。")
            self.client.replace_materials(store)
            self.model_directory = selected
            result = self.model_info()
            result["message"] += " 下一次建议使用新方式；当前回答保留原出处。"
            try:
                remember_semantic(enabled, selected, self.settings_path)
            except (OSError, ValueError):
                result["message"] += " 本次已生效，但无法记住设置；原设置文件未覆盖。"
            return result

    def prepare_semantic(self, *, progress=None, cancel=None) -> dict:
        from .model_setup import prepare_model

        # Download outside the reload lock; an unrelated materials refresh can finish.
        directory = prepare_model(self.model_home, progress=progress, cancel=cancel)
        return self.set_semantic(True, directory, progress=progress, cancel=cancel)

    def current(self) -> dict:
        store = self.client.materials
        diagnostics = dict(store.diagnostics)
        skipped = sum(diagnostics.get(key, 0) for key in
                      ("invalid_utf8", "file_read_error", "file_changed", "file_size_or_type_limit", "unsafe_path", "total_chars_limit"))
        limited = any(diagnostics.get(key) for key in ("index_limit_reached", "scan_limit_reached", "depth_limit_reached"))
        message = f"已读取 {diagnostics.get('indexed_files', 0)} 个文件。"
        if diagnostics.get("semantic_enabled"):
            message += (" 本地语义查找暂不可用，已回到关键词查找。" if store.semantic_failed.is_set() else
                        " 已开启本地多语言查找，支持不同说法；相关片段不一定包含答案。")
        if skipped or limited:
            message += " 部分资料未读取：请检查UTF-8编码、文件大小或目录层数。"
        if diagnostics.get("missing_root"):
            message = "资料文件夹还不存在，请选择已有文件夹。"
        elif diagnostics.get("unsafe_root") or diagnostics.get("root_read_error"):
            message = "资料文件夹无法读取，请选择实际存在且可访问的文件夹。"
        elif not store.chunks:
            message += " 放入UTF-8的md/txt后点“重新读取资料”。"
        return {"root": str(store.root), "files": diagnostics.get("indexed_files", 0),
                "chunks": len(store.chunks), "message": message}

    def reload(self, folder: str | Path | None = None) -> dict:
        """Run from a background worker. Existing suggestions retain old sources."""
        with self._reload_lock:
            selected = Path(folder).expanduser().absolute() if folder is not None else self.client.materials.root
            try:
                store = self.client.materials.rebuild(selected)
            except RuntimeError:
                raise ValueError("本地资料索引暂不可用，已保留原资料。请重试，或重启后使用关键词查找。") from None
            if any(store.diagnostics.get(key) for key in ("missing_root", "unsafe_root", "root_read_error")):
                raise ValueError("这个文件夹无法读取，已保留原资料。请选择实际存在的文件夹。")
            if store.diagnostics.get("directory_read_error") and not store.chunks:
                raise ValueError("没有权限读取这个文件夹，已保留原资料。")
            self.client.replace_materials(store)
            result = self.current()
            result["message"] += " 下一次建议会使用更新后的资料；当前回答保留原出处。"
            try:
                remember_materials(store.root, self.settings_path)
            except (OSError, ValueError):
                result["message"] += " 这次已生效，但无法记住文件夹；请检查设置目录权限。"
            return result
