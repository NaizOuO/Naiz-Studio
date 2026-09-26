"""電路元件的形狀。

每個元件用自己的座標描述(單位和畫布一樣,格點間距 0.5):
- 兩端元件(two):放在 a、b 兩點之間,x 軸從 a 指向 b,原點在中點;本體佔 -half～half,兩邊的引線自動補上
- 多端元件(multi):放在一個點上,可以旋轉 90 度與左右鏡像;terminals 是接線的位置
- 標註(span):電壓 +−、電流箭頭,放在 a、b 兩點之間,不畫引線
- 方框(box):虛線框,a、b 是對角

形狀只有五種,程式裡的預覽、圖片、SVG 與產生的 Python 程式碼都照同一份資料畫:
("L", 點列)            折線
("P", 點列, 填滿)       多邊形
("C", 圓心, 半徑, 填滿)  圓(填滿是 "bg" 時用底色填,像端點的空心圈蓋住接進來的導線)
("T", 位置, 文字, 大小)  固定方向的文字(例如 + − V),大小是字高
("A", 起點, 終點)        箭頭(線加實心箭頭)
弧線在這裡先換成折線,畫的程式就不用處理弧。
"""

import math

GRID = 0.5
TEXT_SIZE = 0.24        # 元件裡的 + − V 這類符號


def arc(cx, cy, r, start, end, steps=None):
    """圓弧換成折線;角度是度,逆時針。"""
    steps = steps or max(6, int(abs(end - start) / 10))
    return [(round(cx + r * math.cos(math.radians(start + (end - start) * i / steps)), 4),
             round(cy + r * math.sin(math.radians(start + (end - start) * i / steps)), 4)) for i in range(steps + 1)]


def _coil(count=4, length=1.0, bulge=1):
    """電感的圈:沿 x 軸排 count 個半圓。"""
    r = length / count / 2
    points = []
    for i in range(count):
        cx = -length / 2 + r * (2 * i + 1)
        part = arc(cx, 0, r, 180, 0) if bulge > 0 else arc(cx, 0, r, 180, 360)
        points += part if not points else part[1:]
    return points


def _swap(points):
    """x、y 對調(把沿 x 軸的形狀轉成沿 y 軸)。"""
    return [(y, x) for x, y in points]


def _shift(points, dx=0, dy=0):
    return [(round(x + dx, 4), round(y + dy, 4)) for x, y in points]


def _zigzag(half=0.5, amp=0.14, peaks=6):
    step = 2 * half / peaks
    points = [(-half, 0)]
    for i in range(peaks):
        points.append((round(-half + step * (i + 0.5), 4), amp if i % 2 == 0 else -amp))
    points.append((half, 0))
    return points


def resistor_body(style):
    if style == "box":
        return [("P", [(-0.5, -0.15), (0.5, -0.15), (0.5, 0.15), (-0.5, 0.15)], False)]
    return [("L", _zigzag())]


def _plates(x1, x2, h1=0.3, h2=0.3):
    return [("L", [(x1, -h1), (x1, h1)]), ("L", [(x2, -h2), (x2, h2)])]


def _diode(bar=None):
    shapes = [("P", [(-0.2, 0.2), (-0.2, -0.2), (0.2, 0)], True)]
    shapes.append(("L", bar or [(0.2, -0.2), (0.2, 0.2)]))
    return shapes


def _circle_source(r=0.35):
    return [("C", (0, 0), r, False)]


def _wave(kind):
    """電源圓圈裡的波形,橫跨圓的中間(沿元件的垂直方向畫,轉成直式時波形是橫的)。"""
    if kind == "sine":
        points = [(round(-0.1 * math.sin(math.pi * (y + 0.2) / 0.2), 4), round(y, 4))
                  for y in [-0.2 + 0.4 * i / 24 for i in range(25)]]
    else:
        points = [(0, -0.2), (-0.1, -0.2), (-0.1, 0), (0.1, 0), (0.1, 0.2), (0, 0.2)]
    return [("L", points)]


def _bjt(pnp=False, gate=False):
    shapes = [("L", [(0, 0), (0.35 if gate else 0.5, 0)]), ("L", [(0.5, 0.35), (0.5, -0.35)]),
              ("L", [(0.5, 0.17), (1, 0.5), (1, 1)]), ("L", [(0.5, -0.17), (1, -0.5), (1, -1)])]
    if gate:
        shapes.append(("L", [(0.35, 0.35), (0.35, -0.35)]))
    if pnp:
        shapes.append(("A", (0.92, -0.45), (0.6, -0.24)))
    else:
        shapes.append(("A", (0.5, -0.17), (0.88, -0.42)))
    return shapes


