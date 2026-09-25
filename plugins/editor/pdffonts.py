"""取出 PDF 裡內嵌的原字型,改字、編輯段落時用它,新字才會和原字長得一模一樣。

PDF 裡的字型大多只留下用到的字(子集),而且對照表常常被拿掉或改成自己的編號,不能直接拿來用。
這裡把字型檔取出來,依 PDF 記的「代碼 → 文字」(ToUnicode)與「代碼 → 字形」重建一份正常的對照表,
存成字型檔放在 fonts\\pdf\\,之後就能和電腦上的字型一樣排版、預覽、嵌入。
原檔沒有用到的字不在子集裡,打這些字時會自動改用相近的字型補上。

舊式的 Type1 字型(教科書、LaTeX 產生的 PDF 常見,CFF 或 PFB 格式、只有 256 個代碼)沒有正常的對照表,
這裡依 PDF 記的編碼與字形名稱,把每個字形重新畫進一個新的 OpenType 字型。
數學符號這類認不出是哪個字的字形(PDFium 讀出來是控制字元),對應到私用區 U+F000+代碼,
段落文字裡也換成同一個字,這樣符號照樣用原字型畫,不會變成方框。
"""

import hashlib
import io
import re

import pikepdf

from core import paths

from . import fonts

_cache = {}         # (來源, 字型名稱) → FontFace 或 None
SYMBOL_BASE = 0xF000    # 認不出是哪個字的字形:私用區 U+F000 + 代碼


def symbol_text(ch):
    """PDFium 讀出來的控制字元(認不出的符號)→ 私用區的字;其他字照舊。"""
    code = ord(ch)
    return chr(SYMBOL_BASE + code) if code < 0x20 and ch not in "\n\r" else ch


def cache_dir():
    return paths.FONTS_DIR / "pdf"


def _unicode_map(stream):
    """ToUnicode CMap:{代碼: 文字}。"""
    try:
        text = stream.read_bytes().decode("latin-1")
    except Exception:
        return {}
    result = {}

    def decode(hex_text):
        data = bytes.fromhex(hex_text)
        try:
            return data.decode("utf-16-be")
        except UnicodeDecodeError:
            return ""

    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for code, value in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            result[int(code, 16)] = decode(value)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for low, high, rest in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]*>)", block):
            low, high = int(low, 16), int(high, 16)
            if rest.startswith("["):
                for offset, value in enumerate(re.findall(r"<([0-9A-Fa-f]*)>", rest)):
                    result[low + offset] = decode(value)
            else:
                start = bytes.fromhex(rest[1:-1])
                for offset in range(min(high - low + 1, 65536)):
                    value = (int.from_bytes(start, "big") + offset).to_bytes(len(start), "big")
                    try:
                        result[low + offset] = value.decode("utf-16-be")
                    except UnicodeDecodeError:
                        pass
    return result


def _find_font(resources, name, depth=0):
    """在頁面資源(含表單物件裡的資源)找 BaseFont 是 name 的字型字典。"""
    if resources is None or depth > 4:
        return None
    for font in (resources.get("/Font") or {}).values():
        base = str(font.get("/BaseFont", ""))[1:]
        # PDFium 回報的名稱有時不含子集前綴(ABCDEF+),兩種都要比
        if base == name or _family(base) == _family(name):
            return font
    for xobject in (resources.get("/XObject") or {}).values():
        if isinstance(xobject, pikepdf.Stream) and str(xobject.get("/Subtype", "")) == "/Form":
            found = _find_font(xobject.get("/Resources"), name, depth + 1)
            if found is not None:
                return found
    return None


def _program(descriptor):
    """字型檔本體;只支援 TrueType 與 OpenType(大部分 Word、LibreOffice 產生的 PDF 都是)。"""
    if descriptor is None:
        return None
    if "/FontFile2" in descriptor:
        return descriptor.FontFile2.read_bytes()
    font_file = descriptor.get("/FontFile3")
    if font_file is not None and str(font_file.get("/Subtype", "")) == "/OpenType":
        return font_file.read_bytes()
    return None


# ------------------------------------------------------------ 舊式 Type1 字型

def _win_ansi():
    from fontTools import agl

    names = []
    for code in range(256):
        try:
            ch = bytes([code]).decode("cp1252")
        except UnicodeDecodeError:
            names.append(".notdef")
            continue
        names.append(agl.UV2AGL.get(ord(ch), ".notdef") if code >= 0x20 else ".notdef")
    return names


