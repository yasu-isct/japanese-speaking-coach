"""Energy-based speech segmenter with an adaptive noise floor.

Turns a stream of short audio blocks into utterances:

- ``partial`` events while someone is speaking (the audio so far), so subtitles
  can appear before the sentence ends;
- a ``final`` event when the speaker pauses (or the utterance gets too long).

Why energy-based instead of a neural VAD: no extra model to load, fully
inspectable, and good enough for clean speech (mic or system audio). The
thresholds adapt to the background level, so a fan or room noise is tolerated.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .audio import BLOCK_SECONDS, SAMPLE_RATE


@dataclass
class SegmentEvent:
    kind: str  # "partial" or "final"
    audio: np.ndarray
    utterance_id: int


def rms_db(block: np.ndarray) -> float:
    """Root-mean-square level in dBFS (silence -> about -100)."""
    rms = float(np.sqrt(np.mean(np.square(block), dtype=np.float64))) if len(block) else 0.0
    return 20.0 * np.log10(max(rms, 1e-5))


class Segmenter:
    def __init__(
        self,
        start_margin_db: float = 12.0,   # speech must be this much louder than the noise floor
        min_speech_db: float = -48.0,    # ...and at least this loud in absolute terms
        start_frames: int = 3,           # consecutive loud blocks to start an utterance (90 ms)
        end_silence: float = 0.6,        # pause length that ends an utterance (s)
        pre_roll: float = 0.3,           # audio kept from before the start (s)
        max_utterance: float = 12.0,     # force a final result after this long (s)
        partial_interval: float = 0.8,   # how often to emit partial audio (s)
        min_utterance: float = 0.3,      # drop blips shorter than this (s)
        cut_search: float = 4.0,         # at max_utterance, look back this far for a quiet cut point (s)
    ) -> None:
        self.cut_search_blocks = int(round(cut_search / BLOCK_SECONDS))
        self.start_margin_db = start_margin_db
        self.min_speech_db = min_speech_db
        self.start_frames = start_frames
        self.end_blocks = int(round(end_silence / BLOCK_SECONDS))
        self.max_samples = int(max_utterance * SAMPLE_RATE)
        self.partial_samples = int(partial_interval * SAMPLE_RATE)
        self.min_samples = int(min_utterance * SAMPLE_RATE)
        self.pre_roll = deque(maxlen=max(1, int(round(pre_roll / BLOCK_SECONDS))))
        self.noise_db = -60.0
        self.reset()
        self.utterance_id = 0

    def reset(self) -> None:
        self.active = False
        self.buffer: list[np.ndarray] = []
        self.loud_run = 0
        self.quiet_run = 0
        self.since_partial = 0

    def _is_loud(self, level: float) -> bool:
        return level > max(self.noise_db + self.start_margin_db, self.min_speech_db)

    def _update_noise(self, level: float) -> None:
        # Fall quickly to quieter levels, rise slowly (so speech doesn't become "noise").
        rate = 0.3 if level < self.noise_db else 0.02
        self.noise_db += rate * (level - self.noise_db)

    def _buffered(self) -> np.ndarray:
        return np.concatenate(self.buffer) if self.buffer else np.zeros(0, np.float32)

    def feed(self, block: np.ndarray) -> list[SegmentEvent]:
        level = rms_db(block)
        loud = self._is_loud(level)
        events: list[SegmentEvent] = []

        if not self.active:
            self.pre_roll.append(block)
            if loud:
                self.loud_run += 1
            else:
                self.loud_run = 0
                self._update_noise(level)
            if self.loud_run >= self.start_frames:
                self.active = True
                self.buffer = list(self.pre_roll)
                self.pre_roll.clear()
                self.quiet_run = 0
                self.since_partial = 0
                self.utterance_id += 1
            return events

        self.buffer.append(block)
        self.since_partial += len(block)
        self.quiet_run = 0 if loud else self.quiet_run + 1
        total = sum(len(b) for b in self.buffer)

        if self.quiet_run >= self.end_blocks:
            # Trim most of the trailing silence; keep a little for natural endings.
            audio = self._buffered()
            keep = len(audio) - (self.quiet_run - 5) * len(block)
            audio = audio[:max(keep, 0)]
            if len(audio) >= self.min_samples:
                events.append(SegmentEvent("final", audio, self.utterance_id))
            self.reset()
        elif total >= self.max_samples:
            events.append(self._cut_long_utterance())
        elif self.since_partial >= self.partial_samples:
            self.since_partial = 0
            events.append(SegmentEvent("partial", self._buffered(), self.utterance_id))
        return events

    def _cut_long_utterance(self) -> SegmentEvent:
        """Someone has talked for max_utterance without a 0.6 s pause: split at the quietest moment.

        Cutting exactly at the limit used to split words in half, and a clip that starts
        with half a word can make Whisper invent a stock phrase for the whole clip
        (「ご視聴ありがとうございました」 for 12 s of real speech). So look back over the
        last ``cut_search`` seconds for the quietest 150 ms (a breath between phrases),
        finish the utterance there and carry the rest over into the next one.
        """
        levels = np.array([rms_db(b) for b in self.buffer])
        smooth = np.convolve(levels, np.ones(5) / 5, mode="same")
        last = len(self.buffer) - 3                      # keep a few blocks for the next utterance
        first = max(1, len(self.buffer) - self.cut_search_blocks)
        cut = first + int(np.argmin(smooth[first:last])) if last > first else len(self.buffer)
        event = SegmentEvent("final", np.concatenate(self.buffer[:cut]), self.utterance_id)
        self.buffer = self.buffer[cut:]
        self.utterance_id += 1                           # still active: the speaker goes on
        self.since_partial = sum(len(b) for b in self.buffer)
        self.quiet_run = 0
        return event

    def flush(self) -> list[SegmentEvent]:
        """Close an open utterance (end of stream)."""
        if self.active and self.buffer:
            audio = self._buffered()
            self.reset()
            if len(audio) >= self.min_samples:
                return [SegmentEvent("final", audio, self.utterance_id)]
        self.reset()
        return []
