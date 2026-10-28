"""Load saved materials without blocking Tk or the audio worker."""
from __future__ import annotations

import threading

from .index_task import IndexCancelled, check_cancel


class MaterialsStartup:
    def __init__(self, controller, model_directory=None):
        self.controller = controller
        self.model_directory = model_directory
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._silent = False
        self._state = dict(busy=True, ready=False, phase="reading",
                           message="正在读取资料，字幕可先使用；资料就绪前回答暂不参考文件。",
                           notice="正在读取资料；就绪前回答暂不参考文件。点“资料”查看或取消。")
        self.thread = threading.Thread(target=self._work, daemon=True)

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    def _update(self, **values):
        with self._lock:
            self._state.update(values)
            if self._silent:
                self._state["notice"] = ""

    def cancel(self, *, silent=False):
        with self._lock:
            self._silent = self._silent or silent
            if silent:
                self._state["notice"] = ""
            if self._state["busy"]:
                self.cancelled.set()
                self._state["message"] = "正在停止资料准备，字幕继续可用…"

    def _progress(self, message):
        check_cancel(self.cancelled)
        self._update(message=message)

    def _work(self):
        from .materials import MaterialsStore

        try:
            with self.controller._operation(self.cancelled):
                current = self.controller.client.materials
                store = MaterialsStore(current.root, limits=current.limits, tokenizer=current._tokenizer,
                                       progress=self._progress, cancel=self.cancelled)
                check_cancel(self.cancelled)
                self.controller.client.replace_materials(store)
                self._update(ready=True)
                if self.model_directory is not None:
                    self._update(phase="semantic", message="资料已读取，正在准备多语言查找…",
                                 notice="资料已读取，正准备多语言查找；当前先用关键词。点“资料”查看或取消。")
                    from .semantic_materials import SemanticEncoder, SemanticMaterialsStore

                    encoder = SemanticEncoder(self.model_directory)
                    check_cancel(self.cancelled)
                    store = SemanticMaterialsStore(store.root, encoder=encoder, limits=store.limits,
                                                   tokenizer=store._tokenizer,
                                                   progress=self._progress, cancel=self.cancelled)
                    check_cancel(self.cancelled)
                    self.controller.client.replace_materials(store)
                info = self.controller.current()
                self._update(phase="done", message=info["message"],
                             notice="" if store.chunks else "还没有读到资料，点“资料”选择文件夹或查看原因。")
        except IndexCancelled:
            ready = self.snapshot()["ready"]
            self._update(phase="cancelled", message=("已取消多语言准备，已读取的资料继续使用关键词查找；可在“多语言查找”中重新开启。" if ready else
                         "已取消读取，当前回答不参考资料。可点“重新读取资料”继续。"),
                         notice="多语言准备已取消，当前使用关键词查找。" if ready else
                         "资料读取已取消，当前回答未使用资料。点“资料”重新读取。")
        except Exception:
            ready = self.snapshot()["ready"]
            self._update(phase="error", message=("多语言查找未能开启，已保留关键词资料。请在“多语言查找”检查模型位置或重新准备。" if ready else
                         "资料准备未完成。字幕继续可用，请在资料窗口检查路径或重新读取。"),
                         notice="多语言未能开启，当前使用关键词。点“资料”检查。" if ready else
                         "资料未就绪，当前回答未使用资料。点“资料”检查。")
        finally:
            self._update(busy=False)
