"""儲存註解:產生標準的 PDF 註解與外觀(/AP),嵌入文字框用到的字型,或把註解合併到頁面內容。

外觀由程式自己畫,不依賴閱讀器,所以在不同閱讀器看起來都一樣。
文字框的字型只嵌入用到的字(子集),可變字型固定成一般字重。
"""

import decimal
import hashlib
import io
import math
import time
import uuid
import zlib

from dataclasses import replace

import pikepdf

from . import annots, fonts, geometry, redact

KAPPA = 0.5523
ARROW_ANGLE = math.radians(28)
_WEIGHT_WORDS = {"Thin", "Hairline", "ExtraLight", "UltraLight", "Light", "Regular", "Book", "Medium", "SemiBold",
                 "DemiBold", "Bold", "ExtraBold", "UltraBold", "Black", "Heavy"}


def _num(value):
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _rgb(color):
    return [round(c / 255, 4) for c in color]


# ------------------------------------------------------------ 字型嵌入

class FontEmbedder:
    """同一份文件裡同一個字型只嵌入一次;先記下用到的字,全部註解寫完後 finish() 才產生字型子集。
    子集保留原本的字形編號,所以寫文字時就能先決定編碼。"""

    def __init__(self, pdf):
        self.pdf = pdf
        self.entries = {}
        self.images = {}            # 同一張圖(例如同一個簽名蓋在很多頁)只存一份
        self.kept_text = 0          # 改字時字型沒把握、只被蓋住沒刪掉的文字段數
        self.kept_images = 0        # 原檔圖片的內容太特殊,沒能照編輯器移動、刪除的張數

    def _entry(self, face):
        entry = self.entries.get(face.id)
        if entry is None:
            from fontTools.ttLib import TTFont

            font = TTFont(str(face.path), fontNumber=face.index, lazy=True)
            try:
                cmap = font.getBestCmap() or {}
                order = {name: gid for gid, name in enumerate(font.getGlyphOrder())}
            finally:
                font.close()
            entry = self.entries[face.id] = {
                "face": face, "cmap": cmap, "order": order, "gids": {},
                "ref": self.pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font)),
            }
        return entry

    def encode(self, face, text):
        """回傳 (字型物件, 文字的編碼);沒有的字用 0 號字形(通常是方框)。"""
        entry = self._entry(face)
        codes = bytearray()
        for ch in text:
            gid = entry["order"].get(entry["cmap"].get(ord(ch)), 0)
            entry["gids"][gid] = ch
            codes += gid.to_bytes(2, "big")
        return entry["ref"], bytes(codes)

    def finish(self):
        for entry in self.entries.values():
            self._embed(entry)

    def _program(self, face, chars):
        from fontTools import subset
        from fontTools.ttLib import TTFont

        # 不要重算字形外框:標楷體、細明體的組合字要靠字型裡的微調程式拼筆畫,
        # 依外框重算出來的左側間距是錯的,嵌入後筆畫會錯位(實測)
        font = TTFont(str(face.path), fontNumber=face.index, recalcBBoxes=False)
        options = subset.Options()
        options.retain_gids = True
        options.notdef_outline = True
        options.layout_features = []
        options.name_IDs = [1, 2, 4, 6]
        options.drop_tables += ["DSIG", "GSUB", "GPOS", "GDEF", "BASE", "JSTF", "MATH", "COLR", "CPAL", "SVG "]
        subsetter = subset.Subsetter(options)
        subsetter.populate(unicodes=[ord(ch) for ch in chars])
        subsetter.subset(font)
        postscript = font["name"].getDebugName(6) if "name" in font else ""
        if "fvar" in font:
            # 可變字型的名稱記的是預設字重(例如 Thin),固定成一般字重後改用「家族名-Regular」
            family = font["name"].getDebugName(16) or font["name"].getDebugName(1) or ""
            words = family.split()
            while len(words) > 1 and words[-1] in _WEIGHT_WORDS:
                words.pop()
            postscript = f"{''.join(words)}-Regular"
            from fontTools.varLib import instancer

            font = instancer.instantiateVariableFont(font, fonts.data(face).variation())
        if "CFF2" in font:
            try:
                from fontTools.cffLib.CFF2ToCFF import convertCFF2ToCFF

                convertCFF2ToCFF(font)
            except Exception as exc:
                raise ValueError(f"字型「{face.name}」的格式沒辦法嵌入 PDF，請換一個字型") from exc
        import io

        buffer = io.BytesIO()
        font.save(buffer)
        return font, buffer.getvalue(), postscript

    def _embed(self, entry):
        face = entry["face"]
        font, program, postscript = self._program(face, set(entry["gids"].values()) | {" "})
        units = font["head"].unitsPerEm
        scale = 1000 / units
        cff = "CFF " in font
        order = font.getGlyphOrder()
        widths = pikepdf.Array()
        for gid in sorted(entry["gids"]):
            if gid < len(order):
                widths.extend([gid, pikepdf.Array([round(font["hmtx"][order[gid]][0] * scale)])])
        postscript = "".join(ch for ch in (postscript or "") if ch.isascii() and ch.isalnum() or ch == "-")
        tag = "".join(chr(65 + (int(uuid.uuid4().hex[i], 16) % 26)) for i in range(6))
        base_name = pikepdf.Name(f"/{tag}+{postscript or 'NaizFont'}")
        hhea, head = font["hhea"], font["head"]
        cap = getattr(font["OS/2"], "sCapHeight", 0) if "OS/2" in font else 0
        descriptor = pikepdf.Dictionary(
            Type=pikepdf.Name.FontDescriptor, FontName=base_name, Flags=4,
            FontBBox=[round(v * scale) for v in (head.xMin, head.yMin, head.xMax, head.yMax)],
            ItalicAngle=float(font["post"].italicAngle) if "post" in font else 0, Ascent=round(hhea.ascent * scale),
            Descent=round(hhea.descent * scale), CapHeight=round((cap or hhea.ascent * 0.7) * scale), StemV=80)
        stream = pikepdf.Stream(self.pdf, program)
        if cff:
            stream.Subtype = pikepdf.Name.OpenType
            descriptor.FontFile3 = stream
        else:
            descriptor.FontFile2 = stream
        cid_font = pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.CIDFontType0 if cff else pikepdf.Name.CIDFontType2,
            BaseFont=base_name, FontDescriptor=self.pdf.make_indirect(descriptor), W=widths, DW=1000,
            CIDSystemInfo=pikepdf.Dictionary(Registry=pikepdf.String("Adobe"), Ordering=pikepdf.String("Identity"),
                                             Supplement=0))
        if not cff:
            cid_font.CIDToGIDMap = pikepdf.Name.Identity
        ref = entry["ref"]
        ref.Subtype = pikepdf.Name.Type0
        ref.BaseFont = base_name
        ref.Encoding = pikepdf.Name("/Identity-H")
        ref.DescendantFonts = pikepdf.Array([self.pdf.make_indirect(cid_font)])
        ref.ToUnicode = self.pdf.make_indirect(pikepdf.Stream(self.pdf, _to_unicode(entry["gids"])))


