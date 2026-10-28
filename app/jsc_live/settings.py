"""Small user preferences outside the repository, with atomic updates."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import threading
from .materials import default_materials_root

_WRITE_LOCK = threading.Lock()


def settings_path() -> Path:
    return default_materials_root().parent / "settings.json"


def load_settings(path: Path | None = None) -> dict:
    selected = path if path is not None else settings_path()
    try:
        if selected.stat().st_size > 65536 or selected.is_symlink():
            return {}
        value = json.loads(selected.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def selected_materials(explicit: Path | None, path: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.expanduser().absolute()
    saved = load_settings(path).get("materials_directory")
    return Path(saved) if isinstance(saved, str) and Path(saved).is_absolute() else default_materials_root()


def remember_materials(folder: Path, path: Path | None = None) -> None:
    update_settings({"materials_directory": str(folder.resolve())}, path)


def selected_semantic_model(explicit: Path | None, *, keywords_only: bool = False, path: Path | None = None) -> Path | None:
    if keywords_only:
        return None
    if explicit is not None:
        return explicit.expanduser().absolute()
    saved = load_settings(path)
    directory = saved.get("semantic_model_directory")
    if saved.get("semantic_enabled") is True and isinstance(directory, str) and Path(directory).is_absolute():
        return Path(directory)
    return None


def remember_semantic(enabled: bool, directory: Path, path: Path | None = None) -> None:
    update_settings({"semantic_enabled": bool(enabled), "semantic_model_directory": str(directory.resolve())}, path)


def update_settings(changes: dict, path: Path | None = None) -> None:
    with _WRITE_LOCK:
        _update_settings(changes, path)


def _update_settings(changes: dict, path: Path | None = None) -> None:
    selected = path if path is not None else settings_path()
    if selected.exists():
        if selected.is_symlink() or selected.stat().st_size > 65536:
            raise ValueError("设置文件不可写，资料只在本次使用。")
        try:
            value = json.loads(selected.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError):
            raise ValueError("设置文件无法读取，未覆盖；资料只在本次使用。") from None
        if not isinstance(value, dict):
            raise ValueError("设置文件格式不正确，未覆盖；资料只在本次使用。")
    else:
        value = {}
    value.update(changes)
    selected.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=selected.parent, delete=False,
                                         prefix="settings-", suffix=".tmp") as output:
            temporary = Path(output.name)
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temporary, selected)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
