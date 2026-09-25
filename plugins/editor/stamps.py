"""日期章、姓名章、文字章:在面板裡填字、選樣式,產生透明背景的印章圖片,放到頁面上和圖片一樣可以移動、縮放。

日期章:圓形,上排、下排可以放單位或姓名,中間是日期(西元或民國);
姓名章:方形,字由右到左、由上到下排,用標楷體;可以選陽刻(朱文,紅字)或陰刻(白文,紅底白字),
    兩個字的名字可以補成「某某之印」、三個字補成「某某某印」,這是傳統姓名章的慣例;
文字章:「已核准」「機密」這類,外面兩圈框。
都可以加上「印泥質感」:邊緣和筆畫有一點點斑駁,看起來像真的蓋上去。
上次填的字記在 config.json,下次打開還在。

注意:印章圖片只是「看起來像蓋章」,依電子簽章法不算數位簽章,別人可以複製、文件也能被改,
面板上會寫清楚;正式文件應該用自然人憑證、工商憑證做數位簽章。
"""

import datetime
import io
import os

import pygame
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from core import paths, theme, widgets
from core.widgets import Button, SegmentedControl, TextInput, draw_text, rounded_panel

PANEL_W = 640
SIZE = 600                  # 印章圖片的邊長(像素);放到頁面上再縮放
STYLES = [("date", "日期章"), ("seal", "姓名章"), ("text", "文字章")]
COLORS = [("red", "紅色"), ("blue", "藍色"), ("black", "黑色")]
RGB = {"red": (200, 30, 40), "blue": (25, 60, 170), "black": (25, 25, 25)}
ERAS = [("ad", "西元"), ("roc", "民國")]
PRESETS = ["已核准", "已收到", "機密", "急件", "副本", "作廢"]
CARVES = [("zhu", "陽刻(紅字)"), ("bai", "陰刻(白字)")]
NOTICE = "印章圖片不具數位簽章的法律效力(別人可以複製、文件也可能被改)；正式文件請用自然人憑證或工商憑證簽署"
WIDTHS = {"date": 90.0, "seal": 54.0, "text": 110.0}   # 放到頁面上的預設寬度(點)
CONFIG_KEY = "stamp"
CONFIG_DIR = None           # 存 config.json 的資料夾;None 是程式資料夾(測試時改到別的地方)
KAI_FONTS = [r"C:\Windows\Fonts\kaiu.ttf"]
BOLD_FONTS = [r"C:\Windows\Fonts\msjhbd.ttc", r"C:\Windows\Fonts\msjh.ttc", r"C:\Windows\Fonts\kaiu.ttf"]


def _font_path(candidates):
    found = next((path for path in candidates if os.path.exists(path)), None)
    if found:
        return found
    from . import fonts

    face = fonts.CATALOG.fallback()
    return str(face.path) if face is not None else None


def _font(candidates, size):
    path = _font_path(candidates)
    return ImageFont.truetype(path, size) if path else ImageFont.load_default()


def date_text(day, era):
    if era == "roc":
        return f"{day.year - 1911}.{day.month:02d}.{day.day:02d}"
    return f"{day.year}.{day.month:02d}.{day.day:02d}"


def _fit_font(candidates, text, width, height):
    """讓一行字剛好放進 width × height 的最大字級。"""
    size = max(8, int(height))
    while size > 8:
        font = _font(candidates, size)
        left, top, right, bottom = font.getbbox(text)
        if right - left <= width and bottom - top <= height:
            return font
        size = int(size * 0.92)
    return _font(candidates, size)


def _center_text(draw, box, text, font, color):
    x0, y0, x1, y1 = box
    draw.text(((x0 + x1) / 2, (y0 + y1) / 2), text, font=font, fill=color, anchor="mm")


