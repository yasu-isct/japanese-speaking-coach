"""Japanese speech recognition with faster-whisper (CTranslate2).

Default model: Whisper large-v3-turbo (CTranslate2 build). On real video audio
recorded from a real video it made fewer errors than kotoba-whisper-v2.0
(CER 13.9% vs 16.5%) at about the same speed, and was equal on clean
synthesized speech (3.6% vs 3.7%); see docs/decisions.md D-015.
kotoba-whisper (large-v3 distilled on Japanese ReazonSpeech) remains available
with --model kotoba-tech/kotoba-whisper-v2.0-faster.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from .cuda_setup import register_cuda_dlls

DEFAULT_MODEL = "large-v3-turbo"  # faster-whisper alias for mobiuslabsgmbh/faster-whisper-large-v3-turbo
# Silence put in front of every clip. Without it, a clip that starts right on a word made
# large-v3-turbo replace 12 s of real speech with 「ご視聴ありがとうございました」; 0.3 s fixed
# all 20 clips of a real recording, while 0.5-1.0 s made it drop the start of another clip.
LEAD_SILENCE = 0.3

# Stock phrases Whisper invents for noise (keyboard, breathing, room sound). In a 89-minute
# class recording 「ご視聴ありがとうございました」 was 20% of all subtitles, almost all of it on
# short, quiet clips (median 0.8 s, about 8 dB quieter than speech). Dropped only on such clips.
# Video-site endings and subtitle credits nobody says in a class or a call: always dropped
# (in the class replay, the 7 left after the short/quiet rule were 2.6-9.4 s long).
VIDEO_CREDITS = ("ご視聴ありがとうございました", "ご清聴ありがとうございました", "チャンネル登録",
                 "最後までご視聴", "字幕視聴ありがとうございました",
                 # The same noise transcribed as Chinese gives Chinese video-site credits instead.
                 "字幕志愿者", "字幕志願者", "字幕提供", "中文字幕", "优优独播剧场", "YoYo Television", "请不吝点赞", "明镜")
# Phrases people really do say: dropped only on short or quiet clips.
NOISY_PHRASES = ("おやすみなさい", "感谢观看", "謝謝觀看", "订阅")
STOCK_PHRASES = VIDEO_CREDITS + NOISY_PHRASES
# Previous recognized sentences given to Whisper as earlier text (D-052). Without them a short
# clip has no context: in a 46-minute interview replay 28 of 505 clips came out as 「Oh...」 /
# 「Uh...」; with the last 2 sentences (at most 80 chars) most became the right Japanese.
PROMPT_SENTENCES = 2
PROMPT_MAX_CHARS = 80
PROMPT_STRIP = re.compile(r"[\s、。，,.!！?？…・ー〜~「」]+")
NOISE_MAX_SECONDS = 2.5
NOISE_MAX_DB = -30.0


def looks_like_noise_phrase(text: str, audio: np.ndarray) -> bool:
    """True for a video credit, or a stock phrase recognized from a short or quiet clip (i.e. probably noise)."""
    core = text.strip().rstrip("。！!.、 ")
    if not core:
        return False
    if any(p in core for p in VIDEO_CREDITS):
        return True
    if not any(p in core for p in NOISY_PHRASES):
        return False
    seconds = len(audio) / 16000
    level = 20 * np.log10(float(np.sqrt(np.mean(np.square(audio), dtype=np.float64))) + 1e-9) if len(audio) else -100.0
    return seconds < NOISE_MAX_SECONDS or level < NOISE_MAX_DB


def copies_prompt(text: str, prompt: str) -> bool:
    """True if the recognized text is only a repeat of (part of) the earlier text it was given."""
    core = PROMPT_STRIP.sub("", text)
    # Very short replies (はい, うん) repeat for real and cost nothing to keep.
    return len(core) >= 3 and core in PROMPT_STRIP.sub("", prompt)


@dataclass
class AsrResult:
    text: str
    seconds: float      # wall-clock decoding time (language check included)
    audio_seconds: float
    language: str = "ja"
    language_prob: float | None = None
    dropped: str = ""   # why the text was discarded ("noise phrase"), empty if kept


class Transcriber:
    def __init__(self, model: str = DEFAULT_MODEL, device: str = "auto", compute_type: str | None = None,
                 languages: tuple[str, ...] = ("ja",), hotwords: str | None = None) -> None:
        # Default: Japanese only (the app is for Japanese conversations). With languages=("ja", "zh")
        # (CLI --mixed-chinese, shelved for now: D-019/D-020) each finished sentence is first classified
        # as one of them and transcribed in that language; this costs ~0.2 s per sentence.
        self.languages = languages
        self.hotwords = hotwords  # glossary terms (D-046); partial and final decodes both use them
        self.recent: deque[str] = deque(maxlen=PROMPT_SENTENCES)  # last kept final texts (D-052)
        register_cuda_dlls()
        from faster_whisper import WhisperModel

        self.device = self._pick_device(device)
        self.compute_type = compute_type or ("float16" if self.device == "cuda" else "int8")
        self.model = WhisperModel(model, device=self.device, compute_type=self.compute_type)
        self.model_name = model

    @staticmethod
    def _pick_device(device: str) -> str:
        if device != "auto":
            return device
        try:
            import ctranslate2

            return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            return "cpu"

    def detect(self, padded: np.ndarray) -> tuple[str, float]:
        """Most likely language among ``self.languages`` and its probability (renormalized over them)."""
        _, _, all_probs = self.model.detect_language(padded)
        probs = {lang: p for lang, p in all_probs if lang in self.languages}
        total = sum(probs.values())
        if not total:
            return self.languages[0], 0.0
        lang = max(probs, key=probs.get)
        return lang, probs[lang] / total

    def transcribe(self, audio: np.ndarray, final: bool = True) -> AsrResult:
        start = time.perf_counter()
        padded = np.concatenate([np.zeros(int(LEAD_SILENCE * 16000), np.float32), audio])
        # Partial (in-progress) results skip the check to stay fast; the final one decides.
        lang, prob = (self.detect(padded) if final and len(self.languages) > 1 else (self.languages[0], None))
        prompt = self.prompt() if final and lang == "ja" else None
        text = self._decode(padded, lang, final, prompt)
        if prompt and copies_prompt(text, prompt):
            # A noise clip given earlier text tends to echo it; decide that clip without it.
            text = self._decode(padded, lang, final, None)
        dropped = "noise phrase" if final and looks_like_noise_phrase(text, audio) else ""
        if final and text and not dropped:
            self.recent.append(text)
        return AsrResult("" if dropped else text, time.perf_counter() - start, len(audio) / 16000,
                         language=lang, language_prob=prob, dropped=dropped)

    def prompt(self) -> str | None:
        """The last few recognized sentences, as earlier text for the next final decode."""
        return "".join(self.recent)[-PROMPT_MAX_CHARS:] or None

    def _decode(self, padded: np.ndarray, lang: str, final: bool, prompt: str | None) -> str:
        segments, _ = self.model.transcribe(
            padded,
            language=lang,
            task="transcribe",
            beam_size=5 if final else 1,       # partial results favour speed
            vad_filter=False,                  # segmentation is done upstream
            condition_on_previous_text=False,  # only within one clip; earlier clips come in as prompt
            without_timestamps=True,
            hotwords=self.hotwords,
            initial_prompt=prompt,
        )
        return "".join(seg.text for seg in segments).strip()

    def warm_up(self) -> None:
        """Run once on silence so the first real utterance isn't slowed by CUDA init."""
        self.transcribe(np.zeros(16000, dtype=np.float32), final=False)
