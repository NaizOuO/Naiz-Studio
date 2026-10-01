"""去背:找出主體,背景變透明或換成白色、其他顏色、模糊的原背景。

兩種方式:
- AI(BiRefNet,MIT 授權):一般用輕量版(約 109 MB),精細用完整版(約 467 MB,頭髮、顏色接近背景時更準)。
  用 onnxruntime 的 DirectML 版在本地執行:NVIDIA、AMD、Intel 顯示卡都能加速,沒有的話用處理器。
  執行元件與模型第一次使用時才下載(onnxruntime 從 PyPI、模型從 Hugging Face,都固定版本並驗證 SHA-256)。
  同時只載入一個模型:兩個一起放在顯示卡上會互搶記憶體,慢好幾倍。
- 單色背景:不用 AI、不用下載。從圖片邊緣往內找和背景同色、又連在一起的地方變透明,
  所以主體裡面的同色(例如眼睛的白色)不會被挖掉;邊緣半透明的地方會把背景色減掉,放到深色背景上不會有白邊。
  每一格動畫都能做,適合白底插畫、證件照、MediBang 等繪圖軟體匯出的圖。

遮罩是「原圖」上的(還沒旋轉、裁切):ops.apply_edit 會讓遮罩跟著圖片一起旋轉、校正、裁切,
之後改編輯也不用重算,修正筆刷也不會跑掉。AI 的遮罩算好後存在記憶體(MASKS),之後縮圖、預覽、存檔都共用。
"""

import sys
import threading
from statistics import NormalDist
from collections import OrderedDict
from typing import NamedTuple

import numpy
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from core import deps

ENGINE = deps.Dependency(
    id="onnxruntime-dml",
    name="AI 去背執行元件（ONNX Runtime）",
    purpose="在本地執行去背 AI，有顯示卡時用顯示卡加速，不需要上傳",
    size_text="約 26 MB",
    url="https://files.pythonhosted.org/packages/b7/04/816932a3ade867a687e406716ca76e0774c6b921545b45818e3ebfcc54ce/"
        "onnxruntime_directml-1.24.4-cp314-cp314-win_amd64.whl",
    files={"onnxruntime-dml/onnxruntime/capi/onnxruntime_pybind11_state.pyd": None},
    folder="onnxruntime-dml",
    sha256="51d86bb949488e572b00422f344990a4a81d982416d73b6c0e4ced2bcd423d19",
    install_size=70 * 1024 * 1024,
)
LITE = deps.Dependency(
    id="birefnet-lite",
    name="去背模型（一般）",
    purpose="找出照片裡的人物、動物、商品，速度快",
    size_text="約 109 MB",
    url="https://huggingface.co/onnx-community/BiRefNet_lite-ONNX/resolve/"
        "de15b22ba131738a16dff04aab8bdf8dc32e3ac1/onnx/model_fp16.onnx",
    files={"cutout/birefnet-lite-fp16.onnx": None},
    location="models",
    sha256="d39b897ceb16ae654c1731f3dba0cf9b368d9cae74b5a57459b455cc8bfec402",
)
FULL = deps.Dependency(
    id="birefnet-full",
    name="去背模型（精細）",
    purpose="頭髮、毛邊，以及和背景顏色接近的主體切得更準，比一般慢",
    size_text="約 467 MB",
    url="https://huggingface.co/onnx-community/BiRefNet-ONNX/resolve/"
        "534d3c82d3bb8b2f0867db6dfbc3a525b8e42f67/onnx/model_fp16.onnx",
    files={"cutout/birefnet-full-fp16.onnx": None},
    location="models",
    sha256="3654c741eb80bd926ada8fed1713b506ccf8d30eb1f6487e87eb9f234f33df09",
)
AI_MODELS = {"lite": LITE, "full": FULL}

# (代號, 名稱, 說明)
METHODS = [
    ("lite", "一般", "適合人物、動物、商品照片，速度快"),
    ("full", "精細", "頭髮、毛邊和顏色接近背景的主體更準，比較慢"),
    ("color", "單色背景", "不用 AI、不用下載；白底插畫、證件照等背景是單一顏色時最快，動畫也能用"),
]
METHOD_NAMES = {key: name for key, name, _ in METHODS}
METHOD_NOTES = {key: note for key, _, note in METHODS}
BACKGROUNDS = [("clear", "透明"), ("white", "白色"), ("color", "其他顏色"), ("blur", "模糊原背景")]
SWATCHES = [(0, 0, 0), (40, 44, 52), (239, 239, 239), (255, 228, 225), (255, 240, 200),
            (220, 237, 255), (216, 240, 222), (34, 102, 204), (200, 40, 50), (0, 177, 64)]