def _base_encoding(name):
    from fontTools.encodings.MacRoman import MacRoman
    from fontTools.encodings.StandardEncoding import StandardEncoding

    if name == "/MacRomanEncoding":
        return list(MacRoman)
    if name == "/WinAnsiEncoding":
        return _win_ansi()
    if name == "/StandardEncoding":
        return list(StandardEncoding)
    return None


def _code_names(font_dict, builtin):
    """代碼 0～255 各是哪個字形名稱:PDF 的編碼(含 Differences),沒寫時用字型自己的編碼。"""
    encoding = font_dict.get("/Encoding")
    base, differences = None, {}
    if isinstance(encoding, pikepdf.Name):
        base = _base_encoding(str(encoding))
    elif isinstance(encoding, pikepdf.Dictionary):
        if "/BaseEncoding" in encoding:
            base = _base_encoding(str(encoding.BaseEncoding))
        code = 0
        for item in encoding.get("/Differences", []):
            if isinstance(item, pikepdf.Name):
                differences[code] = str(item)[1:]
                code += 1
            else:
                code = int(item)
    if base is None:
        base = list(builtin) if builtin else _base_encoding("/StandardEncoding")
    names = (list(base) + [".notdef"] * 256)[:256]
    for code, name in differences.items():
        if 0 <= code < 256:
            names[code] = name
    return names


def _type1_glyphs(descriptor):
    """(字形名稱 → 畫字形的函式, 字型自己的編碼, 單位換算);讀不懂時回傳 None。"""
    font_file = descriptor.get("/FontFile3")
    if font_file is not None and str(font_file.get("/Subtype", "")) == "/Type1C":
        from fontTools.cffLib import CFFFontSet

        cff = CFFFontSet()
        cff.decompile(io.BytesIO(font_file.read_bytes()), None)
        top = cff[cff.fontNames[0]]
        if hasattr(top, "ROS"):
            return None             # CID 字型另外處理(目前不支援)
        strings = top.CharStrings
        builtin = top.Encoding if isinstance(top.Encoding, list) else None
        scale = float(top.FontMatrix[0]) * 1000 if hasattr(top, "FontMatrix") else 1.0
        return {name: strings[name].draw for name in strings.keys()}, builtin, scale
    if "/FontFile" in descriptor:
        import tempfile
        from pathlib import Path

        from fontTools.t1Lib import T1Font

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "font.pfa"
            path.write_bytes(descriptor.FontFile.read_bytes())
            font = T1Font(str(path))
            font.parse()
        glyph_set = font.getGlyphSet()
        encoding = font.font.get("Encoding")
        builtin = encoding if isinstance(encoding, list) else None
        matrix = font.font.get("FontMatrix", [0.001])
        return {name: glyph_set[name].draw for name in glyph_set.keys()}, builtin, float(matrix[0]) * 1000
    return None


def _type1_font(font_dict, descriptor, family):
    """舊式 Type1 字型 → (新的 OpenType 字型檔內容, 對照到的字數);做不出來時回傳 None。"""
    from fontTools import agl
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.t2CharStringPen import T2CharStringPen
    from fontTools.pens.transformPen import TransformPen

    found = _type1_glyphs(descriptor)
    if found is None:
        return None
    draws, builtin, scale = found
    names = _code_names(font_dict, builtin)
    unicode = _unicode_map(font_dict.ToUnicode) if "/ToUnicode" in font_dict else {}
    first = int(font_dict.get("/FirstChar", 0))
    widths = [float(v) for v in font_dict.get("/Widths", [])]
    missing = float(descriptor.get("/MissingWidth", 0))
    cmap, advance = {}, {}
    for code, name in enumerate(names):
        if name == ".notdef" or name not in draws:
            continue
        if first <= code < first + len(widths):
            advance.setdefault(name, widths[code - first])
        text = unicode.get(code) or agl.toUnicode(name)
        if len(text) == 1 and text.isprintable():
            cmap.setdefault(ord(text), name)
        cmap[SYMBOL_BASE + code] = name        # 認不出是哪個字時,段落文字會換成這個私用區的字
    if not cmap:
        return None
    order = [".notdef"] + sorted(set(cmap.values()))
    charstrings, metrics = {}, {}
    for name in order:
        width = round(advance.get(name, missing))
        pen = T2CharStringPen(width, None)
        bounds = BoundsPen(None)
        if name in draws:
            for target in (pen, bounds):
                draws[name](TransformPen(target, (scale, 0, 0, scale, 0, 0)) if abs(scale - 1) > 1e-6 else target)
        charstrings[name] = pen.getCharString()
        metrics[name] = (width, round(bounds.bounds[0]) if bounds.bounds else 0)
    ascent = round(float(descriptor.get("/Ascent", 800)) or 800)
    descent = round(float(descriptor.get("/Descent", -200)) or -200)
    ps_name = re.sub(r"[^A-Za-z0-9-]", "", family) or "PDFFont"
    builder = FontBuilder(1000, isTTF=False)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(cmap)
    builder.setupCFF(ps_name, {"FullName": family, "FamilyName": family}, charstrings, {})
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=ascent, descent=descent)
    builder.setupNameTable({"familyName": family, "styleName": "Regular", "psName": ps_name})
    builder.setupOS2(sTypoAscender=ascent, sTypoDescender=descent, usWinAscent=ascent, usWinDescent=abs(descent),
                     fsType=0)
    builder.setupPost()
    buffer = io.BytesIO()
    builder.save(buffer)
    return buffer.getvalue(), len(cmap)


