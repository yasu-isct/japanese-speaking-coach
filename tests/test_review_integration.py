"""Offline lookup/archive/CLI integration checks, using only synthetic content."""

import io
import json
import queue
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from jsc_live import history, main  # noqa: E402
from jsc_live.session import Session  # noqa: E402

WORD = "調べた"
SENTENCE = "架空の資料を調べた。"
MEANING = "調べる（しらべる）：在这句话里指查阅资料。"
LOOKUP_RECORD = {
    "kind": "lookup", "word": WORD, "reading": "しらべた", "lemma": "調べる",
    "meaning": MEANING, "sentence": SENTENCE, "utterance_id": 7, "looked_up_at": 123.5,
}


def fake_history(client=None, on_lookup=None):
    window = history.HistoryWindow.__new__(history.HistoryWindow)
    window.lookup = history.Lookup(client)
    window.on_lookup = on_lookup
    window.results = queue.Queue()
    window._pending = None
    window.panel, window.top, window.text = Mock(), Mock(), Mock()
    window.line_ids = {}
    window.furigana = object()
    return window


class HistoryLookupIntegrationTest(unittest.TestCase):
    def test_completed_lookup_saves_context_uid_and_first_line_only(self):
        client = Mock()
        client.ask.return_value = "  " + MEANING + "\nUnused extra line"
        saved = []
        window = fake_history(client, saved.append)
        with patch.object(history.time, "time", return_value=123.5):
            window._ask(WORD, SENTENCE, "synthetic heading", "しらべた", "調べる", 7)
        self.assertEqual(saved, [LOOKUP_RECORD])
        client.ask.assert_called_once_with(history.LOOKUP_SYSTEM, f"词：{WORD}\n句子：{SENTENCE}", max_tokens=120)
        self.assertEqual(window.results.get_nowait(), (WORD, SENTENCE, "synthetic heading", MEANING))

    def test_cached_lookup_still_records_each_explicit_selection_without_new_request(self):
        client = Mock()
        client.ask.return_value = MEANING
        saved = []
        window = fake_history(client, saved.append)
        for uid in (7, 9):
            window._ask(WORD, SENTENCE, "head", "しらべた", "調べる", uid)
        self.assertEqual(client.ask.call_count, 1)
        self.assertEqual([item["utterance_id"] for item in saved], [7, 9])
        self.assertEqual([item["meaning"] for item in saved], [MEANING, MEANING])
        window._ask(WORD, "架空の別の文脈。", "head", "しらべた", "調べる", 10)
        self.assertEqual(client.ask.call_count, 2, "cache must include the sentence context")

    def test_no_client_empty_result_and_network_failure_never_become_vocabulary(self):
        empty, failed = Mock(), Mock()
        empty.ask.return_value = " \n\t"
        failed.ask.side_effect = OSError("synthetic network failure")
        for name, client, expected in (("no client", None, "没有 .env"),
                                       ("empty response", empty, "没查到"),
                                       ("network failure", failed, "查询失败")):
            with self.subTest(name=name):
                saved = Mock()
                window = fake_history(client, saved)
                window._ask(WORD, SENTENCE, "head")
                saved.assert_not_called()
                self.assertIn(expected, window.results.get_nowait()[3])

    def test_completed_old_selection_is_saved_but_does_not_replace_current_panel(self):
        client = Mock()
        client.ask.return_value = MEANING
        saved = Mock()
        window = fake_history(client, saved)
        window._pending = ("別の詞", "架空の新しい文。")
        window._ask(WORD, SENTENCE, "old heading", "しらべた", "調べる", 7)
        window._poll()
        saved.assert_called_once()
        self.assertEqual(saved.call_args.args[0]["utterance_id"], 7)
        window.panel.configure.assert_not_called()
        window.top.after.assert_called_once_with(100, window._poll)

    def test_archive_oserror_keeps_definition_and_reports_save_failure(self):
        client = Mock()
        client.ask.return_value = MEANING
        saved = Mock(side_effect=OSError("synthetic disk failure"))
        window = fake_history(client, saved)
        window._pending = (WORD, SENTENCE)
        window._ask(WORD, SENTENCE, "head")
        window._poll()
        panel_text = window.panel.configure.call_args.kwargs["text"]
        self.assertIn(MEANING, panel_text)
        self.assertIn("本地查词记录未能保存", panel_text)
        self.assertNotIn("synthetic disk failure", panel_text)
        self.assertEqual(window.lookup.cache[(WORD, SENTENCE)], MEANING)

    def test_history_line_uid_reaches_selected_word_without_a_real_thread(self):
        window = fake_history()
        window.text.yview.return_value = (0.0, 1.0)
        window.text.index.side_effect = ["4.0", "4.12"]
        window.add(SENTENCE, uid=7)
        self.assertEqual(window.line_ids, {"4": 7})
        window.text.see.assert_called_once_with("end")
        window.text.get.side_effect = [WORD, "12:00:00  " + SENTENCE]
        with patch.object(history, "reading_and_lemma", return_value=("しらべた", "調べる")), \
                patch.object(history.threading, "Thread") as thread:
            window._on_select()
        supplied = thread.call_args.kwargs
        self.assertEqual(supplied["target"], window._ask)
        self.assertEqual(supplied["args"][:2], (WORD, SENTENCE))
        self.assertEqual(supplied["args"][3:], ("しらべた", "調べる", 7))
        thread.return_value.start.assert_called_once_with()


