"""PDF 編輯器的資料:頁面清單(每一頁記錄來源與旋轉)和復原、重做。

原檔不會被改動:編輯時只改這份清單,儲存時才依清單產生新檔。之後的註解、改字也會記在這裡。
"""

import itertools
from dataclasses import dataclass, replace

from . import annots as annot_mod
from . import geometry

HISTORY_LIMIT = 200
A4 = (595.0, 842.0)
_ids = itertools.count(1)


@dataclass(frozen=True)
class PageRef:
    kind: str                   # pdf:來自 PDF 的頁面;blank:空白頁;image:插入的圖片
    source: str = ""            # PDF 或圖片的路徑
    index: int = 0              # 在來源 PDF 裡是第幾頁(從 0 起算)
    size: tuple = A4            # 原本方向的寬高(點),已經包含頁面本身的旋轉
    base_rotation: int = 0      # 來源頁面本身設定的旋轉角度
    rotation: int = 0           # 編輯時另外轉的角度
    uid: int = 0                # 每一頁獨一無二的編號;複製出來的頁面也是新的編號
    origin: tuple = (0.0, 0.0)  # 頁面框左下角在 PDF 裡的座標(大多是 0, 0)
    annots: tuple = ()          # 目前的註解(annots.Annot),座標見 geometry 的說明
    originals: tuple = ()       # 開檔時讀到的註解;沒被改過的原註解儲存時原封不動保留
    full: tuple = ()            # 裁切過的頁面:裁切前的 (寬高, 頁面框原點),還原用;沒裁切過是空的
    bookmarks: tuple = ()       # 指到這一頁的書籤:((標題, 層級), ...),依加入的先後
    ocr: tuple = ()             # 掃描頁辨識出來的字:((文字, 範圍, 底線 y), ...),頁面座標;存檔時變成看不見的文字層

    @property
    def shown_size(self):
        width, height = self.size
        return (height, width) if self.rotation % 180 else (width, height)


def new_ref(kind, **fields):
    return PageRef(kind, uid=next(_ids), **fields)


def rotate(pages, indexes, degrees):
    chosen = set(indexes)
    return [replace(page, rotation=(page.rotation + degrees) % 360) if i in chosen else page
            for i, page in enumerate(pages)]


def delete(pages, indexes):
    chosen = set(indexes)
    kept = [page for i, page in enumerate(pages) if i not in chosen]
    if not kept:
        raise ValueError("至少要保留 1 頁")
    return kept


def duplicate(pages, indexes):
    """選取的每一頁複製一份放在它後面;回傳 (新清單, 複製出來的頁面索引)。"""
    chosen = set(indexes)
    result, copies = [], []
    for i, page in enumerate(pages):
        result.append(page)
        if i in chosen:
            result.append(replace(page, uid=next(_ids)))
            copies.append(len(result) - 1)
    return result, copies


def crop(pages, indexes, box):
    """把選取的頁面裁成 box(頁面座標,左上為原點);box 超出某一頁時只取那一頁裡面的部分。
    註解跟著平移,位置看起來不變。回傳新的頁面清單。"""
    chosen = set(indexes)
    result = []
    for i, page in enumerate(pages):
        if i not in chosen:
            result.append(page)
            continue
        width, height = page.size
        x0, y0 = max(0.0, box[0]), max(0.0, box[1])
        x1, y1 = min(width, box[2]), min(height, box[3])
        if x1 - x0 < 10 or y1 - y0 < 10:
            result.append(page)
            continue
        corners = [geometry.apply(geometry.ref_to_user(page), p) for p in ((x0, y0), (x1, y1))]
        origin = (min(c[0] for c in corners), min(c[1] for c in corners))
        size = (x1 - x0, y1 - y0)
        full = page.full or (page.size, page.origin)
        result.append(replace(page, size=size, origin=origin, full=full,
                              annots=tuple(annot_mod.translated(a, -x0, -y0) for a in page.annots),
                              originals=tuple(annot_mod.translated(a, -x0, -y0) for a in page.originals)))
    return result


def uncrop(pages, indexes):
    """還原選取頁面裁切前的大小;註解跟著平移回去。"""
    chosen = set(indexes)
    result = []
    for i, page in enumerate(pages):
        if i not in chosen or not page.full:
            result.append(page)
            continue
        size, origin = page.full
        restored = replace(page, size=size, origin=origin, full=())
        # 舊的左上角在還原後的頁面座標
        corner = geometry.apply(geometry.ref_from_user(restored),
                                geometry.apply(geometry.ref_to_user(page), (0.0, 0.0)))
        dx, dy = corner
        result.append(replace(restored, annots=tuple(annot_mod.translated(a, dx, dy) for a in page.annots),
                              originals=tuple(annot_mod.translated(a, dx, dy) for a in page.originals)))
    return result


def set_bookmarks(pages, index, marks):
    return [replace(page, bookmarks=tuple(marks)) if i == index else page for i, page in enumerate(pages)]


def set_annots(pages, index, annots):
    return [replace(page, annots=tuple(annots)) if i == index else page for i, page in enumerate(pages)]


def insert(pages, at, refs):
    at = max(0, min(len(pages), at))
    refs = list(refs)
    return list(pages[:at]) + refs + list(pages[at:]), list(range(at, at + len(refs)))


class History:
    """頁面清單的復原、重做;記住上次儲存時的樣子,用來判斷有沒有未儲存的變更。"""

    def __init__(self, pages=()):
        self.reset(pages)

    def reset(self, pages):
        self.pages = list(pages)
        self._undo, self._redo = [], []
        self._saved = tuple(self.pages)

    def apply(self, pages):
        """換成新的頁面清單;和目前一樣時不記錄,回傳 False。"""
        if tuple(pages) == tuple(self.pages):
            return False
        self._undo.append(tuple(self.pages))
        del self._undo[:-HISTORY_LIMIT]
        self._redo.clear()
        self.pages = list(pages)
        return True

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    def undo(self):
        if self._undo:
            self._redo.append(tuple(self.pages))
            self.pages = list(self._undo.pop())

    def redo(self):
        if self._redo:
            self._undo.append(tuple(self.pages))
            self.pages = list(self._redo.pop())

    @property
    def dirty(self):
        return tuple(self.pages) != self._saved

    def mark_saved(self):
        self._saved = tuple(self.pages)

    def patch(self, change):
        """每一個版本(目前、復原、重做、上次儲存)的每一頁都套用 change;用在補上晚一點才讀到的資料
        (例如頁面第一次顯示時才找的原檔圖片),不算一次修改,也不影響「有沒有未儲存的變更」。"""
        self.pages = [change(page) for page in self.pages]
        self._undo = [tuple(change(page) for page in pages) for pages in self._undo]
        self._redo = [tuple(change(page) for page in pages) for pages in self._redo]
        self._saved = tuple(change(page) for page in self._saved)