def _glyph_map(font_dict, font):
    """{文字: 字形編號}。"""
    unicode = _unicode_map(font_dict.ToUnicode) if "/ToUnicode" in font_dict else {}
    result = {}
    if str(font_dict.get("/Subtype", "")) == "/Type0":
        if str(font_dict.get("/Encoding", "")) not in ("/Identity-H", "/Identity-V"):
            return {}
        descendant = font_dict.DescendantFonts[0]
        mapping = descendant.get("/CIDToGIDMap")
        table = mapping.read_bytes() if isinstance(mapping, pikepdf.Stream) else None
        for code, text in unicode.items():
            gid = int.from_bytes(table[code * 2:code * 2 + 2], "big") if table is not None else code
            if len(text) == 1 and gid:
                result.setdefault(text, gid)
        return result
    cmap = font["cmap"] if "cmap" in font else None
    subtables = {(t.platformID, t.platEncID): t.cmap for t in cmap.tables} if cmap is not None else {}
    order = {name: gid for gid, name in enumerate(font.getGlyphOrder())}
    differences = {}
    encoding = font_dict.get("/Encoding")
    if isinstance(encoding, pikepdf.Dictionary) and "/Differences" in encoding:
        code = 0
        for item in encoding.Differences:
            if isinstance(item, pikepdf.Name):
                differences[code] = str(item)[1:]
                code += 1
            else:
                code = int(item)
    for code in range(256):
        text = unicode.get(code)
        if text is None:
            try:
                text = bytes([code]).decode("cp1252")
            except UnicodeDecodeError:
                continue
        name = None
        if (3, 0) in subtables:
            name = subtables[(3, 0)].get(0xF000 | code) or subtables[(3, 0)].get(code)
        if name is None and (1, 0) in subtables:
            name = subtables[(1, 0)].get(code)
        if name is None and (3, 1) in subtables:
            name = subtables[(3, 1)].get(ord(text[0])) if text else None
        if name is None and code in differences:
            name = differences[code] if differences[code] in order else None
        gid = order.get(name) if name else None
        if gid and len(text) == 1 and text.isprintable():
            result.setdefault(text, gid)
    return result


