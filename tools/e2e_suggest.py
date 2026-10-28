"""End-to-end check for live reply suggestions, driven by synthesized speech.

    python tools/e2e_suggest.py                        # 10 single-sentence turns (sample_dialogues.md)
    python tools/e2e_suggest.py --scenario monologue   # long turns cut into pieces, like a podcast

Sentences are synthesized with a built-in Windows voice, joined into one WAV
(short pauses inside a turn, long pauses between turns) and played at
real-time speed through the full app path:

    WAV -> Segmenter -> Transcriber -> subtitle -> SuggestionManager (API) -> hidden Tk Canvas

Default Tk windows remain hidden. --show-window explicitly enables a Windows
click-through, non-activating preview. Hidden Canvas completion is not visible
display latency. Screenshots require both --show-window and --screenshot PATH;
--no-screenshot remains available for compatibility.

Outputs (in tests/live_suggest/):
- e2e-<timestamp>.jsonl  per-sentence log: recognized text, replies, asr / api / render ms
- e2e-<timestamp>.md     the same as a table plus latency percentiles
- --screenshot PATH    optional explicit screenshot once --shot-at is drawn (local only)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import queue
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

from jsc_live.asr import DEFAULT_MODEL  # noqa: E402
from jsc_live.audio import SAMPLE_RATE, WavFileSource  # noqa: E402
from jsc_live.main import worker  # noqa: E402
from jsc_live.overlay import SubtitleWindow  # noqa: E402
from jsc_live.suggest import Suggester, load_profile  # noqa: E402
from test_window import create_test_window  # noqa: E402

AUDIO_DIR = ROOT / "tests" / "asr_eval" / "audio"
OUT_DIR = ROOT / "tests" / "live_suggest"
# Each scenario is a list of turns; each turn is a list of (id, text) pieces.
SCENARIOS = {
    # Public fictional reference documents in tests/fixtures/materials/general/data.
    "references": [
        [("r01", "自然言語処理の授業では、どの形式で提出しますか？")],
        [("r02", "レビュー会議に参加する前に、何を用意しますか？")],
        [("r03", "録音機の赤いランプが点滅したら、どうすればいいですか？")],
        [("r04", "間隔を空けて復習する利点は何ですか？")],
    ],
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
    if getattr(root, "_jsc_test_window_mode", None) != "passive" or not root.winfo_viewable():
        raise RuntimeError("Screenshots require an explicit visible passive test window")
    from window_capture import capture_owned_window

    capture_owned_window(root, out)


def summarize(log_path: Path, md_path: Path, model: str, gap: float, scenario: str, inner_gap: float,
              *, completed: bool = True, expected_turns: int | None = None, show_window: bool = False) -> None:
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows.sort(key=lambda r: r["utterance_id"])

    def fmt(v):
        return f"{v:.0f}" if isinstance(v, (int, float)) else "-"

    render_label = "可见窗口 Canvas 完成" if show_window else "隐藏 Canvas 完成"
    shown_label = "字幕→" + render_label
    lines = [f"# 接话建议端到端测试（{time.strftime('%Y-%m-%d %H:%M')}）", "",
             f"- 场景：`{scenario}`；模型：`{model}`；语音：Windows TTS；一段话内停顿 {inner_gap} s，"
             f"段与段之间停顿 {gap} s；对象标记：无",
             f"- 原始日志：`{log_path.name}`（每段话一行 JSON，含模型原始输出）",
             f"- 建议请求次数：{len(rows)}；字幕句数：{sum(len(r.get('sentences', [1])) for r in rows)}",
             "- 测量模式：" + ("显式可见被动窗口（鼠标穿透、不激活）；记录 Canvas 完成回调，未测屏幕像素呈现时刻。" if show_window else
                                  "后台隐藏 Tk；记录隐藏 Canvas 完成耗时，不是可见显示延迟，不能据此验收实际屏幕显示速度。"),
             f"- 运行状态：{'完整' if completed else '未完成，不能作为整轮验收'}；预定段数：{expected_turns if expected_turns is not None else '未记录'}", "",
             f"| 段 | 这段话的字幕（按停顿切开） | ⭐ 建议 | 识别 ms | 等待停顿 ms | API ms（首字） | {render_label} ms | {shown_label} ms | 状态 |",
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
                       ("render_ms", render_label), ("shown_after_ms", shown_label)]:
        vals = sorted(r[key] for r in rows if isinstance(r.get(key), (int, float)))
        if vals:
            p90 = vals[min(len(vals) - 1, round(0.9 * (len(vals) - 1)))]
            p95 = vals[math.ceil(0.95 * len(vals)) - 1]
            lines.append(f"- {label}：中位数 {statistics.median(vals):.0f} ms，p90 {p90:.0f} ms，p95 {p95:.0f} ms，"
                         f"最大 {vals[-1]:.0f} ms（n={len(vals)}）")
    within = sum(1 for r in rows if isinstance(r.get("shown_after_ms"), (int, float)) and r["shown_after_ms"] <= 3000)
    lines.append(f"- 3 秒内{render_label}：{within}/{len(rows)}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def without_temp_dir(text: str) -> str:
    """Replace the temp folder (long and 8.3 short form, JSON-escaped or not) with <temp>."""
    forms = {tempfile.gettempdir(), os.path.realpath(tempfile.gettempdir())}
    for form in sorted(forms, key=len, reverse=True):
        for spelled in (form, json.dumps(form)[1:-1]):
            text = text.replace(spelled, "<temp>")
    return text

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--voice", default="Haruka")
    p.add_argument("--scenario", choices=sorted(SCENARIOS), default="dialogue")
    p.add_argument("--gap", type=float, default=3.5, help="pause between turns (s)")
    p.add_argument("--inner-gap", type=float, default=0.75, help="pause between pieces of one turn (s)")
    p.add_argument("--shot-at", type=int, default=2, help="screenshot when this many suggestions have been drawn")
    p.add_argument("--screenshot", type=Path, help="optional screenshot destination; requires --show-window")
    source = p.add_mutually_exclusive_group()
    source.add_argument("--profile", type=Path, help="explicit test profile; default: empty, never read private profile.md")
    source.add_argument("--materials", type=Path, help="explicit test materials folder; never discover the default user folder")
    p.add_argument("--semantic-model", type=Path, help="prepared local model; requires explicit --materials")
    p.add_argument("--show-window", action="store_true", help="explicit Windows passive preview; default stays hidden")
    p.add_argument("--no-screenshot", action="store_true", help="disable screenshots even with --show-window")
    cli = p.parse_args()
    if cli.semantic_model and cli.materials is None:
        p.error("--semantic-model requires explicit --materials")
    if cli.screenshot and not cli.show_window and not cli.no_screenshot:
        p.error("--screenshot requires --show-window")
    sys.stdout.reconfigure(encoding="utf-8")

    demo = Path(tempfile.gettempdir()) / "jsc_e2e_suggest.wav"
    build_wav(synthesize(SCENARIOS[cli.scenario], cli.voice), cli.gap, cli.inner_gap, demo)
    if cli.materials is not None:
        from jsc_live.semantic_materials import open_materials
        store, warning = open_materials(cli.materials, cli.semantic_model)
        if warning:
            p.error("Requested local multilingual model is unavailable; refusing an accidental keyword-only test")
        suggester = Suggester(materials=store)
    elif cli.profile:
        suggester = Suggester(profile=load_profile(cli.profile))
    else:
        # Default = what the app does when the learner has added no materials: the
        # external-materials prompt with an empty folder (D-038). The legacy profile
        # prompt is only used with an explicit --profile.
        from jsc_live.materials import MaterialsStore
        suggester = Suggester(materials=MaterialsStore(root=tempfile.mkdtemp(prefix="jsc-empty-materials-")))
    args = argparse.Namespace(source="file", file=str(demo), realtime=True, device=None,
                              model=DEFAULT_MODEL, device_type="auto",
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
        if cli.show_window and cli.screenshot and not cli.no_screenshot and drawn_count[0] == cli.shot_at:
            cli.screenshot.parent.mkdir(parents=True, exist_ok=True)
            screenshot(window.root, cli.screenshot.resolve())

    thread = threading.Thread(target=worker, args=(args, events, stop, suggester, holder), daemon=True)
    window = create_test_window(SubtitleWindow, events, alpha=1.0, on_suggestion_drawn=drawn, show=cli.show_window)
    thread.start()

    def check_done():
        manager = holder.get("manager")
        if not thread.is_alive() and manager and manager.idle:
            finished.set()
            window.close()
        else:
            window.root.after(500, check_done)

    try:
        window.root.after(500, check_done)
        window.run()
    finally:
        stop.set()
        if not window.closed:
            window.close()
        # Preserve the footer and never accept a partial run as complete.
        thread.join(timeout=5)

    log_path = holder["manager"].log.path
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = log_path.parent.name if log_path.name == "suggestions.jsonl" else log_path.stem.replace("live-", "")
    saved = OUT_DIR / f"e2e-{cli.scenario}-{stamp}.jsonl"
    # The empty materials folder lives in the user's temp folder, whose path contains the
    # user name: keep it out of the saved evidence.
    saved.write_text(without_temp_dir(log_path.read_text(encoding="utf-8")), encoding="utf-8")
    count = sum(bool(line.strip()) for line in saved.read_text(encoding="utf-8").splitlines())
    expected = len(SCENARIOS[cli.scenario])
    completed = finished.is_set() and count == expected
    metadata = {"completed": completed, "expected_turns": expected, "recorded_turns": count,
                "input_kind": "public synthetic TTS", "show_window": cli.show_window,
                "retrieval": "local_multilingual_hybrid" if cli.semantic_model else "keywords_or_explicit_profile",
                "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                                  ("app/jsc_live/suggest.py", "app/jsc_live/semantic_materials.py")}}
    (OUT_DIR / f"e2e-{cli.scenario}-{stamp}-run.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    summarize(saved, OUT_DIR / f"e2e-{cli.scenario}-{stamp}.md", suggester.model, cli.gap, cli.scenario, cli.inner_gap,
              completed=completed, expected_turns=expected, show_window=cli.show_window)
    return 0 if completed else 2


if __name__ == "__main__":
    raise SystemExit(main())