def _to_unicode(gids):
    lines = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
             "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
             "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def",
             "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange"]
    items = sorted(gids.items())
    for start in range(0, len(items), 100):
        chunk = items[start:start + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        lines += [f"<{gid:04X}> <{ch.encode('utf-16-be').hex().upper()}>" for gid, ch in chunk]
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(lines).encode("ascii")


# ------------------------------------------------------------ 圖片

def image_xobject(pdf, data, cache):
    """把 PNG 內容變成 PDF 的圖片物件;有透明的部分另外存成遮罩。沒有透明的照片用 JPEG,檔案小很多。"""
    key = hashlib.sha1(data).hexdigest()
    if key in cache:
        return cache[key]
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    image.load()
    alpha = None
    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        if alpha.getextrema()[0] >= 255:
            alpha = None
        image = rgba.convert("RGB")
    else:
        image = image.convert("RGB")
    width, height = image.size
    if alpha is None and _photo_like(image):
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=90)
        stream = pikepdf.Stream(pdf, buffer.getvalue())
        stream.Filter = pikepdf.Name.DCTDecode
    else:
        stream = pikepdf.Stream(pdf, zlib.compress(image.tobytes(), 9))
        stream.Filter = pikepdf.Name.FlateDecode
    stream.Type, stream.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    stream.Width, stream.Height = width, height
    stream.ColorSpace, stream.BitsPerComponent = pikepdf.Name.DeviceRGB, 8
    if alpha is not None:
        mask = pikepdf.Stream(pdf, zlib.compress(alpha.tobytes(), 9))
        mask.Filter = pikepdf.Name.FlateDecode
        mask.Type, mask.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
        mask.Width, mask.Height = width, height
        mask.ColorSpace, mask.BitsPerComponent = pikepdf.Name.DeviceGray, 8
        stream.SMask = pdf.make_indirect(mask)
    cache[key] = pdf.make_indirect(stream)
    return cache[key]


