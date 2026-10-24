"""Take a screenshot of the subtitle window while the real pipeline is running.

    python tools/capture_overlay.py --out docs/images/overlay.png

Input is synthesized speech (tests/asr_eval/audio, from tools/eval_asr.py --synth),
joined into one WAV and played through the live path at real-time speed
(WavFileSource -> Segmenter -> Transcriber -> Furigana -> SubtitleWindow).
The screenshot is taken as soon as two finished sentences and one partial
(grey) sentence are on screen. Only the window's own rectangle is captured,
and the window is made opaque so nothing behind it shows through.
"""

from __future__ import annotations

import argparse
import queue
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.audio import SAMPLE_RATE, WavFileSource  # noqa: E402
from jsc_live.main import worker  # noqa: E402
from jsc_live.overlay import SubtitleWindow  # noqa: E402

AUDIO_DIR = ROOT / "tests" / "asr_eval" / "audio"
CLIPS = ["t06_Haruka", "t09_Haruka", "s03_Ayumi"]


def build_demo_wav(path: Path) -> None:
    gap = np.zeros(int(0.9 * SAMPLE_RATE), np.float32)
    parts = [gap]
    for name in CLIPS:
        parts += [WavFileSource(str(AUDIO_DIR / f"{name}.wav"), tail_silence=0).load(), gap]
    pcm = (np.clip(np.concatenate(parts), -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())


def screenshot(root, out: Path) -> None:
    root.update()
    x, y, w, h = root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()
    ps = ("Add-Type -AssemblyName System.Drawing; "
          f"$b = New-Object Drawing.Bitmap {w},{h}; $g = [Drawing.Graphics]::FromImage($b); "
          f"$g.CopyFromScreen({x},{y},0,0,$b.Size); $b.Save('{out}')")
    subprocess.run(["powershell", "-NoProfile", "-c", ps], check=True)


class CapturingWindow(SubtitleWindow):
    def __init__(self, *args, out: Path, **kwargs) -> None:
        self.out = out
        super().__init__(*args, **kwargs)

    def _poll(self) -> None:
        super()._poll()
        if not self.closed and len(self.lines) >= 2 and self.partial and len(self.partial[1]) > 2:
            screenshot(self.root, self.out)
            print("saved", self.out)
            self.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=ROOT / "docs" / "images" / "overlay.png")
    cli = p.parse_args()
    cli.out.parent.mkdir(parents=True, exist_ok=True)
    demo = Path(tempfile.gettempdir()) / "jsc_overlay_demo.wav"
    build_demo_wav(demo)

    args = argparse.Namespace(source="file", file=str(demo), realtime=True, device=None,
                              model="kotoba-tech/kotoba-whisper-v2.0-faster", device_type="auto",
                              furigana="graded", end_silence=0.6, no_partial=False)
    events: queue.Queue = queue.Queue()
    stop = threading.Event()
    threading.Thread(target=worker, args=(args, events, stop), daemon=True).start()
    window = CapturingWindow(events, history=2, alpha=1.0, out=cli.out.resolve())
    window.run()
    stop.set()


if __name__ == "__main__":
    main()
