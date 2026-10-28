"""Showcase media of the current version (D-041), rendered offscreen from public TTS runs.

    python tools/capture_latest_showcase.py --stills
    python tools/capture_latest_showcase.py --video        # sends ~10 API requests

--stills  renders saved results of the current prompt through the real SubtitleWindow:
          showcase-conversation.png  newest tests/live_suggest/e2e-dialogue-*.jsonl, a turn whose
                                     reason offers the other answer (glance view, ⭐ conclusion bold)
          showcase-document.png      the "meal" answer of the newest document-answers-conclusion-final-*.json
--video   plays the 10 TTS sentences of tests/sample_dialogues.md through the live path
          (file input, real ASR, real API, empty materials folder, no recording) with the window
          placed beyond every monitor, and records only this process's window: showcase-demo.webm.

Never captures the desktop, never reads profile.md or the user's materials, never overwrites.
Output: docs/images/ (new files only).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "tools")]

from jsc_live.furigana import Furigana  # noqa: E402
from jsc_live.overlay import SubtitleWindow  # noqa: E402
from test_window import create_test_window  # noqa: E402
from window_capture import capture_owned_window, grab_owned_window  # noqa: E402

IMAGES = ROOT / "docs" / "images"
FPS = 8
VIDEO_SECONDS = 28


def newest(pattern: str) -> Path:
    files = sorted(glob.glob(str(ROOT / pattern)), key=os.path.getmtime)
    if not files:
        raise SystemExit(f"no file matches {pattern}")
    return Path(files[-1])


def payload_from(replies: list[dict], reason: str, furigana: Furigana) -> dict:
    return {"state": "done", "reason": reason, "replies": [
        {"style": x["style"], "star": x["star"], "warn": x.get("warn", False), "meaning": x.get("meaning", ""),
         "segments": furigana.annotate(x["text"])} for x in replies]}


def render(subtitles: list[str], payload: dict, out: Path, width: int = 900) -> dict:
    furigana = Furigana()
    window = create_test_window(SubtitleWindow, queue.Queue(), width=width, alpha=1.0, offscreen=True)
    try:
        for i, text in enumerate(subtitles, 1):
            window.lines.append((i, furigana.annotate(text), "#f5f5f5"))
        window.suggestion = {"uid": 99, "timed_out": False, "reported": True, "payload": payload}
        window.mode = "full"
        window.show_details = False
        window.redraw()
        window.root.update()
        return capture_owned_window(window.root, out)
    finally:
        window.close()


def stills() -> None:
    furigana = Furigana()
    log = newest("tests/live_suggest/e2e-dialogue-*.jsonl")
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    # Prefer a turn with a ⚠ and a reason that offers the other answer as ready Japanese.
    pick = next((i for i, r in enumerate(rows) if any(x.get("warn") for x in r["replies"]) and "可说「" in r["reason"]),
                next(i for i, r in enumerate(rows) if "可说「" in r["reason"]))
    subtitles = [s for r in rows[max(0, pick - 1):pick + 1] for s in r.get("sentences", [r["text"]])]
    meta = render(subtitles, payload_from(rows[pick]["replies"], rows[pick]["reason"], furigana),
                  IMAGES / "showcase-conversation.png")
    print("showcase-conversation.png", meta, "from", log.name, "turn", pick + 1)

    doc = newest("tests/live_suggest/document-answers-conclusion-final-*.json")
    meal = next(r for r in json.loads(doc.read_text(encoding="utf-8"))["results"] if r["case"]["id"] == "meal")
    meta = render([meal["case"]["query"]], payload_from(meal["replies"], meal["reason"], furigana),
                  IMAGES / "showcase-document.png")
    print("showcase-document.png", meta, "from", doc.name)


def video() -> None:
    import av
    import numpy as np

    import e2e_suggest
    from jsc_live.asr import DEFAULT_MODEL
    from jsc_live.main import worker
    from jsc_live.materials import MaterialsStore
    from jsc_live.suggest import Suggester

    # The path is shown in the window (「聞き取り中：<path>」): keep it neutral and inside the project,
    # never in the user's temp folder (which contains the user name).
    demo = ROOT / "logs" / "TTS_sample_dialogues.wav"
    demo.parent.mkdir(exist_ok=True)
    e2e_suggest.build_wav(e2e_suggest.synthesize(e2e_suggest.SCENARIOS["dialogue"], "Haruka"), 3.5, 0.75, demo)
    suggester = Suggester(materials=MaterialsStore(root=tempfile.mkdtemp(prefix="jsc-empty-materials-")))
    args = argparse.Namespace(source="file", file=str(demo), realtime=True, device=None, model=DEFAULT_MODEL,
                              device_type="auto", furigana="graded", end_silence=0.6, no_partial=False,
                              turn_gap=0.8, no_record=True)
    events: queue.Queue = queue.Queue()
    stop = threading.Event()
    holder: dict = {}

    def drawn(uid, payload, timed_out):
        if holder.get("manager"):
            holder["manager"].rendered(uid, payload, timed_out)

    window = create_test_window(SubtitleWindow, events, width=900, alpha=1.0, offscreen=True,
                                on_suggestion_drawn=drawn)
    thread = threading.Thread(target=worker, args=(args, events, stop, suggester, holder), daemon=True)
    frames: list = []
    state = {"start": None}

    def tick():
        if window.closed:
            return
        if state["start"] is None and window.status.startswith("聞き取り中"):
            state["start"] = time.perf_counter()
        if state["start"] is not None:
            frames.append(grab_owned_window(window.root))
            if time.perf_counter() - state["start"] >= VIDEO_SECONDS:
                window.close()
                return
        window.root.after(int(1000 / FPS), tick)

    thread.start()
    window.root.after(100, tick)
    try:
        window.run()
    finally:
        stop.set()
        if not window.closed:
            window.close()
        thread.join(timeout=5)
    if not frames:
        raise SystemExit("no frames recorded")
    # The window grows upwards as text arrives: pad every frame to the tallest one, bottom-aligned.
    width = max(f.shape[1] for f in frames)
    height = max(f.shape[0] for f in frames)
    width, height = width - width % 2, height - height % 2
    background = np.array([0x11, 0x14, 0x18], np.uint8)
    out = IMAGES / "showcase-demo.webm"
    if out.exists():
        raise SystemExit(f"{out} exists; not overwriting")
    with av.open(str(out), "w", format="webm") as container:
        stream = container.add_stream("libvpx-vp9", rate=FPS)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        stream.options = {"crf": "36", "b:v": "0", "deadline": "good", "cpu-used": "4"}
        for f in frames:
            canvas = np.empty((height, width, 3), np.uint8)
            canvas[:] = background
            h, w = min(f.shape[0], height), min(f.shape[1], width)
            canvas[height - h:, :w] = f[f.shape[0] - h:, :w]
            for packet in stream.encode(av.VideoFrame.from_ndarray(canvas, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    print(f"showcase-demo.webm {width}x{height} {len(frames) / FPS:.0f} s {out.stat().st_size // 1024} KB")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--stills", action="store_true")
    p.add_argument("--video", action="store_true")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if not (a.stills or a.video):
        p.print_help()
        return
    # A process started from the foreground app inherits the right to take the foreground, so
    # the offscreen window could steal focus (test_window refuses to continue if it does).
    # Lock foreground changes for the duration of the capture instead of weakening that check.
    import ctypes

    user32 = ctypes.windll.user32
    user32.LockSetForegroundWindow(1)  # LSFW_LOCK
    try:
        if a.stills:
            stills()
        if a.video:
            video()
    finally:
        user32.LockSetForegroundWindow(2)  # LSFW_UNLOCK


if __name__ == "__main__":
    main()
