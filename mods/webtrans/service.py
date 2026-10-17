"""網頁翻譯的本機服務:瀏覽器擴充功能把選到的文字送來,用本地翻譯模型翻好送回去;「記下來」存成紀錄。

- 只聽本機(127.0.0.1),而且只接受瀏覽器擴充功能的請求(Origin 是 chrome-extension://):
  一般網頁發出的請求瀏覽器一定會帶上自己的網址,沒辦法假冒,所以網頁不能偷用這個服務
- 翻譯模型、目標語言沿用即時字幕的設定(內建引擎或 Ollama)
- 選到的是單字、片語:說明在這句裡的意思、詞性(不給讀音:小模型常編錯);是句子或一段:整段翻譯
- 查過、記下來的字:直接回傳之前的結果,次數加 1(不用再翻)
"""

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core import mods, paths, theme

PORT = 47821
TOOL_ID = "webtrans"
WORD_LIMIT = 4              # 英文幾個字以內算片語(查字典的樣子)
CJK_WORD_LIMIT = 8          # 中日文幾個字以內算詞
_lock = threading.Lock()    # 紀錄檔一次只讓一個人寫
_translate_lock = threading.Lock()
state = {"server": None, "error": "", "busy": False, "last": ""}


# ------------------------------------------------------------ 紀錄

def records_path():
    return mods.data_dir(TOOL_ID) / "records.json"


