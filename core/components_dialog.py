"""「元件與空間」視窗:列出下載過的元件和大小,用不到的可以刪掉(按兩次才刪)。從設定打開。"""

import pygame

from . import components, theme, widgets
from .scroll import BAR_SPACE, ScrollView
from .widgets import Button, draw_text, rounded_panel

ROW_H = 58


class ComponentsDialog:
    def __init__(self, get_screen, accent=theme.ACCENT, on_change=None):
        """on_change():刪掉元件後呼叫(設定面板更新總大小)。"""
        self.get_screen = get_screen
        self.accent = accent
        self.on_change = on_change
        self.is_open = False
        self.scanner = None
        self.view = ScrollView(accent=accent)
        self.btn_close = Button("關閉", filled=False, size=14)
        self.list_area = pygame.Rect(0, 0, 0, 0)
        self.rows = []
        self.confirm = None             # 按過一次「刪除」的那一項(再按一次才真的刪)
        self.message, self.message_color = "", theme.TEXT_DIM

    @property
    def items(self):
        return self.scanner.items if self.scanner is not None else None

    def open(self):
        self.scanner = components.Scanner().start()
        self.confirm = None
        self.message = ""
        self.view.reset()
        self.is_open = True

    def close(self):
        self.view.reset()
        self.is_open = False

    def _delete(self, item):
        if self.confirm is not item:
            self.confirm = item
            self.message, self.message_color = f"再按一次「確定刪除」就會刪掉「{item['name']}」", theme.WARN
            return
        self.confirm = None
        if components.remove(item):
            self.scanner.items = [other for other in self.items if other is not item]
            self.message = f"已刪除「{item['name']}」，空出 {components.human(item['size'])}"
            self.message_color = self.accent
            if self.on_change:
                self.on_change()
        else:
            self.message = f"「{item['name']}」正在使用中，關掉用到它的功能後再試"
            self.message_color = theme.WARN

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, pos):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.close()
            return
        if self.items and self.view.handle_event(event, pos):
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        if self.list_area.collidepoint(pos):
            for item, button in self.rows:
                if button.collidepoint(pos):
                    self._delete(item)
                    return
        if self.btn_close.clicked(pos, True):
            self.close()
            return
        if self.confirm is not None:        # 點別的地方:取消剛才的「確定刪除」
            self.confirm = None
            self.message = ""

    # ------------------------------------------------------------ 繪製

    def draw(self, mouse_pos):
        screen = self.get_screen()
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 150))
        screen.blit(veil, (0, 0))
        panel_w, panel_h = min(600, width - 60), min(620, height - 60)
        panel = pygame.Rect((width - panel_w) // 2, (height - panel_h) // 2, panel_w, panel_h)
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.PANEL_EDGE)
        x, inner = panel.x + 24, panel_w - 48
        y = panel.y + 20
        draw_text(screen, "元件與空間", (x, y), 17, theme.TEXT, bold=True)
        items = self.items
        if items:
            total = components.human(sum(item["size"] for item in items))
            draw_text(screen, f"{len(items)} 項，共 {total}", (x + inner, y + 12), 13, theme.TEXT_DIM, right=True)
        y += 32
        for line in ("用到某些功能時，程式會先詢問再下載需要的元件，下載過的都列在這裡",
                     "用不到的可以刪掉空出空間；之後又用到時，會再詢問要不要下載"):
            draw_text(screen, widgets.clip_text(line, 12, inner), (x, y), 12, theme.TEXT_FAINT)
            y += 20
        y += 6
        draw_text(screen, widgets.clip_text(self.message, 12, inner), (x, y), 12, self.message_color)
        y += 24

        footer_y = panel.bottom - 54
        area = pygame.Rect(x - 6, y, inner + 12, footer_y - 10 - y)
        self.list_area = area
        self.rows = []
        self.view.update(mouse_pos)
        self.view.layout(area, len(items or ()) * ROW_H + 4)
        if items is None:
            draw_text(screen, "計算大小中…", area.center, 13, theme.TEXT_FAINT, center=True)
        elif not items:
            draw_text(screen, "還沒有下載任何元件", area.center, 13, theme.TEXT_FAINT, center=True)
        screen.set_clip(area)
        for index, item in enumerate(items or ()):
            ry = area.y + 2 + index * ROW_H - self.view.scroll
            if ry + ROW_H < area.y or ry > area.bottom:
                continue
            row = pygame.Rect(area.x + 6, ry, area.width - 6 - BAR_SPACE, ROW_H - 6)
            rounded_panel(screen, row, theme.BG_DEEP, radius=8, alpha=200)
            button = pygame.Rect(row.right - 96, row.centery - 14, 84, 28)
            size_x = button.x - 14
            draw_text(screen, components.human(item["size"]), (size_x, row.y + 17), 14, theme.TEXT, right=True)
            text_w = size_x - 90 - (row.x + 12)
            draw_text(screen, widgets.clip_text(item["name"], 14, text_w), (row.x + 12, row.y + 8), 14, theme.TEXT)
            draw_text(screen, widgets.clip_text(item["used_by"] or "其他檔案", 12, text_w), (row.x + 12, row.y + 30),
                      12, theme.TEXT_FAINT)
            confirming = self.confirm is item
            hovered = button.collidepoint(mouse_pos) and area.collidepoint(mouse_pos)
            fill = theme.DANGER if confirming else (theme.PANEL_LIGHT if hovered else theme.PANEL)
            rounded_panel(screen, button, fill, radius=7, border=theme.DANGER if hovered else theme.PANEL_EDGE)
            draw_text(screen, "確定刪除" if confirming else "刪除", button.center, 13,
                      theme.BG_DEEP if confirming else theme.TEXT_DIM, center=True)
            self.rows.append((item, button))
        screen.set_clip(None)
        self.view.draw(screen, mouse_pos)
        self.btn_close.draw(screen, pygame.Rect(panel.right - 24 - 90, footer_y, 90, 36), mouse_pos)
