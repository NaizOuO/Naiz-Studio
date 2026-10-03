"""元件與空間:列出使用者同意後下載的元件(bin、models、字型包)和各佔多少空間,用不到的可以刪掉。

直接掃描資料夾,不靠各工具的元件清單(有些工具還沒打開,清單還沒載入);認得的檔案換成看得懂的名稱,
認不得的照檔名列出來。刪掉的元件下次用到時,程式會照常詢問要不要下載。
"""

import os
import shutil
import threading
import time
from pathlib import Path

from . import paths

# (名稱, 誰會用到, [(資料夾, 檔名或子資料夾)]):同一個功能的檔案算成一項,一起刪
KNOWN = [
    ("FFmpeg", "影音轉檔、錄音轉逐字稿、即時字幕", [("bin", "ffmpeg.exe"), ("bin", "ffprobe.exe")]),
    ("LibreOffice", "文件轉檔（電腦上沒有 Office 時）", [("bin", "libreoffice")]),
    ("文字辨識（Tesseract）", "掃描檔轉 Word、PDF 編輯器的掃描頁搜尋", [("bin", "tesseract"), ("bin", "tessdata")]),
    ("語音辨識程式", "錄音轉逐字稿、即時字幕", [("bin", "whisper-cpu")]),
    ("語音辨識程式（NVIDIA 加速）", "錄音轉逐字稿、即時字幕", [("bin", "whisper-cuda")]),
    ("辨識模型（快速）", "錄音轉逐字稿、即時字幕", [("models", "ggml-base.bin")]),
    ("辨識模型（輕量）", "即時字幕", [("models", "ggml-small-q8_0.bin")]),
    ("辨識模型（推薦）", "錄音轉逐字稿、即時字幕", [("models", "ggml-large-v3-turbo-q5_0.bin")]),
    ("辨識模型（最準確）", "錄音轉逐字稿、即時字幕", [("models", "ggml-large-v3.bin")]),
    ("人聲偵測", "錄音轉逐字稿、即時字幕", [("models", "ggml-silero-v6.2.0.bin"), ("models", "silero_vad.onnx")]),
    ("區分說話者", "錄音轉逐字稿", [("bin", "sherpa-onnx"), ("models", "3dspeaker-campplus-zh.onnx"),
                                ("models", "pyannote-segmentation-3-0.onnx")]),
    ("圖片高清", "圖片工具的高清", [("bin", "realesrgan"), ("bin", "realesrgan-models")]),
    ("去背", "圖片工具的去背", [("bin", "onnxruntime-dml"), ("models", "cutout")]),
    ("日文讀音字典（IPADIC）", "即時字幕的振假名", [("models", "ipadic")]),
    ("開源字型包", "PDF 編輯器的文字框與改字", [("fonts", "downloads")]),
]


def _roots():
    return {"bin": paths.BIN_DIR, "models": paths.MODELS_DIR, "fonts": paths.FONTS_DIR}


def size_of(path):
    """檔案或資料夾的大小(位元組);讀不到的檔案略過。"""
    path = Path(path)
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for folder, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                pass
    return total


def scan():
    """[{"name", "used_by", "paths": [Path], "size"}],大的排前面。下載到一半的暫存(.part)也算進去,刪得掉。"""
    roots = _roots()
    items, claimed = [], set()
    for name, used_by, parts in KNOWN:
        found = [roots[root] / entry for root, entry in parts if (roots[root] / entry).exists()]
        claimed.update(roots[root] / entry for root, entry in parts)
        if found:
            items.append({"name": name, "used_by": used_by, "paths": found})
    for key in ("bin", "models"):
        folder = roots[key]
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path not in claimed:
                items.append({"name": path.name, "used_by": "", "paths": [path]})
    for item in items:
        item["size"] = sum(size_of(path) for path in item["paths"])
    return sorted(items, key=lambda item: item["size"], reverse=True)


def remove(item):
    """刪掉一個元件;成功回傳 True。先把每個檔案/資料夾改名(正在使用時改名會失敗,這時什麼都不動),
    全部改名成功才真的刪,不會刪到一半讓元件壞掉。"""
    moved = []
    for path in item["paths"]:
        if not path.exists():
            continue
        trash = path.with_name(f".{path.name}.deleting")
        try:
            os.replace(path, trash)
        except OSError:
            for original, renamed in moved:         # 有一個改不了名(正在使用):已經改的改回來
                try:
                    os.replace(renamed, original)
                except OSError:
                    pass
            return False
        moved.append((path, trash))
    for _, trash in moved:
        if trash.is_dir():
            shutil.rmtree(trash, ignore_errors=True)
        else:
            try:
                trash.unlink()
            except OSError:
                pass
    return True


def human(size):
    for unit, step in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if size >= step:
            return f"{size / step:.1f} {unit}" if unit == "GB" else f"{size / step:.0f} {unit}"
    return f"{size} B"


class Scanner:
    """在背景掃描(LibreOffice 有上千個檔案,在畫面執行緒算會卡);完成後 .items 不是 None。"""

    def __init__(self):
        self.items = None
        self.started = time.monotonic()

    def start(self):
        def work():
            try:
                self.items = scan()
            except OSError:
                self.items = []
        threading.Thread(target=work, daemon=True).start()
        return self
