"""逐字稿的辨識模型:和即時字幕共用模型檔(已經下載過就不用再下載)。都用 whisper.cpp 整個檔案一次辨識,
有每個字的時間(字幕切細時每一段都對得上聲音、一句裡換人切得開)。

- 中文(台灣)不會輸出標點:辨識完先在每個字後面補上標點模型判斷的標點,再切成字幕條(標點是切字幕的依據)
- 不放「中文(通話)」:沒有每個字的時間,整檔辨識時錯字也沒有比較少(實驗 E10),時間軸對不準
"""

from core import deps, diarize, transcribe

from plugins.subtitle import asr, llm, punct

# (代號, 名稱, 說明)
MODELS = [
    ("base", "快速", "檔案最小、速度最快，但錯字較多，適合先大概看內容"),
    ("small", "輕量", "沒有獨立顯示卡也跑得動，比快速準"),
    ("turbo", "推薦", "準確又快，大多數情況選這個"),
    ("large", "最準確", "錯字最少，但速度約慢 3 倍、檔案約 3GB"),
    ("breeze", "中文（台灣）", "台灣口語、中英混用錯字較少，用標點模型補標點；只辨識中文，需要 NVIDIA 顯示卡，速度約推薦的一半"),
]
NAMES = {key: name for key, name, _ in MODELS}
NOTES = {key: note for key, _, note in MODELS}
ZH_ONLY = {"breeze"}
LANGUAGES = asr.LANGUAGES
_ENDS = "，。？！、,.?!…：；;:"


def model_file(key):
    if key == "small":
        return asr.SMALL
    if key == "breeze":
        return asr.BREEZE
    return transcribe.MODELS[key]


def required(key, speakers=None, refine=False):
    """要的所有元件(呼叫端篩出還沒裝的交給同意視窗);同一個元件只列一次。refine:更精確(精修)要 Qwen3-ASR。"""
    items = [deps.FFMPEG, transcribe.engine(), model_file(key), transcribe.VAD]
    if refine:
        items += [llm.ENGINE, asr.QWEN]
    if key == "breeze":
        items.append(punct.PUNCT)
    if speakers is not None:
        items += diarize.DEPS
    return list({dep.id: dep for dep in items}.values())


def recognizer(key, language="zh"):
    return transcribe.whisper_recognizer(model_file(key), transcribe.MODEL_DTW[key],
                                         "zh" if key in ZH_ONLY else language)


def preparer(key):
    """切成字幕條之前整理每個字:中文(台灣)補標點。"""
    if key != "breeze" or not punct.available():
        return None
    return punctuate_lines


def punctuate_lines(lines):
    """每個字後面補上標點模型判斷的標點(整份一起判斷,前後文較準);原本就有標點的不重複加。"""
    owners, units = [], []
    for li, line in enumerate(lines):
        for wi, word in enumerate(line["words"]):
            piece = word[1]
            for unit in punct._units(piece):
                owners.append((li, wi))
                units.append(unit)
    if not units:
        return lines
    marks = punct.marks(units)
    words = [list(line["words"]) for line in lines]
    for (li, wi), mark in zip(owners, marks):
        if not mark:
            continue
        word = words[li][wi]
        if word[1].rstrip()[-1:] not in _ENDS:
            words[li][wi] = (word[0], word[1].rstrip() + mark, *word[2:])
    return [dict(line, words=w, text="".join(word[1] for word in w)) for line, w in zip(lines, words)]
