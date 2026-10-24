"""End-to-end check for live reply suggestions, driven by synthesized speech.

    python tools/e2e_suggest.py                        # 10 single-sentence turns (sample_dialogues.md)
    python tools/e2e_suggest.py --scenario monologue   # long turns cut into pieces, like a podcast

Sentences are synthesized with a built-in Windows voice, joined into one WAV
(short pauses inside a turn, long pauses between turns) and played at
real-time speed through the full app path:

    WAV -> Segmenter -> Transcriber -> subtitle -> SuggestionManager (API) -> window

Outputs (in tests/live_suggest/):
- e2e-<timestamp>.jsonl  per-sentence log: recognized text, replies, asr / api / render ms
- e2e-<timestamp>.md     the same as a table plus latency percentiles
- docs/images/live-suggest.png  screenshot of the window once suggestion number --shot-at is drawn
"""

from __future__ import annotations

import argparse
import json
import queue
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.audio import SAMPLE_RATE, WavFileSource  # noqa: E402
from jsc_live.main import worker  # noqa: E402
from jsc_live.overlay import SubtitleWindow  # noqa: E402
from jsc_live.suggest import Suggester  # noqa: E402

AUDIO_DIR = ROOT / "tests" / "asr_eval" / "audio"
OUT_DIR = ROOT / "tests" / "live_suggest"
# Each scenario is a list of turns; each turn is a list of (id, text) pieces.
SCENARIOS = {
    # From tests/sample_dialogues.md: shop clerk, workplace, friends. One sentence per turn.
    "dialogue": [[("t01", "ポイントカードはお持ちですか？")], [("t02", "お箸おつけしますか？")],
                 [("t03", "こちら温めますか？")], [("t05", "来週の飲み会、来られそうですか？")],
                 [("t07", "日本に来てどれくらいですか？")], [("t08", "お疲れ様です。今日はもう上がりですか？")],
                 [("t09", "すみません、明日のシフトなんですけど、急に一人休みが出ちゃって、もし可能だったら夕方から入ってもらえたりしませんか？")],
                 [("t11", "今日マジ寒くない？")], [("t12", "明日ひま？ご飯行かない？")], [("t13", "それ、めっちゃいいじゃん！")]],
    # Written for this test (no real recording): someone talking at length, with a
    # half sentence (m02) like the one that was answered on its own in the first live test.
    "monologue": [
        [("m01", "今回のテーマは、恥ずかしかった思い出です。"), ("m02", "そうですね、私"),
         ("m03", "今までを振り返ると、恥ずかしかった思い出がたくさんあるんです。"),
         ("m04", "今日はその中で、海外旅行の時の話をしたいと思います。"),
         ("m05", "皆さんは旅行先で恥ずかしい思いをしたことはありますか？")],
        [("m06", "七年前にチェコのプラハに行ったんですけど、"), ("m07", "レストランで注文するときに、"),
         ("m08", "間違えて同じ料理を三つも頼んじゃったんです。"), ("m09", "店員さんにすごく笑われました。")],
        [("m10", "お疲れ様です。"), ("m11", "明日の会議の資料、もうできましたか？")],
    ],
}


def synthesize(turns, voice: str) -> list[list[Path]]:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    out_turns = []
    for turn in turns:
        paths = []
        for sid, text in turn:
            out = AUDIO_DIR / f"{sid}_{voice}.wav"
            if not out.exists():
                subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                str(ROOT / "tools" / "tts_winrt.ps1"), "-Text", text, "-Voice", voice,
                                "-Out", str(out)], check=True)
            paths.append(out)
        out_turns.append(paths)
    return out_turns


def trim_silence(audio: np.ndarray, threshold_db: float = -45.0, margin: float = 0.05) -> np.ndarray:
    """Cut the silence TTS puts at both ends (~0.15 s before, ~0.8 s after) so the gaps are what we set."""
    frame = SAMPLE_RATE // 100
    n = len(audio) // frame
    level = 20 * np.log10(np.sqrt((audio[: n * frame].reshape(n, frame) ** 2).mean(axis=1)) + 1e-9)
    loud = np.where(level > threshold_db)[0]
    if len(loud) == 0:
        return audio
    pad = int(margin * SAMPLE_RATE)
    return audio[max(0, loud[0] * frame - pad): (loud[-1] + 1) * frame + pad]


def build_wav(turns: list[list[Path]], gap: float, inner_gap: float, path: Path) -> None:
    silence = np.zeros(int(gap * SAMPLE_RATE), np.float32)
    short = np.zeros(int(inner_gap * SAMPLE_RATE), np.float32)
    parts = [silence[: SAMPLE_RATE]]
    for clips in turns:
        for i, clip in enumerate(clips):
            speech = trim_silence(WavFileSource(str(clip), tail_silence=0).load())
            parts += [speech, short if i < len(clips) - 1 else silence]
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


