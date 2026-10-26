"""Capture a fixed, synthetic subtitle window without API calls or audio.

    python tools/capture_ui_iteration.py --show-window --label before

Only the app's opaque window is captured. Each run gets a new directory under
logs/ui-iterations/20261026-readability/. A locked desktop or capture failure is
recorded, not worked around. The window closes automatically within five seconds.
Without --show-window this tool prints instructions and never creates a window.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import queue
import re
import sys
import time
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools"))
from test_window import create_test_window  # noqa: E402

TOKYO = timezone(timedelta(hours=9))
OUTPUT = ROOT / "logs/ui-iterations/20261026-readability"
SUBTITLES = (
    "お疲れ様です。今日はもう上がりですか？",
    "日本に来てどれくらいですか？",
    "ご出身はどちらですか？",
)
REPLIES = (
    {"style": "丁寧", "star": True, "warn": False,
     "text": "【出身地】の出身です。よろしくお願いします。",
     "meaning": "我来自【出身地】。很高兴认识你，具体地点请先换成自己的真实信息；这段较长的中文专门用于检查窄窗口里是否完整显示。"},
    {"style": "カジュアル", "star": False, "warn": True,
     "text": "【出身地】だよ。よろしくね。", "meaning": "我来自【出身地】，多多关照。"},
    {"style": "かんたん", "star": False, "warn": False,
     "text": "【出身地】です。", "meaning": "来自【出身地】。"},
)
REASON = (
    "对方用了「どちらですか」询问出身，用丁寧回复；【出身地】要先换成自己的真实信息。"
    "这是一段刻意加长的合成说明，用来检查理由能否在窗口内换行，以及中文内容较多时是否挤占日语正文的阅读空间。"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canvas_metrics(win) -> dict:
    canvas = win.canvas
    width, height = canvas.winfo_width(), canvas.winfo_height()
    overflow = []
    text_count = 0
    for item in canvas.find_all():
        kind = canvas.type(item)
        text_count += int(kind == "text")
        bounds = canvas.bbox(item)
        if bounds and (bounds[0] < 0 or bounds[1] < 0 or bounds[2] > width or bounds[3] > height):
            detail = {"id": item, "kind": kind, "bounds": list(bounds), "tags": list(canvas.gettags(item))}
            if kind == "text":
                detail["text"] = canvas.itemcget(item, "text")
            overflow.append(detail)
    return {"width": width, "height": height, "item_count": len(canvas.find_all()),
            "text_item_count": text_count, "overflow_count": len(overflow),
            "text_overflow_count": sum(item["kind"] == "text" for item in overflow),
            "overflow_items": overflow}


def owned_window_at_points(rect: tuple[int, int, int, int]) -> bool:
    """Check that the capture rectangle is visibly occupied by this process."""
    user32 = ctypes.windll.user32
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    x, y, width, height = rect
    for dx, dy in ((2, 2), (width - 3, 2), (2, height - 3),
                   (width - 3, height - 3), (width // 2, height // 2)):
        hwnd = user32.WindowFromPoint(wintypes.POINT(x + dx, y + dy))
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not hwnd or pid.value != os.getpid():
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="before")
    parser.add_argument("--font-size", type=int, default=16)
    parser.add_argument("--show-window", action="store_true", help="explicit Windows passive preview required for capture")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", args.label):
        parser.error("--label must contain only ASCII letters, digits, underscores or hyphens")
    if not 12 <= args.font_size <= 72:
        parser.error("--font-size must be between 12 and 72 points")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not args.show_window:
        print("No window opened. Capture requires explicit --show-window (Windows click-through, non-activating preview).")
        return 0
    started = datetime.now(TOKYO)
    out = OUTPUT / f"{args.label}-{started:%Y%m%d-%H%M%S-%f}"
    out.mkdir(parents=True, exist_ok=False)
    metrics = {
        "schema_version": 1, "label": args.label, "started_at": started.isoformat(),
        "input_kind": "fixed synthetic text; no audio, API, personal profile, or private session data",
        "input": {"subtitles": list(SUBTITLES), "replies": list(REPLIES), "reason": REASON},
        "source_sha256": {"overlay.py": sha256(ROOT / "app/jsc_live/overlay.py"),
                          "test_window.py": sha256(ROOT / "tools/test_window.py"),
                          "capture_ui_iteration.py": sha256(Path(__file__))},
        "requested": {"alpha": 1.0, "font_size_pt": args.font_size, "width": 900, "history": 3},
        "operation_definition": "programmatic pointer events through the app's existing canvas binding; no direct mode setter",
        "states": [], "operations": [], "failure": None,
    }
    win = None
    wall_start = time.perf_counter()
    try:
        import screen_capture as sc
        from jsc_live.furigana import Furigana
        from jsc_live.overlay import SubtitleWindow

        events = queue.Queue()
        furigana = Furigana("graded")
        win = create_test_window(SubtitleWindow, events, font_size=args.font_size, width=900, history=3, alpha=1.0,
                                 history_window=None, mode="full", show=True)
        metrics["test_window"] = win.test_window
        root = win.root
        root.title("jsc-live synthetic UI capture")
        win.status = "合成输入 · 界面检查"
        for index, sentence in enumerate(SUBTITLES, start=1):
            events.put(("final", index, furigana.annotate(sentence)))
        replies = [{**{key: value for key, value in reply.items() if key != "text"},
                    "segments": furigana.annotate(reply["text"])} for reply in REPLIES]
        events.put(("suggest", 3, {"state": "pending"}))
        events.put(("suggest", 3, {"state": "done", "replies": replies,
                                   "reason": REASON, "error": None, "queued_at": time.perf_counter()}))
        root.update_idletasks()
        metrics["screen"] = {"width": root.winfo_screenwidth(), "height": root.winfo_screenheight(),
                             "tk_scaling": float(root.tk.call("tk", "scaling")),
                             "pixels_per_inch": float(root.winfo_fpixels("1i"))}
        metrics["fonts"] = {
            name: {"actual": getattr(win, name).actual(), "metrics": getattr(win, name).metrics()}
            for name in ("base_font", "ruby_font", "sugg_font", "sugg_star_font", "sugg_ruby_font", "meaning_font", "toolbar_font")
            if hasattr(win, name)
        }

        def fail(code: str, exc: Exception | None = None) -> None:
            if metrics["failure"] is None:
                metrics["failure"] = {"code": code, "error_type": type(exc).__name__ if exc else None,
                                      "message": str(exc) if exc else None}
            if not win.closed:
                win.close()

        def capture(mode: str, state_name: str | None = None) -> None:
            try:
                root.update_idletasks()
                if not root.winfo_viewable() or win.mode != mode:
                    raise RuntimeError("The requested app state is not visible")
                bounds = (root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height())
                x, y, width, height = bounds
                sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
                state = {"mode": mode, "name": state_name or mode,
                         "meanings_visible": getattr(win, "show_meanings", True),
                         "bounds": list(bounds), "alpha": float(root.attributes("-alpha")),
                         "screen_area_fraction": width * height / (sw * sh),
                         "screen_height_fraction": height / sh, "canvas": canvas_metrics(win),
                         "operation_steps_from_initial": len(metrics["operations"]),
                         "captured": False}
                metrics["states"].append(state)
                if x < 0 or y < 0 or x + width > sw or y + height > sh:
                    raise RuntimeError("App window is partly outside the primary screen; no screenshot saved")
                if state["alpha"] != 1.0:
                    raise RuntimeError("App window is not opaque; no screenshot saved")
                if not owned_window_at_points(bounds):
                    raise RuntimeError("Capture ownership check failed (passive click-through may hit the underlying window); "
                                       "no screenshot saved and interactive mode will not be enabled")
                rgb = sc.grab(*bounds)
                if rgb.size == 0 or int(rgb.max()) == int(rgb.min()):
                    raise RuntimeError("Capture returned an empty or flat image; no screenshot saved")
                path = out / f"{args.label}-{state_name or mode}.png"
                if path.exists():
                    raise FileExistsError("Screenshot already exists; refusing to overwrite")
                sc.save_png(path, rgb)
                state["captured"] = True
                state["image"] = path.name
                state["image_sha256"] = sha256(path)
                state["captured_after_ms"] = round((time.perf_counter() - wall_start) * 1000, 1)
            except Exception as exc:
                fail("capture_failed", exc)

        def click_button(name: str, expected_mode: str, expected_meanings: bool | None = None) -> None:
            try:
                items = win.canvas.find_withtag(f"btn:{name}")
                rectangles = [item for item in items if win.canvas.type(item) == "rectangle"]
                if len(rectangles) != 1:
                    raise RuntimeError("Toolbar target was not found uniquely")
                x0, y0, x1, y1 = win.canvas.coords(rectangles[0])
                x, y = round((x0 + x1) / 2), round((y0 + y1) / 2)
                win.canvas.event_generate("<ButtonPress-1>", x=x, y=y)
                win.canvas.event_generate("<ButtonRelease-1>", x=x, y=y)
                root.update_idletasks()
                if win.mode != expected_mode or (expected_meanings is not None and win.show_meanings != expected_meanings):
                    raise RuntimeError("Click did not produce the requested state")
                metrics["operations"].append({"action": f"click {name} toolbar button", "clicks": 1,
                                               "canvas_point": [x, y], "result_mode": win.mode,
                                               "meanings_visible": getattr(win, "show_meanings", True)})
            except Exception as exc:
                fail("operation_failed", exc)

        root.after(450, lambda: capture("full"))
        if hasattr(win, "show_meanings"):
            metrics["expected_states"] = 4
            root.after(850, lambda: click_button("meanings", "full", False))
            root.after(1150, lambda: capture("full", "chinese-hidden"))
            root.after(1450, lambda: click_button("meanings", "full", True))
            root.after(1750, lambda: capture("full", "chinese-restored"))
            root.after(2050, lambda: click_button("collapsed", "collapsed"))
            root.after(2350, lambda: capture("collapsed"))
            root.after(2600, win.close)
        else:
            metrics["expected_states"] = 2
            root.after(850, lambda: click_button("collapsed", "collapsed"))
            root.after(1250, lambda: capture("collapsed"))
            root.after(1500, win.close)
        root.after(4500, lambda: fail("window_deadline"))
        win.run()
    except Exception as exc:
        metrics["failure"] = {"code": "setup_failed", "error_type": type(exc).__name__, "message": str(exc)}
    finally:
        if win is not None and not win.closed:
            win.close()
        metrics["finished_at"] = datetime.now(TOKYO).isoformat()
        metrics["elapsed_ms"] = round((time.perf_counter() - wall_start) * 1000, 1)
        metrics["success"] = metrics["failure"] is None and len(metrics["states"]) == metrics.get("expected_states", 2) and all(
            state["captured"] for state in metrics["states"])
        if not metrics["success"] and metrics["failure"] is None:
            metrics["failure"] = {"code": "closed_before_both_captures", "error_type": None, "message": None}
        with (out / "metrics.json").open("x", encoding="utf-8") as handle:
            json.dump(metrics, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print(json.dumps({"success": metrics["success"], "output": str(out), "failure": metrics["failure"],
                      "states": [{key: state[key] for key in ("mode", "bounds", "captured")}
                                 for state in metrics["states"]]}, ensure_ascii=False), flush=True)
    return 0 if metrics["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
