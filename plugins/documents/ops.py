"""文件轉檔的實際運算。

兩種轉檔方式:
- 電腦上已經安裝的 Office:排版最準,速度也快,但只吃 Word、PowerPoint、Excel 的檔案
- LibreOffice:需要時才下載,免安裝(從安裝檔解出來),格式支援最廣

PDF 轉純文字用程式自己的 PDF 元件做,結果比整個文件重新排版乾淨,也不用等 LibreOffice。
"""

import json
import os
import sys
import subprocess
import tempfile
import threading
from pathlib import Path

from core import deps, paths
from core.files import free_path

SCRIPT = Path(__file__).with_name("office.ps1")
TIMEOUT = 600           # 單一檔案轉太久就放棄,避免卡住整批

WORD_EXTS = {".doc", ".docx", ".docm", ".rtf", ".odt", ".txt", ".html", ".htm"}
PPT_EXTS = {".ppt", ".pptx", ".pps", ".ppsx", ".odp"}
EXCEL_EXTS = {".xls", ".xlsx", ".xlsm", ".csv", ".ods"}
PDF_EXTS = {".pdf"}
SOURCE_EXTS = WORD_EXTS | PPT_EXTS | EXCEL_EXTS | PDF_EXTS
KIND_LABELS = {"word": "文件", "ppt": "簡報", "excel": "試算表", "pdf": "PDF"}
ODF_EXTS = {"word": ".odt", "ppt": ".odp", "excel": ".ods", "pdf": ".odt"}


class Cancelled(Exception):
    pass


class Target:
    """一種輸出格式。sources 是可以從哪些來源轉過來。"""

    def __init__(self, key, ext, label, note, sources, office=(), libre=""):
        self.key, self.ext, self.label, self.note = key, ext, label, note
        self.sources = set(sources)
        self.office = set(office)       # 哪些來源可以交給 Office 做
        self.libre = libre              # LibreOffice 的輸出篩選器;空的表示用預設

    def extension(self, kind):
        return self.ext or ODF_EXTS[kind]


TARGETS = [
    Target("pdf", ".pdf", "PDF", "最通用，版面固定不會跑掉",
           {"word", "ppt", "excel"}, office={"word", "ppt", "excel"}),
    Target("docx", ".docx", "Word", "可以繼續編輯的 Word 文件",
           {"word", "pdf"}, office={"word"}, libre="MS Word 2007 XML"),
    Target("pptx", ".pptx", "PowerPoint", "可以繼續編輯的簡報",
           {"ppt"}, office={"ppt"}, libre="Impress MS PowerPoint 2007 XML"),
    Target("xlsx", ".xlsx", "Excel", "可以繼續編輯的試算表",
           {"excel"}, office={"excel"}, libre="Calc MS Excel 2007 XML"),
    Target("odf", None, "ODF 開放格式", "LibreOffice 等軟體用的格式：文件 odt、簡報 odp、試算表 ods",
           {"word", "ppt", "excel", "pdf"}, office={"word", "ppt", "excel"}),
    Target("rtf", ".rtf", "RTF", "幾乎每種文書軟體都打得開的文件格式",
           {"word", "pdf"}, office={"word"}, libre="Rich Text Format"),
    Target("txt", ".txt", "純文字", "只留下文字，排版與圖片都不會保留",
           {"word", "excel", "pdf"}, office={"word", "excel"}, libre="Text (encoded):UTF8"),
    Target("html", ".html", "網頁", "可以直接用瀏覽器開啟",
           {"word", "excel", "pdf"}, office={"word", "excel"}, libre="HTML (StarWriter)"),
    Target("csv", ".csv", "CSV", "試算表的純文字格式，只會輸出第一個工作表",
           {"excel"}, office={"excel"}, libre="Text - txt - csv (StarCalc)"),
]
TARGET_BY_KEY = {target.key: target for target in TARGETS}
# Calc、Impress 的網頁篩選器和 Writer 不一樣
LIBRE_HTML = {"excel": "HTML (StarCalc)", "ppt": "impress_html_Export"}

