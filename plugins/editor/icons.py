"""PDF 編輯器工具列的圖示:程式自己畫,不需要圖檔。

以 24×24 的格子設計,先放大 4 倍畫好再縮小(邊緣才平滑),風格和 images/ui_*.png 一樣是圓頭的粗線條。
"""

import math
import os

import pygame
from PIL import Image, ImageDraw, ImageFont

GRID = 24
SCALE = 4
STROKE = 2.0
_cache = {}
_FONTS = [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf"]


def _pt(points):
    return [(x * SCALE, y * SCALE) for x, y in points]


def _line(draw, points, width=STROKE, closed=False):
    points = _pt(points)
    if closed:
        points = points + points[:1]
    draw.line(points, fill=255, width=round(width * SCALE), joint="curve")
    radius = width * SCALE / 2
    for x, y in points:                 # 圓頭、圓角
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)


def _fill(draw, points):
    draw.polygon(_pt(points), fill=255)


def _box(draw, box, width=STROKE, radius=1.5, fill=False):
    x0, y0, x1, y1 = (v * SCALE for v in box)
    if fill:
        draw.rounded_rectangle((x0, y0, x1, y1), radius * SCALE, fill=255)
    else:
        draw.rounded_rectangle((x0, y0, x1, y1), radius * SCALE, outline=255, width=round(width * SCALE))


def _letter(draw, text, center, size):
    path = next((p for p in _FONTS if os.path.exists(p)), None)
    font = ImageFont.truetype(path, round(size * SCALE)) if path else ImageFont.load_default()
    draw.text((center[0] * SCALE, center[1] * SCALE), text, fill=255, font=font, anchor="mm")


def _wave(x0, x1, y, height, turns, steps=40):
    return [(x0 + (x1 - x0) * i / steps, y - height * math.sin(math.pi * 2 * turns * i / steps))
            for i in range(steps + 1)]


def _select(draw):
    _line(draw, [(6, 3.5), (6, 19), (10, 15.4), (12.8, 21), (15.2, 19.8), (12.5, 14.4), (17.6, 14)], 1.8, closed=True)


def _highlight(draw):
    _line(draw, [(13.5, 3.5), (20.5, 10.5), (12.5, 18.5), (5.5, 11.5)], 1.8, closed=True)
    _fill(draw, [(5.5, 13.5), (10.5, 18.5), (7.5, 21), (3, 21), (3, 16.5)])
    _line(draw, [(13, 21), (21, 21)], 2.2)


def _underline(draw):
    _letter(draw, "U", (12, 9.5), 18)
    _line(draw, [(5, 20.5), (19, 20.5)])


def _strike(draw):
    _letter(draw, "S", (12, 11.5), 19)
    _line(draw, [(4, 12.5), (20, 12.5)])


def _textbox(draw):
    _box(draw, (3, 4, 21, 20), 1.8, 2)
    _line(draw, [(8, 8.5), (16, 8.5)])
    _line(draw, [(12, 8.5), (12, 16)])


def _replace(draw):
    _letter(draw, "A", (9, 12), 18)
    _line(draw, [(18.5, 5), (18.5, 19)], 1.8)
    _line(draw, [(16.2, 4.5), (20.8, 4.5)], 1.6)
    _line(draw, [(16.2, 19.5), (20.8, 19.5)], 1.6)


def _redact(draw):
    _line(draw, [(4, 5.5), (20, 5.5)])
    _box(draw, (3, 9.5, 21, 14.5), radius=1, fill=True)
    _line(draw, [(4, 18.5), (14, 18.5)])


def _note(draw):
    _line(draw, [(4, 4), (20, 4), (20, 14), (14, 20), (4, 20)], 1.8, closed=True)
    _line(draw, [(14, 20), (14, 14), (20, 14)], 1.6)
    _line(draw, [(7.5, 9), (16.5, 9)], 1.6)


def _straight(draw):
    _line(draw, [(4.5, 19.5), (19.5, 4.5)])


