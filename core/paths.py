"""程式用到的資料夾位置。"""

import sys
from pathlib import Path

# 打包成 exe 後,程式碼會解壓到暫存區,但設定檔、圖片、輸出要放在 exe 旁邊
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent.parent
    BUNDLE_DIR = APP_DIR

IMAGES_DIR = APP_DIR / "images"
OUTPUT_DIR = APP_DIR / "output"
PLUGINS_DIR = BUNDLE_DIR / "plugins"
DEV_DIR = APP_DIR / "dev"
BIN_DIR = APP_DIR / "bin"
MODELS_DIR = APP_DIR / "models"
