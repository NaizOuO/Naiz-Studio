"""PDF 壓縮、拆分、合併的實際運算;全部支援進度回報與中途取消。"""

import io
import re
import unicodedata
from pathlib import Path

import pikepdf
from PIL import Image
from pikepdf import Name, PdfImage

from core import pdfium
from core.files import atomic_path, free_names, free_path

COMPRESS_MODES = {
    "lossless": "無損重打包",
    "jpeg": "JPEG (DCT)",
    "jpeg2000": "JPEG2000",
    "grayscale": "灰階化",
    "bw": "CCITT G4 黑白",
}

SPLIT_FORMATS = {"pdf": "PDF", "png": "PNG 圖片", "jpg": "JPG 圖片"}


class Cancelled(Exception):
    pass


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def _report(progress, done, total, message=""):
    if progress:
        progress(done, total, message)


def page_count(path) -> int:
    with pikepdf.open(path) as pdf:
        return len(pdf.pages)


# ---------------------------------------------------------------- 壓縮

def looks_bilevel(image, threshold=0.92) -> bool:
    """判斷是否幾乎只有黑與白;CCITT 只能存雙色調,彩圖硬轉會變成一團網點。"""
    gray = image.convert("L")
    hist = gray.histogram()
    total = sum(hist) or 1
    return (sum(hist[:45]) + sum(hist[211:])) / total >= threshold


def _encode_ccitt(image):
    from PIL import TiffImagePlugin

    previous = TiffImagePlugin.STRIP_SIZE
    # G4 是連續編碼,被切成多個 strip 後再串接會解碼錯亂,所以強制單一 strip
    TiffImagePlugin.STRIP_SIZE = 2 ** 28
    try:
        bw = image.convert("1")
        buffer = io.BytesIO()
        bw.save(buffer, format="TIFF", compression="group4")
    finally:
        TiffImagePlugin.STRIP_SIZE = previous

    buffer.seek(0)
    tif = Image.open(buffer)
    offsets, counts = tif.tag_v2[273], tif.tag_v2[279]
    photometric = tif.tag_v2.get(262, 0)
    raw = buffer.getvalue()
    data = b"".join(raw[o:o + c] for o, c in zip(offsets, counts))
    return data, bw.size, photometric == 1


