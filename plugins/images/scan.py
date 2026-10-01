"""文件掃描:自動找出照片裡文件的四個角,以及把拍的文件變成乾淨掃描檔的濾鏡(去陰影、白底)。
只用 numpy 與 Pillow,不需要下載任何東西。

找邊:從畫面邊緣往內灌水,被文件的邊線擋住、灌不到的那一塊就是文件,
再取最左上、右上、右下、左下的點當四個角。
濾鏡:先估計每個地方「紙的顏色」(包含陰影、光線不均),原圖除以它,紙就變成均勻的白色。
"""

import numpy
from PIL import Image, ImageDraw, ImageFilter, ImageOps

FILTERS = [(None, "原色"), ("enhance", "增強"), ("gray", "灰階"), ("bw", "黑白")]
FILTER_NOTES = {None: "照片原本的顏色", "enhance": "去掉陰影、紙變白，保留顏色，適合有彩色的講義",
                "gray": "去掉陰影的灰階，字比較柔和", "bw": "只有黑白，字最清楚、檔案最小，適合純文字"}
DETECT_SIDE = 400       # 找邊時先縮到這麼大,快又不受雜訊影響
MIN_AREA, MAX_AREA = 0.12, 0.97
MAX_INK = 0.9           # 圈到的範圍裡幾乎都是筆畫:是一團密密的字,不是一張紙


def _otsu(values):
    """大津法:把灰階分成兩群時,讓兩群差最多的門檻。"""
    hist = numpy.bincount(values.ravel(), minlength=256).astype(numpy.float64)
    total = hist.sum()
    weight = numpy.cumsum(hist)
    mean = numpy.cumsum(hist * numpy.arange(256))
    between = (mean[-1] * weight - mean * total) ** 2 / numpy.maximum(weight * (total - weight), 1)
    return int(numpy.argmax(between))


def _corners(region):
    """一塊區域最左上、右上、右下、左下的點(比例座標)。"""
    ys, xs = numpy.nonzero(region)
    height, width = region.shape
    total, diff = xs + ys, xs - ys
    picks = [numpy.argmin(total), numpy.argmax(diff), numpy.argmax(total), numpy.argmin(diff)]
    return tuple((float(xs[i]) / (width - 1), float(ys[i]) / (height - 1)) for i in picks)


def _area(points):
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]))) / 2


def _walls(gray):
    """明顯的邊線(文件的邊、字的筆畫);柔和的陰影邊緣不算。"""
    values = numpy.asarray(gray, dtype=numpy.float32)
    gx = numpy.zeros_like(values)
    gy = numpy.zeros_like(values)
    gx[:, 1:-1] = values[:, 2:] - values[:, :-2]
    gy[1:-1, :] = values[2:, :] - values[:-2, :]
    magnitude = numpy.hypot(gx, gy)
    level = max(14.0, 0.3 * float(numpy.percentile(magnitude, 99)))
    walls = Image.fromarray(numpy.where(magnitude > level, 255, 0).astype(numpy.uint8))
    return walls.filter(ImageFilter.MaxFilter(3))     # 加粗一點,邊線上小小的缺口也擋得住


