"""A user-opened view of the snippets sent with the current suggestion."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def source_text(root: str, sources: list[dict], cited_ids: list[str]) -> str:
    lines = [f"资料文件夹：{root}", "",
             "下面是点击“资料”时这条建议发送给 API 的片段。新建议出现后，再点“资料”可刷新。",
             "“回答引用”表示模型声明使用，不代表已经核实。", ""]
    if not sources:
        lines += ["这轮没有查到相关片段。", "可在资料文件夹放入 UTF-8 的 .md / .txt 文件，修改后重新启动应用。"]
    for source in sources:
        label = "回答引用" if source["source_id"] in cited_ids else "本轮发送"
        lines += [f"{label} · {source['relative_path']} · 第{source['start_line']}–{source['end_line']}行",
                  source.get("heading", ""), source["text"], ""]
    return "\n".join(lines)


class MaterialsWindow:
    def __init__(self, master, family: str, materials_root: str):
        self.materials_root = materials_root
        self.top = tk.Toplevel(master)
        self.top.title("本轮参考资料")
        self.top.geometry("720x480")
        self.top.protocol("WM_DELETE_WINDOW", self.top.withdraw)
        self.text = tk.Text(self.top, wrap="word", font=(family, 12), padx=16, pady=12)
        scroll = ttk.Scrollbar(self.top, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        self.text.configure(state="disabled")

    def show(self, sources: list[dict], cited_ids: list[str]) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", source_text(self.materials_root, sources, cited_ids))
        self.text.configure(state="disabled")
        self.top.deiconify()
        self.top.lift()
