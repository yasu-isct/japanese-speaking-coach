"""Japanese speech recognition with faster-whisper (CTranslate2).

Default model: Whisper large-v3-turbo (CTranslate2 build). On real video audio
recorded from a real video it made fewer errors than kotoba-whisper-v2.0
(CER 13.9% vs 16.5%) at about the same speed, and was equal on clean
synthesized speech (3.6% vs 3.7%); see docs/decisions.md D-015.
kotoba-whisper (large-v3 distilled on Japanese ReazonSpeech) remains available
with --model kotoba-tech/kotoba-whisper-v2.0-faster.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from .cuda_setup import register_cuda_dlls

DEFAULT_MODEL = "large-v3-turbo"  # faster-whisper alias for mobiuslabsgmbh/faster-whisper-large-v3-turbo
# Silence put in front of every clip. Without it, a clip that starts right on a word made
# large-v3-turbo replace 12 s of real speech with 「ご視聴ありがとうございました」; 0.3 s fixed
# all 20 clips of a real recording, while 0.5-1.0 s made it drop the start of another clip.
LEAD_SILENCE = 0.3


@dataclass
class AsrResult:
    text: str
    seconds: float      # wall-clock decoding time
    audio_seconds: float


class Transcriber:
    def __init__(self, model: str = DEFAULT_MODEL, device: str = "auto", compute_type: str | None = None) -> None:
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

    def transcribe(self, audio: np.ndarray, final: bool = True) -> AsrResult:
        start = time.perf_counter()
        padded = np.concatenate([np.zeros(int(LEAD_SILENCE * 16000), np.float32), audio])
        segments, _ = self.model.transcribe(
            padded,
            language="ja",
            task="transcribe",
            beam_size=5 if final else 1,       # partial results favour speed
            vad_filter=False,                  # segmentation is done upstream
            condition_on_previous_text=False,  # avoids repeating earlier text on short clips
            without_timestamps=True,
        )
        text = "".join(seg.text for seg in segments).strip()
        return AsrResult(text, time.perf_counter() - start, len(audio) / 16000)

    def warm_up(self) -> None:
        """Run once on silence so the first real utterance isn't slowed by CUDA init."""
        self.transcribe(np.zeros(16000, dtype=np.float32), final=False)
