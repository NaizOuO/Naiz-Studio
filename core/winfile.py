"""Windows 內建的「開啟檔案」「另存新檔」視窗;不需要額外套件。其他系統上回傳 None。"""

import ctypes
import platform
from ctypes import wintypes
from pathlib import Path

OFN_OVERWRITEPROMPT = 0x00000002
OFN_NOCHANGEDIR = 0x00000008
OFN_PATHMUSTEXIST = 0x00000800
OFN_FILEMUSTEXIST = 0x00001000
OFN_EXPLORER = 0x00080000
MAX_PATH_CHARS = 4096


class _OPENFILENAMEW(ctypes.Structure):
    _fields_ = [("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND), ("hInstance", wintypes.HINSTANCE),
                ("lpstrFilter", wintypes.LPCWSTR), ("lpstrCustomFilter", wintypes.LPWSTR),
                ("nMaxCustFilter", wintypes.DWORD), ("nFilterIndex", wintypes.DWORD),
                ("lpstrFile", wintypes.LPWSTR), ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", wintypes.LPWSTR),
                ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", wintypes.LPCWSTR),
                ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD), ("nFileOffset", wintypes.WORD),
                ("nFileExtension", wintypes.WORD), ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", wintypes.LPARAM),
                ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", wintypes.LPCWSTR),
                ("pvReserved", ctypes.c_void_p), ("dwReserved", wintypes.DWORD), ("FlagsEx", wintypes.DWORD)]


def available():
    return platform.system() == "Windows"


def _window_handle():
    try:
        import pygame

        return pygame.display.get_wm_info().get("window")
    except Exception:
        return None


def _dialog(save, title, initial_dir, filename, filters, default_ext):
    if not available():
        return None
    # 篩選條件是「說明\0樣式\0...\0\0」,中間有 \0,要用緩衝區傳入
    filter_buffer = ctypes.create_unicode_buffer("".join(f"{label}\0{pattern}\0" for label, pattern in filters) + "\0")
    file_buffer = ctypes.create_unicode_buffer(filename or "", MAX_PATH_CHARS)
    info = _OPENFILENAMEW()
    info.lStructSize = ctypes.sizeof(info)
    info.hwndOwner = _window_handle()
    info.lpstrFilter = ctypes.cast(filter_buffer, wintypes.LPCWSTR)
    info.lpstrFile = ctypes.cast(file_buffer, wintypes.LPWSTR)
    info.nMaxFile = MAX_PATH_CHARS
    info.lpstrInitialDir = str(initial_dir) if initial_dir else None
    info.lpstrTitle = title
    info.lpstrDefExt = default_ext
    info.Flags = OFN_EXPLORER | OFN_NOCHANGEDIR | OFN_PATHMUSTEXIST | (
        OFN_OVERWRITEPROMPT if save else OFN_FILEMUSTEXIST)
    dialog = ctypes.windll.comdlg32.GetSaveFileNameW if save else ctypes.windll.comdlg32.GetOpenFileNameW
    if not dialog(ctypes.byref(info)):
        return None     # 按了取消
    return Path(file_buffer.value)


def ask_open(title, filters, initial_dir=None):
    """filters:[(說明, "*.pdf;*.png")];回傳選的檔案,取消時回傳 None。"""
    return _dialog(False, title, initial_dir, "", filters, None)


def ask_save(title, filename, filters, default_ext, initial_dir=None):
    return _dialog(True, title, initial_dir, filename, filters, default_ext)