SIDE = 1024             # 模型的輸入大小
KEEP_SIDE = 2048        # 記憶體裡保留的遮罩最長邊(夠存檔用,也不會太佔記憶體)
MAX_MASKS = 80
TOLERANCE = 32          # 單色背景:和背景色差多少以內算背景(0～255)
MEAN = numpy.array([0.485, 0.456, 0.406], numpy.float32)
STD = numpy.array([0.229, 0.224, 0.225], numpy.float32)


class Cutout(NamedTuple):
    """一張圖的去背設定(放在 ops.Edit.cutout)。
    method:lite/full(AI)或 color(單色背景);key:AI 遮罩在 MASKS 裡的名字。
    strokes:修正筆刷,每筆是 (保留?, 半徑, ((x, y), ...)),都是占原圖最長邊 / 寬高的比例。
    shrink、feather:邊緣往內縮、柔和(0～10);background、color:背景;trim:裁到主體;tolerance:單色背景的容許差異。"""
    method: str = "lite"
    key: str = None
    strokes: tuple = ()
    shrink: int = 0
    feather: int = 0
    background: str = "clear"
    color: tuple = (255, 255, 255)
    trim: bool = False
    tolerance: int = TOLERANCE

    @property
    def ai(self):
        return self.method in AI_MODELS

    @property
    def transparent(self):
        return self.background == "clear"


class Missing(Exception):
    """AI 的遮罩還沒算好(畫面上先顯示原圖,背景算好再更新)。"""


def required(method):
    """這個方式還沒下載的元件。"""
    if method not in AI_MODELS:
        return []
    return [dep for dep in (ENGINE, AI_MODELS[method]) if not dep.installed()]


def mask_key(path, method):
    """AI 遮罩的名字:檔案改過(修改時間、大小變了)就要重算。"""
    try:
        stat = path.stat()
        stamp = f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        stamp = ""
    return f"{method}|{path}|{stamp}"


# ------------------------------------------------------------ AI

MASKS = OrderedDict()
_masks_lock = threading.Lock()
_run_lock = threading.Lock()        # 一次只跑一張(顯示卡記憶體)
_session = None                     # (方式, InferenceSession, 實際用的運算方式)


def stored(key):
    with _masks_lock:
        mask = MASKS.get(key)
        if mask is not None:
            MASKS.move_to_end(key)
        return mask


_generation = 0


def generation():
    """遮罩算好一次就加一:畫面的快取靠它知道要重畫。"""
    return _generation


def remember(key, mask):
    if max(mask.size) > KEEP_SIDE:
        mask = mask.copy()
        mask.thumbnail((KEEP_SIDE, KEEP_SIDE), Image.Resampling.LANCZOS)
    with _masks_lock:
        MASKS[key] = mask
        MASKS.move_to_end(key)
        while len(MASKS) > MAX_MASKS:
            MASKS.popitem(last=False)
    global _generation
    _generation += 1


def _runtime():
    """載入下載好的 onnxruntime(放在 bin\\onnxruntime-dml\\,不在 exe 裡)。"""
    folder = str(ENGINE.base_dir / ENGINE.folder)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    import onnxruntime

    onnxruntime.set_default_logger_severity(3)
    return onnxruntime


def _load(method):
    """換模型時先放掉舊的,同時只留一個在顯示卡上。顯示卡用不了時改用處理器。"""
    global _session
    if _session is not None and _session[0] == method:
        return _session
    _session = None
    ort = _runtime()
    path = str(AI_MODELS[method].path())
    try:
        session = ort.InferenceSession(path, providers=["DmlExecutionProvider", "CPUExecutionProvider"])
    except Exception:
        session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    _session = (method, session, session.get_providers()[0])
    return _session


def device():
    """目前用顯示卡還是處理器(載入過模型才知道)。"""
    if _session is None:
        return ""
    return "顯示卡" if _session[2] == "DmlExecutionProvider" else "處理器"


def release():
    """離開工具時放掉模型,讓出顯示卡記憶體。"""
    global _session
    with _run_lock:
        _session = None


def predict(image, method):
    """AI 算出主體的遮罩(L,和 image 一樣大;255 是主體)。"""
    rgb = image.convert("RGB")
    small = numpy.asarray(rgb.resize((SIDE, SIDE), Image.Resampling.BILINEAR), numpy.float32) / 255
    tensor = ((small - MEAN) / STD).transpose(2, 0, 1)[None].astype(numpy.float32)
    with _run_lock:
        _, session, _ = _load(method)
        raw = session.run(None, {session.get_inputs()[0].name: tensor})[0][0, 0]
    if raw.min() < 0 or raw.max() > 1:         # 模型輸出的是還沒換算的分數
        raw = 1 / (1 + numpy.exp(-numpy.clip(raw, -30, 30)))
    mask = Image.fromarray((numpy.clip(raw, 0, 1) * 255 + 0.5).astype(numpy.uint8), "L")
    return mask.resize(image.size, Image.Resampling.LANCZOS)


