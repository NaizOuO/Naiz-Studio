"""給外部程式、AI 模型呼叫的指令:每個指令只寫一份說明(參數、類型、選項、說明文字),
命令列(core/cli.py)、`info` 的指令清單、MCP 工具(core/mcp.py)都從這份說明產生,三邊不會對不上。

寫一個指令:
    @command("video-look", "影片拆成畫面＋逐字稿，給 AI 看影片內容", [
        Param("video", "影片檔", kind="file"),
        Param("every", "每隔幾秒擷取一張", kind="float", default=0),
    ])
    def video_look(args, ctx):
        ctx.need([deps.FFMPEG])          # 缺元件時:沒有允許下載就停下並回報要下載什麼
        ctx.progress(0.5, "擷取畫面")
        return {"output": ...}          # 回傳的 dict 會加上 "ok": True

規則:
- 只處理本機檔案;輸出不覆蓋原檔(同名自動加編號)
- 缺少的元件預設不下載,回傳需要下載什麼;呼叫時允許下載(--allow-download)才下載
- 錯誤訊息是可以直接給使用者看的中文
- 指令模組載入時不能載入畫面相關的套件(要用的東西在函式裡才載入)
"""

import json
import sys
import threading
from dataclasses import dataclass, field

from core import deps

KINDS = ("text", "file", "files", "path", "folder", "int", "float", "bool", "choice")


@dataclass
class Param:
    name: str                       # 參數名稱(命令列是 --name,底線換成 -)
    help: str                       # 說明(中文,給人和 AI 看)
    kind: str = "text"              # 見 KINDS;file 要存在的檔案、files 好幾個檔案、path／folder 輸出位置
    default: object = None
    choices: tuple = ()             # kind="choice" 時的選項;(值, 說明) 或只有值
    required: bool = False
    positional: bool = False        # 命令列不用寫 --name(例如輸入檔)

    def __post_init__(self):
        assert self.kind in KINDS, self.kind
        self.choices = tuple(c if isinstance(c, tuple) else (c, "") for c in self.choices)

    @property
    def values(self):
        return [value for value, _ in self.choices]

    def schema(self):
        """JSON Schema(給 MCP 和 info 用)。"""
        if self.kind == "files":
            out = {"type": "array", "items": {"type": "string"}}
        elif self.kind == "int":
            out = {"type": "integer"}
        elif self.kind == "float":
            out = {"type": "number"}
        elif self.kind == "bool":
            out = {"type": "boolean"}
        else:
            out = {"type": "string"}
        description = self.help
        if self.choices:
            out["enum"] = self.values
            notes = "；".join(f"{value}＝{note}" for value, note in self.choices if note)
            if notes:
                description += f"（{notes}）"
        if self.kind in ("file", "files", "path", "folder"):
            description += "（本機路徑）"
        out["description"] = description
        if self.default is not None:
            out["default"] = self.default
        return out


@dataclass
class Command:
    name: str
    summary: str
    params: list
    run: object = None
    long: bool = False              # 會跑很久(轉逐字稿等):建議呼叫時看進度
    examples: list = field(default_factory=list)

    def schema(self):
        properties = {p.name: p.schema() for p in self.params}
        required = [p.name for p in self.params if p.required]
        return {"type": "object", "properties": properties, "required": required}

    def describe(self):
        return {"name": self.name, "summary": self.summary, "long": self.long, "examples": self.examples,
                "parameters": self.schema()}


REGISTRY = {}


def command(name, summary, params, long=False, examples=()):
    def wrap(func):
        REGISTRY[name] = Command(name, summary, list(params), func, long, list(examples))
        return func
    return wrap


class CommandError(Exception):
    """可以直接給使用者看的錯誤;extra 會放進結果(例如缺少的元件)。"""

    def __init__(self, message, **extra):
        super().__init__(message)
        self.extra = extra


