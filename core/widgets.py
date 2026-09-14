"""可重複使用的 pygame UI 元件。"""

import math

import pygame

from . import theme


def rounded_panel(surface, rect, color=theme.PANEL, radius=10, alpha=None, border=None):
    if alpha is None:
        pygame.draw.rect(surface, color, rect, border_radius=radius)
    else:
        layer = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(layer, (*color, alpha), layer.get_rect(), border_radius=radius)
        surface.blit(layer, rect.topleft)
    if border:
        pygame.draw.rect(surface, border, rect, 1, border_radius=radius)


def draw_text(surface, text, pos, size=16, color=theme.TEXT, bold=False, center=False, right=False):
    img = theme.font(size, bold).render(text, True, color)
    rect = img.get_rect()
    if center:
        rect.center = pos
    elif right:
        rect.midright = pos
    else:
        rect.topleft = pos
    surface.blit(img, rect)
    return rect


def clip_text(text: str, size: int, max_width: int, bold: bool = False) -> str:
    f = theme.font(size, bold)
    if f.size(text)[0] <= max_width:
        return text
    while text and f.size(text + "...")[0] > max_width:
        text = text[:-1]
    return text + "..."


def wrap_text(text: str, size: int, max_width: int, bold: bool = False, max_lines=None) -> list:
    """依寬度自動換行;英文單字盡量不從中間切開,超過 max_lines 時最後一行結尾加上「...」。"""
    f = theme.font(size, bold)
    lines, current = [], ""
    for ch in text:
        if f.size(current + ch)[0] <= max_width:
            current += ch
            continue
        cut = current.rfind(" ")
        if ch.isascii() and ch.isalnum() and current[-1:].isascii() and current[-1:].isalnum() and cut > 0:
            # 正在英文單字中間:退回上一個空白再換行
            lines.append(current[:cut])
            current = current[cut + 1:] + ch
        else:
            lines.append(current)
            current = ch.lstrip()
    if current:
        lines.append(current)
    if max_lines and len(lines) > max_lines:
        rest = " ".join(lines[max_lines - 1:])
        lines = lines[:max_lines - 1] + [clip_text(rest, size, max_width, bold)]
    return lines


class Button:
    def __init__(self, label, accent=theme.ACCENT, filled=True, size=16, icon=None):
        self.label = label
        self.accent = accent
        self.filled = filled
        self.size = size
        self.icon = icon
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.enabled = True

    def draw(self, surface, rect, mouse_pos):
        self.rect = rect
        hover = self.enabled and rect.collidepoint(mouse_pos)

        if not self.enabled:
            bg, fg, edge = theme.PANEL_LIGHT, theme.TEXT_FAINT, theme.PANEL_EDGE
        elif self.filled:
            bg = self.accent if hover else theme.ACCENT_DIM if self.accent == theme.ACCENT else self.accent
            if hover:
                bg = tuple(min(255, c + 28) for c in self.accent)
            else:
                bg = tuple(int(c * 0.82) for c in self.accent)
            fg, edge = (18, 22, 28), None
        else:
            bg = theme.PANEL_LIGHT if hover else theme.PANEL
            fg = self.accent if hover else theme.TEXT_DIM
            edge = self.accent if hover else theme.PANEL_EDGE

        rounded_panel(surface, rect, bg, radius=8, border=edge)
        label = self.label if not self.icon else f"{self.icon}  {self.label}"
        draw_text(surface, label, rect.center, self.size, fg, bold=self.filled, center=True)
        return hover

    def clicked(self, mouse_pos, click) -> bool:
        return bool(click and self.enabled and self.rect.collidepoint(mouse_pos))


