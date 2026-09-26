"""擴充模組:哪些已經啟用、拖曳 zip 安裝、移除,以及每個模組自己的資料夾。

模組就是 mods 裡的一個資料夾(裡面有 __init__.py)。模組是會在本地執行的程式碼,
所以新放進來的模組第一次要使用者按「啟用」才會載入;拖曳 zip 安裝時的確認就算啟用。
"""

import ctypes
import re
import shutil
import zipfile
from ctypes import wintypes
from pathlib import Path

from . import paths, theme

CONFIG_KEY = "mods_enabled"
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")


# ------------------------------------------------------------ 啟用清單(存在 config.json)

def enabled():
    return set(theme.load_config(str(paths.APP_DIR)).get(CONFIG_KEY, []))


def _save_enabled(names):
    stored = theme.load_config(str(paths.APP_DIR))       # 只改這一個鍵,其他設定照原樣寫回
    stored[CONFIG_KEY] = sorted(names)
    theme.save_config(str(paths.APP_DIR), stored)


def enable(name):
    _save_enabled(enabled() | {name})


def disable(name):
    _save_enabled(enabled() - {name})


def folders():
    """mods 裡所有模組資料夾的名稱。"""
    if not paths.MODS_DIR.is_dir():
        return []
    return sorted(entry.name for entry in paths.MODS_DIR.iterdir()
                  if entry.is_dir() and (entry / "__init__.py").is_file())


def pending():
    """放進來了但還沒啟用的模組。"""
    allowed = enabled()
    return [name for name in folders() if name not in allowed]


def data_dir(tool_id):
    """模組存設定、快取的地方(mods_data\\模組代號);移除模組時不會刪掉,重新安裝還在。"""
    folder = paths.APP_DIR / "mods_data" / tool_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# ------------------------------------------------------------ 從 __init__.py 讀出名稱、版本、作者(不執行程式碼)

def describe(source_text):
    """從模組的 __init__.py 原始碼找出 name、version、author、min_app;找不到的欄位是空字串。"""
    info = {}
    for key in ("name", "version", "author", "min_app"):
        match = re.search(rf'^\s*{key}\s*=\s*["\'](.*?)["\']', source_text, re.M)
        info[key] = match.group(1) if match else ""
    return info


def describe_folder(name):
    try:
        text = (paths.MODS_DIR / name / "__init__.py").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    return describe(text)


# ------------------------------------------------------------ zip 安裝

class InstallError(Exception):
    pass


def inspect_zip(path):
    """看 zip 裡是哪個模組:回傳 (資料夾名稱, zip 裡的前綴, 模組資訊)。
    zip 可以是「模組資料夾/__init__.py」,也可以是「mods/模組資料夾/...」或直接把檔案放在最外層。"""
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as error:
        raise InstallError("這不是可以讀的 zip 檔") from error
    with archive:
        names = [n.replace("\\", "/") for n in archive.namelist()]
        inits = sorted((n for n in names if n.endswith("__init__.py")), key=lambda n: n.count("/"))
        if not inits:
            raise InstallError("zip 裡沒有模組(找不到 __init__.py)")
        prefix = inits[0][:-len("__init__.py")]
        folder = prefix.rstrip("/").split("/")[-1] if prefix else Path(path).stem
        folder = re.sub(r"[-_ ]?v?\d+(\.\d+)*$", "", folder) if not prefix else folder   # 「circuit-v0.1.0.zip」→ circuit
        if not NAME_PATTERN.match(folder or ""):
            raise InstallError(f"模組資料夾名稱「{folder}」只能用英文、數字、底線")
        text = archive.read(inits[0]).decode("utf-8", errors="replace")
        for name in names:
            if name.startswith(prefix) and (".." in Path(name).parts or name.startswith("/") or ":" in name):
                raise InstallError("zip 裡有不安全的路徑,已停止安裝")
    return folder, prefix, describe(text)


def install_zip(path):
    """解壓縮到 mods\\資料夾名稱(已經有的話整個換掉,也就是更新),並直接啟用。回傳資料夾名稱。"""
    folder, prefix, _ = inspect_zip(path)
    target = paths.MODS_DIR / folder
    temp = paths.MODS_DIR / f".{folder}.installing"
    shutil.rmtree(temp, ignore_errors=True)
    temp.mkdir(parents=True)
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                name = info.filename.replace("\\", "/")
                if not name.startswith(prefix) or name.endswith("/"):
                    continue
                relative = name[len(prefix):]
                out = temp / relative
                out.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(out, "wb") as dest:
                    shutil.copyfileobj(source, dest)
        if target.exists():
            _recycle(target)
        temp.rename(target)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    enable(folder)
    return folder


# ------------------------------------------------------------ 移除(丟到資源回收筒,還救得回來)

class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]


FO_DELETE = 3
FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x4, 0x10, 0x40, 0x400


def _recycle(folder):
    """把資料夾丟進資源回收筒;不是 Windows 或失敗時改成直接刪除。"""
    try:
        op = _SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        op.pFrom = str(Path(folder).resolve()) + "\0\0"
        op.fFlags = FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI
        if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not Path(folder).exists():
            return
    except (AttributeError, OSError):
        pass
    shutil.rmtree(folder, ignore_errors=True)


def remove(name):
    """移除模組:資料夾丟進資源回收筒,從啟用清單拿掉;模組自己的資料(mods_data)留著。"""
    folder = paths.MODS_DIR / name
    if folder.is_dir():
        _recycle(folder)
    disable(name)