def _photo_like(image):
    """顏色很多的是照片(適合 JPEG);顏色少的截圖、圖示用無損壓縮才不會糊。"""
    small = image.copy()
    small.thumbnail((200, 200))
    return len(small.getcolors(200 * 200) or ()) > 4000


# ------------------------------------------------------------ 註解外觀

class _Canvas:
    def __init__(self):
        self.parts = []

    def add(self, *items):
        self.parts.append(" ".join(_num(v) if isinstance(v, (int, float)) else str(v) for v in items))

    def data(self):
        return "\n".join(self.parts).encode("latin-1")


def _state(pdf, opacity, multiply=False):
    if opacity >= 0.999 and not multiply:
        return None
    state = pikepdf.Dictionary(Type=pikepdf.Name.ExtGState, CA=round(opacity, 3), ca=round(opacity, 3))
    if multiply:
        state.BM = pikepdf.Name.Multiply
    return pdf.make_indirect(state)


def _ellipse(canvas, x0, y0, x1, y1):
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    canvas.add(cx + rx, cy, "m")
    canvas.add(cx + rx, cy + KAPPA * ry, cx + KAPPA * rx, cy + ry, cx, cy + ry, "c")
    canvas.add(cx - KAPPA * rx, cy + ry, cx - rx, cy + KAPPA * ry, cx - rx, cy, "c")
    canvas.add(cx - rx, cy - KAPPA * ry, cx - KAPPA * rx, cy - ry, cx, cy - ry, "c")
    canvas.add(cx + KAPPA * rx, cy - ry, cx + rx, cy - KAPPA * ry, cx + rx, cy, "c")
    canvas.add("h")


def text_layout(annot):
    """文字框的排版(畫面預覽和儲存共用):回傳 (實際使用的字型, 排版結果)。"""
    face = fonts.CATALOG.resolve(annot.font)
    if face is None:
        return None, None
    width = max(1.0, annot.box[2] - annot.box[0] - annots.TEXT_PAD * 2 - annot.width * 2)
    fallback = [fonts.CATALOG.get(annot.fallback) if annot.fallback else None, fonts.CATALOG.fallback()]
    latin = [fonts.CATALOG.get(annot.latin), fonts.CATALOG.get(annot.latin_fallback) if annot.latin_fallback else None] \
        if annot.latin else None
    return face, fonts.layout(annot.text, face, annot.font_size, width, fallback, annot.align, annot.offsets,
                              annot.line_height, latin)


def textbox_height(annot, result):
    return result.height + annots.TEXT_PAD * 2 + annot.width * 2


def cover_areas(annot):
    """改字要塗上底色的範圍:原字的範圍,加上新文字實際佔的範圍(新字比較長時,底下原本的東西也要蓋住)。
    只塗到新字的寬度為止,不塗整個文字框,旁邊的字才不會被多蓋掉一塊。"""
    areas = list(annot.rects)
    if not annot.text.strip():
        return areas
    _, result = text_layout(replace(annot, width=0.0))
    if result is None:
        return areas
    left, top = annot.box[0] + annots.TEXT_PAD, annot.box[1] + annots.TEXT_PAD
    for row, line in enumerate(result.lines):
        if line.width > 0:
            areas.append((left, top + row * result.line_height, left + line.width,
                          top + (row + 1) * result.line_height))
    return areas


