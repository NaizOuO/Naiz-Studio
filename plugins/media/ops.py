"""影音工具的運算:讀取影音資訊與縮圖、偵測顯示卡、組出 FFmpeg 參數並執行(含進度與取消)。全部在本機處理。"""

import atexit
import io
import json
import math
import shutil
import subprocess
import tempfile
import threading
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path

from core import deps
from core.files import atomic_path, free_path

from . import formats as F

THUMB_SEEK = 3          # 縮圖取第幾秒的畫面(影片太短時取 10% 的位置)
SIZE_OVERHEAD = 0.96    # 目標大小要扣掉封裝格式本身佔的空間
MIN_VIDEO_KBPS = 50
RETRY_MARGIN = 0.93
RETRIES = 3
HARDWARE_RETRIES = 1    # 顯示卡重轉一次還超過,多半是壓不到這麼小,直接改用處理器


class Cancelled(Exception):
    pass


# 正在執行的 FFmpeg;關閉 Naiz Studio 時一併結束,避免在背景繼續轉檔
_active = set()
atexit.register(lambda: [deps.kill_tree(proc) for proc in list(_active)])


def ffmpeg():
    return deps.FFMPEG.path("ffmpeg.exe")


def ffprobe():
    return deps.FFMPEG.path("ffprobe.exe")


def human_time(seconds):
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def parse_time(text):
    """「90」「1:30」「1:02:03」「1:30.5」轉成秒數;空白回傳 None,看不懂時丟 ValueError。"""
    text = unicodedata.normalize("NFKC", text or "").strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        if len(parts) > 3 or any(not p.strip() for p in parts):
            raise ValueError
        numbers = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"看不懂「{text}」，請用 分:秒 或 時:分:秒") from None
    if any(n < 0 for n in numbers) or any(n >= 60 for n in numbers[1:]):
        raise ValueError(f"看不懂「{text}」，分和秒要小於 60")
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


# ------------------------------------------------------------ 讀取


def _ratio(text):
    try:
        top, bottom = (text or "0/0").split("/")
        return float(top) / float(bottom) if float(bottom) else 0.0
    except ValueError:
        return 0.0


