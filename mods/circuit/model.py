"""電路圖的資料:每個元件是一個 dict,方便存成 JSON、復原與複製。

兩端元件、標註、框:{"kind", "a": [x, y], "b": [x, y], "label", "value", "flip"}
多端元件:{"kind", "at": [x, y], "rot": 0～3(每次逆時針 90 度), "mirror": 自己的左右鏡像, "label", "value", "flip"}
文字:{"kind": "text", "at": [x, y], "label"}
迴路電流:{"kind": "loop", "a", "b"(外框對角), "square": 0 是橢圓、1 接近長方形, "flip": 逆時針,
          "start"、"end":弧線起點與箭頭的角度(度,從右邊逆時針量), "label"}
方塊(可打字):{"kind": "block", "a", "b"(對角), "label"}
導線:{"kind": "wire", "pts": [[x, y], ...]}
標籤拖曳過的位置記在 "offsets": {"label": [dx, dy], "value": [dx, dy]}。
座標的 y 往上,和數學、matplotlib 一樣;畫到螢幕時才翻過來。
"""

import copy
import json
import math

from . import parts

BASE_LABEL_SIZE = 0.26  # 名稱、數值的字高(字級倍數 1 時)
LABEL_GAP = 0.1         # 標籤離元件本體的距離
TEXT_SCALE = 1.0        # 字級倍數;由畫面依這張圖的設定調整
LINE_SCALE = 1.0        # 線寬倍數


def label_size():
    return BASE_LABEL_SIZE * TEXT_SCALE


def set_look(text_scale=1.0, line_scale=1.0):
    """設定字級、線寬倍數(畫圖、產生程式碼、點選標籤都照這個)。"""
    global TEXT_SCALE, LINE_SCALE
    TEXT_SCALE, LINE_SCALE = float(text_scale), float(line_scale)
FILE_VERSION = 2


def text_width(text):
    """文字在畫布上的寬度(單位);draw 模組載入後會換成實際量字寬的版本。"""
    return len(text) * label_size() * 0.55


def kind_of(el):
    if el["kind"] == "wire":
        return "wire"
    return parts.PARTS[el["kind"]]["kind"]


def new(kind, **fields):
    el = dict(kind=kind, label="", value="", flip=False)
    if kind == "wire":
        el = dict(kind="wire", pts=[])
    elif parts.PARTS[kind]["kind"] == "multi":
        el.update(at=[0, 0], rot=0, mirror=False)
    elif parts.PARTS[kind]["kind"] == "text":
        el = dict(kind=kind, at=[0, 0], label="")
    else:
        el.update(a=[0, 0], b=[1, 0])
        if kind == "loop":
            el["square"] = 0.0
    el.update(fields)
    return el


def snap(value, step=parts.GRID):
    return round(round(value / step) * step, 4)


def snap_point(point):
    return [snap(point[0]), snap(point[1])]


# ------------------------------------------------------------ 座標轉換

