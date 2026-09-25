"""可重複使用的 pygame UI 元件。"""

import math

import pygame

from . import theme


# 每一幀都會重畫整個畫面,同樣的文字、半透明底板不必每次重新產生;超過上限時清空重來
_CACHE_LIMIT = 3000
_panels = {}
_texts = {}
_layouts = {}


def _remember(cache, key, value):
    if len(cache) >= _CACHE_LIMIT:
        cache.clear()
    cache[key] = value
    return value


def rounded_panel(surface, rect, color=theme.PANEL, radius=10, alpha=None, border=None):
    if alpha is None:
        pygame.draw.rect(surface, color, rect, border_radius=radius)
    else:
        key = (rect.width, rect.height, tuple(color), alpha, radius)
        layer = _panels.get(key)
        if layer is None:
            layer = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(layer, (*color, alpha), layer.get_rect(), border_radius=radius)
            _remember(_panels, key, layer)
        surface.blit(layer, rect.topleft)
    if border:
        pygame.draw.rect(surface, border, rect, 1, border_radius=radius)


# ------------------------------------------------------------ 開發者模式:複製畫面上的文字
# 開啟時記錄這一幀畫出來的每段文字和位置,讓 Ctrl+左鍵 可以複製;關閉時完全不記錄

_text_log = None     # [(畫面上看得到的範圍, 原文)];None 表示不記錄
_layer_start = 0     # 最上層視窗的文字從第幾筆開始
_full_text = {}      # 被截斷成「...」的文字 → 完整原文


def begin_text_log():
    """每一幀開始畫之前呼叫;上一幀的紀錄會被清掉,不會越存越多。"""
    global _text_log, _layer_start
    _text_log, _layer_start = [], 0
    _full_text.clear()


def stop_text_log():
    global _text_log
    _text_log = None
    _full_text.clear()


def mark_text_layer():
    """之後畫的是浮在上層的視窗;複製整個畫面時只取最上層的文字。"""
    global _layer_start
    if _text_log is not None:
        _layer_start = len(_text_log)


def pause_text_log():
    global _text_log
    paused, _text_log = _text_log, None
    return paused


def resume_text_log(paused):
    global _text_log
    _text_log = paused


def text_at(pos):
    """滑鼠位置最上面的那段文字;沒有則回傳 None。"""
    for rect, text in reversed(_text_log or []):
        if rect.collidepoint(pos):
            return text
    return None


def screen_text():
    """最上層畫面的所有文字,由上到下、同一列由左到右,用換行串起來。"""
    rows = []
    for rect, text in sorted((_text_log or [])[_layer_start:], key=lambda entry: (entry[0].centery, entry[0].x)):
        if rows and abs(rows[-1][0] - rect.centery) <= 8:
            rows[-1][1].append((rect.x, text))
        else:
            rows.append([rect.centery, [(rect.x, text)]])
    return "\n".join("  ".join(text for _, text in sorted(items)) for _, items in rows)


def draw_text(surface, text, pos, size=16, color=theme.TEXT, bold=False, center=False, right=False):
    key = (text, size, bold, tuple(color))
    img = _texts.get(key)
    if img is None:
        img = _remember(_texts, key, theme.font(size, bold).render(text, True, color))
    rect = img.get_rect()
    if center:
        rect.center = pos
    elif right:
        rect.midright = pos
    else:
        rect.topleft = pos
    surface.blit(img, rect)
    if _text_log is not None and text and surface is pygame.display.get_surface():
        visible = rect.clip(surface.get_clip())   # 被捲到看不見的文字不算
        if visible.width and visible.height:
            _text_log.append((visible, _full_text.get(text, text)))
    return rect


def clip_text(text: str, size: int, max_width: int, bold: bool = False) -> str:
    key = ("clip", text, size, max_width, bold)
    clipped = _layouts.get(key)
    if clipped is None:
        clipped = _remember(_layouts, key, _clip_text(text, size, max_width, bold))
    if clipped != text and _text_log is not None:
        _full_text[clipped] = text
    return clipped


