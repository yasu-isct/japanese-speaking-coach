"""Construct test overlays hidden; explicit Windows previews never accept input.

Use only on the Tk/main thread. This helper changes test tools, not the app window.
"""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from ctypes import wintypes

GWL_EXSTYLE = -20
GA_ROOT = 2
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
PASSIVE_FLAGS = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_FRAMECHANGED = 1, 2, 0x10, 0x20


def _user32():
    api = ctypes.WinDLL("user32", use_last_error=True)
    signatures = {
        "GetAncestor": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
        "GetWindowLongW": ([wintypes.HWND, ctypes.c_int], wintypes.LONG),
        "SetWindowLongW": ([wintypes.HWND, ctypes.c_int, wintypes.LONG], wintypes.LONG),
        "SetLayeredWindowAttributes": ([wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD], wintypes.BOOL),
        "SetWindowPos": ([wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
        "GetForegroundWindow": ([], wintypes.HWND),
        "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
        "GetSystemMetrics": ([ctypes.c_int], ctypes.c_int),
        "GetWindowRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api


def _style(api, hwnd):
    ctypes.set_last_error(0)
    value = api.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if not value and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    return value & 0xFFFFFFFF


def _show_passive(root):
    """Fail closed: no show call occurs until the hidden HWND is protected."""
    api = _user32()
    before = api.GetForegroundWindow()
    root.update_idletasks()
    hwnd = api.GetAncestor(root.winfo_id(), GA_ROOT)
    if not hwnd or root.state() != "withdrawn" or api.IsWindowVisible(hwnd):
        raise RuntimeError("Passive preview requires an already hidden top-level HWND")
    desired = _style(api, hwnd) | PASSIVE_FLAGS
    ctypes.set_last_error(0)
    previous = api.SetWindowLongW(hwnd, GWL_EXSTYLE, desired)
    if not previous and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    alpha = max(0, min(255, round(float(root.attributes("-alpha")) * 255)))
    if not api.SetLayeredWindowAttributes(hwnd, 0, alpha, 2):  # LWA_ALPHA, even for alpha=1
        raise ctypes.WinError(ctypes.get_last_error())
    position_flags = SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_FRAMECHANGED
    if not api.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, position_flags):
        raise ctypes.WinError(ctypes.get_last_error())
    if _style(api, hwnd) & PASSIVE_FLAGS != PASSIVE_FLAGS:
        raise RuntimeError("Windows did not retain all passive preview flags")

    # Tk on Windows implements 'wm state normal' with SW_SHOWNOACTIVATE;
    # unlike deiconify it does not force raising/focus (Tk win/tkWinWm.c).
    root.state("normal")
    root.update_idletasks()
    if api.GetAncestor(root.winfo_id(), GA_ROOT) != hwnd or _style(api, hwnd) & PASSIVE_FLAGS != PASSIVE_FLAGS:
        raise RuntimeError("Passive preview HWND or flags changed while showing")
    if not api.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, position_flags):
        raise ctypes.WinError(ctypes.get_last_error())
    after = api.GetForegroundWindow()
    if after == hwnd:
        raise RuntimeError("Passive preview unexpectedly became the foreground window")
    return {"mode": "passive", "hwnd": hwnd, "exstyle": _style(api, hwnd),
            "alpha_byte": alpha, "foreground_before": before, "foreground_after": after}


def verify_offscreen(root):
    """Check native bounds against the entire virtual desktop, without reading it."""
    api = _user32()
    hwnd = api.GetAncestor(root.winfo_id(), GA_ROOT)
    rect = wintypes.RECT()
    right = api.GetSystemMetrics(76) + api.GetSystemMetrics(78)
    bottom = api.GetSystemMetrics(77) + api.GetSystemMetrics(79)
    if not hwnd or not api.GetWindowRect(hwnd, ctypes.byref(rect)) or rect.left <= right or rect.top <= bottom:
        root.withdraw()
        raise RuntimeError("Offscreen test window must stay beyond the virtual desktop")
    return {"bounds": [rect.left, rect.top, rect.right, rect.bottom], "virtual_right": right, "virtual_bottom": bottom}


def _place_offscreen(window):
    api = _user32()
    window.left = api.GetSystemMetrics(76) + api.GetSystemMetrics(78) + 1000
    window.bottom = api.GetSystemMetrics(77) + api.GetSystemMetrics(79) + 4000
    window.redraw()
    window.root.update_idletasks()
    return verify_offscreen(window.root)


def create_test_window(factory, *args, show=False, offscreen=False, **kwargs):
    """Run a window factory with every Tk root withdrawn immediately on creation.

    Default windows stay withdrawn, while their Canvas and event queue still run.
    Explicit offscreen renders map a protected window beyond every monitor,
    verifying native bounds both before and after mapping.
    Explicit previews are Windows-only, click-through, and non-activating. Failure
    destroys every root created by this call; it never leaves an interactive UI.
    """
    if show and offscreen:
        raise ValueError("Choose either a visible preview or an offscreen render")
    if (show or offscreen) and sys.platform != "win32":
        raise RuntimeError("Passive test-window previews are supported only on Windows")
    original, roots = tk.Tk, []

    def hidden_tk(*tk_args, **tk_kwargs):
        root = original(*tk_args, **tk_kwargs)
        roots.append(root)
        root.withdraw()
        return root

    try:
        tk.Tk = hidden_tk
        try:
            window = factory(*args, **kwargs)
        finally:
            tk.Tk = original
        root = window.root
        if len(roots) != 1 or root is not roots[0]:
            raise RuntimeError("Test factory must construct exactly one Tk root")
        root.withdraw()
        root.update_idletasks()
        if offscreen:
            _place_offscreen(window)  # prove position while still withdrawn
        metadata = _show_passive(root) if (show or offscreen) else {"mode": "hidden", "visible_display_measured": False}
        if offscreen:
            metadata.update(mode="offscreen", visible_display_measured=False, **verify_offscreen(root))
        window.test_window = metadata
        root._jsc_test_window_mode = metadata["mode"]
        return window
    except BaseException:
        for root in roots:
            try:
                root.withdraw()
                root.destroy()
            except Exception:
                pass
        raise
