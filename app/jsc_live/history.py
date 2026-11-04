"""History window: every finished subtitle of this run, with a word lookup.

A normal (movable, resizable) window next to the subtitles. Each finished
sentence is appended with its time; select a word with the mouse to see its
reading at once (MeCab, local) and its meaning a moment later (asked from the
API with the sentence as context, so the sense fits; cached per word+sentence).
Only subtitles are kept here, not the reply suggestions: the app cannot know
which suggestion was actually said, so listing them would only add noise.
With the 中文翻译 switch on, each Japanese line also gets a Chinese translation
under it, asked from the API one sentence at a time on a background thread.
"""

from __future__ import annotations

import queue
import re
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

from .furigana import Furigana, kata_to_hira
from .glossary import Glossary

BG = "#16191d"
FG = "#e8e8e8"
FG_TIME = "#6f7c89"
FG_READING = "#ffd479"
FG_MEANING = "#b9c7d6"
FG_TRANSLATION = "#8fa98f"

LOOKUP_SYSTEM = (
    "你是日语词典。用户给出一个日语词（可能是活用形或一小段）和它所在的句子。"
    "只输出一行中文：先写辞书形和读音（平假名），再用不超过 30 个字解释它在这句话里的意思。"
    "格式：辞书形（读音）：意思。不要输出其它内容。"
)
TRANSLATE_SYSTEM = (
    "你是日译中翻译。把用户给出的「这句」日语翻成自然的口语中文，只输出译文一行。"
    "日语来自语音识别，可能有错字，按最可能的意思翻译，不加注释。「上文」只用来理解语境，不要翻译它。"
    "如果给了「固有名词」，句中读音相近的词多半就是它们，按固有名词翻译；句中没有读音相近的词时不要套用。"
    "固有名词说明里的「我」指使用者本人（听这句话的人），不是说这句话的人。"
)
# Short acknowledgements while the learner is talking; shown, but not translated or used as context.
# Long vowel marks are stripped first, so おー / あー / へー / ふーん match as お / あ / へ / ふん.
BACKCHANNEL = re.compile(r"(?:はい|うん|ええ|ん|うっ|う|お|あ|へ|え|ほう|ふん|なるほど|そうですね|そうですか"
                         r"|そうなんですね|そうなんだ|確かに)+")
BACKCHANNEL_IGNORED = re.compile(r"[\s、。，,.!！?？…ー〜~]+")


def is_backchannel(sentence: str) -> bool:
    core = BACKCHANNEL_IGNORED.sub("", sentence)
    return not core or bool(BACKCHANNEL.fullmatch(core))


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


class Translator:
    """Chinese translation of finished subtitles, one request at a time, in order."""

    def __init__(self, client, on_done, glossary: Glossary | None = None) -> None:
        self.client = client          # a suggest.Suggester (only its API settings are used), or None
        self.on_done = on_done        # called on the worker thread with (key, translation)
        self.glossary = glossary.for_translation() if glossary else ""
        self.jobs: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None

    def request(self, key, sentence: str, context: list[str]) -> None:
        if self.client is None:
            return
        self.jobs.put((key, sentence, context))
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def translate(self, sentence: str, context: list[str]) -> str:
        user = (("固有名词：\n" + self.glossary + "\n" if self.glossary else "")
                + ("上文：\n" + "\n".join(context) + "\n" if context else "") + f"这句：{sentence}")
        text = self.client.ask(TRANSLATE_SYSTEM, user, max_tokens=150).strip()
        return text.splitlines()[0].strip() if text else ""

    def _run(self) -> None:
        while True:
            key, sentence, context = self.jobs.get()
            try:
                translation = self.translate(sentence, context)
            except Exception:
                translation = ""  # a failed translation is simply not shown
            if translation:
                self.on_done(key, translation)