def _mosfet(p=False, depletion=False, body=False):
    shapes = [("L", [(0, 0), (0.4, 0)]), ("L", [(0.4, -0.35), (0.4, 0.35)])]
    if depletion:
        shapes.append(("L", [(0.55, -0.45), (0.55, 0.45)]))
    else:
        shapes += [("L", [(0.55, 0.25), (0.55, 0.45)]), ("L", [(0.55, -0.1), (0.55, 0.1)]),
                   ("L", [(0.55, -0.45), (0.55, -0.25)])]
    shapes += [("L", [(0.55, 0.35), (1, 0.35), (1, 1)]), ("L", [(0.55, -0.35), (1, -0.35), (1, -1)])]
    if body:
        shapes.append(("L", [(0.55, 0), (1.5, 0)]))
        tip = (0.58, 0) if not p else (0.95, 0)
        tail = (0.95, 0) if not p else (0.58, 0)
    else:
        shapes.append(("L", [(0.55, 0), (1, 0), (1, -0.35)]))
        tip = (0.58, 0) if not p else (0.92, 0)
        tail = (0.92, 0) if not p else (0.58, 0)
    shapes.append(("A", tail, tip))
    return shapes


def _jfet(p=False):
    shapes = [("L", [(0.5, 0.4), (0.5, -0.4)]), ("L", [(0.5, 0.3), (1, 0.3), (1, 1)]),
              ("L", [(0.5, -0.3), (1, -0.3), (1, -1)]), ("L", [(0, 0), (0.5, 0)])]
    shapes.append(("A", (0.05, 0), (0.47, 0)) if not p else ("A", (0.45, 0), (0.08, 0)))
    return shapes


def _opamp(comparator=False):
    shapes = [("P", [(0, 1), (0, -1), (1.5, 0)], False), ("L", [(-0.5, 0.5), (0, 0.5)]),
              ("L", [(-0.5, -0.5), (0, -0.5)]), ("L", [(1.5, 0), (2, 0)]),
              ("T", (0.22, 0.5), "−", TEXT_SIZE), ("T", (0.22, -0.5), "+", TEXT_SIZE)]
    if comparator:
        shapes.append(("L", [(0.45, -0.12), (0.6, -0.12), (0.6, 0.12), (0.75, 0.12)]))
    return shapes


def _coils(core):
    left = _swap(_coil(bulge=1))            # 沿 y 軸,往 +x 凸
    right = _shift([(-x, y) for x, y in left], 1)
    shapes = [("L", [(0, 1), (0, 0.5)]), ("L", left), ("L", [(0, -0.5), (0, -1)]),
              ("L", [(1, 1), (1, 0.5)]), ("L", right), ("L", [(1, -0.5), (1, -1)])]
    if core:
        shapes += [("L", [(0.43, 0.55), (0.43, -0.55)]), ("L", [(0.57, 0.55), (0.57, -0.55)])]
    else:
        shapes += [("C", (0.3, 0.6), 0.05, True), ("C", (0.7, 0.6), 0.05, True)]
    return shapes


# ------------------------------------------------------------ 元件清單

def _two(name, half, shapes, extra=None, label_inside=False):
    return dict(kind="two", name=name, half=half, shapes=shapes, extra=extra or {}, inside=label_inside)


def _multi(name, terminals, shapes, label=None):
    return dict(kind="multi", name=name, terminals=terminals, shapes=shapes, label=label)


ARROW_VAR = ("A", (-0.42, -0.35), (0.45, 0.38))

