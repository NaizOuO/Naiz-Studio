"""修正錯字的編輯視窗:新增、開關、刪除規則,並可套用到已完成的檔案。"""

from pathlib import Path

import pygame

from . import corrections, theme, widgets
from .scroll import BAR_SPACE, ScrollView
from .widgets import Button, TextInput, Toggle, draw_text, rounded_panel

NOTES = [
    ("轉錄完成後，可以直接將「辨識錯的字」換成「正確的字」，不需要重新轉錄", theme.TEXT_DIM),
    ("規則會被儲存，之後每次轉錄都會自動套用；可自由決定哪些替換啟用", theme.TEXT_DIM),
    ("注意：此規則為硬替換，只要文字相同就會被換掉，即使原本被替換掉的字是正確的", theme.WARN),
]
ROW_H = 42


class CorrectionsDialog:
    def __init__(self, get_screen, accent, finished_files, convert):
        """finished_files():回傳 [(檔案路徑, 輸出文字設定)];convert(文字, 設定):轉成和逐字稿相同的繁簡。"""
        self.get_screen = get_screen
        self.accent = accent
        self.finished_files = finished_files
        self.convert = convert
        self.is_open = False
        self.data = corrections.load()
        self.wrong = TextInput(placeholder="辨識錯的字", accent=accent)
        self.right = TextInput(placeholder="正確的字", accent=accent)
        self.btn_add = Button("新增", accent=accent, size=14)
        self.btn_apply = Button("套用到已完成的檔案", filled=False, size=13)
        self.btn_close = Button("關閉", filled=False, size=14)
        self.view = ScrollView(accent=accent)
        self.list_area = pygame.Rect(0, 0, 0, 0)
        self.rows = []
        self.message = ""
        self.message_color = theme.TEXT_DIM

    # ------------------------------------------------------------ 資料

    def open(self):
        self.data = corrections.load()
        self.message = ""
        self.is_open = True

    def close(self):
        self.wrong.blur()
        self.right.blur()
        self.view.reset()
        self.is_open = False

    def _say(self, text, color=theme.TEXT_DIM):
        self.message, self.message_color = text, color

    def add(self):
        wrong, right = self.wrong.text.strip(), self.right.text.strip()
        if not wrong:
            self._say("請先填「辨識錯的字」", theme.WARN)
            return
        if wrong == right:
            self._say("兩邊的字一樣，不需要修正", theme.WARN)
            return
        rules = self.data["rules"]
        existing = next((rule for rule in rules if rule["wrong"].lower() == wrong.lower()), None)
        if existing:
            existing.update(wrong=wrong, right=right, on=True)
            self._say(f"已更新「{wrong}」的規則", self.accent)
        else:
            rules.append({"wrong": wrong, "right": right, "on": True})
            self._say(f"已新增「{wrong} → {right}」", self.accent)
        corrections.save(self.data)
        self.wrong.set_text("")
        self.right.set_text("")
        self.wrong.blur()
        self.right.blur()

    def apply_to_files(self):
        fixed_files = total = 0
        for path, script in self.finished_files():
            path = Path(path)
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            convert = (lambda value, script=script: self.convert(value, script))
            if path.suffix.lower() == ".srt":
                new, count = corrections.apply_srt(text, self.data["rules"], convert)
            else:
                new, count = corrections.apply(text, self.data["rules"], convert)
            if count:
                path.write_text(new, encoding="utf-8")
                fixed_files += 1
                total += count
        if total:
            self._say(f"已修正 {fixed_files} 個檔案，共 {total} 處", self.accent)
        else:
            self._say("已完成的檔案裡沒有需要修正的字")

    # ------------------------------------------------------------ 事件

    def update(self):
        self.view.update(pygame.mouse.get_pos())

    def handle_event(self, event, pos):
        typing = self.wrong.focused or self.right.focused
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE and not typing:
                self.close()
                return
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and typing:
                self.add()
                return
        self.wrong.handle(event, pos)
        self.right.handle(event, pos)
        if self.data["rules"] and self.view.handle_event(event, pos):
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return

        if self.list_area.collidepoint(pos):
            for index, toggle_rect, delete_rect in self.rows:
                if toggle_rect.collidepoint(pos):
                    rule = self.data["rules"][index]
                    rule["on"] = not rule["on"]
                    corrections.save(self.data)
                    return
                if delete_rect.collidepoint(pos):
                    removed = self.data["rules"].pop(index)
                    corrections.save(self.data)
                    self._say(f"已刪除「{removed['wrong']}」的規則")
                    return
        if self.btn_add.clicked(pos, True):
            self.add()
        elif self.btn_apply.clicked(pos, True):
            self.apply_to_files()
        elif self.btn_close.clicked(pos, True):
            self.close()

    # ------------------------------------------------------------ 繪製

    def draw(self, mouse_pos):
        screen = self.get_screen()
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 170))
        screen.blit(veil, (0, 0))

        panel_w, panel_h = min(680, width - 60), min(600, height - 60)
        panel = pygame.Rect((width - panel_w) // 2, (height - panel_h) // 2, panel_w, panel_h)
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.PANEL_EDGE)
        x, inner = panel.x + 24, panel_w - 48
        y = panel.y + 20
        draw_text(screen, "修正錯字", (x, y), 17, theme.TEXT, bold=True)
        y += 34
        for text, color in NOTES:
            draw_text(screen, widgets.clip_text(text, 12, inner), (x, y), 12, color)
            y += 20
        y += 10

        field_w = (inner - 40 - 80 - 12) // 2
        self.wrong.draw(screen, pygame.Rect(x, y, field_w, 34), mouse_pos)
        ax, ay = x + field_w + 20, y + 17
        pygame.draw.line(screen, theme.TEXT_DIM, (ax - 9, ay), (ax + 7, ay), 2)
        pygame.draw.polygon(screen, theme.TEXT_DIM, [(ax + 9, ay), (ax + 3, ay - 5), (ax + 3, ay + 5)])
        self.right.draw(screen, pygame.Rect(x + field_w + 40, y, field_w, 34), mouse_pos)
        self.btn_add.enabled = bool(self.wrong.text.strip())
        self.btn_add.draw(screen, pygame.Rect(x + inner - 80, y, 80, 34), mouse_pos)
        y += 42
        if self.message:
            draw_text(screen, widgets.clip_text(self.message, 12, inner), (x, y), 12, self.message_color)
        y += 22
        pygame.draw.line(screen, theme.PANEL_EDGE, (x, y), (x + inner, y))
        y += 10
        rules = self.data["rules"]
        draw_text(screen, f"規則 ({len(rules)})", (x, y), 14, theme.TEXT, bold=True)
        draw_text(screen, "啟用", (x + inner - BAR_SPACE - 72, y + 2), 12, theme.TEXT_FAINT)
        y += 26

        footer_y = panel.bottom - 54
        area = pygame.Rect(x - 6, y, inner + 12, footer_y - 10 - y)
        self.list_area = area
        self.rows = []
        self.view.layout(area, len(rules) * ROW_H + 4)
        if not rules:
            draw_text(screen, "還沒有規則，在上方輸入後按「新增」", area.center, 13, theme.TEXT_FAINT, center=True)
        screen.set_clip(area)
        for index, rule in enumerate(rules):
            ry = area.y + 2 + index * ROW_H - self.view.scroll
            if ry + ROW_H < area.y or ry > area.bottom:
                continue
            row = pygame.Rect(area.x + 6, ry, area.width - 6 - BAR_SPACE, ROW_H - 6)
            rounded_panel(screen, row, theme.BG_DEEP, radius=8, alpha=200)
            label = f"{rule['wrong']}  →  {rule['right'] or '(刪掉這段字)'}"
            draw_text(screen, widgets.clip_text(label, 14, row.width - 130), (row.x + 12, row.y + 8), 14,
                      theme.TEXT if rule["on"] else theme.TEXT_FAINT)
            toggle = Toggle(rule["on"], accent=self.accent)
            toggle.draw(screen, (row.right - 92, row.y + 7), mouse_pos)
            delete = pygame.Rect(row.right - 36, row.y + 5, 26, 26)
            hovered = delete.collidepoint(mouse_pos)
            rounded_panel(screen, delete, theme.DANGER if hovered else theme.PANEL, radius=6)
            cross = theme.BG_DEEP if hovered else theme.TEXT_DIM
            cx, cy = delete.center
            pygame.draw.line(screen, cross, (cx - 5, cy - 5), (cx + 5, cy + 5), 2)
            pygame.draw.line(screen, cross, (cx + 5, cy - 5), (cx - 5, cy + 5), 2)
            self.rows.append((index, toggle.rect, delete))
        screen.set_clip(None)
        self.view.draw(screen, mouse_pos)

        self.btn_apply.enabled = any(rule["on"] for rule in rules) and bool(self.finished_files())
        self.btn_apply.draw(screen, pygame.Rect(x, footer_y, 180, 36), mouse_pos)
        if not self.finished_files():
            draw_text(screen, "清單裡有完成的轉錄時才能套用", (x + 192, footer_y + 10), 12, theme.TEXT_FAINT)
        self.btn_close.draw(screen, pygame.Rect(panel.right - 24 - 90, footer_y, 90, 36), mouse_pos)
