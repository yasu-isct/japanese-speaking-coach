"""Always-on-top subtitle window that draws furigana (ruby) above kanji.

tkinter has no ruby support, so text is laid out by hand on a Canvas:
each segment is measured with the base font, its reading is centred above it
in a smaller font, and lines wrap at the window width.

The window shows the last few finished sentences plus the current partial one
(greyed). Drag to move, mouse wheel to resize the text (Ctrl + wheel: opacity).

A toolbar row at the top: 「● 录音中」 while the run is archived; opacity − / +;
历史 (show/hide the history window); three display modes — 收起 (toolbar only),
字幕 (subtitles only), 完整 (subtitles + suggestions) — and ✕ to quit.
In full mode, 收起中文 / 展开中文 toggles meanings and the recommendation reason;
warnings stay visible. The recommended reply is bold and labelled ⭐ 推荐.

Glance view (default, D-039): in a conversation there are only seconds to look, so the ⭐
reply is shown in full with its meaning, the other two replies and the reason take one
line each (cut with …), and older subtitle lines give way when the window would grow
past half the screen (the history window keeps them all). 详情 shows everything.

With reply suggestions on, a second area under the subtitles shows "…" while
the API is working, the three replies (⭐ = recommended) as they stream in,
and a timeout notice if nothing arrived within the time limit.
"""

from __future__ import annotations

import queue
import tkinter as tk
import tkinter.font as tkfont

from .furigana import Segment

BG = "#111418"
FG_FINAL = "#f5f5f5"
FG_PARTIAL = "#9aa4ae"
FG_OTHER_LANG = "#7d8a96"   # Chinese (non-Japanese) subtitles: grey, no furigana"
FG_RUBY = "#ffd479"
FG_MEANING = "#8fa3b8"
FG_WARN = "#ff8a65"
SEPARATOR = "#2c333b"
BUTTON = "#262c33"
BUTTON_ACTIVE = "#3d5a78"
FG_REC = "#ff5c5c"
TOOLBAR_H = 26
MODES = ("collapsed", "subtitles", "full")


SENTENCE_END = "。！？!?"


def other_answer(reason: str) -> str:
    """「若…，可说「…」」 from a recommendation reason (the last one), or ''."""
    import re

    found = re.findall(r"(?:若|如果)[^。；;]*?[，,、]?\s*可(?:以)?说「[^」]+」", reason)
    return found[-1] if found else ""


def split_first_sentence(segments: list[Segment]) -> tuple[list[Segment], list[Segment]]:
    """(segments of the first sentence, the rest). Ruby words are never split."""
    head: list[Segment] = []
    for i, seg in enumerate(segments):
        if seg.ruby is None:
            cut = next((k for k, ch in enumerate(seg.text) if ch in SENTENCE_END), None)
            if cut is not None:
                first, rest = seg.text[:cut + 1], seg.text[cut + 1:]
                head.append(Segment(first))
                tail = ([Segment(rest)] if rest.strip() else []) + list(segments[i + 1:])
                return head, tail
        head.append(seg)
    return head, []


def enable_dpi_awareness() -> None:
    """Render at native resolution on high-DPI screens (otherwise Windows blurs the text)."""
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