class Slider:
    """可拖曳的數值滑桿。value 為 int 或 float,依 step 決定。"""

    def __init__(self, minimum, maximum, value, step=1, accent=theme.ACCENT, width_ratio=1.0,
                 ticks=False):
        self.min = minimum
        self.max = maximum
        self.value = value
        self.step = step
        self.accent = accent
        self.width_ratio = width_ratio
        self.ticks = ticks
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.dragging = False

    def _to_value(self, x):
        ratio = (x - self.rect.x) / max(1, self.rect.width)
        ratio = max(0.0, min(1.0, ratio))
        raw = self.min + ratio * (self.max - self.min)
        if self.step >= 1:
            return int(round(raw / self.step) * self.step)
        return round(raw / self.step) * self.step

    def handle(self, event, mouse_pos):
        # 左右放寬到涵蓋整顆圓鈕:數值在最大或最小時圓鈕有一半落在軌道外
        hit = self.rect.inflate(20, 16)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and hit.collidepoint(mouse_pos):
            self.dragging = True
            self.value = self._to_value(mouse_pos[0])
            return True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.dragging = False
        if event.type == pygame.MOUSEMOTION and self.dragging:
            self.value = self._to_value(mouse_pos[0])
            return True
        return False

    def draw(self, surface, rect, mouse_pos):
        self.rect = rect
        ratio = (self.value - self.min) / max(1e-9, (self.max - self.min))
        cy = rect.centery

        pygame.draw.rect(surface, theme.PANEL_LIGHT, (rect.x, cy - 3, rect.width, 6), border_radius=3)
        fill_w = int(rect.width * ratio)
        if fill_w > 0:
            pygame.draw.rect(surface, self.accent, (rect.x, cy - 3, fill_w, 6), border_radius=3)

        knob_x = rect.x + fill_w

        if self.ticks:
            steps = int(round((self.max - self.min) / self.step))
            for i in range(steps + 1):
                tx = rect.x + int(rect.width * i / max(1, steps))
                if abs(tx - knob_x) < 7:
                    continue
                passed = tx <= knob_x
                pygame.draw.circle(surface, theme.BG_DEEP, (tx, cy), 4)
                pygame.draw.circle(surface, self.accent if passed else theme.PANEL_EDGE, (tx, cy), 2)

        hover = rect.inflate(0, 16).collidepoint(mouse_pos) or self.dragging
        pygame.draw.circle(surface, theme.BG_DEEP, (knob_x, cy), 9)
        pygame.draw.circle(surface, self.accent, (knob_x, cy), 8 if hover else 6)


class Toggle:
    def __init__(self, value=True, accent=theme.ACCENT):
        self.value = value
        self.accent = accent
        self.rect = pygame.Rect(0, 0, 0, 0)

    def draw(self, surface, pos, mouse_pos):
        self.rect = pygame.Rect(pos[0], pos[1], 42, 22)
        hover = self.rect.collidepoint(mouse_pos)
        bg = self.accent if self.value else theme.PANEL_LIGHT
        if hover:
            bg = tuple(min(255, c + 20) for c in bg)
        rounded_panel(surface, self.rect, bg, radius=11,
                      border=None if self.value else theme.PANEL_EDGE)
        knob_x = self.rect.right - 11 if self.value else self.rect.x + 11
        pygame.draw.circle(surface, (245, 247, 250) if self.value else theme.TEXT_DIM,
                           (knob_x, self.rect.centery), 8)
        return hover

    def clicked(self, mouse_pos, click) -> bool:
        if click and self.rect.collidepoint(mouse_pos):
            self.value = not self.value
            return True
        return False


