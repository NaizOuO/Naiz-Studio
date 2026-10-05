"""判斷誰說話「區分方式」的編輯視窗:建立、刪除設定檔,調整比法和門檻(和專有名詞同一套操作);
「開啟資料夾」打開設定檔所在的資料夾,方便匯入匯出。"""

import os

import pygame

from core import theme, widgets
from core.widgets import Button, Dropdown, SegmentedControl, Slider, TextInput, draw_text, rounded_panel

from . import speaker_profiles as profiles, speakers

NOTES = [
    ("預設的「自動分群」不用設門檻，大多數情況選它就好；分得不好時（例如背景有遊戲聲）可以建一個門檻比對的設定檔來調", theme.TEXT_DIM),
    ("門檻比對：不同人被當成同一個顏色就調高「同一人門檻」；同一個人變成好幾個顏色就調低", theme.TEXT_FAINT),
]


class SpeakerDialog:
    def __init__(self, get_screen, accent, on_change):
        """on_change(data):有任何修改時呼叫(存檔、更新進行中的字幕)。"""
        self.get_screen = get_screen
        self.accent = accent
        self.on_change = on_change
        self.data = {"active": profiles.GENERAL, "profiles": {}}
        self.editing = ""
        self.is_open = False
        self.profile = Dropdown([("", "（還沒有設定檔）")], accent=accent, size=13)
        self.new_name = TextInput(placeholder="新設定檔的名稱，例如：遊戲直播", accent=accent, size=14)
        self.btn_create = Button("建立", accent=accent, size=13)
        self.btn_delete_profile = Button("刪除這個設定檔", filled=False, size=12)
        self.method = SegmentedControl([(key, name) for key, name, _ in profiles.METHODS], accent=accent)
        self.same = Slider(0.0, 0.9, 0.46, step=0.01, accent=accent)
        self.split = Slider(0.3, 0.8, 0.46, step=0.01, accent=accent)
        self.btn_reset = Button("恢復預設值", filled=False, size=12)
        self.btn_close = Button("關閉", filled=False, size=14)
        self.btn_folder = Button("開啟資料夾", filled=False, size=13)
        self.message, self.message_color = "", theme.TEXT_DIM
        self._confirm_delete = False
        self._dragging = False

    # ------------------------------------------------------------ 資料

    def open(self, data):
        self.data = data
        active = data["active"]
        self.editing = active if active in data["profiles"] else next(iter(data["profiles"]), "")
        self._load_fields()
        self.message = ""
        self._confirm_delete = False
        self.is_open = True

    def close(self):
        self.new_name.blur()
        self.profile.close()
        self.is_open = False

    def _say(self, text, color=theme.TEXT_DIM):
        self.message, self.message_color = text, color

    def _value(self):
        return self.data["profiles"].get(self.editing)

    def _load_fields(self):
        self.profile.set_options([(name, name, profiles.METHOD_NAMES[value["method"]])
                                  for name, value in self.data["profiles"].items()]
                                 or [("", "（還沒有設定檔）")], self.editing)
        self.profile.set_value(self.editing)
        value = self._value()
        if value is None:
            return
        self.method.index = next(i for i, (key, _) in enumerate(self.method.options) if key == value["method"])
        self.same.value, self.split.value = value["same"], value["split"]

    def _changed(self):
        self._load_fields()
        self.on_change(self.data)

    def create(self):
        name = self.new_name.text.strip()
        if not name:
            self._say("請先輸入設定檔的名稱", theme.WARN)
            return
        if name in self.data["profiles"]:
            self._say(f"已經有「{name}」這個設定檔", theme.WARN)
            return
        base = self._value() or profiles.DEFAULTS[profiles.GENERAL]
        self.data["profiles"][name] = dict(base)        # 從目前看的設定檔複製一份開始調
        self.editing = name
        self.new_name.set_text("")
        self.new_name.blur()
        self._say(f"已建立「{name}」（數值從原本的設定檔複製），在下面調整", self.accent)
        self._changed()

    def delete_profile(self):
        if not self.editing:
            return
        if self.editing == profiles.GENERAL:
            self._say("「自動分群」是預設的區分方式，不能刪除", theme.WARN)
            return
        if not self._confirm_delete:
            self._confirm_delete = True
            self._say(f"再按一次「刪除這個設定檔」就會刪掉「{self.editing}」", theme.WARN)
            return
        removed = self.editing
        del self.data["profiles"][removed]
        self.data.setdefault("removed", []).append(removed)     # 存檔時把它的檔案刪掉
        if self.data["active"] == removed:
            self.data["active"] = profiles.GENERAL
        self.editing = next(iter(self.data["profiles"]), "")
        self._confirm_delete = False
        self._say(f"已刪除「{removed}」")
        self._changed()

    def reset(self):
        default = profiles.DEFAULTS.get(self.editing)
        if default is None:
            return
        self.data["profiles"][self.editing] = dict(default)
        self._say(f"「{self.editing}」已恢復預設值", self.accent)
        self._changed()

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, pos):
        if self.profile.is_open or (event.type == pygame.MOUSEBUTTONDOWN and self.profile.rect.collidepoint(pos)):
            before = self.profile.value
            if self.profile.handle(event, pos):
                if self.profile.value != before and self.profile.value:
                    self.editing = self.profile.value
                    self._confirm_delete = False
                    self.message = ""
                    self._load_fields()
                return
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE and not self.new_name.focused:
                self.close()
                return
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and self.new_name.focused:
                self.create()
                return
        self.new_name.handle(event, pos)
        value = self._value()
        if value is not None:
            sliders = [(self.split, "split")] + ([] if value["method"] == "cluster" else [(self.same, "same")])
            for slider, key in sliders:
                if slider.handle(event, pos):
                    value[key] = round(slider.value, 2)
                    self._dragging = True
                    return
            if event.type == pygame.MOUSEBUTTONUP and self._dragging:
                self._dragging = False
                self._changed()                     # 放開才存檔(拖曳中不一直寫檔)
                return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        if value is not None and self.method.clicked(pos, True):
            before, value["method"] = value["method"], self.method.value
            if value["method"] == "centered" and before != "centered":
                value["same"] = speakers.CENTERED_SAME  # 扣掉共同音色後分數小很多,門檻跟著換到建議值
            elif value["method"] == "plain" and before != "plain":
                value["same"] = speakers.SAME
            self._changed()
        elif self.btn_create.clicked(pos, True):
            self.create()
        elif self.btn_delete_profile.clicked(pos, True):
            self.delete_profile()
        elif self.btn_reset.clicked(pos, True):
            self.reset()
        elif self.btn_close.clicked(pos, True):
            self.close()
        elif self.btn_folder.clicked(pos, True):
            os.startfile(profiles.STORE.ensure_folder())

    # ------------------------------------------------------------ 繪製

    def draw(self, mouse_pos):
        screen = self.get_screen()
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 170))
        screen.blit(veil, (0, 0))

        panel_w, panel_h = min(700, width - 60), min(600, height - 40)
        panel = pygame.Rect((width - panel_w) // 2, (height - panel_h) // 2, panel_w, panel_h)
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.PANEL_EDGE)
        x, inner = panel.x + 24, panel_w - 48
        y = panel.y + 20
        draw_text(screen, "判斷誰說話：區分方式", (x, y), 17, theme.TEXT, bold=True)
        y += 34
        for text, color in NOTES:
            draw_text(screen, widgets.clip_text(text, 12, inner), (x, y), 12, color)
            y += 20
        y += 8

        # 設定檔
        draw_text(screen, "設定檔", (x, y + 8), 13, theme.TEXT)
        self.btn_delete_profile.enabled = bool(self.editing) and self.editing != profiles.GENERAL
        self.btn_delete_profile.label = "確定刪除" if self._confirm_delete else "刪除這個設定檔"
        self.btn_delete_profile.draw(screen, pygame.Rect(x + inner - 120, y, 120, 34), mouse_pos)
        self.profile.enabled = bool(self.data["profiles"])
        profile_rect = pygame.Rect(x + 60, y, inner - 60 - 130, 34)
        self.profile.draw(screen, profile_rect, mouse_pos)
        if self.editing and self.editing == self.data["active"]:
            draw_text(screen, "使用中", (profile_rect.right - 40, y + 17), 11, self.accent, right=True)
        y += 42
        self.new_name.draw(screen, pygame.Rect(x + 60, y, inner - 60 - 90, 34), mouse_pos)
        self.btn_create.enabled = bool(self.new_name.text.strip())
        self.btn_create.draw(screen, pygame.Rect(x + inner - 80, y, 80, 34), mouse_pos)
        y += 46
        pygame.draw.line(screen, theme.PANEL_EDGE, (x, y), (x + inner, y))
        y += 14

        value = self._value()
        self.same.rect = pygame.Rect(-10000, -10000, 0, 0)
        if value is None:
            draw_text(screen, "先在上方建立一個設定檔", (panel.centerx, y + 80), 13, theme.TEXT_FAINT, center=True)
        else:
            label_w = 110
            draw_text(screen, "比法", (x, y + 8), 14, theme.TEXT, bold=True)
            self.method.draw(screen, pygame.Rect(x + label_w, y, inner - label_w, 34), mouse_pos)
            y += 40
            note = next(note for key, _, note in profiles.METHODS if key == value["method"])
            draw_text(screen, widgets.clip_text(note, 12, inner - label_w), (x + label_w, y), 12, theme.TEXT_FAINT)
            y += 30
            rows = [(self.split, "換人門檻", "split", "一句裡有好幾個人時切開：調高切得比較多（可能切錯）；調低比較少切")]
            if value["method"] != "cluster":
                rows.insert(0, (self.same, "同一人門檻", "same",
                                "調高：分得比較細（同一個人可能變好幾個顏色）；調低：不同人比較容易被當成同一人"))
            for slider, title, key, note in rows:
                draw_text(screen, title, (x, y + 4), 14, theme.TEXT, bold=True)
                slider.value = value[key]
                slider.draw(screen, pygame.Rect(x + label_w, y + 4, inner - label_w - 56, 20), mouse_pos)
                draw_text(screen, f"{value[key]:.2f}", (x + inner, y + 14), 14, self.accent, right=True)
                y += 30
                draw_text(screen, widgets.clip_text(note, 12, inner - label_w), (x + label_w, y), 12, theme.TEXT_FAINT)
                y += 30
            if self.editing in profiles.DEFAULTS:
                self.btn_reset.draw(screen, pygame.Rect(x, y, 110, 30), mouse_pos)
            else:
                self.btn_reset.rect = pygame.Rect(-10000, -10000, 0, 0)
        if self.message:
            draw_text(screen, widgets.clip_text(self.message, 12, inner - 130), (x + 124, y + 8), 12, self.message_color)

        footer_y = panel.bottom - 54
        self.btn_close.draw(screen, pygame.Rect(panel.right - 24 - 90, footer_y, 90, 36), mouse_pos)
        self.btn_folder.draw(screen, pygame.Rect(x, footer_y, 110, 36), mouse_pos)
        draw_text(screen, widgets.clip_text("設定檔存在這裡，可以複製給別人，或放入別人給的檔案", 12, inner - 240),
                  (x + 122, footer_y + 11), 12, theme.TEXT_FAINT)
        self.profile.draw_menu(screen, mouse_pos)
