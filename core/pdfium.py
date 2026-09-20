"""PDFium(pypdfium2)的共用入口:開啟 PDF、把頁面畫成圖片、查詢文字位置。

PDFium 不能同時在多個執行緒使用(實測會讓整個程式直接崩潰),所以所有呼叫都要經過這裡並先取得 LOCK。
例如縮圖在背景畫,同時另一個執行緒正在把頁面輸出成圖片。
"""

import threading

LOCK = threading.RLock()
_original_flags = {}        # (文件, 頁, 第幾個註解) → 開檔時的旗標;畫面暫時藏起註解後要能還原
_touched_pages = set()


def open_document(path, password=None):
    import pypdfium2

    with LOCK:
        return pypdfium2.PdfDocument(str(path), password=password)


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


def render(doc, index, scale, rotation=0, crop=(0, 0, 0, 0), hidden=()):
    """把第 index 頁(從 0 起算)畫成 RGB 圖片;scale 為 1 時 1 點 = 1 像素。
    rotation 是在頁面原本方向上再轉的角度;crop 是旋轉後從左、下、右、上各切掉多少點(只畫看得到的範圍時用);
    hidden 是這次不要畫的註解編號(被編輯器改過或刪掉的原註解)。"""
    with LOCK:
        page = doc[index]
        try:
            _apply_hidden(doc, page, index, hidden)
            bitmap = page.render(scale=scale, rotation=rotation, crop=crop)
            # to_pil 和點陣圖共用記憶體,複製一份再釋放,之後在鎖外面使用才安全
            image = bitmap.to_pil().convert("RGB").copy()
            bitmap.close()
            return image
        finally:
            page.close()


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

    def rects(self, first, last):
        """第 first 到 last 個字(含)占的範圍,同一行合併成一個 (左, 下, 右, 上)。"""
        first, last = sorted((first, last))
        with LOCK:
            count = self.text.count_rects(first, last - first + 1)
            return [self.text.get_rect(i) for i in range(count)]

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
