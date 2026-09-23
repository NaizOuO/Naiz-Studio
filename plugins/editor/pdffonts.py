"""取出 PDF 裡內嵌的原字型,改字、編輯段落時用它,新字才會和原字長得一模一樣。

PDF 裡的字型大多只留下用到的字(子集),而且對照表常常被拿掉或改成自己的編號,不能直接拿來用。
這裡把字型檔取出來,依 PDF 記的「代碼 → 文字」(ToUnicode)與「代碼 → 字形」重建一份正常的對照表,
存成字型檔放在 fonts\\pdf\\,之後就能和電腦上的字型一樣排版、預覽、嵌入。
原檔沒有用到的字不在子集裡,打這些字時會自動改用相近的字型補上。
"""

import hashlib
import io
import re

import pikepdf

from core import paths

from . import fonts

_cache = {}         # (來源, 字型名稱) → FontFace 或 None


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
        if str(font.get("/BaseFont", ""))[1:] == name:
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
    except Exception:
        face = None
    _cache[cache_key] = face
    return face


def _make_face(program, font_dict, font_name):
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(program), lazy=True)
    status = fonts.embed_status(font["OS/2"].fsType) if "OS/2" in font else "ok"
    glyph_map = _glyph_map(font_dict, font)
    font.close()
    if status != "ok" or len(glyph_map) < 1:
        return None                 # 字型授權不允許拿來編輯,或對照不出任何字
    family = font_name.split("+", 1)[1] if len(font_name) > 7 and font_name[6] == "+" else font_name
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
