"""模組需要的外部元件:判斷是否已安裝、下載安裝,以及不閃主控台地執行外部程式。

實際要下載什麼由各模組自己宣告 Dependency,這裡只提供通用流程。
"""

import hashlib
import platform
import re
import shutil
import subprocess
import urllib.request
import zipfile
from dataclasses import dataclass, field

from . import paths

# 用 pythonw 啟動時,呼叫命令列程式預設會跳出黑色主控台視窗
NO_WINDOW = 0x08000000 if platform.system() == "Windows" else 0
_SHA256_PATTERN = re.compile(r"\b[0-9a-fA-F]{64}\b")


class Cancelled(Exception):
    pass


class ChecksumError(Exception):
    pass


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def run(args, **kwargs):
    kwargs.setdefault("creationflags", NO_WINDOW)
    return subprocess.run([str(a) for a in args], **kwargs)


def popen(args, **kwargs):
    kwargs.setdefault("creationflags", NO_WINDOW)
    return subprocess.Popen([str(a) for a in args], **kwargs)


def kill_tree(proc):
    """連同子程式一起結束;打包成單一 exe 的工具會再開子程式,只結束外層時裡面會繼續跑。"""
    if proc.poll() is not None:
        return
    if platform.system() == "Windows":
        run(["taskkill", "/T", "/F", "/PID", proc.pid], capture_output=True)
    else:
        proc.kill()


@dataclass
class Dependency:
    """一個要下載的元件。

    files: 安裝後放在 bin/ 的檔名 -> 若下載的是 zip,對應壓縮檔內路徑的結尾(例如 "bin/ffmpeg.exe");
           直接下載單一執行檔時值填 None。
    check_args: 安裝後用這些參數執行第一個檔案,回傳碼為 0 才算安裝成功。
    sha256_url: 官方公布的 SHA-256 檔案;有填就會在安裝前驗證下載內容。
    sha256_name: 驗證檔若是「雜湊 檔名」清單,用這個檔名找對應的那一行。
    """

    id: str
    name: str
    purpose: str
    size_text: str
    url: str
    files: dict
    check_args: list = field(default_factory=list)
    sha256_url: str = ""
    sha256_name: str = ""

    def path(self, filename=None):
        return paths.BIN_DIR / (filename or next(iter(self.files)))

    def installed(self) -> bool:
        return all((paths.BIN_DIR / name).is_file() and (paths.BIN_DIR / name).stat().st_size > 0
                   for name in self.files)


FFMPEG = Dependency(
    id="ffmpeg",
    name="FFmpeg",
    purpose="處理影片與音訊,例如合併影像和聲音、轉換格式",
    size_text="約 106 MB",
    url="https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    files={"ffmpeg.exe": "bin/ffmpeg.exe", "ffprobe.exe": "bin/ffprobe.exe"},
    check_args=["-version"],
    sha256_url="https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.sha256",
)


def fetch_expected_sha256(dep: Dependency) -> str:
    """從官方驗證檔取出雜湊值。支援「雜湊 檔名」清單、只有一串雜湊、PowerShell Get-FileHash 格式。"""
    request = urllib.request.Request(dep.sha256_url, headers={"User-Agent": "NaizStudio"})
    with urllib.request.urlopen(request, timeout=30) as response:
        text = response.read(1024 * 1024).decode("utf-8", "replace")
    for line in text.splitlines():
        match = _SHA256_PATTERN.search(line)
        if not match:
            continue
        if dep.sha256_name and dep.sha256_name not in (token.lstrip("*") for token in line.split()):
            continue
        return match.group(0).lower()
    raise ChecksumError(f"找不到 {dep.name} 的官方驗證碼,為了安全已停止安裝")


def install(dep: Dependency, progress=None, cancel=None):
    paths.BIN_DIR.mkdir(parents=True, exist_ok=True)
    # 先拿到官方驗證碼才開始下載;拿不到就不下載,避免裝上無法確認來源的程式
    expected = fetch_expected_sha256(dep) if dep.sha256_url else None
    download = paths.BIN_DIR / f".{dep.id}.download"
    try:
        request = urllib.request.Request(dep.url, headers={"User-Agent": "NaizStudio"})
        digest = hashlib.sha256()
        with urllib.request.urlopen(request, timeout=60) as response, open(download, "wb") as fp:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                fp.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)

        if expected is not None and digest.hexdigest() != expected:
            raise ChecksumError(f"下載的 {dep.name} 驗證失敗(SHA-256 不符),已刪除,請重試")

        for target, member_suffix in dep.files.items():
            destination = paths.BIN_DIR / target
            partial = destination.with_name(destination.name + ".part")
            if member_suffix is None:
                shutil.copyfile(download, partial)
            else:
                with zipfile.ZipFile(download) as archive:
                    member = next((n for n in archive.namelist()
                                   if n.replace("\\", "/").endswith(member_suffix)), None)
                    if member is None:
                        raise ValueError(f"下載的 {dep.name} 裡找不到 {member_suffix}")
                    with archive.open(member) as src, open(partial, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            partial.replace(destination)
    except BaseException:
        for target in dep.files:
            (paths.BIN_DIR / target).with_name(target + ".part").unlink(missing_ok=True)
        raise
    finally:
        download.unlink(missing_ok=True)

    if dep.check_args:
        result = run([dep.path(), *dep.check_args], capture_output=True, timeout=60)
        if result.returncode != 0:
            for target in dep.files:
                (paths.BIN_DIR / target).unlink(missing_ok=True)
            raise RuntimeError(f"{dep.name} 下載後無法執行,已移除,請重試")
