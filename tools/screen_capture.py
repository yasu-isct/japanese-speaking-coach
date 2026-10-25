"""Screen capture helpers for the showcase media (Windows only, standard library + numpy).

- grab(x, y, w, h) -> HxWx3 uint8 RGB array of that screen rectangle (GDI BitBlt, physical pixels).
- save_png(path, rgb): minimal PNG writer (zlib), so no Pillow is needed in the jsc env.
- window_rect(title): screen rectangle of a top-level window found by its exact title.
"""

from __future__ import annotations

import ctypes
import struct
import zlib
from ctypes import wintypes
from pathlib import Path

import numpy as np

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
SRCCOPY = 0x00CC0020


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def grab(x: int, y: int, w: int, h: int) -> np.ndarray:
    screen = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(mem, 0, 0, w, h, screen, x, y, SRCCOPY):
            raise OSError("BitBlt failed (is the screen locked?)")
        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth, header.biHeight = w, -h  # negative height: top-down rows
        header.biPlanes, header.biBitCount = 1, 32
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(header), 0)
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(0, screen)
    bgra = np.frombuffer(buf.raw, np.uint8).reshape(h, w, 4)
    return bgra[:, :, 2::-1].copy()  # BGRA -> RGB


def save_png(path: Path, rgb: np.ndarray) -> None:
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[row].tobytes() for row in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    Path(path).write_bytes(png)


def find_window(title_prefix: str) -> int:
    """Handle of a visible top-level window whose title starts with ``title_prefix`` (0 if none)."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value.startswith(title_prefix):
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(callback, 0)
    return found[0] if found else 0


def hwnd_rect(hwnd: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) of a window including its title bar and frame."""
    rect = wintypes.RECT()
    # DWM extended frame bounds = the visible window without the invisible resize border.
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def window_rect(title: str) -> tuple[int, int, int, int] | None:
    """(x, y, w, h) of the window with this exact title, including its frame; None if not found."""
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return None
    rect = wintypes.RECT()
    # DWM extended frame bounds = the visible window without the invisible resize border.
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
