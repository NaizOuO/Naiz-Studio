"""顏色選單:參考文書軟體的配色盤,分成主題色（每個顏色由淺到深）、標準色、最近使用。

用法:open() 指定要貼在哪個按鈕下面,選好後呼叫 on_pick(顏色);顏色是 (r, g, b),選「無」時是空的 ()。
"""

import pygame

from core import theme
from core.widgets import draw_text, rounded_panel

CELL = 22
GAP = 4
COLUMNS = 10
PAD = 14
THEME_COLORS = [(255, 255, 255), (0, 0, 0), (231, 230, 230), (68, 84, 106), (68, 114, 196),
                (237, 125, 49), (165, 165, 165), (255, 192, 0), (91, 155, 213), (112, 173, 71)]
STANDARD_COLORS = [(192, 0, 0), (255, 0, 0), (255, 192, 0), (255, 255, 0), (146, 208, 80),
                   (0, 176, 80), (0, 176, 240), (0, 112, 192), (0, 32, 96), (112, 48, 160)]
SHADES = (0.8, 0.6, 0.4, -0.25, -0.5)   # 正數往白色調淡,負數往黑色調深
RECENT_MAX = COLUMNS
_recent = []


def shade(color, amount):
    if amount >= 0:
        return tuple(round(c + (255 - c) * amount) for c in color)
    return tuple(round(c * (1 + amount)) for c in color)


def remember(color):
    color = tuple(color)
    if not color:
        return
    if color in _recent:
        _recent.remove(color)
    _recent.insert(0, color)
    del _recent[RECENT_MAX:]


def recent():
    return list(_recent)


class ColorPalette:
    def __init__(self, accent):
        self.accent = accent
        self.is_open = False
        self.current = ()
        self.allow_none = False
        self.on_pick = None
        self.rect = pygame.Rect(0, 0, 0, 0)
        self._cells = []

    def open(self, anchor, current, on_pick, allow_none=False):
        self.current = tuple(current or ())
        self.allow_none = allow_none
        self.on_pick = on_pick
        self.is_open = True
        self._anchor = pygame.Rect(anchor)

    def close(self):
        self.is_open = False
        self.on_pick = None

    def _pick(self, color):
        action = self.on_pick
        remember(color)
        self.close()
        if action is not None:
            action(tuple(color))

    def handle_event(self, event, pos) -> bool:
        """回傳 True 代表事件被顏色選單用掉了(包含點在選單外把它收起來)。"""
        if not self.is_open:
            return False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.close()
            return True
        if event.type == pygame.MOUSEBUTTONDOWN:
            for color, rect in self._cells:
                if rect.collidepoint(pos):
                    self._pick(color)
                    return True
            if not self.rect.collidepoint(pos):
                self.close()
            return True
        return event.type in (pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION, pygame.MOUSEWHEEL)

    # ------------------------------------------------------------ 繪製

    def _rows(self):
        """[(標題, [顏色])];主題色每一欄是同一個顏色的不同深淺。"""
        rows = [("主題色彩", [THEME_COLORS])]
        rows[0][1].extend([[shade(color, amount) for color in THEME_COLORS] for amount in SHADES])
        result = [("主題色彩", rows[0][1]), ("標準色彩", [STANDARD_COLORS])]
        if _recent:
            result.append(("最近使用", [_recent]))
        return result

    def draw(self, screen, mouse_pos):
        if not self.is_open:
            return
        sections = self._rows()
        height = PAD
        for title, rows in sections:
            height += 20 + len(rows) * (CELL + GAP)
        height += 6 + (CELL + 14 if self.allow_none else 0) + PAD - GAP
        width = PAD * 2 + COLUMNS * CELL + (COLUMNS - 1) * GAP
        rect = pygame.Rect(self._anchor.x, self._anchor.bottom + 6, width, height)
        if rect.bottom > screen.get_height() - 8:
            rect.bottom = self._anchor.y - 6
        rect.clamp_ip(screen.get_rect().inflate(-8, -8))
        self.rect = rect
        rounded_panel(screen, rect, theme.PANEL_LIGHT, radius=10, alpha=252, border=self.accent)
        self._cells = []
        y = rect.y + PAD
        for title, rows in sections:
            draw_text(screen, title, (rect.x + PAD, y), 12, theme.TEXT_DIM)
            y += 20
            for row in rows:
                for column, color in enumerate(row):
                    cell = pygame.Rect(rect.x + PAD + column * (CELL + GAP), y, CELL, CELL)
                    pygame.draw.rect(screen, color, cell, border_radius=4)
                    if tuple(color) == self.current:
                        pygame.draw.rect(screen, self.accent, cell.inflate(6, 6), 2, border_radius=6)
                    elif cell.collidepoint(mouse_pos):
                        pygame.draw.rect(screen, theme.TEXT, cell.inflate(4, 4), 1, border_radius=5)
                    else:
                        pygame.draw.rect(screen, theme.PANEL_EDGE, cell, 1, border_radius=4)
                    self._cells.append((color, cell))
                y += CELL + GAP
        if self.allow_none:
            box = pygame.Rect(rect.x + PAD, y + 2, width - PAD * 2, CELL + 6)
            hover = box.collidepoint(mouse_pos)
            rounded_panel(screen, box, theme.BG_DEEP, radius=6, alpha=220,
                          border=self.accent if not self.current else (theme.TEXT_FAINT if hover else theme.PANEL_EDGE))
            mark = pygame.Rect(box.x + 6, box.centery - 8, 16, 16)
            pygame.draw.rect(screen, theme.PANEL, mark, border_radius=3)
            pygame.draw.line(screen, theme.DANGER, mark.bottomleft, mark.topright, 2)
            draw_text(screen, "無", (mark.right + 8, box.centery - 9), 13, theme.TEXT)
            self._cells.append(((), box))
