"""PDF 指令。運算用 plugins/pdf/ops.py(直接載入檔案,不經過會載入畫面的套件)。"""

import importlib.util
import sys
from pathlib import Path

from core import paths
from core.files import free_path

from . import CommandError, Param, command

# 壓縮等級 → (JPEG 品質, 長邊上限像素);none 表示不重新壓縮
QUALITY = {"none": None, "light": (85, 2400), "standard": (70, 2000), "strong": (55, 1600)}
QUALITY_NOTES = (("none", "不壓縮"), ("light", "輕"), ("standard", "一般"), ("strong", "最小"))
PAGE_SIZES = (("keep", "每頁維持原本大小"), ("a4", "統一成 A4"), ("first", "統一成第一頁的大小"))


def ops():
    """載入 PDF 工具的運算模組(走 import plugins.pdf 會連帶載入畫面程式,命令列用不到)。"""
    name = "naiz_cli_pdf_ops"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, paths.PLUGINS_DIR / "pdf" / "ops.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def output_path(path, suffix, overwrite=False):
    """輸出路徑:補上副檔名、建資料夾;同名時自動加編號(overwrite 時直接覆蓋)。"""
    out = Path(path)
    if out.suffix.lower() != suffix:
        out = out.with_suffix(suffix)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and not overwrite:
        out = free_path(out.parent, out.stem, suffix)
    return out


@command("images-to-pdf", "把圖片（也可以混 PDF）依順序合成一份 PDF", [
    Param("files", "圖片或 PDF 的路徑，順序就是頁面順序", kind="files", positional=True),
    Param("list", "改用文字檔提供清單（每行一個路徑），檔案很多時用", kind="file"),
    Param("output", "輸出的 PDF 路徑", kind="path", required=True),
    Param("page_size", "頁面大小", kind="choice", choices=PAGE_SIZES, default="a4"),
    Param("quality", "壓縮等級", kind="choice", choices=QUALITY_NOTES, default="standard"),
    Param("overwrite", "同名時直接覆蓋（預設自動加編號）", kind="bool", default=False),
], examples=['images-to-pdf --output 報告.pdf 照片1.jpg 照片2.jpg'])
def images_to_pdf(args, ctx):
    module = ops()
    files = list(args["files"])
    if args["list"]:
        files += [Path(line.strip()) for line in args["list"].read_text(encoding="utf-8").splitlines() if line.strip()]
    if not files:
        raise CommandError("沒有指定任何檔案")
    missing = [f.name for f in files if not f.is_file()]
    if missing:
        raise CommandError("找不到檔案：" + "、".join(missing[:5]))
    allowed = module.IMAGE_EXTS | {".pdf"}
    unsupported = [f.name for f in files if f.suffix.lower() not in allowed]
    if unsupported:
        raise CommandError("不支援的檔案：" + "、".join(unsupported[:5]))
    out = output_path(args["output"], ".pdf", args["overwrite"])
    level = QUALITY[args["quality"]]
    info = module.merge(files, out, page_size=args["page_size"], compress_after=level is not None,
                        quality=level[0] if level else 70, max_dim=level[1] if level else 2000)
    return {"output": str(out), "name": out.name, "pages": info["pages"], "bytes": info["after"],
            "source_bytes": info["before"]}
