"""How tall does the subtitle window get with long suggestions? (D-039, no API, no audio)

    python tools/measure_overlay_density.py

Renders the real SubtitleWindow in a withdrawn Tk root (nothing appears on screen) with
three subtitle lines plus a saved, public suggestion, in the glance view (default) and the
details view (the previous full layout), at 900 and 650 px wide. Payloads:
- meal  : the longest fictional document answer of D-037 (tests/live_suggest/document-answers-final-*.json)
- shift : the long refusal from the TTS dialogue run of D-038/D-039 (tests/live_suggest/e2e-dialogue-*.jsonl)
Prints the window heights; writes tests/live_suggest/overlay-density-<time>.json.
"""

from __future__ import annotations

import glob
import json
import queue
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools"))

from jsc_live.furigana import Furigana  # noqa: E402
from jsc_live.overlay import SubtitleWindow  # noqa: E402
from test_window import create_test_window  # noqa: E402

SUBTITLES = ["お疲れ様です。", "今日の授業の資料、もう見ましたか？", "昼ご飯は用意してもらえますか？どこで食べられますか？"]


def payloads() -> dict:
    meal = None
    for f in sorted(glob.glob(str(ROOT / "tests/live_suggest/document-answers-final-*.json"))):
        for r in json.load(open(f, encoding="utf-8"))["results"]:
            if r["case"]["id"] == "meal":
                meal = r
    shift = None
    for f in sorted(glob.glob(str(ROOT / "tests/live_suggest/e2e-dialogue-20261028-*.jsonl"))):
        for line in open(f, encoding="utf-8"):
            r = json.loads(line)
            if "明日のシフト" in r.get("text", "") and r.get("replies"):
                shift = r
    return {"meal": meal, "shift": shift}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    furigana = Furigana()
    results = []
    for name, row in payloads().items():
        if row is None:
            print("missing payload", name)
            continue
        replies = [{"style": x["style"], "star": x["star"], "warn": x.get("warn", False), "meaning": x.get("meaning", ""),
                    "segments": furigana.annotate(x["text"])} for x in row["replies"]]
        for width in (900, 650):
            window = create_test_window(SubtitleWindow, queue.Queue(), width=width, alpha=1.0)
            try:
                for i, text in enumerate(SUBTITLES, 1):
                    window.lines.append((i, furigana.annotate(text), "#f5f5f5"))
                window.suggestion = {"uid": 9, "timed_out": False, "reported": True,
                                     "payload": {"state": "done", "replies": replies, "reason": row.get("reason", "")}}
                window.mode = "full"
                heights = {}
                for details in (True, False):
                    window.show_details = details
                    heights["details" if details else "glance"] = window._redraw(len(window.lines))
                window.show_details = False
                window.redraw()
                heights["glance_with_cap"] = int(window.canvas.cget("height"))
                screen = window.root.winfo_screenheight()
            finally:
                window.close()
            results.append({"payload": name, "width": width, **heights, "screen_height": screen})
            print(f"{name:5} {width}px  details {heights['details']}  glance {heights['glance']}  "
                  f"(with half-screen cap {heights['glance_with_cap']}, screen {screen})")
    out = ROOT / "tests" / "live_suggest" / f"overlay-density-{time.strftime('%Y%m%d-%H%M')}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", out.relative_to(ROOT))


if __name__ == "__main__":
    main()