PARTS = {
    # 被動元件(電阻的形狀依設定是鋸齒或方框,shapes 在 shapes_of 裡換)
    "resistor": _two("電阻", 0.5, []),
    "rheostat": _two("可變電阻", 0.5, [ARROW_VAR]),
    "potentiometer": _two("電位器", 0.5, [("A", (0, 0.5), (0, 0.19))], extra={"w": (0, 0.5)}),
    "capacitor": _two("電容", 0.1, _plates(-0.1, 0.1)),
    "polar_cap": _two("極性電容", 0.1, [("L", [(-0.1, -0.3), (-0.1, 0.3)]), ("L", arc(0.45, 0, 0.35, 121, 239)),
                                         ("T", (-0.28, 0.3), "+", TEXT_SIZE)]),
    "var_cap": _two("可變電容", 0.1, _plates(-0.1, 0.1) + [("A", (-0.35, -0.38), (0.35, 0.4))]),
    "inductor": _two("電感", 0.5, [("L", _coil())]),
    "battery": _two("電池", 0.3, [("L", [(-0.3, -0.15), (-0.3, 0.15)]), ("L", [(-0.1, -0.32), (-0.1, 0.32)]),
                                ("L", [(0.1, -0.15), (0.1, 0.15)]), ("L", [(0.3, -0.32), (0.3, 0.32)]),
                                ("T", (0.45, 0.35), "+", TEXT_SIZE)]),
    "fuse": _two("保險絲", 0.35, [("P", [(-0.35, -0.11), (0.35, -0.11), (0.35, 0.11), (-0.35, 0.11)], False),
                               ("L", [(-0.35, 0), (0.35, 0)])]),
    "crystal": _two("石英振盪器", 0.18, [("P", [(-0.08, -0.22), (0.08, -0.22), (0.08, 0.22), (-0.08, 0.22)], False)]
                    + _plates(-0.18, 0.18)),
    "transformer": _multi("變壓器", {"p1": (0, 1), "p2": (0, -1), "s1": (1, 1), "s2": (1, -1)}, _coils(True)),
    "coupled": _multi("耦合電感", {"p1": (0, 1), "p2": (0, -1), "s1": (1, 1), "s2": (1, -1)}, _coils(False)),

    # 電源
    "vsource": _two("直流電壓源", 0.35, _circle_source() + [("T", (0.16, 0), "+", TEXT_SIZE),
                                                        ("T", (-0.16, 0), "−", TEXT_SIZE)]),
    "isource": _two("直流電流源", 0.35, _circle_source() + [("A", (-0.2, 0), (0.22, 0))]),
    "sine": _two("交流電源", 0.35, _circle_source() + _wave("sine")),
    "square": _two("方波電源", 0.35, _circle_source() + _wave("square")),
    "vcvs": _two("電壓控制電壓源", 0.35, [("P", [(-0.35, 0), (0, 0.35), (0.35, 0), (0, -0.35)], False),
                                      ("T", (0.15, 0), "+", TEXT_SIZE), ("T", (-0.15, 0), "−", TEXT_SIZE)]),
    "ccvs": None, "vccs": None, "cccs": None,       # 下面補上(形狀和上一個相同)
    "ground": _multi("接地", {"a": (0, 0)}, [("L", [(0, 0), (0, -0.25)]), ("L", [(-0.25, -0.25), (0.25, -0.25)]),
                                           ("L", [(-0.16, -0.35), (0.16, -0.35)]),
                                           ("L", [(-0.07, -0.45), (0.07, -0.45)])], label=(0, -0.62)),
    "rail": _multi("電源端", {"a": (0, 0)}, [("L", [(0, 0), (0, 0.25)]), ("L", [(-0.2, 0.25), (0.2, 0.25)])],
                   label=(0, 0.45)),

    # 二極體
    "diode": _two("二極體", 0.2, _diode()),
    "schottky": _two("蕭特基二極體", 0.2, _diode([(0.28, 0.12), (0.28, 0.2), (0.2, 0.2), (0.2, -0.2),
                                                (0.12, -0.2), (0.12, -0.12)])),
    "zener": _two("稽納二極體", 0.2, _diode([(0.12, 0.25), (0.2, 0.2), (0.2, -0.2), (0.28, -0.25)])),
    "tunnel": _two("穿隧二極體", 0.2, _diode([(0.12, 0.2), (0.2, 0.2), (0.2, -0.2), (0.12, -0.2)])),
    "varactor": _two("變容二極體", 0.28, _diode() + [("L", [(0.28, -0.2), (0.28, 0.2)])]),
    "led": _two("發光二極體", 0.2, _diode() + [("A", (-0.02, 0.24), (0.14, 0.48)), ("A", (0.14, 0.24), (0.3, 0.48))]),
    "photodiode": _two("光二極體", 0.2, _diode() + [("A", (0.1, 0.52), (-0.04, 0.28)),
                                               ("A", (0.28, 0.52), (0.14, 0.28))]),
    "scr": _two("閘流體(SCR)", 0.2, _diode() + [("L", [(0.2, -0.12), (0.5, -0.5)])], extra={"g": (0.5, -0.5)}),

    # 電晶體
    "npn": _multi("NPN 電晶體", {"b": (0, 0), "c": (1, 1), "e": (1, -1)}, _bjt()),
    "pnp": _multi("PNP 電晶體", {"b": (0, 0), "c": (1, 1), "e": (1, -1)}, _bjt(pnp=True)),
    "nmos": _multi("N 通道 MOSFET(增強)", {"g": (0, 0), "d": (1, 1), "s": (1, -1)}, _mosfet()),
    "pmos": _multi("P 通道 MOSFET(增強)", {"g": (0, 0), "d": (1, 1), "s": (1, -1)}, _mosfet(p=True)),
    "nmos_dep": _multi("N 通道 MOSFET(空乏)", {"g": (0, 0), "d": (1, 1), "s": (1, -1)}, _mosfet(depletion=True)),
    "pmos_dep": _multi("P 通道 MOSFET(空乏)", {"g": (0, 0), "d": (1, 1), "s": (1, -1)},
                       _mosfet(p=True, depletion=True)),
    "nmos4": _multi("N 通道 MOSFET(四端)", {"g": (0, 0), "d": (1, 1), "s": (1, -1), "b": (1.5, 0)},
                    _mosfet(body=True)),
    "pmos4": _multi("P 通道 MOSFET(四端)", {"g": (0, 0), "d": (1, 1), "s": (1, -1), "b": (1.5, 0)},
                    _mosfet(p=True, body=True)),
    "njfet": _multi("N 通道 JFET", {"g": (0, 0), "d": (1, 1), "s": (1, -1)}, _jfet()),
    "pjfet": _multi("P 通道 JFET", {"g": (0, 0), "d": (1, 1), "s": (1, -1)}, _jfet(p=True)),
    "igbt": _multi("IGBT", {"g": (0, 0), "c": (1, 1), "e": (1, -1)}, _bjt(gate=True)),
    "opamp": _multi("運算放大器", {"-": (-0.5, 0.5), "+": (-0.5, -0.5), "out": (2, 0)}, _opamp(), label=(0.6, 0.9)),
    "comparator": _multi("比較器", {"-": (-0.5, 0.5), "+": (-0.5, -0.5), "out": (2, 0)}, _opamp(True),
                         label=(0.6, 0.9)),

    # 量測與其他
    "voltmeter": _two("電壓表", 0.3, [("C", (0, 0), 0.3, False), ("T", (0, 0), "V", 0.3)]),
    "ammeter": _two("電流表", 0.3, [("C", (0, 0), 0.3, False), ("T", (0, 0), "A", 0.3)]),
    "ohmmeter": _two("歐姆表", 0.3, [("C", (0, 0), 0.3, False), ("T", (0, 0), "Ω", 0.3)]),
    "switch": _two("開關", 0.34, [("C", (-0.3, 0), 0.04, False), ("C", (0.3, 0), 0.04, False),
                                ("L", [(-0.26, 0.02), (0.28, 0.26)])]),
    "lamp": _two("燈泡", 0.3, [("C", (0, 0), 0.3, False), ("L", [(-0.212, -0.212), (0.212, 0.212)]),
                             ("L", [(-0.212, 0.212), (0.212, -0.212)])]),
    "relay": _multi("繼電器", {"c1": (0, 1), "c2": (0, -1), "s1": (1, 1), "s2": (1, -1)},
                    [("P", [(-0.2, -0.35), (0.2, -0.35), (0.2, 0.35), (-0.2, 0.35)], False),
                     ("L", [(0, 1), (0, 0.35)]), ("L", [(0, -0.35), (0, -1)]), ("L", [(-0.2, -0.35), (0.2, 0.35)]),
                     ("L", [(1, 1), (1, 0.34)]), ("C", (1, 0.3), 0.04, False), ("L", [(1, -1), (1, -0.34)]),
                     ("C", (1, -0.3), 0.04, False), ("L", [(1, -0.26), (0.78, 0.26)]),
                     ("L", [(0.2, 0), (0.28, 0)]), ("L", [(0.4, 0), (0.48, 0)]), ("L", [(0.6, 0), (0.68, 0)]),
                     ("L", [(0.8, 0), (0.86, 0)])]),
    "speaker": _multi("喇叭", {"a": (0, 0.5), "b": (0, -0.5)},
                      [("L", [(0, 0.5), (0.6, 0.5), (0.6, 0.25)]), ("L", [(0, -0.5), (0.6, -0.5), (0.6, -0.25)]),
                       ("P", [(0.5, 0.25), (0.75, 0.25), (0.75, -0.25), (0.5, -0.25)], False),
                       ("P", [(0.75, 0.25), (1.05, 0.55), (1.05, -0.55), (0.75, -0.25)], False)],
                      label=(0.8, 0.8)),
    "antenna": _multi("天線", {"a": (0, 0)}, [("L", [(0, 0), (0, 0.85)]),
                                            ("P", [(-0.25, 0.95), (0.25, 0.95), (0, 0.55)], False)],
                      label=(0.4, 0.75)),
    "probe": _multi("探棒", {"a": (0, 0)}, [("L", [(0, 0), (0.3, 0)]), ("C", (0.38, 0), 0.08, "bg")],
                    label=(0.6, 0)),
    "block": dict(kind="rect", name="方塊(可打字)", shapes=[]),

    # 標註
    "node": _multi("節點", {"a": (0, 0)}, [("C", (0, 0), 0.06, True)], label=(0.18, 0.18)),
    "terminal": _multi("端點", {"a": (0, 0)}, [("C", (0, 0), 0.06, "bg")], label=(0.18, 0.18)),
    "jumper": _two("跳線", 0.15, [("L", arc(0, 0, 0.15, 180, 0))]),
    "voltage": dict(kind="span", name="電壓 +−", shapes=[]),
    "current": dict(kind="span", name="電流箭頭", shapes=[]),
    "loop": dict(kind="loop", name="迴路電流", shapes=[]),
    "text": dict(kind="text", name="文字", shapes=[]),
    "frame": dict(kind="box", name="虛線框", shapes=[]),
}

