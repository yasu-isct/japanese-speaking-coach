"""Compare the app's recognition with a Buzz transcript of the same real recording.

    python tools/compare_with_buzz.py <recording.mp3|wav> <buzz.srt> --out logs/asr_buzz/<name>
    python tools/compare_with_buzz.py <recording> <buzz.srt> --out logs/asr_buzz/<name> --reuse   # skip decoding

There is no person-checked reference here, so this measures agreement, not accuracy:
  live   the app's path (Segmenter -> Transcriber.transcribe(final=True), noise-phrase filter)
  file   the same model on the whole recording in one call, faster-whisper defaults
  buzz   the .srt Buzz wrote (Buzz runs the PyTorch build of the same Whisper model on the whole file)

The three texts are aligned character by character on Buzz's text. Every place where they differ
is a "spot"; spots whose MeCab readings agree (一日 / 1日, 様 / さま) are only spelling and set
aside. The rest go to judge.md with time and context, so a person listens only to those spots and
says which version is right (far less work than writing a full transcript).

Everything is written to --out (keep it under logs/, git-ignored: the recording's content stays local).
"""

from __future__ import annotations

import argparse
import bisect
import difflib
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "tools")]

import numpy as np  # noqa: E402

from eval_asr import edit_distance, normalize  # noqa: E402
from jsc_live.asr import STOCK_PHRASES  # noqa: E402
from jsc_live.audio import BLOCK_SIZE, SAMPLE_RATE  # noqa: E402

STOCK = ("ご視聴ありがとうございました", "ご清聴ありがとうございました", "チャンネル登録") + tuple(STOCK_PHRASES)
CONTEXT = 8  # characters of Buzz's text shown on each side of a spot


def read_srt(path: Path) -> list[tuple[float, float, str]]:
    def secs(t: str) -> float:
        h, m, s = t.replace(",", ".").split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    out = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
        lines = block.strip().splitlines()
        if len(lines) >= 3 and "-->" in lines[1]:
            a, b = (x.strip() for x in lines[1].split("-->"))
            out.append((secs(a), secs(b), "".join(lines[2:]).strip()))
    return out


def run_live(transcriber, audio: np.ndarray) -> tuple[list[tuple[float, float, str]], int]:
    from jsc_live.segmenter import Segmenter

    segmenter, out, dropped = Segmenter(), [], 0
    padded = np.concatenate([audio, np.zeros(SAMPLE_RATE, np.float32)])
    events = []
    for i in range(0, len(padded), BLOCK_SIZE):
        end = (i + BLOCK_SIZE) / SAMPLE_RATE
        events += [(end, e) for e in segmenter.feed(padded[i:i + BLOCK_SIZE]) if e.kind == "final"]
    events += [(len(padded) / SAMPLE_RATE, e) for e in segmenter.flush()]
    for end, e in events:
        r = transcriber.transcribe(e.audio, final=True)
        dropped += bool(r.dropped)
        if r.text:
            out.append((end - len(e.audio) / SAMPLE_RATE, end, r.text))
    return out, dropped


def run_file(transcriber, audio: np.ndarray) -> list[tuple[float, float, str]]:
    segments, _ = transcriber.model.transcribe(audio, language="ja", task="transcribe", beam_size=5)
    return [(s.start, s.end, s.text.strip()) for s in segments if s.text.strip()]


def with_times(segs) -> tuple[str, list[float]]:
    """Normalized text of all segments and, per character, the start time of its segment."""
    chars, times = [], []
    for s, _, t in segs:
        n = normalize(t)
        chars.append(n)
        times += [s] * len(n)
    return "".join(chars), times


def stock_count(segs) -> int:
    return sum(any(p in t for p in STOCK) for _, _, t in segs)


def mmss(x: float) -> str:
    return f"{int(x) // 60:02d}:{int(x) % 60:02d}"


def diff_ranges(base: str, other: str) -> list[tuple[int, int, int, int]]:
    """Non-equal opcodes as (base_from, base_to, other_from, other_to)."""
    sm = difflib.SequenceMatcher(None, base, other, autojunk=False)
    return [(i1, i2, j1, j2) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]


