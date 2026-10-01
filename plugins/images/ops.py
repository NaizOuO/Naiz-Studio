"""圖片工具的運算:讀取(含 HEIC、SVG)、轉換格式、壓縮、合成 PDF,全部在本機處理。"""

import io
import math
import re
import zlib
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps, ImageSequence, UnidentifiedImageError

from core import large_files
from core.files import free_path, write_bytes

from . import cutout, looks, scan

# Pillow 預設超過約 1.8 億像素就直接報錯、不讓處理。大圖改成處理前跳提醒,讓使用者自己決定要不要繼續
Image.MAX_IMAGE_PIXELS = None

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # 原始碼版沒裝這個套件時,只有 HEIC 讀不了
    pillow_heif = None

EXT_FORMAT = {".jpg": "jpg", ".jpeg": "jpg", ".jfif": "jpg", ".png": "png", ".webp": "webp", ".avif": "avif",
              ".heic": "heic", ".heif": "heic", ".gif": "gif", ".bmp": "bmp", ".tif": "tiff", ".tiff": "tiff",
              ".ico": "ico", ".svg": "svg"}
READ_EXTS = set(EXT_FORMAT)
SAVE_EXT = {"jpg": ".jpg", "png": ".png", "webp": ".webp", "avif": ".avif", "gif": ".gif", "bmp": ".bmp",
            "tiff": ".tif", "ico": ".ico", "svg": ".svg", "pdf": ".pdf", "heic": ".heic"}
LABELS = {"jpg": "JPG", "png": "PNG", "webp": "WebP", "avif": "AVIF", "gif": "GIF", "bmp": "BMP", "tiff": "TIFF",
          "ico": "ICO", "svg": "SVG", "pdf": "PDF", "heic": "HEIC"}
ANIMATED_FORMATS = {"gif", "webp"}
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
SVG_MAX_SIDE = 2000   # 描邊的時間隨像素數暴增;SVG 本身可以任意放大,先縮小再描不會損失尺寸
PDF_DPI = 96
FRAME_BUDGET = 256 * 1024 * 1024   # 編輯視窗播放動畫時,所有格子加起來最多用多少記憶體
MIN_FRAME_SIDE = 240
HIGH_QUALITY = 92     # 沒開壓縮品質時,非 JPG 原圖改存有損格式所用的品質
A4 = (595, 842)
ORIENTATION = 0x0112


class Cancelled(Exception):
    pass


@dataclass
class Settings:
    fmt: str = "keep"
    quality: int = 80
    limit: bool = False
    max_side: int = 2000
    reduce_colors: bool = False
    strip_meta: bool = True
    auto_rotate: bool = True
    svg_color: str = "color"
    pdf_combine: bool = True
    pdf_page: str = "fit"
    compress: bool = False     # 關閉時保持原圖品質,quality 用不到
    split_frames: bool = False
    gif_combine: bool = False  # 轉 GIF 時把全部圖片依順序合成一個動畫
    gif_delay: int = 100       # 合成動畫時每一格顯示幾毫秒
    clear_bg: bool = False     # 單色背景變透明(白底插畫、證件照;不用 AI)
    clear_tolerance: int = cutout.TOLERANCE

    @property
    def side(self):
        return self.max_side if self.limit else None


@dataclass
class Edit:
    """單張圖的編輯,內部依序套用:任意角度(順時針為正)→ 右轉 quarter 次 90 度 → 水平翻轉 → 四點校正 → 裁切。
    warp 是四點校正的四個角(左上、右上、右下、左下),位置是占「旋轉、翻轉後畫面」的比例;
    這四點圍起來的範圍會拉正成長方形(拍斜的文件、消失點不在中間的照片)。
    crop 是 (左, 上, 右, 下) 占「校正後畫面」的比例,套用到尺寸不同的圖時會照比例裁。
    adjust 是色彩調整 (亮度, 對比, 飽和度, 色溫, 銳利度),各 -100～100;之後是重新著色 recolor(looks.RECOLORS)
    與美術效果 effect (種類, 強度),最後才套用。
    下面的操作都以使用者看到的畫面為準,會自動換算成上面的順序。"""

    angle: float = 0.0
    quarter: int = 0
    flip: bool = False
    crop: tuple = None
    fill: str = "clear"     # 旋轉、擴展畫布多出來的地方補什麼:clear 透明、white 白色、black 黑色
    warp: tuple = None
    adjust: tuple = None
    recolor: str = None
    effect: tuple = None
    scan: str = None           # 文件掃描的濾鏡(scan.FILTERS),在裁切之後、色彩之前
    cutout: tuple = None       # 去背(cutout.Cutout):遮罩在原圖上算,跟著旋轉、裁切,最後才換背景

    @property
    def active(self):
        return bool(self.angle % 360 or self.quarter % 4 or self.flip or self.crop or self.warp
                    or (self.adjust and any(self.adjust)) or self.recolor or self.effect or self.scan or self.cutout)

    @property
    def shape_active(self):
        """有沒有改到形狀(旋轉、翻轉、校正、裁切);只調色彩時是 False。"""
        return bool(self.angle % 360 or self.quarter % 4 or self.flip or self.crop or self.warp)

    def copy(self):
        return replace(self)

    def reset(self):
        self.angle, self.quarter, self.flip, self.crop, self.fill = 0.0, 0, False, None, "clear"
        self.warp = None
        self.adjust = self.recolor = self.effect = self.scan = self.cutout = None

    def _move_warp(self, change):
        if self.warp:
            self.warp = order_corners([change(x, y) for x, y in self.warp])

    @property
    def view_angle(self):
        # 翻轉過的圖,原本順時針的傾斜在畫面上看起來是逆時針
        return -self.angle if self.flip else self.angle

    def set_view_angle(self, value):
        value = max(-180.0, min(180.0, float(value)))
        self.angle = -value if self.flip else value

    def rotate_right(self, turns=1):
        """畫面順時針轉 turns 個 90 度(3 就是左轉),裁切框跟著轉。"""
        self.quarter = (self.quarter + (-turns if self.flip else turns)) % 4
        for _ in range(turns % 4 if self.crop else 0):
            left, top, right, bottom = self.crop
            self.crop = (1 - bottom, left, 1 - top, right)
        for _ in range(turns % 4):
            self._move_warp(lambda x, y: (1 - y, x))

    def flip_horizontal(self):
        self.flip = not self.flip
        if self.crop:
            left, top, right, bottom = self.crop
            self.crop = (1 - right, top, 1 - left, bottom)
        self._move_warp(lambda x, y: (1 - x, y))

    def flip_vertical(self):
        # 垂直翻轉 = 水平翻轉再轉 180 度
        self.flip = not self.flip
        self.quarter = (self.quarter + 2) % 4
        if self.crop:
            left, top, right, bottom = self.crop
            self.crop = (left, 1 - bottom, right, 1 - top)
        self._move_warp(lambda x, y: (x, 1 - y))