def _appearance(pdf, annot, embedder):
    """回傳 (外觀內容, 資源, 外觀在頁面上的範圍, 額外的註解欄位)。"""
    x0, y0, x1, y1 = annots.bounds(annot)
    to_local = geometry.invert(geometry.local_to_page((x0, y0, x1, y1)))

    def local(point):
        return geometry.apply(to_local, point)

    canvas = _Canvas()
    resources = pikepdf.Dictionary()
    extra = {}
    red, green, blue = _rgb(annot.color)
    kind = annot.kind
    state = _state(pdf, annot.opacity, multiply=kind == "highlight")
    if state is not None:
        resources.ExtGState = pikepdf.Dictionary(GS0=state)
        canvas.add("/GS0 gs")

    if kind in annots.MARKUP:
        for rx0, ry0, rx1, ry1 in annot.rects:
            lx0, ly1 = local((rx0, ry0))
            lx1, ly0 = local((rx1, ry1))
            height = ly1 - ly0
            if kind == "highlight":
                canvas.add(red, green, blue, "rg", lx0, ly0, lx1 - lx0, height, "re f")
            else:
                thickness = max(0.6, height * 0.075)
                y = ly0 + thickness / 2 if kind == "underline" else ly0 + height * 0.45
                canvas.add(red, green, blue, "RG", thickness, "w", lx0, y, "m", lx1, y, "l S")
    elif kind in annots.IMAGES:
        bx0, by1 = local((annot.box[0], annot.box[1]))
        bx1, by0 = local((annot.box[2], annot.box[3]))
        resources.XObject = pikepdf.Dictionary(Im0=image_xobject(pdf, annot.image, embedder.images))
        canvas.add("q", bx1 - bx0, 0, 0, by1 - by0, bx0, by0, "cm /Im0 Do Q")
    elif kind in annots.TEXTS:
        face, result = text_layout(annot)
        if annot.background:
            bx0, by1 = local((annot.box[0], annot.box[1]))
            bx1, by0 = local((annot.box[2], annot.box[3]))
            canvas.add(*_rgb(annot.background), "rg", bx0, by0, bx1 - bx0, by1 - by0, "re f")
        if annot.width:
            bx0, by1 = local((annot.box[0], annot.box[1]))
            bx1, by0 = local((annot.box[2], annot.box[3]))
            half = annot.width / 2
            canvas.add(red, green, blue, "RG", annot.width, "w", bx0 + half, by0 + half,
                       bx1 - bx0 - annot.width, by1 - by0 - annot.width, "re S")
        fonts_used = pikepdf.Dictionary()
        names = {}
        if result is not None:
            left = annot.box[0] + annots.TEXT_PAD + annot.width
            top = annot.box[1] + annots.TEXT_PAD + annot.width
            canvas.add("BT", red, green, blue, "rg")
            for row, line in enumerate(result.lines):
                for run_face, text, x in line.runs:
                    if not text.strip():
                        continue
                    ref, codes = embedder.encode(run_face, text)
                    name = names.get(run_face.id)
                    if name is None:
                        name = names[run_face.id] = f"F{len(names) + 1}"
                        fonts_used[f"/{name}"] = ref
                    lx, ly = local((left + x, top + result.baseline(row)))
                    canvas.add(f"/{name}", annot.font_size, "Tf 1 0 0 1", lx, ly, "Tm", f"<{codes.hex()}> Tj")
            canvas.add("ET")
            extra["NaizFont"] = pikepdf.String(face.id)
        if names:
            resources.Font = fonts_used
        first = f"/{next(iter(names.values()))}" if names else "/Helv"
        extra["DA"] = pikepdf.String(f"{first} {_num(annot.font_size)} Tf {red} {green} {blue} rg")
    elif kind == "note":
        size = annots.NOTE_SIZE
        canvas.add(red, green, blue, "rg 0.25 0.25 0.25 RG 0.8 w 1.5 1.5", size - 3, size - 3, "re B")
        canvas.add("0.2 0.2 0.2 RG 1 w 5 14 m 15 14 l 5 10 m 15 10 l 5 6 m 12 6 l S")
    elif kind in ("line", "arrow"):
        (ax, ay), (bx, by) = local(annot.points[0]), local(annot.points[1])
        canvas.add(red, green, blue, "RG", annot.width, "w 1 J 1 j", ax, ay, "m", bx, by, "l S")
        if kind == "arrow":
            angle = math.atan2(by - ay, bx - ax)
            size = annots.arrow_size(annot.width)
            left = (bx - size * math.cos(angle - ARROW_ANGLE), by - size * math.sin(angle - ARROW_ANGLE))
            right = (bx - size * math.cos(angle + ARROW_ANGLE), by - size * math.sin(angle + ARROW_ANGLE))
            canvas.add(left[0], left[1], "m", bx, by, "l", right[0], right[1], "l S")
    elif kind in ("rect", "ellipse"):
        bx0, by1 = local((annot.box[0], annot.box[1]))
        bx1, by0 = local((annot.box[2], annot.box[3]))
        if annot.background:
            canvas.add(*_rgb(annot.background), "rg")
        if annot.width:
            canvas.add(red, green, blue, "RG", annot.width, "w")
        if kind == "rect":
            canvas.add(bx0, by0, bx1 - bx0, by1 - by0, "re")
        else:
            _ellipse(canvas, bx0, by0, bx1, by1)
        # 有底色就填,有粗細就描邊,兩個都有時一次做完
        canvas.add("B" if annot.background and annot.width else ("f" if annot.background else "S"))
    elif kind == "ink":
        canvas.add(red, green, blue, "RG", annot.width, "w 1 J 1 j")
        for stroke in annot.points:
            points = [local(p) for p in stroke]
            if len(points) == 1:
                points.append(points[0])
            canvas.add(points[0][0], points[0][1], "m")
            for px, py in points[1:]:
                canvas.add(px, py, "l")
            canvas.add("S")
    return canvas.data(), resources, (x0, y0, x1, y1), extra


