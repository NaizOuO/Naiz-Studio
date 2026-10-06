"""檢查更新:啟動時在背景問 GitHub 最新的 Release,有新版本就通知;使用者同意後下載 zip,把 exe 換成新版。

只讀取公開的 Release 資訊(不送出任何資料)。擴充模組的 Release 不會標成「最新」,不會被當成主程式的新版。
正在執行的 exe 不能覆蓋,但可以改名:舊的改成 .old,新的放到原本的名字,下次開啟就是新版,.old 在啟動時清掉。

補丁:每個 Release 除了完整 zip,還附一個「從上一版升級」的補丁(打包時由 build.py 產生)。
補丁裡是新舊 exe 的差異(zstd 格式,用舊 exe 當參考資料解開就是新 exe)與有改過的隨附檔案。
落後好幾版時依序套用每一版的補丁;接不起來、太多版、加起來不划算,或 exe 和官方版本對不上時,改下載完整 zip。
"""

import hashlib
import json
import re
import shutil
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import files, paths, version

REPO = "NaizOuO/Naiz-Studio"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=50"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
APP_NAME = "Naiz Studio"
TAG_PATTERN = re.compile(r"^v?\d+\.\d+\.\d+$")
MAX_PATCHES = 10            # 落後超過這麼多版就直接下載完整版
WINDOW_LOG_MAX = 31         # 補丁以整個舊 exe 當參考資料,解開時要允許很大的參考範圍


def tag_of(text):
    return "v" + str(text).lstrip("vV")


def patch_name(old_tag, new_tag):
    """補丁在 Release 裡的檔名;build.py 產生時也用這個。"""
    return f"Naiz-Studio-{tag_of(new_tag)}-from-{tag_of(old_tag)}.patch"


def _sha256(asset):
    digest = str(asset.get("digest") or "")
    return digest.split(":", 1)[1] if digest.startswith("sha256:") else ""


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
    return dict(tag=tag_of(tag), notes=notes, page=data.get("html_url") or RELEASES_PAGE,
                url=asset.get("browser_download_url") if asset else "", size=int(asset.get("size", 0)) if asset else 0,
                sha256=_sha256(asset) if asset else "", patches=None)


def plan_patches(releases, current, target, full_size=0):
    """從目前版本一路接到 target 要依序下載的補丁 [{url, size, sha256}];
    中間哪一版沒有補丁、落後太多版,或補丁加起來比完整版還大時回傳 None(改下載完整版)。"""
    by_version = {}
    for data in releases:
        tag = str(data.get("tag_name", ""))
        if TAG_PATTERN.match(tag) and not data.get("draft") and not data.get("prerelease"):
            by_version[version.parse(tag)] = data
    low, high = version.parse(current), version.parse(target)
    chain = [v for v in sorted(by_version) if low < v <= high]
    if not chain or chain[-1] != high or len(chain) > MAX_PATCHES:
        return None
    steps, previous = [], tag_of(current)
    for key in chain:
        tag = tag_of(by_version[key]["tag_name"])
        name = patch_name(previous, tag)
        asset = next((a for a in by_version[key].get("assets", []) if a.get("name") == name), None)
        if asset is None or not asset.get("browser_download_url"):
            return None
        steps.append(dict(url=asset["browser_download_url"], size=int(asset.get("size", 0)), sha256=_sha256(asset)))
        previous = tag
    if full_size and sum(step["size"] for step in steps) >= full_size:
        return None
    return steps


CHECK_DELAY = 3.0           # 開啟後等一下才檢查:先讓視窗開好,網路慢也不會和開啟搶時間
CHECK_TIMEOUT = 8           # GitHub 這麼久沒回就放棄(這次不檢查,下次開啟再試)


