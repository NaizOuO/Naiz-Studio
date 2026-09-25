"""PDF 編輯器的檔案處理:開啟(含密碼)、讀取頁面資訊、依頁面清單儲存新檔、修復損壞的 PDF。"""

import io
from dataclasses import replace
from pathlib import Path

import pikepdf
from PIL import Image, ImageOps

from core import pdfium
from core.files import atomic_path, free_path

from . import annots, geometry, model, pdfwrite

IMAGE_DPI = 96      # 插入圖片時,每 96 像素算 1 英吋(和圖片工具轉 PDF 一樣)
MEMORY_LIMIT = 400 * 1024 * 1024    # 超過這個大小的 PDF 不讀進記憶體,改成直接開檔
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    IMAGE_EXTS |= {".heic", ".heif"}
except ImportError:
    pass


class PasswordRequired(Exception):
    pass


class Damaged(Exception):
    pass


def open_pdf(path, password=None):
    """回傳 (PDFium 文件, 頁面清單, 檔案內容)。需要密碼或密碼錯誤時丟 PasswordRequired,檔案壞掉時丟 Damaged。

    檔案會先讀進記憶體再交給 PDFium,編輯期間不會鎖住原檔(可以刪除、改名、移動);
    太大的檔案讀進記憶體不划算,改用原本的開檔方式,這時回傳的內容是 None。
    """
    path = Path(path)
    data = None
    try:
        if path.stat().st_size <= MEMORY_LIMIT:
            data = path.read_bytes()
    except OSError:
        data = None
    try:
        doc = pdfium.open_document(data if data is not None else path, password)
    except pdfium.error_type() as exc:
        if "password" in str(exc).lower():
            raise PasswordRequired("密碼錯誤" if password else "這份檔案需要密碼才能開啟") from exc
        raise Damaged("檔案可能損壞，沒辦法開啟") from exc
    pages = page_refs(doc, path, password, data)
    if not pages:
        pdfium.close(doc)
        raise Damaged("這份檔案沒有任何頁面")
    return doc, pages, data


def _inherited(obj, key):
    """頁面的屬性;頁面自己沒有時往上層找(旋轉、頁面框可以寫在上層)。"""
    node, depth = obj, 0
    while node is not None and depth < 64:
        if key in node:
            return node[key]
        node, depth = node.get("/Parent"), depth + 1
    return None


def _box(value):
    try:
        x0, y0, x1, y1 = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    return box if box[2] > box[0] and box[3] > box[1] else None


def _page_boxes(obj):
    """(旋轉, 頁面框, 紙張範圍),和 PDFium 的算法一樣:沒有 MediaBox 用 Letter,裁切範圍要落在紙張裡面。"""
    try:
        rotate = int(float(_inherited(obj, "/Rotate") or 0))
    except (TypeError, ValueError):
        rotate = 0
    quarter = abs(rotate) // 90 * (1 if rotate >= 0 else -1)
    media = _box(_inherited(obj, "/MediaBox")) or (0.0, 0.0, 612.0, 792.0)
    crop = _box(_inherited(obj, "/CropBox"))
    if crop is None:
        crop = media
    else:
        crop = (max(crop[0], media[0]), max(crop[1], media[1]), min(crop[2], media[2]), min(crop[3], media[3]))
        if crop[2] <= crop[0] or crop[3] <= crop[1]:
            crop = media
    return (quarter % 4) * 90, crop, media


