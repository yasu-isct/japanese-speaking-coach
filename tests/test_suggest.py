"""Unit tests for app/jsc_live/suggest.py (no network: the HTTP stream is faked)."""

import sys
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.suggest import (  # noqa: E402
    Reply, Suggester, build_system_prompt, build_user_message, load_env, parse_line, strip_ruby,
)

GOOD = (
    "丁寧|⭐|はい、承知しました。明日までにまとめます。|好的，明白了。明天之前整理好。|\n"
    "カジュアル||うん、わかった。|嗯，知道了。|⚠\n"
    "かんたん||はい、わかりました。|好的，明白了。|\n"
    "理由|你说明对方是上司，所以用丁寧。"
)
ENV = {"LLM_API_KEY": "test", "LLM_BASE_URL": "http://localhost:9", "LLM_MODEL": "fake"}


def fake_suggester(chunks=None, error=None):
    s = Suggester(partner="上司", env=ENV)

    def stream(messages):
        s.sent = messages
        if error:
            raise error
        yield from chunks

    s._stream = stream
    return s


class PromptTest(unittest.TestCase):
    def test_system_prompt_reuses_skill_rules(self):
        prompt = build_system_prompt()
        self.assertIn("## 通用规则", prompt)
        self.assertIn("### 推荐（⭐）规则", prompt)
        self.assertIn("references/politeness.md", prompt)
        self.assertIn("实时字幕模式", prompt)
        # Modes B-D are not part of the live prompt.
        self.assertNotIn("## C. 情景陪练", prompt)
        self.assertNotIn("name: japanese-speaking-coach", prompt)

    def test_user_message_is_text_only(self):
        msg = build_user_message("明日ひま？", ["お疲れ", "今日寒いね"], "朋友")
        self.assertEqual(msg, "（对方是朋友）\n上文：\n- お疲れ\n- 今日寒いね\n对方刚说：明日ひま？")
        self.assertEqual(build_user_message("大丈夫？", [], None), "对方刚说：大丈夫？")


class ParseTest(unittest.TestCase):
    def test_reply_line(self):
        r = parse_line("カジュアル||うん、わかった。|嗯，知道了。|⚠")
        self.assertEqual(r, Reply("カジュアル", "うん、わかった。", "嗯，知道了。", star=False, warn=True))

    def test_star_and_numbering(self):
        r = parse_line("1. 丁寧⭐|⭐|はい。|好的。|")
        self.assertTrue(r.star)
        self.assertEqual(r.style, "丁寧")

    def test_reason_and_noise(self):
        self.assertEqual(parse_line("理由|对方用了です/ます。"), "对方用了です/ます。")
        self.assertIsNone(parse_line("## 回复建议"))
        self.assertIsNone(parse_line(""))

    def test_extra_empty_cell(self):
        r = parse_line("丁寧||それは大変でしたね。||那真是够呛呢。|")
        self.assertEqual((r.text, r.meaning, r.star, r.warn), ("それは大変でしたね。", "那真是够呛呢。", False, False))
        r = parse_line("カジュアル|うん、わかった。|嗯，知道了。|⚠ 对此对象不要用")
        self.assertEqual((r.text, r.meaning, r.warn), ("うん、わかった。", "嗯，知道了。", True))

    def test_missing_meaning_keeps_the_reply(self):
        r = parse_line("丁寧||それはいい経験ですね。|")
        self.assertEqual((r.text, r.meaning), ("それはいい経験ですね。", ""))
        self.assertIsNone(parse_line("丁寧|⭐||"))

    def test_reason_with_colon(self):
        self.assertEqual(parse_line("理由：对方用了「マジ」。"), "对方用了「マジ」。")
        self.assertEqual(parse_line("推荐理由: 对方用了です。"), "对方用了です。")

    def test_ruby_removed(self):
        self.assertEqual(strip_ruby("資料(しりょう)をまとめます"), "資料をまとめます")


class SuggestTest(unittest.TestCase):
    def test_streamed_lines_split_across_chunks(self):
        chunks = [GOOD[i:i + 7] for i in range(0, len(GOOD), 7)]
        updates = []
        s = fake_suggester(chunks)
        result = s.suggest("この資料、明日までにまとめてもらえますか？", [], on_update=lambda r: updates.append(len(r.replies)))
        self.assertIsNone(result.error)
        self.assertTrue(result.complete)
        self.assertEqual([r.style for r in result.replies], ["丁寧", "カジュアル", "かんたん"])
        self.assertEqual(updates[:3], [1, 2, 3])  # each reply is shown as soon as its line is done
        self.assertTrue(result.replies[1].warn)
        self.assertIn("上司", result.reason)
        self.assertIsNotNone(result.first_token_ms)
        self.assertIn("（对方是上司）", s.sent[1]["content"])

    def test_out_of_order_lines_are_sorted(self):
        lines = GOOD.splitlines()
        result = fake_suggester(["\n".join([lines[1], lines[2], lines[0], lines[3]])]).suggest("x", [])
        self.assertTrue(result.complete)
        self.assertEqual([r.style for r in result.replies], ["丁寧", "カジュアル", "かんたん"])

    def test_deepseek_thinking_off_by_default(self):
        s = Suggester(env={**ENV, "LLM_BASE_URL": "https://api.deepseek.com"})
        self.assertEqual(s.extra_body, {"thinking": {"type": "disabled"}})
        self.assertEqual(Suggester(env=ENV).extra_body, {})

    def test_missing_star_cell_and_star_from_reason(self):
        raw = ("丁寧||明日は予定があるので、また今度お願いします。|我明天有安排。|\n"
               "カジュアル|明日はちょっと無理かも。|明天可能不太行。|\n"
               "かんたん|ごめん、明日は無理。|抱歉，明天不行。|\n"
               "理由|对方用普通体邀约，用カジュアル最自然。")
        result = fake_suggester([raw]).suggest("ご飯いかない", [])
        self.assertTrue(result.complete)
        self.assertEqual(result.replies[1].text, "明日はちょっと無理かも。")
        self.assertTrue(result.replies[1].star)

    def test_malformed_reply_is_an_error(self):
        result = fake_suggester(["丁寧||はい。|好的。|\n"]).suggest("x", [])
        self.assertEqual(result.error, "incomplete or malformed reply")

    def test_network_error_is_reported_not_raised(self):
        result = fake_suggester(error=urllib.error.URLError("down")).suggest("x", [])
        self.assertIn("URLError", result.error)
        self.assertIsNotNone(result.api_ms)

    def test_missing_settings(self):
        with self.assertRaises(RuntimeError):
            Suggester(env={"LLM_API_KEY": "x"})


