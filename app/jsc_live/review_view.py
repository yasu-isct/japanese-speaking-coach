"""User-opened archive picker and offline review exporter."""
from __future__ import annotations
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog
import webbrowser
from .review_control import ReviewController


class ReviewWindow:
    def __init__(self, master, family: str, controller=None):
        self.controller = controller if controller is not None else ReviewController()
        self.results = queue.Queue()
        self.sessions = {}
        self.selected_dir = None
        self.last_export = None
        self.busy = False
        self.top = tk.Toplevel(master)
        self.top.title("会话复盘")
        self.top.geometry("780x570")
        self.top.minsize(620, 460)
        self.top.protocol("WM_DELETE_WINDOW", self.top.withdraw)
        body = ttk.Frame(self.top, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        ttk.Label(body, text="选择一次存档，回听并练习", font=(family, 16, "bold")).grid(row=0, column=0, sticky="w")
        self.help = ttk.Label(body, text="只在本机整理已有字幕、建议与查词，不调用 API。原录音和旧导出都会保留。",
                              wraplength=740)
        self.help.grid(row=1, column=0, sticky="w", pady=(6, 10))
        actions = ttk.Frame(body)
        actions.grid(row=2, column=0, sticky="ew")
        self.refresh_button = ttk.Button(actions, text="刷新存档列表", command=self._refresh)
        self.refresh_button.pack(side="left")
        self.choose_button = ttk.Button(actions, text="选择其它存档文件夹…", command=self._choose)
        self.choose_button.pack(side="left", padx=8)
        listing = ttk.Frame(body)
        listing.grid(row=3, column=0, sticky="nsew", pady=10)
        self.tree = ttk.Treeview(listing, columns=("started", "minutes", "sentences"), show="headings",
                                 selectmode="browse", height=7)
        for name, label, width in (("started", "开始时间", 300), ("minutes", "分钟", 100), ("sentences", "字幕数", 100)):
            self.tree.heading(name, text=label)
            self.tree.column(name, width=width, minwidth=75, stretch=name == "started")
        scroll = ttk.Scrollbar(listing, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.tree.bind("<Button-1>", lambda event: "break" if self.busy else None)
        self.tree.bind("<Key>", lambda event: "break" if self.busy else None)
        self.selection_label = ttk.Label(body, text="尚未选择存档。", wraplength=740)
        self.selection_label.grid(row=4, column=0, sticky="w")
        export_bar = ttk.Frame(body)
        export_bar.grid(row=5, column=0, sticky="ew", pady=(10, 8))
        ttk.Label(export_bar, text="Anki格式：").pack(side="left")
        self.layout = tk.StringVar(value="Basic 双面卡")
        self.layout_box = ttk.Combobox(export_bar, textvariable=self.layout, state="readonly", width=18,
                                      values=("Basic 双面卡", "自建四字段"))
        self.layout_box.pack(side="left", padx=(0, 12))
        self.export_button = ttk.Button(export_bar, text="生成复盘页和卡片", command=self._export, state="disabled")
        self.export_button.pack(side="left")
        self.status = ttk.Label(body, text="正在准备存档列表…", wraplength=740)
        self.status.grid(row=6, column=0, sticky="w", pady=(0, 8))
        open_bar = ttk.Frame(body)
        open_bar.grid(row=7, column=0, sticky="ew")
        self.page_button = ttk.Button(open_bar, text="打开复盘页", command=self._open_page, state="disabled")
        self.page_button.pack(side="left")
        self.folder_button = ttk.Button(open_bar, text="打开输出文件夹", command=self._open_folder, state="disabled")
        self.folder_button.pack(side="left", padx=8)
        self.top.bind("<Configure>", self._resize)
        self.top.after(100, self._poll)

    def _resize(self, event):
        if event.widget is self.top:
            for label in (self.help, self.selection_label, self.status):
                label.configure(wraplength=max(250, event.width - 28))

    def _set_busy(self, value):
        self.busy = value
        state = "disabled" if value else "normal"
        self.refresh_button.configure(state=state)
        self.choose_button.configure(state=state)
        self.layout_box.configure(state="disabled" if value else "readonly")
        self.export_button.configure(state="disabled" if value or self.selected_dir is None else "normal")

    def _clear_export(self):
        self.last_export = None
        self.page_button.configure(state="disabled")
        self.folder_button.configure(state="disabled")

    def _select(self, _event=None):
        if self.busy:
            return
        selection = self.tree.selection()
        if not selection or selection[0] not in self.sessions:
            return
        session = self.sessions[selection[0]]
        self.selected_dir = session.path
        self.selection_label.configure(text=f"已选：{session.path}")
        self._clear_export()
        self.export_button.configure(state="normal")
        self.status.configure(text="点击生成即可。旧存档没有查词记录时，仍能复盘，卡片数为零。" if session.ended else
                              "这份存档未记录结束时间；可能仍在录音或未完整保存。可以导出现有记录。")

    def _choose(self):
        if self.busy:
            return
        folder = filedialog.askdirectory(parent=self.top, title="选择一次会话的存档文件夹", mustexist=True)
        if folder:
            self.selected_dir = Path(folder)
            self.selection_label.configure(text=f"已选：{self.selected_dir}")
            self.tree.selection_remove(*self.tree.selection())
            self._clear_export()
            self.export_button.configure(state="normal")
            self.status.configure(text="点击生成。请选择包含字幕或建议记录的一次会话文件夹。")

    def _refresh(self):
        if self.busy:
            return
        self._set_busy(True)
        self.status.configure(text="正在本机读取最近存档…")
        threading.Thread(target=self._load_list, daemon=True).start()

    def _load_list(self):
        try:
            self.results.put(("list", True, self.controller.list_sessions()))
        except (OSError, ValueError):
            self.results.put(("list", False, "无法读取默认存档目录；可以选择其它存档文件夹。"))

    def _export(self):
        if self.busy or self.selected_dir is None:
            return
        self._clear_export()
        self._set_busy(True)
        self.status.configure(text="正在本机生成复盘页和卡片…")
        layout = "fields" if self.layout.get() == "自建四字段" else "basic"
        threading.Thread(target=self._generate, args=(self.selected_dir, layout), daemon=True).start()

    def _generate(self, folder, layout):
        try:
            self.results.put(("export", True, self.controller.export(folder, layout)))
        except ValueError as exc:
            self.results.put(("export", False, str(exc)))
        except OSError:
            self.results.put(("export", False, "导出失败，请检查存档可读、磁盘有空间且输出目录可写。原存档未改动。"))

    def _poll(self):
        try:
            while True:
                kind, ok, result = self.results.get_nowait()
                self._set_busy(False)
                if not ok:
                    self.status.configure(text=result)
                elif kind == "list":
                    self.tree.delete(*self.tree.get_children())
                    self.sessions = {str(i): session for i, session in enumerate(result)}
                    for key, session in self.sessions.items():
                        self.tree.insert("", "end", iid=key, values=(session.started,
                                         f"{session.minutes:.1f}" if session.minutes is not None else "—",
                                         session.sentences if session.sentences is not None else "—"))
                    self._clear_export()
                    self.selected_dir = None
                    self.selection_label.configure(text="请选择列表中的一次存档。")
                    self.export_button.configure(state="disabled")
                    self.status.configure(text=(f"显示最近 {len(result)} 次存档（最多200次）；更早的可手动选择。" if result else
                                                "还没有存档。用录音版保存一次对话后再来，也可选择其它存档文件夹。"))
                else:
                    self.last_export = result
                    text = f"已生成：{result.sentence_count}条字幕、{result.turn_count}轮建议、{result.card_count}张卡。"
                    if not result.card_count:
                        text += " 这份存档没有可导出的查词记录，没有补造释义。"
                    if result.diagnostics:
                        text += " 部分记录未完整读取，详情见复盘页。"
                    self.status.configure(text=text)
                    self.page_button.configure(state="normal")
                    self.folder_button.configure(state="normal")
        except queue.Empty:
            pass
        try:
            self.top.after(100, self._poll)
        except tk.TclError:
            pass

    def _open_page(self):
        if self.last_export is not None:
            if not webbrowser.open(self.last_export.html_path.as_uri()):
                self.status.configure(text="浏览器未能打开，请在输出文件夹双击review.html。")

    def _open_folder(self):
        if self.last_export is not None:
            try:
                os.startfile(str(self.last_export.html_path.parent))
            except (OSError, AttributeError):
                self.status.configure(text=f"请手动打开：{self.last_export.html_path.parent}")

    def show(self):
        self.top.deiconify()
        self.top.lift()
        self._refresh()


def run_review_window():
    root = tk.Tk()
    root.withdraw()
    view = ReviewWindow(root, "TkDefaultFont")
    view.top.protocol("WM_DELETE_WINDOW", root.destroy)
    view.show()
    root.mainloop()
