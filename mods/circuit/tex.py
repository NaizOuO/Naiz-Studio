"""小型 LaTeX 排版:把標籤裡的數學式排成一串「文字 + 線條」,畫面預覽、PNG、SVG 都用它。

程式包成 exe 時沒有 matplotlib,所以自己排;支援電路圖標籤常用的寫法:
$...$ 包起來的數學式(沒有 $ 時,含 _ ^ \\ 的字自動當成數學式,例如 R_1、V_{in})、上下標、
\\frac、\\sqrt、\\overline、\\hat、\\vec、\\dot、\\mathrm、\\text、希臘字母與常見符號。
數學式裡的英文字母用斜體,數字與符號用正體,和 LaTeX 一樣。
"""

import re
from pathlib import Path

from PIL import ImageFont

FONT_DIR = Path(r"C:\Windows\Fonts")
# 西文用 Times New Roman;數學符號用 Cambria Math(cambria.ttc 的第 2 個字型);中文用微軟正黑體
FONT_FILES = {"serif": ("times.ttf", 0), "italic": ("timesi.ttf", 0), "symbol": ("cambria.ttc", 1),
              "cjk": ("msjh.ttc", 0)}

GREEK = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
         "eta": "η", "theta": "θ", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π",
         "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
         "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ",
         "Omega": "Ω"}
SYMBOLS = {"cdot": "·", "times": "×", "pm": "±", "mp": "∓", "infty": "∞", "to": "→", "rightarrow": "→",
           "leftarrow": "←", "leftrightarrow": "↔", "Rightarrow": "⇒", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥",
           "neq": "≠", "ne": "≠", "approx": "≈", "sim": "∼", "propto": "∝", "partial": "∂", "nabla": "∇",
           "int": "∫", "oint": "∮", "sum": "∑", "prod": "∏", "circ": "°", "degree": "°", "prime": "′", "ldots": "…",
           "cdots": "⋯", "parallel": "∥", "perp": "⊥", "angle": "∠", "ell": "ℓ", "hbar": "ħ", "equiv": "≡",
           "div": "÷", "star": "⋆", "uparrow": "↑", "downarrow": "↓", "in": "∈", "Re": "ℜ", "Im": "ℑ",
           "%": "%", "&": "&", "#": "#", "$": "$", "{": "{", "}": "}", "_": "_"}
SPACES = {",": 0.17, ":": 0.22, ";": 0.28, "!": -0.17, "quad": 1.0, "qquad": 2.0, " ": 0.25}
IGNORED = {"left", "right", "big", "Big", "displaystyle", "limits", "nolimits"}
ROMAN = {"mathrm", "text", "textrm", "operatorname", "rm", "mathbf", "textbf", "mathsf"}
ACCENTS = {"hat": "^", "vec": "→", "dot": "·", "ddot": "¨", "tilde": "~", "bar": None, "overline": None}
BINARY = set("+−=<>±×÷≤≥≠≈∼∝→←≡")
UNICODE_TEX = {char: "\\" + name for name, char in list(GREEK.items()) + [("Omega", "Ω"), ("mu", "μ")]}

ASCENT, DESCENT = 0.72, 0.24        # 字的上緣、下緣(字級的倍數)
SCRIPT = 0.7                        # 上下標縮小的比例
AXIS = 0.26                         # 分數線的高度

_fonts = {}


def font(kind, size):
    size = max(4, int(round(size)))
    key = (kind, size)
    if key not in _fonts:
        name, index = FONT_FILES[kind]
        try:
            _fonts[key] = ImageFont.truetype(str(FONT_DIR / name), size, index=index)
        except OSError:
            _fonts[key] = ImageFont.load_default(size)
    return _fonts[key]


class Box:
    """排好的一段:寬、基線以上的高、基線以下的深,和內容 items。
    items:("t", x, 往上的位移, 文字, 字型, 字級) 或 ("l", x1, y1, x2, y2, 粗細),位置都相對於這段的起點與基線。"""

    def __init__(self, width=0.0, ascent=0.0, descent=0.0, items=None):
        self.width, self.ascent, self.descent = width, ascent, descent
        self.items = items or []

    def add(self, other, dx, dy=0.0):
        """把 other 放在 (dx, dy) 的位置(dy 往上為正)。"""
        for item in other.items:
            if item[0] == "t":
                self.items.append(("t", item[1] + dx, item[2] + dy) + item[3:])
            else:
                self.items.append(("l", item[1] + dx, item[2] + dy, item[3] + dx, item[4] + dy, item[5]))
        self.ascent = max(self.ascent, other.ascent + dy)
        self.descent = max(self.descent, other.descent - dy)


