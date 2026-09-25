"""書籤(目錄):左側欄切到「書籤」時顯示,點一下跳到那一頁,按兩下改名;可以加入、刪除、調整層級。

書籤記在它指到的那一頁上(PageRef.bookmarks,每一個是 (標題, 層級)),頁面搬動、刪除時書籤跟著走,
也和其他修改共用復原紀錄。整份文件的書籤順序是頁面順序,同一頁裡照加入的先後。
"""

import pygame

from core import theme, widgets
from core.widgets import Button, draw_text, rounded_panel

from . import model

ROW_H = 30
INDENT = 16
OPS_H = 92
DOUBLE_CLICK_MS = 400


def flatten(pages):
    """整份文件的書籤:[(第幾頁, 這一頁的第幾個, 標題, 層級)]。"""
    return [(index, number, title, level)
            for index, page in enumerate(pages) for number, (title, level) in enumerate(page.bookmarks)]


class BookmarkPanel:
    def __init__(self, page, accent):
        self.page = page
        self.accent = accent
        self.selected = None        # (頁面 uid, 這一頁的第幾個)
        self.scroll = 0
        self.area = pygame.Rect(0, 0, 0, 0)
        self._rows = []
        self._last_click = (-10000, None)
        self.btn_add = Button("加入書籤", accent=accent, size=13)
        self.btn_rename = Button("改名", filled=False, size=13)
        self.btn_delete = Button("刪除", filled=False, size=13)
        self.btn_out = Button("往外", filled=False, size=13)     # 層級往外(變成上一層)
        self.btn_in = Button("往內", filled=False, size=13)

    def reset(self):
        self.selected = None
        self.scroll = 0

    def _find(self):
        """選取的書籤:(第幾頁, 這一頁的第幾個) ;不存在時回傳 None。"""
        if self.selected is None:
            return None
        uid, number = self.selected
        index = self.page.index_of(uid)
        if index is None or number >= len(self.page.pages[index].bookmarks):
            return None
        return index, number

    def _set(self, index, marks, message, select=None):
        pages = model.set_bookmarks(self.page.pages, index, marks)
        if self.page._change(pages, message=message) and select is not None:
            self.selected = (self.page.pages[index].uid, select)

    # ------------------------------------------------------------ 動作

    def add(self):
        index = self.page.current_index()
        if not self.page.pages:
            return
        uid = self.page.pages[index].uid
        # 新書籤的層級和它前面那一個一樣
        before = [entry for entry in flatten(self.page.pages) if entry[0] <= index]
        level = before[-1][3] if before else 0

        def choice(key, text):
            where = self.page.index_of(uid)
            if key != "ok" or where is None or not text.strip():
                return
            marks = self.page.pages[where].bookmarks + ((text.strip(), level),)
            self._set(where, marks, "已加入書籤", len(marks) - 1)

        self.page.dialog.open("加入書籤", [f"書籤會指到第 {index + 1} 頁(目前看的這一頁)。"],
                              [("cancel", "取消", False), ("ok", "加入", True)], choice,
                              field="書籤名稱", value=f"第 {index + 1} 頁")

    def rename(self):
        found = self._find()
        if found is None:
            return
        index, number = found
        uid = self.page.pages[index].uid
        title, level = self.page.pages[index].bookmarks[number]

        def choice(key, text):
            where = self.page.index_of(uid)
            if key != "ok" or where is None or not text.strip():
                return
            marks = list(self.page.pages[where].bookmarks)
            if number < len(marks):
                marks[number] = (text.strip(), marks[number][1])
                self._set(where, marks, "已修改書籤名稱", number)

        self.page.dialog.open("書籤改名", [], [("cancel", "取消", False), ("ok", "確定", True)], choice,
                              field="書籤名稱", value=title)

    def delete(self):
        found = self._find()
        if found is None:
            return
        index, number = found
        marks = list(self.page.pages[index].bookmarks)
        del marks[number]
        self.selected = None
        self._set(index, marks, "已刪除書籤")

    def shift(self, step):
        """調整層級:往內一層最多比前一個書籤深一層,往外最少到最外層。"""
        found = self._find()
        if found is None:
            return
        index, number = found
        entries = flatten(self.page.pages)
        position = next(i for i, entry in enumerate(entries) if entry[:2] == (index, number))
        title, level = self.page.pages[index].bookmarks[number]
        limit = entries[position - 1][3] + 1 if position > 0 else 0
        new_level = max(0, min(limit, level + step))
        if new_level == level:
            return
        marks = list(self.page.pages[index].bookmarks)
        marks[number] = (title, new_level)
        self._set(index, marks, "已調整書籤層級", number)

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, pos) -> bool:
        if event.type == pygame.MOUSEWHEEL and self.area.collidepoint(pygame.mouse.get_pos()):
            self.scroll = max(0, self.scroll - event.y * ROW_H * 2)
            return True
        if event.type == pygame.KEYDOWN and self._find() is not None:
            if event.key == pygame.K_DELETE:
                self.delete()
                return True
            if event.key == pygame.K_F2:
                self.rename()
                return True
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        for button, action in ((self.btn_add, self.add), (self.btn_rename, self.rename), (self.btn_delete, self.delete),
                               (self.btn_out, lambda: self.shift(-1)), (self.btn_in, lambda: self.shift(1))):
            if button.clicked(pos, True):
                action()
                return True
        for key, row in self._rows:
            if row.collidepoint(pos):
                now = pygame.time.get_ticks()
                double = self._last_click[1] == key and now - self._last_click[0] < DOUBLE_CLICK_MS
                self._last_click = (now, key)
                self.selected = key
                index = self.page.index_of(key[0])
                if index is not None:
                    self.page.scroll_to_page(index)
                if double:
                    self.rename()
                return True
        if self.area.collidepoint(pos):
            self.selected = None
            return True
        return False

    # ------------------------------------------------------------ 繪製

    def draw(self, screen, rect, mouse_pos):
        """rect 是左側欄標題下面的整塊範圍(含下方按鈕)。"""
        area = pygame.Rect(rect.x, rect.y, rect.width, rect.height - OPS_H)
        self.area = area
        entries = flatten(self.page.pages)
        self.scroll = max(0, min(self.scroll, len(entries) * ROW_H + 12 - area.height))
        self._rows = []
        found = self._find()
        if not entries:
            for number, line in enumerate(("還沒有書籤", "翻到要標記的頁面，", "再按下面的「加入書籤」")):
                draw_text(screen, line, (area.centerx, area.y + 40 + number * 22), 13,
                          theme.TEXT_DIM if number == 0 else theme.TEXT_FAINT, center=True)
        previous = screen.get_clip()
        screen.set_clip(area)
        current = self.page.current_index()
        for number, (index, position, title, level) in enumerate(entries):
            row = pygame.Rect(area.x + 8, area.y + 6 + number * ROW_H - self.scroll, area.width - 16, ROW_H - 2)
            if row.bottom < area.y or row.top > area.bottom:
                continue
            key = (self.page.pages[index].uid, position)
            chosen = found == (index, position)
            if chosen:
                rounded_panel(screen, row, tuple(int(c * 0.28) for c in self.accent), radius=6, border=self.accent)
            elif row.collidepoint(mouse_pos):
                rounded_panel(screen, row, theme.PANEL_LIGHT, radius=6)
            x = row.x + 8 + level * INDENT
            color = self.accent if chosen or index == current else theme.TEXT
            label = widgets.clip_text(title, 13, row.right - x - 34)
            draw_text(screen, label, (x, row.centery - 9), 13, color)
            draw_text(screen, str(index + 1), (row.right - 8, row.centery - 8), 11, theme.TEXT_FAINT, right=True)
            self._rows.append((key, row))
        screen.set_clip(previous)
        ops = pygame.Rect(rect.x, area.bottom, rect.width, OPS_H)
        pygame.draw.line(screen, theme.PANEL_EDGE, (ops.x + 10, ops.y), (ops.right - 10, ops.y))
        x, y = ops.x + 12, ops.y + 10
        full = ops.width - 24
        self.btn_add.enabled = bool(self.page.pages)
        self.btn_add.draw(screen, pygame.Rect(x, y, full, 32), mouse_pos)
        small = (full - 18) // 4
        for number, button in enumerate((self.btn_rename, self.btn_delete, self.btn_out, self.btn_in)):
            button.enabled = found is not None
            button.draw(screen, pygame.Rect(x + number * (small + 6), y + 38, small, 32), mouse_pos)
