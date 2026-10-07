"""干涉環分析的計算(不含畫面):讀圖、取通道、橫切線亮度剖面、平滑、去刻度線、找峰、對稱配對、中心、標籤、輸出。
只用 numpy 和 Pillow(主程式都有),不需要另外安裝套件。座標一律是原圖的像素。"""

import csv
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

CHANNELS = [("R", "紅"), ("G", "綠"), ("B", "藍"), ("L", "亮度")]
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".dng"}


# ------------------------------------------------------------ 讀圖

def load_rgb(path):
    """原始解析度的 RGB 陣列(高, 寬, 3),uint8;方向依 EXIF 轉正。DNG 用相機內建的全尺寸照片。"""
    path = Path(path)
    data = path.read_bytes()
    if path.suffix.lower() == ".dng":
        image = _open_dng(data)
    else:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    return np.asarray(image.convert("RGB"))


def _open_dng(data):
    import rawpy

    with rawpy.imread(io.BytesIO(data)) as raw:
        full = max(raw.sizes.width, raw.sizes.height)
        try:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                preview = Image.open(io.BytesIO(thumb.data))
                if max(preview.size) >= full * 0.9:
                    return ImageOps.exif_transpose(preview)
        except (rawpy.LibRawError, OSError, ValueError):
            pass
        return Image.fromarray(raw.postprocess(use_camera_wb=True, output_bps=8), "RGB")


def channel(rgb, key):
    """強度圖(浮點數,0～255):R/G/B 單一通道,或 L 亮度(0.299R+0.587G+0.114B)。"""
    if key == "L":
        return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
    return rgb[..., "RGB".index(key)].astype(np.float32)


def display_gray(intensity, stretch):
    """顯示用的灰階(uint8);stretch 時把 1%～99% 的範圍拉到 0～255(只影響顯示)。"""
    if stretch:
        low, high = np.percentile(intensity[::4, ::4], (1, 99))
        if high > low:
            return np.clip((intensity - low) * (255.0 / (high - low)), 0, 255).astype(np.uint8)
    return np.clip(intensity, 0, 255).astype(np.uint8)


# ------------------------------------------------------------ 剖面

def profile(intensity, y, x0, x1, band):
    """橫切線:y 為中心、上下共 band 列取平均;回傳 (x 座標陣列, 亮度陣列)。"""
    height, width = intensity.shape
    x0, x1 = sorted((int(round(x0)), int(round(x1))))
    x0, x1 = max(0, x0), min(width - 1, x1)
    band = max(1, int(band))
    top = int(round(y - band / 2))
    top = max(0, min(height - band, top))
    rows = intensity[top:top + band, x0:x1 + 1]
    return np.arange(x0, x1 + 1, dtype=np.float64), rows.mean(axis=0).astype(np.float64)


def profile_line(intensity, start, end, band):
    """任意方向的橫切線:從 start 到 end 每 1 像素取一點,垂直方向取 band 條平行線的平均(雙線性內插)。
    回傳 (沿線距離 t 陣列(px,起點為 0), 亮度陣列)。"""
    (x0, y0), (x1, y1) = start, end
    length = float(np.hypot(x1 - x0, y1 - y0))
    if length < 2:
        return np.zeros(1), np.zeros(1)
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    nx, ny = -uy, ux
    t = np.arange(int(length) + 1, dtype=np.float64)
    band = max(1, int(band))
    offsets = np.linspace(-(band - 1) / 2, (band - 1) / 2, band)
    px = x0 + ux * t[None, :] + nx * offsets[:, None]
    py = y0 + uy * t[None, :] + ny * offsets[:, None]
    return t, bilinear(intensity, px, py).mean(axis=0)


def bilinear(image, x, y):
    height, width = image.shape
    x = np.clip(x, 0, width - 1.001)
    y = np.clip(y, 0, height - 1.001)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = x - x0, y - y0
    top = image[y0, x0] * (1 - fx) + image[y0, x0 + 1] * fx
    bottom = image[y0 + 1, x0] * (1 - fx) + image[y0 + 1, x0 + 1] * fx
    return top * (1 - fy) + bottom * fy


