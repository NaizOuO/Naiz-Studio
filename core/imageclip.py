"""把圖片放進 Windows 剪貼簿、從剪貼簿讀出圖片;不需要額外套件。其他系統上什麼都不做。

放進去時同時放兩種格式:一般的點陣圖(小畫家、Word 都能貼),以及 PNG(保留透明,較新的程式會優先用)。
"""

import ctypes
import io
import platform
from ctypes import wintypes

CF_DIB = 8
GMEM_MOVEABLE = 0x0002


def _api():
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    return user32, kernel32


def _global(kernel32, data):
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        return None
    pointer = kernel32.GlobalLock(handle)
    ctypes.memmove(pointer, data, len(data))
    kernel32.GlobalUnlock(handle)
    return handle


def sequence():
    """剪貼簿的變更次數;和上次記下的不同,代表之後有別的程式放了新東西進去。"""
    if platform.system() != "Windows":
        return 0
    return ctypes.windll.user32.GetClipboardSequenceNumber()


def copy_image(png):
    """png 是 PNG 檔的內容;成功回傳 True。"""
    if platform.system() != "Windows":
        return False
    from PIL import Image

    image = Image.open(io.BytesIO(png))
    image.load()
    flat = image.convert("RGBA")
    ground = Image.new("RGB", flat.size, (255, 255, 255))
    ground.paste(flat, mask=flat.getchannel("A"))       # 不支援透明的程式看到的是白底
    buffer = io.BytesIO()
    ground.save(buffer, "BMP")
    dib = buffer.getvalue()[14:]        # 去掉 BMP 檔頭,剩下的就是剪貼簿要的 DIB
    user32, kernel32 = _api()
    for _ in range(5):                  # 別的程式剛好在用剪貼簿時稍等再試
        if user32.OpenClipboard(None):
            break
        kernel32.Sleep(20)
    else:
        return False
    try:
        user32.EmptyClipboard()
        ok = False
        for kind, data in ((CF_DIB, dib), (user32.RegisterClipboardFormatW("PNG"), png)):
            handle = _global(kernel32, data)
            if handle is None:
                continue
            if user32.SetClipboardData(kind, handle):
                ok = True
            else:
                kernel32.GlobalFree(handle)
        return ok
    finally:
        user32.CloseClipboard()


def _read_png():
    user32, kernel32 = _api()
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kind = user32.RegisterClipboardFormatW("PNG")
    if not user32.IsClipboardFormatAvailable(kind) or not user32.OpenClipboard(None):
        return None
    try:
        handle = user32.GetClipboardData(kind)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        try:
            return ctypes.string_at(pointer, kernel32.GlobalSize(handle)) if pointer else None
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def paste_image():
    """剪貼簿裡的圖片(複製的圖片,或在檔案總管複製的圖片檔),轉成 PNG 內容;沒有時回傳 None。"""
    if platform.system() != "Windows":
        return None
    png = _read_png()
    if png is not None:
        return png                      # 有 PNG 格式就用它,才留得住透明
    try:
        from PIL import Image, ImageGrab

        found = ImageGrab.grabclipboard()
        if isinstance(found, list):
            image = None
            for name in found:
                try:
                    image = Image.open(name)
                    image.load()
                    break
                except Exception:
                    image = None
            found = image
        if found is None:
            return None
        buffer = io.BytesIO()
        found.save(buffer, "PNG")
        return buffer.getvalue()
    except Exception:
        return None
