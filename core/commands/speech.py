"""聲音和影片的指令:錄音轉逐字稿、影片轉看圖(給看得懂圖片的 AI 讀影片內容)。"""

import json
import math
from pathlib import Path

from core import deps, paths

from . import CommandError, Param, command

MODELS = (("base", "快速"), ("small", "輕量，沒有獨立顯示卡也跑得動"), ("turbo", "推薦"), ("large", "最準確、最慢"),
          ("breeze", "中文（台灣），只辨識中文、需要 NVIDIA 顯示卡"))
LANGUAGES = (("auto", "自動判斷"), ("zh", "中文"), ("en", "英文"), ("ja", "日文"), ("ko", "韓文"), ("es", "西班牙文"),
             ("fr", "法文"), ("de", "德文"), ("ru", "俄文"), ("th", "泰文"), ("vi", "越南文"))
SPEAKERS = (("off", "不區分"), ("auto", "自動判斷人數"), ("2", ""), ("3", ""), ("4", ""), ("5", ""), ("6", ""))
SCRIPTS = (("tw", "台灣繁體"), ("cn", "簡體"), ("none", "不轉換"))
FORMATS = (("both", "SRT 和 TXT"), ("srt", "只要 SRT"), ("txt", "只要 TXT"))
TEXT_LIMIT = 20000          # 結果裡直接附上的逐字稿最多幾個字(太長的請讀 TXT 檔)
SHEET_COLUMNS = 4



def _folder(value, default):
    folder = (Path(value) if value else default).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _free(folder, stem, suffix):
    from core.files import free_path

    return free_path(folder, stem, suffix)


def _transcribe(media, folder, stem, model, language, speakers, refining, script, fmt, ctx):
    """轉逐字稿並寫出檔案;回傳結果 dict。"""
    from core import transcribe
    from plugins.transcript import models, refine

    if model in models.ZH_ONLY:
        language = "zh"
    count = None if speakers == "off" else (0 if speakers == "auto" else int(speakers))
    refining = refining and language in ("zh", "auto")
    ctx.need(models.required(model, count, refining))
    cues, found = transcribe.transcribe_cues(
        media, models.recognizer(model, language), script, progress=lambda r, t: ctx.progress(r, t), cancel=ctx.cancel,
        speakers=count, prepare=models.preparer(model), refine=refine.refine_file(script) if refining else None)
    srt_text = transcribe.cues_to_srt(cues, script=script)
    txt_text = transcribe.cues_to_text(cues, script=script)
    out = {"language": found, "cues": len(cues),
           "speakers": len({c["speaker"] for c in cues if c.get("speaker") is not None})}
    if fmt in ("both", "srt"):
        path = _free(folder, stem, ".srt")
        path.write_text(srt_text, encoding="utf-8")
        out["srt"] = str(path)
    if fmt in ("both", "txt"):
        path = _free(folder, stem, ".txt")
        path.write_text(txt_text, encoding="utf-8")
        out["txt"] = str(path)
    out["text"] = txt_text[:TEXT_LIMIT]
    out["text_truncated"] = len(txt_text) > TEXT_LIMIT
    return out


@command("transcribe", "把錄音或影片轉成逐字稿（SRT 有時間軸、TXT 純文字），全部在本地處理", [
    Param("media", "錄音或影片檔", kind="file", required=True, positional=True),
    Param("model", "辨識模型", kind="choice", choices=MODELS, default="turbo"),
    Param("language", "原文語言", kind="choice", choices=LANGUAGES, default="auto"),
    Param("speakers", "區分說話者（說話者 1、2…）", kind="choice", choices=SPEAKERS, default="off"),
    Param("refine", "更精確：轉完再仔細聽一次修正同音錯字（只對中文有效，時間約多 3 倍，要另外下載約 2.5 GB 模型）",
          kind="bool", default=False),
    Param("script", "中文的輸出用字", kind="choice", choices=SCRIPTS, default="tw"),
    Param("format", "輸出檔案", kind="choice", choices=FORMATS, default="both"),
    Param("output", "輸出資料夾（預設 Naiz Studio 的 output\\transcripts）", kind="folder"),
], long=True, examples=["transcribe 會議.m4a --speakers auto", "transcribe 影片.mp4 --model breeze --refine"])
def transcribe_command(args, ctx):
    folder = _folder(args["output"], paths.OUTPUT_DIR / "transcripts")
    result = _transcribe(args["media"], folder, args["media"].stem, args["model"], args["language"], args["speakers"],
                         args["refine"], args["script"], args["format"], ctx)
    return {"output": str(folder), **result}


