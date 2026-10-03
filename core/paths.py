"""程式用到的資料夾位置。"""

import json
import os
import shutil
import sys
from pathlib import Path

# 打包成 exe 後,程式碼會解壓到暫存區,但設定檔、圖片、輸出要放在 exe 旁邊
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent.parent
    BUNDLE_DIR = APP_DIR

SETTING_NAME = "setting"
# 使用者的設定都集中在 setting 資料夾:config.json、簽名、字型、圖片,和各功能的設定檔(專有名詞、修正錯字…)
IMAGES_DIR = APP_DIR / SETTING_NAME / "images"
FONTS_DIR = APP_DIR / SETTING_NAME / "fonts"
# 輸出位置:預設是程式旁邊的 output;設定裡打開「輸出到文件」時換成 documents_output()(由主程式切換)
DEFAULT_OUTPUT_DIR = APP_DIR / "output"
OUTPUT_DIR = DEFAULT_OUTPUT_DIR
PLUGINS_DIR = BUNDLE_DIR / "plugins"
DEV_DIR = APP_DIR / "dev"
# 擴充模組:別人寫的或另外下載的工具,放進 exe 旁邊的 mods 資料夾就會出現在首頁
MODS_DIR = APP_DIR / "mods"
BIN_DIR = APP_DIR / "bin"
MODELS_DIR = APP_DIR / "models"


def documents_output():
    """「文件」資料夾裡的 Naiz Studio(文件被 OneDrive 接管時也找得到);找不到回傳 None。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x05, None, 0, buffer) != 0:     # CSIDL_PERSONAL
            return None
        return Path(buffer.value) / "Naiz Studio"
    except (OSError, AttributeError):
        return None


def __getattr__(name):
    # 存使用者資料的位置跟著 APP_DIR 算(測試時會把 APP_DIR 換到測試資料夾,才不會寫到使用者的設定)
    if name == "SETTING_DIR":
        return APP_DIR / SETTING_NAME
    if name == "SIGNATURES_DIR":
        return APP_DIR / SETTING_NAME / "signatures"
    raise AttributeError(name)


# v1.18.0 以前放在程式資料夾裡的東西,搬進 setting 資料夾。
# 只搬確定是 Naiz Studio 留下的(exe 可能被放在桌面、下載這類資料夾,旁邊剛好有使用者自己的 images、fonts…):
# config.json 要有 Naiz Studio 的設定鍵;images 裡要有程式附的圖示;fonts 只搬程式建立的那幾項;signatures 只搬「簽名_*.png」
SHIPPED_IMAGES = ("app_icon.png", "app_icon.ico", "ui_autoscroll.png", "ui_gear.png", "ui_link_on.png", "ui_link_off.png")
LEGACY_FONTS = ("downloads", "custom", "pdf", "recent.json", "system_fonts.json")
CONFIG_KEYS = ("bg_mode", "bg_image")


def migrate_legacy():
    """舊版的設定、圖片、字型、簽名搬進 setting。不刪也不覆蓋使用者的檔案:
    同名時新的留在 setting、舊的留在原處;搬不動(例如檔案正被開著)也留在原處,下次啟動再搬。"""
    setting = APP_DIR / SETTING_NAME
    config = APP_DIR / "config.json"
    ours = _is_our_config(config)
    if ours:
        target = setting / "config.json"
        # 兩邊都有時用比較新的(例如更新後舊版還開著,關掉時又把設定寫回舊位置);被換掉的那份另存備份
        if not target.exists():
            _move(config, target)
        elif config.stat().st_mtime > target.stat().st_mtime:
            _move(target, _free(setting, "config.bak", ".json"))
            _move(config, target)
    images = APP_DIR / "images"
    shipped = images.is_dir() and any((images / name).is_file() for name in SHIPPED_IMAGES)
    if ours or shipped:
        for bak in APP_DIR.glob("config.bak*.json"):        # 設定壞掉時程式留的備份
            _move(bak, _free(setting, bak.stem, ".json"))
    if shipped:
        _merge(images, setting / "images", drop=SHIPPED_IMAGES)
    fonts = APP_DIR / "fonts"
    for name in LEGACY_FONTS if fonts.is_dir() else ():
        source, target = fonts / name, setting / "fonts" / name
        if source.is_dir():
            _merge(source, target)
        elif source.is_file() and not target.exists():
            _move(source, target)
    _remove_empty(fonts)
    signatures = APP_DIR / "signatures"
    for path in signatures.glob("簽名_*.png") if signatures.is_dir() else ():
        target = setting / "signatures" / path.name
        if not target.exists():
            _move(path, target)
    _remove_empty(signatures)


def _is_our_config(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and any(key in data for key in CONFIG_KEYS)


def _free(folder, stem, suffix):
    """folder 裡沒被用過的檔名:stem.json、stem (2).json…"""
    path, number = folder / f"{stem}{suffix}", 2
    while path.exists():
        path, number = folder / f"{stem} ({number}){suffix}", number + 1
    return path


def _move(source, target):
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
    except OSError:
        pass


def _merge(old, new, drop=()):
    """old 資料夾的內容搬進 new;new 已經有同名檔案時兩邊都保留(舊的留在原處)。
    drop:程式附的檔案,新位置已經有新版的,舊的直接刪掉(只限這幾個檔名)。"""
    if not new.exists():
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))
            return
        except OSError:
            pass
    for path in sorted(old.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        target = new / path.relative_to(old)
        if path.is_file():
            if not target.exists():
                _move(path, target)
            elif path.parent == old and path.name in drop:
                try:
                    path.unlink()
                except OSError:
                    pass
        elif path.is_dir():
            _remove_empty(path)
    _remove_empty(old)


def _remove_empty(folder):
    try:
        folder.rmdir()          # 只有空資料夾刪得掉;還有搬不動的檔案就留著
    except OSError:
        pass
