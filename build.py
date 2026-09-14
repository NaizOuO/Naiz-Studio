"""用 PyInstaller 打包成單一 exe,並整理成可以直接上傳 Release 的資料夾與 zip。

用法:python build.py v1.9.2
產出:dist/Naiz Studio/(exe、images、README.md、LICENSE)與 dist/Naiz Studio v1.9.2.zip
發布資料夾裡自己放的測試檔案(下載的元件、輸出、設定等)不會被刪除,也不會被放進 zip。
"""

import os
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "Naiz Studio"
# 插件是執行時才從資料夾讀入,PyInstaller 看不到它們用了哪些套件,要自己列出來
EXTRA_IMPORTS = ["pypdfium2", "resvg_py", "pikepdf", "opencc", "pillow_heif", "vtracer", "queue"]
# 已經不用的套件:開發環境裡可能還裝著,明確排除才不會被一起打包
EXCLUDE = ["tkinter", "pymupdf", "fitz"]
COLLECT = ["core", "PIL"]


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


def pyinstaller_args(script, name, windowed=True, workdir=None):
    args = [str(script), "--noconfirm", "--onefile", "--name", name,
            "--icon", str(ROOT / "images" / "app_icon.ico"),
            "--paths", str(ROOT),
            # 插件原始檔要放進 exe,程式才能照資料夾載入(開發者插件不在這裡,不會被打包)
            "--add-data", f"{ROOT / 'plugins'};plugins"]
    for module in EXCLUDE:
        args += ["--exclude-module", module]
    if windowed:
        args.append("--windowed")
    for module in plugin_modules() + EXTRA_IMPORTS:
        args += ["--hidden-import", module]
    for package in COLLECT:
        args += ["--collect-submodules", package]
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


def main():
    import PyInstaller.__main__

    version = sys.argv[1] if len(sys.argv) > 1 else ""
    dist = ROOT / "dist"
    release = dist / NAME
    exe = release / f"{NAME}.exe"
    if exe.exists():
        # 先確認 exe 沒有在執行,免得打包好幾分鐘後才因為檔案被佔用而失敗
        try:
            with open(exe, "r+b"):
                pass
        except PermissionError:
            sys.exit(f"{exe} 正在執行，請先關閉再打包")

    PyInstaller.__main__.run(pyinstaller_args(ROOT / "naiz_studio.py", NAME))

    # 發布資料夾裡可能有測試用的檔案,不刪除整個資料夾,只更新打包產生的檔案
    (release / "images").mkdir(parents=True, exist_ok=True)
    os.replace(dist / f"{NAME}.exe", exe)
    for source, relative in release_files():
        shutil.copy2(source, release / relative)

    archive = dist / (f"{NAME} {version}.zip" if version else f"{NAME}.zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        # 只放打包產生的檔案,資料夾裡其他測試檔案不會被放進去
        zf.write(exe, Path(NAME) / exe.name)
        for _, relative in release_files():
            zf.write(release / relative, Path(NAME) / relative)
    print(f"\n完成:{release}\n壓縮檔:{archive}({archive.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
