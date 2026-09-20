"""PDF 編輯器的資料:頁面清單(每一頁記錄來源與旋轉)和復原、重做。

原檔不會被改動:編輯時只改這份清單,儲存時才依清單產生新檔。之後的註解、改字也會記在這裡。
"""

import itertools
from dataclasses import dataclass, replace

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
