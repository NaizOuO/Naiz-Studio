"""圖片工具:把圖片拖進視窗,轉換格式、壓縮,或依順序合成 PDF。"""

import os
import queue
import threading
from pathlib import Path

import pygame
from PIL import Image

from core import deps, large_files, paths, theme, widgets
from core.plugins import Page
from core.scroll import BAR_SPACE, ScrollView
from core.widgets import Button, ChoiceGrid, SegmentedControl, Slider, Toggle, draw_text, rounded_panel

from . import ops
from .editor import ImageEditor

ROW_H = 72
THUMB = 50
# 兩列各 6 個:左邊一欄是「原格式」和 PDF,右邊 10 個是圖片格式
FORMAT_OPTIONS = [("keep", "原格式"), ("jpg", "JPG"), ("png", "PNG"), ("webp", "WebP"), ("avif", "AVIF"),
                  ("heic", "HEIC"), ("pdf", "PDF"), ("gif", "GIF"), ("svg", "SVG"), ("ico", "ICO"),
                  ("bmp", "BMP"), ("tiff", "TIFF")]
FORMAT_NOTES = {
    "keep": "格式不變，只壓縮或縮小；SVG 沒辦法壓縮，會存成 PNG",
    "jpg": "相容性最好，適合照片；不支援透明，透明處會變成白色",
    "png": "無損、支援透明，適合截圖和圖示；照片的檔案會比較大",
    "webp": "比 JPG 小約三成，支援透明，網頁常用",
    "avif": "比 WebP 更小，但部分舊軟體打不開",
    "heic": "iPhone 照片格式，比 JPG 小很多，但 Windows 舊軟體可能打不開",
    "gif": "最多 256 色，支援動畫；照片轉 GIF 顏色會變差",
    "svg": "把色塊描成向量圖形，放大不會模糊，適合圖示和 Logo",
    "ico": "Windows 圖示檔，會自動做出 16~256 px 多種尺寸",
    "bmp": "完全不壓縮，檔案很大，只在舊軟體需要時使用",
    "tiff": "無損，印刷和掃描常用，檔案較大",
    "pdf": "每張圖一頁，依左側清單的順序排列",
}
QUALITY_FORMATS = {"keep", "jpg", "webp", "avif", "heic", "pdf"}
QUALITY_NOTES = {
    "keep": "只影響 JPG、WebP、AVIF、HEIC；70~85 通常看不出差別",
    "pdf": "照片以 JPG 放入，70~85 通常看不出差別；有透明的圖以無損方式放入",
}
PDF_OUTPUTS = [("combine", "合成一個檔"), ("each", "每張一個檔")]
PDF_OUTPUT_NOTES = {"combine": "全部圖片合成一份 PDF，可用 ▲▼ 調整順序", "each": "每張圖各自存成一份 PDF"}
PDF_PAGES = [("fit", "依圖片大小"), ("min", "依最小圖片"), ("a4", "A4")]
PDF_PAGE_NOTES = {"fit": "頁面和圖片一樣大，不留白邊",
                  "min": "以最小圖片的長邊為準，調整所有圖片的大小", "a4": "放進 A4 頁面，橫的圖自動用橫向"}
SVG_COLORS = [("color", "彩色"), ("bw", "黑白")]
SVG_NOTES = {"color": "保留顏色，描出每一塊色塊", "bw": "只分黑白，適合文字、線稿和簽名"}
STATUS_COLORS = {"error": theme.DANGER, "cancelled": theme.TEXT_FAINT}


def output_dir():
    return paths.OUTPUT_DIR / "images"


class Item:
    def __init__(self, path: Path):
        self.path = path
        self.size = path.stat().st_size if path.exists() else 0
        self.info = None     # 讀完後是 dict;讀不了時是錯誤訊息
        self.thumb = None
        self.status = "waiting"
        self.message = ""
        self.target = ""
        self.out_size = 0
        self.edit = ops.Edit()

    @property
    def frames(self):
        return self.info["frames"] if isinstance(self.info, dict) else 1

    def detail(self):
        if self.info is None:
            return f"讀取中... · {deps.human_size(self.size)}"
        if isinstance(self.info, str):
            return f"無法讀取：{self.info}"
        width, height = self.info["size"]
        text = f"{self.info['format']} · {width}×{height} · {deps.human_size(self.size)}"
        if self.frames > 1:
            text += f" · 動畫 {self.frames} 格"
        if self.edit.active:
            text += " · 已編輯"
        return text

    def result(self):
        """(文字, 顏色);完成後顯示大小變化。"""
        if self.status == "running":
            return "處理中...", None
        if self.status == "error":
            return f"失敗：{self.message}", theme.DANGER
        if self.status == "cancelled":
            return "已取消", theme.TEXT_FAINT
        if self.status == "done":
            if not self.out_size:
                return self.message, None
            change = (self.out_size - self.size) / max(1, self.size) * 100
            text = (f"{deps.human_size(self.size)} → {self.target} {deps.human_size(self.out_size)}"
                    f"（{change:+.0f}%）")
            if self.message:
                text += f" · {self.message}"
            return text, None if change <= 0 else theme.WARN
        return self.detail(), theme.DANGER if isinstance(self.info, str) else theme.TEXT_DIM


