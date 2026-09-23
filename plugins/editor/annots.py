"""註解的資料:種類、外觀設定、點選判斷、移動與改大小,以及讀取 PDF 原本就有的註解。

座標都是頁面座標(見 geometry)。註解是不可變的資料,修改時產生新的一份,放回頁面後由頁面清單的復原紀錄管理。
"""

import itertools
import re
from dataclasses import dataclass, replace

from . import geometry

MARKUP = ("highlight", "underline", "strike")
SHAPES = ("line", "arrow", "rect", "ellipse", "ink")
IMAGES = ("image", "signature")     # 插入的圖片、簽名:存成圖章註解,外觀就是那張圖
TEXTS = ("textbox", "replace")      # 可以打字、排版的種類;改字是「蓋住原字 + 文字框」
NOTE_SIZE = 20.0
TEXT_PAD = 4.0
LINE_HEIGHT = 1.3           # 行高是字型大小的幾倍
BASELINE = 1.0              # 每行的基線離行頂多少(字型大小的倍數)
MIN_TEXT_W = 24.0
MIN_SHAPE = 4.0
DEFAULT_COLORS = {     # 和顏色選單裡的標準色一致
    "highlight": (255, 255, 0), "underline": (0, 112, 192), "strike": (255, 0, 0), "textbox": (0, 0, 0),
    "note": (255, 192, 0), "line": (255, 0, 0), "arrow": (255, 0, 0), "rect": (255, 0, 0),
    "ellipse": (255, 0, 0), "ink": (0, 112, 192), "other": (150, 150, 150),
    "image": (0, 0, 0), "signature": (0, 0, 0), "replace": (0, 0, 0), "redact": (0, 0, 0),
}
_SUBTYPES = {"Highlight": "highlight", "Underline": "underline", "StrikeOut": "strike", "FreeText": "textbox",
             "Text": "note", "Line": "line", "Square": "rect", "Circle": "ellipse", "Ink": "ink"}
SKIPPED_SUBTYPES = ("Link", "Widget", "Popup")     # 連結、表單欄位、註解的彈出框:不當成註解編輯,儲存時照原樣保留
_ids = itertools.count(1)


@dataclass(frozen=True)
class Annot:
    kind: str                       # MARKUP、textbox、note、SHAPES,或 other(不支援修改的原註解)
    uid: int = 0
    color: tuple = (255, 214, 0)
    opacity: float = 1.0
    width: float = 2.0              # 線條粗細;文字框是外框粗細(0 表示沒有外框)
    rects: tuple = ()               # 文字標記:每一行的範圍 (x0, y0, x1, y1);改字:要蓋掉(刪掉)的原字範圍
    box: tuple = (0.0, 0.0, 0.0, 0.0)   # 文字框、便利貼、方框、圓形、其他:範圍
    points: tuple = ()              # 直線與箭頭:(起點, 終點);手繪:(筆畫, ...),每一筆是點的 tuple
    text: str = ""
    font: str = ""                  # 字型代號(fonts.FontFace.id)
    font_size: float = 12.0
    background: tuple = ()          # 文字框的背景、方框與圓形的填滿顏色;空的表示沒有底色。改字是蓋住原字的顏色
    image: bytes = b""              # 圖片、簽名:PNG 檔的內容
    align: str = ""                 # 改字整段編輯:left、center、right、justify
    line_height: float = 0.0        # 改字整段編輯:原檔的行距(相鄰兩行底線的距離);0 表示用預設
    offsets: tuple = (0.0, 0.0)     # 改字整段編輯:第一行、其他行的縮排
    fallback: str = ""              # 主字型(原檔字型只有用到的字)沒有的字,先用這個字型補
    latin: str = ""                 # 英數字、符號用的字型(像 Word 一樣中文和英文分開設定);空的表示和主字型相同
    latin_fallback: str = ""        # 英數字的原檔字型沒有的字,用這個補
    wrap: bool = False              # 改字:True 是整段編輯(自動換行),False 是只改幾個字(框跟著字變寬)
    pixels: tuple = ()              # 圖片的像素寬高;改大小時維持這個比例
    origin: int = -1                # 原檔這一頁 /Annots 的第幾個;-1 是在編輯器裡新增的
    subtype: str = ""               # 原檔的註解類型名稱


def create(kind, **fields):
    fields.setdefault("color", DEFAULT_COLORS.get(kind, (255, 214, 0)))
    return Annot(kind, uid=next(_ids), **fields)


def arrow_size(width):
    return max(7.0, width * 3.5)


def raw_box(annot):
    """不含線條粗細的範圍;改字是文字框的範圍(不含蓋住的原字)。"""
    if annot.kind in MARKUP or annot.kind == "redact":
        return geometry.bbox([p for x0, y0, x1, y1 in annot.rects for p in ((x0, y0), (x1, y1))])
    if annot.kind in ("line", "arrow"):
        return geometry.bbox(annot.points)
    if annot.kind == "ink":
        return geometry.bbox([p for stroke in annot.points for p in stroke])
    return annot.box


