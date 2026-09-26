"""檢查更新:啟動時在背景問 GitHub 最新的 Release,有新版本就通知;使用者同意後下載 zip,把 exe 換成新版。

只讀取公開的 Release 資訊(不送出任何資料)。擴充模組的 Release 不會標成「最新」,不會被當成主程式的新版。
正在執行的 exe 不能覆蓋,但可以改名:舊的改成 .old,新的放到原本的名字,下次開啟就是新版,.old 在啟動時清掉。
"""

import hashlib
import json
import re
import shutil
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

from . import files, paths, version

REPO = "NaizOuO/Naiz-Studio"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
APP_NAME = "Naiz Studio"
TAG_PATTERN = re.compile(r"^v?\d+\.\d+\.\d+$")


def _get_json(url, timeout=10):
    request = urllib.request.Request(url, headers={"User-Agent": "NaizStudio", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_release(data):
    """GitHub API 的 Release 換成需要的資訊;不是主程式的版本、或沒有比目前新就回傳 None。"""
    tag = str(data.get("tag_name", ""))
    if not TAG_PATTERN.match(tag) or data.get("draft") or data.get("prerelease"):
        return None
    if version.parse(tag) <= version.parse(version.VERSION):
        return None
    asset = next((a for a in data.get("assets", []) if str(a.get("name", "")).lower().endswith(".zip")
                  and "naiz" in str(a.get("name", "")).lower()), None)
    notes = [line.strip() for line in str(data.get("body", "")).splitlines() if line.strip()]
    digest = str(asset.get("digest") or "") if asset else ""
    return dict(tag=tag if tag.startswith("v") else "v" + tag, notes=notes, page=data.get("html_url") or RELEASES_PAGE,
                url=asset.get("browser_download_url") if asset else "", size=int(asset.get("size", 0)) if asset else 0,
                sha256=digest.split(":", 1)[1] if digest.startswith("sha256:") else "")


class Checker:
    """在背景檢查一次;結果放在 result(沒有新版或連不上時是 None)。"""

    def __init__(self, fetch=None):
        self.result = None
        self.done = False
        self._fetch = fetch or (lambda: _get_json(LATEST_URL))

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        try:
            self.result = parse_release(self._fetch())
        except Exception:
            self.result = None      # 沒網路、GitHub 暫時連不上:安靜略過,下次開啟再檢查
        self.done = True


# ------------------------------------------------------------ 下載並換成新版

def can_self_update():
    """只有 exe 版能自己換;從原始碼執行時改成打開下載頁面。"""
    return bool(getattr(sys, "frozen", False))


def cleanup():
    """上次更新留下的舊 exe 與暫存(新版啟動後舊的就沒在用了)。"""
    for old in paths.APP_DIR.glob("*.exe.old"):
        try:
            old.unlink()
        except OSError:
            pass
    shutil.rmtree(paths.APP_DIR / "update", ignore_errors=True)


class Updater:
    """背景下載更新:state 是 downloading、ready(已換好,重新開啟就是新版)或 failed;progress 0～1。"""

    def __init__(self, info, exe=None):
        self.info = info
        self.exe = Path(exe or sys.executable)
        self.state = "downloading"
        self.progress = 0.0
        self.message = ""
        self.cancel = threading.Event()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        try:
            self._download_and_swap()
            self.state = "ready"
        except Exception as error:
            self.message = str(error) or type(error).__name__
            self.state = "failed"

    def _download_and_swap(self):
        if not self.info.get("url"):
            raise RuntimeError("這個版本沒有可以下載的 zip")
        work = paths.APP_DIR / "update"
        work.mkdir(parents=True, exist_ok=True)
        archive = work / "update.zip"
        digest = hashlib.sha256()
        request = urllib.request.Request(self.info["url"], headers={"User-Agent": "NaizStudio"})
        with urllib.request.urlopen(request, timeout=60) as response, open(archive, "wb") as out:
            total = int(response.headers.get("Content-Length") or self.info.get("size") or 0)
            received = 0
            while True:
                if self.cancel.is_set():
                    raise RuntimeError("已取消")
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                self.progress = received / total if total else 0.0
        if self.info.get("sha256") and digest.hexdigest() != self.info["sha256"]:
            raise RuntimeError("下載的檔案不完整(檢查碼不符)")
        self.apply(archive)

    def apply(self, archive):
        """從 zip 取出新的 exe 與隨附檔案:exe 用改名的方式替換,其他檔案直接覆蓋。"""
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            exe_name = next((n for n in names if n.lower().endswith(f"/{APP_NAME.lower()}.exe")
                             or n.lower() == f"{APP_NAME.lower()}.exe"), None)
            if exe_name is None:
                raise RuntimeError("更新檔裡沒有程式")
            root = exe_name[:-len(Path(exe_name).name)]
            folder = self.exe.parent
            new_exe = folder / (self.exe.name + ".new")
            with zf.open(exe_name) as source, open(new_exe, "wb") as dest:
                shutil.copyfileobj(source, dest)
            for name in names:
                relative = name[len(root):] if name.startswith(root) else None
                if not relative or name.endswith("/") or name == exe_name or ".." in Path(relative).parts:
                    continue
                target = folder / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                files.write_bytes(target, zf.read(name))
        old = folder / (self.exe.name + ".old")
        if old.exists():
            old.unlink()
        self.exe.rename(old)                # 正在執行的 exe 可以改名,不能覆蓋
        new_exe.rename(self.exe)
