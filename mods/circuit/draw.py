"""把電路圖畫成圖片(畫面預覽、PNG)或 SVG;標籤的 LaTeX 由 tex 模組排版。"""

import io
import math

from PIL import Image, ImageDraw

from . import model, parts, tex

SOURCES = {"vsource", "isource", "sine", "square", "vcvs", "ccvs", "vccs", "cccs", "battery", "rail"}
DOT_R = 0.065
ARROW_LEN = 0.14
ARROW_HALF = 0.06
DASH = (0.12, 0.08)

# 風格:線條、元件、電源、名稱、數值、底色;lw 是線寬(和格子的比例)
STYLES = {
    "pretty": dict(name="美觀", note="深灰線條，名稱用藍色，數值灰色，最適合筆記",
                   wire=(40, 44, 52), part=(40, 44, 52), source=(40, 44, 52), label=(28, 126, 214),
                   value=(96, 102, 116), bg=(255, 255, 255), lw=0.03),
    "classic": dict(name="黑白", note="全部黑色、線條較細，像教科書，列印最清楚",
                    wire=(0, 0, 0), part=(0, 0, 0), source=(0, 0, 0), label=(0, 0, 0), value=(0, 0, 0),
                    bg=(255, 255, 255), lw=0.022),
    "color": dict(name="彩色", note="元件藍色、電源橘色、導線黑色，一眼分出元件",
                  wire=(30, 30, 30), part=(28, 126, 214), source=(232, 89, 12), label=(30, 30, 30),
                  value=(110, 110, 110), bg=(255, 255, 255), lw=0.03),
    "dark": dict(name="深色", note="深色底、淺色線條，適合放在簡報或深色筆記",
                 wire=(222, 226, 234), part=(222, 226, 234), source=(255, 180, 84), label=(116, 192, 252),
                 value=(160, 168, 184), bg=(30, 33, 40), lw=0.03),
}
STYLE_ORDER = ["pretty", "classic", "color", "dark"]

def color_of(el, style):
    look = STYLES[style]
    if el["kind"] == "wire":
        return look["wire"]
    return look["source"] if el["kind"] in SOURCES else look["part"]


def _measure(text):
    """標籤在畫布上的寬度(單位);給 model 點選、框選標籤用。"""
    return tex.layout(text, 100).width / 100 * model.label_size()


model.text_width = _measure


# ------------------------------------------------------------ PIL 畫圖

class Painter:
    """把畫布座標畫到 PIL 圖片上;scale 是每單位幾個像素,origin 是圖片左上角對應的畫布座標。"""

    def __init__(self, image, scale, origin, style):
        self.image = image
        self.draw = ImageDraw.Draw(image)
        self.scale = scale
        self.ox, self.oy = origin
        self.style = style
        self.width = max(1, round(STYLES[style]["lw"] * model.LINE_SCALE * scale))

    def px(self, point):
        return ((point[0] - self.ox) * self.scale, (self.oy - point[1]) * self.scale)

    def _line(self, points, color):
        points = [self.px(p) for p in points]
        if len(points) < 2:
            return
        self.draw.line(points, fill=color, width=self.width, joint="curve")
        r = self.width / 2
        for x, y in (points[0], points[-1]):
            self.draw.ellipse((x - r, y - r, x + r, y + r), fill=color)

    def _dashed(self, points, color):
        on, off = DASH
        for p, q in zip(points, points[1:]):
            length = math.dist(p, q)
            t = 0.0
            while t < length:
                end = min(length, t + on)
                a = (p[0] + (q[0] - p[0]) * t / length, p[1] + (q[1] - p[1]) * t / length)
                b = (p[0] + (q[0] - p[0]) * end / length, p[1] + (q[1] - p[1]) * end / length)
                self._line([a, b], color)
                t = end + off

    def _arrow(self, tail, tip, color):
        length = math.dist(tail, tip)
        if length < 1e-6:
            return
        ux, uy = (tip[0] - tail[0]) / length, (tip[1] - tail[1]) / length
        head = min(ARROW_LEN, length * 0.8)
        base = (tip[0] - ux * head, tip[1] - uy * head)
        self._line([tail, (tip[0] - ux * head * 0.6, tip[1] - uy * head * 0.6)], color)
        wing = (-uy * ARROW_HALF, ux * ARROW_HALF)
        triangle = [tip, (base[0] + wing[0], base[1] + wing[1]), (base[0] - wing[0], base[1] - wing[1])]
        self.draw.polygon([self.px(p) for p in triangle], fill=color)

    def text(self, point, text, size, color, ha="center", va="center"):
        if not text:
            return
        x, y = self.px(point)
        box = tex.layout(text, size * self.scale)
        left = x - {"left": 0, "center": box.width / 2, "right": box.width}[ha]
        base = tex.baseline(box, y, va)
        for item in box.items:
            if item[0] == "t":
                _, dx, dy, chunk, kind, run_size = item
                self.draw.text((left + dx, base - dy), chunk, font=tex.font(kind, run_size), fill=color, anchor="ls")
            else:
                _, x1, y1, x2, y2, thick = item
                self.draw.line([(left + x1, base - y1), (left + x2, base - y2)], fill=color, width=max(1, round(thick)))

    def shapes(self, shapes, color):
        for shape in shapes:
            kind = shape[0]
            if kind == "L":
                self._line(shape[1], color)
            elif kind == "D":
                self._dashed(shape[1], color)
            elif kind == "P":
                points = [self.px(p) for p in shape[1]]
                if shape[2]:
                    self.draw.polygon(points, fill=color)
                self.draw.line(points + [points[0], points[1]], fill=color, width=self.width, joint="curve")
            elif kind == "C":
                (x, y), r = self.px(shape[1]), shape[2] * self.scale
                fill = STYLES[self.style]["bg"] if shape[3] == "bg" else color if shape[3] else None
                self.draw.ellipse((x - r, y - r, x + r, y + r), fill=fill, outline=color, width=self.width)
            elif kind == "T":
                self.text(shape[1], shape[2], shape[3], color)
            elif kind == "A":
                self._arrow(shape[1], shape[2], color)

    def dot(self, point, color):
        (x, y), r = self.px(point), DOT_R * self.scale
        self.draw.ellipse((x - r, y - r, x + r, y + r), fill=color)


