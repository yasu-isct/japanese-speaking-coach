"""Compare recognition models / settings on real recordings saved with --save-audio.

    python tools/compare_asr.py logs/audio/20261024-233227
    python tools/compare_asr.py logs/audio/* --models kotoba-tech/kotoba-whisper-v2.0-faster large-v3-turbo
    python tools/compare_asr.py logs/audio/20261024-234557 --resegment   # re-cut with the current Segmenter

Each folder holds NNNN.wav + transcripts.tsv (what the app showed) and, optionally,
references.tsv (what was really said, written by a person). With references the
character error rate (CER, after removing punctuation) is reported; without, only
the outputs are printed side by side.

Settings tried for every model:
  plain   - what the app does now (beam 5, no prompt)
  prompt  - the previous clip's reference/output given as initial_prompt, i.e. the
            recognizer is told what was said just before (helps repeated words)
Recordings stay on this machine (logs/ is git-ignored); only the summary numbers
are meant to be written into docs.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import numpy as np  # noqa: E402

from jsc_live.asr import LEAD_SILENCE  # noqa: E402
from jsc_live.audio import BLOCK_SIZE, SAMPLE_RATE, WavFileSource  # noqa: E402
from jsc_live.segmenter import Segmenter  # noqa: E402
from jsc_live.cuda_setup import register_cuda_dlls  # noqa: E402

PUNCT = set("。、，,．.？?！!…「」『』（）()・〜~　 ")


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


def read_tsv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "\t" in line:
            name, text = line.split("\t", 1)
            rows[name] = text
    return rows


def resegment(clips: list[dict]) -> list[dict]:
    """Join the clips of each folder and let the current Segmenter cut them again."""
    out = []
    for folder in dict.fromkeys(c["folder"] for c in clips):
        stream = np.concatenate([np.zeros(SAMPLE_RATE // 2, np.float32)]
                                + [c["audio"] for c in clips if c["folder"] == folder]
                                + [np.zeros(SAMPLE_RATE, np.float32)])
        seg = Segmenter()
        events = []
        for i in range(0, len(stream), BLOCK_SIZE):
            events += [e for e in seg.feed(stream[i:i + BLOCK_SIZE]) if e.kind == "final"]
        events += seg.flush()
        out += [{"folder": folder, "name": f"re{e.utterance_id:03d} ({len(e.audio) / SAMPLE_RATE:.1f}s)",
                 "audio": e.audio, "ref": None, "shown": ""} for e in events]
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("folders", nargs="+", type=Path)
    p.add_argument("--resegment", action="store_true",
                   help="join each folder's clips back into one stream and cut it again with the current Segmenter "
                        "(to test segmentation changes; references are not used then)")
    p.add_argument("--models", nargs="+",
                   default=["kotoba-tech/kotoba-whisper-v2.0-faster", "large-v3-turbo"])
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    register_cuda_dlls()
    from faster_whisper import WhisperModel

    clips = []
    for folder in args.folders:
        refs = read_tsv(folder / "references.tsv")
        shown = read_tsv(folder / "transcripts.tsv")
        for wav in sorted(folder.glob("*.wav")):
            clips.append({"folder": folder.name, "name": wav.name, "audio": WavFileSource(str(wav), tail_silence=0).load(),
                          "ref": refs.get(wav.name), "shown": shown.get(wav.name, "")})
    if args.resegment:
        clips = resegment(clips)
    print(f"{len(clips)} clips, {sum(c['ref'] is not None for c in clips)} with references\n")

    summary = []
    for model_name in args.models:
        model = WhisperModel(model_name, device="cuda", compute_type="float16")
        model.transcribe(clips[0]["audio"][:16000], language="ja")  # warm-up
        for setting in ("plain", "prompt"):
            edits = chars = 0
            times = []
            previous = None
            print(f"== {model_name} / {setting}")
            for c in clips:
                prompt = previous if setting == "prompt" else None
                start = time.perf_counter()
                audio = np.concatenate([np.zeros(int(LEAD_SILENCE * 16000), np.float32), c["audio"]])
                segments, _ = model.transcribe(audio, language="ja", beam_size=5, vad_filter=False,
                                               condition_on_previous_text=False, without_timestamps=True,
                                               initial_prompt=prompt)
                text = "".join(s.text for s in segments).strip()
                times.append((time.perf_counter() - start) * 1000)
                line = f"  {c['name']}  {times[-1]:5.0f} ms  {text}"
                if c["ref"]:
                    e = edit_distance(normalize(c["ref"]), normalize(text))
                    edits += e
                    chars += len(normalize(c["ref"]))
                    line += f"   [CER {100 * e / len(normalize(c['ref'])):.1f}%]"
                print(line)
                # The prompt is what the app would know: the previous subtitle it showed.
                previous = text
            cer = f"{100 * edits / chars:.1f}%" if chars else "-"
            summary.append((model_name, setting, cer, statistics.median(times)))
        del model

    print("\n| model | setting | CER | median decode ms |\n|---|---|---|---|")
    for m, s, cer, t in summary:
        print(f"| {m} | {s} | {cer} | {t:.0f} |")
    refd = [c for c in clips if c["ref"]]
    if refd:
        e = sum(edit_distance(normalize(c["ref"]), normalize(c["shown"])) for c in refd)
        n = sum(len(normalize(c["ref"])) for c in refd)
        print(f"\n(what the app showed during the test: CER {100 * e / n:.1f}%)")


if __name__ == "__main__":
    main()
