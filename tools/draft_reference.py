"""Cut a stretch of a real recording and draft a transcript for a person to correct.

    python tools/draft_reference.py logs/sessions/<id>/stream.wav --start 11:50 --minutes 5 --out logs/asr_real/<name>

Writes into --out (keep it under logs/, which is git-ignored; real recordings are never committed):
  audio.wav         the stretch, 16 kHz mono, to listen to while checking
  check.md          one block per sentence: time, whole-file text (A) and, where it differs,
                    the live app's text (B); the 「正确：」 line starts as A and is what the person edits
Both drafts come from large-v3-turbo; showing both keeps the reference from silently favouring
either way of recognizing, and marks where to listen closely. Scoring: tools/compare_live_vs_file.py --real.
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import numpy as np  # noqa: E402

from jsc_live.audio import BLOCK_SIZE, SAMPLE_RATE, WavFileSource  # noqa: E402


def mmss(seconds: float) -> str:
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def parse_time(text: str) -> float:
    parts = [float(x) for x in text.split(":")]
    return sum(v * 60 ** i for i, v in enumerate(reversed(parts)))


def live_utterances(transcriber, audio: np.ndarray) -> list[tuple[float, float, str]]:
    from jsc_live.segmenter import Segmenter

    segmenter = Segmenter()
    out = []
    padded = np.concatenate([audio, np.zeros(SAMPLE_RATE, np.float32)])
    for i in range(0, len(padded), BLOCK_SIZE):
        for e in segmenter.feed(padded[i:i + BLOCK_SIZE]):
            if e.kind == "final":
                end = (i + BLOCK_SIZE) / SAMPLE_RATE
                out.append((end - len(e.audio) / SAMPLE_RATE, end, transcriber.transcribe(e.audio, final=True).text))
    for e in segmenter.flush():
        out.append((len(padded) / SAMPLE_RATE - len(e.audio) / SAMPLE_RATE, len(padded) / SAMPLE_RATE,
                    transcriber.transcribe(e.audio, final=True).text))
    return [u for u in out if u[2]]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("wav")
    p.add_argument("--start", required=True, help="mm:ss in the recording")
    p.add_argument("--minutes", type=float, default=5)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if (a.out / "check.md").exists():
        sys.exit(f"{a.out / 'check.md'} exists; not overwriting a transcript that may already be corrected")
    a.out.mkdir(parents=True, exist_ok=True)

    start = parse_time(a.start)
    full = WavFileSource(a.wav, tail_silence=0).load()
    audio = full[int(start * SAMPLE_RATE):int((start + a.minutes * 60) * SAMPLE_RATE)]
    with wave.open(str(a.out / "audio.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())

    from jsc_live.asr import Transcriber

    transcriber = Transcriber()
    transcriber.warm_up()
    segments, _ = transcriber.model.transcribe(audio, language="ja", task="transcribe", beam_size=5)
    file_segs = [(s.start, s.end, s.text.strip()) for s in segments if s.text.strip()]
    live = live_utterances(transcriber, audio)

    # Give each live utterance to the whole-file segment it overlaps most.
    live_text = [[] for _ in file_segs]
    for ls, le, text in live:
        overlaps = [max(0.0, min(le, fe) - max(ls, fs)) for fs, fe, _ in file_segs]
        if file_segs:
            live_text[int(np.argmax(overlaps))].append(text)

    lines = [f"# 逐字稿核对：{Path(a.wav).parent.name}，{mmss(start)} 起 {a.minutes:g} 分钟", "",
             "边听 `audio.wav` 边改每段的「正确：」一行，写成实际说的话（时间是 audio.wav 里的时间）。",
             "- A = 整段识别，B = 应用实时识别；只有两者不同时才显示 B，这些地方请特别仔细听。",
             "- 听不清的部分写 `（听不清）`，没人说话却有文字的整段删掉「正确：」后的内容，说的是中文就照写中文。",
             "- 写法（漢字/かな、数字）不用纠结，按你觉得自然的写；评分时会另算一个不计写法的读音错误率。", ""]
    for n, ((fs, fe, text), others) in enumerate(zip(file_segs, live_text), 1):
        b = "".join(others)
        lines.append(f"## {n}　[{mmss(fs)}–{mmss(fe)}]（原录音 {mmss(start + fs)}）")
        lines.append(f"A：{text}")
        if b.replace("。", "").replace("、", "") != text.replace("。", "").replace("、", ""):
            lines.append(f"B：{b or '（实时没有输出）'}")
        lines.append(f"正确：{text}")
        lines.append("")
    unmatched = len(live) - sum(len(x) for x in live_text)
    (a.out / "check.md").write_text("\n".join(lines), encoding="utf-8")
    differ = sum(1 for line in lines if line.startswith("B："))
    print(f"{len(file_segs)} segments, {len(live)} live utterances, {differ} differ, unmatched {unmatched}")
    print("wrote", a.out / "audio.wav", "and", a.out / "check.md")


if __name__ == "__main__":
    main()
