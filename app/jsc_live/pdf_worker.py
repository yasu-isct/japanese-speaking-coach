"""Isolated PDF text worker: stdin bytes -> bounded JSON, no UI/network/paths."""
from __future__ import annotations

from io import BytesIO
import json
import logging
import sys

MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_PAGES = 80
MAX_PAGE_STREAM_BYTES = 2 * 1024 * 1024
MAX_TOTAL_STREAM_BYTES = 8 * 1024 * 1024


class _ReadLimit(ValueError):
    pass


def parse_pdf(raw: bytes, max_chars: int) -> dict:
    """Used in the child process; public library limits are scoped to this call."""
    try:
        from pypdf import PdfReader, apply_configuration
    except ImportError:
        return {'ok': False, 'error': 'PDF读取组件未安装或版本过旧，请更新应用依赖（pypdf 6.21.2）。'}
    if len(raw) > MAX_INPUT_BYTES:
        return {'ok': False, 'error': 'PDF超过10MB，请拆成较小的文件。'}
    warning_count = 0

    class WarningCounter(logging.Handler):
        def emit(self, record):
            nonlocal warning_count
            if record.levelno >= logging.WARNING:
                warning_count += 1

    logger = logging.getLogger('pypdf')
    handler = WarningCounter()
    logger.addHandler(handler)
    try:
        with apply_configuration(
            maximum_declared_stream_length=MAX_TOTAL_STREAM_BYTES,
            array_based_stream_maximum_output_length=MAX_PAGE_STREAM_BYTES,
            zlib_maximum_output_length=MAX_TOTAL_STREAM_BYTES,
            lzw_maximum_output_length=MAX_TOTAL_STREAM_BYTES,
            run_length_maximum_output_length=MAX_TOTAL_STREAM_BYTES,
            brotli_maximum_output_length=MAX_TOTAL_STREAM_BYTES,
            image_maximum_buffer_size=MAX_TOTAL_STREAM_BYTES,
            stream_decoding_work_maximum_length=4 * MAX_TOTAL_STREAM_BYTES,
            stream_parsing_work_maximum_count=200000,
            array_maximum_element_count=20000, dictionary_maximum_element_count=20000,
            page_tree_maximum_entries=4096, page_tree_maximum_depth=32,
            xform_maximum_invocations_per_extraction=128, jbig2dec_binary=None,
        ):
            reader = PdfReader(BytesIO(raw), strict=True, root_object_recovery_limit=1000)
            if reader.is_encrypted:
                return {'ok': False, 'error': '加密PDF无法读取，请提供未加密的副本。'}
            page_count = len(reader.pages)
            if not page_count:
                return {'ok': False, 'error': 'PDF没有可读取的页面，请重新导出文档。'}
            if page_count > MAX_PAGES:
                return {'ok': False, 'error': 'PDF超过80页，请按章节拆成较小的文件。'}
            parts, empty_pages = [], []
            total_chars = stream_bytes = 0
            for number, page in enumerate(reader.pages, 1):
                content = page.get_contents()
                size = len(content.get_data()) if content is not None else 0
                stream_bytes += size
                if size > MAX_PAGE_STREAM_BYTES or stream_bytes > MAX_TOTAL_STREAM_BYTES:
                    raise _ReadLimit('PDF页面内容过大，请拆分或另存为文字较简洁的PDF。')
                text = (page.extract_text() or '').strip()
                if not text:
                    empty_pages.append(number)
                    continue
                total_chars += len(text)
                if total_chars > max_chars:
                    raise _ReadLimit('PDF文字超过资料容量，请按章节拆成较小的文件。')
                parts.append({'text': text, 'location': f'第{number}页', 'heading': ''})
        notices = []
        if empty_pages:
            shown = '、'.join(map(str, empty_pages[:10])) + ('等' if len(empty_pages) > 10 else '')
            notices.append(f'第{shown}页没有可提取文字（共{len(empty_pages)}页）；可能是空白或扫描页，如有图片内容请先转成文字。')
        if warning_count:
            notices.append('部分PDF字体或结构解析有提示，文字可能不完整；请核对原页或另存为文字。')
        return {'ok': True, 'parts': parts, 'notices': notices}
    except _ReadLimit as exc:
        return {'ok': False, 'error': str(exc)}
    except Exception:
        # The parent retains the other documents. Never return raw PDF/library dumps.
        return {'ok': False, 'error': 'PDF无法完整读取，可能已损坏或超过处理范围；请拆分、重新导出或转成文字。'}
    finally:
        logger.removeHandler(handler)


def main() -> None:
    try:
        max_chars = max(1, min(int(sys.argv[1]), 100000))
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        result = parse_pdf(raw, max_chars)
    except Exception:
        result = {'ok': False, 'error': 'PDF读取未完成，请重新导出或转成文字。'}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))


if __name__ == '__main__':
    main()