class SegmentedControl:
    """一列互斥選項,用來選壓縮模式之類的。"""

    def __init__(self, options, index=0, accent=theme.ACCENT):
        self.options = options
        self.index = index
        self.accent = accent
        self.rects = []

    @property
    def value(self):
        return self.options[self.index][0]

    def draw(self, surface, rect, mouse_pos):
        rounded_panel(surface, rect, theme.PANEL, radius=8, border=theme.PANEL_EDGE)
        self.rects = []
        seg_w = rect.width / len(self.options)
        for i, (_key, label) in enumerate(self.options):
            seg = pygame.Rect(int(rect.x + i * seg_w), rect.y, int(seg_w), rect.height)
            self._draw_segment(surface, seg, label, i == self.index, mouse_pos)

    def _draw_segment(self, surface, seg, label, active, mouse_pos):
        self.rects.append(seg)
        if active:
            inner = seg.inflate(-6, -6)
            rounded_panel(surface, inner, tuple(int(c * 0.30) for c in self.accent), radius=6)
            draw_text(surface, label, inner.center, 14, self.accent, bold=True, center=True)
        else:
            draw_text(surface, label, seg.center, 14,
                      theme.TEXT if seg.collidepoint(mouse_pos) else theme.TEXT_DIM, center=True)

    def clicked(self, mouse_pos, click) -> bool:
        if not click:
            return False
        for i, seg in enumerate(self.rects):
            if seg.collidepoint(mouse_pos):
                self.index = i
                return True
        return False


class ChoiceGrid(SegmentedControl):
    """選項太多、一列放不下時分成多列排列;用法和 SegmentedControl 相同,draw 只看 rect 的寬度並回傳佔用的高度。"""

    def __init__(self, options, columns, index=0, accent=theme.ACCENT, row_h=34):
        super().__init__(options, index, accent)
        self.columns = columns
        self.row_h = row_h

    def draw(self, surface, rect, mouse_pos):
        rows = math.ceil(len(self.options) / self.columns)
        box = pygame.Rect(rect.x, rect.y, rect.width, rows * self.row_h)
        rounded_panel(surface, box, theme.PANEL, radius=8, border=theme.PANEL_EDGE)
        self.rects = []
        cell_w = box.width / self.columns
        for i, (_key, label) in enumerate(self.options):
            row, col = divmod(i, self.columns)
            cell = pygame.Rect(int(box.x + col * cell_w), box.y + row * self.row_h, int(cell_w), self.row_h)
            self._draw_segment(surface, cell, label, i == self.index, mouse_pos)
        return box.height


def _clipboard_text() -> str:
    try:
        return pygame.scrap.get_text() or ""
    except Exception:
        return ""


def _set_clipboard(text):
    try:
        pygame.scrap.put_text(text)
    except Exception:
        pass


def _is_word_char(ch):
    return ch.isalnum() or ch == "_"


