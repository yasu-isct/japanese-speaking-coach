"""Proper nouns the learner expects to hear (company, product, technical terms, own name).

glossary.txt in the repository root is git-ignored, like profile.md; the template is
glossary.example.txt. One term per line, optionally ``term = note`` (the note goes to
the translation only, e.g. what the term is often misheard as). ``#`` starts a comment.

The terms are given to Whisper as hotwords (the decoder sees them as earlier text, so
close-sounding words lean toward the listed spelling) and to the history translation,
so a misheard term can still be translated as what was meant.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "glossary.txt"
MAX_HOTWORD_CHARS = 200  # faster-whisper keeps at most half the 448-token context for hotwords


@dataclass(frozen=True)
class Glossary:
    terms: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()  # one per term, "" when none

    def hotwords(self) -> str | None:
        """Terms joined for Whisper, trimmed to whole terms within MAX_HOTWORD_CHARS; None when empty.

        The closing 。 matters: a bare list as the "previous text" made Whisper drop
        sentence-final punctuation on about a third of the test clips (D-046).
        """
        kept, size = [], 1
        for term in self.terms:
            if size + len(term) + 1 > MAX_HOTWORD_CHARS:
                break
            kept.append(term)
            size += len(term) + 1
        return "、".join(kept) + "。" if kept else None

    def for_translation(self) -> str:
        """One line per term for the translation prompt; "" when empty."""
        return "\n".join(f"- {t}" + (f"（{n}）" if n else "") for t, n in zip(self.terms, self.notes))


def parse(text: str) -> Glossary:
    terms, notes = [], []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        term, _, note = line.partition("=")
        term, note = term.strip(), note.strip()
        if term and term not in terms:
            terms.append(term)
            notes.append(note)
    return Glossary(tuple(terms), tuple(notes))


def load(path: Path | None = DEFAULT_PATH) -> Glossary:
    """Missing or unreadable file -> empty glossary (the app works the same as without it)."""
    if path is None:
        return Glossary()
    try:
        return parse(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError):
        return Glossary()
