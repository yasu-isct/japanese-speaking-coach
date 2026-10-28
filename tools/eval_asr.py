"""Small ASR evaluation for jsc_live: CER and per-utterance decoding time.

    python tools/eval_asr.py --synth     # 1) synthesize tests/asr_eval/audio/*.wav with Windows voices
    python tools/eval_asr.py             # 2) run the jsc_live recognition path and score it

Each clip goes through the same path as the live app (WavFileSource ->
Segmenter -> Transcriber with final=True), not a direct model call, so
segmentation mistakes show up in the score too.

Two error rates are reported:
- CER: character error rate on the surface text after NFKC and punctuation removal;
- reading CER: the same on hiragana readings from MeCab, which ignores purely
  orthographic differences (お疲れ様 / お疲れさま, 一分 / 1分).
"""

from __future__ import annotations

import argparse
import csv
import statistics
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

EVAL_DIR = ROOT / "tests" / "asr_eval"
AUDIO_DIR = EVAL_DIR / "audio"
SENTENCES = EVAL_DIR / "sentences.tsv"
RESULTS = EVAL_DIR / "results.csv"  # overridden per model by --model
VOICES = ["Haruka", "Ayumi", "Ichiro"]
PUNCT = set("。、，,．.？?！!…「」『』（）()・〜~ー　 ")


def load_sentences() -> list[dict]:
    with SENTENCES.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def synthesize(sentences: list[dict]) -> None:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    script = ROOT / "tools" / "tts_winrt.ps1"
    for voice in VOICES:
        for s in sentences:
            out = AUDIO_DIR / f"{s['id']}_{voice}.wav"
            if out.exists():
                continue
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                            "-Text", s["text"], "-Voice", voice, "-Out", str(out)], check=True)
            print("wrote", out.name, flush=True)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return "".join(c for c in text if c not in PUNCT and not c.isspace())


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> tuple[int, int]:
    """(edits, reference length) so totals can be pooled across sentences."""
    return edit_distance(ref, hyp), len(ref)


def percentile(values: list[float], q: float) -> float:
    values = sorted(values)
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def evaluate(sentences: list[dict], model: str) -> None:
    from jsc_live.asr import Transcriber
    from jsc_live.audio import BLOCK_SIZE, WavFileSource
    from jsc_live.furigana import Furigana, kata_to_hira
    from jsc_live.segmenter import Segmenter

    furigana = Furigana()

    def reading(text: str) -> str:
        out = []
        for word in furigana.tagger(text):
            r = furigana._reading(word)
            out.append(kata_to_hira(r) if r else word.surface)
        return normalize("".join(out))

    transcriber = Transcriber(model)
    transcriber.warm_up()
    rows = []
    for voice in VOICES:
        for s in sentences:
            path = AUDIO_DIR / f"{s['id']}_{voice}.wav"
            if not path.exists():
                sys.exit(f"missing {path}; run with --synth first")
            audio = WavFileSource(str(path)).load()
            segmenter = Segmenter()
            texts, decode, n_seg = [], [], 0
            events = []
            for i in range(0, len(audio), BLOCK_SIZE):
                events += [e for e in segmenter.feed(audio[i:i + BLOCK_SIZE]) if e.kind == "final"]
            events += segmenter.flush()
            for e in events:
                r = transcriber.transcribe(e.audio, final=True)
                texts.append(r.text)
                decode.append(r.seconds)
                n_seg += 1
            hyp = "".join(texts)
            e1, n1 = cer(normalize(s["text"]), normalize(hyp))
            e2, n2 = cer(reading(s["text"]), reading(hyp))
            rows.append({
                "id": s["id"], "voice": voice, "reference": s["text"], "hypothesis": hyp,
                "edits": e1, "ref_chars": n1, "reading_edits": e2, "ref_kana": n2,
                "audio_s": round(len(audio) / 16000 - 1.0, 2),  # minus the 1 s tail added by WavFileSource
                "segments": n_seg,
                "decode_ms": round(1000 * sum(decode)),
                "max_segment_decode_ms": round(1000 * max(decode)) if decode else 0,
            })
            print(f"{s['id']} {voice:6} cer={e1}/{n1} {round(1000 * sum(decode))}ms  {hyp}", flush=True)

    with results_path(model).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summarize(rows)


def summarize(rows: list[dict]) -> None:
    def pooled(rs, e, n):
        return 100 * sum(int(r[e]) for r in rs) / sum(int(r[n]) for r in rs)

    print("\n| voice | clips | CER | reading CER | exact match |")
    print("|---|---|---|---|---|")
    for voice in VOICES + ["all"]:
        rs = [r for r in rows if voice in ("all", r["voice"])]
        exact = sum(int(r["edits"]) == 0 for r in rs)
        print(f"| {voice} | {len(rs)} | {pooled(rs, 'edits', 'ref_chars'):.1f}% | "
              f"{pooled(rs, 'reading_edits', 'ref_kana'):.1f}% | {exact}/{len(rs)} |")
    seg_ms = [int(r["max_segment_decode_ms"]) for r in rows]
    print(f"\nper-utterance decode ms: min {min(seg_ms)}, median {statistics.median(seg_ms):.0f}, "
          f"p90 {percentile(seg_ms, 0.9):.0f}, max {max(seg_ms)}")
    print(f"clips split into >1 segment: {sum(int(r['segments']) > 1 for r in rows)}; "
          f"clips with 0 segments: {sum(int(r['segments']) == 0 for r in rows)}")


def results_path(model: str) -> Path:
    return RESULTS if model == "kotoba-tech/kotoba-whisper-v2.0-faster" else EVAL_DIR / f"results-{model.split('/')[-1]}.csv"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--synth", action="store_true", help="synthesize the evaluation audio")
    p.add_argument("--summary", action="store_true", help="re-print the summary of results.csv")
    p.add_argument("--model", default="kotoba-tech/kotoba-whisper-v2.0-faster",
                   help="ASR model; results.csv is kotoba, other models get results-<name>.csv")
    args = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sentences = load_sentences()
    if args.synth:
        synthesize(sentences)
    elif args.summary:
        with results_path(args.model).open(encoding="utf-8") as f:
            summarize(list(csv.DictReader(f)))
    else:
        evaluate(sentences, args.model)


if __name__ == "__main__":
    main()