def _clip_text(text, size, max_width, bold):
    f = theme.font(size, bold)
    if f.size(text)[0] <= max_width:
        return text
    while text and f.size(text + "...")[0] > max_width:
        text = text[:-1]
    return text + "..."


def wrap_text(text: str, size: int, max_width: int, bold: bool = False, max_lines=None) -> list:
    """依寬度自動換行;英文單字盡量不從中間切開,超過 max_lines 時最後一行結尾加上「...」。"""
    key = ("wrap", text, size, max_width, bold, max_lines)
    cached = _layouts.get(key)
    if cached is None:
        cached = _remember(_layouts, key, _wrap_text(text, size, max_width, bold, max_lines))
    lines, rest = cached
    if rest is not None and _text_log is not None:
        _full_text[lines[-1]] = rest        # 開發者模式複製文字時要能找回被截掉的原文
    return list(lines)


def _wrap_text(text, size, max_width, bold, max_lines):
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
    rest = None
    if max_lines and len(lines) > max_lines:
        rest = " ".join(lines[max_lines - 1:])
        lines = lines[:max_lines - 1] + [_clip_text(rest, size, max_width, bold)]
    return lines, rest


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


class Dropdown:
    """下拉選單:選項太多、排成按鈕放不下時使用。

    options 是 [(值, 顯示文字)] 或 [(值, 顯示文字, 右側小字)]。
    用法:draw() 畫出收合的框;整個畫面(或視窗)都畫完後再呼叫 draw_menu(),展開的清單才會蓋在最上面。
    事件先交給 handle(),回傳 True 代表這個事件被選單用掉了(包括點在清單外把它收起來)。
    """

    ROW_H = 30
    MAX_ROWS = 8

    def __init__(self, options, index=0, accent=theme.ACCENT, size=14):
        self.options = list(options)
        self.index = index
        self.accent = accent
        self.size = size
        self.enabled = True
        self.is_open = False
        self.scroll = 0
        self.rect = pygame.Rect(0, 0, 0, 0)
        self._menu = pygame.Rect(0, 0, 0, 0)

    @property
    def value(self):
        return self.options[self.index][0]

    @property
    def label(self):
        return self.options[self.index][1]

    def set_options(self, options, value=None):
        """換一組選項;原本的值還在新選項裡就保留,否則選 value 或第一個。"""
        keep = self.value if self.options else None
        self.options = list(options)
        keys = [option[0] for option in self.options]
        wanted = keep if keep in keys else value
        self.index = keys.index(wanted) if wanted in keys else 0
        self.is_open = False

    def set_value(self, value):
        keys = [option[0] for option in self.options]
        if value in keys:
            self.index = keys.index(value)

    def close(self):
        self.is_open = False

    def _visible_rows(self):
        return min(len(self.options), self.MAX_ROWS)

    def _max_scroll(self):
        return max(0, len(self.options) - self.MAX_ROWS)

    # ------------------------------------------------------------ 事件

    def handle(self, event, pos) -> bool:
        if not self.is_open:
            if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.enabled
                    and self.rect.collidepoint(pos)):
                self.is_open = True
                # 打開時讓目前選的項目出現在清單裡
                self.scroll = max(0, min(self.index - self.MAX_ROWS // 2, self._max_scroll()))
                return True
            return False
        if event.type == pygame.MOUSEWHEEL:
            self.scroll = max(0, min(self.scroll - event.y, self._max_scroll()))
            return True
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.is_open = False
            return True
        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 1 and self._menu.collidepoint(pos):
                row = (pos[1] - self._menu.y - 4) // self.ROW_H
                if 0 <= row < self._visible_rows():
                    self.index = row + self.scroll
            self.is_open = False    # 點在清單外面只是收起來,不會點到底下的東西
            return True
        return event.type in (pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION)

    # ------------------------------------------------------------ 繪製

    def draw(self, surface, rect, mouse_pos):
        self.rect = rect
        hover = self.enabled and rect.collidepoint(mouse_pos)
        edge = self.accent if self.is_open else (theme.TEXT_FAINT if hover else theme.PANEL_EDGE)
        rounded_panel(surface, rect, theme.BG_DEEP, radius=8, alpha=220, border=edge)
        color = theme.TEXT if self.enabled else theme.TEXT_FAINT
        text = clip_text(self.label, self.size, rect.width - 44)
        image_h = theme.font(self.size).get_height()
        draw_text(surface, text, (rect.x + 12, rect.centery - image_h // 2), self.size, color)
        cx, cy = rect.right - 18, rect.centery
        points = [(cx - 5, cy - 2), (cx, cy + 3), (cx + 5, cy - 2)] if not self.is_open else \
            [(cx - 5, cy + 2), (cx, cy - 3), (cx + 5, cy + 2)]
        pygame.draw.lines(surface, theme.TEXT_DIM if self.enabled else theme.TEXT_FAINT, False, points, 2)

    def draw_menu(self, surface, mouse_pos):
        if not self.is_open:
            return
        rows = self._visible_rows()
        height = rows * self.ROW_H + 8
        menu = pygame.Rect(self.rect.x, self.rect.bottom + 4, self.rect.width, height)
        if menu.bottom > surface.get_height() - 8:
            menu.bottom = self.rect.y - 4     # 下面放不下時往上展開
        self._menu = menu
        rounded_panel(surface, menu, theme.PANEL_LIGHT, radius=8, border=self.accent)
        for row in range(rows):
            index = row + self.scroll
            option = self.options[index]
            item = pygame.Rect(menu.x + 4, menu.y + 4 + row * self.ROW_H, menu.width - 8, self.ROW_H)
            active = index == self.index
            if active:
                rounded_panel(surface, item, tuple(int(c * 0.30) for c in self.accent), radius=6)
            elif item.collidepoint(mouse_pos):
                rounded_panel(surface, item, theme.PANEL, radius=6)
            note_w = 0
            if len(option) > 2 and option[2]:
                note_rect = draw_text(surface, option[2], (item.right - 10, item.centery), 12, theme.TEXT_FAINT,
                                      right=True)
                note_w = note_rect.width + 12
            text = clip_text(option[1], self.size, item.width - 20 - note_w)
            draw_text(surface, text, (item.x + 10, item.centery - theme.font(self.size).get_height() // 2),
                      self.size, self.accent if active else theme.TEXT)
        if self._max_scroll():
            track = pygame.Rect(menu.right - 5, menu.y + 6, 3, menu.height - 12)
            bar_h = max(20, track.height * rows // len(self.options))
            top = track.y + (track.height - bar_h) * self.scroll // self._max_scroll()
            pygame.draw.rect(surface, theme.PANEL_EDGE, track, border_radius=2)
            pygame.draw.rect(surface, self.accent, (track.x, top, 3, bar_h), border_radius=2)


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


def copy_to_clipboard(text):
    _set_clipboard(text)


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
        self.mask = False          # 密碼欄:畫面上用圓點顯示,也不能複製

    def _shown(self, text):
        return "•" * len(text) if self.mask else text

    def focus(self):
        self._focus()

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
            left = font.size(self._shown(self.text[:i - 1]))[0]
            right = font.size(self._shown(self.text[:i]))[0]
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
            if self.selected_text and not self.mask:
                _set_clipboard(self.selected_text)
        elif ctrl and key == pygame.K_x:
            if self.selected_text and not self.mask:
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
        shown = self._shown(shown)
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
            x1 = inner.x + font.size(self._shown(self.text[:start]))[0] - self._offset
            x2 = inner.x + font.size(self._shown(self.text[:end]))[0] - self._offset
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
            x1 = inner.x + font.size(self._shown(self.text[:self.cursor]))[0] - self._offset
            x2 = x1 + font.size(self._shown(self.composition))[0]
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