FULL_CORNERS = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
ADJUST_KEYS = ("brightness", "contrast", "saturation", "warmth", "sharpness")


def _curve(factor):
    return [max(0, min(255, round(v * factor))) for v in range(256)]


def adjust_image(image, adjust):
    """色彩調整;透明的地方保持透明。各項 0 是不變,-100～100。"""
    brightness, contrast, saturation, warmth, sharpness = adjust
    alpha = image.getchannel("A") if image.mode in ("RGBA", "LA", "PA") else None
    rgb = image.convert("RGB")
    if brightness:
        rgb = ImageEnhance.Brightness(rgb).enhance(1 + brightness / 100)
    if contrast:
        rgb = ImageEnhance.Contrast(rgb).enhance(1 + contrast / 100)
    if saturation:
        rgb = ImageEnhance.Color(rgb).enhance(1 + saturation / 100)
    if warmth:
        # 暖色:紅加、藍減;冷色相反。最多各 ±20%
        shift = warmth / 100 * 0.2
        red, green, blue = rgb.split()
        rgb = Image.merge("RGB", (red.point(_curve(1 + shift)), green, blue.point(_curve(1 - shift))))
    if sharpness:
        rgb = ImageEnhance.Sharpness(rgb).enhance(1 + sharpness / 50)
    if alpha is not None:
        rgb.putalpha(alpha)
    return rgb


def order_corners(points):
    """四個點排成左上、右上、右下、左下(繞中心的角度排序,從最靠左上的開始),拖到交叉也不會扭成蝴蝶結。"""
    cx = sum(x for x, _ in points) / 4
    cy = sum(y for _, y in points) / 4
    ring = sorted(points, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    start = min(range(4), key=lambda i: ring[i][0] + ring[i][1])
    return tuple(tuple(ring[(start + i) % 4]) for i in range(4))


def _cross(a, b):
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


PHONE_FOCAL = 0.75          # 一般手機主鏡頭的焦距約是畫面長邊的這麼多倍(推不出焦距時用)


def true_ratio(size, warp):
    """四個角圍起來的東西「實際的」寬高比。斜拍時近大遠小,直接量四條邊會算錯(直式的紙變得接近正方形);
    這裡從四個角的透視變形反推相機的焦距,再算出真正的比例(Zhang & He 的白板掃描方法)。
    只往一個方向傾斜時(有一組邊平行)推不出焦距,改用一般手機的焦距;正對著拍時結果就等於量邊長。
    算出來不合理時回傳 None。"""
    width, height = size
    cx, cy = width / 2, height / 2
    tl, tr, br, bl = [(x * width - cx, y * height - cy, 1.0) for x, y in warp]
    m1, m2, m3, m4 = tl, tr, bl, br
    try:
        k2 = _dot(_cross(m1, m4), m3) / _dot(_cross(m2, m4), m3)
        k3 = _dot(_cross(m1, m4), m2) / _dot(_cross(m3, m4), m2)
    except ZeroDivisionError:
        return None
    n2 = tuple(k2 * a - b for a, b in zip(m2, m1))
    n3 = tuple(k3 * a - b for a, b in zip(m3, m1))
    longest = max(size)
    focal2 = None
    if abs(n2[2] * n3[2]) > 1e-12:
        focal2 = -(n2[0] * n3[0] + n2[1] * n3[1]) / (n2[2] * n3[2])
    if focal2 is None or not (0.3 * longest) ** 2 <= focal2 <= (5 * longest) ** 2:
        focal2 = (PHONE_FOCAL * longest) ** 2
    top = (n2[0] ** 2 + n2[1] ** 2) / focal2 + n2[2] ** 2
    bottom = (n3[0] ** 2 + n3[1] ** 2) / focal2 + n3[2] ** 2
    if top <= 0 or bottom <= 0:
        return None
    ratio = math.sqrt(top / bottom)
    return ratio if 0.05 <= ratio <= 20 else None


def warp_size(size, warp):
    """四點校正後的像素尺寸:寬高比用 true_ratio 算出的實際比例(算不出來時,上下兩邊取長的當寬、
    左右兩邊取長的當高);大小以四條邊裡最長的為準,細節不會被壓縮掉。"""
    width, height = size
    tl, tr, br, bl = [(x * width, y * height) for x, y in warp]
    new_w = max(math.dist(tl, tr), math.dist(bl, br))
    new_h = max(math.dist(tl, bl), math.dist(tr, br))
    ratio = true_ratio(size, warp)
    if ratio is not None:
        if ratio >= new_w / max(new_h, 1e-9):
            new_h = new_w / ratio
        else:
            new_w = new_h * ratio
    return max(1, round(new_w)), max(1, round(new_h))


def _solve(matrix, values):
    """解 n 元一次方程組(高斯消去法,選最大的主元比較不會算歪)。"""
    n = len(values)
    rows = [list(row) + [value] for row, value in zip(matrix, values)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < 1e-12:
            raise ValueError("四個點不能排成一直線")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(n):
            if r != col:
                factor = rows[r][col] / rows[col][col]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[col])]
    return [rows[i][n] / rows[i][i] for i in range(n)]