def find_document(image):
    """照片裡文件的四個角(左上、右上、右下、左下的比例座標);找不到或整張都是文件時回傳 None。

    從畫面邊緣往內灌水:桌面連著畫面邊緣,水流得過去;文件的邊是明顯的邊線,像牆一樣擋住水,
    灌不到的那一塊就是文件。陰影的邊緣是柔和的漸層,擋不住水,所以不會被當成文件。"""
    small = image.convert("RGB")
    small.thumbnail((DETECT_SIDE, DETECT_SIDE), Image.Resampling.BILINEAR)
    gray = ImageOps.grayscale(small).filter(ImageFilter.GaussianBlur(1.5))
    walls = _walls(gray)
    ink = numpy.asarray(walls) == 255
    width, height = walls.size
    # 外面加一圈空地,從角落灌一次水就能沿著整圈邊緣流進來
    field = Image.new("L", (width + 2, height + 2), 0)
    field.paste(walls, (1, 1))
    ImageDraw.floodfill(field, (0, 0), 128)
    inside = numpy.asarray(field)[1:-1, 1:-1] != 128
    mask = Image.fromarray(numpy.where(inside, 255, 0).astype(numpy.uint8))
    mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))     # 去掉零星的小點
    best = None
    seeds = [(width // 2, height // 2)] + [(int(width * fx), int(height * fy)) for fx in (0.35, 0.65)
                                           for fy in (0.35, 0.65)]
    for seed in seeds:
        if mask.getpixel(seed) != 255:
            continue
        filled = mask.copy()
        ImageDraw.floodfill(filled, seed, 128)
        region = numpy.asarray(filled) == 128
        share = region.mean()
        if not MIN_AREA <= share <= MAX_AREA:
            continue
        points = _corners(region)
        # 四個角圍起來的面積要和區域差不多,不然是奇怪的形狀(例如連到別的東西)
        if _area(list(points)) < share * 0.8 or _area(list(points)) > share * 1.35:
            continue
        # 紙裡面大部分是空白;整塊幾乎都是筆畫的話,是被當成牆的密集文字(例如白紙放在白桌上、整張都是文件)
        core = numpy.asarray(Image.fromarray(numpy.where(region, 255, 0).astype(numpy.uint8))
                             .filter(ImageFilter.MinFilter(9))) == 255
        if core.any() and ink[core].mean() > MAX_INK:
            continue
        if best is None or share > best[0]:
            best = (share, points)
    if best is None:
        return None
    # 文件的邊線(加粗過)本身也算在灌不到的範圍裡,四個角會往外多出約 3.5 像素,往內收回來
    cx = sum(x for x, _ in best[1]) / 4
    cy = sum(y for _, y in best[1]) / 4
    grow = -3.5 / max(width, height)
    return tuple((min(1.0, max(0.0, x + (grow if x > cx else -grow))), min(1.0, max(0.0, y + (grow if y > cy else -grow))))
                 for x, y in best[1])


def _paper(rgb):
    """每個地方「紙的顏色」:縮小後取附近最亮的(去掉字),再大範圍模糊,放大回原尺寸。"""
    small = rgb.copy()
    small.thumbnail((600, 600), Image.Resampling.BILINEAR)
    radius = max(small.size) / 60
    paper = small.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(radius * 3))
    return paper.resize(rgb.size, Image.Resampling.BILINEAR)


def scan_filter(image, key):
    """把拍的文件變成掃描檔的樣子;key 是 FILTERS 裡的代號。透明的地方保持透明。"""
    if not key:
        return image
    alpha = image.getchannel("A") if image.mode in ("RGBA", "LA", "PA") else None
    rgb = image.convert("RGB")
    source = numpy.asarray(rgb, dtype=numpy.float32)
    paper = numpy.asarray(_paper(rgb), dtype=numpy.float32)
    flat = numpy.clip(source / numpy.maximum(paper, 1) * 255, 0, 255)
    if key == "enhance":
        # 白底更白、字更深;顏色稍微加強,看起來像影印出來的彩色講義
        result = Image.fromarray(numpy.clip((flat - 30) * 255 / 215, 0, 255).astype(numpy.uint8))
        from PIL import ImageEnhance

        result = ImageEnhance.Color(result).enhance(1.25)
    else:
        gray = flat @ numpy.array([0.299, 0.587, 0.114], dtype=numpy.float32)
        if key == "gray":
            result = Image.fromarray(numpy.clip((gray - 40) * 255 / 200, 0, 255).astype(numpy.uint8))
        else:
            smooth = Image.fromarray(gray.astype(numpy.uint8)).filter(ImageFilter.GaussianBlur(0.6))
            result = smooth.point(lambda v: 255 if v > 175 else 0)
        result = result.convert("RGB")
    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)
    return result