def bounds(annot):
    """畫出來會佔到的範圍(含線條粗細、箭頭);改字包含蓋住原字的範圍。"""
    x0, y0, x1, y1 = raw_box(annot)
    if annot.kind == "replace" and annot.rects:
        return geometry.bbox([(x0, y0), (x1, y1)] + [p for a, b, c, d in annot.rects for p in ((a, b), (c, d))])
    if annot.kind == "arrow":
        pad = arrow_size(annot.width) + annot.width
    elif annot.kind in ("line", "ink", "rect", "ellipse") or (annot.kind == "textbox" and annot.width):
        pad = annot.width / 2 + 0.5
    else:
        pad = 0.0
    return x0 - pad, y0 - pad, x1 + pad, y1 + pad


def _inside(box, point, tolerance=0.0):
    x0, y0, x1, y1 = box
    return x0 - tolerance <= point[0] <= x1 + tolerance and y0 - tolerance <= point[1] <= y1 + tolerance


def hit(annot, point, tolerance=3.0):
    if annot.kind == "replace":
        return _inside(annot.box, point, tolerance) or any(_inside(rect, point, tolerance) for rect in annot.rects)
    if annot.kind in MARKUP or annot.kind == "redact":
        return any(_inside(rect, point, tolerance) for rect in annot.rects)
    reach = tolerance + annot.width / 2
    if annot.kind in ("line", "arrow"):
        return geometry.distance_to_segment(point, *annot.points) <= max(reach, 4.0)
    if annot.kind == "ink":
        for stroke in annot.points:
            if len(stroke) == 1 and geometry.distance_to_segment(point, stroke[0], stroke[0]) <= reach:
                return True
            if any(geometry.distance_to_segment(point, a, b) <= reach for a, b in zip(stroke, stroke[1:])):
                return True
        return False
    return _inside(bounds(annot), point, tolerance)


def moved(annot, dx, dy):
    def shift(p):
        return p[0] + dx, p[1] + dy

    changes = {}
    if annot.rects and annot.kind != "replace":     # 改字移動的是新文字,蓋住原字的範圍留在原地
        changes["rects"] = tuple((x0 + dx, y0 + dy, x1 + dx, y1 + dy) for x0, y0, x1, y1 in annot.rects)
    if annot.kind in ("line", "arrow"):
        changes["points"] = tuple(shift(p) for p in annot.points)
    elif annot.kind == "ink":
        changes["points"] = tuple(tuple(shift(p) for p in stroke) for stroke in annot.points)
    x0, y0, x1, y1 = annot.box
    changes["box"] = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
    return replace(annot, **changes)


def handles(annot):
    """可以拖曳改大小的控制點:[(名稱, 位置)]。"""
    if annot.kind in ("line", "arrow"):
        return [("p1", annot.points[0]), ("p2", annot.points[1])]
    x0, y0, x1, y1 = raw_box(annot)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if annot.kind in TEXTS:
        return [("w", (x0, cy)), ("e", (x1, cy))]
    if annot.kind in IMAGES:
        return [("nw", (x0, y0)), ("ne", (x1, y0)), ("se", (x1, y1)), ("sw", (x0, y1))]
    if annot.kind in ("rect", "ellipse", "ink"):
        return [("nw", (x0, y0)), ("n", (cx, y0)), ("ne", (x1, y0)), ("e", (x1, cy)),
                ("se", (x1, y1)), ("s", (cx, y1)), ("sw", (x0, y1)), ("w", (x0, cy))]
    return []


def resized(annot, handle, point):
    px, py = point
    if handle in ("p1", "p2"):
        first, second = annot.points
        return replace(annot, points=((px, py), second) if handle == "p1" else (first, (px, py)))
    x0, y0, x1, y1 = raw_box(annot)
    if annot.kind in IMAGES:
        return replace(annot, box=_keep_ratio(annot, handle, point))
    if annot.kind in TEXTS:
        if handle == "w":
            x0 = min(px, x1 - MIN_TEXT_W)
        else:
            x1 = max(px, x0 + MIN_TEXT_W)
        return replace(annot, box=(x0, annot.box[1], x1, annot.box[3]))
    nx0, ny0, nx1, ny1 = x0, y0, x1, y1
    if "w" in handle:
        nx0 = px
    if "e" in handle:
        nx1 = px
    if "n" in handle:
        ny0 = py
    if "s" in handle:
        ny1 = py
    nx0, nx1 = sorted((nx0, nx1))
    ny0, ny1 = sorted((ny0, ny1))
    nx1, ny1 = max(nx1, nx0 + MIN_SHAPE), max(ny1, ny0 + MIN_SHAPE)
    if annot.kind != "ink":
        return replace(annot, box=(nx0, ny0, nx1, ny1))
    sx = (nx1 - nx0) / (x1 - x0) if x1 > x0 else 1.0
    sy = (ny1 - ny0) / (y1 - y0) if y1 > y0 else 1.0
    strokes = tuple(tuple((nx0 + (x - x0) * sx, ny0 + (y - y0) * sy) for x, y in stroke) for stroke in annot.points)
    return replace(annot, points=strokes)


