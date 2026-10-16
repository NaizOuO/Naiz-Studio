"""MCP 伺服器(Model Context Protocol):讓 Claude Code 等 AI 工具直接使用 Naiz Studio 的指令。
工具清單從 core/commands 產生(和命令列同一份說明)。透過標準輸入輸出,一行一個 JSON-RPC 2.0 訊息。

啟動:"Naiz Studio.exe" --mcp   或   python naiz_cli.py --mcp

- exe 是沒有主控台的視窗程式:標準輸入輸出不能靠 sys.stdin/sys.stdout(可能是 None),直接用作業系統給的管線
- 標準輸出只能放協定訊息:執行指令時把 print 之類的輸出導到標準錯誤
- 每個工具多一個 allow_download 參數:缺元件時預設不下載,回報要下載什麼
- 會跑很久的工具在背景執行:呼叫時有給 progressToken 就回報進度;收到取消就停下
- video-look 的結果另外附上總覽圖(圖片),看得懂圖片的模型可以直接看
"""

import base64
import io
import json
import os
import sys
import threading
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from core import commands, version  # noqa: E402

PROTOCOL = "2025-06-18"
NAME = "naiz-studio"
IMAGE_LIMIT = 4 * 1024 * 1024       # 附上的圖片最大多大(太大的只給路徑)


def _streams():
    """(讀, 寫):直接用檔案代號 0、1(視窗程式的 sys.stdin/sys.stdout 可能是 None)。"""
    reader = io.open(0, "rb", closefd=False)
    writer = io.open(1, "wb", buffering=0, closefd=False)
    return reader, writer


class Server:
    def __init__(self, reader=None, writer=None):
        if reader is None or writer is None:
            reader, writer = _streams()
        self.reader, self.writer = reader, writer
        self.lock = threading.Lock()
        self.running = {}               # 請求 id → 取消用的 Event
        self.registry = commands.load_all()

    # ------------------------------------------------------------ 傳送
    def send(self, message):
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        with self.lock:
            self.writer.write(data)
            if hasattr(self.writer, "flush"):
                self.writer.flush()

    def reply(self, request_id, result=None, error=None):
        message = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result
        self.send(message)

    # ------------------------------------------------------------ 工具
    def tools(self):
        out = []
        for name in sorted(self.registry):
            spec = self.registry[name]
            schema = spec.schema()
            schema["properties"]["allow_download"] = {
                "type": "boolean", "default": False,
                "description": "缺少的元件（辨識模型等）自動下載；預設不下載，回報需要下載什麼（可能好幾 GB，先問使用者）"}
            summary = spec.summary + ("（會跑比較久）" if spec.long else "")
            out.append({"name": name, "title": summary.split("，")[0], "description": summary,
                        "inputSchema": schema})
        return out

    def call(self, request_id, params):
        name = params.get("name")
        arguments = dict(params.get("arguments") or {})
        token = (params.get("_meta") or {}).get("progressToken")
        cancel = threading.Event()
        self.running[request_id] = cancel

        def progress(ratio, text):
            if token is None:
                return
            note = {"progressToken": token, "progress": round((ratio or 0) * 100, 1), "total": 100}
            if text:
                note["message"] = text
            self.send({"jsonrpc": "2.0", "method": "notifications/progress", "params": note})

        def work():
            allow = bool(arguments.pop("allow_download", False))
            ctx = commands.Context(allow_download=allow, on_progress=progress, cancel=cancel)
            saved = sys.stdout
            sys.stdout = sys.stderr             # 指令裡印出的東西不能混進協定訊息
            try:
                result = commands.run(name, arguments, ctx)
            finally:
                sys.stdout = saved
            self.running.pop(request_id, None)
            if cancel.is_set():
                return                          # 已經取消:不用回覆
            content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
            # 總覽圖直接附上(好幾張時照順序附,合計不超過 IMAGE_LIMIT;沒附上的看結果裡的路徑)
            budget = IMAGE_LIMIT
            for sheet in result.get("sheets") or ([result["sheet"]] if result.get("sheet") else []):
                path = Path(sheet)
                if not path.is_file() or path.stat().st_size > budget:
                    break
                budget -= path.stat().st_size
                content.append({"type": "image", "mimeType": "image/jpeg",
                                "data": base64.b64encode(path.read_bytes()).decode()})
            self.reply(request_id, {"content": content, "structuredContent": result, "isError": not result.get("ok")})

        if name not in self.registry:
            self.running.pop(request_id, None)
            self.reply(request_id, error={"code": -32602, "message": f"沒有這個工具：{name}"})
            return
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ 主迴圈
    def handle(self, message):
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            self.reply(request_id, {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": NAME, "title": "Naiz Studio", "version": version.VERSION},
                "instructions": "Naiz Studio 的本地工具：影片轉看圖（拆畫面、總覽圖、逐字稿）、錄音轉逐字稿、PDF 等。"
                                "全部在使用者電腦上處理；路徑請用完整的本機路徑。缺元件時預設不下載，"
                                "會回報要下載什麼，請先問使用者再用 allow_download 重試。"})
        elif method == "ping":
            self.reply(request_id, {})
        elif method == "tools/list":
            self.reply(request_id, {"tools": self.tools()})
        elif method == "tools/call":
            self.call(request_id, params)
        elif method == "notifications/cancelled":
            cancel = self.running.get(params.get("requestId"))
            if cancel is not None:
                cancel.set()
        elif method and method.startswith("notifications/"):
            pass                                # initialized 等通知:不用回覆
        elif request_id is not None:
            self.reply(request_id, error={"code": -32601, "message": f"不支援：{method}"})

    def serve(self):
        while True:
            line = self.reader.readline()
            if not line:
                break                           # 對方關掉:結束
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line.decode("utf-8"))
            except ValueError:
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "看不懂的訊息"}})
                continue
            for item in message if isinstance(message, list) else [message]:
                try:
                    self.handle(item)
                except Exception as exc:        # 一個訊息出錯不要讓整個伺服器停掉
                    if item.get("id") is not None:
                        self.reply(item["id"], error={"code": -32603, "message": str(exc)})


def main():
    Server().serve()
    return 0
