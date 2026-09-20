"""在編輯器畫面上畫註解、選取框和控制點。

沒被改過的原註解由 PDFium 畫(和其他閱讀器看到的一樣),這裡只畫新增或改過的註解;
文字框用和儲存時相同的排版,畫面上看到的換行位置就是存檔後的樣子。
"""

import math
from collections import OrderedDict

import pygame
from PIL import Image, ImageDraw

from . import annots, fonts, geometry, pdfwrite

CACHE_ITEMS = 128
HANDLE = 9


class SurfaceCache:
    def __init__(self):
        self._items = OrderedDict()

    def get(self, key):
        item = self._items.get(key)
        if item is not None:
            self._items.move_to_end(key)
        return item

    def put(self, key, surface):
        self._items[key] = surface
        while len(self._items) > CACHE_ITEMS:
            self._items.popitem(last=False)

    def clear(self):
        self._items.clear()


class Mapper:
    """頁面座標 ↔ 畫面像素。rect 是這一頁在畫面上的範圍。"""

    def __init__(self, ref, rect, scale):
        self.size, self.rotation = ref.size, ref.rotation
        self.rect, self.scale = rect, scale

    def point(self, point):
        s, t = geometry.shown(point, self.size, self.rotation)
        return self.rect.x + s * self.scale, self.rect.y + t * self.scale

    def box(self, box):
        x0, y0, x1, y1 = geometry.shown_box(box, self.size, self.rotation)
        left, top = self.rect.x + x0 * self.scale, self.rect.y + y0 * self.scale
        return pygame.Rect(math.floor(left), math.floor(top), max(1, math.ceil(self.rect.x + x1 * self.scale) - math.floor(left)),
                           max(1, math.ceil(self.rect.y + y1 * self.scale) - math.floor(top)))

    def to_page(self, pos):
        return geometry.unshown(((pos[0] - self.rect.x) / self.scale, (pos[1] - self.rect.y) / self.scale),
                                self.size, self.rotation)


def _tint(color, opacity):
    """螢光筆:和白色依透明度混合,再用「色彩增值」疊上去,底下的字不會被蓋掉。"""
    return tuple(round(255 - (255 - c) * opacity) for c in color)


def _thick_line(surface, color, a, b, width):
    width = max(1, round(width))
    pygame.draw.line(surface, color, a, b, width)
    if width >= 3:
        for point in (a, b):
            pygame.draw.circle(surface, color, (round(point[0]), round(point[1])), width / 2)


def _layer(screen, mapper, annot):
    """畫在一張透明圖層上,最後依透明度貼到畫面;圖層只取看得到的範圍,放很大時也不會太耗記憶體。"""
    area = mapper.box(annots.bounds(annot)).inflate(4, 4).clip(screen.get_clip())
    if area.width <= 0 or area.height <= 0:
        return None, None
    return pygame.Surface(area.size, pygame.SRCALPHA), area


def _textbox_surface(mapper, annot, cache):
    key = (annot, round(mapper.scale, 4), mapper.rotation)
    surface = cache.get(key)
    if surface is not None:
        return surface
    scale = mapper.scale
    x0, y0, x1, y1 = annot.box
    width, height = max(1, math.ceil((x1 - x0) * scale)), max(1, math.ceil((y1 - y0) * scale))
    if width * height > 40_000_000:
        return None
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = tuple(annot.color) + (255,)
    if annot.width:
        line = max(1, round(annot.width * scale))
        draw.rectangle([line / 2, line / 2, width - line / 2 - 1, height - line / 2 - 1], outline=color, width=line)
    _, result = pdfwrite.text_layout(annot)
    if result is not None:
        inset = (annots.TEXT_PAD + annot.width) * scale
        fonts.draw_layout(draw, result, (inset, inset), scale, color)
    surface = pygame.image.frombytes(image.tobytes(), image.size, "RGBA")
    if mapper.rotation:
        surface = pygame.transform.rotate(surface, -mapper.rotation)
    cache.put(key, surface)
    return surface


