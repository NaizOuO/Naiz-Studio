"""圖片工具的運算:讀取(含 HEIC、SVG)、轉換格式、壓縮、合成 PDF,全部在本機處理。"""

import io
import math
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # 原始碼版沒裝這個套件時,只有 HEIC 讀不了
    pillow_heif = None

EXT_FORMAT = {".jpg": "jpg", ".jpeg": "jpg", ".jfif": "jpg", ".png": "png", ".webp": "webp", ".avif": "avif",
              ".heic": "heic", ".heif": "heic", ".gif": "gif", ".bmp": "bmp", ".tif": "tiff", ".tiff": "tiff",
              ".ico": "ico", ".svg": "svg"}
READ_EXTS = set(EXT_FORMAT)
SAVE_EXT = {"jpg": ".jpg", "png": ".png", "webp": ".webp", "avif": ".avif", "gif": ".gif", "bmp": ".bmp",
            "tiff": ".tif", "ico": ".ico", "svg": ".svg", "pdf": ".pdf", "heic": ".heic"}
LABELS = {"jpg": "JPG", "png": "PNG", "webp": "WebP", "avif": "AVIF", "gif": "GIF", "bmp": "BMP", "tiff": "TIFF",
          "ico": "ICO", "svg": "SVG", "pdf": "PDF", "heic": "HEIC"}
ANIMATED_FORMATS = {"gif", "webp"}
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
SVG_MAX_SIDE = 2000   # 描邊的時間隨像素數暴增;SVG 本身可以任意放大,先縮小再描不會損失尺寸
PDF_DPI = 96
HIGH_QUALITY = 92     # 沒開壓縮品質時,非 JPG 原圖改存有損格式所用的品質
A4 = (595, 842)
ORIENTATION = 0x0112


class Cancelled(Exception):
    pass


@dataclass
class Settings:
    fmt: str = "keep"
    quality: int = 80
    limit: bool = False
    max_side: int = 2000
    reduce_colors: bool = False
    strip_meta: bool = True
    auto_rotate: bool = True
    svg_color: str = "color"
    pdf_combine: bool = True
    pdf_page: str = "fit"
    compress: bool = False     # 關閉時保持原圖品質,quality 用不到
    split_frames: bool = False

    @property
    def side(self):
        return self.max_side if self.limit else None


@dataclass
class Edit:
    """單張圖的編輯,內部依序套用:任意角度(順時針為正)→ 右轉 quarter 次 90 度 → 水平翻轉 → 裁切。
    crop 是 (左, 上, 右, 下) 占「旋轉、翻轉後畫面」的比例,套用到尺寸不同的圖時會照比例裁。
    下面的操作都以使用者看到的畫面為準,會自動換算成上面的順序。"""

    angle: float = 0.0
    quarter: int = 0
    flip: bool = False
    crop: tuple = None
    fill: str = "clear"     # 旋轉、擴展畫布多出來的地方補什麼:clear 透明、white 白色、black 黑色

    @property
    def active(self):
        return bool(self.angle % 360 or self.quarter % 4 or self.flip or self.crop)

    def copy(self):
        return replace(self)

    def reset(self):
        self.angle, self.quarter, self.flip, self.crop, self.fill = 0.0, 0, False, None, "clear"

    @property
    def view_angle(self):
        # 翻轉過的圖,原本順時針的傾斜在畫面上看起來是逆時針
        return -self.angle if self.flip else self.angle

    def set_view_angle(self, value):
        value = max(-180.0, min(180.0, float(value)))
        self.angle = -value if self.flip else value

    def rotate_right(self, turns=1):
        """畫面順時針轉 turns 個 90 度(3 就是左轉),裁切框跟著轉。"""
        self.quarter = (self.quarter + (-turns if self.flip else turns)) % 4
        for _ in range(turns % 4 if self.crop else 0):
            left, top, right, bottom = self.crop
            self.crop = (1 - bottom, left, 1 - top, right)

    def flip_horizontal(self):
        self.flip = not self.flip
        if self.crop:
            left, top, right, bottom = self.crop
            self.crop = (1 - right, top, 1 - left, bottom)

    def flip_vertical(self):
        # 垂直翻轉 = 水平翻轉再轉 180 度
        self.flip = not self.flip
        self.quarter = (self.quarter + 2) % 4
        if self.crop:
            left, top, right, bottom = self.crop
            self.crop = (left, 1 - bottom, right, 1 - top)


