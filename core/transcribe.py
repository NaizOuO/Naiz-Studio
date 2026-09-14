"""本機語音轉文字引擎(目前使用 whisper.cpp),任何模組都可以拿來把影片或錄音轉成逐字稿。

用法:
    missing = [d for d in transcribe.required("turbo") if not d.installed()]  # 缺的先經同意視窗下載
    srt = transcribe.transcribe(path, "turbo", "tw", progress=..., cancel=...)
    text = transcribe.srt_to_text(srt)
"""

import atexit
import bisect
import ctypes
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
from functools import cache
from pathlib import Path

from . import deps, diarize, paths

WHISPER_TAG = "b5130"
_RELEASE = f"https://github.com/ggml-org/whisper.cpp/releases/download/{WHISPER_TAG}"
_MODELS_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"

WHISPER_CPU = deps.Dependency(
    id="whisper-cpu",
    name="Whisper 語音辨識",
    purpose="在電腦上把語音轉成文字,不需要上傳",
    size_text="約 8 MB",
    url=f"{_RELEASE}/whisper-bin-x64.zip",
    files={"whisper-cpu/whisper-cli.exe": None},
    folder="whisper-cpu",
    check_args=["--help"],
    sha256="f9ec6c52a2e949b62ab51fa21d0d497958f9e41c3010c157c4e42932d5316f3c",
)

WHISPER_CUDA = deps.Dependency(
    id="whisper-cuda",
    name="Whisper 語音辨識(NVIDIA 顯示卡版)",
    purpose="在電腦上把語音轉成文字,用顯示卡加速",
    size_text="約 643 MB",
    url=f"{_RELEASE}/whisper-cublas-12.4.0-bin-x64.zip",
    files={"whisper-cuda/whisper-cli.exe": None},
    folder="whisper-cuda",
    check_args=["--help"],
    sha256="af520ddd034d985b55dfeea3e465ed93653ba2aee1a55e865033edc548c272a7",
)

VAD = deps.Dependency(
    id="whisper-vad",
    name="人聲偵測模型",
    purpose="跳過沒有人說話的片段,避免辨識出不存在的句子",
    size_text="約 1 MB",
    url="https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin",
    files={"ggml-silero-v6.2.0.bin": None},
    location="models",
    sha256="2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987",
)


def _model(key, filename, size_text, sha256):
    return deps.Dependency(
        id=f"whisper-model-{key}",
        name=f"辨識模型({MODEL_LABELS[key]})",
        purpose="語音辨識用的模型",
        size_text=size_text,
        url=f"{_MODELS_URL}/{filename}",
        files={filename: None},
        location="models",
        sha256=sha256,
    )


# 模型選項:(值, 顯示名稱),說明另外放在 MODEL_NOTES
MODEL_OPTIONS = [("base", "快速"), ("turbo", "推薦"), ("large", "最準確")]
MODEL_LABELS = dict(MODEL_OPTIONS)
MODEL_NOTES = {
    "base": "檔案最小、速度最快,但錯字較多,適合先大概看內容",
    "turbo": "準確又快,大多數情況選這個",
    "large": "錯字最少,但速度約慢 3 倍、檔案約 3GB",
}
MODELS = {
    "base": _model("base", "ggml-base.bin", "約 141 MB",
                   "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"),
    "turbo": _model("turbo", "ggml-large-v3-turbo-q5_0.bin", "約 547 MB",
                    "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"),
    "large": _model("large", "ggml-large-v3.bin", "約 2.9 GB",
                    "64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2"),
}
# 每個模型計算「每個字時間」用的設定名稱
MODEL_DTW = {"base": "base", "turbo": "large.v3.turbo", "large": "large.v3"}

# 輸出文字選項
SCRIPT_OPTIONS = [("tw", "台灣繁體"), ("cn", "簡體"), ("none", "不轉換")]
SCRIPT_NOTES = {
    "tw": "轉成台灣用字,例如「軟體」「影片」",
    "cn": "轉成簡體中文",
    "none": "保留辨識原本的結果,可能繁簡混雜",
}
_OPENCC_CONFIG = {"tw": "s2twp", "cn": "t2s"}


