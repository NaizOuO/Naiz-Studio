"""圖片編輯視窗:在預覽上拖曳裁切框、旋轉、翻轉、四點校正。細調時畫面分成四格,各自放大裁切框的一個角;
四點校正時拖曳四個角對準文件邊緣,右邊設定區下方有放大鏡;動畫可以播放預覽。"""

import math
import threading

import pygame

from core import paths, theme, widgets
from core.scroll import ScrollView
from core.widgets import (Button, ChoiceGrid, SegmentedControl, Slider, TextInput, Toggle, draw_text,
                          rounded_panel)

from . import ops

VIEW_SIDE = 1200      # 一般預覽的解析度;拖曳旋轉滑桿時要即時重畫,不能太大
FINE_SIDE = 2400      # 細調放大用的解析度
ANIM_SIDE = 800       # 動畫每一格都要留在記憶體裡播放,解析度壓低一點
SIDE_W = 300
GRAB = 10             # 滑鼠離框線多近算是抓到
FINE_ZOOM = 4         # 細調時相對於整張顯示的放大倍數
MAX_ZOOM = 8
PLAY_BAR = 50         # 動畫播放列的高度
SHADE = (8, 10, 14, 150)
RATIOS = [("free", "自由"), ("1:1", "1:1"), ("4:3", "4:3"), ("3:4", "3:4"),
          ("3:2", "3:2"), ("2:3", "2:3"), ("16:9", "16:9"), ("9:16", "9:16")]
SWAPPED = {"4:3": "3:4", "3:4": "4:3", "3:2": "2:3", "2:3": "3:2", "16:9": "9:16", "9:16": "16:9"}
QUADRANTS = [("lt", "左上角"), ("rt", "右上角"), ("lb", "左下角"), ("rb", "右下角")]
FIELDS = [("w", "寬"), ("h", "高")]
FULL = (0.0, 0.0, 1.0, 1.0)
NUDGE = {pygame.K_LEFT: (-1, 0), pygame.K_RIGHT: (1, 0), pygame.K_UP: (0, -1), pygame.K_DOWN: (0, 1)}
FILL_OPTIONS = [("clear", "透明"), ("white", "白色"), ("black", "黑色")]
FILL_PREVIEW = {"white": (255, 255, 255), "black": (0, 0, 0)}
IMAGE_EDGE = (150, 158, 173)   # 擴展畫布時原圖範圍的細框
WARP_GRAB = 16        # 四點校正時滑鼠離角多近算是抓到
LOUPE = 170           # 四點校正放大鏡的邊長(設定區下方空間夠大時放大到 LOUPE_MAX)
LOUPE_MAX = 260
LOUPE_SIDE_MIN = 120   # 設定區留的位置至少有這麼大才放進去,不然放在畫面角落
LOUPE_ZOOM = 4
# 放大鏡的倍數是「比畫面上的圖大幾倍」(大照片在畫面上縮很小,照原圖像素算的話放大鏡只看得到一小片);
# 滾滾輪可以換,最多放到原圖 1 像素 = 螢幕 LOUPE_PIXEL_MAX 像素
LOUPE_ZOOMS = (2, 3, 4, 6, 8, 12, 16, 24, 32)
LOUPE_PIXEL_MAX = 12
WARP_HINT = "四點校正：拖曳四個角對準文件的邊緣；點選的角會在右邊放大，拖曳放大圖裡的圖片或按方向鍵微調；Enter 套用、Esc 取消"

_checker = None
_icons = {}


