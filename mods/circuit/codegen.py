"""把電路圖寫成可以貼進 Obsidian 筆記的 Python(matplotlib)程式碼。

格式照筆記的慣例:```python 區塊開頭是 #| plot、#| caption:;程式開頭附上這張圖用到的元件小工具,
下面每一行放一個元件,看得懂也改得動。只用 matplotlib,不需要另外安裝套件;字型、字級交給筆記的設定,
顏色照選的圖片風格寫在開頭,想改顏色改那幾行就好。
"""

import re

from . import draw, model, parts, tex

# 筆記的字級是 11pt;一單位(兩格)0.6 英寸時,圖不用縮放,標籤就和畫面上的比例一樣
INCH_PER_UNIT = 0.6
MAX_WIDTH = 6.5             # 整頁寬;更寬的圖整張縮小
LINE_WIDTH = 1.2

HELPERS = '''import math
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Polygon

LW = {lw}  # 線寬
WIRE, PART, SOURCE = "{wire}", "{part}", "{source}"  # 導線、元件、電源的顏色
LABEL, VALUE, BG = "{label}", "{value}", "{bg}"  # 名稱、數值、底色


def _put(p, at, ux, uy):
    """元件自己的座標 p 換成圖上的座標:at 是原點,ux、uy 是兩個軸的方向。"""
    return (at[0] + p[0] * ux[0] + p[1] * uy[0], at[1] + p[0] * ux[1] + p[1] * uy[1])


def _draw(ax, shapes, at, ux, uy, color):
    for s in shapes:
        if s[0] in "LD":
            xs, ys = zip(*[_put(p, at, ux, uy) for p in s[1]])
            ax.plot(xs, ys, color=color, lw=LW, solid_capstyle="round", solid_joinstyle="round",
                    linestyle="--" if s[0] == "D" else "-")
        elif s[0] == "P":
            ax.add_patch(Polygon([_put(p, at, ux, uy) for p in s[1]], closed=True, fill=s[2], fc=color, ec=color,
                                 lw=LW, joinstyle="round"))
        elif s[0] == "C":  # 填 "bg" 的是空心圈(端點),用底色蓋住接進來的導線
            ax.add_patch(Circle(_put(s[1], at, ux, uy), s[2], fill=bool(s[3]), fc=BG if s[3] == "bg" else color,
                                ec=color, lw=LW, zorder=3 if s[3] == "bg" else 1))
        elif s[0] == "T":
            ax.text(*_put(s[1], at, ux, uy), s[2], ha="center", va="center", fontsize="small", color=color)
        elif s[0] == "A":  # 箭頭:線加實心三角形
            (x1, y1), (x2, y2) = _put(s[1], at, ux, uy), _put(s[2], at, ux, uy)
            n = math.hypot(x2 - x1, y2 - y1)
            dx, dy = (x2 - x1) / n, (y2 - y1) / n
            head = min({arrow_len}, n * 0.8)
            ax.plot([x1, x2 - dx * head * 0.6], [y1, y2 - dy * head * 0.6], color=color, lw=LW,
                    solid_capstyle="round")
            bx, by, w = x2 - dx * head, y2 - dy * head, {arrow_half}
            ax.add_patch(Polygon([(x2, y2), (bx - dy * w, by + dx * w), (bx + dy * w, by - dx * w)], fc=color, lw=0))


def _label(ax, p, text, vx, vy, shift=None, color=LABEL):
    """標籤放在 p(shift 是拖曳過的位移),(vx, vy) 是它在元件的哪個方向,用來決定對齊方式;(0, 0) 是置中。"""
    if not text:
        return
    if vx == vy == 0:
        ha, va = "center", "center"
    elif vx == "left":  # 虛線框:標在左上角外面
        ha, va = "left", "bottom"
    elif abs(vy) >= abs(vx):
        ha, va = "center", ("bottom" if vy > 0 else "top")
    else:
        ha, va = ("left" if vx > 0 else "right"), "center"
    if shift:
        p = (p[0] + shift[0], p[1] + shift[1])
    ax.text(*p, text, ha=ha, va=va, color=color)
'''

