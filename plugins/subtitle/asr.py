"""即時辨識:讓 whisper.cpp 的伺服器常駐(模型只載入一次),每一小段聲音送進去馬上辨識。
執行檔、模型和「錄音轉逐字稿」共用(core.transcribe),已經下載過就不用再下載;另外多一個「輕量」模型給沒有獨立顯示卡的電腦。

注意:
- 模型用「工作目錄 + 純英文檔名」傳入(whisper.cpp 開不了含中文的路徑)
- 伺服器的輸出一定要丟掉或讀掉:管線塞滿後伺服器會卡住、不再回應
- -ac 768:每次只處理 15 秒內的聲音(預設當成 30 秒),短片段快約三分之一
"""

import io
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave

from core import deps, paths, transcribe

from . import vad

_BROKEN = re.compile(r"\ufffd+\s*")


def _repair(text):
    """要分段時間時,Whisper 偶爾只吐出中文標點的一半位元組,伺服器回傳亂碼「�」(原本的字已經救不回來)。
    實測都出現在該有逗號的地方:句子中間補回「，」,句尾的去掉。"""
    if "�" not in text:
        return text
    text = _BROKEN.sub("，", text)
    return re.sub(r"[，\s]+$", "", re.sub(r"，\s*([，。？！、])", r"\1", text))

RATE = 16000
MAX_SECONDS = 15                # -ac 768 對應的長度;送進去的聲音不超過這個
SMALL = deps.Dependency(
    id="whisper-model-small",
    name="辨識模型（輕量）",
    purpose="語音辨識用的模型，沒有獨立顯示卡也跑得動",
    size_text="約 252 MB",
    url="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q8_0.bin",
    files={"ggml-small-q8_0.bin": None},
    location="models",
    sha256="49c8fb02b65e6049d5fa6c04f81f53b867b5ec9540406812c643f177317f779f",
)
# (代號, 名稱, 模型, 說明)
MODELS = [
    ("base", "快速", transcribe.MODELS["base"], "最省資源；錯字較多，日文、中文尤其明顯"),
    ("small", "輕量", SMALL, "沒有獨立顯示卡也能即時；比快速準"),
    ("turbo", "推薦", transcribe.MODELS["turbo"], "準確又快，有 NVIDIA 顯示卡時選這個"),
    ("large", "最準確", transcribe.MODELS["large"], "錯字最少；需要約 3.5 GB 顯示卡記憶體"),
]
MODEL_NAMES = {key: name for key, name, _, _ in MODELS}
MODEL_FILES = {key: dep for key, _, dep, _ in MODELS}
MODEL_NOTES = {key: note for key, _, _, note in MODELS}
# 原文語言:(Whisper 的代號, 名稱);auto 是自動判斷(每次多約 0.2 秒)
LANGUAGES = [("auto", "自動判斷"), ("en", "英文"), ("ja", "日文"), ("ko", "韓文"), ("zh", "中文"), ("es", "西班牙文"),
             ("fr", "法文"), ("de", "德文"), ("ru", "俄文"), ("th", "泰文"), ("vi", "越南文")]
LANGUAGE_NAMES = dict(LANGUAGES)
WHISPER_NAMES = {"english": "en", "japanese": "ja", "korean": "ko", "chinese": "zh", "spanish": "es", "french": "fr",
                 "german": "de", "russian": "ru", "thai": "th", "vietnamese": "vi"}


def gpu():
    return transcribe.has_nvidia()


def required(model_key):
    needed = [transcribe.engine(), MODEL_FILES[model_key], *vad.DEPS]
    return [dep for dep in needed if not dep.installed()]


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wav(pcm):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(pcm)
    return buffer.getvalue()


class Server:
    """常駐的 whisper-server。start() 會等到模型載入好(顯示卡第一次使用可能要約 30 秒)。"""

    def __init__(self, model_key):
        self.model_key = model_key
        self.port = None
        self.proc = None
        self._lock = threading.Lock()
        # 上一次辨識的細節:segments [(開始秒, 結束秒, 文字)](Whisper 大多一句一段)、
        # confidence 平均對數機率(真的台詞約 -0.1～-0.3,對著配樂、音效亂猜的約 -0.9)
        self.last = {"segments": [], "confidence": 0.0, "languages": {}}

    def start(self, cancel=None, timeout=180):
        exe = transcribe.engine().path().with_name("whisper-server.exe")
        if not exe.is_file():
            raise RuntimeError("語音辨識元件不完整，請到設定裡重新下載")
        self.port = _free_port()
        threads = max(1, min(8, (os.cpu_count() or 4) - 2))
        args = [exe, "-m", MODEL_FILES[self.model_key].path().name, "--host", "127.0.0.1", "--port", self.port,
                "-ac", "768", "-l", "auto", "-nt", "-t", threads]
        self.proc = deps.popen([str(a) for a in args], cwd=paths.MODELS_DIR,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        transcribe._active.add(self.proc)          # 關閉 Naiz Studio 時一併結束
        end = time.time() + timeout
        while time.time() < end:
            if cancel is not None and cancel.is_set():
                self.stop()
                raise deps.Cancelled()
            if self.proc.poll() is not None:
                raise RuntimeError("語音辨識程式無法啟動（可能是顯示卡記憶體不夠，可以換小一點的模型）")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=1)
                return
            except (urllib.error.URLError, OSError):
                time.sleep(0.2)
        self.stop()
        raise RuntimeError("語音辨識程式載入太久，請換小一點的模型再試")

    def stop(self):
        if self.proc is not None:
            deps.kill_tree(self.proc)
            transcribe._active.discard(self.proc)
            self.proc = None

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def transcribe(self, pcm, language="auto", prompt=""):
        """辨識一段 16kHz 單聲道 16 位元 PCM;回傳 (文字, 語言代號, 沒有人聲的機率)。"""
        pcm = pcm[-MAX_SECONDS * RATE * 2:]
        boundary = uuid.uuid4().hex
        # 這次要時間(伺服器預設不給):用來把一口氣講的好幾句切開
        fields = {"language": language, "response_format": "verbose_json", "temperature": "0.0",
                  "no_timestamps": "false"}
        if prompt:
            fields["prompt"] = prompt
        body = b"".join(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
                        for key, value in fields.items())
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
                 "Content-Type: audio/wav\r\n\r\n").encode() + _wav(pcm) + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/inference", data=body,
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with self._lock:                            # 一次辨識一段
            with urllib.request.urlopen(request, timeout=60) as response:
                result = json.load(response)
        segments = result.get("segments") or []
        for segment in segments:
            segment["text"] = _repair(segment.get("text", ""))
        text = "".join(segment.get("text", "") for segment in segments).strip() or _repair(result.get("text", "")).strip()
        detected = WHISPER_NAMES.get(result.get("detected_language") or result.get("language") or "", language)
        silence = max((segment.get("no_speech_prob", 0) for segment in segments), default=0)
        weights = [max(1, len(segment.get("tokens") or [])) for segment in segments]
        confidence = sum(w * segment.get("avg_logprob", 0) for w, segment in zip(weights, segments)) / max(1, sum(weights))
        self.last = {"segments": [(float(segment.get("start", 0)), float(segment.get("end", 0)),
                                   segment.get("text", "").strip()) for segment in segments],
                     "confidence": confidence,
                     "languages": {WHISPER_NAMES.get(code, code): p
                                   for code, p in (result.get("language_probabilities") or {}).items()}}
        return text, detected, silence