def _arrow(draw):
    _line(draw, [(4.5, 19.5), (19, 5)])
    _line(draw, [(11, 5), (19, 5), (19, 13)])


def _rect(draw):
    _box(draw, (3.5, 5.5, 20.5, 18.5), radius=1.5)


def _ellipse(draw):
    draw.ellipse(tuple(v * SCALE for v in (3, 5, 21, 19)), outline=255, width=round(STROKE * SCALE))


def _ink(draw):
    _line(draw, _wave(3.5, 20.5, 12.5, 4, 1.25))


def _image(draw):
    _box(draw, (3, 4, 21, 20), 1.8, 2)
    draw.ellipse(tuple(v * SCALE for v in (7, 7.5, 10.6, 11.1)), fill=255)
    _fill(draw, [(4, 19.2), (9.8, 12.8), (13.6, 16.4), (16, 14), (20.2, 18.2), (20.2, 19.2)])


def _signature(draw):
    _line(draw, [(3, 20.5), (21, 20.5)], 1.6)
    _line(draw, _wave(3.5, 12.5, 14, 3.5, 1.5), 1.6)
    _line(draw, [(14, 16.5), (20.5, 10)], 2.4)
    _fill(draw, [(12.4, 18.2), (13.2, 15.6), (14.9, 17.3)])


def _link(draw):
    # 兩個斜放的環扣在一起:先畫粗線當外框,再用細線挖空中間
    for (x0, y0), (x1, y1) in (((5.5, 17.5), (10.5, 12.5)), ((13.5, 11.5), (18.5, 6.5))):
        draw.line(_pt([(x0, y0), (x1, y1)]), fill=255, width=round(7.5 * SCALE))
        for x, y in ((x0, y0), (x1, y1)):
            r = 3.75 * SCALE
            draw.ellipse((x * SCALE - r, y * SCALE - r, x * SCALE + r, y * SCALE + r), fill=255)
        draw.line(_pt([(x0, y0), (x1, y1)]), fill=0, width=round(3.7 * SCALE))
        for x, y in ((x0, y0), (x1, y1)):
            r = 1.85 * SCALE
            draw.ellipse((x * SCALE - r, y * SCALE - r, x * SCALE + r, y * SCALE + r), fill=0)
    _line(draw, [(9.5, 14.5), (14.5, 9.5)], 2.0)


def _stamp(draw):
    # 蓋章用的印章:握把、頸部、印面,下面一條印出來的線
    draw.ellipse(tuple(v * SCALE for v in (8.5, 2.5, 15.5, 9.5)), outline=255, width=round(1.8 * SCALE))
    _line(draw, [(10.5, 9.5), (10, 13), (14, 13), (13.5, 9.5)], 1.8)
    _box(draw, (4, 13, 20, 17.5), 1.8, 1.2)
    _line(draw, [(4, 21), (20, 21)], 2.0)


DRAW = {"select": _select, "link": _link, "stamp": _stamp, "highlight": _highlight, "underline": _underline, "strike": _strike,
        "textbox": _textbox, "replace": _replace, "redact": _redact, "note": _note, "line": _straight,
        "arrow": _arrow, "rect": _rect, "ellipse": _ellipse, "ink": _ink, "image": _image, "signature": _signature}


def icon(name, size, color):
    """name 的圖示,邊長 size 像素、顏色 color;沒有這個圖示時回傳 None。"""
    key = (name, size, tuple(color))
    surface = _cache.get(key)
    if surface is None:
        painter = DRAW.get(name)
        if painter is None:
            return None
        mask = Image.new("L", (GRID * SCALE, GRID * SCALE), 0)
        painter(ImageDraw.Draw(mask))
        mask = mask.resize((size, size), Image.Resampling.LANCZOS)
        image = Image.new("RGBA", (size, size), tuple(color) + (0,))
        image.putalpha(mask)
        surface = pygame.image.frombytes(image.tobytes(), image.size, "RGBA")
        _cache[key] = surface
    return surface
