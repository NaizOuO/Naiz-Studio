"""插入的圖片與簽名:讀圖、手寫簽名、從照片取出簽名,以及選簽名的小視窗。

簽名存在程式資料夾的 signatures\\(PNG,背景透明),只存在本地,下次可以直接點選使用。
"""

import io
import time
from pathlib import Path

import pygame
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from core import paths, theme, widgets, winfile
from core.widgets import Button, SegmentedControl, draw_text, rounded_panel

from .ops import IMAGE_EXTS

MAX_PIXELS = 2000           # 放進 PDF 的圖片最長邊;更大的縮小,檔案才不會暴增
SIGN_PIXELS = 1200          # 簽名的最長邊
IMAGE_FILTER = [("圖片", ";".join(f"*{ext}" for ext in sorted(IMAGE_EXTS)))]
PANEL_W = 640
CARD_W, CARD_H = 184, 84
PEN_COLORS = [("black", "黑色"), ("blue", "藍色")]
PEN_RGB = {"black": (20, 20, 20), "blue": (18, 52, 140)}
PEN_SIZES = [("thin", "細"), ("medium", "中"), ("thick", "粗")]
PEN_WIDTH = {"thin": 2.2, "medium": 3.4, "thick": 5.0}      # 畫布上的像素
OUTPUT_SCALE = 3            # 手寫簽名存檔時放大幾倍,放大列印才不會有鋸齒


def sign_dir():
    return paths.APP_DIR / "signatures"


# ------------------------------------------------------------ 圖片

def _png(image):
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def load_image(path):
    """插入的圖片:轉正、太大就縮小,保留透明;回傳 (PNG 內容, 像素寬高)。"""
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        image.load()
    has_alpha = image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
    image = image.convert("RGBA" if has_alpha else "RGB")
    image.thumbnail((MAX_PIXELS, MAX_PIXELS), Image.Resampling.LANCZOS)
    return _png(image), image.size


def signature_files():
    folder = sign_dir()
    try:
        files = [p for p in folder.iterdir() if p.suffix.lower() == ".png" and p.is_file()]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def save_signature(image):
    folder = sign_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path, number = folder / f"簽名_{stamp}.png", 2
    while path.exists():
        path, number = folder / f"簽名_{stamp}_{number}.png", number + 1
    image.save(path, "PNG", optimize=True)
    return path


def read_signature(path):
    with Image.open(path) as image:
        image = image.convert("RGBA")
    return _png(image), image.size


def _crop(image, pad):
    box = image.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
    if box is None:
        return None
    x0, y0, x1, y1 = box
    return image.crop((max(0, x0 - pad), max(0, y0 - pad), min(image.width, x1 + pad), min(image.height, y1 + pad)))


def _smooth(points, rounds=2):
    """把滑鼠取樣到的折線修圓(Chaikin 切角法),筆畫才不會一段一段的。"""
    for _ in range(rounds):
        if len(points) < 3:
            return points
        result = [points[0]]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            result.append((x0 * 0.75 + x1 * 0.25, y0 * 0.75 + y1 * 0.25))
            result.append((x0 * 0.25 + x1 * 0.75, y0 * 0.25 + y1 * 0.75))
        result.append(points[-1])
        points = result
    return points


def render_strokes(strokes, size, color, width):
    """手寫的筆畫 → 背景透明的簽名圖。先畫在放大 4 倍的圖上再縮小,邊緣才平滑。"""
    scale = OUTPUT_SCALE * 4
    canvas = Image.new("L", (size[0] * scale, size[1] * scale), 0)
    draw = ImageDraw.Draw(canvas)
    line = max(1, round(width * scale))
    for stroke in strokes:
        points = [(x * scale, y * scale) for x, y in _smooth(list(stroke))]
        if len(points) == 1:
            x, y = points[0]
            draw.ellipse((x - line / 2, y - line / 2, x + line / 2, y + line / 2), fill=255)
            continue
        draw.line(points, fill=255, width=line, joint="curve")
        for x, y in (points[0], points[-1]):
            draw.ellipse((x - line / 2, y - line / 2, x + line / 2, y + line / 2), fill=255)
    alpha = canvas.resize((size[0] * OUTPUT_SCALE, size[1] * OUTPUT_SCALE), Image.Resampling.LANCZOS)
    image = Image.new("RGBA", alpha.size, color + (0,))
    image.putalpha(alpha)
    return _crop(image, 6 * OUTPUT_SCALE)