@cache
def has_nvidia() -> bool:
    if platform.system() != "Windows" or shutil.which("nvidia-smi") is None:
        return False
    try:
        return deps.run(["nvidia-smi", "-L"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


def engine():
    return WHISPER_CUDA if has_nvidia() else WHISPER_CPU


def required(model_key, speakers=None):
    """需要的所有元件(含 FFmpeg),呼叫端篩出未安裝的交給同意視窗。speakers 不是 None 時包含說話者分離。"""
    items = [deps.FFMPEG, engine(), MODELS[model_key], VAD]
    return items + diarize.DEPS if speakers is not None else items


_SPEAKER = re.compile(r"^(說話者|说话者) (\d+):")
_VAD_INFO = re.compile(r"vad_segment_info: orig_start: ([\d.]+), orig_end: ([\d.]+), "
                       r"vad_start: ([\d.]+), vad_end: ([\d.]+)")
_REPEATED = re.compile(r"(.{2,8}?)\1{2,}")


def srt_to_text(content: str) -> str:
    """去掉時間軸,連續重複的行也一併去除;有標示說話者時,同一人連續說的話放在同一段。"""
    lines = []
    current = None
    for block in re.split(r"\r?\n\s*\r?\n", content):
        for raw in block.splitlines():
            text = re.sub(r"<[^>]+>", "", raw).strip()
            if not text or text.isdigit() or "-->" in text:
                continue
            match = _SPEAKER.match(text)
            if match:
                label = match.group(0)[:-1]
                text = text[match.end():].strip()
                if label != current:
                    if lines:
                        lines.append("")
                    lines.append(f"{label}:")
                    current = label
            if text and (not lines or lines[-1] != text):
                lines.append(text)
    return "\n".join(lines)


def time_map(infos):
    """依 whisper.cpp 的做法,建立「剪掉靜音後的時間 → 原始時間」對照點(秒)。
    infos 是 log 裡每段人聲的 (原始起點, 原始終點, 剪後起點, 剪後終點)。"""
    points = []
    for i, (orig_start, orig_end, vad_start, vad_end) in enumerate(infos):
        points += [(vad_start, orig_start), (vad_end, orig_end)]
        if i + 1 < len(infos):
            # 每段後面多接 0.1 秒,再插入 0.1 秒靜音;靜音對應到兩段人聲之間的空檔
            points.append((infos[i + 1][2] - 0.1, orig_end))
    points.sort()
    unique = []
    for point in points:
        if not unique or unique[-1][0] != point[0]:
            unique.append(point)
    return unique


def to_original(t, points):
    if not points:
        return t
    if t <= points[0][0]:
        return points[0][1]
    if t >= points[-1][0]:
        return points[-1][1]
    index = bisect.bisect_left(points, (t,))
    upper, lower = points[index], points[index - 1]
    if upper[0] == t or upper[0] == lower[0]:
        return upper[1]
    return lower[1] + (t - lower[0]) * (upper[1] - lower[1]) / (upper[0] - lower[0])


def read_lines(json_path, points):
    """讀 whisper 的完整 JSON,回傳每句 {start, end, text, words:[(原始時間, 文字)]}。"""
    data = json.loads(Path(json_path).read_text(encoding="utf-8", errors="replace"))
    lines = []
    for seg in data.get("transcription", []):
        text = seg.get("text", "").strip()
        repeated = _REPEATED.fullmatch(re.sub(r"[\W_]", "", text))
        if not text or (repeated and len(set(repeated.group(1))) >= 2):
            # 同一小段字連續重複 3 次以上(例如「五星座五星座五星座」)是常見的幻覺句,直接略過;
            # 單一個字重複(哈哈哈、對對對)通常是真的聲音,保留
            continue
        words = []
        for token in seg.get("tokens", []):
            piece = token.get("text", "")
            if piece.startswith("[_"):
                continue
            dtw = token.get("t_dtw", -1)
            processed = dtw / 100 if dtw >= 0 else token["offsets"]["from"] / 1000
            if words and re.match(r"[A-Za-z0-9]", piece) and re.search(r"[A-Za-z0-9]$", words[-1][1]):
                # 英文單字會被拆成好幾塊(例如 Sil + icon),接回前一塊,切換說話者時才不會斷在字中間
                words[-1] = (words[-1][0], words[-1][1] + piece)
                continue
            words.append((to_original(processed, points), piece))
        if any("�" in piece for _, piece in words):
            # 中文字被拆成好幾塊時沒辦法逐字切,整句當成一個單位
            words = words[:1] and [(words[0][0], text)]
        lines.append({"start": seg["offsets"]["from"] / 1000, "end": seg["offsets"]["to"] / 1000,
                      "text": text, "words": words})
    return lines


def _speaker_at(t, segments):
    containing = [s for s in segments if s[0] <= t <= s[1]]
    if containing:
        # 有重疊時選最短的那段,插話通常很短
        return min(containing, key=lambda s: s[1] - s[0])[2]
    return min(segments, key=lambda s: min(abs(s[0] - t), abs(s[1] - t)))[2]


def _interval_at(t, voice, tolerance=0.15):
    index = bisect.bisect_right(voice, (t + tolerance, float("inf"))) - 1
    if index >= 0 and voice[index][1] + tolerance >= t:
        return voice[index]
    return None


def build_cues(lines, voice, segments=None):
    """把每句拆成字幕條:有說話者時在換人處切開;時間對齊到實際人聲,聲音開始才出現、結束就消失。
    voice 是依時間排序的人聲區間 [(開始秒, 結束秒)]。"""
    voice = sorted(voice)
    cues = []
    for line in lines:
        words = line["words"] or [(line["start"], line["text"])]
        speakers = [_speaker_at(t, segments) for t, _ in words] if segments else [None] * len(words)
        groups = []
        for word, speaker in zip(words, speakers):
            if groups and groups[-1][0] == speaker:
                groups[-1][1].append(word)
            else:
                groups.append([speaker, [word]])
        # 太零碎的片段(不到 2 個字)併回前一段,避免一個字就換人
        merged = []
        for speaker, group in groups:
            if merged and len(re.sub(r"\W", "", "".join(w for _, w in group))) < 2:
                merged[-1][1].extend(group)
            else:
                merged.append([speaker, group])
        if len(merged) > 1 and len(re.sub(r"\W", "", "".join(w for _, w in merged[0][1]))) < 2:
            first = merged.pop(0)
            merged[0][1][:0] = first[1]
        for speaker, group in merged:
            text = "".join(w for _, w in group).strip()
            if text:
                cues.append({"start": group[0][0], "last": group[-1][0], "text": text, "speaker": speaker})

    for cue in cues:
        start, last = cue["start"], cue["last"]
        interval = _interval_at(start, voice, tolerance=0)
        if interval is not None and start >= interval[1] - 0.02:
            # 剛好落在一段人聲的結尾:換算時兩段之間的空檔會被壓成這個點,其實是在靜音裡
            interval = None
        if interval is None:
            # 字的時間落在靜音上(換算時靜音會被攤平):往後找這條字幕結束前的第一段人聲
            index = bisect.bisect_left(voice, (start,))
            if index < len(voice) and voice[index][0] <= max(last, start) + 0.5:
                start = voice[index][0]
        elif start - 0.5 <= interval[0]:
            start = interval[0]
        cue["start"] = start
        interval = _interval_at(last, voice)
        if interval is None:
            # 最後一個字落在靜音上:用它之前那段人聲的結束
            index = bisect.bisect_right(voice, (last, float("inf"))) - 1
            interval = voice[index] if index >= 0 and voice[index][1] >= start else None
        cue["end"] = interval[1] if interval else last + 0.4

    for previous, cue in zip(cues, cues[1:]):
        cue["start"] = max(cue["start"], previous["start"] + 0.3)
        previous["end"] = min(previous["end"], cue["start"])
    for cue in cues:
        cue["end"] = max(cue["end"], cue["start"] + 0.3)
    return cues


def _srt_time(t):
    ms = max(0, round(t * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    seconds, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def cues_to_srt(cues) -> str:
    """字幕條轉成 SRT;說話者編號照第一次出現的順序。"""
    order = {}
    blocks = []
    for number, cue in enumerate(cues, start=1):
        text = cue["text"]
        if cue["speaker"] is not None:
            text = f"說話者 {order.setdefault(cue['speaker'], len(order) + 1)}:{text}"
        blocks.append(f"{number}\n{_srt_time(cue['start'])} --> {_srt_time(cue['end'])}\n{text}")
    return "\n\n".join(blocks) + "\n" if blocks else ""


def convert_script(text: str, script: str) -> str:
    config = _OPENCC_CONFIG.get(script)
    if not config:
        return text
    from opencc import OpenCC
    return OpenCC(config).convert(text)


def _ascii_temp_dir() -> Path:
    """whisper.cpp 開不了含中文的路徑;使用者名稱是中文時,改用 Windows 短檔名或公用資料夾。"""
    temp = tempfile.gettempdir()
    if temp.isascii():
        return Path(temp)
    if platform.system() == "Windows":
        buffer = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(temp, buffer, 1024) and buffer.value.isascii():
            return Path(buffer.value)
    public = os.environ.get("PUBLIC", "")
    if public.isascii() and Path(public).is_dir():
        return Path(public)
    return Path(temp)


# 正在執行的外部程式;關閉 Naiz Studio 時一併結束,避免辨識在背景繼續跑
_active = set()
atexit.register(lambda: [deps.kill_tree(proc) for proc in list(_active)])


def _run(args, cancel, on_line=None, cwd=None):
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
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").rstrip()
        log.append(line)
        del log[:-40]
        if on_line:
            on_line(line)
    code = proc.wait()
    _active.discard(proc)
    if cancel is not None and cancel.is_set():
        raise deps.Cancelled()
    return code, log


def transcribe(media_path, model_key="turbo", script="tw", language="zh", progress=None, cancel=None,
               speakers=None) -> str:
    """把影片或音訊轉成 SRT 字幕文字。progress(比例或 None, 說明文字);cancel 是 threading.Event。
    speakers:None 不區分說話者,0 自動判斷人數,其他數字為指定人數。"""
    report = progress or (lambda ratio, text: None)
    cancel = cancel or threading.Event()
    exe = engine().path()
    model = MODELS[model_key]
    work = Path(tempfile.mkdtemp(prefix="naiz_asr_", dir=_ascii_temp_dir()))
    try:
        report(None, "準備音訊")
        wav = work / "audio.wav"
        code, log = _run([deps.FFMPEG.path(), "-nostdin", "-y", "-i", media_path, "-vn",
                          "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav], cancel)
        if code != 0 or not wav.is_file():
            raise RuntimeError("無法讀取這個檔案的聲音")

        gpu = exe.parent.name == "whisper-cuda"
        report(None, "載入模型(第一次使用顯示卡約需 30 秒)" if gpu else "載入模型")

        infos = []

        def on_line(line):
            match = re.search(r"progress\s*=\s*(\d+)%", line)
            if match:
                report(int(match.group(1)) / 100, "辨識語音")
            match = _VAD_INFO.search(line)
            if match:
                infos.append(tuple(float(x) for x in match.groups()))

        # 模型用「工作目錄 + 純英文檔名」傳入,避免程式資料夾路徑含中文時開不了
        # -mc 0:不帶前文,避免整段重複;--vad:跳過靜音,避免憑空冒出句子
        # -dtw + -nfa:算出精確的字時間,字幕開頭才會和聲音對齊、換人處才切得準(和人工字幕比,
        # 開頭誤差 0.3 秒內從 22~65% 提升到 91~97%);代價是關閉 flash attention 慢約 36%,
        # 偶爾多出的重複幻覺句由 read_lines 過濾
        out = work / "result"
        code, log = _run([exe, "-m", model.path().name, "-f", wav, "-l", language, "-mc", "0",
                          "--vad", "-vm", VAD.path().name, "-t", max(1, (os.cpu_count() or 4) - 1),
                          "-dtw", MODEL_DTW[model_key], "-nfa", "-pp", "-ojf", "-of", out],
                         cancel, on_line=on_line, cwd=paths.MODELS_DIR)
        result = out.with_suffix(".json")
        if code != 0 or not result.is_file():
            detail = next((l for l in reversed(log) if l.strip()), "")
            raise RuntimeError(f"語音辨識失敗 {detail[:120]}".strip())
        report(1, "整理文字")
        lines = read_lines(result, time_map(infos))
        voice = [(orig_start, orig_end) for orig_start, orig_end, _, _ in infos]
        segments = None
        if speakers is not None and voice:
            report(None, "區分說話者")
            # 聲音特徵由 C 介面直接讀 float32 原始音訊,不必在 Python 裡逐個樣本轉換
            raw = work / "audio.f32"
            code, _ = _run([deps.FFMPEG.path(), "-nostdin", "-y", "-i", wav, "-f", "f32le", "-c:a", "pcm_f32le", raw],
                           cancel)
            if code != 0 or not raw.is_file():
                raise RuntimeError("區分說話者失敗")
            segments = diarize.diarize(raw, voice, speakers, cancel, work_dir=work,
                                       progress=lambda r: report(r, "區分說話者"))
        content = cues_to_srt(build_cues(lines, voice, segments))
        # 先標說話者再轉換,「說話者」三個字才會跟著轉成簡體
        return convert_script(content, script)
    finally:
        shutil.rmtree(work, ignore_errors=True)