LIBREOFFICE = deps.Dependency(
    id="libreoffice",
    name="LibreOffice",
    purpose="把 Word、PowerPoint、Excel、PDF 等文件轉成其他格式",
    size_text="約 357 MB",
    url="https://download.documentfoundation.org/libreoffice/stable/26.8.0/win/x86_64/"
        "LibreOffice_26.8.0_Win_x86-64.msi",
    sha256_url="https://download.documentfoundation.org/libreoffice/stable/26.8.0/win/x86_64/"
               "LibreOffice_26.8.0_Win_x86-64.msi.sha256",
    files={"libreoffice/program/soffice.exe": None},
    folder="libreoffice",
    installer="msi",
    install_size=1700 * 1024 * 1024,
)

_office_lock = threading.Lock()
_office_apps = None


def kind_of(path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in PDF_EXTS:
        return "pdf"
    if suffix in PPT_EXTS:
        return "ppt"
    if suffix in EXCEL_EXTS:
        return "excel"
    if suffix in WORD_EXTS:
        return "word"
    return ""


def targets_for(kind):
    return [target for target in TARGETS if kind in target.sources]


def same_format(path, target) -> bool:
    """輸出格式和來源一樣就不用轉。"""
    kind = kind_of(path)
    return bool(kind) and Path(path).suffix.lower() == target.extension(kind)


def office_apps() -> dict:
    """電腦上有哪些 Office 程式可以用;問過一次就記住。"""
    global _office_apps
    with _office_lock:
        if _office_apps is not None:
            return _office_apps
        result = {"word": False, "ppt": False, "excel": False}
        if os.name == "nt":
            command = ("foreach ($id in 'Word.Application','PowerPoint.Application','Excel.Application') "
                       "{ if ([Type]::GetTypeFromProgID($id)) { '1' } else { '0' } }")
            try:
                done = deps.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                                capture_output=True, text=True, timeout=60)
                answers = [line.strip() for line in done.stdout.splitlines() if line.strip() in ("0", "1")]
                if len(answers) == 3:
                    result = dict(zip(("word", "ppt", "excel"), [a == "1" for a in answers]))
            except (OSError, subprocess.SubprocessError):
                pass
        _office_apps = result
        return result


def office_ready(kind) -> bool:
    return kind in ("word", "ppt", "excel") and office_apps().get(kind, False)


def libre_ready() -> bool:
    return LIBREOFFICE.installed()


def pick_engine(kind, target, preferred="auto") -> str:
    """回傳 office、libre,或空字串(這個組合做不到)。"""
    if kind not in target.sources:
        return ""
    can_office = kind in target.office and office_ready(kind)
    can_libre = target.key != "pdf" or kind != "pdf"
    if preferred == "office":
        return "office" if can_office else ""
    if preferred == "libre":
        return "libre" if can_libre else ""
    if kind == "pdf" and target.key == "txt":
        return "pdfium"                 # PDF 取文字用自己的元件最乾淨
    if kind == "pdf" and target.key == "docx":
        return "rebuild"                # PDF 轉 Word 用自己重建的,不會變成一堆文字方塊
    return "office" if can_office else ("libre" if can_libre else "")


def needs_libreoffice(files, target, preferred="auto") -> bool:
    return any(pick_engine(kind_of(path), target, preferred) == "libre" for path in files)


# ---------------------------------------------------------------- 各種轉檔方式

def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def _run(args, cancel, timeout=TIMEOUT):
    """執行外部程式,中途取消時把它結束掉。"""
    process = deps.popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace")
    while True:
        try:
            output = process.communicate(timeout=1)[0]
            return process.returncode, output
        except subprocess.TimeoutExpired:
            if cancel is not None and cancel.is_set():
                deps.kill_tree(process)
                raise Cancelled()
            timeout -= 1
            if timeout <= 0:
                deps.kill_tree(process)
                raise RuntimeError("轉檔時間過長，已中止")


def convert_office(jobs, cancel=None):
    """用電腦上的 Office 轉;jobs 是 [(來源, 輸出, 格式代號)],一次處理整批,重複使用同一個 Office 程式。"""
    with tempfile.TemporaryDirectory(prefix="naiz_docs_") as folder:
        listing = Path(folder) / "jobs.json"
        listing.write_text(json.dumps([{"source": str(s), "output": str(o), "format": f} for s, o, f in jobs],
                                      ensure_ascii=False), encoding="utf-8")
        code, output = _run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-File", str(SCRIPT), "-Jobs", str(listing)], cancel)
    results = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] in ("OK", "ERR"):
            results[int(parts[1])] = (parts[0] == "OK", parts[2] if len(parts) > 2 else "")
    if not results and code != 0:
        raise RuntimeError(output.strip().splitlines()[-1] if output.strip() else "Office 轉檔失敗")
    return [results.get(index, (False, "Office 沒有回報結果")) for index in range(len(jobs))]


