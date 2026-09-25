"""PDFium(pypdfium2)的共用入口:開啟 PDF、把頁面畫成圖片、查詢文字位置。

PDFium 不能同時在多個執行緒使用(實測會讓整個程式直接崩潰),所以所有呼叫都要經過這裡並先取得 LOCK。
例如縮圖在背景畫,同時另一個執行緒正在把頁面輸出成圖片。
"""

import threading

LOCK = threading.RLock()
_original_flags = {}        # (文件, 頁, 第幾個註解) → 開檔時的旗標;畫面暫時藏起註解後要能還原
_touched_pages = set()


def open_document(source, password=None):
    """source 是檔案路徑,或已經讀進記憶體的內容(bytes)。
    用 bytes 開啟時 Windows 不會鎖住檔案,使用者可以照常刪除、改名、移動那個檔。"""
    import pypdfium2

    with LOCK:
        return pypdfium2.PdfDocument(source if isinstance(source, (bytes, bytearray)) else str(source),
                                     password=password)


def error_type():
    """PDFium 開不了檔時丟出的例外類別(需要密碼、檔案損壞等)。"""
    import pypdfium2

    return pypdfium2.PdfiumError


def page_rotation(doc, index):
    """第 index 頁本身設定的旋轉角度(0、90、180、270)。"""
    with LOCK:
        page = doc[index]
        try:
            return page.get_rotation()
        finally:
            page.close()


def page_count(doc):
    with LOCK:
        return len(doc)


def page_sizes(doc):
    """每一頁的寬高(點,已含頁面本身的旋轉);不用載入頁面內容,幾百頁也很快。"""
    with LOCK:
        return [tuple(doc.get_page_size(index)) for index in range(len(doc))]


def page_size(doc, index):
    """第 index 頁(從 0 起算)的寬高,單位是點(1/72 英吋)。"""
    with LOCK:
        page = doc[index]
        try:
            return page.get_size()
        finally:
            page.close()


def page_info(doc, index):
    """一次讀出 (寬高, 旋轉角度, 頁面框左下角座標)。"""
    with LOCK:
        page = doc[index]
        try:
            left, bottom, _, _ = page.get_cropbox()
            return page.get_size(), page.get_rotation(), (float(left), float(bottom))
        finally:
            page.close()


def _apply_hidden(doc, page, index, hidden):
    """把 hidden 裡的註解暫時設成隱藏(只改記憶體裡的文件),其他註解還原成開檔時的樣子。"""
    import pypdfium2.raw as raw

    page_key = (id(doc), index)
    if not hidden and page_key not in _touched_pages:
        return
    _touched_pages.add(page_key)
    hidden = set(hidden)
    for number in range(raw.FPDFPage_GetAnnotCount(page.raw)):
        handle = raw.FPDFPage_GetAnnot(page.raw, number)
        if not handle:
            continue
        try:
            flags = raw.FPDFAnnot_GetFlags(handle)
            base = _original_flags.setdefault(page_key + (number,), flags)
            wanted = (base | raw.FPDF_ANNOT_FLAG_HIDDEN) if number in hidden else base
            if flags != wanted:
                raw.FPDFAnnot_SetFlags(handle, wanted)
        finally:
            raw.FPDFPage_CloseAnnot(handle)


def outline(doc):
    """文件的書籤(目錄):[(層級, 標題, 第幾頁)];沒有指到頁面的書籤略過。"""
    found = []
    with LOCK:
        try:
            for mark in doc.get_toc():
                dest = mark.get_dest()
                index = dest.get_index() if dest is not None else None
                if index is not None and index >= 0:
                    found.append((mark.level, mark.get_title() or "", index))
        except Exception:
            return found
    return found


def is_scan(doc, index):
    """這一頁是不是掃描的:完全沒有文字物件,但有圖片。"""
    import pypdfium2.raw as raw

    with LOCK:
        page = doc[index]
        try:
            kinds = {raw.FPDFPageObj_GetType(raw.FPDFPage_GetObject(page.raw, i))
                     for i in range(raw.FPDFPage_CountObjects(page.raw))}
            return raw.FPDF_PAGEOBJ_TEXT not in kinds and raw.FPDF_PAGEOBJ_IMAGE in kinds
        finally:
            page.close()


