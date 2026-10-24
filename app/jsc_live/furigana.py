"""Furigana (ruby) generation with morphological analysis.

MeCab (via fugashi) + UniDic splits the sentence into words and gives each word
its reading in context, e.g. 今日 -> キョウ, 一人 -> ヒトリ. This is what makes
the readings reliable: a per-character dictionary lookup cannot know that 今日
is not こん+にち.

Each word's reading is then aligned to its surface so that only the kanji part
gets ruby (温め -> 温[あたた]め, not 温め[あたため]).

Graded mode reuses skill/references/basic_kanji.md: words written only with
N5-N4 kanji get no ruby, unless they are listed as special readings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

KANJI_RE = re.compile(r"[㐀-鿿豈-﫿々〆ヵヶ]")
BASIC_KANJI_PATH = Path(__file__).resolve().parents[2] / "skill" / "references" / "basic_kanji.md"


@dataclass
class Segment:
    text: str
    ruby: str | None = None  # hiragana reading, or None when no ruby is shown


def kata_to_hira(text: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def has_kanji(text: str) -> bool:
    return bool(KANJI_RE.search(text))


def align(surface: str, reading: str) -> list[Segment]:
    """Split a word into kanji runs (with ruby) and kana runs (without).

    The kana in the surface act as anchors: 取り扱い / とりあつかい ->
    取[と] り 扱[あつか] い. Falls back to ruby over the whole word.
    """
    reading = kata_to_hira(reading)
    runs = re.findall(r"[㐀-鿿豈-﫿々〆ヵヶ]+|[^㐀-鿿豈-﫿々〆ヵヶ]+", surface)
    pattern = "".join("(.+?)" if has_kanji(r) else f"({re.escape(kata_to_hira(r))})" for r in runs)
    m = re.fullmatch(pattern, reading)
    if not m:
        return [Segment(surface, reading if has_kanji(surface) else None)]
    return [Segment(run, m.group(i + 1) if has_kanji(run) else None) for i, run in enumerate(runs)]


@lru_cache(maxsize=1)
def load_basic_kanji(path: Path = BASIC_KANJI_PATH) -> tuple[frozenset[str], frozenset[str]]:
    """(N5-N4 kanji, words that always need ruby) from basic_kanji.md."""
    if not path.exists():
        return frozenset(), frozenset()
    text = path.read_text(encoding="utf-8")
    kanji: set[str] = set()
    for level in ("N5", "N4"):
        m = re.search(rf"^## {level}\n(.+)$", text, re.M)
        if m:
            kanji.update(KANJI_RE.findall(m.group(1)))
    exceptions: set[str] = set()
    m = re.search(r"^## 例外.*?\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if m:
        exceptions.update(re.findall(r"([^\s、，,：:()（）]+?)\(", m.group(1)))
    return frozenset(kanji), frozenset(exceptions)


class Furigana:
    """Annotate Japanese text. ``level``: "graded" (default), "all" or "none"."""

    def __init__(self, level: str = "graded") -> None:
        import fugashi

        self.tagger = fugashi.Tagger()
        self.level = level
        self.basic, self.exceptions = load_basic_kanji()

    def needs_ruby(self, surface: str) -> bool:
        if self.level == "none" or not has_kanji(surface):
            return False
        if self.level == "all":
            return True
        if surface in self.exceptions:
            return True
        return any(k not in self.basic for k in KANJI_RE.findall(surface))

    @staticmethod
    def _reading(word) -> str | None:
        # UniDic: "kana" is the reading as written (コンビニ), "pron" the pronunciation (コーヒー).
        for field in ("kana", "pron"):
            value = getattr(word.feature, field, None)
            if value and value != "*":
                return value
        return None

    def annotate(self, text: str) -> list[Segment]:
        segments: list[Segment] = []
        for word in self.tagger(text):
            # MeCab moves spaces before a word into white_space; keep them.
            if getattr(word, "white_space", ""):
                segments.append(Segment(word.white_space))
            surface = word.surface
            reading = self._reading(word)
            if reading and self.needs_ruby(surface):
                segments.extend(align(surface, reading))
            else:
                segments.append(Segment(surface))
        return merge_plain(segments)

    def to_bracket(self, text: str) -> str:
        """Plain-text form: 温[あたた]め, for the console and tests."""
        return "".join(f"{s.text}[{s.ruby}]" if s.ruby else s.text for s in self.annotate(text))


def merge_plain(segments: list[Segment]) -> list[Segment]:
    """Join neighbouring segments without ruby (fewer draw calls, simpler wrapping)."""
    merged: list[Segment] = []
    for seg in segments:
        if merged and seg.ruby is None and merged[-1].ruby is None:
            merged[-1] = Segment(merged[-1].text + seg.text)
        else:
            merged.append(seg)
    return merged
