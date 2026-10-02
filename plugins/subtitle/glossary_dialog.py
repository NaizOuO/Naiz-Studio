"""專有名詞的編輯視窗:建立、刪除設定檔,在設定檔裡新增、開關、刪除名詞(和逐字稿的「修正錯字」同一套操作)。"""

import pygame

from core import theme, widgets
from core.scroll import BAR_SPACE, ScrollView
from core.widgets import Button, Dropdown, TextInput, Toggle, draw_text, rounded_panel

from . import glossary

NOTES = [
    ("人名、地名、招式名等固定的翻法：翻譯會照這裡翻，辨識時也會提示這些字怎麼寫", theme.TEXT_DIM),
    ("可以建好幾個設定檔（例如一部動畫一個、一款遊戲一個），在字幕設定裡選要用哪一個", theme.TEXT_DIM),
    ("原文要和字幕上辨識出來的字一樣才會套用（例如日文的人名寫片假名）", theme.TEXT_FAINT),
]
ROW_H = 42


class GlossaryDialog:
    def __init__(self, get_screen, accent, on_change):
        """on_change(data):有任何修改時呼叫(存檔、更新進行中的字幕)。"""
        self.get_screen = get_screen
        self.accent = accent
        self.on_change = on_change
        self.data = {"active": glossary.NONE, "profiles": {}}
        self.editing = ""                   # 正在編輯的設定檔
        self.is_open = False
        self.profile = Dropdown([("", "（還沒有設定檔）")], accent=accent, size=13)
        self.new_name = TextInput(placeholder="新設定檔的名稱，例如：死亡筆記本", accent=accent, size=14)
        self.btn_create = Button("建立", accent=accent, size=13)
        self.btn_delete_profile = Button("刪除這個設定檔", filled=False, size=12)
        self.source = TextInput(placeholder="原文，例如：ニア", accent=accent, size=14)
        self.target = TextInput(placeholder="譯名，例如：尼亞", accent=accent, size=14)
        self.btn_add = Button("新增", accent=accent, size=14)
        self.btn_close = Button("關閉", filled=False, size=14)
        self.view = ScrollView(accent=accent)
        self.list_area = pygame.Rect(0, 0, 0, 0)
        self.rows = []
        self.message, self.message_color = "", theme.TEXT_DIM
        self._confirm_delete = False

    # ------------------------------------------------------------ 資料

    def open(self, data):
        self.data = data
        self.editing = data["active"] or next(iter(data["profiles"]), "")
        self._sync_profiles()
        self.message = ""
        self._confirm_delete = False
        self.is_open = True

    def close(self):
        for field in (self.new_name, self.source, self.target):
            field.blur()
        self.profile.close()
        self.view.reset()
        self.is_open = False

    def _say(self, text, color=theme.TEXT_DIM):
        self.message, self.message_color = text, color

    def _sync_profiles(self):
        names = list(self.data["profiles"])
        self.profile.set_options([(name, name, f"{len(self.data['profiles'][name])} 個") for name in names]
                                 or [("", "（還沒有設定檔）")], self.editing)
        self.profile.set_value(self.editing)

    def _terms(self):
        return self.data["profiles"].get(self.editing, [])

    def _changed(self):
        self._sync_profiles()
        self.on_change(self.data)

    def create(self):
        name = self.new_name.text.strip()
        if not name:
            self._say("請先輸入設定檔的名稱", theme.WARN)
            return
        if name in self.data["profiles"]:
            self._say(f"已經有「{name}」這個設定檔", theme.WARN)
            return
        self.data["profiles"][name] = []
        self.editing = name
        if not self.data["active"]:
            self.data["active"] = name             # 第一個設定檔建好就直接使用
        self.new_name.set_text("")
        self.new_name.blur()
        self._say(f"已建立「{name}」，在下面新增名詞", self.accent)
        self._changed()
        self.source.focus()

    def delete_profile(self):
        if not self.editing:
            return
        if not self._confirm_delete:
            self._confirm_delete = True
            self._say(f"再按一次「刪除這個設定檔」就會刪掉「{self.editing}」和裡面的名詞", theme.WARN)
            return
        removed = self.editing
        del self.data["profiles"][removed]
        if self.data["active"] == removed:
            self.data["active"] = glossary.NONE
        self.editing = next(iter(self.data["profiles"]), "")
        self._confirm_delete = False
        self._say(f"已刪除「{removed}」")
        self._changed()

    def add(self):
        if not self.editing:
            self._say("請先建立一個設定檔", theme.WARN)
            return
        source, target = self.source.text.strip(), self.target.text.strip()
        if not source:
            self._say("請先填原文", theme.WARN)
            return
        terms = self._terms()
        existing = next((t for t in terms if t["source"] == source), None)
        if existing:
            existing.update(target=target, on=True)
            self._say(f"已更新「{source}」", self.accent)
        else:
            terms.append({"source": source, "target": target, "on": True})
            self._say(f"已新增「{source} → {target or '（照原文）'}」", self.accent)
        self.source.set_text("")
        self.target.set_text("")
        self.target.blur()
        self.source.focus()
        self._changed()

    # ------------------------------------------------------------ 事件

    def update(self):
        self.view.update(pygame.mouse.get_pos())

    def handle_event(self, event, pos):
        if self.profile.is_open or (event.type == pygame.MOUSEBUTTONDOWN and self.profile.rect.collidepoint(pos)):
            before = self.profile.value
            if self.profile.handle(event, pos):
                if self.profile.value != before and self.profile.value:
                    self.editing = self.profile.value
                    self._confirm_delete = False
                    self.message = ""
                return
        typing = self.new_name.focused or self.source.focused or self.target.focused
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE and not typing:
                self.close()
                return
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and typing:
                self.create() if self.new_name.focused else self.add()
                return
            if event.key == pygame.K_TAB and (self.source.focused or self.target.focused):
                leaving, going = (self.source, self.target) if self.source.focused else (self.target, self.source)
                leaving.blur()
                going.focus()
                return
        for field in (self.new_name, self.source, self.target):
            field.handle(event, pos)
        if self._terms() and self.view.handle_event(event, pos):
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        if self.list_area.collidepoint(pos):
            for index, toggle_rect, delete_rect in self.rows:
                if toggle_rect.collidepoint(pos):
                    term = self._terms()[index]
                    term["on"] = not term["on"]
                    self._changed()
                    return
                if delete_rect.collidepoint(pos):
                    removed = self._terms().pop(index)
                    self._say(f"已刪除「{removed['source']}」")
                    self._changed()
                    return
        if self.btn_create.clicked(pos, True):
            self.create()
        elif self.btn_delete_profile.clicked(pos, True):
            self.delete_profile()
        elif self.btn_add.clicked(pos, True):
            self.add()
        elif self.btn_close.clicked(pos, True):
            self.close()

    # ------------------------------------------------------------ 繪製

    def draw(self, mouse_pos):
        screen = self.get_screen()
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 170))
        screen.blit(veil, (0, 0))

        panel_w, panel_h = min(700, width - 60), min(640, height - 40)
        panel = pygame.Rect((width - panel_w) // 2, (height - panel_h) // 2, panel_w, panel_h)
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.PANEL_EDGE)
        x, inner = panel.x + 24, panel_w - 48
        y = panel.y + 20
        draw_text(screen, "專有名詞", (x, y), 17, theme.TEXT, bold=True)
        y += 34
        for text, color in NOTES:
            draw_text(screen, widgets.clip_text(text, 12, inner), (x, y), 12, color)
            y += 20
        y += 8

        # 設定檔
        draw_text(screen, "設定檔", (x, y + 8), 13, theme.TEXT)
        self.btn_delete_profile.enabled = bool(self.editing)
        self.btn_delete_profile.label = "確定刪除" if self._confirm_delete else "刪除這個設定檔"
        self.btn_delete_profile.draw(screen, pygame.Rect(x + inner - 120, y, 120, 34), mouse_pos)
        self.profile.enabled = bool(self.data["profiles"])
        profile_rect = pygame.Rect(x + 60, y, inner - 60 - 130, 34)
        self.profile.draw(screen, profile_rect, mouse_pos)
        if self.editing and self.editing == self.data["active"]:
            draw_text(screen, "使用中", (profile_rect.right - 40, y + 10), 11, self.accent, right=True)
        y += 42
        self.new_name.draw(screen, pygame.Rect(x + 60, y, inner - 60 - 90, 34), mouse_pos)
        self.btn_create.enabled = bool(self.new_name.text.strip())
        self.btn_create.draw(screen, pygame.Rect(x + inner - 80, y, 80, 34), mouse_pos)
        y += 46
        pygame.draw.line(screen, theme.PANEL_EDGE, (x, y), (x + inner, y))
        y += 12

        # 新增名詞
        field_w = (inner - 40 - 80 - 12) // 2
        self.source.draw(screen, pygame.Rect(x, y, field_w, 34), mouse_pos)
        ax, ay = x + field_w + 20, y + 17
        pygame.draw.line(screen, theme.TEXT_DIM, (ax - 9, ay), (ax + 7, ay), 2)
        pygame.draw.polygon(screen, theme.TEXT_DIM, [(ax + 9, ay), (ax + 3, ay - 5), (ax + 3, ay + 5)])
        self.target.draw(screen, pygame.Rect(x + field_w + 40, y, field_w, 34), mouse_pos)
        self.btn_add.enabled = bool(self.editing and self.source.text.strip())
        self.btn_add.draw(screen, pygame.Rect(x + inner - 80, y, 80, 34), mouse_pos)
        y += 42
        if self.message:
            draw_text(screen, widgets.clip_text(self.message, 12, inner), (x, y), 12, self.message_color)
        y += 22

        terms = self._terms()
        title = f"「{self.editing}」的名詞 ({len(terms)})" if self.editing else "名詞"
        draw_text(screen, widgets.clip_text(title, 14, inner - 120), (x, y), 14, theme.TEXT, bold=True)
        draw_text(screen, "啟用", (x + inner - BAR_SPACE - 72, y + 2), 12, theme.TEXT_FAINT)
        y += 26
        footer_y = panel.bottom - 54
        area = pygame.Rect(x - 6, y, inner + 12, footer_y - 10 - y)
        self.list_area = area
        self.rows = []
        self.view.layout(area, len(terms) * ROW_H + 4)
        if not terms:
            hint = "在上方輸入原文和譯名後按「新增」" if self.editing else "先在上方建立一個設定檔"
            draw_text(screen, hint, area.center, 13, theme.TEXT_FAINT, center=True)
        screen.set_clip(area)
        for index, term in enumerate(terms):
            ry = area.y + 2 + index * ROW_H - self.view.scroll
            if ry + ROW_H < area.y or ry > area.bottom:
                continue
            row = pygame.Rect(area.x + 6, ry, area.width - 6 - BAR_SPACE, ROW_H - 6)
            rounded_panel(screen, row, theme.BG_DEEP, radius=8, alpha=200)
            label = f"{term['source']}  →  {term['target'] or '（照原文）'}"
            draw_text(screen, widgets.clip_text(label, 14, row.width - 130), (row.x + 12, row.y + 8), 14,
                      theme.TEXT if term["on"] else theme.TEXT_FAINT)
            toggle = Toggle(term["on"], accent=self.accent)
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
        self.btn_close.draw(screen, pygame.Rect(panel.right - 24 - 90, footer_y, 90, 36), mouse_pos)
        self.profile.draw_menu(screen, mouse_pos)