def read_pages(doc, path, password, data=None):
    """每一頁的 (寬高, 旋轉, 頁面框原點, 紙張範圍, 原本的註解)。

    不逐頁載入 PDFium 的頁面(要解析整頁內容,幾百頁的書會慢好幾秒):寬高用 PDFium 不解析內容的讀法,
    旋轉與頁面框直接讀 PDF 的頁面資料。讀不到時改用 PDFium 一頁一頁讀,這時沒有原本的註解。"""
    count = pdfium.page_count(doc)
    sizes = pdfium.page_sizes(doc)
    try:
        with pikepdf.open(io.BytesIO(data) if data is not None else path, password=password or "") as pdf:
            if len(pdf.pages) != count:
                raise ValueError("頁數不一致")
            result = []
            for index, page in enumerate(pdf.pages):
                rotation, crop, media = _page_boxes(page.obj)
                size, origin = sizes[index], (crop[0], crop[1])
                found = ()
                if "/Annots" in page.obj:
                    try:
                        found = annots.read_page(page.obj, size, rotation, origin)
                    except Exception:
                        found = ()
                result.append((size, rotation, origin, media, found))
            return result
    except Exception:
        result = []
        for index in range(count):
            size, rotation, origin = pdfium.page_info(doc, index)
            result.append((size, rotation, origin, pdfium.media_box(doc, index), ()))
        return result


def _full_page(media, size, rotation, origin):
    """原檔就有裁切的頁面:記下完整紙張的 (寬高, 原點),才能「還原原本大小」;沒有裁切時是空的。"""
    left, bottom, right, top = media
    width, height = right - left, top - bottom
    full_size = (height, width) if rotation % 180 else (width, height)
    if abs(left - origin[0]) < 0.5 and abs(bottom - origin[1]) < 0.5 \
            and abs(full_size[0] - size[0]) < 0.5 and abs(full_size[1] - size[1]) < 0.5:
        return ()
    return full_size, (left, bottom)


def page_refs(doc, path, password=None, data=None):
    """PDF 每一頁的頁面資料(含原本的註解、書籤);原檔的圖片等頁面顯示時才找(見 load_page_images)。"""
    marks = [[] for _ in range(pdfium.page_count(doc))]
    for level, title, index in pdfium.outline(doc):
        if index < len(marks):
            marks[index].append((title, level))
    refs = []
    for index, (size, rotation, origin, media, found) in enumerate(read_pages(doc, path, password, data)):
        refs.append(model.new_ref("pdf", source=str(path), index=index, size=size, base_rotation=rotation,
                                  origin=origin, annots=found, originals=found,
                                  full=_full_page(media, size, rotation, origin), bookmarks=tuple(marks[index])))
    return refs


def with_page_images(ref, found, uids):
    """把原檔的圖片加到這一頁(放在最前面:點選時註解優先,圖片在最底下)。
    found 是 core.pdfium.page_images 的結果;uids 是 (頁面 uid, 圖片編號) → 註解 uid,同一頁每個版本用同一個 uid。"""
    images = annots.page_images(found, ref.size, ref.base_rotation, ref.origin)
    images = tuple(replace(image, uid=uids.setdefault((ref.uid, image.number), image.uid)) for image in images)
    return replace(ref, annots=images + ref.annots, originals=images + ref.originals)


