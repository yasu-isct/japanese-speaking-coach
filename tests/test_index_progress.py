"""Larger snapshots, atomic cancellation and progress without desktop windows."""
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tools')]
from jsc_live.index_task import IndexCancelled
from jsc_live.materials import MaterialsStore, MaterialsLimits
from jsc_live.semantic_materials import SemanticMaterialsStore
from jsc_live.material_control import MaterialsController
from jsc_live.material_view import MaterialsWindow
from jsc_live.pdf_text import extract_pdf
from jsc_live.suggest import Suggester
from pdf_helpers import pdf_bytes
from eval_materials_scale import library

ENV={'LLM_API_KEY':'fake','LLM_BASE_URL':'https://example.invalid','LLM_MODEL':'unused'}


class Encoder:
    def __init__(self,cancel=None):self.cancel=cancel;self.batches=[]
    def windows(self,heading,text):return [text]
    def encode(self,texts):
        self.batches.append(len(texts))
        if self.cancel is not None:self.cancel.set()
        return np.tile(np.array([[1.,0.]]),(len(texts),1))
    def query(self,text):return np.array([1.,0.])


class IndexProgressTest(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.folder=self.root/'materials';self.folder.mkdir()
        (self.folder/'initial.md').write_text('original reference',encoding='utf-8')
        self.client=Suggester(env=ENV,materials=MaterialsStore(self.folder))
        self.settings=self.root/'settings.json'
        self.controller=MaterialsController(self.client,settings_path=self.settings)

    def test_default_limits_index_all_100_synthetic_files_including_their_last_paragraph(self):
        self.root.joinpath('large').mkdir()
        for name,text in library().items():(self.root/'large'/name).write_bytes(text.encode())
        store=MaterialsStore(self.root/'large')
        self.assertEqual(store.diagnostics['indexed_files'],100)
        self.assertEqual(len(store.chunks),1600)
        self.assertTrue(any('fixture100section16' in chunk.text for chunk in store.chunks))
        self.assertFalse(store.diagnostics.get('index_limit_reached'))

    def test_single_final_file_cut_by_chunk_limit_is_explicitly_marked_partial(self):
        (self.folder/'initial.md').write_text('one\n\ntwo\n\nthree\n',encoding='utf-8')
        store=MaterialsStore(self.folder,limits=MaterialsLimits(max_chunks=2))
        self.assertEqual(len(store.chunks),2)
        self.assertEqual(store.diagnostics['partial_files'],1)
        self.assertEqual(store.diagnostics['index_limit_reached'],1)
        self.assertEqual(store.document_issues[0]['path'],'initial.md')
        self.client.replace_materials(store)
        self.assertIn('只读取了前一部分',self.controller.current()['message'])

    def test_exact_chunk_limit_without_omitted_content_is_not_marked_partial(self):
        (self.folder/'initial.md').write_text('one\n\ntwo\n',encoding='utf-8')
        store=MaterialsStore(self.folder,limits=MaterialsLimits(max_chunks=2))
        self.assertFalse(store.diagnostics.get('partial_files'))
        self.assertFalse(store.diagnostics.get('index_limit_reached'))

    def test_progress_cancellation_keeps_old_snapshot_cache_and_preferences(self):
        old=self.client.materials;cancel=threading.Event();messages=[]
        self.client.prefetch('original')
        (self.folder/'new.md').write_text('new reference',encoding='utf-8')
        def report(message):
            messages.append(message)
            if '正在读取' in message:cancel.set()
        with self.assertRaises(IndexCancelled):self.controller.reload(progress=report,cancel=cancel)
        self.assertIs(self.client.materials,old)
        self.assertFalse(self.settings.exists())
        self.assertTrue(self.client._retrieve('original')[3])
        self.assertTrue(any('1/2' in message for message in messages))

    def test_cancellation_while_waiting_for_other_operation_does_not_wait_for_that_operation(self):
        cancel=threading.Event();errors=[]
        self.controller._reload_lock.acquire()
        def work():
            try:self.controller.reload(cancel=cancel)
            except Exception as exc:errors.append(exc)
        worker=threading.Thread(target=work);worker.start()
        try:
            cancel.set();worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertIsInstance(errors[0],IndexCancelled)
        finally:
            self.controller._reload_lock.release();worker.join(1)

    def test_semantic_cancellation_stops_between_small_batches(self):
        (self.folder/'initial.md').write_text('\n\n'.join('topic '+str(i) for i in range(20)),encoding='utf-8')
        cancel=threading.Event();encoder=Encoder(cancel)
        with self.assertRaises(IndexCancelled):SemanticMaterialsStore(self.folder,encoder=encoder,cancel=cancel)
        self.assertEqual(encoder.batches,[4])

    def test_semantic_progress_finishes_all_batches_without_changing_rebuild_limits(self):
        (self.folder/'initial.md').write_text('\n\n'.join('topic '+str(i) for i in range(20)),encoding='utf-8')
        encoder=Encoder();messages=[]
        store=SemanticMaterialsStore(self.folder,encoder=encoder,progress=messages.append)
        self.assertEqual(sum(encoder.batches),20)
        self.assertTrue(all(size<=4 for size in encoder.batches))
        self.assertTrue(any('多语言' in message for message in messages))
        self.assertEqual(store.rebuild(self.folder).limits,store.limits)

    def test_real_pdf_child_with_cancel_flag_can_complete_normally(self):
        parts,_=extract_pdf(pdf_bytes(['public page']),max_chars=1000,cancel=threading.Event())
        self.assertEqual(parts[0].text,'public page')

    def test_real_blocked_pdf_child_is_terminated_on_cancel(self):
        cancel=threading.Event();processes=[];original=subprocess.Popen
        def blocked(command,**kwargs):
            process=original([sys.executable,'-I','-c','import sys,time; sys.stdin.buffer.read(4); time.sleep(30)'],**kwargs)
            processes.append(process);return process
        timer=threading.Timer(.2,cancel.set);timer.start()
        try:
            with patch('jsc_live.pdf_text.subprocess.Popen',side_effect=blocked),self.assertRaises(IndexCancelled):
                extract_pdf(b'TEST',max_chars=1000,cancel=cancel)
        finally:timer.join()
        self.assertEqual(len(processes),1)
        self.assertIsNotNone(processes[0].poll())

    def make_view(self):
        view=MaterialsWindow.__new__(MaterialsWindow)
        view.controller=self.controller;view.results=queue.Queue();view.busy=True
        view.cancel=threading.Event();view.actions=[Mock(),Mock(),Mock()]
        view.status,view.top,view.folder=Mock(),Mock(),Mock()
        view.progress,view.progress_row,view.cancel_button=Mock(),Mock(),Mock()
        return view

    def test_progress_does_not_reenable_actions_and_cancelled_view_recovers(self):
        view=self.make_view();view.results.put(('progress','读取1/10'))
        view._poll();self.assertTrue(view.busy)
        self.assertFalse(view.actions[0].configure.called)
        view._cancel();view.results.put(('progress','stale progress'))
        view._poll();self.assertNotEqual(view.status.configure.call_args.kwargs['text'],'stale progress')
        view.results.put((False,'已取消'));view._poll()
        self.assertFalse(view.busy);view.progress.stop.assert_called_once()
        view.actions[0].configure.assert_called_once_with(state='normal')

    def test_closing_busy_view_cancels_without_destroying_or_reopening_window(self):
        view=self.make_view();view._close()
        self.assertTrue(view.cancel.is_set());view.top.withdraw.assert_called_once()
        view.top.deiconify.assert_not_called()

    def test_unexpected_background_failure_releases_busy_state_and_preserves_old_store(self):
        view=self.make_view();old=self.client.materials
        with patch.object(self.controller,'reload',side_effect=RuntimeError('synthetic failure')):view._load(None)
        view._poll();self.assertFalse(view.busy)
        self.assertIs(self.client.materials,old)
        self.assertIn('读取未完成',view.status.configure.call_args.kwargs['text'])


if __name__=='__main__':unittest.main()
