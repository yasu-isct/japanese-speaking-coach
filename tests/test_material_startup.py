"""Startup concurrency without microphones, generation requests, or windows."""
from pathlib import Path
import contextlib
import io
import queue
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from jsc_live.materials import MaterialsStore
from jsc_live.material_control import MaterialsController
from jsc_live.material_view import MaterialsWindow
from jsc_live.material_startup import MaterialsStartup
from jsc_live.index_task import check_cancel
from jsc_live.semantic_materials import SemanticUnavailable
from jsc_live.suggest import Suggester

ENV = {'LLM_API_KEY': 'fake', 'LLM_BASE_URL': 'https://example.invalid', 'LLM_MODEL': 'unused'}


class MaterialsStartupTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.folder = self.root / 'materials'
        self.folder.mkdir()
        (self.folder / 'course.md').write_text('deadline Friday', encoding='utf-8')
        self.client = Suggester(env=ENV, materials=MaterialsStore.empty(self.folder))
        self.settings = self.root / 'settings.json'
        self.controller = MaterialsController(self.client, settings_path=self.settings)

    def join(self, job):
        job.thread.join(3)
        self.assertFalse(job.thread.is_alive())

    def blocked_encoder(self):
        entered, release = threading.Event(), threading.Event()
        def load(path):
            entered.set()
            if not release.wait(3):
                raise AssertionError('test did not release encoder')
            return object()
        return entered, release, load

    def test_empty_snapshot_never_reads_selected_folder(self):
        with patch.object(Path, 'exists', side_effect=AssertionError('no filesystem read')):
            store = MaterialsStore.empty(self.folder)
        self.assertEqual(store.search('deadline'), [])
        self.assertEqual(store.diagnostics['not_loaded'], 1)
        self.assertIn('尚未读取', self.controller.current()['message'])

    def test_launch_does_not_wait_for_reader_and_publishes_without_saving_preferences(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(3)
            return MaterialsStore(*args, **kwargs)
        old = self.client.materials
        with patch('jsc_live.materials.MaterialsStore', side_effect=blocked):
            job = self.controller.start_initial()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(job.snapshot()['busy'])
                self.assertIs(self.client.materials, old)
                self.assertEqual(self.client._retrieve('deadline')[1], [])
            finally:
                release.set()
                self.join(job)
        self.assertEqual(job.snapshot()['phase'], 'done')
        self.assertTrue(self.client._retrieve('deadline')[1])
        self.assertFalse(self.settings.exists())

    def test_keyword_snapshot_is_usable_while_model_loads_and_completed_semantic_replaces_it(self):
        entered, release, load = self.blocked_encoder()
        final = MaterialsStore(self.folder)
        with patch('jsc_live.semantic_materials.SemanticEncoder', side_effect=load), \
                patch('jsc_live.semantic_materials.SemanticMaterialsStore', return_value=final):
            job = self.controller.start_initial(self.root / 'model')
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(job.snapshot()['ready'])
                self.assertTrue(job.snapshot()['busy'])
                self.assertTrue(self.client._retrieve('deadline')[1])
                self.assertIsNot(self.client.materials, final)
            finally:
                release.set()
                self.join(job)
        self.assertIs(self.client.materials, final)
        self.assertFalse(self.client._retrieval_cache)
        self.assertFalse(self.settings.exists())

    def test_cancel_before_first_publish_keeps_empty_snapshot(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(3)
            check_cancel(kwargs['cancel'])
            return MaterialsStore(*args, **kwargs)
        old = self.client.materials
        with patch('jsc_live.materials.MaterialsStore', side_effect=blocked):
            job = self.controller.start_initial()
            try:
                self.assertTrue(entered.wait(1))
                self.controller.cancel_initial()
            finally:
                release.set()
                self.join(job)
        self.assertIs(self.client.materials, old)
        self.assertEqual(job.snapshot()['phase'], 'cancelled')
        self.assertIn('未使用资料', job.snapshot()['notice'])

    def test_cancel_during_model_load_keeps_keywords_and_never_builds_semantic_store(self):
        entered, release, load = self.blocked_encoder()
        with patch('jsc_live.semantic_materials.SemanticEncoder', side_effect=load), \
                patch('jsc_live.semantic_materials.SemanticMaterialsStore') as semantic:
            job = self.controller.start_initial(self.root / 'model')
            try:
                self.assertTrue(entered.wait(1))
                old = self.client.materials
                self.controller.cancel_initial()
            finally:
                release.set()
                self.join(job)
        self.assertIs(self.client.materials, old)
        semantic.assert_not_called()
        self.assertIn('关键词', job.snapshot()['notice'])
        self.assertFalse(self.settings.exists())

    def test_user_folder_selection_cancels_startup_and_cannot_be_overwritten_by_it(self):
        entered, release, load = self.blocked_encoder()
        other = self.root / 'other'
        other.mkdir()
        (other / 'guide.md').write_text('location lobby', encoding='utf-8')
        errors = []
        def change():
            try:
                self.controller.reload(other)
            except Exception as exc:
                errors.append(exc)
        with patch('jsc_live.semantic_materials.SemanticEncoder', side_effect=load):
            job = self.controller.start_initial(self.root / 'model')
            worker = None
            try:
                self.assertTrue(entered.wait(1))
                worker = threading.Thread(target=change)
                worker.start()
                self.assertTrue(job.cancelled.wait(1))
            finally:
                release.set()
                self.join(job)
                if worker is not None:
                    worker.join(3)
        self.assertFalse(errors)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.client.materials.root, other)
        self.assertEqual(job.snapshot()['notice'], '')
        self.assertTrue(self.settings.exists())

    def test_unavailable_model_falls_back_to_keywords_without_changing_saved_mode(self):
        original = b'{"semantic_enabled":true,"custom":"preserved"}'
        self.settings.write_bytes(original)
        with patch('jsc_live.semantic_materials.SemanticEncoder', side_effect=SemanticUnavailable('missing')):
            job = self.controller.start_initial(self.root / 'model')
            self.join(job)
        self.assertEqual(job.snapshot()['phase'], 'error')
        self.assertTrue(self.client._retrieve('deadline')[1])
        self.assertEqual(self.settings.read_bytes(), original)
        self.assertIn('关键词', job.snapshot()['notice'])

    def test_reader_failure_does_not_leave_busy_state_or_publish_partial_store(self):
        old = self.client.materials
        with patch('jsc_live.materials.MaterialsStore', side_effect=RuntimeError('synthetic error')):
            job = self.controller.start_initial()
            self.join(job)
        self.assertFalse(job.snapshot()['busy'])
        self.assertEqual(job.snapshot()['phase'], 'error')
        self.assertIs(self.client.materials, old)

    def test_startup_is_not_restarted_when_opening_views(self):
        job = self.controller.start_initial()
        self.join(job)
        with self.assertRaises(RuntimeError):
            self.controller.start_initial()

    def test_materials_view_tracks_startup_and_recovers_controls_on_cancel(self):
        job = MaterialsStartup(self.controller)
        self.controller.startup = job
        view = MaterialsWindow.__new__(MaterialsWindow)
        view.controller = self.controller
        view.busy, view._startup_busy, view._startup_seen = False, False, None
        view.actions = [Mock(), Mock(), Mock()]
        view.progress, view.progress_row, view.cancel_button = Mock(), Mock(), Mock()
        view.status, view.folder, view.top = Mock(), Mock(), Mock()
        view._render_sources = Mock()
        view._sync_startup()
        self.assertTrue(view._startup_busy)
        view.actions[0].configure.assert_called_with(state='disabled')
        view._close()
        self.assertTrue(job.cancelled.is_set())
        view.top.withdraw.assert_called_once()
        job._update(busy=False, phase='cancelled', notice='cancelled', message='已取消')
        view._sync_startup()
        self.assertFalse(view._startup_busy)
        view.actions[0].configure.assert_called_with(state='normal')
        view.top.deiconify.assert_not_called()

    def test_overlay_close_cancels_background_loading(self):
        from jsc_live.overlay import SubtitleWindow
        self.controller.startup = MaterialsStartup(self.controller)
        view = SubtitleWindow.__new__(SubtitleWindow)
        view.materials_controller, view.root = self.controller, Mock()
        view.close()
        self.assertTrue(self.controller.startup.cancelled.is_set())
        view.root.destroy.assert_called_once()

    def test_real_main_creates_window_and_dispatches_audio_worker_before_model_is_ready(self):
        from jsc_live.main import main
        entered, release, load = self.blocked_encoder()
        dispatched = threading.Event()
        created = []
        def audio(args, events, stop, *rest):
            dispatched.set()
            stop.wait(3)
        def client(*args, **kwargs):
            return Suggester(*args, env=ENV, **kwargs)
        def control(*args, **kwargs):
            return MaterialsController(*args, settings_path=self.settings, **kwargs)
        def window(*args, **kwargs):
            controller = kwargs['materials_controller']
            created.append(controller)
            def run():
                try:
                    self.assertTrue(dispatched.wait(1))
                    self.assertTrue(entered.wait(1))
                    self.assertTrue(controller.startup.snapshot()['busy'])
                    controller.cancel_initial()
                finally:
                    release.set()
            return Mock(run=run)
        with patch('jsc_live.semantic_materials.SemanticEncoder', side_effect=load), \
                patch('jsc_live.main.worker', side_effect=audio), \
                patch('jsc_live.suggest.Suggester', side_effect=client), \
                patch('jsc_live.material_control.MaterialsController', side_effect=control), \
                patch('jsc_live.overlay.SubtitleWindow', side_effect=window), \
                contextlib.redirect_stdout(io.StringIO()):
            main(['--suggest', '--no-history', '--no-record', '--materials', str(self.folder),
                  '--semantic-model', str(self.root / 'model')])
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].startup.thread.is_alive())
        self.assertFalse(self.settings.exists())

    def test_console_mode_still_loads_complete_materials_before_dispatching_audio(self):
        from jsc_live.main import main
        store = MaterialsStore(self.folder)
        captured = []
        def audio(args, events, stop, client, holder):
            captured.append(client.materials)
            events.put(('end', 0, []))
        with patch('jsc_live.semantic_materials.open_materials', return_value=(store, None)), \
                patch('jsc_live.main.worker', side_effect=audio), \
                patch('jsc_live.suggest.Suggester', side_effect=lambda *a, **kw: Suggester(*a, env=ENV, **kw)), \
                patch.object(MaterialsController, 'start_initial', side_effect=AssertionError('CLI must wait')), \
                patch('jsc_live.overlay.SubtitleWindow', side_effect=AssertionError('No console window')), \
                contextlib.redirect_stdout(io.StringIO()):
            main(['--suggest', '--no-window', '--no-record', '--materials', str(self.folder)])
        self.assertEqual(captured, [store])


if __name__ == '__main__':
    unittest.main()
