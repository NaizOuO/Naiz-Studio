"""中文標點模型(CT-Transformer,FunASR 的中英文標點模型,sherpa-onnx 轉成 int8 的版本,約 65 MB):
給不會輸出標點的辨識模型(中文(台灣),Breeze ASR 25)補上，。？、。用處理器執行(和人聲偵測共用執行元件),一句約幾毫秒。

2026-10-08 盲測(另一個 AI 評「意思對不對、讀不讀得懂」,4 個版本排名,1 最好、4 最差):
Breeze 用停頓猜標點平均第 3.1 名(最差);同樣的字改用這個模型補標點升到第 2.1～2.2 名。"""

import threading

import numpy

from core import deps

PUNCT = deps.Dependency(
    id="punct-zh",
    name="中文標點模型",
    purpose="幫「中文（台灣）」辨識結果補上標點（逗號、句號、問號），字幕比較好讀",
    size_text="約 65 MB",
    url="https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/"
        "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8.tar.bz2",
    files={"punct-zh/model.int8.onnx": "model.int8.onnx"},
    location="models",
    sha256="c0d5aa5f8eeb686032345e180bedf39319dc2e0556781c6264bcadba8328a6e1",
    install_size=75_000_000,
)
MARKS = "，。？、"
_ENDS = "，。？！、,.?!…：；"
LIMIT = 200                     # 模型一次最多看幾個單位(字、英文詞)

_session = None
_lock = threading.Lock()


def _load():
    global _session
    with _lock:
        if _session is None:
            from plugins.images import cutout

            ort = cutout._runtime()
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            session = ort.InferenceSession(str(PUNCT.path()), options, providers=["CPUExecutionProvider"])
            meta = session.get_modelmeta().custom_metadata_map
            tokens = meta["tokens"].split("|")
            _session = (session, meta["punctuations"].split("|"), {t: i for i, t in enumerate(tokens)},
                        tokens.index(meta["unk_symbol"]) if meta["unk_symbol"] in tokens else 0)
        return _session


def available():
    return PUNCT.installed()


def _units(text):
    """中文一個字一個單位、英文和數字一個詞一個單位:[(原文, 查表用)];原本的標點和空白不算。
    模型是用簡體中文訓練的:查表時轉成簡體(顯示的還是原文)。"""
    from core import transcribe

    simple = transcribe.convert_script(text, "cn")
    if len(simple) != len(text):
        simple = text
    units, word = [], ""
    for original, ch in zip(text, simple):
        if ch.isascii() and (ch.isalnum() or ch in "'-"):
            word += original
            continue
        if word:
            units.append((word, word.lower()))
            word = ""
        if ch.strip() and not ch.isascii() and ch not in _ENDS:
            units.append((original, ch))
    if word:
        units.append((word, word.lower()))
    return units


def marks(units):
    """每個單位後面要加的標點("" 是不加)。"""
    session, punct, token2id, unk = _load()
    ids = [token2id.get(key, unk) for _, key in units]
    out = []
    for start in range(0, len(ids), LIMIT):
        part = ids[start:start + LIMIT]
        logits = session.run([session.get_outputs()[0].name], {
            session.get_inputs()[0].name: numpy.array(part, dtype=numpy.int32).reshape(1, -1),
            session.get_inputs()[1].name: numpy.array([len(part)], dtype=numpy.int32)})[0][0]
        out += [punct[i] if punct[i] in MARKS else "" for i in logits.argmax(axis=-1)]
    return out


def _join(pieces):
    """把單位接回文字:英文詞之間留空白。"""
    text = ""
    for piece in pieces:
        if text and piece[:1].isascii() and piece[:1].isalnum() and text[-1].isascii() and text[-1].isalnum():
            text += " "
        text += piece
    return text


def punctuate_segments(segments, finished=False):
    """[(開始, 結束, 文字)] → 同樣的分段、文字加上標點(整段一起判斷,前後文較準)。
    finished=False 時最後一個標點不加(這句可能還沒講完;問號例外)。"""
    texts = [text.strip() for _, _, text in segments]
    units_of = [_units(text) for text in texts]
    flat = [unit for units in units_of for unit in units]
    if not flat:
        return segments
    found = marks(flat)
    out, index = [], 0
    last_segment = max((i for i, units in enumerate(units_of) if units), default=-1)
    for number, ((start, end, _), units) in enumerate(zip(segments, units_of)):
        pieces = []
        for k, (piece, _) in enumerate(units):
            mark = found[index]
            index += 1
            is_last = number == last_segment and k == len(units) - 1
            if mark and (not is_last or finished or mark == "？"):
                piece += mark
            pieces.append(piece)
        out.append((start, end, _join(pieces)))
    return out


def punctuate(text, finished=True):
    return punctuate_segments([(0.0, 0.0, text)], finished)[0][2]
