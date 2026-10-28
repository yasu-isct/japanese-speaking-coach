"""User-opened local model settings; preparation and switching stay off Tk."""
from __future__ import annotations

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog


class ModelWindow:
    def __init__(self, master, family, controller, on_change=None):
        self.controller, self.on_change = controller, on_change
        self.results = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.info = {}
        self.top = tk.Toplevel(master)
        self.top.title("本地多语言查找")
        self.top.geometry("640x520")
        self.top.minsize(560, 500)
        self.top.protocol("WM_DELETE_WINDOW", self._close)
        body = ttk.Frame(self.top, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="让不同说法也能找到资料", font=(family, 16, "bold")).pack(anchor="w")
        self.help = ttk.Label(body, text="适合日语提问、中文笔记等情况。模型在本机查找；本轮命中片段仍会发给回答 API。",
                              wraplength=600)
        self.help.pack(anchor="w", pady=(8, 10))
        self.mode = ttk.Label(body)
        self.mode.pack(anchor="w")
        self.location = tk.Text(body, height=2, wrap="char", font=("Consolas", 10), relief="flat", takefocus=True)
        self.location.pack(fill="x", pady=(4, 10))
        actions = ttk.Frame(body)
        actions.pack(fill="x")
        self.toggle_button = ttk.Button(actions, command=self._toggle)
        self.toggle_button.pack(side="left")
        self.choose_button = ttk.Button(actions, text="选择并开启已有模型…", command=self._choose)
        self.choose_button.pack(side="left", padx=8)
        download = ttk.Frame(body)
        download.pack(fill="x", pady=(10, 0))
        self.download_button = ttk.Button(download, text="下载并开启（约128MB）", command=self._download)
        self.download_button.pack(side="left")
        self.cancel_button = ttk.Button(download, text="取消操作", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=8)
        self.disclosure = ttk.Label(body, text="只有点击下载才联网获取官方模型；已有文件通过校验就直接使用。准备期间可以继续对话。",
                                    wraplength=600)
        self.disclosure.pack(anchor="w", pady=(10, 8))
        self.progress = ttk.Progressbar(body, mode="indeterminate")
        self.status = ttk.Label(body, wraplength=600)
        self.status.pack(anchor="w", pady=(10, 0))
        self.actions = (self.toggle_button, self.choose_button, self.download_button)
        self.top.bind("<Configure>", self._resize)
        self._show_info()
        self.top.after(100, self._poll)

    def _resize(self, event):
        if event.widget is self.top:
            for label in (self.help, self.disclosure, self.status):
                label.configure(wraplength=max(250, event.width - 36))

    def _show_info(self, result=None):
        self.info = result or self.controller.model_info()
        self.mode.configure(text="当前：" + ("本地多语言查找" if self.info["enabled"] else "关键词查找"))
        self.location.configure(state="normal")
        self.location.delete("1.0", "end")
        self.location.insert("1.0", "模型位置：" + self.info["directory"])
        self.location.configure(state="disabled")
        self.toggle_button.configure(text="关闭，使用关键词" if self.info["enabled"] else "开启已有模型")
        self.status.configure(text=self.info["message"])

    def _toggle(self):
        self._start("switch", not self.info.get("enabled", False), None)

    def _choose(self):
        if self.busy:
            return
        selected = filedialog.askdirectory(parent=self.top, title="选择含tokenizer.json和onnx子文件夹的模型目录", mustexist=True)
        if selected:
            self._start("switch", True, Path(selected))

    def _download(self):
        self._start("prepare", True, None)

    def _start(self, action, enabled, directory):
        if self.busy:
            return
        self.busy = True
        self.cancel.clear()
        for button in self.actions:
            button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress.pack(fill="x", before=self.status)
        self.progress.start(15)
        self.status.configure(text="正在本机准备，当前对话继续使用原查找方式…")
        threading.Thread(target=self._work, args=(action, enabled, directory), daemon=True).start()

    def _work(self, action, enabled, directory):
        notify = lambda text: self.results.put(("progress", text))
        try:
            if action == "prepare":
                result = self.controller.prepare_semantic(progress=notify, cancel=self.cancel)
            else:
                result = self.controller.set_semantic(enabled, directory, progress=notify, cancel=self.cancel)
            self.results.put(("done", result))
        except (ValueError, RuntimeError) as exc:
            self.results.put(("error", str(exc)))
        except Exception:
            self.results.put(("error", "操作未完成，当前查找方式保持不变。请检查目录权限和可用空间后重试。"))

    def _poll(self):
        try:
            while True:
                kind, value = self.results.get_nowait()
                if kind == "progress":
                    self.status.configure(text=value)
                    continue
                self.busy = False
                self.progress.stop()
                self.progress.pack_forget()
                self.cancel_button.configure(state="disabled")
                for button in self.actions:
                    button.configure(state="normal")
                if kind == "done":
                    self._show_info(value)
                    if self.on_change is not None:
                        self.on_change()
                else:
                    self._show_info()
                    self.status.configure(text=value)
        except queue.Empty:
            pass
        try:
            self.top.after(100, self._poll)
        except tk.TclError:
            pass

    def _cancel(self):
        self.cancel.set()
        self.status.configure(text="正在取消，完成当前网络读取或本机步骤后停止…")

    def _close(self):
        if self.busy:
            self._cancel()
        self.top.withdraw()

    def show(self):
        if not self.busy:
            self._show_info()
        self.top.deiconify()
        self.top.lift()