def media_box(doc, index):
    """第 index 頁完整的紙張範圍 (左, 下, 右, 上)(MediaBox);頁面有裁切時比 page_info 的範圍大。"""
    with LOCK:
        page = doc[index]
        try:
            return tuple(float(v) for v in page.get_mediabox())
        finally:
            page.close()


def _image_objects(page):
    """頁面最上層的圖片物件(不含表單裡面的),依內容的先後順序;第幾個就是編輯器記的圖片編號。"""
    import pypdfium2.raw as raw

    found = []
    for number in range(raw.FPDFPage_CountObjects(page.raw)):
        obj = raw.FPDFPage_GetObject(page.raw, number)
        if raw.FPDFPageObj_GetType(obj) == raw.FPDF_PAGEOBJ_IMAGE:
            found.append(obj)
    return found


def _bounds(obj):
    import ctypes

    import pypdfium2.raw as raw

    values = [ctypes.c_float() for _ in range(4)]
    raw.FPDFPageObj_GetBounds(obj, *[ctypes.byref(v) for v in values])
    return tuple(v.value for v in values)


def page_images(doc, index):
    """第 index 頁上的圖片:[(編號, (左, 下, 右, 上))],使用者座標。"""
    with LOCK:
        page = doc[index]
        try:
            return [(number, _bounds(obj)) for number, obj in enumerate(_image_objects(page))]
        finally:
            page.close()


def image_png(doc, index, number, max_side=1600):
    """第 index 頁第 number 張圖片畫成 PNG(含透明);長邊最多 max_side 像素,拖曳預覽用小一點,複製時給 None 取原解析度。"""
    import io

    with LOCK:
        page = doc[index]
        try:
            objects = _image_objects(page)
            if number >= len(objects):
                return b""
            import pypdfium2 as pdfium_lib

            image = pdfium_lib.PdfImage(objects[number], page=page, pdf=doc)
            bitmap = image.get_bitmap(render=True, scale_to_original=True)
            picture = bitmap.to_pil().convert("RGBA").copy()
            bitmap.close()
        finally:
            page.close()
    if max_side and max(picture.size) > max_side:
        picture.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    picture.save(buffer, "PNG")
    return buffer.getvalue()


def image_rgba(doc, index, number, scale=2.0):
    """第 index 頁第 number 張圖片照頁面上顯示的樣子畫出來(含透明、旋轉),1 點 = scale 像素;
    拖曳預覽用,直接回傳 (寬高, RGBA 像素),不轉成 PNG,按下去的那一刻才不會卡。沒有這張圖時回傳 None。"""
    import ctypes

    import pypdfium2.raw as raw

    with LOCK:
        page = doc[index]
        try:
            objects = _image_objects(page)
            if number >= len(objects):
                return None
            obj = objects[number]
            matrix = raw.FS_MATRIX()
            raw.FPDFPageObj_GetMatrix(obj, ctypes.byref(matrix))
            original = raw.FS_MATRIX(matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f)
            scaled = raw.FS_MATRIX(*(v * scale for v in (matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f)))
            raw.FPDFPageObj_SetMatrix(obj, ctypes.byref(scaled))
            try:
                bitmap = raw.FPDFImageObj_GetRenderedBitmap(doc.raw, page.raw, obj)
            finally:
                raw.FPDFPageObj_SetMatrix(obj, ctypes.byref(original))
            if not bitmap:
                return None
            try:
                stride = raw.FPDFBitmap_GetStride(bitmap)
                size = (raw.FPDFBitmap_GetWidth(bitmap), raw.FPDFBitmap_GetHeight(bitmap))
                data = ctypes.string_at(raw.FPDFBitmap_GetBuffer(bitmap), stride * size[1])
            finally:
                raw.FPDFBitmap_Destroy(bitmap)
        finally:
            page.close()
    from PIL import Image

    image = Image.frombuffer("RGBA", size, data, "raw", "BGRA", stride, 1)
    return size, image.tobytes()


