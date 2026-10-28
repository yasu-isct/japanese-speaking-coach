"""Cooperative cancellation/progress while building a replacement materials index."""


class IndexCancelled(ValueError):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise IndexCancelled('已取消，本次更改未应用，当前对话继续使用原资料。')


def announce(progress, message, cancel=None):
    check_cancel(cancel)
    if progress is not None:
        progress(message)
    check_cancel(cancel)
