"""找出頁面上的段落:哪些字是同一行、哪些行是同一段,以及對齊方式、縮排、行距。

改字時點一下文字就能整段修改,靠的就是這裡。座標是 PDF 使用者座標(左下為原點、往上為正)。
"""

import ctypes
import statistics
from collections import Counter
from dataclasses import dataclass, field

import pypdfium2.raw as raw

from core import pdfium, textrules
from core.textrules import is_cjk


@dataclass
class Char:
    index: int              # 在 PDFium 文字頁裡的編號
    text: str
    x: float                # 起點(底線上)
    y: float
    size: float
    right: float            # 字的右緣(下一個字的起點,或字框右緣)
    font: str
    color: tuple


@dataclass
class TextLine:
    chars: list = field(default_factory=list)

    @property
    def left(self):
        return self.chars[0].x

    @property
    def right(self):
        return self.chars[-1].right

    @property
    def baseline(self):
        return statistics.median(c.y for c in self.chars)

    @property
    def size(self):
        return statistics.median(c.size for c in self.chars if not c.text.isspace())             if any(not c.text.isspace() for c in self.chars) else self.chars[0].size

    @property
    def text(self):
        return "".join(c.text for c in self.chars)

    def box(self):
        size = self.size
        return self.left, self.baseline - size * 0.22, self.right, self.baseline + size * 0.88


@dataclass
class Paragraph:
    lines: list
    align: str = "left"
    pitch: float = 0.0              # 相鄰兩行底線的距離;只有一行時是 0
    offsets: tuple = (0.0, 0.0)     # 第一行、其他行比段落左緣多縮進去多少

    @property
    def first(self):
        return self.lines[0].chars[0].index

    @property
    def last(self):
        return self.lines[-1].chars[-1].index

    @property
    def left(self):
        return min(line.left for line in self.lines)

    @property
    def right(self):
        return max(line.right for line in self.lines)

    @property
    def size(self):
        return statistics.median(line.size for line in self.lines)

    def boxes(self):
        return [line.box() for line in self.lines]

    def bounds(self):
        boxes = self.boxes()
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))

    def contains(self, index):
        return any(c.index == index for line in self.lines for c in line.chars)

    @property
    def text(self):
        """整段的文字:行和行之間中文直接接起來,英文補一個空白。"""
        parts = []
        for line in self.lines:
            text = line.text.strip()
            if parts and parts[-1] and text:
                previous = parts[-1][-1]
                if not (is_cjk(previous) or is_cjk(text[0])):
                    parts.append(" ")
            parts.append(text)
        return "".join(parts)

    @property
    def chars(self):
        return [c for line in self.lines for c in line.chars]

    def sample(self):
        """代表這一段樣式的字:最多字用的字型裡的一個字;段落有中文時挑中文字(判斷有沒有襯線才準)。"""
        font, _ = self.style()
        chars = [c for line in self.lines for c in line.chars if not c.text.isspace() and c.font == font]
        chars = chars or [c for line in self.lines for c in line.chars if not c.text.isspace()]
        return next((c for c in chars if is_cjk(c.text)), chars[0]).index

    def style(self):
        """最多字用的 (字型名稱, 顏色)。"""
        chars = [c for line in self.lines for c in line.chars if not c.text.isspace()]
        font = Counter(c.font for c in chars).most_common(1)[0][0] if chars else ""
        color = Counter(c.color for c in chars).most_common(1)[0][0] if chars else (0, 0, 0)
        return font, color


def script_fonts(chars):
    """中文字、英數字各自最多字用的字型:{"cjk": (字型名稱, 代表的字), "latin": (...)};沒有那種字就不列。"""
    result = {}
    for script, test in (("cjk", lambda ch: is_cjk(ch)), ("latin", lambda ch: not is_cjk(ch))):
        picked = [c for c in chars if not c.text.isspace() and test(c.text)]
        if picked:
            name = Counter(c.font for c in picked).most_common(1)[0][0]
            result[script] = (name, next(c for c in picked if c.font == name).index)
    return result