def draw_annot(screen, mapper, annot, cache):
    kind = annot.kind
    scale = mapper.scale
    color = tuple(annot.color)
    if kind == "highlight":
        tint = _tint(color, annot.opacity)
        for rect in annot.rects:
            area = mapper.box(rect).clip(screen.get_clip())
            if area.width > 0 and area.height > 0:
                patch = pygame.Surface(area.size)
                patch.fill(tint)
                screen.blit(patch, area.topleft, special_flags=pygame.BLEND_RGB_MULT)
        return
    if kind == "textbox":
        surface = _textbox_surface(mapper, annot, cache)
        if surface is not None:
            if annot.opacity < 0.999:
                surface = surface.copy()
                surface.set_alpha(round(255 * annot.opacity))
            screen.blit(surface, mapper.box(annot.box).topleft)
        return
    if kind == "note":
        area = mapper.box(annot.box)
        radius = max(1, area.width // 6)
        pygame.draw.rect(screen, color, area, border_radius=radius)
        pygame.draw.rect(screen, (70, 70, 70), area, max(1, round(scale * 0.8)), border_radius=radius)
        for ratio, length in ((0.32, 0.5), (0.52, 0.5), (0.72, 0.35)):
            y = area.y + area.height * ratio
            pygame.draw.line(screen, (60, 60, 60), (area.x + area.width * 0.25, y),
                             (area.x + area.width * (0.25 + length), y), max(1, round(scale)))
        return
    layer, area = _layer(screen, mapper, annot)
    if layer is None:
        return

    def local(point):
        x, y = mapper.point(point)
        return x - area.x, y - area.y

    paint = color + (255,)
    line_width = annot.width * scale
    if kind in ("underline", "strike"):
        for x0, y0, x1, y1 in annot.rects:
            thickness = max(0.6, (y1 - y0) * 0.075)
            y = y1 - thickness / 2 if kind == "underline" else y0 + (y1 - y0) * 0.55
            _thick_line(layer, paint, local((x0, y)), local((x1, y)), max(1, thickness * scale))
    elif kind in ("line", "arrow"):
        a, b = local(annot.points[0]), local(annot.points[1])
        _thick_line(layer, paint, a, b, line_width)
        if kind == "arrow":
            angle = math.atan2(b[1] - a[1], b[0] - a[0])
            size = annots.arrow_size(annot.width) * scale
            for side in (-pdfwrite.ARROW_ANGLE, pdfwrite.ARROW_ANGLE):
                end = (b[0] - size * math.cos(angle + side), b[1] - size * math.sin(angle + side))
                _thick_line(layer, paint, b, end, line_width)
    elif kind in ("rect", "ellipse"):
        box = mapper.box(annot.box).move(-area.x, -area.y)
        width = 0 if annot.fill else max(1, round(line_width))
        if kind == "rect":
            if width:
                box = box.inflate(width // 2 * 2 - width + 1, width // 2 * 2 - width + 1)
            pygame.draw.rect(layer, paint, box, width)
        else:
            pygame.draw.ellipse(layer, paint, box, width)
    elif kind == "ink":
        for stroke in annot.points:
            points = [local(p) for p in stroke]
            if len(points) == 1:
                pygame.draw.circle(layer, paint, points[0], max(1, line_width / 2))
                continue
            pygame.draw.lines(layer, paint, False, points, max(1, round(line_width)))
            if line_width >= 3:
                for point in points:
                    pygame.draw.circle(layer, paint, point, line_width / 2)
    if annot.opacity < 0.999:
        layer.set_alpha(round(255 * annot.opacity))
    screen.blit(layer, area.topleft)


def draw_markup_preview(screen, mapper, kind, rects, color):
    for rect in rects:
        area = mapper.box(rect).clip(screen.get_clip())
        if area.width > 0 and area.height > 0:
            patch = pygame.Surface(area.size, pygame.SRCALPHA)
            patch.fill(tuple(color) + (90,))
            screen.blit(patch, area.topleft)


def selection_rect(mapper, annot):
    return mapper.box(annots.bounds(annot)).inflate(8, 8)


def draw_selection(screen, mapper, annot, accent, handles=True):
    rect = selection_rect(mapper, annot)
    pygame.draw.rect(screen, accent, rect, 1)
    if not handles:
        return
    for _, point in annots.handles(annot):
        box = pygame.Rect(0, 0, HANDLE, HANDLE)
        box.center = tuple(round(v) for v in mapper.point(point))
        pygame.draw.rect(screen, (255, 255, 255), box)
        pygame.draw.rect(screen, accent, box, 1)


def handle_at(mapper, annot, pos):
    for name, point in annots.handles(annot):
        x, y = mapper.point(point)
        if abs(pos[0] - x) <= HANDLE and abs(pos[1] - y) <= HANDLE:
            return name
    return None
