"""文字框的字型選單:開源字型包(需要時才下載)、自己加入的字型、電腦上的字型。"""

from pathlib import Path

import pygame
from PIL import Image, ImageDraw

from core import theme, widgets, winfile
from core.widgets import Button, TextInput, draw_text, rounded_panel

from . import fonts

PANEL_W = 620
ROW_H = 46
HEADER_H = 34
INFO_H = 30
SAMPLE = "永字 Aa"
FONT_FILTER = [("字型檔", "*.ttf;*.otf;*.ttc")]
def _glyph(font, ch):
    image = Image.new("L", (40, 40))
    ImageDraw.Draw(image).text((4, 4), ch, font=font, fill=255)
    return image.tobytes()


EMBED_NOTES = {"preview": "只能預覽列印：存檔後別人只能檢視、列印，不能修改", "no": "字型檔不允許嵌入，沒辦法使用"}


class FontPicker:
    def __init__(self, page, accent):
        self.page = page
        self.accent = accent
        self.is_open = False
        self.current = ""
        self.on_pick = None
        self.scroll = 0
        self.message = ("", theme.TEXT_DIM)
        self.search = TextInput(placeholder="搜尋字型名稱", accent=accent, size=14)
        self.btn_add = Button("加入字型檔", filled=False, size=13)
        self.btn_cancel = Button("取消", filled=False, size=13)
        self.list_rect = pygame.Rect(0, 0, 0, 0)
        self._hits = []
        self._samples = {}

    def open(self, current, on_pick):
        self.current, self.on_pick = current or "", on_pick
        self.scroll = 0
        self.message = ("", theme.TEXT_DIM)
        self.search.set_text("")
        self.is_open = True
        fonts.CATALOG.start_scan()

    def close(self):
        self.search.blur()
        self.is_open = False
        controller = getattr(self.page, "annot", None)
        if controller is not None and controller.editing is not None:
            pygame.key.start_text_input()     # 搜尋框結束輸入時會關掉輸入法,正在頁面上打字時要再打開

    # ------------------------------------------------------------ 清單

    def rows(self):
        query = self.search.text.strip().lower()
        sections = [
            ("開源字型包", "需要時才下載，可以自由嵌入 PDF", fonts.CATALOG.packs(), "pack"),
            ("自己加入的字型", "放在 fonts\\custom\\；也可以把字型檔直接拖進這個視窗", fonts.CATALOG.custom(), "custom"),
            ("電腦上的字型", "依字型檔的授權設定判斷能不能嵌入", fonts.CATALOG.system, "system"),
        ]
        rows = []
        for title, note, faces, group in sections:
            matched = [face for face in faces if query in face.name.lower()] if query else faces
            rows.append(("header", (title, note), HEADER_H))
            if group == "system" and fonts.CATALOG.scan_state != "ready":
                rows.append(("info", "正在讀取電腦上的字型...", INFO_H))
            elif not matched:
                rows.append(("info", "沒有符合的字型" if query else "還沒有加入字型", INFO_H))
            else:
                rows += [("face", face, ROW_H) for face in matched]
        return rows

    def _sample(self, face):
        if face.id in self._samples:
            return self._samples[face.id]
        surface = None
        try:
            font = fonts.preview_font(face, 20)
            # 沒有中文的字型(例如英文字型)畫「永」會變成方框,改用英文範例;缺字時兩個字畫出來會一模一樣
            has_cjk = _glyph(font, "永") != _glyph(font, "\U0010FFFD")
            sample = SAMPLE if has_cjk else "Aa 123"
            left, top, right, bottom = font.getbbox(sample)
            image = Image.new("RGBA", (max(1, right - left + 4), max(1, bottom - top + 4)), (0, 0, 0, 0))
            ImageDraw.Draw(image).text((2 - left, 2 - top), sample, font=font, fill=theme.TEXT + (255,))
            surface = pygame.image.frombytes(image.tobytes(), image.size, "RGBA")
        except Exception:
            surface = None
        if len(self._samples) > 300:
            self._samples.clear()
        self._samples[face.id] = surface
        return surface

    # ------------------------------------------------------------ 選擇

    def choose(self, face):
        if face.installed and face.embed == "no":
            self.message = (f"「{face.name}」的字型檔不允許嵌入 PDF，請換一個字型", theme.WARN)
            return
        if not face.installed:
            if face.dependency is None:
                self.message = (f"找不到「{face.name}」的字型檔", theme.DANGER)
                return
            self.page.app.consent.open(face.name, [face.dependency], on_done=lambda: self.choose(face))
            return
        action = self.on_pick
        self.close()
        if action is not None:
            action(face.id)

    def add_files(self, files):
        added, errors = [], []
        for path in files:
            try:
                added += fonts.CATALOG.add_custom(path)
            except ValueError as exc:
                errors.append(str(exc))
            except OSError as exc:
                errors.append(f"複製「{Path(path).name}」失敗：{exc.strerror or exc}")
        if errors:
            self.message = (errors[0], theme.DANGER)
        elif added:
            usable = [face for face in added if face.usable]
            self.message = (f"已加入 {len(added)} 個字型" + ("" if usable else "，但字型檔不允許嵌入，沒辦法使用"),
                            theme.ACCENT if usable else theme.WARN)
            self.search.set_text("")
            self.scroll = 0
        return added

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, pos):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE and not self.search.composition:
            self.close()
            return
        if event.type == pygame.DROPFILE:
            self.add_files([event.file])
            return
        if event.type == pygame.MOUSEWHEEL:
            self.scroll = max(0, self.scroll - event.y * ROW_H)
            return
        if self.search.handle(event, pos):
            self.scroll = 0
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        if self.btn_cancel.clicked(pos, True):
            self.close()
        elif self.btn_add.clicked(pos, True):
            chosen = winfile.ask_open("加入字型檔", FONT_FILTER)
            if chosen is not None:
                self.add_files([chosen])
        elif self.list_rect.collidepoint(pos):
            for face, rect in self._hits:
                if rect.collidepoint(pos):
                    self.choose(face)
                    return

    # ------------------------------------------------------------ 繪製

    def draw(self, mouse_pos):
        screen = self.page.screen
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 170))
        screen.blit(veil, (0, 0))
        panel_h = min(660, height - 60)
        panel = pygame.Rect((width - PANEL_W) // 2, (height - panel_h) // 2, PANEL_W, panel_h)
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.PANEL_EDGE)
        x, inner = panel.x + 24, PANEL_W - 48
        draw_text(screen, "選擇字型", (x, panel.y + 18), 17, theme.TEXT, bold=True)
        self.search.draw(screen, pygame.Rect(x, panel.y + 54, inner, 36), mouse_pos)
        self.list_rect = pygame.Rect(x, panel.y + 102, inner, panel_h - 102 - 108)
        self._draw_list(screen, mouse_pos)

        y = self.list_rect.bottom + 10
        fallback = fonts.CATALOG.fallback()
        if fallback is not None:
            hint, color = f"選的字型裡沒有的字（例如英文字型裡的中文），會自動用「{fallback.name}」補上", theme.TEXT_FAINT
        else:
            hint, color = "電腦上找不到能補字的中文字型，建議下載「Noto Sans TC 黑體」", theme.WARN
        for row, line in enumerate(widgets.wrap_text(hint, 12, inner, max_lines=2)):
            draw_text(screen, line, (x, y + row * 18), 12, color)
        text, color = self.message
        if text:
            draw_text(screen, widgets.clip_text(text, 12, inner), (x, y + 38), 12, color)
        foot = panel.bottom - 52
        self.btn_add.enabled = winfile.available()
        self.btn_add.draw(screen, pygame.Rect(x, foot, 110, 34), mouse_pos)
        self.btn_cancel.draw(screen, pygame.Rect(panel.right - 24 - 86, foot, 86, 34), mouse_pos)

    def _draw_list(self, screen, mouse_pos):
        area = self.list_rect
        rows = self.rows()
        total = sum(h for _, _, h in rows)
        self.scroll = max(0, min(self.scroll, max(0, total - area.height)))
        rounded_panel(screen, area, theme.BG_DEEP, radius=10, alpha=200)
        previous = screen.get_clip()
        screen.set_clip(area)
        self._hits = []
        y = area.y - self.scroll
        for kind, item, h in rows:
            if y + h < area.y or y > area.bottom:
                y += h
                continue
            if kind == "header":
                title, note = item
                draw_text(screen, title, (area.x + 12, y + 10), 13, self.accent, bold=True)
                title_w = theme.font(13, True).size(title)[0]
                draw_text(screen, widgets.clip_text(note, 12, area.width - title_w - 40), (area.x + 24 + title_w, y + 12),
                          12, theme.TEXT_FAINT)
            elif kind == "info":
                draw_text(screen, item, (area.x + 24, y + 7), 12, theme.TEXT_FAINT)
            else:
                self._draw_face(screen, item, pygame.Rect(area.x + 6, y + 2, area.width - 18, h - 4), mouse_pos)
            y += h
        screen.set_clip(previous)
        if total > area.height:
            track = pygame.Rect(area.right - 6, area.y + 6, 3, area.height - 12)
            bar_h = max(24, track.height * area.height // total)
            top = track.y + (track.height - bar_h) * self.scroll // max(1, total - area.height)
            pygame.draw.rect(screen, theme.PANEL_EDGE, track, border_radius=2)
            pygame.draw.rect(screen, self.accent, (track.x, top, 3, bar_h), border_radius=2)

    def _draw_face(self, screen, face, rect, mouse_pos):
        self._hits.append((face, rect))
        disabled = face.installed and face.embed == "no"
        selected = face.id == self.current
        if selected:
            rounded_panel(screen, rect, tuple(int(c * 0.28) for c in self.accent), radius=8, border=self.accent)
        elif rect.collidepoint(mouse_pos) and self.list_rect.collidepoint(mouse_pos) and not disabled:
            rounded_panel(screen, rect, theme.PANEL_LIGHT, radius=8)
        name_color = theme.TEXT_FAINT if disabled else (self.accent if selected else theme.TEXT)
        right = rect.right - 12
        if face.group == "pack":
            label, label_color = ("已下載", theme.TEXT_FAINT) if face.installed else \
                (f"下載 {face.dependency.size_text}", self.accent)
        elif face.group == "custom":
            label, label_color = "自己加入", theme.TEXT_FAINT
        else:
            label, label_color = "", theme.TEXT_FAINT
        label_rect = draw_text(screen, label, (right, rect.centery), 12, label_color, right=True) if label else \
            pygame.Rect(right, rect.y, 0, 0)
        sample_right = label_rect.x - 14
        sample_w = 0
        if face.installed and not disabled:
            sample = self._sample(face)
            if sample is not None:
                sample_w = min(sample.get_width(), 120)
                screen.blit(sample, (sample_right - sample_w, rect.centery - sample.get_height() // 2),
                            pygame.Rect(0, 0, sample_w, sample.get_height()))
        text_w = sample_right - sample_w - 16 - (rect.x + 12)
        draw_text(screen, widgets.clip_text(face.name, 14, text_w), (rect.x + 12, rect.y + 5), 14, name_color)
        note = face.note if face.group == "pack" else EMBED_NOTES.get(face.embed, "")
        note_color = theme.WARN if face.embed == "preview" else theme.TEXT_FAINT
        if note:
            draw_text(screen, widgets.clip_text(note, 12, text_w), (rect.x + 12, rect.y + 24), 12, note_color)
