"""掃描檔辨識文字:整頁都是圖片、沒有文字的頁面,用 Tesseract 在本地辨識,存檔時加上看不見的文字層。

外觀完全不變(字是透明的,疊在圖片上同樣的位置),存檔後任何閱讀器都能搜尋、選字、複製;
還沒存檔前,編輯器的搜尋也找得到辨識出來的字。辨識結果記在 PageRef.ocr,和其他修改共用復原紀錄。
"""

import threading
from dataclasses import replace

import pygame

from core import pdfium, theme, widgets
from core.widgets import Button, draw_text, rounded_panel

from . import geometry

BAR_H = 44


def ocr_module():
    from core import ocr

    return ocr


class OcrBanner:
    """有掃描頁時顯示在頁面下方的提示條:「辨識文字」「不用了」。"""

    def __init__(self, page, accent):
        self.page = page
        self.accent = accent
        self.dismissed = False
        self.rect = pygame.Rect(0, 0, 0, 0)
        self._scans = {}            # (來源, 第幾頁) → 是不是掃描頁;在背景一頁一頁判斷
        self._generation = 0
        self._checking = False
        self.btn_run = Button("辨識文字", accent=accent, size=13)
        self.btn_close = Button("不用了", filled=False, size=13)

    def reset(self):
        self.dismissed = False
        self._generation += 1
        self._scans = {}
        self._checking = False

    def _check(self):
        """在背景判斷每一頁是不是掃描頁(要解析頁面,幾百頁放在畫面上判斷會卡住)。"""
        keys = [(ref.source, ref.index) for ref in self.page.pages
                if ref.kind == "pdf" and (ref.source, ref.index) not in self._scans]
        if self._checking or not keys:
            return
        self._checking = True
        docs, generation, scans = dict(self.page.docs), self._generation, self._scans

        def work():
            for source, index in keys:
                if generation != self._generation:
                    return
                doc = docs.get(source)
                try:
                    scans[(source, index)] = doc is not None and pdfium.is_scan(doc, index)
                except Exception:
                    scans[(source, index)] = False
            if generation == self._generation:
                self._checking = False

        threading.Thread(target=work, daemon=True).start()

    def waiting(self):
        """還沒辨識的掃描頁(頁面索引);還沒判斷完的頁面先不算。"""
        self._check()
        return [i for i, ref in enumerate(self.page.pages)
                if ref.kind == "pdf" and not ref.ocr and self._scans.get((ref.source, ref.index))]

    # ------------------------------------------------------------ 辨識

    def start(self):
        indexes = self.waiting()
        if not indexes:
            self.page.notify("沒有需要辨識的掃描頁", theme.TEXT_DIM)
            return
        ocr = ocr_module()
        missing = ocr.missing()
        if missing:
            # 第一次使用:說明要下載的元件,同意後自動下載,下載完接著辨識
            self.page.app.consent.open("PDF 編輯器的文字辨識", missing, on_done=self.start)
            return
        refs = [self.page.pages[i] for i in indexes]
        docs = dict(self.page.docs)
        total = len(refs)

        def work():
            found = {}
            for number, ref in enumerate(refs, start=1):
                self.page.task = (self.page.task[0], f"辨識文字中…(第 {number} / {total} 頁)")
                scale = ocr.DPI / 72
                image = pdfium.render(docs[ref.source], ref.index, scale, box=geometry.user_box(ref))
                words = []
                for word in ocr.recognize(image):
                    box = (word.left / scale, word.top / scale, word.right / scale, word.bottom / scale)
                    words.append((word.text, box, word.line_bottom / scale))
                found[ref.uid] = tuple(words)
            return found

        def done(found):
            pages = [replace(page, ocr=found[page.uid]) if page.uid in found else page
                     for page in self.page.pages]
            count = sum(1 for words in found.values() if words)
            if self.page._change(pages, message=f"已辨識 {count} 頁的文字，儲存後就能搜尋、選字"):
                self.dismissed = True

        self.page._run(f"辨識文字中…(共 {total} 頁)", work, done)

    # ------------------------------------------------------------ 事件、繪製

    def handle_event(self, event, pos) -> bool:
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1 or not self.rect.collidepoint(pos):
            return False
        if self.btn_run.clicked(pos, True):
            self.start()
        elif self.btn_close.clicked(pos, True):
            self.dismissed = True
        return True

    def draw(self, screen, area, mouse_pos):
        self.rect = pygame.Rect(0, 0, 0, 0)
        if self.dismissed or self.page.busy:
            return
        count = len(self.waiting())
        if not count:
            return
        rect = pygame.Rect(area.x + 16, area.bottom - BAR_H - 14, area.width - 44, BAR_H)
        self.rect = rect
        rounded_panel(screen, rect, theme.PANEL_LIGHT, radius=10, alpha=250, border=self.accent)
        text = f"有 {count} 頁是掃描的圖片，沒有文字；辨識文字後就能搜尋、選字(在本地辨識，外觀不變)"
        draw_text(screen, widgets.clip_text(text, 13, rect.width - 220), (rect.x + 14, rect.centery - 9), 13, theme.TEXT)
        self.btn_close.draw(screen, pygame.Rect(rect.right - 10 - 70, rect.y + 6, 70, 32), mouse_pos)
        self.btn_run.draw(screen, pygame.Rect(rect.right - 10 - 70 - 8 - 90, rect.y + 6, 90, 32), mouse_pos)