def _encode_image(obj, mode, quality, max_dim):
    """回傳 'changed' / 'skipped' / 'unsuitable'。"""
    try:
        pdf_image = PdfImage(obj)
        if pdf_image.bits_per_component == 1 and mode != "bw":
            return "skipped"
        image = pdf_image.as_pil_image()
    except Exception:
        return "skipped"

    if mode == "bw":
        if not looks_bilevel(image):
            return "unsuitable"
        width, height = image.size
        if max(width, height) > max_dim:
            scale = max_dim / max(width, height)
            image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))),
                                 Image.LANCZOS)
        data, (w, h), black_is_1 = _encode_ccitt(image)
        try:
            if len(data) >= len(obj.read_raw_bytes()):
                return "skipped"
        except Exception:
            pass
        obj.write(data, filter=Name("/CCITTFaxDecode"),
                  decode_parms=pikepdf.Dictionary(K=-1, Columns=w, Rows=h, BlackIs1=black_is_1))
        obj["/Width"] = w
        obj["/Height"] = h
        obj["/BitsPerComponent"] = 1
        obj["/ColorSpace"] = Name("/DeviceGray")
        for key in ("/SMask", "/Mask", "/Decode"):
            if key in obj:
                del obj[key]
        return "changed"

    if mode == "grayscale":
        target_mode = "L"
    elif image.mode in ("RGB", "L"):
        target_mode = image.mode
    else:
        target_mode = "RGB"
    if image.mode != target_mode:
        image = image.convert(target_mode)

    width, height = image.size
    if max(width, height) > max_dim:
        scale = max_dim / max(width, height)
        image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))),
                             Image.LANCZOS)

    buffer = io.BytesIO()
    if mode == "jpeg2000":
        image.save(buffer, format="JPEG2000", quality_mode="rates",
                   quality_layers=[max(1, 100 // max(1, quality))])
        pdf_filter = Name("/JPXDecode")
    else:
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        pdf_filter = Name("/DCTDecode")
    data = buffer.getvalue()

    try:
        if len(data) >= len(obj.read_raw_bytes()):
            return "skipped"
    except Exception:
        pass

    obj.write(data, filter=pdf_filter)
    obj["/Width"] = image.width
    obj["/Height"] = image.height
    obj["/BitsPerComponent"] = 8
    obj["/ColorSpace"] = Name("/DeviceGray") if image.mode == "L" else Name("/DeviceRGB")
    for key in ("/SMask", "/Mask", "/Decode", "/DecodeParms"):
        if key in obj:
            del obj[key]
    return "changed"


def compress(input_path, output_path, *, mode="jpeg", quality=70, max_dim=2000,
             keep_bookmarks=True, keep_links=True, progress=None, cancel=None) -> dict:
    input_path, output_path = Path(input_path), Path(output_path)
    before = input_path.stat().st_size
    changed = 0
    unsuitable = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 先寫暫存檔,原檔關閉後才換成正式檔名:中途取消或程式被關掉不會留下壞檔,輸出和輸入是同一個檔案也沒問題
    with atomic_path(output_path) as temp, pikepdf.open(input_path) as pdf:
        total = len(pdf.pages)

        if not keep_bookmarks:
            for key in ("/Outlines", "/Names"):
                if key in pdf.Root:
                    del pdf.Root[key]

        if mode == "lossless" and keep_links:
            _report(progress, total // 2, total, "重新打包中")
        else:
            seen = set()
            for index, page in enumerate(pdf.pages):
                _check(cancel)
                if not keep_links and "/Annots" in page:
                    del page["/Annots"]
                if mode != "lossless":
                    try:
                        images = page.get_images()
                    except Exception:
                        images = {}
                    for _name, obj in images.items():
                        key = id(obj.obj) if hasattr(obj, "obj") else id(obj)
                        if key in seen:
                            continue
                        seen.add(key)
                        _check(cancel)
                        outcome = _encode_image(obj, mode, quality, max_dim)
                        if outcome == "changed":
                            changed += 1
                        elif outcome == "unsuitable":
                            unsuitable += 1
                _report(progress, index + 1, total, f"第 {index + 1}/{total} 頁")

        _check(cancel)
        _report(progress, total, total, "寫入檔案中")
        try:
            pdf.remove_unreferenced_resources()
        except Exception:
            pass
        pdf.save(temp, compress_streams=True,
                 object_stream_mode=pikepdf.ObjectStreamMode.generate, linearize=False)

    after = output_path.stat().st_size
    return {"before": before, "after": after, "images": changed, "unsuitable": unsuitable,
            "saved_ratio": (1 - after / before) * 100 if before else 0.0}


# ---------------------------------------------------------------- 拆分

def plan_split(total_pages: int, weights) -> list:
    """把頁數依權重切成 (起始索引, 頁數);純計算,UI 可即時呼叫預覽。"""
    if not weights:
        return []
    total_weight = sum(weights)
    if total_weight <= 0:
        return []
    counts, assigned = [], 0
    for weight in weights[:-1]:
        count = max(1, int(total_pages * weight / total_weight + 0.5))
        counts.append(count)
        assigned += count
    counts.append(total_pages - assigned)
    if counts[-1] < 1:
        return []
    ranges, start = [], 0
    for count in counts:
        ranges.append((start, count))
        start += count
    return ranges


def parse_page_spec(text: str, total_pages: int) -> list:
    """把「1, 3, 5-8」轉成由小到大的頁碼清單(從 1 起算);格式錯或超出範圍會丟 ValueError。"""
    # 使用者可能開著中文輸入法,全形數字、全形逗號、頓號都要能認
    normalized = unicodedata.normalize("NFKC", text).replace("、", ",").replace("~", "-")
    pages = set()
    for raw in normalized.split(","):
        part = raw.strip()
        if not part:
            continue
        match = re.fullmatch(r"([0-9]+)\s*-\s*([0-9]+)", part)
        if match:
            start, end = sorted((int(match[1]), int(match[2])))
        elif re.fullmatch(r"[0-9]+", part):
            start = end = int(part)
        else:
            raise ValueError(f"看不懂「{part}」")
        if start < 1 or end > total_pages:
            raise ValueError(f"「{part}」超出範圍，這份只有 {total_pages} 頁")
        pages.update(range(start, end + 1))
    return sorted(pages)


def format_page_spec(pages) -> str:
    """parse_page_spec 的反向:把頁碼清單寫回「1-3, 5, 8-10」這種精簡寫法。"""
    ordered = sorted(pages)
    parts = []
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        parts.append(str(ordered[i]) if i == j else f"{ordered[i]}-{ordered[j]}")
        i = j + 1
    return ", ".join(parts)


def _save_pages(src, indexes, out_path):
    dst = pikepdf.Pdf.new()
    try:
        for index in indexes:
            dst.pages.append(src.pages[index])
        with atomic_path(out_path) as temp:
            dst.save(temp, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    finally:
        dst.close()


def split(input_path, output_dir, *, weights, progress=None, cancel=None) -> dict:
    input_path, output_dir = Path(input_path), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with pikepdf.open(input_path) as src:
        ranges = plan_split(len(src.pages), weights)
        if not ranges:
            raise ValueError("頁數不足，無法照這個設定拆分")
        # 已經有同名檔案時整組加上編號,不覆蓋
        outputs = free_names(output_dir, [f"{input_path.stem}_part{i}.pdf" for i in range(1, len(ranges) + 1)])
        written = []
        for index, ((start, count), out_path) in enumerate(zip(ranges, outputs), start=1):
            _check(cancel)
            _save_pages(src, range(start, start + count), out_path)
            written.append(out_path)
            _report(progress, index, len(ranges), f"{index}/{len(ranges)} 份")

    return {"files": written, "count": len(written)}


def extract_pages(input_path, output_dir, pages, *, combine=False, fmt="pdf", image_dpi=150,
                  progress=None, cancel=None) -> dict:
    """取出指定頁(從 1 起算)。combine=True 合成一個 PDF,否則每頁一個檔,可輸出成 PDF / PNG / JPG。"""
    input_path, output_dir = Path(input_path), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pages = sorted(set(pages))
    if not pages:
        raise ValueError("沒有選取任何頁面")
    stem = input_path.stem

    if not combine and fmt in ("png", "jpg"):
        return _pages_to_images(input_path, output_dir, stem, pages, fmt, image_dpi, progress, cancel)

    with pikepdf.open(input_path) as src:
        total = len(src.pages)
        if pages[-1] > total:
            raise ValueError(f"第 {pages[-1]} 頁超出範圍，這份只有 {total} 頁")

        if combine:
            _check(cancel)
            _report(progress, 0, 1, f"合成 {len(pages)} 頁")
            out_path = free_path(output_dir, f"{stem}_selected", ".pdf")
            _save_pages(src, [n - 1 for n in pages], out_path)
            _report(progress, 1, 1, "完成")
            return {"files": [out_path], "count": 1}

        width = len(str(total))
        outputs = free_names(output_dir, [f"{stem}_p{number:0{width}d}.pdf" for number in pages])
        written = []
        for index, (number, out_path) in enumerate(zip(pages, outputs), start=1):
            _check(cancel)
            _save_pages(src, [number - 1], out_path)
            written.append(out_path)
            _report(progress, index, len(pages), f"第 {number} 頁 ({index}/{len(pages)})")

    return {"files": written, "count": len(written)}


def largest_render(input_path, pages, dpi):
    """把這些頁(從 1 起算)輸出成圖片時,像素最多的那一頁:回傳 (像素數, 頁碼)。"""
    doc = pdfium.open_document(input_path)
    try:
        total = pdfium.page_count(doc)
        best = (0, 0)
        for number in pages:
            if 1 <= number <= total:
                width, height = pdfium.page_size(doc, number - 1)
                best = max(best, (width * dpi / 72 * height * dpi / 72, number))
        return best
    finally:
        pdfium.close(doc)


def _pages_to_images(input_path, output_dir, stem, pages, fmt, dpi, progress, cancel):
    doc = pdfium.open_document(input_path)
    try:
        total = pdfium.page_count(doc)
        if pages[-1] > total:
            raise ValueError(f"第 {pages[-1]} 頁超出範圍，這份只有 {total} 頁")
        width = len(str(total))
        outputs = free_names(output_dir, [f"{stem}_p{number:0{width}d}.{fmt}" for number in pages])
        written = []
        for index, (number, out_path) in enumerate(zip(pages, outputs), start=1):
            _check(cancel)
            image = pdfium.render(doc, number - 1, dpi / 72)
            with atomic_path(out_path) as temp:
                if fmt == "jpg":
                    image.save(temp, "JPEG", quality=85)
                else:
                    image.save(temp, "PNG")
            written.append(out_path)
            _report(progress, index, len(pages), f"第 {number} 頁 ({index}/{len(pages)})")
    finally:
        pdfium.close(doc)
    return {"files": written, "count": len(written)}


# ---------------------------------------------------------------- 合併

def merge(input_paths, output_path, *, compress_after=False, quality=70, max_dim=2000,
          progress=None, cancel=None) -> dict:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    paths = [Path(p) for p in input_paths]
    before = sum(p.stat().st_size for p in paths)

    merged = pikepdf.Pdf.new()
    pages = 0
    try:
        # 合併、壓縮都做完才換成正式檔名
        with atomic_path(output_path) as staging:
            for index, path in enumerate(paths, start=1):
                _check(cancel)
                with pikepdf.open(path) as src:
                    merged.pages.extend(src.pages)
                    pages += len(src.pages)
                _report(progress, index, len(paths), f"併入 {path.name}")

            _report(progress, len(paths), len(paths), "寫入檔案中")
            merged.save(staging, compress_streams=True,
                        object_stream_mode=pikepdf.ObjectStreamMode.generate)
            merged.close()

            if compress_after:
                _report(progress, len(paths), len(paths), "壓縮中")
                compress(staging, staging, mode="jpeg", quality=quality, max_dim=max_dim,
                         progress=progress, cancel=cancel)
    finally:
        merged.close()

    after = output_path.stat().st_size
    return {"before": before, "after": after, "pages": pages}
