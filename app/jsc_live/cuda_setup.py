"""Make the CUDA libraries installed by pip (nvidia-cublas-cu12, nvidia-cudnn-cu12) loadable on Windows.

CTranslate2 looks for cublas/cudnn DLLs on the DLL search path. The pip wheels put
them under site-packages/nvidia/*/bin, which is not searched by default.
Call ``register_cuda_dlls()`` before importing faster_whisper.
"""

from __future__ import annotations

import glob
import os
import site
import sys


def register_cuda_dlls() -> list[str]:
    """Add every site-packages/nvidia/*/bin directory to the DLL search path."""
    if sys.platform != "win32":
        return []
    added = []
    for base in site.getsitepackages():
        for directory in glob.glob(os.path.join(base, "nvidia", "*", "bin")):
            os.add_dll_directory(directory)
            os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")
            added.append(directory)
    return added
