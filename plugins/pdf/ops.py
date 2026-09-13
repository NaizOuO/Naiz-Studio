"""PDF 壓縮、拆分、合併的實際運算;全部支援進度回報與中途取消。"""

import io
from pathlib import Path

import pikepdf
from PIL import Image
from pikepdf import Name, PdfImage

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
    same_file = output_path.exists() and input_path.resolve() == output_path.resolve()

    with pikepdf.open(input_path, allow_overwriting_input=same_file) as pdf:
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
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pdf.save(output_path, compress_streams=True,
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


def split(input_path, output_dir, *, weights=None, every_page=False, fmt="pdf",
          image_dpi=150, progress=None, cancel=None) -> dict:
    input_path, output_dir = Path(input_path), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = input_path.stem

    if every_page and fmt in ("png", "jpg"):
        return _split_to_images(input_path, output_dir, stem, fmt, image_dpi, progress, cancel)

    with pikepdf.open(input_path) as src:
        total = len(src.pages)
        ranges = [(i, 1) for i in range(total)] if every_page else plan_split(total, weights)
        if not ranges:
            raise ValueError("頁數不足,無法照這個設定拆分")

        files = []
        width = len(str(len(ranges)))
        for index, (start, count) in enumerate(ranges, start=1):
            _check(cancel)
            name = f"{stem}_p{index:0{width}d}.pdf" if every_page else f"{stem}_part{index}.pdf"
            out_path = output_dir / name
            dst = pikepdf.Pdf.new()
            for page in src.pages[start:start + count]:
                dst.pages.append(page)
            dst.save(out_path, compress_streams=True,
                     object_stream_mode=pikepdf.ObjectStreamMode.generate)
            dst.close()
            files.append(out_path)
            _report(progress, index, len(ranges), f"{index}/{len(ranges)} 份")

    return {"files": files, "count": len(files)}


def _split_to_images(input_path, output_dir, stem, fmt, dpi, progress, cancel):
    import pymupdf

    doc = pymupdf.open(input_path)
    total = doc.page_count
    width = len(str(total))
    files = []
    try:
        for index in range(total):
            _check(cancel)
            pix = doc[index].get_pixmap(dpi=dpi)
            out_path = output_dir / f"{stem}_p{index + 1:0{width}d}.{fmt}"
            if fmt == "jpg":
                out_path.write_bytes(pix.tobytes("jpeg", jpg_quality=85))
            else:
                pix.save(out_path)
            files.append(out_path)
            _report(progress, index + 1, total, f"第 {index + 1}/{total} 頁")
    finally:
        doc.close()
    return {"files": files, "count": len(files)}


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
        for index, path in enumerate(paths, start=1):
            _check(cancel)
            with pikepdf.open(path) as src:
                merged.pages.extend(src.pages)
                pages += len(src.pages)
            _report(progress, index, len(paths), f"併入 {path.name}")

        _report(progress, len(paths), len(paths), "寫入檔案中")
        merged.save(output_path, compress_streams=True,
                    object_stream_mode=pikepdf.ObjectStreamMode.generate)
    finally:
        merged.close()

    if compress_after:
        _report(progress, len(paths), len(paths), "壓縮中")
        compress(output_path, output_path, mode="jpeg", quality=quality, max_dim=max_dim,
                 progress=progress, cancel=cancel)

    after = output_path.stat().st_size
    return {"before": before, "after": after, "pages": pages}
