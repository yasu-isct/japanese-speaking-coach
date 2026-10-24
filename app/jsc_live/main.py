"""Command-line entry point.

    python -m jsc_live --source loopback        # subtitles for what the PC is playing
    python -m jsc_live --source mic             # subtitles for the microphone
    python -m jsc_live --source file --file a.wav --realtime
    python -m jsc_live --list-devices
    python -m jsc_live --source mic --suggest --partner 上司   # + reply suggestions (needs .env)

Threads: the audio/ASR worker produces (kind, utterance_id, segments) events;
the tkinter window (main thread) consumes them. With --no-window the events
are printed to the console instead.
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time

from .furigana import Furigana, Segment


def build_source(args):
    from . import audio

    if args.source == "mic":
        return audio.MicrophoneSource(args.device)
    if args.source == "loopback":
        return audio.LoopbackSource(args.device)
    if not args.file:
        sys.exit("--source file needs --file PATH")
    return audio.WavFileSource(args.file, realtime=args.realtime)


def worker(args, events: queue.Queue, stop: threading.Event, suggester=None, holder: dict | None = None) -> None:
    from .asr import Transcriber
    from .segmenter import Segmenter

    def status(text: str) -> None:
        events.put(("status", 0, [Segment(text)]))

    try:
        status("モデル読み込み中…")
        transcriber = Transcriber(args.model, device=args.device_type)
        transcriber.warm_up()
        furigana = Furigana(args.furigana)
        manager = None
        if suggester is not None:
            from .live_suggest import SuggestionManager

            manager = SuggestionManager(events, suggester, furigana, turn_gap=args.turn_gap)
            threading.Thread(target=suggester.warm_up, daemon=True).start()
            if holder is not None:
                holder["manager"] = manager
            log(args, f"[suggest] model={suggester.model} partner={suggester.partner or '-'} log={manager.log.path}")
        source = build_source(args)
        segmenter = Segmenter(end_silence=args.end_silence)
        status(f"聞き取り中：{source.name}")
        log(args, f"[ready] model={transcriber.model_name} device={transcriber.device}/{transcriber.compute_type} "
                  f"source={source.name}")

        last_partial_at = 0.0
        for block in source.blocks():
            if stop.is_set():
                break
            events_out = segmenter.feed(block)
            if manager:
                manager.set_speaking(segmenter.active)
            for event in events_out:
                if event.kind == "partial":
                    if args.no_partial:
                        continue
                    # If the worker is behind (ASR slower than speech), skip partials.
                    if time.perf_counter() - last_partial_at < 0.3:
                        continue
                    result = transcriber.transcribe(event.audio, final=False)
                    last_partial_at = time.perf_counter()
                else:
                    result = transcriber.transcribe(event.audio, final=True)
                    log(args, f"[final #{event.utterance_id}] {result.audio_seconds:.1f}s audio, "
                              f"{result.seconds * 1000:.0f} ms  {furigana.to_bracket(result.text)}")
                events.put((event.kind, event.utterance_id, furigana.annotate(result.text)))
                if manager and event.kind == "final":
                    manager.submit(event.utterance_id, result.text, result.seconds * 1000)
        for event in segmenter.flush():
            result = transcriber.transcribe(event.audio, final=True)
            log(args, f"[final #{event.utterance_id}] {furigana.to_bracket(result.text)}")
            events.put(("final", event.utterance_id, furigana.annotate(result.text)))
            if manager:
                manager.submit(event.utterance_id, result.text, result.seconds * 1000)
        log(args, "[done]")
    except Exception as exc:  # surface errors in the window, not only the console
        status(f"エラー: {exc}")
        raise
    finally:
        events.put(("end", 0, []))


def log(args, message: str) -> None:
    print(message, flush=True)


def main(argv: list[str] | None = None) -> None:
    from .asr import DEFAULT_MODEL

    p = argparse.ArgumentParser(prog="jsc_live", description="Real-time Japanese subtitles with furigana")
    p.add_argument("--source", choices=["loopback", "mic", "file"], default="loopback",
                   help="loopback = audio the PC is playing (default); mic = microphone")
    p.add_argument("--device", help="microphone or speaker name (see --list-devices)")
    p.add_argument("--file", help="WAV file for --source file")
    p.add_argument("--realtime", action="store_true", help="play the file at real-time speed")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--device-type", default="auto", choices=["auto", "cuda", "cpu"])
    p.add_argument("--furigana", default="graded", choices=["graded", "all", "none"],
                   help="graded = skip N5-N4 kanji (default)")
    p.add_argument("--end-silence", type=float, default=0.6, help="pause (s) that ends a sentence")
    p.add_argument("--no-partial", action="store_true", help="only show finished sentences")
    p.add_argument("--no-window", action="store_true", help="print subtitles to the console only")
    p.add_argument("--font-size", type=int, default=16)
    p.add_argument("--list-devices", action="store_true")
    p.add_argument("--suggest", action="store_true", help="show 3 reply suggestions (API settings in .env)")
    p.add_argument("--partner", help="who you are talking to, e.g. 上司 / 老师 / 店员 / 朋友 (helps politeness)")
    p.add_argument("--suggest-timeout", type=float, default=3.0, help="seconds before a suggestion counts as late")
    p.add_argument("--turn-gap", type=float, default=0.8,
                   help="extra quiet seconds after a sentence before the turn is answered (on top of --end-silence)")
    p.add_argument("--history", type=int, default=5, help="finished subtitle lines kept on screen")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.list_devices:
        from .audio import list_devices

        print(list_devices())
        return

    suggester = None
    if args.suggest:
        from .suggest import Suggester

        try:
            suggester = Suggester(partner=args.partner)
        except RuntimeError as exc:  # no .env: keep the subtitles, skip suggestions
            print(f"[suggest] disabled: {exc}", flush=True)
    holder: dict = {}

    def suggestion_drawn(uid, payload, timed_out):
        if holder.get("manager"):
            holder["manager"].rendered(uid, payload, timed_out)

    events: queue.Queue = queue.Queue()
    stop = threading.Event()
    thread = threading.Thread(target=worker, args=(args, events, stop, suggester, holder), daemon=True)
    thread.start()

    if args.no_window:
        try:
            while thread.is_alive() or not events.empty():
                try:
                    kind, uid, payload = events.get(timeout=0.2)
                except queue.Empty:
                    continue
                if kind == "suggest" and payload["state"] in ("done", "error"):
                    suggestion_drawn(uid, payload, False)  # nothing to draw: render time ~0
                if kind == "end":
                    break
        except KeyboardInterrupt:
            stop.set()
        return

    from .overlay import SubtitleWindow

    window = SubtitleWindow(events, font_size=args.font_size, history=args.history,
                            suggestion_timeout=args.suggest_timeout,
                            on_suggestion_drawn=suggestion_drawn)
    try:
        window.run()
    finally:
        stop.set()


if __name__ == "__main__":
    main()
