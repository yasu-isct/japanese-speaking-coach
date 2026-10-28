"""Generate simple PDF bytes for tests using the production pure-Python parser."""
from io import BytesIO
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def pdf_bytes(pages, *, password=None):
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=595, height=842)
        if text:
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                                     NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
            lines = [line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') for line in text.splitlines()]
            stream = DecodedStreamObject()
            stream.set_data(('BT /F1 12 Tf 30 750 Td ' + ' 0 -20 Td '.join(f'({line}) Tj' for line in lines) + ' ET').encode('ascii'))
            page[NameObject('/Contents')] = stream
    if password is not None:
        writer.encrypt(password, algorithm='RC4-128')
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
