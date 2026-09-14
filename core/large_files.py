"""處理很大的檔案前的提醒視窗:不直接擋,讓使用者看完說明、勾選已閱讀後自己決定要不要繼續。

設定裡可以關掉提醒(config.json 的 large_file_warning)。
"""

import pygame

from . import deps, theme, widgets
from .widgets import Button, draw_text, rounded_panel

WARN_PIXELS = 100_000_000     # 約 1 億像素:解開後光是圖片本身就要 400 MB 左右的記憶體
WARN_BYTES = 1024 ** 3        # 1 GB
BYTES_PER_PIXEL = 4
CONFIG_KEY = "large_file_warning"
ROW_H = 58
MAX_ROWS = 4


def reason(size=0, pixels=0) -> str:
    """需要提醒的原因;不需要提醒時回傳空字串。"""
    parts = []
    if pixels >= WARN_PIXELS:
        parts.append(f"處理時約需 {deps.human_size(pixels * BYTES_PER_PIXEL)} 記憶體")
    if size >= WARN_BYTES:
        parts.append(f"檔案大小 {deps.human_size(size)}")
    return "，".join(parts)


class LargeFileDialog:
    def __init__(self, app):
        self.app = app
        self.is_open = False
        self.rows = []
        self.on_continue = None
        self.read = False
        self.check_rect = pygame.Rect(0, 0, 0, 0)
        self.btn_next = Button("下一步", accent=theme.WARN, size=15)
        self.btn_cancel = Button("取消", filled=False, size=14)

    def confirm(self, rows, on_continue):
        """rows:[(檔名, 原因)]。沒有要提醒的檔案、或設定裡關掉提醒時直接繼續。"""
        if not rows or not self.app.config.get(CONFIG_KEY, True):
            on_continue()
            return
        self.rows, self.on_continue = list(rows), on_continue
        self.read = False
        self.is_open = True

    def close(self):
        self.is_open = False
        self.rows, self.on_continue = [], None

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, pos):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.close()
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        if self.check_rect.collidepoint(pos):
            self.read = not self.read
        elif self.btn_cancel.clicked(pos, True):
            self.close()
        elif self.btn_next.clicked(pos, True):
            action = self.on_continue
            self.close()
            action()

    # ------------------------------------------------------------ 繪製

    def draw(self, mouse_pos):
        screen = self.app.screen
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 170))
        screen.blit(veil, (0, 0))

        shown = self.rows[:MAX_ROWS]
        more = len(self.rows) - len(shown)
        panel_w = 520
        panel_h = 238 + len(shown) * ROW_H + (22 if more else 0)
        panel = pygame.Rect((width - panel_w) // 2, (height - panel_h) // 2, panel_w, panel_h)
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.WARN)

        x, inner = panel.x + 24, panel_w - 48
        y = panel.y + 20
        draw_text(screen, "檔案很大，繼續前請先確認", (x, y), 17, theme.WARN, bold=True)
        y += 32
        draw_text(screen, "以下檔案處理時會用到大量記憶體或時間", (x, y), 13, theme.TEXT_DIM)
        y += 28

        for name, why in shown:
            row = pygame.Rect(x, y, inner, ROW_H - 8)
            rounded_panel(screen, row, theme.BG_DEEP, radius=8, alpha=210, border=theme.PANEL_EDGE)
            draw_text(screen, widgets.clip_text(name, 14, row.width - 28, bold=True), (row.x + 14, row.y + 7), 14,
                      theme.TEXT, bold=True)
            draw_text(screen, widgets.clip_text(why, 12, row.width - 28), (row.x + 14, row.y + 28), 12, theme.WARN)
            y += ROW_H
        if more:
            draw_text(screen, f"還有 {more} 個檔案", (x, y), 12, theme.TEXT_FAINT)
            y += 22

        y += 4
        for line in ("記憶體不夠時處理會失敗，處理期間電腦也可能變慢",
                     "建議先關閉其他佔用記憶體的程式；不確定的話可以先取消"):
            draw_text(screen, line, (x, y), 12, theme.TEXT_DIM)
            y += 20

        y += 12
        box = pygame.Rect(x, y, 18, 18)
        label = draw_text(screen, "我已閱讀以上說明", (box.right + 10, y), 13,
                          theme.TEXT if self.read else theme.TEXT_DIM)
        self.check_rect = box.union(label).inflate(8, 8)
        hovered = self.check_rect.collidepoint(mouse_pos)
        if self.read:
            rounded_panel(screen, box, theme.WARN, radius=4)
            pygame.draw.lines(screen, theme.BG_DEEP, False,
                              [(box.x + 4, box.centery), (box.x + 8, box.bottom - 5), (box.right - 4, box.y + 5)], 2)
        else:
            rounded_panel(screen, box, theme.PANEL_LIGHT, radius=4, border=theme.WARN if hovered else theme.TEXT_FAINT)

        foot_y = panel.bottom - 56
        draw_text(screen, "可以在設定裡關閉大檔警告", (x, foot_y + 12), 12, theme.TEXT_FAINT)
        self.btn_next.enabled = self.read
        self.btn_cancel.draw(screen, pygame.Rect(panel.right - 24 - 110 - 10 - 86, foot_y, 86, 36), mouse_pos)
        self.btn_next.draw(screen, pygame.Rect(panel.right - 24 - 110, foot_y, 110, 36), mouse_pos)
