"""顏色選單:色彩表(橫排依色相排列、直排由淺到深)、標準色、最近使用,以及可以自由調色的「自訂色彩」。

自訂色彩和小畫家的「編輯色彩」一樣:左邊的方塊選鮮豔度與明暗,旁邊的色相條選顏色,也可以直接打色碼或 RGB。
最近使用的顏色記在 config.json,下次開程式還在。
左鍵選顏色,右鍵打開自訂色彩從那個顏色開始調;選好後選單不會自己關,點選單外面(或原本的顏色按鈕)才收起來。

用法:open() 指定要貼在哪個按鈕下面,每次選好都呼叫 on_pick(顏色);顏色是 (r, g, b),選「無」時是空的 ()。
"""

import colorsys

import pygame
from PIL import Image, ImageDraw

from core import paths, theme
from core.widgets import Button, TextInput, draw_text, rounded_panel

CELL = 22
GAP = 4
COLUMNS = 10
PAD = 14
FOOT_H = 30
# 色彩表每一欄的基本色:灰、紅、橙、黃、綠、青、藍、靛、紫、粉紅(色相由暖到冷)
BASE_HUES = [None, 0, 28, 50, 125, 175, 207, 235, 272, 325]
SHADES = (0.8, 0.6, 0.35, 0.0, -0.3, -0.55)   # 正數往白色調淡,負數往黑色調深;0 是基本色
GRAYS = (255, 217, 166, 128, 64, 0)
STANDARD_COLORS = [(192, 0, 0), (255, 0, 0), (255, 192, 0), (255, 255, 0), (146, 208, 80),
                   (0, 176, 80), (0, 176, 240), (0, 112, 192), (0, 32, 96), (112, 48, 160)]
RECENT_MAX = COLUMNS
CONFIG_KEY = "recent_colors"
CONFIG_DIR = None    # 存 config.json 的資料夾;None 是程式資料夾(測試時改到別的地方,不動到使用者的設定)
SQUARE = 200        # 自訂色彩:鮮豔度、明暗方塊的邊長
HUE_W = 18
_recent = None


def shade(color, amount):
    if amount >= 0:
        return tuple(round(c + (255 - c) * amount) for c in color)
    return tuple(round(c * (1 + amount)) for c in color)


def _hsv(h, s, v):
    return tuple(round(c * 255) for c in colorsys.hsv_to_rgb(h, s, v))


def color_rows():
    """色彩表:6 列 × 10 欄,每一欄是同一個顏色由淺到深。"""
    rows = []
    for amount, gray in zip(SHADES, GRAYS):
        row = []
        for hue in BASE_HUES:
            row.append((gray,) * 3 if hue is None else shade(_hsv(hue / 360, 0.86, 0.93), amount))
        rows.append(row)
    return rows


def to_hex(color):
    return "#" + "".join(f"{c:02X}" for c in color)


def from_hex(text):
    text = text.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return None
    try:
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _config_dir():
    return str(CONFIG_DIR or paths.APP_DIR)


def _load_recent():
    stored = theme.load_config(_config_dir()).get(CONFIG_KEY)
    colors = []
    for item in stored if isinstance(stored, list) else []:
        color = from_hex(str(item))
        if color is not None and color not in colors:
            colors.append(color)
    return colors[:RECENT_MAX]


def recent():
    global _recent
    if _recent is None:
        try:
            _recent = _load_recent()
        except Exception:
            _recent = []
    return list(_recent)


def remember(color):
    """記住最近用過的顏色(最新的在最前面),存進 config.json。"""
    global _recent
    color = tuple(color)
    if not color:
        return
    _recent = [color] + [item for item in recent() if item != color]
    del _recent[RECENT_MAX:]
    try:
        stored = theme.load_config(_config_dir())
        stored[CONFIG_KEY + "說明"] = "PDF 編輯器顏色選單的「最近使用」，最新的在最前面"
        stored[CONFIG_KEY] = [to_hex(item) for item in _recent]
        theme.save_config(_config_dir(), stored)
    except Exception:
        pass


