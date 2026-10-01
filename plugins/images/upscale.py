"""圖片高清:用 Real-ESRGAN(AI 放大,用顯示卡運算)把小圖、模糊的圖放大變清楚;
也有不需要下載的「一般放大」。Real-ESRGAN 第一次使用時才下載(約 43 MB),放在 bin\\realesrgan\\。

授權:Real-ESRGAN 本體與模型是 BSD-3-Clause,Windows 執行檔(ncnn-vulkan 版)是 MIT,都可以自由使用。
「照片」同時跑兩個模型再依「質感」混合:銳利的是 realesrgan-x4plus(乾淨但會抹平細紋,有塑膠感),
自然的是 Philip Hofmann 的 4xNomosWebPhoto_esrgan(保留紋理,但壓縮嚴重的圖會有顆粒、鋸齒;CC BY 4.0)。
自然的模型官方只有 PyTorch/ONNX,
這裡用的是轉成 ncnn 的版本(權重照官方 realesrgan-x4plus 的網路結構排好,舊版執行檔也能讀),
放在 Naiz Studio 自己的「下載元件」Release(tag components),裝在 bin\\realesrgan-models\\(路徑裡一定要有 models,執行檔才肯讀)。
需要支援 Vulkan 的顯示卡(近幾年的 NVIDIA、AMD、Intel 內顯都可以);沒有的話改用一般放大。
"""

import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from PIL import Image, ImageFilter

from core import deps

ESRGAN = deps.Dependency(
    id="realesrgan",
    name="Real-ESRGAN 圖片高清",
    purpose="用 AI 把小圖、模糊的圖放大變清楚，在本地用顯示卡運算，不需要上傳",
    size_text="約 43 MB",
    url="https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
        "realesrgan-ncnn-vulkan-20220424-windows.zip",
    files={"realesrgan/realesrgan-ncnn-vulkan.exe": None},
    folder="realesrgan",
    sha256="abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d",
)

NATURAL = deps.Dependency(
    id="realesrgan-nomos",
    name="照片高清模型（自然）",
    purpose="讓照片變清楚時保留紋理、顏色忠實，比較不會有塑膠感",
    size_text="約 31 MB",
    url="https://github.com/NaizOuO/Naiz-Studio/releases/download/components/nomos-webphoto-x4-ncnn-v1.zip",
    files={"realesrgan-models/realesrgan-x4plus-nomoswebphoto.param": "realesrgan-x4plus-nomoswebphoto.param",
           "realesrgan-models/realesrgan-x4plus-nomoswebphoto.bin": "realesrgan-x4plus-nomoswebphoto.bin"},
    sha256="4b2e9551373c1fff116c10aed7ba0743c42ec076676a1c4f054f1a86c78378d0",
)

# (代號, 名稱, Real-ESRGAN 模型名稱或 None(一般放大), 說明)
MODELS = [
    ("photo", "照片", "realesrgan-x4plus", "適合照片；用「質感」在銳利和自然之間調整"),
    ("anime", "插畫、動漫", "realesrgan-x4plus-anime", "線條銳利、色塊乾淨，適合插畫、貼圖、漫畫"),
    ("fast", "快速", "realesr-animevideov3", "最快，適合大量圖片、截圖；細節少一點"),
    ("plain", "一般放大", None, "不用 AI、不用下載；畫質提升有限"),
]
MODEL_NAMES = {key: name for key, name, _, _ in MODELS}
MODEL_NOTES = {key: note for key, _, _, note in MODELS}
SCALES = [("1", "原尺寸"), ("2", "2 倍"), ("3", "3 倍"), ("4", "4 倍")]     # 原尺寸:只變清楚,大小不變
BIG_SIDE = 8000         # 放大後最長邊超過這個就提醒:檔案會很大、處理很久
# 這兩個模型指定 2、3 倍時輸出是壞的(Real-ESRGAN ncnn 版的問題),一律放大 4 倍再縮小
FOUR_ONLY = {"realesrgan-x4plus", "realesrgan-x4plus-anime", "realesrgan-x4plus-nomoswebphoto"}
EXTRA = {"realesrgan-x4plus-nomoswebphoto": NATURAL}    # 不在 Real-ESRGAN 壓縮檔裡、另外下載的模型
NATURAL_NETWORK = "realesrgan-x4plus-nomoswebphoto"
# 每個模型實際要跑的網路:照片跑銳利、自然兩個,依「質感」混合
NETWORKS = {"photo": ["realesrgan-x4plus", NATURAL_NETWORK]}
TEXTURE = 50            # 質感預設:銳利、自然各半(看起來最平衡:邊緣乾淨又有質感)


