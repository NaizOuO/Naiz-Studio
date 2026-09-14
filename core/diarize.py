"""說話者分離:用聲音特徵模型(sherpa-onnx)判斷每一小段是誰在說話,讓逐字稿能標示「說話者 1、2…」。

做法:依人聲偵測的區間切成約 1.5 秒的小段 → 每段算聲音特徵 → 整份錄音一起分群。
整份一起分群,同一個人在整段錄音中才會一直被歸在同一群(一段一段各自分群時容易 1、2 來回跳)。
"""

import ctypes
import math
import os
import random
import shutil
from operator import mul
from pathlib import Path

from . import deps

_RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"

SHERPA = deps.Dependency(
    id="sherpa-speaker",
    name="說話者分離",
    purpose="分辨錄音中每一段是誰在說話",
    size_text="約 23 MB",
    url=f"{_RELEASES}/v1.13.8/sherpa-onnx-v1.13.8-win-x64-shared-MT-Release-no-tts.tar.bz2",
    files={
        "sherpa-onnx/sherpa-onnx-c-api.dll": "lib/sherpa-onnx-c-api.dll",
        "sherpa-onnx/onnxruntime.dll": "lib/onnxruntime.dll",
        "sherpa-onnx/onnxruntime_providers_shared.dll": "lib/onnxruntime_providers_shared.dll",
    },
    sha256="4b0a94f7b5c606b1b64a19a831c2127559e4b3d34e195465ebc7be73d9ed4783",
)

# 官方沒有公布這個模型的 SHA-256,這裡是第一次下載時自行計算的值,用來擋下之後被竄改或損壞的檔案
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

DEPS = [SHERPA, EMBEDDING]

RATE = 16000
WINDOW, HOP = 1.5, 0.75          # 長的人聲區間切成 1.5 秒、每次移 0.75 秒的小段
WHOLE, MIN_WINDOW = 2.0, 0.4     # 2 秒以內整段算一次;不到 0.4 秒太短,聲音特徵不可靠
MAX_AUTO = 6
AUTO_MARGIN = 0.03               # 自動判斷人數時,分數差不到這麼多就選人數少的,避免多算
MIN_SHARE = 0.05                 # 自動模式下,說話時間佔全部不到這個比例的群視為背景雜音


class _Config(ctypes.Structure):
    _fields_ = [("model", ctypes.c_char_p), ("num_threads", ctypes.c_int32),
                ("debug", ctypes.c_int32), ("provider", ctypes.c_char_p)]


_api = None
_dll_directory = None


def _load():
    global _api, _dll_directory
    if _api is not None:
        return _api
    folder = SHERPA.path().parent
    _dll_directory = os.add_dll_directory(str(folder))   # 讓 C 介面找得到同資料夾的 onnxruntime.dll
    api = ctypes.CDLL(str(folder / "sherpa-onnx-c-api.dll"))
    ptr, stream, floats = ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_float)
    signatures = {
        "SherpaOnnxCreateSpeakerEmbeddingExtractor": (ptr, [ctypes.POINTER(_Config)]),
        "SherpaOnnxDestroySpeakerEmbeddingExtractor": (None, [ptr]),
        "SherpaOnnxSpeakerEmbeddingExtractorDim": (ctypes.c_int32, [ptr]),
        "SherpaOnnxSpeakerEmbeddingExtractorCreateStream": (stream, [ptr]),
        "SherpaOnnxOnlineStreamAcceptWaveform": (None, [stream, ctypes.c_int32, floats, ctypes.c_int32]),
        "SherpaOnnxOnlineStreamInputFinished": (None, [stream]),
        "SherpaOnnxSpeakerEmbeddingExtractorIsReady": (ctypes.c_int32, [ptr, stream]),
        "SherpaOnnxSpeakerEmbeddingExtractorComputeEmbedding": (floats, [ptr, stream]),
        "SherpaOnnxSpeakerEmbeddingExtractorDestroyEmbedding": (None, [floats]),
        "SherpaOnnxDestroyOnlineStream": (None, [stream]),
    }
    for name, (restype, argtypes) in signatures.items():
        function = getattr(api, name)
        function.restype, function.argtypes = restype, argtypes
    _api = api
    return api


def windows(voice):
    """把人聲區間切成要算聲音特徵的小段。"""
    out = []
    for start, end in sorted(voice):
        length = end - start
        if length < MIN_WINDOW:
            continue
        if length <= WHOLE:
            out.append((start, end))
            continue
        t = start
        while t + WINDOW <= end + 1e-6:
            out.append((t, t + WINDOW))
            t += HOP
        if out[-1][1] < end - 0.3:
            out.append((max(start, end - WINDOW), end))
    return out


