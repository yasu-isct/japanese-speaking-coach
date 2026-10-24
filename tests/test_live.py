"""Unit tests for app/jsc_live (furigana alignment, graded ruby, segmenter).

No GPU or audio device needed. Furigana tests need fugashi + unidic-lite
(app/requirements.txt) and are skipped if they are missing.
"""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.audio import BLOCK_SIZE, SAMPLE_RATE, resample_linear  # noqa: E402
from jsc_live.furigana import Segment, align, kata_to_hira, load_basic_kanji, merge_plain  # noqa: E402
from jsc_live.segmenter import Segmenter  # noqa: E402

try:
    import fugashi  # noqa: F401

    HAS_MECAB = True
except ImportError:
    HAS_MECAB = False


class AlignTest(unittest.TestCase):
    def test_kata_to_hira(self):
        self.assertEqual(kata_to_hira("ベントウ"), "べんとう")
        self.assertEqual(kata_to_hira("コーヒー"), "こーひー")  # long vowel mark is kept

    def test_okurigana_gets_no_ruby(self):
        self.assertEqual(align("温め", "アタタメ"), [Segment("温", "あたた"), Segment("め")])

    def test_kana_in_the_middle(self):
        self.assertEqual(
            align("取り扱い", "トリアツカイ"),
            [Segment("取", "と"), Segment("り"), Segment("扱", "あつか"), Segment("い")],
        )

    def test_prefix_kana(self):
        self.assertEqual(align("お弁当", "オベントウ"), [Segment("お"), Segment("弁当", "べんとう")])

    def test_unalignable_falls_back_to_whole_word(self):
        self.assertEqual(align("今日は", "キョウワ"), [Segment("今日は", "きょうわ")])

    def test_merge_plain(self):
        merged = merge_plain([Segment("こ"), Segment("ちら"), Segment("弁当", "べんとう"), Segment("を")])
        self.assertEqual(merged, [Segment("こちら"), Segment("弁当", "べんとう"), Segment("を")])


class BasicKanjiTest(unittest.TestCase):
    def test_table_shared_with_skill(self):
        kanji, exceptions = load_basic_kanji()
        self.assertEqual(len(kanji), 245)
        self.assertIn("今日", exceptions)
        self.assertIn("一人", exceptions)


@unittest.skipUnless(HAS_MECAB, "fugashi / unidic-lite not installed")
class FuriganaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from jsc_live.furigana import Furigana

        cls.graded = Furigana("graded")
        cls.all = Furigana("all")
        cls.none = Furigana("none")

    def test_graded_skips_basic_kanji(self):
        self.assertEqual(self.graded.to_bracket("来週の会議"), "来週の会議[かいぎ]")

    def test_graded_keeps_special_readings(self):
        self.assertEqual(self.graded.to_bracket("今日は一人です"), "今日[きょう]は一人[ひとり]です")

    def test_context_reading(self):
        # MeCab picks the reading of the whole word, not per character.
        self.assertEqual(self.all.to_bracket("大人"), "大人[おとな]")

    def test_all_and_none(self):
        self.assertEqual(self.all.to_bracket("十時"), "十[じゅう]時[じ]")
        self.assertEqual(self.none.to_bracket("会議"), "会議")

    def test_text_is_preserved(self):
        text = "すみません、明日のシフトなんですけど。"
        self.assertEqual("".join(s.text for s in self.graded.annotate(text)), text)


def tone(seconds: float, amp: float = 0.2) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(seconds: float, amp: float = 0.0) -> np.ndarray:
    rng = np.random.default_rng(0)
    return (amp * rng.standard_normal(int(seconds * SAMPLE_RATE))).astype(np.float32)


def run_segmenter(audio: np.ndarray, **kwargs):
    seg = Segmenter(**kwargs)
    events = []
    for i in range(0, len(audio), BLOCK_SIZE):
        events += seg.feed(audio[i:i + BLOCK_SIZE])
    return events + seg.flush()


class SegmenterTest(unittest.TestCase):
    def finals(self, events):
        return [e for e in events if e.kind == "final"]

    def test_two_utterances_split_by_pause(self):
        audio = np.concatenate([silence(0.5), tone(1.0), silence(0.8), tone(1.5), silence(0.8)])
        finals = self.finals(run_segmenter(audio))
        self.assertEqual(len(finals), 2)
        self.assertEqual([e.utterance_id for e in finals], [1, 2])
        # Pre-roll + speech + a little trailing silence.
        self.assertAlmostEqual(len(finals[0].audio) / SAMPLE_RATE, 1.0, delta=0.5)

    def test_short_pause_does_not_split(self):
        audio = np.concatenate([silence(0.5), tone(1.0), silence(0.3), tone(1.0), silence(0.8)])
        self.assertEqual(len(self.finals(run_segmenter(audio))), 1)

    def test_partials_while_speaking(self):
        audio = np.concatenate([silence(0.3), tone(3.0), silence(0.8)])
        events = run_segmenter(audio, partial_interval=0.8)
        partials = [e for e in events if e.kind == "partial"]
        self.assertGreaterEqual(len(partials), 2)
        self.assertTrue(all(e.utterance_id == 1 for e in partials))

    def test_long_utterance_is_cut(self):
        audio = np.concatenate([silence(0.3), tone(5.0), silence(0.8)])
        self.assertGreaterEqual(len(self.finals(run_segmenter(audio, max_utterance=2.0))), 2)

    def test_background_noise_is_ignored(self):
        audio = np.concatenate([silence(1.0, amp=0.003), tone(1.0), silence(1.0, amp=0.003)])
        self.assertEqual(len(self.finals(run_segmenter(audio))), 1)

    def test_silence_only(self):
        self.assertEqual(run_segmenter(silence(2.0, amp=0.003)), [])

    def test_end_of_stream_flushes(self):
        audio = np.concatenate([silence(0.3), tone(1.0)])
        self.assertEqual(len(self.finals(run_segmenter(audio))), 1)


class ResampleTest(unittest.TestCase):
    def test_length(self):
        self.assertEqual(len(resample_linear(np.zeros(48000, np.float32), 48000)), 16000)


if __name__ == "__main__":
    unittest.main()
