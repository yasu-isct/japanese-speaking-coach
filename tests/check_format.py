"""Automatic format checker for the skill's reply-suggestion output (modes A and B).

It implements the mechanical part of the checklist in tests/sample_dialogues.md
so a regression run can be scored without reading every answer by hand.
Japanese correctness is NOT checked here; that is the job of the review step
(tests/review_prompt.md).

Usage:
    python tests/check_format.py tests/results_2026-10-24.md --section "第 4 轮"
    python tests/check_format.py some_output.md            # every "### #N" entry in the file

Each entry in the results file looks like:

    ### #7 ...
    ```
    ## 听懂
    ...
    ```
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
from export_anki import RUBY, strip_readings  # noqa: E402  (shared furigana regex)

STYLES = ["丁寧", "カジュアル", "かんたん"]
REPLY_LINE = re.compile(r"^(\d)\.\s*(丁寧|カジュアル|かんたん)(⭐?)｜(.+)$")
KANJI = re.compile(r"[㐀-䶿一-鿿々]")
SENTENCE_END = re.compile(r"[。！？!?]+")
WARN = "⚠ 对此对象不要用"


@dataclass
class Result:
    entry: str
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def count_sentences(text: str) -> int:
    """Count sentences by terminal punctuation; trailing text without one counts too."""
    parts = [p for p in SENTENCE_END.split(text.strip()) if p.strip()]
    return len(parts)


def to_spoken(text: str) -> str:
    """Replace annotated words with their readings and drop punctuation/brackets."""
    return re.sub(r"[\s、。！？!?【】「」…〜]", "", RUBY.sub(r"\2", text))


def load_basic_kanji() -> set[str]:
    """N5–N4 kanji from skill/references/basic_kanji.md (the default furigana level skips these)."""
    path = ROOT / "skill" / "references" / "basic_kanji.md"
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8")
    chars: set[str] = set()
    for level in ("N5", "N4"):
        m = re.search(rf"^## {level}\n(.+)$", text, re.M)
        if m:
            chars.update(KANJI.findall(m.group(1)))
    return chars


BASIC_KANJI = load_basic_kanji()


def missing_readings(japanese: str, basic: set[str] = BASIC_KANJI) -> list[str]:
    """Non-basic kanji left over after removing every annotated word (heuristic).

    Under the default furigana level, words made only of N5–N4 kanji need no
    reading, so only kanji outside that set are reported. Special readings
    (今日, 一人 ...) cannot be detected this way and are left to review.
    """
    leftover = RUBY.sub("", japanese)
    leftover = re.sub(r"[〇]", "", leftover)  # 〇〇さん placeholder
    return [k for k in KANJI.findall(leftover) if k not in basic]


def check_entry(name: str, body: str, source: str | None = None) -> Result:
    res = Result(name)
    err = res.errors.append
    lines = [l.rstrip() for l in body.strip().splitlines()]
    if not lines:
        err("empty output")
        return res

    first = lines[0].strip()
    mode_b = first == "## 你想说"
    if first not in ("## 听懂", "## 你想说"):
        err(f"first line is {first!r}, expected '## 听懂' (no preamble)")

    headers = [l.strip() for l in lines if l.startswith("## ")]
    expected = ["## 你想说" if mode_b else "## 听懂", "## 回复建议", "## 生词"]
    if headers != expected:
        err(f"headers {headers} != {expected}")

    def section(title: str) -> list[str]:
        try:
            start = next(i for i, l in enumerate(lines) if l.strip() == title)
        except StopIteration:
            return []
        out = []
        for l in lines[start + 1:]:
            if l.startswith("## "):
                break
            if l.strip():
                out.append(l)
        return out

    # --- 听懂 block -------------------------------------------------------
    if not mode_b:
        listen = section("## 听懂")
        keys = [l.split("：", 1)[0] for l in listen]
        if keys != ["原文", "意思", "要点"]:
            err(f"听懂 lines {keys} != ['原文', '意思', '要点']")
        for l in listen:
            if l.startswith("要点：") and "無" in l.split("：", 1)[1][:2]:
                err("要点 uses Japanese 無 instead of 无")
            if l.startswith("原文："):
                original = l.split("：", 1)[1]
                if missing := missing_readings(original):
                    err(f"原文 kanji without reading: {''.join(missing)}")
                if source is not None:
                    plain = strip_readings(original).strip()
                    if plain != source.strip():
                        err(f"原文 not verbatim: {plain!r} vs input {source!r}")

    # --- replies ------------------------------------------------------------
    replies = section("## 回复建议")
    parsed = []
    for i, l in enumerate(replies):
        m = REPLY_LINE.match(l.strip())
        if m:
            arrow = replies[i + 1].strip() if i + 1 < len(replies) else ""
            parsed.append((m.group(1), m.group(2), bool(m.group(3)), m.group(4), arrow))
    if [(p[0], p[1]) for p in parsed] != [("1", "丁寧"), ("2", "カジュアル"), ("3", "かんたん")]:
        err(f"replies are {[(p[0], p[1]) for p in parsed]}, expected 丁寧/カジュアル/かんたん in order")
    stars = [p for p in parsed if p[2]]
    if len(stars) != 1:
        err(f"{len(stars)} ⭐ found, expected exactly 1")

    texts = {p[1]: p[3] for p in parsed}
    for num, style, starred, text, arrow in parsed:
        if not arrow.startswith("→"):
            err(f"{style}: missing '→ 中文意思' line")
        if WARN in text or "⚠" in text:
            err(f"{style}: ⚠ must not be inside the Japanese line")
        if style != "カジュアル" and "⚠" in arrow:
            err(f"{style}: ⚠ only belongs to カジュアル")
        if style == "カジュアル" and "⚠" in arrow and starred:
            err("カジュアル has ⚠ but is also ⭐")
        n = count_sentences(strip_readings(text))
        # Mode B self-introductions may give a longer polite version (SKILL.md B).
        limit = 5 if (mode_b and style == "丁寧") else 2
        if n > limit:
            err(f"{style}: {n} sentences (max {limit})")
        if missing := missing_readings(text):
            err(f"{style}: kanji without reading: {''.join(missing)}")
        if "てお願" in strip_readings(text):
            err(f"{style}: 〜てお願いします pattern")
    if "丁寧" in texts and "かんたん" in texts:
        if strip_readings(texts["かんたん"]) == strip_readings(texts["丁寧"]):
            err("かんたん is identical to 丁寧")
        # Compare spoken length (readings instead of kanji), not character count.
        elif len(to_spoken(texts["かんたん"])) > len(to_spoken(texts["丁寧"])):
            err("かんたん is longer than 丁寧")

    reason = [l for l in replies if l.startswith("推荐理由：")]
    if len(reason) != 1:
        err("missing 推荐理由 line")
    elif re.search(r"第\s*\d\s*步|§", reason[0]):
        err("推荐理由 leaks internal step numbers")

    # --- 生词 ---------------------------------------------------------------
    vocab = [l for l in section("## 生词") if l.lstrip().startswith("-")]
    if not vocab:
        err("生词 section empty (write '- 无')")
    elif len(vocab) > 3:
        err(f"{len(vocab)} vocabulary items (max 3)")
    if any(l.strip() in ("- 無", "-無") for l in vocab):
        err("生词 uses Japanese 無 instead of 无")
    return res


ENTRY = re.compile(r"^### #(\d+)[^\n]*\n```[^\n]*\n(.*?)```", re.S | re.M)


def load_inputs(path: Path) -> dict[str, str]:
    """Map test number -> input sentence from the tables in sample_dialogues.md."""
    inputs = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|", line)
        if m:
            sentence = re.sub(r"^（[^）]*）", "", m.group(2))  # drop scene note
            inputs[m.group(1)] = sentence
    return inputs


def iter_entries(text: str, section: str | None):
    if section:
        start = text.find(section)
        if start < 0:
            raise SystemExit(f"section {section!r} not found")
        nxt = text.find("\n# ", start + 1)
        text = text[start: nxt if nxt > 0 else None]
    for m in ENTRY.finditer(text):
        yield m.group(1), m.group(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check skill output format.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--section", help="only check entries after this heading text (until the next '# ' heading)")
    parser.add_argument("--inputs", type=Path, default=ROOT / "tests" / "sample_dialogues.md",
                        help="test sentence table used for the verbatim 原文 check")
    args = parser.parse_args(argv)

    inputs = load_inputs(args.inputs) if args.inputs.exists() else {}
    results = [check_entry(f"#{num}", body, inputs.get(num))
               for num, body in iter_entries(args.file.read_text(encoding="utf-8"), args.section)]
    if not results:
        print("no entries found", file=sys.stderr)
        return 2
    sys.stdout.reconfigure(encoding="utf-8")
    for r in results:
        print(f"{'PASS' if r.ok else 'FAIL'} {r.entry}" + "".join(f"\n    - {e}" for e in r.errors))
    passed = sum(r.ok for r in results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
