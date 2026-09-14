"""說話者分離(目前使用 sherpa-onnx):找出錄音裡每一段是誰在說話,讓逐字稿能標示「說話者 1、2…」。"""

import re

from . import deps, paths

_RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"

SHERPA = deps.Dependency(
    id="sherpa-diarization",
    name="說話者分離",
    purpose="分辨錄音中每一段是誰在說話",
    size_text="約 23 MB",
    url=f"{_RELEASES}/v1.13.8/sherpa-onnx-v1.13.8-win-x64-shared-MT-Release-no-tts.tar.bz2",
    files={
        "sherpa-onnx/sherpa-onnx-offline-speaker-diarization.exe": "bin/sherpa-onnx-offline-speaker-diarization.exe",
        "sherpa-onnx/onnxruntime.dll": "bin/onnxruntime.dll",
        "sherpa-onnx/onnxruntime_providers_shared.dll": "bin/onnxruntime_providers_shared.dll",
    },
    sha256="4b0a94f7b5c606b1b64a19a831c2127559e4b3d34e195465ebc7be73d9ed4783",
)

# 以下兩個模型官方沒有公布 SHA-256,這裡是第一次下載時自行計算的值,用來擋下之後被竄改或損壞的檔案
SEGMENTATION = deps.Dependency(
    id="speaker-segmentation",
    name="人聲分段模型",
    purpose="找出有人說話、換人說話的時間點",
    size_text="約 7 MB",
    url=f"{_RELEASES}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
    files={"pyannote-segmentation-3-0.onnx": "/model.onnx"},
    location="models",
    sha256="24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488",
)

EMBEDDING = deps.Dependency(
    id="speaker-embedding",
    name="聲音特徵模型",
    purpose="比對聲音特徵,判斷是不是同一個人",
    size_text="約 28 MB",
    url=f"{_RELEASES}/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx",
    files={"3dspeaker-campplus-zh.onnx": None},
    location="models",
    sha256="f682b514c05d947ee3fa91cd6ec6c5c7543479a128373fa29b1faedccd21fd11",
)

DEPS = [SHERPA, SEGMENTATION, EMBEDDING]
# 自動判斷人數時的門檻:實測 0.5 會多分、0.8 以上會把不同人併在一起
AUTO_THRESHOLD = 0.7
# 自動模式下,說話時間佔全部不到這個比例的群視為背景雜音,併入主要說話者
MIN_SHARE = 0.05
_SEGMENT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s+--\s+(\d+(?:\.\d+)?)\s+speaker_(\d+)")
_PROGRESS = re.compile(r"progress\s+(\d+(?:\.\d+)?)%")


def diarize(wav_path, speakers=0, cancel=None, progress=None):
    """wav_path 必須是純英文路徑的 16kHz WAV。speakers=0 表示自動判斷人數。
    回傳 [(開始秒, 結束秒, 說話者編號), ...]。"""
    from .transcribe import _run

    segments = []

    def on_line(line):
        match = _SEGMENT.match(line)
        if match:
            segments.append((float(match.group(1)), float(match.group(2)), int(match.group(3))))
        elif progress:
            match = _PROGRESS.search(line)
            if match:
                progress(float(match.group(1)) / 100)

    # 指定人數時多分一群:背景偶爾出聲的人常會搶走一個名額,讓兩位主講者被併成同一群
    clustering = f"--clustering.num-clusters={speakers + 1}" if speakers else \
        f"--clustering.cluster-threshold={AUTO_THRESHOLD}"
    # sherpa-onnx 一樣開不了含中文的路徑,模型用「工作目錄 + 純英文檔名」
    args = [SHERPA.path(), f"--segmentation.pyannote-model={SEGMENTATION.path().name}",
            f"--embedding.model={EMBEDDING.path().name}", "--segmentation.num-threads=2",
            "--embedding.num-threads=2", clustering, wav_path]
    code, log = _run(args, cancel, on_line=on_line, cwd=paths.MODELS_DIR)
    if code != 0 or any("Errors in config" in line for line in log):
        raise RuntimeError("區分說話者失敗")
    return merge_minor(segments, speakers)


def merge_minor(segments, keep=0):
    """把說話時間很短的群(例如背景偶爾出聲的人)併入時間上最接近的主要說話者。
    keep > 0 時只保留說話時間最長的 keep 位;keep = 0(自動)時保留佔總說話時間 MIN_SHARE 以上的。"""
    if not segments:
        return segments
    total = {}
    for start, end, speaker in segments:
        total[speaker] = total.get(speaker, 0) + end - start
    ranked = sorted(total, key=total.get, reverse=True)
    if keep:
        main = set(ranked[:keep])
    else:
        whole = sum(total.values())
        main = {k for k in ranked if total[k] >= whole * MIN_SHARE} or {ranked[0]}
    anchors = [seg for seg in segments if seg[2] in main]

    def distance(anchor, middle):
        return 0 if anchor[0] <= middle <= anchor[1] else min(abs(anchor[0] - middle), abs(anchor[1] - middle))

    merged = []
    for start, end, speaker in segments:
        if speaker not in main:
            middle = (start + end) / 2
            speaker = min(anchors, key=lambda anchor: distance(anchor, middle))[2]
        merged.append((start, end, speaker))
    return merged