_PDF_SUBTYPES = {"highlight": "Highlight", "underline": "Underline", "strike": "StrikeOut", "textbox": "FreeText",
                 "note": "Text", "line": "Line", "arrow": "Line", "rect": "Square", "ellipse": "Circle", "ink": "Ink",
                 "image": "Stamp", "signature": "Stamp"}


def build_annot(pdf, page, ref, annot, embedder):
    """產生一個標準註解(含外觀),回傳間接物件。"""
    content, resources, box, extra = _appearance(pdf, annot, embedder)
    to_user = geometry.ref_to_user(ref)
    matrix = geometry.compose(geometry.local_to_page(box), to_user)
    width, height = box[2] - box[0], box[3] - box[1]
    rect = geometry.transform_box(matrix, (0, 0, width, height))
    stream = pikepdf.Stream(pdf, content)
    stream.Type, stream.Subtype = pikepdf.Name.XObject, pikepdf.Name.Form
    stream.BBox = [0, 0, round(width, 3), round(height, 3)]
    stream.Matrix = [round(v, 4) for v in matrix]
    stream.Resources = resources
    obj = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name("/" + _PDF_SUBTYPES[annot.kind]),
        Rect=[round(v, 3) for v in rect], F=4, C=_rgb(annot.color),
        NM=pikepdf.String(f"naiz-{uuid.uuid4()}"), M=pikepdf.String(time.strftime("D:%Y%m%d%H%M%S")),
        AP=pikepdf.Dictionary(N=pdf.make_indirect(stream)), P=page.obj)
    if annot.opacity < 0.999:
        obj.CA = round(annot.opacity, 3)
    if annot.text:
        obj.Contents = pikepdf.String(annot.text)

    def user(point):
        return geometry.apply(to_user, point)

    kind = annot.kind
    if kind in annots.MARKUP:
        quads = []
        for rx0, ry0, rx1, ry1 in annot.rects:
            for point in ((rx0, ry0), (rx1, ry0), (rx0, ry1), (rx1, ry1)):
                quads.extend(round(v, 3) for v in user(point))
        obj.QuadPoints = quads
    elif kind == "note":
        obj.Name = pikepdf.Name.Comment
        obj.Open = False
    elif kind in ("line", "arrow"):
        obj.L = [round(v, 3) for p in annot.points for v in user(p)]
        obj.LE = [pikepdf.Name.None_ if hasattr(pikepdf.Name, "None_") else pikepdf.Name("/None"),
                  pikepdf.Name("/OpenArrow") if kind == "arrow" else pikepdf.Name("/None")]
    elif kind == "ink":
        obj.InkList = [[round(v, 3) for p in stroke for v in user(p)] for stroke in annot.points]
    elif kind in annots.IMAGES:
        obj.NaizKind = pikepdf.String(kind)     # 讀回來時才知道是自己放的圖片或簽名,可以繼續移動、改大小
        obj.Name = pikepdf.Name("/NaizImage")
        if "/C" in obj:
            del obj["/C"]
    if kind in ("line", "arrow", "rect", "ellipse", "ink", "textbox"):
        obj.BS = pikepdf.Dictionary(Type=pikepdf.Name.Border, W=round(annot.width, 3), S=pikepdf.Name.S)
    if kind in ("rect", "ellipse") and annot.background:
        obj.IC = _rgb(annot.background)
    if kind == "textbox":
        # PDF 規格裡 FreeText 的 /C 是底色;自己的檔案另外用 /NaizBG 記,讀回來才不會和文字顏色搞混
        obj.NaizBG = _rgb(annot.background) if annot.background else pikepdf.Array([])
        if annot.background:
            obj.C = _rgb(annot.background)
        elif "/C" in obj:
            del obj["/C"]
    for key, value in extra.items():
        obj[f"/{key}"] = value
    return pdf.make_indirect(obj)


