"""Capture the showcase media and numbers from one real run of the app on TTS audio.

    python tools/capture_showcase.py

Input: the 10 sentences of tests/sample_dialogues.md used in tools/e2e_suggest.py
(Windows TTS voice "Haruka", no real person, no personal data), played through the
app's own file input (--source file --realtime) with suggestions, the history window
and session recording on. profile.md is the placeholder copy of profile.example.md.

Outputs (docs/images/):
  live-subtitle-suggest.png  subtitle window once a suggestion with one ⭐ (preferably a ⚠) is drawn
  live-history-lookup.png    history window right after a word lookup has its meaning
  live-archive.png           the session folder opened after closing (as jsc-live-录音.bat does)
  live-demo.webm             ~25 s from 「聞き取り中」 on, VP9, 10 fps, no sound
and tests/live_suggest/showcase-<time>.json with the measured numbers.

A plain dark backdrop window is put behind the subtitle window while recording, so
nothing else on the desktop is captured; the subtitle window itself is unchanged.
"""

from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools"))

import e2e_suggest  # noqa: E402
import screen_capture as sc  # noqa: E402
from jsc_live import audio as jaudio  # noqa: E402
from jsc_live import main as jmain  # noqa: E402
from jsc_live import overlay  # noqa: E402

IMAGES = ROOT / "docs" / "images"
OUT_NUMBERS = ROOT / "tests" / "live_suggest"
VIDEO_SECONDS = 26
FPS = 10
BACKDROP = "#0d0f12"
LOOKUP_WORD = "可能"   # from 「もし可能だったら」 in sentence 7

marks: dict = {"finals": []}


def save(name: str, rgb) -> None:
    sc.save_png(IMAGES / name, rgb)
    print(f"saved {name} {rgb.shape[1]}x{rgb.shape[0]} {(IMAGES / name).stat().st_size // 1024} KB", flush=True)


# --- hooks into the real app ------------------------------------------------------
_orig_blocks = jaudio.WavFileSource.blocks


def blocks(self):
    marks["audio_start"] = time.perf_counter()
    yield from _orig_blocks(self)


jaudio.WavFileSource.blocks = blocks
_orig_poll = overlay.SubtitleWindow._poll


def poll(self):
    before = len(self.lines)
    _orig_poll(self)
    if len(self.lines) > before:
        marks["finals"].append(time.perf_counter())


overlay.SubtitleWindow._poll = poll
_orig_run = overlay.SubtitleWindow.run


def run(self):
    import tkinter as tk

    win = self
    root = self.root
    frames: list = []
    state = {"recording": False, "video_done": False, "score": -1, "lookup": False}

    # Backdrop under the subtitle window (not topmost), covering the area the window can grow into.
    cap_h = 760  # tall enough for 5 subtitle lines + 3 suggestions
    back = tk.Toplevel(root)
    back.overrideredirect(True)
    back.configure(bg=BACKDROP)
    back.geometry(f"{win.width + 40}x{cap_h + 40}+{win.left - 20}+{win.bottom - cap_h - 20}")
    back.lower(root)

    orig_drawn = self.on_suggestion_drawn

    def drawn(uid, payload, timed_out):
        orig_drawn(uid, payload, timed_out)
        replies = payload.get("replies", [])
        good = payload["state"] == "done" and len(replies) == 3 and sum(r["star"] for r in replies) == 1
        if good:
            # Prefer a ⚠ on カジュアル, then furigana in the subtitles, then more subtitle lines.
            has_warn = any(r["warn"] for r in replies)
            has_ruby = any(seg.ruby for _, segs, _ in win.lines for seg in segs)
            score = 4 * has_warn + 2 * has_ruby + min(len(win.lines), 3) / 10
            if score > state["score"]:
                root.update()
                x, y, w, h = root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()
                save("live-subtitle-suggest.png", sc.grab(x, y, w, h))
                state["score"] = score
                marks["suggest_shot"] = {"warn": has_warn, "ruby": has_ruby, "lines": len(win.lines)}

    self.on_suggestion_drawn = drawn

    def video_tick():
        if not state["recording"] and win.status.startswith("聞き取り中"):
            state["recording"] = True
            marks["video_start"] = time.perf_counter()
        if state["recording"] and not state["video_done"]:
            frames.append(sc.grab(win.left, win.bottom - cap_h, win.width, cap_h))
            if len(frames) >= VIDEO_SECONDS * FPS:
                state["video_done"] = True
                threading.Thread(target=write_video, args=(frames,), daemon=True).start()
        if not win.closed:
            root.after(int(1000 / FPS), video_tick)

    def finish_tick():
        worker_done = marks.get("worker_done")
        manager = marks["holder"].get("manager")
        if worker_done and manager and manager.idle and state["video_done"] and not state["lookup"]:
            state["lookup"] = True
            lookup_word()
        if not win.closed:
            root.after(500, finish_tick)

    def lookup_word():
        hw = win.history_window
        text = hw.text
        idx = text.search(LOOKUP_WORD, "1.0")
        if not idx:
            print("lookup word not found in history", flush=True)
            win.close()
            return
        text.tag_remove("sel", "1.0", "end")
        text.tag_add("sel", idx, f"{idx}+{len(LOOKUP_WORD)}c")
        hw.top.lift()
        hw._on_select()

        def wait_meaning(tries=0):
            label = hw.panel.cget("text")
            if "查询中" not in label or tries > 40:
                root.update()
                frame = int(hw.top.wm_frame(), 16)  # the Toplevel's outer window, with its title bar
                save("live-history-lookup.png", sc.grab(*sc.hwnd_rect(frame)))
                marks["lookup_panel"] = label
                root.after(500, win.close)
            else:
                root.after(250, lambda: wait_meaning(tries + 1))

        root.after(300, wait_meaning)

    root.after(100, video_tick)
    root.after(500, finish_tick)
    _orig_run(self)


