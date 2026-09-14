"""模組缺少外部元件時的同意視窗:簡短列出要下載的東西與用途,同意後才下載。"""

import pygame

from . import deps, tasks, theme, widgets
from .widgets import Button, ProgressBar, draw_text, rounded_panel

ROW_H = 60


class ConsentDialog:
    def __init__(self, app):
        self.app = app
        self.is_open = False
        self.name = ""
        self.on_done = None
        self.items = []
        self.phase = "ask"          # ask / downloading / error
        self.error = ""
        self.runner = tasks.TaskRunner()
        self.btn_agree = Button("同意並下載", accent=theme.ACCENT, size=15)
        self.btn_cancel = Button("取消", filled=False, size=14)

    def open(self, name, items, on_done=None):
        """name: 顯示在說明裡的功能名稱;on_done: 全部下載完成後要做的事。"""
        self.name = name
        self.on_done = on_done
        self.items = list(items)
        self.phase = "ask"
        self.error = ""
        self.is_open = True

    def close(self):
        self.is_open = False
        self.on_done = None
        self.items = []

    def _start(self):
        items = list(self.items)

        def job(runner):
            for index, dep in enumerate(items, start=1):
                if dep.installed():
                    continue

                def progress(done, total, name=dep.name, i=index):
                    size = f"{deps.human_size(done)} / {deps.human_size(total)}" if total else deps.human_size(done)
                    runner.report(done, max(1, total), f"[{i}/{len(items)}] 下載 {name}  {size}")

                deps.install(dep, progress=progress, cancel=runner.cancel_event)

        self.phase = "downloading"
        self.error = ""
        self.runner.start(job)

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, pos):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._cancel()
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        if self.btn_cancel.clicked(pos, True):
            self._cancel()
        elif self.phase != "downloading" and self.btn_agree.clicked(pos, True):
            self._start()

    def _cancel(self):
        if self.phase == "downloading":
            self.runner.request_cancel()
        else:
            self.close()

    def update(self):
        if self.phase != "downloading" or self.runner.running or not self.runner.finished:
            return
        _, _, _, _, error, _ = self.runner.snapshot()
        if error:
            self.phase = "error"
            if error.startswith("Cancelled"):
                self.error = "已取消下載"
            elif error.startswith("HTTPError"):
                self.error = "下載來源暫時無法使用，請稍後再試"
            elif "URLError" in error or "timed out" in error:
                self.error = "無法連線，請確認網路後再試一次"
            else:
                # 例外名稱對使用者沒有意義,只顯示後面的說明
                self.error = error.split(": ", 1)[-1]
            return
        on_done = self.on_done
        self.close()
        if on_done:
            on_done()

    # ------------------------------------------------------------ 繪製

    def draw(self, mouse_pos):
        screen = self.app.screen
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 170))
        screen.blit(veil, (0, 0))

        panel_w = 520
        panel_h = 196 + len(self.items) * ROW_H + (34 if self.phase != "ask" else 0)
        panel = pygame.Rect((width - panel_w) // 2, (height - panel_h) // 2, panel_w, panel_h)
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.PANEL_EDGE)

        x = panel.x + 24
        inner = panel_w - 48
        y = panel.y + 20
        draw_text(screen, "需要下載額外元件", (x, y), 17, theme.TEXT, bold=True)
        y += 32
        draw_text(screen, widgets.clip_text(f"使用「{self.name}」前，需要先下載以下元件", 13, inner),
                  (x, y), 13, theme.TEXT_DIM)
        y += 30

        for dep in self.items:
            row = pygame.Rect(x, y, inner, ROW_H - 8)
            rounded_panel(screen, row, theme.BG_DEEP, radius=8, alpha=210, border=theme.PANEL_EDGE)
            draw_text(screen, dep.name, (row.x + 14, row.y + 8), 15, theme.TEXT, bold=True)
            if dep.installed():
                draw_text(screen, "已完成", (row.right - 14, row.y + 17), 13, theme.ACCENT, right=True)
            else:
                draw_text(screen, dep.size_text, (row.right - 14, row.y + 17), 13, theme.ACCENT, right=True)
            draw_text(screen, widgets.clip_text(dep.purpose, 12, row.width - 110), (row.x + 14, row.y + 30),
                      12, theme.TEXT_DIM)
            y += ROW_H

        draw_text(screen, "下載後放在程式資料夾內，之後不需要再下載", (x, y + 2), 12, theme.TEXT_FAINT)
        y += 28

        if self.phase == "downloading":
            done, total, message, _, _, _ = self.runner.snapshot()
            ratio = done / total if total else 0
            ProgressBar(theme.ACCENT).draw(screen, pygame.Rect(x, y + 4, inner, 18), ratio, f"{int(ratio * 100)}%")
            draw_text(screen, widgets.clip_text(message, 12, inner), (x, y + 28), 12, theme.TEXT_DIM)
        elif self.phase == "error":
            draw_text(screen, widgets.clip_text(self.error, 13, inner), (x, y + 8), 13, theme.DANGER)

        foot_y = panel.bottom - 56
        if self.phase == "downloading":
            self.btn_cancel.label = "取消下載"
            self.btn_cancel.draw(screen, pygame.Rect(panel.right - 24 - 110, foot_y, 110, 36), mouse_pos)
        else:
            self.btn_cancel.label = "取消"
            self.btn_agree.label = "重試" if self.phase == "error" else "同意並下載"
            self.btn_cancel.draw(screen, pygame.Rect(panel.right - 24 - 130 - 10 - 86, foot_y, 86, 36), mouse_pos)
            self.btn_agree.draw(screen, pygame.Rect(panel.right - 24 - 130, foot_y, 130, 36), mouse_pos)
