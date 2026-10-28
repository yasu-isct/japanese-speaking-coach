"""Unit tests for app/jsc_live/suggest.py (no network: the HTTP stream is faked)."""

import sys
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.suggest import (  # noqa: E402
    Recommendation, Reply, Suggester, build_system_prompt, build_user_message, clean_profile, load_env, load_profile,
    parse_line, strip_ruby,
)

GOOD = (
    "丁寧|⭐|はい、承知しました。明日までにまとめます。|好的，明白了。明天之前整理好。|\n"
    "カジュアル||うん、わかった。|嗯，知道了。|⚠\n"
    "かんたん||はい、わかりました。|好的，明白了。|\n"
    "理由|你说明对方是上司，所以用丁寧。"
)
UNSTARRED = (
    "丁寧||そうなんですね。|这样啊。|\n"
    "カジュアル||そうなんだ。|这样啊。|\n"
    "かんたん||はい。|嗯。|\n"
    "理由|顺着话头回应即可。"
)
ENV = {"LLM_API_KEY": "test", "LLM_BASE_URL": "http://localhost:9", "LLM_MODEL": "fake"}


def fake_suggester(chunks=None, error=None):
    s = Suggester(partner="上司", env=ENV, profile="")

    def stream(messages):
        s.sent = messages
        if error:
            raise error
        yield from chunks

    s._stream = stream
    return s


class PromptTest(unittest.TestCase):
    def test_materials_mode_overrides_only_the_short_style_contract(self):
        from jsc_live.materials import MaterialsStore
        from jsc_live.suggest import POLITENESS, _section

        prompt = Suggester(env=ENV, materials=MaterialsStore()).system_prompt
        rules = POLITENESS.read_text(encoding="utf-8")
        self.assertNotIn("15 个假名", prompt)
        # Object priority, warning conditions and reason rules remain verbatim.
        self.assertIn(_section(rules, "## 1.", "### かんたん"), prompt)
        self.assertIn(_section(rules, "## 5.", None), prompt)
        self.assertIn("15 个假名", Suggester(env=ENV, profile="").system_prompt)

    def test_system_prompt_reuses_skill_rules(self):
        prompt = build_system_prompt()
        self.assertIn("## 通用规则", prompt)
        self.assertIn("### 推荐（⭐）规则", prompt)
        self.assertIn("references/politeness.md", prompt)
        self.assertIn("实时字幕模式", prompt)
        # Modes B-D are not part of the live prompt.
        self.assertNotIn("## C. 情景陪练", prompt)
        self.assertNotIn("name: japanese-speaking-coach", prompt)
        self.assertNotIn("【友達(ともだち)と鎌倉(かまくら)】", prompt)
        self.assertNotIn("能搜索的过往对话", prompt)
        self.assertNotIn("若对方的话需要学生做选择", prompt)

    def test_user_message_is_text_only(self):
        msg = build_user_message("明日ひま？", ["お疲れ", "今日寒いね"], "朋友")
        self.assertEqual(msg, "（对方是朋友）\n上文：\n- お疲れ\n- 今日寒いね\n对方刚说：明日ひま？")
        self.assertEqual(build_user_message("大丈夫？", [], None), "对方刚说：大丈夫？")