def read_chars(lookup):
    """一頁所有的字(不含換行);lookup 是 pdfium.TextLookup。"""
    chars = []
    buffer = ctypes.create_string_buffer(256)
    flags = ctypes.c_int()
    with pdfium.LOCK:
        handle = lookup.text.raw
        for index in range(lookup.count):
            code = raw.FPDFText_GetUnicode(handle, index)
            if code in (0, 10, 13, 0xFFFE, 0xFFFF):
                continue
            x, y = ctypes.c_double(), ctypes.c_double()
            raw.FPDFText_GetCharOrigin(handle, index, ctypes.byref(x), ctypes.byref(y))
            size = pdfium.char_size(handle, index) or 10.0
            _, _, right, _ = lookup.text.get_charbox(index)
            raw.FPDFText_GetFontInfo(handle, index, buffer, 256, ctypes.byref(flags))
            red, green, blue, alpha = (ctypes.c_uint() for _ in range(4))
            raw.FPDFText_GetFillColor(handle, index, *[ctypes.byref(v) for v in (red, green, blue, alpha)])
            chars.append(Char(index, chr(code), x.value, y.value, size, right,
                              buffer.value.decode("utf-8", "replace"), (red.value, green.value, blue.value)))
    # 字的右緣:同一行下一個字的起點最準;行尾用字框,但不超過一個字級(有些字型的字框大很多)
    for char, following in zip(chars, chars[1:] + [None]):
        if following is not None and abs(following.y - char.y) < char.size * 0.3 and following.x > char.x:
            char.right = min(following.x, char.x + char.size * 1.5)
        elif is_cjk(char.text):
            char.right = char.x + char.size         # 中文是全形字:「：」「，」的字形只佔左半邊,但字寬是一整格
        else:
            char.right = min(max(char.right, char.x + char.size * 0.3), char.x + char.size * 1.05)
    return chars


def group_lines(chars):
    """依文字順序把字接成行:底線差不多高、往右走的算同一行;中間空很大一段(分欄、表格)就斷開。"""
    lines = []
    for char in chars:
        if lines:
            line = lines[-1]
            # 空白不拿來比:PDFium 自己補的空白字級只有 1、位置也不準
            last = next((c for c in reversed(line.chars) if not c.text.isspace()), line.chars[-1])
            size = max(char.size, last.size)
            if char.text.isspace():
                if abs(char.y - last.y) < size * 0.35:
                    line.chars.append(char)
                    continue
            else:
                same = abs(char.y - last.y) < size * 0.35 and char.x >= last.x + last.size * 0.3 - size * 0.3
                if same and char.x - last.right < size * 2.0:
                    line.chars.append(char)
                    continue
        lines.append(TextLine([char]))
    for line in lines:          # 行首行尾的空白不算
        while len(line.chars) > 1 and line.chars[-1].text.isspace():
            line.chars.pop()
        while len(line.chars) > 1 and line.chars[0].text.isspace():
            line.chars.pop(0)
    return [line for line in lines if line.text.strip()]


def _same_paragraph(paragraph, line, column_right):
    last = paragraph[-1]
    size = last.size
    if textrules.starts_list(line.text):
        return False
    if textrules.ends_paragraph(last.text, last.right, line.right, column_right, size):
        return False
    if abs(line.size - size) > size * 0.15:
        return False
    drop = last.baseline - line.baseline
    if not size * 0.9 <= drop <= size * 1.8:       # 一般行距約是字級的 1.2～1.7 倍
        return False
    if len(paragraph) >= 2:
        pitch = paragraph[-2].baseline - last.baseline
        if abs(drop - pitch) > size * 0.25:
            return False
    left = min(l.left for l in paragraph)
    right = max(l.right for l in paragraph)
    # 左右要有重疊;新的一行從很右邊開始(另一欄)不算
    if line.left > right - size or line.right < left + size:
        return False
    if len(paragraph) >= 2 and abs(line.left - paragraph[-1].left) > size * 0.5 \
            and abs(line.left - paragraph[1].left) > size * 0.5:
        return False
    return True


def _layout(lines):
    """對齊方式與縮排。"""
    if len(lines) == 1:
        return "left", (0.0, 0.0)
    size = statistics.median(line.size for line in lines)
    tolerance = max(1.5, size * 0.3)
    left = min(line.left for line in lines)
    right = max(line.right for line in lines)
    body = lines[1:]
    lefts_even = all(abs(line.left - body[0].left) < tolerance for line in body)
    full_rights = all(abs(line.right - right) < tolerance for line in lines[:-1])
    centers = [(line.left + line.right) / 2 for line in lines]
    if all(abs(c - centers[0]) < tolerance for c in centers) and not lefts_even:
        return "center", (0.0, 0.0)
    if all(abs(line.right - right) < tolerance for line in lines) and not lefts_even:
        return "right", (0.0, 0.0)
    offsets = (lines[0].left - left, body[0].left - left) if lefts_even else (0.0, 0.0)
    return ("justify" if full_rights else "left"), offsets


def find_paragraphs(lookup):
    lines = group_lines(read_chars(lookup))
    columns = dict(zip(map(id, lines), textrules.column_rights([(l.left, l.right, l.size) for l in lines])))
    paragraphs = []
    current = []
    for line in lines:
        if current and _same_paragraph(current, line, columns[id(current[-1])]):
            current.append(line)
            continue
        if current:
            paragraphs.append(current)
        current = [line]
    if current:
        paragraphs.append(current)
    result = []
    for lines in paragraphs:
        align, offsets = _layout(lines)
        pitch = statistics.median(a.baseline - b.baseline for a, b in zip(lines, lines[1:])) if len(lines) > 1 else 0.0
        result.append(Paragraph(lines, align, pitch, offsets))
    return result


def paragraph_at(paragraphs, index):
    return next((p for p in paragraphs if p.contains(index)), None)