def ensure(image, cut):
    """AI 遮罩:記憶體裡有就用,沒有就現在算(只在背景執行緒呼叫;畫面那邊遇到沒算好的會拿到 Missing)。"""
    mask = stored(cut.key)
    if mask is None:
        if threading.current_thread() is threading.main_thread():
            raise Missing()
        if required(cut.method):
            raise RuntimeError("去背的元件還沒下載")
        mask = predict(image, cut.method)
        remember(cut.key, mask)
    return mask


# ------------------------------------------------------------ 單色背景

def _border(values):
    return numpy.concatenate([values[0], values[-1], values[:, 0], values[:, -1]])


def color_mask(image, tolerance=TOLERANCE):
    """從邊緣往內找和背景色(邊緣最常見的顏色)相近、又連在一起的地方;回傳 (遮罩, 背景色)。
    原本就透明的地方也算背景。"""
    import pygame

    rgba = numpy.asarray(image.convert("RGBA"), numpy.int16)
    rgb, alpha = rgba[..., :3], rgba[..., 3]
    edge = _border(rgb)[_border(alpha) > 0]
    background = numpy.median(edge, axis=0) if len(edge) else numpy.array([255, 255, 255])
    distance = numpy.abs(rgb - background).max(axis=2)
    near = (distance <= tolerance) | (alpha == 0)
    height, width = near.shape
    # 用 pygame 的遮罩找「連到邊緣」的區塊(C 寫的,很大的圖也很快)
    surface = pygame.Surface((width, height), pygame.SRCALPHA, 32)
    pixels = pygame.surfarray.pixels_alpha(surface)
    pixels[...] = near.T * 255
    del pixels
    candidates = pygame.mask.from_surface(surface, 127)
    filled = pygame.mask.Mask((width, height))
    seeds = [(x, y) for y in (0, height - 1) for x in range(width)] + \
            [(x, y) for x in (0, width - 1) for y in range(height)]
    for seed in seeds:
        if candidates.get_at(seed) and not filled.get_at(seed):
            filled.draw(candidates.connected_component(seed), (0, 0))
    out = pygame.Surface((width, height), pygame.SRCALPHA, 32)
    filled.to_surface(out, setcolor=(0, 0, 0, 255), unsetcolor=(0, 0, 0, 0))
    region = pygame.surfarray.array_alpha(out).T > 0
    # 背景外圍一兩個像素是反鋸齒的邊:依和背景色差多少給半透明
    soft = numpy.asarray(Image.fromarray(region.astype(numpy.uint8) * 255).filter(ImageFilter.MaxFilter(5))) > 0
    level = numpy.clip((distance - tolerance) / max(1, 2 * tolerance), 0, 1) * 255
    mask = numpy.where(region, 0, numpy.where(soft, level, 255)).astype(numpy.uint8)
    return Image.fromarray(mask, "L"), tuple(int(v) for v in background)


def unblend(image, mask, background):
    """半透明的邊把混進去的背景色減掉(白底插畫放到深色背景上才不會有一圈白邊)。"""
    rgba = image.convert("RGBA")
    values = numpy.asarray(rgba, numpy.float32)
    a = numpy.asarray(mask, numpy.float32)[..., None] / 255
    edge = (a > 0) & (a < 1)
    if not edge.any():
        return rgba
    bg = numpy.array(background, numpy.float32)
    fixed = numpy.clip((values[..., :3] - (1 - a) * bg) / numpy.maximum(a, 1 / 255), 0, 255)
    values[..., :3] = numpy.where(edge, fixed, values[..., :3])
    return Image.fromarray(values.astype(numpy.uint8), "RGBA")


def clear_background(image, tolerance=TOLERANCE):
    """圖片轉檔的「背景變透明」:單色背景變透明,回傳 RGBA。"""
    mask, background = color_mask(image, tolerance)
    result = unblend(image, mask, background)
    result.putalpha(ImageChops.multiply(result.getchannel("A"), mask))
    return result


# ------------------------------------------------------------ 套用(給 ops.apply_edit 用)

def _draw_strokes(mask, strokes):
    if not strokes:
        return mask
    mask = mask.copy()
    draw = ImageDraw.Draw(mask)
    width, height = mask.size
    long_side = max(width, height)
    for keep, radius, points in strokes:
        value = 255 if keep else 0
        r = max(1.0, radius * long_side)
        xy = [(x * width, y * height) for x, y in points]
        if len(xy) > 1:
            draw.line(xy, fill=value, width=max(1, round(r * 2)), joint="curve")
        for x, y in xy:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=value)
    return mask


