"""External Word documents, provenance and bounded parsing, using synthetic data."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from docx_helpers import W, docx_bytes, paragraph, table
from jsc_live.document_text import DocumentError, extract_docx
from jsc_live.materials import MaterialsLimits, MaterialsStore, source_location
from jsc_live.material_control import MaterialsController
from jsc_live.material_view import MaterialsWindow, source_text
from jsc_live.suggest import Suggester, resolve_citations
from jsc_live.session_review import _turn_html

ENV = {'LLM_API_KEY': 'fake', 'LLM_BASE_URL': 'https://example.invalid', 'LLM_MODEL': 'unused'}


class DocumentExtractionTest(unittest.TestCase):
    def extract(self, body, **kwargs):
        return extract_docx(docx_bytes(body, **kwargs), max_chars=100000)

    def test_body_paragraphs_and_table_rows_keep_stable_human_locations(self):
        body = paragraph('講座', 'Heading1') + paragraph('提出は金曜日です。') + '<w:p/>'
        body += table([['機材', '期間'], ['マイク', '一週間']]) + paragraph('予約は前日です。')
        parts, notes = self.extract(body)
        self.assertEqual([p.location for p in parts], ['第1段', '第2段', '表1・第1行', '表1・第2行', '第4段'])
        self.assertEqual(parts[3].text, 'マイク | 一週間')
        self.assertEqual(parts[3].heading, '講座 / 機材 | 期間')
        self.assertEqual(notes, ())

    def test_visible_inserted_and_link_text_without_deleted_hidden_or_field_instructions(self):
        body = '<w:p><w:r><w:t>現在</w:t></w:r><w:del><w:r><w:delText>OLD</w:delText></w:r></w:del>'
        body += '<w:moveFrom><w:r><w:t>MOVED</w:t></w:r></w:moveFrom>'
        body += '<w:r><w:rPr><w:vanish/></w:rPr><w:t>HIDDEN</w:t></w:r>'
        body += '<w:r><w:rPr><w:vanish w:val="false"/></w:rPr><w:t>表示</w:t></w:r>'
        body += '<w:r><w:instrText>REMOTE_FIELD</w:instrText></w:r>'
        body += '<w:hyperlink><w:r><w:t>リンク表示</w:t></w:r></w:hyperlink>'
        body += '<w:ins><w:r><w:t>追加</w:t></w:r></w:ins></w:p>'
        parts, _ = self.extract(body)
        self.assertEqual(parts[0].text, '現在表示リンク表示追加')

    def test_japanese_ruby_keeps_base_without_duplicate_pronunciation(self):
        body = '<w:p><w:ruby><w:rt><w:r><w:t>とうきょう</w:t></w:r></w:rt>'
        body += '<w:rubyBase><w:r><w:t>東京</w:t></w:r></w:rubyBase></w:ruby></w:p>'
        self.assertEqual(self.extract(body)[0][0].text, '東京')

    def test_only_main_document_is_opened_without_relationships_comments_or_embedded_files(self):
        extra = {'word/comments.xml': 'COMMENT_SENTINEL', 'word/header1.xml': 'HEADER_SENTINEL',
                 'word/_rels/document.xml.rels': '<Relationship Target="https://example.invalid/private"/>',
                 '../escaped.txt': 'OUTSIDE_SENTINEL', 'word/embeddings/thing.bin': 'EMBED_SENTINEL'}
        original = ZipFile.open
        opened = []
        raw = docx_bytes(paragraph('公開資料'), extra)
        def track(package, name, *args, **kwargs):
            opened.append(name.filename if hasattr(name, 'filename') else name)
            return original(package, name, *args, **kwargs)
        with patch.object(ZipFile, 'open', new=track):
            parts, _ = extract_docx(raw, max_chars=100)
        self.assertEqual(opened, ['word/document.xml'])
        self.assertEqual([part.text for part in parts], ['公開資料'])

    def test_merged_or_nested_tables_and_drawings_have_visible_limit_notices(self):
        merged = table([['種類', '説明'], ['merged', 'value']]).replace('<w:tc>', '<w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>', 1)
        body = paragraph('前の段落') + merged + '<w:p><w:r><w:drawing><w:t>IMAGE</w:t></w:drawing></w:r></w:p>'
        parts, notes = self.extract(body)
        self.assertEqual([p.text for p in parts], ['前の段落'])
        self.assertTrue(any('合并' in n for n in notes))
        self.assertTrue(any('图片' in n for n in notes))

    def test_xml_entities_rejected_in_utf8_and_utf16_before_expansion(self):
        xml = '<!DOCTYPE document [<!ENTITY secret "ENTITY_SENTINEL">]>' + f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>&secret;</w:t></w:r></w:p></w:body></w:document>'
        for encoding in ('utf-8', 'utf-16'):
            with self.subTest(encoding=encoding), self.assertRaises(DocumentError):
                extract_docx(docx_bytes('', {'word/document.xml': xml.encode(encoding)}), max_chars=1000)

    def test_xml_depth_size_and_extracted_char_limits_fail_without_partial_document(self):
        with self.assertRaises(DocumentError):
            self.extract('<w:ins>' * 130 + paragraph('deep') + '</w:ins>' * 130)
        with patch('jsc_live.document_text.MAX_XML_BYTES', 60), self.assertRaises(DocumentError):
            self.extract(paragraph('text'))
        with self.assertRaises(DocumentError):
            extract_docx(docx_bytes(paragraph('abcde') + paragraph('fghij')), max_chars=6)

    def test_empty_or_image_only_documents_explain_why_no_text(self):
        parts, notes = self.extract('<w:p/>')
        self.assertEqual(parts, ())
        self.assertTrue(any('没有可提取' in note for note in notes))

    def test_damaged_packages_and_xml_do_not_expose_parser_contents(self):
        for raw in (b'not a zip', docx_bytes('', {'word/document.xml': '<bad PRIVATE_SENTINEL'})):
            with self.assertRaises(DocumentError) as caught:
                extract_docx(raw, max_chars=1000)
            self.assertNotIn('PRIVATE_SENTINEL', str(caught.exception))


class DocumentIntegrationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.folder = self.root/'materials'
        self.folder.mkdir()

    def put(self, name, body):
        path = self.folder/name
        path.write_bytes(docx_bytes(body))
        return path

    def test_docx_sources_survive_prompt_citation_view_and_review(self):
        self.put('manual.docx', paragraph('提出形式はMP3です。'))
        store = MaterialsStore(self.folder)
        sources = json.loads(store.prompt_data(store.search('提出形式')))['sources']
        self.assertEqual(len(sources), 1)
        source = sources[0] | {'id': 'S1'}
        self.assertEqual(source['location'], '第1段')
        self.assertEqual(source['owner_hint'], 'unverified')
        reason, ids = resolve_citations('提出案内 [S1]', [source])
        self.assertIn('manual.docx:第1段', reason)
        self.assertIn('manual.docx:第1段', source_text('', [source], ids))
        html = _turn_html({'sources': [source], 'cited_source_ids': ids}, 1, set())
        self.assertIn('manual.docx:第1段', html)

    def test_document_limit_is_separate_from_small_utf8_file_limit(self):
        self.put('manual.docx', paragraph('提出形式はMP3です。'))
        (self.folder/'note.txt').write_text('submitted material', encoding='utf-8')
        store = MaterialsStore(self.folder, limits=MaterialsLimits(max_file_bytes=5))
        self.assertEqual({c.relative_path for c in store.chunks}, {'manual.docx'})
        limited = MaterialsStore(self.folder, limits=MaterialsLimits(max_document_bytes=10))
        self.assertEqual({c.relative_path for c in limited.chunks}, {'note.txt'})
        self.assertTrue(limited.document_issues)

    def test_corrupt_document_does_not_block_valid_neighbor_and_names_the_problem(self):
        (self.folder/'broken.docx').write_bytes(b'broken')
        self.put('valid.docx', paragraph('提出形式はMP3です。'))
        store = MaterialsStore(self.folder)
        client = Suggester(env=ENV, materials=store)
        info = MaterialsController(client, settings_path=self.root/'settings.json').current()
        self.assertEqual(info['files'], 1)
        self.assertEqual(info['issues'][0]['path'], 'broken.docx')
        self.assertIn('未完整读取', info['message'])
        self.assertNotIn('broken.docx', store.prompt_data(store.search('提出形式')))

    def test_repeated_paragraphs_have_distinct_ids_and_reload_preserves_old_snapshot(self):
        path = self.put('manual.docx', paragraph('提出形式はMP3です。') * 2)
        old = MaterialsStore(self.folder)
        self.assertEqual(len({c.source_id for c in old.chunks}), 2)
        self.assertEqual(old.chunks, old.rebuild(self.folder).chunks)
        path.write_bytes(docx_bytes(paragraph('提出形式はTXTです。')))
        fresh = old.rebuild(self.folder)
        self.assertIn('MP3', old.prompt_data(old.search('提出形式')))
        self.assertIn('TXT', fresh.prompt_data(fresh.search('提出形式')))

    def test_text_payloads_keep_schema_and_original_line_citations(self):
        (self.folder/'note.md').write_text('# Topic\n\n提出形式はMP3です。\n', encoding='utf-8')
        source = json.loads(MaterialsStore(self.folder).prompt_data(MaterialsStore(self.folder).search('提出形式')))['sources'][0]
        self.assertNotIn('location', source)
        self.assertEqual(source_location(source, short=True), 'note.md:3')

    def test_location_cannot_be_forged_in_prompt_data(self):
        self.put('manual.docx', paragraph('提出形式はMP3です。'))
        store = MaterialsStore(self.folder)
        hit = replace(store.search('提出形式')[0], location='FORGED', text='FORGED')
        self.assertNotIn('FORGED', store.prompt_data([hit]))

    def test_suggestion_request_uses_docx_and_retains_its_source_after_document_edit(self):
        path = self.put('manual.docx', paragraph('提出形式はMP3です。'))
        client = Suggester(env=ENV, materials=MaterialsStore(self.folder))
        sent = []
        def stream(messages):
            sent.append(messages[-1]['content'])
            path.write_bytes(docx_bytes(paragraph('提出形式はTXTです。')))
            client.replace_materials(client.materials.rebuild(self.folder))
            yield '推荐|丁寧\n丁寧|MP3形式です。|是MP3。|\nカジュアル|MP3だよ。|是MP3。|\nかんたん|MP3です。|是MP3。|\n理由|提出案内 [S1]\n'
        client._stream = stream
        result = client.suggest('提出形式', [])
        self.assertIn('MP3', sent[0])
        self.assertIn('MP3', result.sources[0]['text'])
        self.assertIn('manual.docx:第1段', result.reason)
        self.assertEqual(result.sources[0]['location'], '第1段')
        self.assertIn('TXT', client.materials.prompt_data(client.materials.search('提出形式')))

    def test_view_displays_import_notices_separately_from_api_sources(self):
        (self.folder/'broken.docx').write_bytes(b'broken')
        client = Suggester(env=ENV, materials=MaterialsStore(self.folder))
        view = MaterialsWindow.__new__(MaterialsWindow)
        view.controller = MaterialsController(client, settings_path=self.root/'settings.json')
        view.materials_root, view._last_sources = str(self.folder), ([], [], None)
        view.text = Mock()
        view._render_sources()
        text = ''.join(call.args[1] for call in view.text.insert.call_args_list)
        self.assertIn('broken.docx', text)
        self.assertIn('不随问题发送', text)


if __name__ == '__main__':
    unittest.main()