def _rebuild(program, glyph_map, family):
    """字型檔換上新的對照表,補齊畫面預覽與嵌入需要的表格;回傳新的字型檔內容。"""
    from fontTools.ttLib import TTFont, newTable
    from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

    font = TTFont(io.BytesIO(program), recalcBBoxes=False)       # 原因見 pdfwrite 的字型嵌入
    for tag in ("head", "hhea", "hmtx", "maxp"):
        if tag not in font:
            raise ValueError(f"字型少了 {tag} 表")
    if "glyf" not in font and "CFF " not in font:
        raise ValueError("字型沒有字形資料")
    order = font.getGlyphOrder()
    mapping = {ord(ch): order[gid] for ch, gid in glyph_map.items() if gid < len(order)}
    table = newTable("cmap")
    table.tableVersion = 0
    table.tables = []
    bmp = CmapSubtable.newSubtable(4)
    bmp.platformID, bmp.platEncID, bmp.language = 3, 1, 0
    bmp.cmap = {code: name for code, name in mapping.items() if code <= 0xFFFF}
    table.tables.append(bmp)
    if any(code > 0xFFFF for code in mapping):
        full = CmapSubtable.newSubtable(12)
        full.platformID, full.platEncID, full.language = 3, 10, 0
        full.cmap = dict(mapping)
        table.tables.append(full)
    font["cmap"] = table
    if "post" not in font:
        post = newTable("post")
        post.formatType, post.italicAngle, post.underlinePosition, post.underlineThickness = 3.0, 0, -100, 50
        post.isFixedPitch = post.minMemType42 = post.maxMemType42 = post.minMemType1 = post.maxMemType1 = 0
        font["post"] = post
    # 字型原本的名稱一定要留著:標楷體、細明體這類字型的筆畫要靠字型裡的微調程式拼起來,
    # 閱讀器是看字型名稱(例如 DFKai-SB)才知道要這樣處理,名稱改掉畫出來的筆畫會錯位
    if "name" not in font:
        font["name"] = newTable("name")
        font["name"].names = []
    name_table = font["name"]
    for name_id, value in ((1, family), (2, "Regular"), (4, family), (6, re.sub(r"[^A-Za-z0-9-]", "", family) or "PDFFont")):
        if not name_table.getDebugName(name_id):
            name_table.setName(value, name_id, 3, 1, 0x409)
    buffer = io.BytesIO()
    font.save(buffer)
    return buffer.getvalue()


def face_for(key, pdf, page_number, font_name):
    """PDF 第 page_number 頁用到的字型 font_name(含子集前綴)→ 可以拿來排版的 FontFace;不能用時回傳 None。
    key 用來快取(通常是來源檔的路徑)。"""
    cache_key = (key, font_name)
    if cache_key in _cache:
        return _cache[cache_key]
    face = None
    try:
        page = pdf.pages[page_number]
        node, resources = page.obj, None
        while node is not None and resources is None:
            resources = node.get("/Resources")
            node = node.get("/Parent")
        font_dict = _find_font(resources, font_name)
        if font_dict is not None:
            descriptor = (font_dict.DescendantFonts[0].get("/FontDescriptor")
                          if str(font_dict.get("/Subtype", "")) == "/Type0" else font_dict.get("/FontDescriptor"))
            program = _program(descriptor)
            if program is not None:
                face = _make_face(program, font_dict, font_name)
            elif descriptor is not None and str(font_dict.get("/Subtype", "")) in ("/Type1", "/MMType1"):
                face = _make_type1_face(font_dict, descriptor, font_name)
    except Exception:
        face = None
    _cache[cache_key] = face
    return face


def _family(font_name):
    return font_name.split("+", 1)[1] if len(font_name) > 7 and font_name[6] == "+" else font_name


def _make_type1_face(font_dict, descriptor, font_name):
    family = _family(font_name)
    key = descriptor.get("/FontFile3") or descriptor.get("/FontFile")
    if key is None:
        return None
    raw = key.read_raw_bytes() + repr(font_dict.get("/Encoding")).encode("utf-8")
    digest = hashlib.sha1(raw + b"type1-v1").hexdigest()[:16]
    folder = cache_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{digest}.otf"
    if not path.is_file():
        made = _type1_font(font_dict, descriptor, family)
        if made is None:
            return None
        temp = path.with_suffix(".part")
        temp.write_bytes(made[0])
        temp.replace(path)
    return fonts.FontFace(f"pdf:{path.name}", f"{family}(原檔字型)", "pdf", path)


def _make_face(program, font_dict, font_name):
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(program), lazy=True)
    status = fonts.embed_status(font["OS/2"].fsType) if "OS/2" in font else "ok"
    glyph_map = _glyph_map(font_dict, font)
    font.close()
    if status != "ok" or len(glyph_map) < 1:
        return None                 # 字型授權不允許拿來編輯,或對照不出任何字
    family = _family(font_name)
    digest = hashlib.sha1(program + repr(sorted(glyph_map.items())).encode("utf-8")).hexdigest()[:16]
    folder = cache_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{digest}.ttf"
    if not path.is_file():
        data = _rebuild(program, glyph_map, family)
        temp = path.with_suffix(".part")
        temp.write_bytes(data)
        temp.replace(path)
    return fonts.FontFace(f"pdf:{path.name}", f"{family}(原檔字型)", "pdf", path)