class Cancelled(Exception):
    pass


def networks(model):
    """這個模型實際要跑的 Real-ESRGAN 網路;一般放大是空的。"""
    network = dict((key, name) for key, _, name, _ in MODELS).get(model)
    return NETWORKS.get(model, [network] if network else [])


def required(model):
    """這個模型需要的元件(還沒下載的)。"""
    names = networks(model)
    if not names:
        return []
    needed = [ESRGAN] + [EXTRA[name] for name in names if name in EXTRA]
    return [dep for dep in needed if not dep.installed()]


def combine(parts, texture=TEXTURE):
    """照片的兩個結果依質感(0 銳利～100 自然)混合;其他模型只有一個結果,直接回傳。"""
    if len(parts) == 1:
        return parts[0]
    sharp, natural = parts
    texture = max(0, min(100, int(texture)))
    if texture <= 0:
        return sharp
    if texture >= 100:
        return natural
    return Image.blend(sharp, natural.convert(sharp.mode), texture / 100)


def needs_download(model):
    return bool(required(model))


def blend(result, source, strength):
    """強度 0～100:AI 結果和原圖(放大到同樣大小)混合;100 是完全用 AI 的結果。"""
    strength = max(0, min(100, int(strength)))
    if strength >= 100:
        return result
    base = source.resize(result.size, Image.Resampling.LANCZOS)
    if base.mode != result.mode:
        base = base.convert(result.mode)
    return Image.blend(base, result, strength / 100)


def output_size(size, scale):
    return size[0] * scale, size[1] * scale


def _plain(image, scale):
    big = image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS) if scale > 1 else image
    return big.filter(ImageFilter.UnsharpMask(radius=max(0.8, 1.2 * scale / 2), percent=70, threshold=2))


def _bleed(rgb, alpha):
    """透明的地方填上旁邊的顏色:AI 放大時邊緣才不會混進黑色,出現黑邊。"""
    import numpy

    weight = numpy.asarray(alpha, dtype=numpy.float32) / 255
    color = numpy.asarray(rgb, dtype=numpy.float32)
    filled = color.copy()
    for radius in (2, 6, 18, 54):
        blur_w = numpy.asarray(Image.fromarray((weight * 255).astype(numpy.uint8))
                               .filter(ImageFilter.GaussianBlur(radius)), dtype=numpy.float32) / 255
        mixed = numpy.stack([numpy.asarray(Image.fromarray((color[..., c] * weight).astype(numpy.uint8))
                                           .filter(ImageFilter.GaussianBlur(radius)), dtype=numpy.float32)
                             for c in range(3)], -1)
        estimate = mixed / numpy.maximum(blur_w, 1e-3)[..., None]
        empty = (weight < 0.5) & (filled.sum(-1) == color.sum(-1)) & (blur_w > 0.02)
        filled[empty] = estimate[empty]
    filled[weight >= 0.5] = color[weight >= 0.5]
    return Image.fromarray(numpy.clip(filled, 0, 255).astype(numpy.uint8))


def upscale(image, model, scale, progress=None, cancel=None, texture=TEXTURE):
    """放大 scale 倍(1 是原尺寸:AI 放大後再縮回原本大小,細節更清楚);progress(0～1) 回報進度,
    cancel 是 threading.Event,設定後中止。透明的地方保持透明。照片依 texture 混合銳利與自然。"""
    return combine(upscale_parts(image, model, scale, progress, cancel), texture)