def _warp_coefficients(size, warp):
    """四點校正的透視轉換係數(輸出圖上的點 → 原圖上的點)與輸出尺寸。"""
    width, height = size
    out_w, out_h = warp_size(size, warp)
    matrix, values = [], []
    # 輸出圖上的每個點 (x, y) 對應到原圖的 (u, v):u = (ax+by+c)/(gx+hy+1)、v = (dx+ey+f)/(gx+hy+1)
    for (x, y), (u, v) in zip(((0, 0), (out_w, 0), (out_w, out_h), (0, out_h)),
                              [(px * width, py * height) for px, py in warp]):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        values.append(u)
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        values.append(v)
    return _solve(matrix, values), (out_w, out_h)


def warp_image(image, warp):
    """把四個角圍起來的範圍拉正成長方形。"""
    coefficients, size = _warp_coefficients(image.size, warp)
    if image.mode not in ("RGB", "RGBA", "L", "LA"):
        image = image.convert("RGBA")
    return image.transform(size, Image.Transform.PERSPECTIVE, coefficients, Image.Resampling.BICUBIC)


def to_source(point, size, edit):
    """編輯後(旋轉、翻轉、校正、裁切後)畫面上的一點,換回原圖上的位置(像素)。
    去背的修正筆刷記在原圖上:之後再改旋轉、裁切,筆刷的位置也不會跑掉。size 是原圖尺寸。"""
    width, height = size
    sizes = [(width, height)]                     # 每一步之後的尺寸
    if edit.angle % 360:
        rad = math.radians(edit.angle)
        cos, sin = abs(math.cos(rad)), abs(math.sin(rad))
        sizes.append((width * cos + height * sin, width * sin + height * cos))
    rotated = sizes[-1]
    turned = (rotated[1], rotated[0]) if edit.quarter % 2 else rotated
    x, y = point
    if edit.crop:
        base = warp_size(_round_size(turned), edit.warp) if edit.warp else _round_size(turned)
        box = crop_box(base, edit.crop)
        x, y = x + box[0], y + box[1]
    if edit.warp:
        (a, b, c, d, e, f, g, h), _ = _warp_coefficients(_round_size(turned), edit.warp)
        w = g * x + h * y + 1
        x, y = (a * x + b * y + c) / w, (d * x + e * y + f) / w
    if edit.flip:
        x = turned[0] - x
    quarter = edit.quarter % 4
    if quarter == 1:
        x, y = y, rotated[1] - x
    elif quarter == 2:
        x, y = rotated[0] - x, rotated[1] - y
    elif quarter == 3:
        x, y = rotated[0] - y, x
    if edit.angle % 360:
        # 畫面上順時針轉 angle 度:反過來轉回去(以中心為準,畫布有放大)
        rad = math.radians(edit.angle)
        dx, dy = x - rotated[0] / 2, y - rotated[1] / 2
        x = dx * math.cos(rad) + dy * math.sin(rad) + width / 2
        y = -dx * math.sin(rad) + dy * math.cos(rad) + height / 2
    return x, y


def _round_size(size):
    return max(1, round(size[0])), max(1, round(size[1]))


QUARTER_TURNS = {1: Image.Transpose.ROTATE_270, 2: Image.Transpose.ROTATE_180, 3: Image.Transpose.ROTATE_90}
FILLS = {"clear": (0, 0, 0, 0), "white": (255, 255, 255, 255), "black": (0, 0, 0, 255)}


def crop_box(size, crop):
    """比例裁切框換成像素範圍,至少留 1 像素;擴展畫布時範圍可以超出圖片(小於 0 或大於寬高)。"""
    width, height = size
    left, top = round(crop[0] * width), round(crop[1] * height)
    return left, top, max(left + 1, round(crop[2] * width)), max(top + 1, round(crop[3] * height))


