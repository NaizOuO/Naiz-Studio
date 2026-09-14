#!/usr/bin/env python
"""Naiz Studio - 本地工具包:首頁、插件載入與共用介面。"""

import math
import os
import platform
import sys
import time
from pathlib import Path

# SDL 預設會丟掉「讓視窗變成作用中」的那一下點擊。從檔案總管拖檔進來後作用中的是檔案總管,
# 不加這行的話,拖完檔第一次點選檔案、輸入框或頁面都會沒反應
os.environ.setdefault("SDL_MOUSE_FOCUS_CLICKTHROUGH", "1")
# 讓 Windows 顯示輸入法的選字清單;打注音時組字中的文字由輸入框自己畫出來
os.environ.setdefault("SDL_IME_SHOW_UI", "1")

if platform.system() == "Windows":
    import ctypes

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
    try:
        # 沒設定的話,用 pythonw 啟動時工作列會顯示 Python 的圖示,而不是視窗自己的圖示
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NaizOuO.NaizStudio")
    except Exception:
        pass

import pygame

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import paths, plugins, theme, widgets
from core.consent import ConsentDialog
from core.settings_panel import SettingsPanel
from core.widgets import Button, draw_text, rounded_panel

HEADER_H = 66
CATEGORY_ORDER = ["文件", "影像", "影音"]
DEV_CLICKS = 7
DEV_WINDOW_SECONDS = 3.0


