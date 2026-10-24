"""Audio sources that yield 16 kHz mono float32 blocks.

- MicrophoneSource: the default (or a named) microphone.
- LoopbackSource: whatever the computer is playing (WASAPI loopback of a speaker),
  e.g. a video call or a YouTube video.
- WavFileSource: a WAV file, optionally paced in real time to simulate a live stream.
"""

from __future__ import annotations

import time
import wave
from typing import Iterator

import numpy as np

SAMPLE_RATE = 16000
BLOCK_SECONDS = 0.03  # 30 ms blocks: small enough for responsive segmentation
BLOCK_SIZE = int(SAMPLE_RATE * BLOCK_SECONDS)


def to_mono(data: np.ndarray) -> np.ndarray:
    """(frames, channels) -> (frames,) float32."""
    if data.ndim == 2:
        data = data.mean(axis=1)
    return data.astype(np.float32, copy=False)


def resample_linear(audio: np.ndarray, src_rate: int, dst_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Simple linear-interpolation resampler (good enough for speech recognition input)."""
    if src_rate == dst_rate or len(audio) == 0:
        return audio.astype(np.float32, copy=False)
    duration = len(audio) / src_rate
    n_out = int(round(duration * dst_rate))
    x_old = np.linspace(0.0, duration, num=len(audio), endpoint=False)
    x_new = np.linspace(0.0, duration, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


class _SoundcardSource:
    """Shared recording loop for soundcard microphones (real or loopback)."""

    def __init__(self, microphone) -> None:
        self.microphone = microphone
        self.name = microphone.name

    def blocks(self) -> Iterator[np.ndarray]:
        # WASAPI resamples to the requested rate for us.
        with self.microphone.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK_SIZE) as rec:
            while True:
                yield to_mono(rec.record(numframes=BLOCK_SIZE))


class MicrophoneSource(_SoundcardSource):
    def __init__(self, name: str | None = None) -> None:
        import soundcard as sc

        mic = sc.get_microphone(name) if name else sc.default_microphone()
        super().__init__(mic)


class LoopbackSource(_SoundcardSource):
    """Capture the audio being played on a speaker (default: the default speaker)."""

    def __init__(self, speaker_name: str | None = None) -> None:
        import soundcard as sc

        speaker = sc.get_speaker(speaker_name) if speaker_name else sc.default_speaker()
        mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        super().__init__(mic)


class WavFileSource:
    """Read a WAV file; with ``realtime=True`` blocks are released at playback speed."""

    def __init__(self, path: str, realtime: bool = False, tail_silence: float = 1.0) -> None:
        self.path = path
        self.name = path
        self.realtime = realtime
        self.tail_silence = tail_silence

    def load(self) -> np.ndarray:
        with wave.open(self.path, "rb") as wf:
            rate, width, channels = wf.getframerate(), wf.getsampwidth(), wf.getnchannels()
            raw = wf.readframes(wf.getnframes())
        if width != 2:
            raise ValueError(f"{self.path}: only 16-bit PCM WAV is supported")
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)
        audio = resample_linear(audio, rate)
        # Trailing silence lets the segmenter close the last utterance.
        return np.concatenate([audio, np.zeros(int(self.tail_silence * SAMPLE_RATE), np.float32)])

    def blocks(self) -> Iterator[np.ndarray]:
        audio = self.load()
        start = time.perf_counter()
        for i in range(0, len(audio), BLOCK_SIZE):
            if self.realtime:
                delay = start + i / SAMPLE_RATE - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
            yield audio[i:i + BLOCK_SIZE]


def list_devices() -> str:
    import soundcard as sc

    lines = ["Microphones:"]
    lines += [f"  - {m.name}" for m in sc.all_microphones()]
    lines.append("Speakers (usable with --source loopback --device NAME):")
    lines += [f"  - {s.name}" for s in sc.all_speakers()]
    lines.append(f"Default microphone: {sc.default_microphone().name}")
    lines.append(f"Default speaker: {sc.default_speaker().name}")
    return "\n".join(lines)
