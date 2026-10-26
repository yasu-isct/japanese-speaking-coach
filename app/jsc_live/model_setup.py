"""Explicit user-triggered preparation of the pinned, public retrieval model."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
import urllib.request

from .materials import default_materials_root
from .semantic_materials import ASSETS, MODEL_REPO, MODEL_REVISION


class PreparationCancelled(RuntimeError):
    pass


def default_model_root() -> Path:
    return default_materials_root().parent / "models" / ("multilingual-minilm-" + MODEL_REVISION[:8])


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise PreparationCancelled("已取消准备，当前查找方式保持不变。")


def _actual_directory(path):
    # Only write real directories inside the explicitly selected managed cache.
    for part in (path, *path.parents):
        if part.exists() and (part.is_symlink() or getattr(part.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("模型缓存路径包含链接，请改用“选择已有模型”。")


def _matches(path, size, expected, cancel):
    if not path.is_file() or path.stat().st_size != size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            _cancelled(cancel)
            digest.update(block)
    return digest.hexdigest() == expected


def prepare_model(directory: Path | None = None, *, progress=None, cancel: threading.Event | None = None,
                  opener=None) -> Path:
    """Cache verified assets atomically; no material text is read or transmitted."""
    selected = Path(directory) if directory is not None else default_model_root()
    _actual_directory(selected)
    _cancelled(cancel)
    selected.mkdir(parents=True, exist_ok=True)
    total = sum(size for size, _ in ASSETS.values())
    finished = 0
    notify = progress or (lambda message: None)
    open_url = opener or urllib.request.urlopen
    for name, (size, expected) in ASSETS.items():
        _cancelled(cancel)
        target = selected / name
        _actual_directory(target.parent)
        if target.exists() and (target.is_symlink() or getattr(target.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("模型文件是链接，未覆盖；请选择已有模型文件夹。")
        notify("正在检查已有模型文件…")
        if _matches(target, size, expected, cancel):
            finished += size
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            request = urllib.request.Request(
                f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{name}",
                headers={"User-Agent": "JapaneseSpeakingCoach/local-model-setup", "Accept-Encoding": "identity"})
            notify(f"正在连接官方下载源（约{total / 1e6:.0f}MB）…")
            with open_url(request, timeout=15) as response:
                length = response.headers.get("Content-Length")
                if length is not None and int(length) != size:
                    raise ValueError("下载文件大小与固定版本不符，未替换已有文件。")
                digest = hashlib.sha256()
                received, last_update = 0, 0.0
                with tempfile.NamedTemporaryFile("wb", dir=target.parent, prefix="model-", suffix=".part", delete=False) as output:
                    temporary = Path(output.name)
                    while True:
                        _cancelled(cancel)
                        block = response.read(64 * 1024)
                        if not block:
                            break
                        received += len(block)
                        if received > size:
                            raise ValueError("下载超过预期大小，已停止；已有模型未改动。")
                        output.write(block)
                        digest.update(block)
                        now = time.monotonic()
                        if now - last_update >= .25:
                            notify(f"正在下载：{(finished + received) / 1e6:.1f} / {total / 1e6:.1f} MB")
                            last_update = now
            _cancelled(cancel)
            if received != size or digest.hexdigest() != expected:
                raise ValueError("下载不完整或校验未通过，请重试。已有模型未替换。")
            os.replace(temporary, target)
            temporary = None
            finished += size
        except (OSError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("下载"):
                raise
            raise ValueError("模型下载失败，请检查网络和缓存目录空间后重试；当前查找方式未改动。") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    notify("模型文件已准备并校验，正在开启…")
    return selected