def _font_kind(ch, italic):
    if ord(ch) > 0x2E80:
        return "cjk"
    if ch.isascii() and ch.isalpha():
        return "italic" if italic else "serif"
    if ch.isdigit() or ch.isspace() or ch in ".,:;!?()[]/|'\"-":
        return "serif"
    if "α" <= ch <= "ω":
        return "italic" if italic else "serif"
    return "serif" if ch.isalpha() or ch in "%&#$·°′…" else "symbol"


def _glyphs(text, size, mode):
    """一串字排成一行。mode:italic 數學式的變數(斜體)、math 數學式的數字與符號(正體,運算符號兩邊留空)、
    plain 一般文字。"""
    box = Box(ascent=ASCENT * size, descent=DESCENT * size)
    x = 0.0
    for ch in text:
        if ch == "-" and mode != "plain":
            ch = "−"
        kind = _font_kind(ch, mode == "italic")
        width = font(kind, size).getlength(ch)
        pad = size * 0.2 if ch in BINARY and mode != "plain" else 0
        box.items.append(("t", x + pad, 0.0, ch, kind, size))
        x += width + pad * 2
    box.width = x
    return box


# ------------------------------------------------------------ 解析

def _read_group(text, i):
    """從 text[i] 讀一個參數:{...} 或單一個字(或 \\指令);回傳 (內容, 下一個位置)。"""
    while i < len(text) and text[i] == " ":
        i += 1
    if i >= len(text):
        return "", i
    if text[i] == "{":
        depth, j = 0, i
        while j < len(text):
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    return text[i + 1:j], j + 1
            j += 1
        return text[i + 1:], len(text)
    if text[i] == "\\":
        match = re.match(r"\\([A-Za-z]+|.)", text[i:])
        return match.group(0), i + len(match.group(0))
    return text[i], i + 1


def _math(text, size, roman=False):
    """排一段數學式。"""
    out = Box(ascent=ASCENT * size, descent=DESCENT * size)
    x = 0.0
    i = 0
    last = None             # 上一個原子(上下標接在它後面)
    while i < len(text):
        ch = text[i]
        atom = None
        if ch in "_^":
            first, i = _read_group(text, i + 1)
            sub = sup = None
            if ch == "_":
                sub = first
            else:
                sup = first
            if i < len(text) and text[i] in "_^" and text[i] != ch:
                second, i = _read_group(text, i + 1)
                sub, sup = (sub, second) if sup is None else (second, sup)
            base_ascent = last.ascent if last else ASCENT * size
            script = max(size * SCRIPT, 5)
            width = 0.0
            if sup is not None:
                box = _math(sup, script, roman)
                out.add(box, x, max(size * 0.38, base_ascent - box.ascent * 0.6))
                width = box.width
            if sub is not None:
                box = _math(sub, script, roman)
                out.add(box, x, -size * (0.3 if sup is not None else 0.2))
                width = max(width, box.width)
            x += width + size * 0.03
            last = None
            continue
        if ch == "{":
            group, i = _read_group(text, i)
            atom = _math(group, size, roman)
        elif ch == "\\":
            match = re.match(r"\\([A-Za-z]+|.)", text[i:])
            name = match.group(1)
            i += len(match.group(0))
            if name in SPACES:
                x += SPACES[name] * size
                continue
            if name in IGNORED:
                continue
            if name in ("frac", "dfrac", "tfrac"):
                top, i = _read_group(text, i)
                bottom, i = _read_group(text, i)
                atom = _fraction(top, bottom, size, roman)
            elif name == "sqrt":
                inside, i = _read_group(text, i)
                atom = _root(inside, size, roman)
            elif name in ACCENTS:
                inside, i = _read_group(text, i)
                atom = _accent(inside, ACCENTS[name], size, roman)
            elif name in ROMAN:
                inside, i = _read_group(text, i)
                atom = _plain(inside, size) if name.startswith("text") else _math(inside, size, roman=True)
            elif name in GREEK:
                atom = _glyphs(GREEK[name], size, "italic" if not roman and GREEK[name].islower() else "math")
            elif name in SYMBOLS:
                atom = _glyphs(SYMBOLS[name], size, "math")
            else:                                   # 不認得的指令:照原文用正體顯示
                atom = _glyphs(name, size, "math")
        elif ch == " ":
            i += 1
            continue
        else:
            i += 1
            atom = _glyphs(ch, size, "italic" if not roman and ch.isalpha() else "math")
        out.add(atom, x)
        x += atom.width
        last = atom
    out.width = x
    return out


