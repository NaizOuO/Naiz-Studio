"""PDF 編輯器的搜尋文字:Ctrl+F 打開,在背景一頁一頁找,找到的地方在頁面上標出來。

Enter 跳到下一個、Shift+Enter 上一個,Esc 關掉。找的是 PDF 本身的文字(不分大小寫);
掃描檔要先辨識文字才找得到。
"""

import threading

import pygame

from core import pdfium, theme
from core.widgets import TextInput, draw_text, rounded_panel

from . import geometry

BAR_W = 380
BAR_H = 44
HIT_COLOR = (255, 214, 0, 90)


class SearchBar:
    def __init__(self, page, accent):
        self.page = page
        self.accent = accent
        self.is_open = False
        self.input = TextInput(placeholder="搜尋文字", accent=accent, size=14)
        self.results = []           # [(頁面 uid, 範圍)],範圍是頁面座標
        self.current = -1
        self.searching = False
        self.rect = pygame.Rect(0, 0, 0, 0)
        self._buttons = {}
        self._query = ""
        self._generation = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 開關

    def open(self):
        self.is_open = True
        self.input.focus()
        self.input.select_all()

    def close(self):
        self.is_open = False
        self.input.blur()
        self._generation += 1
        with self._lock:
            self.results = []
        self.current = -1
        self._query = ""
        self.searching = False

    def reset(self):
        """換檔案時清掉結果。"""
        if self.is_open:
            self.close()

    # ------------------------------------------------------------ 搜尋

    def _start(self, query):
        self._query = query
        self._generation += 1
        generation = self._generation
        with self._lock:
            self.results = []
        self.current = -1
        if not query.strip():
            self.searching = False
            return
        self.searching = True
        pages = [(ref.uid, ref) for ref in self.page.pages]
        docs = dict(self.page.docs)
        threading.Thread(target=self._search, args=(query, pages, docs, generation), daemon=True).start()

    def _search(self, query, pages, docs, generation):
        for uid, ref in pages:
            if generation != self._generation:
                return
            doc = docs.get(ref.source) if ref.kind == "pdf" else None
            found = []
            if doc is None and not ref.ocr:
                continue
            try:
                if doc is None:
                    raise LookupError
                with pdfium.LOCK:
                    page = doc[ref.index]
                    text = page.get_textpage()
                    try:
                        searcher = text.search(query, match_case=False)
                        while True:
                            hit = searcher.get_next()
                            if hit is None:
                                break
                            count = text.count_rects(*hit)
                            found.append([text.get_rect(i) for i in range(count)])
                        searcher.close()
                    finally:
                        text.close()
                        page.close()
            except Exception:
                found = []
            to_page = geometry.ref_from_user(ref)
            needle = query.casefold()
            ocr_hits = [(box,) for text, box, _ in ref.ocr if needle in text.casefold()]    # 辨識出來、還沒存檔的字
            with self._lock:
                if generation != self._generation:
                    return
                for rects in found:
                    boxes = tuple(geometry.transform_box(to_page, rect) for rect in rects)
                    if boxes:
                        self.results.append((uid, boxes))
                self.results.extend((uid, boxes) for boxes in ocr_hits)
            found = found or ocr_hits
            if found and self.current < 0 and generation == self._generation:
                self.current = 0
                self._show_current()
        if generation == self._generation:
            self.searching = False

    def go(self, step):
        with self._lock:
            count = len(self.results)
        if not count:
            return
        self.current = (self.current + step) % count
        self._show_current()

    def _show_current(self):
        with self._lock:
            if not 0 <= self.current < len(self.results):
                return
            uid, boxes = self.results[self.current]
        index = self.page.index_of(uid)
        if index is not None:
            self.page.scroll_to_box(index, boxes[0])

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, pos) -> bool:
        """回傳 True 代表事件被搜尋列用掉了。"""
        if event.type == pygame.KEYDOWN and event.mod & pygame.KMOD_CTRL and event.key == pygame.K_f:
            self.open()
            return True
        if not self.is_open:
            return False
        if event.type == pygame.KEYDOWN and event.key == pygame.K_F3:
            self.go(-1 if event.mod & pygame.KMOD_SHIFT else 1)
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for name, rect in self._buttons.items():
                if rect.collidepoint(pos):
                    if name == "close":
                        self.close()
                    else:
                        self.go(-1 if name == "prev" else 1)
                    return True
            if not self.rect.collidepoint(pos):
                self.input.blur()
                return False
        if self.input.focused and event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE and not self.input.composition:
                self.close()
                return True
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and not self.input.composition:
                if self.input.text != self._query:
                    self._start(self.input.text)
                else:
                    self.go(-1 if event.mod & pygame.KMOD_SHIFT else 1)
                return True
        if self.input.handle(event, pos):
            self._start(self.input.text)
        if self.input.focused and event.type in (pygame.KEYDOWN, pygame.TEXTINPUT, pygame.TEXTEDITING):
            return True
        return event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP) and self.rect.collidepoint(pos)

    # ------------------------------------------------------------ 繪製

    def draw_page(self, screen, ref, mapper):
        """在這一頁標出找到的地方;目前這一個加上框線。"""
        if not self.is_open:
            return
        with self._lock:
            hits = [(i, boxes) for i, (uid, boxes) in enumerate(self.results) if uid == ref.uid]
        for i, boxes in hits:
            for box in boxes:
                area = mapper.box(box).inflate(2, 2)
                layer = pygame.Surface(area.size, pygame.SRCALPHA)
                layer.fill(HIT_COLOR if i != self.current else (255, 150, 0, 110))
                screen.blit(layer, area.topleft)
                if i == self.current:
                    pygame.draw.rect(screen, (230, 110, 0), area, 2)

    def draw(self, screen, area, mouse_pos):
        if not self.is_open:
            return
        rect = pygame.Rect(area.right - BAR_W - 24, area.y + 10, BAR_W, BAR_H)
        self.rect = rect
        rounded_panel(screen, rect, theme.PANEL_LIGHT, radius=10, alpha=250, border=self.accent)
        self.input.draw(screen, pygame.Rect(rect.x + 8, rect.y + 7, 190, 30), mouse_pos)
        with self._lock:
            count = len(self.results)
        if not self._query:
            status = ""
        elif count:
            status = f"{self.current + 1} / {count}" + (" …" if self.searching else "")
        else:
            status = "搜尋中…" if self.searching else "找不到"
        draw_text(screen, status, (rect.x + 206, rect.centery - 9), 13,
                  theme.TEXT_DIM if count or self.searching else theme.WARN)
        self._buttons = {}
        x = rect.right - 8 - 30 * 3
        for name in ("prev", "next", "close"):
            button = pygame.Rect(x, rect.y + 7, 28, 30)
            hover = button.collidepoint(mouse_pos)
            rounded_panel(screen, button, theme.PANEL if hover else theme.BG_DEEP, radius=6, alpha=220,
                          border=theme.TEXT_FAINT if hover else theme.PANEL_EDGE)
            cx, cy = button.center
            if name == "prev":
                pygame.draw.lines(screen, theme.TEXT, False, [(cx - 5, cy + 3), (cx, cy - 3), (cx + 5, cy + 3)], 2)
            elif name == "next":
                pygame.draw.lines(screen, theme.TEXT, False, [(cx - 5, cy - 3), (cx, cy + 3), (cx + 5, cy - 3)], 2)
            else:
                pygame.draw.line(screen, theme.TEXT, (cx - 5, cy - 5), (cx + 5, cy + 5), 2)
                pygame.draw.line(screen, theme.TEXT, (cx - 5, cy + 5), (cx + 5, cy - 5), 2)
            self._buttons[name] = button
            x += 30
