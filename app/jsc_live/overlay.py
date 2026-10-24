"""Always-on-top subtitle window that draws furigana (ruby) above kanji.

tkinter has no ruby support, so text is laid out by hand on a Canvas:
each segment is measured with the base font, its reading is centred above it
in a smaller font, and lines wrap at the window width.

The window shows the last few finished sentences plus the current partial one
(greyed). Drag to move, mouse wheel to resize the text, Esc / right-click to quit.
"""

from __future__ import annotations

import queue
import tkinter as tk
import tkinter.font as tkfont

from .furigana import Segment

BG = "#111418"
FG_FINAL = "#f5f5f5"
FG_PARTIAL = "#9aa4ae"
FG_RUBY = "#ffd479"


def enable_dpi_awareness() -> None:
    """Render at native resolution on high-DPI screens (otherwise Windows blurs the text)."""
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


class SubtitleWindow:
    def __init__(self, events: "queue.Queue[tuple[str, int, list[Segment]]]", font_size: int = 22,
                 width: int = 1100, history: int = 2, alpha: float = 0.88) -> None:
        self.events = events
        self.history = history
        self.lines: list[tuple[int, list[Segment]]] = []   # finished sentences
        self.partial: tuple[int, list[Segment]] | None = None

        enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.title("jsc-live")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", alpha)
        self.root.configure(bg=BG)
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.width = min(width, screen_w - 40)
        # The bottom edge stays put; the window grows upwards as text is added.
        self.left = (screen_w - self.width) // 2
        self.bottom = screen_h - 90
        self.root.geometry(f"{self.width}x10+{self.left}+{self.bottom - 10}")

        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0, width=self.width)
        self.canvas.pack(fill="both", expand=True)
        self.family = self._pick_family()
        self.set_font_size(font_size)

        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<MouseWheel>", lambda e: self.set_font_size(self.font_size + (1 if e.delta > 0 else -1)))
        self.canvas.bind("<Button-3>", lambda e: self.close())
        self.root.bind("<Escape>", lambda e: self.close())
        self.closed = False
        self.status = "準備中…（モデル読み込み）"
        self.redraw()
        self.root.after(50, self._poll)

    def _pick_family(self) -> str:
        families = set(tkfont.families(self.root))
        for name in ("Yu Gothic UI", "Meiryo UI", "Meiryo", "MS Gothic", "Noto Sans CJK JP"):
            if name in families:
                return name
        return "TkDefaultFont"

    def set_font_size(self, size: int) -> None:
        self.font_size = max(12, min(size, 72))
        self.base_font = tkfont.Font(family=self.family, size=self.font_size)
        self.ruby_font = tkfont.Font(family=self.family, size=max(8, self.font_size // 2))
        self.ruby_h = self.ruby_font.metrics("linespace")
        self.base_h = self.base_font.metrics("linespace")
        if hasattr(self, "closed"):
            self.redraw()

    # --- window dragging -------------------------------------------------
    def _start_drag(self, event) -> None:
        self._drag_origin = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag(self, event) -> None:
        dx, dy = self._drag_origin
        self.left, top = event.x_root - dx, event.y_root - dy
        self.bottom = top + self.root.winfo_height()
        self.root.geometry(f"+{self.left}+{top}")

    # --- event loop --------------------------------------------------------
    def _poll(self) -> None:
        changed = False
        try:
            while True:
                kind, uid, segments = self.events.get_nowait()
                changed = True
                if kind == "status":
                    self.status = "".join(s.text for s in segments)
                elif kind == "partial":
                    self.partial = (uid, segments)
                elif kind == "final":
                    if self.partial and self.partial[0] == uid:
                        self.partial = None
                    if segments:
                        self.lines.append((uid, segments))
                        self.lines = self.lines[-self.history:]
                    self.status = ""
        except queue.Empty:
            pass
        if changed:
            self.redraw()
        if not self.closed:
            self.root.after(50, self._poll)

    # --- layout ------------------------------------------------------------
    def _layout(self, segments: list[Segment], max_w: int) -> list[list[tuple[str, str | None, int]]]:
        """Wrap segments into rows of (text, ruby, width). Plain text may break anywhere."""
        rows: list[list[tuple[str, str | None, int]]] = [[]]
        x = 0
        for seg in segments:
            pieces = [seg.text] if seg.ruby else list(seg.text)  # ruby words stay whole
            for piece in pieces:
                w = self.base_font.measure(piece)
                if seg.ruby:
                    w = max(w, self.ruby_font.measure(seg.ruby))
                if x + w > max_w and rows[-1]:
                    rows.append([])
                    x = 0
                rows[-1].append((piece, seg.ruby, w))
                x += w
        # Merge consecutive plain characters back into runs for drawing.
        merged_rows = []
        for row in rows:
            merged: list[tuple[str, str | None, int]] = []
            for text, ruby, w in row:
                if merged and ruby is None and merged[-1][1] is None:
                    merged[-1] = (merged[-1][0] + text, None, merged[-1][2] + w)
                else:
                    merged.append((text, ruby, w))
            merged_rows.append(merged)
        return merged_rows

    def redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        pad = 16
        max_w = self.width - 2 * pad
        y = pad
        blocks = [(segs, FG_FINAL) for _, segs in self.lines]
        if self.partial:
            blocks.append((self.partial[1], FG_PARTIAL))
        if not blocks and self.status:
            blocks.append(([Segment(self.status)], FG_PARTIAL))
        for segments, color in blocks:
            for row in self._layout(segments, max_w):
                x = pad
                for text, ruby, w in row:
                    base_w = self.base_font.measure(text)
                    if ruby:
                        c.create_text(x + w / 2, y, text=ruby, font=self.ruby_font, fill=FG_RUBY, anchor="n")
                    c.create_text(x + (w - base_w) / 2, y + self.ruby_h, text=text, font=self.base_font,
                                  fill=color, anchor="nw")
                    x += w
                y += self.ruby_h + self.base_h + 4
            y += 6
        height = max(y + pad - 6, self.ruby_h + self.base_h + 2 * pad)
        c.configure(height=height)
        self.root.geometry(f"{self.width}x{int(height)}+{self.left}+{self.bottom - int(height)}")

    def close(self) -> None:
        self.closed = True
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
