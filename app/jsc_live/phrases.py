"""The user's practice phrases, which generated answers should reuse when they fit (D-049).

phrases.txt in the repository root is git-ignored, like glossary.txt; the template is
phrases.example.txt. The text goes into the fixed system prompt, so it is sent once per
run and stays inside the provider's prefix cache.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "phrases.txt"
MAX_CHARS = 3000  # keeps the fixed prompt small; a longer list is cut at a whole line


def parse(text: str) -> str:
    """Drop comments and blank lines; keep the user's own grouping and order."""
    lines, total = [], 0
    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if total + len(line) + 1 > MAX_CHARS:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def load(path: Path | None = DEFAULT_PATH) -> str:
    """Return the phrase list, or "" when the file is missing or unreadable."""
    if path is None:
        return ""
    try:
        return parse(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError):
        return ""