def apply_edit(image, edit):
    if edit is None or not edit.active:
        return image
    fill = FILLS.get(edit.fill, FILLS["clear"])
    mask = None
    if edit.cutout:
        # 去背的遮罩在原圖上算,下面每一步形狀的改變都同樣套用到遮罩上
        try:
            image, mask = cutout.source_mask(image, edit.cutout)
        except cutout.Missing:
            mask = None         # AI 還在算:畫面先顯示原圖
    if edit.angle % 360:
        # 畫布放大保留整張圖,多出來的角補上選的顏色(透明存成 JPG 時會變白色)
        image = image.convert("RGBA").rotate(-edit.angle, Image.Resampling.BICUBIC, expand=True, fillcolor=fill)
        if mask is not None:
            mask = mask.rotate(-edit.angle, Image.Resampling.BICUBIC, expand=True, fillcolor=0)
    if edit.quarter % 4:
        image = image.transpose(QUARTER_TURNS[edit.quarter % 4])
        mask = mask.transpose(QUARTER_TURNS[edit.quarter % 4]) if mask is not None else None
    if edit.flip:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        mask = mask.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if mask is not None else None
    if edit.warp:
        image = warp_image(image, edit.warp)
        mask = warp_image(mask, edit.warp) if mask is not None else None
    if edit.crop:
        box = crop_box(image.size, edit.crop)
        if box[0] < 0 or box[1] < 0 or box[2] > image.width or box[3] > image.height:
            # 擴展畫布:先鋪滿選的顏色再把圖貼上去;直接貼上不混色,原圖本身的透明會保留
            canvas = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), fill)
            canvas.paste(image.convert("RGBA"), (-box[0], -box[1]))
            image = canvas
            if mask is not None:
                grown = Image.new("L", canvas.size, 0)
                grown.paste(mask, (-box[0], -box[1]))
                mask = grown
        else:
            image = image.crop(box)
            mask = mask.crop(box) if mask is not None else None
    if edit.scan:
        image = scan.scan_filter(image, edit.scan)
    if edit.adjust and any(edit.adjust):
        image = adjust_image(image, edit.adjust)
    if edit.recolor:
        image = looks.recolor(image, edit.recolor)
    if edit.effect:
        image = looks.effect(image, *edit.effect)
    if mask is not None:
        image = cutout.compose(image, mask, edit.cutout)
    return image


def edited_size(size, edit, with_crop=True):
    """不實際處理圖片,算出編輯後的尺寸。"""
    width, height = size
    if edit is not None:
        if edit.angle % 360:
            rad = math.radians(edit.angle)
            cos, sin = abs(math.cos(rad)), abs(math.sin(rad))
            width, height = width * cos + height * sin, width * sin + height * cos
        if edit.quarter % 2:
            width, height = height, width
    width, height = max(1, round(width)), max(1, round(height))
    if edit is not None and edit.warp:
        width, height = warp_size((width, height), edit.warp)
    if edit is not None and with_crop and edit.crop:
        # 和實際裁切用同一套取整數,顯示的尺寸才不會差 1
        left, top, right, bottom = crop_box((width, height), edit.crop)
        width, height = right - left, bottom - top
    return width, height


def describe_error(exc):
    if isinstance(exc, UnidentifiedImageError):
        return "檔案損壞或不是支援的圖片"
    if isinstance(exc, MemoryError):
        return "圖片太大，記憶體不足"
    return str(exc) or type(exc).__name__


def source_format(path):
    return EXT_FORMAT.get(Path(path).suffix.lower(), "")


NO_ALPHA = {"jpg", "bmp"}


def output_format(path, fmt, transparent=False):
    """實際存成的格式:「原格式」遇到不支援透明的 JPG、BMP,但圖片去背成透明時改存 PNG,透明才不會變白。"""
    target = target_format(path, fmt)
    if fmt == "keep" and transparent and target in NO_ALPHA:
        return "png"
    return target


def transparent_output(s, edit):
    return bool(s.clear_bg or (edit is not None and edit.cutout and edit.cutout.transparent))


def target_format(path, fmt):
    """「原格式」時 SVG 存成 PNG(向量圖本身沒辦法壓縮),其他格式維持不變。"""
    if fmt != "keep":
        return fmt
    source = source_format(path)
    return "png" if source == "svg" else source


# ------------------------------------------------------------ 讀取

_SVG_TAG = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)
_SVG_LENGTH = re.compile(r"\s*([0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*(px|em|%)?\s*")


def _svg_attr(tag, name):
    match = re.search(rf"\s{name}\s*=\s*[\"']([^\"']*)[\"']", tag)
    return match.group(1) if match else None


def svg_size(text):
    """不畫出來,從 <svg> 標籤的 width、height、viewBox 算出 resvg 會畫出的大小;算不出來時回傳 None。"""
    tag = _SVG_TAG.search(text)
    if not tag:
        return None
    tag = tag.group(0)
    box = None
    numbers = re.split(r"[\s,]+", (_svg_attr(tag, "viewBox") or "").strip())
    if len(numbers) == 4:
        try:
            box = (float(numbers[2]), float(numbers[3]))
        except ValueError:
            box = None
        if box and (box[0] <= 0 or box[1] <= 0):
            box = None
    dims = []
    for index, name in enumerate(("width", "height")):
        raw = _svg_attr(tag, name)
        match = _SVG_LENGTH.fullmatch(raw) if raw is not None else None
        if raw is not None and not match:
            return None     # mm、pt 之類的單位,resvg 也畫不出來
        if match is None:
            dims.append(None)
        elif match.group(2) == "%":
            dims.append(box[index] * float(match.group(1)) / 100 if box else None)
        else:
            dims.append(float(match.group(1)) * (16 if match.group(2) == "em" else 1))
    width, height = dims
    if width is None or height is None:
        if not box:
            return None
        if width is None and height is None:
            width, height = box
        elif width is None:
            width = height * box[0] / box[1]
        else:
            height = width * box[1] / box[0]
    if width <= 0 or height <= 0:
        return None
    return max(1, round(width)), max(1, round(height))


