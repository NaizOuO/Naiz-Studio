"""檢查更新:啟動時在背景問 GitHub 最新的 Release,有新版本就通知;使用者同意後下載,關閉程式時換成新版。

只讀取公開的 Release 資訊(不送出任何資料)。擴充模組的 Release 不會標成「最新」,不會被當成主程式的新版。
v1.19.0 起程式是資料夾版(exe + _internal):執行中 _internal 的檔案在使用中不能換,
所以新版先完整放在 update/new,關閉程式後由新版的 exe 把舊的換掉(finish),舊的改名成 .old 在下次開啟時清掉。
(v1.18.6 以前的單一 exe 版會下載完整 zip,把 exe 和 _internal 一起放好,不用另外處理。)

補丁:每個 Release 除了完整 zip,還附一個「從上一版升級」的補丁(打包時由 build.py 產生)。
補丁裡是有改過的檔案:大檔案放新舊差異(zstd 格式,用舊檔當參考資料解開就是新檔),小檔案直接放新的。
落後好幾版時依序套用每一版的補丁;接不起來、太多版、加起來不划算,或檔案和官方版本對不上時,改下載完整 zip。
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

from . import paths, version

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


# ------------------------------------------------------------ 下載並準備新版

CONTENTS = "_internal"          # 資料夾版的程式內容(PyInstaller 的 contents directory)
SKIP_PARTS = {"__pycache__"}    # 執行時自己產生的快取,不算程式的檔案
FINISH_FLAG = "--finish-update"
MARKER = "finishing.pid"        # 換成新版的程式正在做事:這時開啟的程式不要清掉 update 資料夾


def can_self_update():
    """只有 exe 版能自己換;從原始碼執行時改成打開下載頁面。"""
    return bool(getattr(sys, "frozen", False))


def _process_alive(pid):
    if sys.platform != "win32" or not pid:
        return False
    import ctypes

    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x1000, False, int(pid))             # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259     # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


def _wait_exit(pid, timeout):
    deadline = time.monotonic() + timeout
    while _process_alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True


def _finishing(folder):
    try:
        return _process_alive(int((Path(folder) / "update" / MARKER).read_text()))
    except (OSError, ValueError):
        return False


def cleanup(folder=None):
    """上次更新留下的舊程式與暫存(新版啟動後舊的就沒在用了);換新版的程式還在做事時先不動。"""
    folder = Path(folder or paths.APP_DIR)
    if _finishing(folder):
        return
    for pattern in ("*.exe.old", "*.exe.new"):
        for old in folder.glob(pattern):
            try:
                old.unlink()
            except OSError:
                pass
    for name in (CONTENTS + ".old", CONTENTS + ".new", "update"):
        shutil.rmtree(folder / name, ignore_errors=True)


def refresh_icon(path):
    """通知檔案總管這個檔案換過了:不然總管會一直顯示快取裡的舊圖示(內容視窗裡卻是新的)。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SHChangeNotify(0x00002000, 0x0005, str(path), None)     # SHCNE_UPDATEITEM, SHCNF_PATHW
    except Exception:
        pass


def _safe(relative):
    parts = Path(relative).parts
    return bool(relative) and ".." not in parts and not Path(relative).is_absolute() and ":" not in relative


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _launch(args, cwd):
    import os
    import subprocess

    # 打包後的 exe 開另一個 exe 時要重設 PyInstaller 的環境變數,不然新程式會以為自己是舊程式開出來的子程式
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen([str(a) for a in args], cwd=str(cwd), close_fds=True, env=env)