def _apply_images(page, edits):
    """編輯器移動、縮放、刪除過的圖片:只改這次載入的頁面(關掉頁面就還原),原檔不動。
    edits:[(編號, 新的範圍 (左, 下, 右, 上) 或 None 表示刪除)]。"""
    import pypdfium2.raw as raw

    objects = _image_objects(page)
    for number, box in edits:
        if number >= len(objects):
            continue
        obj = objects[number]
        if box is None:
            if raw.FPDFPage_RemoveObject(page.raw, obj):
                raw.FPDFPageObj_Destroy(obj)
            continue
        left, bottom, right, top = _bounds(obj)
        if right - left <= 0 or top - bottom <= 0:
            continue
        sx, sy = (box[2] - box[0]) / (right - left), (box[3] - box[1]) / (top - bottom)
        raw.FPDFPageObj_Transform(obj, sx, 0, 0, sy, box[0] - left * sx, box[1] - bottom * sy)


def render(doc, index, scale, rotation=0, crop=(0, 0, 0, 0), hidden=(), images=(), box=None):
    """把第 index 頁(從 0 起算)畫成 RGB 圖片;scale 為 1 時 1 點 = 1 像素。
    rotation 是在頁面原本方向上再轉的角度;crop 是旋轉後從左、下、右、上各切掉多少點(只畫看得到的範圍時用);
    hidden 是這次不要畫的註解編號(被編輯器改過或刪掉的原註解);images 是移動、刪除過的圖片(見 _apply_images);
    box 是要畫的頁面範圍 (左, 下, 右, 上)(編輯器裁切過的頁面),畫完改回原本的範圍。"""
    with LOCK:
        page = doc[index]
        original = None
        try:
            _apply_hidden(doc, page, index, hidden)
            if images:
                _apply_images(page, images)
            if box is not None:
                original = page.get_cropbox()
                if any(abs(a - b) > 0.01 for a, b in zip(original, box)):
                    page.set_cropbox(*box)
                else:
                    original = None
            bitmap = page.render(scale=scale, rotation=rotation, crop=crop)
            # to_pil 和點陣圖共用記憶體,複製一份再釋放,之後在鎖外面使用才安全
            image = bitmap.to_pil().convert("RGB").copy()
            bitmap.close()
            return image
        finally:
            if original is not None:
                page.set_cropbox(*original)         # 裁切範圍會留在文件裡,改回來才不會影響其他地方
            page.close()


def char_size(text_handle, index):
    """一個字實際的字級(點)。PDFium 回報的字級沒有算進頁面的縮放:例如 Chrome 產生的 PDF
    整頁縮小成 0.75 倍,回報 13.44 的字實際只有 10.08,所以要再乘上這個字的變換矩陣的縮放比例。"""
    import ctypes

    import pypdfium2.raw as raw

    size = abs(raw.FPDFText_GetFontSize(text_handle, index))
    matrix = raw.FS_MATRIX()
    if raw.FPDFText_GetMatrix(text_handle, index, ctypes.byref(matrix)):
        scale = abs(matrix.a * matrix.d - matrix.b * matrix.c) ** 0.5
        if scale > 1e-6:
            size *= scale
    return size