TWO = '''

def _two(ax, a, b, part, label="", value="", flip=False, shift=None, vshift=None):
    """兩端元件:本體放在 a、b 中間,兩邊補上引線;名稱和數值放在兩側。"""
    n = math.hypot(b[0] - a[0], b[1] - a[1])
    ux = ((b[0] - a[0]) / n, (b[1] - a[1]) / n)
    uy = (-ux[1], ux[0])
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    half, low, high = part["half"], part["low"], part["high"]
    leads = [("L", [(-n / 2, 0), (-half, 0)]), ("L", [(half, 0), (n / 2, 0)])] if n > 2 * half else []
    _draw(ax, leads + part["shapes"], mid, ux, uy, part["color"])
    if part.get("inside"):
        _label(ax, mid, label, 0, 0, shift)
        _label(ax, (mid[0], mid[1] - 0.45), value, 0, -1, vshift, VALUE)
        return
    side = (1 if uy[1] > 0 else -1) if abs(uy[1]) > 0.3 else (1 if uy[0] > 0 else -1)  # 橫放在上、直放在右
    side = -side if flip else side
    for text, sign, move, color in ((label, side, shift, LABEL), (value, -side, vshift, VALUE)):
        reach = (high if sign > 0 else -low) + {gap}
        _label(ax, _put((0, sign * reach), mid, ux, uy), text, uy[0] * sign, uy[1] * sign, move, color)
'''

MULTI = '''

def _multi(ax, at, part, rot=0, mirror=False, label="", value="", flip=False, shift=None, vshift=None):
    """多端元件:放在 at,rot 是逆時針轉幾個 90 度,mirror 是左右鏡像。"""
    c, s = [(1, 0), (0, 1), (-1, 0), (0, -1)][rot % 4]
    k = -1 if mirror else 1
    ux, uy = (c * k, s * k), (-s, c)
    _draw(ax, part["shapes"], at, ux, uy, part["color"])
    x0, y0, x1, y1 = part["box"]
    spot = part.get("spot") or (x1 + 0.15, (y0 + y1) / 2)
    if flip:
        spot = (x0 - 0.15, spot[1]) if not part.get("spot") else (spot[0], -spot[1])
    p, mid = _put(spot, at, ux, uy), _put(((x0 + x1) / 2, (y0 + y1) / 2), at, ux, uy)
    vx, vy = p[0] - mid[0], p[1] - mid[1]
    _label(ax, p, label, vx, vy, shift)
    if value:
        if label and abs(vy) >= abs(vx) and vy > 0:
            vx, vy = 0, -1
        _label(ax, (p[0], p[1] - {size} * 1.25 * bool(label)), value, vx, vy, vshift, VALUE)
'''

WIRE = '''

def wire(ax, *points):
    xs, ys = zip(*points)
    ax.plot(xs, ys, color=WIRE, lw=LW, solid_capstyle="round", solid_joinstyle="round")
'''

DOT = '''

def dot(ax, p):
    ax.add_patch(Circle(p, {dot}, fc=WIRE, lw=0, zorder=3))
'''

TEXT = '''

def text(ax, at, s):
    """單純的文字(可以寫 LaTeX)。"""
    ax.text(*at, s, ha="center", va="center", color=LABEL)
'''

VOLTAGE = '''

def voltage(ax, a, b, label="", shift=None):
    """電壓標註:a 端 +、b 端 −,名稱在中間。"""
    ax.text(*a, "$+$", ha="center", va="center", color=PART)
    ax.text(*b, "$-$", ha="center", va="center", color=PART)
    _label(ax, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), label, 0, 0, shift)
'''