class Updater:
    """背景下載更新:state 是 downloading、installing(在 update 資料夾準備新版)、ready(準備好了,關閉程式時換成新版)、
    cancelled(使用者取消)或 failed;progress 0～1。
    執行中 _internal 裡的檔案在使用中不能換:新版先完整放在 update/new,關閉程式後由新版的 exe 把舊的換掉(finish)。
    準備好之前隨時可以取消或關閉程式,留下的暫存下次開啟時清掉。"""

    def __init__(self, info, folder=None):
        self.info = info
        self.folder = Path(folder or paths.APP_DIR)
        self.work = self.folder / "update"
        self.staged = self.work / "new"
        self.state = "downloading"
        self.progress = 0.0
        self.remaining = None           # 預估還要幾秒;剛開始下載還算不準時是 None
        self.used_patch = False         # 這次是用補丁更新的
        self.patch_error = ""           # 補丁套用失敗、改下載完整版的原因
        self.message = ""
        self.cancel = threading.Event()
        self._lock = threading.Lock()       # 取消和「準備好了」不會同時發生

    def stop(self):
        """取消:馬上算取消(網路很慢時,下載的那條線可能要等一下才收到,收到後自己清掉暫存)。準備好以後就不能取消。"""
        with self._lock:
            if self.state in ("downloading", "installing"):
                self.cancel.set()
                self.state = "cancelled"

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        try:
            self._download_and_stage()
            with self._lock:
                self._check_cancel()
                self.state = "ready"
        except Exception as error:
            if self.cancel.is_set():
                self.state = "cancelled"
            else:
                self.message = str(error) or type(error).__name__
                self.state = "failed"
            shutil.rmtree(self.work, ignore_errors=True)

    def _check_cancel(self):
        if self.cancel.is_set():
            raise RuntimeError("已取消")

    def _set_state(self, state):
        with self._lock:
            self._check_cancel()
            self.state = state

    def _download_and_stage(self):
        self.work.mkdir(parents=True, exist_ok=True)
        patches = self.info.get("patches")
        if patches:
            try:
                self.apply_patches([self._download(step, self.work / f"patch{i}.patch", patches)
                                    for i, step in enumerate(patches)])
                self.used_patch = True
                return
            except Exception as error:
                self._check_cancel()
                self.patch_error = str(error) or type(error).__name__     # 改下載完整版
                self.progress, self.remaining = 0.0, None
                self._set_state("downloading")
        if not self.info.get("url"):
            raise RuntimeError("這個版本沒有可以下載的 zip")
        full = dict(url=self.info["url"], size=self.info.get("size", 0), sha256=self.info.get("sha256", ""))
        self.apply(self._download(full, self.work / "update.zip", [full]))

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
                self._check_cancel()
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

    def apply(self, archive):
        """完整 zip 裡發布資料夾的檔案解壓到 update/new(不安全的路徑略過)。"""
        self._set_state("installing")
        shutil.rmtree(self.staged, ignore_errors=True)
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            exe_name = next((n for n in names if n.lower().endswith(f"/{APP_NAME.lower()}.exe")
                             or n.lower() == f"{APP_NAME.lower()}.exe"), None)
            if exe_name is None:
                raise RuntimeError("更新檔裡沒有程式")
            root = exe_name[:-len(Path(exe_name).name)]
            for name in names:
                relative = name[len(root):]
                if not name.startswith(root) or name.endswith("/") or not _safe(relative):
                    continue
                self._check_cancel()
                target = self.staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as source, open(target, "wb") as out:
                    shutil.copyfileobj(source, out, 1 << 20)
        if not (self.staged / CONTENTS).is_dir():
            raise RuntimeError("更新檔裡沒有程式內容(_internal)")

    def apply_patches(self, patch_files):
        """依序套用補丁:先把目前的程式複製到 update/new,每一步改有變的檔案(差異或整個檔案)、刪掉不要的,
        最後確認每個檔案都和官方新版一模一樣。目前的程式被改過或缺檔案時對不上,改下載完整版。"""
        from compression import zstd

        self._set_state("installing")
        shutil.rmtree(self.staged, ignore_errors=True)
        manifests = []
        for patch in patch_files:
            with zipfile.ZipFile(patch) as zf:
                manifests.append(json.loads(zf.read("manifest.json")))
        if any(m.get("format") != 2 for m in manifests):
            raise RuntimeError("補丁格式不對")
        for relative in manifests[0]["old"]:
            source = self.folder / relative
            if _safe(relative) and source.is_file():
                self._check_cancel()
                target = self.staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        for patch, manifest in zip(patch_files, manifests):
            with zipfile.ZipFile(patch) as zf:
                for relative in manifest["diff"]:
                    self._check_cancel()
                    target = self.staged / relative
                    if not target.is_file() or file_sha256(target) != manifest["old"][relative]:
                        raise RuntimeError(f"目前的程式和補丁對不上({relative})")
                    data = zstd.decompress(zf.read(f"diff/{relative}.zst"),
                                           zstd_dict=zstd.ZstdDict(target.read_bytes(), is_raw=True).as_prefix,
                                           options={zstd.DecompressionParameter.window_log_max: WINDOW_LOG_MAX})
                    target.write_bytes(data)
                for relative in manifest["files"]:
                    self._check_cancel()
                    if not _safe(relative):
                        continue
                    target = self.staged / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(f"files/{relative}"))
            for relative in set(manifest["old"]) - set(manifest["new"]):
                if _safe(relative):
                    (self.staged / relative).unlink(missing_ok=True)
        for relative, digest in manifests[-1]["new"].items():
            self._check_cancel()
            target = self.staged / relative
            if not target.is_file() or file_sha256(target) != digest:
                raise RuntimeError(f"套用補丁後和官方版本不同({relative})")

    def finish(self, relaunch):
        """關閉程式時呼叫:開 update/new 裡的新版 exe,等這個程式結束後把舊的換掉(relaunch 時換好再打開)。
        開不起來時回傳 False(程式維持舊版,下次開啟可以再更新)。"""
        if self.state != "ready":
            return False
        import os

        try:
            _launch([self.staged / f"{APP_NAME}.exe", FINISH_FLAG, self.folder, os.getpid(), int(bool(relaunch))],
                    self.staged)
            return True
        except OSError:
            return False


