"""敏感詞過濾的編輯視窗:建立、刪除設定檔,開關內建的四類(照 Twitch 的分類),自己加詞、開關、刪除;
「開啟資料夾」打開設定檔所在的資料夾,方便匯入匯出。操作和專有名詞的視窗一樣。"""

import os

import pygame

from core import theme, widgets
from core.scroll import BAR_SPACE, ScrollView
from core.widgets import Button, Dropdown, TextInput, Toggle, draw_text, rounded_panel

from . import filters

NOTES = [
    ("字幕視窗和 OBS 上出現這些詞時換成「[filter]」；字幕紀錄和存下來的檔案保留原文", theme.TEXT_DIM),
    ("內建四類照 Twitch 的分類整理（中、英、日文），也可以自己加詞；英文只比完整的單字", theme.TEXT_FAINT),
]
ROW_H = 42


class FilterDialog:
    def __init__(self, get_screen, accent, on_change):
        """on_change(data):有任何修改時呼叫(存檔、馬上套用)。"""
        self.get_screen = get_screen
        self.accent = accent
        self.on_change = on_change
        self.data = {"active": filters.NONE, "profiles": {}}
        self.editing = ""
        self.is_open = False
        self.profile = Dropdown([("", "（還沒有設定檔）")], accent=accent, size=13)
        self.new_name = TextInput(placeholder="新設定檔的名稱，例如：我的實況", accent=accent, size=14)
        self.btn_create = Button("建立", accent=accent, size=13)
        self.btn_delete_profile = Button("刪除這個設定檔", filled=False, size=12)
        self.word = TextInput(placeholder="要遮的詞，例如：某個綽號；英文結尾加 * 代表開頭一樣就算", accent=accent,
                              size=14)
        self.btn_add = Button("新增", accent=accent, size=14)
        self.btn_close = Button("關閉", filled=False, size=14)
        self.btn_folder = Button("開啟資料夾", filled=False, size=13)
        self.view = ScrollView(accent=accent)
        self.list_area = pygame.Rect(0, 0, 0, 0)
        self.rows = []
        self.category_rects = []            # [(開關的範圍, 分類)]
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
        for field in (self.new_name, self.word):
            field.blur()
        self.profile.close()
        self.view.reset()
        self.is_open = False

    def _say(self, text, color=theme.TEXT_DIM):
        self.message, self.message_color = text, color

    def _sync_profiles(self):
        names = list(self.data["profiles"])
        self.profile.set_options([(name, name, f"{len(filters.words(self.data['profiles'][name]))} 個詞")
                                  for name in names] or [("", "（還沒有設定檔）")], self.editing)
        self.profile.set_value(self.editing)

    def _items(self):
        return self.data["profiles"].get(self.editing, [])

    def _words(self):
        return [item for item in self._items() if "word" in item]

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
        self.data["profiles"][name] = filters.template()      # 從範本開始(四類全開)
        self.editing = name
        if not self.data["active"]:
            self.data["active"] = name
        self.new_name.set_text("")
        self.new_name.blur()
        self._say(f"已建立「{name}」（四類都先打開），可以在下面自己加詞", self.accent)
        self._changed()
        self.word.focus()

    def delete_profile(self):
        if not self.editing:
            return
        if not self._confirm_delete:
            self._confirm_delete = True
            self._say(f"再按一次「確定刪除」就會刪掉「{self.editing}」", theme.WARN)
            return
        removed = self.editing
        del self.data["profiles"][removed]
        self.data.setdefault("removed", []).append(removed)
        if self.data["active"] == removed:
            self.data["active"] = filters.NONE
        if removed == filters.TEMPLATE:
            self.data["profiles"] = {filters.TEMPLATE: filters.template(), **self.data["profiles"]}  # 範本刪了會恢復原樣
        self.editing = next(iter(self.data["profiles"]), "")
        self._confirm_delete = False
        self._say(f"已刪除「{removed}」" + ("（範本恢復成原本的樣子）" if removed == filters.TEMPLATE else ""))
        self._changed()

    def toggle_category(self, key):
        item = next((i for i in self._items() if i.get("category") == key), None)
        if item is not None:
            item["on"] = not item["on"]
            self._say(f"「{filters.CATEGORY_NAMES[key]}」已{'打開' if item['on'] else '關掉'}", self.accent)
            self._changed()

    def add(self):
        if not self.editing:
            self._say("請先建立一個設定檔", theme.WARN)
            return
        word = self.word.text.strip()
        if not word:
            self._say("請先輸入要遮的詞", theme.WARN)
            return
        existing = next((i for i in self._words() if i["word"] == word), None)
        if existing:
            existing["on"] = True
            self._say(f"「{word}」已經在清單裡", self.accent)
        else:
            self._items().append({"word": word, "on": True})
            self._say(f"已新增「{word}」", self.accent)
        self.word.set_text("")
        self.word.focus()
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
        typing = self.new_name.focused or self.word.focused
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE and not typing:
                self.close()
                return
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and typing:
                self.create() if self.new_name.focused else self.add()
                return
        for field in (self.new_name, self.word):
            field.handle(event, pos)
        if self._words() and self.view.handle_event(event, pos):
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        for rect, key in self.category_rects:
            if rect.collidepoint(pos):
                self.toggle_category(key)
                return
        if self.list_area.collidepoint(pos):
            words = self._words()
            for index, toggle_rect, delete_rect in self.rows:
                if toggle_rect.collidepoint(pos):
                    words[index]["on"] = not words[index]["on"]
                    self._changed()
                    return
                if delete_rect.collidepoint(pos):
                    self._items().remove(words[index])
                    self._say(f"已刪除「{words[index]['word']}」")
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
        elif self.btn_folder.clicked(pos, True):
            os.startfile(filters.STORE.ensure_folder())

    # ------------------------------------------------------------ 繪製

    def draw(self, mouse_pos):
        screen = self.get_screen()
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 170))
        screen.blit(veil, (0, 0))

        panel_w, panel_h = min(700, width - 60), min(680, height - 40)
        panel = pygame.Rect((width - panel_w) // 2, (height - panel_h) // 2, panel_w, panel_h)
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.PANEL_EDGE)
        x, inner = panel.x + 24, panel_w - 48
        y = panel.y + 20
        draw_text(screen, "敏感詞過濾", (x, y), 17, theme.TEXT, bold=True)
        y += 34
        for text, color in NOTES:
            draw_text(screen, widgets.clip_text(text, 12, inner), (x, y), 12, color)
            y += 20
        y += 8

        # 設定檔
        draw_text(screen, "設定檔", (x, y + 8), 13, theme.TEXT)
        self.btn_delete_profile.enabled = bool(self.editing)
        self.btn_delete_profile.label = "確定刪除" if self._confirm_delete else \
            ("恢復成範本" if self.editing == filters.TEMPLATE else "刪除這個設定檔")
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

        # 內建的四類:兩欄
        draw_text(screen, "內建分類", (x, y), 14, theme.TEXT, bold=True)
        y += 26
        states = {item["category"]: item["on"] for item in self._items() if "category" in item}
        self.category_rects = []
        col_w = (inner - 12) // 2
        for index, (key, name, note, terms) in enumerate(filters.CATEGORIES):
            cell = pygame.Rect(x + (index % 2) * (col_w + 12), y + (index // 2) * 58, col_w, 52)
            rounded_panel(screen, cell, theme.BG_DEEP, radius=8, alpha=200)
            on = states.get(key, False)
            draw_text(screen, name, (cell.x + 12, cell.y + 7), 14, theme.TEXT if on else theme.TEXT_FAINT, bold=True)
            draw_text(screen, widgets.clip_text(note, 11, col_w - 80), (cell.x + 12, cell.y + 30), 11, theme.TEXT_FAINT)
            toggle = Toggle(on, accent=self.accent)
            toggle.draw(screen, (cell.right - 54, cell.y + 14), mouse_pos)
            self.category_rects.append((toggle.rect, key))
        y += 2 * 58 + 6

        # 自己加的詞
        self.word.draw(screen, pygame.Rect(x, y, inner - 92, 34), mouse_pos)
        self.btn_add.enabled = bool(self.editing and self.word.text.strip())
        self.btn_add.draw(screen, pygame.Rect(x + inner - 80, y, 80, 34), mouse_pos)
        y += 42
        if self.message:
            draw_text(screen, widgets.clip_text(self.message, 12, inner), (x, y), 12, self.message_color)
        y += 22

        words = self._words()
        draw_text(screen, f"自己加的詞 ({len(words)})", (x, y), 14, theme.TEXT, bold=True)
        draw_text(screen, "啟用", (x + inner - BAR_SPACE - 72, y + 2), 12, theme.TEXT_FAINT)
        y += 26
        footer_y = panel.bottom - 54
        area = pygame.Rect(x - 6, y, inner + 12, footer_y - 10 - y)
        self.list_area = area
        self.rows = []
        self.view.layout(area, len(words) * ROW_H + 4)
        if not words:
            hint = "在上方輸入要遮的詞後按「新增」" if self.editing else "先在上方建立一個設定檔"
            draw_text(screen, hint, area.center, 13, theme.TEXT_FAINT, center=True)
        screen.set_clip(area)
        for index, item in enumerate(words):
            ry = area.y + 2 + index * ROW_H - self.view.scroll
            if ry + ROW_H < area.y or ry > area.bottom:
                continue
            row = pygame.Rect(area.x + 6, ry, area.width - 6 - BAR_SPACE, ROW_H - 6)
            rounded_panel(screen, row, theme.BG_DEEP, radius=8, alpha=200)
            draw_text(screen, widgets.clip_text(item["word"], 14, row.width - 130), (row.x + 12, row.y + 8), 14,
                      theme.TEXT if item["on"] else theme.TEXT_FAINT)
            toggle = Toggle(item["on"], accent=self.accent)
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
        self.btn_folder.draw(screen, pygame.Rect(x, footer_y, 110, 36), mouse_pos)
        draw_text(screen, widgets.clip_text("設定檔存在這裡，可以複製給別人，或放入別人給的檔案", 12, inner - 240),
                  (x + 122, footer_y + 11), 12, theme.TEXT_FAINT)
        self.profile.draw_menu(screen, mouse_pos)