def point_on(line, t):
    """沿線距離 t 在原圖上的座標。"""
    (x0, y0), (x1, y1) = line
    length = float(np.hypot(x1 - x0, y1 - y0)) or 1.0
    return x0 + (x1 - x0) * t / length, y0 + (y1 - y0) * t / length


def gaussian(values, sigma):
    if sigma <= 0 or len(values) < 3:
        return values.copy()
    radius = max(1, int(round(sigma * 3)))
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    kernel /= kernel.sum()
    padded = np.pad(values, radius, mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def remove_dips(values, width):
    """去掉比 width 個像素窄的暗谷(刻度線):灰階「閉運算」(先取區間最大、再取區間最小),
    窄的凹陷會被兩側補平,寬的峰谷形狀不變。"""
    width = int(width)
    if width < 2 or len(values) < width:
        return values.copy()
    half = width // 2

    def running(func, data):
        padded = np.pad(data, half, mode="edge")
        windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1)
        return func(windows, axis=1)

    return running(np.min, running(np.max, values))


def process(values, sigma, dip_width, despike):
    out = remove_dips(values, dip_width) if despike else values.copy()
    return gaussian(out, sigma)


# ------------------------------------------------------------ 找峰

def prominences(values, peaks):
    """每個峰的顯著度:往左右走到比它高的地方(或邊界)之前的最低點,取兩邊最低點較高的那個,峰高減掉它。"""
    result = []
    n = len(values)
    for p in peaks:
        height = values[p]
        left = p
        low_left = height
        while left > 0 and values[left - 1] <= height:
            left -= 1
            low_left = min(low_left, values[left])
        right = p
        low_right = height
        while right < n - 1 and values[right + 1] <= height:
            right += 1
            low_right = min(low_right, values[right])
        result.append(height - max(low_left, low_right))
    return np.array(result)