def _fraction(top, bottom, size, roman):
    small = max(size * 0.8, 5)
    num, den = _math(top, small, roman), _math(bottom, small, roman)
    width = max(num.width, den.width) + size * 0.2
    axis, gap = AXIS * size, size * 0.12
    box = Box(width + size * 0.1, 0, 0)
    box.add(num, size * 0.05 + (width - num.width) / 2, axis + gap + num.descent)
    box.add(den, size * 0.05 + (width - den.width) / 2, axis - gap - den.ascent)
    box.items.append(("l", size * 0.05, axis, size * 0.05 + width, axis, max(1.0, size * 0.05)))
    return box


def _root(inside, size, roman):
    body = _math(inside, size, roman)
    top = body.ascent + size * 0.12
    bottom = -body.descent
    lead = size * 0.5
    thick = max(1.0, size * 0.05)
    box = Box(lead + body.width + size * 0.1, top + thick, body.descent)
    box.add(body, lead)
    box.items += [("l", 0, (top + bottom) * 0.45, lead * 0.3, (top + bottom) * 0.55, thick),
                  ("l", lead * 0.3, (top + bottom) * 0.55, lead * 0.6, bottom, thick),
                  ("l", lead * 0.6, bottom, lead * 0.95, top, thick),
                  ("l", lead * 0.95, top, lead + body.width + size * 0.05, top, thick)]
    return box


def _accent(inside, mark, size, roman):
    body = _math(inside, size, roman)
    top = body.ascent + size * 0.06
    box = Box(body.width, top, body.descent)
    box.add(body, 0)
    if mark is None:                    # 上橫線
        box.items.append(("l", size * 0.05, top, body.width, top, max(1.0, size * 0.05)))
        box.ascent = top + size * 0.05
    else:
        glyph = _glyphs(mark, size * 0.8, "math")
        box.add(glyph, (body.width - glyph.width) / 2 + size * 0.05, top - size * 0.45)
    return box


def _plain(text, size):
    """一般文字(不是數學式):正體,空白照原樣。"""
    return _glyphs(text, size, "plain")


# ------------------------------------------------------------ 對外

def is_math(token):
    """沒有用 $ 的標籤裡,含上下標的字當成數學式(R_1、V_{in})。"""
    return bool(re.search(r"[_^][^_^\s]", token))


def plain_symbols(text):
    """一般文字裡的 \\Omega、\\mu 這類指令換成字母本身(正體,例如 10 k\\Omega → 10 kΩ)。"""
    text = re.sub(r"\\[,;:!]", lambda m: "" if m.group(0) == "\\!" else " ", text)     # \, \; 這類間距換成空白
    return re.sub(r"\\([A-Za-z]+)", lambda m: GREEK.get(m.group(1), SYMBOLS.get(m.group(1), m.group(0))), text)


def segments(text):
    """標籤拆成 [(是不是數學式, 內容)]。有 $ 時照 $ 分段;沒有的話,含上下標的字自動當數學式。"""
    if "$" in text.replace("\\$", ""):
        parts = re.split(r"(?<!\\)\$", text)
        return [(index % 2 == 1, part) for index, part in enumerate(parts) if part]
    out = []
    for token in re.split(r"(\s+)", text):
        if token:
            out.append((is_math(token), token))
    return out


_layouts = {}


def layout(text, size):
    """排好整個標籤,回傳 Box(單位是像素)。"""
    key = (text, round(size, 2))
    if key in _layouts:
        return _layouts[key]
    box = Box(ascent=ASCENT * size, descent=DESCENT * size)
    x = 0.0
    for math_mode, part in segments(text):
        if math_mode:
            piece = _math(part, size)
        else:
            piece = _plain(plain_symbols(part.replace("\\$", "$").replace("\\%", "%")), size)
        box.add(piece, x)
        x += piece.width
    box.width = x
    if len(_layouts) > 2000:
        _layouts.clear()
    _layouts[key] = box
    return box


def baseline(box, y, va):
    """以對齊點算出基線的 y(像素,往下為正)。"""
    if va == "top":
        return y + box.ascent
    if va == "bottom":
        return y - box.descent
    return y + (box.ascent - box.descent) / 2