class Checker:
    """在背景檢查一次;結果放在 result(沒有新版或連不上時是 None)。不會擋住程式開啟或操作。"""

    def __init__(self, fetch=None, fetch_all=None, delay=CHECK_DELAY):
        self.result = None
        self.error = False              # 連不上 GitHub
        self.done = False
        self.delay = delay
        self._fetch = fetch or (lambda: _get_json(LATEST_URL, CHECK_TIMEOUT))
        self._fetch_all = fetch_all or (lambda: _get_json(RELEASES_URL, CHECK_TIMEOUT))

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        time.sleep(self.delay)
        try:
            self.result = parse_release(self._fetch())
        except Exception:
            self.result = None      # 沒網路、GitHub 暫時連不上:安靜略過,下次開啟再檢查
            self.error = True
        if self.result and can_self_update():
            try:
                self.result["patches"] = plan_patches(self._fetch_all(), version.VERSION, self.result["tag"],
                                                      self.result["size"])
            except Exception:
                self.result["patches"] = None
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


def refresh_icon(path):
    """通知檔案總管這個檔案換過了:不然總管會一直顯示快取裡的舊圖示(內容視窗裡卻是新的)。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SHChangeNotify(0x00002000, 0x0005, str(path), None)     # SHCNE_UPDATEITEM, SHCNF_PATHW
    except Exception:
        pass


class Updater:
    """背景下載更新:state 是 downloading、installing(正在換檔案,幾秒內完成)、ready(已換好,重新開啟就是新版)、
    cancelled(使用者取消)或 failed;progress 0～1。下載中隨時可以取消或關閉程式(下載到一半的檔案下次開啟時清掉);
    換檔案的那幾秒不能中斷,關閉程式時會等它做完(wait_installed)。"""

    def __init__(self, info, exe=None):
        self.info = info
        self.exe = Path(exe or sys.executable)
        self.state = "downloading"
        self.progress = 0.0
        self.remaining = None           # 預估還要幾秒;剛開始下載還算不準時是 None
        self.used_patch = False         # 這次是用補丁更新的
        self.patch_error = ""           # 補丁套用失敗、改下載完整版的原因
        self.message = ""
        self.cancel = threading.Event()
        self._installed = threading.Event()
        self._installed.set()
        self._lock = threading.Lock()       # 取消和「開始換檔案」不會同時發生

    def stop(self):
        """取消下載:馬上算取消(網路很慢時,下載的那條線可能要等一下才收到,收到後自己清掉暫存);
        換檔案開始後就不能取消,會照常做完。"""
        with self._lock:
            if self.state == "downloading":
                self.cancel.set()
                self.state = "cancelled"

    def wait_installed(self, timeout=30):
        """關閉程式前呼叫:正在換檔案時等它做完,不會留下壞掉的 exe。"""
        return self._installed.wait(timeout)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        try:
            self._download_and_swap()
            self.state = "ready"
        except Exception as error:
            if self.cancel.is_set():
                self.state = "cancelled"
            else:
                self.message = str(error) or type(error).__name__
                self.state = "failed"
            shutil.rmtree(paths.APP_DIR / "update", ignore_errors=True)
        finally:
            self._installed.set()

    def _download_and_swap(self):
        work = paths.APP_DIR / "update"
        work.mkdir(parents=True, exist_ok=True)
        patches = self.info.get("patches")
        if patches:
            try:
                self.apply_patches([self._download(step, work / f"patch{i}.patch", patches)
                                    for i, step in enumerate(patches)])
                self.used_patch = True
                return
            except Exception as error:
                if self.cancel.is_set():
                    raise
                self.patch_error = str(error) or type(error).__name__     # 改下載完整版
                self.progress, self.remaining = 0.0, None
        if not self.info.get("url"):
            raise RuntimeError("這個版本沒有可以下載的 zip")
        full = dict(url=self.info["url"], size=self.info.get("size", 0), sha256=self.info.get("sha256", ""))
        self.apply(self._download(full, work / "update.zip", [full]))

    def _download(self, step, target, steps):
        """下載一個檔案並核對檢查碼;進度以 steps 全部的大小計算。"""
        total = sum(int(s.get("size") or 0) for s in steps)
        before = sum(int(s.get("size") or 0) for s in steps[:steps.index(step)])
        digest = hashlib.sha256()
        request = urllib.request.Request(step["url"], headers={"User-Agent": "NaizStudio"})
        with urllib.request.urlopen(request, timeout=30) as response, open(target, "wb") as out:
            total = total or int(response.headers.get("Content-Length") or 0)
            received = 0
            started = time.monotonic()
            while True:
                if self.cancel.is_set():
                    raise RuntimeError("已取消")
                chunk = response.read1(1 << 16)      # 有多少先拿多少:網路很慢時也能很快發現被取消
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                self.progress = (before + received) / total if total else 0.0
                elapsed = time.monotonic() - started
                if total and elapsed > 2:
                    self.remaining = (total - before - received) / (received / elapsed)
        if step.get("sha256") and digest.hexdigest() != step["sha256"]:
            raise RuntimeError("下載的檔案不完整(檢查碼不符)")
        return target

    def apply_patches(self, patch_files):
        """依序套用補丁:每一步先確認手上的 exe 正是補丁要的那一版,解開後再確認和官方新版一模一樣。"""
        from compression import zstd

        current = self.exe.read_bytes()
        extra = {}
        for patch in patch_files:
            with zipfile.ZipFile(patch) as zf:
                manifest = json.loads(zf.read("manifest.json"))
                if hashlib.sha256(current).hexdigest() != manifest["from_sha256"]:
                    raise RuntimeError("目前的程式和補丁對不上")
                current = zstd.decompress(zf.read("exe.zst"),
                                          zstd_dict=zstd.ZstdDict(current, is_raw=True).as_prefix,
                                          options={zstd.DecompressionParameter.window_log_max: WINDOW_LOG_MAX})
                if hashlib.sha256(current).hexdigest() != manifest["to_sha256"]:
                    raise RuntimeError("套用補丁後的程式和官方版本不同")
                for name in zf.namelist():
                    if name.startswith("files/") and not name.endswith("/"):
                        extra[name[len("files/"):]] = zf.read(name)     # 後面版本的同名檔案蓋過前面的
        self._install(current, extra)

    def apply(self, archive):
        """從完整 zip 取出新的 exe 與隨附檔案。"""
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            exe_name = next((n for n in names if n.lower().endswith(f"/{APP_NAME.lower()}.exe")
                             or n.lower() == f"{APP_NAME.lower()}.exe"), None)
            if exe_name is None:
                raise RuntimeError("更新檔裡沒有程式")
            root = exe_name[:-len(Path(exe_name).name)]
            extra = {name[len(root):]: zf.read(name) for name in names
                     if name.startswith(root) and name != exe_name and not name.endswith("/")}
            self._install(zf.read(exe_name), extra)

    def _install(self, exe_data, extra):
        """寫入新的 exe 與隨附檔案:exe 用改名的方式替換,其他檔案直接覆蓋;不安全的路徑略過。"""
        with self._lock:
            if self.cancel.is_set():
                raise RuntimeError("已取消")
            self._installed.clear()         # 從這裡開始不能中斷(關閉程式時會等)
            self.state = "installing"
        folder = self.exe.parent
        new_exe = folder / (self.exe.name + ".new")
        new_exe.write_bytes(exe_data)
        for relative, data in extra.items():
            parts = Path(relative).parts
            if not relative or ".." in parts or Path(relative).is_absolute() or ":" in relative:
                continue
            target = folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            files.write_bytes(target, data)
        old = folder / (self.exe.name + ".old")
        if old.exists():
            old.unlink()
        self.exe.rename(old)                # 正在執行的 exe 可以改名,不能覆蓋
        new_exe.rename(self.exe)
        refresh_icon(self.exe)
