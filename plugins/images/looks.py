"""圖片的重新著色與美術效果(參考 Word 的「色彩」「美術效果」),只用 Pillow,不需要下載任何東西。

重新著色:灰階、懷舊、刷淡、黑白(三種門檻),以及各種顏色的深色、淺色單色調。
美術效果:鉛筆素描、線條畫、模糊、柔光、馬賽克、海報、油畫、浮雕、負片、曝光過度、底片顆粒、卡通;
每種都有強度 0～100。透明的地方保持透明。
"""

from PIL import Image, ImageChops, ImageFilter, ImageOps

TINTS = [("blue", "藍", (68, 114, 196)), ("orange", "橘", (237, 125, 49)), ("gray", "灰", (128, 128, 128)),
         ("gold", "金", (214, 160, 0)), ("sky", "天藍", (91, 155, 213)), ("green", "綠", (84, 150, 60)),
         ("purple", "紫", (128, 80, 160))]
RECOLORS = [(None, "原色"), ("gray", "灰階"), ("sepia", "懷舊"), ("washout", "刷淡"),
            ("bw25", "黑白 25%"), ("bw50", "黑白 50%"), ("bw75", "黑白 75%")]
RECOLORS += [(f"dark-{key}", f"{name}色，深") for key, name, _ in TINTS]
RECOLORS += [(f"light-{key}", f"{name}色，淺") for key, name, _ in TINTS]
RECOLOR_NAMES = dict(RECOLORS)

EFFECTS = [(None, "無"), ("sketch", "鉛筆素描"), ("lines", "線條畫"), ("blur", "模糊"), ("glow", "柔光"),
           ("mosaic", "馬賽克"), ("poster", "海報"), ("paint", "油畫"), ("emboss", "浮雕"), ("cartoon", "卡通"),
           ("grain", "底片顆粒"), ("solarize", "曝光過度"), ("negative", "負片")]
EFFECT_NAMES = dict(EFFECTS)
DEFAULT_STRENGTH = 60


def _split(image):
    """(RGB 圖, 透明度或 None)。"""
    alpha = image.getchannel("A") if image.mode in ("RGBA", "LA", "PA") else None
    return image.convert("RGB"), alpha


def _join(rgb, alpha):
    if alpha is not None:
        rgb = rgb.convert("RGB")
        rgb.putalpha(alpha)
    return rgb


def recolor(image, key):
    if not key:
        return image
    rgb, alpha = _split(image)
    gray = ImageOps.grayscale(rgb)
    if key == "gray":
        rgb = gray.convert("RGB")
    elif key == "sepia":
        rgb = ImageOps.colorize(gray, black=(40, 26, 13), mid=(160, 120, 80), white=(255, 240, 210))
    elif key == "washout":
        rgb = Image.blend(rgb, Image.new("RGB", rgb.size, (255, 255, 255)), 0.55)
    elif key.startswith("bw"):
        level = {"bw25": 64, "bw50": 128, "bw75": 192}[key]
        rgb = gray.point(lambda v: 255 if v >= level else 0).convert("RGB")
    else:
        shade, name = key.split("-", 1)
        color = next((c for k, _, c in TINTS if k == name), (128, 128, 128))
        if shade == "dark":
            rgb = ImageOps.colorize(gray, black=(0, 0, 0), mid=color, white=tuple(min(255, c + 90) for c in color))
        else:
            rgb = ImageOps.colorize(gray, black=color, white=(255, 255, 255))
    return _join(rgb, alpha)


def _mix(original, changed, strength):
    """強度 0～100:和原圖混合的比例。"""
    return Image.blend(original, changed, max(0.0, min(1.0, strength / 100)))


def effect(image, key, strength=DEFAULT_STRENGTH):
    if not key:
        return image
    rgb, alpha = _split(image)
    side = max(rgb.size)
    scale = side / 800           # 模糊半徑、馬賽克大小依圖片大小調整,縮圖和原圖看起來一樣
    s = max(0.0, min(100.0, strength)) / 100
    if key == "sketch":
        gray = ImageOps.grayscale(rgb)
        blurred = ImageOps.invert(gray).filter(ImageFilter.GaussianBlur(max(1.0, (2 + 10 * s) * scale)))
        # 顏色減淡:原圖 ÷ (反相模糊的反相),亮部變白、線條留下
        import numpy

        base = numpy.asarray(gray, dtype=numpy.float32)
        mask = numpy.asarray(blurred, dtype=numpy.float32)
        out = numpy.clip(base * 255 / (256 - mask), 0, 255).astype(numpy.uint8)
        result = ImageOps.autocontrast(Image.fromarray(out), cutoff=1).convert("RGB")
    elif key == "lines":
        gray = ImageOps.grayscale(rgb).filter(ImageFilter.GaussianBlur(max(0.5, scale)))
        edges = ImageOps.invert(gray.filter(ImageFilter.FIND_EDGES))
        result = ImageOps.autocontrast(edges, cutoff=2 + 10 * s).convert("RGB")
    elif key == "blur":
        return _join(rgb.filter(ImageFilter.GaussianBlur(max(0.5, 20 * s * scale))), alpha)
    elif key == "glow":
        soft = rgb.filter(ImageFilter.GaussianBlur(max(1.0, 12 * scale)))
        result = ImageChops.screen(rgb, soft)
    elif key == "mosaic":
        block = max(2, round((4 + 40 * s) * scale))
        small = rgb.resize((max(1, rgb.width // block), max(1, rgb.height // block)), Image.Resampling.BILINEAR)
        return _join(small.resize(rgb.size, Image.Resampling.NEAREST), alpha)
    elif key == "poster":
        return _join(ImageOps.posterize(rgb, max(1, round(6 - 5 * s))), alpha)
    elif key == "paint":
        # 縮小一半再抹平色塊、放大回來:筆觸明顯,大圖也不會太慢
        half = rgb.reduce(2) if min(rgb.size) >= 8 else rgb
        size = max(3, int((3 + 6 * s) * scale / 2) | 1)
        daubed = half.filter(ImageFilter.MedianFilter(size)).filter(ImageFilter.ModeFilter(3))
        result = daubed.resize(rgb.size, Image.Resampling.BICUBIC).filter(ImageFilter.EDGE_ENHANCE)
    elif key == "emboss":
        result = rgb.filter(ImageFilter.EMBOSS)
    elif key == "cartoon":
        flat = ImageOps.posterize(rgb.filter(ImageFilter.MedianFilter(max(3, int(3 * scale) | 1))), 3)
        gray = ImageOps.grayscale(rgb).filter(ImageFilter.GaussianBlur(max(0.5, scale)))
        edges = gray.filter(ImageFilter.FIND_EDGES).point(lambda v: 0 if v > 28 else 255)
        result = ImageChops.multiply(flat, edges.convert("RGB"))
    elif key == "grain":
        noise = Image.effect_noise(rgb.size, 10 + 50 * s)
        return _join(ImageChops.overlay(rgb, noise.convert("RGB")), alpha)
    elif key == "solarize":
        return _join(ImageOps.solarize(rgb, round(255 - 200 * s)), alpha)
    elif key == "negative":
        result = ImageOps.invert(rgb)
    else:
        return image
    return _join(_mix(rgb, result, 30 + 70 * s), alpha)
