"""Local session catalogue and review export, without audio or API startup."""
from __future__ import annotations
from dataclasses import dataclass
import json
import heapq
import os
from pathlib import Path
import re
from .session_review import export_session_review

SESSIONS_ROOT = Path(__file__).resolve().parents[2] / "logs" / "sessions"
SESSION_NAME = re.compile(r"\d{8}-\d{6}\Z")


@dataclass(frozen=True)
class SavedSession:
    path: Path
    started: str
    minutes: float | None
    sentences: int | None
    ended: bool


def _link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


class ReviewController:
    def __init__(self, sessions_root: Path | None = None):
        self.root = Path(sessions_root) if sessions_root is not None else SESSIONS_ROOT

    def list_sessions(self) -> list[SavedSession]:
        """Read only small metadata files, not conversations or audio."""
        if not self.root.exists():
            return []
        if not self.root.is_dir() or _link(self.root):
            raise ValueError("存档目录不是可读取的实际文件夹。")
        with os.scandir(self.root) as entries:
            folders = heapq.nlargest(200, (Path(entry.path) for entry in entries
                                    if SESSION_NAME.fullmatch(entry.name) and entry.is_dir(follow_symlinks=False)),
                                    key=lambda path: path.name)
        sessions = []
        for folder in folders:
            if _link(folder):
                continue
            meta = {}
            path = folder / "meta.json"
            try:
                if path.is_file() and not _link(path) and path.stat().st_size <= 65536:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    meta = value if isinstance(value, dict) else {}
            except (OSError, ValueError):
                pass  # Incomplete metadata must not hide an otherwise usable archive.
            minutes, sentences = meta.get("minutes"), meta.get("sentences")
            minutes = float(minutes) if type(minutes) in (int, float) and 0 <= minutes < 100000 else None
            sentences = sentences if type(sentences) is int and sentences >= 0 else None
            started = meta.get("started")
            if not isinstance(started, str) or len(started) > 40:
                started = folder.name
            sessions.append(SavedSession(folder, started, minutes, sentences, bool(meta.get("ended"))))
        return sessions

    def export(self, session: Path, layout: str = "basic"):
        return export_session_review(session, anki_layout=layout)
