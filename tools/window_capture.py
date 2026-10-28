"""Capture one HWND owned by this process; never copy pixels from the desktop."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys


def _apis():
    if sys.platform != "win32":
        raise RuntimeError("Window capture is supported only on Windows")
    user = ctypes.WinDLL("user32", use_last_error=True)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    signatures = {
        "GetWindowThreadProcessId": ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        "GetAncestor": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
        "GetClientRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        "GetDC": ([wintypes.HWND], wintypes.HDC),
        "ReleaseDC": ([wintypes.HWND, wintypes.HDC], ctypes.c_int),
        "PrintWindow": ([wintypes.HWND, wintypes.HDC, wintypes.UINT], wintypes.BOOL),
        "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(user, name)
        fn.argtypes, fn.restype = args, result
    gdi_signatures = {
        "CreateCompatibleDC": ([wintypes.HDC], wintypes.HDC),
        "CreateCompatibleBitmap": ([wintypes.HDC, ctypes.c_int, ctypes.c_int], wintypes.HBITMAP),
        "SelectObject": ([wintypes.HDC, wintypes.HANDLE], wintypes.HANDLE),
        "DeleteObject": ([wintypes.HANDLE], wintypes.BOOL),
        "DeleteDC": ([wintypes.HDC], wintypes.BOOL),
        "PatBlt": ([wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD], wintypes.BOOL),
        "GetDIBits": ([wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT], ctypes.c_int),
    }
    for name, (args, result) in gdi_signatures.items():
        fn = getattr(gdi, name)
        fn.argtypes, fn.restype = args, result
    return user, gdi


def capture_owned_window(root, out: Path) -> dict:
    """Request this process's own window to paint to memory; fail on blank output.

    This can block while the window paints. Use only in explicit developer
    captures, after timed work; it is not a latency measurement.
    """
    return grab_owned_window(root, out)


def grab_owned_window(root, out: Path | None = None):
    """Same as capture_owned_window; with out=None return the RGB array instead of saving.

    Used for frame-by-frame recording of an offscreen window (tools/capture_latest_showcase.py).
    """
    import numpy as np

    user, gdi = _apis()
    root.update_idletasks()
    hwnd = user.GetAncestor(root.winfo_id(), 2)
    owner = wintypes.DWORD()
    if not hwnd or not user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner)) or owner.value != os.getpid():
        raise RuntimeError("Refusing to capture a window not owned by this process")
    rect = wintypes.RECT()
    if not user.GetClientRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    width, height = rect.right, rect.bottom
    if not (20 <= width <= 8192 and 20 <= height <= 8192):
        raise RuntimeError("Window has no capturable client area")

    from screen_capture import BITMAPINFOHEADER, save_png

    dc = user.GetDC(hwnd)
    memory = bitmap = previous = None
    try:
        if not dc:
            raise RuntimeError("Could not obtain the owned window DC")
        memory = gdi.CreateCompatibleDC(dc)
        bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
        if not memory or not bitmap:
            raise RuntimeError("Could not allocate a capture bitmap")
        previous = gdi.SelectObject(memory, bitmap)
        if not previous or previous == ctypes.c_void_p(-1).value:
            raise RuntimeError("Could not select the capture bitmap")
        gdi.PatBlt(memory, 0, 0, width, height, 0x00000042)  # BLACKNESS, no source pixels
        if not user.PrintWindow(hwnd, memory, 1):  # PW_CLIENTONLY
            raise RuntimeError("The owned window did not render; no desktop fallback")
        gdi.SelectObject(memory, previous)
        previous = None
        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(header)
        header.biWidth, header.biHeight = width, -height
        header.biPlanes, header.biBitCount = 1, 32
        buffer = ctypes.create_string_buffer(width * height * 4)
        if gdi.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(header), 0) != height:
            raise RuntimeError("The owned window bitmap could not be read")
        rgb = np.frombuffer(buffer.raw, np.uint8).reshape(height, width, 4)[:, :, 2::-1].copy()
        if not np.any(rgb != rgb[0, 0]):
            raise RuntimeError("The owned window rendered a blank image; no desktop fallback")
        # Validate ownership again before writing. No title search or other HWND
        # is ever accepted, even if the caller's window was destroyed meanwhile.
        if not user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner)) or owner.value != os.getpid():
            raise RuntimeError("Window ownership changed during capture")
        if out is None:
            return rgb
        out = Path(out)
        if out.exists():
            raise FileExistsError("Capture destination already exists")
        out.parent.mkdir(parents=True, exist_ok=True)
        save_png(out, rgb)
        return {"method": "PrintWindow owned HWND", "width": width, "height": height,
                "visible": bool(user.IsWindowVisible(hwnd)), "desktop_pixels_copied": False}
    finally:
        if previous and memory:
            gdi.SelectObject(memory, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if memory:
            gdi.DeleteDC(memory)
        if dc:
            user.ReleaseDC(hwnd, dc)
