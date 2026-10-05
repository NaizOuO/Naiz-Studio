"""字幕翻譯(本地執行,不會上傳)。邊翻邊把字傳回來,字幕上的翻譯會一個字一個字長出來。
有安裝 Ollama 就用 Ollama(v1.18.0);沒有的話用內建的 llama.cpp 引擎(v1.18.4,見 llm.py),模型從網路下載。
這裡的函式會自己判斷用哪一個,呼叫的地方不用分。

實測(RTX 5060 Ti;只用處理器時是 20 執行緒的 CPU):
- qwen3:8b(預設)每句 0.2～0.4 秒;qwen3:14b 品質最好 0.4～0.8 秒
- TranslateGemma 4B(Google 的翻譯專用模型)品質接近 8b,只用處理器每句 2～3 秒,弱電腦用它
- qwen3:4b 目前是「一定會思考」的版本,會把思考過程當成翻譯,不推薦;qwen3:1.7b 日文常整句沒翻
- 第一次載入 3～45 秒:按「開始」時就先載好
"""

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from core import transcribe

from . import llm
from .glossary import relevant as names_in

DEFAULT_MODEL = "qwen3:8b"
# 目標語言:(代號, 名稱, 給模型看的名稱, TranslateGemma 的語言代號)
TARGETS = [("zh-TW", "台灣繁體中文", "Traditional Chinese (Taiwan)", "zh-TW"),
           ("zh-CN", "簡體中文", "Simplified Chinese", "zh-Hans"),
           ("en", "英文", "English", "en"), ("ja", "日文", "Japanese", "ja"), ("ko", "韓文", "Korean", "ko")]
TARGET_NAMES = {key: name for key, name, _, _ in TARGETS}
SOURCE_NAMES = {"en": "English", "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "es": "Spanish", "fr": "French",
                "de": "German", "ru": "Russian", "th": "Thai", "vi": "Vietnamese"}
# 實測翻譯很差的模型(名稱含這些字):模型清單上標「不建議」並寫出原因,選了會提醒
NOT_RECOMMENDED = {"llama3-taide": "作者實測拉爆了，完全不行拿來翻譯（為台灣的中文模型，不擅長翻譯）",
                   "llama-3-taiwan": "實測日文常翻錯，意思和原文不同"}


def not_recommended(name):
    """實測很差的模型回傳原因,其他回傳空字串。"""
    return next((reason for key, reason in NOT_RECOMMENDED.items() if key in name), "")


# 建議下載的模型:(名稱, 大小, 大約需要的顯示卡記憶體 GB, 說明);依電腦配備標出建議。
# 需要的記憶體 = 模型檔案加上翻譯時的暫存;不夠時 Ollama 會把一部分放到一般記憶體,可以用但會慢很多
SUGGESTED = [
    ("translategemma:4b", "3.3 GB", 4, "Google 的翻譯專用模型；沒有獨立顯示卡也能用，只用處理器每句約 2～3 秒"),
    ("qwen3:8b", "5.2 GB", 7, "速度和品質平衡，有顯示卡的電腦大多選這個；每句約 0.2～0.4 秒"),
    ("translategemma:12b", "8.1 GB", 10, "翻譯專用的中型版本，比 4b 自然"),
    ("qwen3:14b", "9.3 GB", 11, "實測品質最好，速度還跟得上字幕；每句約 0.4～0.8 秒"),
    ("translategemma:27b", "17 GB", 20, "翻譯專用的最大版本，用字最講究"),
    ("qwen3:30b", "19 GB", 22, "「混合專家」架構，每次只動用一小部分，所以同大小裡最快"),
    ("qwen3:32b", "20 GB", 24, "最大最準確，但也最慢；講話快的時候字幕可能跟不上"),
]
HIGH_END = 16       # 需要這麼多顯示卡記憶體以上的模型標「需要高階電腦」
DENIED_RETRIES = 30  # Windows 偶爾不給開新的本地連線(WinError 10013,實測 3～18 秒):每秒再試一次,最多約 30 秒
_THINK = re.compile(r"<think>.*?(</think>|$)", re.S)
# 模型多給的「其他翻法」從這裡開始整段不要(translategemma:12b 常在後面加「或者：」)
_ALTERNATIVE = re.compile(r"\n\s*(?:或者|或是|也可以|另一種|又或|(?:Or|Alternatively|Another option)\s*[:：,，]).*",
                          re.S | re.I)