for key, name, shapes in (("ccvs", "電流控制電壓源", PARTS["vcvs"]["shapes"]),
                          ("vccs", "電壓控制電流源", [PARTS["vcvs"]["shapes"][0], ("A", (-0.2, 0), (0.22, 0))]),
                          ("cccs", "電流控制電流源", [PARTS["vcvs"]["shapes"][0], ("A", (-0.2, 0), (0.22, 0))])):
    PARTS[key] = _two(name, 0.35, shapes)
PARTS["vcvs"]["name"] = "電壓控制電壓源"

CATEGORIES = [
    ("被動元件", ["resistor", "rheostat", "potentiometer", "capacitor", "polar_cap", "var_cap", "inductor",
              "transformer", "coupled", "battery", "fuse", "crystal"]),
    ("電源", ["vsource", "isource", "sine", "square", "vcvs", "ccvs", "vccs", "cccs", "ground", "rail"]),
    ("二極體", ["diode", "schottky", "zener", "tunnel", "varactor", "led", "photodiode", "scr"]),
    ("電晶體與放大器", ["npn", "pnp", "nmos", "pmos", "nmos_dep", "pmos_dep", "nmos4", "pmos4", "njfet", "pjfet",
                 "igbt", "opamp", "comparator"]),
    ("量測與其他", ["voltmeter", "ammeter", "ohmmeter", "switch", "lamp", "relay", "speaker", "antenna", "probe",
               "block"]),
    ("標註", ["text", "voltage", "current", "loop", "node", "terminal", "jumper", "frame"]),
]