QUARTER_TURNS = {1: Image.Transpose.ROTATE_270, 2: Image.Transpose.ROTATE_180, 3: Image.Transpose.ROTATE_90}
FILLS = {"clear": (0, 0, 0, 0), "white": (255, 255, 255, 255), "black": (0, 0, 0, 255)}


def crop_box(size, crop):
    """比例裁切框換成像素範圍,至少留 1 像素;擴展畫布時範圍可以超出圖片(小於 0 或大於寬高)。"""
    width, height = size
    left, top = round(crop[0] * width), round(crop[1] * height)
    return left, top, max(left + 1, round(crop[2] * width)), max(top + 1, round(crop[3] * height))


def apply_edit(image, edit):
    if edit is None or not edit.active:
        return image
    fill = FILLS.get(edit.fill, FILLS["clear"])
    if edit.angle % 360:
        # 畫布放大保留整張圖,多出來的角補上選的顏色(透明存成 JPG 時會變白色)
        image = image.convert("RGBA").rotate(-edit.angle, Image.Resampling.BICUBIC, expand=True, fillcolor=fill)
    if edit.quarter % 4:
        image = image.transpose(QUARTER_TURNS[edit.quarter % 4])
    if edit.flip:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if edit.crop:
        box = crop_box(image.size, edit.crop)
        if box[0] < 0 or box[1] < 0 or box[2] > image.width or box[3] > image.height:
            # 擴展畫布:先鋪滿選的顏色再把圖貼上去;直接貼上不混色,原圖本身的透明會保留
            canvas = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), fill)
            canvas.paste(image.convert("RGBA"), (-box[0], -box[1]))
            image = canvas
        else:
            image = image.crop(box)
    return image


def edited_size(size, edit, with_crop=True):
    """不實際處理圖片,算出編輯後的尺寸。"""
    width, height = size
    if edit is not None:
        if edit.angle % 360:
            rad = math.radians(edit.angle)
            cos, sin = abs(math.cos(rad)), abs(math.sin(rad))
            width, height = width * cos + height * sin, width * sin + height * cos
        if edit.quarter % 2:
            width, height = height, width
    width, height = max(1, round(width)), max(1, round(height))
    if edit is not None and with_crop and edit.crop:
        # 和實際裁切用同一套取整數,顯示的尺寸才不會差 1
        left, top, right, bottom = crop_box((width, height), edit.crop)
        width, height = right - left, bottom - top
    return width, height


def describe_error(exc):
    if isinstance(exc, UnidentifiedImageError):
        return "檔案損壞或不是支援的圖片"
    if isinstance(exc, MemoryError):
        return "圖片太大，記憶體不足"
    return str(exc) or type(exc).__name__


def source_format(path):
    return EXT_FORMAT.get(Path(path).suffix.lower(), "")


def target_format(path, fmt):
    """「原格式」時 SVG 存成 PNG(向量圖本身沒辦法壓縮),其他格式維持不變。"""
    if fmt != "keep":
        return fmt
    source = source_format(path)
    return "png" if source == "svg" else source


def free_path(folder: Path, stem: str, ext: str) -> Path:
    """不覆蓋既有檔案:同名時加上 (2)、(3)。"""
    out, number = folder / f"{stem}{ext}", 2
    while out.exists():
        out = folder / f"{stem} ({number}){ext}"
        number += 1
    return out


# ------------------------------------------------------------ 讀取


def open_image(path, svg_side=None):
    """SVG 用 PyMuPDF 畫成點陣圖(svg_side 指定最長邊,沒指定時用原本大小)。"""
    path = Path(path)
    data = path.read_bytes()   # 先讀進記憶體,處理時不會一直佔用原檔
    source = source_format(path)
    if source == "svg":
        import pymupdf

        with pymupdf.open(stream=data, filetype="svg") as doc:
            page = doc[0]
            zoom = svg_side / max(page.rect.width, page.rect.height, 1) if svg_side else 1.0
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True)
            return Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
    if source == "heic" and pillow_heif is None:
        raise RuntimeError("缺少讀取 HEIC 的元件")
    return Image.open(io.BytesIO(data))