_NOTE = re.compile(r"[（(]\s*(註|注|直譯|意思|意譯|Note|Literally)[^）)]*[）)]", re.I)
# 模型自己解釋沒翻的外文字:「（ definitely 是個強調詞，用來加強前面的說法…）」
_EXPLAIN = re.compile(r"\s*[（(]\s*[A-Za-z][^）)]{0,40}?(是|指|表示)[^）)]*(詞|用法|意思|說法|語氣)[^）)]*[）)]")
_PAIRS = {"「": "」", "『": "』", "\"": "\"", "“": "”", "'": "'"}


def suggested():
    """建議清單;用內建引擎時大小換成實際下載的模型檔大小(和 Ollama 的不同)。"""
    if not builtin():
        return list(SUGGESTED)
    return [(name, llm.size_text(llm.CATALOG[name][1]) if name in llm.CATALOG else size, vram, note)
            for name, size, vram, note in SUGGESTED]


_detected = [-1e9, False]       # (上次檢查的時間, 是否用內建引擎)


def builtin():
    """沒有安裝 Ollama 時用內建引擎。開發測試時設環境變數 NAIZ_NO_OLLAMA=1 可以當作沒有 Ollama。"""
    if os.environ.get("NAIZ_NO_OLLAMA"):
        return True
    now = time.monotonic()
    if now - _detected[0] > 5:
        _detected[:] = [now, app_path() is None and not _ollama_running()]
    return _detected[1]


def missing(name):
    """用這個模型前還要下載的元件(只有內建引擎會有:引擎本身、模型檔)。"""
    return llm.missing(name) if builtin() else []


def host():
    """Ollama 的位址;使用者改過 OLLAMA_HOST 時照著用。"""
    value = os.environ.get("OLLAMA_HOST", "").strip() or "127.0.0.1:11434"
    if value.startswith("0.0.0.0"):
        value = "127.0.0.1" + value[7:]
    return value if value.startswith("http") else f"http://{value}"


def _get(path, timeout=3):
    with urllib.request.urlopen(host() + path, timeout=timeout) as response:
        return json.load(response)


