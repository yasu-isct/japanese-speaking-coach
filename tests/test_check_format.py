"""Unit tests for tests/check_format.py."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_format as cf  # noqa: E402

GOOD = """## 听懂
原文：週末(しゅうまつ)何(なに)してたんですか？
意思：你周末做什么了？
要点：「してた」是「していた」的口语缩约。

## 回复建议
1. 丁寧⭐｜【友達(ともだち)と鎌倉(かまくら)】に行(い)ってきました。〇〇さんは？
   → 和朋友去了镰仓。你呢？
2. カジュアル｜【友達(ともだち)と鎌倉(かまくら)】行(い)ってきた！〇〇は？
   → 和朋友去镰仓了！你呢？
3. かんたん｜【家(いえ)でゆっくり】しました。
   → 在家好好休息了。
推荐理由：对方用了です/ます，按同等礼貌程度回答最稳妥。

## 生词
- ゆっくりする：悠闲地度过、好好休息
"""


class CheckEntryTests(unittest.TestCase):
    def test_good_output_passes(self):
        res = cf.check_entry("#x", GOOD, "週末何してたんですか？")
        self.assertEqual(res.errors, [])

    def test_preamble_detected(self):
        res = cf.check_entry("#x", "好的！\n" + GOOD)
        self.assertTrue(any("first line" in e for e in res.errors))

    def test_two_stars(self):
        res = cf.check_entry("#x", GOOD.replace("2. カジュアル｜", "2. カジュアル⭐｜"))
        self.assertTrue(any("⭐" in e for e in res.errors))

    def test_three_sentences(self):
        bad = GOOD.replace("〇〇は？\n", "〇〇は？楽しかった。\n", 1)
        res = cf.check_entry("#x", bad)
        self.assertTrue(any("3 sentences" in e for e in res.errors))

    def test_missing_reading(self):
        res = cf.check_entry("#x", GOOD.replace("鎌倉(かまくら)", "鎌倉", 1))
        self.assertTrue(any("without reading" in e for e in res.errors))

    def test_basic_kanji_need_no_reading(self):
        # 家 and 行 are N5–N4 kanji: the default furigana level leaves them bare.
        bare = GOOD.replace("家(いえ)", "家").replace("行(い)って", "行って")
        self.assertEqual(cf.check_entry("#x", bare).errors, [])

    def test_basic_kanji_list_loaded(self):
        self.assertGreater(len(cf.BASIC_KANJI), 200)
        self.assertIn("学", cf.BASIC_KANJI)
        self.assertNotIn("鎌", cf.BASIC_KANJI)

    def test_japanese_mu(self):
        bad = GOOD.replace("要点：「してた」是「していた」的口语缩约。", "要点：無")
        res = cf.check_entry("#x", bad)
        self.assertTrue(any("無" in e for e in res.errors))

    def test_not_verbatim(self):
        res = cf.check_entry("#x", GOOD, "週末は何してたんですか？")
        self.assertTrue(any("verbatim" in e for e in res.errors))

    def test_warning_inside_japanese_line(self):
        bad = GOOD.replace("〇〇は？\n", "〇〇は？⚠ 对此对象不要用\n", 1)
        res = cf.check_entry("#x", bad)
        self.assertTrue(any("inside the Japanese line" in e for e in res.errors))

    def test_concatenated_request(self):
        bad = GOOD.replace("【家(いえ)でゆっくり】しました。", "温(あたた)めてお願(ねが)いします。")
        res = cf.check_entry("#x", bad)
        self.assertTrue(any("てお願" in e for e in res.errors))

    def test_mode_b_header(self):
        b = GOOD.replace("## 听懂\n原文：週末(しゅうまつ)何(なに)してたんですか？\n意思：你周末做什么了？\n"
                         "要点：「してた」是「していた」的口语缩约。", "## 你想说\n我想说周末去了镰仓")
        res = cf.check_entry("#x", b)
        self.assertEqual(res.errors, [])


class HelperTests(unittest.TestCase):
    def test_count_sentences(self):
        self.assertEqual(cf.count_sentences("ありがとう！うれしい。"), 2)
        self.assertEqual(cf.count_sentences("はい"), 1)

    def test_spoken_length_uses_readings(self):
        self.assertEqual(cf.to_spoken("お願(ねが)いします。"), "おねがいします")


if __name__ == "__main__":
    unittest.main()