def load_records():
    try:
        return json.loads(records_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_records(records):
    path = records_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    temp.replace(path)


def key(text):
    return re.sub(r"\s+", " ", text.strip()).lower()


def find(records, text):
    k = key(text)
    return next((r for r in records if key(r["text"]) == k), None)


def remember(item):
    """記下來;已經有的話更新翻譯和例句。回傳紀錄。"""
    with _lock:
        records = load_records()
        found = find(records, item["text"])
        now = time.strftime("%Y-%m-%d %H:%M")
        if found is None:
            found = {"text": item["text"].strip(), "count": 1, "created": now}
            records.append(found)
        for field in ("kind", "translation", "meaning", "pos", "reading", "context", "url", "title"):
            if item.get(field):
                found[field] = item[field]
        found["updated"] = now
        save_records(records)
        return found


def bump(text):
    """又查了一次已經記下來的字:次數加 1。回傳紀錄(沒記過是 None)。"""
    with _lock:
        records = load_records()
        found = find(records, text)
        if found is not None:
            found["count"] = found.get("count", 1) + 1
            found["updated"] = time.strftime("%Y-%m-%d %H:%M")
            save_records(records)
        return found


# ------------------------------------------------------------ 翻譯

def settings():
    """(翻譯模型, 目標語言):沿用即時字幕的設定。"""
    from plugins.subtitle import translate

    stored = theme.load_config(str(paths.SETTING_DIR)).get("subtitle") or {}
    return stored.get("translator") or translate.DEFAULT_MODEL, stored.get("target") or "zh-TW"


MARKER = re.compile(r"^\s*([•●○■◆▪‣∙·\-–*]|\(?\d{1,2}[.)]|\(?[a-zA-Z][.)]|[A-Z]\d{0,2}:|Note:)\s*")
SENTENCE_END = re.compile(r"[.?!:;。？！：；)）]$")


def blocks(text):
    """選到一大段時照原本的排版切開:清單項目、編號、段落各自翻譯,換行和「1.」「•」留著。
    PDF 每一行都會斷行,前一行沒有句尾符號就當成同一段接起來。回傳 [(開頭記號, 內容)]。"""
    result = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        found = MARKER.match(line)
        marker, body = (found.group(0).strip() + " ", line[found.end():]) if found else ("", line)
        if result and not found and not SENTENCE_END.search(result[-1][1]):
            previous = result[-1][1]
            joiner = "" if re.search(r"[぀-ヿ㐀-鿿-]$", previous) else " "
            result[-1] = (result[-1][0], previous + joiner + body)
        else:
            result.append((marker, body))
    return result


def is_word(text):
    text = text.strip()
    if re.search(r"[。？！.?!;；]\s*\S", text) or "\n" in text:
        return False
    cjk = len(re.findall(r"[぀-ヿ㐀-鿿]", text))
    if cjk:
        return cjk <= CJK_WORD_LIMIT
    return len(text.split()) <= WORD_LIMIT


def _chat(model, messages, limit):
    """問模型一次(內建引擎或 Ollama),回傳文字。"""
    from plugins.subtitle import llm, translate

    if translate.builtin():
        return llm.chat(model, messages, limit)
    import urllib.request

    payload = {"model": model, "stream": False, "think": False, "keep_alive": "30m",
               "options": {"temperature": 0.2, "num_predict": limit}, "messages": messages}
    request = urllib.request.Request(translate.host() + "/api/chat", json.dumps(payload).encode(),
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)["message"]["content"]


def _word(model, text, context, target_name):
    """單字、片語:在這句話裡的意思、詞性。"""
    from plugins.subtitle import translate

    if "translategemma" in model:           # 翻譯專用模型不會照格式回答:只翻這個字
        return {"meaning": translate.translate(model, text, "auto", settings()[1])}
    # 不問讀音:實測本地小模型的音標、假名常常編錯(convolution 給成 /tənˈvaɪ.ʃən/、勾配給成かくはい),錯的比沒有更糟
    prompt = (f"說明「{text}」在下面這句話裡的意思，用{target_name}回答。\n句子：{context or text}\n"
              "只輸出兩行，格式：\n"
              "意思：（先寫最貼切的翻譯詞，再用逗號接一句很短的補充，例如「卷積，把濾波器套在影像每個區域上的運算」）\n"
              "詞性：（只寫一個，例如 名詞、動詞、形容詞；兩個字以上的片語寫 片語）")
    output = _chat(model, [{"role": "user", "content": prompt + (" /no_think" if "qwen3" in model else "")}], 200)
    output = translate.clean(re.sub(r"<think>.*?</think>", "", output, flags=re.S), settings()[1], prompt)
    result = {}
    for label, field in (("意思", "meaning"), ("詞性", "pos")):
        match = re.search(rf"{label}\s*[:：]\s*(.+?)(?=(意思|詞性|讀音)\s*[:：]|$)", output, re.S)
        if match and match.group(1).strip(" -"):
            result[field] = match.group(1).strip()
    if not result.get("meaning"):
        result["meaning"] = output.strip()
    return result


def translate_text(text, context="", page=False):
    """翻譯選到的文字:回傳給擴充功能的結果。page:整頁翻譯的一段,不查紀錄、不算次數、不當單字解釋。"""
    from plugins.subtitle import translate

    text = text.strip()
    if not text:
        raise ValueError("沒有選到文字")
    if len(text) > 4000:
        raise ValueError("選到的文字太長（超過 4000 字），請分段選取")
    saved = None if page else bump(text)
    if saved is not None:
        return {**saved, "saved": True}
    model, target = settings()
    missing = translate.missing(model)
    if missing:
        raise ValueError(f"翻譯模型還沒下載：請在 Naiz Studio 的即時字幕「選擇翻譯模型」下載 {model}")
    target_name = translate.TARGET_NAMES.get(target, target)
    with _translate_lock:
        state["busy"], state["last"] = True, text[:40]
        try:
            if is_word(text) and not page:
                return {"text": text, "kind": "word", **_word(model, text, context, target_name), "saved": False,
                        "context": context}
            parts = [marker + translate.translate(model, body, "auto", target) if body else marker
                     for marker, body in blocks(text)]
            return {"text": text, "kind": "sentence", "translation": "\n".join(parts),
                    "saved": False, "context": context}
        finally:
            state["busy"] = False


# ------------------------------------------------------------ 服務

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _allowed(self):
        origin = self.headers.get("Origin") or ""
        return origin.startswith("chrome-extension://")

    def _send(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin") or "")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not self._allowed():
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin"))
        self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if not self._allowed():
            return self._send(403, {"ok": False, "error": "只接受 Naiz Studio 網頁翻譯擴充功能"})
        if self.path == "/status":
            model, target = settings()
            return self._send(200, {"ok": True, "model": model, "target": target})
        self._send(404, {"ok": False, "error": "沒有這個功能"})

    def do_POST(self):
        if not self._allowed():
            return self._send(403, {"ok": False, "error": "只接受 Naiz Studio 網頁翻譯擴充功能"})
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 200_000)
            data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if self.path == "/translate":
                result = translate_text(str(data.get("text", "")), str(data.get("context", ""))[:1000],
                                        bool(data.get("page")))
                return self._send(200, {"ok": True, **result})
            if self.path == "/save":
                record = remember({k: str(v)[:4000] for k, v in data.items() if isinstance(v, (str, int))})
                return self._send(200, {"ok": True, **record, "saved": True})
            self._send(404, {"ok": False, "error": "沒有這個功能"})
        except Exception as exc:
            self._send(200, {"ok": False, "error": str(exc) or type(exc).__name__})


def start():
    """開始待命(Naiz Studio 開著就一直在);連接埠被占用時記下錯誤,畫面上會顯示。"""
    if state["server"] is not None:
        return
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        state["error"] = f"連接埠 {PORT} 被其他程式占用，擴充功能連不上（可能開了兩個 Naiz Studio）"
        return
    server.daemon_threads = True
    state["server"], state["error"] = server, ""
    threading.Thread(target=server.serve_forever, daemon=True).start()