def drawing_order(elements):
    """先畫導線再畫元件,端點、探棒的圈才蓋得住接進來的導線。"""
    return [el for el in elements if el["kind"] == "wire"] + [el for el in elements if el["kind"] != "wire"]


def paint(painter, elements, resistor_style, dots=True):
    """dots:True 照這些元件算交點;也可以直接給要畫的交點清單。"""
    look = STYLES[painter.style]
    for el in drawing_order(elements):
        geo = model.geometry(el, resistor_style)
        painter.shapes(geo["shapes"], color_of(el, painter.style))
    points = model.junctions(elements) if dots is True else dots or []
    for point in points:
        painter.dot(point, look["wire"])
    for el in elements:
        for point, text, ha, va, role in model.geometry(el, resistor_style)["labels"]:
            painter.text(point, text, model.label_size(), look[role], ha, va)


def render_view(elements, style, resistor_style, size, scale, origin, supersample=2, dots=True):
    """畫面預覽:透明底,supersample 倍大畫完再縮小(邊緣平滑)。"""
    width, height = size
    image = Image.new("RGBA", (max(1, width) * supersample, max(1, height) * supersample), (0, 0, 0, 0))
    paint(Painter(image, scale * supersample, origin, style), elements, resistor_style, dots)
    return image.reduce(supersample) if supersample > 1 else image


MARGIN = 0.35


def export_bounds(elements):
    box = model.all_bounds(elements)
    if box is None:
        return None
    return (box[0] - MARGIN, box[1] - MARGIN, box[2] + MARGIN, box[3] + MARGIN)


def render_png(elements, style, resistor_style, scale=200, transparent=False):
    """整張電路圖輸出成 PNG 內容;scale 是每單位(兩格)幾個像素。"""
    box = export_bounds(elements)
    if box is None:
        return None
    supersample = 2
    width = max(1, math.ceil((box[2] - box[0]) * scale))
    height = max(1, math.ceil((box[3] - box[1]) * scale))
    background = (0, 0, 0, 0) if transparent else STYLES[style]["bg"] + (255,)
    image = Image.new("RGBA", (width * supersample, height * supersample), background)
    paint(Painter(image, scale * supersample, (box[0], box[3]), style), elements, resistor_style)
    image = image.reduce(supersample)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def part_icon(kind, resistor_style, size, color):
    """元件清單的小圖示(RGBA 圖片)。"""
    part = parts.PARTS[kind]
    if part["kind"] == "text":
        width, height = size
        supersample = 3
        image = Image.new("RGBA", (width * supersample, height * supersample), (0, 0, 0, 0))
        painter = Painter(image, height * supersample, (-width / height / 2, 0.5), "pretty")
        painter.text((0, 0), "Aa", 0.75, color)
        return image.reduce(supersample)
    if part["kind"] == "multi":
        el = model.new(kind)
    elif part["kind"] in ("box", "loop", "rect"):
        el = model.new(kind, a=[-0.6, 0.4], b=[0.6, -0.4])
    elif kind == "voltage":
        el = model.new(kind, a=[-0.5, 0], b=[0.5, 0])
    else:
        half = max(part.get("half", 0.3), 0.3)
        el = model.new(kind, a=[-half - 0.25, 0], b=[half + 0.25, 0])
    geo = model.geometry(el, resistor_style)
    points = model._shape_points(geo["shapes"]) or [(0, 0)]
    x0, x1 = min(p[0] for p in points), max(p[0] for p in points)
    y0, y1 = min(p[1] for p in points), max(p[1] for p in points)
    width, height = size
    scale = min((width - 4) / max(x1 - x0, 0.3), (height - 4) / max(y1 - y0, 0.3), height * 0.9)
    supersample = 3
    image = Image.new("RGBA", (width * supersample, height * supersample), (0, 0, 0, 0))
    origin = ((x0 + x1) / 2 - width / 2 / scale, (y0 + y1) / 2 + height / 2 / scale)
    painter = Painter(image, scale * supersample, origin, "pretty")
    painter.width = max(1, round(1.6 * supersample))
    painter.shapes(geo["shapes"], color)
    return image.reduce(supersample)


