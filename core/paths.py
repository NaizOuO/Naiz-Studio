"""程式用到的資料夾位置。"""

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
OUTPUT_DIR = APP_DIR / "output"
PLUGINS_DIR = BUNDLE_DIR / "plugins"
DEV_DIR = APP_DIR / "dev"
# 擴充模組:別人寫的或另外下載的工具,放進 exe 旁邊的 mods 資料夾就會出現在首頁
MODS_DIR = APP_DIR / "mods"
BIN_DIR = APP_DIR / "bin"
MODELS_DIR = APP_DIR / "models"


def __getattr__(name):
    # 存使用者資料的位置跟著 APP_DIR 算(測試時會把 APP_DIR 換到測試資料夾,才不會寫到使用者的設定)
    if name == "SETTING_DIR":
        return APP_DIR / SETTING_NAME
    if name == "SIGNATURES_DIR":
        return APP_DIR / SETTING_NAME / "signatures"
    raise AttributeError(name)


# v1.18.0 以前放在程式資料夾裡的東西,搬進 setting 資料夾
LEGACY_FOLDERS = ("images", "fonts", "signatures")
UI_ASSET = ("app_icon.", "ui_")


def migrate_legacy():
    """舊版的 config.json、images、fonts、signatures 搬進 setting;已經有同名檔案的不覆蓋。
    搬不動(例如檔案正被開著)就留在原處,下次啟動再搬。"""
    setting = APP_DIR / SETTING_NAME
    for path in [APP_DIR / "config.json", *APP_DIR.glob("config.bak*.json")]:
        target = setting / path.name
        # 兩邊都有時留比較新的(例如更新後舊版還開著,關掉時又把設定寫回舊位置)
        if path.is_file() and (not target.exists() or path.stat().st_mtime > target.stat().st_mtime):
            _move(path, target)
    for name in LEGACY_FOLDERS:
        old = APP_DIR / name
        if old.is_dir():
            _merge(old, setting / name, drop_assets=(name == "images"))


def _move(source, target):
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
    except OSError:
        pass


def _merge(old, new, drop_assets=False):
    """old 資料夾的內容搬進 new;new 已經有的檔案保留新的。
    drop_assets:舊的介面圖片(更新時已經放進新位置了)直接刪掉。"""
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
            elif drop_assets and path.parent == old and path.name.lower().startswith(UI_ASSET):
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
