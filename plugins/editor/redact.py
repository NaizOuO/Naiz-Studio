"""真正刪掉頁面上某些範圍裡的字(白底改字、之後的塗黑個資共用)。

只蓋一層白框的話,舊字其實還在底下,選取、搜尋、複製都找得到。
這裡直接改頁面內容:照 PDF 規格一個字一個字算出位置,落在範圍裡的字從文字指令中拿掉,
並補上相同寬度的間距,同一行其他的字才會留在原位。

字寬算不準的字型(少見的編碼、直書)不冒險改,只統計數量回報;這些字仍會被白底蓋住。

塗黑個資時連圖片也要處理(images=True):範圍內的像素直接塗掉、換成新的圖片,
只在上面疊一層黑框的話,別人把黑框移開,底下的照片還看得到。
"""

import re
from pathlib import Path

import pikepdf

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
MAX_FORM_DEPTH = 4
# 標準 14 種字型沒有附字寬,借用字寬完全相同的 Windows 字型來量
_STANDARD_FILES = {
    "Helvetica": "arial.ttf", "Helvetica-Bold": "arialbd.ttf", "Helvetica-Oblique": "ariali.ttf",
    "Helvetica-BoldOblique": "arialbi.ttf", "Times-Roman": "times.ttf", "Times-Bold": "timesbd.ttf",
    "Times-Italic": "timesi.ttf", "Times-BoldItalic": "timesbi.ttf",
}
_ALIASES = {"Arial": "Helvetica", "Arial,Bold": "Helvetica-Bold", "Arial,Italic": "Helvetica-Oblique",
            "Arial,BoldItalic": "Helvetica-BoldOblique", "TimesNewRoman": "Times-Roman",
            "TimesNewRoman,Bold": "Times-Bold", "TimesNewRoman,Italic": "Times-Italic",
            "TimesNewRoman,BoldItalic": "Times-BoldItalic"}
_standard_cache = {}


def _mul(a, b):
    """矩陣相乘(PDF 的列向量寫法):先套 a 再套 b。"""
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5])


def _apply(m, x, y):
    return x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5]


