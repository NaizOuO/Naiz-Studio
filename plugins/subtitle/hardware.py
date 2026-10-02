"""看電腦配備給建議:有沒有 NVIDIA 顯示卡、顯示卡記憶體多大、電腦記憶體多大。
建議只是預先選好,所有選項都還是可以自己換(弱電腦能用、強電腦也能選更大的模型)。"""

import ctypes
import shutil
import subprocess
from functools import cache

from core import transcribe


@cache
def gpu_memory():
    """NVIDIA 顯示卡的記憶體(GB);沒有 NVIDIA 顯示卡是 0。"""
    if not transcribe.has_nvidia() or shutil.which("nvidia-smi") is None:
        return 0
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10, creationflags=0x08000000).stdout
        return round(max(int(line) for line in out.split() if line.strip().isdigit()) / 1024)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


@cache
def ram():
    """電腦的記憶體(GB)。"""
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(status)
    try:
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return round(status.ullTotalPhys / 1024 ** 3)
    except (AttributeError, OSError):
        return 0


def recommend():
    """(辨識模型, 翻譯模型, 講到一半就試翻, 說明)"""
    vram = gpu_memory()
    if vram == 0:
        return "small", "translategemma:4b", False, "沒有偵測到 NVIDIA 顯示卡：建議用「輕量」和翻譯專用的小模型，只翻講完的句子"
    if vram < 10:
        return "turbo", "translategemma:4b", True, f"顯示卡記憶體約 {vram} GB：建議「推薦」和翻譯專用的小模型"
    return "turbo", "qwen3:8b", True, f"顯示卡記憶體約 {vram} GB：建議「推薦」和 qwen3:8b"


def describe():
    vram = gpu_memory()
    gpu = f"NVIDIA 顯示卡 {vram} GB" if vram else "沒有 NVIDIA 顯示卡"
    return f"{gpu}，記憶體 {ram()} GB"
