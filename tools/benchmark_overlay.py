"""Hidden Tk Canvas benchmark; synthetic text only, no API/audio/screenshots.

Run: python tools/benchmark_overlay.py --iterations 20
Iterations INCLUDE the first warm-up. Raw/all-round and post-warm-up stats are saved.
Baseline and current windows run sequentially in isolated child processes.
Use --show-window only for an explicit Windows click-through passive preview.
Hidden Canvas completion does not measure visible display latency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import queue
import statistics
import subprocess
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from jsc_live.furigana import Segment  # noqa: E402
from test_window import create_test_window  # noqa: E402

SOURCE = "app/jsc_live/overlay.py"
OUTPUT = ROOT / "logs/ui-iterations"
PARAMETERS = {"font_size": 16, "width": 900, "history": 5, "alpha": 0.88, "mode": "full"}
SUBTITLE = [("今日", "きょう"), ("は", None), ("会議", "かいぎ"), ("の", None), ("準備", "じゅんび"), ("をしましょう。", None)]
REPLIES = [("丁寧", "資料を確認してからお返事します。", "我确认资料后回复您。", True, False),
           ("カジュアル", "資料を確認してから返事するね。", "我确认资料后回复你。", False, True),
           ("かんたん", "少し確認します。", "我确认一下。", False, False)]


def stats(values: list[float]) -> dict:
    ordered = sorted(values)
    return {"n": len(ordered), "p50_ms": statistics.median(ordered) if ordered else None,
            "p95_ms": ordered[math.ceil(len(ordered) * .95) - 1] if ordered else None,
            "max_ms": max(ordered) if ordered else None}


def overlay_source(variant: str) -> bytes:
    if variant == "current":
        return (ROOT / SOURCE).read_bytes()
    return subprocess.run(["git", "-c", "safe.directory=" + ROOT.as_posix(), "show", "2fa3014:" + SOURCE],
                          cwd=ROOT, check=True, capture_output=True).stdout


def payload(queued_at: float) -> dict:
    return {"state": "done", "queued_at": queued_at, "error": None, "reason": "合成测试：先确认再回复。",
            "replies": [{"style": style, "segments": [Segment(text)], "meaning": meaning,
                         "star": star, "warn": warn} for style, text, meaning, star, warn in REPLIES]}


def worker(variant: str, iterations: int, directory: Path, *, show=False) -> int:
    rows, events, window = [], queue.Queue(), None
    result = {"variant": variant, "baseline_revision": "2fa3014", "iterations_including_warmup": iterations,
              "parameters": PARAMETERS, "fixture": {"subtitle": SUBTITLE, "replies": REPLIES}, "rounds": rows,
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "test_window_sha256": hashlib.sha256((ROOT / "tools/test_window.py").read_bytes()).hexdigest(),
              "window_mode": "passive" if show else "hidden",
              "measurement": "done event enqueued -> on_suggestion_drawn callback; includes poll wait and Tk Canvas idle work; "
                             + ("passive visible window, not a pixel presentation timestamp" if show else
                                "window remains withdrawn; NOT visible display latency"),
              "completed": False, "error": None}
    try:
        source = overlay_source(variant)
        result["source_sha256"] = hashlib.sha256(source).hexdigest()
        module = types.ModuleType("jsc_live._overlay_benchmark_" + variant)
        module.__file__, module.__package__ = str(ROOT / SOURCE), "jsc_live"
        sys.modules[module.__name__] = module
        exec(compile(source, module.__file__, "exec"), module.__dict__)

        def expire(uid):
            if not window.closed and len(rows) < uid:
                result["error"] = "No draw callback within 10 seconds for round " + str(uid)
                window.close()

        def enqueue():
            uid = len(rows) + 1
            for index in range(5):
                events.put(("final", uid * 10 + index, [Segment(*part) for part in SUBTITLE]))
            events.put(("suggest", uid, {"state": "pending"}))
            done = payload(0.0)
            done["queued_at"] = time.perf_counter()
            events.put(("suggest", uid, done))
            window.root.after(10000, lambda: expire(uid))

        def drawn(uid, done, timed_out):
            elapsed = (time.perf_counter() - done["queued_at"]) * 1000
            rows.append({"round": uid, "warmup": uid == 1, "elapsed_ms": round(elapsed, 3), "timed_out": timed_out})
            if len(rows) == iterations:
                result["completed"] = True
                window.root.after_idle(window.close)
            else:
                window.root.after(100, enqueue)

        window = create_test_window(module.SubtitleWindow, events, on_suggestion_drawn=drawn, show=show, **PARAMETERS)
        result["test_window"] = window.test_window
        result["display"] = {"font_family": window.family, "actual_width": window.width,
                             "screen": [window.root.winfo_screenwidth(), window.root.winfo_screenheight()],
                             "tk_scaling": float(window.root.tk.call("tk", "scaling")),
                             "tk_version": window.root.tk.call("info", "patchlevel"),
                             "show_meanings": getattr(window, "show_meanings", True)}
        window.root.after(200, enqueue)
        window.run()
        if not result["completed"] and result["error"] is None:
            result["error"] = "Window closed before all rounds completed"
    except Exception as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        if window is not None and not window.closed:
            window.close()
        result["all_rounds"] = stats([row["elapsed_ms"] for row in rows])
        result["scored_after_warmup"] = stats([row["elapsed_ms"] for row in rows if not row["warmup"]])
        result["warmup"] = rows[0] if rows else None
        (directory / (variant + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result["completed"] else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, choices=range(1, 51), default=20, metavar="1-50")
    parser.add_argument("--show-window", action="store_true", help="explicit Windows passive preview; default stays hidden")
    parser.add_argument("--worker", choices=("baseline", "current"), help=argparse.SUPPRESS)
    parser.add_argument("--run-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if args.run_dir is None or not args.run_dir.resolve().is_relative_to(OUTPUT.resolve()):
            parser.error("worker output must be under logs/ui-iterations")
        return worker(args.worker, args.iterations, args.run_dir, show=args.show_window)
    timestamp = datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d-%H%M%S-%f")
    directory = OUTPUT / ("benchmark-" + timestamp)
    directory.mkdir(parents=True, exist_ok=False)
    summary = {"iterations_including_warmup": args.iterations, "window_mode": "passive" if args.show_window else "hidden",
               "order": ["baseline", "current"], "variants": {}}
    for variant in summary["order"]:
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", variant,
                   "--iterations", str(args.iterations), "--run-dir", str(directory)]
        if args.show_window:
            command.append("--show-window")
        try:
            child = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=args.iterations * 12 + 20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            path = directory / (variant + ".json")
            summary["variants"][variant] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"error": child.stderr}
        except subprocess.TimeoutExpired:
            summary["variants"][variant] = {"error": "Worker process timed out and was terminated"}
    (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(directory / "summary.json"), flush=True)
    return 0 if all(row.get("completed") for row in summary["variants"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