def mapper(base: str, other: str):
    """Function: position in base -> corresponding position in other (via matching blocks)."""
    blocks = difflib.SequenceMatcher(None, base, other, autojunk=False).get_matching_blocks()
    starts = [b.a for b in blocks]

    def to_other(i: int) -> int:
        k = max(0, bisect.bisect_right(starts, i) - 1)
        b = blocks[k]
        return b.b + min(i - b.a, b.size) if i >= b.a else b.b
    return to_other


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("recording")
    p.add_argument("srt", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--reuse", action="store_true", help="reuse segments.json from an earlier run in --out")
    p.add_argument("--judge", type=int, default=40, help="spots to put in judge.md (spread over the recording)")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    a.out.mkdir(parents=True, exist_ok=True)
    saved = a.out / "segments.json"

    buzz = read_srt(a.srt)
    if a.reuse:
        data = json.loads(saved.read_text(encoding="utf-8"))
        live, file, dropped, decode, minutes = data["live"], data["file"], data["dropped"], data["decode_seconds"], data["minutes"]
    else:
        from faster_whisper import decode_audio

        from jsc_live.asr import Transcriber

        audio = decode_audio(a.recording, sampling_rate=SAMPLE_RATE)
        minutes = round(len(audio) / SAMPLE_RATE / 60, 1)
        transcriber = Transcriber()
        transcriber.warm_up()
        t0 = time.perf_counter()
        live, dropped = run_live(transcriber, audio)
        t1 = time.perf_counter()
        file = run_file(transcriber, audio)
        decode = {"live": round(t1 - t0, 1), "file": round(time.perf_counter() - t1, 1)}
        saved.write_text(json.dumps({"live": live, "file": file, "dropped": dropped, "decode_seconds": decode,
                                     "minutes": minutes}, ensure_ascii=False) + "\n", encoding="utf-8")
    runs = {"live": live, "file": file, "buzz": buzz}
    for name, segs in runs.items():
        (a.out / f"{name}.txt").write_text("\n".join(f"[{mmss(s)}] {t}" for s, _, t in segs) + "\n", encoding="utf-8")

    from jsc_live.furigana import Furigana, kata_to_hira

    furigana = Furigana()

    def reading(text: str) -> str:
        return normalize("".join(kata_to_hira(r) if (r := furigana._reading(w)) else w.surface for w in furigana.tagger(text)))

    texts, times = {}, {}
    for k, v in runs.items():
        texts[k], times[k] = with_times(v)
    pairs = {}
    for x, y in (("buzz", "live"), ("buzz", "file"), ("file", "live")):
        d = edit_distance(texts[x], texts[y])
        dk = edit_distance(reading(texts[x]), reading(texts[y]))
        pairs[f"{x}-{y}"] = {"chars": round(100 * d / max(1, len(texts[x])), 1), "reading": round(100 * dk / max(1, len(reading(texts[x]))), 1)}

    # Spots on Buzz's text where live or file differ, merged when closer than 3 characters.
    b = texts["buzz"]
    ranges = sorted([(i1, i2) for i1, i2, _, _ in diff_ranges(b, texts["live"])] +
                    [(i1, i2) for i1, i2, _, _ in diff_ranges(b, texts["file"])])
    merged: list[list[int]] = []
    for i1, i2 in ranges:
        if merged and i1 <= merged[-1][1] + 3:
            merged[-1][1] = max(merged[-1][1], i2)
        else:
            merged.append([i1, i2])
    to_live, to_file = mapper(b, texts["live"]), mapper(b, texts["file"])
    spots = []
    for i1, i2 in merged:
        # Expand to whole context so the readings compare words, not fragments.
        c1, c2 = max(0, i1 - 2), min(len(b), i2 + 2)
        v = {"buzz": b[c1:c2], "live": texts["live"][to_live(c1):to_live(c2)], "file": texts["file"][to_file(c1):to_file(c2)]}
        r = {k: reading(x) for k, x in v.items()}
        if len(set(v.values())) == 1:
            continue
        if len(set(r.values())) == 1:
            kind = "spelling"
        elif r["file"] == r["buzz"]:
            kind = "live alone"
        elif r["live"] == r["buzz"]:
            kind = "file alone"
        elif r["live"] == r["file"]:
            kind = "buzz alone"
        else:
            kind = "all differ"
        t = times["buzz"][min(i1, len(b) - 1)] if b else 0
        spots.append({"time": t, "kind": kind, "before": b[max(0, c1 - CONTEXT):c1], "after": b[c2:c2 + CONTEXT], **v})

    kinds = {k: sum(s["kind"] == k for s in spots) for k in ("spelling", "live alone", "file alone", "buzz alone", "all differ")}
    real = [s for s in spots if s["kind"] != "spelling"]
    random.Random(0).shuffle(real)
    pick = sorted(real[:a.judge], key=lambda s: s["time"])

    summary = {"recording_minutes": minutes, "segments": {k: len(v) for k, v in runs.items()},
               "characters": {k: len(v) for k, v in texts.items()}, "stock_phrase_segments": {k: stock_count(v) for k, v in runs.items()},
               "live_dropped_by_filter": dropped, "pairwise_distance_percent": pairs, "decode_seconds": decode,
               "spots": len(spots), "spot_kinds": kinds, "judged_spots": len(pick)}
    (a.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (a.out / "spots.json").write_text(json.dumps(spots, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    lines = ["# 三方对比：哪个版本对？", "",
             f"三种识别结果一共有 {len(spots)} 处不同，其中 {kinds['spelling']} 处只是写法不同（读音一样），不用管。",
             f"下面从剩下的 {len(real)} 处里平均抽了 {len(pick)} 处。请听原录音对应时间，在「判断：」后写",
             "L / F / B（哪个对；两个一样且都对就写两个，如 FB），都不对写 X，听不出来写 ?。", "",
             "- L = 我们的应用（实时切句）　F = 同一模型整段识别　B = Buzz",
             "- 「…」两边是上下文（取自 Buzz，已去掉标点），只需判断中间不同的部分", ""]
    for n, s in enumerate(pick, 1):
        lines += [f"## {n}　约 {mmss(s['time'])}", f"…{s['before']}【】{s['after']}…",
                  f"L：{s['live'] or '（没有）'}", f"F：{s['file'] or '（没有）'}", f"B：{s['buzz'] or '（没有）'}", "判断：", ""]
    (a.out / "judge.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
