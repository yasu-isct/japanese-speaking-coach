"""Download integrity, cancellation, mode switching and preferences, offline."""
from pathlib import Path
import hashlib
import io
import json
import queue
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from jsc_live.model_setup import prepare_model, PreparationCancelled
from jsc_live.materials import MaterialsStore
from jsc_live.material_control import MaterialsController
from jsc_live.model_view import ModelWindow
from jsc_live.semantic_materials import SemanticMaterialsStore, SemanticUnavailable
from jsc_live.settings import remember_materials, remember_semantic, selected_materials, selected_semantic_model
from jsc_live.suggest import Suggester


class DownloadTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = b"synthetic official bytes"
        self.assets = {"onnx/model.onnx": (len(self.data), hashlib.sha256(self.data).hexdigest())}
        patcher = patch("jsc_live.model_setup.ASSETS", self.assets)
        patcher.start()
        self.addCleanup(patcher.stop)

    def response(self, data=None, length=None):
        value = io.BytesIO(self.data if data is None else data)
        value.headers = {} if length is None else {"Content-Length": str(length)}
        return value

    def test_download_uses_fixed_public_url_and_cache_skips_network(self):
        opener = Mock(return_value=self.response(length=len(self.data)))
        messages = []
        result = prepare_model(self.root, opener=opener, progress=messages.append)
        request = opener.call_args.args[0]
        self.assertTrue(request.full_url.startswith("https://huggingface.co/sentence-transformers/"))
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual((result / "onnx/model.onnx").read_bytes(), self.data)
        self.assertTrue(any("下载" in message for message in messages))
        with patch("jsc_live.model_setup.urllib.request.urlopen", side_effect=AssertionError("No network for cache")):
            prepare_model(self.root)

    def test_bad_hash_preserves_old_file_and_removes_partial(self):
        target = self.root / "onnx/model.onnx"
        target.parent.mkdir()
        target.write_bytes(b"OLD INCOMPLETE FILE")
        with self.assertRaisesRegex(ValueError, "校验"):
            prepare_model(self.root, opener=Mock(return_value=self.response(b"x" * len(self.data))))
        self.assertEqual(target.read_bytes(), b"OLD INCOMPLETE FILE")
        self.assertEqual(list(self.root.rglob("*.part")), [])

    def test_cancel_during_download_keeps_old_file_and_cleans_partial(self):
        cancelled = threading.Event()
        stream = self.response()
        original = stream.read
        def read(size):
            result = original(size)
            cancelled.set()
            return result
        stream.read = read
        with self.assertRaises(PreparationCancelled):
            prepare_model(self.root, opener=Mock(return_value=stream), cancel=cancelled)
        self.assertFalse((self.root / "onnx/model.onnx").exists())
        self.assertEqual(list(self.root.rglob("*.part")), [])

    def test_declared_or_actual_oversize_is_rejected(self):
        for response in (self.response(length=999), self.response(self.data + b"extra")):
            with self.subTest(), self.assertRaises(ValueError):
                prepare_model(self.root, opener=Mock(return_value=response))
        self.assertFalse((self.root / "onnx/model.onnx").exists())

    def test_cancel_before_start_does_not_create_cache_or_contact_network(self):
        cancelled = threading.Event()
        cancelled.set()
        target = self.root / "unused"
        opener = Mock(side_effect=AssertionError("No network"))
        with self.assertRaises(PreparationCancelled):
            prepare_model(target, opener=opener, cancel=cancelled)
        self.assertFalse(target.exists())
        opener.assert_not_called()


class FakeEncoder:
    def __init__(self, path):
        self.directory = Path(path).resolve()
    def windows(self, heading, text):
        return [text]
    def encode(self, values):
        return np.array([[1., 0.] for _ in values]).reshape(-1, 2)
    def query(self, text):
        return np.array([1., 0.])


class ModelModeTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.folder = self.root / "materials"
        self.folder.mkdir()
        (self.folder / "note.md").write_text("Synthetic course instructions", encoding="utf-8")
        self.settings = self.root / "settings.json"
        self.model = self.root / "model"
        self.client = Suggester(env={"LLM_API_KEY":"fake", "LLM_BASE_URL":"https://example.invalid", "LLM_MODEL":"unused"},
                               materials=MaterialsStore(self.folder))
        self.controller = MaterialsController(self.client, settings_path=self.settings, model_home=self.model)

    def test_switch_and_restart_precedence_preserve_unrelated_settings(self):
        self.settings.write_text('{"custom":"keep"}', encoding="utf-8")
        remember_materials(self.folder, self.settings)
        with patch("jsc_live.semantic_materials.SemanticEncoder", FakeEncoder):
            result = self.controller.set_semantic(True, self.model)
        self.assertTrue(result["enabled"])
        self.assertIsInstance(self.client.materials, SemanticMaterialsStore)
        self.assertEqual(selected_semantic_model(None, path=self.settings), self.model)
        other = self.root / "explicit"
        self.assertEqual(selected_semantic_model(other, path=self.settings), other)
        self.assertIsNone(selected_semantic_model(other, keywords_only=True, path=self.settings))
        self.controller.set_semantic(False)
        self.assertIs(type(self.client.materials), MaterialsStore)
        self.assertIsNone(selected_semantic_model(None, path=self.settings))
        self.assertEqual(selected_materials(None, self.settings), self.folder)
        self.assertEqual(json.loads(self.settings.read_text())["custom"], "keep")

    def test_missing_model_does_not_switch_or_save_settings(self):
        old = self.client.materials
        with patch("jsc_live.semantic_materials.SemanticEncoder", side_effect=SemanticUnavailable("synthetic missing")):
            with self.assertRaisesRegex(ValueError, "已保留"):
                self.controller.set_semantic(True, self.model)
        self.assertIs(self.client.materials, old)
        self.assertFalse(self.settings.exists())

    def test_cancellation_after_model_load_does_not_publish_or_save(self):
        cancel = threading.Event()
        old = self.client.materials
        def encode(path):
            cancel.set()
            return FakeEncoder(path)
        with patch("jsc_live.semantic_materials.SemanticEncoder", side_effect=encode):
            with self.assertRaises(PreparationCancelled):
                self.controller.set_semantic(True, self.model, cancel=cancel)
        self.assertIs(self.client.materials, old)
        self.assertFalse(self.settings.exists())

    def test_corrupt_settings_do_not_prevent_this_run_or_get_overwritten(self):
        self.settings.write_text("broken", encoding="utf-8")
        with patch("jsc_live.semantic_materials.SemanticEncoder", FakeEncoder):
            result = self.controller.set_semantic(True, self.model)
        self.assertTrue(result["enabled"])
        self.assertIn("无法记住", result["message"])
        self.assertEqual(self.settings.read_text(), "broken")

    def test_prepare_does_not_hold_reload_lock_and_uses_current_folder_after_download(self):
        other = self.root / "new-materials"
        other.mkdir()
        (other / "new.md").write_text("Updated synthetic source", encoding="utf-8")
        def download(*args, **kwargs):
            self.assertTrue(self.controller._reload_lock.acquire(blocking=False))
            self.controller._reload_lock.release()
            self.controller.reload(other)
            return self.model
        with patch("jsc_live.model_setup.prepare_model", side_effect=download), \
                patch("jsc_live.semantic_materials.SemanticEncoder", FakeEncoder):
            self.controller.prepare_semantic()
        self.assertEqual(self.client.materials.root, other)
        self.assertTrue(self.controller.model_info()["enabled"])

    def test_untrusted_saved_flags_and_relative_paths_are_ignored(self):
        for value in ({"semantic_enabled":"true", "semantic_model_directory":str(self.model)},
                      {"semantic_enabled":True, "semantic_model_directory":"relative/model"}):
            self.settings.write_text(json.dumps(value), encoding="utf-8")
            self.assertIsNone(selected_semantic_model(None, path=self.settings))


class ModelViewTest(unittest.TestCase):
    def view(self):
        view = ModelWindow.__new__(ModelWindow)
        view.busy, view.info = False, {}
        view.cancel, view.results = threading.Event(), queue.Queue()
        view.controller, view.top, view.status, view.progress, view.cancel_button = (Mock() for _ in range(5))
        view.actions = (Mock(), Mock(), Mock())
        view._show_info, view.on_change = Mock(), Mock()
        return view

    def test_download_is_only_started_by_explicit_action_on_worker(self):
        view = self.view()
        with patch("jsc_live.model_view.threading.Thread") as thread:
            view._download()
        self.assertTrue(view.busy)
        self.assertEqual(thread.call_args.kwargs["args"], ("prepare", True, None))
        view.controller.prepare_semantic.assert_not_called()

    def test_progress_does_not_clear_busy_and_completion_updates_parent(self):
        view = self.view()
        view.busy = True
        view.results.put(("progress", "Downloading synthetic bytes"))
        view._poll()
        self.assertTrue(view.busy)
        view.results.put(("done", {"enabled":True}))
        view._poll()
        self.assertFalse(view.busy)
        view._show_info.assert_called_once_with({"enabled":True})
        view.on_change.assert_called_once_with()

    def test_close_requests_cancel_without_focusing_or_opening_other_windows(self):
        view = self.view()
        view.busy = True
        view._close()
        self.assertTrue(view.cancel.is_set())
        view.top.withdraw.assert_called_once_with()

    def test_worker_error_returns_to_interactive_state(self):
        view = self.view()
        view.busy = True
        view.controller.set_semantic.side_effect = ValueError("model unavailable")
        view._work("switch", True, None)
        view._poll()
        self.assertFalse(view.busy)
        view.status.configure.assert_called_with(text="model unavailable")
        view.on_change.assert_not_called()


if __name__ == "__main__":
    unittest.main()
