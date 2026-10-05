"""內建翻譯引擎(v1.18.4):電腦上沒有 Ollama 時,用 llama.cpp 的 llama-server 執行下載來的模型檔(GGUF)。
引擎和模型都從網路下載到本地,翻譯在本地執行,不會上傳。

實測(RTX 5060 Ti、qwen3:8b,同一個模型檔、同一批句子):
- 每句 0.19 秒,和 Ollama(0.17 秒)差不多;翻譯結果大多一樣
- 選 Vulkan 版(約 32 MB):NVIDIA、AMD、Intel 顯示卡都能用,沒有顯示卡時改用處理器;
  CUDA 版(約 250 MB)沒有比較快,載入反而慢(43 秒對 14.5 秒)
- 剛載入好的第一句要多等幾秒,所以載入後先翻一句暖身
- 模型放不進顯示卡時,llama-server 會自己把放不下的部分改用一般記憶體(預設 --fit on)

注意:模型用「工作目錄 + 純英文路徑」傳入,避免程式所在的資料夾有中文時開不了。
"""

import json
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

from core import deps, paths, transcribe

_LLAMA = "https://github.com/ggml-org/llama.cpp/releases/download/b11402"
ENGINE = deps.Dependency(
    id="llama-server",
    name="翻譯引擎（llama.cpp）",
    purpose="在本地執行翻譯模型，不需要上傳",
    size_text="約 32 MB",
    url=f"{_LLAMA}/llama-b11402-bin-win-vulkan-x64.zip",
    files={"llama/llama-server.exe": None},
    folder="llama",
    check_args=["--version"],
    sha256="e8fbe4e2c00ee4b6706f3c68b7f267f51270ede3ef1d377ffe1c318d70d25eec",
    install_size=95_000_000,
)
ENGINE_BYTES = 33308213
FOLDER = "llm"                  # 模型放在 models/llm/
CONTEXT = 4096                  # 說明 + 前面三句 + 這一句,用不到這麼多
LOAD_TIMEOUT = 600              # 只用處理器、硬碟又慢時,大模型可能要載入好幾分鐘
DENIED_RETRIES = 30             # 同 translate.DENIED_RETRIES:Windows 偶爾幾秒不給開新的本地連線

# 模型:名稱(和 Ollama 的一樣,設定可以共用) -> (Hugging Face 上的位置, 檔名, 大小, SHA-256)
# Qwen3 用官方的 GGUF;TranslateGemma 官方沒有出 GGUF,用 mradermacher 轉好的版本(不含看圖用的檔案,翻文字用不到)
_QWEN = "https://huggingface.co/Qwen/{repo}/resolve/{rev}/{file}"
_GEMMA = "https://huggingface.co/mradermacher/{repo}/resolve/{rev}/{file}"
CATALOG = {
    "translategemma:4b": (_GEMMA.format(repo="translategemma-4b-it-GGUF", rev="35a7486e128b19642cdc72d7b91b21ba388aaf42",
                                        file="translategemma-4b-it.Q4_K_M.gguf"),
                          2489909760, "81200d03e843d2ec1ece6eeafe7d13cb6e5211e1fcd336ade55790b683a08330"),
    "qwen3:8b": (_QWEN.format(repo="Qwen3-8B-GGUF", rev="7c41481f57cb95916b40956ab2f0b139b296d974",
                              file="Qwen3-8B-Q4_K_M.gguf"),
                 5027783488, "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785"),
    "translategemma:12b": (_GEMMA.format(repo="translategemma-12b-it-GGUF", rev="fdf84c9f6fe14e69d58814f14e7b5b63bb6a1b28",
                                         file="translategemma-12b-it.Q4_K_M.gguf"),
                           7300794112, "b7aac4b4be7ab0c49b6556c29c4467e74313df7f1e95d9f9676bb2adf0afa528"),
    "qwen3:14b": (_QWEN.format(repo="Qwen3-14B-GGUF", rev="530227a7d994db8eca5ab5ced2fb692b614357fd",
                               file="Qwen3-14B-Q4_K_M.gguf"),
                  9001752960, "500a8806e85ee9c83f3ae08420295592451379b4f8cf2d0f41c15dffeb6b81f0"),
    "translategemma:27b": (_GEMMA.format(repo="translategemma-27b-it-GGUF", rev="0f2c24b456631d519a919e20429119bbd524d86e",
                                         file="translategemma-27b-it.Q4_K_M.gguf"),
                           16546704480, "7f1e67c4ecfec676b38c1ea2ef85c46fafe2f02d3c050fb9540e51787405d8a3"),
    "qwen3:30b": (_QWEN.format(repo="Qwen3-30B-A3B-GGUF", rev="e4d4bafdfb96a411a163846265362aceb0b9c63a",
                               file="Qwen3-30B-A3B-Q4_K_M.gguf"),
                  18556685824, "0d003f6662faee786ed5da3e31b29c978de5ae5d275c8794c606a7f3c01aa8f5"),
    "qwen3:32b": (_QWEN.format(repo="Qwen3-32B-GGUF", rev="938a7432affaec9157f883a87164e2646ae17555",
                               file="Qwen3-32B-Q4_K_M.gguf"),
                  19762149024, "efd971561896866f0e910cce52761ca77b1b138090c7f15fe284676d57d1f689"),
}