overlay.SubtitleWindow.run = run
_orig_init = overlay.SubtitleWindow.__init__


def init(self, *args, **kwargs):
    kwargs["mode"] = "full"
    _orig_init(self, *args, **kwargs)


overlay.SubtitleWindow.__init__ = init
_orig_worker = jmain.worker


def worker(args, events, stop, suggester=None, holder=None):
    marks["holder"] = holder
    try:
        _orig_worker(args, events, stop, suggester, holder)
    finally:
        marks["worker_done"] = time.perf_counter()


jmain.worker = worker


def write_video(frames) -> None:
    import av

    h, w, _ = frames[0].shape
    w2, h2 = w - w % 2, h - h % 2
    path = IMAGES / "live-demo.webm"
    with av.open(str(path), "w", format="webm") as container:
        stream = container.add_stream("libvpx-vp9", rate=FPS)
        stream.width, stream.height, stream.pix_fmt = w2, h2, "yuv420p"
        stream.options = {"crf": "36", "b:v": "0", "deadline": "good", "cpu-used": "4"}
        for rgb in frames:
            frame = av.VideoFrame.from_ndarray(rgb[:h2, :w2], format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    print(f"saved live-demo.webm {w2}x{h2} {len(frames) / FPS:.0f} s {path.stat().st_size // 1024} KB", flush=True)


def pct(values, q):
    values = sorted(values)
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    IMAGES.mkdir(parents=True, exist_ok=True)
    profile = ROOT / "profile.md"
    if profile.exists():
        sys.exit("profile.md exists: move it away first (the showcase must use the placeholder profile)")
    shutil.copy(ROOT / "profile.example.md", profile)
    turns = e2e_suggest.SCENARIOS["dialogue"]
    # The file name is shown in the window (「聞き取り中：<path>」), so keep it neutral and inside the project.
    demo = ROOT / "logs" / "TTS_sample_dialogues.wav"
    demo.parent.mkdir(exist_ok=True)
    clips = e2e_suggest.synthesize(turns, "Haruka")
    e2e_suggest.build_wav(clips, 3.5, 0.75, demo)
    first_speech_end = 1.0 + len(e2e_suggest.trim_silence(jaudio.WavFileSource(str(clips[0][0]), tail_silence=0).load())) / 16000
    try:
        jmain.main(["--source", "file", "--file", str(demo), "--realtime", "--suggest"])
    finally:
        profile.unlink(missing_ok=True)

    session = marks["holder"]["session"]
    import capture_archive

    capture_archive.capture(session.dir, IMAGES / "live-archive.png")
    rows = [json.loads(l) for l in session.suggestions_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    done = [r for r in rows if r.get("status") == "done" or str(r.get("status", "")).startswith("late")]

    def stats(key):
        vals = [r[key] for r in done if isinstance(r.get(key), (int, float))]
        return {"n": len(vals), "p50_ms": round(pct(vals, 0.5)), "p95_ms": round(pct(vals, 0.95))} if vals else None

    from jsc_live.asr import DEFAULT_MODEL
    from jsc_live.suggest import load_env

    gpu = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True).stdout.strip()
    numbers = {
        "captured": time.strftime("%Y-%m-%d %H:%M"),
        "input": "TTS (Windows Haruka), tests/sample_dialogues.md 10 sentences, --source file --realtime; no personal data",
        "asr_model": DEFAULT_MODEL,
        "api_model": load_env().get("LLM_MODEL"),
        "gpu": gpu,
        "sentences_shown": session.sentences,
        "suggestion_screenshot": marks.get("suggest_shot"),
        "suggestion_turns": len(rows),
        "suggestions_done": len(done),
        "asr": stats("asr_ms"),
        "api_round_trip": stats("api_ms"),
        "api_first_token": stats("first_token_ms"),
        "render": stats("render_ms"),
        "last_subtitle_to_suggestion": stats("shown_after_ms"),
        "first_subtitle_after_speech_end_ms": round((marks["finals"][0] - marks["audio_start"] - first_speech_end) * 1000),
        "lookup_panel": marks.get("lookup_panel"),
        "session_folder": session.dir.name,
    }
    OUT_NUMBERS.mkdir(parents=True, exist_ok=True)
    out = OUT_NUMBERS / f"showcase-{time.strftime('%Y%m%d-%H%M')}.json"
    out.write_text(json.dumps(numbers, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(numbers, ensure_ascii=False, indent=1))
    print("numbers:", out)


if __name__ == "__main__":
    main()
