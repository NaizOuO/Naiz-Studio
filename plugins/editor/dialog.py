"""PDF 編輯器的小視窗:輸入密碼、詢問要不要修復、未儲存的變更、插入空白頁的大小等。"""

import pygame

from core import theme, widgets
from core.widgets import Button, TextInput, draw_text, rounded_panel

PANEL_W = 470


class Dialog:
    def __init__(self, get_screen, accent):
        self.get_screen = get_screen
        self.accent = accent
        self.is_open = False
        self.title = ""
        self.lines = []
        self.buttons = []
        self.on_choice = None
        self.show_field = False
        self.error = ""
        self.field = TextInput(accent=accent, size=15)
        self._button_widgets = []

    def open(self, title, lines, buttons, on_choice, field=None, mask=False, error="", value=""):
        """buttons:[(代號, 文字, 是否醒目)],由左到右排;on_choice(代號, 輸入的文字)。
        field 是輸入框的提示文字,給 None 時不顯示輸入框;按 Esc 等於按「cancel」。"""
        self.title, self.lines, self.buttons, self.on_choice = title, list(lines), list(buttons), on_choice
        self.error = error
        self.show_field = field is not None
        self.field.set_text(value)
        self.field.placeholder = field or ""
        self.field.mask = mask
        self._button_widgets = [Button(label, accent=self.accent, filled=filled, size=14)
                                for _, label, filled in self.buttons]
        self.is_open = True
        if self.show_field:
            self.field.focus()
            self.field.select_all()

    def close(self):
        self.field.blur()
        self.is_open = False

    def choose(self, key):
        action, text = self.on_choice, self.field.text
        self.close()
        if action is not None:
            action(key, text)

    def _primary(self):
        return next((key for key, _, filled in self.buttons if filled), self.buttons[-1][0])

    def handle_event(self, event, pos):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if any(key == "cancel" for key, _, _ in self.buttons):
                self.choose("cancel")
            return
        if event.type == pygame.KEYDOWN and event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and \
                (self.show_field or not self.field.focused):
            self.choose(self._primary())
            return
        if self.show_field:
            self.field.handle(event, pos)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for (key, _, _), button in zip(self.buttons, self._button_widgets):
                if button.clicked(pos, True):
                    self.choose(key)
                    return

    def draw(self, mouse_pos):
        screen = self.get_screen()
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 170))
        screen.blit(veil, (0, 0))
        inner = PANEL_W - 48
        wrapped = [row for line in self.lines for row in (widgets.wrap_text(line, 13, inner) or [""])]
        panel_h = 64 + len(wrapped) * 21 + (50 if self.show_field else 0) + (22 if self.error else 0) + 70
        panel = pygame.Rect((width - PANEL_W) // 2, (height - panel_h) // 2, PANEL_W, panel_h)
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.PANEL_EDGE)
        x, y = panel.x + 24, panel.y + 20
        draw_text(screen, self.title, (x, y), 17, theme.TEXT, bold=True)
        y += 38
        for row in wrapped:
            draw_text(screen, row, (x, y), 13, theme.TEXT_DIM)
            y += 21
        if self.show_field:
            y += 6
            self.field.draw(screen, pygame.Rect(x, y, inner, 36), mouse_pos)
            y += 44
        if self.error:
            draw_text(screen, widgets.clip_text(self.error, 12, inner), (x, y), 12, theme.DANGER)
        right = panel.right - 24
        foot_y = panel.bottom - 54
        for button in reversed(self._button_widgets):
            button_w = max(80, theme.font(14, button.filled).size(button.label)[0] + 30)
            button.draw(screen, pygame.Rect(right - button_w, foot_y, button_w, 36), mouse_pos)
            right -= button_w + 10
