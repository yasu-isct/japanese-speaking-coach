"""Folder refresh, preferences and in-flight request isolation; no API or GUI."""
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from jsc_live.material_control import MaterialsController
from jsc_live.materials import MaterialsStore
from jsc_live.settings import selected_materials
from jsc_live.suggest import Suggester
from jsc_live.material_view import MaterialsWindow

ENV = {"LLM_API_KEY": "fake", "LLM_BASE_URL": "https://example.invalid", "LLM_MODEL": "unused"}
RESPONSE = "推荐|丁寧\n丁寧|資料を確認します。|我确认资料。|\nカジュアル|資料を見るね。|我看资料。|\nかんたん|確認します。|我确认。|\n理由|資料[S1]を使う。\n"


class MaterialControlTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.first, self.second = self.root / "first", self.root / "second"
        for folder, text in [(self.first, "提出形式はPDFです。"), (self.second, "提出形式はTXTです。")]:
            folder.mkdir()
            (folder / "course.md").write_text(text, encoding="utf-8")
        self.settings = self.root / "preferences/settings.json"
        self.client = Suggester(env=ENV, materials=MaterialsStore(self.first))
        self.client._stream = Mock(return_value=iter(RESPONSE.splitlines(keepends=True)))
        self.controller = MaterialsController(self.client, settings_path=self.settings)

    def test_reload_same_folder_refreshes_cache_and_reads_edited_content_without_api(self):
        self.client.prefetch("提出形式")
        (self.first / "course.md").write_text("提出形式はHTMLです。", encoding="utf-8")
        self.assertIn("PDF", self.client._retrieve("提出形式")[0])
        result = self.controller.reload()
        data, sources, _, cached = self.client._retrieve("提出形式")
        self.assertIn("HTML", data)
        self.assertNotIn("PDF", data)
        self.assertFalse(cached)
        self.assertEqual(result["files"], 1)
        self.client._stream.assert_not_called()

    def test_selected_folder_persists_and_cli_override_wins_without_reading_settings(self):
        self.settings.parent.mkdir()
        self.settings.write_text('{"unrelated_preference": 16}', encoding="utf-8")
        self.controller.reload(self.second)
        self.assertEqual(selected_materials(None, self.settings), self.second.resolve())
        self.assertEqual(json.loads(self.settings.read_text())["unrelated_preference"], 16)
        with patch("jsc_live.settings.load_settings", side_effect=AssertionError("Do not read saved settings")):
            self.assertEqual(selected_materials(self.first, self.settings), self.first)

    def test_unreadable_folder_keeps_old_snapshot_and_preferences(self):
        old = self.client.materials
        with self.assertRaises(ValueError):
            self.controller.reload(self.root / "missing")
        self.assertIs(self.client.materials, old)
        self.assertFalse(self.settings.exists())

    def test_empty_folder_is_valid_and_clears_previous_matches(self):
        empty = self.root / "empty"
        empty.mkdir()
        result = self.controller.reload(empty)
        self.assertEqual(result["files"], 0)
        self.assertEqual(self.client._retrieve("提出形式")[1], [])

    def test_bad_settings_are_preserved_while_current_session_selection_still_works(self):
        self.settings.parent.mkdir()
        self.settings.write_text("broken settings", encoding="utf-8")
        result = self.controller.reload(self.second)
        self.assertEqual(self.settings.read_text(), "broken settings")
        self.assertEqual(self.client.materials.root, self.second)
        self.assertIn("这次已生效", result["message"])

    def test_relative_saved_path_is_ignored(self):
        self.settings.parent.mkdir()
        self.settings.write_text('{"materials_directory":"profile.md"}', encoding="utf-8")
        with patch("jsc_live.settings.default_materials_root", return_value=self.first):
            self.assertEqual(selected_materials(None, self.settings), self.first)

    def test_in_flight_request_keeps_old_source_root_and_next_request_uses_new(self):
        sent = []
        def stream(messages):
            sent.append(messages[-1]["content"])
            self.controller.reload(self.second)
            yield RESPONSE
        self.client._stream = stream
        old = self.client.suggest("提出形式", [])
        self.assertEqual(old.materials_root, str(self.first))
        self.assertIn("PDF", old.sources[0]["text"])
        self.assertIn("PDF", sent[0])
        self.client._stream = lambda messages: iter([RESPONSE])
        new = self.client.suggest("提出形式", [])
        self.assertEqual(new.materials_root, str(self.second))
        self.assertIn("TXT", new.sources[0]["text"])
        self.assertNotEqual(old.cited_source_ids, new.cited_source_ids)

    def test_old_search_finishing_after_reload_does_not_poison_new_cache(self):
        old = self.client.materials
        entered, release = threading.Event(), threading.Event()
        original = old.search
        errors = []
        def blocked(query):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test barrier timed out")
            return original(query)
        old.search = blocked
        def prefetch():
            try:
                self.client.prefetch("提出形式")
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=prefetch)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.controller.reload(self.second)
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        result = self.client._retrieve("提出形式")
        self.assertIn("TXT", result[0])
        self.assertFalse(result[3])

    def test_explicit_profile_mode_does_not_silently_switch_to_materials(self):
        client = Suggester(env=ENV, profile="")
        with self.assertRaises(ValueError):
            client.replace_materials(MaterialsStore(self.first))

    def test_view_loads_on_worker_and_poll_updates_widgets_without_a_real_window(self):
        view = MaterialsWindow.__new__(MaterialsWindow)
        view.controller = self.controller
        view.results = queue.Queue()
        view.busy = False
        view.actions = [Mock(), Mock()]
        view.top, view.status, view.folder = Mock(), Mock(), Mock()
        with patch("jsc_live.material_view.threading.Thread") as worker:
            view._reload(self.second)
            self.assertTrue(view.busy)
            worker.return_value.start.assert_called_once()
            self.assertEqual(self.client.materials.root, self.first)
        view._load(self.second)
        view._poll()
        self.assertFalse(view.busy)
        self.assertEqual(view.materials_root, str(self.second))
        self.assertIn("当前资料文件夹", view.folder.configure.call_args.kwargs["text"])

    def test_source_view_keeps_old_request_directory_after_folder_switch(self):
        view = MaterialsWindow.__new__(MaterialsWindow)
        view.controller, view.materials_root, view.busy = self.controller, str(self.first), False
        view.folder, view.status, view.text, view.top = Mock(), Mock(), Mock(), Mock()
        self.controller.reload(self.second)
        view.show([], [], snapshot_root=str(self.first))
        text = "".join(call.args[1] for call in view.text.insert.call_args_list)
        self.assertIn(str(self.first), text)
        self.assertEqual(view.materials_root, str(self.second))


if __name__ == "__main__":
    unittest.main()