def _libre_profile():
    """LibreOffice 的設定檔資料夾放在系統暫存區:路徑一定是英數,不會因為中文路徑出問題。"""
    folder = Path(tempfile.gettempdir()) / "naiz_libreoffice"
    folder.mkdir(parents=True, exist_ok=True)
    return folder.as_uri()


def convert_libre(sources, target, kind, out_dir, cancel=None):
    """用 LibreOffice 轉一批同類型的檔案;回傳 [(來源, 產生的檔案或 None, 錯誤訊息)]。"""
    soffice = LIBREOFFICE.path("libreoffice/program/soffice.exe")
    if not soffice.is_file():
        raise RuntimeError("還沒安裝 LibreOffice")
    extension = target.extension(kind)
    filter_name = LIBRE_HTML.get(kind, target.libre) if target.key == "html" else target.libre
    convert_to = f"{extension.lstrip('.')}:{filter_name}" if filter_name else extension.lstrip(".")
    with tempfile.TemporaryDirectory(prefix="naiz_docs_") as folder:
        args = [str(soffice), "--headless", "--norestore", f"-env:UserInstallation={_libre_profile()}"]
        if kind == "pdf":
            args.append("--infilter=writer_pdf_import")     # 不指定的話 PDF 會用繪圖模組開,轉不出文件
        args += ["--convert-to", convert_to, "--outdir", str(folder), *[str(p) for p in sources]]
        code, output = _run(args, cancel)
        results = []
        for source in sources:
            produced = Path(folder) / (Path(source).stem + extension)
            if produced.is_file():
                final = free_path(Path(out_dir), Path(source).stem, extension)
                final.parent.mkdir(parents=True, exist_ok=True)
                produced.replace(final)
                results.append((source, final, ""))
            else:
                message = next((line for line in output.splitlines() if "Error" in line), "")
                results.append((source, None, message.strip() or "LibreOffice 轉不出這個檔案"))
    return results


def pdf_to_text(source, out_dir, ocr=None, cancel=None):
    """PDF 取出文字:用程式自己的 PDF 元件,比整份重新排版乾淨。有給 ocr 時,沒有文字的掃描頁會辨識出文字。"""
    from core import pdfium

    doc = pdfium.open_document(Path(source).read_bytes())
    try:
        pages = []
        for index in range(pdfium.page_count(doc)):
            _check(cancel)
            image = None
            with pdfium.LOCK:
                page = doc[index]
                try:
                    text = page.get_textpage().get_text_range()
                    if ocr is not None and not text.strip():
                        bitmap = page.render(scale=ocr.DPI / 72)
                        image = bitmap.to_pil().convert("RGB").copy()
                        bitmap.close()
                finally:
                    page.close()
            if image is not None:
                text = ocr.text_of(ocr.recognize(image))
            pages.append(text.replace("\r\n", "\n").strip())
    finally:
        pdfium.close(doc)
    out = free_path(Path(out_dir), Path(source).stem, ".txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n\n".join(pages).strip() + "\n", encoding="utf-8")
    return out


def office_format(kind, target) -> str:
    """Office 那邊用的格式代號;ODF 要依來源分成 odt、odp、ods。"""
    if target.key == "odf":
        return {"word": "odt", "ppt": "odp", "excel": "ods"}[kind]
    return target.key