CURRENT = '''

def current(ax, a, b, label="", flip=False, shift=None):
    """電流箭頭:在 a、b 中間畫一個從 a 指向 b 的箭頭。"""
    n = math.hypot(b[0] - a[0], b[1] - a[1])
    ux = ((b[0] - a[0]) / n, (b[1] - a[1]) / n)
    uy = (-ux[1], ux[0])
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    _draw(ax, [("A", (-0.22, 0), (0.22, 0))], mid, ux, uy, PART)
    side = (1 if uy[1] > 0 else -1) if abs(uy[1]) > 0.3 else (1 if uy[0] > 0 else -1)
    side = -side if flip else side
    _label(ax, _put((0, 0.2 * side), mid, ux, uy), label, uy[0] * side, uy[1] * side, shift)
'''

LOOP = '''

def loop(ax, a, b, label="", square=0.0, counter=False, start=120, end=180, shift=None):
    """迴路電流:a、b 是外框的對角;square 0 是橢圓、1 接近長方形;counter 為逆時針;
    start、end 是起點與箭頭的角度(度,從右邊逆時針量)。"""
    cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    rx, ry = abs(b[0] - a[0]) / 2, abs(b[1] - a[1]) / 2
    power, sign = 2 / (2 + square * 10), 1 if counter else -1
    sweep = (end - start) % 360 if counter else (start - end) % 360
    sweep = sweep if sweep > 5 else 355
    pts = []
    for i in range(97):
        t = math.radians(start + sign * sweep * i / 96)
        c, s = math.cos(t), math.sin(t)
        pts.append((cx + rx * math.copysign(abs(c) ** power, c), cy + ry * math.copysign(abs(s) ** power, s)))
    _draw(ax, [("L", pts[:-3]), ("A", pts[-5], pts[-1])], (0, 0), (1, 0), (0, 1), PART)
    _label(ax, (cx, cy), label, 0, 0, shift)
'''

BLOCK = '''

def block(ax, a, b, label=""):
    """方塊:a、b 是對角,字在正中間。"""
    (x1, y1), (x2, y2) = a, b
    ax.add_patch(Polygon([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], closed=True, fill=False, ec=PART, lw=LW))
    ax.text((x1 + x2) / 2, (y1 + y2) / 2, label, ha="center", va="center", color=LABEL)
'''

FRAME = '''

def frame(ax, a, b, label="", shift=None):
    """虛線框:a、b 是對角。"""
    (x1, y1), (x2, y2) = a, b
    ax.plot([x1, x2, x2, x1, x1], [y1, y1, y2, y2, y1], color=PART, lw=LW, linestyle="--")
    _label(ax, (min(x1, x2), max(y1, y2) + {gap}), label, "left", 0, shift)
'''

ANNOTATIONS = {"voltage": VOLTAGE, "current": CURRENT, "loop": LOOP, "frame": FRAME, "text": TEXT, "block": BLOCK}


def _num(value):
    value = round(float(value), 4)
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip("0")


def _point(p):
    return f"({_num(p[0])}, {_num(p[1])})"


def to_tex(text):
    """標籤換成 matplotlib 的寫法。已經用 $...$ 寫好的照原樣;沒有 $ 的話,含上下標的字放進 $...$,
    Ω、μ 這類字母換成 \\Omega、\\mu;數學式外面的 % & # 加反斜線(筆記用 LaTeX 排字)。"""
    if "$" in text.replace("\\$", ""):
        return text
    pieces = []
    for math_mode, part in tex.segments(text):
        if math_mode:
            body = "".join(tex.UNICODE_TEX.get(ch, ch) + (" " if ch in tex.UNICODE_TEX else "") for ch in part)
            pieces.append("$" + re.sub(r" (?=[_^}\s]|$)", "", body) + "$")
        else:
            plain = re.sub(r"(?<!\\)([%&#])", r"\\\1", tex.plain_symbols(part))
            pieces.append("".join(f"${tex.UNICODE_TEX[ch]}$" if ch in tex.UNICODE_TEX else ch for ch in plain))
    return "".join(pieces)


