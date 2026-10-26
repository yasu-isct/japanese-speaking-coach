"""A user-opened view of the snippets sent with the current suggestion."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog
import queue
import threading


def source_text(root: str, sources: list[dict], cited_ids: list[str]) -> str:
    lines = [f"这条回答使用的资料文件夹：{root}" if root else "本轮参考片段", "",
             "下面是点击“资料”时这条建议发送给 API 的片段。新建议出现后，再点“资料”可刷新。",
             "“回答引用”表示模型声明使用，不代表已经核实。", ""]
    if not sources:
        lines += ["这轮没有查到相关片段。", "可在资料文件夹放入 UTF-8 的 .md / .txt 文件，修改后点“重新读取资料”。"]
    for source in sources:
        label = "回答引用" if source["source_id"] in cited_ids else "本轮发送"
        lines += [f"{label} · {source['relative_path']} · 第{source['start_line']}–{source['end_line']}行",
                  source.get("heading", ""), source["text"], ""]
    return "\n".join(lines)


class MaterialsWindow:
    def __init__(self, master, family: str, materials_root: str, controller=None):
        self.materials_root = materials_root
        self.controller = controller
        self.results = queue.Queue()
        self.busy = False
        self.actions = []
        self.top = tk.Toplevel(master)
        self.top.title("资料与本轮出处")
        self.top.geometry("780x600")
        self.top.minsize(520, 400)
        self.top.protocol("WM_DELETE_WINDOW", self.top.withdraw)
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
            for text, command in (("选择资料文件夹…", self._choose), ("重新读取资料", self._reload)):
                button = ttk.Button(actions, text=text, command=command)
                button.pack(side="left", padx=(0, 8))
                self.actions.append(button)
        self.status = ttk.Label(header, wraplength=720)
        self.status.pack(anchor="w", pady=(8, 0))
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
                label.configure(wraplength=max(200, event.width - 24))

    def _choose(self):
        if not self.busy:
            folder = filedialog.askdirectory(parent=self.top, title="选择愿意用于回答的资料文件夹", mustexist=True)
            if folder:
                self._reload(folder)

    def _reload(self, folder=None):
        if self.controller is None or self.busy:
            return
        self.busy = True
        for action in self.actions:
            action.configure(state="disabled")
        self.status.configure(text="正在本机读取资料，当前对话继续使用原资料…")
        threading.Thread(target=self._load, args=(folder,), daemon=True).start()

    def _load(self, folder):
        try:
            self.results.put((True, self.controller.reload(folder)))
        except ValueError as exc:
            self.results.put((False, str(exc)))
        except OSError:
            self.results.put((False, "这个文件夹无法读取，已保留原资料。请检查路径、权限，或选择其它文件夹。"))

    def _poll(self):
        try:
            while True:
                ok, result = self.results.get_nowait()
                self.busy = False
                for action in self.actions:
                    action.configure(state="normal")
                if ok:
                    self.materials_root = result["root"]
                    self.folder.configure(text=f"当前资料文件夹：{self.materials_root}")
                    self.status.configure(text=result["message"])
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
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        old_root = snapshot_root if snapshot_root and snapshot_root != self.materials_root else ""
        self.text.insert("1.0", source_text(old_root, sources, cited_ids))
        self.text.configure(state="disabled")
        self.top.deiconify()
        self.top.lift()
