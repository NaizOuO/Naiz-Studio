"""命令列介面:讓其他程式、AI 模型直接叫 Naiz Studio 處理檔案,不用打開視窗。
指令和參數都在 core/commands 裡定義(命令列參數、info 的說明、MCP 工具都從同一份產生)。

用法:
    python naiz_cli.py info                                  # 列出所有指令、參數、說明
    python naiz_cli.py images-to-pdf --output 輸出.pdf 圖片1.jpg 圖片2.png
    python naiz_cli.py transcribe 會議.m4a --speakers auto --progress
    "Naiz Studio.exe" --cli video-look 錄影.mp4 --result 結果.json

共用參數:
    --result 檔案          結果 JSON 另外寫成檔案(exe 沒有主控台,印出來的東西會不見)
    --progress             處理中每一行印一筆進度 {"progress": 0.4, "text": "辨識語音"},最後一行是結果
    --allow-download       缺少的元件(辨識模型等)自動下載;沒加時回傳需要下載什麼並停下

結果一律是一行 JSON:成功 {"ok": true, ...},失敗 {"ok": false, "error": "...", "error_type": "..."}
並以離開碼 1 結束。缺元件時多一個 "missing":[{id, name, size}]。
"""

import argparse
import json
import os
import sys
from pathlib import Path

# 有些功能會載入 pygame:不要讓它印歡迎訊息(會混進 JSON 結果)、不要開視窗
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from core import commands, version  # noqa: E402

COMMON = ("result", "progress", "allow_download")


def info(_args=None) -> dict:
    """版本、所有指令和參數(外部模型呼叫一次就知道能做什麼)。"""
    from core.commands import pdf

    registry = commands.load_all()
    return {"ok": True, "version": version.VERSION, "commands": sorted(registry) + ["info"],
            "details": [registry[name].describe() for name in sorted(registry)],
            "common_options": {"result": "結果 JSON 另外寫成檔案（exe 沒有主控台時用）",
                               "progress": "處理中每一行印一筆進度，最後一行是結果",
                               "allow_download": "缺少的元件自動下載（預設不下載，回傳需要下載什麼）"},
            # 舊版(v1.12)就有的欄位,保留給已經在用的程式
            "page_sizes": [value for value, _ in pdf.PAGE_SIZES], "qualities": list(pdf.QUALITY),
            "image_types": sorted(pdf.ops().IMAGE_EXTS)}


def build_parser():
    parser = argparse.ArgumentParser(prog="naiz", description="Naiz Studio 命令列介面")
    subs = parser.add_subparsers(dest="command", required=True)
    registry = commands.load_all()
    for name in sorted(registry):
        spec = registry[name]
        sub = subs.add_parser(name, help=spec.summary, description=spec.summary)
        for p in spec.params:
            flag = p.name if p.positional else "--" + p.name.replace("_", "-")
            options = {"help": p.help + (f"（{'、'.join(p.values)}）" if p.choices else "")}
            if p.kind == "bool":
                sub.add_argument(flag, action=argparse.BooleanOptionalAction, default=None, **options)
                continue
            if p.kind == "files":
                options["nargs"] = "*"
            elif p.positional and not p.required:
                options["nargs"] = "?"
            if p.choices:
                options["choices"] = p.values
            if not p.positional:
                options["dest"] = p.name
                if p.name == "output":
                    sub.add_argument("-o", flag, **options)
                    continue
            sub.add_argument(flag, **options)
        _common(sub)
    _common(subs.add_parser("info", help="列出版本、所有指令與參數"))
    return parser


def _common(sub):
    sub.add_argument("--result", help="把結果 JSON 另外寫到這個檔案（exe 沒有主控台時用）")
    sub.add_argument("--progress", action="store_true", help="處理中每一行印一筆進度，最後一行是結果")
    sub.add_argument("--allow-download", dest="allow_download", action="store_true",
                     help="缺少的元件（辨識模型等）自動下載")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")          # 一律 UTF-8(主控台是 cp950 時中文才不會變亂碼)
    except (AttributeError, ValueError):
        pass
    if argv and argv[0] == "--cli":
        # exe 需要 --cli 才知道不要開視窗;這裡也接受,呼叫的程式就能用同一種寫法指向 exe 或 .py
        argv = argv[1:]
    if argv and argv[0] == "--mcp":
        from core import mcp

        return mcp.main()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:      # 參數錯誤:argparse 已經印過說明
        return int(exc.code or 2)
    if args.command == "info":
        result = info()
    else:
        values = {key: value for key, value in vars(args).items()
                  if key not in COMMON and key != "command" and value is not None}
        on_progress = None
        if args.progress:
            def on_progress(ratio, text):
                commands.emit({"progress": None if ratio is None else round(ratio, 3), "text": text})
        ctx = commands.Context(allow_download=args.allow_download, on_progress=on_progress)
        result = commands.run(args.command, values, ctx)
    text = json.dumps(result, ensure_ascii=False)
    if getattr(args, "result", None):
        try:
            Path(args.result).parent.mkdir(parents=True, exist_ok=True)
            Path(args.result).write_text(text, encoding="utf-8")
        except OSError as exc:
            commands.emit({"ok": False, "error": f"寫不出結果檔案：{exc}"})
            return 1
    if sys.stdout is not None:
        print(text)
    return 0 if result.get("ok") else 1
