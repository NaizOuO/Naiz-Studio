"""PDFium(pypdfium2)的共用入口:開啟 PDF、把頁面畫成圖片。

PDFium 不能同時在多個執行緒使用(實測會讓整個程式直接崩潰),所以所有呼叫都要經過這裡並先取得 LOCK。
例如縮圖在背景畫,同時另一個執行緒正在把頁面輸出成圖片。
"""

import threading

LOCK = threading.RLock()


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


def render(doc, index, scale, rotation=0, crop=(0, 0, 0, 0)):
    """把第 index 頁(從 0 起算)畫成 RGB 圖片;scale 為 1 時 1 點 = 1 像素。
    rotation 是在頁面原本方向上再轉的角度;crop 是旋轉後從左、下、右、上各切掉多少點(只畫看得到的範圍時用)。"""
    with LOCK:
        page = doc[index]
        try:
            bitmap = page.render(scale=scale, rotation=rotation, crop=crop)
            # to_pil 和點陣圖共用記憶體,複製一份再釋放,之後在鎖外面使用才安全
            image = bitmap.to_pil().convert("RGB").copy()
            bitmap.close()
            return image
        finally:
            page.close()


def close(doc):
    with LOCK:
        doc.close()