def _render_svg(text, folder, zoom=None):
    import resvg_py

    # 用字串搭配 resources_dir:SVG 裡用相對路徑引用的圖片才讀得到(直接給檔案路徑反而讀不到)
    png = bytes(resvg_py.svg_to_bytes(svg_string=text, resources_dir=str(folder), zoom=zoom))
    image = Image.open(io.BytesIO(png))
    image.load()
    return image.convert("RGBA")


def open_image(path, svg_side=None):
    """SVG 用 resvg 畫成點陣圖(svg_side 指定最長邊,沒指定時用原本大小)。"""
    path = Path(path)
    data = path.read_bytes()   # 先讀進記憶體,處理時不會一直佔用原檔
    source = source_format(path)
    if source == "svg":
        text = data.decode("utf-8-sig", "replace")
        size = svg_size(text)
        if svg_side and size:
            return _render_svg(text, path.parent, svg_side / max(size))   # 直接畫成要的大小,不用先畫一次原尺寸
        image = _render_svg(text, path.parent)
        if svg_side and max(image.size) != svg_side:
            image = _render_svg(text, path.parent, svg_side / max(image.size))
        return image
    if source == "heic" and pillow_heif is None:
        raise RuntimeError("缺少讀取 HEIC 的元件")
    return _eight_bit(Image.open(io.BytesIO(data)))


def _eight_bit(image):
    """16 位元、32 位元的灰階圖(科學影像、深度圖常見)換成一般的 8 位元灰階;
    直接 convert 會把超過 255 的值全部截掉,整張變成白的。"""
    if image.mode not in ("I;16", "I;16L", "I;16B", "I;16N", "I", "F") or getattr(image, "n_frames", 1) > 1:
        return image
    import numpy

    values = numpy.asarray(image, dtype=numpy.float64)
    top = float(values.max()) if values.size else 0.0
    if image.mode.startswith("I;16") or top > 255:
        values = values / 257                   # 0～65535 對應到 0～255
    elif image.mode == "F" and top <= 1.0:
        values = values * 255                   # 0～1 的浮點數
    result = Image.fromarray(numpy.clip(values + 0.5, 0, 255).astype(numpy.uint8), "L")
    result.info = dict(image.info)
    result.format = image.format
    return result


def _probe_svg(path, thumb_box):
    text = path.read_bytes().decode("utf-8-sig", "replace")
    size = svg_size(text)
    # 知道大小時直接畫成縮圖大小,很大的 SVG 加入清單時也不用畫出整張
    thumb = _render_svg(text, path.parent, max(thumb_box) * 2 / max(size) if size else None)
    size = size or thumb.size
    thumb.thumbnail(thumb_box, Image.Resampling.LANCZOS)
    return {"format": LABELS["svg"], "size": size, "frames": 1, "thumb": (thumb.size, thumb.tobytes())}


def probe(path, thumb_box):
    """清單要顯示的資訊:格式、尺寸(轉正後)、動畫格數,以及 RGBA 縮圖。
    很大的圖不做縮圖(thumb 是 None):加入清單時就整張解開會用掉大量記憶體,等使用者看過提醒再處理。"""
    path = Path(path)
    if source_format(path) == "svg":
        return _probe_svg(path, thumb_box)
    image = open_image(path)
    frames = getattr(image, "n_frames", 1)
    width, height = image.size
    if frames == 1 and image.getexif().get(ORIENTATION, 1) in (5, 6, 7, 8):
        width, height = height, width
    if image.format == "JPEG":
        image.draft("RGB", (thumb_box[0] * 2, thumb_box[1] * 2))   # 大張 JPG 直接用低解析度解碼,快很多
    thumb = None
    if image.width * image.height < large_files.WARN_PIXELS:
        shown = ImageOps.exif_transpose(image) if frames == 1 else image
        thumb = shown.convert("RGBA")
        thumb.thumbnail(thumb_box, Image.Resampling.LANCZOS)
    return {"format": LABELS.get(source_format(path), "?"), "size": (width, height), "frames": frames,
            "thumb": (thumb.size, thumb.tobytes()) if thumb else None}


# ------------------------------------------------------------ 處理


def _normalize(image):
    """統一成 RGB / RGBA / L,縮放和存檔時才不會遇到調色盤、CMYK 之類的模式。"""
    if image.mode in ("RGB", "RGBA", "L"):
        return image
    alpha = image.mode in ("LA", "PA", "RGBa", "La") or "transparency" in image.info
    return image.convert("RGBA" if alpha else "RGB")


def _fit(image, side):
    """最長邊超過 side 時等比例縮小;只縮不放。"""
    if side and max(image.size) > side:
        scale = side / max(image.size)
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        return image.resize(size, Image.Resampling.LANCZOS)
    return image


def load_view(path, auto_rotate, side):
    """編輯視窗的底圖:和輸出時一樣轉正,縮到 side 以內;回傳 (圖, 原本尺寸)。"""
    image = open_image(path)
    if auto_rotate and getattr(image, "n_frames", 1) == 1:
        image = ImageOps.exif_transpose(image)
    image = image.convert("RGBA")
    full = image.size
    image.thumbnail((side, side), Image.Resampling.LANCZOS)
    return image, full


