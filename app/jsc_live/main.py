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
from pathlib import Path
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

    session = None
    try:
        status("モデル読み込み中…")
        transcriber = Transcriber(args.model, device=args.device_type,
                                  languages=("ja", "zh") if getattr(args, "mixed_chinese", False) else ("ja",))
        transcriber.warm_up()
        furigana = Furigana(args.furigana)
        source = build_source(args)
        if not getattr(args, "no_record", False):
            from .session import Session

            session = Session(source.name, transcriber.model_name, getattr(args, "partner", None))
            events.put(("session", 0, {"dir": str(session.dir)}))
            if holder is not None:
                holder["session"] = session
            log(args, f"[record] {session.dir}")
        manager = None
        if suggester is not None:
            from .live_suggest import SuggestionManager, TimingLog

            log_file = TimingLog(session.suggestions_path) if session else None
            manager = SuggestionManager(events, suggester, furigana, turn_gap=args.turn_gap, log=log_file)
            threading.Thread(target=suggester.warm_up, daemon=True).start()
            if holder is not None:
                holder["manager"] = manager
            log(args, f"[suggest] model={suggester.model} partner={suggester.partner or '-'} log={manager.log.path}")
        segmenter = Segmenter(end_silence=args.end_silence)
        status(f"聞き取り中：{source.name}")
        log(args, f"[ready] model={transcriber.model_name} device={transcriber.device}/{transcriber.compute_type} "
                  f"source={source.name}")

        def finish(uid: int, audio) -> None:
            """A sentence is complete: recognize it, show it, archive it, maybe ask for suggestions."""
            result = transcriber.transcribe(audio, final=True)
            if session:
                session.add_sentence(uid, audio, result.text, result.language, result.dropped)
            if result.dropped:  # noise turned into a stock phrase: clear the partial line, show nothing
                log(args, f"[dropped #{uid}] {result.dropped}")
                events.put(("final", uid, []))
                return
            log(args, f"[final #{uid}] {result.language} {result.audio_seconds:.1f}s audio, "
                      f"{result.seconds * 1000:.0f} ms  {furigana.to_bracket(result.text) if result.language == 'ja' else result.text}")
            if result.language == "ja":
                events.put(("final", uid, furigana.annotate(result.text)))
                if manager:
                    manager.submit(uid, result.text, result.seconds * 1000)
            else:  # Chinese (or another non-Japanese language): plain grey subtitle, no furigana, no suggestion
                events.put(("final_other", uid, [Segment(result.text)] if result.text else []))

        last_partial_at = 0.0
        for block in source.blocks():
            if stop.is_set():
                break
            if session:
                session.write_audio(block)
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
                    events.put(("partial", event.utterance_id, furigana.annotate(result.text)))
                    if manager and result.text:
                        manager.prefetch(result.text)
                else:
                    finish(event.utterance_id, event.audio)
        for event in segmenter.flush():
            finish(event.utterance_id, event.audio)
        log(args, "[done]")
    except Exception as exc:  # surface errors in the window, not only the console
        status(f"エラー: {exc}")
        raise
    finally:
        if session:
            session.close()
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
    p.add_argument("--materials", type=Path, help="external folder of UTF-8 .md/.txt reference documents")
    retrieval = p.add_mutually_exclusive_group()
    retrieval.add_argument("--semantic-model", type=Path, help="use this prepared local model instead of saved retrieval settings; no automatic download")
    retrieval.add_argument("--keywords-only", action="store_true", help="use keyword retrieval for this run without changing saved preferences")
    p.add_argument("--init-materials", action="store_true", help="create the materials folder and exit; no API call")
    p.add_argument("--import-profile", type=Path, help="copy an old profile to external materials without overwriting, then exit")
    p.add_argument("--review-session", type=Path, help="export a local session review and Anki cards, then exit without API/audio")
    p.add_argument("--review-window", action="store_true", help="open only the local archive picker; no live audio or API startup")
    p.add_argument("--review-output", type=Path, help="new output folder for --review-session (never overwrites)")
    p.add_argument("--anki-layout", choices=["basic", "fields"], default="basic",
                   help="Anki card layout: Basic front/back (default) or four custom fields")
    p.add_argument("--suggest-timeout", type=float, default=3.0, help="seconds before a suggestion counts as late")
    p.add_argument("--turn-gap", type=float, default=0.8,
                   help="extra quiet seconds after a sentence before the turn is answered (on top of --end-silence)")
    p.add_argument("--history", type=int, default=5, help="finished subtitle lines kept on screen")
    p.add_argument("--no-history", action="store_true", help="do not open the history window")
    p.add_argument("--mixed-chinese", action="store_true",
                   help="(shelved) detect Chinese sentences too and show them in grey without suggestions")
    p.add_argument("--no-record", action="store_true",
                   help="do not archive this run (by default audio + transcript go to logs/sessions/, local only)")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.review_output is not None and args.review_session is None:
        p.error("--review-output 请与 --review-session 一起使用。")
    if args.review_window:
        if args.review_session or args.init_materials or args.import_profile:
            p.error("复盘窗口与命令导出、资料导入请分开运行。")
        from .review_view import run_review_window

        run_review_window()
        return
    if args.review_session is not None:
        if args.init_materials or args.import_profile:
            p.error("复盘和资料导入请分开执行。")
        from .session_review import export_session_review

        try:
            exported = export_session_review(args.review_session, args.review_output, anki_layout=args.anki_layout)
        except (OSError, ValueError) as exc:
            p.error(str(exc))
        print(f"复盘页：{exported.html_path}\nAnki 文件：{exported.anki_path}\n"
              f"{exported.sentence_count} 条字幕，{exported.turn_count} 轮建议，{exported.card_count} 张卡。\n"
              "全程在本机生成，未调用 API；用浏览器打开复盘页即可回看。")
        if not exported.card_count:
            print("这份存档没有可导出的查词记录。旧存档不会补造释义；录音版中查过的词会随存档保存。")
        if exported.diagnostics:
            print(f"部分记录需检查：{exported.diagnostics}（详情见复盘页）")
        return
    if args.init_materials or args.import_profile:
        from .material_setup import prepare_materials
        from .settings import selected_materials

        try:
            destination = prepare_materials(selected_materials(args.materials), args.import_profile)
        except (OSError, ValueError) as exc:
            p.error(str(exc))
        print(f"资料文件夹：{destination}\n可放入课程笔记、文章或工作文档（UTF-8 .md/.txt）。\n"
              "开启建议后，检索命中的片段会发给 API；原资料保留，不会自动上传全部文件。")
        return
    if args.list_devices:
        from .audio import list_devices

        print(list_devices())
        return

    suggester = None
    model_path = None
    if args.suggest:
        from .suggest import Suggester
        from .semantic_materials import open_materials
        from .settings import selected_materials, selected_semantic_model

        try:
            model_path = selected_semantic_model(args.semantic_model, keywords_only=args.keywords_only)
            store, model_warning = open_materials(selected_materials(args.materials), model_path)
            if model_warning:
                print(f"[资料] {model_warning} 本次继续使用关键词查找。", flush=True)
            suggester = Suggester(partner=args.partner, materials=store)
            print(f"[资料] {suggester.materials.root}（{len(suggester.materials.chunks)} 个片段）；"
                  "本轮查到的相关片段会随问题发给 API。", flush=True)
            if args.materials is None and (Path(__file__).resolve().parents[2] / "profile.md").is_file():
                print("[资料] 检测到旧 profile.md；需要使用时运行 jsc-live.bat --import-profile \"旧资料的完整路径\"。"
                      "原文件会保留，不自动读取或覆盖。", flush=True)
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

    from .history import HistoryWindow, Lookup
    from .overlay import SubtitleWindow

    # The word lookup asks the same API as the suggestions; it works even without --suggest.
    lookup_client = suggester
    if lookup_client is None:
        try:
            from .suggest import Suggester

            lookup_client = Suggester(profile="")  # dictionary lookup does not need external documents
        except RuntimeError:
            lookup_client = None
    lookup = Lookup(lookup_client)
    history_furigana = Furigana("all")

    def record_lookup(record):
        session = holder.get("session")
        if session is not None:
            session.add_lookup(record)

    def make_history(root, family):
        return HistoryWindow(root, history_furigana, lookup, family, on_lookup=record_lookup)

    materials_controller = None
    if suggester and suggester.materials is not None:
        from .material_control import MaterialsController
        materials_controller = MaterialsController(suggester, model_directory=model_path)

    window = SubtitleWindow(events, font_size=args.font_size, history=args.history,
                            suggestion_timeout=args.suggest_timeout,
                            on_suggestion_drawn=suggestion_drawn,
                            history_window=None if args.no_history else make_history,
                            materials_root=str(suggester.materials.root) if suggester and suggester.materials is not None else None,
                            materials_controller=materials_controller,
                            mode="full" if suggester else "subtitles")
    try:
        window.run()
    finally:
        stop.set()
        thread.join(timeout=5)  # let the worker close the session archive (meta.json, index.tsv)


if __name__ == "__main__":
    main()