class ProfileTest(unittest.TestCase):
    def test_profile_is_appended_last_without_hint_lines(self):
        import tempfile

        from jsc_live.suggest import load_profile

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "profile.md"
            path.write_text("# 我的个人资料\n> 说明行会被忽略\n- 来日本：一年半\n", encoding="utf-8")
            profile = load_profile(path)
            self.assertEqual(load_profile(Path(d) / "missing.md"), "")
        self.assertEqual(profile, "# 我的个人资料\n- 来日本：一年半")
        prompt = build_system_prompt(profile)
        self.assertTrue(prompt.endswith("- 来日本：一年半"))
        self.assertIn("不加【】", prompt)
        self.assertNotIn("## 学生提供的资料", build_system_prompt(""))

    def test_suggester_takes_an_explicit_profile(self):
        self.assertIn("一年半", Suggester(env=ENV, profile="- 来日本：一年半").system_prompt)
        self.assertNotIn("## 学生提供的资料", Suggester(env=ENV, profile="").system_prompt)

    def test_unfilled_public_template_is_not_a_persons_background(self):
        self.assertEqual(load_profile(ROOT / "profile.example.md"), "")
        example = "# Profile\n- 出身：（例）上海\n- 名字：(例) 李明\n- 爱好：未填写\n"
        client = Suggester(env=ENV, profile=example)
        self.assertEqual(client.profile, "")
        self.assertEqual(client.system_prompt, build_system_prompt())

    def test_mixed_profile_keeps_known_facts_without_interpreting_unknowns(self):
        raw = ("# Test profile\n> guidance\n- 出身：南京\n- 年龄：\n- 工作：未填写\n"
               "- 爱好：不知道（资料没有填写）\n- 学校：【学校名】\n- 来日本：一年半\n"
               "- 家人：弟は医者です。\n")
        cleaned = clean_profile(raw)
        self.assertIn("- 出身：南京", cleaned)
        self.assertIn("- 来日本：一年半", cleaned)
        self.assertIn("- 家人：弟は医者です。", cleaned)  # preserve ownership instead of promoting it to learner facts
        self.assertNotIn("不知道", cleaned)
        self.assertNotIn("未填写", cleaned)
        self.assertNotIn("【学校名】", cleaned)
        self.assertEqual(clean_profile(cleaned), cleaned)

    def test_profile_cleaning_does_not_rewrite_the_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fictional-profile.md"
            raw = "# Test\n- 名字：（例）例子\n- 出身：南京\n"
            path.write_text(raw, encoding="utf-8")
            self.assertEqual(load_profile(path), "# Test\n- 出身：南京")
            self.assertEqual(path.read_text(encoding="utf-8"), raw)


class ParseTest(unittest.TestCase):
    def test_recommendation_accepts_one_style_and_supported_separators(self):
        for style in ("丁寧", "カジュアル", "かんたん"):
            for separator in ("|", "｜", ":", "："):
                with self.subTest(style=style, separator=separator):
                    self.assertEqual(parse_line(f"  推荐 {separator} {style}  "), Recommendation(style))

    def test_recommendation_rejects_unknown_multiple_or_extra_fields(self):
        for line in ("推荐|", "推荐|普通体", "推荐|丁寧、カジュアル", "推荐|丁寧/カジュアル",
                     "推荐|丁寧|カジュアル", "推荐|丁寧|", "推荐|丁寧|这是理由", "推荐|⭐丁寧"):
            with self.subTest(line=line):
                self.assertIsNone(parse_line(line))

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

    def test_warning_attached_to_meaning_preserves_translation(self):
        reply = parse_line("カジュアル|よろしくね。|请多关照。　⚠ 对此对象不要用")
        self.assertEqual(reply, Reply("カジュアル", "よろしくね。", "请多关照。", warn=True))

    def test_reason_with_colon(self):
        self.assertEqual(parse_line("理由：对方用了「マジ」。"), "对方用了「マジ」。")
        self.assertEqual(parse_line("推荐理由: 对方用了です。"), "对方用了です。")

    def test_ruby_removed(self):
        self.assertEqual(strip_ruby("資料(しりょう)をまとめます"), "資料をまとめます")

    def test_unknown_value_does_not_keep_a_decisive_yes_or_no_prefix(self):
        for prefix in ("はい", "うん", "ええ", "いいえ"):
            with self.subTest(prefix=prefix):
                reply = parse_line(f"丁寧|{prefix}、カードは【有無】です。|是的，卡【有无】。|")
                self.assertEqual(reply.text, "カードは【有無】です。")
                self.assertEqual(reply.meaning, "卡【有无】。")
        self.assertEqual(parse_line("丁寧|はい、お願いします。|好的，麻烦您。|").text, "はい、お願いします。")
        self.assertEqual(parse_line("丁寧|はい、お願いします。【名前】です。|麻烦您。我叫【名字】。|").text,
                         "はい、お願いします。【名前】です。")