def extract_from_photo(path):
    """紙上簽名的照片 → 只留筆跡、背景透明。

    光線不均勻時整張紙的亮度不一樣,所以先估出每個地方「紙的顏色」,再看每個點比紙暗多少;
    筆跡統一用筆的顏色(筆跡裡最深那些點的平均),只有邊緣保留漸層,看起來才乾淨。
    """
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    gray = image.convert("L")
    paper = gray.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(24))
    # 比當地的紙暗 30 以上才算筆跡,暗 110 以上算完全不透明;中間是邊緣的漸層
    darker = ImageChops.subtract(paper, gray)
    alpha = darker.point(lambda v: max(0, min(255, round((v - 30) * 255 / 80)))).filter(ImageFilter.MedianFilter(3))
    small_alpha = alpha.resize((max(1, alpha.width // 4), max(1, alpha.height // 4)))
    small_image = image.resize(small_alpha.size)
    strong = [pixel for pixel, a in zip(small_image.getdata(), small_alpha.getdata()) if a >= 230]
    if len(strong) < 12:
        raise ValueError("照片裡找不到明顯的筆跡，請在白紙上用深色的筆簽名，拍清楚一點")
    strong.sort(key=sum)
    darkest = strong[: max(12, len(strong) // 3)]
    color = tuple(round(sum(p[i] for p in darkest) / len(darkest)) for i in range(3))
    result = Image.new("RGBA", image.size, color + (0,))
    result.putalpha(alpha)
    result = _crop(result, 10)
    if result is None:
        raise ValueError("照片裡找不到明顯的筆跡")
    result.thumbnail((SIGN_PIXELS, SIGN_PIXELS), Image.Resampling.LANCZOS)
    return result


# ------------------------------------------------------------ 小視窗

class SignaturePanel:
    """選簽名:列出存好的簽名,也可以手寫新的、從照片匯入;選好後交給 on_pick(PNG 內容, 像素寬高)。"""

    def __init__(self, page, accent):
        self.page = page
        self.accent = accent
        self.is_open = False
        self.mode = "list"
        self.on_pick = None
        self.message = ("", theme.TEXT_DIM)
        self.files = []
        self._thumbs = {}
        self._cards = []
        self._armed = None          # 按過一次刪除、等待確認的簽名
        self.strokes = []
        self.canvas = pygame.Rect(0, 0, 0, 0)
        self.drawing = False
        self.pen_color = SegmentedControl(PEN_COLORS, accent=accent)
        self.pen_size = SegmentedControl(PEN_SIZES, index=1, accent=accent)
        self.btn_draw = Button("手寫新簽名", accent=accent, size=13)
        self.btn_import = Button("匯入簽名照片", filled=False, size=13)
        self.btn_cancel = Button("取消", filled=False, size=13)
        self.btn_clear = Button("清除", filled=False, size=13)
        self.btn_back = Button("返回", filled=False, size=13)
        self.btn_save = Button("儲存並使用", accent=accent, size=13)

    def open(self, on_pick):
        self.on_pick = on_pick
        self.mode = "list"
        self.message = ("", theme.TEXT_DIM)
        self._armed = None
        self.refresh()
        self.is_open = True

    def close(self):
        self.is_open = False
        self.drawing = False

    def refresh(self):
        self.files = signature_files()
        self._thumbs = {key: thumb for key, thumb in self._thumbs.items() if key[0] in self.files}

    def _pick(self, path):
        try:
            data, pixels = read_signature(path)
        except Exception:
            self.message = (f"「{path.name}」讀不到，可能已損壞", theme.DANGER)
            return
        self.close()
        if self.on_pick is not None:
            self.on_pick(data, pixels)

    def _import(self):
        chosen = winfile.ask_open("匯入簽名照片", IMAGE_FILTER)
        if chosen is None:
            return
        try:
            save_signature(extract_from_photo(chosen))
        except ValueError as exc:
            self.message = (str(exc), theme.WARN)
            return
        except Exception:
            self.message = (f"「{Path(chosen).name}」讀不到圖片", theme.DANGER)
            return
        self.refresh()
        self.message = ("已匯入，背景已去掉；點一下就能使用，不滿意可以刪掉重來", theme.ACCENT)
        self._armed = None

    def _save_drawing(self):
        strokes = [stroke for stroke in self.strokes if stroke]
        if not strokes:
            self.message = ("還沒有簽名", theme.WARN)
            return
        local = [[(x - self.canvas.x, y - self.canvas.y) for x, y in stroke] for stroke in strokes]
        image = render_strokes(local, self.canvas.size, PEN_RGB[self.pen_color.value],
                               PEN_WIDTH[self.pen_size.value])
        if image is None:
            self.message = ("還沒有簽名", theme.WARN)
            return
        self._pick(save_signature(image))

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, pos):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if self.mode == "draw":
                self.mode = "list"
            else:
                self.close()
            return
        if self.mode == "draw":
            self._handle_draw(event, pos)
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.btn_cancel.clicked(pos, True):
                self.close()
                return
            if self.btn_draw.clicked(pos, True):
                self.mode, self.strokes = "draw", []
                self.message = ("", theme.TEXT_DIM)
                return
            if self.btn_import.clicked(pos, True):
                self._import()
                return
            for path, card, remove in self._cards:
                if remove.collidepoint(pos):
                    if self._armed == path:
                        path.unlink(missing_ok=True)
                        self._armed = None
                        self.refresh()
                        self.message = ("已刪除簽名", theme.TEXT_DIM)
                    else:
                        self._armed = path
                        self.message = ("再按一次紅色的 × 刪除這個簽名", theme.WARN)
                    return
                if card.collidepoint(pos):
                    self._pick(path)
                    return
            self._armed = None

    def _handle_draw(self, event, pos):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.canvas.collidepoint(pos):
                self.drawing = True
                self.strokes.append([pos])
                return
            if self.btn_clear.clicked(pos, True):
                self.strokes = []
            elif self.btn_back.clicked(pos, True):
                self.mode = "list"
            elif self.btn_save.clicked(pos, True):
                self._save_drawing()
            else:
                self.pen_color.clicked(pos, True)
                self.pen_size.clicked(pos, True)
        elif event.type == pygame.MOUSEMOTION and self.drawing:
            x = max(self.canvas.left, min(self.canvas.right - 1, pos[0]))
            y = max(self.canvas.top, min(self.canvas.bottom - 1, pos[1]))
            last = self.strokes[-1][-1]
            if abs(x - last[0]) + abs(y - last[1]) >= 2:
                self.strokes[-1].append((x, y))
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.drawing = False

    # ------------------------------------------------------------ 繪製

    def draw(self, mouse_pos):
        screen = self.page.screen
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 170))
        screen.blit(veil, (0, 0))
        panel_h = min(470, height - 40)
        panel = pygame.Rect((width - PANEL_W) // 2, (height - panel_h) // 2, PANEL_W, panel_h)
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.PANEL_EDGE)
        if self.mode == "draw":
            self._draw_pad(screen, panel, mouse_pos)
        else:
            self._draw_list(screen, panel, mouse_pos)
        text, color = self.message
        if text:
            draw_text(screen, widgets.clip_text(text, 12, PANEL_W - 48), (panel.x + 24, panel.bottom - 84), 12, color)

    def _thumb(self, path, size):
        key = (path, size)
        if key not in self._thumbs:
            try:
                with Image.open(path) as image:
                    image = image.convert("RGBA")
                image.thumbnail(size, Image.Resampling.LANCZOS)
                self._thumbs[key] = pygame.image.frombytes(image.tobytes(), image.size, "RGBA")
            except Exception:
                self._thumbs[key] = None
        return self._thumbs[key]

    def _draw_list(self, screen, panel, mouse_pos):
        x, inner = panel.x + 24, PANEL_W - 48
        draw_text(screen, "簽名", (x, panel.y + 18), 17, theme.TEXT, bold=True)
        draw_text(screen, "點一下簽名就能放到頁面上；簽名只存在本地的 signatures\\ 資料夾",
                  (x, panel.y + 48), 12, theme.TEXT_FAINT)
        area = pygame.Rect(x, panel.y + 76, inner, panel.height - 76 - 100)
        rounded_panel(screen, area, theme.BG_DEEP, radius=10, alpha=200)
        self._cards = []
        if not self.files:
            draw_text(screen, "還沒有簽名，先手寫一個或從照片匯入", area.center, 14, theme.TEXT_DIM, center=True)
        columns = max(1, (area.width - 16) // (CARD_W + 12))
        previous = screen.get_clip()
        screen.set_clip(area)
        for number, path in enumerate(self.files):
            row, column = divmod(number, columns)
            card = pygame.Rect(area.x + 12 + column * (CARD_W + 12), area.y + 12 + row * (CARD_H + 12), CARD_W, CARD_H)
            if card.top > area.bottom:
                break
            hover = card.collidepoint(mouse_pos)
            rounded_panel(screen, card, (250, 250, 248), radius=8, border=self.accent if hover else theme.PANEL_EDGE)
            thumb = self._thumb(path, (CARD_W - 24, CARD_H - 16))
            if thumb is not None:
                screen.blit(thumb, thumb.get_rect(center=card.center))
            remove = pygame.Rect(card.right - 24, card.y + 4, 20, 20)
            if hover or self._armed == path:
                armed = self._armed == path
                pygame.draw.rect(screen, theme.DANGER if armed or remove.collidepoint(mouse_pos) else (200, 200, 200),
                                 remove, border_radius=5)
                cx, cy = remove.center
                pygame.draw.line(screen, (255, 255, 255), (cx - 4, cy - 4), (cx + 4, cy + 4), 2)
                pygame.draw.line(screen, (255, 255, 255), (cx + 4, cy - 4), (cx - 4, cy + 4), 2)
            self._cards.append((path, card, remove))
        screen.set_clip(previous)
        foot = panel.bottom - 52
        self.btn_draw.draw(screen, pygame.Rect(x, foot, 116, 34), mouse_pos)
        self.btn_import.enabled = winfile.available()
        self.btn_import.draw(screen, pygame.Rect(x + 126, foot, 124, 34), mouse_pos)
        self.btn_cancel.draw(screen, pygame.Rect(panel.right - 24 - 86, foot, 86, 34), mouse_pos)

    def _draw_pad(self, screen, panel, mouse_pos):
        x, inner = panel.x + 24, PANEL_W - 48
        draw_text(screen, "手寫簽名", (x, panel.y + 18), 17, theme.TEXT, bold=True)
        draw_text(screen, "用滑鼠或觸控筆在白色區域簽名，筆畫會自動修得比較平順",
                  (x, panel.y + 48), 12, theme.TEXT_FAINT)
        self.canvas = pygame.Rect(x, panel.y + 76, inner, panel.height - 76 - 150)
        pygame.draw.rect(screen, (255, 255, 255), self.canvas, border_radius=10)
        guide_y = self.canvas.bottom - self.canvas.height // 4
        pygame.draw.line(screen, (215, 215, 215), (self.canvas.x + 24, guide_y), (self.canvas.right - 24, guide_y), 1)
        if not self.strokes:
            draw_text(screen, "在這裡簽名", self.canvas.center, 15, (180, 180, 180), center=True)
        color, width = PEN_RGB[self.pen_color.value], PEN_WIDTH[self.pen_size.value]
        for stroke in self.strokes:
            points = _smooth(list(stroke)) if not (self.drawing and stroke is self.strokes[-1]) else stroke
            if len(points) == 1:
                pygame.draw.circle(screen, color, points[0], width / 2)
            else:
                pygame.draw.lines(screen, color, False, points, max(1, round(width)))
                for point in points:
                    pygame.draw.circle(screen, color, point, width / 2)
        row = self.canvas.bottom + 14
        draw_text(screen, "筆的顏色", (x, row + 8), 13, theme.TEXT_DIM)
        self.pen_color.draw(screen, pygame.Rect(x + 70, row, 150, 32), mouse_pos)
        draw_text(screen, "粗細", (x + 244, row + 8), 13, theme.TEXT_DIM)
        self.pen_size.draw(screen, pygame.Rect(x + 284, row, 170, 32), mouse_pos)
        foot = panel.bottom - 52
        self.btn_clear.draw(screen, pygame.Rect(x, foot, 80, 34), mouse_pos)
        self.btn_save.draw(screen, pygame.Rect(panel.right - 24 - 112, foot, 112, 34), mouse_pos)
        self.btn_back.draw(screen, pygame.Rect(panel.right - 24 - 112 - 10 - 80, foot, 80, 34), mouse_pos)
