"""在頁面上直接編輯多行文字(文字框、便利貼):游標、選取、剪貼簿、輸入法組字(例如注音)。

排版由呼叫的地方提供(fonts.Layout),這裡只處理文字和游標,所以文字框和便利貼可以共用。
"""

import pygame

from core.widgets import _clipboard_text, _is_word_char, _set_clipboard

from .fonts import Layout, arrange

DOUBLE_CLICK_MS = 450


def simple_layout(text, font, max_width):
    """用介面字型(pygame)排版,便利貼的編輯框用。"""
    advances = [0 if ch == "\n" else font.size(ch)[0] for ch in text]
    return Layout(arrange(text, advances, None, max_width), font.get_ascent(), font.get_linesize())


class TextEditor:
    def __init__(self, text=""):
        self.text = text
        self.cursor = self.anchor = len(text)
        self.composition = ""           # 輸入法組字中、還沒選字的文字
        self.composition_cursor = 0
        self.dragging = False
        self._last_click = (-10000, -1)
        self._clicks = 0

    @property
    def selection(self):
        return min(self.anchor, self.cursor), max(self.anchor, self.cursor)

    def shown(self):
        """畫面上顯示的文字(組字中的字插在游標位置)。"""
        return self.text[:self.cursor] + self.composition + self.text[self.cursor:]

    def caret_index(self):
        return self.cursor + (self.composition_cursor if self.composition else 0)

    # ------------------------------------------------------------ 修改

    def _delete_selection(self):
        start, end = self.selection
        if start == end:
            return False
        self.text = self.text[:start] + self.text[end:]
        self.cursor = self.anchor = start
        return True

    def _insert(self, value):
        self._delete_selection()
        self.text = self.text[:self.cursor] + value + self.text[self.cursor:]
        self.cursor = self.anchor = self.cursor + len(value)

    def _move(self, index, extend):
        self.cursor = max(0, min(len(self.text), index))
        if not extend:
            self.anchor = self.cursor

    def _word_bounds(self, index):
        if not self.text:
            return 0, 0
        probe = min(index, len(self.text) - 1)
        kind = _is_word_char(self.text[probe])
        start = end = probe
        while start > 0 and _is_word_char(self.text[start - 1]) == kind and self.text[start - 1] != "\n":
            start -= 1
        while end < len(self.text) and _is_word_char(self.text[end]) == kind and self.text[end] != "\n":
            end += 1
        return start, end

    def _jump_word(self, index, direction):
        i = index
        if direction < 0:
            while i > 0 and not _is_word_char(self.text[i - 1]):
                i -= 1
            while i > 0 and _is_word_char(self.text[i - 1]):
                i -= 1
        else:
            while i < len(self.text) and not _is_word_char(self.text[i]):
                i += 1
            while i < len(self.text) and _is_word_char(self.text[i]):
                i += 1
        return i

    # ------------------------------------------------------------ 事件

    def handle(self, event, layout):
        """處理鍵盤與輸入法事件;layout 是目前文字的排版(上下移動游標用)。"""
        if event.type == pygame.TEXTEDITING:
            self.composition = event.text
            self.composition_cursor = max(0, min(len(event.text), getattr(event, "start", len(event.text))))
            return
        if event.type == pygame.TEXTINPUT:
            self.composition = ""
            self._insert(event.text)
            return
        if event.type != pygame.KEYDOWN or self.composition:
            return      # 組字中的按鍵是給輸入法用的
        ctrl = event.mod & pygame.KMOD_CTRL
        shift = bool(event.mod & pygame.KMOD_SHIFT)
        key = event.key
        if key == pygame.K_BACKSPACE:
            if not self._delete_selection() and self.cursor > 0:
                start = self._jump_word(self.cursor, -1) if ctrl else self.cursor - 1
                self.text = self.text[:start] + self.text[self.cursor:]
                self.cursor = self.anchor = start
        elif key == pygame.K_DELETE:
            if not self._delete_selection() and self.cursor < len(self.text):
                end = self._jump_word(self.cursor, 1) if ctrl else self.cursor + 1
                self.text = self.text[:self.cursor] + self.text[end:]
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._insert("\n")
        elif ctrl and key == pygame.K_a:
            self.anchor, self.cursor = 0, len(self.text)
        elif ctrl and key in (pygame.K_c, pygame.K_x):
            start, end = self.selection
            if start != end:
                _set_clipboard(self.text[start:end])
                if key == pygame.K_x:
                    self._delete_selection()
        elif ctrl and key == pygame.K_v:
            pasted = _clipboard_text().replace("\r\n", "\n").replace("\r", "\n")
            if pasted:
                self._insert(pasted)
        elif key in (pygame.K_LEFT, pygame.K_RIGHT):
            direction = -1 if key == pygame.K_LEFT else 1
            start, end = self.selection
            if start != end and not shift:
                self._move(start if direction < 0 else end, False)
            elif ctrl:
                self._move(self._jump_word(self.cursor, direction), shift)
            else:
                self._move(self.cursor + direction, shift)
        elif key in (pygame.K_UP, pygame.K_DOWN) and layout is not None:
            x, row = layout.caret(self.cursor)
            target = row + (-1 if key == pygame.K_UP else 1)
            if target < 0:
                self._move(0, shift)
            elif target >= len(layout.lines):
                self._move(len(self.text), shift)
            else:
                self._move(layout.index_at(x, (target + 0.5) * layout.line_height), shift)
        elif key in (pygame.K_HOME, pygame.K_END):
            if ctrl or layout is None:
                self._move(0 if key == pygame.K_HOME else len(self.text), shift)
            else:
                line = layout.lines[layout.line_of(self.cursor)]
                self._move(line.start if key == pygame.K_HOME else line.end, shift)

    def select_all(self):
        self.anchor, self.cursor = 0, len(self.text)

    def click(self, index, shift=False):
        now = pygame.time.get_ticks()
        if now - self._last_click[0] < DOUBLE_CLICK_MS and abs(index - self._last_click[1]) <= 1:
            self._clicks += 1
        else:
            self._clicks = 1
        self._last_click = (now, index)
        if self._clicks == 2:
            self.anchor, self.cursor = self._word_bounds(index)
        elif self._clicks >= 3:
            self.anchor, self.cursor = 0, len(self.text)
        else:
            self._move(index, shift)
            self.dragging = True

    def drag_to(self, index):
        if self.dragging:
            self.cursor = max(0, min(len(self.text), index))

    def release(self):
        self.dragging = False
