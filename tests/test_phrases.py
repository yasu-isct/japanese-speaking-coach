"""Unit tests for app/jsc_live/phrases.py and the phrase / unclear-question prompt rules (D-049)."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from jsc_live import phrases  # noqa: E402
from jsc_live.suggest import PHRASE_RULES, UNCLEAR_QUESTION, Suggester  # noqa: E402

ENV = {"LLM_API_KEY": "test", "LLM_BASE_URL": "http://localhost:9", "LLM_MODEL": "fake"}


class PhrasesTest(unittest.TestCase):
    def test_parse_drops_comments_and_blank_lines(self):
        self.assertEqual(phrases.parse("# 注释\n\n结论\n* 結論から言うと、〜です。\n  \n"),
                         "结论\n* 結論から言うと、〜です。")

    def test_parse_cuts_at_a_whole_line_within_the_limit(self):
        text = phrases.parse("\n".join("* 練習" + "あ" * 50 for _ in range(200)))
        self.assertLessEqual(len(text), phrases.MAX_CHARS)
        self.assertTrue(all(line == "* 練習" + "あ" * 50 for line in text.splitlines()))

    def test_missing_or_unreadable_file_is_empty(self):
        self.assertEqual(phrases.load(None), "")
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(phrases.load(Path(d) / "none.txt"), "")
            bad = Path(d) / "bad.txt"
            bad.write_bytes(b"\xff\xfe\xfa")
            self.assertEqual(phrases.load(bad), "")

    def test_example_file_parses(self):
        self.assertIn("結論から言うと", phrases.load(ROOT / "phrases.example.txt"))

    def test_prompt_has_the_unclear_rule_and_phrases_only_when_given(self):
        plain = Suggester(env=ENV, profile="", polite_only=True).system_prompt
        self.assertIn(UNCLEAR_QUESTION.strip(), plain)
        self.assertNotIn(PHRASE_RULES.strip(), plain)
        prompt = Suggester(env=ENV, profile="", polite_only=True, phrases="* 結論から言うと、〜です。").system_prompt
        self.assertTrue(prompt.startswith(plain))  # the shared prefix stays cacheable
        self.assertTrue(prompt.endswith(PHRASE_RULES.strip() + "\n* 結論から言うと、〜です。"))


if __name__ == "__main__":
    unittest.main()
