"""網頁翻譯的畫面:服務狀態、安裝擴充功能的說明、記下來的紀錄(搜尋、排序、刪除、匯出)。"""

import csv
import os
import time
from pathlib import Path

import pygame

from core import paths, theme, widgets
from core.dialog import Dialog
from core.files import free_path
from core.plugins import Page
from core.scroll import BAR_SPACE, ScrollView
from core.widgets import Button, SegmentedControl, TextInput, draw_text, rounded_panel

from . import service

ROW_H = 84
EXTENSION_DIR = Path(__file__).resolve().parent / "extension"
SORTS = [("new", "最近"), ("count", "最常查"), ("text", "字母")]


class WebTranslatePage(Page):
    def __init__(self, app, tool):
        super().__init__(app, tool)
        accent = tool.accent
        self.search = TextInput(placeholder="搜尋紀錄（原文、翻譯、例句）", accent=accent, size=14)
        self.sort = SegmentedControl(SORTS, accent=accent)
        self.btn_install = Button("安裝擴充功能", filled=False, size=13)
        self.btn_export = Button("匯出 CSV", filled=False, size=13)
        self.view = ScrollView(accent=accent)
        self.dialog = Dialog(lambda: self.screen, accent)
        self.records, self._mtime, self._checked = [], None, 0.0
        self.rows = []
        self.area = pygame.Rect(0, 0, 0, 0)
        self.notice = ""

    # ------------------------------------------------------------ 資料
    def _reload(self):
        """紀錄檔有變(擴充功能剛記下來)就重新讀;一秒看一次。"""
        if time.monotonic() - self._checked < 1:
            return
        self._checked = time.monotonic()
        path = service.records_path()
        mtime = path.stat().st_mtime if path.exists() else None
        if mtime != self._mtime:
            self._mtime = mtime
            self.records = service.load_records()

    def shown(self):
        words = self.search.text.strip().lower()
        items = [r for r in self.records if not words or any(
            words in str(r.get(f, "")).lower() for f in ("text", "translation", "meaning", "context", "title"))]
        if self.sort.value == "count":
            return sorted(items, key=lambda r: (-r.get("count", 1), r.get("updated", "")))
        if self.sort.value == "text":
            return sorted(items, key=lambda r: r["text"].lower())
        return sorted(items, key=lambda r: r.get("updated", ""), reverse=True)

    def delete(self, record):
        with service._lock:
            records = [r for r in service.load_records() if service.key(r["text"]) != service.key(record["text"])]
            service.save_records(records)
        self._checked = 0
        self._reload()

    def export(self):
        folder = paths.OUTPUT_DIR / "webtrans"
        folder.mkdir(parents=True, exist_ok=True)
        path = free_path(folder, time.strftime("網頁翻譯紀錄_%Y-%m-%d"), ".csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as fp:        # utf-8-sig:Excel 直接開不會亂碼
            writer = csv.writer(fp)
            writer.writerow(["原文", "意思／翻譯", "詞性", "讀音", "例句", "查過幾次", "網頁", "網址", "記錄時間"])
            for r in self.shown():
                writer.writerow([r["text"], r.get("meaning") or r.get("translation", ""), r.get("pos", ""),
                                 r.get("reading", ""), r.get("context", ""), r.get("count", 1), r.get("title", ""),
                                 r.get("url", ""), r.get("created", "")])
        self.notice = f"已匯出 {path.name}"
        os.startfile(folder)

    def install_help(self):
        lines = ["1. 在 Brave（或 Chrome、Edge）的網址列輸入 brave://extensions（Chrome 是 chrome://extensions）",
                 "2. 打開右上角的「開發人員模式」",
                 "3. 按「載入未封裝項目」，選擇等一下打開的 extension 資料夾",
                 "4. 已經開著的網頁重新整理一次，選取文字後旁邊會出現「譯」",
                 "PDF 會自動改用擴充功能內的檢視器打開；要讀電腦裡的 PDF，請在擴充功能的「詳細資料」開啟「允許存取檔案網址」。",
                 "整頁翻譯：按工具列上的擴充功能圖示或 Alt+T，再按一次換回原文（圖示可以在拼圖選單裡釘選）。",
                 "Naiz Studio 要開著才能翻譯；翻譯模型和目標語言跟即時字幕的設定一樣。"]

        def chosen(key, _text):
            if key == "open":
                widgets.copy_to_clipboard(str(EXTENSION_DIR))
                os.startfile(EXTENSION_DIR)
                self.notice = "已打開 extension 資料夾（路徑也複製好了，可以貼上）"

        self.dialog.open("安裝擴充功能", lines, [("close", "關閉", False), ("open", "打開 extension 資料夾", True)],
                         chosen)

    # ------------------------------------------------------------ 浮動視窗
    def modal_open(self):
        return self.dialog.is_open

    def draw_modal(self, mouse_pos):
        self.dialog.draw(mouse_pos)

    def handle_modal_event(self, event, mouse_pos):
        self.dialog.handle_event(event, mouse_pos)

    def deactivate(self):
        self.search.blur()
        self.view.reset()

    def update(self):
        self._reload()
        self.view.update(pygame.mouse.get_pos())

    # ------------------------------------------------------------ 繪製
    def draw(self, rect, mouse_pos):
        self._reload()
        screen = self.screen
        margin = 20
        top = pygame.Rect(rect.x + margin, rect.y + margin, rect.width - margin * 2, 64)
        rounded_panel(screen, top, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        model, target = service.settings()
        if service.state["error"]:
            status, color = service.state["error"], theme.DANGER
        elif service.state["busy"]:
            status, color = f"翻譯中：{service.state['last']}", self.tool.accent
        else:
            status, color = "待命中：在瀏覽器選取文字，按旁邊的「譯」", theme.TEXT
        right = top.right - 16
        self.btn_export.draw(screen, pygame.Rect(right - 96, top.y + 16, 96, 32), mouse_pos)
        self.btn_install.draw(screen, pygame.Rect(right - 96 - 10 - 120, top.y + 16, 120, 32), mouse_pos)
        text_w = top.width - 270
        draw_text(screen, widgets.clip_text(status, 14, text_w), (top.x + 16, top.y + 12), 14, color)
        from plugins.subtitle import translate

        detail = self.notice or (f"翻譯模型 {model}，翻成{translate.TARGET_NAMES.get(target, target)}"
                                 "（和即時字幕的設定一樣，要換請到即時字幕的「選擇翻譯模型」）")
        draw_text(screen, widgets.clip_text(detail, 12, text_w), (top.x + 16, top.y + 36), 12, theme.TEXT_FAINT)

        body = pygame.Rect(top.x, top.bottom + 14, top.width, rect.bottom - margin - top.bottom - 14)
        rounded_panel(screen, body, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        items = self.shown()
        draw_text(screen, f"紀錄 ({len(items)})", (body.x + 16, body.y + 16), 15, theme.TEXT, bold=True)
        self.sort.draw(screen, pygame.Rect(body.right - 16 - 210, body.y + 10, 210, 30), mouse_pos)
        self.search.draw(screen, pygame.Rect(body.x + 110, body.y + 10, body.width - 110 - 240, 30), mouse_pos)
        pygame.draw.line(screen, theme.PANEL_EDGE, (body.x + 12, body.y + 50), (body.right - 12, body.y + 50))
        area = pygame.Rect(body.x, body.y + 51, body.width, body.height - 55)
        self.area = area
        self.rows = []
        if not items:
            self.view.clear()
            message = "沒有符合的紀錄" if self.records else "還沒有紀錄：在網頁上翻譯後按「記下來」"
            draw_text(screen, message, area.center, 14, theme.TEXT_DIM, center=True)
            return
        view = self.view
        view.layout(area, len(items) * ROW_H + 12)
        screen.set_clip(area)
        accent = self.tool.accent
        for index, record in enumerate(items):
            y = area.y + 6 + index * ROW_H - view.scroll
            if y + ROW_H < area.y or y > area.bottom:
                continue
            row = pygame.Rect(area.x + 10, y, area.width - 20 - BAR_SPACE, ROW_H - 8)
            hover = row.collidepoint(mouse_pos) and area.collidepoint(mouse_pos)
            rounded_panel(screen, row, theme.PANEL_LIGHT if hover else theme.BG_DEEP, radius=8, alpha=200)
            inner = row.width - 60
            head = record["text"]
            extra = "・".join(v for v in (record.get("pos"), record.get("reading")) if v and v != "-")
            title = draw_text(screen, widgets.clip_text(head, 15, inner // 2, bold=True), (row.x + 14, row.y + 8), 15,
                              theme.TEXT, bold=True)
            if extra:
                draw_text(screen, widgets.clip_text(extra, 12, inner - title.width - 16), (title.right + 10, row.y + 11),
                          12, theme.TEXT_FAINT)
            meaning = record.get("meaning") or record.get("translation") or ""
            draw_text(screen, widgets.clip_text(meaning, 13, inner), (row.x + 14, row.y + 33), 13, accent)
            context = record.get("context", "").replace("\n", " ")
            if service.key(context) == service.key(record["text"]):
                context = ""                    # 選的就是整句:例句和原文一樣,不重複顯示
            meta = " · ".join(v for v in (f"查過 {record.get('count', 1)} 次", context) if v)
            draw_text(screen, widgets.clip_text(meta, 12, inner), (row.x + 14, row.y + 55), 12, theme.TEXT_FAINT)
            cross = pygame.Rect(row.right - 34, row.y + 8, 24, 24)
            over = cross.collidepoint(mouse_pos)
            rounded_panel(screen, cross, theme.DANGER if over else theme.PANEL, radius=6)
            cx, cy = cross.center
            pen = theme.BG_DEEP if over else theme.TEXT_DIM
            pygame.draw.line(screen, pen, (cx - 4, cy - 4), (cx + 4, cy + 4), 2)
            pygame.draw.line(screen, pen, (cx + 4, cy - 4), (cx - 4, cy + 4), 2)
            self.rows.append((cross, record))
        screen.set_clip(None)
        view.draw(screen, mouse_pos)

    # ------------------------------------------------------------ 事件
    def handle_event(self, event, mouse_pos):
        if self.search.handle(event, mouse_pos):
            return
        if self.view.handle_event(event, mouse_pos):
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        if self.btn_install.clicked(mouse_pos, True):
            self.install_help()
        elif self.btn_export.clicked(mouse_pos, True):
            self.export()
        elif self.sort.clicked(mouse_pos, True):
            self.view.reset()
        elif self.area.collidepoint(mouse_pos):
            for cross, record in self.rows:
                if cross.collidepoint(mouse_pos):
                    self.delete(record)
                    return