def _edges(mask, shrink, feather):
    """往內縮、柔和(0～10,可以有小數)。數值以最長邊 1000 像素為準。
    往內縮:先高斯模糊,再把邊界移到模糊後「往內 d 像素」的亮度,d 可以是小數,每 0.1 都有差。"""
    scale = max(mask.size) / 1000
    if shrink:
        distance = shrink * scale
        sigma = max(1.0, distance)
        z = distance / sigma
        level = 255 * NormalDist().cdf(z)                              # 新的邊界在模糊後的這個亮度
        slope = 255 * NormalDist().pdf(z) / sigma                      # 那裡每個像素亮度變化多少
        gain = 255 / max(slope, 1e-3)                                  # 新的邊緣大約 1 像素寬,小數的位移變成半透明
        blurred = mask.filter(ImageFilter.GaussianBlur(sigma))
        mask = blurred.point(lambda v: max(0, min(255, round(127.5 + (v - level) * gain))))
    if feather:
        mask = mask.filter(ImageFilter.GaussianBlur(feather * scale * 0.8))
    return mask


def source_mask(image, cut):
    """原圖(還沒旋轉、裁切)上的遮罩與調整過的原圖:回傳 (圖, 遮罩)。AI 遮罩還沒算好時丟 Missing。"""
    if cut.method == "color":
        mask, background = color_mask(image, cut.tolerance)
        image = unblend(image, mask, background)
    else:
        mask = ensure(image, cut)
        if mask.size != image.size:
            mask = mask.resize(image.size, Image.Resampling.LANCZOS)
    mask = _draw_strokes(mask, cut.strokes)
    return image, _edges(mask, cut.shrink, cut.feather)


def _blurred_background(image, mask):
    """模糊的原背景:只用背景的顏色去模糊(主體的位置由周圍的背景補上),主體周圍才不會有一圈暗影。
    先縮小再模糊,很大的圖也快。"""
    scale = max(1, max(image.size) // 400)
    size = (max(1, image.width // scale), max(1, image.height // scale))
    small = numpy.asarray(image.convert("RGB").resize(size, Image.Resampling.BILINEAR), numpy.float32)
    weight = 1 - numpy.asarray(mask.resize(size, Image.Resampling.BILINEAR), numpy.float32)[..., None] / 255
    radius = max(2, round(max(size) / 60))

    def blur(values):
        # 連做三次平均模糊,很接近高斯模糊;邊緣用最外圈的值延伸
        for _ in range(3):
            for axis in (0, 1):
                pad = [(0, 0)] * values.ndim
                pad[axis] = (radius + 1, radius)
                summed = numpy.cumsum(numpy.pad(values, pad, mode="edge"), axis=axis)
                upper = numpy.take(summed, range(2 * radius + 1, summed.shape[axis]), axis=axis)
                lower = numpy.take(summed, range(0, summed.shape[axis] - 2 * radius - 1), axis=axis)
                values = (upper - lower) / (2 * radius + 1)
        return values

    total = blur(small * weight)
    amount = blur(weight)
    plain = blur(small)
    mixed = numpy.where(amount > 0.02, total / numpy.maximum(amount, 1e-6), plain)
    background = Image.fromarray(numpy.clip(mixed, 0, 255).astype(numpy.uint8), "RGB").resize(
        image.size, Image.Resampling.BICUBIC).convert("RGBA")
    background.putalpha(image.getchannel("A"))
    return background


def compose(image, mask, cut):
    """套用遮罩並換背景(在色彩、效果之後)。背景 ghost 是修正筆刷時畫面用的:被去掉的地方淡淡顯示原圖。"""
    image = image.convert("RGBA")
    if mask.size != image.size:
        mask = mask.resize(image.size, Image.Resampling.BILINEAR)
    subject = image.copy()
    subject.putalpha(ImageChops.multiply(image.getchannel("A"), mask))
    if cut.background == "ghost":
        faded = image.copy()
        faded.putalpha(image.getchannel("A").point(lambda v: v * 35 // 100))
        result = Image.alpha_composite(Image.alpha_composite(faded, Image.new("RGBA", image.size, (200, 40, 120, 70))),
                                       subject)
    elif cut.background == "clear":
        result = subject
    elif cut.background == "blur":
        result = Image.alpha_composite(_blurred_background(image, mask), subject)
    else:
        color = (255, 255, 255) if cut.background == "white" else tuple(cut.color)
        result = Image.alpha_composite(Image.new("RGBA", image.size, color + (255,)), subject)
    if cut.trim and cut.background != "ghost":
        box = mask.point(lambda v: 255 if v > 24 else 0).getbbox()
        if box:
            pad = round(max(image.size) * 0.02)
            result = result.crop((max(0, box[0] - pad), max(0, box[1] - pad),
                                  min(image.width, box[2] + pad), min(image.height, box[3] + pad)))
    return result