# ------------------------------------------------------------ 換成新版(在 update/new 的新版 exe 裡執行)

SWAP_WAIT = 30          # 舊程式關掉後,檔案還被佔用(例如字幕視窗還沒關)時最多再等這麼久


def _retry(action, timeout):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return action()
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.3)


def finish(staged, target, pid, relaunch, wait=SWAP_WAIT, launch=_launch, alert=None):
    """把 target(安裝的資料夾)換成 staged(update/new)裡的新版:
    1. 舊程式還在關的時候,先複製成 _internal.new、exe.new(最花時間的一步,舊版完全沒動到)
    2. 等舊程式結束,用改名換上:_internal → _internal.old、_internal.new → _internal,exe 也一樣;換到一半失敗就改回去
    3. 其他隨附檔案(圖示、說明、授權)直接覆蓋,清掉 .old;relaunch 時打開新版
    失敗時程式維持舊版,用 alert 告知。回傳 True/False。"""
    import os

    staged, target = Path(staged), Path(target)
    marker = target / "update" / MARKER
    exe = target / f"{APP_NAME}.exe"
    contents = target / CONTENTS
    new_contents, old_contents = target / (CONTENTS + ".new"), target / (CONTENTS + ".old")
    new_exe, old_exe = target / (exe.name + ".new"), target / (exe.name + ".old")
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(os.getpid()))
    except OSError:
        pass
    ok, reason = False, ""
    try:
        shutil.rmtree(new_contents, ignore_errors=True)
        shutil.copytree(staged / CONTENTS, new_contents, ignore=shutil.ignore_patterns(*SKIP_PARTS))
        shutil.copyfile(staged / exe.name, new_exe)
        if not _wait_exit(pid, wait + 30):
            raise RuntimeError("舊的程式一直沒有關閉")
        shutil.rmtree(old_contents, ignore_errors=True)
        if old_contents.exists():
            raise RuntimeError(f"{old_contents.name} 刪不掉")
        if contents.exists():
            _retry(lambda: contents.rename(old_contents), wait)
        try:
            new_contents.rename(contents)
            if exe.exists():
                old_exe.unlink(missing_ok=True)
                _retry(lambda: exe.rename(old_exe), wait)
            try:
                new_exe.rename(exe)
            except OSError:
                if old_exe.exists() and not exe.exists():
                    old_exe.rename(exe)
                raise
        except OSError:
            if old_contents.exists():
                if contents.exists():
                    contents.rename(new_contents)
                old_contents.rename(contents)
            raise
        ok = True
        for path in staged.rglob("*"):         # 圖示、說明、授權:失敗也不影響程式
            relative = path.relative_to(staged)
            if path.is_file() and relative.parts[0] not in (CONTENTS, exe.name, "mods") \
                    and not SKIP_PARTS & set(relative.parts):
                try:
                    (target / relative).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target / relative)
                except OSError:
                    pass
        shutil.rmtree(old_contents, ignore_errors=True)
        try:
            old_exe.unlink(missing_ok=True)
        except OSError:
            pass
        refresh_icon(exe)
    except Exception as error:
        reason = str(error) or type(error).__name__
        shutil.rmtree(new_contents, ignore_errors=True)
        try:
            new_exe.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        pass
    if not ok and alert is not None:
        alert(f"沒辦法換成新版：{reason}\n\n程式還是舊版，可以照常使用，下次開啟時可以再更新。")
    if relaunch and exe.exists():
        try:
            launch([exe], target)
        except OSError:
            pass
    return ok


def finish_main(argv):
    """exe 以「--finish-update 安裝資料夾 舊程式的pid 要不要打開(1/0)」啟動時執行。"""
    def alert(text):
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text, "Naiz Studio 更新", 0x30)
        except Exception:
            pass

    target, pid, relaunch = Path(argv[0]), int(argv[1]), argv[2] == "1"
    return finish(Path(sys.executable).resolve().parent, target, pid, relaunch, alert=alert)
