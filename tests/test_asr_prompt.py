"""Previous sentences as Whisper prompt, and the echo guard (D-052). No model is loaded."""

import sys
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.asr import PROMPT_MAX_CHARS, Transcriber, copies_prompt  # noqa: E402

CLIP = np.full(16000, 0.1, np.float32)  # 1 s, loud enough not to count as a quiet noise clip


def fake_transcriber(*outputs: str) -> Transcriber:
    t = Transcriber.__new__(Transcriber)
    t.languages, t.hotwords, t.recent = ("ja",), None, deque(maxlen=2)
    t.model = Mock()
    t.model.transcribe.side_effect = [([SimpleNamespace(text=o)], None) for o in outputs]
    return t


def prompts(t: Transcriber) -> list:
    return [c.kwargs["initial_prompt"] for c in t.model.transcribe.call_args_list]


class PromptTest(unittest.TestCase):
    def test_final_decodes_get_the_last_two_sentences(self):
        t = fake_transcriber("最初の文です。", "二つ目の文です。", "三つ目の文です。", "四つ目。")
        for _ in range(4):
            t.transcribe(CLIP, final=True)
        self.assertEqual(prompts(t), [None, "最初の文です。", "最初の文です。二つ目の文です。",
                                      "二つ目の文です。三つ目の文です。"])

    def test_partial_decodes_get_no_prompt_and_are_not_remembered(self):
        t = fake_transcriber("途中の文", "確定した文です。", "次の文です。")
        t.transcribe(CLIP, final=False)
        t.transcribe(CLIP, final=True)
        t.transcribe(CLIP, final=True)
        self.assertEqual(prompts(t), [None, None, "確定した文です。"])

    def test_prompt_keeps_only_the_newest_characters(self):
        t = fake_transcriber()
        t.recent.extend(["あ" * 70, "い" * 70])
        self.assertEqual(t.prompt(), "あ" * (PROMPT_MAX_CHARS - 70) + "い" * 70)

    def test_an_echo_of_the_prompt_is_decoded_again_without_it(self):
        t = fake_transcriber("面接の時の話です。", "面接の時…", "Uh...")
        t.transcribe(CLIP, final=True)
        result = t.transcribe(CLIP, final=True)
        self.assertEqual(prompts(t), [None, "面接の時の話です。", None])
        self.assertEqual(result.text, "Uh...")

    def test_echo_check(self):
        self.assertTrue(copies_prompt("あ、そうか…", "そうですね。あ、そうか。"))
        self.assertFalse(copies_prompt("はい。", "はい、そうです。"))        # short replies really repeat
        self.assertFalse(copies_prompt("大阪の会社です", "東京の会社です"))
        self.assertFalse(copies_prompt("", "前の文"))


if __name__ == "__main__":
    unittest.main()
