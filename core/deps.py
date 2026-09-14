"""模組需要的外部元件:判斷是否已安裝、下載安裝,以及不閃主控台地執行外部程式。

實際要下載什麼由各模組自己宣告 Dependency,這裡只提供通用流程。
"""

import hashlib
import platform
import re
import shutil
import subprocess
import tarfile
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


class DiskSpaceError(Exception):
    pass


DISK_FULL = 28      # errno ENOSPC;Windows 的「磁碟已滿」也會對應到這個值
SPACE_MARGIN = 100 * 1024 * 1024


def _check_space(dep, folder, total):
    """下載檔和解出來的檔案會同時存在,至少要有兩倍大小再多留一點;不夠就在下載前停下來。"""
    if not total:
        return
    need = total * 2 + SPACE_MARGIN
    free = shutil.disk_usage(folder).free
    if free < need:
        raise DiskSpaceError(f"磁碟空間不足：下載 {dep.name} 約需要 {human_size(need)}，"
                             f"目前只剩 {human_size(free)}，請先清出空間再重試")


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

    files: 安裝後的檔名(相對於 bin/ 或 models/)-> 若下載的是壓縮檔(zip 或 tar.bz2),對應壓縮檔內路徑的結尾
           (例如 "bin/ffmpeg.exe");直接下載單一檔案時值填 None。
           有設定 folder 時,files 只用來判斷是否已安裝。
    folder: 把整個壓縮檔解壓到這個子資料夾(自動去掉共同的最上層資料夾),適合需要一堆 DLL 的程式。
    location: "bin" 放執行檔,"models" 放模型。
    check_args: 安裝後用這些參數執行第一個檔案,回傳碼為 0 才算安裝成功。
    sha256: 固定版本的 SHA-256;有填就不再另外抓驗證檔。
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
    sha256: str = ""
    sha256_url: str = ""
    sha256_name: str = ""
    folder: str = ""
    location: str = "bin"

    @property
    def base_dir(self):
        return paths.MODELS_DIR if self.location == "models" else paths.BIN_DIR

    def path(self, filename=None):
        return self.base_dir / (filename or next(iter(self.files)))

    def installed(self) -> bool:
        return all((self.base_dir / name).is_file() and (self.base_dir / name).stat().st_size > 0
                   for name in self.files)


FFMPEG = Dependency(
    id="ffmpeg",
    name="FFmpeg",
    purpose="處理影片與音訊，例如合併影像和聲音、轉換格式",
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
    raise ChecksumError(f"找不到 {dep.name} 的官方驗證碼，為了安全已停止安裝")


def _open_archive(archive_path):
    """回傳 (壓縮檔物件, [(檔內路徑, 開啟函式)]),支援 zip 與 tar.bz2 / tar.gz。"""
    if zipfile.is_zipfile(archive_path):
        archive = zipfile.ZipFile(archive_path)
        entries = [(info.filename.replace("\\", "/"), lambda info=info: archive.open(info))
                   for info in archive.infolist() if not info.is_dir()]
    else:
        archive = tarfile.open(archive_path)
        entries = [(member.name, lambda member=member: archive.extractfile(member))
                   for member in archive.getmembers() if member.isfile()]
    return archive, entries


def _extract_folder(dep: Dependency, archive_path):
    destination = dep.base_dir / dep.folder
    staging = dep.base_dir / f".{dep.folder}.part"
    shutil.rmtree(staging, ignore_errors=True)
    archive, entries = _open_archive(archive_path)
    with archive:
        names = [name for name, _ in entries]
        tops = {name.split("/", 1)[0] for name in names}
        strip = len(next(iter(tops))) + 1 if len(tops) == 1 and all("/" in n for n in names) else 0
        root = staging.resolve()
        for name, opener in entries:
            target = (staging / name[strip:]).resolve()
            # 防止壓縮檔裡用 ../ 把檔案寫到資料夾外面
            if root not in target.parents:
                raise ValueError(f"下載的 {dep.name} 內容異常，已停止安裝")
            target.parent.mkdir(parents=True, exist_ok=True)
            with opener() as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    shutil.rmtree(destination, ignore_errors=True)
    staging.replace(destination)


def _extract_files(dep: Dependency, download):
    base = dep.base_dir
    archive, entries = (None, [])
    if any(suffix is not None for suffix in dep.files.values()):
        archive, entries = _open_archive(download)
    try:
        for target, member_suffix in dep.files.items():
            destination = base / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_name(destination.name + ".part")
            if member_suffix is None:
                shutil.copyfile(download, partial)
            else:
                opener = next((open_ for name, open_ in entries if name.endswith(member_suffix)), None)
                if opener is None:
                    raise ValueError(f"下載的 {dep.name} 裡找不到 {member_suffix}")
                with opener() as src, open(partial, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            partial.replace(destination)
    finally:
        if archive is not None:
            archive.close()


def install(dep: Dependency, progress=None, cancel=None):
    base = dep.base_dir
    base.mkdir(parents=True, exist_ok=True)
    # 先拿到官方驗證碼才開始下載;拿不到就不下載,避免裝上無法確認來源的程式
    expected = dep.sha256.lower() or (fetch_expected_sha256(dep) if dep.sha256_url else None)
    download = base / f".{dep.id}.download"
    try:
        request = urllib.request.Request(dep.url, headers={"User-Agent": "NaizStudio"})
        digest = hashlib.sha256()
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            _check_space(dep, base, total)
            with open(download, "wb") as fp:
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
            raise ChecksumError(f"下載的 {dep.name} 驗證失敗(SHA-256 不符)，已刪除，請重試")

        if dep.folder:
            _extract_folder(dep, download)
        else:
            _extract_files(dep, download)
    except BaseException as exc:
        if dep.folder:
            shutil.rmtree(base / f".{dep.folder}.part", ignore_errors=True)
        for target in dep.files:
            (base / target).with_name((base / target).name + ".part").unlink(missing_ok=True)
        if isinstance(exc, OSError) and exc.errno == DISK_FULL:
            # 伺服器沒給檔案大小、或解壓縮時才滿的情況
            raise DiskSpaceError("磁碟空間不足，已停止下載並刪除未完成的檔案，請先清出空間再重試") from exc
        raise
    finally:
        download.unlink(missing_ok=True)

    if dep.check_args:
        result = run([dep.path(), *dep.check_args], capture_output=True, timeout=120)
        if result.returncode != 0:
            if dep.folder:
                shutil.rmtree(base / dep.folder, ignore_errors=True)
            for target in dep.files:
                (base / target).unlink(missing_ok=True)
            raise RuntimeError(f"{dep.name} 下載後無法執行，已移除，請重試")
