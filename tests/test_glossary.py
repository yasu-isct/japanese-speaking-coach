"""Unit tests for app/jsc_live/glossary.py and its use by the recognizer (synthetic terms only)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from jsc_live import glossary  # noqa: E402
from jsc_live.asr import Transcriber  # noqa: E402


class GlossaryTest(unittest.TestCase):
    def test_parse_skips_comments_blanks_and_duplicates(self):
        g = glossary.parse("# 注释\n\n架空AI = 常被听成「かくう」 # 行尾注释\n山田\n架空AI\n = 没有词\n")
        self.assertEqual(g.terms, ("架空AI", "山田"))
        self.assertEqual(g.notes, ("常被听成「かくう」", ""))
        self.assertEqual(g.hotwords(), "架空AI、山田。")
        self.assertEqual(g.for_translation(), "- 架空AI（常被听成「かくう」）\n- 山田")

    def test_hotwords_keep_whole_terms_within_the_limit(self):
        g = glossary.parse("\n".join(f"用語{i:03d}" for i in range(100)))
        words = g.hotwords()
        self.assertLessEqual(len(words), glossary.MAX_HOTWORD_CHARS)
        self.assertTrue(words.endswith("。"))
        self.assertTrue(all(w.startswith("用語") and len(w) == 5 for w in words[:-1].split("、")))

    def test_missing_empty_or_unreadable_file_is_an_empty_glossary(self):
        self.assertIsNone(glossary.Glossary().hotwords())
        self.assertEqual(glossary.Glossary().for_translation(), "")
        self.assertEqual(glossary.load(None).terms, ())
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(glossary.load(Path(d) / "none.txt").terms, ())
            bad = Path(d) / "bad.txt"
            bad.write_bytes(b"\xff\xfe\xfa")
            self.assertEqual(glossary.load(bad).terms, ())
            good = Path(d) / "glossary.txt"
            good.write_text("﻿架空AI\n", encoding="utf-8")
            self.assertEqual(glossary.load(good).terms, ("架空AI",))

    def test_example_file_parses(self):
        self.assertTrue(glossary.parse((ROOT / "glossary.example.txt").read_text(encoding="utf-8")).terms)

    def test_transcriber_passes_hotwords_to_partial_and_final_decodes(self):
        t = Transcriber.__new__(Transcriber)
        t.languages, t.hotwords = ("ja",), "架空AI、山田"
        t.model = Mock()
        t.model.transcribe.return_value = ([], None)
        for final in (False, True):
            t.transcribe(np.zeros(1600, np.float32), final=final)
            self.assertEqual(t.model.transcribe.call_args.kwargs["hotwords"], "架空AI、山田")


if __name__ == "__main__":
    unittest.main()