def find_peaks(values, prominence, distance):
    """局部最大值裡,顯著度夠的;太近的只留比較高的(和 scipy.signal.find_peaks 的 distance 一樣的規則)。"""
    v = np.asarray(values, dtype=np.float64)
    if len(v) < 3:
        return []
    candidates = []
    i = 1
    while i < len(v) - 1:
        if v[i] > v[i - 1]:
            j = i
            while j < len(v) - 1 and v[j + 1] == v[i]:
                j += 1                       # 平頂:取中間
            if j < len(v) - 1 and v[j + 1] < v[i]:
                candidates.append((i + j) // 2)
            i = j + 1
        else:
            i += 1
    if not candidates:
        return []
    proms = prominences(v, candidates)
    kept = [p for p, prom in zip(candidates, proms) if prom >= prominence]
    if distance > 1:
        order = sorted(kept, key=lambda p: v[p], reverse=True)
        chosen = []
        for p in order:
            if all(abs(p - q) >= distance for q in chosen):
                chosen.append(p)
        kept = sorted(chosen)
    return kept


def refine(values, index):
    """次像素峰位置:峰頂和左右各 2 點(共 5 點)擬合拋物線,頂點就是峰的位置(等於局部扣掉背景)。
    Fabry–Perot 的亮紋接近 Lorentzian 不是高斯,只擬合峰頂幾點比擬合整個峰形穩定。
    回傳浮點數的索引;峰在邊緣或擬合不合理時回傳原本的整數。"""
    if index < 2 or index > len(values) - 3:
        return float(index)
    xs = np.arange(-2, 3, dtype=np.float64)
    a, b, _ = np.polyfit(xs, np.asarray(values[index - 2:index + 3], dtype=np.float64), 2)
    if a >= 0:
        return float(index)                 # 不是往下彎的峰頂
    offset = -b / (2 * a)
    return float(index + offset) if abs(offset) <= 1.5 else float(index)


# ------------------------------------------------------------ Zeeman 分析
# r²:左右都有的標籤用 (右 − 左)/2;只有一邊的用 |x − 中心|(切線不過環心時,r² 的差仍然正確,見下)
# 半弦長平方 x² = r² − y²,同一條切線上 y² 一樣,做 r² 的差時消去 → 比值 δ 不受切線位置影響
# Δ:同一級 σ 線的分裂 = (r²(p+) − r²(p−)) / 2(對稱分裂成 ±,取單邊的偏移量)
# D:π 線(p)的 r² 對級數 p 做線性迴歸的斜率(相鄰級的 r² 差;比只用兩環相減抗雜訊)
# δ = Δ / D:分裂是自由光譜範圍的幾分之幾;Δk = δ / (2 n t)(波數),Δλ = λ² δ / (2 n t)
HC_OVER_MUB = 2.14195       # h·c / μB(T·cm):Δk(cm⁻¹) = g·μB·B / (h·c) → g = 斜率 × 2.14195


def radii(peaks, center):
    """{標籤: r(px)};peaks:[(x, 標籤)]。"""
    by_label = {}
    for x, label in peaks:
        if label:
            by_label.setdefault(label, []).append(x)
    result = {}
    for label, xs in by_label.items():
        left = [x for x in xs if x < center]
        right = [x for x in xs if x > center]
        if len(left) == 1 and len(right) == 1:
            result[label] = (right[0] - left[0]) / 2
        elif len(xs) == 1:
            result[label] = abs(xs[0] - center)
    return result


def zeeman(peaks, center, px_per_mm=None):
    """單張照片的分析:回傳 {r: {標籤: r}, r2: {標籤: r²}, delta: {級: Δ}, D: 斜率或 None, ratio: δ 或 None}。
    有比例尺時 r、r² 用 mm,否則 px(比值 δ 和單位無關)。"""
    if center is None:
        return None
    scale = px_per_mm or 1.0
    r = {label: value / scale for label, value in radii(peaks, center).items()}
    r2 = {label: value ** 2 for label, value in r.items()}
    orders = sorted({_order_of(label) for label in r2 if _order_of(label)})
    delta = {}
    for p in orders:
        low, high = r2.get(f"{p}-"), r2.get(f"{p}+")
        if low is not None and high is not None:
            delta[p] = (high - low) / 2
    pi = [(p, r2[str(p)]) for p in orders if str(p) in r2]
    slope = None
    if len(pi) >= 2:
        ps = np.array([p for p, _ in pi], dtype=np.float64)
        values = np.array([v for _, v in pi])
        slope = float(np.polyfit(ps, values, 1)[0])
    ratio = None
    if slope and delta:
        ratio = float(np.mean(list(delta.values())) / slope)
    return {"r": r, "r2": r2, "delta": delta, "D": slope, "ratio": ratio, "unit": "mm" if px_per_mm else "px"}


def linear_fit(xs, ys):
    """最小平方直線 y = a·x + b;回傳 (a, b, a 的標準誤差)。點不夠時回傳 None。"""
    xs, ys = np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64)
    if len(xs) < 2 or np.ptp(xs) == 0:
        return None
    a, b = np.polyfit(xs, ys, 1)
    if len(xs) > 2:
        residual = ys - (a * xs + b)
        error = float(np.sqrt(np.sum(residual ** 2) / (len(xs) - 2) / np.sum((xs - xs.mean()) ** 2)))
    else:
        error = None
    return float(a), float(b), error


SUMMARY_HEADER = ["照片", "電流(A)", "磁場 B(T)", "r²單位", "D(相鄰級 r² 差)", "Δ(各級平均)", "δ = Δ/D",
                  "Δk(1/cm)", "Δλ(nm)"]


def snap(values, index, reach=6):
    """手動加峰:在點的位置附近找最高點。"""
    lo, hi = max(0, index - reach), min(len(values), index + reach + 1)
    return lo + int(np.argmax(values[lo:hi]))


# ------------------------------------------------------------ 中心與標籤

def pair_peaks(xs, tolerance):
    """依左右對稱自動配對:試每兩個峰的中點當中心,鏡射後對得上的峰最多(誤差總和最小)的那個。
    回傳 (中心估計, [(左峰 x, 右峰 x)],由內往外)。峰不夠時回傳 (None, [])。"""
    xs = sorted(xs)
    if len(xs) < 2:
        return None, []
    best = None
    for i in range(len(xs)):
        for j in range(i + 1, len(xs)):
            c = (xs[i] + xs[j]) / 2
            pairs, error = _mirror_pairs(xs, c, tolerance)
            score = (len(pairs), -error)
            if best is None or score > best[0]:
                best = (score, c, pairs)
    if best is None or not best[2]:
        return None, []
    _, c, pairs = best
    return c, sorted(pairs, key=lambda pair: pair[1] - pair[0])


