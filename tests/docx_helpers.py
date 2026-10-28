"""Small, synthetic Word packages for parser tests; no Office or user documents."""
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
from xml.sax.saxutils import escape

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def paragraph(text, style=None):
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ''
    return f'<w:p>{props}<w:r><w:t>{escape(text)}</w:t></w:r></w:p>'


def table(rows):
    return '<w:tbl>' + ''.join(
        '<w:tr>' + ('<w:trPr><w:tblHeader/></w:trPr>' if i == 0 else '')
        + ''.join('<w:tc>' + paragraph(cell) + '</w:tc>' for cell in row) + '</w:tr>'
        for i, row in enumerate(rows)) + '</w:tbl>'


def docx_bytes(body, extras=None):
    items = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>',
        'word/document.xml': f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W}"><w:body>{body}<w:sectPr/></w:body></w:document>',
    }
    items.update(extras or {})
    output = BytesIO()
    with ZipFile(output, 'w') as package:
        for name, value in items.items():
            info = ZipInfo(name, (2026, 10, 26, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            package.writestr(info, value)
    return output.getvalue()
