"""Convert review (复盘) output of the japanese-speaking-coach skill into an Anki import file.

The skill's review mode ends with a fenced ```csv block whose header is
``front,reading,meaning,example``. This script collects every such block from
one or more input files (or stdin), de-duplicates the cards and writes a
tab-separated file that Anki (2.1.55+) can import directly.

Usage:
    python export_anki.py review.md -o cards.txt
    python export_anki.py review1.md review2.md --deck 日本語::会話 --layout fields
    type review.md | python export_anki.py - -o cards.txt

Only the Python standard library is used, so the script also runs inside the
Claude code-execution sandbox, which cannot install packages.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import re
import sys
from pathlib import Path

HEADER = ["front", "reading", "meaning", "example"]

# A ```csv fenced block (the closing fence may be followed by anything).
CSV_BLOCK = re.compile(r"```csv[^\n]*\n(.*?)```", re.S)

_KANJI = r"㐀-䶿一-鿿豈-﫿々〆ヵヶ"
_KANA = r"ぁ-ゖァ-ヺー"
# Base text of an inline reading: starts and ends with a kanji and may contain
# kana in between (so 一か月(いっかげつ) keeps 一か月 as one unit, while
# お弁当(べんとう) only takes 弁当). The reading is half- or full-width brackets.
RUBY = re.compile(
    rf"([{_KANJI}](?:[{_KANJI}{_KANA}]*[{_KANJI}])?)[(（]([{_KANA}]+)[)）]"
)


def strip_readings(text: str) -> str:
    """Remove inline readings: 週末(しゅうまつ) -> 週末."""
    return RUBY.sub(r"\1", text)


def to_anki_furigana(text: str) -> str:
    """Convert inline readings to Anki's furigana syntax: 週末(しゅうまつ) -> ' 週末[しゅうまつ]'.

    Anki's {{furigana:Field}} filter treats the text between the previous space
    and '[' as the base, so a space is inserted before every annotated word.
    """
    converted = RUBY.sub(r" \1[\2]", text)
    return converted.strip()


def extract_rows(text: str) -> tuple[list[dict[str, str]], list[str]]:
    """Return (rows, warnings) for every valid card row found in ``text``."""
    rows: list[dict[str, str]] = []
    warnings: list[str] = []
    for block_no, match in enumerate(CSV_BLOCK.finditer(text), start=1):
        reader = csv.reader(io.StringIO(match.group(1).strip()))
        lines = [line for line in reader if any(cell.strip() for cell in line)]
        if not lines:
            continue
        header = [cell.strip().lower() for cell in lines[0]]
        if header != HEADER:
            warnings.append(f"block {block_no}: unexpected header {lines[0]!r}, skipped")
            continue
        for line_no, cells in enumerate(lines[1:], start=2):
            if len(cells) != len(HEADER):
                warnings.append(
                    f"block {block_no} line {line_no}: expected 4 fields, got {len(cells)}: {cells!r}"
                )
                continue
            row = dict(zip(HEADER, (cell.strip() for cell in cells)))
            if not row["front"]:
                warnings.append(f"block {block_no} line {line_no}: empty front, skipped")
                continue
            rows.append(row)
    return rows, warnings


def dedupe(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep the first card for each word (compared without readings)."""
    seen: set[str] = set()
    unique = []
    for row in rows:
        key = strip_readings(row["front"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _field(text: str) -> str:
    # Fields are HTML in Anki; tabs/newlines would break the TSV layout.
    return html.escape(re.sub(r"[\t\r\n]+", " ", text), quote=False)


def build_lines(rows: list[dict[str, str]], layout: str) -> list[list[str]]:
    """Turn card rows into note fields for the chosen layout."""
    notes = []
    for row in rows:
        front = strip_readings(row["front"])
        reading = strip_readings(row["reading"])
        meaning = row["meaning"]
        example = to_anki_furigana(row["example"])
        if layout == "fields":
            notes.append([_field(front), _field(reading), _field(meaning), _field(example)])
        else:
            # Basic note type: the back shows reading, meaning and example.
            back_parts = [reading if reading != front else "", meaning, example]
            back = "<br>".join(_field(part) for part in back_parts if part)
            notes.append([_field(front), back])
    return notes


def render(notes: list[list[str]], layout: str, deck: str | None, tags: str) -> str:
    """Render the Anki import file, including its header directives."""
    out = ["#separator:tab", "#html:true"]
    if layout == "basic":
        out.append("#notetype:Basic")
        out.append("#columns:Front\tBack\tTags")
    else:
        out.append("#columns:Front\tReading\tMeaning\tExample\tTags")
    if deck:
        out.append(f"#deck:{deck}")
    out.append(f"#tags column:{len(notes[0]) + 1 if notes else 3}")
    for fields in notes:
        out.append("\t".join(fields + [tags]))
    return "\n".join(out) + "\n"


def read_inputs(paths: list[str]) -> str:
    chunks = []
    for path in paths:
        if path == "-":
            chunks.append(sys.stdin.read())
        else:
            chunks.append(Path(path).read_text(encoding="utf-8"))
    return "\n".join(chunks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("inputs", nargs="+", help="review output files (markdown/text); '-' for stdin")
    parser.add_argument("-o", "--output", help="output file (default: stdout)")
    parser.add_argument("--layout", choices=["basic", "fields"], default="basic",
                        help="basic: Front/Back for Anki's Basic note type (default); "
                             "fields: Front/Reading/Meaning/Example for a custom note type")
    parser.add_argument("--deck", help="target deck name, e.g. 日本語::会話")
    parser.add_argument("--tags", default="jsc", help="space-separated tags added to every card (default: jsc)")
    args = parser.parse_args(argv)

    rows, warnings = extract_rows(read_inputs(args.inputs))
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    rows = dedupe(rows)
    if not rows:
        print("error: no cards found (expected a ```csv block with header front,reading,meaning,example)",
              file=sys.stderr)
        return 1

    text = render(build_lines(rows, args.layout), args.layout, args.deck, args.tags)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {len(rows)} cards to {args.output}", file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
