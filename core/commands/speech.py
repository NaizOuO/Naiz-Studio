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
SHEET_THUMB = 360           # 總覽圖每格寬度


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


def _clock(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}" if seconds >= 3600 \
        else f"{seconds // 60:02d}:{seconds % 60:02d}"


def _sheet(frames, path):
    """總覽圖:縮圖排成格子,每張左上角標時間。"""
    import os

    from PIL import Image, ImageDraw, ImageFont

    thumbs = []
    for frame in frames:
        with Image.open(frame["path"]) as image:
            image = image.convert("RGB")
            image.thumbnail((SHEET_THUMB, SHEET_THUMB))
            thumbs.append((image.copy(), frame["clock"]))
    cell_w = SHEET_THUMB
    cell_h = max(t.height for t, _ in thumbs)
    columns = min(SHEET_COLUMNS, len(thumbs))
    rows = math.ceil(len(thumbs) / columns)
    sheet = Image.new("RGB", (columns * cell_w + (columns + 1) * 6, rows * cell_h + (rows + 1) * 6), (20, 22, 28))
    pen = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype(os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "msjh.ttc"), 20)
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


@command("video-look", "影片拆成一張張畫面（附時間）和一張總覽圖，可以順便轉逐字稿；給看得懂圖片的 AI 了解影片內容", [
    Param("video", "影片檔（只有聲音的檔案就只轉逐字稿）", kind="file", required=True, positional=True),
    Param("every", "每隔幾秒擷取一張（0＝依影片長度自動決定）", kind="float", default=0),
    Param("max_frames", "最多擷取幾張", kind="int", default=24),
    Param("width", "每張畫面的寬度（像素）", kind="int", default=960),
    Param("sheet", "另外拼一張總覽圖（所有畫面排成格子、標時間）", kind="bool", default=True),
    Param("transcript", "順便轉逐字稿用的辨識模型（none＝不轉）", kind="choice",
          choices=(("none", "不轉"),) + MODELS, default="none"),
    Param("language", "逐字稿的原文語言", kind="choice", choices=LANGUAGES, default="auto"),
    Param("output", "輸出資料夾（預設 Naiz Studio 的 output\\video_look\\影片名）", kind="folder"),
], long=True, examples=["video-look 錄影.mp4", "video-look 錄影.mp4 --every 2 --transcript turbo"])
def video_look(args, ctx):
    video = args["video"]
    ctx.need([deps.FFMPEG])
    if args["max_frames"] < 1 or args["width"] < 64:
        raise CommandError("max_frames 至少 1、width 至少 64")
    folder = _folder(args["output"], paths.OUTPUT_DIR / "video_look" / video.stem)
    duration, has_video = _probe(video)
    result = {"output": str(folder), "duration": round(duration, 2), "frames": []}
    if has_video:
        every = args["every"] or max(1.0, duration / args["max_frames"])
        count = max(1, min(args["max_frames"], math.ceil(duration / every) if duration else 1))
        first = min(every / 2, max(0.0, duration - 0.1))           # 每段的中間,不會拍到開頭的黑畫面
        ctx.progress(0.0, "擷取畫面")
        pattern = folder / "frame_%03d.jpg"
        for old in folder.glob("frame_*.jpg"):
            old.unlink()
        code = deps.run([deps.FFMPEG.path(), "-nostdin", "-y", "-v", "error", "-ss", f"{first:.3f}", "-i", video,
                         "-vf", f"fps=1/{every:.6f},scale={args['width']}:-2", "-frames:v", str(count), "-q:v", "3",
                         pattern], capture_output=True, timeout=3600).returncode
        files = sorted(folder.glob("frame_*.jpg"))
        if code != 0 or not files:
            raise CommandError("擷取畫面失敗（影片可能損壞）")
        for index, path in enumerate(files):
            at = first + index * every
            named = path.with_name(f"frame_{index + 1:03d}_{_clock(at).replace(':', '-')}.jpg")
            path.replace(named)
            result["frames"].append({"time": round(at, 2), "clock": _clock(at), "path": str(named)})
        result["every"] = round(every, 2)
        if args["sheet"]:
            sheet = folder / "sheet.jpg"
            _sheet(result["frames"], sheet)
            result["sheet"] = str(sheet)
    elif args["transcript"] == "none":
        raise CommandError("這個檔案沒有畫面；只有聲音的檔案請用 transcribe，或加上 --transcript 轉逐字稿")
    if args["transcript"] != "none":
        ctx.progress(None, "轉逐字稿")
        result["transcript"] = _transcribe(video, folder, "transcript", args["transcript"], args["language"], "off",
                                           False, "tw", "both", ctx)
    ctx.progress(1.0, "完成")
    return result