def _probe(video):
    """(長度秒數, 有沒有畫面)。"""
    ffprobe = deps.FFMPEG.path().with_name("ffprobe.exe")
    result = deps.run([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json",
                       video], capture_output=True, timeout=60)
    if result.returncode != 0:
        raise CommandError("讀不出這個檔案（可能不是影片或錄音）")
    data = json.loads(result.stdout.decode("utf-8", "replace") or "{}")
    duration = float((data.get("format") or {}).get("duration") or 0)
    has_video = any(s.get("codec_type") == "video" for s in data.get("streams") or [])
    return duration, has_video


def _clock(seconds, precise=False):
    """畫面上的時間:00:12、1:02:03;precise 時到 0.1 秒(00:12.5,取得很密時用)。"""
    # 先整個四捨五入到 0.1 秒再拆:分開算整數和小數的話,8.95 會變成「00:08.0」(小數進位了,整數沒有)
    tenths = int(round(seconds * 10)) if precise else int(seconds) * 10
    whole, tenth = divmod(tenths, 10)
    text = f"{whole // 3600}:{whole // 60 % 60:02d}:{whole % 60:02d}" if whole >= 3600 \
        else f"{whole // 60:02d}:{whole % 60:02d}"
    return text + f".{tenth}" if precise else text


def _sheet(frames, path, thumb_width):
    """總覽圖:縮圖排成格子,每張左上角標時間。"""
    import os

    from PIL import Image, ImageDraw, ImageFont

    thumbs = []
    for frame in frames:
        with Image.open(frame["path"]) as image:
            image = image.convert("RGB")
            image.thumbnail((thumb_width, thumb_width * 4))
            thumbs.append((image.copy(), frame["clock"]))
    cell_w = thumb_width
    cell_h = max(t.height for t, _ in thumbs)
    columns = min(SHEET_COLUMNS, len(thumbs))
    rows = math.ceil(len(thumbs) / columns)
    sheet = Image.new("RGB", (columns * cell_w + (columns + 1) * 6, rows * cell_h + (rows + 1) * 6), (20, 22, 28))
    pen = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype(os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "msjh.ttc"),
                                  max(14, thumb_width // 18))
    except OSError:
        font = ImageFont.load_default()
    for index, (thumb, clock) in enumerate(thumbs):
        x = 6 + (index % columns) * (cell_w + 6)
        y = 6 + (index // columns) * (cell_h + 6)
        sheet.paste(thumb, (x + (cell_w - thumb.width) // 2, y))
        box = pen.textbbox((0, 0), clock, font=font)
        pen.rectangle((x, y, x + box[2] + 12, y + box[3] + 8), fill=(0, 0, 0))
        pen.text((x + 6, y + 3), clock, fill=(255, 255, 255), font=font)
    sheet.save(path, quality=88)


def _parse_crop(text):
    """「x,y,寬,高」(原始畫面的像素)→ ffmpeg 的 crop 濾鏡。"""
    try:
        x, y, w, h = (int(float(v)) for v in text.replace("，", ",").split(","))
    except ValueError:
        raise CommandError(f"crop 要寫成「x,y,寬,高」（原始畫面的像素），例如 0,80,1280,720：{text}") from None
    if w < 16 or h < 16 or x < 0 or y < 0:
        raise CommandError("crop 的寬、高至少 16，x、y 不能是負的")
    return f"crop={w}:{h}:{x}:{y}"


def _changes(frames):
    """每張和前一張差多少(0～100,畫面平均變化;第一張是 0):先讀這串數字找動作大的時刻,只看那幾張。"""
    from PIL import Image, ImageChops, ImageStat

    previous = None
    for frame in frames:
        with Image.open(frame["path"]) as image:
            small = image.convert("L").resize((96, 54))
        frame["change"] = 0.0 if previous is None else round(
            ImageStat.Stat(ImageChops.difference(small, previous)).mean[0] / 2.55, 1)
        previous = small


def _grab(video, folder, times, width, crop, prefix, precise):
    """在指定的時間點各擷取一張(時間點不多時用:--at、或間隔很大)。width 0 是原尺寸。"""
    frames = []
    filters = ",".join(f for f in (crop, f"scale='min({width},iw)':-2" if width else "") if f)
    for index, at in enumerate(times):
        path = folder / f"{prefix}_{index + 1:03d}_{_clock(at, precise).replace(':', '-')}.jpg"
        command = [deps.FFMPEG.path(), "-nostdin", "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", video,
                   "-frames:v", "1", "-q:v", "2"]
        if filters:
            command += ["-vf", filters]
        if deps.run([*command, path], capture_output=True, timeout=600).returncode == 0 and path.is_file():
            frames.append({"time": round(at, 2), "clock": _clock(at, precise), "path": str(path)})
    return frames


@command("video-look", "影片拆成一張張畫面（附時間、和前一張的變化量）和總覽圖，可以只取一段時間、裁切、順便轉逐字稿；"
                       "給看得懂圖片的 AI 了解影片內容", [
    Param("video", "影片檔（只有聲音的檔案就只轉逐字稿）", kind="file", required=True, positional=True),
    Param("every", "每隔幾秒擷取一張（0＝依長度自動決定，約 24 張）；要看快速動作可以 0.5 或 0.1", kind="float", default=0),
    Param("start", "從第幾秒開始（只看一段時）", kind="float", default=0),
    Param("end", "到第幾秒結束（0＝到影片結尾）", kind="float", default=0),
    Param("max_frames", "最多擷取幾張（0＝自動：沒指定間隔時 24 張，指定間隔時最多 1000 張）", kind="int", default=0),
    Param("width", "每張畫面的寬度（像素，越小越省看圖的額度）", kind="int", default=960),
    Param("crop", "只留畫面的一塊：「x,y,寬,高」（原始畫面的像素），例如只留遊戲視窗、去掉瀏覽器和工作列", kind="text"),
    Param("at", "只取這幾個時間點的原尺寸畫面，用逗號分開（秒），例如 12.8,20.2；總覽圖看到可疑處後用", kind="text"),
    Param("sheet", "另外拼總覽圖（畫面排成格子、每格標時間）", kind="bool", default=True),
    Param("sheet_size", "每張總覽圖放幾格（畫面多時分成好幾張總覽圖）", kind="int", default=24),
    Param("tile_width", "總覽圖每格的寬度（像素）", kind="int", default=360),
    Param("transcript", "順便轉逐字稿用的辨識模型（none＝不轉）", kind="choice",
          choices=(("none", "不轉"),) + MODELS, default="none"),
    Param("language", "逐字稿的原文語言", kind="choice", choices=LANGUAGES, default="auto"),
    Param("output", "輸出資料夾（預設 Naiz Studio 的 output\\video_look\\影片名）", kind="folder"),
], long=True, examples=["video-look 錄影.mp4", "video-look 錄影.mp4 --every 2 --crop 0,80,1280,720",
                        "video-look 錄影.mp4 --start 8 --end 13 --every 0.1", "video-look 錄影.mp4 --at 12.8,20.2",
                        "video-look 錄影.mp4 --transcript turbo"])
def video_look(args, ctx):
    video = args["video"]
    ctx.need([deps.FFMPEG])
    if args["width"] and args["width"] < 64 or args["tile_width"] < 64 or args["sheet_size"] < 1:
        raise CommandError("width、tile_width 至少 64，sheet_size 至少 1")
    if args["max_frames"] < 0 or args["every"] < 0:
        raise CommandError("every、max_frames 不能是負的")
    folder = _folder(args["output"], paths.OUTPUT_DIR / "video_look" / video.stem)
    duration, has_video = _probe(video)
    crop = _parse_crop(args["crop"]) if args["crop"] else ""
    result = {"output": str(folder), "duration": round(duration, 2), "frames": []}
    for old in folder.glob("*.jpg"):
        if old.name.startswith(("frame_", "at_", "sheet")):
            old.unlink()
    if has_video and args["at"]:
        try:
            times = [float(v) for v in args["at"].replace("，", ",").split(",") if v.strip()]
        except ValueError:
            raise CommandError(f"at 要寫成秒數，用逗號分開，例如 12.8,20.2：{args['at']}") from None
        bad = [t for t in times if t < 0 or (duration and t > duration)]
        if bad:
            raise CommandError(f"時間點超出影片長度（{duration:.1f} 秒）：{bad}")
        ctx.progress(0.0, "擷取畫面")
        result["frames"] = _grab(video, folder, times, 0 if args["width"] == 960 else args["width"], crop, "at", True)
        if not result["frames"]:
            raise CommandError("擷取畫面失敗（影片可能損壞）")
    elif has_video:
        start = max(0.0, args["start"])
        end = min(args["end"] or duration, duration) if duration else args["end"]
        if duration and start >= end:
            raise CommandError(f"開始時間要比結束早，而且在影片長度（{duration:.1f} 秒）以內")
        span = (end - start) if end else 0
        limit = args["max_frames"] or (24 if not args["every"] else 1000)
        every = args["every"] or max(1.0, span / limit if span else 1.0)
        count = max(1, min(limit, math.ceil(span / every) if span else 1))
        first = start + min(every / 2, max(0.0, span - 0.05))      # 每段的中間,不會拍到開頭的黑畫面
        precise = every < 1
        ctx.progress(0.0, "擷取畫面")
        pattern = folder / "frame_%04d.jpg"
        filters = ",".join(f for f in (f"fps=1/{every:.6f}", crop, f"scale='min({args['width']},iw)':-2" if args["width"] else "")
                           if f)
        code = deps.run([deps.FFMPEG.path(), "-nostdin", "-y", "-v", "error", "-ss", f"{first:.3f}", "-i", video,
                         "-vf", filters, "-frames:v", str(count), "-q:v", "3", pattern],
                        capture_output=True, timeout=3600).returncode
        files = sorted(folder.glob("frame_[0-9][0-9][0-9][0-9].jpg"))
        if code != 0 or not files:
            raise CommandError("擷取畫面失敗（影片可能損壞，或裁切範圍超出畫面）")
        for index, path in enumerate(files):
            at = first + index * every
            named = path.with_name(f"frame_{index + 1:04d}_{_clock(at, precise).replace(':', '-')}.jpg")
            path.replace(named)
            result["frames"].append({"time": round(at, 2), "clock": _clock(at, precise), "path": str(named)})
        result["every"] = round(every, 3)
        result["range"] = [round(start, 2), round(end, 2)]
    elif args["transcript"] == "none":
        raise CommandError("這個檔案沒有畫面；只有聲音的檔案請用 transcribe，或加上 --transcript 轉逐字稿")
    if result["frames"]:
        _changes(result["frames"])
        if args["sheet"]:
            size = args["sheet_size"]
            sheets = []
            for page, begin in enumerate(range(0, len(result["frames"]), size)):
                sheet = folder / (f"sheet_{page + 1:02d}.jpg" if len(result["frames"]) > size else "sheet.jpg")
                _sheet(result["frames"][begin:begin + size], sheet, args["tile_width"])
                sheets.append(str(sheet))
            result["sheets"] = sheets
            result["sheet"] = sheets[0]                 # 舊欄位:第一張
    if args["transcript"] != "none":
        ctx.progress(None, "轉逐字稿")
        result["transcript"] = _transcribe(video, folder, "transcript", args["transcript"], args["language"], "off",
                                           False, "tw", "both", ctx)
    ctx.progress(1.0, "完成")
    return result
