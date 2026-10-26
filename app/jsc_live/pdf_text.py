"""Read a PDF in a hidden, time-limited child, then keep immutable page text."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from .document_text import DocumentError, DocumentPart

PDF_TIMEOUT_SECONDS = 15


def extract_pdf(raw: bytes, *, max_chars: int) -> tuple[tuple[DocumentPart, ...], tuple[str, ...]]:
    try:
        result = subprocess.run(
            [sys.executable, '-I', str(Path(__file__).with_name('pdf_worker.py')), str(max_chars)],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=PDF_TIMEOUT_SECONDS, check=False,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if result.returncode != 0 or len(result.stdout) > 2 * 1024 * 1024:
            raise DocumentError('PDF读取进程未完成，请拆成较小的文件后重试。')
        data = json.loads(result.stdout)
        if not data.get('ok'):
            raise DocumentError(data['error'])
        parts = tuple(DocumentPart(**part) for part in data['parts'])
        return parts, tuple(data['notices'])
    except subprocess.TimeoutExpired:
        raise DocumentError('此PDF读取超过15秒，已停止处理；请拆成较小的文件或转成文字。') from None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, DocumentError):
            raise
        raise DocumentError('PDF读取未完成，请检查应用依赖或重新导出文档。') from None
