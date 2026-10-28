"""How much accuracy does live recognition lose against recognizing the whole file at once?

    python tools/compare_live_vs_file.py                    # large-v3-turbo
    python tools/compare_live_vs_file.py --models large-v3-turbo large-v3

Uses the TTS clips of tools/eval_asr.py (tests/asr_eval/audio, 28 sentences x 3 voices).
For each voice the clips are joined into one continuous stream with pauses like a
conversation (0.4 / 0.8 / 1.5 s, so some sentences run into each other), once clean and
once with pink noise at 10 dB SNR. Each stream is then recognized two ways:

  live   what the app does: Segmenter cuts it into utterances, Transcriber.transcribe(final=True)
         on each (0.3 s lead silence, beam 5, no previous text)
  file   the whole stream in one call with faster-whisper's defaults (beam 5, the previous
         window's text as context, 30 s windows), i.e. how a file transcriber such as Buzz works

Scored on the joined text (CER after NFKC and punctuation removal, and on MeCab readings,
which ignores purely orthographic differences). Output: tests/asr_eval/live-vs-file-<stamp>.{json,md}.
Only public TTS audio; nothing personal.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "tools")]

import numpy as np  # noqa: E402

from eval_asr import AUDIO_DIR, VOICES, cer, load_sentences, normalize  # noqa: E402
from jsc_live.audio import BLOCK_SIZE, SAMPLE_RATE, WavFileSource  # noqa: E402

GAPS = (0.4, 0.8, 1.5)
SNR_DB = 10.0


def stream_for(voice: str, sentences: list[dict]) -> np.ndarray:
    parts = []
    for i, s in enumerate(sentences):
        # tail_silence=0: the pauses are ours, not the loader's
        parts.append(WavFileSource(str(AUDIO_DIR / f"{s['id']}_{voice}.wav"), tail_silence=0).load())
        parts.append(np.zeros(int(GAPS[i % len(GAPS)] * SAMPLE_RATE), np.float32))
    parts.append(np.zeros(SAMPLE_RATE, np.float32))  # let the segmenter close the last utterance
    return np.concatenate(parts)


def with_pink_noise(audio: np.ndarray, snr_db: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    spectrum = np.fft.rfft(rng.standard_normal(len(audio)))
    spectrum /= np.sqrt(np.maximum(np.arange(len(spectrum)), 1))
    noise = np.fft.irfft(spectrum, len(audio)).astype(np.float32)
    speech = audio[np.abs(audio) > 1e-4]
    speech_power = float(np.mean(np.square(speech)))
    noise *= np.sqrt(speech_power / 10 ** (snr_db / 10) / float(np.mean(np.square(noise))))
    return np.clip(audio + noise, -1, 1)


def live(transcriber, audio: np.ndarray) -> tuple[str, float, int]:
    from jsc_live.segmenter import Segmenter

    segmenter = Segmenter()
    events = []
    for i in range(0, len(audio), BLOCK_SIZE):
        events += [e for e in segmenter.feed(audio[i:i + BLOCK_SIZE]) if e.kind == "final"]
    events += segmenter.flush()
    start = time.perf_counter()
    text = "".join(transcriber.transcribe(e.audio, final=True).text for e in events)
    return text, time.perf_counter() - start, len(events)


def whole_file(transcriber, audio: np.ndarray) -> tuple[str, float]:
    start = time.perf_counter()
    segments, _ = transcriber.model.transcribe(audio, language="ja", task="transcribe", beam_size=5)
    text = "".join(seg.text for seg in segments)
    return text, time.perf_counter() - start


def differences(ref: str, hyp: str, limit: int = 12) -> list[str]:
    out = []
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, ref, hyp, autojunk=False).get_opcodes():
        if op != "equal":
            out.append(f"{ref[max(0, a1 - 4):a1]}[{ref[a1:a2]}→{hyp[b1:b2]}]{ref[a2:a2 + 4]}")
    return out[:limit]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--models", nargs="+", default=["large-v3-turbo"])
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    from jsc_live.asr import Transcriber
    from jsc_live.furigana import Furigana, kata_to_hira

    furigana = Furigana()

    def reading(text: str) -> str:
        return normalize("".join(kata_to_hira(r) if (r := furigana._reading(w)) else w.surface
                                 for w in furigana.tagger(text)))

    sentences = load_sentences()
    ref_text = "".join(s["text"] for s in sentences)
    ref, ref_kana = normalize(ref_text), reading(ref_text)
    streams = {(v, cond): (stream_for(v, sentences) if cond == "clean" else with_pink_noise(stream_for(v, sentences), SNR_DB))
               for v in VOICES for cond in ("clean", f"noise{SNR_DB:.0f}dB")}

    rows = []
    for model in a.models:
        transcriber = Transcriber(model)
        transcriber.warm_up()
        for (voice, cond), audio in streams.items():
            for mode in ("live", "file"):
                if mode == "live":
                    hyp, seconds, n = live(transcriber, audio)
                else:
                    (hyp, seconds), n = whole_file(transcriber, audio), None
                e, length = cer(ref, normalize(hyp))
                ek, length_k = cer(ref_kana, reading(hyp))
                rows.append({"model": model, "voice": voice, "condition": cond, "mode": mode, "edits": e, "chars": length,
                             "reading_edits": ek, "kana": length_k, "decode_s": round(seconds, 2), "utterances": n,
                             "audio_s": round(len(audio) / SAMPLE_RATE, 1), "hypothesis": hyp,
                             "differences": differences(ref, normalize(hyp))})
                print(f"{model} {voice:6} {cond:9} {mode:4} CER {100 * e / length:5.1f}%  reading {100 * ek / length_k:5.1f}%  "
                      f"{seconds:.1f}s", flush=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = ROOT / "tests" / "asr_eval" / f"live-vs-file-{stamp}"
    out.with_suffix(".json").write_text(json.dumps({"gaps_s": GAPS, "snr_db": SNR_DB, "rows": rows}, ensure_ascii=False, indent=1) + "\n",
                                        encoding="utf-8")
    lines = [f"# 实时切句 vs 整段识别（{stamp}）", "",
             f"TTS 28 句 × {len(VOICES)} 种声音，每种声音连成一条约 {rows[0]['audio_s']:.0f} 秒的音频（句间停顿 {' / '.join(map(str, GAPS))} 秒）；"
             f"噪声条件加粉红噪声 SNR {SNR_DB:.0f} dB。CER 按 3 种声音合并计算。", "",
             "| 模型 | 条件 | 方式 | CER | 读音 CER | 解码总耗时 |", "|---|---|---|---|---|---|"]
    for model in a.models:
        for cond in ("clean", f"noise{SNR_DB:.0f}dB"):
            for mode in ("live", "file"):
                rs = [r for r in rows if (r["model"], r["condition"], r["mode"]) == (model, cond, mode)]
                c = 100 * sum(r["edits"] for r in rs) / sum(r["chars"] for r in rs)
                k = 100 * sum(r["reading_edits"] for r in rs) / sum(r["kana"] for r in rs)
                lines.append(f"| {model} | {cond} | {mode} | {c:.1f}% | {k:.1f}% | {sum(r['decode_s'] for r in rs):.1f} s |")
    lines += ["", "## 错误位置（[正确→识别]）", ""]
    for r in rows:
        lines.append(f"- {r['model']} {r['voice']} {r['condition']} {r['mode']}：" + ("、".join(r["differences"]) or "无"))
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:4 + 2 + 4 * len(a.models)]))
    print("wrote", out.with_suffix(".md").relative_to(ROOT))


if __name__ == "__main__":
    main()