class Context:
    """執行一個指令時的環境:回報進度、取消、要不要自動下載元件。"""

    def __init__(self, allow_download=False, on_progress=None, cancel=None):
        self.allow_download = allow_download
        self.on_progress = on_progress
        self.cancel = cancel or threading.Event()
        self.downloaded = []

    def progress(self, ratio, text=""):
        if self.on_progress:
            self.on_progress(None if ratio is None else max(0.0, min(1.0, float(ratio))), text)

    def need(self, items):
        """確認元件都裝好了;缺的沒有允許下載就停下並回報,允許時下載(進度照常回報)。"""
        missing = list({dep.id: dep for dep in items if not dep.installed()}.values())
        if not missing:
            return
        listing = [{"id": dep.id, "name": dep.name, "size": dep.size_text} for dep in missing]
        if not self.allow_download:
            names = "、".join(f"{dep.name}（{dep.size_text}）" for dep in missing)
            raise CommandError(f"需要先下載：{names}。允許下載（命令列加 --allow-download）就會自動下載，"
                               "或先打開 Naiz Studio 用到這個功能時下載", missing=listing)
        for dep in missing:
            def report(done, total, name=dep.name):
                self.progress(done / total if total else None, f"下載 {name}")
            deps.install(dep, report, self.cancel)
            self.downloaded.append(dep.name)


def load_all():
    """載入所有指令模組(只有說明,執行時才載入真正做事的程式)。"""
    from . import media, pdf, speech  # noqa: F401
    return REGISTRY


def run(name, values, ctx=None):
    """用 dict 參數執行指令,回傳結果 dict(一定有 ok)。命令列和 MCP 都走這裡。"""
    load_all()
    ctx = ctx or Context()
    spec = REGISTRY.get(name)
    if spec is None:
        return {"ok": False, "error": f"沒有這個指令：{name}", "error_type": "UnknownCommand"}
    try:
        args = _normalize(spec, values or {})
        result = spec.run(args, ctx) or {}
        if ctx.downloaded:
            result["downloaded"] = ctx.downloaded
        return {"ok": True, **result}
    except CommandError as exc:
        return {"ok": False, "error": str(exc), "error_type": "CommandError", **exc.extra}
    except deps.Cancelled:
        return {"ok": False, "error": "已取消", "error_type": "Cancelled"}
    except Exception as exc:
        return {"ok": False, "error": str(exc) or type(exc).__name__, "error_type": type(exc).__name__}


def _normalize(spec, values):
    """檢查參數、補上預設值、轉成正確的型別。"""
    from pathlib import Path

    known = {p.name for p in spec.params}
    unknown = [key for key in values if key not in known]
    if unknown:
        raise CommandError(f"不認得的參數：{'、'.join(unknown)}（可以用的有：{'、'.join(sorted(known))}）")
    args = {}
    for p in spec.params:
        value = values.get(p.name, p.default)
        if value is None or value == [] and p.kind == "files":
            if p.required:
                raise CommandError(f"缺少參數：{p.name}（{p.help}）")
            args[p.name] = [] if p.kind == "files" else value
            continue
        try:
            if p.kind == "int":
                value = int(value)
            elif p.kind == "float":
                value = float(value)
            elif p.kind == "bool":
                value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
            elif p.kind == "files":
                value = [Path(v) for v in (value if isinstance(value, (list, tuple)) else [value])]
            elif p.kind in ("file", "path", "folder"):
                value = Path(str(value))
            else:
                value = str(value)
        except (TypeError, ValueError):
            raise CommandError(f"參數 {p.name} 的格式不對：{value!r}") from None
        if p.kind == "choice" and value not in p.values:
            raise CommandError(f"參數 {p.name} 只能是：{'、'.join(map(str, p.values))}")
        if p.kind == "file" and not value.is_file():
            raise CommandError(f"找不到檔案：{value}")
        if p.kind == "files":
            missing = [str(v) for v in value if not v.is_file()]
            if missing:
                raise CommandError("找不到檔案：" + "、".join(missing[:5]))
        args[p.name] = value
    return args


def emit(data, stream=None):
    """印出一行 JSON(進度或結果);exe 沒有主控台時 stdout 可能是 None。"""
    stream = stream or sys.stdout
    if stream is None:
        return
    stream.write(json.dumps(data, ensure_ascii=False) + "\n")
    stream.flush()
