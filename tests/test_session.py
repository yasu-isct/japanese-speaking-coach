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
            s.close()
            s.close()                                  # closing twice is harmless

            with wave.open(str(s.dir / "stream.wav")) as wf:
                self.assertEqual(wf.getnframes(), 100 * 480 + 16000)
            self.assertEqual(sorted(p.name for p in (s.dir / "clips").iterdir()),
                             ["0001.wav", "0002.wav", "transcripts.tsv"])
            self.assertEqual((s.dir / "clips" / "transcripts.tsv").read_text(encoding="utf-8"),
                             "0001.wav\tお疲れ様です。\n0002.wav\t今日はもう上がりですか。\n")
            self.assertEqual((s.dir / "transcript.txt").read_text(encoding="utf-8").splitlines(),
                             ["[00:02] お疲れ様です。", "[00:03] 今日はもう上がりですか。"])
            meta = json.loads((s.dir / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual((meta["sentences"], meta["partner"]), (2, "上司"))
            index = (root / "index.tsv").read_text(encoding="utf-8").splitlines()
            self.assertEqual(index[0].split("\t")[0], "started")
            self.assertTrue(index[1].endswith("\t2\tmic\t" + s.dir.name))


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
