"""右鍵選單:在滑鼠的位置跳出一排選項,點一下就執行;點選單外面或按 Esc 收起來。

用法:open(位置, [(文字, 快捷鍵說明, 可不可以按, 要做的事), ...]),每一幀呼叫 draw(),事件先交給 handle_event()。
"""

import pygame

from core import theme
from core.widgets import draw_text, rounded_panel

ITEM_H = 30
PAD = 6
WIDTH = 190


class ContextMenu:
    def __init__(self, accent=theme.ACCENT):
        self.accent = accent
        self.is_open = False
        self.items = []
        self.rect = pygame.Rect(0, 0, 0, 0)
        self._pos = (0, 0)
        self._rows = []

    def open(self, pos, items):
        self.items = list(items)
        self._pos = pos
        self.is_open = bool(self.items)

    def close(self):
        self.is_open = False
        self.items = []

    def handle_event(self, event, pos) -> bool:
        """回傳 True 代表事件被選單用掉了。"""
        if not self.is_open:
            return False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.close()
            return True
        if event.type == pygame.MOUSEBUTTONDOWN:
            if not self.rect.collidepoint(pos):
                self.close()
                return event.button != 3        # 在別處按右鍵:收起這個選單,讓那裡再跳一個新的
            if event.button == 1:
                for (label, _, enabled, action), row in zip(self.items, self._rows):
                    if row.collidepoint(pos) and enabled:
                        self.close()
                        action()
                        break
            return True
        return event.type in (pygame.MOUSEBUTTONUP, pygame.MOUSEWHEEL)

    def draw(self, screen, mouse_pos):
        if not self.is_open:
            return
        rect = pygame.Rect(self._pos[0], self._pos[1], WIDTH, PAD * 2 + ITEM_H * len(self.items))
        rect.clamp_ip(screen.get_rect().inflate(-8, -8))
        self.rect = rect
        rounded_panel(screen, rect, theme.PANEL_LIGHT, radius=8, alpha=250, border=theme.PANEL_EDGE)
        self._rows = []
        y = rect.y + PAD
        for label, shortcut, enabled, _ in self.items:
            row = pygame.Rect(rect.x + 4, y, rect.width - 8, ITEM_H)
            if enabled and row.collidepoint(mouse_pos):
                rounded_panel(screen, row, tuple(int(c * 0.3) for c in self.accent), radius=6)
            color = theme.TEXT if enabled else theme.TEXT_FAINT
            middle = row.centery - theme.font(13).get_height() // 2
            draw_text(screen, label, (row.x + 10, middle), 13, color)
            if shortcut:
                draw_text(screen, shortcut, (row.right - 10, middle), 12, theme.TEXT_FAINT, right=True)
            self._rows.append(row)
            y += ITEM_H