def _post(path, payload, timeout=30):
    request = urllib.request.Request(host() + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    for attempt in range(DENIED_RETRIES + 1):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.URLError as exc:
            # 實測每 5～20 分鐘會有 3～18 秒所有新的本地連線都被擋(不只 Ollama):稍等再送,
            # 不然那幾秒的句子會翻譯失敗。Ollama 沒開(連線被拒)等其他錯誤照舊馬上回報
            denied = isinstance(exc.reason, PermissionError) and not isinstance(exc, urllib.error.HTTPError)
            if not denied or attempt == DENIED_RETRIES:
                raise
            time.sleep(1.0)


def running():
    """翻譯可以用了嗎:用 Ollama 時要它有在執行;內建引擎要用時才啟動,一直算可以用。"""
    return True if builtin() else _ollama_running()


def _ollama_running():
    try:
        _get("/api/version", timeout=1.5)
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def app_path():
    """有安裝但沒在執行時,用這個把它打開。"""
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama"
    for name in ("ollama app.exe", "ollama.exe"):
        if (base / name).is_file():
            return base / name
    return None


def launch():
    """打開 Ollama(在背景執行,不會跳出視窗)。"""
    path = app_path()
    if path is None:
        return False
    args = [str(path)] if path.name == "ollama app.exe" else [str(path), "serve"]
    subprocess.Popen(args, creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def models():
    """已安裝的模型:[(名稱, 大小 bytes)],翻譯不能用的(例如向量模型)不列。"""
    if builtin():
        return llm.models()
    result = []
    for model in _get("/api/tags").get("models", []):
        name = model.get("name", "")
        if "embed" in name:
            continue
        result.append((name, model.get("size", 0)))
    return sorted(result, key=lambda item: item[1])


def pull(name, progress=None, cancel=None):
    """下載模型(內建引擎第一次還會下載引擎);progress(已下載, 全部)。"""
    if builtin():
        return llm.pull(name, progress, cancel)
    with _post("/api/pull", {"model": name, "stream": True}, timeout=60) as response:
        for line in response:
            if cancel is not None and cancel.is_set():
                return False
            data = json.loads(line)
            if data.get("error"):
                raise RuntimeError(data["error"])
            if progress and data.get("total"):
                progress(data.get("completed", 0), data["total"])
            if data.get("status") == "success":
                return True
    return True


def preload(name, cancel=None):
    """先把模型載入顯示卡(第一次 3～45 秒),開始字幕後才不會卡一下。cancel():內建引擎載入中可以中途停止。"""
    if builtin():
        llm.preload(name, cancel)
        return
    with _post("/api/generate", {"model": name, "keep_alive": "30m", "prompt": ""}, timeout=300) as response:
        response.read()


def delete(name):
    """刪掉模型(空出硬碟空間)。"""
    if builtin():
        llm.delete(name)
        return
    request = urllib.request.Request(host() + "/api/delete", data=json.dumps({"model": name}).encode(),
                                     headers={"Content-Type": "application/json"}, method="DELETE")
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def unload(name):
    """停止字幕時把模型從顯示卡移掉,讓出記憶體給遊戲。"""
    if builtin():
        llm.unload(name)
        return
    try:
        with _post("/api/generate", {"model": name, "keep_alive": 0}, timeout=10) as response:
            response.read()
    except (urllib.error.URLError, OSError):
        pass


def _messages(model, text, source, target, context, strict=False, names=()):
    """context:前面幾句 [(原文, 譯文)],讓人名、稱呼沿用前面的翻法。strict:上次沒翻完(留著原文),這次特別交代。
    names:專有名詞表裡這句用得到的 [(原文, 譯名)]。"""
    target_name, gemma_code = next((t[2], t[3]) for t in TARGETS if t[0] == target)
    if "translategemma" in model:
        # TranslateGemma 要用官方的提示格式(要翻的文字前面空兩行)
        name = SOURCE_NAMES.get(source, "the original")
        code = source if source in SOURCE_NAMES else "auto"
        prompt = (f"You are a professional {name} ({code}) to {target_name} ({gemma_code}) translator. Your goal is to "
                  f"accurately convey the meaning and nuances of the original {name} text while adhering to "
                  f"{target_name} grammar, vocabulary, and cultural sensitivities.\n"
                  f"Produce only the {target_name} translation, without any additional explanations or commentary. "
                  f"Please translate the following {name} text into {target_name}:\n\n\n{text}")
        return [{"role": "user", "content": prompt}]
    system = (f"你是即時字幕翻譯。把使用者給的句子翻成{TARGET_NAMES[target]}"
              + ("，用台灣慣用的說法" if target == "zh-TW" else "")
              + "。只輸出翻譯，不要解釋、不要加引號；句子沒講完就照字面翻已有的部分。")
    # 不另外交代語助詞、結巴:實測 qwen3:8b 不交代時本來就會照樣翻(「えーと、ぼ、僕は」→「呃，我、我」),
    # 交代了反而到處加「啦」、改用「您」,翻譯變差
    if strict:
        system += f"每個字都要翻成{TARGET_NAMES[target]}，不能留下原文（例如日文的假名）；人名用音譯。"
    if names:
        system += "固定譯名（一定要照這樣翻）：" + "、".join(f"{s}→{t}" for s, t in names) + "。"
    messages = [{"role": "system", "content": system + "人名、稱呼沿用前面翻過的譯名。"}]
    # 前面幾句當成之前的問答(寫成「原文 → 譯文」放進說明的話,模型有時會照抄原文)
    for original, translation in context[-3:]:
        if translation:
            messages += [{"role": "user", "content": original}, {"role": "assistant", "content": translation}]
    return messages + [{"role": "user", "content": text}]


def _unquote(text, source):
    """整句被引號包起來(原文沒有)才拿掉;句子中間的引號(原文本來就有)保留,不會只拿掉一邊。"""
    for _ in range(2):
        if len(text) < 2 or text[0] not in _PAIRS or text[-1] != _PAIRS[text[0]]:
            break
        inner = text[1:-1]
        if text[0] in source or text[0] in inner or _PAIRS[text[0]] in inner:
            break
        text = inner.strip()
    return text


def clean(text, target, source=""):
    """去掉思考內容、「/no_think」、其他翻法、註解、多餘的引號與換行;台灣繁體再轉一次用字(小模型偶爾混進簡體字)。
    source 是原文:原文有引號、括號時就不拿掉。"""
    text = _THINK.sub("", text).replace("/no_think", "").replace("／no_think", "").strip()
    text = re.sub(r"^(翻譯|譯文|Translation)\s*[:：]\s*", "", text)
    text = _ALTERNATIVE.sub("", text)
    if "(" not in source and "（" not in source:
        text = _EXPLAIN.sub("", _NOTE.sub("", text))
    rows = [row.strip() for row in text.splitlines() if row.strip()]
    if "(" not in source and "（" not in source:
        # 整行都是括號補充(例如「（神奈川縣）」)的不要
        rows = [row for row in rows if not (row[0] in "（(" and row[-1] in "）)")] or rows
    joiner = "" if target.startswith(("zh", "ja")) else " "
    text = joiner.join(rows)
    text = _unquote(text, source)
    if target == "zh-TW":
        text = transcribe.convert_script(text, "tw")
    elif target == "zh-CN":
        text = transcribe.convert_script(text, "cn")
    return text.strip()


def _continuation(prefix, output):
    """接著 prefix 翻出來的後半段;回傳 (完整翻譯, 是不是從頭重翻)。
    qwen3 偶爾會先吐「</think>」或換個說法從頭重翻:這時整段當成新的翻譯,不接在 prefix 後面(否則會重複)。"""
    if "</think>" in output:
        output = output.split("</think>")[-1]
    output = output.lstrip("\n ")
    core = prefix.strip()
    if not core:
        return output, False
    if core in output:
        return prefix + output[output.rfind(core) + len(core):], False
    head = core[:3]
    if len(output) >= 3 and output.startswith(head):
        return output, True
    return prefix + output, False


def _apply_names(text, names):
    """模型還是照抄了名詞的原文(例如留著「ニア」):直接換成譯名。"""
    for source, target in names:
        if target and source in text:
            text = text.replace(source, target)
    return text


def translate(model, text, source, target, context=(), on_text=None, cancel=None, prefix="", strict=False,
              glossary=()):
    """翻譯一句;on_text(到目前為止的翻譯) 會隨著字出來一直被呼叫。回傳最後的翻譯。
    prefix:已經顯示、要固定不變的翻譯開頭,模型從它後面接著翻(畫面上只有後面的字會變)。
    glossary:專有名詞表 [(原文, 譯名)],只會用到這句和前文出現的。"""
    names = [pair for pair in names_in(glossary, text, *(c[0] for c in context)) if pair[1]]
    messages = _messages(model, text, source, target, context, strict, names)
    if prefix:
        messages.append({"role": "assistant", "content": prefix})
    # 最多輸出的長度:翻譯不會比原文長太多,限制住才不會一路寫解釋、寫好幾種翻法
    limit = 48 + len(text) * 3

    def finish(output):
        return _apply_names(clean(_continuation(prefix, output)[0], target, text), names)

    def show(output):
        if on_text and "<think>" not in output:
            on_text(finish(output))

    if builtin():
        return finish(llm.chat(model, messages, limit, on_delta=show, cancel=cancel))
    payload = {"model": model, "stream": True, "think": False, "keep_alive": "30m",
               "options": {"temperature": 0.2, "num_predict": limit}, "messages": messages}
    output = ""
    with _post("/api/chat", payload, timeout=120) as response:
        for line in response:
            if cancel is not None and cancel():
                break
            data = json.loads(line)
            if data.get("error"):
                raise RuntimeError(data["error"])
            output += data.get("message", {}).get("content", "")
            show(output)
            if data.get("done"):
                break
    return finish(output)