def _char_image(ch, color, candidates, cell):
    """一個字放進印章的一格:筆畫加粗(像刻出來的),等比例放到最大;格子特別長時(三個字的那一排)再拉長一些。"""
    font = _font(candidates, 400)
    left, top, right, bottom = font.getbbox(ch)
    pad = 12
    mask = Image.new("L", (max(1, right - left) + pad * 2, max(1, bottom - top) + pad * 2), 0)
    ImageDraw.Draw(mask).text((pad - left, pad - top), ch, font=font, fill=255)
    mask = mask.filter(ImageFilter.MaxFilter(9))
    width, height = cell
    scale = min(width / mask.width, height / mask.height)
    stretch_y = min(1.6, height / (mask.height * scale))       # 高的格子:字往下拉長,最多 1.6 倍
    stretch_x = min(1.6, width / (mask.width * scale))
    size = (max(1, int(mask.width * scale * stretch_x)), max(1, int(mask.height * scale * stretch_y)))
    mask = mask.resize(size, Image.Resampling.LANCZOS)
    image = Image.new("RGBA", size, color)
    image.putalpha(mask)
    return image


def seal_text(name, add_seal):
    """姓名章要刻的字:add_seal 為真時兩個字補成「某某之印」、三個字補成「某某某印」。"""
    chars = "".join(ch for ch in name if not ch.isspace())
    if add_seal and len(chars) == 2:
        return chars + "之印"
    if add_seal and len(chars) == 3:
        return chars + "印"
    return chars[:4]


def _texture(image, seed):
    """印泥質感:透明度加上細小的斑點,邊緣稍微不齊,像真的蓋上去(同樣的字每次都一樣)。"""
    import random

    width, height = image.size
    rng = random.Random(seed)
    noise = Image.new("L", (width // 6 + 1, height // 6 + 1))
    noise.putdata([rng.randint(150, 255) if rng.random() < 0.18 else 255 for _ in range(noise.width * noise.height)])
    noise = noise.resize((width, height), Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(1.2))
    alpha = image.getchannel("A")
    image.putalpha(ImageChops.multiply(alpha, noise))
    return image


def render(style, color_key, top="", middle="", bottom="", carve="zhu", texture=False):
    """產生印章圖片(PNG 內容, 像素寬高);沒有字可以印時回傳 None。
    carve 是姓名章的刻法:zhu 陽刻(紅字)、bai 陰刻(紅底白字);texture 加上印泥質感。"""
    color = RGB[color_key] + (255,)
    if style == "seal":
        chars = [ch for ch in middle if not ch.isspace()][:4]
        if not chars:
            return None
        image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        line = 26
        if carve == "bai":
            draw.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), 30, fill=color)
        else:
            draw.rounded_rectangle((line / 2, line / 2, SIZE - line / 2, SIZE - line / 2), 30, outline=color,
                                   width=line)
        inner = 70
        span = SIZE - inner * 2
        half = span / 2
        # 格子:(左, 上, 寬, 高);傳統印章由右到左、由上到下
        if len(chars) == 1:
            cells = [(inner, inner, span, span)]
        elif len(chars) == 2:
            cells = [(inner, inner, span, half), (inner, inner + half, span, half)]
        elif len(chars) == 3:
            cells = [(inner + half, inner, half, span), (inner, inner, half, half), (inner, inner + half, half, half)]
        else:
            cells = [(inner + half, inner, half, half), (inner + half, inner + half, half, half),
                     (inner, inner, half, half), (inner, inner + half, half, half)]
        for ch, (x, y, w, h) in zip(chars, cells):
            glyph = _char_image(ch, color, KAI_FONTS, (w * 0.96, h * 0.96))
            spot = (int(x + (w - glyph.width) / 2), int(y + (h - glyph.height) / 2))
            if carve == "bai":
                # 陰刻:字的地方把紅色挖掉(透明,蓋在白紙上就是白字)
                hole = Image.new("L", image.size, 0)
                hole.paste(glyph.getchannel("A"), spot)
                image.putalpha(ImageChops.subtract(image.getchannel("A"), hole))
            else:
                image.alpha_composite(glyph, spot)
    elif style == "date":
        image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        line = 16
        draw.ellipse((line / 2, line / 2, SIZE - line / 2, SIZE - line / 2), outline=color, width=line)
        upper, lower = SIZE * 0.34, SIZE * 0.66
        for y in (upper, lower):
            half = ((SIZE / 2 - line) ** 2 - (y - SIZE / 2) ** 2) ** 0.5
            draw.line((SIZE / 2 - half, y, SIZE / 2 + half, y), fill=color, width=10)
        if top:
            _center_text(draw, (SIZE * 0.2, SIZE * 0.1, SIZE * 0.8, upper - 12), top,
                         _fit_font(BOLD_FONTS, top, SIZE * 0.56, upper - SIZE * 0.14), color)
        if middle:
            _center_text(draw, (SIZE * 0.08, upper + 8, SIZE * 0.92, lower - 8), middle,
                         _fit_font(BOLD_FONTS, middle, SIZE * 0.78, (lower - upper) * 0.62), color)
        if bottom:
            _center_text(draw, (SIZE * 0.2, lower + 12, SIZE * 0.8, SIZE * 0.9), bottom,
                         _fit_font(BOLD_FONTS, bottom, SIZE * 0.56, upper - SIZE * 0.14), color)
        if not (top or middle or bottom):
            return None
    else:
        text = middle.strip()
        if not text:
            return None
        font = _font(BOLD_FONTS, 150)
        left, top_px, right, bottom_px = font.getbbox(text)
        width, height = right - left + 140, 250
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, width - 8, height - 8), 34, outline=color, width=16)
        draw.rounded_rectangle((34, 34, width - 34, height - 34), 18, outline=color, width=6)
        draw.text((width / 2, height / 2), text, font=font, fill=color, anchor="mm")
    if texture:
        image = _texture(image, f"{style}{top}{middle}{bottom}{carve}")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue(), image.size


