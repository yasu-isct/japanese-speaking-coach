"""Read a PDF in a hidden, time-limited child, then keep immutable page text."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

from .document_text import DocumentError, DocumentPart
from .index_task import IndexCancelled, check_cancel

PDF_TIMEOUT_SECONDS = 15


def _run(command, raw, cancel):
    options = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if cancel is None:
        options.pop('stdin')
        return subprocess.run(command, input=raw, timeout=PDF_TIMEOUT_SECONDS, check=False, **options)
    check_cancel(cancel)
    with subprocess.Popen(command, **options) as process:
        deadline = time.monotonic() + PDF_TIMEOUT_SECONDS
        data = raw
        try:
            while True:
                check_cancel(cancel)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, PDF_TIMEOUT_SECONDS)
                try:
                    output, _ = process.communicate(input=data, timeout=min(.1, remaining))
                    check_cancel(cancel)
                    return subprocess.CompletedProcess(command, process.returncode, output)
                except subprocess.TimeoutExpired:
                    data = None  # communicate resumes its buffers; do not send the file twice.
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()


def extract_pdf(raw: bytes, *, max_chars: int, cancel=None) -> tuple[tuple[DocumentPart, ...], tuple[str, ...]]:
    try:
        result = _run([sys.executable, '-I', str(Path(__file__).with_name('pdf_worker.py')), str(max_chars)], raw, cancel)
        if result.returncode != 0 or len(result.stdout) > 2 * 1024 * 1024:
            raise DocumentError('PDF读取进程未完成，请拆成较小的文件后重试。')
        data = json.loads(result.stdout)
        if not data.get('ok'):
            raise DocumentError(data['error'])
        parts = tuple(DocumentPart(**part) for part in data['parts'])
        return parts, tuple(data['notices'])
    except subprocess.TimeoutExpired:
        raise DocumentError('此PDF读取超过15秒，已停止处理；请拆成较小的文件或转成文字。') from None
    except IndexCancelled:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, DocumentError):
            raise
        raise DocumentError('PDF读取未完成，请检查应用依赖或重新导出文档。') from None
