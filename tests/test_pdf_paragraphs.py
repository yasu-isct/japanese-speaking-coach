"""Visual PDF boundaries must preserve original text and physical provenance."""
from io import BytesIO
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'app'))
from jsc_live.pdf_worker import layout_blocks, page_parts, parse_pdf
from jsc_live.materials import MaterialsStore, chunk_data, source_location
from jsc_live.material_view import source_text
from jsc_live.suggest import resolve_citations
from pdf_helpers import pdf_bytes


def compact(text):
    return re.sub(r'\s+', '', text)


class PdfParagraphTest(unittest.TestCase):
    def test_all_long_fixture_sections_remain_whole_and_page_character_order_is_preserved(self):
        corpus = ROOT/'tests/fixtures/materials/long_pdf'
        raw = (corpus/'data/handbook.pdf').read_bytes()
        result = parse_pdf(raw,100000)
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['parts']),28)
        self.assertEqual(result['notices'],[])
        for number,page in enumerate(PdfReader(BytesIO(raw)).pages,1):
            parts = [part for part in result['parts'] if part['location'].startswith(f'第{number}页・')]
            self.assertEqual(compact(''.join(part['text'] for part in parts)),compact(page.extract_text()))
        content = json.loads((corpus/'content.json').read_text(encoding='utf-8'))
        for number,(_,_,sections) in enumerate(content,1):
            parts = [part for part in result['parts'] if part['location'].startswith(f'第{number}页・')]
            for heading,text in sections:
                self.assertTrue(any(compact(heading+text) in compact(part['text']) for part in parts))

    def test_layout_only_supplies_boundaries_and_never_rewrites_original_words_or_spaces(self):
        text = '  ice cream  costs\t100 yen.\nsecond paragraph.  '
        layout = 'icecream costs 100yen.\n\n\nsecondparagraph.'
        self.assertEqual(layout_blocks(text,layout),['ice cream  costs\t100 yen.','second paragraph.'])

    def test_missing_or_reordered_layout_text_is_rejected(self):
        for layout in ('first\n\nthird','second\n\nfirst\n\nthird'):
            self.assertIsNone(layout_blocks('first\nsecond\nthird',layout))

    def test_excessive_layout_whitespace_or_fragment_count_is_rejected(self):
        self.assertIsNone(layout_blocks('text','text'+'\n'*9000))
        text='\n'.join(f'block{i}' for i in range(65))
        self.assertIsNone(layout_blocks(text,text.replace('\n','\n\n')))

    def test_short_page_never_runs_layout_and_keeps_original_location(self):
        page=Mock()
        parts,fallback=page_parts(page,'Short original text.',3,None)
        self.assertFalse(fallback)
        self.assertEqual(parts,[dict(text='Short original text.',location='第3页',heading='')])
        page.extract_text.assert_not_called()

    def test_optional_layout_failure_retains_the_entire_plain_page(self):
        page=Mock();page.extract_text.side_effect=RuntimeError('layout failed')
        text='Long original text. '*50
        parts,fallback=page_parts(page,text,4,Mock(operations=[]))
        self.assertTrue(fallback)
        self.assertEqual(parts[0]['text'],text)
        self.assertEqual(parts[0]['location'],'第4页')

    def test_layout_work_limits_fall_back_without_attempting_layout(self):
        page=Mock();text='original '*100
        with patch('jsc_live.pdf_worker.MAX_LAYOUT_OPERATIONS',1):
            parts,fallback=page_parts(page,text,1,Mock(operations=[1,2]))
        self.assertTrue(fallback)
        self.assertEqual(parts[0]['text'],text)
        page.extract_text.assert_not_called()
        parts,fallback=page_parts(page,'a'*20001,1,Mock(operations=[]))
        self.assertTrue(fallback)
        self.assertEqual(len(parts[0]['text']),20001)

    def test_real_rotated_text_is_not_dropped_when_layout_excludes_it(self):
        reader=PdfReader(BytesIO(pdf_bytes(['A long plain statement. '*40])))
        writer=PdfWriter();writer.add_page(reader.pages[0]);page=writer.pages[0]
        stream=DecodedStreamObject()
        stream.set_data(page.get_contents().get_data()+b'\nBT /F1 12 Tf 0 1 -1 0 300 100 Tm (ROTATED-CONTENT) Tj ET')
        page[NameObject('/Contents')]=stream
        out=BytesIO();writer.write(out)
        result=parse_pdf(out.getvalue(),10000)
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['parts']),1)
        self.assertIn('ROTATED-CONTENT',result['parts'][0]['text'])
        self.assertEqual(result['parts'][0]['location'],'第1页')
        self.assertTrue(any('段落边界' in notice for notice in result['notices']))

    def test_repeated_blocks_on_same_page_have_distinct_stable_source_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'document.pdf').write_bytes(b'fixture')
            from jsc_live.document_text import DocumentPart
            parts=tuple(DocumentPart('same paragraph',f'第1页・片段{i}','') for i in (1,2))
            with patch('jsc_live.pdf_text.extract_pdf',return_value=(parts,())):
                first=MaterialsStore(root);second=first.rebuild(root)
            self.assertEqual(len(set(c.source_id for c in first.chunks)),2)
            self.assertEqual(first.chunks,second.chunks)

    def test_physical_page_and_fragment_flow_to_citations_and_source_view(self):
        store=MaterialsStore(ROOT/'tests/fixtures/materials/long_pdf/data')
        chunk=next(c for c in store.chunks if 'WAV' in c.text)
        source=dict(chunk_data(chunk),id='S1')
        self.assertEqual(source['location'],'第3页・片段3')
        reason,cited=resolve_citations('資料[S1]を参照。',[source])
        self.assertIn(source_location(source),reason)
        self.assertEqual(cited,[source['source_id']])
        self.assertIn('第3页・片段3',source_text('',[source],cited))


if __name__=='__main__':unittest.main()