class ImagePage(Page):
    def __init__(self, app, tool):
        super().__init__(app, tool)
        accent = tool.accent
        self.items = []
        self.notice = ""
        self.combined = None
        self._lock = threading.Lock()
        self.worker = None
        self.stop_event = threading.Event()
        self._probe_queue = queue.Queue()
        threading.Thread(target=self._probe_worker, daemon=True).start()

        self.fmt = ChoiceGrid(FORMAT_OPTIONS, columns=6, accent=accent)
        self.pdf_output = SegmentedControl(PDF_OUTPUTS, accent=accent)
        self.pdf_page = SegmentedControl(PDF_PAGES, accent=accent)
        self.svg_color = SegmentedControl(SVG_COLORS, accent=accent)
        self.quality = Slider(10, 100, 80, accent=accent)
        self.reduce_colors = Toggle(False, accent=accent)
        self.limit = Toggle(False, accent=accent)
        self.max_side = Slider(200, 8000, 2000, step=100, accent=accent)
        self.strip_meta = Toggle(True, accent=accent)
        self.auto_rotate = Toggle(True, accent=accent)
        self.compress = Toggle(False, accent=accent)
        self._unused_toggle = Toggle(False, accent=accent)   # 無損格式用不到壓縮品質時,畫一個灰色的佔位
        self.split_frames = Toggle(False, accent=accent)
        self.controls = []   # 這一幀畫出來、可以點的設定
        self.sliders = []

        self.list_view = ScrollView(accent=accent)
        self.list_area = pygame.Rect(0, 0, 0, 0)
        self.settings_view = ScrollView(accent=accent, indicator=True)
        self.settings_area = pygame.Rect(0, 0, 0, 0)
        self.row_buttons = []
        self.btn_clear = Button("清空清單", filled=False, size=13)
        self.btn_output = Button("輸出資料夾", filled=False, size=14)
        self.btn_cancel = Button("取消", accent=theme.DANGER, filled=False)
        self.btn_run = Button("開始轉換", accent=accent)
        self.editor = ImageEditor(lambda: self.screen, accent)

    # ------------------------------------------------------------ 資料

    @property
    def running(self):
        return self.worker is not None and self.worker.is_alive()

    @property
    def ordering(self):
        return self.fmt.value == "pdf" and self.pdf_output.value == "combine"

    def count(self, status):
        return sum(item.status == status for item in self.items)

    def settings(self):
        return ops.Settings(fmt=self.fmt.value, quality=int(self.quality.value), limit=self.limit.value,
                            max_side=int(self.max_side.value), reduce_colors=self.reduce_colors.value,
                            strip_meta=self.strip_meta.value, auto_rotate=self.auto_rotate.value,
                            svg_color=self.svg_color.value, pdf_combine=self.pdf_output.value == "combine",
                            pdf_page=self.pdf_page.value, compress=self.compress.value,
                            split_frames=self.split_frames.value)

    def add_files(self, raw_paths):
        candidates, skipped = [], 0
        for raw in raw_paths:
            path = Path(raw)
            if path.is_dir():
                # 拖進資料夾時加入裡面的圖片(不含子資料夾)
                candidates += sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in ops.READ_EXTS)
            elif path.is_file() and path.suffix.lower() in ops.READ_EXTS:
                candidates.append(path)
            else:
                skipped += 1
        with self._lock:
            existing = {item.path for item in self.items}
            for path in candidates:
                if path in existing:
                    continue
                item = Item(path)
                self.items.append(item)
                existing.add(path)
                self._probe_queue.put(item)
        if skipped:
            self.notice = "有檔案不是支援的圖片格式，已略過"
        elif candidates:
            self.notice = ""

    def _probe_worker(self):
        while True:
            item = self._probe_queue.get()
            try:
                item.info = ops.probe(item.path, (THUMB, THUMB))
            except Exception as exc:
                item.info = ops.describe_error(exc)

    def large_rows(self, items, s=None):
        """需要先提醒的大檔:[(檔名, 原因)]。s 是轉換設定;沒給時是打開編輯視窗。"""
        rows = []
        for item in items:
            pixels = 0
            if isinstance(item.info, dict):
                width, height = item.info["size"]
                if s is not None and s.side and ops.source_format(item.path) == "svg":
                    scale = s.side / max(width, height)   # 限制尺寸時 SVG 直接畫成設定的大小
                    width, height = width * scale, height * scale
                pixels = width * height
                target = ops.target_format(item.path, s.fmt) if s is not None else ""
                if s is not None and item.frames > 1 and s.fmt != "pdf" and target in ops.ANIMATED_FORMATS:
                    pixels *= item.frames   # 轉成動畫時每一格都要留在記憶體裡
            why = large_files.reason(item.size, int(pixels))
            if why:
                rows.append((item.path.name, why))
        return rows

    def start(self):
        if self.running or not self.items:
            return
        settings = self.settings()
        with self._lock:
            items = list(self.items)
        self.app.large_files.confirm(self.large_rows(items, settings), lambda: self._begin(settings, items))

    def _begin(self, settings, items):
        if self.running:
            return
        with self._lock:
            for item in items:
                item.status, item.message, item.out_size = "waiting", "", 0
        self.notice = ""
        self.combined = None
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._work, args=(settings, items, [item.edit.copy() for item in items]),
                                       daemon=True)
        self.worker.start()

    def _work(self, s, items, edits):
        folder = output_dir()
        if s.fmt == "pdf" and s.pdf_combine:
            self._work_combined(s, items, edits, folder)
        else:
            for item, edit in zip(items, edits):
                if self.stop_event.is_set():
                    break
                item.status = "running"
                try:
                    out, item.message = ops.convert(item.path, folder, s, edit)
                    item.target = ops.LABELS[ops.target_format(item.path, s.fmt)]
                    # 逐格拆開時輸出的是資料夾,大小算裡面所有圖片的合計
                    item.out_size = (sum(f.stat().st_size for f in out.iterdir()) if out.is_dir()
                                     else out.stat().st_size)
                    item.status = "done"
                except Exception as exc:
                    item.message = ops.describe_error(exc)
                    item.status = "error"
        for item in items:
            if item.status in ("waiting", "running"):
                item.status = "cancelled"

    def _work_combined(self, s, items, edits, folder):
        out = ops.free_path(folder, f"{items[0].path.stem}_merged", ".pdf")

        def on_image(index):
            if index:
                items[index - 1].status = "done"
            items[index].status = "running"

        try:
            failed = ops.images_to_pdf([item.path for item in items], out, s, on_image, self.stop_event,
                                        edits)
        except ops.Cancelled:
            return
        except Exception as exc:
            self.notice = f"合成失敗：{ops.describe_error(exc)}"
            for item in items:
                item.status = "error" if item.status == "running" else item.status
            return
        page = 0
        for index, item in enumerate(items):
            if index in failed:
                item.status, item.message = "error", failed[index]
            else:
                page += 1
                item.status, item.message = "done", f"已放入 PDF 第 {page} 頁"
        self.combined = (out, out.stat().st_size, page)

    def stop(self):
        self.stop_event.set()

    def _edited(self, item):
        item.thumb = None   # 重新產生縮圖,顯示編輯後的樣子

    def open_editor(self, item):
        self.app.large_files.confirm(
            self.large_rows([item]),
            lambda: self.editor.open(item, lambda: list(self.items), self.auto_rotate.value, self._edited))

    def modal_open(self):
        return self.editor.is_open

    def draw_modal(self, mouse_pos):
        self.editor.draw(mouse_pos)

    def handle_modal_event(self, event, mouse_pos):
        self.editor.handle_event(event, mouse_pos)

    def deactivate(self):
        self.list_view.reset()
        self.settings_view.reset()
        for slider in (self.quality, self.max_side):
            slider.dragging = False

    def update(self):
        mouse = pygame.mouse.get_pos()
        self.list_view.update(mouse)
        self.settings_view.update(mouse)
        if self.editor.is_open:
            self.editor.update()

    # ------------------------------------------------------------ 繪製

    def draw(self, rect, mouse_pos):
        margin = 20
        content_y = rect.y + margin
        footer_h = 74
        content_h = rect.bottom - content_y - footer_h - margin * 2
        list_w = int((rect.width - margin * 3) * 0.54)
        self.draw_list(pygame.Rect(rect.x + margin, content_y, list_w, content_h), mouse_pos)
        self.draw_settings(pygame.Rect(rect.x + margin * 2 + list_w, content_y,
                                       rect.width - list_w - margin * 3, content_h), mouse_pos)
        self.draw_footer(pygame.Rect(rect.x + margin, rect.bottom - footer_h - margin,
                                     rect.width - margin * 2, footer_h), mouse_pos)

    def _draw_empty(self, area):
        screen = self.screen
        cx, cy = area.centerx, area.centery - 34
        # 簡單的圖片圖示:相框、山、太陽
        frame = pygame.Rect(cx - 34, cy - 26, 68, 52)
        pygame.draw.rect(screen, theme.PANEL_EDGE, frame, 3, border_radius=6)
        pygame.draw.polygon(screen, theme.PANEL_EDGE, [(frame.x + 8, frame.bottom - 8), (cx - 8, cy - 4),
                                                       (cx + 6, cy + 8), (cx + 14, cy + 1),
                                                       (frame.right - 8, frame.bottom - 8)])
        pygame.draw.circle(screen, theme.PANEL_EDGE, (cx + 16, cy - 10), 6)
        draw_text(screen, "把圖片拖曳到這個視窗", (cx, area.centery + 30), 16, theme.TEXT_DIM, center=True)
        draw_text(screen, "支援 JPG、PNG、WebP、AVIF、HEIC、GIF、SVG、ICO、BMP、TIFF",
                  (cx, area.centery + 54), 13, theme.TEXT_FAINT, center=True)
        draw_text(screen, "可一次拖多張或整個資料夾", (cx, area.centery + 74), 13, theme.TEXT_FAINT, center=True)

    def draw_list(self, rect, mouse_pos):
        screen = self.screen
        accent = self.tool.accent
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        draw_text(screen, f"檔案清單 ({len(self.items)})", (rect.x + 16, rect.y + 13), 15, theme.TEXT, bold=True)
        if self.items and not self.running:
            self.btn_clear.draw(screen, pygame.Rect(rect.right - 96, rect.y + 9, 80, 26), mouse_pos)
        pygame.draw.line(screen, theme.PANEL_EDGE, (rect.x + 12, rect.y + 44), (rect.right - 12, rect.y + 44))

        area = pygame.Rect(rect.x, rect.y + 45, rect.width, rect.height - 45)
        self.list_area = area
        self.row_buttons = []
        if not self.items:
            self.list_view.clear()
            self._draw_empty(area)
            return

        view = self.list_view
        items = list(self.items)
        view.layout(area, len(items) * ROW_H + 16)
        ordering = self.ordering and not self.running
        screen.set_clip(area)
        for index, item in enumerate(items):
            y = area.y + 8 + index * ROW_H - view.scroll
            if y + ROW_H < area.y or y > area.bottom:
                continue
            row = pygame.Rect(area.x + 10, y, area.width - 10 - BAR_SPACE, ROW_H - 8)
            hover = row.collidepoint(mouse_pos) and area.collidepoint(mouse_pos)
            rounded_panel(screen, row, theme.PANEL_LIGHT if hover else theme.BG_DEEP, radius=8, alpha=200)

            box = pygame.Rect(row.x + 8, row.centery - THUMB // 2, THUMB, THUMB)
            rounded_panel(screen, box, theme.PANEL, radius=6)
            editable = isinstance(item.info, dict)
            if item.thumb is None and editable and item.info["thumb"] is not None:
                size, data = item.info["thumb"]
                image = ops.apply_edit(Image.frombytes("RGBA", size, data), item.edit)
                image.thumbnail((THUMB, THUMB))
                item.thumb = pygame.image.frombytes(image.tobytes(), image.size, "RGBA")
            if item.thumb is not None:
                screen.blit(item.thumb, item.thumb.get_rect(center=box.center))
            elif editable:
                draw_text(screen, "大圖", box.center, 12, theme.TEXT_FAINT, center=True)   # 太大不做縮圖
            else:
                draw_text(screen, "?" if isinstance(item.info, str) else "...", box.center, 13, theme.TEXT_FAINT,
                          center=True)

            shift = 56 if editable else 0
            buttons_w = 34 + shift + (92 if ordering else 0) if not self.running else 0
            text_x = box.right + 12
            text_w = row.right - text_x - buttons_w - 10
            draw_text(screen, widgets.clip_text(item.path.name, 14, text_w, bold=True), (text_x, row.y + 11),
                      14, theme.TEXT, bold=True)
            text, color = item.result()
            draw_text(screen, widgets.clip_text(text, 12, text_w), (text_x, row.y + 36), 12, color or accent)

            if self.running:
                continue
            if editable:
                edit = pygame.Rect(row.right - 88, row.y + 8, 48, 26)
                hovered = edit.collidepoint(mouse_pos)
                rounded_panel(screen, edit, theme.PANEL_LIGHT if hovered else theme.PANEL, radius=6,
                              border=accent if hovered else None)
                draw_text(screen, "編輯", edit.center, 12, accent if hovered or item.edit.active else theme.TEXT_DIM,
                          center=True)
                self.row_buttons.append(("edit", item, edit))
            if ordering:
                draw_text(screen, str(index + 1), (row.right - 116 - shift, row.y + 21), 13, accent, bold=True,
                          center=True)
                up = pygame.Rect(row.right - 96 - shift, row.y + 8, 26, 26)
                down = pygame.Rect(row.right - 66 - shift, row.y + 8, 26, 26)
                for button, symbol, enabled in ((up, "▲", index > 0), (down, "▼", index < len(items) - 1)):
                    hovered = button.collidepoint(mouse_pos) and enabled
                    rounded_panel(screen, button, theme.PANEL_LIGHT if hovered else theme.PANEL, radius=6)
                    draw_text(screen, symbol, button.center, 11, theme.TEXT if enabled else theme.TEXT_FAINT,
                              center=True)
                self.row_buttons.append(("up", item, up))
                self.row_buttons.append(("down", item, down))
            remove = pygame.Rect(row.right - 34, row.y + 8, 26, 26)
            hovered = remove.collidepoint(mouse_pos)
            rounded_panel(screen, remove, theme.DANGER if hovered else theme.PANEL, radius=6)
            cross = theme.BG_DEEP if hovered else theme.TEXT_DIM
            ax, ay = remove.center
            pygame.draw.line(screen, cross, (ax - 5, ay - 5), (ax + 5, ay + 5), 2)
            pygame.draw.line(screen, cross, (ax + 5, ay - 5), (ax - 5, ay + 5), 2)
            self.row_buttons.append(("remove", item, remove))
        screen.set_clip(None)
        view.draw(screen, mouse_pos)

    def draw_settings(self, rect, mouse_pos):
        screen = self.screen
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        draw_text(screen, "轉換設定", (rect.x + 18, rect.y + 14), 15, theme.TEXT, bold=True)
        pygame.draw.line(screen, theme.PANEL_EDGE, (rect.x + 12, rect.y + 44), (rect.right - 12, rect.y + 44))
        view = self.settings_view
        area = pygame.Rect(rect.x, rect.y + 45, rect.width, rect.height - 49)
        self.settings_area = area
        screen.set_clip(area)
        bottom = self._draw_setting_rows(rect.x + 18, area.y + 15 - view.scroll, rect.width - 36,
                                         rect.right, mouse_pos)
        screen.set_clip(None)
        view.layout(area, bottom + view.scroll - area.y + 8)
        view.draw(screen, mouse_pos)

    def _note(self, text, x, y, inner, color=theme.TEXT_FAINT):
        draw_text(self.screen, widgets.clip_text(text, 12, inner), (x, y), 12, color)
        return y + 20

    def _segment_row(self, title, control, notes, x, y, inner, mouse_pos, title_color):
        draw_text(self.screen, title, (x, y), 14, title_color)
        y += 22
        control.draw(self.screen, pygame.Rect(x, y, inner, 32), mouse_pos)
        self.controls.append(control)
        return self._note(notes[control.value], x, y + 38, inner) + 10

    def _toggle_row(self, title, hint, toggle, x, y, inner, right, mouse_pos, title_color):
        draw_text(self.screen, title, (x, y + 2), 14, title_color)
        draw_text(self.screen, widgets.clip_text(hint, 12, inner - 64), (x, y + 22), 12, theme.TEXT_FAINT)
        toggle.draw(self.screen, (right - 60, y + 6), mouse_pos)
        self.controls.append(toggle)
        return y + 48

    def _slider_row(self, title, value_text, slider, x, y, inner, right, mouse_pos, title_color):
        draw_text(self.screen, title, (x, y), 14, title_color)
        draw_text(self.screen, value_text, (right - 18, y + 7), 13, slider.accent, right=True)
        slider.draw(self.screen, pygame.Rect(x, y + 28, inner, 16), mouse_pos)
        self.sliders.append(slider)
        return y + 52

    def _draw_setting_rows(self, x, y, inner, right, mouse_pos):
        """設定欄的內容;依輸出格式只顯示用得到的選項。回傳內容底部的 y。"""
        screen = self.screen
        locked = self.running
        title_color = theme.TEXT_FAINT if locked else theme.TEXT
        self.controls, self.sliders = [], []
        fmt = self.fmt.value

        draw_text(screen, "輸出格式", (x, y), 14, title_color)
        y += 22
        y += self.fmt.draw(screen, pygame.Rect(x, y, inner, 0), mouse_pos) + 6
        self.controls.append(self.fmt)
        y = self._note(FORMAT_NOTES[fmt], x, y, inner)
        if fmt == "svg":
            y = self._note("照片轉出來會像色塊畫，檔案也可能比原圖大", x, y, inner, theme.WARN)
        animated = any(item.frames > 1 for item in self.items)
        if fmt == "pdf" and animated:
            y = self._note("動畫圖片只會放入第一格", x, y, inner, theme.WARN)
        y += 10

        if fmt == "pdf":
            y = self._segment_row("PDF 輸出", self.pdf_output, PDF_OUTPUT_NOTES, x, y, inner, mouse_pos, title_color)
            y = self._segment_row("頁面大小", self.pdf_page, PDF_PAGE_NOTES, x, y, inner, mouse_pos, title_color)
        if fmt == "svg":
            y = self._segment_row("描邊顏色", self.svg_color, SVG_NOTES, x, y, inner, mouse_pos, title_color)

        # 壓縮品質每個格式都在同一個位置,切換格式時面板不會跳動;無損格式用不到,顯示成灰色
        if fmt in QUALITY_FORMATS:
            y = self._toggle_row("壓縮品質", "關閉時保持原圖品質；開啟後可自己調整品質", self.compress, x, y, inner,
                                 right, mouse_pos, title_color)
            if self.compress.value:
                y = self._slider_row("品質", str(int(self.quality.value)), self.quality, x, y + 4, inner, right,
                                     mouse_pos, title_color)
                y = self._note(QUALITY_NOTES.get(fmt, "數字越小檔案越小；70~85 通常看不出差別"), x, y, inner)
                y = self._note("品質 100 不代表原圖品質", x, y, inner) + 10
        else:
            y = self._toggle_row("壓縮品質", "這個格式是無損的，不需要壓縮品質", self._unused_toggle, x, y, inner,
                                 right, mouse_pos, theme.TEXT_FAINT)
            self.controls.remove(self._unused_toggle)
        if fmt in ("keep", "png"):
            y = self._toggle_row("PNG 減少顏色", "最多保留 256 色，檔案通常小一半以上；漸層可能出現顆粒",
                                 self.reduce_colors, x, y, inner, right, mouse_pos, title_color)

        y = self._toggle_row("限制尺寸", "最長邊超過設定值時等比例縮小", self.limit, x, y, inner, right, mouse_pos,
                             title_color)
        if self.limit.value:
            y = self._slider_row("最長邊", f"{int(self.max_side.value)} px", self.max_side, x, y + 4, inner, right,
                                 mouse_pos, title_color)
            y = self._note("只縮小不放大；SVG 會直接畫成這個大小", x, y, inner) + 10
        if animated and fmt not in ("keep", "webp", "gif", "pdf"):
            y = self._toggle_row("動畫逐格拆開", "每一格存成一張圖，放在「檔名_frames」資料夾；關閉時只保留第一格",
                                 self.split_frames, x, y, inner, right, mouse_pos, title_color)

        pygame.draw.line(screen, theme.PANEL_EDGE, (x, y), (right - 18, y))
        y += 14
        y = self._toggle_row("移除拍攝資訊", "GPS 位置、拍攝時間、相機型號等", self.strip_meta, x, y, inner, right,
                             mouse_pos, title_color)
        y = self._toggle_row("自動轉正", "依照片記錄的方向，把橫躺的照片轉正", self.auto_rotate, x, y, inner, right,
                             mouse_pos, title_color)

        pygame.draw.line(screen, theme.PANEL_EDGE, (x, y), (right - 18, y))
        y += 14
        if locked:
            draw_text(screen, "處理中無法變更設定", (x, y), 12, theme.WARN)
            y += 22
        for line in ("不會覆蓋原檔，同名時自動加上編號", "全部在本地處理，不會上傳"):
            draw_text(screen, line, (x, y), 12, theme.TEXT_FAINT)
            y += 20
        return y

    def draw_footer(self, rect, mouse_pos):
        screen = self.screen
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        color = theme.TEXT_DIM
        if self.notice:
            summary, color = self.notice, theme.WARN
        elif self.running:
            finished = self.count("done") + self.count("error")
            summary = f"處理中 {finished} / {len(self.items)}"
        elif self.combined:
            out, size, pages = self.combined
            summary = f"已合成 {out.name}（{pages} 頁，{deps.human_size(size)}）"
            if self.count("error"):
                summary += f" · {self.count('error')} 張讀取失敗"
        elif self.count("done") or self.count("error"):
            done = [item for item in self.items if item.status == "done" and item.out_size]
            summary = f"完成 {self.count('done')} 張"
            if self.count("error"):
                summary += f" · 失敗 {self.count('error')} 張"
            if done:
                before = sum(item.size for item in done)
                after = sum(item.out_size for item in done)
                summary += f" · 合計 {deps.human_size(before)} → {deps.human_size(after)}"
        elif self.items:
            summary = f"共 {len(self.items)} 張，按「開始轉換」開始處理"
        else:
            summary = "拖入圖片後按「開始轉換」"
        text_w = rect.width - 268
        draw_text(screen, widgets.clip_text(summary, 13, text_w), (rect.x + 18, rect.y + 18), 13, color)
        draw_text(screen, "輸出位置：output\\images\\", (rect.x + 18, rect.y + 40), 12, theme.TEXT_FAINT)

        side = pygame.Rect(rect.right - 238, rect.y + 18, 104, 38)
        if self.running:
            self.btn_cancel.draw(screen, side, mouse_pos)
        else:
            self.btn_output.draw(screen, side, mouse_pos)
        self.btn_run.enabled = not self.running and bool(self.items)
        self.btn_run.label = "處理中..." if self.running else "開始轉換"
        self.btn_run.draw(screen, pygame.Rect(rect.right - 122, rect.y + 18, 104, 38), mouse_pos)

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, mouse_pos):
        if self.items and self.list_view.handle_event(event, mouse_pos):
            return
        if self.settings_view.handle_event(event, mouse_pos):
            return
        if event.type == pygame.DROPFILE:
            self.add_files([event.file])
            return

        # 設定欄捲動後,被捲到看不見的控制項不能被點到;處理中不能改設定
        editable = self.settings_area.collidepoint(mouse_pos) and not self.running
        for slider in self.sliders:
            if event.type == pygame.MOUSEBUTTONDOWN and not editable:
                continue
            if slider.handle(event, mouse_pos):
                return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return

        if self.list_area.collidepoint(mouse_pos) and not self.running:
            for action, item, rect in self.row_buttons:
                if not rect.collidepoint(mouse_pos):
                    continue
                if action == "edit":
                    self.open_editor(item)
                    return
                with self._lock:
                    if item not in self.items:
                        return
                    index = self.items.index(item)
                    if action == "remove":
                        self.items.remove(item)
                    elif action == "up" and index > 0:
                        self.items[index - 1], self.items[index] = item, self.items[index - 1]
                    elif action == "down" and index < len(self.items) - 1:
                        self.items[index + 1], self.items[index] = item, self.items[index + 1]
                return

        if self.items and not self.running and self.btn_clear.clicked(mouse_pos, True):
            with self._lock:
                self.items = []
            self.list_view.scroll = 0
            self.combined = None
            self.notice = ""
            return
        if editable:
            for control in self.controls:
                if control.clicked(mouse_pos, True):
                    return

        if self.running:
            if self.btn_cancel.clicked(mouse_pos, True):
                self.stop()
        elif self.btn_output.clicked(mouse_pos, True):
            folder = output_dir()
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)
        elif self.btn_run.clicked(mouse_pos, True):
            self.start()