def _mirror_pairs(xs, c, tolerance):
    left = [x for x in xs if x < c]
    right = [x for x in xs if x > c]
    pairs, used, error = [], set(), 0.0
    for x in sorted(left, reverse=True):
        mirror = 2 * c - x
        candidates = [r for r in right if r not in used and abs(r - mirror) <= tolerance]
        if candidates:
            r = min(candidates, key=lambda r: abs(r - mirror))
            used.add(r)
            pairs.append((x, r))
            error += abs(r - mirror)
    return pairs, error


def center_of(pairs):
    """中心 = 每組 (左+右)/2 的算術平均;回傳 (中心, [每組的中點])。"""
    if not pairs:
        return None, []
    mids = [(a + b) / 2 for a, b in pairs]
    return float(np.mean(mids)), mids


def label_peaks(xs, center, per_order, orders):
    """從中心往外每邊依序標:三條分裂時 1-、1、1+、2-、2、2+…;單條時 1、2、3…。
    超過 orders 級的峰不標(標籤是空的)。回傳 {x: 標籤}。"""
    labels = {}
    for side in (-1, 1):
        ordered = sorted((x for x in xs if (x - center) * side > 0), key=lambda x: abs(x - center))
        for k, x in enumerate(ordered):
            order = k // per_order + 1
            if order > orders:
                labels[x] = ""
            elif per_order == 3:
                labels[x] = f"{order}{['-', '', '+'][k % 3]}"
            else:
                labels[x] = str(order)
    return labels


def label_pairs(peaks, split, orders_used=(1,)):
    """同一個標籤左右各一個的,配成一組(用來定中心):回傳 [(標籤, 左 x, 右 x)]。
    peaks:[(x, 標籤)];split:分左右用的大概中心;orders_used:用哪幾級(一般只用第 1 級的 1-、1、1+)。"""
    result = []
    names = sorted({label for _, label in peaks if label}, key=_label_key)
    for name in names:
        if _order_of(name) not in orders_used:
            continue
        left = [x for x, label in peaks if label == name and x < split]
        right = [x for x, label in peaks if label == name and x > split]
        if len(left) == 1 and len(right) == 1:
            result.append((name, left[0], right[0]))
    return result


def _order_of(label):
    digits = "".join(ch for ch in label if ch.isdigit())
    return int(digits) if digits else 0


def _label_key(label):
    return _order_of(label), {"-": 0, "": 1, "+": 2}.get(label.lstrip("0123456789"), 3), label


def auto_labels(xs, center, per_order):
    """從中心往外每邊依序標:每級 3 條時 1-、1、1+、2-、2、2+…(用一般的減號,字型都有);每級 1 條時 1、2、3…。"""
    labels = {}
    for side in (-1, 1):
        ordered = sorted((x for x in xs if (x - center) * side > 0), key=lambda x: abs(x - center))
        for k, x in enumerate(ordered):
            if per_order == 3:
                labels[x] = f"{k // 3 + 1}{['-', '', '+'][k % 3]}"
            else:
                labels[x] = str(k + 1)
    return labels


# ------------------------------------------------------------ 輸出

CSV_HEADER = ["照片", "電流", "px/mm", "橫切線起點 x", "起點 y", "終點 x", "終點 y", "帶寬(px)", "中心(px)",
              "標籤", "位置(px)", "位置(mm)", "強度"]


def csv_rows(name, current, px_per_mm, line, band, center, peaks):
    """peaks:[(沿線距離 px, 標籤, 強度)];位置 mm 以中心為 0,往起點那邊為負、往終點那邊為正。"""
    (x0, y0), (x1, y1) = line
    rows = []
    for x, label, value in sorted(peaks):
        mm = (x - center) / px_per_mm if center is not None and px_per_mm else ""
        rows.append([name, current, round(px_per_mm, 4) if px_per_mm else "", round(x0, 1), round(y0, 1),
                     round(x1, 1), round(y1, 1), band, round(center, 2) if center is not None else "", label,
                     round(x, 2), round(mm, 4) if mm != "" else "", round(value, 2)])
    return rows


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as file:      # utf-8-sig:Excel 打開中文不會亂碼
        writer = csv.writer(file)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