def size_text(size_bytes):
    return f"{size_bytes / 1e9:.1f} GB"


def model_dep(name):
    """清單裡的模型對應的下載項目;不在清單裡的(使用者自己放進資料夾的)回傳 None。"""
    if name not in CATALOG:
        return None
    url, size, sha256 = CATALOG[name]
    source = "Qwen 官方" if "/Qwen/" in url else "mradermacher 轉換的版本"
    return deps.Dependency(
        id=f"llm-{name.replace(':', '-')}",
        name=f"翻譯模型 {name}",
        purpose=f"翻譯字幕用的模型（從 Hugging Face 下載，{source}）",
        size_text=f"約 {size_text(size)}",
        url=url,
        files={f"{FOLDER}/{url.rsplit('/', 1)[1]}": None},
        location="models",
        sha256=sha256,
    )


def _folder():
    return paths.MODELS_DIR / FOLDER


def _own_name(path):
    """使用者自己放進 models/llm 的模型:用檔名(不含副檔名)當名稱。"""
    return path.stem


def _path(name):
    """模型檔(相對於 models 資料夾);找不到回傳 None。"""
    dep = model_dep(name)
    if dep is not None:
        return next(iter(dep.files)) if dep.installed() else None
    folder = _folder()
    if folder.is_dir():
        for path in folder.glob("*.gguf"):
            if _own_name(path) == name and path.is_file():
                return f"{FOLDER}/{path.name}"
    return None


def models():
    """已下載的模型:[(名稱, 大小 bytes)]。"""
    folder = _folder()
    if not folder.is_dir():
        return []
    known = {model_dep(name).path().name: name for name in CATALOG}
    result = []
    for path in folder.glob("*.gguf"):
        if not path.is_file() or path.name.startswith("mmproj"):
            continue
        result.append((known.get(path.name, _own_name(path)), path.stat().st_size))
    return sorted(result, key=lambda item: item[1])


def missing(name):
    """用這個模型前還要下載的東西(引擎、模型)。"""
    needed = [ENGINE]
    dep = model_dep(name)
    if dep is not None:
        needed.append(dep)
    return [dep for dep in needed if not dep.installed()]


def pull(name, progress=None, cancel=None):
    """下載引擎(還沒有的話)和模型;progress(已下載, 全部) 合併兩個的進度。"""
    items = missing(name)
    if not items:
        return True
    sizes = [ENGINE_BYTES if dep is ENGINE else CATALOG[name][1] for dep in items]
    total, finished = sum(sizes), 0
    for dep, size in zip(items, sizes):
        def each(done, _total, base=finished):
            if progress and (done or _total):
                progress(base + done, total)
        try:
            deps.install(dep, each, cancel)
        except deps.Cancelled:
            return False
        finished += size
    return True