def load_image(path):
    """插入的圖片:轉正、透明處墊白色,統一成 RGB。"""
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        image.load()
    if image.mode in ("RGBA", "LA", "P", "PA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        ground = Image.new("RGB", rgba.size, (255, 255, 255))
        ground.paste(rgba, mask=rgba.getchannel("A"))
        return ground
    return image.convert("RGB")


def image_ref(path):
    with Image.open(path) as image:
        width, height = ImageOps.exif_transpose(image).size
    return model.new_ref("image", source=str(path), size=(width * 72 / IMAGE_DPI, height * 72 / IMAGE_DPI))


def blank_ref(size):
    return model.new_ref("blank", size=(float(size[0]), float(size[1])))


def _image_pdf(path):
    buffer = io.BytesIO()
    load_image(path).save(buffer, "PDF", resolution=IMAGE_DPI)
    return pikepdf.open(io.BytesIO(buffer.getvalue()))


def build(pages, out_path, passwords=None, progress=None, cancel=None, flatten=False, sources=None, report=None):
    """依頁面清單產生新的 PDF;先寫暫存檔,完成才換成正式檔名。存檔後不會保留原檔的密碼。
    flatten 為 True 時把註解合併到頁面內容;sources 是已經讀進記憶體的來源檔(路徑 → 內容)。
    report 是 dict 的話會填入 kept_text:改字時沒辦法真正刪掉、只被蓋住的文字段數;
    kept_images:原檔圖片的內容太特殊、沒能移動或刪除的張數。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    passwords = passwords or {}
    opened, extra = {}, []
    dst = pikepdf.Pdf.new()
    embedder = pdfwrite.FontEmbedder(dst)
    try:
        with atomic_path(out_path) as temp:
            for number, ref in enumerate(pages, start=1):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("已取消")
                if ref.kind == "pdf":
                    src = opened.get(ref.source)
                    if src is None:
                        data = (sources or {}).get(ref.source)
                        src = opened[ref.source] = pikepdf.open(
                            io.BytesIO(data) if data is not None else ref.source,
                            password=passwords.get(ref.source, ""))
                    # 同一頁加兩次時 pikepdf 會各自複製一份,之後分別旋轉不會互相影響(已實測)
                    dst.pages.append(src.pages[ref.index])
                elif ref.kind == "image":
                    image_pdf = _image_pdf(ref.source)
                    extra.append(image_pdf)
                    dst.pages.append(image_pdf.pages[0])
                else:
                    dst.add_blank_page(page_size=ref.size)
                # 旋轉用 PDFium 讀到的角度重新設定,連從上層繼承來的旋轉也算進去
                dst.pages[-1].obj.Rotate = (ref.base_rotation + ref.rotation) % 360
                if ref.kind == "pdf" and ref.full:
                    # 裁切過的頁面:只改看得到的範圍,內容都還在
                    dst.pages[-1].obj.CropBox = [round(v, 3) for v in geometry.user_box(ref)]
                pdfwrite.write_page(dst, dst.pages[-1], ref, embedder)
                if progress:
                    progress(number, len(pages))
            embedder.finish()
            pdfwrite.connect_links(dst, embedder, [ref.uid for ref in pages])
            _write_outline(dst, pages)
            if report is not None:
                report["kept_text"] = embedder.kept_text
                report["kept_images"] = embedder.kept_images
            if flatten:
                for page in dst.pages:
                    pdfwrite.flatten_page(dst, page)
            dst.save(temp, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    finally:
        dst.close()
        for pdf in (*opened.values(), *extra):
            pdf.close()
    return out_path


def _write_outline(pdf, pages):
    """依頁面順序寫出書籤;層級比前一個深的放在它底下。"""
    if not any(ref.bookmarks for ref in pages):
        return
    with pdf.open_outline() as outline:
        stack = [(-1, outline.root)]
        for number, ref in enumerate(pages):
            for title, level in ref.bookmarks:
                item = pikepdf.OutlineItem(title, number)
                while stack[-1][0] >= level:
                    stack.pop()
                stack[-1][1].append(item)
                stack.append((level, item.children))


def default_output(folder, source, suffix="_edited"):
    return free_path(Path(folder), f"{Path(source).stem}{suffix}", ".pdf")


def repair(path, folder):
    """用 pikepdf 重建損壞檔案的索引,另存新檔。回傳 (新檔, 救回的頁數)。"""
    path = Path(path)
    out = free_path(Path(folder), f"{path.stem}_repaired", ".pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with pikepdf.open(path) as pdf:
            with atomic_path(out) as temp:
                pdf.save(temp, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    except pikepdf.PasswordError as exc:
        raise PasswordRequired("這份檔案需要密碼，沒辦法修復") from exc
    except (pikepdf.PdfError, OSError) as exc:
        raise Damaged("檔案損壞得太嚴重，沒辦法修復") from exc
    doc = None
    try:
        doc = pdfium.open_document(out)
        count = pdfium.page_count(doc)
    except pdfium.error_type() as exc:
        out.unlink(missing_ok=True)
        raise Damaged("檔案損壞得太嚴重，沒辦法修復") from exc
    finally:
        if doc is not None:
            pdfium.close(doc)
    if not count:
        out.unlink(missing_ok=True)
        raise Damaged("沒有救回任何頁面")
    return out, count
