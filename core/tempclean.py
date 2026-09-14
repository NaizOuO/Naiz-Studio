"""清掉以前留在系統暫存資料夾的解壓檔(_MEI 開頭的資料夾)。

打包成單一 exe 的程式每次啟動都會把自己解壓到暫存資料夾,正常關閉時會自動刪掉;
被工作管理員強制結束、當機或停電時就會留下來,每次一百多 MB。
只刪確定是本程式(或擴充點登記的程式)留下、而且已經沒有程式在使用的資料夾。
"""

import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import plugins

# 其他同樣會留下暫存的程式,可以往這個擴充點登記它暫存資料夾裡特有的路徑
EXTENSION = "temp_markers"
MARKERS = ("plugins/pdf", "plugins/images")    # 本程式打包進去的插件資料夾
LIBRARY_SUFFIXES = (".dll", ".pyd")
MIN_AGE = 300    # 剛建立的資料夾可能是正在啟動、還沒載入 DLL 的程式,先不動
_FOLDER = re.compile(r"_MEI[0-9A-Fa-f]+")
_REMOVING = re.compile(r"_MEI[0-9A-Fa-f]+\.removing")


def in_use(folder: Path) -> bool:
    """執行中的程式會載入資料夾裡的 DLL,載入中的 DLL 沒辦法用寫入模式開啟。
    (把資料夾改名在 DLL 載入中也會成功,不能拿來判斷。)看不出來時一律當作使用中。"""
    libraries = [p for p in folder.iterdir() if p.suffix.lower() in LIBRARY_SUFFIXES]
    if not libraries:
        return True
    for library in libraries:
        try:
            with open(library, "r+b"):
                pass
        except OSError:
            return True
    return False


def stale_folders(markers, root=None, now=None):
    root = Path(root) if root is not None else Path(tempfile.gettempdir())
    now = time.time() if now is None else now
    own = getattr(sys, "_MEIPASS", None)
    own = Path(own).resolve() if own else None
    found = []
    for entry in root.iterdir():
        try:
            if _REMOVING.fullmatch(entry.name) and entry.is_dir():
                found.append(entry)     # 上次刪到一半(例如程式被關掉),確定是要刪的
                continue
            if not _FOLDER.fullmatch(entry.name) or not entry.is_dir():
                continue
            if own is not None and entry.resolve() == own:
                continue
            if now - entry.stat().st_mtime < MIN_AGE:
                continue
            if not any((entry / marker).exists() for marker in markers):
                continue
            if in_use(entry):
                continue
            found.append(entry)
        except OSError:
            continue
    return found


def clean(markers, root=None) -> int:
    """回傳刪掉幾個資料夾。先改名再刪:刪到一半被中斷時,下次啟動還認得出來繼續刪。"""
    removed = 0
    for folder in stale_folders(markers, root):
        target = folder
        if not _REMOVING.fullmatch(folder.name):
            target = folder.with_name(folder.name + ".removing")
            try:
                folder.rename(target)
            except OSError:
                continue
        shutil.rmtree(target, ignore_errors=True)
        removed += not target.exists()
    return removed


def start():
    """在背景清理,不拖慢啟動;要在插件載入後呼叫,擴充點登記的路徑才算得進去。"""
    markers = MARKERS + tuple(plugins.extensions(EXTENSION))
    threading.Thread(target=clean, args=(markers,), daemon=True).start()
