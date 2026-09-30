"""桌面捷徑:exe 版第一次開啟時詢問要不要在桌面建立捷徑。

捷徑指向目前的 exe;更新只換 exe 本身、檔名不變,所以更新後捷徑照樣能用。
用 Windows 內建的 PowerShell 建立(不需要額外套件),路徑經由環境變數傳入,中文與空白都不會出錯。
"""

import ctypes
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path

NAME = "Naiz Studio"
CSIDL_DESKTOPDIRECTORY = 0x10
CREATE_NO_WINDOW = 0x08000000
SCRIPT = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:NAIZ_LINK); "
          "$s.TargetPath = $env:NAIZ_EXE; $s.WorkingDirectory = $env:NAIZ_DIR; $s.IconLocation = $env:NAIZ_EXE + ',0'; "
          "$s.Save()")


def desktop_dir():
    """目前使用者的桌面(桌面被 OneDrive 接管時也找得到);找不到回傳 None。"""
    if platform.system() != "Windows":
        return None
    buffer = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_DESKTOPDIRECTORY, None, 0, buffer) != 0:
        return None
    return Path(buffer.value)


def link_path():
    folder = desktop_dir()
    return folder / f"{NAME}.lnk" if folder else None


def exists():
    link = link_path()
    return bool(link and link.exists())


def create(exe=None):
    """建立桌面捷徑;成功回傳 True。"""
    link = link_path()
    if link is None:
        return False
    exe = Path(exe or sys.executable)
    env = dict(os.environ, NAIZ_LINK=str(link), NAIZ_EXE=str(exe), NAIZ_DIR=str(exe.parent))
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", SCRIPT], env=env,
                       capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    return link.exists()


class Creator:
    """在背景建立,不讓畫面卡住;完成後 .done 為 True、.ok 是結果。"""

    def __init__(self, exe=None):
        self.exe = exe
        self.done = False
        self.ok = False

    def start(self):
        def work():
            self.ok = create(self.exe)
            self.done = True
        threading.Thread(target=work, daemon=True).start()
        return self