def _frame_two(a, b):
    """兩端元件的座標軸:原點(中點)、x 軸單位向量、y 軸單位向量、長度。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return ((a[0], a[1]), (1.0, 0.0), (0.0, 1.0), 0.0)
    ux, uy = dx / length, dy / length
    return (((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), (ux, uy), (-uy, ux), length)


def _frame_multi(el):
    angle = el.get("rot", 0) % 4
    cos, sin = [(1, 0), (0, 1), (-1, 0), (0, -1)][angle]
    sign = -1 if el.get("mirror") else 1
    return (tuple(el["at"]), (cos * sign, sin * sign), (-sin, cos))


def frame(el):
    """(原點, x 軸, y 軸):把元件自己的座標 (x, y) 換成畫布座標 = 原點 + x·x軸 + y·y軸。"""
    if kind_of(el) in ("multi", "text"):
        return _frame_multi(el)
    origin, ux, uy, _ = _frame_two(el["a"], el["b"])
    return origin, ux, uy


def apply(fr, point):
    (ox, oy), (ax, ay), (bx, by) = fr
    return (round(ox + point[0] * ax + point[1] * bx, 4), round(oy + point[0] * ay + point[1] * by, 4))


def inverse(fr, point):
    (ox, oy), (ax, ay), (bx, by) = fr
    px, py = point[0] - ox, point[1] - oy
    det = ax * by - ay * bx
    return ((px * by - py * bx) / det, (ax * py - ay * px) / det)


def transform_shapes(shapes, fr):
    out = []
    for shape in shapes:
        kind = shape[0]
        if kind in ("L", "D"):
            out.append((kind, [apply(fr, p) for p in shape[1]]))
        elif kind == "P":
            out.append(("P", [apply(fr, p) for p in shape[1]], shape[2]))
        elif kind == "C":
            out.append(("C", apply(fr, shape[1]), shape[2], shape[3]))
        elif kind == "T":
            out.append(("T", apply(fr, shape[1]), shape[2], shape[3]))
        elif kind == "A":
            out.append(("A", apply(fr, shape[1]), apply(fr, shape[2])))
    return out


# ------------------------------------------------------------ 迴路電流

LOOP_START, LOOP_END = 120.0, 180.0    # 預設從左上方開始,順時針繞到左邊,在左上角留一個缺口


def _loop_frame(a, b, square):
    cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    rx, ry = max(abs(b[0] - a[0]) / 2, 0.05), max(abs(b[1] - a[1]) / 2, 0.05)
    return cx, cy, rx, ry, 2 / (2 + max(0.0, min(1.0, square)) * 10)


def loop_sweep(start, end, counter):
    """從 start 繞到 end 的角度(度,正數);起點終點重疊時繞一整圈少一點。"""
    sweep = (end - start) % 360 if counter else (start - end) % 360
    return sweep if sweep > 5 else 355


def loop_point(a, b, square, angle):
    """迴路上某個角度(從中心看出去的方向)的點。"""
    cx, cy, rx, ry, power = _loop_frame(a, b, square)
    t = math.radians(angle)
    c, s = math.cos(t), math.sin(t)
    return (round(cx + rx * math.copysign(abs(c) ** power, c), 4), round(cy + ry * math.copysign(abs(s) ** power, s), 4))


def loop_angle(a, b, point, square=0.0):
    """滑鼠位置換成迴路上的角度(拉起點、終點用):像 Desmos 曲線上的點,取迴路上離滑鼠最近的點。"""
    best, best_distance = 0.0, None
    for step in range(720):
        angle = step / 2
        x, y = loop_point(a, b, square, angle)
        distance = (x - point[0]) ** 2 + (y - point[1]) ** 2
        if best_distance is None or distance < best_distance:
            best, best_distance = angle, distance
    return best


def loop_points(a, b, square=0.0, counter=False, start=LOOP_START, end=LOOP_END, steps=96):
    """迴路電流的弧線:從 start 繞到 end(counter 為逆時針),square 越大越接近長方形(超橢圓)。
    回傳 (折線, 箭頭尾, 箭頭尖)。"""
    sweep = loop_sweep(start, end, counter)
    sign = 1 if counter else -1
    points = [loop_point(a, b, square, start + sign * sweep * i / steps) for i in range(steps + 1)]
    return points[:-3], points[-5], points[-1]


def loop_args(el):
    return (el["a"], el["b"], el.get("square", 0.0), el.get("flip", False), el.get("start", LOOP_START),
            el.get("end", LOOP_END))


# ------------------------------------------------------------ 形狀、標籤、接點

def _align(vector):
    """標籤在元件的哪一邊,決定文字怎麼對齊。"""
    vx, vy = vector
    if abs(vy) >= abs(vx):
        return "center", "bottom" if vy > 0 else "top"
    return ("left" if vx > 0 else "right"), "center"


def _two_side(ux, uy):
    """兩端元件的名稱放哪一邊(自己的 y 軸是 +1 或 -1):橫的放上面,直的放右邊。"""
    nx, ny = -uy, ux
    if abs(ny) > 0.3:
        return 1 if ny > 0 else -1
    return 1 if nx > 0 else -1


def _raw_geometry(el, resistor_style):
    kind = kind_of(el)
    labels = []
    if kind == "wire":
        return dict(shapes=[("L", [tuple(p) for p in el["pts"]])] if len(el["pts"]) > 1 else [], labels=[],
                    terminals=[tuple(el["pts"][0]), tuple(el["pts"][-1])] if el["pts"] else [])
    part = parts.PARTS[el["kind"]]
    name, value = el.get("label", ""), el.get("value", "")
    if kind == "text":
        return dict(shapes=[], labels=[(tuple(el["at"]), name, "center", "center", "label")], terminals=[])
    if kind == "multi":
        fr = _frame_multi(el)
        local = parts.shapes_of(el["kind"], resistor_style)
        shapes = transform_shapes(local, fr)
        x0, y0, x1, y1 = parts.extent(local)
        center = apply(fr, ((x0 + x1) / 2, (y0 + y1) / 2))
        spot = part.get("label") or (x1 + 0.15, (y0 + y1) / 2)
        if el.get("flip"):
            spot = (x0 - 0.15, spot[1]) if not part.get("label") else (spot[0], -spot[1])
        where = apply(fr, spot)
        ha, va = _align((where[0] - center[0], where[1] - center[1]))
        labels.append((where, name, ha, va, "label"))
        if value:
            shift = label_size() * 1.25 if name else 0
            labels.append(((where[0], where[1] - shift), value, ha, "top" if va == "bottom" and name else va,
                           "value"))
        terminals = [apply(fr, p) for p in part["terminals"].values()]
        return dict(shapes=shapes, labels=labels, terminals=terminals)

    a, b = tuple(el["a"]), tuple(el["b"])
    origin, ux, uy, length = _frame_two(a, b)
    fr = (origin, ux, uy)
    if kind in ("box", "rect"):
        (xa, ya), (xb, yb) = a, b
        corners = [(xa, ya), (xb, ya), (xb, yb), (xa, yb), (xa, ya)]
        if kind == "rect":                          # 方塊:實線框,字在正中間
            return dict(shapes=[("P", corners[:4], False)], labels=[(origin, name, "center", "center", "label")],
                        terminals=[])
        top = (min(xa, xb), max(ya, yb) + LABEL_GAP)
        return dict(shapes=[("D", corners)], labels=[(top, name, "left", "bottom", "label")], terminals=[])
    if kind == "loop":
        line, tail, tip = loop_points(*loop_args(el))
        return dict(shapes=[("L", line), ("A", tail, tip)], labels=[(origin, name, "center", "center", "label")],
                    terminals=[])
    if kind == "span":
        side = -1 if el.get("flip") else 1
        if el["kind"] == "voltage":
            shapes = [("T", a, "+", 0.3), ("T", b, "−", 0.3)]
            labels.append((origin, name, "center", "center", "label"))
        else:
            tail, tip = apply(fr, (-0.22, 0)), apply(fr, (0.22, 0))
            shapes = [("A", tail, tip)]
            normal = (uy[0] * side * _two_side(ux[0], ux[1]), uy[1] * side * _two_side(ux[0], ux[1]))
            where = (origin[0] + normal[0] * 0.2, origin[1] + normal[1] * 0.2)
            ha, va = _align(normal)
            labels.append((where, name, ha, va, "label"))
        return dict(shapes=shapes, labels=labels, terminals=[])

    half = part["half"]
    body = parts.shapes_of(el["kind"], resistor_style)
    local = []
    if length > 2 * half:
        local += [("L", [(-length / 2, 0), (-half, 0)]), ("L", [(half, 0), (length / 2, 0)])]
    local += body
    shapes = transform_shapes(local, fr)
    _, y0, _, y1 = parts.extent(body)
    if part["inside"]:
        labels.append((origin, name, "center", "center", "label"))
        if value:
            labels.append(((origin[0], origin[1] - 0.45), value, "center", "top", "value"))
    else:
        side = _two_side(*ux) * (-1 if el.get("flip") else 1)
        for text, sign, role in ((name, side, "label"), (value, -side, "value")):
            reach = (y1 if sign > 0 else -y0) + LABEL_GAP
            where = apply(fr, (0, sign * reach))
            ha, va = _align((uy[0] * sign, uy[1] * sign))
            labels.append((where, text, ha, va, role))
    terminals = [a, b] + [apply(fr, p) for p in part["extra"].values()]
    return dict(shapes=shapes, labels=labels, terminals=terminals)


def geometry(el, resistor_style="zigzag"):
    """回傳 dict(shapes=畫布座標的形狀, labels=[(位置, 文字, 水平對齊, 垂直對齊, 種類)], terminals=[接點])。
    標籤拖曳過的話,位置加上記下的偏移。"""
    geo = _raw_geometry(el, resistor_style)
    offsets = el.get("offsets")
    if offsets:
        geo["labels"] = [((x + offsets.get(role, (0, 0))[0], y + offsets.get(role, (0, 0))[1]), text, ha, va, role)
                         for (x, y), text, ha, va, role in geo["labels"]]
    return geo


def label_box(point, text, ha, va):
    """標籤文字在畫布上的範圍 (xmin, ymin, xmax, ymax)。"""
    x, y = point
    width = text_width(text)
    left = x - (width / 2 if ha == "center" else width if ha == "right" else 0)
    low = y - (label_size() if va == "top" else 0 if va == "bottom" else label_size() / 2)
    return (left, low - label_size() * 0.15, left + width, low + label_size() * 1.1)


def label_at(el, point, pad=0.06):
    """point 落在哪個標籤上:回傳 "label"、"value" 或 None。"""
    for where, text, ha, va, role in reversed(geometry(el)["labels"]):
        if text:
            x0, y0, x1, y1 = label_box(where, text, ha, va)
            if x0 - pad <= point[0] <= x1 + pad and y0 - pad <= point[1] <= y1 + pad:
                return role
    return None


def connection_points(el):
    """會和導線接在一起的點(標註、框、文字沒有)。"""
    if kind_of(el) in ("span", "box", "loop", "text", "rect"):
        return []
    return geometry(el)["terminals"]


def _key(point):
    return (round(point[0] * 4), round(point[1] * 4))


def same(p, q):
    return _key(p) == _key(q)


def _on_segment(point, p, q):
    """point 在 p、q 之間的線段上(不含兩端)。"""
    (x, y), (x1, y1), (x2, y2) = point, p, q
    if abs((x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)) > 1e-6:
        return False
    inside = min(x1, x2) - 1e-6 <= x <= max(x1, x2) + 1e-6 and min(y1, y2) - 1e-6 <= y <= max(y1, y2) + 1e-6
    return inside and _key(point) not in (_key(p), _key(q))


def connection_counts(elements):
    """每個接點接了幾條線:{格點: (位置, 數量)};導線轉角算兩條,接到導線中間(T 字)也加兩條。"""
    count, where = {}, {}
    for el in elements:
        if el["kind"] == "wire":
            pts = el["pts"]
            ends = [pts[0], pts[-1]] if len(pts) > 1 else []
            corners = pts[1:-1]
        else:
            ends, corners = connection_points(el), []
        for p in ends:
            count[_key(p)] = count.get(_key(p), 0) + 1
            where[_key(p)] = tuple(p)
        for p in corners:
            count[_key(p)] = count.get(_key(p), 0) + 2
            where[_key(p)] = tuple(p)
    wires = [el["pts"] for el in elements if el["kind"] == "wire"]
    for key, point in where.items():
        for pts in wires:
            if any(_on_segment(point, p, q) for p, q in zip(pts, pts[1:])):
                count[key] += 2
    return {key: (where[key], number) for key, number in count.items()}


def junctions(elements):
    """要畫實心圓點的地方:三條以上的線接在一起,或是接到導線的中間(T 字)。"""
    return [point for point, number in connection_counts(elements).values() if number >= 3]


def open_terminals(elements):
    """還沒接任何東西的元件接點(編輯時畫小圈提示,輸出的圖不畫)。"""
    counts = connection_counts(elements)
    result = []
    for el in elements:
        if el["kind"] != "wire":
            result += [p for p in connection_points(el) if counts[_key(p)][1] == 1]
    return result


# ------------------------------------------------------------ 點選、範圍

def _segment_distance(point, p, q):
    (x, y), (x1, y1), (x2, y2) = point, p, q
    dx, dy = x2 - x1, y2 - y1
    if dx == dy == 0:
        return math.hypot(x - x1, y - y1)
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - x1 - t * dx, y - y1 - t * dy)


def _shape_points(shapes):
    points = []
    for shape in shapes:
        if shape[0] in ("L", "P", "D"):
            points += shape[1]
        elif shape[0] == "C":
            (x, y), r = shape[1], shape[2]
            points += [(x - r, y - r), (x + r, y + r)]
        elif shape[0] == "T":
            (x, y), size = shape[1], shape[3]
            points += [(x - size / 2, y - size / 2), (x + size / 2, y + size / 2)]
        elif shape[0] == "A":
            points += [shape[1], shape[2]]
    return points


def bounds(el, include_labels=False):
    """元件在畫布上的範圍 (xmin, ymin, xmax, ymax);文字只有標籤,一定算進去。"""
    geo = geometry(el)
    points = _shape_points(geo["shapes"]) + list(geo["terminals"])
    if kind_of(el) == "loop":                   # 迴路電流算整個外框(缺口那邊也算)
        points += [tuple(el["a"]), tuple(el["b"])]
    if include_labels or kind_of(el) == "text":
        for where, text, ha, va, _ in geo["labels"]:
            if text:
                x0, y0, x1, y1 = label_box(where, text, ha, va)
                points += [(x0, y0), (x1, y1)]
    if not points and kind_of(el) == "text":
        x, y = el["at"]
        points = [(x - 0.2, y - 0.15), (x + 0.2, y + 0.15)]
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def hit(el, point, tolerance=0.14):
    kind = kind_of(el)
    if kind == "wire":
        pts = el["pts"]
        return any(_segment_distance(point, p, q) <= tolerance for p, q in zip(pts, pts[1:]))
    if kind == "text":
        x0, y0, x1, y1 = bounds(el)
        return x0 - 0.05 <= point[0] <= x1 + 0.05 and y0 - 0.05 <= point[1] <= y1 + 0.05
    if kind == "rect":
        (xa, ya), (xb, yb) = el["a"], el["b"]
        return min(xa, xb) - tolerance <= point[0] <= max(xa, xb) + tolerance and \
            min(ya, yb) - tolerance <= point[1] <= max(ya, yb) + tolerance
    if kind == "box":
        (xa, ya), (xb, yb) = el["a"], el["b"]
        corners = [(xa, ya), (xb, ya), (xb, yb), (xa, yb), (xa, ya)]
        return any(_segment_distance(point, p, q) <= tolerance for p, q in zip(corners, corners[1:]))
    if kind == "loop":
        line, tail, tip = loop_points(*loop_args(el), steps=48)
        line = line + [tip]
        return any(_segment_distance(point, p, q) <= tolerance for p, q in zip(line, line[1:]))
    if kind == "span":
        return _segment_distance(point, el["a"], el["b"]) <= max(tolerance, 0.2)
    shapes = parts.shapes_of(el["kind"])
    x0, y0, x1, y1 = parts.extent(shapes)
    x, y = inverse(frame(el), point)
    if kind == "two":
        if _segment_distance(point, el["a"], el["b"]) <= tolerance:
            return True
        return x0 - 0.05 <= x <= x1 + 0.05 and y0 - 0.05 <= y <= y1 + 0.05
    return x0 - 0.1 <= x <= x1 + 0.1 and y0 - 0.1 <= y <= y1 + 0.1


def handles(el):
    """選取後可以拖曳的端點:[(名稱, 位置)]。"""
    kind = kind_of(el)
    if kind == "wire":
        return [(i, tuple(p)) for i, p in ((0, el["pts"][0]), (len(el["pts"]) - 1, el["pts"][-1]))]
    if kind in ("two", "span"):
        return [("a", tuple(el["a"])), ("b", tuple(el["b"]))]
    if kind in ("box", "loop", "rect"):         # 四個角都可以拉
        (xa, ya), (xb, yb) = el["a"], el["b"]
        corners = [("a", (xa, ya)), ("b", (xb, yb)), ("ab", (xa, yb)), ("ba", (xb, ya))]
        if kind == "loop":                      # 迴路電流的起點、箭頭也可以拉(排前面,和角靠很近時先抓到它們)
            _, _, _, _, start, end = loop_args(el)
            corners = [("start", loop_point(el["a"], el["b"], el.get("square", 0.0), start)),
                       ("end", loop_point(el["a"], el["b"], el.get("square", 0.0), end))] + corners
        return corners
    return []


def set_handle(el, name, point):
    """把端點 name 拉到 point。"""
    point = [point[0], point[1]]
    if name in ("start", "end"):
        el[name] = loop_angle(el["a"], el["b"], point, el.get("square", 0.0))
        return
    if name == "ab":            # 框的另外兩個角:x 跟 a、y 跟 b
        el["a"] = [point[0], el["a"][1]]
        el["b"] = [el["b"][0], point[1]]
    elif name == "ba":
        el["b"] = [point[0], el["b"][1]]
        el["a"] = [el["a"][0], point[1]]
    else:
        el[name] = point


def move(el, dx, dy):
    if el["kind"] == "wire":
        el["pts"] = [[round(x + dx, 4), round(y + dy, 4)] for x, y in el["pts"]]
    elif "at" in el:
        el["at"] = [round(el["at"][0] + dx, 4), round(el["at"][1] + dy, 4)]
    else:
        el["a"] = [round(el["a"][0] + dx, 4), round(el["a"][1] + dy, 4)]
        el["b"] = [round(el["b"][0] + dx, 4), round(el["b"][1] + dy, 4)]


def _points_of(el):
    if el["kind"] == "wire":
        return el["pts"]
    if "at" in el:
        return [el["at"]]
    return [el["a"], el["b"]]


def own_center(elements):
    points = [p for el in elements for p in _points_of(el)]
    if len(elements) == 1 and kind_of(elements[0]) in ("multi", "text"):
        return tuple(elements[0]["at"])
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return tuple(snap_point(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)))


def _map_points(el, func):
    if el["kind"] == "wire":
        el["pts"] = [list(func(p)) for p in el["pts"]]
    elif "at" in el:
        el["at"] = list(func(el["at"]))
    else:
        el["a"], el["b"] = list(func(el["a"])), list(func(el["b"]))


def _offsets(el, func):
    """標籤拖曳過的偏移跟著轉。"""
    if el.get("offsets"):
        el["offsets"] = {role: list(func(offset)) for role, offset in el["offsets"].items()}


def rotate(el, center):
    """繞 center 逆時針轉 90 度。"""
    cx, cy = center
    _map_points(el, lambda p: (round(cx - (p[1] - cy), 4), round(cy + (p[0] - cx), 4)))
    _offsets(el, lambda d: (-d[1], d[0]))
    if kind_of(el) == "multi":
        el["rot"] = (el.get("rot", 0) + 1) % 4
    elif kind_of(el) == "loop":
        for name, default in (("start", LOOP_START), ("end", LOOP_END)):
            el[name] = (el.get(name, default) + 90) % 360


def flip(el, center, horizontal=True):
    """以 center 為中心水平(左右)或垂直(上下)翻轉。多端元件換算成新的旋轉與鏡像:
    水平翻轉 = 轉 -rot、鏡像反過來;垂直翻轉 = 轉 2-rot、鏡像反過來。"""
    cx, cy = center
    if horizontal:
        _map_points(el, lambda p: (round(2 * cx - p[0], 4), p[1]))
        _offsets(el, lambda d: (-d[0], d[1]))
    else:
        _map_points(el, lambda p: (p[0], round(2 * cy - p[1], 4)))
        _offsets(el, lambda d: (d[0], -d[1]))
    kind = kind_of(el)
    if kind == "multi":
        rot = el.get("rot", 0)
        el["rot"] = (-rot) % 4 if horizontal else (2 - rot) % 4
        el["mirror"] = not el.get("mirror")
    elif kind == "loop":
        el["flip"] = not el.get("flip")         # 鏡像後繞的方向相反,起點、箭頭的角度也跟著鏡像
        for name, default in (("start", LOOP_START), ("end", LOOP_END)):
            angle = el.get(name, default)
            el[name] = (180 - angle) % 360 if horizontal else (-angle) % 360


def all_bounds(elements, include_labels=True):
    boxes = [box for box in (bounds(el, include_labels) for el in elements) if box]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


# ------------------------------------------------------------ 移動時接在上面的導線跟著走

def attachments(elements, moving):
    """moving 是要移動的元件編號;找出接在它們接點上、自己不動的導線端點與元件。
    回傳 [(元件編號, 端點名稱, 原本位置)],端點名稱:導線是 0 或 -1,兩端元件是 "a"、"b"。"""
    points = []
    for i in moving:
        el = elements[i]
        points += [tuple(el["pts"][0]), tuple(el["pts"][-1])] if el["kind"] == "wire" else connection_points(el)
    found = []
    for index, el in enumerate(elements):
        if index in moving:
            continue
        if el["kind"] == "wire" and len(el["pts"]) > 1:
            for end in (0, -1):
                if any(same(el["pts"][end], p) for p in points):
                    found.append((index, end, tuple(el["pts"][end])))
    return found


def touching(elements, moving):
    """不動的元件直接接在要移動的元件接點上(中間沒有導線)的位置;移動時要補一條導線才不會斷開。"""
    points = [p for i in moving if elements[i]["kind"] != "wire" for p in connection_points(elements[i])]
    found = []
    for index, el in enumerate(elements):
        if index in moving or el["kind"] == "wire":
            continue
        for q in connection_points(el):
            if any(same(q, p) for p in points) and not any(same(q, f) for f in found):
                found.append(q)
    return found


def route(fixed, moved, original):
    """導線一端固定、另一端移到 moved:照原本的路線調整轉角,保持直角。
    original 是移動前的點列(fixed 在第一個)。"""
    pts = [list(p) for p in original]
    pts[-1] = list(moved)
    if len(pts) == 2:
        if pts[0][0] != pts[1][0] and pts[0][1] != pts[1][1]:
            first_horizontal = original[0][1] == original[1][1] and original[0][0] != original[1][0]
            corner = [pts[1][0], pts[0][1]] if first_horizontal else [pts[0][0], pts[1][1]]
            pts.insert(1, corner)
        return pts
    before, neighbor = original[-1], original[-2]
    if neighbor[1] == before[1]:                # 最後一段是橫的:轉角跟著上下移
        pts[-2] = [pts[-2][0], moved[1]]
    elif neighbor[0] == before[0]:              # 最後一段是直的:轉角跟著左右移
        pts[-2] = [moved[0], pts[-2][1]]
    return pts


def segment_at(pts, point, tolerance):
    """point 落在導線的第幾段上;沒有回傳 None。"""
    for index, (p, q) in enumerate(zip(pts, pts[1:])):
        if _segment_distance(point, p, q) <= tolerance:
            return index
    return None


def drag_segment(original, index, delta):
    """像 PSpice 一樣拖曳導線的其中一段:橫的只能上下移、直的只能左右移,前後兩段跟著伸縮,
    導線的兩端不動(拖的是頭尾那段時,補一小段轉角接回原本的端點)。"""
    pts = [list(p) for p in original]
    p, q = pts[index], pts[index + 1]
    horizontal = p[1] == q[1]
    dx, dy = (0, delta[1]) if horizontal else (delta[0], 0)
    if dx == dy == 0:
        return pts
    moved = [[round(p[0] + dx, 4), round(p[1] + dy, 4)], [round(q[0] + dx, 4), round(q[1] + dy, 4)]]
    last = len(pts) - 1
    before = pts[:index] + ([pts[0]] if index == 0 else [])            # 拖頭一段:原本的起點留著
    after = pts[index + 2:] + ([pts[-1]] if index + 1 == last else [])  # 拖最後一段:原本的終點留著
    return clean_wire(before + moved + after)


def clean_wire(pts):
    """去掉重複的點與走回頭的轉角。"""
    out = []
    for p in pts:
        if out and same(out[-1], p):
            continue
        out.append(list(p))
        while len(out) >= 3:
            (x1, y1), (x2, y2), (x3, y3) = out[-3], out[-2], out[-1]
            if (x1 == x2 == x3) or (y1 == y2 == y3):    # 三點一直線,中間的點不需要
                out.pop(-2)
            else:
                break
    return out


# ------------------------------------------------------------ 自動命名、存檔

def next_label(elements, kind):
    prefix = parts.PREFIX.get(kind)
    if not prefix:
        return ""
    used = set()
    for el in elements:
        label = el.get("label", "").strip("$")
        for form in (prefix + "_", prefix + "_{", prefix):
            if label.startswith(form) and label[len(form):].rstrip("}").isdigit():
                used.add(int(label[len(form):].rstrip("}")))
    number = 1
    while number in used:
        number += 1
    if len(prefix) > 1:
        return f"{prefix}{number}"
    return f"{prefix}_{number}" if number < 10 else f"{prefix}_{{{number}}}"   # 兩位數要加大括號才整個變下標


def dumps(elements, settings):
    return json.dumps(dict(app="Naiz Studio 電路圖", version=FILE_VERSION, settings=settings, elements=elements),
                      ensure_ascii=False, indent=1)


def _upgrade(el):
    """舊版的檔案:網目電流(固定大小)換成可以調大小的迴路電流;方塊從兩端元件換成可以拉大小的框。"""
    if el.get("kind") == "block" and el.get("a") and el.get("b") and el["a"][1] == el["b"][1]:
        cx, cy = (el["a"][0] + el["b"][0]) / 2, el["a"][1]      # 舊的方塊是兩端元件(高度 0)
        return dict(kind="block", a=[cx - 0.5, cy + 0.5], b=[cx + 0.5, cy - 0.5], label=el.get("label", ""))
    if el.get("kind") == "mesh":
        x, y = el.get("at", [0, 0])
        return dict(kind="loop", a=[x - 0.35, y + 0.35], b=[x + 0.35, y - 0.35], square=0.0, flip=False,
                    label=el.get("label", ""), value="")
    return el


def _point(value):
    """[x, y] 兩個數字;不是的話回傳 None。"""
    if isinstance(value, (list, tuple)) and len(value) == 2 and \
            all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value):
        return [round(float(value[0]), 4), round(float(value[1]), 4)]
    return None


def _number(value, default, low=None, high=None):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return default
    value = float(value)
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def clean(el):
    """整理讀進來的一個元件:缺欄位、型別不對的修正或丟掉(回傳 None),
    手改過或壞掉的檔案也不會讓畫面出錯。"""
    if not isinstance(el, dict):
        return None
    el = _upgrade(el)
    kind = el.get("kind")
    if kind == "wire":
        pts = [p for p in (_point(v) for v in el.get("pts", []) if isinstance(el.get("pts"), list)) if p]
        return dict(kind="wire", pts=pts) if len(pts) >= 2 else None
    if kind not in parts.PARTS:
        return None
    shape = parts.PARTS[kind]["kind"]
    out = new(kind)
    for key in ("label", "value"):
        if key in out:
            out[key] = el.get(key) if isinstance(el.get(key), str) else str(el.get(key, "") or "")
    if "flip" in out:
        out["flip"] = bool(el.get("flip", False))
    if shape in ("multi", "text"):
        out["at"] = _point(el.get("at"))
        if out["at"] is None:
            return None
        if shape == "multi":
            out["rot"] = int(_number(el.get("rot"), 0)) % 4
            out["mirror"] = bool(el.get("mirror", False))
    else:
        out["a"], out["b"] = _point(el.get("a")), _point(el.get("b"))
        if out["a"] is None or out["b"] is None:
            return None
    if kind == "loop":
        out["square"] = _number(el.get("square"), 0.0, 0.0, 1.0)
        for key, default in (("start", LOOP_START), ("end", LOOP_END)):
            if key in el:
                out[key] = _number(el.get(key), default) % 360
    offsets = el.get("offsets")
    if isinstance(offsets, dict):
        kept = {role: _point(value) for role, value in offsets.items() if role in ("label", "value")}
        kept = {role: value for role, value in kept.items() if value}
        if kept:
            out["offsets"] = kept
    return out


def loads(text):
    """讀電路圖檔;讀不懂的部分略過。整份不是電路圖時丟出 ValueError。"""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("不是電路圖檔")
    items = data.get("elements", [])
    elements = [el for el in (clean(item) for item in (items if isinstance(items, list) else [])) if el]
    settings = data.get("settings", {})
    return elements, settings if isinstance(settings, dict) else {}


def clone(elements):
    return copy.deepcopy(elements)