def upscale_parts(image, model, scale, progress=None, cancel=None):
    """每個網路各自的結果(照片是 [銳利, 自然]);畫面上存起來,拉「質感」時只要重新混合、不用重跑。"""
    scale = int(scale)
    names = networks(model)
    if not names:
        return [_plain(image, scale)]
    parts = []
    for index, network in enumerate(names):
        step = None
        if progress is not None:
            step = (lambda v, i=index: progress((i + v) / len(names)))
        parts.append(_one(image, network, scale, step, cancel))
    return parts


def _one(image, network, scale, progress, cancel):
    # 透明度另外放大(這個版本處理透明圖有問題):AI 只放大顏色
    alpha = image.getchannel("A") if image.mode in ("RGBA", "LA", "PA") else None
    rgb = image.convert("RGB")
    if alpha is not None:
        rgb = _bleed(rgb, alpha)
    size = (image.width * scale, image.height * scale)
    result = _run(rgb, network, 4 if network in FOUR_ONLY else max(2, scale), progress, cancel)
    if result.size != size:
        result = result.resize(size, Image.Resampling.LANCZOS)
    result = result.convert("RGB")
    if alpha is not None:
        result.putalpha(alpha.resize(size, Image.Resampling.LANCZOS))
    return result


def _run(image, network, scale, progress, cancel):
    exe = ESRGAN.path()
    work = Path(tempfile.mkdtemp(prefix="naiz-upscale-"))
    try:
        source, target = work / "in.png", work / "out.png"
        image.save(source)
        models = EXTRA[network].base_dir / "realesrgan-models" if network in EXTRA else exe.parent / "models"
        args = [str(exe), "-i", str(source), "-o", str(target), "-n", network, "-s", str(scale), "-m", str(models)]
        proc = deps.popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        lines = []

        def watch():
            for raw in proc.stdout:
                text = raw.decode("utf-8", "replace").strip()
                lines.append(text)
                found = re.match(r"([\d.]+)%", text)
                if found and progress is not None:
                    progress(min(1.0, float(found.group(1)) / 100))

        reader = threading.Thread(target=watch, daemon=True)
        reader.start()
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                deps.kill_tree(proc)
                raise Cancelled
            reader.join(0.1)
        reader.join(1)
        if proc.returncode != 0 or not target.exists():
            detail = " ".join(line for line in lines[-3:] if line and not line.endswith("%"))
            if "vk" in detail.lower() or "gpu" in detail.lower() or not detail:
                raise RuntimeError("沒辦法用顯示卡運算(需要支援 Vulkan 的顯示卡)，可以改用「一般放大」")
            raise RuntimeError(detail)
        with Image.open(target) as result:
            result.load()
            return result.copy()
    finally:
        shutil.rmtree(work, ignore_errors=True)


SAVE_TARGETS = {"jpg", "png", "webp", "avif", "heic", "bmp", "tiff"}


def load_edited(path, edit):
    """原圖(完整解析度)套用編輯後的樣子:放大的來源。動畫只取第一格。"""
    from . import ops

    image = ops.open_image(path)
    prepared, _ = ops._prepare(image, ops.Settings(), edit)
    return prepared


def save(image, path, folder, fmt, scale):
    """存放大後的圖:檔名加上「_2x」(原尺寸時是「_高清」);fmt 是「原格式」或指定格式,
    不適合的格式(SVG、GIF 等)改存 PNG。"""
    from core.files import free_path, write_bytes

    from . import ops

    alpha = image.mode in ("RGBA", "LA") and image.getchannel("A").getextrema()[0] < 255
    target = ops.output_format(path, fmt, alpha)        # 去背成透明的 JPG 改存 PNG
    if target not in SAVE_TARGETS:
        target = "png"
    folder.mkdir(parents=True, exist_ok=True)
    suffix = "高清" if int(scale) == 1 else f"{scale}x"
    out = free_path(folder, f"{Path(path).stem}_{suffix}", ops.SAVE_EXT[target])
    write_bytes(out, ops.encode(image, target, ops.Settings(fmt=target)))
    return out
