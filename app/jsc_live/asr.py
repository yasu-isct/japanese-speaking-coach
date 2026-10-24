"""Japanese speech recognition with faster-whisper (CTranslate2) and kotoba-whisper.

kotoba-whisper is Whisper large-v3 distilled on the ReazonSpeech Japanese corpus:
similar accuracy to large-v3 on Japanese at a fraction of the decoding cost.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from .cuda_setup import register_cuda_dlls

DEFAULT_MODEL = "kotoba-tech/kotoba-whisper-v2.0-faster"


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
        segments, _ = self.model.transcribe(
            audio,
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
