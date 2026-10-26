"""Bounded local DOCX body extraction. Never opens Office, links or attachments."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re
import zlib
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

WORD_NAMESPACES = ('http://schemas.openxmlformats.org/wordprocessingml/2006/main',
                   'http://purl.oclc.org/ooxml/wordprocessingml/main')
MAX_XML_BYTES = 4 * 1024 * 1024
MAX_ZIP_ENTRIES = 2048


class DocumentError(ValueError):
    """A short user-facing reason; never includes source content or parser dumps."""


@dataclass(frozen=True)
class DocumentPart:
    text: str
    location: str
    heading: str = ''


class _BoundedTree(ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.depth = self.nodes = 0

    def doctype(self, name, pubid, system):
        raise DocumentError('包含不支持的XML声明，请另存为新的docx。')

    def start(self, tag, attrs):
        self.depth += 1
        self.nodes += 1
        if self.depth > 128 or self.nodes > 100000:
            raise DocumentError('文档结构过大，请拆成较小的docx。')
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)


def extract_docx(raw: bytes, *, max_chars: int) -> tuple[tuple[DocumentPart, ...], tuple[str, ...]]:
    """Read only word/document.xml, with limits before decompression and parsing.

    Locations count body paragraphs (including headings/empty paragraphs) and
    top-level tables. They are not Word's layout-dependent page/line numbers.
    """
    try:
        with ZipFile(BytesIO(raw)) as package:
            entries = package.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise DocumentError('文档内部文件过多，请拆成较小的docx。')
            matches = [info for info in entries if info.filename == 'word/document.xml']
            if len(matches) != 1:
                raise DocumentError('不是可读取的docx正文，请用Word另存为docx。')
            info = matches[0]
            if info.flag_bits & 1:
                raise DocumentError('加密文档无法读取，请提供未加密的副本。')
            if info.file_size > MAX_XML_BYTES:
                raise DocumentError('解压后的正文超过4MB，请拆成较小的docx。')
            with package.open(info) as stream:
                xml = stream.read(MAX_XML_BYTES + 1)
            if len(xml) > MAX_XML_BYTES:
                raise DocumentError('解压后的正文超过4MB，请拆成较小的docx。')
        root = ET.fromstring(xml, parser=ET.XMLParser(target=_BoundedTree()))
    except DocumentError:
        raise
    except (BadZipFile, KeyError, RuntimeError, NotImplementedError, OSError, EOFError, zlib.error, ET.ParseError, ValueError):
        raise DocumentError('docx损坏、已加密或格式不支持，请另存为新的docx。') from None
    namespace = next((ns for ns in WORD_NAMESPACES if root.tag == '{' + ns + '}document'), None)
    if namespace is None:
        raise DocumentError('无法识别Word正文格式，请另存为新的docx。')
    w = '{' + namespace + '}'
    body = root.find(w + 'body')
    if body is None:
        raise DocumentError('文档没有可读取的正文。')
    notices = set()
    parts = []
    headings = []
    paragraph_count = table_count = total_chars = 0

    def on(element):
        return element is not None and element.get(w + 'val', 'true').casefold() not in ('false', '0', 'off')

    def visible_text(element):
        if element.tag in {w + name for name in ('del', 'moveFrom', 'instrText', 'delText', 'rt')}:
            return ''
        if element.tag in {w + name for name in ('drawing', 'pict', 'object', 'altChunk')}:
            notices.add('图片、文本框或嵌入内容未读取；需要时请另存为纯文字。')
            return ''
        if element.tag == w + 'r':
            props = element.find(w + 'rPr')
            if props is not None and any(on(props.find(w + key)) for key in ('vanish', 'webHidden')):
                return ''
        if element.tag == w + 't':
            return element.text or ''
        if element.tag in (w + 'tab', w + 'br', w + 'cr'):
            return ' '
        return ''.join(visible_text(child) for child in element)

    def add(text, location, extra_heading=''):
        nonlocal total_chars
        text = text.strip()
        if not text:
            return
        total_chars += len(text)
        if total_chars > max_chars or len(parts) >= 4096:
            raise DocumentError('正文文字超过资料容量，请拆成较小的文件。')
        heading = ' / '.join(value for _, value in headings)
        if extra_heading:
            heading += (' / ' if heading else '') + extra_heading
        parts.append(DocumentPart(text, location, heading[:160]))

    def walk(container):
        nonlocal paragraph_count, table_count, headings
        for item in container:
            if item.tag == w + 'p':
                paragraph_count += 1
                text = visible_text(item)
                style = item.find(w + 'pPr/' + w + 'pStyle')
                value = style.get(w + 'val', '') if style is not None else ''
                match = re.fullmatch(r'(?:Heading|标题|標題)\s*([1-6])', value, re.IGNORECASE)
                if match and text.strip():
                    level = int(match[1])
                    headings = [(depth, value) for depth, value in headings if depth < level]
                    headings.append((level, text.strip()))
                add(text, f'第{paragraph_count}段')
            elif item.tag == w + 'tbl':
                table_count += 1
                if (item.find('.//' + w + 'tbl') is not None
                        or item.find('.//' + w + 'vMerge') is not None
                        or any(int(span.get(w + 'val', '1')) != 1 for span in item.iter(w + 'gridSpan'))):
                    notices.add(f'表{table_count}有合并单元格或嵌套表格，未读取；请整理为简单表格或文字。')
                    continue
                header = ''
                for number, row in enumerate(item.findall(w + 'tr'), 1):
                    cells = [' '.join(visible_text(p).strip() for p in cell.findall(w + 'p')).strip()
                             for cell in row.findall(w + 'tc')]
                    line = ' | '.join(cells)
                    if number == 1 and on(row.find(w + 'trPr/' + w + 'tblHeader')):
                        header = line
                    add(line, f'表{table_count}・第{number}行', header if number > 1 else '')
            elif item.tag == w + 'sdt':
                content = item.find(w + 'sdtContent')
                if content is not None:
                    walk(content)
            elif item.tag in (w + 'ins', w + 'moveTo', w + 'customXml'):
                walk(item)
            elif item.tag not in (w + 'sectPr', w + 'del', w + 'moveFrom'):
                notices.add('部分特殊正文结构未读取；需要时请另存为纯文字。')

    try:
        walk(body)
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, DocumentError):
            raise
        raise DocumentError('正文结构无法读取，请另存为新的docx。') from None
    if not parts:
        notices.add('没有可提取的正文文字；图片或扫描件请先转成文字。')
    return tuple(parts), tuple(sorted(notices))