def _keep_ratio(annot, handle, point):
    """圖片改大小:拖的是角,對面的角固定,寬高維持原本的比例。"""
    x0, y0, x1, y1 = annot.box
    ratio = (y1 - y0) / (x1 - x0) if x1 > x0 else 1.0
    fixed_x = x1 if "w" in handle else x0
    fixed_y = y1 if "n" in handle else y0
    width = max(MIN_SHAPE * 2, abs(point[0] - fixed_x), abs(point[1] - fixed_y) / ratio if ratio else 0.0)
    height = width * ratio
    left = fixed_x - width if "w" in handle else fixed_x
    top = fixed_y - height if "n" in handle else fixed_y
    return left, top, left + width, top + height


def editable(annot):
    return annot.kind != "other"


def styled(annot, **settings):
    """套用顏色、粗細等設定;不適用這種註解的設定會被略過。"""
    allowed = set() if annot.kind in IMAGES else {"color"}
    if annot.kind in MARKUP or annot.kind in SHAPES or annot.kind in IMAGES or annot.kind == "textbox":
        allowed.add("opacity")
    if annot.kind in SHAPES or annot.kind == "textbox":
        allowed.add("width")
    if annot.kind in ("rect", "ellipse", "textbox", "replace"):
        allowed.add("background")
    if annot.kind in TEXTS:
        allowed |= {"font", "font_size"}
    changes = {key: value for key, value in settings.items() if key in allowed}
    return replace(annot, **changes) if changes else annot


# ------------------------------------------------------------ 原檔註解與儲存時的保留判斷

def untouched_origins(ref):
    """沒被改動、也沒被刪除的原註解編號;這些儲存時原封不動,畫面上也直接交給 PDFium 畫。"""
    originals = set(ref.originals)
    return {annot.origin for annot in ref.annots if annot.origin >= 0 and annot in originals}


def hidden_origins(ref):
    """被改動或刪除的原註解編號:畫頁面時要把它們藏起來,改由編輯器畫新的樣子。"""
    if not ref.originals:
        return ()
    kept = untouched_origins(ref)
    return tuple(sorted(annot.origin for annot in ref.originals if annot.origin not in kept))


def _color(value, fallback):
    try:
        numbers = [float(v) for v in value]
    except (TypeError, ValueError):
        return fallback
    if len(numbers) == 1:
        return (round(numbers[0] * 255),) * 3
    if len(numbers) == 3:
        return tuple(round(max(0.0, min(1.0, v)) * 255) for v in numbers)
    if len(numbers) == 4:
        c, m, y, k = numbers
        return tuple(round(255 * (1 - min(1.0, v + k))) for v in (c, m, y))
    return fallback


def _number(value, fallback):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _border_width(obj, fallback):
    border_style = obj.get("/BS")
    if border_style is not None and "/W" in border_style:
        return _number(border_style.W, fallback)
    border = obj.get("/Border")
    if border is not None and len(border) >= 3:
        return _number(border[2], fallback)
    return fallback


def _page_point(m, x, y):
    return geometry.apply(m, (float(x), float(y)))


