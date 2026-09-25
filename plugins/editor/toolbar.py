"""PDF 編輯器上方的工具按鈕:平常只有圖示,滑鼠移過去文字就往右拉開、把後面的按鈕往右推;選取中的工具文字一直顯示。

拉開的程度每一幀往目標靠近(依經過的時間計算,畫面快慢不同也一樣順),滑鼠一直移動時也會跟著動。
一個按鈕收起來、下一個同時拉開,兩者速度相同,所以滑鼠底下的按鈕不會被推走。
"""

import pygame

from core import theme
from core.widgets import draw_text, rounded_panel

from . import icons

ICON_BOX = 34               # 按鈕平常只有圖示時的寬度
ICON_SIZE = 20
TOOL_H = 30
GAP = 4
GROW_SPEED = 16             # 拉開、收起的速度(越大越快)
LABEL_SIZE = 13


class ToolBar:
    def __init__(self, tools, accent):
        self.tools = list(tools)            # [(代號, 名稱)]
        self.accent = accent
        self.rects = []                     # 這一幀每個按鈕的範圍:[(代號, 範圍)]
        self.hover = None
        self._grow = {}                     # 代號 → 拉開的程度 0～1
        self._tick = None
        self._label_w = {}

    def tool_at(self, pos):
        for key, rect in self.rects:
            if rect.collidepoint(pos):
                return key
        return None

    def _extra(self, label):
        """文字拉開後多出來的寬度;量字很花時間,每個名稱只量一次。"""
        width = self._label_w.get(label)
        if width is None:
            width = self._label_w[label] = theme.font(LABEL_SIZE).size(label)[0] + 12
        return width

    def draw(self, screen, origin, current, mouse_pos, enabled=True):
        """origin 是第一個按鈕的左上角;current 是選取中的工具;enabled 為 False 時(有選單開著)不跟著滑鼠拉開。"""
        now = pygame.time.get_ticks()
        seconds = 0.0 if self._tick is None else max(0.0, (now - self._tick) / 1000)
        self._tick = now
        step = min(1.0, seconds * GROW_SPEED)
        self.hover = self.tool_at(mouse_pos) if enabled else None
        x, y = origin
        self.rects = []
        for key, label in self.tools:
            target = 1.0 if key in (current, self.hover) else 0.0
            grow = self._grow.get(key, target)
            grow += (target - grow) * step
            if abs(target - grow) < 0.01:
                grow = target
            self._grow[key] = grow
            box = pygame.Rect(x, y, ICON_BOX + round(self._extra(label) * grow), TOOL_H)
            self.rects.append((key, box))
            x = box.right + GAP
            active = key == current
            if active:
                rounded_panel(screen, box, tuple(int(c * 0.3) for c in self.accent), radius=7, border=self.accent)
            elif key == self.hover:
                rounded_panel(screen, box, theme.PANEL_LIGHT, radius=7, border=theme.TEXT_FAINT)
            else:
                rounded_panel(screen, box, theme.BG_DEEP, radius=7, alpha=160, border=theme.PANEL_EDGE)
            color = self.accent if active else theme.TEXT
            picture = icons.icon(key, ICON_SIZE, color)
            if picture is not None:
                screen.blit(picture, (box.x + (ICON_BOX - ICON_SIZE) // 2, box.centery - ICON_SIZE // 2))
            if box.width > ICON_BOX + 4:
                clip = screen.get_clip()
                screen.set_clip(box.inflate(-4, 0).clip(clip))
                height = theme.font(LABEL_SIZE).get_height()
                draw_text(screen, label, (box.x + ICON_BOX - 4, box.centery - height // 2), LABEL_SIZE, color)
                screen.set_clip(clip)
        return x
