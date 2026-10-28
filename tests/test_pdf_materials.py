"""PDF parsing, physical-page sources and failure isolation; no UI or network."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'app'))
from pdf_helpers import pdf_bytes
from jsc_live.document_text import DocumentError
from jsc_live.pdf_text import extract_pdf
from jsc_live.pdf_worker import parse_pdf
from jsc_live.materials import MaterialsStore, MaterialsLimits
from jsc_live.suggest import Suggester, resolve_citations
from jsc_live.material_control import MaterialsController
from jsc_live.material_view import MaterialsWindow, source_text
from jsc_live.session_review import _turn_html

ENV = {'LLM_API_KEY':'fake','LLM_BASE_URL':'https://example.invalid','LLM_MODEL':'unused'}


class PdfExtractionTest(unittest.TestCase):
    def test_public_japanese_pdf_text_and_physical_pages_through_real_worker(self):
        raw = (ROOT/'tests/fixtures/materials/pdf/data/course.pdf').read_bytes()
        parts, notices = extract_pdf(raw, max_chars=100000)
        self.assertEqual([part.location for part in parts], ['第1页', '第2页'])
        self.assertIn('WAV', parts[0].text)
        self.assertIn('四十八時間', parts[1].text)
        self.assertEqual(notices, ())

    def test_empty_middle_page_does_not_renumber_following_text_page(self):
        parts, notices = extract_pdf(pdf_bytes(['first page', '', 'third page']), max_chars=1000)
        self.assertEqual([p.location for p in parts], ['第1页','第3页'])
        self.assertEqual(parts[1].text, 'third page')
        self.assertTrue(any('第2页' in notice for notice in notices))

    def test_image_only_page_is_reported_without_inventing_text_or_ocr(self):
        raw = (ROOT/'tests/fixtures/materials/pdf/data/scan-only.pdf').read_bytes()
        parts, notices = extract_pdf(raw, max_chars=1000)
        self.assertEqual(parts, ())
        self.assertTrue(any('扫描' in notice and '第1页' in notice for notice in notices))

    def test_encrypted_pdf_is_skipped_without_prompting_for_password(self):
        with self.assertRaisesRegex(DocumentError, '加密'):
            extract_pdf(pdf_bytes(['secret fixture'], password='synthetic-password'), max_chars=1000)

    def test_zero_page_document_explains_why_it_cannot_be_read(self):
        result = parse_pdf(pdf_bytes([]),1000)
        self.assertFalse(result['ok'])
        self.assertIn('没有可读取的页面',result['error'])

    def test_page_character_and_content_limits_do_not_publish_partial_documents(self):
        raw = pdf_bytes(['first', 'second'])
        with patch('jsc_live.pdf_worker.MAX_PAGES',1):
            self.assertFalse(parse_pdf(raw,1000)['ok'])
        chars = parse_pdf(raw,6)
        self.assertFalse(chars['ok'])
        self.assertNotIn('parts', chars)
        with patch('jsc_live.pdf_worker.MAX_PAGE_STREAM_BYTES',1):
            streams = parse_pdf(raw,1000)
        self.assertFalse(streams['ok'])
        self.assertNotIn('parts',streams)

    def test_timeout_and_abnormal_child_exit_have_actionable_errors(self):
        with patch('jsc_live.pdf_text.subprocess.run', side_effect=subprocess.TimeoutExpired('fixture',15)):
            with self.assertRaisesRegex(DocumentError,'15秒'):
                extract_pdf(b'',max_chars=1000)
        with patch('jsc_live.pdf_text.subprocess.run', return_value=subprocess.CompletedProcess([],3,b'bad')):
            with self.assertRaisesRegex(DocumentError,'未完成'):
                extract_pdf(b'',max_chars=1000)

    def test_missing_dependency_does_not_disable_other_material_formats(self):
        with patch.dict(sys.modules, {'pypdf':None}):
            result = parse_pdf(b'',1000)
        self.assertFalse(result['ok'])
        self.assertIn('更新应用依赖',result['error'])

    def test_worker_gets_bytes_without_shell_file_path_or_interactive_window(self):
        complete = subprocess.CompletedProcess([],0,b'{"ok":true,"parts":[],"notices":[]}')
        with patch('jsc_live.pdf_text.subprocess.run',return_value=complete) as run:
            extract_pdf(b'SYNTHETIC_BYTES',max_chars=1000)
        args,kwargs = run.call_args
        self.assertEqual(kwargs['input'],b'SYNTHETIC_BYTES')
        self.assertEqual(kwargs['stderr'],subprocess.DEVNULL)
        self.assertEqual(kwargs['creationflags'],getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertFalse(kwargs.get('shell',False))
        self.assertIn('-I',args[0])


class PdfIntegrationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.folder = self.root/'materials'
        self.folder.mkdir()

    def test_bad_pdf_does_not_block_neighbor_and_filename_is_reported(self):
        (self.folder/'broken.pdf').write_bytes(b'not a PDF; PRIVATE_SENTINEL')
        (self.folder/'valid.md').write_text('router instructions',encoding='utf-8')
        store = MaterialsStore(self.folder)
        client = Suggester(env=ENV,materials=store)
        info = MaterialsController(client,settings_path=self.root/'settings.json').current()
        self.assertEqual(info['files'],1)
        self.assertEqual(info['issues'][0]['path'],'broken.pdf')
        self.assertNotIn('PRIVATE_SENTINEL',str(info))
        self.assertTrue(store.search('router'))

    def test_pdf_page_sources_flow_into_prompt_reason_view_and_review(self):
        (self.folder/'manual.pdf').write_bytes(pdf_bytes(['start page','router instructions']))
        store = MaterialsStore(self.folder)
        sources = json.loads(store.prompt_data(store.search('router')))['sources']
        source = sources[0] | {'id':'S1'}
        self.assertEqual(source['location'],'第2页')
        reason,ids = resolve_citations('手册说明 [S1]',[source])
        self.assertIn('manual.pdf:第2页',reason)
        self.assertIn('manual.pdf:第2页',source_text('',[source],ids))
        self.assertIn('manual.pdf:第2页',_turn_html({'sources':[source],'cited_source_ids':ids},1,set()))

    def test_search_never_reopens_pdf_and_refresh_retains_old_page_snapshot(self):
        path = self.folder/'manual.pdf'
        path.write_bytes(pdf_bytes(['router wait twenty seconds']))
        old = MaterialsStore(self.folder)
        with patch('jsc_live.pdf_text.subprocess.run',side_effect=AssertionError('no parsing during search')):
            self.assertIn('twenty',old.prompt_data(old.search('router')))
        path.write_bytes(pdf_bytes(['router wait thirty seconds']))
        new = old.rebuild(self.folder)
        self.assertIn('twenty',old.prompt_data(old.search('router')))
        self.assertIn('thirty',new.prompt_data(new.search('router')))
        self.assertNotEqual(old.chunks[0].source_id,new.chunks[0].source_id)

    def test_identical_text_on_different_pages_has_distinct_source_ids(self):
        (self.folder/'manual.pdf').write_bytes(pdf_bytes(['router instructions']*2))
        store = MaterialsStore(self.folder)
        self.assertEqual(len({c.source_id for c in store.chunks}),2)
        self.assertEqual({c.location for c in store.chunks},{'第1页','第2页'})

    def test_file_size_limit_applies_before_starting_pdf_process(self):
        (self.folder/'manual.pdf').write_bytes(pdf_bytes(['router instructions']))
        with patch('jsc_live.pdf_text.subprocess.run',side_effect=AssertionError('oversized document must not start')):
            store = MaterialsStore(self.folder,limits=MaterialsLimits(max_document_bytes=10))
        self.assertEqual(store.chunks,())
        self.assertTrue(store.document_issues)

    def test_found_passage_precedes_unrelated_scan_notice_in_source_window(self):
        source = {'source_id':'public','relative_path':'manual.pdf','start_line':1,'end_line':1,
                  'location':'第2页','text':'router instructions'}
        view = MaterialsWindow.__new__(MaterialsWindow)
        view.materials_root = 'public folder'
        view._last_sources = ([source], ['public'], None)
        view.text = Mock()
        view.controller = Mock()
        view.controller.current.return_value = {'issues':[{'path':'scan-only.pdf','message':'请先转成文字'}]}
        view._render_sources()
        text = ''.join(call.args[1] for call in view.text.insert.call_args_list)
        self.assertLess(text.index('router instructions'), text.index('scan-only.pdf'))
        self.assertIn('请先转成文字',text)


if __name__=='__main__': unittest.main()