def probe(path, thumb_box):
    """清單要顯示的資訊:格式、尺寸(轉正後)、動畫格數,以及 RGBA 縮圖。"""
    image = open_image(path)
    frames = getattr(image, "n_frames", 1)
    width, height = image.size
    if frames == 1 and image.getexif().get(ORIENTATION, 1) in (5, 6, 7, 8):
        width, height = height, width
    if image.format == "JPEG":
        image.draft("RGB", (thumb_box[0] * 2, thumb_box[1] * 2))   # 大張 JPG 直接用低解析度解碼,快很多
    shown = ImageOps.exif_transpose(image) if frames == 1 else image
    thumb = shown.convert("RGBA")
    thumb.thumbnail(thumb_box, Image.Resampling.LANCZOS)
    return {"format": LABELS.get(source_format(path), "?"), "size": (width, height), "frames": frames,
            "thumb": (thumb.size, thumb.tobytes())}


# ------------------------------------------------------------ 處理


def _normalize(image):
    """統一成 RGB / RGBA / L,縮放和存檔時才不會遇到調色盤、CMYK 之類的模式。"""
    if image.mode in ("RGB", "RGBA", "L"):
        return image
    alpha = image.mode in ("LA", "PA", "RGBa", "La") or "transparency" in image.info
    return image.convert("RGBA" if alpha else "RGB")


def _fit(image, side):
    """最長邊超過 side 時等比例縮小;只縮不放。"""
    if side and max(image.size) > side:
        scale = side / max(image.size)
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        return image.resize(size, Image.Resampling.LANCZOS)
    return image


def load_view(path, auto_rotate, side):
    """編輯視窗的底圖:和輸出時一樣轉正,縮到 side 以內;回傳 (圖, 原本尺寸)。"""
    image = open_image(path)
    if auto_rotate and getattr(image, "n_frames", 1) == 1:
        image = ImageOps.exif_transpose(image)
    image = image.convert("RGBA")
    full = image.size
    image.thumbnail((side, side), Image.Resampling.LANCZOS)
    return image, full


def load_frames(path, side):
    """編輯視窗播放動畫用:每一格(RGBA,縮到 side 以內)和顯示的毫秒數。"""
    image = open_image(path)
    frames = []
    for frame in ImageSequence.Iterator(image):
        picture = frame.convert("RGBA")
        picture.thumbnail((side, side), Image.Resampling.LANCZOS)
        frames.append((picture, frame.info.get("duration", image.info.get("duration", 100)) or 100))
    return frames


def _prepare(image, s, edit=None):
    """轉正、統一色彩模式、編輯、縮小;回傳 (圖片, 要保留的 EXIF 或 None)。"""
    icc = image.info.get("icc_profile")
    if s.auto_rotate:
        image = ImageOps.exif_transpose(image)
    exif = None
    if not s.strip_meta:
        data = image.getexif()
        exif = data.tobytes() if len(data) else None
    image = _fit(apply_edit(_normalize(image), edit), s.side)
    # 清掉其他附帶資料(XMP、PNG 文字欄位等),只留色彩描述檔,避免移除拍攝資訊時還有漏網之魚
    image.info = {"icc_profile": icc} if icc else {}
    return image, exif


def _flatten(image):
    """透明處墊白色,給不支援透明的 JPG 用。"""
    if image.mode != "RGBA":
        return image
    ground = Image.new("RGB", image.size, (255, 255, 255))
    ground.paste(image, mask=image.getchannel("A"))
    return ground


def _reduce_colors(image):
    if image.mode == "L":
        return image
    method = Image.Quantize.FASTOCTREE if image.mode == "RGBA" else Image.Quantize.MEDIANCUT
    return image.quantize(256, method=method)


def _trace(image, s):
    import vtracer

    image = _fit(image, SVG_MAX_SIDE)
    if s.svg_color == "bw":
        image = _flatten(image)
    png = io.BytesIO()
    image.save(png, "PNG")
    # vtracer 0.6.15 在 Python 3.14 用具名參數呼叫會直接讓程式崩潰,所以全部依位置傳入:
    # 格式、顏色、分層、曲線、雜點、色彩精度、色層差、轉角、線段長度、迭代、接合、座標精度
    svg = vtracer.convert_raw_image_to_svg(png.getvalue(), "png", "binary" if s.svg_color == "bw" else "color",
                                           "stacked", "spline", 4, 6, 16, 60, 4.0, 10, 45, 8)
    return svg.encode("utf-8")


