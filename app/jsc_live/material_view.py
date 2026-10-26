"""A user-opened view of the snippets sent with the current suggestion."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog
import queue
import threading
from .materials import source_location


def source_text(root: str, sources: list[dict], cited_ids: list[str]) -> str:
    lines = [f"这条回答使用的资料文件夹：{root}" if root else "本轮参考片段", ""]
    if not sources:
        lines += ["这轮没有查到相关片段。", "可放入 UTF-8 的 .md / .txt、.docx 或文字PDF，修改后点“重新读取资料”。"]
    for source in sources:
        label = "回答引用" if source["source_id"] in cited_ids else "本轮发送"
        lines += [f"{label} · {source_location(source)}",
                  source.get("heading", ""), source["text"], ""]
    if sources:
        lines += ["以上是这条建议发送给 API 的片段。新建议出现后，再点“资料”刷新。",
                  "“回答引用”是模型标出的出处，请结合原文使用。"]
    return "\n".join(lines)


class MaterialsWindow:
    def __init__(self, master, family: str, materials_root: str, controller=None):
        self.materials_root = materials_root
        self.controller = controller
        self.family = family
        self.model_window = None
        self.results = queue.Queue()
        self.busy = False
        self.cancel = threading.Event()
        self._last_sources = ([], [], None)
        self.actions = []
        self.top = tk.Toplevel(master)
        self.top.title("资料与本轮出处")
        self.top.geometry("780x600")
        self.top.minsize(520, 400)
        self.top.protocol("WM_DELETE_WINDOW", self._close)
        header = ttk.Frame(self.top, padding=12)
        header.pack(fill="x")
        self.folder = ttk.Label(header, text=f"当前资料文件夹：{materials_root}", wraplength=720)
        self.folder.pack(anchor="w")
        self.disclosure = ttk.Label(header, text="仅查到的相关片段会随问题发给 API。选择或重新读取文件夹本身不会发送资料。",
                                    wraplength=720)
        self.disclosure.pack(anchor="w", pady=(6, 8))
        if controller is not None:
            actions = ttk.Frame(header)
            actions.pack(anchor="w")
            for text, command in (("选择资料文件夹…", self._choose), ("重新读取资料", self._reload),
                                  ("多语言查找…", self._models)):
                button = ttk.Button(actions, text=text, command=command)
                button.pack(side="left", padx=(0, 8))
                self.actions.append(button)
        self.status = ttk.Label(header, wraplength=720)
        self.status.pack(anchor="w", pady=(8, 0))
        self.progress_row = ttk.Frame(header)
        self.progress = ttk.Progressbar(self.progress_row, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.cancel_button = ttk.Button(self.progress_row, text="取消读取", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="right")
        self.top.bind("<Configure>", self._resize)
        body = ttk.Frame(self.top)
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", font=(family, 12), padx=16, pady=12)
        scroll = ttk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        self.text.configure(state="disabled")
        self._show_current()
        self.top.after(100, self._poll)

    def _show_current(self):
        if self.controller is not None and not self.busy:
            info = self.controller.current()
            self.materials_root = info["root"]
            self.folder.configure(text=f"当前资料文件夹：{self.materials_root}")
            self.status.configure(text=info["message"])

    def _resize(self, event):
        if event.widget is self.top:
            for label in (self.folder, self.disclosure, self.status):
                label.configure(wraplength=max(200, event.width - 32))

    def _choose(self):
        if not self.busy:
            folder = filedialog.askdirectory(parent=self.top, title="选择愿意用于回答的资料文件夹", mustexist=True)
            if folder:
                self._reload(folder)

    def _models(self):
        if self.controller is None or self.busy:
            return
        if self.model_window is None:
            from .model_view import ModelWindow

            self.model_window = ModelWindow(self.top, self.family, self.controller, on_change=self._show_current)
        self.model_window.show()

    def _reload(self, folder=None):
        if self.controller is None or self.busy:
            return
        self.busy = True
        self.cancel.clear()
        for action in self.actions:
            action.configure(state="disabled")
        self.status.configure(text="正在本机读取资料，当前对话继续使用原资料…")
        self.progress_row.pack(fill="x", before=self.status, pady=(6, 0))
        self.progress.start(15)
        self.cancel_button.configure(state="normal")
        threading.Thread(target=self._load, args=(folder,), daemon=True).start()

    def _load(self, folder):
        notify = lambda message: self.results.put(("progress", message))
        try:
            self.results.put((True, self.controller.reload(folder, progress=notify, cancel=self.cancel)))
        except ValueError as exc:
            self.results.put((False, str(exc)))
        except OSError:
            self.results.put((False, "这个文件夹无法读取，已保留原资料。请检查路径、权限，或选择其它文件夹。"))
        except Exception:
            self.results.put((False, "读取未完成，当前对话继续使用原资料。请重试或选择其它文件夹。"))

    def _cancel(self):
        self.cancel.set()
        self.cancel_button.configure(state="disabled")
        self.status.configure(text="正在取消，当前对话继续使用原资料…")

    def _close(self):
        if self.busy:
            self._cancel()
        self.top.withdraw()

    def _poll(self):
        try:
            while True:
                ok, result = self.results.get_nowait()
                if ok == "progress":
                    if not self.cancel.is_set():
                        self.status.configure(text=result)
                    continue
                self.busy = False
                self.progress.stop()
                self.progress_row.pack_forget()
                self.cancel_button.configure(state="disabled")
                for action in self.actions:
                    action.configure(state="normal")
                if ok:
                    self.materials_root = result["root"]
                    self.folder.configure(text=f"当前资料文件夹：{self.materials_root}")
                    self.status.configure(text=result["message"])
                    if hasattr(self, "_last_sources"):
                        self._render_sources()
                else:
                    self.status.configure(text=result)
        except queue.Empty:
            pass
        try:
            self.top.after(100, self._poll)
        except tk.TclError:
            pass

    def show(self, sources: list[dict], cited_ids: list[str], snapshot_root: str | None = None) -> None:
        self._show_current()
        self._last_sources = (sources, cited_ids, snapshot_root)
        self._render_sources()
        self.top.deiconify()
        self.top.lift()

    def _render_sources(self):
        sources, cited_ids, snapshot_root = self._last_sources
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        old_root = snapshot_root if snapshot_root and snapshot_root != self.materials_root else ""
        if sources:
            self.text.insert("end", source_text(old_root, sources, cited_ids))
        if self.controller is not None:
            issues = self.controller.current().get("issues", [])
            if issues:
                self.text.insert("end", "\n\n当前文件夹的读取提示（不随问题发送）：\n"
                                 + "\n\n".join(f"{issue['path']}\n{issue['message']}" for issue in issues) + "\n\n")
        if not sources:
            self.text.insert("end", source_text(old_root, sources, cited_ids))
        self.text.insert("end", "\n\n支持md/txt、Word正文/简单表格和文字PDF；出处标到行、段落或PDF页。\n"
                         "Word图片、页眉页脚、批注与文本框不参与查找；PDF扫描页请先转成文字。")
        self.text.configure(state="disabled")
