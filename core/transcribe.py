"""本機語音轉文字引擎(目前使用 whisper.cpp),任何模組都可以拿來把影片或錄音轉成逐字稿。

用法:
    missing = [d for d in transcribe.required("turbo") if not d.installed()]  # 缺的先經同意視窗下載
    srt = transcribe.transcribe(path, "turbo", "tw", progress=..., cancel=...)
    text = transcribe.srt_to_text(srt)
"""

import atexit
import ctypes
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
_SRT_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


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


def label_speakers(srt: str, segments) -> str:
    """依時間重疊把每句字幕標上「說話者 N:」,編號照第一次出現的順序。"""
    if not segments:
        return srt
    order = {}
    blocks = []
    for block in re.split(r"\r?\n\s*\r?\n", srt.strip()):
        lines = block.splitlines()
        index = next((i for i, line in enumerate(lines) if _SRT_TIME.search(line)), None)
        if index is None or index + 1 >= len(lines):
            blocks.append(block)
            continue
        g = [int(x) for x in _SRT_TIME.search(lines[index]).groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        best = max(segments, key=lambda s: min(end, s[1]) - max(start, s[0]))
        if min(end, best[1]) - max(start, best[0]) <= 0:
            # 沒有重疊(例如分段模型漏掉很短的句子),改用時間最接近的一段
            middle = (start + end) / 2
            best = min(segments, key=lambda s: abs((s[0] + s[1]) / 2 - middle))
        speaker = order.setdefault(best[2], len(order) + 1)
        lines[index + 1] = f"說話者 {speaker}:{lines[index + 1]}"
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


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

        def on_line(line):
            match = re.search(r"progress\s*=\s*(\d+)%", line)
            if match:
                report(int(match.group(1)) / 100, "辨識語音")

        # 模型用「工作目錄 + 純英文檔名」傳入,避免程式資料夾路徑含中文時開不了
        # -mc 0:不帶前文,避免整段重複;--vad:跳過靜音,避免憑空冒出句子
        out = work / "result"
        code, log = _run([exe, "-m", model.path().name, "-f", wav, "-l", language, "-mc", "0",
                          "--vad", "-vm", VAD.path().name, "-t", max(1, (os.cpu_count() or 4) - 1),
                          "-pp", "-osrt", "-of", out], cancel, on_line=on_line, cwd=paths.MODELS_DIR)
        srt = out.with_suffix(".srt")
        if code != 0 or not srt.is_file():
            detail = next((l for l in reversed(log) if l.strip()), "")
            raise RuntimeError(f"語音辨識失敗 {detail[:120]}".strip())
        content = srt.read_text(encoding="utf-8", errors="replace")
        if speakers is not None:
            report(None, "區分說話者")
            segments = diarize.diarize(wav, speakers, cancel, progress=lambda r: report(r, "區分說話者"))
            content = label_speakers(content, segments)
        report(1, "整理文字")
        # 先標說話者再轉換,「說話者」三個字才會跟著轉成簡體
        return convert_script(content, script)
    finally:
        shutil.rmtree(work, ignore_errors=True)