def _jpeg_tables(image, s):
    """沒開壓縮品質(保持原圖品質):原圖是 JPG 時記下它的量化表和色度取樣,存 JPG 時照用,畫質等同原圖。
    必須在 _prepare 之前取得,處理過後就不知道原圖怎麼壓的。"""
    if s.compress or getattr(image, "format", None) != "JPEG" or not getattr(image, "quantization", None):
        return None
    from PIL import JpegImagePlugin

    return image.quantization, JpegImagePlugin.get_sampling(image)


def encode(image, target, s, exif=None, tables=None):
    """把處理好的單張圖存成指定格式,回傳檔案內容;tables 是原圖的 JPG 壓縮設定。"""
    if target == "svg":
        return _trace(image, s)
    buf = io.BytesIO()
    extra = {}
    if image.info.get("icc_profile"):
        extra["icc_profile"] = image.info["icc_profile"]
    if exif and target in ("jpg", "png", "webp", "avif", "heic"):
        extra["exif"] = exif
    # 保持原圖品質時:JPG 原圖照用原本的壓縮設定;其他格式讀不出原本的品質,用接近原圖的高品質
    quality = s.quality if s.compress else HIGH_QUALITY
    if target == "jpg":
        if tables:
            qtables, sampling = tables
            if sampling >= 0:
                extra["subsampling"] = sampling
            _flatten(image).save(buf, "JPEG", qtables=qtables, optimize=True, progressive=True, **extra)
        else:
            _flatten(image).save(buf, "JPEG", quality=quality, optimize=True, progressive=True, **extra)
    elif target == "png":
        (_reduce_colors(image) if s.reduce_colors else image).save(buf, "PNG", optimize=True, **extra)
    elif target == "webp":
        image.save(buf, "WEBP", quality=quality, method=5, **extra)
    elif target == "heic":
        if pillow_heif is None:
            raise RuntimeError("缺少輸出 HEIC 的元件")
        image.save(buf, "HEIF", quality=quality, **extra)
    elif target == "avif":
        image.save(buf, "AVIF", quality=quality, **extra)
    elif target == "gif":
        image.save(buf, "GIF", optimize=True)
    elif target == "bmp":
        image.save(buf, "BMP")
    elif target == "tiff":
        extra.pop("exif", None)
        image.save(buf, "TIFF", compression="tiff_adobe_deflate", **extra)
    elif target == "ico":
        # 圖示必須是正方形:不裁切,四周補透明
        side = max(image.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(image.convert("RGBA"), ((side - image.width) // 2, (side - image.height) // 2))
        sizes = [(n, n) for n in ICO_SIZES if n <= side] or [(side, side)]
        square.save(buf, "ICO", sizes=sizes)
    else:
        raise ValueError(f"不支援的輸出格式：{target}")
    return buf.getvalue()


def _encode_animation(image, target, s, edit=None):
    """動畫不做有損壓縮:每一格原樣保留(需要時編輯、縮小),WebP 用無損模式。"""
    frames, durations = [], []
    for frame in ImageSequence.Iterator(image):
        durations.append(frame.info.get("duration", image.info.get("duration", 100)))
        frames.append(_fit(apply_edit(frame.convert("RGBA"), edit), s.side))
    options = {"save_all": True, "append_images": frames[1:], "duration": durations}
    if "loop" in image.info:
        options["loop"] = image.info["loop"]
    buf = io.BytesIO()
    if target == "gif":
        frames[0].save(buf, "GIF", disposal=2, **options)
    else:
        frames[0].save(buf, "WEBP", lossless=True, method=4, **options)
    return buf.getvalue()


def convert(path, folder: Path, s: Settings, edit=None):
    """轉換一張圖;回傳 (輸出檔, 附註)。"""
    path = Path(path)
    target = target_format(path, s.fmt)
    edited = edit is not None and edit.active
    folder.mkdir(parents=True, exist_ok=True)
    out = free_path(folder, path.stem, SAVE_EXT[target])
    if target == "pdf":
        images_to_pdf([path], out, s, edits=[edit])
        return out, ""

    if target == "svg" and source_format(path) == "svg" and not edited:
        out.write_bytes(path.read_bytes())   # 已經是向量圖,重新描邊只會變差
        return out, "SVG 保持原樣"

    image = open_image(path, s.side)
    frames = getattr(image, "n_frames", 1)
    note = ""
    if frames > 1 and s.split_frames and target not in ANIMATED_FORMATS:
        return _split_frames(path, image, target, folder, s, edit)
    if frames > 1 and target in ANIMATED_FORMATS:
        untouched = (not edited and target == source_format(path)
                     and max(image.size) <= (s.side or max(image.size))
                     and not (s.strip_meta and ("exif" in image.info or "xmp" in image.info)))
        if untouched:
            data = path.read_bytes()
            note = "動畫保持原樣"
        else:
            data = _encode_animation(image, target, s, edit)
    else:
        if frames > 1:
            note = "動畫只保留第一格"
        tables = _jpeg_tables(image, s)
        image, exif = _prepare(image, s, edit)
        data = encode(image, target, s, exif, tables)
    out.write_bytes(data)
    return out, note


def _split_frames(path, image, target, folder, s, edit):
    """動畫逐格拆開:每一格套用編輯後各存成一張圖,放進「檔名_frames」資料夾;回傳 (資料夾, 附註)。"""
    out = free_path(folder, f"{path.stem}_frames", "")
    out.mkdir(parents=True)
    digits = max(3, len(str(image.n_frames)))
    for index, frame in enumerate(ImageSequence.Iterator(image), start=1):
        picture = _fit(apply_edit(frame.convert("RGBA"), edit), s.side)
        picture.info = {}
        name = f"{path.stem}_{index:0{digits}d}{SAVE_EXT[target]}"
        (out / name).write_bytes(encode(picture, target, s))
    return out, f"拆成 {image.n_frames} 張"


def _smallest_side(paths, s, edits):
    """所有圖片編輯、限制尺寸後,長邊最短那一張的長邊;讀不了的圖不算。"""
    sides = []
    for path, edit in zip(paths, edits):
        try:
            if source_format(path) == "svg":
                size = open_image(path, s.side).size
            else:
                with Image.open(path) as image:
                    size = image.size
                    if s.auto_rotate and image.getexif().get(ORIENTATION, 1) in (5, 6, 7, 8):
                        size = size[::-1]
            side = max(edited_size(size, edit))
            sides.append(min(side, s.side) if s.side else side)
        except Exception:
            continue
    return min(sides) if sides else None


def images_to_pdf(paths, out: Path, s: Settings, on_image=None, cancel=None, edits=None):
    """依順序每張圖一頁合成 PDF;讀不了的圖會跳過。回傳 {第幾張: 錯誤訊息}。"""
    import pymupdf

    edits = list(edits) if edits is not None else [None] * len(paths)
    doc = pymupdf.open()
    failed = {}
    smallest = _smallest_side(paths, s, edits) if s.pdf_page == "min" else None
    try:
        for index, path in enumerate(paths):
            if cancel is not None and cancel.is_set():
                raise Cancelled
            if on_image is not None:
                on_image(index)
            try:
                source = open_image(path, s.side)
                tables = _jpeg_tables(source, s)
                image, _ = _prepare(source, s, edits[index])
                if smallest:
                    image = _fit(image, smallest)
                if image.mode == "RGBA" and image.getchannel("A").getextrema()[0] == 255:
                    image = image.convert("RGB")
                if image.mode == "RGBA":
                    # 有透明的圖用無損 PNG 放入,透明處才不會變黑
                    buf = io.BytesIO()
                    image.save(buf, "PNG", optimize=True)
                    data = buf.getvalue()
                else:
                    data = encode(image, "jpg", s, tables=tables)
            except Exception as exc:
                failed[index] = describe_error(exc)
                continue
            width, height = image.size
            if s.pdf_page == "a4":
                page_w, page_h = A4 if height >= width else A4[::-1]
            elif smallest:
                # 長邊一律對齊最小那張,大圖不會比小圖大好幾倍
                scale = smallest / max(width, height) * 72 / PDF_DPI
                page_w, page_h = width * scale, height * scale
            else:
                page_w, page_h = width * 72 / PDF_DPI, height * 72 / PDF_DPI
            page = doc.new_page(width=page_w, height=page_h)
            page.insert_image(page.rect, stream=data, keep_proportion=True)
        if not doc.page_count:
            raise RuntimeError(failed[0] if len(paths) == 1 else "沒有可以放入 PDF 的圖片")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(doc.tobytes(garbage=3, deflate=True))
    finally:
        doc.close()
    return failed
