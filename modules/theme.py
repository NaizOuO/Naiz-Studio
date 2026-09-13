"""配色、字型與背景圖載入。"""

import json
import os
import platform

import pygame

BG_DEEP = (22, 25, 32)
PANEL = (33, 38, 48)
PANEL_LIGHT = (44, 50, 62)
PANEL_EDGE = (58, 66, 82)

ACCENT = (78, 201, 176)
ACCENT_DIM = (52, 138, 121)
ACCENT_SOFT = (78, 201, 176, 40)
WARN = (232, 165, 71)
DANGER = (224, 108, 117)

TEXT = (232, 234, 240)
TEXT_DIM = (150, 158, 173)
TEXT_FAINT = (104, 112, 128)

TAB_COLORS = {
    "compress": (78, 201, 176),
    "split": (97, 175, 239),
    "merge": (198, 148, 233),
}

_FONT_CANDIDATES = {
    "Windows": ["C:\\Windows\\Fonts\\msjh.ttc", "C:\\Windows\\Fonts\\msjhl.ttc"],
    "Darwin": ["/System/Library/Fonts/PingFang.ttc"],
}

_cache = {}


def font(size: int, bold: bool = False) -> pygame.font.Font:
    key = (size, bold)
    if key in _cache:
        return _cache[key]

    path = None
    for candidate in _FONT_CANDIDATES.get(platform.system(), []):
        if os.path.exists(candidate):
            path = candidate
            break

    if path:
        f = pygame.font.Font(path, size)
        f.set_bold(bold)
    else:
        f = pygame.font.SysFont("arial", size, bold=bold)
    _cache[key] = f
    return f


BG_MODES = ("cover", "contain", "stretch", "center", "tile", "manual")

DEFAULT_CONFIG = {
    "說明": "bg_image 放 images/ 資料夾裡的檔名;留空則使用純色背景",
    "bg_image": "",
    "bg_alpha": 90,

    "bg_mode說明": "cover=覆蓋(保持比例,裁掉超出部分) / contain=完整顯示(保持比例,可能留白) / "
                   "stretch=延展(拉滿視窗,會變形) / center=原始大小置中 / "
                   "tile=並排重複 / manual=自由調整",
    "bg_mode": "cover",

    "bg_center說明": "center 模式專用:x/y 為 0-100,50 表示置中,0 靠左(上),100 靠右(下)",
    "bg_center": {"x": 50, "y": 50},

    "bg_manual說明": "manual 模式專用:x/y 同上,scale 為縮放百分比(100 = 原始大小)",
    "bg_manual": {"x": 50, "y": 50, "scale": 100},
}


def load_config(base_dir: str) -> dict:
    path = os.path.join(base_dir, "config.json")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(DEFAULT_CONFIG, fp, indent=4, ensure_ascii=False)
        return dict(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as fp:
            user = json.load(fp)
    except Exception:
        return dict(DEFAULT_CONFIG)

    config = {**DEFAULT_CONFIG, **user}
    # 補上使用者 config 缺的子欄位,避免舊設定檔少了鍵就壞掉
    for key in ("bg_center", "bg_manual"):
        merged = dict(DEFAULT_CONFIG[key])
        if isinstance(user.get(key), dict):
            merged.update(user[key])
        config[key] = merged
    if config.get("bg_mode") not in BG_MODES:
        config["bg_mode"] = "cover"
    return config


def save_config(base_dir: str, config: dict) -> bool:
    try:
        with open(os.path.join(base_dir, "config.json"), "w", encoding="utf-8") as fp:
            json.dump(config, fp, indent=4, ensure_ascii=False)
        return True
    except Exception:
        return False


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
# 介面自己的素材(圖示等)不該出現在背景圖清單裡
UI_ASSET_PREFIXES = ("app_icon", "ui_")


def list_images(base_dir: str) -> list:
    folder = os.path.join(base_dir, "images")
    if not os.path.isdir(folder):
        return []
    return sorted(name for name in os.listdir(folder)
                  if name.lower().endswith(IMAGE_EXTS)
                  and not name.lower().startswith(UI_ASSET_PREFIXES))


def load_background(base_dir: str, config: dict):
    """讀 images/ 裡指定的背景圖;沒設定或載入失敗就回傳 None,改用純色底。"""
    name = config.get("bg_image", "")
    if not name:
        return None
    path = os.path.join(base_dir, "images", name)
    if not os.path.exists(path):
        return None
    try:
        return pygame.image.load(path).convert_alpha()
    except Exception:
        return None


class Background:
    """依 config 的填充方式把背景圖畫到畫面上,結果會依視窗大小快取。"""

    def __init__(self, image, config):
        self.image = image
        self.config = config
        self._key = None
        self._surface = None

    def _build(self, size):
        screen_w, screen_h = size
        img_w, img_h = self.image.get_size()
        mode = self.config.get("bg_mode", "cover")
        layer = pygame.Surface(size, pygame.SRCALPHA)

        if mode == "tile":
            for y in range(0, screen_h, img_h):
                for x in range(0, screen_w, img_w):
                    layer.blit(self.image, (x, y))
        else:
            if mode == "stretch":
                scaled = pygame.transform.smoothscale(self.image, size)
                layer.blit(scaled, (0, 0))
            else:
                if mode == "cover":
                    scale = max(screen_w / img_w, screen_h / img_h)
                elif mode == "contain":
                    scale = min(screen_w / img_w, screen_h / img_h)
                elif mode == "manual":
                    scale = max(1, int(self.config["bg_manual"].get("scale", 100))) / 100
                else:
                    scale = 1.0

                new_size = (max(1, int(img_w * scale)), max(1, int(img_h * scale)))
                scaled = pygame.transform.smoothscale(self.image, new_size)

                if mode in ("center", "manual"):
                    spot = self.config["bg_center" if mode == "center" else "bg_manual"]
                    ratio_x = max(0, min(100, int(spot.get("x", 50)))) / 100
                    ratio_y = max(0, min(100, int(spot.get("y", 50)))) / 100
                else:
                    ratio_x = ratio_y = 0.5

                layer.blit(scaled, (int((screen_w - new_size[0]) * ratio_x),
                                    int((screen_h - new_size[1]) * ratio_y)))

        layer.set_alpha(max(0, min(255, int(self.config.get("bg_alpha", 90)))))
        return layer

    def draw(self, surface):
        size = surface.get_size()
        key = (size, self.config.get("bg_mode"), self.config.get("bg_alpha"),
               tuple(self.config["bg_center"].items()), tuple(self.config["bg_manual"].items()))
        if key != self._key:
            self._surface = self._build(size)
            self._key = key
        surface.blit(self._surface, (0, 0))