def _checker_surface(size):
    """透明處顯示的棋盤格;做一張夠大的,需要多大就截多大。"""
    global _checker
    if _checker is None or _checker.get_width() < size[0] or _checker.get_height() < size[1]:
        width, height = max(size[0], 2000), max(size[1], 1400)
        _checker = pygame.Surface((width, height))
        _checker.fill((58, 62, 72))
        for y in range(0, height, 12):
            for x in range((y // 12) % 2 * 12, width, 24):
                _checker.fill((44, 48, 56), (x, y, 12, 12))
    return _checker


def _blit_checker(screen, rect, origin):
    """畫棋盤格;同一個畫面都從 origin 起算,原圖的透明和補上的透明格子才會對齊成同一層。"""
    if rect.width <= 0 or rect.height <= 0 or rect.x < origin[0] or rect.y < origin[1]:
        return
    board = _checker_surface((rect.right - origin[0], rect.bottom - origin[1]))
    screen.blit(board, rect.topleft, pygame.Rect(rect.x - origin[0], rect.y - origin[1], rect.width, rect.height))


def _icon(name, size, color):
    """images/ui_*.png 的白色圖示,染成指定顏色;讀不到時回傳 None。"""
    key = (name, size)
    if key not in _icons:
        try:
            image = pygame.image.load(str(paths.IMAGES_DIR / f"ui_{name}.png")).convert_alpha()
            _icons[key] = pygame.transform.smoothscale(image, (size, size))
        except Exception:
            _icons[key] = None
    if _icons[key] is None:
        return None
    tinted = _icons[key].copy()
    tinted.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MULT)
    return tinted


def _shade(screen, area, box):
    """把 area 裡、box 以外的部分蓋暗,讓裁掉的範圍一眼看得出來。"""
    inner = box.clip(area)
    if not inner.width or not inner.height:
        parts = [area]
    else:
        parts = [pygame.Rect(area.x, area.y, area.width, inner.top - area.y),
                 pygame.Rect(area.x, inner.bottom, area.width, area.bottom - inner.bottom),
                 pygame.Rect(area.x, inner.top, inner.left - area.x, inner.height),
                 pygame.Rect(inner.right, inner.top, area.right - inner.right, inner.height)]
    for part in parts:
        if part.width > 0 and part.height > 0:
            layer = pygame.Surface(part.size, pygame.SRCALPHA)
            layer.fill(SHADE)
            screen.blit(layer, part.topleft)


class ImageEditor:
    def __init__(self, get_screen, accent, embedded=False):
        self.get_screen = get_screen
        self.accent = accent
        self.embedded = embedded   # 嵌在「圖片工具」頁面裡:沒有標題列、取消與完成,Esc 不會關掉
        self.side_view = ScrollView(accent=accent, indicator=True)  # 嵌入時視窗小,設定區放不下就捲動
        self.side_clip = pygame.Rect(0, 0, 0, 0)
        self.is_open = False
        self.item = None
        self.edit = ops.Edit()
        self.original = None
        self.get_items = list
        self.on_change = None
        self.base = None       # 細調用的底圖(PIL,已轉正)
        self.small = None      # 一般預覽用的底圖
        self.frames = []       # 動畫的每一格 (PIL, 顯示毫秒);不是動畫時是空的
        self.frame_index = 0
        self.playing = False
        self._frame_at = 0
        self.full_size = (1, 1)
        self.error = ""
        self.message = ""
        self.fine = False
        self.warping = False     # 四點校正模式:畫面顯示校正前的圖和四個角
        self.warp_points = None  # 校正模式中正在調整的四個角(比例座標)
        self.warp_hover = None
        self.warp_selected = None     # 點選過的角:滑鼠離開後放大鏡還是顯示它,方向鍵也移動它
        self.loupe_zoom = LOUPE_ZOOM
        self._loupe_index = None      # 上一幀放大鏡顯示的是第幾個角
        self._loupe_slot = None       # 設定區留給放大鏡的位置(調整模式校正時,裁切那一區)
        self.loupe_block = None       # 放大鏡佔住的範圍:點在這裡不會點到底下的設定
        self._crop_section_h = 260    # 裁切那一區的高度,校正時同樣高度留給放大鏡(版面才不會跳)
        self.locked = False
        self.lock_ratio = None   # 固定比例時的寬高比(像素)
        self.expand = False      # 擴展畫布:裁切框可以拉到圖片外,多出的部分補空白
        self._layout = FULL      # 預覽畫面要容納的範圍(比例座標);擴展畫布時比圖片大
        self.drag = None
        self._views = {}
        self._scaled = None
        self.loupe_rect = None       # 上一幀放大鏡畫在哪(測試用)
        self._loupe = None           # 這一幀要畫的放大鏡:(畫面範圍, 第幾個角),等設定區畫完再畫
        self.canvas = pygame.Rect(0, 0, 0, 0)
        self.image_rect = pygame.Rect(0, 0, 1, 1)
        self.lock_rect = pygame.Rect(0, 0, 0, 0)
        self.quadrants = []
        self.fine_centers = {}   # 細調時每一格畫面的中心;角快跑出格子時才重新置中
        self._fine_key = None

        self.ratio = ChoiceGrid(RATIOS, columns=4, accent=accent, row_h=32)
        self.fields = {key: TextInput(accent=accent, size=14) for key, _ in FIELDS}
        self.angle_input = TextInput(accent=accent, size=14)
        self.angle = Slider(-180, 180, 0, accent=accent)
        self.expand_toggle = Toggle(False, accent=accent)
        self.fill = SegmentedControl(FILL_OPTIONS, accent=accent)
        self.btn_play = Button("播放", accent=accent, filled=False, size=13)
        self.btn_fine = Button("細調", accent=accent, filled=False, size=13)
        self.btn_clear_crop = Button("清除裁切", filled=False, size=13)
        self.btn_left = Button("左轉", filled=False, size=13)
        self.btn_right = Button("右轉", filled=False, size=13)
        self.btn_flip_h = Button("左右翻", filled=False, size=13)
        self.btn_flip_v = Button("上下翻", filled=False, size=13)
        self.btn_warp = Button("四點校正", accent=accent, filled=False, size=13)
        self.btn_clear_warp = Button("清除校正", filled=False, size=13)
        self.btn_reset = Button("重設", filled=False, size=13)
        self.btn_all = Button("套用到全部", filled=False, size=13)
        self.btn_cancel = Button("取消", filled=False, size=14)
        self.btn_done = Button("完成", accent=accent, size=14)

    # ------------------------------------------------------------ 開關

    def open(self, item, get_items, auto_rotate, on_change):
        """編輯直接改 item.edit;按取消時再換回打開時的樣子。"""
        self.item, self.get_items, self.on_change = item, get_items, on_change
        self.edit = item.edit
        self.original = item.edit.copy()
        self.base = self.small = None
        self.frames, self.frame_index, self.playing = [], 0, False
        self.error = self.message = ""
        self.fine, self.drag = False, None
        self.warping, self.warp_points, self.warp_hover, self.warp_selected = False, None, None, None
        self.locked, self.lock_ratio = False, None
        crop = item.edit.crop
        self.expand = bool(crop) and (crop[0] < 0 or crop[1] < 0 or crop[2] > 1 or crop[3] > 1)
        self._views, self._scaled = {}, None
        self.fine_centers, self._fine_key = {}, None
        self.ratio.index = 0
        self.is_open = True
        threading.Thread(target=self._load, args=(item, auto_rotate), daemon=True).start()

    def _load(self, item, auto_rotate):
        try:
            base, full = ops.load_view(item.path, auto_rotate, FINE_SIDE)
        except Exception as exc:
            if self.item is item:
                self.error = ops.describe_error(exc)
            return
        small = base.copy()
        small.thumbnail((VIEW_SIDE, VIEW_SIDE))
        if self.item is not item:
            return
        self.full_size, self.small, self.base = full, small, base
        if getattr(item, "frames", 1) > 1:
            try:
                frames = ops.load_frames(item.path, ANIM_SIDE)
            except Exception:
                return
            if self.item is item:
                self.frames = frames

    def close(self, keep=True):
        if not keep and self.item is not None:
            self.item.edit = self.original
            self.on_change(self.item)
        for field in (*self.fields.values(), self.angle_input):
            field.blur()
        self.drag = None
        self.angle.dragging = False
        self.is_open = False
        self.playing = False
        self.item = self.base = self.small = None
        self.frames = []
        self._views, self._scaled = {}, None

    def update(self):
        if self.playing and self.frames and not self.fine:
            now = pygame.time.get_ticks()
            if now - self._frame_at >= self.frames[self.frame_index][1]:
                self.frame_index = (self.frame_index + 1) % len(self.frames)
                self._frame_at = now

    # ------------------------------------------------------------ 資料

    def _changed(self):
        self.message = ""
        if self.item is not None:
            self.on_change(self.item)

    def _full_view(self):
        """原圖旋轉、翻轉、校正後(還沒裁切)的像素尺寸;數字欄和裁切都以它為準。"""
        e = self.edit
        return ops.edited_size(self.full_size, ops.Edit(e.angle, e.quarter, e.flip, warp=e.warp))

    def _turned_size(self):
        """旋轉、翻轉後、還沒校正的像素尺寸;四點校正的座標以它為準。"""
        e = self.edit
        return ops.edited_size(self.full_size, ops.Edit(e.angle, e.quarter, e.flip))

    # ------------------------------------------------------------ 四點校正

    def _start_warp(self):
        self.fine, self.drag = False, None
        self.warping = True
        self.warp_points = list(self.edit.warp or ops.FULL_CORNERS)
        self.warp_hover = self.warp_selected = None

    def _finish_warp(self):
        """離開校正模式:四個角都還在圖片的四個角上就等於沒有校正。"""
        points = ops.order_corners(self.warp_points)
        unchanged = all(abs(a - b) < 1e-4 for p, q in zip(points, ops.FULL_CORNERS) for a, b in zip(p, q))
        area = abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]))) / 2
        if not unchanged and area < 0.002:
            self.message = "四個角圍起來的範圍太小，請把四個角拉開一點"
            return
        before = self.edit.warp
        self.edit.warp = None if unchanged else points
        self.warping, self.warp_points, self.warp_hover, self.drag = False, None, None, None
        self.warp_selected = None
        if self.edit.warp != before:
            cleared = bool(self.edit.crop)
            self.edit.crop = None       # 校正後是另一張圖,原本的裁切範圍對不上,清掉重新框
            self._changed()
            if cleared:
                self.message = "已校正；原本的裁切範圍已清除，需要的話再重新框選"

    def _warp_screen(self):
        rect = self.image_rect
        return [(rect.x + x * rect.width, rect.y + y * rect.height) for x, y in self.warp_points]

    def _warp_at(self, pos):
        found = [(abs(pos[0] - x) + abs(pos[1] - y), i) for i, (x, y) in enumerate(self._warp_screen())]
        distance, index = min(found)
        return index if distance <= WARP_GRAB * 1.5 else None

    def _move_warp(self, index, point):
        self.warp_points[index] = (min(max(point[0], 0.0), 1.0), min(max(point[1], 0.0), 1.0))

    def _crop_pixels(self):
        width, height = self._full_view()
        left, top, right, bottom = self.edit.crop or FULL
        x, y = round(left * width), round(top * height)
        return {"x": x, "y": y, "w": round(right * width) - x, "h": round(bottom * height) - y}

    def _set_crop(self, crop):
        # 框剛好等於整張圖才算沒有裁切;擴展畫布的框會超出圖片,不能當成整張圖
        full = all(abs(value - edge) <= 1e-9 for value, edge in zip(crop, FULL))
        self.edit.crop = None if full else tuple(crop)

    def _bounds(self):
        """裁切框可以到的範圍(比例座標);擴展畫布時每邊可以再往外一整張圖。"""
        return (-1.0, 2.0) if self.expand else (0.0, 1.0)

    def _set_expand(self, value):
        self.expand = value
        if value or not self.edit.crop:
            return
        # 關掉擴展畫布:超出圖片的部分收回圖片內
        left, top, right, bottom = self.edit.crop
        left, top, right, bottom = max(0.0, left), max(0.0, top), min(1.0, right), min(1.0, bottom)
        if right - left <= 0 or bottom - top <= 0:
            self.edit.crop = None
        else:
            self._set_crop((left, top, right, bottom))
        self._sync_preset()
        self._changed()

    def _preset_aspect(self, key):
        """比例選項的寬高比(像素);「自由」回傳 None。要用原圖比例時直接按鎖鏈固定即可。"""
        if key == "free":
            return None
        a, b = key.split(":")
        return int(a) / int(b)

    def _locked_ratio(self):
        """固定比例時,換算成比例座標下的寬高比;沒有固定時回傳 None。"""
        if not self.locked or not self.lock_ratio:
            return None
        width, height = self._full_view()
        return self.lock_ratio * height / width

    def _sync_preset(self):
        """裁切框的比例和選的比例已經不一樣(沒固定比例時改了尺寸),選項跳回「自由」。"""
        aspect = self._preset_aspect(self.ratio.value)
        if aspect is None:
            return
        pixels = self._crop_pixels()
        if abs(pixels["w"] - pixels["h"] * aspect) > max(1.5, pixels["w"] * 0.003):
            self.ratio.index = 0

    def _toggle_lock(self):
        self.locked = not self.locked
        if self.locked:
            pixels = self._crop_pixels()
            self.lock_ratio = pixels["w"] / max(1, pixels["h"])

    def _constrain(self, crop, mode):
        """固定比例時調整裁切框;mode 是被拖動的邊或角,對面的邊或角固定不動。"""
        ratio = self._locked_ratio()
        if ratio is None or mode == "move":
            return crop
        lo, hi = self._bounds()
        span = hi - lo
        left, top, right, bottom = crop
        width, height = right - left, bottom - top
        horizontal = "l" in mode or "r" in mode
        vertical = "t" in mode or "b" in mode
        if horizontal and vertical:
            if width > height * ratio:
                width = height * ratio
            else:
                height = width / ratio
            anchor_x = right if "l" in mode else left
            anchor_y = bottom if "t" in mode else top
            room_x = anchor_x - lo if "l" in mode else hi - anchor_x
            room_y = anchor_y - lo if "t" in mode else hi - anchor_y
            if width > room_x:
                width, height = room_x, room_x / ratio
            if height > room_y:
                width, height = room_y * ratio, room_y
            left, right = (anchor_x - width, anchor_x) if "l" in mode else (anchor_x, anchor_x + width)
            top, bottom = (anchor_y - height, anchor_y) if "t" in mode else (anchor_y, anchor_y + height)
        elif horizontal:
            height = width / ratio
            if height > span:
                height, width = span, span * ratio
                left, right = (right - width, right) if "l" in mode else (left, left + width)
            middle = (top + bottom) / 2
            top = min(max(middle - height / 2, lo), hi - height)
            bottom = top + height
        else:
            width = height * ratio
            if width > span:
                width, height = span, span / ratio
                top, bottom = (bottom - height, bottom) if "t" in mode else (top, top + height)
            middle = (left + right) / 2
            left = min(max(middle - width / 2, lo), hi - width)
            right = left + width
        return left, top, right, bottom

    def _apply_ratio(self):
        """選了比例:以目前裁切框的中心,做出這個比例、能放進整張圖的最大框。要不要固定由鎖鏈決定。"""
        aspect = self._preset_aspect(self.ratio.value)
        if aspect is None:
            return
        full_w, full_h = self._full_view()
        ratio = aspect * full_h / full_w
        if self.expand:
            # 擴展畫布:做出剛好包住整張圖的框,原圖置中,不夠的地方補空白,內容不會被切掉
            width, height = (ratio, 1) if ratio >= 1 else (1, 1 / ratio)
            x, y = 0.5 - width / 2, 0.5 - height / 2
        else:
            width, height = (1, 1 / ratio) if ratio >= 1 else (ratio, 1)
            left, top, right, bottom = self.edit.crop or FULL
            x = min(max((left + right) / 2 - width / 2, 0), 1 - width)
            y = min(max((top + bottom) / 2 - height / 2, 0), 1 - height)
        self._set_crop((x, y, x + width, y + height))
        if self.locked:
            self.lock_ratio = aspect
        self._changed()

    def _apply_fields(self, changed):
        try:
            typed = int(self.fields[changed].text)
        except ValueError:
            return
        full_w, full_h = self._full_view()
        current = self._crop_pixels()
        width, height = current["w"], current["h"]
        if changed == "w":
            width = typed
        else:
            height = typed
        aspect = self.lock_ratio if self.locked else None
        if aspect:
            # 固定比例:改寬時高跟著變,改高時寬跟著變
            if changed == "w":
                height = round(width / aspect)
            else:
                width = round(height * aspect)
        lo, hi = self._bounds()
        max_w, max_h = round(full_w * (hi - lo)), round(full_h * (hi - lo))
        width, height = max(1, width), max(1, height)
        if width > max_w:
            width, height = max_w, round(max_w / aspect) if aspect else height
        if height > max_h:
            width, height = (round(max_h * aspect) if aspect else width), max_h
        width, height = min(max(1, width), max_w), min(max(1, height), max_h)
        # 以目前框的左上角為準;會超出可用範圍時整個框往回推
        x = min(max(current["x"], round(full_w * lo)), round(full_w * hi) - width)
        y = min(max(current["y"], round(full_h * lo)), round(full_h * hi) - height)
        self._set_crop((x / full_w, y / full_h, (x + width) / full_w, (y + height) / full_h))
        self._sync_preset()
        self._changed()

    def _apply_angle_text(self):
        try:
            value = float(self.angle_input.text)
        except ValueError:
            return
        self.edit.set_view_angle(value)
        self._changed()

    def _rotate(self, turns):
        self.edit.rotate_right(turns)
        if turns % 2:
            # 裁切框轉了 90 度,比例選項和固定的比例都直橫互換
            if self.ratio.value in SWAPPED:
                keys = [key for key, _ in RATIOS]
                self.ratio.index = keys.index(SWAPPED[self.ratio.value])
            if self.lock_ratio:
                self.lock_ratio = 1 / self.lock_ratio

    def _apply_to_all(self):
        others = [item for item in self.get_items() if item is not self.item and isinstance(item.info, dict)]
        for other in others:
            other.edit = self.edit.copy()
            self.on_change(other)
        self.message = f"已套用到其他 {len(others)} 張圖片，裁切範圍依各張圖的比例換算"

    # ------------------------------------------------------------ 拖曳

    def _point(self, pos):
        """滑鼠位置換成畫面上的比例座標(可以超出 0~1)。"""
        drag = self.drag
        if drag is not None and "quad" in drag:
            quad, (cx, cy), zoom, (vw, vh) = drag["quad"], drag["center"], drag["zoom"], drag["view"]
            return (cx + (pos[0] - quad.centerx) / zoom) / vw, (cy + (pos[1] - quad.centery) / zoom) / vh
        rect = self.image_rect
        return (pos[0] - rect.x) / max(1, rect.width), (pos[1] - rect.y) / max(1, rect.height)

    def _box_screen(self):
        rect = self.image_rect
        left, top, right, bottom = self.edit.crop or FULL
        x1, y1 = round(rect.x + left * rect.width), round(rect.y + top * rect.height)
        x2, y2 = round(rect.x + right * rect.width), round(rect.y + bottom * rect.height)
        return pygame.Rect(x1, y1, max(1, x2 - x1), max(1, y2 - y1))

    def _hit(self, pos):
        box = self._box_screen()
        x, y = pos
        horizontal = "l" if abs(x - box.left) <= GRAB else "r" if abs(x - box.right) <= GRAB else ""
        vertical = "t" if abs(y - box.top) <= GRAB else "b" if abs(y - box.bottom) <= GRAB else ""
        if horizontal and vertical:
            return horizontal + vertical
        if horizontal and box.top - GRAB <= y <= box.bottom + GRAB:
            return horizontal
        if vertical and box.left - GRAB <= x <= box.right + GRAB:
            return vertical
        if box.collidepoint(pos) and self.edit.crop is not None:
            return "move"   # 還沒裁切時框就是整張圖,在圖上拖曳是框出新範圍
        # 擴展畫布時圖片外面也可以框;否則只有圖片上可以
        area = self.canvas if self.expand else self.image_rect.inflate(GRAB * 2, GRAB * 2)
        if area.collidepoint(pos):
            return "new"
        return None

    def _drag_begin(self, pos):
        crop = self.edit.crop or FULL
        if self.fine:
            for corner, quad, center, zoom, view in self.quadrants:
                if quad.collidepoint(pos):
                    self.drag = {"mode": corner, "crop": crop, "quad": quad, "center": center, "zoom": zoom,
                                 "view": view}
                    self.drag["start"] = self._point(pos)
                    return
            return
        mode = self._hit(pos)
        if mode is None:
            return
        self.drag = {"mode": mode, "crop": crop, "original": self.edit.crop, "layout": self._layout}
        x, y = self._point(pos)
        lo, hi = self._bounds()
        self.drag["start"] = (min(max(x, lo), hi), min(max(y, lo), hi)) if mode == "new" else (x, y)

    def _drag_update(self, point):
        drag = self.drag
        start_x, start_y = drag["start"]
        x, y = point
        mode = drag["mode"]
        left, top, right, bottom = drag["crop"]
        full_w, full_h = self._full_view()
        min_w, min_h = min(1.0, 1 / full_w), min(1.0, 1 / full_h)   # 至少留 1 像素
        lo, hi = self._bounds()
        if mode == "new":
            x, y = min(max(x, lo), hi), min(max(y, lo), hi)
            mode = ("l" if x < start_x else "r") + ("t" if y < start_y else "b")
            left, right = sorted((start_x, x))
            top, bottom = sorted((start_y, y))
        elif mode == "move":
            dx = min(max(x - start_x, lo - left), hi - right)
            dy = min(max(y - start_y, lo - top), hi - bottom)
            left, right, top, bottom = left + dx, right + dx, top + dy, bottom + dy
        else:
            dx, dy = x - start_x, y - start_y
            if "l" in mode:
                left = min(max(lo, left + dx), right - min_w)
            if "r" in mode:
                right = max(min(hi, right + dx), left + min_w)
            if "t" in mode:
                top = min(max(lo, top + dy), bottom - min_h)
            if "b" in mode:
                bottom = max(min(hi, bottom + dy), top + min_h)
        self.edit.crop = self._constrain((left, top, right, bottom), mode)
        if mode != "move":
            self._sync_preset()

    def _drag_end(self):
        drag, self.drag = self.drag, None
        crop = self.edit.crop
        if drag["mode"] == "new" and crop is not None:
            too_small = ((crop[2] - crop[0]) * self.image_rect.width < 6
                         or (crop[3] - crop[1]) * self.image_rect.height < 6)
            if too_small:   # 只是點一下,不改變原本的裁切
                self.edit.crop = drag["original"]
        if self.edit.crop is not None:
            self._set_crop(self.edit.crop)
        self._changed()

    def _nudge(self, event, pos):
        """細調時用方向鍵把滑鼠所在那一格的角移動 1 像素。"""
        corner = next((corner for corner, quad, *_ in self.quadrants if quad.collidepoint(pos)), None)
        if corner is None:
            return
        full_w, full_h = self._full_view()
        dx, dy = NUDGE[event.key]
        self.drag = {"mode": corner, "crop": self.edit.crop or FULL, "start": (0.0, 0.0)}
        self._drag_update((dx / full_w, dy / full_h))
        self.drag = None
        self._set_crop(self.edit.crop)
        self._changed()

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, pos):
        if self.handle_loupe(event, pos):       # 放大鏡可能蓋在設定上,要比設定先處理
            return
        if self.embedded:
            if self.side_view.handle_event(event, pos):
                return
            # 嵌入時只理會畫面和設定區看得到的範圍:捲到外面、被底下按鈕蓋住的設定不能被點到
            if event.type == pygame.MOUSEBUTTONDOWN and not self.canvas.collidepoint(pos) \
                    and not self.side_clip.collidepoint(pos):
                return
        typing = self.angle_input.focused or any(field.focused for field in self.fields.values())
        ready = self.base is not None
        if self.warping and self._handle_warp(event, pos):
            return
        if event.type == pygame.KEYDOWN and not typing and not self.embedded:
            if event.key == pygame.K_ESCAPE:
                self.close(keep=False)
                return
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.close()
                return
        if event.type == pygame.KEYDOWN and not typing and self.fine:
            if event.key == pygame.K_ESCAPE:
                self.fine = False
                return
            if ready and event.key in NUDGE:
                self._nudge(event, pos)
                return
        if ready:
            for key, field in self.fields.items():
                if field.handle(event, pos):
                    self._apply_fields(key)
            if self.angle_input.handle(event, pos):
                self._apply_angle_text()
            if self.angle.handle(event, pos):
                self.edit.set_view_angle(self.angle.value)
                self._changed()
                return
            if self.drag is not None:
                if event.type == pygame.MOUSEMOTION:
                    self._drag_update(self._point(pos))
                    return
                if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    self._drag_end()
                    return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        if self.warping and not (self.btn_warp.clicked(pos, True) or self.btn_clear_warp.clicked(pos, True)):
            self._finish_warp()     # 按其他設定(旋轉、裁切、完成…)時先結束校正

        if not self.embedded and self.btn_cancel.clicked(pos, True):
            self.close(keep=False)
            return
        if not self.embedded and self.btn_done.clicked(pos, True):
            self.close()
            return
        if not ready:
            return
        if self.frames and not self.fine and self.btn_play.clicked(pos, True):
            self.playing = not self.playing
            self._frame_at = pygame.time.get_ticks()
            return
        if self.canvas.collidepoint(pos):
            self._drag_begin(pos)
            return
        if self.ratio.clicked(pos, True):
            self._apply_ratio()
            return
        if self.lock_rect.collidepoint(pos):
            self._toggle_lock()
            return
        if self.expand_toggle.clicked(pos, True):
            self._set_expand(self.expand_toggle.value)
            return
        if self.fill.clicked(pos, True):
            self.edit.fill = self.fill.value
            self._changed()
            return
        if self.btn_warp.clicked(pos, True):
            if self.warping:
                self._finish_warp()
            else:
                self._start_warp()
            return
        if self.btn_clear_warp.clicked(pos, True):
            self.warp_points = list(ops.FULL_CORNERS)
            if self.warping:
                self._finish_warp()
            else:
                self.warping = True
                self._finish_warp()
            return
        if self.btn_fine.clicked(pos, True):
            self.fine = not self.fine
            self.fine_centers = {}   # 每次進入細調都重新以四個角為中心
            return
        if self.btn_clear_crop.clicked(pos, True):
            self.edit.crop = None
            self.ratio.index = 0
            if self.locked:
                self._toggle_lock()
            self._changed()
            return
        actions = ((self.btn_left, lambda: self._rotate(3)), (self.btn_right, lambda: self._rotate(1)),
                   (self.btn_flip_h, self.edit.flip_horizontal), (self.btn_flip_v, self.edit.flip_vertical))
        for button, action in actions:
            if button.clicked(pos, True):
                action()
                self._changed()
                return
        if self.btn_reset.clicked(pos, True):
            self.warping, self.warp_points = False, None
            self.edit.reset()
            self.ratio.index = 0
            self.locked, self.lock_ratio = False, None
            self.expand = False
            self._changed()
        elif self.btn_all.clicked(pos, True):
            self._apply_to_all()

    def _handle_warp(self, event, pos):
        """四點校正模式的滑鼠、鍵盤;用掉事件時回傳 True。"""
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:       # 取消:這次拖的角都不要,回到進來之前的樣子
                self.warping, self.warp_points, self.warp_hover, self.drag = False, None, None, None
                self.warp_selected = None
                self.message = ""
                return True
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._finish_warp()
                return True
            target = self.warp_hover if self.warp_hover is not None else self.warp_selected
            if event.key in NUDGE and target is not None:
                width, height = self._turned_size()
                dx, dy = NUDGE[event.key]
                x, y = self.warp_points[target]
                self._move_warp(target, (x + dx / width, y + dy / height))
                return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.canvas.collidepoint(pos):
            index = self._warp_at(pos)
            if index is not None:
                self.drag = {"warp": index}
                self.warp_selected = index
            return True
        if self.drag is not None and "warp" in self.drag:
            self.warp_hover = self.drag["warp"]
            if event.type == pygame.MOUSEMOTION:
                self._move_warp(self.drag["warp"], self._point(pos))
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self.drag = None
            return True
        if event.type == pygame.MOUSEMOTION:
            self.warp_hover = self._warp_at(pos) if self.canvas.collidepoint(pos) else None
        return False

    def handle_loupe(self, event, pos):
        """放大鏡裡的操作:拖曳=拉動放大鏡裡的圖片(十字固定在中間,圖片下面對到的點就是新的角)、滾輪換倍數。
        放大鏡可能蓋在設定上,要比設定先處理;用掉事件時回傳 True。"""
        if not self.warping:
            return False
        if self.drag is not None and "loupe" in self.drag:
            if event.type == pygame.MOUSEMOTION:
                last = self.drag["last"]
                self._loupe_shift(self.drag["loupe"], pos[0] - last[0], pos[1] - last[1])
                self.drag["last"] = pos
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self.drag = None
            return True
        box, index = self.loupe_rect, self._loupe_index
        if box is None or index is None or not box.collidepoint(pos):
            block = self.loupe_block
            return bool(block and block.collidepoint(pos)
                        and event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP))
        if event.type == pygame.MOUSEWHEEL:
            zooms = self._loupe_zooms()
            at = zooms.index(min(self.loupe_zoom, zooms[-1])) + (1 if event.y > 0 else -1)
            self.loupe_zoom = zooms[min(max(at, 0), len(zooms) - 1)]
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.warp_selected = index
            self.drag = {"loupe": index, "last": pos}
            return True
        return event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP)

    def _loupe_zooms(self):
        """這張圖可以用的倍數:放到原圖 1 像素 = 螢幕 LOUPE_PIXEL_MAX 像素為止。"""
        scale = self._display_scale()
        return [z for z in LOUPE_ZOOMS if scale * z <= LOUPE_PIXEL_MAX] or [LOUPE_ZOOMS[0]]

    def _display_scale(self):
        """畫面上的圖 1 個螢幕像素 = 原圖幾分之一像素(校正前的圖)。"""
        width = self._view(True, warp=False).get_width()
        return self.image_rect.width / max(1, width)

    def loupe_pixels(self):
        """放大鏡裡原圖 1 像素佔幾個螢幕像素。"""
        return self._display_scale() * min(self.loupe_zoom, self._loupe_zooms()[-1])

    def _loupe_shift(self, index, dx, dy):
        """放大鏡裡的圖片被拉動 (dx, dy) 個螢幕像素:十字下面換成另一個點,角往反方向移動。"""
        width, height = self._view(True, warp=False).get_size()
        pixels = self.loupe_pixels()
        x, y = self.warp_points[index]
        self._move_warp(index, (x - dx / pixels / width, y - dy / pixels / height))

    # ------------------------------------------------------------ 繪製

    def _view_key(self, fine, warp=True):
        frame = self.frame_index if self.frames and not fine else 0
        return (fine, self.edit.angle, self.edit.quarter, self.edit.flip, self.edit.fill,
                self.edit.warp if warp else None, self.edit.adjust, frame)

    def _view(self, fine, warp=True):
        """旋轉、翻轉、校正後(還沒裁切)的預覽;warp 為 False 時是校正前。動畫播放時每一格都留著,編輯改變後才清掉。"""
        key = self._view_key(fine, warp)
        surface = self._views.get(key)
        if surface is None:
            if fine or not self.frames:
                source = self.base if fine else self.small
            else:
                source = self.frames[key[7]][0]
            image = ops.apply_edit(source, ops.Edit(key[1], key[2], key[3], None, key[4], key[5], key[6]))
            image = image.convert("RGBA")
            surface = pygame.image.frombytes(image.tobytes(), image.size, "RGBA")
            self._views = {k: v for k, v in self._views.items() if k[0] != fine or k[:7] == key[:7]}
            if len(self._views) > 150:
                self._views = {}
            self._views[key] = surface
        return surface

    def hint(self):
        """底下那一行的說明(嵌入時由頁面自己畫):(文字, 顏色)。"""
        if self.message:
            return self.message, self.accent
        if self.base is None:
            return "", theme.TEXT_DIM
        out_w, out_h = ops.edited_size(self.full_size, self.edit)
        hint = (WARP_HINT if self.warping else
                "拖曳任意格做細微調整；滑鼠停在格子上可用方向鍵逐像素移動" if self.fine
                else "拖曳框的角或邊調整範圍，在框外拖曳可重新框選；按鎖鏈可固定比例")
        return f"編輯後 {out_w}×{out_h} px · {hint}", theme.TEXT_DIM

    def draw_in(self, area, mouse_pos):
        """嵌入頁面時畫在 area 裡:左邊畫面、右邊設定。"""
        screen = self.get_screen()
        # 設定區要有底板:頁面後面可能是使用者的背景圖,直接畫上去字會看不清楚
        panel = pygame.Rect(area.right - SIDE_W - 28, area.y, SIDE_W + 28, area.height)
        rounded_panel(screen, panel, theme.PANEL, radius=12, alpha=235, border=theme.PANEL_EDGE)
        side = panel.inflate(-28, -28)
        self.canvas = pygame.Rect(area.x, area.y, panel.x - 16 - area.x, area.height)
        self._draw_canvas(screen, mouse_pos)
        # 放不下時捲動;畫在範圍外的設定被切掉,也不能被點到(見 handle_event)
        view = self.side_view
        previous = screen.get_clip()
        screen.set_clip(side)
        bottom = self._draw_side(screen, pygame.Rect(side.x, side.y - view.scroll, side.width, side.height), mouse_pos)
        screen.set_clip(previous)
        view.layout(panel.inflate(-4, -16), bottom - (side.y - view.scroll) + 8)
        view.draw(screen, mouse_pos)
        self.side_clip = side
        self.draw_loupe(screen, side, bottom)

    def draw(self, mouse_pos):
        screen = self.get_screen()
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 170))
        screen.blit(veil, (0, 0))

        panel = pygame.Rect(24, 24, width - 48, height - 48)
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.PANEL_EDGE)
        title = draw_text(screen, "編輯圖片", (panel.x + 24, panel.y + 18), 17, theme.TEXT, bold=True)
        if self.item is not None:
            draw_text(screen, widgets.clip_text(self.item.path.name, 13, panel.width - 200),
                      (title.right + 14, panel.y + 22), 13, theme.TEXT_DIM)

        body_h = panel.height - 60 - 72
        side = pygame.Rect(panel.right - 24 - SIDE_W, panel.y + 60, SIDE_W, body_h)
        self.canvas = pygame.Rect(panel.x + 24, panel.y + 60, side.x - 20 - (panel.x + 24), body_h)
        self._draw_canvas(screen, mouse_pos)
        self.draw_loupe(screen, side, self._draw_side(screen, side, mouse_pos))

        footer_y = panel.bottom - 56
        text, color = self.hint()
        draw_text(screen, widgets.clip_text(text, 13, panel.width - 48 - 220), (panel.x + 24, footer_y + 18), 13,
                  color)
        self.btn_cancel.draw(screen, pygame.Rect(panel.right - 24 - 192, footer_y + 8, 90, 36), mouse_pos)
        self.btn_done.draw(screen, pygame.Rect(panel.right - 24 - 90, footer_y + 8, 90, 36), mouse_pos)

    def _draw_canvas(self, screen, mouse_pos):
        canvas = self.canvas
        rounded_panel(screen, canvas, theme.BG_DEEP, radius=10, alpha=220)
        self.quadrants = []
        if self.error:
            draw_text(screen, f"無法讀取：{self.error}", canvas.center, 14, theme.DANGER, center=True)
        elif self.base is None:
            draw_text(screen, "讀取中...", canvas.center, 14, theme.TEXT_DIM, center=True)
        elif self.warping:
            self._draw_warp(screen, mouse_pos)
        elif self.fine:
            self._draw_fine(screen, mouse_pos)
        else:
            self._draw_normal(screen, mouse_pos)

    def _draw_warp(self, screen, mouse_pos):
        """四點校正:整張校正前的圖、四個角圍起來的範圍,抓著或停在角上時旁邊顯示放大鏡。"""
        canvas = self.canvas
        area = pygame.Rect(canvas.x, canvas.y, canvas.width, canvas.height - (PLAY_BAR if self.frames else 0))
        surface = self._view(False, warp=False)
        view_w, view_h = surface.get_size()
        full_w, _ = self._turned_size()
        scale = min((area.width - 48) / view_w, (area.height - 48) / view_h, full_w / view_w)
        size = (max(1, int(view_w * scale)), max(1, int(view_h * scale)))
        rect = pygame.Rect((0, 0), size)
        rect.center = area.center
        self.image_rect = rect
        cache_key = (self._view_key(False, warp=False), size)
        if self._scaled is None or self._scaled[0] != cache_key:
            self._scaled = (cache_key, pygame.transform.smoothscale(surface, size))
        _blit_checker(screen, rect.clip(area), canvas.topleft)
        screen.blit(self._scaled[1], rect.topleft)
        corners = self._warp_screen()
        veil = pygame.Surface(rect.size, pygame.SRCALPHA)
        veil.fill(SHADE)
        pygame.draw.polygon(veil, (0, 0, 0, 0), [(x - rect.x, y - rect.y) for x, y in corners])
        screen.blit(veil, rect.topleft)
        pygame.draw.polygon(screen, self.accent, corners, 2)
        # 放大鏡顯示:正在拖的角 > 滑鼠停著的角 > 點選過的角(滑鼠移開也一直顯示,才能在放大鏡裡微調)
        if self.drag is not None and ("warp" in self.drag or "loupe" in self.drag):
            active = self.drag.get("warp", self.drag.get("loupe"))
        else:
            active = self.warp_hover if self.warp_hover is not None else self.warp_selected
        for index, (x, y) in enumerate(corners):
            radius = 9 if index == active else 7
            pygame.draw.circle(screen, theme.BG_DEEP, (x, y), radius + 2)
            pygame.draw.circle(screen, self.accent, (x, y), radius, 0 if index == active else 3)
        self._loupe = (area, active) if active is not None else None

    def draw_loupe(self, screen, side=None, bottom=None):
        """四點校正的放大鏡:點選、抓著或停在角上時顯示,放在右邊設定區(side),不會擋住圖片。
        調整模式校正時放在裁切那一區(_draw_side 留的位置);其他時候放在設定的最後一項(bottom)下面空著的地方;
        都放不下(視窗很小)才放在畫面四個角落裡離四個點最遠的那個。要在設定區畫完之後呼叫,才不會被蓋住。"""
        slot, self._loupe_slot = self._loupe_slot, None
        self.loupe_rect = self._loupe_index = None
        # 留給放大鏡的位置底下是藏起來的裁切設定,沒顯示放大鏡時也不能點到
        self.loupe_block = slot.clip(side) if slot is not None and side is not None else slot
        if slot is not None and side is not None and not side.contains(slot):
            slot = None                 # 設定區捲動後留的位置看不完整:放大鏡改放別處,不畫到設定區外面
        if not self.warping or self._loupe is None:
            return
        area, index = self._loupe
        self._loupe = None
        corners = self._warp_screen()
        free = side.bottom - (bottom + 16) if side is not None and bottom is not None else 0
        if slot is not None and min(slot.width, slot.height) >= LOUPE_SIDE_MIN:
            size = min(slot.width, slot.height, LOUPE_MAX)
            box = pygame.Rect(0, 0, size, size)
            box.center = slot.center
        elif free >= LOUPE_SIDE_MIN:
            size = min(side.width, free, LOUPE_MAX)
            box = pygame.Rect(0, 0, size, size)
            box.midbottom = (side.centerx, side.bottom)
        else:
            size = LOUPE
            spots = [pygame.Rect(left, top, LOUPE, LOUPE) for left in (area.x + 12, area.right - 12 - LOUPE)
                     for top in (area.y + 12, area.bottom - 12 - LOUPE)]
            box = max(spots, key=lambda r: min(math.dist(r.center, c) for c in corners))
        base = self._view(True, warp=False)
        base_w, base_h = base.get_size()
        px, py = self.warp_points[index][0] * base_w, self.warp_points[index][1] * base_h
        self.loupe_rect, self._loupe_index = box, index
        self.loupe_block = self.loupe_block or box
        pixels = self.loupe_pixels()
        half = size / pixels / 2
        source = pygame.Rect(int(px - half) - 1, int(py - half) - 1, int(half * 2) + 3, int(half * 2) + 3)
        rounded_panel(screen, box.inflate(6, 6), theme.PANEL, radius=8, border=self.accent)
        clipped = source.clip(base.get_rect())
        previous = screen.get_clip()
        screen.set_clip(box)
        _blit_checker(screen, box, box.topleft)
        if clipped.width and clipped.height:
            left = round(box.centerx + (clipped.x - px) * pixels)
            top = round(box.centery + (clipped.y - py) * pixels)
            scaled = (max(1, round(clipped.width * pixels)), max(1, round(clipped.height * pixels)))
            resize = pygame.transform.scale if pixels >= 1 else pygame.transform.smoothscale
            screen.blit(resize(base.subsurface(clipped), scaled), (left, top))
        pygame.draw.line(screen, self.accent, (box.centerx - 14, box.centery), (box.centerx + 14, box.centery), 1)
        pygame.draw.line(screen, self.accent, (box.centerx, box.centery - 14), (box.centerx, box.centery + 14), 1)
        badge = pygame.Rect(box.x + 6, box.y + 6, 34, 20)
        rounded_panel(screen, badge, theme.BG_DEEP, radius=6, alpha=200)
        draw_text(screen, f"{min(self.loupe_zoom, self._loupe_zooms()[-1])}×", badge.center, 12, theme.TEXT,
                  center=True)
        screen.set_clip(previous)

    def _draw_normal(self, screen, mouse_pos):
        canvas = self.canvas
        area = pygame.Rect(canvas.x, canvas.y, canvas.width, canvas.height - (PLAY_BAR if self.frames else 0))
        surface = self._view(False)
        full_w, _ = self._full_view()
        view_w, view_h = surface.get_size()
        crop = self.edit.crop or FULL
        if self.drag is not None and "layout" in self.drag:
            layout = self.drag["layout"]   # 拖曳中版面固定,畫面不會跟著框縮放
        elif self.expand:
            # 擴展畫布:圖片四周留空間可以往外拉,超出圖片的框也要完整顯示
            layout = (min(-0.15, crop[0]), min(-0.15, crop[1]), max(1.15, crop[2]), max(1.15, crop[3]))
        else:
            layout = FULL
        self._layout = layout
        span_w, span_h = layout[2] - layout[0], layout[3] - layout[1]
        scale = min((area.width - 48) / (view_w * span_w), (area.height - 48) / (view_h * span_h), full_w / view_w)
        size = (max(1, int(view_w * scale)), max(1, int(view_h * scale)))
        rect = pygame.Rect((0, 0), size)
        rect.x = round(area.centerx - size[0] * (layout[0] + span_w / 2))
        rect.y = round(area.centery - size[1] * (layout[1] + span_h / 2))
        self.image_rect = rect
        cache_key = (self._view_key(False), size)
        if self._scaled is None or self._scaled[0] != cache_key:
            self._scaled = (cache_key, pygame.transform.smoothscale(surface, size))

        box = self._box_screen()
        if self.expand:
            self._draw_fill(screen, box.clip(area), canvas.topleft)
        _blit_checker(screen, rect.clip(area), canvas.topleft)
        screen.blit(self._scaled[1], rect.topleft)
        _shade(screen, rect, box)
        if self.expand:
            pygame.draw.rect(screen, IMAGE_EDGE, rect, 1)   # 細框標出原圖範圍
        if self.edit.crop or self.drag:
            for i in (1, 2):   # 三分線
                x, y = box.x + box.width * i // 3, box.y + box.height * i // 3
                pygame.draw.line(screen, theme.TEXT_FAINT, (x, box.top), (x, box.bottom))
                pygame.draw.line(screen, theme.TEXT_FAINT, (box.left, y), (box.right, y))
        pygame.draw.rect(screen, self.accent, box, 2)
        for hx in (box.left, box.centerx, box.right):
            for hy in (box.top, box.centery, box.bottom):
                if hx == box.centerx and hy == box.centery:
                    continue
                corner = hx != box.centerx and hy != box.centery
                handle = pygame.Rect(0, 0, 12 if corner else 9, 12 if corner else 9)
                handle.center = (hx, hy)
                pygame.draw.rect(screen, theme.BG_DEEP, handle.inflate(4, 4), border_radius=3)
                pygame.draw.rect(screen, self.accent, handle, border_radius=2)

        if self.frames:
            # 動畫播放列:直接看編輯套用到每一格的效果
            bar_y = canvas.bottom - PLAY_BAR
            pygame.draw.line(screen, theme.PANEL_EDGE, (canvas.x + 12, bar_y), (canvas.right - 12, bar_y))
            self.btn_play.label = "暫停" if self.playing else "播放"
            self.btn_play.draw(screen, pygame.Rect(canvas.x + 14, bar_y + 8, 80, 34), mouse_pos)
            label = draw_text(screen, f"第 {self.frame_index + 1} / {len(self.frames)} 格",
                              (canvas.x + 108, bar_y + 16), 13, theme.TEXT_DIM)
            total = getattr(self.item, "frames", 0)
            if len(self.frames) < total:
                # 格數太多時只把前面的格子讀進記憶體預覽;輸出時每一格都會處理
                draw_text(screen, f"動畫共 {total} 格，只預覽前 {len(self.frames)} 格，輸出不受影響",
                          (label.right + 14, bar_y + 17), 12, theme.TEXT_FAINT)

    def _draw_fine(self, screen, mouse_pos):
        """四格各自放大裁切框的一個角;拖曳某個角時其他格的畫面不跟著移動,看得出框線在動。"""
        canvas = self.canvas
        surface = self._view(True)
        view_w, view_h = surface.get_size()
        fit = min(canvas.width / view_w, canvas.height / view_h)
        zoom = max(fit, min(fit * FINE_ZOOM, MAX_ZOOM))
        gap = 8
        quad_w, quad_h = (canvas.width - gap) // 2, (canvas.height - gap) // 2
        crop = self.edit.crop or FULL
        key = self._view_key(True)
        if key != self._fine_key:   # 旋轉、翻轉後畫面整個不同,重新置中
            self.fine_centers, self._fine_key = {}, key
        for index, (corner, label) in enumerate(QUADRANTS):
            quad = pygame.Rect(canvas.x + index % 2 * (quad_w + gap), canvas.y + index // 2 * (quad_h + gap),
                               quad_w, quad_h)
            point = ((crop[0] if "l" in corner else crop[2]) * view_w,
                     (crop[1] if "t" in corner else crop[3]) * view_h)
            center = self.fine_centers.get(corner)
            dragging = self.drag is not None and self.drag.get("quad") is not None and self.drag["mode"] == corner
            # 只有角快跑出格子時才重新置中(正在拖的那格不動)
            leaving = center is not None and (abs(point[0] - center[0]) * zoom > quad_w / 2 - 24
                                              or abs(point[1] - center[1]) * zoom > quad_h / 2 - 24)
            if center is None or (leaving and not dragging):
                center = self.fine_centers[corner] = point
            self.quadrants.append((corner, quad, center, zoom, (view_w, view_h)))
            self._draw_quadrant(screen, quad, corner, label, center, zoom, surface, mouse_pos)

    def _draw_quadrant(self, screen, quad, corner, label, center, zoom, surface, mouse_pos):
        cx, cy = center
        view_w, view_h = surface.get_size()
        crop = self.edit.crop or FULL

        def to_x(value):
            return quad.centerx + (value - cx) * zoom

        def to_y(value):
            return quad.centery + (value - cy) * zoom

        rounded_panel(screen, quad, theme.BG_DEEP, radius=8)
        screen.set_clip(quad)
        x1, y1 = round(to_x(crop[0] * view_w)), round(to_y(crop[1] * view_h))
        x2, y2 = round(to_x(crop[2] * view_w)), round(to_y(crop[3] * view_h))
        box = pygame.Rect(x1, y1, max(1, x2 - x1), max(1, y2 - y1))
        if self.expand:
            self._draw_fill(screen, box.clip(quad), quad.topleft)
        source = pygame.Rect(int(cx - quad.width / 2 / zoom) - 1, int(cy - quad.height / 2 / zoom) - 1,
                             int(quad.width / zoom) + 3, int(quad.height / zoom) + 3).clip(surface.get_rect())
        if source.width and source.height:
            left, top = round(to_x(source.x)), round(to_y(source.y))
            size = (max(1, round(to_x(source.right)) - left), max(1, round(to_y(source.bottom)) - top))
            area = pygame.Rect((left, top), size)
            _blit_checker(screen, area.clip(quad), quad.topleft)
            # 放大用最近點縮放,像素邊界清楚,細調時才看得出差一格
            screen.blit(pygame.transform.scale(surface.subsurface(source), size), area.topleft)
            _shade(screen, area, box)
        if self.expand:
            edge = pygame.Rect(round(to_x(0)), round(to_y(0)), round(view_w * zoom), round(view_h * zoom))
            pygame.draw.rect(screen, IMAGE_EDGE, edge, 1)
        # 框線和角的標記一律要畫;擴展畫布時角可能在圖片外,格子裡不一定有圖片
        pygame.draw.rect(screen, self.accent, box, 2)
        px, py = (x1 if "l" in corner else x2), (y1 if "t" in corner else y2)
        step_x, step_y = (1 if "l" in corner else -1), (1 if "t" in corner else -1)
        pygame.draw.line(screen, self.accent, (px, py), (px + 26 * step_x, py), 5)
        pygame.draw.line(screen, self.accent, (px, py), (px, py + 26 * step_y), 5)
        screen.set_clip(None)
        pygame.draw.rect(screen, self.accent if quad.collidepoint(mouse_pos) else theme.PANEL_EDGE, quad, 1,
                         border_radius=8)

        values = self._crop_pixels()
        corner_x = values["x"] + (0 if "l" in corner else values["w"])
        corner_y = values["y"] + (0 if "t" in corner else values["h"])
        tag = f"{label}  {corner_x}, {corner_y}"
        tag_rect = pygame.Rect(quad.x + 8, quad.y + 8, theme.font(12).size(tag)[0] + 16, 22)
        rounded_panel(screen, tag_rect, theme.PANEL, radius=6, alpha=220)
        draw_text(screen, tag, (tag_rect.x + 8, tag_rect.y + 3), 12, theme.TEXT)

    def _draw_fill(self, screen, rect, origin):
        """補空白的地方:透明用和原圖相同的棋盤格,白色、黑色直接塗滿。"""
        if rect.width <= 0 or rect.height <= 0:
            return
        if self.edit.fill in FILL_PREVIEW:
            screen.fill(FILL_PREVIEW[self.edit.fill], rect)
        else:
            _blit_checker(screen, rect, origin)

    def _draw_lock(self, screen, rect, mouse_pos):
        """寬、高中間的鎖鏈按鈕:接起來的鎖鏈是固定比例,斷開是不固定。"""
        self.lock_rect = rect
        hovered = rect.collidepoint(mouse_pos)
        if self.locked:
            rounded_panel(screen, rect, tuple(int(c * 0.30) for c in self.accent), radius=8, border=self.accent)
        else:
            rounded_panel(screen, rect, theme.PANEL_LIGHT if hovered else theme.PANEL, radius=8,
                          border=self.accent if hovered else theme.PANEL_EDGE)
        color = self.accent if self.locked or hovered else theme.TEXT_DIM
        icon = _icon("link_on" if self.locked else "link_off", 22, color)
        if icon is not None:
            screen.blit(icon, icon.get_rect(center=rect.center))
        else:
            draw_text(screen, "鎖" if self.locked else "開", rect.center, 13, color, center=True)

    def _draw_side(self, screen, rect, mouse_pos):
        x, y, inner = rect.x, rect.y, rect.width
        half = (inner - 12) // 2
        ready = self.base is not None

        if self.warping:
            # 校正時用不到裁切(完成校正會清掉裁切範圍),同樣大小的位置改放放大鏡,版面不會跳
            draw_text(screen, "放大鏡", (x, y), 14, theme.TEXT, bold=True)
            draw_text(screen, "滾輪換倍數", (x + inner, y + 3), 12, theme.TEXT_FAINT, right=True)
            slot = pygame.Rect(x, y + 24, inner, self._crop_section_h - 24 - 10)
            self._loupe_slot = slot
            draw_text(screen, "點選一個角，就會在這裡放大", slot.center, 13, theme.TEXT_FAINT, center=True)
            y += self._crop_section_h
        else:
            top = y
            draw_text(screen, "裁切", (x, y), 14, theme.TEXT, bold=True)
            y += 24
            y += self.ratio.draw(screen, pygame.Rect(x, y, inner, 0), mouse_pos) + 10
            values = self._crop_pixels()
            lock_w, label_w = 34, 22
            field_w = (inner - lock_w - 16 - label_w * 2) // 2
            fx = x
            for index, (key, label) in enumerate(FIELDS):
                draw_text(screen, label, (fx, y + 8), 14, theme.TEXT_DIM)
                field = self.fields[key]
                text = str(values[key]) if ready else ""
                if not field.focused and field.text != text:
                    field.set_text(text)
                field.draw(screen, pygame.Rect(fx + label_w, y, field_w, 34), mouse_pos)
                fx += label_w + field_w + 8
                if index == 0:
                    self._draw_lock(screen, pygame.Rect(fx, y, lock_w, 34), mouse_pos)
                    fx += lock_w + 8
            y += 44
            draw_text(screen, "擴展畫布", (x, y + 3), 14, theme.TEXT)
            draw_text(screen, "框可以拉到圖片外", (x + 70, y + 5), 12, theme.TEXT_FAINT)
            self.expand_toggle.value = self.expand
            self.expand_toggle.draw(screen, (x + inner - 42, y + 2), mouse_pos)
            y += 32
            # 旋轉多出的角也會補空白,所以沒開擴展畫布時也能選
            draw_text(screen, "空白處", (x, y + 6), 13, theme.TEXT_DIM)
            self.fill.index = [key for key, _ in FILL_OPTIONS].index(self.edit.fill)
            self.fill.draw(screen, pygame.Rect(x + 52, y, inner - 52, 28), mouse_pos)
            y += 38
            self.btn_fine.filled = self.fine
            self.btn_fine.label = "結束細調" if self.fine else "細調"
            self.btn_fine.enabled = ready
            self.btn_fine.draw(screen, pygame.Rect(x, y, half, 32), mouse_pos)
            self.btn_clear_crop.enabled = self.edit.crop is not None
            self.btn_clear_crop.draw(screen, pygame.Rect(x + half + 12, y, half, 32), mouse_pos)
            y += 40
            self._crop_section_h = y - top
        pygame.draw.line(screen, theme.PANEL_EDGE, (x, y), (x + inner, y))
        y += 10

        draw_text(screen, "旋轉、翻轉與校正", (x, y), 14, theme.TEXT, bold=True)
        y += 24
        quarter = (inner - 18) // 4
        for i, button in enumerate((self.btn_left, self.btn_right, self.btn_flip_h, self.btn_flip_v)):
            button.draw(screen, pygame.Rect(x + i * (quarter + 6), y, quarter, 32), mouse_pos)
        y += 42
        view_angle = round(self.edit.view_angle, 1) + 0.0
        if not self.angle.dragging:
            self.angle.value = round(view_angle)
        self.angle.draw(screen, pygame.Rect(x + 8, y + 9, inner - 96, 16), mouse_pos)
        text = f"{view_angle:g}"
        if not self.angle_input.focused and self.angle_input.text != text:
            self.angle_input.set_text(text)
        self.angle_input.draw(screen, pygame.Rect(x + inner - 76, y, 60, 34), mouse_pos)
        draw_text(screen, "°", (x + inner - 12, y + 6), 15, theme.TEXT_DIM)
        y += 42
        self.btn_warp.filled = self.warping
        self.btn_warp.label = "完成校正" if self.warping else "四點校正"
        self.btn_warp.enabled = ready
        self.btn_warp.draw(screen, pygame.Rect(x, y, half, 32), mouse_pos)
        self.btn_clear_warp.enabled = self.edit.warp is not None or self.warping
        self.btn_clear_warp.draw(screen, pygame.Rect(x + half + 12, y, half, 32), mouse_pos)
        y += 42
        pygame.draw.line(screen, theme.PANEL_EDGE, (x, y), (x + inner, y))
        y += 10

        self.btn_reset.enabled = self.edit.active
        self.btn_reset.draw(screen, pygame.Rect(x, y, half, 32), mouse_pos)
        self.btn_all.enabled = ready
        self.btn_all.draw(screen, pygame.Rect(x + half + 12, y, half, 32), mouse_pos)
        return y + 32