def delete(name):
    unload(name)
    path = _path(name)
    if path is not None:
        (paths.MODELS_DIR / path).unlink()


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Server:
    """一個常駐的 llama-server(一次只載入一個模型)。"""

    def __init__(self, name):
        self.name = name
        self.port = None
        self.proc = None

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, cancel=None):
        exe = ENGINE.path()
        model = _path(self.name)
        if not exe.is_file():
            raise RuntimeError("翻譯引擎還沒下載，請到「翻譯模型」重新下載")
        if model is None:
            raise RuntimeError(f"找不到翻譯模型 {self.name}，請到「翻譯模型」重新下載")
        self.port = _free_port()
        args = [exe, "-m", model, "--host", "127.0.0.1", "--port", self.port, "-c", CONTEXT, "-np", "1", "--no-webui"]
        # TranslateGemma 檔案裡的對話格式只收它自己的特殊格式(一般文字會讓 llama-server 開不起來):
        # 改用內建的一般 Gemma 格式(要關掉 jinja 才認得),提示照官方寫法放在文字裡(和 Ollama 一樣,見 translate._messages)
        args += ["--no-jinja", "--chat-template", "gemma"] if "translategemma" in self.name else ["--jinja"]
        self.proc = deps.popen(args, cwd=paths.MODELS_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        transcribe._active.add(self.proc)          # 關閉 Naiz Studio 時一併結束
        end = time.time() + LOAD_TIMEOUT
        while time.time() < end:
            if cancel is not None and cancel():
                self.stop()
                raise deps.Cancelled()
            if self.proc.poll() is not None:
                self.stop()
                raise RuntimeError("翻譯模型無法載入（可能是記憶體不夠，可以換小一點的模型）")
            try:
                with urllib.request.urlopen(self.url("/health"), timeout=1) as response:
                    if json.load(response).get("status") == "ok":
                        return
            except (urllib.error.URLError, OSError, ValueError):
                pass
            time.sleep(0.3)
        self.stop()
        raise RuntimeError("翻譯模型載入太久，請換小一點的模型再試")

    def stop(self):
        if self.proc is not None:
            deps.kill_tree(self.proc)
            transcribe._active.discard(self.proc)
            self.proc = None

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def post(self, path, payload, timeout=120):
        request = urllib.request.Request(self.url(path), data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
        for attempt in range(DENIED_RETRIES + 1):
            try:
                return urllib.request.urlopen(request, timeout=timeout)
            except urllib.error.URLError as exc:
                denied = isinstance(exc.reason, PermissionError) and not isinstance(exc, urllib.error.HTTPError)
                if not denied or attempt == DENIED_RETRIES or not self.alive:
                    raise
                time.sleep(1.0)


_lock = threading.Lock()
_server = None


def _ensure(name, cancel=None):
    """確定這個模型已經載入;換模型時先關掉舊的(一次只放一個模型,不會兩個一起佔顯示卡)。"""
    global _server
    with _lock:
        if _server is not None and _server.name == name and _server.alive:
            return _server
        if _server is not None:
            _server.stop()
            _server = None
        server = Server(name)
        server.start(cancel)
        _server = server
    # 暖身:剛載入好的第一句要多等好幾秒,先翻一句,開始字幕後才不會卡一下
    try:
        chat(name, [{"role": "user", "content": "你好"}], max_tokens=4)
    except (urllib.error.URLError, OSError, RuntimeError):
        pass
    return server


def preload(name, cancel=None):
    _ensure(name, cancel)


def unload(name):
    """停止字幕時把模型關掉,讓出記憶體給遊戲;換成別的模型後才呼叫的話不動新的。"""
    global _server
    with _lock:
        if _server is not None and _server.name == name:
            _server.stop()
            _server = None


def loaded():
    """目前載入的模型名稱(測試用)。"""
    server = _server
    return server.name if server is not None and server.alive else None


def chat(name, messages, max_tokens, on_delta=None, cancel=None, temperature=0.2):
    """送出對話,邊收邊把新的字交給 on_delta;回傳模型輸出的全部文字。
    最後一則是 assistant 時,llama-server 會接著它往下寫(用來從已經顯示的翻譯後面接著翻)。"""
    server = _server
    if server is None or server.name != name or not server.alive:
        server = _ensure(name)
    payload = {"messages": messages, "stream": True, "temperature": temperature, "max_tokens": max_tokens,
               "chat_template_kwargs": {"enable_thinking": False}}
    output = ""
    with server.post("/v1/chat/completions", payload) as response:
        for raw in response:
            if cancel is not None and cancel():
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if body == "[DONE]":
                break
            data = json.loads(body)
            if data.get("error"):
                raise RuntimeError(data["error"].get("message", "翻譯失敗"))
            choice = (data.get("choices") or [{}])[0]
            delta = (choice.get("delta") or {}).get("content") or ""
            if delta:
                output += delta
                if on_delta:
                    on_delta(output)
            if choice.get("finish_reason"):
                break
    return output

