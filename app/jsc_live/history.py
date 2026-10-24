"""History window: every finished subtitle of this run, with a word lookup.

A normal (movable, resizable) window next to the subtitles. Each finished
sentence is appended with its time; select a word with the mouse to see its
reading at once (MeCab, local) and its meaning a moment later (asked from the
API with the sentence as context, so the sense fits; cached per word+sentence).
Only subtitles are kept here, not the reply suggestions: the app cannot know
which suggestion was actually said, so listing them would only add noise.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

from .furigana import Furigana, kata_to_hira

BG = "#16191d"
FG = "#e8e8e8"
FG_TIME = "#6f7c89"
FG_READING = "#ffd479"
FG_MEANING = "#b9c7d6"

LOOKUP_SYSTEM = (
    "你是日语词典。用户给出一个日语词（可能是活用形或一小段）和它所在的句子。"
    "只输出一行中文：先写辞书形和读音（平假名），再用不超过 30 个字解释它在这句话里的意思。"
    "格式：辞书形（读音）：意思。不要输出其它内容。"
)


def reading_and_lemma(furigana: Furigana, text: str) -> tuple[str, str]:
    """(hiragana reading of the selection, dictionary form if it is a single word, else '')."""
    words = list(furigana.tagger(text))
    reading = "".join(kata_to_hira(furigana._reading(w) or w.surface) for w in words)
    lemma = ""
    content = [w for w in words if getattr(w.feature, "pos1", "") not in ("助詞", "助動詞", "補助記号")]
    if len(content) == 1:
        lemma = getattr(content[0].feature, "lemma", "") or ""
        lemma = lemma.split("-")[0]  # UniDic writes e.g. 「為る」 or 「ジュース-juice」
    return reading, lemma


class Lookup:
    """Meaning of a word in context, from the API (background thread, cached)."""

    def __init__(self, client) -> None:
        self.client = client          # a suggest.Suggester (only its API settings are used), or None
        self.cache: dict[tuple[str, str], str] = {}

    def meaning(self, word: str, sentence: str) -> str:
        key = (word, sentence)
        if key not in self.cache:
            if self.client is None:
                return "（没有 .env，查不了意思；读音照常显示）"
            text = self.client.ask(LOOKUP_SYSTEM, f"词：{word}\n句子：{sentence}", max_tokens=120)
            self.cache[key] = text.strip().splitlines()[0] if text.strip() else "（没查到）"
        return self.cache[key]


class HistoryWindow:
    def __init__(self, master: tk.Tk, furigana: Furigana, lookup: Lookup, family: str,
                 font_size: int = 13, x: int | None = None, y: int | None = None) -> None:
        self.furigana = furigana
        self.lookup = lookup
        self.results: queue.Queue = queue.Queue()
        self.top = tk.Toplevel(master)
        self.top.title("字幕历史（选中词语查读音和意思）")
        self.top.configure(bg=BG)
        self.top.attributes("-topmost", True)
        sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
        w, h = 420, int(sh * 0.6)
        self.top.geometry(f"{w}x{h}+{x if x is not None else sw - w - 20}+{y if y is not None else 60}")
        self.top.protocol("WM_DELETE_WINDOW", self.hide)  # closing only hides it; the toolbar can bring it back

        text_font = tkfont.Font(family=family, size=font_size)
        small = tkfont.Font(family=family, size=max(9, font_size - 3))
        # Pack the lookup panel first so the text area cannot squeeze it out of the window.
        self.panel = tk.Label(self.top, bg="#1d2228", fg=FG_MEANING, font=small, anchor="w", justify="left",
                              wraplength=w - 20, padx=10, pady=8, text="选中一个词，这里显示读音和意思")
        self.panel.pack(fill="x", side="bottom")
        frame = tk.Frame(self.top, bg=BG)
        frame.pack(fill="both", expand=True)
        self.text = tk.Text(frame, bg=BG, fg=FG, font=text_font, wrap="char", relief="flat", padx=10, pady=8,
                            insertbackground=BG, selectbackground="#3a5a7a", inactiveselectbackground="#3a5a7a",
                            spacing3=6, height=1)
        scroll = tk.Scrollbar(frame, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("time", foreground=FG_TIME, font=small)
        # Read-only but still selectable: swallow every key that would edit the text.
        self.text.bind("<Key>", lambda e: None if (e.state & 0x4 and e.keysym.lower() in ("c", "a")) else "break")
        self.text.bind("<ButtonRelease-1>", self._on_select)

        self.visible = True
        self._pending: tuple[str, str] | None = None
        self.top.after(100, self._poll)

    # --- content -----------------------------------------------------------
    def add(self, sentence: str) -> None:
        at_bottom = self.text.yview()[1] > 0.98
        self.text.insert("end", time.strftime("%H:%M:%S") + "  ", "time")
        self.text.insert("end", sentence + "\n")
        if at_bottom:  # follow new lines unless the user scrolled up to read
            self.text.see("end")

    # --- lookup ------------------------------------------------------------
    def _on_select(self, _event=None) -> None:
        try:
            word = self.text.get("sel.first", "sel.last").strip()
            line = self.text.get("sel.first linestart", "sel.first lineend")
        except tk.TclError:
            return  # nothing selected
        word = word.replace("\n", "")
        if not word or len(word) > 30:
            return
        sentence = line.split("  ", 1)[-1]
        reading, lemma = reading_and_lemma(self.furigana, word)
        head = f"{word}　【{reading}】" + (f"　辞书形：{lemma}" if lemma and lemma != word else "")
        self.panel.configure(text=head + "\n意思：查询中…")
        self._pending = (word, sentence)
        threading.Thread(target=self._ask, args=(word, sentence, head), daemon=True).start()

    def _ask(self, word: str, sentence: str, head: str) -> None:
        try:
            meaning = self.lookup.meaning(word, sentence)
        except Exception as exc:  # network errors etc. should not break the window
            meaning = f"（查询失败：{exc}）"
        self.results.put((word, sentence, head, meaning))

    def _poll(self) -> None:
        try:
            while True:
                word, sentence, head, meaning = self.results.get_nowait()
                if self._pending == (word, sentence):  # ignore answers for an older selection
                    self.panel.configure(text=f"{head}\n{meaning}")
        except queue.Empty:
            pass
        try:
            self.top.after(100, self._poll)
        except tk.TclError:
            pass  # window destroyed

    # --- show / hide -------------------------------------------------------
    def hide(self) -> None:
        self.top.withdraw()
        self.visible = False

    def show(self) -> None:
        self.top.deiconify()
        self.visible = True

    def toggle(self) -> None:
        self.hide() if self.visible else self.show()
