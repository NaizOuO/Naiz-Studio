"""座標換算。

編輯器裡的註解一律記在「頁面座標」:以頁面左上角為原點、往右往下為正,單位是點,
方向是頁面本身設定的旋轉之後(也就是 PageRef.size 的方向),還沒套用編輯時另外轉的角度。
這樣頁面在編輯器裡旋轉時註解不用改,儲存時再換回 PDF 的使用者座標(左下為原點、往上為正、不含旋轉)。

矩陣和 PDF 一樣是 (a, b, c, d, e, f):x' = a·x + c·y + e,y' = b·x + d·y + f。
"""

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def apply(m, point):
    x, y = point
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def compose(first, then):
    """先套用 first 再套用 then 的矩陣。"""
    a1, b1, c1, d1, e1, f1 = first
    a2, b2, c2, d2, e2, f2 = then
    return (a1 * a2 + b1 * c2, a1 * b2 + b1 * d2,
            c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
            e1 * a2 + f1 * c2 + e2, e1 * b2 + f1 * d2 + f2)


def invert(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    return (d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det)


def bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def transform_box(m, box):
    x0, y0, x1, y1 = box
    return bbox([apply(m, p) for p in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))])


def unrotated_size(size, base_rotation):
    """PDF 裡頁面框(沒轉之前)的寬高。"""
    width, height = size
    return (height, width) if base_rotation % 180 else (width, height)


def page_to_user(size, base_rotation, origin=(0.0, 0.0)):
    """頁面座標 → PDF 使用者座標。origin 是頁面框左下角的座標(裁切框不一定從 0 開始)。"""
    x0, y0 = origin
    width, height = unrotated_size(size, base_rotation)
    rotation = base_rotation % 360
    if rotation == 90:
        return (0.0, 1.0, 1.0, 0.0, x0, y0)
    if rotation == 180:
        return (-1.0, 0.0, 0.0, 1.0, x0 + width, y0)
    if rotation == 270:
        return (0.0, -1.0, -1.0, 0.0, x0 + width, y0 + height)
    return (1.0, 0.0, 0.0, -1.0, x0, y0 + height)


def user_to_page(size, base_rotation, origin=(0.0, 0.0)):
    return invert(page_to_user(size, base_rotation, origin))


def ref_to_user(ref):
    return page_to_user(ref.size, ref.base_rotation, ref.origin)


def ref_from_user(ref):
    return user_to_page(ref.size, ref.base_rotation, ref.origin)


def local_to_page(box):
    """註解外觀內部的座標(以註解範圍左下角為原點、往上為正)→ 頁面座標。"""
    x0, _, _, y1 = box
    return (1.0, 0.0, 0.0, -1.0, x0, y1)


def shown(point, size, rotation):
    """頁面座標 → 編輯器畫面上(套用編輯時旋轉後)的位置,單位仍是點。"""
    p, q = point
    width, height = size
    rotation %= 360
    if rotation == 90:
        return height - q, p
    if rotation == 180:
        return width - p, height - q
    if rotation == 270:
        return q, width - p
    return p, q


def unshown(point, size, rotation):
    s, t = point
    width, height = size
    rotation %= 360
    if rotation == 90:
        return t, height - s
    if rotation == 180:
        return width - s, height - t
    if rotation == 270:
        return width - t, s
    return s, t


def shown_box(box, size, rotation):
    x0, y0, x1, y1 = box
    return bbox([shown(p, size, rotation) for p in ((x0, y0), (x1, y1))])


def distance_to_segment(point, a, b):
    px, py = point
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