def _replace_text(pdf, page, ref, items, embedder):
    """改字:先真正刪掉被蓋住的原字,再把底色和新的文字直接畫進頁面(不是註解,別的閱讀器看起來就是原本的字)。"""
    to_user = geometry.ref_to_user(ref)
    areas = [geometry.transform_box(to_user, rect) for annot in items for rect in annot.rects]
    _, skipped = redact.redact_page(pdf, page, areas)
    embedder.kept_text += skipped
    commands = []
    for annot in items:
        cover = [geometry.transform_box(to_user, rect) for rect in cover_areas(annot)]
        if annot.background:
            fill = " ".join(f"{_num(x0)} {_num(y0)} {_num(x1 - x0)} {_num(y1 - y0)} re"
                            for x0, y0, x1, y1 in cover)
            commands.append(f"q {' '.join(_num(v) for v in _rgb(annot.background))} rg {fill} f Q")
        if not annot.text.strip():
            continue
        text = replace(annot, kind="textbox", width=0.0, background=(), rects=())
        content, resources, box, _ = _appearance(pdf, text, embedder)
        form = pikepdf.Stream(pdf, content)
        form.Type, form.Subtype = pikepdf.Name.XObject, pikepdf.Name.Form
        form.BBox = [0, 0, round(box[2] - box[0], 3), round(box[3] - box[1], 3)]
        form.Resources = resources
        matrix = geometry.compose(geometry.local_to_page(box), to_user)
        name = page.add_resource(pdf.make_indirect(form), pikepdf.Name.XObject, prefix="NaizT")
        commands.append(f"q {' '.join(_num(v) for v in matrix)} cm {name} Do Q")
    if commands:
        # 原本的內容包在 q ... Q 裡,後面畫的東西才不會受它留下的座標變換影響
        page.contents_add(pikepdf.Stream(pdf, b"q\n"), prepend=True)
        page.contents_add(pikepdf.Stream(pdf, ("Q\n" + "\n".join(commands) + "\n").encode("latin-1")), prepend=False)


def _redact(pdf, page, ref, items, embedder):
    """塗黑個資:範圍裡的字真正刪掉、圖片的那一塊像素塗掉,再畫上色塊(不是註解,別人拿不掉)。"""
    to_user = geometry.ref_to_user(ref)
    commands = []
    for annot in items:
        areas = [geometry.transform_box(to_user, rect) for rect in annot.rects]
        _, skipped = redact.redact_page(pdf, page, areas, images=True, fill=annot.color)
        embedder.kept_text += skipped
        fill = " ".join(f"{_num(x0)} {_num(y0)} {_num(x1 - x0)} {_num(y1 - y0)} re" for x0, y0, x1, y1 in areas)
        commands.append(f"q {' '.join(_num(v) for v in _rgb(annot.color))} rg {fill} f Q")
    page.contents_add(pikepdf.Stream(pdf, b"q\n"), prepend=True)
    page.contents_add(pikepdf.Stream(pdf, ("Q\n" + "\n".join(commands) + "\n").encode("latin-1")), prepend=False)


def _walk_images(page):
    """依序走過頁面最上層的內容:[(指令, 當時的座標矩陣, 圖片編號或 None, 圖片物件或 None)]。
    圖片編號和 core.pdfium 一樣是最上層的第幾張圖片(含內嵌圖片);內嵌圖片沒有圖片物件。"""
    node, resources = page.obj, None
    while node is not None and resources is None:
        resources = node.get("/Resources")
        node = node.get("/Parent")
    xobjects = resources.get("/XObject") if resources is not None else None
    ctm, stack, number, result = geometry.IDENTITY, [], 0, []
    for item in pikepdf.parse_content_stream(page):
        operator = str(item.operator)
        operands = item.operands
        if operator == "q":
            stack.append(ctm)
        elif operator == "Q":
            ctm = stack.pop() if stack else geometry.IDENTITY
        elif operator == "cm" and len(operands) == 6:
            ctm = geometry.compose(tuple(float(v) for v in operands), ctm)
        target = None
        is_image = operator == "INLINE IMAGE"
        if operator == "Do" and operands and xobjects is not None:
            target = xobjects.get(operands[0])
            is_image = target is not None and str(target.get("/Subtype", "")) == "/Image"
        if is_image:
            result.append((item, ctm, number, target))
            number += 1
        else:
            result.append((item, ctm, None, None))
    return result