class TemporaryWorkspaceTest(unittest.TestCase):
    def setUp(self):
        base = ROOT / "logs/star-eval/tmp"
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base, prefix="review-integration-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)


class SessionLookupIntegrationTest(TemporaryWorkspaceTest):
    def test_lookup_after_recording_close_is_saved_with_only_allowlisted_fields(self):
        session = Session("synthetic", "offline-test", root=self.root)
        self.addCleanup(session.close)
        session.close()
        self.assertIsNone(session._stream)
        wav_before = (session.dir / "stream.wav").read_bytes()
        index_before = (self.root / "index.tsv").read_bytes()
        session.add_lookup({**LOOKUP_RECORD, "api_key": "FAKE_NEVER_ARCHIVE", "raw_response": "EXTRA_FIELD"})
        saved = json.loads((session.dir / "vocabulary.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(saved, LOOKUP_RECORD)
        self.assertEqual((session.dir / "stream.wav").read_bytes(), wav_before)
        session.close()
        self.assertEqual((self.root / "index.tsv").read_bytes(), index_before)

    def test_missing_word_meaning_or_wrong_kind_does_not_create_vocabulary(self):
        session = Session("synthetic", "offline-test", root=self.root)
        self.addCleanup(session.close)
        for changes in ({"word": ""}, {"meaning": ""}, {"kind": "suggestion"}):
            session.add_lookup({**LOOKUP_RECORD, **changes})
        self.assertFalse((session.dir / "vocabulary.jsonl").exists())

    def test_history_callback_persists_to_an_already_closed_session(self):
        session = Session("synthetic", "offline-test", root=self.root)
        self.addCleanup(session.close)
        session.close()
        client = Mock()
        client.ask.return_value = MEANING
        window = fake_history(client, session.add_lookup)
        with patch.object(history.time, "time", return_value=123.5):
            window._ask(WORD, SENTENCE, "head", "しらべた", "調べる", 7)
        self.assertEqual(json.loads((session.dir / "vocabulary.jsonl").read_text(encoding="utf-8")), LOOKUP_RECORD)


class ReviewCliIntegrationTest(TemporaryWorkspaceTest):
    def guards(self, exporter=None):
        """No ASR import, model/client construction, Tk root, or worker can run."""
        stack = ExitStack()
        asr = types.ModuleType("jsc_live.asr")
        asr.DEFAULT_MODEL = "offline-placeholder"
        asr.Transcriber = Mock(side_effect=AssertionError("ASR must not start"))
        suggest = types.ModuleType("jsc_live.suggest")
        suggest.Suggester = Mock(side_effect=AssertionError("API client must not be constructed"))
        replacements = {"jsc_live.asr": asr, "jsc_live.suggest": suggest}
        if exporter is not None:
            review = types.ModuleType("jsc_live.session_review")
            review.export_session_review = exporter
            replacements[review.__name__] = review
        stack.enter_context(patch.dict(sys.modules, replacements))
        stack.enter_context(patch.object(main.threading, "Thread", side_effect=AssertionError("Worker must not start")))
        stack.enter_context(patch.object(main, "build_source", side_effect=AssertionError("Audio must not start")))
        stack.enter_context(patch("tkinter.Tk", side_effect=AssertionError("Tk must not start")))
        return stack

    def test_export_exits_before_live_setup_and_forwards_layout_and_paths(self):
        session_dir, output_dir = self.root / "synthetic-session", self.root / "export"
        exported = types.SimpleNamespace(html_path=output_dir / "review.html", anki_path=output_dir / "anki.tsv",
                                         sentence_count=3, turn_count=2, card_count=1, diagnostics={})
        exporter = Mock(return_value=exported)
        output = io.StringIO()
        with self.guards(exporter), redirect_stdout(output):
            main.main(["--review-session", str(session_dir), "--review-output", str(output_dir),
                       "--anki-layout", "fields", "--suggest", "--source", "file"])
        exporter.assert_called_once_with(session_dir, output_dir, anki_layout="fields")
        self.assertIn(str(exported.html_path), output.getvalue())
        self.assertIn("3 条字幕，2 轮建议，1 张卡", output.getvalue())

    def test_zero_card_export_reports_missing_lookups_without_inventing_definitions(self):
        exported = types.SimpleNamespace(html_path=self.root / "review.html", anki_path=self.root / "anki.tsv",
                                         sentence_count=1, turn_count=0, card_count=0,
                                         diagnostics={"synthetic_invalid_records": 1})
        exporter = Mock(return_value=exported)
        output = io.StringIO()
        with self.guards(exporter), redirect_stdout(output):
            main.main(["--review-session", str(self.root)])
        exporter.assert_called_once_with(self.root, None, anki_layout="basic")
        self.assertIn("旧存档不会补造释义", output.getvalue())
        self.assertIn("synthetic_invalid_records", output.getvalue())

    def test_invalid_export_arguments_and_export_failure_do_not_fall_through(self):
        cases = [(["--review-output", str(self.root)], None),
                 (["--review-session", str(self.root), "--init-materials"], None),
                 (["--review-session", str(self.root), "--import-profile", "synthetic.md"], None),
                 (["--review-session", str(self.root)], OSError("synthetic archive is unavailable"))]
        for argv, failure in cases:
            with self.subTest(argv=argv):
                exporter = Mock(side_effect=failure)
                with self.guards(exporter), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    main.main(argv)
                self.assertEqual(error.exception.code, 2)
                if failure is None:
                    exporter.assert_not_called()

    def test_live_history_callback_uses_session_if_recording_is_available(self):
        for recording in (True, False):
            with self.subTest(recording=recording):
                session = Mock()
                fake_thread = Mock()
                asr = types.ModuleType("jsc_live.asr")
                asr.DEFAULT_MODEL = "offline-placeholder"
                suggest = types.ModuleType("jsc_live.suggest")
                suggest.Suggester = Mock(return_value=object())

                def construct_thread(*, target, args, daemon):
                    if recording:
                        args[-1]["session"] = session
                    return fake_thread

                def construct_history(*args, on_lookup):
                    on_lookup(LOOKUP_RECORD)
                    return object()

                def construct_window(events, **kwargs):
                    kwargs["history_window"](object(), "Synthetic font")
                    return types.SimpleNamespace(run=Mock())

                with patch.dict(sys.modules, {"jsc_live.asr": asr, "jsc_live.suggest": suggest}), \
                        patch.object(main.threading, "Thread", side_effect=construct_thread), \
                        patch.object(main, "Furigana", return_value=object()), \
                        patch.object(history, "HistoryWindow", side_effect=construct_history), \
                        patch("jsc_live.overlay.SubtitleWindow", side_effect=construct_window), \
                        patch("tkinter.Tk", side_effect=AssertionError("Tk must not start")):
                    main.main([])
                suggest.Suggester.assert_called_once_with(profile="")
                if recording:
                    session.add_lookup.assert_called_once_with(LOOKUP_RECORD)
                else:
                    session.add_lookup.assert_not_called()
                fake_thread.join.assert_called_once_with(timeout=5)


if __name__ == "__main__":
    unittest.main()