class SubtitleWindow:
    def __init__(self, events: "queue.Queue[tuple[str, int, list[Segment]]]", font_size: int = 16,
                 width: int = 900, history: int = 5, alpha: float = 0.88,
                 suggestion_timeout: float = 3.0, on_suggestion_drawn=None, history_window=None,
                 mode: str = "full", materials_root: str | None = None, materials_controller=None) -> None:
        self.events = events
        self.alpha = alpha
        self.mode = mode
        self.show_meanings = True
        self.show_details = False  # glance view by default (D-039)
        self.materials_root = materials_root
        self.materials_controller = materials_controller
        self.materials_notice = ""
        self.materials_window = None
        self.review_window = None
        self.recording = False
        self.history_factory = history_window   # callable(root, family) -> HistoryWindow, or None
        self.history_window = None
        self.suggestion_timeout = suggestion_timeout
        self.on_suggestion_drawn = on_suggestion_drawn  # callback(uid, payload, timed_out)
        self.suggestion: dict | None = None  # {"uid", "payload", "timed_out", "reported"}
        self.history = history
        self.lines: list[tuple[int, list[Segment], str]] = []   # finished sentences: (uid, segments, colour)
        self.partial: tuple[int, list[Segment]] | None = None
        self._drag_origin: tuple[int, int] | None = None

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
        self.canvas.bind("<Control-MouseWheel>", lambda e: self.set_alpha(self.alpha + (0.1 if e.delta > 0 else -0.1)))
        self.canvas.bind("<Button-3>", lambda e: self.close())
        self.root.bind("<Escape>", lambda e: self.close())
        self.closed = False
        self.status = "準備中…（モデル読み込み）"
        if self.history_factory:
            self.history_window = self.history_factory(self.root, self.family)
        self.redraw()
        self.root.after(50, self._poll)

    def set_alpha(self, alpha: float) -> None:
        self.alpha = round(max(0.3, min(alpha, 1.0)), 2)
        self.root.attributes("-alpha", self.alpha)

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.redraw()

    def _press(self, name: str) -> None:
        if name == "close":
            self.close()
        elif name in MODES:
            self.set_mode(name)
        elif name == "alpha-":
            self.set_alpha(self.alpha - 0.1)
        elif name == "alpha+":
            self.set_alpha(self.alpha + 0.1)
        elif name == "history" and self.history_window:
            self.history_window.toggle()
            self.redraw()
        elif name == "meanings":
            self.show_meanings = not self.show_meanings
            self.redraw()
        elif name == "details":
            self.show_details = not self.show_details
            self.redraw()
        elif name == "review":
            from .review_view import ReviewWindow

            if self.review_window is None:
                self.review_window = ReviewWindow(self.root, self.family)
            self.review_window.show()
        elif name == "sources" and self.materials_root is not None:
            from .material_view import MaterialsWindow

            if self.materials_window is None:
                self.materials_window = MaterialsWindow(self.root, self.family, self.materials_root,
                                                        controller=self.materials_controller)
            payload = self.suggestion["payload"] if self.suggestion else {}
            self.materials_window.show(payload.get("sources", []), payload.get("cited_source_ids", []),
                                       snapshot_root=payload.get("materials_root"))

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
        small = max(10, round(self.font_size * 0.8))
        self.sugg_font = tkfont.Font(family=self.family, size=small)
        self.sugg_star_font = tkfont.Font(family=self.family, size=small, weight="bold")
        self.sugg_ruby_font = tkfont.Font(family=self.family, size=max(7, small // 2))
        self.meaning_font = tkfont.Font(family=self.family, size=max(9, round(self.font_size * 0.55)))
        self.toolbar_font = tkfont.Font(family=self.family, size=9)
        if hasattr(self, "closed"):
            self.redraw()

    # --- window dragging -------------------------------------------------
    def _start_drag(self, event) -> None:
        hit = self.canvas.find_overlapping(event.x, event.y, event.x, event.y)
        buttons = [t[4:] for item in hit for t in self.canvas.gettags(item) if t.startswith("btn:")]
        if buttons:
            self._drag_origin = None
            self._press(buttons[0])
            return
        self._drag_origin = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag(self, event) -> None:
        if self._drag_origin is None:
            return
        dx, dy = self._drag_origin
        self.left, top = event.x_root - dx, event.y_root - dy
        self.bottom = top + self.root.winfo_height()
        self.root.geometry(f"+{self.left}+{top}")

    # --- event loop --------------------------------------------------------
    def _poll(self) -> None:
        changed = False
        controller = getattr(self, "materials_controller", None)
        startup = getattr(controller, "startup", None)
        notice = startup.snapshot()["notice"] if startup is not None else ""
        if notice != getattr(self, "materials_notice", ""):
            self.materials_notice = notice
            changed = True
        try:
            while True:
                kind, uid, segments = self.events.get_nowait()
                changed = True
                if kind == "status":
                    self.status = "".join(s.text for s in segments)
                elif kind == "partial":
                    self.partial = (uid, segments)
                elif kind in ("final", "final_other"):
                    if self.partial and self.partial[0] == uid:
                        self.partial = None
                    if segments:
                        other = kind == "final_other"
                        self.lines.append((uid, segments, FG_OTHER_LANG if other else FG_FINAL))
                        self.lines = self.lines[-self.history:]
                        if self.history_window:
                            self.history_window.add("".join(s.text for s in segments), other_language=other, uid=uid)
                    self.status = ""
                elif kind == "session":
                    self.recording = True
                elif kind == "suggest":
                    self._on_suggest(uid, segments)
        except queue.Empty:
            pass
        if changed:
            self.redraw()
            self._report_suggestion()
        if not self.closed:
            self.root.after(50, self._poll)

    # --- suggestions ---------------------------------------------------------
    def _on_suggest(self, uid: int, payload: dict) -> None:
        current = self.suggestion
        if payload["state"] == "pending":
            self.suggestion = {"uid": uid, "payload": payload, "timed_out": False, "reported": False}
            self.root.after(int(self.suggestion_timeout * 1000), lambda: self._check_timeout(uid))
        elif current and current["uid"] == uid:
            current["payload"] = payload

    def _check_timeout(self, uid: int) -> None:
        s = self.suggestion
        if not self.closed and s and s["uid"] == uid and s["payload"]["state"] not in ("done", "error"):
            s["timed_out"] = True
            self.redraw()

    def _report_suggestion(self) -> None:
        s = self.suggestion
        if s and not s["reported"] and s["payload"]["state"] in ("done", "error"):
            s["reported"] = True
            self.root.update_idletasks()
            if self.on_suggestion_drawn:
                self.on_suggestion_drawn(s["uid"], s["payload"], s["timed_out"])

    def _draw_suggestion(self, y: float, pad: int, max_w: int) -> float:
        s = self.suggestion
        c = self.canvas
        c.create_line(pad, y, self.width - pad, y, fill=SEPARATOR)
        y += 8
        payload = s["payload"]
        replies = payload.get("replies", [])
        notice = None
        if payload["state"] == "error" and not replies:
            notice = f"提案エラー：{payload.get('error') or ''}"[:80]
        elif s["timed_out"] and payload["state"] not in ("done", "error"):
            notice = f"タイムアウト（{self.suggestion_timeout:.0f} 秒以内に提案が届きませんでした）"
        elif not replies:
            notice = "…"
        if notice:
            c.create_text(pad, y, text=notice, font=self.meaning_font, fill=FG_MEANING, anchor="nw")
            return y + self.meaning_font.metrics("linespace") + 4
        if s["timed_out"]:
            c.create_text(self.width - pad, y, text="遅延", font=self.meaning_font, fill=FG_WARN, anchor="ne")
        label_w = max(self.meaning_font.measure(label) for label in ("⭐ 推荐", "丁寧", "カジュアル", "かんたん")) + 16
        reply_w = max(1, max_w - label_w)
        ruby_h = self.sugg_ruby_font.metrics("linespace")
        for r in replies:
            font = self.sugg_star_font if r["star"] else self.sugg_font
            base_h = font.metrics("linespace")
            label = ("⭐ 推荐\n" if r["star"] else "") + r["style"]
            label_y = y if r["star"] else y + ruby_h + (base_h - self.meaning_font.metrics("linespace")) / 2
            label_item = c.create_text(pad, label_y, text=label, font=self.meaning_font,
                                       fill=FG_RUBY if r["star"] else FG_MEANING, anchor="nw")
            label_bottom = c.bbox(label_item)[3]
            color = FG_FINAL if r["star"] else FG_PARTIAL
            glance = not self.show_details and not r["star"]
            # The ⭐ reply leads with its conclusion (D-040): that sentence is bold on its own
            # line(s); conditions and exceptions follow in the regular weight.
            if r["star"]:
                head, tail = split_first_sentence(r["segments"])
                blocks = [(head, font)] + ([(tail, self.sugg_font)] if tail else [])
            else:
                blocks = [(r["segments"], font)]
            for segments, block_font in blocks:
                rows = self._layout(segments, reply_w, block_font, self.sugg_ruby_font)
                if glance and len(rows) > 1:
                    rows = [self._with_ellipsis(rows[0], reply_w, block_font)]
                row_h = block_font.metrics("linespace")
                for row in rows:
                    x = pad + label_w
                    for text, ruby, w in row:
                        base_w = block_font.measure(text)
                        if ruby:
                            c.create_text(x + w / 2, y, text=ruby, font=self.sugg_ruby_font, fill=FG_RUBY, anchor="n")
                        c.create_text(x + (w - base_w) / 2, y + ruby_h, text=text, font=block_font,
                                      fill=color, anchor="nw")
                        x += w
                    y += ruby_h + row_h
            y = max(y, label_bottom)
            # Warnings describe whether a reply is appropriate, so never fold them with translations.
            if r["warn"]:
                item = c.create_text(pad + label_w, y, text="⚠ 对此对象不要用", font=self.meaning_font,
                                     fill=FG_WARN, anchor="nw", width=reply_w)
                y = c.bbox(item)[3] + 2
            if self.show_meanings and r["meaning"] and not glance:
                item = c.create_text(pad + label_w, y, text=r["meaning"], font=self.meaning_font,
                                     fill=FG_MEANING, anchor="nw", width=reply_w)
                y = c.bbox(item)[3] + 2
            y += 6
        if self.show_meanings and payload.get("reason"):
            reason = "理由：" + payload["reason"]
            if not self.show_details:
                # In a conversation the other answer (「若…，可说「…」」) is what can be used right
                # away, so the glance view shows it instead of the style rationale (D-041).
                other = other_answer(payload["reason"])
                reason = self._clip_text("另一种说法：" + other if other else reason, max_w, self.meaning_font)
            item = c.create_text(pad, y, text=reason, font=self.meaning_font,
                                 fill=FG_MEANING, anchor="nw", width=max_w)
            x0, y0, x1, y1 = c.bbox(item)
            y = y1 + 4
        return y

    @staticmethod
    def _clip_text(text: str, max_w: int, font) -> str:
        """The longest prefix that fits on one line, with … when something was cut."""
        if font.measure(text) <= max_w:
            return text
        while text and font.measure(text + "…") > max_w:
            text = text[:-1]
        return text + "…"

    @staticmethod
    def _with_ellipsis(row, max_w: int, font):
        """First layout row of a reply, shortened so a trailing … fits."""
        row = list(row)
        ell = font.measure("…")
        while row and sum(w for _, _, w in row) + ell > max_w:
            text, ruby, w = row[-1]
            if ruby is None and len(text) > 1:
                text = text[:-1]
                row[-1] = (text, None, font.measure(text))
            else:
                row.pop()
        return row + [("…", None, ell)]

    # --- layout ------------------------------------------------------------
    def _layout(self, segments: list[Segment], max_w: int, base_font=None,
                ruby_font=None) -> list[list[tuple[str, str | None, int]]]:
        """Wrap segments into rows of (text, ruby, width). Plain text may break anywhere."""
        base_font = base_font or self.base_font
        ruby_font = ruby_font or self.ruby_font
        rows: list[list[tuple[str, str | None, int]]] = [[]]
        x = 0
        for seg in segments:
            pieces = [seg.text] if seg.ruby else list(seg.text)  # ruby words stay whole
            for piece in pieces:
                w = base_font.measure(piece)
                if seg.ruby:
                    w = max(w, ruby_font.measure(seg.ruby))
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

    def _draw_toolbar(self) -> None:
        c = self.canvas
        if self.recording:
            c.create_text(10, TOOLBAR_H / 2, text="● 录音中", font=self.toolbar_font, fill=FG_REC, anchor="w")
        if getattr(self, "materials_root", None) is not None:
            c.create_text(110 if self.recording else 10, TOOLBAR_H / 2, text="资料片段会发给 API",
                          font=self.toolbar_font, fill=FG_MEANING, anchor="w")
        buttons = [("alpha-", "透明 −", False), ("alpha+", "透明 +", False), ("review", "复盘", False),
                   ("history", "历史", bool(self.history_window and self.history_window.visible)),
                   ("meanings", "收起中文" if self.show_meanings else "展开中文", self.show_meanings),
                   ("details", "详情", self.show_details),
                   ("collapsed", "收起", self.mode == "collapsed"), ("subtitles", "字幕", self.mode == "subtitles"),
                   ("full", "完整", self.mode == "full"), ("close", "✕", False)]
        if not self.history_window:
            buttons = [b for b in buttons if b[0] != "history"]
        if getattr(self, "materials_root", None) is not None:
            buttons.insert(2, ("sources", "资料", False))
        if self.mode != "full":
            buttons = [b for b in buttons if b[0] not in ("meanings", "details")]
        x = self.width
        for name, label, active in reversed(buttons):
            w = self.toolbar_font.measure(label) + 16
            x -= w
            c.create_rectangle(x, 0, x + w - 2, TOOLBAR_H - 2, fill=BUTTON_ACTIVE if active else BUTTON,
                               outline="", tags=f"btn:{name}")
            c.create_text(x + (w - 2) / 2, TOOLBAR_H / 2 - 1, text=label, font=self.toolbar_font,
                          fill=FG_FINAL, tags=f"btn:{name}")

    def redraw(self) -> None:
        # Glance view keeps the window under half the screen: drop the oldest subtitle lines
        # (never below two) before anything else; the history window still has every line.
        limit = self.root.winfo_screenheight() // 2
        keep = len(self.lines)
        while True:
            height = self._redraw(keep)
            if self.show_details or height <= limit or keep <= 2:
                return
            keep -= 1

    def _redraw(self, keep_lines: int) -> int:
        c = self.canvas
        c.delete("all")
        pad = 16
        max_w = self.width - 2 * pad
        self._draw_toolbar()
        if self.mode == "collapsed":
            c.configure(height=TOOLBAR_H)
            self.root.geometry(f"{self.width}x{TOOLBAR_H}+{self.left}+{self.bottom - TOOLBAR_H}")
            return TOOLBAR_H
        y = TOOLBAR_H + 6
        notice = getattr(self, "materials_notice", "")
        if notice:
            item = c.create_text(pad, y, text=notice, width=max_w, font=self.toolbar_font,
                                 fill=FG_MEANING, anchor="nw")
            y = c.bbox(item)[3] + 6
        shown = self.lines[-keep_lines:] if keep_lines < len(self.lines) else self.lines
        blocks = [(segs, color) for _, segs, color in shown]
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
        if self.suggestion and self.mode == "full":
            y = self._draw_suggestion(y, pad, max_w)
        height = max(y + pad - 6, TOOLBAR_H + self.ruby_h + self.base_h + pad)
        c.configure(height=height)
        self.root.geometry(f"{self.width}x{int(height)}+{self.left}+{self.bottom - int(height)}")
        return int(height)

    def close(self) -> None:
        self.closed = True
        if getattr(self, "materials_controller", None) is not None:
            self.materials_controller.cancel_initial(silent=True)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