def _embeddings(raw_path, wins, work_dir, cancel=None, progress=None):
    api = _load()
    model = EMBEDDING.path()
    if not str(model).isascii():
        # 模型在同一個程式裡由 C 介面開啟,開不了含中文的路徑,先複製到純英文的暫存資料夾
        copy = Path(work_dir) / model.name
        shutil.copyfile(model, copy)
        model = copy
    config = _Config(str(model).encode("utf-8"), max(1, min(4, os.cpu_count() or 1)), 0, b"cpu")
    extractor = api.SherpaOnnxCreateSpeakerEmbeddingExtractor(ctypes.byref(config))
    if not extractor:
        raise RuntimeError("無法載入聲音特徵模型")
    raw = Path(raw_path).read_bytes()
    total = len(raw) // 4
    dim = api.SherpaOnnxSpeakerEmbeddingExtractorDim(extractor)
    vectors = []
    try:
        for index, (start, end) in enumerate(wins):
            if cancel is not None and cancel.is_set():
                raise deps.Cancelled()
            a, b = int(start * RATE), min(total, int(end * RATE))
            if b - a < MIN_WINDOW * RATE / 2:
                vectors.append(None)
                continue
            samples = (ctypes.c_float * (b - a)).from_buffer_copy(raw, a * 4)
            stream = api.SherpaOnnxSpeakerEmbeddingExtractorCreateStream(extractor)
            try:
                api.SherpaOnnxOnlineStreamAcceptWaveform(stream, RATE, samples, b - a)
                api.SherpaOnnxOnlineStreamInputFinished(stream)
                if api.SherpaOnnxSpeakerEmbeddingExtractorIsReady(extractor, stream):
                    result = api.SherpaOnnxSpeakerEmbeddingExtractorComputeEmbedding(extractor, stream)
                    vector = result[:dim]
                    api.SherpaOnnxSpeakerEmbeddingExtractorDestroyEmbedding(result)
                    norm = math.sqrt(sum(map(mul, vector, vector))) or 1.0
                    vectors.append([x / norm for x in vector])
                else:
                    vectors.append(None)
            finally:
                api.SherpaOnnxDestroyOnlineStream(stream)
            if progress and index % 25 == 0:
                progress(index / max(1, len(wins)))
    finally:
        api.SherpaOnnxDestroySpeakerEmbeddingExtractor(extractor)
    return vectors


def _kmeans(vectors, k, seed):
    """以方向相似度分 k 群(聲音特徵已正規化);回傳 (每段的群編號, 分數)。"""
    rng = random.Random(seed)
    centers = [vectors[rng.randrange(len(vectors))]]
    while len(centers) < k:
        # k-means++:離現有中心越遠的越容易被選為新中心
        far = [max(0.0, 1 - max(sum(map(mul, v, c)) for c in centers)) for v in vectors]
        pick, acc = rng.random() * (sum(far) or 1.0), 0.0
        for v, d in zip(vectors, far):
            acc += d
            if acc >= pick:
                centers.append(v)
                break
        else:
            centers.append(vectors[rng.randrange(len(vectors))])
    labels = None
    for _ in range(30):
        new = [max(range(k), key=lambda j, v=v: sum(map(mul, v, centers[j]))) for v in vectors]
        if new == labels:
            break
        labels = new
        for j in range(k):
            members = [v for v, label in zip(vectors, labels) if label == j]
            if members:
                center = [sum(column) for column in zip(*members)]
                norm = math.sqrt(sum(map(mul, center, center))) or 1.0
                centers[j] = [x / norm for x in center]
    score = sum(sum(map(mul, v, centers[label])) for v, label in zip(vectors, labels))
    return labels, score


def _best_kmeans(vectors, k, tries=3):
    return max((_kmeans(vectors, k, seed) for seed in range(tries)), key=lambda result: result[1])[0]


def _silhouette(similarity, sample, labels, k):
    """抽樣計算輪廓係數:越接近 1 代表每群內部越像、群與群之間越不像。"""
    scores = []
    for row, i in zip(similarity, sample):
        sums, counts = [0.0] * k, [0] * k
        for d, j in zip(row, sample):
            if j != i:
                sums[labels[j]] += 1 - d
                counts[labels[j]] += 1
        own = labels[i]
        others = [sums[c] / counts[c] for c in range(k) if c != own and counts[c]]
        if not counts[own] or not others:
            scores.append(0.0)
            continue
        a, b = sums[own] / counts[own], min(others)
        scores.append((b - a) / max(a, b) if max(a, b) > 0 else 0.0)
    return sum(scores) / len(scores)


def cluster(vectors, speakers=0):
    """speakers > 0 時分成指定人數;0 時在 2~6 人之間挑分得最清楚的人數。"""
    n = len(vectors)
    if n == 0:
        return []
    if speakers:
        return _best_kmeans(vectors, min(speakers, n))
    if n < 4:
        return [0] * n
    sample = random.Random(0).sample(range(n), min(300, n))
    similarity = [[sum(map(mul, vectors[i], vectors[j])) for j in sample] for i in sample]
    candidates = []
    for k in range(2, min(MAX_AUTO, n - 1) + 1):
        labels = _best_kmeans(vectors, k)
        candidates.append((k, _silhouette(similarity, sample, labels, k), labels))
    best = max(score for _, score, _ in candidates)
    return next(labels for _, score, labels in candidates if score >= best - AUTO_MARGIN)


def _to_segments(wins, labels, voice):
    """重疊的小段轉成不重疊的分段:同一段人聲裡,以相鄰小段中心的中點為界。"""
    segments = []
    for start, end in sorted(voice):
        inside = sorted(((a + b) / 2, label) for (a, b), label in zip(wins, labels)
                        if a >= start - 0.01 and b <= end + 0.01)
        for i, (center, label) in enumerate(inside):
            left = start if i == 0 else (inside[i - 1][0] + center) / 2
            right = end if i == len(inside) - 1 else (center + inside[i + 1][0]) / 2
            segments.append((left, right, label))
    return segments


def diarize(raw_path, voice, speakers=0, cancel=None, progress=None, work_dir=None):
    """raw_path:16kHz 單聲道 float32 原始音訊;voice:人聲區間 [(開始秒, 結束秒)]。
    speakers=0 表示自動判斷人數。回傳 [(開始秒, 結束秒, 說話者編號), ...]。"""
    wins = windows(voice)
    vectors = _embeddings(raw_path, wins, work_dir or Path(raw_path).parent, cancel, progress)
    kept = [(win, vector) for win, vector in zip(wins, vectors) if vector is not None]
    if not kept:
        return []
    labels = cluster([vector for _, vector in kept], speakers)
    segments = _to_segments([win for win, _ in kept], labels, voice)
    return segments if speakers else merge_minor(segments, 0)


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