# ------------------------------------------------------------ SVG

def _svg_color(color):
    return "#%02x%02x%02x" % color


def _escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


SVG_FONTS = {"serif": 'font-family="Times New Roman, serif"',
             "italic": 'font-family="Times New Roman, serif" font-style="italic"',
             "symbol": 'font-family="Cambria Math, serif"',
             "cjk": 'font-family="Microsoft JhengHei, sans-serif"'}


def render_svg(elements, style, resistor_style, scale=80):
    box = export_bounds(elements)
    if box is None:
        return None
    look = STYLES[style]
    width, height = (box[2] - box[0]) * scale, (box[3] - box[1]) * scale
    lw = look["lw"] * model.LINE_SCALE * scale
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.1f}" height="{height:.1f}" '
           f'viewBox="0 0 {width:.1f} {height:.1f}">',
           f'<rect width="100%" height="100%" fill="{_svg_color(look["bg"])}"/>']

    def px(p):
        return ((p[0] - box[0]) * scale, (box[3] - p[1]) * scale)

    def points(pts):
        return " ".join(f"{x:.2f},{y:.2f}" for x, y in map(px, pts))

    def text(point, value, size, color, ha="center", va="center"):
        if not value:
            return
        x, y = px(point)
        box = tex.layout(value, size * scale)
        left = x - {"left": 0, "center": box.width / 2, "right": box.width}[ha]
        base = tex.baseline(box, y, va)
        for item in box.items:
            if item[0] == "t":
                _, dx, dy, chunk, kind, run_size = item
                out.append(f'<text x="{left + dx:.2f}" y="{base - dy:.2f}" font-size="{run_size:.2f}" '
                           f'{SVG_FONTS[kind]} fill="{_svg_color(color)}">{_escape(chunk)}</text>')
            else:
                _, x1, y1, x2, y2, thick = item
                out.append(f'<line x1="{left + x1:.2f}" y1="{base - y1:.2f}" x2="{left + x2:.2f}" '
                           f'y2="{base - y2:.2f}" stroke="{_svg_color(color)}" stroke-width="{thick:.2f}"/>')

    stroke = f'stroke-width="{lw:.2f}" stroke-linecap="round" stroke-linejoin="round"'
    for el in drawing_order(elements):
        color = _svg_color(color_of(el, style))
        for shape in model.geometry(el, resistor_style)["shapes"]:
            kind = shape[0]
            if kind in ("L", "D"):
                dash = (f' stroke-dasharray="{DASH[0] * scale:.1f} {DASH[1] * scale:.1f}"' if kind == "D" else "")
                out.append(f'<polyline points="{points(shape[1])}" fill="none" stroke="{color}" {stroke}{dash}/>')
            elif kind == "P":
                fill = color if shape[2] else "none"
                out.append(f'<polygon points="{points(shape[1])}" fill="{fill}" stroke="{color}" {stroke}/>')
            elif kind == "C":
                (x, y), r = px(shape[1]), shape[2] * scale
                fill = _svg_color(look["bg"]) if shape[3] == "bg" else color if shape[3] else "none"
                out.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}" fill="{fill}" stroke="{color}" {stroke}/>')
            elif kind == "T":
                text(shape[1], shape[2], shape[3], color_of(el, style))
            elif kind == "A":
                tail, tip = shape[1], shape[2]
                length = math.dist(tail, tip) or 1
                ux, uy = (tip[0] - tail[0]) / length, (tip[1] - tail[1]) / length
                head = min(ARROW_LEN, length * 0.8)
                base = (tip[0] - ux * head, tip[1] - uy * head)
                end = (tip[0] - ux * head * 0.6, tip[1] - uy * head * 0.6)
                out.append(f'<polyline points="{points([tail, end])}" fill="none" stroke="{color}" {stroke}/>')
                wing = (-uy * ARROW_HALF, ux * ARROW_HALF)
                tri = [tip, (base[0] + wing[0], base[1] + wing[1]), (base[0] - wing[0], base[1] - wing[1])]
                out.append(f'<polygon points="{points(tri)}" fill="{color}"/>')
    for point in model.junctions(elements):
        x, y = px(point)
        out.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{DOT_R * scale:.2f}" fill="{_svg_color(look["wire"])}"/>')
    for el in elements:
        for point, value, ha, va, role in model.geometry(el, resistor_style)["labels"]:
            text(point, value, model.label_size(), look[role], ha, va)
    out.append("</svg>")
    return "\n".join(out)