class SuggestTest(unittest.TestCase):
    def test_streamed_lines_split_across_chunks(self):
        chunks = [GOOD[i:i + 7] for i in range(0, len(GOOD), 7)]
        updates = []
        s = fake_suggester(chunks)
        result = s.suggest("この資料、明日までにまとめてもらえますか？", [], on_update=lambda r: updates.append(len(r.replies)))
        self.assertIsNone(result.error)
        self.assertTrue(result.complete)
        self.assertFalse(result.star_inferred)
        self.assertIsNone(result.recommended_style)
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
        s = Suggester(env={**ENV, "LLM_BASE_URL": "https://api.deepseek.com"}, profile="")
        self.assertEqual(s.extra_body, {"thinking": {"type": "disabled"}})
        self.assertEqual(Suggester(env=ENV, profile="").extra_body, {})

    def test_missing_star_cell_and_star_from_reason(self):
        raw = ("丁寧||明日は予定があるので、また今度お願いします。|我明天有安排。|\n"
               "カジュアル|明日はちょっと無理かも。|明天可能不太行。|\n"
               "かんたん|ごめん、明日は無理。|抱歉，明天不行。|\n"
               "理由|对方用普通体邀约，用カジュアル最自然。")
        result = fake_suggester([raw]).suggest("ご飯いかない", [])
        self.assertTrue(result.complete)
        self.assertEqual(result.replies[1].text, "明日はちょっと無理かも。")
        self.assertTrue(result.replies[1].star)
        self.assertTrue(result.star_inferred)
        self.assertIsNone(result.recommended_style)

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


