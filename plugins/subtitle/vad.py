"""人聲偵測(Silero VAD,MIT 授權,約 2 MB):判斷每一小段聲音有沒有人在說話。
沒人說話時不送去辨識:Whisper 對著靜音或音樂常會冒出「Thank you.」之類不存在的句子;
停頓也用來判斷一句話講完了。用 onnxruntime 執行(和圖片去背共用同一個下載的元件,只用處理器,很輕)。"""

import threading

import numpy

from core import deps

from ..images import cutout

MODEL = deps.Dependency(
    id="silero-vad",
    name="人聲偵測模型（字幕）",
    purpose="判斷有沒有人在說話，沒人說話時不送去辨識，避免出現不存在的字幕",
    size_text="約 2 MB",
    url="https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.3/src/silero_vad/data/silero_vad.onnx",
    files={"silero_vad.onnx": None},
    location="models",
    sha256="1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
)
DEPS = [cutout.ENGINE, MODEL]
FRAME = 512                 # 每次判斷 32 毫秒(16kHz)
CONTEXT = 64                # 模型要接在前一段最後 64 個取樣後面
RATE = 16000


def required():
    return [dep for dep in DEPS if not dep.installed()]


class Vad:
    """一段一段餵進 16 位元 PCM,回傳每 32 毫秒有人說話的機率(0～1)。"""

    def __init__(self):
        ort = cutout._runtime()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1            # 很小的模型:一條執行緒最快,也不跟辨識搶處理器
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(MODEL.path()), options, providers=["CPUExecutionProvider"])
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        self._state = numpy.zeros((2, 1, 128), numpy.float32)
        self._context = numpy.zeros((1, CONTEXT), numpy.float32)
        self._rest = numpy.zeros(0, numpy.float32)

    def feed(self, pcm):
        samples = numpy.frombuffer(pcm, numpy.int16).astype(numpy.float32) / 32768
        with self._lock:
            samples = numpy.concatenate([self._rest, samples])
            count = len(samples) // FRAME
            self._rest = samples[count * FRAME:]
            probabilities = []
            for index in range(count):
                frame = samples[index * FRAME:(index + 1) * FRAME][None]
                inputs = {"input": numpy.concatenate([self._context, frame], axis=1), "state": self._state,
                          "sr": numpy.array(RATE, numpy.int64)}
                output, self._state = self.session.run(None, inputs)
                self._context = frame[:, -CONTEXT:]
                probabilities.append(float(output[0, 0]))
            return probabilities