class HistoryWindow:
    def __init__(self, master: tk.Tk, furigana: Furigana, lookup: Lookup, family: str,
                 font_size: int = 13, x: int | None = None, y: int | None = None,
                 on_lookup=None, translate: bool = True, glossary: Glossary | None = None) -> None:
        self.furigana = furigana
        self.lookup = lookup
        self.on_lookup = on_lookup
        self.line_ids: dict[str, int | None] = {}
        self.results: queue.Queue = queue.Queue()
        self.translations: queue.Queue = queue.Queue()
        self.translator = Translator(lookup.client, lambda key, text: self.translations.put((key, text)), glossary)
        self.entries: list[tuple[str, list[str]]] = []  # Japanese lines (sentence, context); index = mark jp<i>
        self.requested: set[int] = set()
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
        bar = tk.Frame(self.top, bg="#1d2228")
        bar.pack(fill="x", side="top")
        self.translate_on = tk.BooleanVar(value=translate and lookup.client is not None)
        tk.Checkbutton(bar, text="中文翻译" if lookup.client is not None else "中文翻译（没有 .env，用不了）",
                       variable=self.translate_on, command=self._on_translate_toggle,
                       state="normal" if lookup.client is not None else "disabled",
                       bg="#1d2228", fg=FG_MEANING, selectcolor="#1d2228", activebackground="#1d2228",
                       activeforeground=FG, font=small, padx=6).pack(side="left")
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
        self.text.tag_configure("other", foreground="#8b97a3")  # Chinese lines in grey, like the subtitles
        self.text.tag_configure("translation", foreground=FG_TRANSLATION, font=small,
                                elide=not self.translate_on.get())
        # Read-only but still selectable: swallow every key that would edit the text.
        self.text.bind("<Key>", lambda e: None if (e.state & 0x4 and e.keysym.lower() in ("c", "a")) else "break")
        self.text.bind("<ButtonRelease-1>", self._on_select)

        self.visible = True
        self._pending: tuple[str, str] | None = None
        self.top.after(100, self._poll)

    # --- content -----------------------------------------------------------
    def add(self, sentence: str, other_language: bool = False, uid: int | None = None) -> None:
        at_bottom = self.text.yview()[1] > 0.98
        self.line_ids[self.text.index("end-1c").split(".")[0]] = uid
        self.text.insert("end", time.strftime("%H:%M:%S") + "  ", "time")
        self.text.insert("end", sentence + "\n", "other" if other_language else ())
        if not other_language and not is_backchannel(sentence):
            key = len(self.entries)
            self.text.mark_set(f"jp{key}", "end-2c linestart")
            self.text.mark_gravity(f"jp{key}", "left")
            self.entries.append((sentence, [s for s, _ in self.entries[-2:]]))
            if self.translate_on.get():
                self._request(key)
        if at_bottom:  # follow new lines unless the user scrolled up to read
            self.text.see("end")

    # --- translation -------------------------------------------------------
    def _request(self, key: int) -> None:
        if key not in self.requested:
            self.requested.add(key)
            self.translator.request(key, *self.entries[key])

    def _on_translate_toggle(self) -> None:
        on = self.translate_on.get()
        self.text.tag_configure("translation", elide=not on)
        if on:  # lines that arrived while it was off; only the recent ones, to keep the cost small
            for key in range(max(0, len(self.entries) - 20), len(self.entries)):
                self._request(key)

    def _insert_translation(self, key: int, translation: str) -> None:
        at_bottom = self.text.yview()[1] > 0.98
        end = self.text.index(f"jp{key} lineend")
        line = int(end.split(".")[0])
        self.text.insert(end, "\n" + translation, "translation")
        # Lines below moved down by one; keep the word lookup's line -> utterance map in step.
        self.line_ids = {(k if int(k) <= line else str(int(k) + 1)): v for k, v in self.line_ids.items()}
        if at_bottom:
            self.text.see("end")

    # --- lookup ------------------------------------------------------------
    def _on_select(self, _event=None) -> None:
        try:
            word = self.text.get("sel.first", "sel.last").strip()
            line = self.text.get("sel.first linestart", "sel.first lineend")
        except tk.TclError:
            return  # nothing selected
        word = word.replace("\n", "")
        if not word or len(word) > 30 or "translation" in self.text.tag_names("sel.first"):
            return  # the Chinese translation lines are not looked up
        sentence = line.split("  ", 1)[-1]
        reading, lemma = reading_and_lemma(self.furigana, word)
        uid = self.line_ids.get(self.text.index("sel.first").split(".")[0])
        head = f"{word}　【{reading}】" + (f"　辞书形：{lemma}" if lemma and lemma != word else "")
        self.panel.configure(text=head + "\n意思：查询中…")
        self._pending = (word, sentence)
        threading.Thread(target=self._ask, args=(word, sentence, head, reading, lemma, uid), daemon=True).start()

    def _ask(self, word: str, sentence: str, head: str, reading: str = "", lemma: str = "",
             uid: int | None = None) -> None:
        try:
            meaning = self.lookup.meaning(word, sentence)
        except Exception as exc:  # network errors etc. should not break the window
            meaning = f"（查询失败：{exc}）"
        # Only completed dictionary responses become cards; errors and the
        # no-client placeholder never enter the local vocabulary archive.
        if (self.on_lookup is not None and (word, sentence) in self.lookup.cache
                and meaning and meaning != "（没查到）"):
            try:
                self.on_lookup({"kind": "lookup", "word": word, "reading": reading,
                                "lemma": lemma, "meaning": meaning, "sentence": sentence,
                                "utterance_id": uid, "looked_up_at": time.time()})
            except OSError:
                meaning += "\n（本地查词记录未能保存，请检查存档文件夹。）"
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
            while True:
                self._insert_translation(*self.translations.get_nowait())
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