class TextInput:
    """單行輸入框。

    支援:打字、選取(滑鼠拖曳、Shift+方向鍵、雙擊選字、三擊或 Ctrl+A 全選)、
    Ctrl+C / X / V、Backspace / Delete(Ctrl 一次刪一個字),Enter 或 Esc 結束輸入。
    按住按鍵連續刪除需要主程式呼叫 pygame.key.set_repeat。
    """

    def __init__(self, text="", placeholder="", accent=theme.ACCENT, size=15):
        self.text = text
        self.placeholder = placeholder
        self.accent = accent
        self.size = size
        self.cursor = len(text)
        self.anchor = self.cursor
        self.focused = False
        self.error = False
        self.rect = pygame.Rect(0, 0, 0, 0)
        self._offset = 0
        self._dragging = False
        self._last_click_ms = -10000
        self._last_click_index = -1
        self._click_count = 0
        self.composition = ""      # 輸入法組字中、還沒選字的文字(例如注音)
        self.composition_cursor = 0
        self._ime_rect = None

    # ------------------------------------------------------------ 狀態

    @property
    def selection(self):
        return min(self.anchor, self.cursor), max(self.anchor, self.cursor)

    @property
    def selected_text(self):
        start, end = self.selection
        return self.text[start:end]

    def set_text(self, text):
        self.text = text
        self.cursor = self.anchor = len(text)

    def select_all(self):
        self.anchor, self.cursor = 0, len(self.text)

    def blur(self):
        self._dragging = False
        self.composition = ""
        if self.focused:
            self.focused = False
            self.anchor = self.cursor
            pygame.key.stop_text_input()

    def _focus(self):
        if not self.focused:
            self.focused = True
            pygame.key.start_text_input()
            pygame.key.set_text_input_rect(self.rect)

    def _index_at(self, x):
        font = theme.font(self.size)
        target = x - (self.rect.x + 10) + self._offset
        for i in range(1, len(self.text) + 1):
            left = font.size(self.text[:i - 1])[0]
            right = font.size(self.text[:i])[0]
            if target < (left + right) / 2:
                return i - 1
        return len(self.text)

    def _word_bounds(self, index):
        if not self.text:
            return 0, 0
        probe = min(index, len(self.text) - 1)
        kind = _is_word_char(self.text[probe])
        start = end = probe
        while start > 0 and _is_word_char(self.text[start - 1]) == kind:
            start -= 1
        while end < len(self.text) and _is_word_char(self.text[end]) == kind:
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
        self.cursor += len(value)
        self.anchor = self.cursor

    def _move(self, index, extend):
        self.cursor = max(0, min(len(self.text), index))
        if not extend:
            self.anchor = self.cursor

    # ------------------------------------------------------------ 事件

    def handle(self, event, mouse_pos) -> bool:
        """回傳 True 代表文字內容有改變。"""
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if not self.rect.collidepoint(mouse_pos):
                self.blur()
                return False
            self._focus()
            index = self._index_at(mouse_pos[0])
            now = pygame.time.get_ticks()
            if now - self._last_click_ms < 450 and abs(index - self._last_click_index) <= 1:
                self._click_count += 1
            else:
                self._click_count = 1
            self._last_click_ms, self._last_click_index = now, index

            if self._click_count == 2:
                self.anchor, self.cursor = self._word_bounds(index)
            elif self._click_count >= 3:
                self.select_all()
            else:
                shift = pygame.key.get_mods() & pygame.KMOD_SHIFT
                self._move(index, extend=bool(shift))
                self._dragging = True
            return False
        if event.type == pygame.MOUSEMOTION and self._dragging and self.focused:
            self.cursor = self._index_at(mouse_pos[0])
            return False
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._dragging = False
            return False
        if not self.focused:
            return False

        if event.type == pygame.TEXTEDITING:
            # 輸入法組字中(注音還沒選字):先記下來畫在游標位置,選好字後才會送出 TEXTINPUT
            self.composition = event.text
            self.composition_cursor = max(0, min(len(event.text), getattr(event, "start", len(event.text))))
            return False
        if event.type == pygame.TEXTINPUT:
            self.composition = ""
            self._insert(event.text)
            return True
        if event.type != pygame.KEYDOWN:
            return False
        if self.composition:
            # 組字中的 Backspace、方向鍵是給輸入法用的,不能拿來刪或移動已經打好的字
            return False

        ctrl = event.mod & pygame.KMOD_CTRL
        shift = event.mod & pygame.KMOD_SHIFT
        key = event.key
        if key == pygame.K_BACKSPACE:
            if self._delete_selection():
                return True
            if self.cursor > 0:
                start = self._jump_word(self.cursor, -1) if ctrl else self.cursor - 1
                self.text = self.text[:start] + self.text[self.cursor:]
                self.cursor = self.anchor = start
                return True
            return False
        if key == pygame.K_DELETE:
            if self._delete_selection():
                return True
            if self.cursor < len(self.text):
                end = self._jump_word(self.cursor, 1) if ctrl else self.cursor + 1
                self.text = self.text[:self.cursor] + self.text[end:]
                return True
            return False
        if ctrl and key == pygame.K_a:
            self.select_all()
        elif ctrl and key == pygame.K_c:
            if self.selected_text:
                _set_clipboard(self.selected_text)
        elif ctrl and key == pygame.K_x:
            if self.selected_text:
                _set_clipboard(self.selected_text)
                return self._delete_selection()
        elif ctrl and key == pygame.K_v:
            pasted = _clipboard_text().replace("\r", " ").replace("\n", " ")
            if pasted:
                self._insert(pasted)
                return True
        elif key in (pygame.K_LEFT, pygame.K_RIGHT):
            direction = -1 if key == pygame.K_LEFT else 1
            start, end = self.selection
            if start != end and not shift:
                self._move(start if direction < 0 else end, extend=False)
            elif ctrl:
                self._move(self._jump_word(self.cursor, direction), extend=bool(shift))
            else:
                self._move(self.cursor + direction, extend=bool(shift))
        elif key == pygame.K_HOME:
            self._move(0, extend=bool(shift))
        elif key == pygame.K_END:
            self._move(len(self.text), extend=bool(shift))
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE):
            self.blur()
        return False

    # ------------------------------------------------------------ 繪製

    def draw(self, surface, rect, mouse_pos):
        self.rect = rect
        if self.error:
            edge = theme.DANGER
        elif self.focused:
            edge = self.accent
        else:
            edge = theme.TEXT_FAINT if rect.collidepoint(mouse_pos) else theme.PANEL_EDGE
        rounded_panel(surface, rect, theme.BG_DEEP, radius=8, alpha=220, border=edge)

        font = theme.font(self.size)
        inner = rect.inflate(-20, 0)
        composing = self.focused and bool(self.composition)
        shown = self.text[:self.cursor] + self.composition + self.text[self.cursor:] if composing else self.text
        caret = self.cursor + (self.composition_cursor if composing else 0)
        cursor_x = font.size(shown[:caret])[0]
        if cursor_x - self._offset > inner.width - 2:
            self._offset = cursor_x - inner.width + 2
        elif cursor_x < self._offset:
            self._offset = cursor_x
        self._offset = max(0, min(self._offset, max(0, font.size(shown)[0] - inner.width + 2)))

        previous_clip = surface.get_clip()
        surface.set_clip(inner.clip(previous_clip))
        start, end = self.selection
        if self.focused and start != end and not composing:
            x1 = inner.x + font.size(self.text[:start])[0] - self._offset
            x2 = inner.x + font.size(self.text[:end])[0] - self._offset
            highlight = pygame.Surface((max(1, x2 - x1), rect.height - 12), pygame.SRCALPHA)
            highlight.fill((*self.accent, 90))
            surface.blit(highlight, (x1, rect.y + 6))
        if shown:
            image = font.render(shown, True, theme.TEXT)
            surface.blit(image, (inner.x - self._offset, rect.centery - image.get_height() // 2))
        elif not self.focused and self.placeholder:
            image = font.render(self.placeholder, True, theme.TEXT_FAINT)
            surface.blit(image, (inner.x, rect.centery - image.get_height() // 2))
        if composing:
            # 組字中的文字加底線,和一般輸入法的顯示方式一樣
            x1 = inner.x + font.size(self.text[:self.cursor])[0] - self._offset
            x2 = x1 + font.size(self.composition)[0]
            pygame.draw.line(surface, self.accent, (x1, rect.bottom - 8), (x2, rect.bottom - 8), 1)
        if self.focused and (pygame.time.get_ticks() // 530) % 2 == 0:
            x = inner.x + cursor_x - self._offset
            pygame.draw.line(surface, self.accent, (x, rect.centery - 9), (x, rect.centery + 9), 2)
        surface.set_clip(previous_clip)
        if self.focused:
            # 讓輸入法的選字清單出現在游標旁邊
            ime_rect = pygame.Rect(inner.x + cursor_x - self._offset, rect.y, 1, rect.height)
            if ime_rect != self._ime_rect:
                self._ime_rect = ime_rect
                pygame.key.set_text_input_rect(ime_rect)


class ProgressBar:
    def __init__(self, accent=theme.ACCENT):
        self.accent = accent

    def draw(self, surface, rect, ratio, label=""):
        rounded_panel(surface, rect, theme.PANEL_LIGHT, radius=rect.height // 2)
        width = int(rect.width * max(0.0, min(1.0, ratio)))
        if width > 3:
            pygame.draw.rect(surface, self.accent, (rect.x, rect.y, width, rect.height),
                             border_radius=rect.height // 2)
        if label:
            draw_text(surface, label, (rect.centerx, rect.centery), 13,
                      theme.TEXT, center=True, bold=True)