class TurnTest(unittest.TestCase):
    def test_turn_waits_for_pause_and_groups_sentences(self):
        from jsc_live.live_suggest import TurnBuffer

        tb = TurnBuffer(turn_gap=0.8)
        tb.add(1, "今回のテーマは恥ずかしい思い出です", 250, now=10.0)
        self.assertFalse(tb.ready(10.5))           # pause too short so far
        tb.set_speaking(True, now=0)                      # the speaker goes on
        self.assertFalse(tb.ready(11.0))
        tb.add(2, "そうですね私", 250, now=12.0)  # half a sentence...
        tb.set_speaking(True, now=0)
        tb.add(3, "恥ずかしかった思い出がたくさんあるんです", 250, now=14.0)
        tb.set_speaking(False, now=14.0)
        self.assertFalse(tb.ready(14.5))
        self.assertTrue(tb.ready(14.8))
        turn, overflow = tb.take()
        self.assertEqual([s["uid"] for s in turn], [1, 2, 3])
        self.assertEqual(overflow, [])
        self.assertFalse(tb.ready(20.0))           # nothing left

    def test_sentence_still_in_recognition_is_not_cut_off(self):
        from jsc_live.live_suggest import TurnBuffer

        tb = TurnBuffer(turn_gap=0.8)
        tb.add(1, "今回のテーマは恥ずかしかった思い出です", 250, now=7.9)
        tb.set_speaking(True, now=8.0)
        tb.set_speaking(False, now=10.0)   # next sentence closed by the segmenter...
        self.assertFalse(tb.ready(10.1))   # ...but still being transcribed: not the end of the turn
        tb.add(2, "そうですね、私", 220, now=10.3)
        tb.set_speaking(True, now=10.4)
        self.assertFalse(tb.ready(12.0))
        tb.set_speaking(False, now=15.0)
        tb.add(3, "恥ずかしかった思い出がたくさんあるんです", 250, now=15.25)
        self.assertFalse(tb.ready(15.9))
        self.assertTrue(tb.ready(16.05))
        self.assertEqual([s["uid"] for s in tb.take()[0]], [1, 2, 3])

    def test_long_turn_keeps_the_last_sentences(self):
        from jsc_live.live_suggest import TurnBuffer

        tb = TurnBuffer(turn_gap=0.8, max_sentences=3)
        for i in range(5):
            tb.add(i, f"s{i}", 0, now=float(i))
        turn, overflow = tb.take()
        self.assertEqual([s["text"] for s in turn], ["s2", "s3", "s4"])
        self.assertEqual([s["text"] for s in overflow], ["s0", "s1"])

    def test_user_message_for_a_turn(self):
        msg = build_user_message(["今日はその中で", "海外旅行の時の思い出を紹介したいと思います"], ["恥ずかしい思い出"], None)
        self.assertIn("对方刚说（连续的一段话）：\n- 今日はその中で\n- 海外旅行の時の思い出を紹介したいと思います", msg)
        self.assertTrue(msg.startswith("上文：\n- 恥ずかしい思い出"))
        self.assertEqual(build_user_message(["大丈夫？"], [], None), "对方刚说：大丈夫？")

    def test_missing_star_defaults_to_polite(self):
        raw = ("丁寧||そうなんですね。|这样啊。|\n"
               "カジュアル||そうなんだ。|这样啊。|\n"
               "かんたん||はい。|嗯。|\n"
               "理由|顺着话头回应即可。")
        result = fake_suggester([raw]).suggest("それで", [])
        self.assertTrue(result.complete)
        self.assertTrue(result.star_inferred)
        self.assertTrue(result.replies[0].star)


class EnvTest(unittest.TestCase):
    def test_load_env(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env"
            path.write_text('# comment\nLLM_MODEL="m"\nLLM_BASE_URL=https://x\n', encoding="utf-8")
            env = load_env(path)
        self.assertEqual(env["LLM_MODEL"], "m")
        self.assertEqual(env["LLM_BASE_URL"], "https://x")


if __name__ == "__main__":
    unittest.main()
