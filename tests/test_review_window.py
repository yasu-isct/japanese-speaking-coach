"""Archive picker behaviour without windows, audio, API, browser or Explorer."""
import io
import json
from pathlib import Path
import queue
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from jsc_live.review_control import ReviewController, SavedSession
from jsc_live.review_view import ReviewWindow


def fake_view(controller=None):
    view = ReviewWindow.__new__(ReviewWindow)
    view.controller = controller or Mock()
    view.results = queue.Queue()
    view.busy, view.selected_dir, view.last_export, view.sessions = False, None, None, {}
    for name in ("tree", "top", "status", "selection_label", "refresh_button", "choose_button", "layout_box",
                 "export_button", "page_button", "folder_button", "layout"):
        setattr(view, name, Mock())
    view.tree.get_children.return_value = ()
    view.tree.selection.return_value = ()
    view.layout.get.return_value = "Basic 双面卡"
    return view


class ReviewCatalogueTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.controller = ReviewController(self.root)

    def test_catalogue_is_sorted_and_does_not_read_transcripts_or_audio(self):
        for stamp, minutes in (("20261025-120000", 12.3), ("20261026-090000", 3.5)):
            folder = self.root / stamp
            folder.mkdir()
            (folder / "meta.json").write_text(json.dumps({"started": stamp, "minutes": minutes, "sentences": 4,
                                                         "ended": "2026-10-26"}), encoding="utf-8")
            (folder / "transcript.txt").write_text("THIS SHOULD NOT BE READ", encoding="utf-8")
        (self.root / "review-output").mkdir()
        original = Path.read_text
        def metadata_only(path, *args, **kwargs):
            self.assertEqual(path.name, "meta.json")
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", metadata_only):
            sessions = self.controller.list_sessions()
        self.assertEqual([s.path.name for s in sessions], ["20261026-090000", "20261025-120000"])
        self.assertEqual(sessions[0].minutes, 3.5)
        self.assertTrue(sessions[0].ended)

    def test_missing_and_bad_metadata_do_not_hide_folders(self):
        for name in ("20261026-000001", "20261026-000002"):
            (self.root / name).mkdir()
        (self.root / "20261026-000001/meta.json").write_text("{broken", encoding="utf-8")
        sessions = self.controller.list_sessions()
        self.assertEqual(len(sessions), 2)
        self.assertTrue(all(s.minutes is None and s.sentences is None and not s.ended for s in sessions))
        self.assertEqual(ReviewController(self.root / "missing").list_sessions(), [])

    def test_catalogue_keeps_two_hundred_newest_names(self):
        for index in range(205):
            (self.root / f"20261026-{index:06d}").mkdir()
        sessions = self.controller.list_sessions()
        self.assertEqual(len(sessions), 200)
        self.assertEqual(sessions[-1].path.name, "20261026-000005")

    def test_real_export_uses_synthetic_archive_without_overwriting_inputs(self):
        source = self.root / "20261026-100000"
        shutil.copytree(ROOT / "tests/fixtures/session_review", source)
        before = (source / "suggestions.jsonl").read_bytes()
        exported = self.controller.export(source)
        self.assertEqual((exported.sentence_count, exported.turn_count, exported.card_count), (2, 2, 2))
        self.assertEqual((source / "suggestions.jsonl").read_bytes(), before)


class ReviewViewTest(unittest.TestCase):
    def test_list_load_runs_on_worker_and_empty_state_explains_recording(self):
        view = fake_view()
        view.controller.list_sessions.return_value = []
        with patch("jsc_live.review_view.threading.Thread") as worker:
            view._refresh()
            worker.return_value.start.assert_called_once()
            view.controller.list_sessions.assert_not_called()
        view._load_list()
        view._poll()
        self.assertFalse(view.busy)
        self.assertIn("录音版", view.status.configure.call_args.kwargs["text"])
        self.assertIsNone(view.last_export)

    def test_selection_is_explicit_and_clears_previous_export(self):
        view = fake_view()
        session = SavedSession(Path("synthetic"), "2026-10-26", 4, 3, True)
        view.sessions = {"0": session}
        view.tree.selection.return_value = ("0",)
        view.last_export = object()
        view._select()
        self.assertEqual(view.selected_dir, session.path)
        self.assertIsNone(view.last_export)
        view.page_button.configure.assert_called_with(state="disabled")

    def test_export_is_background_and_does_not_open_results_automatically(self):
        view = fake_view()
        view.selected_dir = Path("synthetic")
        view.layout.get.return_value = "自建四字段"
        with patch("jsc_live.review_view.threading.Thread") as worker:
            view._export()
            self.assertEqual(worker.call_args.kwargs["args"], (Path("synthetic"), "fields"))
            view.controller.export.assert_not_called()
        result = SimpleNamespace(sentence_count=4, turn_count=4, card_count=0, diagnostics={})
        view.results.put(("export", True, result))
        with patch("jsc_live.review_view.webbrowser.open", side_effect=AssertionError("No auto browser")), \
                patch("jsc_live.review_view.os.startfile", side_effect=AssertionError("No auto Explorer"), create=True):
            view._poll()
        self.assertIn("没有可导出的查词记录", view.status.configure.call_args.kwargs["text"])
        self.assertIs(view.last_export, result)
        view.page_button.configure.assert_called_with(state="normal")

    def test_export_failure_returns_to_usable_state_without_success_buttons(self):
        view = fake_view()
        view.selected_dir = Path("bad-archive")
        view.controller.export.side_effect = ValueError("这不是会话存档。")
        view._generate(view.selected_dir, "basic")
        view._poll()
        self.assertFalse(view.busy)
        self.assertIsNone(view.last_export)
        self.assertIn("不是会话", view.status.configure.call_args.kwargs["text"])

    def test_user_actions_open_only_the_exported_local_results(self):
        view = fake_view()
        path = (ROOT / "logs/synthetic-export/review.html").resolve()
        view.last_export = SimpleNamespace(html_path=path)
        with patch("jsc_live.review_view.webbrowser.open", return_value=True) as browser, \
                patch("jsc_live.review_view.os.startfile", create=True) as folder:
            view._open_page()
            view._open_folder()
        browser.assert_called_once_with(path.as_uri())
        folder.assert_called_once_with(str(path.parent))

    def test_standalone_cli_exits_before_api_audio_or_live_worker(self):
        from jsc_live.main import main
        with patch("jsc_live.review_view.run_review_window") as run, \
                patch("jsc_live.main.threading.Thread", side_effect=AssertionError("No live worker")), \
                patch("jsc_live.suggest.Suggester", side_effect=AssertionError("No API")), \
                patch("sys.stdout", io.StringIO()):
            main(["--review-window", "--suggest"])
        run.assert_called_once_with()

    def test_toolbar_lazily_constructs_and_reuses_review_window(self):
        from jsc_live.overlay import SubtitleWindow
        win = SubtitleWindow.__new__(SubtitleWindow)
        win.root, win.family, win.review_window = Mock(), "Synthetic", None
        with patch("jsc_live.review_view.ReviewWindow") as factory:
            win._press("review")
            win._press("review")
        factory.assert_called_once_with(win.root, "Synthetic")
        self.assertEqual(factory.return_value.show.call_count, 2)


if __name__ == "__main__":
    unittest.main()
