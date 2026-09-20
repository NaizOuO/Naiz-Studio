"""命令列介面:讓其他程式直接叫 Naiz Studio 處理檔案,不用打開視窗。

用法:
    python naiz_cli.py images-to-pdf --output 輸出.pdf 圖片1.jpg 圖片2.png
    "Naiz Studio.exe" --cli images-to-pdf --output 輸出.pdf --list 清單.txt

結果一律是一行 JSON:成功 {"ok": true, ...},失敗 {"ok": false, "error": "..."} 並以離開碼 1 結束。
打包成 exe 時沒有主控台,印出來的東西會不見,這時要用 --result 把 JSON 寫成檔案。
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from core import paths
from core.files import free_path

VERSION = "1.12.0"
# 壓縮等級 → (JPEG 品質, 長邊上限像素);none 表示不重新壓縮
QUALITY = {"none": None, "light": (85, 2400), "standard": (70, 2000), "strong": (55, 1600)}
PAGE_SIZES = ("keep", "a4", "first")
PAGE_SIZE_HELP = "keep=每頁維持原本大小、a4=統一成 A4、first=統一成第一頁的大小"


def _pdf_ops():
    """直接載入 PDF 工具的運算模組。

    走 import plugins.pdf 會連帶載入畫面程式(需要 pygame 與顯示裝置),命令列用不到。
    """
    path = paths.PLUGINS_DIR / "pdf" / "ops.py"
    spec = importlib.util.spec_from_file_location("naiz_cli_pdf_ops", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _inputs(args):
    files = [Path(p) for p in args.files]
    if args.list:
        listing = Path(args.list)
        if not listing.is_file():
            raise ValueError(f"找不到清單檔案：{listing}")
        files += [Path(line.strip()) for line in listing.read_text(encoding="utf-8").splitlines() if line.strip()]
    return files


def images_to_pdf(args) -> dict:
    ops = _pdf_ops()
    files = _inputs(args)
    if not files:
        raise ValueError("沒有指定任何檔案")
    allowed = ops.IMAGE_EXTS | {".pdf"}
    missing = [f.name for f in files if not f.is_file()]
    if missing:
        raise ValueError("找不到檔案：" + "、".join(missing[:5]))
    unsupported = [f.name for f in files if f.suffix.lower() not in allowed]
    if unsupported:
        raise ValueError("不支援的檔案：" + "、".join(unsupported[:5]))

    out = Path(args.output)
    if out.suffix.lower() != ".pdf":
        out = out.with_suffix(".pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and not args.overwrite:
        out = free_path(out.parent, out.stem, ".pdf")

    level = QUALITY[args.quality]
    info = ops.merge(files, out, page_size=args.page_size, compress_after=level is not None,
                     quality=level[0] if level else 70, max_dim=level[1] if level else 2000)
    return {"ok": True, "output": str(out), "name": out.name, "pages": info["pages"],
            "bytes": info["after"], "source_bytes": info["before"]}


def info(args) -> dict:
    return {"ok": True, "version": VERSION, "commands": ["images-to-pdf", "info"],
            "page_sizes": list(PAGE_SIZES), "qualities": list(QUALITY),
            "image_types": sorted(_pdf_ops().IMAGE_EXTS)}


def build_parser():
    parser = argparse.ArgumentParser(prog="naiz", description="Naiz Studio 命令列介面")
    subs = parser.add_subparsers(dest="command", required=True)

    images = subs.add_parser("images-to-pdf", help="把圖片（也可以混 PDF）依順序合成一份 PDF")
    images.add_argument("files", nargs="*", help="圖片或 PDF 的路徑，順序就是頁面順序")
    images.add_argument("--list", help="改用文字檔提供清單（每行一個路徑），檔案很多時用")
    images.add_argument("--output", "-o", required=True, help="輸出的 PDF 路徑")
    images.add_argument("--page-size", choices=PAGE_SIZES, default="a4", help=PAGE_SIZE_HELP)
    images.add_argument("--quality", choices=list(QUALITY), default="standard",
                        help="壓縮等級：none 不壓縮、light 輕、standard 一般、strong 最小")
    images.add_argument("--overwrite", action="store_true", help="同名時直接覆蓋（預設自動加編號）")
    images.set_defaults(handler=images_to_pdf)

    detail = subs.add_parser("info", help="回報版本與支援的選項")
    detail.set_defaults(handler=info)

    for sub in (images, detail):
        sub.add_argument("--result", help="把結果 JSON 另外寫到這個檔案（exe 沒有主控台時用）")
    return parser


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--cli":
        # exe 需要 --cli 才知道不要開視窗;這裡也接受,呼叫的程式就能用同一種寫法指向 exe 或 .py
        argv = argv[1:]
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:      # 參數錯誤:argparse 已經印過說明
        return int(exc.code or 2)
    try:
        result = args.handler(args)
    except Exception as exc:
        result = {"ok": False, "error": f"{exc}", "error_type": type(exc).__name__}
    text = json.dumps(result, ensure_ascii=False)
    if getattr(args, "result", None):
        try:
            Path(args.result).parent.mkdir(parents=True, exist_ok=True)
            Path(args.result).write_text(text, encoding="utf-8")
        except OSError as exc:
            print(json.dumps({"ok": False, "error": f"寫不出結果檔案：{exc}"}, ensure_ascii=False))
            return 1
    print(text)
    return 0 if result.get("ok") else 1