class TextLookup:
    """查詢一頁的文字位置(螢光筆、底線對齊文字行用);座標是 PDF 使用者座標。用完要 close()。"""

    def __init__(self, doc, index):
        with LOCK:
            self.page = doc[index]
            self.text = self.page.get_textpage()
            self.count = self.text.count_chars()
        self._boxes = None

    def _box_list(self):
        if self._boxes is None:
            with LOCK:
                self._boxes = [self.text.get_charbox(i) for i in range(self.count)]
        return self._boxes

    def index_at(self, x, y, tolerance=4.0):
        """(x, y) 上的字;沒有剛好點在字上時找最近的字,離太遠(超過 tolerance 的 6 倍)回傳 None。"""
        if not self.count:
            return None
        with LOCK:
            found = self.text.get_index(x, y, tolerance, tolerance)
        if found is not None and found >= 0:
            return found
        best, best_distance = None, tolerance * 6
        for i, (left, bottom, right, top) in enumerate(self._box_list()):
            if right <= left and top <= bottom:
                continue
            dx = max(left - x, 0, x - right)
            dy = max(bottom - y, 0, y - top)
            distance = (dx * dx + dy * dy) ** 0.5
            if distance < best_distance:
                best, best_distance = i, distance
        return best

    def on_text(self, x, y, tolerance=1.5):
        """(x, y) 是不是剛好在字上(不找附近的字);選取工具用來分辨點的是文字還是空白處。"""
        if not self.count:
            return False
        with LOCK:
            found = self.text.get_index(x, y, tolerance, tolerance)
        return found is not None and found >= 0

    def rects(self, first, last):
        """第 first 到 last 個字(含)占的範圍,同一行合併成一個 (左, 下, 右, 上)。"""
        first, last = sorted((first, last))
        with LOCK:
            count = self.text.count_rects(first, last - first + 1)
            return [self.text.get_rect(i) for i in range(count)]

    def line_boxes(self, first, last):
        """第 first 到 last 個字(含)實際佔的範圍,同一行合併:[(左, 下, 右, 上)]。

        不用 PDFium 的字框:有些字型的字框比字本身大很多(14 點的字框寬 60 點),拿來蓋字會連旁邊的字一起蓋掉。
        改用每個字的起點與字級:左右是起點到下一個字的起點,上下是底線往下 0.22、往上 0.88 個字級。
        """
        import ctypes

        import pypdfium2.raw as raw

        first, last = sorted((first, last))
        chars = []
        with LOCK:
            handle = self.text.raw
            for index in range(first, min(last + 1, self.count)):
                code = raw.FPDFText_GetUnicode(handle, index)
                if code in (0, 10, 13):
                    continue
                x, y = ctypes.c_double(), ctypes.c_double()
                raw.FPDFText_GetCharOrigin(handle, index, ctypes.byref(x), ctypes.byref(y))
                size = char_size(handle, index) or 10.0
                left, bottom, right, top = self.text.get_charbox(index)
                chars.append((x.value, y.value, size, right, code == 32))
        lines = []
        for position, (x, y, size, box_right, space) in enumerate(chars):
            following = chars[position + 1] if position + 1 < len(chars) else None
            same_line = following is not None and abs(following[1] - y) < size * 0.3 and following[0] > x
            right = following[0] if same_line else min(max(box_right, x + size * 0.3), x + size * 1.05)
            if space and not same_line:
                continue            # 行尾的空白不算
            box = [x, y - size * 0.22, max(right, x + 0.5), y + size * 0.88]
            if lines and abs(lines[-1][1] - box[1]) < size * 0.3 and box[0] >= lines[-1][0] - 1:
                line = lines[-1]
                line[0], line[1] = min(line[0], box[0]), min(line[1], box[1])
                line[2], line[3] = max(line[2], box[2]), max(line[3], box[3])
            else:
                lines.append(box)
        return [tuple(line) for line in lines]

    def text_of(self, first, last):
        """第 first 到 last 個字(含)的文字;PDFium 自己補的換行換成空白。"""
        first, last = sorted((first, last))
        with LOCK:
            text = self.text.get_text_range(first, last - first + 1)
        return " ".join(part.strip() for part in text.replace("\r\n", "\n").split("\n") if part.strip())

    def char_style(self, index):
        """一個字的樣式:(字級, 顏色 (R, G, B), 字型名稱, 是不是有襯線, 是不是粗體, 底線的 y)。"""
        import ctypes

        import pypdfium2.raw as raw

        with LOCK:
            handle = self.text.raw
            size = char_size(handle, index)
            red, green, blue, alpha = (ctypes.c_uint() for _ in range(4))
            raw.FPDFText_GetFillColor(handle, index, *[ctypes.byref(v) for v in (red, green, blue, alpha)])
            buffer = ctypes.create_string_buffer(256)
            flags = ctypes.c_int()
            raw.FPDFText_GetFontInfo(handle, index, buffer, 256, ctypes.byref(flags))
            weight = raw.FPDFText_GetFontWeight(handle, index)
            x, y = ctypes.c_double(), ctypes.c_double()
            raw.FPDFText_GetCharOrigin(handle, index, ctypes.byref(x), ctypes.byref(y))
        name = buffer.value.decode("utf-8", "replace")
        bold = weight >= 600 or "Bold" in name or bool(flags.value & (1 << 18))
        return size, (red.value, green.value, blue.value), name, bool(flags.value & 2), bold, y.value

    def close(self):
        with LOCK:
            self.text.close()
            self.page.close()


def close(doc):
    with LOCK:
        key = id(doc)
        for item in [k for k in _original_flags if k[0] == key]:
            del _original_flags[item]
        for item in [k for k in _touched_pages if k[0] == key]:
            _touched_pages.discard(item)
        doc.close()