class RecommendationTest(unittest.TestCase):
    def test_first_recommendation_streams_without_an_empty_callback(self):
        raw = "推荐|カジュアル\n" + UNSTARRED
        snapshots = []
        result = fake_suggester([raw[i:i + 3] for i in range(0, len(raw), 3)]).suggest(
            "x", [], on_update=lambda s: snapshots.append(
                (len(s.replies), tuple(r.style for r in s.replies if r.star))))
        self.assertEqual(snapshots, [(1, ()), (2, ("カジュアル",)),
                                     (3, ("カジュアル",)), (3, ("カジュアル",))])
        self.assertTrue(result.complete)
        self.assertIsNone(result.error)
        self.assertEqual(result.recommended_style, "カジュアル")
        self.assertFalse(result.star_inferred)
        self.assertEqual(result.reason, "顺着话头回应即可。")

    def test_late_recommendation_without_newline_replaces_old_star_and_keeps_reason(self):
        raw = GOOD + "\n推荐|カジュアル"
        snapshots = []
        result = fake_suggester([raw[:-2], raw[-2:]]).suggest(
            "x", [], on_update=lambda s: snapshots.append(tuple(r.style for r in s.replies if r.star)))
        self.assertEqual(snapshots[:-1], [("丁寧",)] * 4)
        self.assertEqual(snapshots[-1], ("カジュアル",))
        self.assertEqual(result.reason, "你说明对方是上司，所以用丁寧。")
        self.assertEqual(result.recommended_style, "カジュアル")
        self.assertTrue(result.complete)
        self.assertFalse(result.star_inferred)

    def test_first_recommendation_overrides_all_later_inline_stars(self):
        raw = "推荐|かんたん\n" + GOOD.replace("カジュアル||", "カジュアル|⭐|").replace(
            "かんたん||", "かんたん|⭐|")
        snapshots = []
        result = fake_suggester([raw]).suggest(
            "x", [], on_update=lambda s: snapshots.append(tuple(r.style for r in s.replies if r.star)))
        self.assertEqual(snapshots, [(), (), ("かんたん",), ("かんたん",)])
        self.assertEqual(result.recommended_style, "かんたん")
        self.assertTrue(result.complete)
        self.assertFalse(result.star_inferred)

    def test_recommendation_between_out_of_order_replies_updates_existing_reply(self):
        lines = UNSTARRED.splitlines()
        raw = "\n".join([lines[1], "推荐:カジュアル", lines[2], lines[0], lines[3]])
        snapshots = []
        result = fake_suggester([raw]).suggest(
            "x", [], on_update=lambda s: snapshots.append(
                (len(s.replies), tuple(r.style for r in s.replies if r.star))))
        self.assertEqual(snapshots[:2], [(1, ()), (1, ("カジュアル",))])
        self.assertEqual([r.style for r in result.replies], ["丁寧", "カジュアル", "かんたん"])
        self.assertEqual([r.style for r in result.replies if r.star], ["カジュアル"])
        self.assertTrue(result.complete)
        self.assertFalse(result.star_inferred)

    def test_repeated_and_conflicting_recommendations_keep_first_choice_without_callbacks(self):
        lines = UNSTARRED.splitlines()
        raw = "\n".join(["推荐|カジュアル", lines[0], "推荐|丁寧", "推荐|カジュアル",
                          lines[1], "推荐|かんたん", lines[2], lines[3]])
        snapshots = []
        result = fake_suggester([raw]).suggest(
            "x", [], on_update=lambda s: snapshots.append(
                (len(s.replies), tuple(r.style for r in s.replies if r.star))))
        self.assertEqual(snapshots, [(1, ()), (2, ("カジュアル",)),
                                     (3, ("カジュアル",)), (3, ("カジュアル",))])
        self.assertEqual(result.recommended_style, "カジュアル")
        self.assertTrue(result.complete)
        self.assertFalse(result.star_inferred)

    def test_invalid_recommendations_preserve_polite_fallback(self):
        for line in ("推荐|普通体", "推荐|丁寧/カジュアル", "推荐|カジュアル|多余栏"):
            with self.subTest(line=line):
                result = fake_suggester([line + "\n" + UNSTARRED]).suggest("x", [])
                self.assertIsNone(result.recommended_style)
                self.assertTrue(result.star_inferred)
                self.assertTrue(result.complete)
                self.assertEqual([r.style for r in result.replies if r.star], ["丁寧"])

    def test_invalid_recommendation_preserves_legacy_inline_star(self):
        result = fake_suggester(["推荐|未知\n" + GOOD]).suggest("x", [])
        self.assertIsNone(result.recommended_style)
        self.assertFalse(result.star_inferred)
        self.assertTrue(result.complete)
        self.assertEqual([r.style for r in result.replies if r.star], ["丁寧"])

    def test_missing_recommended_reply_does_not_choose_another_style(self):
        lines = GOOD.splitlines()
        for replies in ([], [lines[0], lines[2], lines[3]]):
            with self.subTest(reply_count=len(replies)):
                raw = "\n".join(["推荐|カジュアル", *replies])
                result = fake_suggester([raw]).suggest("x", [])
                self.assertEqual(result.recommended_style, "カジュアル")
                self.assertEqual([r.style for r in result.replies if r.star], [])
                self.assertFalse(result.complete)
                self.assertFalse(result.star_inferred)
                self.assertEqual(result.error, "incomplete or malformed reply")


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


class SpeakableRuleTest(unittest.TestCase):
    """D-038: on-the-spot choices are answered, only background facts become 【】 blanks."""

    def test_both_prompts_separate_choices_from_background_facts(self):
        for prompt in (build_system_prompt("", external_materials=True), build_system_prompt("")):
            self.assertIn("当场自己决定或自己清楚的事", prompt)
            self.assertIn("不留【】", prompt)
            self.assertIn("可说「<另一种回答的日语>」", prompt)
            # The over-blanking instruction from D-023 must be gone.
            self.assertNotIn("【持っています／持っていません】", prompt)
            self.assertNotIn("【参加できます／参加できません】", prompt)

    def test_app_prompt_keeps_background_blanks_and_colloquial_markers(self):
        prompt = build_system_prompt("", external_materials=True)
        self.assertIn("日本に来て【期間】です", prompt)
        self.assertIn("「【時間】からです」", prompt)
        self.assertIn("「ってか」", prompt)


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
