"""Unit tests for the run archive (app/jsc_live/session.py) and the history window's word reading."""

import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.session import Session  # noqa: E402


class SessionTest(unittest.TestCase):
    def test_archive_layout(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            s = Session("mic", "large-v3-turbo", "上司", root=root)
            for _ in range(100):                       # 3 s of audio
                s.write_audio(np.zeros(480, np.float32))
            s.add_sentence(1, np.zeros(16000, np.float32), "お疲れ様です。")
            s.write_audio(np.zeros(16000, np.float32))
            s.add_sentence(2, np.zeros(8000, np.float32), "今日はもう上がりですか。")
            s.add_sentence(3, np.zeros(4000, np.float32), "这是一句中文", language="zh")
            s.add_sentence(4, np.zeros(4000, np.float32), "", dropped="noise phrase")
            s.close()
            s.close()                                  # closing twice is harmless

            with wave.open(str(s.dir / "stream.wav")) as wf:
                self.assertEqual(wf.getnframes(), 100 * 480 + 16000)
            self.assertEqual(sorted(p.name for p in (s.dir / "clips").iterdir()),
                             ["0001.wav", "0002.wav", "0003.wav", "0004.wav", "transcripts.tsv"])
            self.assertEqual((s.dir / "clips" / "transcripts.tsv").read_text(encoding="utf-8"),
                             "0001.wav\tお疲れ様です。\tja\t\n0002.wav\t今日はもう上がりですか。\tja\t\n"
                             "0003.wav\t这是一句中文\tzh\t\n0004.wav\t\tja\tnoise phrase\n")
            self.assertEqual((s.dir / "transcript.txt").read_text(encoding="utf-8").splitlines(),
                             ["[00:02] お疲れ様です。", "[00:03] 今日はもう上がりですか。", "[00:03] [中] 这是一句中文"])
            meta = json.loads((s.dir / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual((meta["sentences"], meta["partner"]), (3, "上司"))   # the dropped one is not counted
            index = (root / "index.tsv").read_text(encoding="utf-8").splitlines()
            self.assertEqual(index[0].split("\t")[0], "started")
            self.assertTrue(index[1].endswith("\t3\tmic\t" + s.dir.name))


class NoisePhraseTest(unittest.TestCase):
    def test_stock_phrase_on_short_or_quiet_clip_is_dropped(self):
        from jsc_live.asr import looks_like_noise_phrase

        short = np.full(8000, 0.1, np.float32)          # 0.5 s, loud enough
        quiet_long = np.full(64000, 0.005, np.float32)  # 4 s, about -46 dB
        loud_long = np.full(64000, 0.1, np.float32)     # 4 s, -20 dB
        self.assertTrue(looks_like_noise_phrase("ご視聴ありがとうございました", short))
        self.assertTrue(looks_like_noise_phrase("字幕志愿者 李宗盛", quiet_long))
        self.assertTrue(looks_like_noise_phrase("优优独播剧场——YoYo Television Series Exclusive", short))
        # Video credits go even when long and loud; phrases people really say survive then.
        self.assertTrue(looks_like_noise_phrase("ご視聴ありがとうございました", loud_long))
        self.assertTrue(looks_like_noise_phrase("おやすみなさい", short))
        self.assertFalse(looks_like_noise_phrase("おやすみなさい", loud_long))
        self.assertFalse(looks_like_noise_phrase("ありがとうございました", short))
        self.assertFalse(looks_like_noise_phrase("今日はここまでです", short))


class ReadingTest(unittest.TestCase):
    def test_reading_and_lemma(self):
        try:
            from jsc_live.furigana import Furigana
            from jsc_live.history import Lookup, reading_and_lemma
        except ImportError:
            self.skipTest("fugashi not installed")
        f = Furigana("all")
        self.assertEqual(reading_and_lemma(f, "恥ずかしかった"), ("はずかしかった", "恥ずかしい"))
        self.assertEqual(reading_and_lemma(f, "食べてみた")[0], "たべてみた")
        self.assertEqual(Lookup(None).meaning("x", "y")[:4], "（没有 ")

        class Fake:
            calls = 0

            def ask(self, system, user, max_tokens=120):
                Fake.calls += 1
                return "恥ずかしい（はずかしい）：难为情\n多余的第二行"

        lk = Lookup(Fake())
        self.assertEqual(lk.meaning("恥ずかしかった", "s"), "恥ずかしい（はずかしい）：难为情")
        lk.meaning("恥ずかしかった", "s")
        self.assertEqual(Fake.calls, 1)              # cached


if __name__ == "__main__":
    unittest.main()