class App:
    def __init__(self):
        pygame.init()
        icon_path = paths.IMAGES_DIR / "app_icon.png"
        if icon_path.exists():
            try:
                pygame.display.set_icon(pygame.image.load(str(icon_path)))
            except Exception:
                pass
        info = pygame.display.Info()
        width = min(1240, int(info.current_w * 0.82))
        height = min(780, int(info.current_h * 0.84))
        self.screen = pygame.display.set_mode((width, height), pygame.RESIZABLE)
        pygame.display.set_caption("Naiz Studio")
        self.clock = pygame.time.Clock()

        self.config = theme.load_config(str(paths.APP_DIR))
        self.background = None
        self.rebuild_background()
        self.dev_mode = bool(self.config.get("dev_mode", False))

        self.windowed_size = self.screen.get_size()
        self.fullscreen = False
        self.f11_held = False
        # 按住 Backspace / Delete / 方向鍵時連續觸發
        pygame.key.set_repeat(400, 35)

        self.gear_img = None
        gear_path = paths.IMAGES_DIR / "ui_gear.png"
        if gear_path.exists():
            try:
                self.gear_img = pygame.transform.smoothscale(
                    pygame.image.load(str(gear_path)).convert_alpha(), (21, 21))
            except Exception:
                pass

        self.settings = SettingsPanel(self)
        self.consent = ConsentDialog(self)
        self.title_rect = pygame.Rect(0, 0, 0, 0)
        self.gear_rect = pygame.Rect(0, 0, 0, 0)
        self.title_clicks = []
        self.btn_home = Button("首頁", filled=False, size=14)
        self.copy_toast = None
        self._swallow_click = False

        self.tools = []
        self.load_errors = []
        self.pages = {}
        self.current = None
        self.card_rects = []
        self.reload_tools()

    # ------------------------------------------------------------ 狀態

    def rebuild_background(self):
        image = theme.load_background(str(paths.APP_DIR), self.config)
        self.background = theme.Background(image, self.config) if image else None

    def reload_tools(self):
        plugins.reset_extensions()
        tools, errors = plugins.load_tools(paths.PLUGINS_DIR, "naiz_plugins")
        if self.dev_mode:
            dev_tools, dev_errors = plugins.load_tools(paths.DEV_DIR, "naiz_dev")
            tools += dev_tools
            errors += dev_errors

        self.tools = tools
        self.load_errors = errors
        if errors:
            try:
                (paths.APP_DIR / "error.log").write_text(
                    "\n\n".join(f"[{name}]\n{detail}" for name, detail in errors), encoding="utf-8")
            except Exception:
                pass

        ids = {tool.id for tool in tools}
        self.pages = {key: page for key, page in self.pages.items() if key in ids}
        if self.current is not None:
            self.current = next((tool for tool in tools if tool.id == self.current.id), None)

    def register_title_click(self):
        now = time.monotonic()
        self.title_clicks = [t for t in self.title_clicks if now - t <= DEV_WINDOW_SECONDS]
        self.title_clicks.append(now)
        if len(self.title_clicks) >= DEV_CLICKS:
            self.title_clicks = []
            self.set_dev_mode(not self.dev_mode)

    def set_dev_mode(self, enabled):
        self.dev_mode = enabled
        self.config["dev_mode"] = enabled
        # 只改這一個鍵再寫回,避免把設定面板裡還沒按儲存的背景變更一起存進去
        stored = theme.load_config(str(paths.APP_DIR))
        stored["dev_mode"] = enabled
        theme.save_config(str(paths.APP_DIR), stored)
        self.reload_tools()

    def deactivate_page(self):
        if self.current and self.current.id in self.pages:
            self.pages[self.current.id].deactivate()

    def open_tool(self, tool):
        missing = [dep for dep in tool.requires if not dep.installed()]
        if missing:
            self.consent.open(tool.name, missing, on_done=lambda: self.open_tool(tool))
            return
        if tool.id not in self.pages:
            self.pages[tool.id] = tool.create_page(self)
        self.current = tool

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            self.windowed_size = self.screen.get_size()
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            self.screen = pygame.display.set_mode(self.windowed_size, pygame.RESIZABLE)

    # ------------------------------------------------------------ 繪製

    def _draw_gear(self, center, color):
        if self.gear_img:
            tinted = self.gear_img.copy()
            tinted.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MULT)
            self.screen.blit(tinted, tinted.get_rect(center=center))
            return
        cx, cy = center
        for i in range(8):
            angle = math.pi * 2 * i / 8
            pygame.draw.circle(self.screen, color,
                               (int(cx + math.cos(angle) * 8), int(cy + math.sin(angle) * 8)), 3)
        pygame.draw.circle(self.screen, color, (cx, cy), 7)
        pygame.draw.circle(self.screen, theme.PANEL, (cx, cy), 3)

    def draw_header(self, width, mouse_pos):
        rounded_panel(self.screen, pygame.Rect(0, 0, width, HEADER_H), theme.PANEL, radius=0, alpha=235)
        pygame.draw.line(self.screen, theme.PANEL_EDGE, (0, HEADER_H), (width, HEADER_H))

        accent = self.current.accent if self.current else theme.ACCENT
        pygame.draw.rect(self.screen, accent, (22, 22, 5, 22), border_radius=3)
        self.title_rect = draw_text(self.screen, "Naiz Studio", (38, 20), 21, theme.TEXT, bold=True)
        crumb_right = self.title_rect.right

        if self.current:
            draw_text(self.screen, "/", (crumb_right + 14, 22), 18, theme.TEXT_FAINT)
            crumb_right = draw_text(self.screen, self.current.name, (crumb_right + 34, 22), 17,
                                    theme.TEXT_DIM).right

        self.gear_rect = pygame.Rect(width - 60, 20, 38, 26)
        hot = self.gear_rect.collidepoint(mouse_pos) or self.settings.is_open
        self._draw_gear(self.gear_rect.center, theme.ACCENT if hot else theme.TEXT_DIM)

        if self.current:
            home = pygame.Rect(width - 150, 17, 74, 32)
            self.btn_home.draw(self.screen, home, mouse_pos)
            toolbar = pygame.Rect(crumb_right + 24, 16, home.x - 12 - (crumb_right + 24), 34)
            self.pages[self.current.id].draw_toolbar(toolbar, mouse_pos)

    def draw_home(self, rect, mouse_pos):
        draw_text(self.screen, "工具", (rect.x, rect.y), 24, theme.TEXT, bold=True)
        draw_text(self.screen, "選擇要使用的功能", (rect.x, rect.y + 36), 13, theme.TEXT_DIM)

        categories = [c for c in CATEGORY_ORDER if any(t.category == c for t in self.tools)]
        categories += sorted({t.category for t in self.tools} - set(categories))

        card_w, card_h, gap = 280, 116, 16
        columns = max(1, (rect.width + gap) // (card_w + gap))
        self.card_rects = []
        y = rect.y + 80

        if not self.tools:
            draw_text(self.screen, "沒有可用的工具", (rect.centerx, rect.centery), 16,
                      theme.TEXT_DIM, center=True)

        for category in categories:
            items = [tool for tool in self.tools if tool.category == category]
            label = draw_text(self.screen, category, (rect.x, y), 15, theme.TEXT, bold=True)
            draw_text(self.screen, str(len(items)), (label.right + 10, y + 2), 13, theme.TEXT_FAINT)
            pygame.draw.line(self.screen, theme.PANEL_EDGE, (rect.x, y + 30), (rect.right, y + 30))
            y += 44

            for index, tool in enumerate(items):
                card = pygame.Rect(rect.x + (index % columns) * (card_w + gap),
                                   y + (index // columns) * (card_h + gap), card_w, card_h)
                self.card_rects.append((tool, card))
                hover = card.collidepoint(mouse_pos)
                rounded_panel(self.screen, card, theme.PANEL_LIGHT if hover else theme.PANEL, radius=12,
                              alpha=232, border=tool.accent if hover else theme.PANEL_EDGE)
                pygame.draw.rect(self.screen, tool.accent, (card.x + 18, card.y + 20, 4, 24), border_radius=2)
                draw_text(self.screen, tool.name, (card.x + 32, card.y + 17), 18, theme.TEXT, bold=True)
                # 說明太長時換行,最多 3 行,不再截斷成看不到內容
                for row, line in enumerate(widgets.wrap_text(tool.description, 13, card_w - 36, max_lines=3)):
                    draw_text(self.screen, line, (card.x + 18, card.y + 54 + row * 19), 13, theme.TEXT_DIM)

            y += math.ceil(len(items) / columns) * (card_h + gap) + 20

        if self.load_errors:
            draw_text(self.screen, f"有 {len(self.load_errors)} 個插件載入失敗，詳細內容已寫入 error.log",
                      (rect.x, rect.bottom - 18), 12, theme.WARN)

    def draw_frame(self, mouse_pos=(-100, -100)):
        if self.dev_mode:
            widgets.begin_text_log()
        else:
            widgets.stop_text_log()
        width, height = self.screen.get_size()
        self.screen.fill(theme.BG_DEEP)
        if self.background:
            self.background.draw(self.screen)

        body = pygame.Rect(0, HEADER_H, width, height - HEADER_H)
        if self.current:
            self.pages[self.current.id].draw(body, mouse_pos)
        else:
            self.draw_home(body.inflate(-64, -56), mouse_pos)

        self.draw_header(width, mouse_pos)
        page = self.pages.get(self.current.id) if self.current else None
        if page is not None and page.modal_open():
            widgets.mark_text_layer()
            page.draw_modal(mouse_pos)
        if self.settings.is_open:
            widgets.mark_text_layer()
            self.settings.draw(mouse_pos)
        if self.consent.is_open:
            widgets.mark_text_layer()
            self.consent.draw(mouse_pos)
        self._draw_copy_toast()

    def _draw_copy_toast(self):
        if self.copy_toast is None:
            return
        label, at, pos = self.copy_toast
        if pygame.time.get_ticks() - at > 1600:
            self.copy_toast = None
            return
        paused = widgets.pause_text_log()   # 提示本身不算畫面上的文字
        text = widgets.clip_text(label, 13, 360)
        box = pygame.Rect(pos[0] + 14, pos[1] + 16, theme.font(13).size(text)[0] + 24, 30)
        box.clamp_ip(self.screen.get_rect())
        rounded_panel(self.screen, box, theme.PANEL_LIGHT, radius=8, alpha=240, border=theme.ACCENT)
        draw_text(self.screen, text, (box.x + 12, box.y + 6), 13, theme.TEXT)
        widgets.resume_text_log(paused)

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, mouse_pos):
        if event.type == pygame.QUIT:
            return False
        if event.type == pygame.VIDEORESIZE and not self.fullscreen:
            self.screen = pygame.display.set_mode((max(960, event.w), max(640, event.h)), pygame.RESIZABLE)
            self.windowed_size = self.screen.get_size()
            return True
        if event.type == pygame.KEYUP and event.key == pygame.K_F11:
            self.f11_held = False
            return True
        if event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
            # 開了按鍵連發,按住 F11 不放時只切換一次
            if not self.f11_held:
                self.f11_held = True
                self.toggle_fullscreen()
            return True

        if self.dev_mode and self._copy_click(event, mouse_pos):
            return True
        if self.consent.is_open:
            self.consent.handle_event(event, mouse_pos)
            return True
        if self.settings.is_open:
            self.settings.handle_event(event, mouse_pos)
            return True
        page = self.pages.get(self.current.id) if self.current else None
        if page is not None and page.modal_open():
            # 頁面自己的彈出視窗開著時,事件只給視窗,標題列和頁面都不會被點到
            page.handle_modal_event(event, mouse_pos)
            return True

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.title_rect.inflate(12, 12).collidepoint(mouse_pos):
                self.deactivate_page()
                self.register_title_click()
                return True
            if self.gear_rect.collidepoint(mouse_pos):
                self.deactivate_page()
                self.settings.open()
                return True
            if self.current and self.btn_home.clicked(mouse_pos, True):
                self.deactivate_page()
                self.current = None
                return True
            if self.current is None:
                for tool, card in self.card_rects:
                    if card.collidepoint(mouse_pos):
                        self.open_tool(tool)
                        return True

        if self.current:
            self.pages[self.current.id].handle_event(event, mouse_pos)
        return True

    def _copy_click(self, event, pos):
        """開發者模式:Ctrl+左鍵複製那段文字,Ctrl+Shift+左鍵複製整個畫面的文字;這次點擊不會傳給畫面。"""
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._swallow_click:
            self._swallow_click = False
            return True
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        mods = pygame.key.get_mods()
        if not mods & pygame.KMOD_CTRL:
            return False
        self._swallow_click = True
        whole = bool(mods & pygame.KMOD_SHIFT)
        text = widgets.screen_text() if whole else widgets.text_at(pos)
        if text:
            widgets.copy_to_clipboard(text)
            label = "已複製整個畫面的文字" if whole else f"已複製：{text}"
        else:
            label = "這裡沒有文字"
        self.copy_toast = (label, pygame.time.get_ticks(), pos)
        return True

    def process_events(self, events):
        for event in events:
            # 用事件本身記錄的位置。觸控板輕點、觸控螢幕時游標是直接跳過去的,
            # 若用這一幀開頭讀到的滑鼠位置,換新目標的第一下會落在舊位置上而失效
            pos = getattr(event, "pos", None) or pygame.mouse.get_pos()
            if not self.handle_event(event, pos):
                return False
        return True

    def run(self):
        running = True
        while running:
            running = self.process_events(pygame.event.get())
            mouse_pos = pygame.mouse.get_pos()
            self.consent.update()
            if self.current:
                self.pages[self.current.id].update()
            self.draw_frame(mouse_pos)
            pygame.display.flip()
            self.clock.tick(60)
        pygame.quit()


def main():
    # 用 pythonw 啟動時沒有主控台,錯誤訊息會直接消失,所以改寫進 log 並跳視窗告知
    try:
        App().run()
    except Exception:
        import traceback

        detail = traceback.format_exc()
        try:
            (paths.APP_DIR / "error.log").write_text(detail, encoding="utf-8")
        except Exception:
            pass
        if platform.system() == "Windows":
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(
                    0, f"{detail[-900:]}\n\n完整內容已存到 error.log", "Naiz Studio 發生錯誤", 0x10)
            except Exception:
                pass
        raise


if __name__ == "__main__":
    main()