def load_frames(path, side, budget=FRAME_BUDGET):
    """編輯視窗播放動畫用:每一格(RGBA,縮到 side 以內)和顯示的毫秒數。
    所有格子都要留在記憶體裡,總量控制在 budget 以內:格數多時先把每格縮小(最小 MIN_FRAME_SIDE),
    還是放不下就只讀前面的格子。"""
    image = open_image(path)
    count = getattr(image, "n_frames", 1)
    per_frame = budget / max(1, count) / 4
    fit = int(max(image.size) * math.sqrt(per_frame / max(1, image.width * image.height)))
    side = max(MIN_FRAME_SIDE, min(side, fit))
    frames, used = [], 0
    for frame in ImageSequence.Iterator(image):
        picture = frame.convert("RGBA")
        picture.thumbnail((side, side), Image.Resampling.LANCZOS)
        cost = picture.width * picture.height * 4
        if frames and used + cost > budget:
            break
        used += cost
        frames.append((picture, frame.info.get("duration", image.info.get("duration", 100)) or 100))
    return frames


def _finish(image, edit, s):
    """一張圖(或動畫的一格)的編輯、背景變透明、縮小。"""
    image = apply_edit(image, edit)
    if s.clear_bg:
        image = cutout.clear_background(image, s.clear_tolerance)
    return _fit(image, s.side)


def _prepare(image, s, edit=None):
    """轉正、統一色彩模式、編輯、縮小;回傳 (圖片, 要保留的 EXIF 或 None)。"""
    icc = image.info.get("icc_profile")
    if s.auto_rotate:
        image = ImageOps.exif_transpose(image)
    exif = None
    if not s.strip_meta:
        data = image.getexif()
        exif = data.tobytes() if len(data) else None
    image = _finish(_normalize(image), edit, s)
    # 清掉其他附帶資料(XMP、PNG 文字欄位等),只留色彩描述檔,避免移除拍攝資訊時還有漏網之魚
    image.info = {"icc_profile": icc} if icc else {}
    return image, exif


def _flatten(image):
    """透明處墊白色,給不支援透明的 JPG 用。"""
    if image.mode != "RGBA":
        return image
    ground = Image.new("RGB", image.size, (255, 255, 255))
    ground.paste(image, mask=image.getchannel("A"))
    return ground


def _reduce_colors(image):
    if image.mode == "L":
        return image
    method = Image.Quantize.FASTOCTREE if image.mode == "RGBA" else Image.Quantize.MEDIANCUT
    return image.quantize(256, method=method)


def _trace(image, s):
    import vtracer

    image = _fit(image, SVG_MAX_SIDE)
    if s.svg_color == "bw":
        image = _flatten(image)
    png = io.BytesIO()
    image.save(png, "PNG")
    # vtracer 0.6.15 在 Python 3.14 用具名參數呼叫會直接讓程式崩潰,所以全部依位置傳入:
    # 格式、顏色、分層、曲線、雜點、色彩精度、色層差、轉角、線段長度、迭代、接合、座標精度
    svg = vtracer.convert_raw_image_to_svg(png.getvalue(), "png", "binary" if s.svg_color == "bw" else "color",
                                           "stacked", "spline", 4, 6, 16, 60, 4.0, 10, 45, 8)
    return svg.encode("utf-8")


def _jpeg_tables(image, s):
    """沒開壓縮品質(保持原圖品質):原圖是 JPG 時記下它的量化表和色度取樣,存 JPG 時照用,畫質等同原圖。
    必須在 _prepare 之前取得,處理過後就不知道原圖怎麼壓的。"""
    if s.compress or getattr(image, "format", None) != "JPEG" or not getattr(image, "quantization", None):
        return None
    from PIL import JpegImagePlugin

    return image.quantization, JpegImagePlugin.get_sampling(image)


