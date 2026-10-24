"""Archive of every run: the full recording, each sentence and its timings.

    logs/sessions/
      index.tsv                  one line per run: start, minutes, sentences, source, folder
      20261025-101500/
        meta.json                source, model, partner, start/end, counts
        stream.wav               everything that was heard, 16 kHz mono
        transcript.txt           [mm:ss] sentence   (human-readable)
        clips/NNNN.wav           each finished sentence's audio
        clips/transcripts.tsv    NNNN.wav<TAB>text  (tools/compare_asr.py reads this folder)
        suggestions.jsonl        per turn: replies + asr / api / render ms (when --suggest is on)

Everything stays under logs/, which is git-ignored. Recordings of a call contain the
other person's voice, so the window shows a "録音中" mark while this is running.
"""

from __future__ import annotations

import json
import threading
import time
import wave
from pathlib import Path

import numpy as np

from .audio import SAMPLE_RATE

SESSIONS_DIR = Path(__file__).resolve().parents[2] / "logs" / "sessions"


def _pcm(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()


def _open_wav(path: Path) -> wave.Wave_write:
    wf = wave.open(str(path), "wb")
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(SAMPLE_RATE)
    return wf


class Session:
    def __init__(self, source: str, model: str, partner: str | None = None, root: Path = SESSIONS_DIR) -> None:
        self.root = root
        self.started = time.time()
        self.dir = root / time.strftime("%Y%m%d-%H%M%S", time.localtime(self.started))
        (self.dir / "clips").mkdir(parents=True, exist_ok=True)
        self.meta = {"source": source, "model": model, "partner": partner,
                     "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started))}
        self._stream = _open_wav(self.dir / "stream.wav")
        self.samples = 0          # samples written to stream.wav so far
        self.sentences = 0
        self.lock = threading.Lock()
        self._write_meta()

    @property
    def suggestions_path(self) -> Path:
        return self.dir / "suggestions.jsonl"

    def write_audio(self, block: np.ndarray) -> None:
        with self.lock:
            if self._stream is not None:
                self._stream.writeframes(_pcm(block))
                self.samples += len(block)

    def add_sentence(self, uid: int, audio: np.ndarray, text: str) -> None:
        """A finished sentence: save its clip and add it to the transcript."""
        start = max(0.0, (self.samples - len(audio)) / SAMPLE_RATE)
        name = f"{uid:04d}.wav"
        with _open_wav(self.dir / "clips" / name) as wf:
            wf.writeframes(_pcm(audio))
        with (self.dir / "clips" / "transcripts.tsv").open("a", encoding="utf-8") as f:
            f.write(f"{name}\t{text}\n")
        with (self.dir / "transcript.txt").open("a", encoding="utf-8") as f:
            f.write(f"[{int(start // 60):02d}:{int(start % 60):02d}] {text}\n")
        self.sentences += 1

    def close(self) -> None:
        with self.lock:
            if self._stream is None:
                return
            self._stream.close()
            self._stream = None
        minutes = self.samples / SAMPLE_RATE / 60
        self.meta.update(ended=time.strftime("%Y-%m-%d %H:%M:%S"), minutes=round(minutes, 2),
                         sentences=self.sentences)
        self._write_meta()
        index = self.root / "index.tsv"
        new = not index.exists()
        with index.open("a", encoding="utf-8") as f:
            if new:
                f.write("started\tminutes\tsentences\tsource\tfolder\n")
            f.write(f"{self.meta['started']}\t{minutes:.1f}\t{self.sentences}\t{self.meta['source']}\t{self.dir.name}\n")

    def _write_meta(self) -> None:
        (self.dir / "meta.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=1), encoding="utf-8")
