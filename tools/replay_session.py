"""Re-run a saved session's sentences through the current recognizer and compare.

    python tools/replay_session.py logs/sessions/20261025-114606

Uses clips/NNNN.wav + clips/transcripts.tsv (what the app showed at the time) and
reports, for the current Transcriber: how many sentences are now Chinese, how many
were dropped as noise phrases, how many stock phrases remain, how many Japanese
sentences changed, and the extra time per sentence. Detailed rows go to
<session>/replay.tsv (inside logs/, so not committed: it contains what was said).
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.asr import STOCK_PHRASES, Transcriber  # noqa: E402
from jsc_live.audio import WavFileSource  # noqa: E402


def has_stock(text: str) -> bool:
    return any(p in text for p in STOCK_PHRASES)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("session", type=Path)
    p.add_argument("--limit", type=int, default=0, help="only the first N sentences")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    clips = args.session / "clips"
    old = dict(line.rstrip("\n").split("\t")[:2] for line in (clips / "transcripts.tsv").read_text(encoding="utf-8").splitlines() if "\t" in line)
    names = list(old)[: args.limit or None]

    t = Transcriber()
    t.warm_up()
    t_ja = Transcriber.__new__(Transcriber)           # same model, Japanese only (the old behaviour) for timing
    t_ja.__dict__.update(t.__dict__)
    t_ja.languages = ("ja",)

    rows, new_ms, old_ms = [], [], []
    for name in names:
        audio = WavFileSource(str(clips / name), tail_silence=0).load()
        start = time.perf_counter()
        r = t.transcribe(audio)
        new_ms.append((time.perf_counter() - start) * 1000)
        start = time.perf_counter()
        t_ja.transcribe(audio)
        old_ms.append((time.perf_counter() - start) * 1000)
        rows.append((name, r.language, r.language_prob, r.dropped, old[name], r.text))

    ja = [r for r in rows if r[1] == "ja" and not r[3]]
    zh = [r for r in rows if r[1] == "zh" and not r[3]]
    dropped = [r for r in rows if r[3]]
    print(f"sentences: {len(rows)}")
    print(f"  Japanese: {len(ja)}   Chinese: {len(zh)}   dropped as noise phrase: {len(dropped)}")
    print(f"  stock phrases shown before: {sum(has_stock(r[4]) for r in rows)}   now: {sum(has_stock(r[5]) for r in rows if not r[3])}")
    same = sum(r[4] == r[5] for r in ja)
    print(f"  Japanese sentences identical to what was shown: {same}/{len(ja)}")
    print(f"  time per sentence: {statistics.median(old_ms):.0f} ms (Japanese only) -> {statistics.median(new_ms):.0f} ms "
          f"(with language check), p90 {sorted(new_ms)[int(0.9 * (len(new_ms) - 1))]:.0f} ms")
    with (args.session / "replay.tsv").open("w", encoding="utf-8") as f:
        f.write("clip\tlanguage\tprob\tdropped\tshown_then\tnow\n")
        for r in rows:
            f.write("\t".join(str(x if x is not None else "") for x in r) + "\n")
    print(f"details: {args.session / 'replay.tsv'}")


if __name__ == "__main__":
    main()
