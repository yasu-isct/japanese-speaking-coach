"""Explicit, non-destructive setup for the user's external reference folder."""

from __future__ import annotations

import stat
from pathlib import Path

from .materials import default_materials_root


def prepare_materials(root: Path | None = None, old_profile: Path | None = None) -> Path:
    destination = (root if root is not None else default_materials_root()).expanduser()
    if destination.exists() and not destination.is_dir():
        raise ValueError("资料路径不是文件夹，请换一个路径。")
    if destination.exists() and (destination.is_symlink() or bool(
            getattr(destination.lstat(), "st_file_attributes", 0) & 0x400)):
        raise ValueError("资料文件夹不能是链接，请指定实际文件夹路径。")
    raw = None
    if old_profile is not None:
        info = old_profile.lstat()
        if not stat.S_ISREG(info.st_mode) or old_profile.is_symlink() or bool(
                getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("旧资料必须是普通文件，请指定实际文件路径。")
        if info.st_size > 65536:
            raise ValueError("旧资料超过64KB，请先拆成较小的 .md/.txt 文档。")
        with old_profile.open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError("旧资料超过64KB，请先拆分。")
        raw.decode("utf-8-sig")
    destination.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        target = destination / "profile.md"
        try:
            with target.open("xb") as output:
                output.write(raw)
        except FileExistsError:
            raise ValueError("资料区已有 profile.md，未覆盖。请先在资料区为已有文件改名，或指定另一个资料文件夹。") from None
    return destination.resolve()