# ------------------------------------------------------------ 記住上次填的字

def _config_dir():
    return str(CONFIG_DIR or paths.APP_DIR)


def load_settings():
    try:
        stored = theme.load_config(_config_dir()).get(CONFIG_KEY)
    except Exception:
        stored = None
    return stored if isinstance(stored, dict) else {}


def save_settings(values):
    try:
        stored = theme.load_config(_config_dir())
        stored[CONFIG_KEY + "說明"] = "PDF 編輯器印章上次填的字與樣式"
        stored[CONFIG_KEY] = values
        theme.save_config(_config_dir(), stored)
    except Exception:
        pass


# ------------------------------------------------------------ 小視窗

class StampPanel:
    """做印章:選樣式、填字、選顏色,右邊即時預覽;按「使用這個印章」後交給 on_pick(PNG 內容, 像素寬高, 預設寬度)。"""

    def __init__(self, page, accent):
        self.page = page
        self.accent = accent
        self.is_open = False
        self.on_pick = None
        self.style = SegmentedControl(STYLES, accent=accent)
        self.color = SegmentedControl(COLORS, accent=accent)
        self.era = SegmentedControl(ERAS, accent=accent)
        self.carve = SegmentedControl(CARVES, index=1, accent=accent)     # 姓名章習慣用陰刻
        self.add_seal = True        # 姓名章補上「之印」「印」
        self.texture = False        # 印泥質感
        self._toggles = []
        self.inputs = {name: TextInput(accent=accent, size=14) for name in ("top", "date", "bottom", "name", "text")}
        self.btn_ok = Button("使用這個印章", accent=accent, size=13)
        self.btn_cancel = Button("取消", filled=False, size=13)
        self.btn_today = Button("今天", filled=False, size=12)
        self._presets = []
        self._preview_key = None
        self._preview = None
        self.message = ""

    def open(self, on_pick):
        self.on_pick = on_pick
        saved = load_settings()
        self.add_seal = bool(saved.get("add_seal", True))
        self.texture = bool(saved.get("texture", False))
        for control, key in ((self.style, "style"), (self.color, "color"), (self.era, "era"), (self.carve, "carve")):
            keys = [option[0] for option in control.options]
            if saved.get(key) in keys:
                control.index = keys.index(saved[key])
        for name, box in self.inputs.items():
            box.set_text(str(saved.get(name, "")))
            box.error = False
        if not self.inputs["text"].text:
            self.inputs["text"].set_text(PRESETS[0])
        self.inputs["date"].set_text(date_text(datetime.date.today(), self.era.value))
        self.message = ""
        self.is_open = True

    def close(self):
        for box in self.inputs.values():
            box.blur()
        self.is_open = False

    def _fields(self):
        """目前樣式要填的欄位:[(欄位, 說明)]。"""
        if self.style.value == "date":
            return [("top", "上排(例如單位)"), ("date", "日期"), ("bottom", "下排(例如姓名)")]
        if self.style.value == "seal":
            return [("name", "姓名(1～4 個字)")]
        return [("text", "文字")]

    def current(self):
        text = {name: box.text.strip() for name, box in self.inputs.items()}
        if self.style.value == "date":
            return render("date", self.color.value, text["top"], text["date"], text["bottom"], texture=self.texture)
        if self.style.value == "seal":
            return render("seal", self.color.value, middle=seal_text(text["name"], self.add_seal),
                          carve=self.carve.value, texture=self.texture)
        return render("text", self.color.value, middle=text["text"], texture=self.texture)

    def _use(self):
        made = self.current()
        if made is None:
            self.message = "還沒有填字"
            return
        values = {name: box.text for name, box in self.inputs.items() if name != "date"}
        values.update(style=self.style.value, color=self.color.value, era=self.era.value, carve=self.carve.value,
                      add_seal=self.add_seal, texture=self.texture)
        save_settings(values)
        self.close()
        if self.on_pick is not None:
            data, pixels = made
            self.on_pick(data, pixels, WIDTHS[self.style.value])

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, pos):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE \
                and not any(box.composition for box in self.inputs.values()):
            self.close()
            return
        if event.type == pygame.KEYDOWN and event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) \
                and not any(box.composition for box in self.inputs.values()):
            self._use()
            return
        shown = [name for name, _ in self._fields()]
        for name in shown:
            self.inputs[name].handle(event, pos)
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        if self.btn_cancel.clicked(pos, True):
            self.close()
        elif self.btn_ok.clicked(pos, True):
            self._use()
        elif self.style.value == "date" and self.btn_today.clicked(pos, True):
            self.inputs["date"].set_text(date_text(datetime.date.today(), self.era.value))
        elif self.style.value == "date" and self.era.clicked(pos, True):
            self.inputs["date"].set_text(date_text(datetime.date.today(), self.era.value))
        elif self.style.clicked(pos, True) or self.color.clicked(pos, True):
            self.message = ""
        elif self.style.value == "seal" and self.carve.clicked(pos, True):
            pass
        elif any(rect.collidepoint(pos) for _, rect in self._toggles):
            key = next(key for key, rect in self._toggles if rect.collidepoint(pos))
            setattr(self, key, not getattr(self, key))
        else:
            for text, rect in self._presets:
                if rect.collidepoint(pos):
                    self.inputs["text"].set_text(text)

    # ------------------------------------------------------------ 繪製

    def _preview_surface(self, size):
        key = (self.style.value, self.color.value, self.carve.value, self.add_seal, self.texture,
               tuple(box.text for box in self.inputs.values()), size)
        if key != self._preview_key:
            self._preview_key = key
            made = self.current()
            self._preview = None
            if made is not None:
                image = Image.open(io.BytesIO(made[0])).convert("RGBA")
                image.thumbnail(size, Image.Resampling.LANCZOS)
                self._preview = pygame.image.frombytes(image.tobytes(), image.size, "RGBA")
        return self._preview

    def _toggle(self, screen, key, label, x, y, mouse_pos):
        """勾選框;回傳下一列的 y。"""
        on = getattr(self, key)
        box = pygame.Rect(x, y + 6, 16, 16)
        row = pygame.Rect(x, y, theme.font(13).size(label)[0] + 28, 28)
        if on:
            pygame.draw.rect(screen, self.accent, box, border_radius=3)
            pygame.draw.lines(screen, theme.BG_DEEP, False, [(box.x + 3, box.centery), (box.x + 7, box.bottom - 4),
                                                            (box.right - 3, box.y + 4)], 2)
        else:
            edge = theme.TEXT_FAINT if row.collidepoint(mouse_pos) else theme.PANEL_EDGE
            pygame.draw.rect(screen, edge, box, 1, border_radius=3)
        draw_text(screen, label, (box.right + 8, row.centery - 9), 13, theme.TEXT)
        self._toggles.append((key, row))
        return y + 34

    def draw(self, mouse_pos):
        screen = self.page.screen
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 170))
        screen.blit(veil, (0, 0))
        panel_h = min(510, height - 40)
        panel = pygame.Rect((width - PANEL_W) // 2, (height - panel_h) // 2, PANEL_W, panel_h)
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.PANEL_EDGE)
        x = panel.x + 24
        draw_text(screen, "印章", (x, panel.y + 18), 17, theme.TEXT, bold=True)
        draw_text(screen, "填好字按「使用這個印章」，再到頁面上點一下放置；放好後可以移動、改大小",
                  (x, panel.y + 48), 12, theme.TEXT_FAINT)
        left_w = 330
        y = panel.y + 80
        self.style.draw(screen, pygame.Rect(x, y, left_w, 34), mouse_pos)
        y += 48
        self._presets = []
        for name, label in self._fields():
            draw_text(screen, label, (x, y), 12, theme.TEXT_DIM)
            box_w = left_w - (64 if name == "date" else 0)
            self.inputs[name].draw(screen, pygame.Rect(x, y + 20, box_w, 32), mouse_pos)
            if name == "date":
                self.btn_today.draw(screen, pygame.Rect(x + box_w + 8, y + 20, 56, 32), mouse_pos)
            y += 62
        if self.style.value == "date":
            self.era.draw(screen, pygame.Rect(x, y, 160, 32), mouse_pos)
            y += 44
        self._toggles = []
        if self.style.value == "seal":
            self.carve.draw(screen, pygame.Rect(x, y, left_w, 32), mouse_pos)
            y += 42
            y = self._toggle(screen, "add_seal", "補上「之印」「印」(傳統姓名章的寫法)", x, y, mouse_pos)
        if self.style.value == "text":
            px = x
            for text in PRESETS:
                chip = pygame.Rect(px, y, theme.font(12).size(text)[0] + 18, 26)
                hover = chip.collidepoint(mouse_pos)
                rounded_panel(screen, chip, theme.PANEL_LIGHT if hover else theme.BG_DEEP, radius=13, alpha=230,
                              border=theme.TEXT_FAINT if hover else theme.PANEL_EDGE)
                draw_text(screen, text, chip.center, 12, theme.TEXT, center=True)
                self._presets.append((text, chip))
                px = chip.right + 6
            y += 40
        draw_text(screen, "顏色", (x, y + 8), 13, theme.TEXT_DIM)
        self.color.draw(screen, pygame.Rect(x + 44, y, 210, 32), mouse_pos)
        y += 42
        self._toggle(screen, "texture", "印泥質感(看起來像真的蓋上去)", x, y, mouse_pos)
        area = pygame.Rect(x + left_w + 24, panel.y + 80, panel.right - 24 - (x + left_w + 24), panel_h - 80 - 76)
        pygame.draw.rect(screen, (255, 255, 255), area, border_radius=10)
        preview = self._preview_surface((area.width - 30, area.height - 30))
        if preview is not None:
            screen.blit(preview, preview.get_rect(center=area.center))
        else:
            draw_text(screen, "填字後在這裡預覽", area.center, 13, (160, 160, 160), center=True)
        if self.message:
            draw_text(screen, self.message, (x, panel.bottom - 84), 12, theme.WARN)
        elif self.style.value == "seal":
            for number, line in enumerate(widgets.wrap_text(NOTICE, 12, panel.width - 48 - 250, max_lines=2)):
                draw_text(screen, line, (x, panel.bottom - 52 + number * 17), 12, theme.WARN)
        foot = panel.bottom - 52
        self.btn_cancel.draw(screen, pygame.Rect(panel.right - 24 - 86, foot, 86, 34), mouse_pos)
        self.btn_ok.draw(screen, pygame.Rect(panel.right - 24 - 86 - 10 - 124, foot, 124, 34), mouse_pos)