def convert_all(files, target_key, out_dir, preferred="auto", progress=None, cancel=None,
                layout="flow", use_ocr=False):
    """把一批檔案轉成同一種格式。回傳 [(來源, 產生的檔案或 None, 訊息)],順序和傳進來的一樣。

    同一種轉檔方式的檔案會合併成一次呼叫:Office 只開一次程式,LibreOffice 也只啟動一次。
    layout 是 PDF 轉 Word 的版面("flow" 重新排版、"exact" 照原樣);
    use_ocr 為真而且元件已經下載時,掃描頁會辨識成文字。
    """
    ocr = ocr_module() if use_ocr else None
    if ocr is not None and not ocr.ready():
        ocr = None
    target = TARGET_BY_KEY[target_key]
    out_dir = Path(out_dir)
    files = [Path(f) for f in files]
    done = {}
    groups = {"office": [], "pdfium": [], "rebuild": [], "libre": {}}
    for path in files:
        kind = kind_of(path)
        if not kind:
            done[path] = (None, "不支援這種檔案")
            continue
        if same_format(path, target):
            done[path] = (None, "已經是這個格式了")
            continue
        engine = pick_engine(kind, target, preferred)
        if not engine:
            done[path] = (None, f"{KIND_LABELS[kind]}沒辦法轉成{target.label}")
        elif engine == "libre":
            groups["libre"].setdefault(kind, []).append(path)
        else:
            groups[engine].append(path)

    total = len(files)
    finished = len(done)

    def report(name):
        if progress:
            progress(finished, total, f"轉換 {name}")

    if groups["pdfium"]:
        for path in groups["pdfium"]:
            _check(cancel)
            report(path.name)
            try:
                done[path] = (pdf_to_text(path, out_dir, ocr, cancel), "")
            except Cancelled:
                raise
            except Exception as exc:
                done[path] = (None, f"{exc}")
            finished += 1

    if groups["rebuild"]:
        pdf_to_docx = _load_rebuilder()

        for path in groups["rebuild"]:
            _check(cancel)
            report(path.name)
            try:
                def page_progress(number, pages, message, name=path.name):
                    if progress:
                        progress(finished, total, f"{name}：{message}")

                done[path] = (pdf_to_docx.convert(path, out_dir, page_progress, cancel, layout, ocr), "")
            except (Cancelled, InterruptedError) as exc:
                raise Cancelled() from exc
            except Exception as exc:
                done[path] = (None, f"{exc}")
            finished += 1

    if groups["office"]:
        report(groups["office"][0].name)
        jobs, outputs = [], []
        for path in groups["office"]:
            kind = kind_of(path)
            extension = target.extension(kind)
            out = free_path(out_dir, path.stem, extension)
            out.parent.mkdir(parents=True, exist_ok=True)
            outputs.append(out)
            jobs.append((path, out, office_format(kind, target)))
        try:
            answers = convert_office(jobs, cancel)
        except Cancelled:
            raise
        except Exception as exc:
            answers = [(False, f"{exc}")] * len(jobs)
        for path, out, (ok, message) in zip(groups["office"], outputs, answers):
            done[path] = (out, "") if ok and out.is_file() else (None, message or "轉檔失敗")
            finished += 1
        if progress:
            progress(finished, total, "")

    for kind, paths_in_kind in groups["libre"].items():
        _check(cancel)
        report(paths_in_kind[0].name)
        try:
            answers = convert_libre(paths_in_kind, target, kind, out_dir, cancel)
        except Cancelled:
            raise
        except Exception as exc:
            answers = [(path, None, f"{exc}") for path in paths_in_kind]
        for source, produced, message in answers:
            done[source] = (produced, message)
            finished += 1
        if progress:
            progress(finished, total, "")

    return [(path, *done.get(path, (None, "沒有處理到"))) for path in files]


def _load_module(name):
    """載入同一個資料夾的模組;單獨載入 ops.py 時(命令列、測試)沒有上層套件,改用路徑載入。"""
    if __package__:
        import importlib

        try:
            return importlib.import_module(f"{__package__}.{name}")
        except ImportError:
            pass
    import importlib.util

    key = f"naiz_documents_{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_rebuilder():
    return _load_module("pdf_to_docx")


def ocr_module():
    return _load_module("ocr")


def has_scanned_pages(path) -> bool:
    """PDF 裡有沒有整頁都沒有文字的頁面(掃描檔);有的話才需要文字辨識。"""
    from core import pdfium

    try:
        doc = pdfium.open_document(Path(path).read_bytes())
    except Exception:
        return False
    try:
        for index in range(pdfium.page_count(doc)):
            with pdfium.LOCK:
                page = doc[index]
                try:
                    text = page.get_textpage()
                    empty = text.count_chars() == 0
                    text.close()
                finally:
                    page.close()
            if empty:
                return True
        return False
    finally:
        pdfium.close(doc)


def needs_ocr(files, target, preferred="auto") -> bool:
    """這次轉換會不會用到文字辨識:PDF 轉 Word 或純文字,而且檔案裡有掃描頁。"""
    return any(kind_of(path) == "pdf" and pick_engine("pdf", target, preferred) in ("rebuild", "pdfium")
               and has_scanned_pages(path) for path in files)


def output_dir():
    return paths.OUTPUT_DIR / "documents"