def _invert(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return IDENTITY
    return (d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det)


def _num(value, fallback=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


# ------------------------------------------------------------ 字寬

def _standard_widths(base):
    """標準字型每個字元代碼(WinAnsi)的字寬;沒辦法量的回傳 None。"""
    base = _ALIASES.get(base, base)
    if base.startswith("Courier"):
        return {code: 600.0 for code in range(256)}
    filename = _STANDARD_FILES.get(base)
    if filename is None:
        return None
    if base in _standard_cache:
        return _standard_cache[base]
    widths = None
    path = Path(r"C:\Windows\Fonts") / filename
    try:
        from fontTools.ttLib import TTFont

        font = TTFont(str(path), lazy=True)
        scale = 1000.0 / font["head"].unitsPerEm
        cmap, metrics = font.getBestCmap(), font["hmtx"].metrics
        widths = {}
        for code in range(32, 256):
            try:
                char = bytes([code]).decode("cp1252")
            except UnicodeDecodeError:
                continue
            glyph = cmap.get(ord(char))
            if glyph in metrics:
                widths[code] = metrics[glyph][0] * scale
        font.close()
    except Exception:
        widths = None
    _standard_cache[base] = widths
    return widths


def _parse_w_array(array):
    """CID 字型的 /W:[起始 [寬 寬 ...]] 或 [起始 結束 寬]。"""
    widths, items, i = {}, list(array), 0
    while i < len(items):
        first = int(items[i])
        following = items[i + 1] if i + 1 < len(items) else None
        if isinstance(following, pikepdf.Array):
            for offset, value in enumerate(following):
                widths[first + offset] = float(value)
            i += 2
        elif i + 2 < len(items):
            for cid in range(first, int(following) + 1):
                widths[cid] = float(items[i + 2])
            i += 3
        else:
            break
    return widths


def _cmap_ranges(stream):
    """內嵌 CMap 的編碼長度(codespacerange)與代碼 → CID 對應;看不懂就回傳 (None, None)。"""
    try:
        text = stream.read_bytes().decode("latin-1")
    except Exception:
        return None, None
    lengths = []
    for block in re.findall(r"begincodespacerange(.*?)endcodespacerange", text, re.S):
        for low, high in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            lengths.append((len(low) // 2, int(low, 16), int(high, 16)))
    mapping = {}
    for block in re.findall(r"begincidrange(.*?)endcidrange", text, re.S):
        for low, high, cid in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\d+)", block):
            for offset in range(int(high, 16) - int(low, 16) + 1):
                mapping[(len(low) // 2, int(low, 16) + offset)] = int(cid) + offset
    for block in re.findall(r"begincidchar(.*?)endcidchar", text, re.S):
        for code, cid in re.findall(r"<([0-9A-Fa-f]+)>\s*(\d+)", block):
            mapping[(len(code) // 2, int(code, 16))] = int(cid)
    return (lengths or None), mapping


class FontInfo:
    """一個字型的編碼方式與字寬;certain 為 False 表示字寬沒把握,不要改動用到它的文字。"""

    def __init__(self, font):
        self.certain = True
        self.two_byte = False
        self.lengths = None         # 內嵌 CMap 的編碼長度
        self.cid_map = None
        self.byte_rule = False      # 預設 CMap(Big5 等):第一個位元組 0x81 以上是兩個位元組
        self.scale = 0.001          # 字寬單位換成文字空間(Type3 用自己的 FontMatrix)
        self.widths, self.default = {}, 0.0
        subtype = str(font.get("/Subtype", ""))
        if subtype == "/Type0":
            self._type0(font)
        else:
            self._simple(font, subtype)

    def _type0(self, font):
        encoding = font.get("/Encoding")
        descendant = font.DescendantFonts[0] if "/DescendantFonts" in font else pikepdf.Dictionary()
        self.default = _num(descendant.get("/DW", 1000), 1000.0)
        has_widths = "/W" in descendant
        self.widths = _parse_w_array(descendant.W) if has_widths else {}
        if isinstance(encoding, pikepdf.Stream):
            self.lengths, self.cid_map = _cmap_ranges(encoding)
            if self.lengths is None:
                self.certain = False
            if "/WMode" in encoding and int(encoding.WMode) == 1:
                self.certain = False
            return
        name = str(encoding or "")
        if name.endswith("-V"):
            self.certain = False                    # 直書
        if name in ("/Identity-H", "/Identity-V"):
            self.two_byte = True
            return
        if "UCS2" in name or "UTF16" in name:
            self.two_byte = True
        else:
            self.byte_rule = True
        # 不知道代碼對應哪個 CID;只有全部字都是預設寬度時才算得準
        self.cid_map = {}
        if has_widths:
            self.certain = False

    def _simple(self, font, subtype):
        if subtype == "/Type3":
            matrix = [float(v) for v in font.get("/FontMatrix", [0.001, 0, 0, 0.001, 0, 0])]
            if abs(matrix[1]) > 1e-9 or abs(matrix[2]) > 1e-9:
                self.certain = False
            self.scale = matrix[0]
        descriptor = font.get("/FontDescriptor")
        self.default = _num(descriptor.get("/MissingWidth", 0), 0.0) if descriptor is not None else 0.0
        if "/Widths" in font:
            first = int(font.get("/FirstChar", 0))
            self.widths = {first + i: float(w) for i, w in enumerate(font.Widths)}
            return
        base = str(font.get("/BaseFont", ""))[1:]
        if "+" in base[:7]:
            base = base.split("+", 1)[1]
        standard = _standard_widths(base)
        if standard is None:
            self.certain = False
        else:
            self.widths = standard

    def codes(self, data):
        """把字串拆成一個個字元代碼:[(代碼, 位元組, 是不是單一位元組的空白)]。"""
        result, i = [], 0
        while i < len(data):
            size = 1
            if self.two_byte:
                size = 2
            elif self.lengths:
                size = next((n for n, low, high in self.lengths
                             if i + n <= len(data) and low <= int.from_bytes(data[i:i + n], "big") <= high), 1)
            elif self.byte_rule and data[i] >= 0x81:
                size = 2
            chunk = data[i:i + size]
            code = int.from_bytes(chunk, "big")
            result.append((code, chunk, size == 1 and code == 32))
            i += size
        return result

    def width(self, code, size):
        cid = code
        if self.cid_map is not None:
            cid = self.cid_map.get((size, code), code if self.two_byte else None)
            if cid is None:
                return self.default * self.scale
        return self.widths.get(cid, self.default) * self.scale


# ------------------------------------------------------------ 改寫頁面內容

class _State:
    def __init__(self):
        self.ctm = IDENTITY
        self.char_space = self.word_space = self.rise = self.leading = 0.0
        self.scale = 1.0
        self.font, self.size = None, 0.0

    def copy(self):
        other = _State()
        other.__dict__.update(self.__dict__)
        return other


class Redactor:
    def __init__(self, pdf, areas, tolerance=0.5, images=False, fill=(0, 0, 0)):
        self.pdf = pdf
        self.areas = [tuple(a) for a in areas]
        self.tolerance = tolerance
        self.images = images
        self.fill = tuple(fill)
        self.removed = 0
        self.skipped = 0
        self.images_changed = 0
        self.replaced = set()        # 換成新版本的圖片、表單物件名稱
        self._fonts = {}

    def _inside(self, x, y):
        t = self.tolerance
        return any(x0 - t <= x <= x1 + t and y0 - t <= y <= y1 + t for x0, y0, x1, y1 in self.areas)

    def _font(self, resources, name):
        fonts = resources.get("/Font") if resources is not None else None
        font = fonts.get(name) if fonts is not None else None
        if font is None:
            return None
        key = font.objgen if font.is_indirect else id(font)
        if key not in self._fonts:
            try:
                self._fonts[key] = FontInfo(font)
            except Exception:
                info = FontInfo.__new__(FontInfo)
                info.certain = False
                self._fonts[key] = info
        return self._fonts[key]

    def _show(self, state, tm, items):
        """處理一段文字:回傳 (新的 TJ 陣列或 None(沒有改動), 顯示完之後的文字矩陣)。"""
        font, size, scale = state.font, state.size, state.scale
        if font is None or not getattr(font, "certain", False) or abs(size * scale) < 1e-9:
            if self._touches(state, tm):
                self.skipped += 1
            return None, tm
        to_user = _mul(tm, state.ctm)
        output, changed, pending, current = [], False, 0.0, b""
        x = 0.0         # 從 tm 開始已經往右前進多少(文字空間,已含水平縮放)

        def flush():
            nonlocal pending, current
            if current:
                if pending:
                    output.append(pending)
                    pending = 0.0
                output.append(current)
                current = b""

        for item in items:
            if not isinstance(item, (bytes, pikepdf.String)):
                value = float(item)             # TJ 裡的數字:往左移 value/1000 個字級
                x -= value / 1000.0 * size * scale
                flush()
                pending += value
                continue
            for code, chunk, space in font.codes(bytes(item)):
                w0 = font.width(code, len(chunk))
                advance = (w0 * size + state.char_space + (state.word_space if space else 0.0)) * scale
                # 字的中心:字寬的一半、離底線約 0.35 個字級高的地方
                cx, cy = _apply(to_user, x + w0 * size * scale / 2, state.rise + size * 0.35)
                if self._inside(cx, cy):
                    changed = True
                    self.removed += 1
                    flush()
                    pending -= advance * 1000.0 / (size * scale)
                else:
                    if pending and not current:
                        output.append(pending)
                        pending = 0.0
                    current += chunk
                x += advance
        flush()
        if pending:
            output.append(pending)
        new_tm = _mul((1.0, 0.0, 0.0, 1.0, x, 0.0), tm)
        if not changed:
            return None, new_tm
        array = []
        for part in output:
            if isinstance(part, bytes):
                array.append(pikepdf.String(part))
            elif array and not isinstance(array[-1], pikepdf.String):
                array[-1] = round(array[-1] + part, 3)
            else:
                array.append(round(part, 3))
        return pikepdf.Array(array), new_tm

    def _touches(self, state, tm):
        """字寬沒把握的文字:用起點粗略判斷有沒有碰到範圍,只用來統計。"""
        x, y = _apply(_mul(tm, state.ctm), 0.0, state.size * 0.35)
        return self._inside(x, y)

    def rewrite(self, operations, resources, ctm=IDENTITY, depth=0):
        """改寫一串內容指令;回傳 (新指令, 有沒有改動)。"""
        state = _State()
        state.ctm = ctm
        stack = []
        tm = tlm = IDENTITY
        result, changed = [], False
        for operands, operator in operations:
            op = str(operator)
            if op == "q":
                stack.append(state.copy())
            elif op == "Q":
                if stack:
                    state = stack.pop()
            elif op == "cm" and len(operands) == 6:
                state.ctm = _mul(tuple(float(v) for v in operands), state.ctm)
            elif op == "BT":
                tm = tlm = IDENTITY
            elif op == "Tf" and len(operands) == 2:
                state.font = self._font(resources, operands[0])
                state.size = float(operands[1])
            elif op == "Tc" and operands:
                state.char_space = float(operands[0])
            elif op == "Tw" and operands:
                state.word_space = float(operands[0])
            elif op == "Tz" and operands:
                state.scale = float(operands[0]) / 100.0
            elif op == "TL" and operands:
                state.leading = float(operands[0])
            elif op == "Ts" and operands:
                state.rise = float(operands[0])
            elif op in ("Td", "TD") and len(operands) == 2:
                tx, ty = float(operands[0]), float(operands[1])
                if op == "TD":
                    state.leading = -ty
                tm = tlm = _mul((1.0, 0.0, 0.0, 1.0, tx, ty), tlm)
            elif op == "Tm" and len(operands) == 6:
                tm = tlm = tuple(float(v) for v in operands)
            elif op == "T*":
                tm = tlm = _mul((1.0, 0.0, 0.0, 1.0, 0.0, -state.leading), tlm)
            elif op in ("Tj", "TJ", "'", '"'):
                prefix = []
                if op == '"' and len(operands) == 3:
                    state.word_space, state.char_space = float(operands[0]), float(operands[1])
                    prefix = [([operands[0]], pikepdf.Operator("Tw")), ([operands[1]], pikepdf.Operator("Tc"))]
                if op in ("'", '"'):
                    tm = tlm = _mul((1.0, 0.0, 0.0, 1.0, 0.0, -state.leading), tlm)
                    prefix.append(([], pikepdf.Operator("T*")))
                items = list(operands[0]) if op == "TJ" else [operands[-1]]
                new_items, tm = self._show(state, tm, items)
                if new_items is not None:
                    changed = True
                    result.extend(prefix)
                    result.append(([new_items], pikepdf.Operator("TJ")))
                    continue
            elif op == "Do" and operands and depth < MAX_FORM_DEPTH:
                replacement = self._form(resources, operands[0], state.ctm, depth)
                if replacement is None and self.images:
                    replacement = self._image(resources, operands[0], state.ctm)
                if replacement is not None:
                    changed = True
                    result.append(([replacement], operator))
                    continue
            result.append((operands, operator))
        return result, changed

    def _image(self, resources, name, ctm):
        """圖片和範圍重疊的地方,像素直接塗成指定的顏色,換成一張新圖片。"""
        xobjects = resources.get("/XObject") if resources is not None else None
        image = xobjects.get(name) if xobjects is not None else None
        if not isinstance(image, pikepdf.Stream) or str(image.get("/Subtype", "")) != "/Image":
            return None
        corners = [_apply(ctm, x, y) for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))]
        box = (min(c[0] for c in corners), min(c[1] for c in corners),
               max(c[0] for c in corners), max(c[1] for c in corners))
        hits = [a for a in self.areas if a[0] < box[2] and a[2] > box[0] and a[1] < box[3] and a[3] > box[1]]
        if not hits:
            return None
        try:
            from PIL import ImageDraw

            picture = pikepdf.PdfImage(image).as_pil_image()
            if picture.mode not in ("RGB", "L"):
                picture = picture.convert("RGB")
        except Exception:
            self.skipped += 1           # 讀不了的圖片格式:只會被上面的色塊蓋住
            return None
        width, height = picture.size
        inverse = _invert(ctm)
        draw = ImageDraw.Draw(picture)
        color = self.fill if picture.mode == "RGB" else round(sum(self.fill) / 3)
        for x0, y0, x1, y1 in hits:
            points = [_apply(inverse, x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
            us, vs = [u for u, _ in points], [v for _, v in points]
            # 圖片空間:(0,0) 是左下、(1,1) 是右上;像素的第一列在上面
            left, right = max(0.0, min(us)) * width, min(1.0, max(us)) * width
            top, bottom = (1 - min(1.0, max(vs))) * height, (1 - max(0.0, min(vs))) * height
            if right > left and bottom > top:
                draw.rectangle((int(left), int(top), int(right + 0.999) - 1, int(bottom + 0.999) - 1), fill=color)
        import zlib

        stream = pikepdf.Stream(self.pdf, zlib.compress(picture.tobytes(), 9))
        stream.Type, stream.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
        stream.Width, stream.Height, stream.BitsPerComponent = width, height, 8
        stream.ColorSpace = pikepdf.Name.DeviceRGB if picture.mode == "RGB" else pikepdf.Name.DeviceGray
        stream.Filter = pikepdf.Name.FlateDecode
        if "/SMask" in image:
            stream.SMask = image.SMask
        self.replaced.add(str(name))
        new_name = pikepdf.Name(f"{name}_naizr{self.images_changed}")
        xobjects[new_name] = self.pdf.make_indirect(stream)
        self.images_changed += 1
        return new_name

    def _form(self, resources, name, ctm, depth):
        """頁面用到的表單物件(一組可以重複使用的內容)裡也有字時,複製一份改好再換上去。"""
        xobjects = resources.get("/XObject") if resources is not None else None
        form = xobjects.get(name) if xobjects is not None else None
        if not isinstance(form, pikepdf.Stream) or str(form.get("/Subtype", "")) != "/Form":
            return None
        matrix = tuple(float(v) for v in form.get("/Matrix", IDENTITY))
        inner = form.get("/Resources", resources)
        try:
            operations = pikepdf.parse_content_stream(form)
        except Exception:
            return None
        rewritten, changed = self.rewrite(operations, inner, _mul(matrix, ctm), depth + 1)
        if not changed:
            return None
        copy = pikepdf.Stream(self.pdf, pikepdf.unparse_content_stream(rewritten))
        for key in form.keys():
            if key not in ("/Length", "/Filter", "/DecodeParms"):
                copy[key] = form[key]
        self.replaced.add(str(name))
        new_name = pikepdf.Name(f"{name}_naiz{self.removed}")
        xobjects[new_name] = self.pdf.make_indirect(copy)
        return new_name


def own_resources(pdf, page):
    """這一頁自己的資源字典(可能是從上層繼承或和別頁共用);要修改前先複製一份,避免改到其他頁。"""
    node, resources = page.obj, None
    while node is not None and resources is None:
        resources = node.get("/Resources")
        node = node.get("/Parent")
    copy = pikepdf.Dictionary({key: resources[key] for key in resources.keys()}) if resources is not None \
        else pikepdf.Dictionary()
    if "/XObject" in copy:
        copy.XObject = pikepdf.Dictionary({key: copy.XObject[key] for key in copy.XObject.keys()})
    page.obj.Resources = pdf.make_indirect(copy)
    return page.obj.Resources


def redact_page(pdf, page, areas, images=False, fill=(0, 0, 0)):
    """刪掉頁面上落在 areas(使用者座標的 (左, 下, 右, 上))裡的字;images 為真時圖片的那一塊也塗掉。
    回傳 (刪掉的字數, 沒把握而保留的段數)。"""
    if not areas:
        return 0, 0
    resources = own_resources(pdf, page)
    redactor = Redactor(pdf, areas, images=images, fill=fill)
    operations = pikepdf.parse_content_stream(page)
    rewritten, changed = redactor.rewrite(operations, resources)
    if changed:
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(rewritten))
        # 被換掉的舊圖片、舊表單物件(裡面還有原本的字和像素)不能留在頁面上,不然存檔後還取得出來
        used = {str(operands[0]) for operands, operator in rewritten if str(operator) == "Do" and operands}
        xobjects = resources.get("/XObject")
        if xobjects is not None:
            for name in list(xobjects.keys()):
                if name in redactor.replaced and name not in used:
                    del xobjects[name]
    return redactor.removed, redactor.skipped
