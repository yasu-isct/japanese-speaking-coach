"""Rebuild materials off the UI thread, then atomically select the new snapshot."""
from __future__ import annotations
from pathlib import Path
import threading
from .materials import MaterialsStore
from .settings import remember_materials


class MaterialsController:
    def __init__(self, client, *, settings_path: Path | None = None):
        self.client = client
        self.settings_path = settings_path
        self._reload_lock = threading.Lock()

    def current(self) -> dict:
        store = self.client.materials
        diagnostics = dict(store.diagnostics)
        skipped = sum(diagnostics.get(key, 0) for key in
                      ("invalid_utf8", "file_read_error", "file_changed", "file_size_or_type_limit", "unsafe_path", "total_chars_limit"))
        limited = any(diagnostics.get(key) for key in ("index_limit_reached", "scan_limit_reached", "depth_limit_reached"))
        message = f"已读取 {diagnostics.get('indexed_files', 0)} 个文件。"
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
            store = MaterialsStore(selected)
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