def _text_literal(text):
    value = to_tex(text)
    if "\\" in value and not value.endswith("\\") and '"' not in value:
        return f'r"{value}"'
    return repr(value)


def _shape_text(value):
    """元件裡的 + − Ω 這類符號。"""
    return {"+": "$+$", "−": "$-$", "Ω": r"$\Omega$"}.get(value, value)


def _raw_literal(value):
    return f'r"{value}"' if "\\" in value else f'"{value}"'


def _shapes_literal(shapes, indent="    "):
    """形狀清單寫成程式碼,一個形狀一行。"""
    lines = []
    for shape in shapes:
        kind = shape[0]
        if kind in ("L", "D"):
            body = f'("{kind}", [{", ".join(_point(p) for p in shape[1])}])'
        elif kind == "P":
            body = f'("P", [{", ".join(_point(p) for p in shape[1])}], {shape[2]})'
        elif kind == "C":
            body = f'("C", {_point(shape[1])}, {_num(shape[2])}, {shape[3]!r})'
        elif kind == "T":
            body = f'("T", {_point(shape[1])}, {_raw_literal(_shape_text(shape[2]))}, {_num(shape[3])})'
        else:
            body = f'("A", {_point(shape[1])}, {_point(shape[2])})'
        lines.append(indent + body + ",")
    return "[\n" + "\n".join(lines) + "\n]"


def _constant(kind):
    return "_" + kind.upper()


def _part_data(kind, resistor_style):
    part = parts.PARTS[kind]
    shapes = parts.shapes_of(kind, resistor_style)
    x0, y0, x1, y1 = parts.extent(shapes)
    color = "SOURCE" if kind in draw.SOURCES else "PART"
    if part["kind"] == "two":
        fields = [f'half={_num(part["half"])}', f"low={_num(y0)}", f"high={_num(y1)}"]
        if part["inside"]:
            fields.append("inside=True")
    else:
        fields = [f"box=({_num(x0)}, {_num(y0)}, {_num(x1)}, {_num(y1)})"]
        if part.get("label"):
            fields.append(f"spot={_point(part['label'])}")
    fields.append(f"color={color}")
    return f"{_constant(kind)} = dict({', '.join(fields)}, shapes={_shapes_literal(shapes)})"


def _function(kind):
    part = parts.PARTS[kind]
    name = part["name"]
    if part["kind"] == "two":
        return (f'\n\ndef {kind}(ax, a, b, label="", value="", flip=False, shift=None, vshift=None):  # {name}\n'
                f"    _two(ax, a, b, {_constant(kind)}, label, value, flip, shift, vshift)\n")
    return (f'\n\ndef {kind}(ax, at, rot=0, mirror=False, label="", value="", flip=False, shift=None, vshift=None):'
            f'  # {name}\n    _multi(ax, at, {_constant(kind)}, rot, mirror, label, value, flip, shift, vshift)\n')


def _shifts(el):
    """拖曳過的標籤位置寫成 shift=、vshift=。"""
    args = []
    for role, name in (("label", "shift"), ("value", "vshift")):
        dx, dy = (el.get("offsets") or {}).get(role, (0, 0))
        if abs(dx) > 1e-6 or abs(dy) > 1e-6:
            args.append(f"{name}={_point((dx, dy))}")
    return args


