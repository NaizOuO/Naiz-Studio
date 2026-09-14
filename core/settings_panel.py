"""齒輪打開的背景設定浮動視窗。"""

import pygame

from . import paths, theme, widgets
from .widgets import Button, SegmentedControl, Slider, draw_text, rounded_panel

MODE_NOTES = {
    "cover": "保持比例放大到填滿，裁掉超出的部分",
    "contain": "保持比例完整顯示，邊緣可能留白",
    "stretch": "拉滿整個視窗，比例會被扭曲",
    "center": "維持原始大小，可調整擺放位置",
    "tile": "以原始大小重複並排",
    "manual": "自由調整位置與縮放比例",
}


class SettingsPanel:
    def __init__(self, app):
        self.app = app
        self.is_open = False
        self.dirty = False
        self.saved_at = 0
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
        self.btn_save = Button("儲存", accent=theme.ACCENT)
        self.btn_revert = Button("還原", filled=False, size=14)
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.close_rect = pygame.Rect(0, 0, 0, 0)
        self.bg_rows = []
        self.sliders = []

    @property
    def config(self):
        return self.app.config

    @property
    def screen(self):
        return self.app.screen

    # ------------------------------------------------------------ 狀態

    def open(self):
        self.bg_files = theme.list_images(str(paths.APP_DIR))
        current = self.config.get("bg_image", "")
        self.bg_index = self.bg_files.index(current) + 1 if current in self.bg_files else 0
        self.mode.index = next(
            (i for i, (key, _) in enumerate(self.mode.options)
             if key == self.config.get("bg_mode")), 0)
        self.alpha.value = int(self.config.get("bg_alpha", 90))
        spot = self.config["bg_manual" if self.config.get("bg_mode") == "manual" else "bg_center"]
        self.pos_x.value = int(spot.get("x", 50))
        self.pos_y.value = int(spot.get("y", 50))
        self.scale.value = int(self.config["bg_manual"].get("scale", 100))
        self.bg_scroll = 0
        self.dirty = False
        self.is_open = True

    def apply(self):
        """把面板上的值寫回 config,背景會在下一幀反映。"""
        chosen = "" if self.bg_index == 0 else self.bg_files[self.bg_index - 1]
        mode = self.mode.value
        changed = (chosen != self.config.get("bg_image")
                   or mode != self.config.get("bg_mode")
                   or int(self.alpha.value) != int(self.config.get("bg_alpha", 90)))

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
        if theme.save_config(str(paths.APP_DIR), self.config):
            self.dirty = False
            self.saved_at = pygame.time.get_ticks()

    def revert(self):
        self.app.config = theme.load_config(str(paths.APP_DIR))
        self.app.rebuild_background()
        self.open()

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, mouse_pos):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.is_open = False
            return
        if event.type == pygame.MOUSEWHEEL:
            self.bg_scroll = max(0, self.bg_scroll - event.y * 30)
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
        for index, row in self.bg_rows:
            if row.collidepoint(mouse_pos):
                self.bg_index = index
                return
        if self.mode.clicked(mouse_pos, True):
            return
        if self.btn_save.clicked(mouse_pos, True):
            self.save()
            return
        if self.btn_revert.clicked(mouse_pos, True):
            self.revert()
            return
        if not self.rect.collidepoint(mouse_pos):
            self.is_open = False

    # ------------------------------------------------------------ 繪製

    def draw(self, mouse_pos):
        screen = self.screen
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 165))
        screen.blit(veil, (0, 0))

        panel_w, panel_h = 520, min(660, height - 60)
        panel = pygame.Rect((width - panel_w) // 2, (height - panel_h) // 2, panel_w, panel_h)
        self.rect = panel
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.PANEL_EDGE)

        draw_text(screen, "背景設定", (panel.x + 22, panel.y + 18), 17, theme.TEXT, bold=True)
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

        inner = panel_w - 44
        x = panel.x + 22
        y = panel.y + 66

        draw_text(screen, "背景圖片", (x, y), 14, theme.TEXT)
        draw_text(screen, f"images 資料夾 · {len(self.bg_files)} 張",
                  (panel.right - 22, y + 7), 12, theme.TEXT_FAINT, right=True)
        y += 24
        y = self._draw_image_list(pygame.Rect(x, y, inner, 116), mouse_pos) + 18

        draw_text(screen, "填充方式", (x, y), 14, theme.TEXT)
        y += 22
        self.mode.draw(screen, pygame.Rect(x, y, inner, 32), mouse_pos)
        y += 40
        mode = self.mode.value
        draw_text(screen, MODE_NOTES[mode], (x, y), 12,
                  theme.WARN if mode == "stretch" else theme.TEXT_FAINT)
        y += 26

        rows = [(self.alpha, "透明度", lambda v: f"{int(v)}")]
        if mode in ("center", "manual"):
            rows.append((self.pos_x, "水平位置", lambda v: f"{int(v)}"))
            rows.append((self.pos_y, "垂直位置", lambda v: f"{int(v)}"))
        if mode == "manual":
            rows.append((self.scale, "縮放", lambda v: f"{int(v)}%"))
        for slider, label, fmt in rows:
            draw_text(screen, label, (x, y), 13, theme.TEXT)
            draw_text(screen, fmt(slider.value), (panel.right - 22, y + 6), 13, theme.ACCENT, right=True)
            y += 20
            slider.draw(screen, pygame.Rect(x, y + 4, inner, 14), mouse_pos)
            y += 28
        self.sliders = [row[0] for row in rows]

        keys_y = panel.bottom - 108
        if y < keys_y - 8:      # 視窗太矮、滑桿快頂到時就不擠這塊
            pygame.draw.line(screen, theme.PANEL_EDGE, (x, keys_y), (panel.right - 22, keys_y))
            draw_text(screen, "快捷鍵", (x, keys_y + 12), 12, theme.TEXT_DIM)
            for offset, (key, desc) in enumerate((("F11", "切換全螢幕"), ("Esc", "關閉這個視窗"))):
                kx = x + 60 + offset * 180
                chip = pygame.Rect(kx, keys_y + 8, 40, 20)
                rounded_panel(screen, chip, theme.PANEL_LIGHT, radius=5, border=theme.PANEL_EDGE)
                draw_text(screen, key, chip.center, 11, theme.TEXT_DIM, center=True)
                draw_text(screen, desc, (kx + 48, keys_y + 11), 12, theme.TEXT_FAINT)

        foot_y = panel.bottom - 56
        if self.dirty:
            draw_text(screen, "有尚未儲存的變更", (x, foot_y + 12), 12, theme.WARN)
        elif pygame.time.get_ticks() - self.saved_at < 2500:
            draw_text(screen, "已儲存到 config.json", (x, foot_y + 12), 12, theme.ACCENT)
        else:
            draw_text(screen, "調整後即時預覽，關閉不會自動儲存", (x, foot_y + 12), 12, theme.TEXT_FAINT)

        self.btn_revert.draw(screen, pygame.Rect(panel.right - 190, foot_y, 78, 34), mouse_pos)
        self.btn_save.draw(screen, pygame.Rect(panel.right - 104, foot_y, 82, 34), mouse_pos)

    def _draw_image_list(self, list_rect, mouse_pos):
        screen = self.screen
        rounded_panel(screen, list_rect, theme.BG_DEEP, radius=8, alpha=210, border=theme.PANEL_EDGE)
        options = ["不使用背景"] + self.bg_files
        row_h = 30
        self.bg_rows = []
        screen.set_clip(list_rect.inflate(-4, -6))
        for i, name in enumerate(options):
            ry = list_rect.y + 5 + i * row_h - self.bg_scroll
            if ry + row_h < list_rect.y or ry > list_rect.bottom:
                continue
            row = pygame.Rect(list_rect.x + 5, ry, list_rect.width - 10, row_h - 4)
            active = i == self.bg_index
            hover = row.collidepoint(mouse_pos) and list_rect.collidepoint(mouse_pos)
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
        screen.set_clip(None)

        content_h = len(options) * row_h + 10
        if content_h > list_rect.height:
            track = pygame.Rect(list_rect.right - 7, list_rect.y + 5, 3, list_rect.height - 10)
            pygame.draw.rect(screen, theme.PANEL_LIGHT, track, border_radius=2)
            bar = max(24, int(track.height * list_rect.height / content_h))
            span = max(1, content_h - list_rect.height)
            pygame.draw.rect(screen, theme.ACCENT,
                             (track.x, track.y + (self.bg_scroll / span) * (track.height - bar), 3, bar),
                             border_radius=2)
        return list_rect.bottom