def _gradients():
    """自訂色彩方塊用的兩層漸層:由左到右白色變透明、由上到下透明變黑色。"""
    white = pygame.Surface((SQUARE, SQUARE), pygame.SRCALPHA)
    black = pygame.Surface((SQUARE, SQUARE), pygame.SRCALPHA)
    for i in range(SQUARE):
        ratio = i / (SQUARE - 1)
        white.fill((255, 255, 255, round(255 * (1 - ratio))), (i, 0, 1, SQUARE))
        black.fill((0, 0, 0, round(255 * ratio)), (0, i, SQUARE, 1))
    return white, black


_wheels = {}


def draw_wheel(screen, center, radius):
    """小小的色輪圖示(自訂色彩的按鈕);放大 4 倍畫再縮小,邊緣才平滑。"""
    wheel = _wheels.get(radius)
    if wheel is None:
        big = radius * 8
        image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        steps = 48
        for i in range(steps):
            draw.pieslice((0, 0, big - 1, big - 1), -360 * (i + 1) / steps, -360 * i / steps + 0.8,
                          fill=_hsv(i / steps, 0.85, 1.0) + (255,))
        inner = big / 3
        draw.ellipse((big / 2 - inner / 2, big / 2 - inner / 2, big / 2 + inner / 2, big / 2 + inner / 2),
                     fill=(255, 255, 255, 255))
        image = image.resize((radius * 2, radius * 2), Image.Resampling.LANCZOS)
        wheel = _wheels[radius] = pygame.image.frombytes(image.tobytes(), image.size, "RGBA")
    screen.blit(wheel, (center[0] - radius, center[1] - radius))


def _ring(radius, width, color):
    """反鋸齒的圓圈(自訂色彩的游標)。"""
    size = (radius + width) * 2 + 2
    big = size * 4
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, big - 5, big - 5), outline=color + (255,), width=width * 4)
    image = image.resize((size, size), Image.Resampling.LANCZOS)
    return pygame.image.frombytes(image.tobytes(), image.size, "RGBA")


_masks = {}


def _rounded(surface, radius):
    """把方塊的四個角修圓(反鋸齒)。"""
    width, height = surface.get_size()
    mask = _masks.get((width, height, radius))
    if mask is None:
        mask = Image.new("L", (width * 4, height * 4), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, width * 4 - 1, height * 4 - 1), radius * 4, fill=255)
        mask = _masks[(width, height, radius)] = mask.resize((width, height), Image.Resampling.LANCZOS)
    image = Image.frombytes("RGB", (width, height), pygame.image.tobytes(surface, "RGB")).convert("RGBA")
    image.putalpha(mask)
    return pygame.image.frombytes(image.tobytes(), image.size, "RGBA")