def _call(el):
    kind = el["kind"]
    shape = model.kind_of(el)
    label, value, flip = el.get("label", ""), el.get("value", ""), el.get("flip", False)
    if kind == "wire":
        return f"wire(ax, {', '.join(_point(p) for p in el['pts'])})"
    if kind == "text":
        return f"text(ax, {_point(el['at'])}, {_text_literal(label)})"
    if kind == "block":
        args = [_point(el["a"]), _point(el["b"])] + ([_text_literal(label)] if label else [])
        return f"block(ax, {', '.join(args)})"
    if shape in ("span", "box", "loop"):
        args = [_point(el["a"]), _point(el["b"])]
        if label:
            args.append(f"label={_text_literal(label)}")
        if kind == "loop":
            if el.get("square"):
                args.append(f"square={_num(el['square'])}")
            if flip:
                args.append("counter=True")
            for name, default in (("start", model.LOOP_START), ("end", model.LOOP_END)):
                if abs(el.get(name, default) - default) > 1e-6:
                    args.append(f"{name}={_num(el[name])}")
        elif kind == "current" and flip:
            args.append("flip=True")
        return f"{kind}(ax, {', '.join(args + _shifts(el))})"
    if shape == "two":
        args = [_point(el["a"]), _point(el["b"])]
    else:
        args = [_point(el["at"])]
        if el.get("rot", 0) % 4:
            args.append(f"rot={el['rot'] % 4}")
        if el.get("mirror"):
            args.append("mirror=True")
    if label:
        args.append(f"label={_text_literal(label)}")
    if value:
        args.append(f"value={_text_literal(value)}")
    if flip:
        args.append("flip=True")
    return f"{kind}(ax, {', '.join(args + _shifts(el))})"


def _hex(color):
    return "#%02x%02x%02x" % color


def generate(elements, resistor_style="zigzag", caption="", style="pretty"):
    """回傳整段 Markdown 程式碼區塊(含 ``` 與 #| 設定)。"""
    kinds = []
    for el in elements:
        if el["kind"] not in kinds:
            kinds.append(el["kind"])
    shapes = {model.kind_of(el) for el in elements}
    look = draw.STYLES[style]
    body = [HELPERS.format(lw=round(LINE_WIDTH * model.LINE_SCALE, 2), arrow_len=draw.ARROW_LEN, arrow_half=draw.ARROW_HALF,
                           **{key: _hex(look[key]) for key in ("wire", "part", "source", "label", "value", "bg")})
            .rstrip("\n")]
    if "two" in shapes:
        body.append(TWO.format(gap=model.LABEL_GAP).rstrip("\n"))
    if "multi" in shapes:
        body.append(MULTI.format(size=round(model.label_size(), 3)).rstrip("\n"))
    if "wire" in kinds:
        body.append(WIRE.rstrip("\n"))
    dots = model.junctions(elements)
    if dots:
        body.append(DOT.format(dot=draw.DOT_R).rstrip("\n"))
    for kind, template in ANNOTATIONS.items():
        if kind in kinds:
            body.append(template.format(gap=model.LABEL_GAP).rstrip("\n") if "{gap}" in template
                        else template.rstrip("\n"))
    for kind in kinds:
        if kind == "wire" or kind in ANNOTATIONS:
            continue
        body.append("\n\n" + _part_data(kind, resistor_style) + _function(kind).rstrip("\n"))

    box = draw.export_bounds(elements) or (0, 0, 1, 1)
    # 字級放大 = 圖(英寸)縮小,筆記的字級不變,字和元件的比例就和畫面一樣
    inch = INCH_PER_UNIT / model.TEXT_SCALE
    width, height = (box[2] - box[0]) * inch, (box[3] - box[1]) * inch
    if width > MAX_WIDTH:
        width, height = MAX_WIDTH, height * MAX_WIDTH / width
    main = [f"\n\nfig, ax = plt.subplots(figsize=({width:.2f}, {height:.2f}))"]
    if style == "dark":
        main.append(f'plt.rcParams["savefig.facecolor"] = "{_hex(look["bg"])}"  # 深色底')
    main += [_call(el) for el in draw.drawing_order(elements)]
    main += [f"dot(ax, {_point(p)})" for p in dots]
    main += [f"ax.set_xlim({_num(box[0])}, {_num(box[2])})", f"ax.set_ylim({_num(box[1])}, {_num(box[3])})",
             'ax.set_aspect("equal")', 'ax.axis("off")']
    header = ["```python", "#| plot"]
    if caption.strip():
        header.append(f"#| caption: {caption.strip()}")
    return "\n".join(header) + "\n" + "".join(body) + "\n".join(main) + "\n```\n"
