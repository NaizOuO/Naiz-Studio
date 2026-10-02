"""用 PyInstaller 打包成單一 exe,並整理成可以直接上傳 Release 的資料夾與 zip。

用法:python build.py v1.9.2
產出:dist/_new/Naiz Studio/(exe、images、README.md、LICENSE)與 dist/Naiz Studio v1.9.2.zip
dist/Naiz Studio/ 不會被動到:那是自己用的一份,用程式內的更新功能換成新版(順便測試更新)。
另外會用 dist 裡上一版的 zip 做出「從上一版升級」的補丁(需要 zstd 指令),和 zip 一起上傳到 Release,
已經安裝的人更新時只要下載補丁。

只重做補丁:python build.py patch v1.15.1(用 dist 裡這一版與上一版的 zip)
打包擴充模組:python build.py mod circuit → dist/circuit-v0.1.0.zip(版本照模組 __init__.py 的 version)

換圖示:python build.py icon 畫好的圖.png(建議 1024×1024、透明背景)
產出 images/app_icon.png(視窗圖示,256)與 images/app_icon.ico(16～256 各種尺寸)
"""

import os
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "Naiz Studio"
# 插件是執行時才從資料夾讀入,PyInstaller 看不到它們用了哪些套件,要自己列出來
EXTRA_IMPORTS = ["pypdfium2", "resvg_py", "pikepdf", "opencc", "pillow_heif", "vtracer", "queue", "compression.zstd",
                 "comtypes"]
# 主程式沒用到、但擴充模組可能會用的內建模組;不列出來的話 exe 裡沒有,模組 import 會失敗
STDLIB_FOR_MODS = ["sqlite3", "configparser", "tomllib", "shelve", "dbm", "wave", "sched", "csv",
                   "http.server", "xml.dom.minidom", "statistics", "fractions", "difflib", "calendar"]
# 已經不用的套件,以及 fontTools 的繪圖、比對工具才需要的重量級相依(我們只用子集與字重固定,用不到)
EXCLUDE = ["tkinter", "pymupdf", "fitz", "scipy", "matplotlib", "mpl_toolkits", "reportlab", "sympy"]
# fontTools 讀字型表格時是依名稱動態匯入模組,要整包收進去
COLLECT = ["core", "PIL", "fontTools", "comtypes"]     # comtypes:即時字幕擷取聲音
# python-docx 會讀自己附的範本檔,資料檔要一起收進去
COLLECT_DATA = ["docx"]


def plugin_modules():
    """plugins 底下每個插件的所有模組;列成 hidden import,PyInstaller 才會分析並收進它們用到的套件。"""
    modules = []
    for init in sorted((ROOT / "plugins").glob("*/__init__.py")):
        package = f"plugins.{init.parent.name}"
        modules.append(package)
        modules += [f"{package}.{p.stem}" for p in sorted(init.parent.glob("*.py")) if p.stem != "__init__"]
    return modules


def ui_images():
    folder = ROOT / "images"
    return [folder / "app_icon.png", folder / "app_icon.ico", *sorted(folder.glob("ui_*.png"))]


VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040404b0', [
      StringStruct('CompanyName', '{author}'),
      StringStruct('FileDescription', '{name}'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', '{name}'),
      StringStruct('LegalCopyright', 'Copyright (c) {year} {author}'),
      StringStruct('OriginalFilename', '{name}.exe'),
      StringStruct('ProductName', '{name}'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [0x0404, 1200])])
  ]
)
"""
AUTHOR = "Naiz"


def version_file(version):
    """exe 的版本資訊(檔案內容 → 詳細資料、工作管理員會顯示作者與版本)。
    註:安全性警告裡的「發行者」要數位簽章才會顯示,這裡寫的不會影響那一欄。"""
    import time

    parts = [int(p) for p in version.split(".")[:3]] + [0]
    path = ROOT / "build" / "version_info.txt"
    path.parent.mkdir(exist_ok=True)
    path.write_text(VERSION_INFO.format(numbers=tuple(parts[:4]), name=NAME, version=version, author=AUTHOR,
                                        year=time.strftime("%Y")), encoding="utf-8")
    return path


def pyinstaller_args(script, name, windowed=True, workdir=None, version=None):
    args = [str(script), "--noconfirm", "--onefile", "--name", name,
            "--icon", str(ROOT / "images" / "app_icon.ico"),
            "--paths", str(ROOT),
            # 插件原始檔要放進 exe,程式才能照資料夾載入(開發者插件不在這裡,不會被打包)
            "--add-data", f"{ROOT / 'plugins'};plugins"]
    for module in EXCLUDE:
        args += ["--exclude-module", module]
    if windowed:
        args.append("--windowed")
    if version:
        args += ["--version-file", str(version_file(version))]
    for module in plugin_modules() + EXTRA_IMPORTS + STDLIB_FOR_MODS:
        args += ["--hidden-import", module]
    for package in COLLECT:
        args += ["--collect-submodules", package]
    for package in COLLECT_DATA:
        args += ["--collect-data", package]
    if workdir is not None:
        workdir = Path(workdir)
        args += ["--distpath", str(workdir / "dist"), "--workpath", str(workdir / "build"),
                 "--specpath", str(workdir)]
    return args


def release_files():
    """除了 exe 以外要放進發布資料夾的檔案:[(來源, 資料夾內的路徑)]。"""
    # 圖示和介面圖片是從 exe 旁邊的 images 資料夾讀取的;授權檔也放進去,下載的人才看得到
    files = [(image, Path("images") / image.name) for image in ui_images()]
    files += [(ROOT / name, Path(name)) for name in ("README.md", "LICENSE") if (ROOT / name).is_file()]
    return files


PATCH_MAX_RATIO = 0.5       # 補丁超過完整版的一半就不做,直接下載完整版比較單純


def previous_release(current):
    """dist 裡比 current 舊、版本最新的發布 zip;(版本, 路徑) 或 None。"""
    from core.version import parse

    found = []
    for archive in (ROOT / "dist").glob(f"{NAME} v*.zip"):
        tag = archive.stem[len(NAME) + 1:]
        if parse(tag) != (0,) and parse(tag) < parse(current):
            found.append((parse(tag), tag, archive))
    return max(found)[1:] if found else None


def make_patch(current, archive):
    """做出從上一版升級到 current 的補丁:新舊 exe 的差異,加上有改過的隨附檔案。"""
    import hashlib
    import json
    import subprocess
    import tempfile

    from core.updater import patch_name

    previous = previous_release(current)
    if previous is None:
        print("dist 裡沒有上一版的 zip,不做補丁")
        return None
    old_tag, old_archive = previous
    zstd = shutil.which("zstd")
    if zstd is None:
        print("找不到 zstd 指令,不做補丁(已經安裝的人會下載完整版)")
        return None
    root = f"{NAME}/"
    with zipfile.ZipFile(old_archive) as old_zip, zipfile.ZipFile(archive) as new_zip:
        old_exe = old_zip.read(root + f"{NAME}.exe")
        new_exe = new_zip.read(root + f"{NAME}.exe")
        old_names = set(old_zip.namelist())
        changed = [n for n in new_zip.namelist() if n != root + f"{NAME}.exe" and not n.endswith("/")
                   and (n not in old_names or old_zip.read(n) != new_zip.read(n))]
        extra = {n[len(root):]: new_zip.read(n) for n in changed}
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        (temp / "old.exe").write_bytes(old_exe)
        (temp / "new.exe").write_bytes(new_exe)
        subprocess.run([zstd, "-q", "-f", "-19", "--long=27", f"--patch-from={temp / 'old.exe'}",
                        str(temp / "new.exe"), "-o", str(temp / "exe.zst")], check=True)
        diff = (temp / "exe.zst").read_bytes()
    patch = ROOT / "dist" / patch_name(old_tag, current)
    manifest = dict(from_tag=old_tag, to_tag=current, from_sha256=hashlib.sha256(old_exe).hexdigest(),
                    to_sha256=hashlib.sha256(new_exe).hexdigest())
    with zipfile.ZipFile(patch, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=1))
        zf.writestr("exe.zst", diff, compress_type=zipfile.ZIP_STORED)
        for relative, data in extra.items():
            zf.writestr(f"files/{relative}", data)
    size = patch.stat().st_size
    if size > archive.stat().st_size * PATCH_MAX_RATIO:
        patch.unlink()
        print(f"補丁({size / 1024 / 1024:.1f} MB)和完整版差不多大,不做補丁")
        return None
    print(f"補丁:{patch}(從 {old_tag},{size / 1024 / 1024:.2f} MB;隨附檔案 {len(extra)} 個有改)")
    return patch


def main():
    import PyInstaller.__main__

    version = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.path.insert(0, str(ROOT))
    from core.version import VERSION

    if version and version.lstrip("vV") != VERSION:
        sys.exit(f"core/version.py 寫的是 {VERSION}，和要打包的 {version} 不同，請先更新")
    dist = ROOT / "dist"
    release = dist / "_new" / NAME       # 不放 dist/Naiz Studio:那份留給自己用更新功能換新版
    exe = release / f"{NAME}.exe"
    if exe.exists():
        # 先確認 exe 沒有在執行,免得打包好幾分鐘後才因為檔案被佔用而失敗
        try:
            with open(exe, "r+b"):
                pass
        except PermissionError:
            sys.exit(f"{exe} 正在執行，請先關閉再打包")

    PyInstaller.__main__.run(pyinstaller_args(ROOT / "naiz_studio.py", NAME, version=VERSION))

    (release / "images").mkdir(parents=True, exist_ok=True)
    os.replace(dist / f"{NAME}.exe", exe)
    for source, relative in release_files():
        shutil.copy2(source, release / relative)

    archive = dist / (f"{NAME} {version}.zip" if version else f"{NAME}.zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(exe, Path(NAME) / exe.name)
        for _, relative in release_files():
            zf.write(release / relative, Path(NAME) / relative)
        zf.writestr(f"{NAME}/mods/", "")      # 空的擴充模組資料夾,下載的模組解壓縮到這裡
    print(f"\n完成:{release}\n壓縮檔:{archive}({archive.stat().st_size / 1024 / 1024:.1f} MB)")
    if version:
        make_patch("v" + VERSION, archive)


ICON_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def make_icon(source):
    """從一張大圖做出程式圖示:每個尺寸都從原圖縮小(不是從 256 再縮),小尺寸稍微銳利一點比較清楚。"""
    from PIL import Image, ImageFilter

    art = Image.open(source).convert("RGBA")
    side = max(art.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(art, ((side - art.width) // 2, (side - art.height) // 2))
    frames = []
    for size in ICON_SIZES:
        frame = square.resize((size, size), Image.Resampling.LANCZOS)
        if size <= 48:
            frame = frame.filter(ImageFilter.UnsharpMask(radius=0.6, percent=60, threshold=2))
        frames.append(frame)
    images = ROOT / "images"
    frames[-1].save(images / "app_icon.png")
    frames[-1].save(images / "app_icon.ico", sizes=[(s, s) for s in ICON_SIZES], append_images=frames[:-1])
    print(f"已更新 {images / 'app_icon.png'} 與 app_icon.ico({len(ICON_SIZES)} 種尺寸)")


def make_mod(name):
    """把 mods 裡的一個模組壓成 zip(裡面是「模組名稱/…」),別人拖進首頁就能安裝。"""
    sys.path.insert(0, str(ROOT))
    from core import mods

    folder = ROOT / "mods" / name
    info = mods.describe((folder / "__init__.py").read_text(encoding="utf-8"))
    archive = ROOT / "dist" / f"{name}-v{info['version'] or '0.0.0'}.zip"
    archive.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                zf.write(path, Path(name) / path.relative_to(folder))
    print(f"完成:{archive}({archive.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "icon":
        make_icon(sys.argv[2])
    elif len(sys.argv) > 2 and sys.argv[1] == "mod":
        make_mod(sys.argv[2])
    elif len(sys.argv) > 2 and sys.argv[1] == "patch":
        sys.path.insert(0, str(ROOT))
        make_patch("v" + sys.argv[2].lstrip("vV"), ROOT / "dist" / f"{NAME} v{sys.argv[2].lstrip('vV')}.zip")
    else:
        main()
