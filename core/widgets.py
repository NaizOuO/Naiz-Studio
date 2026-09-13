"""可重複使用的 pygame UI 元件。"""

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


def clip_text(text: str, size: int, max_width: int) -> str:
    f = theme.font(size)
    if f.size(text)[0] <= max_width:
        return text
    while text and f.size(text + "...")[0] > max_width:
        text = text[:-1]
    return text + "..."


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
            self.rects.append(seg)
            active = i == self.index
            hover = seg.collidepoint(mouse_pos)
            if active:
                inner = seg.inflate(-6, -6)
                rounded_panel(surface, inner, tuple(int(c * 0.30) for c in self.accent), radius=6)
                draw_text(surface, label, inner.center, 14, self.accent, bold=True, center=True)
            else:
                draw_text(surface, label, seg.center, 14,
                          theme.TEXT if hover else theme.TEXT_DIM, center=True)

    def clicked(self, mouse_pos, click) -> bool:
        if not click:
            return False
        for i, seg in enumerate(self.rects):
            if seg.collidepoint(mouse_pos):
                self.index = i
                return True
        return False


def _clipboard_text() -> str:
    try:
        return pygame.scrap.get_text() or ""
    except Exception:
        return ""


class TextInput:
    """單行輸入框:打字、方向鍵、Backspace / Delete、Home / End、Ctrl+V 貼上,Enter 或 Esc 結束輸入。"""

    def __init__(self, text="", placeholder="", accent=theme.ACCENT, size=15):
        self.text = text
        self.placeholder = placeholder
        self.accent = accent
        self.size = size
        self.cursor = len(text)
        self.focused = False
        self.error = False
        self.rect = pygame.Rect(0, 0, 0, 0)
        self._offset = 0

    def set_text(self, text):
        self.text = text
        self.cursor = len(text)

    def blur(self):
        if self.focused:
            self.focused = False
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

    def _insert(self, value):
        self.text = self.text[:self.cursor] + value + self.text[self.cursor:]
        self.cursor += len(value)

    def handle(self, event, mouse_pos) -> bool:
        """回傳 True 代表文字內容有改變。"""
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(mouse_pos):
                self._focus()
                self.cursor = self._index_at(mouse_pos[0])
            else:
                self.blur()
            return False
        if not self.focused:
            return False

        if event.type == pygame.TEXTINPUT:
            self._insert(event.text)
            return True
        if event.type != pygame.KEYDOWN:
            return False

        if event.key == pygame.K_BACKSPACE and self.cursor > 0:
            self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
            self.cursor -= 1
            return True
        if event.key == pygame.K_DELETE and self.cursor < len(self.text):
            self.text = self.text[:self.cursor] + self.text[self.cursor + 1:]
            return True
        if event.key == pygame.K_v and event.mod & pygame.KMOD_CTRL:
            pasted = _clipboard_text().replace("\r", " ").replace("\n", " ")
            if pasted:
                self._insert(pasted)
                return True
        elif event.key == pygame.K_LEFT:
            self.cursor = max(0, self.cursor - 1)
        elif event.key == pygame.K_RIGHT:
            self.cursor = min(len(self.text), self.cursor + 1)
        elif event.key == pygame.K_HOME:
            self.cursor = 0
        elif event.key == pygame.K_END:
            self.cursor = len(self.text)
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE):
            self.blur()
        return False

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
        cursor_x = font.size(self.text[:self.cursor])[0]
        if cursor_x - self._offset > inner.width - 2:
            self._offset = cursor_x - inner.width + 2
        elif cursor_x < self._offset:
            self._offset = cursor_x
        self._offset = max(0, min(self._offset, max(0, font.size(self.text)[0] - inner.width + 2)))

        previous_clip = surface.get_clip()
        surface.set_clip(inner.clip(previous_clip))
        if self.text:
            image = font.render(self.text, True, theme.TEXT)
            surface.blit(image, (inner.x - self._offset, rect.centery - image.get_height() // 2))
        elif not self.focused and self.placeholder:
            image = font.render(self.placeholder, True, theme.TEXT_FAINT)
            surface.blit(image, (inner.x, rect.centery - image.get_height() // 2))
        if self.focused and (pygame.time.get_ticks() // 530) % 2 == 0:
            x = inner.x + cursor_x - self._offset
            pygame.draw.line(surface, self.accent, (x, rect.centery - 9), (x, rect.centery + 9), 2)
        surface.set_clip(previous_clip)


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
