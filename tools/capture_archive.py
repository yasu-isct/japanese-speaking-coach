"""Screenshot of a session folder in Explorer, with the navigation pane blanked out.

    python tools/capture_archive.py logs/sessions/20261025-182220 docs/images/live-archive.png

Explorer is moved to 1280x720 so the shot is small, and the navigation pane (left
column: account name, pinned private folders) is painted over, because it shows
personal information that has nothing to do with the app. Run with the jsc env.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screen_capture as sc  # noqa: E402

NAV_PANE = 330          # physical pixels at this display's scaling (the pane is ~255 px at 100% x 1.28)
FILL = (25, 25, 25)     # Explorer's dark background


def main() -> None:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    capture(Path(sys.argv[1]).resolve(), Path(sys.argv[2]))


def capture(folder: Path, out: Path) -> None:
    subprocess.Popen(["explorer", str(folder)])
    hwnd = 0
    for _ in range(60):
        time.sleep(0.25)
        hwnd = sc.find_window(folder.name)
        if hwnd:
            break
    if not hwnd:
        raise RuntimeError("explorer window not found")
    sc.user32.ShowWindow(hwnd, 9)  # SW_RESTORE (not maximized)
    sc.user32.MoveWindow(hwnd, 120, 120, 1280, 720, True)
    sc.user32.SetForegroundWindow(hwnd)
    time.sleep(1.5)
    rgb = sc.grab(*sc.hwnd_rect(hwnd))
    sc.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE

    # Blank the navigation pane below the address/command bars (top ~190 px stay: tabs, path, toolbar).
    top = 190
    rgb[top:-34, :NAV_PANE] = FILL
    sc.save_png(out, rgb)
    print(f"saved {out} {rgb.shape[1]}x{rgb.shape[0]} {out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
