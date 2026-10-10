"""說話者改名字的小視窗:每個人一列(顏色、名字輸入框、講幾句、第一句話),儲存後重新寫出逐字稿。"""

import pygame

from core import theme, transcribe, widgets
from core.widgets import Button, TextInput, draw_text, rounded_panel

PANEL_W = 600
ROW_H = 58


class NamesDialog:
    def __init__(self, get_screen, accent):
        self.get_screen = get_screen
        self.accent = accent
        self.is_open = False
        self.title = ""
        self.rows = []              # [(說話者編號, 輸入框, 句數, 第一句)]
        self.on_save = None
        self.btn_cancel = Button("取消", accent=accent, filled=False, size=14)
        self.btn_save = Button("儲存", accent=accent, size=14)

    def open(self, title, cues, names, on_save):
        """cues:這份逐字稿的字幕條;names:{編號: 名字};on_save({編號: 名字}) 在按「儲存」時呼叫。"""
        self.title, self.on_save = title, on_save
        self.rows = []
        for number in sorted({cue["speaker"] for cue in cues if cue.get("speaker") is not None}):
            said = [cue["text"] for cue in cues if cue.get("speaker") == number]
            field = TextInput(names.get(number, ""), placeholder=transcribe.speaker_name(number), accent=self.accent,
                              size=14)
            self.rows.append((number, field, len(said), said[0] if said else ""))
        self.is_open = True
        if self.rows:
            self.rows[0][1].focus()

    def close(self):
        for _, field, _, _ in self.rows:
            field.blur()
        self.is_open = False

    def save(self):
        names = {number: field.text.strip() for number, field, _, _ in self.rows if field.text.strip()}
        action = self.on_save
        self.close()
        if action is not None:
            action(names)

    def update(self):
        pass

    def handle_event(self, event, pos):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.close()
                return
            composing = any(field.composition for _, field, _, _ in self.rows)    # 注音選字中的 Enter 不算
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and not composing:
                self.save()
                return
            if event.key == pygame.K_TAB and self.rows:
                # Tab 換到下一個人的名字
                fields = [field for _, field, _, _ in self.rows]
                current = next((i for i, field in enumerate(fields) if field.focused), -1)
                for field in fields:
                    field.blur()
                fields[(current + 1) % len(fields)].focus()
                return
        for _, field, _, _ in self.rows:
            if field.handle(event, pos):
                if event.type == pygame.MOUSEBUTTONDOWN:
                    for _, other, _, _ in self.rows:
                        if other is not field:
                            other.blur()
                return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.btn_save.clicked(pos, True):
                self.save()
            elif self.btn_cancel.clicked(pos, True):
                self.close()

    def draw(self, mouse_pos):
        screen = self.get_screen()
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 170))
        screen.blit(veil, (0, 0))
        shown = self.rows[:max(1, (height - 220) // ROW_H)]
        panel_h = 112 + len(shown) * ROW_H + 64
        panel = pygame.Rect((width - PANEL_W) // 2, (height - panel_h) // 2, PANEL_W, panel_h)
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.PANEL_EDGE)
        x, y = panel.x + 24, panel.y + 20
        inner = PANEL_W - 48
        draw_text(screen, "說話者名字", (x, y), 17, theme.TEXT, bold=True)
        draw_text(screen, widgets.clip_text(self.title, 12, inner - 110), (panel.right - 24, y + 4), 12,
                  theme.TEXT_FAINT, right=True)
        y += 32
        draw_text(screen, "改好後逐字稿會重新寫出；空白的維持「說話者 N」。按 Tab 換下一個人", (x, y), 12, theme.TEXT_DIM)
        y += 30
        for number, field, count, first in shown:
            color_name, rgb = transcribe.speaker_color(number)
            pygame.draw.circle(screen, rgb, (x + 8, y + 17), 7)
            draw_text(screen, color_name, (x + 22, y + 9), 13, rgb)
            field.draw(screen, pygame.Rect(x + 80, y, 180, 34), mouse_pos)
            note = f"{count} 句：{first}"
            draw_text(screen, widgets.clip_text(note, 12, inner - 280), (x + 276, y + 10), 12, theme.TEXT_FAINT)
            y += ROW_H
        if len(shown) < len(self.rows):
            draw_text(screen, f"視窗太小，還有 {len(self.rows) - len(shown)} 人沒顯示", (x, y - 14), 12, theme.WARN)
        right = panel.right - 24
        foot_y = panel.bottom - 54
        self.btn_save.draw(screen, pygame.Rect(right - 90, foot_y, 90, 36), mouse_pos)
        self.btn_cancel.draw(screen, pygame.Rect(right - 190, foot_y, 90, 36), mouse_pos)
