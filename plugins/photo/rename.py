"""批次改檔名:依樣式算出每個檔案的新名字,先預覽,再另外輸出一份或直接改原檔(可以復原)。

樣式裡可以放:{名稱} 原本的檔名(不含副檔名)、{序號} 依清單順序的編號、{日期} 拍攝日期(沒有就用修改日期)。
副檔名一律保留原本的。
"""

import os
import re
import shutil
import time
from pathlib import Path

from PIL import Image

TOKENS = [("{名稱}", "原本的檔名"), ("{序號}", "依清單順序的編號"), ("{日期}", "拍攝日期，沒有就用修改日期")]
BAD_CHARS = re.compile(r'[\\/:*?"<>|]')
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
DATE_TAGS = (36867, 36868, 306)     # 拍攝時間、數位化時間、修改時間(EXIF)
MAX_NAME = 255                      # Windows 一個檔名最多 255 個字
MAX_PATH = 259                      # 沒開長路徑支援時,連資料夾在內最多 259 個字


def taken_date(path):
    """拍攝日期 2026-10-01;照片沒有記錄時用檔案的修改日期。"""
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            values = [exif.get(tag) for tag in DATE_TAGS] + [exif.get_ifd(0x8769).get(tag) for tag in DATE_TAGS[:2]]
        for value in values:
            if isinstance(value, str) and re.match(r"\d{4}:\d{2}:\d{2}", value):
                return value[:10].replace(":", "-")
    except Exception:
        pass
    return time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))


def new_names(paths, pattern, start=1, digits=3, dates=None):
    """每個檔案的新檔名(含原本的副檔名);dates 可以先算好傳進來,不用每次都讀照片。"""
    names = []
    for number, path in enumerate(paths, start):
        path = Path(path)
        stem = (pattern.replace("{名稱}", path.stem)
                .replace("{序號}", str(number).zfill(digits))
                .replace("{日期}", (dates or {}).get(path) or taken_date(path) if "{日期}" in pattern else ""))
        names.append(stem.strip() + path.suffix)
    return names


def _length(text):
    """Windows 算長度的方式(UTF-16):少數罕用字算兩個字。"""
    return len(text.encode("utf-16-le")) // 2


def problems(paths, names, in_place, folder=None):
    """每個新名字的問題(沒問題是空字串):不能用的字、太長、重複、原資料夾已經有別的檔案叫這個名字。
    folder 是另外輸出時的資料夾(用來檢查整個路徑會不會太長)。"""
    result = []
    lowered = [name.lower() for name in names]
    moving = {Path(p).resolve() for p in paths}
    for path, name, low in zip(paths, names, lowered):
        stem = name[:len(name) - len(Path(path).suffix)]
        if not stem.strip():
            result.append("檔名是空的")
        elif BAD_CHARS.search(name):
            result.append('檔名不能有 \\ / : * ? " < > | 這些字')
        elif stem.upper() in RESERVED or name.endswith((" ", ".")):
            result.append("Windows 不能用這個檔名")
        elif _length(name) > MAX_NAME:
            result.append(f"檔名太長，最多 {MAX_NAME} 個字")
        elif (in_place or folder) and _length(str(Path(Path(path).parent if in_place else folder) / name)) > MAX_PATH:
            result.append("連資料夾在內的路徑太長，請把檔名改短一點")
        elif lowered.count(low) > 1:
            result.append("和其他檔案改成同一個名字")
        elif in_place:
            target = (Path(path).parent / name).resolve()
            result.append("資料夾裡已經有這個名字的檔案"
                          if target.exists() and target not in moving else "")
        else:
            result.append("")
    return result


def copy_all(paths, names, folder):
    """另外輸出:複製到 folder,原檔不動;回傳輸出的檔案。"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    done = []
    for path, name in zip(paths, names):
        target = folder / name
        shutil.copy2(path, target)
        done.append(target)
    return done


def rename_all(paths, names):
    """直接改原檔:先改成暫時的名字再改成新名字,A、B 互換名字也不會撞到;回傳 [(舊路徑, 新路徑)] 供復原。
    中途失敗時把已經改的改回來再丟出錯誤。"""
    pairs = [(Path(p), Path(p).parent / n) for p, n in zip(paths, names) if Path(p).name != n]
    temps, finished = [], []
    try:
        for index, (old, _) in enumerate(pairs):
            temp = old.with_name(f".naiz-rename-{os.getpid()}-{index}{old.suffix}")
            old.rename(temp)
            temps.append((old, temp))
        for (old, new), (_, temp) in zip(pairs, temps):
            temp.rename(new)
            finished.append((old, new))
    except OSError:
        moved = dict(finished)
        for old, temp in reversed(temps):
            current = moved.get(old, temp)
            try:
                current.rename(old)
            except OSError:
                pass
        raise
    return finished


def undo(pairs):
    """把直接改過的名字改回來。"""
    rename_all([new for _, new in pairs], [old.name for old, _ in pairs])