def summarize(log_path: Path, md_path: Path, model: str, gap: float, scenario: str, inner_gap: float) -> None:
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows.sort(key=lambda r: r["utterance_id"])

    def fmt(v):
        return f"{v:.0f}" if isinstance(v, (int, float)) else "-"

    lines = [f"# 接话建议端到端测试（{time.strftime('%Y-%m-%d %H:%M')}）", "",
             f"- 场景：`{scenario}`；模型：`{model}`；语音：Windows TTS；一段话内停顿 {inner_gap} s，"
             f"段与段之间停顿 {gap} s；对象标记：无",
             f"- 原始日志：`{log_path.name}`（每段话一行 JSON，含模型原始输出）",
             f"- 建议请求次数：{len(rows)}；字幕句数：{sum(len(r.get('sentences', [1])) for r in rows)}", "",
             "| 段 | 这段话的字幕（按停顿切开） | ⭐ 建议 | 识别 ms | 等待停顿 ms | API ms（首字） | 渲染 ms | 最后一句字幕→建议 ms | 状态 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        star = next((f"{x['style']}：{x['text']}" for x in r.get("replies", []) if x.get("star")), "")
        if r.get("star_inferred"):
            star += "（⭐ 由程序补）"
        pieces = " ／ ".join(r.get("sentences", [r.get("text", "")]))
        lines.append(f"| {i} | {pieces} | {star or (r.get('error') or '')} | "
                     f"{fmt(r.get('asr_ms'))} | {fmt(r.get('turn_wait_ms'))} | "
                     f"{fmt(r.get('api_ms'))}（{fmt(r.get('first_token_ms'))}） | "
                     f"{fmt(r.get('render_ms'))} | {fmt(r.get('shown_after_ms'))} | {r.get('status')} |")
    lines.append("")
    for key, label in [("asr_ms", "识别"), ("turn_wait_ms", "等待停顿"), ("first_token_ms", "API 首字"), ("api_ms", "API 往返"),
                       ("render_ms", "渲染"), ("shown_after_ms", "字幕→建议显示")]:
        vals = sorted(r[key] for r in rows if isinstance(r.get(key), (int, float)))
        if vals:
            p90 = vals[min(len(vals) - 1, round(0.9 * (len(vals) - 1)))]
            lines.append(f"- {label}：中位数 {statistics.median(vals):.0f} ms，p90 {p90:.0f} ms，"
                         f"最大 {vals[-1]:.0f} ms（n={len(vals)}）")
    within = sum(1 for r in rows if isinstance(r.get("shown_after_ms"), (int, float)) and r["shown_after_ms"] <= 3000)
    lines.append(f"- 3 秒内显示：{within}/{len(rows)}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--voice", default="Haruka")
    p.add_argument("--scenario", choices=sorted(SCENARIOS), default="dialogue")
    p.add_argument("--gap", type=float, default=3.5, help="pause between turns (s)")
    p.add_argument("--inner-gap", type=float, default=0.75, help="pause between pieces of one turn (s)")
    p.add_argument("--shot-at", type=int, default=2, help="screenshot when this many suggestions have been drawn")
    p.add_argument("--screenshot", type=Path, default=ROOT / "docs" / "images" / "live-suggest.png")
    cli = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    demo = Path(tempfile.gettempdir()) / "jsc_e2e_suggest.wav"
    build_wav(synthesize(SCENARIOS[cli.scenario], cli.voice), cli.gap, cli.inner_gap, demo)
    suggester = Suggester()
    args = argparse.Namespace(source="file", file=str(demo), realtime=True, device=None,
                              model="kotoba-tech/kotoba-whisper-v2.0-faster", device_type="auto",
                              furigana="graded", end_silence=0.6, no_partial=False, turn_gap=0.8)
    events: queue.Queue = queue.Queue()
    stop = threading.Event()
    holder: dict = {}
    finished = threading.Event()
    drawn_count = [0]

    def drawn(uid, payload, timed_out):
        manager = holder["manager"]
        manager.rendered(uid, payload, timed_out)
        drawn_count[0] += 1
        if drawn_count[0] == cli.shot_at:
            cli.screenshot.parent.mkdir(parents=True, exist_ok=True)
            screenshot(window.root, cli.screenshot.resolve())

    thread = threading.Thread(target=worker, args=(args, events, stop, suggester, holder), daemon=True)
    thread.start()
    window = SubtitleWindow(events, alpha=1.0, on_suggestion_drawn=drawn)

    def check_done():
        manager = holder.get("manager")
        if not thread.is_alive() and manager and manager.idle:
            finished.set()
            window.close()
        else:
            window.root.after(500, check_done)

    window.root.after(500, check_done)
    window.run()
    stop.set()

    log_path = holder["manager"].log.path
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = log_path.stem.replace("live-", "")
    saved = OUT_DIR / f"e2e-{cli.scenario}-{stamp}.jsonl"
    shutil.copy(log_path, saved)
    summarize(saved, OUT_DIR / f"e2e-{cli.scenario}-{stamp}.md", suggester.model, cli.gap, cli.scenario, cli.inner_gap)


if __name__ == "__main__":
    main()