class ColorPalette:
    def __init__(self, accent):
        self.accent = accent
        self.is_open = False
        self.current = ()
        self.allow_none = False
        self.on_pick = None
        self.rect = pygame.Rect(0, 0, 0, 0)
        self._cells = []
        self._anchor = pygame.Rect(0, 0, 0, 0)
        self._restore_text = False
        # 自訂色彩
        self.custom = False
        self.hsv = (0.0, 0.0, 0.0)
        self._drag = None
        self._square = pygame.Rect(0, 0, 0, 0)
        self._hue = pygame.Rect(0, 0, 0, 0)
        self._custom_button = pygame.Rect(0, 0, 0, 0)
        self._layers = None
        self._hue_bar = None
        self._base = {}
        self.hex_input = TextInput(size=14)
        self.rgb_inputs = [TextInput(size=14) for _ in range(3)]
        self.btn_ok = Button("確定", accent=accent, size=14)
        self.btn_cancel = Button("取消", filled=False, size=14)
        self._rings = (_ring(6, 2, (0, 0, 0)), _ring(4, 2, (255, 255, 255)))

    def open(self, anchor, current, on_pick, allow_none=False, restore_text=False):
        """restore_text:打開時正在打字(例如編輯文字框),關掉後要讓輸入法繼續運作。"""
        self.current = tuple(current or ())
        self.allow_none = allow_none
        self.on_pick = on_pick
        self.is_open = True
        self.custom = False
        self._anchor = pygame.Rect(anchor)
        self._restore_text = restore_text

    def close(self):
        typing = any(box.focused for box in self._inputs())
        for box in self._inputs():
            box.blur()
        if typing and self._restore_text:
            pygame.key.start_text_input()
        self.is_open = False
        self.custom = False
        self._drag = None
        self.on_pick = None

    def _pick(self, color):
        """選好顏色就套用,選單留著(可以接著換),點選單外面才收起來。"""
        color = tuple(color)
        remember(color)
        self.current = color
        self.custom = False
        if self.on_pick is not None:
            self.on_pick(color)

    def _inputs(self):
        return [self.hex_input] + self.rgb_inputs

    # ------------------------------------------------------------ 自訂色彩

    def open_custom(self, start=None):
        start = start or self.current or (recent()[:1] or [(0, 112, 192)])[0]
        for box in self._inputs():
            box.error = False
        self.custom = True
        self._set_color(start)

    @property
    def color(self):
        return _hsv(*self.hsv)

    def _set_color(self, color, keep=None):
        """換成指定的顏色;keep 是正在打字的輸入框,不改它的內容。"""
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in color))
        if s == 0 or v == 0:
            h = self.hsv[0]             # 灰色沒有色相:保留原本的色相,色相條才不會跳回紅色
        if v == 0:
            s = self.hsv[1]
        self.hsv = (h, s, v)
        self._sync_inputs(keep, color)

    def _sync_inputs(self, keep=None, color=None):
        color = color or self.color
        if keep is not self.hex_input:
            self.hex_input.set_text(to_hex(color))
        for box, value in zip(self.rgb_inputs, color):
            if keep is not box:
                box.set_text(str(value))

    def _typed(self, box):
        if box is self.hex_input:
            color = from_hex(box.text)
        else:
            try:
                color = tuple(max(0, min(255, int(b.text or "0"))) for b in self.rgb_inputs)
            except ValueError:
                color = None
        box.error = color is None
        if color is not None:
            self._set_color(color, keep=box)

    def _drag_to(self, pos):
        h, s, v = self.hsv
        if self._drag == "square":
            s = min(1.0, max(0.0, (pos[0] - self._square.x) / (self._square.width - 1)))
            v = 1.0 - min(1.0, max(0.0, (pos[1] - self._square.y) / (self._square.height - 1)))
        elif self._drag == "hue":
            h = min(0.9999, max(0.0, (pos[1] - self._hue.y) / (self._hue.height - 1)))
        self.hsv = (h, s, v)
        self._sync_inputs()

    def _handle_custom(self, event, pos):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE and not any(b.focused for b in self._inputs()):
            self.custom = False
            return True
        if event.type == pygame.KEYDOWN and event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._pick(self.color)
            return True
        for box in self._inputs():
            if box.handle(event, pos):
                self._typed(box)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button != 1:
            return True
        if event.type == pygame.MOUSEBUTTONDOWN:
            if self._square.collidepoint(pos):
                self._drag = "square"
            elif self._hue.inflate(8, 0).collidepoint(pos):
                self._drag = "hue"
            elif self.btn_ok.rect.collidepoint(pos):
                self._pick(self.color)
                return True
            elif self.btn_cancel.rect.collidepoint(pos):
                self.custom = False
                return True
            elif not self.rect.collidepoint(pos):
                self.close()
                return True
            if self._drag is not None:
                for box in self._inputs():
                    box.blur()
                self._drag_to(pos)
        elif event.type == pygame.MOUSEMOTION and self._drag is not None:
            self._drag_to(pos)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._drag = None
        return True

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, pos) -> bool:
        """回傳 True 代表事件被顏色選單用掉了(包含點在選單外把它收起來)。"""
        if not self.is_open:
            return False
        if self.custom:
            return self._handle_custom(event, pos)
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.close()
            return True
        if event.type == pygame.MOUSEBUTTONDOWN:
            if not self.rect.collidepoint(pos):
                if event.button in (1, 3):
                    self.close()
                return True
            if event.button == 1 and self._custom_button.collidepoint(pos):
                self.open_custom()
                return True
            for color, rect in self._cells:
                if rect.collidepoint(pos):
                    if event.button == 1:
                        self._pick(color)
                    elif event.button == 3 and color:
                        self.open_custom(color)         # 右鍵:從這個顏色開始自訂
                    return True
            return True
        return event.type in (pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION, pygame.MOUSEWHEEL,
                              pygame.TEXTINPUT, pygame.TEXTEDITING)

    # ------------------------------------------------------------ 繪製

    def _sections(self):
        """[(標題, [一列顏色, ...])]。"""
        result = [("色彩", color_rows()), ("標準色彩", [STANDARD_COLORS])]
        if recent():
            result.append(("最近使用", [recent()]))
        return result

    def _place(self, screen, width, height):
        rect = pygame.Rect(self._anchor.x, self._anchor.bottom + 6, width, height)
        if rect.bottom > screen.get_height() - 8:
            rect.bottom = self._anchor.y - 6
        rect.clamp_ip(screen.get_rect().inflate(-8, -8))
        self.rect = rect
        rounded_panel(screen, rect, theme.PANEL_LIGHT, radius=10, alpha=252, border=self.accent)
        return rect

    def draw(self, screen, mouse_pos):
        if not self.is_open:
            return
        if self.custom:
            self._draw_custom(screen, mouse_pos)
            return
        sections = self._sections()
        height = PAD
        for _, rows in sections:
            height += 20 + len(rows) * (CELL + GAP)
        height += 6 + FOOT_H + PAD
        width = PAD * 2 + COLUMNS * CELL + (COLUMNS - 1) * GAP
        rect = self._place(screen, width, height)
        self._cells = []
        y = rect.y + PAD
        for title, rows in sections:
            draw_text(screen, title, (rect.x + PAD, y), 12, theme.TEXT_DIM)
            if title == "色彩":
                draw_text(screen, "右鍵點顏色可以從那個顏色自訂", (rect.right - PAD, y), 11, theme.TEXT_FAINT, right=True)
            y += 20
            for row in rows:
                for column, color in enumerate(row):
                    cell = pygame.Rect(rect.x + PAD + column * (CELL + GAP), y, CELL, CELL)
                    pygame.draw.rect(screen, color, cell, border_radius=4)
                    if tuple(color) == self.current:
                        pygame.draw.rect(screen, self.accent, cell.inflate(6, 6), 2, border_radius=6)
                    elif cell.collidepoint(mouse_pos):
                        pygame.draw.rect(screen, theme.TEXT, cell.inflate(4, 4), 1, border_radius=5)
                    else:
                        pygame.draw.rect(screen, theme.PANEL_EDGE, cell, 1, border_radius=4)
                    self._cells.append((color, cell))
                y += CELL + GAP
        # 最下面:「無」(可以不要顏色時)和「自訂色彩」
        foot = pygame.Rect(rect.x + PAD, y + 6, width - PAD * 2, FOOT_H)
        custom = foot
        if self.allow_none:
            none_box = pygame.Rect(foot.x, foot.y, (foot.width - 6) // 2, FOOT_H)
            custom = pygame.Rect(none_box.right + 6, foot.y, foot.right - none_box.right - 6, FOOT_H)
            hover = none_box.collidepoint(mouse_pos)
            rounded_panel(screen, none_box, theme.BG_DEEP, radius=6, alpha=220,
                          border=self.accent if not self.current else (theme.TEXT_FAINT if hover else theme.PANEL_EDGE))
            mark = pygame.Rect(none_box.x + 8, none_box.centery - 8, 16, 16)
            pygame.draw.rect(screen, theme.PANEL, mark, border_radius=3)
            pygame.draw.line(screen, theme.DANGER, mark.bottomleft, mark.topright, 2)
            draw_text(screen, "無", (mark.right + 8, none_box.centery - 9), 13, theme.TEXT)
            self._cells.append(((), none_box))
        hover = custom.collidepoint(mouse_pos)
        rounded_panel(screen, custom, theme.BG_DEEP, radius=6, alpha=220,
                      border=theme.TEXT_FAINT if hover else theme.PANEL_EDGE)
        draw_wheel(screen, (custom.x + 16, custom.centery), 8)
        draw_text(screen, "自訂色彩…", (custom.x + 32, custom.centery - 9), 13, theme.TEXT)
        self._custom_button = custom

    def _square_surface(self):
        if self._layers is None:
            self._layers = _gradients()
        key = round(self.hsv[0] * 360)
        surface = self._base.get(key)
        if surface is None:
            surface = pygame.Surface((SQUARE, SQUARE))
            surface.fill(_hsv(key / 360, 1.0, 1.0))
            for layer in self._layers:
                surface.blit(layer, (0, 0))
            surface = _rounded(surface, 8)
            if len(self._base) > 8:
                self._base.clear()
            self._base[key] = surface
        return surface

    def _hue_surface(self):
        if self._hue_bar is None:
            bar = pygame.Surface((HUE_W, SQUARE))
            for i in range(SQUARE):
                bar.fill(_hsv(i / SQUARE, 1.0, 1.0), (0, i, HUE_W, 1))
            self._hue_bar = _rounded(bar, HUE_W // 2)
        return self._hue_bar

    def _draw_custom(self, screen, mouse_pos):
        side_w = 150
        width = PAD * 2 + SQUARE + 12 + HUE_W + 18 + side_w
        height = PAD + 26 + SQUARE + 16 + 34 + PAD
        rect = self._place(screen, width, height)
        draw_text(screen, "自訂色彩", (rect.x + PAD, rect.y + PAD - 2), 14, theme.TEXT, bold=True)
        top = rect.y + PAD + 26
        # 鮮豔度(左→右)與明暗(上→下)
        self._square = pygame.Rect(rect.x + PAD, top, SQUARE, SQUARE)
        screen.blit(self._square_surface(), self._square)
        h, s, v = self.hsv
        marker = (round(self._square.x + s * (SQUARE - 1)), round(self._square.y + (1 - v) * (SQUARE - 1)))
        clip = screen.get_clip()
        screen.set_clip(self._square.inflate(10, 10).clip(clip))
        for ring in self._rings:
            screen.blit(ring, ring.get_rect(center=marker))
        screen.set_clip(clip)
        # 色相
        self._hue = pygame.Rect(self._square.right + 12, top, HUE_W, SQUARE)
        screen.blit(self._hue_surface(), self._hue)
        hy = round(self._hue.y + h * (SQUARE - 1))
        knob = pygame.Rect(self._hue.x - 3, hy - 4, HUE_W + 6, 8)
        pygame.draw.rect(screen, (0, 0, 0), knob.inflate(2, 2), border_radius=4)
        pygame.draw.rect(screen, (255, 255, 255), knob, border_radius=4)
        pygame.draw.rect(screen, _hsv(h, 1.0, 1.0), knob.inflate(-4, -4), border_radius=2)
        # 右邊:新舊顏色對照、色碼、RGB
        x = self._hue.right + 18
        preview = pygame.Rect(x, top, side_w, 40)
        old = self.current or self.color
        pygame.draw.rect(screen, self.color, (preview.x, preview.y, preview.width // 2, preview.height),
                         border_top_left_radius=6, border_bottom_left_radius=6)
        pygame.draw.rect(screen, old, (preview.centerx, preview.y, preview.width - preview.width // 2, preview.height),
                         border_top_right_radius=6, border_bottom_right_radius=6)
        pygame.draw.rect(screen, theme.PANEL_EDGE, preview, 1, border_radius=6)
        draw_text(screen, "新的", (preview.x + preview.width // 4, preview.bottom + 4), 11, theme.TEXT_DIM, center=True)
        draw_text(screen, "目前", (preview.x + preview.width * 3 // 4, preview.bottom + 4), 11, theme.TEXT_DIM,
                  center=True)
        y = preview.bottom + 20
        self.hex_input.draw(screen, pygame.Rect(x, y, side_w, 30), mouse_pos)
        y += 36
        for box, label in zip(self.rgb_inputs, ("紅", "綠", "藍")):
            self._draw_channel(screen, box, label, pygame.Rect(x, y, side_w, 30), mouse_pos)
            y += 34
        # 確定、取消
        buttons_y = self._square.bottom + 16
        self.btn_cancel.draw(screen, pygame.Rect(rect.right - PAD - 90, buttons_y, 90, 34), mouse_pos)
        self.btn_ok.draw(screen, pygame.Rect(rect.right - PAD - 190, buttons_y, 94, 34), mouse_pos)
        draw_text(screen, "拖曳方塊和色相條調色", (rect.x + PAD, buttons_y + 9), 12,
                  theme.TEXT_FAINT)

    def _draw_channel(self, screen, box, label, rect, mouse_pos):
        draw_text(screen, label, (rect.x, rect.centery - 9), 13, theme.TEXT_DIM)
        box.draw(screen, pygame.Rect(rect.x + 24, rect.y, rect.width - 24, rect.height), mouse_pos)