def page_image(page, number):
    """原檔第 number 張圖片的原始像素(PNG,含透明);圖片在頁面上轉過或翻過、讀不到時回傳 None。"""
    for item, ctm, found, target in _walk_images(page):
        if found != number:
            continue
        a, b, c, d, _, _ = ctm
        if abs(b) > 1e-6 or abs(c) > 1e-6 or a <= 0 or d <= 0:
            return None
        try:
            if target is None:
                picture = item.operands[0].as_pil_image()
            else:
                picture = pikepdf.PdfImage(target).as_pil_image()
                if "/SMask" in target:
                    alpha = pikepdf.PdfImage(target.SMask).as_pil_image().convert("L")
                    picture = picture.convert("RGBA")
                    picture.putalpha(alpha.resize(picture.size))
            if picture.mode not in ("RGB", "RGBA", "L", "LA"):
                picture = picture.convert("RGB")
            buffer = io.BytesIO()
            picture.save(buffer, "PNG")
            return buffer.getvalue()
        except Exception:
            return None
    return None


def _edit_images(pdf, page, ref, edits):
    """移動、縮放、刪除原檔的圖片:直接改頁面內容,圖片本身的資料不動(畫質不變)。
    編號和 core.pdfium 一樣是頁面最上層的第幾張圖片(含內嵌圖片);找到的位置和編輯器記的對不上時不動它。
    回傳沒改到的張數。"""
    to_user = geometry.ref_to_user(ref)
    before = {annot.number: geometry.transform_box(to_user, annot.box)
              for annot in ref.originals if annot.kind == annots.PAGE_IMAGE}
    wanted = {number: (before.get(number), geometry.transform_box(to_user, box) if box is not None else None)
              for number, box in edits}
    done, result, removed = set(), [], set()
    for item, ctm, number, target in _walk_images(page):
        if number is not None:
            old, new = wanted.get(number, (None, None))
            box = geometry.transform_box(ctm, (0.0, 0.0, 1.0, 1.0))
            if old is not None and max(abs(a - b) for a, b in zip(box, old)) <= 1.0:
                done.add(number)
                if new is None:
                    if target is not None:
                        removed.add(str(item.operands[0]))
                    continue            # 刪除
                sx = (new[2] - new[0]) / (box[2] - box[0]) if box[2] > box[0] else 1.0
                sy = (new[3] - new[1]) / (box[3] - box[1]) if box[3] > box[1] else 1.0
                shift = (sx, 0.0, 0.0, sy, new[0] - box[0] * sx, new[1] - box[1] * sy)
                # 在目前的座標系裡多套一層:最後的位置 = 原本的位置再移動、縮放到新的範圍
                matrix = geometry.compose(geometry.compose(ctm, shift), geometry.invert(ctm))
                result.append(([], pikepdf.Operator("q")))
                result.append(([decimal.Decimal(f"{v:.6f}") for v in matrix], pikepdf.Operator("cm")))
                result.append(item)
                result.append(([], pikepdf.Operator("Q")))
                continue
        result.append(item)
    if done:
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(result))
        # 刪掉的圖片沒有再用到的話從資源拿掉,存檔後檔案裡就不會留著那張圖
        used = {str(item.operands[0]) for item in result
                if not isinstance(item, tuple) and str(item.operator) == "Do" and item.operands}
        unused = [name for name in removed if name not in used]
        if unused:
            xobjects = redact.own_resources(pdf, page).XObject
            for name in unused:
                if name in xobjects:
                    del xobjects[name]
    return len(wanted) - len(done)


