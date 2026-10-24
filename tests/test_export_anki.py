"""Unit tests for skill/scripts/export_anki.py (stdlib unittest, no extra deps)."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skill" / "scripts" / "export_anki.py"
FIXTURE = ROOT / "tests" / "fixtures" / "review_samples.md"
sys.path.insert(0, str(SCRIPT.parent))

import export_anki as ea  # noqa: E402


class FuriganaTests(unittest.TestCase):
    def test_strip_simple(self):
        self.assertEqual(ea.strip_readings("週末(しゅうまつ)何(なに)してた"), "週末何してた")

    def test_strip_keeps_prefix_kana_outside_base(self):
        self.assertEqual(ea.strip_readings("お弁当(べんとう)"), "お弁当")

    def test_strip_word_level_reading_with_inner_kana(self):
        self.assertEqual(ea.strip_readings("一か月(いっかげつ)"), "一か月")

    def test_kana_only_parentheses_untouched(self):
        # Not a reading: no kanji base before the bracket.
        self.assertEqual(ea.strip_readings("〜くらい(ぐらい)"), "〜くらい(ぐらい)")

    def test_anki_furigana(self):
        self.assertEqual(ea.to_anki_furigana("お弁当(べんとう)を温(あたた)めて"),
                         "お 弁当[べんとう]を 温[あたた]めて")

    def test_anki_furigana_word_level(self):
        self.assertEqual(ea.to_anki_furigana("一か月(いっかげつ)です"), "一か月[いっかげつ]です")


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.text = FIXTURE.read_text(encoding="utf-8")

    def test_reads_all_blocks(self):
        rows, warnings = ea.extract_rows(self.text)
        self.assertEqual(warnings, [])
        self.assertEqual(len(rows), 10 + 9 + 8)

    def test_bad_header_is_skipped_with_warning(self):
        rows, warnings = ea.extract_rows("```csv\nword,meaning\n犬,狗\n```")
        self.assertEqual(rows, [])
        self.assertEqual(len(warnings), 1)

    def test_wrong_field_count_is_reported(self):
        text = "```csv\nfront,reading,meaning,example\n犬,いぬ,狗\n猫,ねこ,猫,猫がいる。\n```"
        rows, warnings = ea.extract_rows(text)
        self.assertEqual([r["front"] for r in rows], ["猫"])
        self.assertEqual(len(warnings), 1)

    def test_quoted_comma(self):
        text = '```csv\nfront,reading,meaning,example\n犬,いぬ,"狗, 犬",犬がいる。\n```'
        rows, _ = ea.extract_rows(text)
        self.assertEqual(rows[0]["meaning"], "狗, 犬")

    def test_dedupe_ignores_readings(self):
        rows = [
            {"front": "喉", "reading": "のど", "meaning": "a", "example": ""},
            {"front": "喉(のど)", "reading": "のど", "meaning": "b", "example": ""},
        ]
        self.assertEqual(len(ea.dedupe(rows)), 1)


class RenderTests(unittest.TestCase):
    def test_basic_layout(self):
        rows = [{"front": "接客", "reading": "せっきゃく", "meaning": "服务顾客",
                 "example": "接客(せっきゃく)の経験(けいけん)はないです。"}]
        text = ea.render(ea.build_lines(rows, "basic"), "basic", "日本語", "jsc")
        lines = text.splitlines()
        self.assertIn("#separator:tab", lines)
        self.assertIn("#notetype:Basic", lines)
        self.assertIn("#deck:日本語", lines)
        front, back, tags = lines[-1].split("\t")
        self.assertEqual(front, "接客")
        self.assertEqual(back, "せっきゃく<br>服务顾客<br>接客[せっきゃく]の 経験[けいけん]はないです。")
        self.assertEqual(tags, "jsc")

    def test_kana_word_does_not_repeat_reading(self):
        rows = [{"front": "シフト", "reading": "シフト", "meaning": "排班", "example": ""}]
        _, back = ea.build_lines(rows, "basic")[0]
        self.assertEqual(back, "排班")

    def test_fields_layout_and_html_escape(self):
        rows = [{"front": "A<B", "reading": "x", "meaning": "y", "example": "z"}]
        fields = ea.build_lines(rows, "fields")[0]
        self.assertEqual(fields, ["A&lt;B", "x", "y", "z"])


class CliTests(unittest.TestCase):
    def test_cli_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cards.txt"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(FIXTURE), "-o", str(out)],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            body = [l for l in out.read_text(encoding="utf-8").splitlines() if not l.startswith("#")]
            self.assertEqual(len(body), 27)

    def test_cli_fails_without_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.md"
            empty.write_text("no cards here", encoding="utf-8")
            result = subprocess.run([sys.executable, str(SCRIPT), str(empty)],
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