def parse(obj, index, to_page):
    """把一個 PDF 註解字典轉成 Annot;不需要處理的類型回傳 None。to_page:使用者座標 → 頁面座標的矩陣。"""
    subtype = str(obj.get("/Subtype", ""))[1:]
    if not subtype or subtype in SKIPPED_SUBTYPES:
        return None
    if int(_number(obj.get("/F", 0), 0)) & 2:       # 隱藏的註解:保留但不顯示在編輯器
        return None
    rect = [float(v) for v in obj.Rect] if "/Rect" in obj and len(obj.Rect) == 4 else [0, 0, 0, 0]
    box = geometry.transform_box(to_page, rect)
    kind = _SUBTYPES.get(subtype, "other")
    if subtype == "Stamp" and str(obj.get("/NaizKind", "")) in IMAGES:
        found = _stamp_image(obj)
        if found is not None:
            data, pixels = found
            return Annot(str(obj.NaizKind), uid=next(_ids), box=box, image=data, pixels=pixels, origin=index,
                         subtype=subtype, opacity=_number(obj.get("/CA", 1.0), 1.0), color=DEFAULT_COLORS["image"])
    color = _color(obj.get("/C"), DEFAULT_COLORS.get(kind, (150, 150, 150)))
    common = dict(uid=next(_ids), color=color, opacity=_number(obj.get("/CA", 1.0), 1.0), origin=index,
                  subtype=subtype, text=str(obj.get("/Contents", "")))
    try:
        if kind in MARKUP:
            quads = [float(v) for v in obj.get("/QuadPoints", [])]
            rects = []
            for i in range(0, len(quads) - 7, 8):
                points = [_page_point(to_page, quads[i + j], quads[i + j + 1]) for j in range(0, 8, 2)]
                rects.append(geometry.bbox(points))
            return Annot(kind, rects=tuple(rects) or (box,), **common)
        if kind == "textbox":
            appearance = str(obj.get("/DA", ""))
            size = re.search(r"([\d.]+)\s+Tf", appearance)
            rgb = re.search(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg", appearance)
            common["color"] = _color(rgb.groups(), (20, 20, 20)) if rgb else (20, 20, 20)
            # 文字框的底色:PDF 規格是放在 /C,但我們自己存的檔另外記在 /NaizBG,才不會和文字顏色混在一起
            if "/NaizFont" in obj:
                background = _color(obj.get("/NaizBG"), ()) if "/NaizBG" in obj else ()
            else:
                background = _color(obj.get("/C"), ()) if "/C" in obj else ()
            return Annot(kind, box=box, width=_border_width(obj, 0.0), font=str(obj.get("/NaizFont", "")),
                         background=background,
                         font_size=_number(size.group(1), 12.0) if size else 12.0, **common)
        if kind == "note":
            x0, y0 = box[0], box[1]
            return Annot(kind, box=(x0, y0, x0 + NOTE_SIZE, y0 + NOTE_SIZE), **common)
        if kind == "line":
            values = [float(v) for v in obj.L]
            ends = [str(name) for name in obj.get("/LE", [])]
            kind = "arrow" if any("Arrow" in name for name in ends) else "line"
            points = (_page_point(to_page, values[0], values[1]), _page_point(to_page, values[2], values[3]))
            return Annot(kind, points=points, width=_border_width(obj, 1.0), **common)
        if kind in ("rect", "ellipse"):
            width = _border_width(obj, 1.0)
            inset = width / 2
            if "/RD" in obj and len(obj.RD) == 4:
                inset = max(inset, sum(float(v) for v in obj.RD) / 4 + width / 2)
            x0, y0, x1, y1 = box
            if x1 - x0 > inset * 2 and y1 - y0 > inset * 2:
                box = (x0 + inset, y0 + inset, x1 - inset, y1 - inset)
            background = _color(obj.get("/IC"), ()) if "/IC" in obj else ()
            return Annot(kind, box=box, width=width, background=background, **common)
        if kind == "ink":
            strokes = []
            for stroke in obj.InkList:
                values = [float(v) for v in stroke]
                strokes.append(tuple(_page_point(to_page, values[i], values[i + 1])
                                     for i in range(0, len(values) - 1, 2)))
            strokes = tuple(s for s in strokes if s)
            if strokes:
                return Annot(kind, points=strokes, width=_border_width(obj, 1.0), **common)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        pass
    common["color"] = (150, 150, 150)
    return Annot("other", box=box, **common)


def _stamp_image(obj):
    """自己存的圖片、簽名:從外觀裡取回原本的圖(含透明);讀不到回傳 None。"""
    try:
        import io

        import pikepdf
        from PIL import Image

        form = obj.AP.N
        xobject = next(iter(form.Resources.XObject.values()))
        image = pikepdf.PdfImage(xobject).as_pil_image().convert("RGB")
        if "/SMask" in xobject:
            alpha = pikepdf.PdfImage(xobject.SMask).as_pil_image().convert("L")
            image = image.convert("RGBA")
            image.putalpha(alpha.resize(image.size))
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        return buffer.getvalue(), image.size
    except Exception:
        return None


def read_page(page_obj, size, base_rotation, origin):
    """一頁 PDF 裡可以在編輯器顯示的註解。"""
    items = page_obj.get("/Annots")
    if items is None:
        return ()
    to_page = geometry.user_to_page(size, base_rotation, origin)
    result = []
    for index, obj in enumerate(items):
        try:
            if not hasattr(obj, "keys"):
                continue
            annot = parse(obj, index, to_page)
        except Exception:
            annot = None
        if annot is not None:
            result.append(annot)
    return tuple(result)


LABELS = {"highlight": "螢光筆", "underline": "底線", "strike": "刪除線", "textbox": "文字框", "note": "便利貼",
          "line": "直線", "arrow": "箭頭", "rect": "方框", "ellipse": "圓形", "ink": "手繪", "other": "其他註解",
          "image": "圖片", "signature": "簽名", "replace": "改字", "redact": "塗黑"}