def write_page(pdf, page, ref, embedder):
    """把頁面上的註解整理好:沒改過的原註解照原樣保留,改過或刪掉的拿掉,新的註解依目前資料產生。
    改字不是註解,直接改寫頁面內容;移動、刪除過的原檔圖片也是。"""
    edits = annots.image_edits(ref)
    if edits:
        embedder.kept_images += _edit_images(pdf, page, ref, edits)
    replaced = [annot for annot in ref.annots if annot.kind == "replace"]
    if replaced:
        _replace_text(pdf, page, ref, replaced, embedder)
    blacked = [annot for annot in ref.annots if annot.kind == "redact"]
    if blacked:
        _redact(pdf, page, ref, blacked, embedder)
    existing = page.obj.get("/Annots")
    existing = list(existing) if existing is not None else []
    known = {annot.origin for annot in ref.originals}
    kept_origins = annots.untouched_origins(ref)
    result, copies = [], {}
    for index, obj in enumerate(existing):
        if not hasattr(obj, "keys") or str(obj.get("/Subtype", "")) == "/Popup":
            continue
        if index in known and index not in kept_origins:
            continue
        # 複製一份再放進這一頁:同一頁被複製成好幾頁時,註解才不會共用同一個物件
        copy = pdf.make_indirect(pikepdf.Dictionary({key: obj[key] for key in obj.keys()}))
        copy.P = page.obj
        result.append(copy)
        if "/Popup" in obj and obj.Popup.is_indirect:
            copies[obj.Popup.objgen] = copy
    for obj in existing:
        if hasattr(obj, "keys") and str(obj.get("/Subtype", "")) == "/Popup" and obj.is_indirect \
                and obj.objgen in copies:
            parent = copies[obj.objgen]
            popup = pdf.make_indirect(pikepdf.Dictionary({key: obj[key] for key in obj.keys()}))
            popup.Parent, popup.P = parent, page.obj
            parent.Popup = popup
            result.append(popup)
    for annot in ref.annots:
        if annot.kind in ("other", "replace", "redact", annots.PAGE_IMAGE) \
                or (annot.origin >= 0 and annot.origin in kept_origins):
            continue
        result.append(build_annot(pdf, page, ref, annot, embedder))
    if result:
        page.obj.Annots = pdf.make_indirect(pikepdf.Array(result))
    elif "/Annots" in page.obj:
        del page.obj["/Annots"]


def flatten_page(pdf, page):
    """把看得到的註解畫進頁面內容(之後不能再當註解修改);連結、表單欄位保留。"""
    existing = page.obj.get("/Annots")
    if existing is None:
        return 0
    keep, flattened, draws = [], set(), []
    for obj in existing:
        if not hasattr(obj, "keys"):
            continue
        subtype = str(obj.get("/Subtype", ""))
        flags = int(obj.get("/F", 0))
        appearance = obj.get("/AP")
        normal = appearance.get("/N") if appearance is not None else None
        if normal is not None and not isinstance(normal, pikepdf.Stream) and hasattr(normal, "keys"):
            state = obj.get("/AS")
            normal = normal.get(str(state)) if state is not None else None
        if subtype in ("/Link", "/Widget", "/Popup") or flags & (2 | 32) or not isinstance(normal, pikepdf.Stream):
            keep.append(obj)
            continue
        draws.append((obj, normal))
        if obj.is_indirect:
            flattened.add(obj.objgen)
    if not draws:
        return 0
    commands = []
    for obj, form in draws:
        rect = [float(v) for v in obj.Rect]
        rx0, ry0, rx1, ry1 = min(rect[0], rect[2]), min(rect[1], rect[3]), max(rect[0], rect[2]), max(rect[1], rect[3])
        bbox = [float(v) for v in form.get("/BBox", [0, 0, 0, 0])]
        matrix = tuple(float(v) for v in form.get("/Matrix", geometry.IDENTITY))
        tx0, ty0, tx1, ty1 = geometry.transform_box(matrix, bbox)
        if tx1 - tx0 <= 0 or ty1 - ty0 <= 0:
            continue
        sx, sy = (rx1 - rx0) / (tx1 - tx0), (ry1 - ry0) / (ty1 - ty0)
        name = page.add_resource(form, pikepdf.Name.XObject, prefix="NaizA")
        commands.append(f"q {_num(sx)} 0 0 {_num(sy)} {_num(rx0 - tx0 * sx)} {_num(ry0 - ty0 * sy)} cm {name} Do Q")
    page.contents_add(pikepdf.Stream(pdf, b"q\n"), prepend=True)
    page.contents_add(pikepdf.Stream(pdf, ("Q\n" + "\n".join(commands) + "\n").encode("latin-1")), prepend=False)
    keep = [obj for obj in keep if not (str(obj.get("/Subtype", "")) == "/Popup" and "/Parent" in obj
                                        and obj.Parent.is_indirect and obj.Parent.objgen in flattened)]
    if keep:
        page.obj.Annots = pdf.make_indirect(pikepdf.Array(keep))
    else:
        del page.obj["/Annots"]
    return len(commands)