def encode(image, target, s, exif=None, tables=None):
    """把處理好的單張圖存成指定格式,回傳檔案內容;tables 是原圖的 JPG 壓縮設定。"""
    if target == "svg":
        return _trace(image, s)
    buf = io.BytesIO()
    extra = {}
    if image.info.get("icc_profile"):
        extra["icc_profile"] = image.info["icc_profile"]
    if exif and target in ("jpg", "png", "webp", "avif", "heic"):
        extra["exif"] = exif
    # 保持原圖品質時:JPG 原圖照用原本的壓縮設定;其他格式讀不出原本的品質,用接近原圖的高品質
    quality = s.quality if s.compress else HIGH_QUALITY
    if target == "jpg":
        if tables:
            qtables, sampling = tables
            if sampling >= 0:
                extra["subsampling"] = sampling
            _flatten(image).save(buf, "JPEG", qtables=qtables, optimize=True, progressive=True, **extra)
        else:
            _flatten(image).save(buf, "JPEG", quality=quality, optimize=True, progressive=True, **extra)
    elif target == "png":
        (_reduce_colors(image) if s.reduce_colors else image).save(buf, "PNG", optimize=True, **extra)
    elif target == "webp":
        image.save(buf, "WEBP", quality=quality, method=5, **extra)
    elif target == "heic":
        if pillow_heif is None:
            raise RuntimeError("缺少輸出 HEIC 的元件")
        image.save(buf, "HEIF", quality=quality, **extra)
    elif target == "avif":
        image.save(buf, "AVIF", quality=quality, **extra)
    elif target == "gif":
        image.save(buf, "GIF", optimize=True)
    elif target == "bmp":
        image.save(buf, "BMP")
    elif target == "tiff":
        extra.pop("exif", None)
        image.save(buf, "TIFF", compression="tiff_adobe_deflate", **extra)
    elif target == "ico":
        # 圖示必須是正方形:不裁切,四周補透明
        side = max(image.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(image.convert("RGBA"), ((side - image.width) // 2, (side - image.height) // 2))
        sizes = [(n, n) for n in ICO_SIZES if n <= side] or [(side, side)]
        square.save(buf, "ICO", sizes=sizes)
    else:
        raise ValueError(f"不支援的輸出格式：{target}")
    return buf.getvalue()


def _encode_animation(image, target, s, edit=None):
    """動畫不做有損壓縮:每一格原樣保留(需要時編輯、縮小),WebP 用無損模式。"""
    frames, durations = [], []
    for frame in ImageSequence.Iterator(image):
        durations.append(frame.info.get("duration", image.info.get("duration", 100)))
        frames.append(_finish(frame.convert("RGBA"), edit, s))
    options = {"save_all": True, "append_images": frames[1:], "duration": durations}
    if "loop" in image.info:
        options["loop"] = image.info["loop"]
    buf = io.BytesIO()
    if target == "gif":
        frames[0].save(buf, "GIF", disposal=2, **options)
    else:
        frames = [_keep_alpha(frame) for frame in frames]
        options["append_images"] = frames[1:]
        frames[0].save(buf, "WEBP", lossless=True, method=4, **options)
    return buf.getvalue()


def _keep_alpha(frame):
    """WebP 動畫:完全透明又剛好是黑色 (0,0,0,0) 的地方會被 libwebp 當成空白畫布省略,
    整個檔案就被標成沒有透明,透明的地方變黑。換成看起來一樣的「透明的白色」就會保留。"""
    alpha = frame.getchannel("A")
    if alpha.getextrema()[0] > 0:
        return frame
    clear = Image.new("RGBA", frame.size, (255, 255, 255, 0))
    return Image.composite(frame, clear, alpha.point(lambda value: 255 if value else 0))


def convert(path, folder: Path, s: Settings, edit=None):
    """轉換一張圖;回傳 (輸出檔, 附註)。"""
    path = Path(path)
    target = output_format(path, s.fmt, transparent_output(s, edit))
    edited = (edit is not None and edit.active) or s.clear_bg
    folder.mkdir(parents=True, exist_ok=True)
    out = free_path(folder, path.stem, SAVE_EXT[target])
    if target == "pdf":
        images_to_pdf([path], out, s, edits=[edit])
        return out, ""

    if target == "svg" and source_format(path) == "svg" and not edited:
        write_bytes(out, path.read_bytes())   # 已經是向量圖,重新描邊只會變差
        return out, "SVG 保持原樣"

    image = open_image(path, s.side)
    frames = getattr(image, "n_frames", 1)
    note = ""
    if frames > 1 and s.split_frames and target not in ANIMATED_FORMATS:
        return _split_frames(path, image, target, folder, s, edit)
    if frames > 1 and target in ANIMATED_FORMATS:
        untouched = (not edited and target == source_format(path)
                     and max(image.size) <= (s.side or max(image.size))
                     and not (s.strip_meta and ("exif" in image.info or "xmp" in image.info)))
        if untouched:
            data = path.read_bytes()
            note = "動畫保持原樣"
        else:
            data = _encode_animation(image, target, s, edit)
    else:
        if frames > 1:
            note = "動畫只保留第一格"
        tables = _jpeg_tables(image, s)
        image, exif = _prepare(image, s, edit)
        data = encode(image, target, s, exif, tables)
    write_bytes(out, data)
    return out, note


def _split_frames(path, image, target, folder, s, edit):
    """動畫逐格拆開:每一格套用編輯後各存成一張圖,放進「檔名_frames」資料夾;回傳 (資料夾, 附註)。"""
    out = free_path(folder, f"{path.stem}_frames", "")
    out.mkdir(parents=True)
    digits = max(3, len(str(image.n_frames)))
    for index, frame in enumerate(ImageSequence.Iterator(image), start=1):
        picture = _finish(frame.convert("RGBA"), edit, s)
        picture.info = {}
        name = f"{path.stem}_{index:0{digits}d}{SAVE_EXT[target]}"
        write_bytes(out / name, encode(picture, target, s))
    return out, f"拆成 {image.n_frames} 張"


def _smallest_side(paths, s, edits):
    """所有圖片編輯、限制尺寸後,長邊最短那一張的長邊;讀不了的圖不算。"""
    sides = []
    for path, edit in zip(paths, edits):
        try:
            if source_format(path) == "svg":
                size = svg_size(Path(path).read_bytes().decode("utf-8-sig", "replace")) or open_image(path).size
                if s.side:   # 限制尺寸時 SVG 會直接畫成最長邊等於設定值(小圖也會放大)
                    scale = s.side / max(size)
                    size = (max(1, round(size[0] * scale)), max(1, round(size[1] * scale)))
            else:
                with Image.open(path) as image:
                    size = image.size
                    if s.auto_rotate and image.getexif().get(ORIENTATION, 1) in (5, 6, 7, 8):
                        size = size[::-1]
            side = max(edited_size(size, edit))
            sides.append(min(side, s.side) if s.side else side)
        except Exception:
            continue
    return min(sides) if sides else None


def _pdf_image(pdf, image, s, tables):
    """把處理好的圖做成 PDF 裡的圖片:沒有透明用 JPG,有透明用無損壓縮再加上透明遮罩。"""
    import pikepdf

    name = pikepdf.Name
    common = {"Type": name.XObject, "Subtype": name.Image, "Width": image.width, "Height": image.height,
              "BitsPerComponent": 8}
    if image.mode == "RGBA":
        mask = pikepdf.Stream(pdf, zlib.compress(image.getchannel("A").tobytes()), ColorSpace=name.DeviceGray,
                              Filter=name.FlateDecode, **common)
        return pikepdf.Stream(pdf, zlib.compress(image.convert("RGB").tobytes()), ColorSpace=name.DeviceRGB,
                              Filter=name.FlateDecode, SMask=mask, **common)
    space = name.DeviceGray if image.mode == "L" else name.DeviceRGB
    return pikepdf.Stream(pdf, encode(image, "jpg", s, tables=tables), ColorSpace=space, Filter=name.DCTDecode,
                          **common)


def _contain(frame, size):
    """等比例縮放放進 size,置中,四周透明;尺寸本來就一樣時不動。"""
    if frame.size == size:
        return frame
    scale = min(size[0] / frame.width, size[1] / frame.height)
    fitted = frame.resize((max(1, round(frame.width * scale)), max(1, round(frame.height * scale))),
                          Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.paste(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    return canvas


def images_to_gif(paths, out: Path, s: Settings, on_image=None, cancel=None, edits=None):
    """依順序把每張圖當成一格合成 GIF 動畫;本身是動畫的圖,裡面每一格都放進去(保留原本的速度)。
    大小以第一張為準,其他圖等比例放進去、置中,四周透明。回傳 ({第幾張: 錯誤訊息}, 格數)。"""
    edits = list(edits) if edits is not None else [None] * len(paths)
    frames, durations, failed = [], [], {}
    size = None
    for index, path in enumerate(paths):
        if cancel is not None and cancel.is_set():
            raise Cancelled
        if on_image is not None:
            on_image(index)
        try:
            source = open_image(path, s.side)
            count = getattr(source, "n_frames", 1)
            for number, frame in enumerate(ImageSequence.Iterator(source)):
                delay = frame.info.get("duration", source.info.get("duration", s.gif_delay)) if count > 1 \
                    else s.gif_delay
                if count > 1:
                    image = _finish(frame.convert("RGBA"), edits[index], s)
                else:
                    image, _ = _prepare(frame, s, edits[index])
                image = image.convert("RGBA")
                size = size or image.size
                frames.append(_contain(image, size))
                durations.append(max(20, int(delay or s.gif_delay)))
        except Exception as exc:
            failed[index] = describe_error(exc)
    if not frames:
        raise RuntimeError(failed.get(0, "沒有可以放入動畫的圖片") if len(paths) == 1 else "沒有可以放入動畫的圖片")
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0, disposal=2)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_bytes(out, buf.getvalue())
    return failed, len(frames)


def images_to_pdf(paths, out: Path, s: Settings, on_image=None, cancel=None, edits=None):
    """依順序每張圖一頁合成 PDF;讀不了的圖會跳過。回傳 {第幾張: 錯誤訊息}。"""
    import pikepdf

    edits = list(edits) if edits is not None else [None] * len(paths)
    doc = pikepdf.Pdf.new()
    failed = {}
    smallest = _smallest_side(paths, s, edits) if s.pdf_page == "min" else None
    try:
        for index, path in enumerate(paths):
            if cancel is not None and cancel.is_set():
                raise Cancelled
            if on_image is not None:
                on_image(index)
            try:
                source = open_image(path, s.side)
                tables = _jpeg_tables(source, s)
                image, _ = _prepare(source, s, edits[index])
                if smallest:
                    image = _fit(image, smallest)
                if image.mode == "RGBA" and image.getchannel("A").getextrema()[0] == 255:
                    image = image.convert("RGB")
                xobject = _pdf_image(doc, image, s, tables)
            except Exception as exc:
                failed[index] = describe_error(exc)
                continue
            width, height = image.size
            if s.pdf_page == "a4":
                page_w, page_h = A4 if height >= width else A4[::-1]
            elif smallest:
                # 長邊一律對齊最小那張,大圖不會比小圖大好幾倍
                scale = smallest / max(width, height) * 72 / PDF_DPI
                page_w, page_h = width * scale, height * scale
            else:
                page_w, page_h = width * 72 / PDF_DPI, height * 72 / PDF_DPI
            # 等比例放進頁面並置中(A4 時圖和頁面比例不同)
            fit = min(page_w / width, page_h / height)
            draw_w, draw_h = width * fit, height * fit
            x, y = (page_w - draw_w) / 2, (page_h - draw_h) / 2
            page = doc.add_blank_page(page_size=(page_w, page_h))
            resource = page.add_resource(xobject, pikepdf.Name.XObject, prefix="Im")
            page.obj.Contents = doc.make_stream(
                f"q {draw_w:.4f} 0 0 {draw_h:.4f} {x:.4f} {y:.4f} cm {resource} Do Q".encode())
        if not len(doc.pages):
            raise RuntimeError(failed[0] if len(paths) == 1 else "沒有可以放入 PDF 的圖片")
        out.parent.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        doc.save(buf)
        write_bytes(out, buf.getvalue())
    finally:
        doc.close()
    return failed