def probe(path):
    """用 ffprobe 讀出長度、解析度、編碼等資訊。"""
    path = Path(path)
    result = deps.run([ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                      capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError("無法讀取，檔案可能損壞或不是影音檔")
    data = json.loads(result.stdout.decode("utf-8", "replace") or "{}")
    streams = data.get("streams", [])
    container = data.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise RuntimeError("這個檔案裡沒有影像也沒有聲音")
    duration = float(container.get("duration") or (video or audio).get("duration") or 0)
    info = {"size": path.stat().st_size, "duration": duration, "container": container.get("format_name", ""),
            "bit_rate": int(container.get("bit_rate") or 0)}
    if video is not None:
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        rotation = 0
        for side in video.get("side_data_list", []):
            rotation = int(side.get("rotation", rotation) or 0)
        rotation = rotation or int(video.get("tags", {}).get("rotate", 0) or 0)
        if abs(rotation) % 180 == 90:
            width, height = height, width   # 手機直拍的影片:轉檔時 FFmpeg 會自動轉正,顯示轉正後的尺寸
        fps = _ratio(video.get("avg_frame_rate")) or _ratio(video.get("r_frame_rate"))
        info.update(video_codec=video.get("codec_name", ""), width=width, height=height, fps=fps,
                    frames=int(video.get("nb_frames") or 0) or int(round(duration * fps)))
        if not duration and info["frames"] and fps:
            info["duration"] = info["frames"] / fps
    if audio is not None:
        info.update(audio_codec=audio.get("codec_name", ""), sample_rate=int(audio.get("sample_rate") or 0),
                    channels=int(audio.get("channels") or 0), audio_bit_rate=int(audio.get("bit_rate") or 0))
    return info


def thumbnail(path, info, box):
    """影片的一格畫面縮成 box 大小,回傳 (尺寸, RGBA 位元組);沒有影像時回傳 None。"""
    if not info.get("width"):
        return None
    from PIL import Image

    seek = min(THUMB_SEEK, info.get("duration", 0) * 0.1)
    for start in ([seek, 0] if seek else [0]):
        result = deps.run([ffmpeg(), "-nostdin", "-v", "error", "-ss", f"{start:.2f}", "-i", path, "-frames:v", "1",
                           "-vf", f"scale={box[0]}:{box[1]}:force_original_aspect_ratio=decrease",
                           "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True, timeout=30)
        if result.returncode == 0 and result.stdout:
            image = Image.open(io.BytesIO(result.stdout)).convert("RGBA")
            return image.size, image.tobytes()
    return None


# ------------------------------------------------------------ 顯示卡

VENDORS = ("nvidia", "amd", "intel")
VENDOR_LABELS = {"nvidia": "NVIDIA", "amd": "AMD", "intel": "Intel"}


def detect_hardware():
    """每種編碼實際用顯示卡轉一小段測試,轉得出來才算支援。回傳 {編碼: (廠牌, Encoder)}。"""
    found, broken = {}, set()
    for key, codec in F.CODECS.items():
        for vendor in VENDORS:
            encoder = codec.hardware.get(vendor)
            if encoder is None or vendor in broken:
                continue
            args = [ffmpeg(), "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=black:size=640x360:rate=30:d=0.2",
                    "-c:v", encoder.name, *encoder.quality_args(50), *encoder.speeds["normal"], "-f", "null", "-"]
            try:
                ok = deps.run(args, capture_output=True, timeout=30).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            if ok:
                found[key] = (vendor, encoder)
                break
            if key == "h264":
                broken.add(vendor)   # 連 H.264 都轉不了,這個廠牌的其他編碼就不用再試
    return found


# ------------------------------------------------------------ 設定


@dataclass
class Advanced:
    """進階設定;影片和音訊分頁各有一份。"""

    codec: str = ""             # 空字串:用格式預設(原格式時沿用原檔的編碼)
    resolution: str = "source"
    width: int = 1920
    height: int = 1080
    fps: str = "source"
    rate_mode: str = "quality"
    quality: float = None       # None:跟著畫質等級;有數值時是自訂品質(0~100)
    bitrate: int = 5000         # kbps,平均或固定位元速率用
    speed: str = "normal"
    start: float = None
    end: float = None
    audio_remove: bool = False
    audio_codec: str = ""
    audio_bitrate: int = None   # None:跟著畫質等級
    sample_rate: str = "source"
    channels: str = "source"

    def copy(self):
        return replace(self)

    @property
    def custom_video(self):
        return self.quality is not None or self.rate_mode != "quality"


@dataclass
class VideoSettings:
    fmt: str = "keep"
    mode: str = "level"         # level 畫質等級 / size 目標大小
    level: str = "balance"
    target_mb: float = 25.0
    device: str = "auto"        # auto / gpu / cpu
    gif_fps: int = 12
    gif_width: int = 480
    gif_dither: str = "sierra2_4a"
    gif_loop: bool = True
    adv: Advanced = field(default_factory=Advanced)


@dataclass
class AudioSettings:
    fmt: str = "mp3"
    mode: str = "level"
    level: str = "balance"
    target_mb: float = 5.0
    depth: int = 16
    adv: Advanced = field(default_factory=Advanced)


@dataclass
class VideoPlan:
    """一個檔案實際會怎麼轉。"""

    fmt: F.VideoFormat
    codec: str = ""
    encoder: F.Encoder = None
    audio: str = ""
    start: float = 0.0
    length: float = 0.0


def time_range(info, adv):
    """回傳 (開始秒數, 長度);讀不到長度又沒指定結束時間時,長度是 0(進度會顯示不出百分比)。"""
    duration = info.get("duration", 0)
    start = adv.start or 0.0
    end = adv.end if adv.end is not None else duration
    if duration:
        end = min(end, duration)
    elif adv.end is None:
        return start, 0.0
    if end <= start:
        raise ValueError("時間範圍超出影片長度，或結束時間早於開始時間")
    return start, end - start


def plan_video(path, info, s, hardware):
    fmt_key = F.source_video_format(path, info) if s.fmt == "keep" else s.fmt
    fmt = F.VIDEO_FORMAT[fmt_key]
    if not info.get("width"):
        raise RuntimeError("這個檔案沒有影像，請改用音訊分頁")
    start, length = time_range(info, s.adv)
    plan = VideoPlan(fmt, start=start, length=length)
    if fmt_key == "gif":
        return plan
    source_codec = F.PROBE_CODECS.get(info.get("video_codec", ""), "")
    wanted = s.adv.codec or (source_codec if s.fmt == "keep" else "")
    plan.codec = wanted if wanted in fmt.codecs else fmt.codecs[0]
    codec = F.CODECS[plan.codec]
    plan.encoder = hardware[plan.codec][1] if s.device != "cpu" and plan.codec in hardware else codec.cpu
    if fmt.audio and info.get("audio_codec") and not s.adv.audio_remove:
        source_audio = F.PROBE_AUDIO.get(info.get("audio_codec", ""), "")
        wanted = s.adv.audio_codec or (source_audio if s.fmt == "keep" else "")
        plan.audio = wanted if wanted in fmt.audio else fmt.audio[0]
    return plan


def audio_kbps(codec_key, s, target_total_kbps=None):
    """音訊位元速率;無損格式回傳 None。"""
    codec = F.AUDIO_CODECS[codec_key]
    if codec.lossless:
        return None
    if s.adv.audio_bitrate:
        return F.nearest(codec.bitrates, s.adv.audio_bitrate)
    kbps = codec.levels[s.level if s.mode == "level" else "balance"]
    if target_total_kbps is not None:
        # 目標大小很小時,聲音也跟著降,不讓聲音吃掉大部分的空間
        kbps = min(kbps, max(codec.levels["small"], target_total_kbps * 0.15))
    return max((b for b in codec.bitrates if b <= kbps), default=codec.bitrates[0])


def _audio_args(codec_key, kbps, s, info, fmt_rates=(), depth=16):
    codec = F.AUDIO_CODECS[codec_key]
    args = ["-c:a", codec.encoder_name(depth)]
    if kbps:
        args += ["-b:a", f"{kbps}k"]
    source_rate = info.get("sample_rate") or 48000
    rate = int(s.adv.sample_rate) if s.adv.sample_rate != "source" else source_rate
    allowed = codec.sample_rates or fmt_rates
    if allowed and rate not in allowed:
        rate = F.nearest(allowed, rate)
    if rate != source_rate or s.adv.sample_rate != "source":
        args += ["-ar", str(rate)]
    channels = info.get("channels") or 2
    wanted = int(s.adv.channels) if s.adv.channels != "source" else min(channels, codec.max_channels)
    if wanted != channels:
        args += ["-ac", str(wanted)]
    if codec_key == "flac":
        args += ["-sample_fmt", "s32" if depth == 24 else "s16"] + (["-bits_per_raw_sample", "24"] if depth == 24 else [])
    elif codec_key == "alac":
        args += ["-sample_fmt", "s32p" if depth == 24 else "s16p"] + (["-bits_per_raw_sample", "24"] if depth == 24 else [])
    return args


def _scale_filter(info, adv):
    if adv.resolution == "source":
        return None
    width, height = info["width"], info["height"]
    if adv.resolution == "custom":
        return f"scale={max(2, adv.width // 2 * 2)}:{max(2, adv.height // 2 * 2)},setsar=1"
    side = int(adv.resolution)
    if min(width, height) <= side:
        return None     # 只縮小不放大
    # 橫的影片限制高度、直的影片限制寬度(例如直拍的 1080p 是 1080×1920)
    return f"scale=-2:{side}" if width >= height else f"scale={side}:-2"


def _fps_filter(adv, info):
    if adv.fps == "source" or float(adv.fps) >= (info.get("fps") or 999) - 0.01:
        return None     # 只降低不提高,補出來的格子只是重複的畫面
    return "fps=30000/1001" if adv.fps == "29.97" else f"fps={adv.fps}"


def target_kbps(target_mb, length):
    return target_mb * 1024 * 1024 * 8 / 1000 / max(0.1, length) * SIZE_OVERHEAD


def _video_rate_args(plan, s, video_kbps):
    encoder, adv = plan.encoder, s.adv
    if video_kbps is not None:
        kbps = max(MIN_VIDEO_KBPS, int(video_kbps))
        if encoder.hardware:
            # 顯示卡沒辦法分析兩次,上限壓在平均值,檔案大小才不會超出目標太多
            return ["-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps}k"]
        return ["-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.5)}k", "-bufsize", f"{kbps * 2}k"]
    if adv.rate_mode in ("vbr", "cbr") and F.CODECS[plan.codec].bitrate:
        kbps = int(adv.bitrate)
        if adv.rate_mode == "cbr":
            args = ["-b:v", f"{kbps}k", "-minrate", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps}k"]
            return (["-rc", "cbr"] if encoder.hardware and "nvenc" in encoder.name else []) + args
        return ["-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.5)}k", "-bufsize", f"{kbps * 2}k"]
    position = adv.quality if adv.quality is not None else encoder.level_position(s.level)
    return encoder.quality_args(position)


def planned_video_kbps(plan, s, info):
    """目標大小或 DVD 時要用的影片位元速率;依品質數值轉檔時回傳 None。"""
    audio = audio_kbps(plan.audio, s) if plan.audio else 0
    if s.mode == "size" and (plan.fmt.target or F.CODECS[plan.codec].bitrate):
        if not plan.length:
            raise ValueError("讀不到這個檔案的長度，沒辦法換算目標大小，請改用畫質等級")
        total = target_kbps(s.target_mb, plan.length)
        audio = audio_kbps(plan.audio, s, total) if plan.audio else 0
        video = total - (audio or 0)
        if video < MIN_VIDEO_KBPS:
            need = (MIN_VIDEO_KBPS + (audio or 0)) * plan.length * 1000 / 8 / 1024 / 1024 / SIZE_OVERHEAD
            raise ValueError(f"目標太小：這段 {human_time(plan.length)} 的影片至少需要約 {math.ceil(need)} MB")
        return min(video, 9000) if plan.fmt.target else video
    if plan.fmt.target:
        if s.adv.rate_mode in ("vbr", "cbr"):
            return min(s.adv.bitrate, 9000)
        return F.DVD_BITRATES[s.level]
    return None


def video_args(path, info, plan, s, output, video_kbps=None, pass_number=0):
    """組出一次 FFmpeg 的參數;pass_number 1、2 是分析兩次時的第一、第二次。"""
    args = [ffmpeg(), "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats"]
    if plan.start:
        args += ["-ss", f"{plan.start:.3f}"]
    args += ["-i", str(path)]
    if s.adv.start is not None or s.adv.end is not None:
        args += ["-t", f"{plan.length:.3f}"]
    args += ["-map", "0:v:0", "-sn", "-dn"]
    fmt = plan.fmt

    if fmt.key == "gif":
        dither = s.gif_dither
        chain = (f"fps={s.gif_fps},scale='min({s.gif_width},iw)':-2:flags=lanczos,split[a][b];"
                 f"[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither={dither}")
        return args + ["-an", "-vf", chain, "-loop", "0" if s.gif_loop else "-1", *fmt.muxer, str(output)]

    if fmt.target:
        wide = info["width"] / max(1, info["height"]) > 1.5
        args += ["-target", fmt.target, "-aspect", "16:9" if wide else "4:3"]
    filters = [f for f in (None if fmt.target else _scale_filter(info, s.adv), _fps_filter(s.adv, info)) if f]
    if not fmt.target:
        filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")    # 大部分編碼器需要偶數的寬高
    encoder = plan.encoder
    if filters:
        args += ["-vf", ",".join(filters)]
    args += ["-c:v", encoder.name, *encoder.extra, *encoder.speeds.get(s.adv.speed, ())]
    if plan.codec not in ("prores", "mjpeg"):
        args += ["-pix_fmt", "yuv420p"]
    args += _video_rate_args(plan, s, video_kbps)

    if pass_number:
        if encoder.two_pass == "x265":
            args += ["-x265-params", f"pass={pass_number}:stats=x265.log"]
        else:
            args += ["-pass", str(pass_number), "-passlogfile", "pass"]
    if pass_number == 1:
        return args + ["-an", "-f", "null", "-"]

    if plan.audio:
        args += ["-map", "0:a:0"]
        kbps = audio_kbps(plan.audio, s, target_kbps(s.target_mb, plan.length) if s.mode == "size" else None)
        args += _audio_args(plan.audio, kbps, s, info, fmt.sample_rates)
    else:
        args += ["-an"]
    return args + [*fmt.muxer, str(output)]


def audio_plan(path, info, s):
    if not info.get("audio_codec"):
        raise RuntimeError("這個檔案裡沒有聲音")
    fmt = F.AUDIO_FORMAT[F.source_audio_format(path, info) if s.fmt == "keep" else s.fmt]
    start, length = time_range(info, s.adv)
    codec = F.AUDIO_CODECS[fmt.codec]
    kbps = None
    if not codec.lossless:
        if s.mode == "size":
            total = target_kbps(s.target_mb, length)
            kbps = max((b for b in codec.bitrates if b <= total), default=None)
            if kbps is None:
                need = codec.bitrates[0] * length * 1000 / 8 / 1024 / 1024 / SIZE_OVERHEAD
                raise ValueError(f"目標太小：這段 {human_time(length)} 的聲音至少需要約 {math.ceil(need * 10) / 10} MB")
        else:
            kbps = audio_kbps(fmt.codec, s)
    return fmt, start, length, kbps


def audio_args(path, info, s, output):
    fmt, start, length, kbps = audio_plan(path, info, s)
    args = [ffmpeg(), "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(path)]
    if s.adv.start is not None or s.adv.end is not None:
        args += ["-t", f"{length:.3f}"]
    depth = s.depth if fmt.depths else 16
    args += ["-map", "0:a:0", "-vn", "-sn", "-dn", *_audio_args(fmt.codec, kbps, s, info, depth=depth)]
    return args + [*fmt.muxer, str(output)]


# ------------------------------------------------------------ 預估大小


# 沒有實測的編碼器:以處理器 H.264 實測的每像素每格位元數,乘上相對於 H.264 的大小比例來預估
BITS_PER_PIXEL = {"small": 0.036, "balance": 0.060, "high": 0.102}
BITS_EXPONENT = -0.24
REFERENCE_PIXELS = 1920 * 1080
ESTIMATE_RANGE = (0.6, 1.6)     # 同樣設定下,畫面內容不同大小也會差很多,所以預估給一個範圍
CODEC_EFFICIENCY = {"h264": 1.0, "hevc": 0.7, "av1": 0.6, "vp9": 0.75, "vp8": 1.2, "mpeg4": 1.6, "mjpeg": 6.0,
                    "prores": 12.0, "mpeg1": 2.5, "mpeg2": 2.0, "wmv2": 2.0, "flv1": 2.2, "theora": 1.5}


def _output_pixels(info, s):
    width, height = info.get("width", 0), info.get("height", 0)
    adv = s.adv
    if adv.resolution == "custom":
        width, height = adv.width, adv.height
    elif adv.resolution != "source":
        side = int(adv.resolution)
        if min(width, height) > side:
            scale = side / min(width, height)
            width, height = width * scale, height * scale
    fps = info.get("fps") or 30
    if adv.fps != "source":
        fps = min(fps, float(adv.fps))
    return width * height, fps


def estimate_video(path, info, s, hardware):
    """預估輸出大小 (最小, 最大) 位元組;無法預估時回傳 None。"""
    try:
        plan = plan_video(path, info, s, hardware)
    except (RuntimeError, ValueError):
        return None
    length = plan.length
    if plan.fmt.key == "gif":
        width = min(s.gif_width, info["width"])
        height = info["height"] * width / max(1, info["width"])
        frames = length * min(s.gif_fps, info.get("fps") or s.gif_fps)
        middle = width * height * frames * 0.12
        return middle * 0.4, middle * 2.5
    audio = audio_kbps(plan.audio, s) if plan.audio else 0
    try:
        video = planned_video_kbps(plan, s, info)
    except ValueError:
        return None
    if s.mode == "size" and video is not None:
        return s.target_mb * 1024 * 1024 * 0.85, s.target_mb * 1024 * 1024
    audio_bytes = (audio or 1411) * 1000 / 8 * length if plan.audio else 0
    if video is None and s.adv.rate_mode in ("vbr", "cbr") and F.CODECS[plan.codec].bitrate:
        video = s.adv.bitrate
    if video is not None:
        middle = video * 1000 / 8 * length + audio_bytes
        return middle * 0.9, middle * 1.1
    pixels, fps = _output_pixels(info, s)
    encoder = plan.encoder
    position = s.adv.quality if s.adv.quality is not None else encoder.level_position(s.level)
    # 在三個等級之間用對數內插,自訂品質也能估
    efficiency = 1.0 if encoder.bpp else CODEC_EFFICIENCY.get(plan.codec, 1.0)
    table = encoder.bpp or BITS_PER_PIXEL
    anchors = sorted((encoder.level_position(level), math.log(table[level] * efficiency)) for level in table)
    if position <= anchors[0][0]:
        slope = (anchors[1][1] - anchors[0][1]) / max(1e-6, anchors[1][0] - anchors[0][0])
        log_bpp = anchors[0][1] + (position - anchors[0][0]) * slope
    elif position >= anchors[-1][0]:
        slope = (anchors[-1][1] - anchors[-2][1]) / max(1e-6, anchors[-1][0] - anchors[-2][0])
        log_bpp = anchors[-1][1] + (position - anchors[-1][0]) * slope
    else:
        for (p1, v1), (p2, v2) in zip(anchors, anchors[1:]):
            if p1 <= position <= p2:
                log_bpp = v1 + (position - p1) / max(1e-6, p2 - p1) * (v2 - v1)
                break
    exponent = encoder.bpp_exponent if encoder.bpp else BITS_EXPONENT
    bpp = math.exp(log_bpp) * (max(1, pixels) / REFERENCE_PIXELS) ** exponent
    video_bytes = pixels * fps * length * bpp / 8
    return video_bytes * ESTIMATE_RANGE[0] + audio_bytes, video_bytes * ESTIMATE_RANGE[1] + audio_bytes


def estimate_audio(path, info, s):
    try:
        fmt, _, length, kbps = audio_plan(path, info, s)
    except (RuntimeError, ValueError):
        return None
    if kbps:
        middle = kbps * 1000 / 8 * length
        return middle * 0.95, middle * 1.05
    rate = int(s.adv.sample_rate) if s.adv.sample_rate != "source" else (info.get("sample_rate") or 44100)
    channels = int(s.adv.channels) if s.adv.channels != "source" else (info.get("channels") or 2)
    raw = rate * channels * (s.depth if fmt.depths else 16) / 8 * length
    if fmt.codec in ("pcm", "pcm_be"):
        return raw, raw * 1.01
    return raw * 0.4, raw * 0.75


# ------------------------------------------------------------ 執行


def _run(args, cancel, on_seconds=None, cwd=None):
    proc = deps.popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd)
    _active.add(proc)

    def watch_cancel():
        while proc.poll() is None:
            if cancel.wait(0.3):
                deps.kill_tree(proc)
                return

    if cancel is not None:
        threading.Thread(target=watch_cancel, daemon=True).start()
    log = []
    try:
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            key, _, value = line.partition("=")
            if key == "out_time_us":
                if on_seconds and value.lstrip("-").isdigit():
                    on_seconds(max(0, int(value)) / 1_000_000)
            elif line and not (value and key.replace("_", "").isalnum()):
                log.append(line)
                del log[:-20]
        code = proc.wait()
    finally:
        _active.discard(proc)
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    return code, log


def _failure(log):
    detail = next((line for line in reversed(log) if line.strip()), "")
    if "No space left" in detail or "not enough space" in detail.lower():
        return "磁碟空間不足"
    return f"轉檔失敗：{detail[:160]}" if detail else "轉檔失敗"


def convert_video(path, info, folder, s, hardware, progress=None, cancel=None):
    """轉換一個影片;回傳 (輸出檔, 附註)。progress 收到 0~1 的比例。"""
    path, folder = Path(path), Path(folder)
    cancel = cancel or threading.Event()
    report = progress or (lambda ratio: None)
    plan = plan_video(path, info, s, hardware)
    folder.mkdir(parents=True, exist_ok=True)
    out = free_path(folder, path.stem, plan.fmt.ext)
    kbps = None if plan.fmt.key == "gif" else planned_video_kbps(plan, s, info)
    two_pass = kbps is not None and s.mode == "size" and bool(plan.encoder.two_pass) and not plan.fmt.target
    note = ""
    if plan.encoder is not None and plan.encoder.hardware:
        vendor = next(v for v, e in F.CODECS[plan.codec].hardware.items() if e is plan.encoder)
        note = f"用 {VENDOR_LABELS[vendor]} 顯示卡"

    def attempt(video_kbps):
        work = Path(tempfile.mkdtemp(prefix="naiz_media_"))
        try:
            with atomic_path(out) as temp:
                passes = (1, 2) if two_pass else (0,)
                for index, number in enumerate(passes):
                    share = 1 / len(passes)
                    args = video_args(path, info, plan, s, temp, video_kbps, number)
                    code, log = _run(args, cancel,
                                     lambda sec, i=index: report(min(1, (i + sec / max(0.1, plan.length)) * share)),
                                     cwd=work)
                    if code:
                        raise RuntimeError(_failure(log))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    attempt(kbps)
    if kbps is not None and s.mode == "size":
        limit = s.target_mb * 1024 * 1024
        audio = audio_kbps(plan.audio, s, target_kbps(s.target_mb, plan.length)) if plan.audio else 0
        audio_bytes = (audio or 0) * 1000 / 8 * plan.length
        retried = 0

        def shrink(kbps, tries):
            nonlocal retried
            for _ in range(tries):
                actual = out.stat().st_size
                if actual <= limit:
                    break
                # 超過目標時扣掉聲音佔的大小,只降低影片的位元速率重轉
                report(0)
                kbps *= max(0.05, (limit * RETRY_MARGIN - audio_bytes) / max(1, actual - audio_bytes))
                attempt(kbps)
                retried += 1

        shrink(kbps, HARDWARE_RETRIES if plan.encoder.hardware else RETRIES)
        notes = []
        if out.stat().st_size > limit and plan.encoder.hardware:
            # 顯示卡把畫質降到最低也壓不到這麼小時,改用處理器(可以分析兩次,壓得更小)
            plan.encoder = F.CODECS[plan.codec].cpu
            two_pass = bool(plan.encoder.two_pass)
            report(0)
            kbps = planned_video_kbps(plan, s, info)
            attempt(kbps)
            shrink(kbps, RETRIES)
            notes.append("顯示卡壓不到這麼小，已改用處理器")
            note = ""
        if retried:
            notes.append(f"超過目標大小，已自動重轉 {retried} 次")
        if out.stat().st_size > limit:
            notes.append("重轉後仍略大於目標大小")
        note = " · ".join(notes + ([note] if note else []))
    report(1)
    return out, note


def convert_audio(path, info, folder, s, progress=None, cancel=None):
    path, folder = Path(path), Path(folder)
    cancel = cancel or threading.Event()
    report = progress or (lambda ratio: None)
    fmt, _, length, _ = audio_plan(path, info, s)
    folder.mkdir(parents=True, exist_ok=True)
    out = free_path(folder, path.stem, fmt.ext)
    with atomic_path(out) as temp:
        code, log = _run(audio_args(path, info, s, temp), cancel, lambda sec: report(min(1, sec / max(0.1, length))))
        if code:
            raise RuntimeError(_failure(log))
    report(1)
    return out, ""
