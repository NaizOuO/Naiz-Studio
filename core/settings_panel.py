"""齒輪打開的設定浮動視窗:外觀(背景)、檔案與空間、更新與關於、快捷鍵。內容放不下時中間可以捲動。"""

import os
import webbrowser

import pygame

from . import components, large_files, paths, shortcut, theme, updater, version, widgets
from .components_dialog import ComponentsDialog
from .scroll import ScrollView
from .widgets import Button, SegmentedControl, Slider, Toggle, draw_text, rounded_panel

MODE_NOTES = {
    "cover": "保持比例放大到填滿，裁掉超出的部分",
    "contain": "保持比例完整顯示，邊緣可能留白",
    "stretch": "拉滿整個視窗，比例會被扭曲",
    "center": "維持原始大小，可調整擺放位置",
    "tile": "以原始大小重複並排",
    "manual": "自由調整位置與縮放比例",
}
REPO_PAGE = f"https://github.com/{updater.REPO}"
LIST_ROW_H = 30


class SettingsPanel:
    def __init__(self, app):
        self.app = app
        self.is_open = False
        self.dirty = False
        self.saved_at = None    # 還沒儲存過;不能用 0,剛開程式時 get_ticks 也小於提示顯示的時間
        self.bg_files = []
        self.bg_index = 0
        self.bg_scroll = 0
        self.mode = SegmentedControl(
            [("cover", "覆蓋"), ("contain", "完整"), ("stretch", "延展"),
             ("center", "置中"), ("tile", "並排"), ("manual", "自由")],
            index=0, accent=theme.ACCENT)
        self.alpha = Slider(0, 255, 90, step=5, accent=theme.ACCENT)
        self.pos_x = Slider(0, 100, 50, accent=theme.ACCENT)
        self.pos_y = Slider(0, 100, 50, accent=theme.ACCENT)
        self.scale = Slider(10, 300, 100, step=5, accent=theme.ACCENT)
        self.large_warning = Toggle(True, accent=theme.ACCENT)
        self.output_documents = Toggle(False, accent=theme.ACCENT)
        self.update_auto = Toggle(True, accent=theme.ACCENT)
        self.btn_save = Button("儲存", accent=theme.ACCENT)
        self.btn_revert = Button("還原", filled=False, size=14)
        self.btn_images = Button("開啟圖片資料夾", filled=False, size=12)     # 背景圖片放這裡
        self.btn_components = Button("管理", filled=False, size=12)
        self.btn_check = Button("立即檢查", filled=False, size=12)
        self.btn_show_update = Button("查看新版本", accent=theme.ACCENT, size=12)
        self.btn_shortcut = Button("建立桌面捷徑", filled=False, size=12)
        self.btn_readme = Button("說明文件", filled=False, size=12)
        self.btn_github = Button("GitHub", filled=False, size=12)
        self.btn_setting_folder = Button("設定資料夾", filled=False, size=12)
        self.view = ScrollView(accent=theme.ACCENT, indicator=True)
        self.components = ComponentsDialog(lambda: self.app.screen, theme.ACCENT, on_change=self._scan_components)
        self.scanner = None             # 背景計算已下載元件的總大小
        self.checker = None             # 「立即檢查」的結果
        self.check_text = ("", theme.TEXT_FAINT)
        self.shortcut_job = None
        self.shortcut_text = ("", theme.TEXT_FAINT)
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.body = pygame.Rect(0, 0, 0, 0)
        self.list_rect = pygame.Rect(0, 0, 0, 0)
        self.close_rect = pygame.Rect(0, 0, 0, 0)
        self.bg_rows = []
        self.sliders = []
        self.body_controls = []

    @property
    def config(self):
        return self.app.config

    @property
    def screen(self):
        return self.app.screen

    # ------------------------------------------------------------ 狀態

    def open(self):
        self._list_images(self.config.get("bg_image", ""))
        self.mode.index = next(
            (i for i, (key, _) in enumerate(self.mode.options)
             if key == self.config.get("bg_mode")), 0)
        self.alpha.value = int(self.config.get("bg_alpha", 90))
        spot = self.config["bg_manual" if self.config.get("bg_mode") == "manual" else "bg_center"]
        self.pos_x.value = int(spot.get("x", 50))
        self.pos_y.value = int(spot.get("y", 50))
        self.scale.value = int(self.config["bg_manual"].get("scale", 100))
        self.large_warning.value = bool(self.config.get(large_files.CONFIG_KEY, True))
        self.output_documents.value = bool(self.config.get("output_documents", False))
        self.update_auto.value = bool(self.config.get("update_check", True))
        self.bg_scroll = 0
        self.view.reset()
        self.dirty = False
        self._scan_components()
        self.is_open = True

    def _scan_components(self):
        self.scanner = components.Scanner().start()

    def _list_images(self, current):
        """重新讀圖片資料夾;current 還在的話維持選著它。"""
        self.bg_files = theme.list_images(paths.IMAGES_DIR)
        self.bg_index = self.bg_files.index(current) + 1 if current in self.bg_files else 0

    def open_images_folder(self):
        self._open(paths.IMAGES_DIR)

    @staticmethod
    def _open(folder):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)
        except OSError:
            pass

    def apply(self):
        """把面板上的值寫回 config,背景與輸出位置會馬上反映。"""
        chosen = "" if self.bg_index == 0 else self.bg_files[self.bg_index - 1]
        mode = self.mode.value
        toggles = ((large_files.CONFIG_KEY, self.large_warning, True), ("output_documents", self.output_documents, False),
                   ("update_check", self.update_auto, True))
        changed = (chosen != self.config.get("bg_image")
                   or mode != self.config.get("bg_mode")
                   or int(self.alpha.value) != int(self.config.get("bg_alpha", 90))
                   or any(toggle.value != bool(self.config.get(key, default)) for key, toggle, default in toggles))
        for key, toggle, _ in toggles:
            self.config[key] = toggle.value
        self.app.apply_output_location()

        if chosen != self.config.get("bg_image"):
            self.config["bg_image"] = chosen
            self.app.rebuild_background()

        self.config["bg_mode"] = mode
        self.config["bg_alpha"] = int(self.alpha.value)
        target = "bg_manual" if mode == "manual" else "bg_center"
        before = dict(self.config[target])
        self.config[target]["x"] = int(self.pos_x.value)
        self.config[target]["y"] = int(self.pos_y.value)
        if mode == "manual":
            self.config["bg_manual"]["scale"] = int(self.scale.value)
        if before != self.config[target] or changed:
            self.dirty = True

    def save(self):
        if theme.save_config(str(paths.SETTING_DIR), self.config):
            self.dirty = False
            self.saved_at = pygame.time.get_ticks()

    def revert(self):
        self.app.config = theme.load_config(str(paths.SETTING_DIR))
        self.app.apply_output_location()
        self.app.rebuild_background()
        self.open()

    # ------------------------------------------------------------ 更新、捷徑、關於

    def check_now(self):
        self.checker = updater.Checker().start()
        self.check_text = ("檢查中…", theme.TEXT_FAINT)

    def _poll(self):
        checker = self.checker
        if checker is not None and checker.done:
            self.checker = None
            if checker.result:
                self.app.update_info = checker.result          # 標題旁也會出現「有新版本」
                self.check_text = (f"有新版本 {checker.result['tag']}", theme.ACCENT)
            elif checker.error:
                self.check_text = ("連不上 GitHub，請確認網路後再試", theme.WARN)
            else:
                self.check_text = (f"目前已經是最新版（v{version.VERSION}）", theme.TEXT_DIM)
        job = self.shortcut_job
        if job is not None and job.done:
            self.shortcut_job = None
            self.shortcut_text = (("已在桌面建立捷徑", theme.ACCENT) if job.ok else
                                  ("建立失敗，可以在 exe 上按右鍵 →「傳送到」→「桌面」", theme.WARN))

    def _shortcut_state(self):
        """(按鈕能不能按, 說明)。"""
        if not updater.can_self_update():
            return False, "從原始碼執行時不需要捷徑"
        if shortcut.in_temp():
            return False, "請先把壓縮檔解壓縮，再從解壓縮後的資料夾開啟"
        if self.shortcut_job is not None:
            return False, "建立中…"
        if self.shortcut_text[0]:
            return True, None
        return True, "桌面已經有捷徑；需要的話可以重新建立" if shortcut.exists() else "在桌面放一個 Naiz Studio 的捷徑"

    def open_readme(self):
        readme = paths.APP_DIR / "README.md"
        try:
            os.startfile(readme)            # 有可以開 .md 的程式就用它開本地這份
        except OSError:
            webbrowser.open(f"{REPO_PAGE}#readme")

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, mouse_pos):
        if self.components.is_open:
            self.components.handle_event(event, mouse_pos)
            if not self.components.is_open:
                self._scan_components()
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.is_open = False
            return
        if event.type == pygame.WINDOWFOCUSGAINED:
            # 在檔案總管放了新圖片再切回來:清單馬上更新,選好的那張不變
            self._list_images("" if self.bg_index == 0 else self.bg_files[self.bg_index - 1])
            return
        if event.type == pygame.MOUSEWHEEL and self.list_rect.collidepoint(mouse_pos) \
                and self.body.collidepoint(mouse_pos):
            span = max(0, (len(self.bg_files) + 1) * LIST_ROW_H + 10 - self.list_rect.height)
            if span:
                self.bg_scroll = max(0, min(self.bg_scroll - event.y * LIST_ROW_H, span))
                return
        if (self.body.collidepoint(mouse_pos) or self.view.grabbing(mouse_pos)) \
                and self.view.handle_event(event, mouse_pos):
            return
        for slider in self.sliders:
            slider.handle(event, mouse_pos)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._click(mouse_pos)
        if self.is_open:
            self.apply()

    def _click(self, mouse_pos):
        if self.close_rect.collidepoint(mouse_pos):
            self.is_open = False
            return
        if self.btn_save.clicked(mouse_pos, True):
            self.save()
            return
        if self.btn_revert.clicked(mouse_pos, True):
            self.revert()
            return
        if not self.rect.collidepoint(mouse_pos):
            self.is_open = False
            return
        if not self.body.collidepoint(mouse_pos):
            return                      # 捲到外面、被上下蓋住的設定不能被點到
        for index, row in self.bg_rows:
            if row.collidepoint(mouse_pos) and self.list_rect.collidepoint(mouse_pos):
                self.bg_index = index
                return
        for toggle in (self.large_warning, self.output_documents, self.update_auto):
            if toggle.clicked(mouse_pos, True):
                return
        if self.mode.clicked(mouse_pos, True):
            return
        actions = ((self.btn_images, self.open_images_folder),
                   (self.btn_components, self.components.open),
                   (self.btn_check, self.check_now),
                   (self.btn_show_update, self.app.ask_update),
                   (self.btn_shortcut, self._create_shortcut),
                   (self.btn_readme, self.open_readme),
                   (self.btn_github, lambda: webbrowser.open(REPO_PAGE)),
                   (self.btn_setting_folder, lambda: self._open(paths.SETTING_DIR)))
        for button, action in actions:
            if button in self.body_controls and button.clicked(mouse_pos, True):
                action()
                return

    def _create_shortcut(self):
        self.shortcut_text = ("", theme.TEXT_FAINT)
        self.shortcut_job = shortcut.Creator().start()

    # ------------------------------------------------------------ 繪製

    def _section(self, title, x, y, right):
        draw_text(self.screen, title, (x, y), 13, theme.ACCENT, bold=True)
        pygame.draw.line(self.screen, theme.PANEL_EDGE, (x + theme.font(13).size(title)[0] + 12, y + 10),
                         (right, y + 10))
        return y + 30

    def _button(self, button, rect, mouse_pos):
        button.draw(self.screen, rect, mouse_pos)
        self.body_controls.append(button)

    def draw(self, mouse_pos):
        self._poll()
        screen = self.screen
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 165))
        screen.blit(veil, (0, 0))

        panel_w, panel_h = 540, min(700, height - 60)
        panel = pygame.Rect((width - panel_w) // 2, (height - panel_h) // 2, panel_w, panel_h)
        self.rect = panel
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.PANEL_EDGE)

        draw_text(screen, "設定", (panel.x + 22, panel.y + 18), 17, theme.TEXT, bold=True)
        close = pygame.Rect(panel.right - 44, panel.y + 14, 28, 28)
        self.close_rect = close
        if self.app.dev_mode:
            draw_text(screen, "於開發者模式", (close.x - 14, panel.y + 28), 12, theme.WARN, right=True)
        hovered = close.collidepoint(mouse_pos)
        rounded_panel(screen, close, theme.DANGER if hovered else theme.PANEL_LIGHT, radius=7)
        cross = theme.BG_DEEP if hovered else theme.TEXT_DIM
        pygame.draw.line(screen, cross, (close.centerx - 5, close.centery - 5),
                         (close.centerx + 5, close.centery + 5), 2)
        pygame.draw.line(screen, cross, (close.centerx + 5, close.centery - 5),
                         (close.centerx - 5, close.centery + 5), 2)
        pygame.draw.line(screen, theme.PANEL_EDGE, (panel.x + 16, panel.y + 52),
                         (panel.right - 16, panel.y + 52))

        foot_y = panel.bottom - 56
        pygame.draw.line(screen, theme.PANEL_EDGE, (panel.x + 16, foot_y - 12), (panel.right - 16, foot_y - 12))
        # 中間的設定放不下時捲動;上面的標題、下面的儲存固定不動
        body = pygame.Rect(panel.x + 4, panel.y + 53, panel_w - 8, foot_y - 13 - (panel.y + 53))
        self.body = body
        self.view.update(mouse_pos)
        self.body_controls = []
        previous = screen.get_clip()
        screen.set_clip(body)
        top = body.y + 14 - self.view.scroll
        bottom = self._draw_body(panel.x + 22, top, panel_w - 44, mouse_pos)
        screen.set_clip(previous)
        self.view.layout(body, bottom - top + 28)
        self.view.draw(screen, mouse_pos)

        if self.dirty:
            draw_text(screen, "有尚未儲存的變更", (panel.x + 22, foot_y + 12), 12, theme.WARN)
        elif self.saved_at is not None and pygame.time.get_ticks() - self.saved_at < 2500:
            draw_text(screen, "已儲存到 config.json", (panel.x + 22, foot_y + 12), 12, theme.ACCENT)
        else:
            draw_text(screen, "調整後即時預覽，關閉不會自動儲存", (panel.x + 22, foot_y + 12), 12, theme.TEXT_FAINT)
        self.btn_revert.draw(screen, pygame.Rect(panel.right - 190, foot_y, 78, 34), mouse_pos)
        self.btn_save.draw(screen, pygame.Rect(panel.right - 104, foot_y, 82, 34), mouse_pos)

        if self.components.is_open:
            widgets.mark_text_layer()
            self.components.draw(mouse_pos)

    def _draw_body(self, x, y, inner, mouse_pos):
        screen = self.screen
        right = x + inner

        # ---------------- 外觀
        y = self._section("外觀", x, y, right)
        draw_text(screen, "背景圖片", (x, y), 14, theme.TEXT)
        draw_text(screen, f"{len(self.bg_files)} 張", (x + 70, y + 3), 12, theme.TEXT_FAINT)
        folder = pygame.Rect(right - 120, y - 5, 120, 26)
        self._button(self.btn_images, folder, mouse_pos)
        y += 26
        draw_text(screen, widgets.clip_text("把想要的圖片放進圖片資料夾，就能在下面選擇使用", 12, inner), (x, y), 12,
                  theme.TEXT_FAINT)
        y += 24
        y = self._draw_image_list(pygame.Rect(x, y, inner, 116), mouse_pos) + 18

        draw_text(screen, "填充方式", (x, y), 14, theme.TEXT)
        y += 22
        self.mode.draw(screen, pygame.Rect(x, y, inner, 32), mouse_pos)
        y += 40
        mode = self.mode.value
        draw_text(screen, MODE_NOTES[mode], (x, y), 12, theme.WARN if mode == "stretch" else theme.TEXT_FAINT)
        y += 26

        rows = [(self.alpha, "透明度", lambda v: f"{int(v)}")]
        if mode in ("center", "manual"):
            rows.append((self.pos_x, "水平位置", lambda v: f"{int(v)}"))
            rows.append((self.pos_y, "垂直位置", lambda v: f"{int(v)}"))
        if mode == "manual":
            rows.append((self.scale, "縮放", lambda v: f"{int(v)}%"))
        for slider, label, fmt in rows:
            draw_text(screen, label, (x, y), 13, theme.TEXT)
            draw_text(screen, fmt(slider.value), (right, y + 6), 13, theme.ACCENT, right=True)
            y += 20
            slider.draw(screen, pygame.Rect(x, y + 4, inner, 14), mouse_pos)
            y += 28
        self.sliders = [row[0] for row in rows]
        y += 10

        # ---------------- 檔案與空間
        y = self._section("檔案與空間", x, y, right)
        documents = paths.documents_output()
        draw_text(screen, "輸出到「文件\\Naiz Studio」", (x, y + 2), 14, theme.TEXT)
        self.output_documents.draw(screen, (right - 42, y + 4), mouse_pos)
        where = paths.OUTPUT_DIR if documents else None
        note = (f"目前存在：{where}" if where else "找不到「文件」資料夾，會存在程式旁邊的 output") \
            if self.output_documents.value else "關閉時存在程式旁邊的 output 資料夾；首頁的「輸出資料夾」會打開目前的位置"
        draw_text(screen, widgets.clip_text(note, 12, inner - 56), (x, y + 24), 12, theme.TEXT_FAINT)
        y += 54

        draw_text(screen, "元件與空間", (x, y + 2), 14, theme.TEXT)
        self._button(self.btn_components, pygame.Rect(right - 72, y - 2, 72, 28), mouse_pos)
        items = self.scanner.items if self.scanner is not None else None
        summary = ("計算中…" if items is None else "還沒有下載任何元件" if not items else
                   f"已下載 {len(items)} 項，共 {components.human(sum(item['size'] for item in items))}；用不到的可以刪掉")
        draw_text(screen, widgets.clip_text(summary, 12, inner - 86), (x, y + 24), 12, theme.TEXT_FAINT)
        y += 54

        draw_text(screen, "大檔警告", (x, y + 2), 14, theme.TEXT)
        # 說明文字固定不變;關閉警告時改成橘色,提醒目前處理大檔前不會先確認
        draw_text(screen, "處理很大的檔案前，先提醒可能佔用大量記憶體", (x, y + 24), 12,
                  theme.TEXT_FAINT if self.large_warning.value else theme.WARN)
        self.large_warning.draw(screen, (right - 42, y + 4), mouse_pos)
        y += 64

        # ---------------- 更新與關於
        y = self._section("更新與關於", x, y, right)
        draw_text(screen, "開啟時檢查新版本", (x, y + 2), 14, theme.TEXT)
        self.update_auto.draw(screen, (right - 42, y + 4), mouse_pos)
        auto_note = "有新版本時在標題旁提醒，可以直接下載更新" if updater.can_self_update() else \
            "從原始碼執行時不會自動檢查，可以用下面的「立即檢查」"
        draw_text(screen, auto_note, (x, y + 24), 12, theme.TEXT_FAINT)
        y += 52
        self.btn_check.enabled = self.checker is None
        self._button(self.btn_check, pygame.Rect(x, y, 96, 28), mouse_pos)
        text, color = self.check_text
        text_x = x + 108
        if self.app.update_info and text.startswith("有新版本"):
            self._button(self.btn_show_update, pygame.Rect(text_x, y, 100, 28), mouse_pos)
            text_x += 112
        draw_text(screen, widgets.clip_text(text, 12, right - text_x), (text_x, y + 7), 12, color)
        y += 42

        enabled, note = self._shortcut_state()
        self.btn_shortcut.enabled = enabled
        self._button(self.btn_shortcut, pygame.Rect(x, y, 116, 28), mouse_pos)
        text, color = (note, theme.TEXT_FAINT) if note else self.shortcut_text
        draw_text(screen, widgets.clip_text(text, 12, inner - 128), (x + 128, y + 7), 12, color)
        y += 46

        draw_text(screen, f"Naiz Studio v{version.VERSION}", (x, y + 4), 14, theme.TEXT)
        bx = right
        for button, w in ((self.btn_setting_folder, 92), (self.btn_github, 72), (self.btn_readme, 84)):
            bx -= w
            self._button(button, pygame.Rect(bx, y, w, 28), mouse_pos)
            bx -= 8
        y += 46

        # ---------------- 快捷鍵
        y = self._section("快捷鍵", x, y, right)
        rows = [("", theme.TEXT_DIM, (("F11", "切換全螢幕"), ("Esc", "關閉這個視窗")))]
        if self.app.dev_mode:
            # 只有開發者模式看得到的操作說明
            rows += [("開發者", theme.WARN, (("Ctrl + Alt + 左鍵", "複製滑鼠指到的文字"),)),
                     ("", theme.WARN, (("Ctrl + Alt + Shift + 左鍵", "複製整個畫面的文字"),))]
        for label, color, keys in rows:
            draw_text(screen, label, (x, y + 4), 12, color)
            kx = x + (60 if self.app.dev_mode else 0)
            for key, desc in keys:
                chip = pygame.Rect(kx, y, theme.font(11).size(key)[0] + 18, 20)
                rounded_panel(screen, chip, theme.PANEL_LIGHT, radius=5, border=theme.PANEL_EDGE)
                draw_text(screen, key, chip.center, 11, theme.TEXT_DIM, center=True)
                desc_rect = draw_text(screen, desc, (chip.right + 8, y + 3), 12, theme.TEXT_FAINT)
                kx = max(kx + 200, desc_rect.right + 24)
            y += 26
        return y

    def _draw_image_list(self, list_rect, mouse_pos):
        screen = self.screen
        self.list_rect = list_rect.clip(self.body)
        rounded_panel(screen, list_rect, theme.BG_DEEP, radius=8, alpha=210, border=theme.PANEL_EDGE)
        options = ["不使用背景"] + self.bg_files
        self.bg_rows = []
        previous = screen.get_clip()
        screen.set_clip(list_rect.inflate(-4, -6).clip(previous))
        for i, name in enumerate(options):
            ry = list_rect.y + 5 + i * LIST_ROW_H - self.bg_scroll
            if ry + LIST_ROW_H < list_rect.y or ry > list_rect.bottom:
                continue
            row = pygame.Rect(list_rect.x + 5, ry, list_rect.width - 10, LIST_ROW_H - 4)
            active = i == self.bg_index
            hover = row.collidepoint(mouse_pos) and self.list_rect.collidepoint(mouse_pos)
            if active or hover:
                rounded_panel(screen, row, tuple(int(c * 0.3) for c in theme.ACCENT) if active
                              else theme.PANEL_LIGHT, radius=6)
            dot = (row.x + 14, row.centery)
            pygame.draw.circle(screen, theme.ACCENT if active else theme.PANEL_EDGE, dot, 6)
            if active:
                pygame.draw.circle(screen, theme.BG_DEEP, dot, 2)
            draw_text(screen, widgets.clip_text(name, 13, row.width - 46), (row.x + 30, row.y + 5), 13,
                      theme.ACCENT if active else theme.TEXT_DIM)
            self.bg_rows.append((i, row))
        screen.set_clip(previous)

        content_h = len(options) * LIST_ROW_H + 10
        if content_h > list_rect.height:
            track = pygame.Rect(list_rect.right - 7, list_rect.y + 5, 3, list_rect.height - 10)
            pygame.draw.rect(screen, theme.PANEL_LIGHT, track, border_radius=2)
            bar = max(24, int(track.height * list_rect.height / content_h))
            span = max(1, content_h - list_rect.height)
            pygame.draw.rect(screen, theme.ACCENT,
                             (track.x, track.y + (self.bg_scroll / span) * (track.height - bar), 3, bar),
                             border_radius=2)
        return list_rect.bottom