# 放下時預設的名字開頭(R1、C1…);沒有列的元件不自動命名
PREFIX = {"resistor": "R", "rheostat": "R", "potentiometer": "R", "capacitor": "C", "polar_cap": "C",
          "var_cap": "C", "inductor": "L", "transformer": "T", "coupled": "L", "battery": "V", "fuse": "F",
          "crystal": "X", "vsource": "V", "isource": "I", "sine": "v", "square": "v", "diode": "D",
          "schottky": "D", "zener": "D", "tunnel": "D", "varactor": "D", "led": "D", "photodiode": "D",
          "scr": "SCR", "npn": "Q", "pnp": "Q", "nmos": "M", "pmos": "M", "nmos_dep": "M", "pmos_dep": "M",
          "nmos4": "M", "pmos4": "M", "njfet": "J", "pjfet": "J", "igbt": "Q", "opamp": "U", "comparator": "U",
          "switch": "S", "lamp": "L", "relay": "K", "speaker": "LS", "voltage": "v", "current": "i", "loop": "I"}


def shapes_of(kind, resistor_style="zigzag"):
    part = PARTS[kind]
    if kind in ("resistor", "rheostat", "potentiometer"):
        return resistor_body(resistor_style) + part["shapes"]
    return part["shapes"]


def extent(shapes):
    """形狀的範圍 (xmin, ymin, xmax, ymax)。"""
    xs, ys = [], []
    for shape in shapes:
        kind = shape[0]
        if kind in ("L", "P"):
            xs += [p[0] for p in shape[1]]
            ys += [p[1] for p in shape[1]]
        elif kind == "C":
            (x, y), r = shape[1], shape[2]
            xs += [x - r, x + r]
            ys += [y - r, y + r]
        elif kind == "T":
            (x, y), size = shape[1], shape[3]
            xs += [x - size / 2, x + size / 2]
            ys += [y - size / 2, y + size / 2]
        elif kind == "A":
            xs += [shape[1][0], shape[2][0]]
            ys += [shape[1][1], shape[2][1]]
    if not xs:
        return (0, 0, 0, 0)
    return (min(xs), min(ys), max(xs), max(ys))
