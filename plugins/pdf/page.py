"""PDF 工具的畫面:壓縮 / 拆分 / 合併三個分頁。"""

import os
import threading
from pathlib import Path

import pygame

from core import paths, tasks, theme, widgets
from core.plugins import Page
from core.widgets import Button, ProgressBar, SegmentedControl, Slider, Toggle, draw_text, rounded_panel

from . import ops

TABS = [("compress", "壓縮"), ("split", "拆分"), ("merge", "合併")]


class FileItem:
    def __init__(self, path: Path):
        self.path = path
        self.size = path.stat().st_size if path.exists() else 0
        self.pages = None
        threading.Thread(target=self._load_pages, daemon=True).start()

    def _load_pages(self):
        try:
            self.pages = ops.page_count(self.path)
        except Exception:
            self.pages = -1

    @property
    def page_label(self):
        if self.pages is None:
            return "讀取中..."
        if self.pages < 0:
            return "無法讀取"
        return f"{self.pages} 頁"


class PdfPage(Page):
    def __init__(self, app, tool):
        super().__init__(app, tool)
        self.tab = "compress"
        self.files = {"compress": [], "split": [], "merge": []}
        self.scroll = {"compress": 0, "split": 0, "merge": 0}
        self.tab_rects = []

        self.runner = tasks.TaskRunner()
        self.status = "把 PDF 拖進視窗即可開始"
        self.result_lines = []

        green = theme.TAB_COLORS["compress"]
        self.c_mode = SegmentedControl(
            [("lossless", "無損"), ("jpeg", "JPEG"), ("jpeg2000", "JP2000"),
             ("grayscale", "灰階"), ("bw", "黑白")],
            index=1, accent=green)
        self.c_quality = Slider(10, 95, 70, accent=green)
        self.c_maxdim = Slider(400, 4000, 2000, step=100, accent=green)
        self.c_bookmarks = Toggle(True, accent=green)
        self.c_links = Toggle(True, accent=green)

        blue = theme.TAB_COLORS["split"]
        self.s_parts = Slider(2, 8, 2, accent=blue, ticks=True)
        self.s_weights = [Slider(5, 95, 50, accent=blue) for _ in range(8)]
        self.s_everypage = Toggle(False, accent=blue)
        self.s_format = SegmentedControl(
            [("pdf", "PDF"), ("png", "PNG"), ("jpg", "JPG")], index=0, accent=blue)
        self.s_dpi = Slider(72, 300, 150, step=6, accent=blue)

        purple = theme.TAB_COLORS["merge"]
        self.m_compress = Toggle(False, accent=purple)
        self.m_quality = Slider(10, 95, 70, accent=purple)

        self.btn_run = Button("開始執行", accent=theme.ACCENT)
        self.btn_cancel = Button("取消", accent=theme.DANGER, filled=False)
        self.btn_clear = Button("清空清單", filled=False, size=14)
        self.btn_output = Button("輸出資料夾", filled=False, size=14)
        self.row_buttons = []

    # ------------------------------------------------------------ 資料

    @property
    def accent(self):
        return theme.TAB_COLORS[self.tab]

    @property
    def current_files(self):
        return self.files[self.tab]

    def add_files(self, raw_paths):
        existing = {item.path for item in self.current_files}
        added = 0
        for raw in raw_paths:
            path = Path(raw)
            if path.suffix.lower() != ".pdf" or not path.is_file() or path in existing:
                continue
            self.current_files.append(FileItem(path))
            added += 1
        if added:
            self.status = f"已加入 {added} 個檔案"
            self.result_lines = []

    def split_weights(self):
        count = int(self.s_parts.value)
        return [float(self.s_weights[i].value) for i in range(count)]

    def split_preview(self):
        files = self.files["split"]
        if not files or files[0].pages is None or files[0].pages < 0:
            return None, []
        total = files[0].pages
        if self.s_everypage.value:
            return total, [(i, 1) for i in range(min(total, 3))]
        return total, ops.plan_split(total, self.split_weights())

    # ------------------------------------------------------------ 任務

    def start_job(self):
        if self.runner.running or not self.current_files:
            return
        output_dir = paths.OUTPUT_DIR
        output_dir.mkdir(exist_ok=True)
        self.result_lines = []

        if self.tab == "compress":
            items = list(self.current_files)
            mode = self.c_mode.value
            quality = int(self.c_quality.value)
            max_dim = int(self.c_maxdim.value)
            keep_bm = self.c_bookmarks.value
            keep_lk = self.c_links.value

            def job(runner):
                for index, item in enumerate(items, start=1):
                    if runner.cancel_event.is_set():
                        break
                    out = output_dir / f"{item.path.stem}_compressed.pdf"

                    def progress(done, total, msg, i=index, name=item.path.name):
                        runner.report(done, max(1, total), f"[{i}/{len(items)}] {name} · {msg}")

                    try:
                        info = ops.compress(item.path, out, mode=mode, quality=quality,
                                            max_dim=max_dim, keep_bookmarks=keep_bm,
                                            keep_links=keep_lk, progress=progress,
                                            cancel=runner.cancel_event)
                        note = ""
                        if info.get("unsuitable"):
                            note = f" · 跳過 {info['unsuitable']} 張非黑白圖"
                        runner.log(f"{item.path.name}: {ops.human_size(info['before'])}"
                                   f" -> {ops.human_size(info['after'])}"
                                   f" (省 {info['saved_ratio']:.1f}%){note}")
                    except ops.Cancelled:
                        runner.log("已取消")
                        break
                    except Exception as exc:
                        runner.log(f"{item.path.name}: 失敗 - {exc}")

        elif self.tab == "split":
            item = self.current_files[0]
            every = self.s_everypage.value
            fmt = self.s_format.value if every else "pdf"
            weights = self.split_weights()
            dpi = int(self.s_dpi.value)

            def job(runner):
                def progress(done, total, msg):
                    runner.report(done, max(1, total), msg)

                try:
                    info = ops.split(item.path, output_dir, weights=weights,
                                     every_page=every, fmt=fmt, image_dpi=dpi,
                                     progress=progress, cancel=runner.cancel_event)
                    runner.log(f"{item.path.name}: 產生 {info['count']} 個檔案")
                    for path in info["files"][:6]:
                        runner.log(f"  {path.name} ({ops.human_size(path.stat().st_size)})")
                    if info["count"] > 6:
                        runner.log(f"  ...另外還有 {info['count'] - 6} 個")
                except ops.Cancelled:
                    runner.log("已取消")
                except Exception as exc:
                    runner.log(f"失敗 - {exc}")

        else:
            items = list(self.current_files)
            do_compress = self.m_compress.value
            quality = int(self.m_quality.value)
            out = output_dir / f"{items[0].path.stem}_merged.pdf"

            def job(runner):
                def progress(done, total, msg):
                    runner.report(done, max(1, total), msg)

                try:
                    info = ops.merge([i.path for i in items], out,
                                     compress_after=do_compress, quality=quality,
                                     progress=progress, cancel=runner.cancel_event)
                    runner.log(f"合併 {len(items)} 個檔案,共 {info['pages']} 頁")
                    runner.log(f"{out.name}: {ops.human_size(info['after'])}")
                except ops.Cancelled:
                    runner.log("已取消")
                except Exception as exc:
                    runner.log(f"失敗 - {exc}")

        self.runner.start(job)
        self.status = "處理中..."

    def update(self):
        if self.runner.finished and not self.runner.running and not self.result_lines:
            _, _, _, lines, error, _ = self.runner.snapshot()
            if lines or error:
                self.result_lines = lines
                self.status = "完成" if not error else "發生錯誤"

    # ------------------------------------------------------------ 繪製

    def draw_toolbar(self, rect, mouse_pos):
        self.tab_rects = []
        x = rect.right
        for key, label in reversed(TABS):
            tab = pygame.Rect(x - 104, rect.y, 104, rect.height)
            x -= 112
            self.tab_rects.append((key, tab))
            active = key == self.tab
            color = theme.TAB_COLORS[key]
            if active:
                rounded_panel(self.screen, tab, tuple(int(c * 0.28) for c in color), radius=8)
                pygame.draw.rect(self.screen, color, (tab.x + 14, tab.bottom - 3, tab.width - 28, 3),
                                 border_radius=2)
            elif tab.collidepoint(mouse_pos):
                rounded_panel(self.screen, tab, theme.PANEL_LIGHT, radius=8)
            draw_text(self.screen, label, tab.center, 16, color if active else theme.TEXT_DIM,
                      bold=active, center=True)

    def draw(self, rect, mouse_pos):
        margin = 20
        content_y = rect.y + margin
        footer_h = 74
        content_h = rect.bottom - content_y - footer_h - margin * 2
        list_w = int((rect.width - margin * 3) * 0.54)

        self.draw_file_list(pygame.Rect(rect.x + margin, content_y, list_w, content_h), mouse_pos)
        self.draw_options(pygame.Rect(rect.x + margin * 2 + list_w, content_y,
                                      rect.width - list_w - margin * 3, content_h), mouse_pos)
        self.draw_footer(pygame.Rect(rect.x + margin, rect.bottom - footer_h - margin,
                                     rect.width - margin * 2, footer_h), mouse_pos)

    def draw_file_list(self, rect, mouse_pos):
        rounded_panel(self.screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        files = self.current_files

        head = pygame.Rect(rect.x, rect.y, rect.width, 44)
        draw_text(self.screen, f"檔案清單 ({len(files)})", (head.x + 16, head.y + 13), 15,
                  theme.TEXT, bold=True)
        if files:
            self.btn_clear.draw(self.screen, pygame.Rect(head.right - 96, head.y + 9, 80, 26), mouse_pos)
        pygame.draw.line(self.screen, theme.PANEL_EDGE, (rect.x + 12, rect.y + 44),
                         (rect.right - 12, rect.y + 44))

        body = pygame.Rect(rect.x, rect.y + 45, rect.width, rect.height - 45)
        self.row_buttons = []

        if not files:
            icon_y = body.centery - 30
            pygame.draw.rect(self.screen, theme.PANEL_EDGE, (body.centerx - 26, icon_y, 52, 64), 2,
                             border_radius=6)
            draw_text(self.screen, "PDF", (body.centerx, icon_y + 32), 14, theme.TEXT_FAINT, center=True)
            draw_text(self.screen, "把 PDF 檔案拖曳到這個視窗", (body.centerx, body.centery + 52),
                      16, theme.TEXT_DIM, center=True)
            draw_text(self.screen, "支援一次拖多個檔案", (body.centerx, body.centery + 76),
                      13, theme.TEXT_FAINT, center=True)
            return

        row_h = 58
        self.screen.set_clip(body)
        offset = self.scroll[self.tab]
        for index, item in enumerate(files):
            y = body.y + 8 + index * row_h - offset
            if y + row_h < body.y or y > body.bottom:
                continue
            row = pygame.Rect(body.x + 10, y, body.width - 20, row_h - 8)
            hover = row.collidepoint(mouse_pos) and body.collidepoint(mouse_pos)
            rounded_panel(self.screen, row, theme.PANEL_LIGHT if hover else theme.BG_DEEP, radius=8, alpha=200)

            pygame.draw.rect(self.screen, self.accent, (row.x + 10, row.y + 14, 3, 22), border_radius=2)
            name = widgets.clip_text(item.path.name, 15, row.width - 180)
            draw_text(self.screen, name, (row.x + 22, row.y + 8), 15, theme.TEXT)
            draw_text(self.screen, f"{item.page_label} · {ops.human_size(item.size)}",
                      (row.x + 22, row.y + 28), 12, theme.TEXT_DIM)

            if self.tab == "merge":
                draw_text(self.screen, str(index + 1), (row.right - 116, row.centery), 13,
                          self.accent, bold=True, center=True)
                up = pygame.Rect(row.right - 96, row.y + 8, 26, 26)
                down = pygame.Rect(row.right - 66, row.y + 8, 26, 26)
                for r, sym, enabled in ((up, "▲", index > 0), (down, "▼", index < len(files) - 1)):
                    hovered = r.collidepoint(mouse_pos) and enabled
                    rounded_panel(self.screen, r, theme.PANEL_LIGHT if hovered else theme.PANEL, radius=6)
                    draw_text(self.screen, sym, r.center, 11,
                              theme.TEXT if enabled else theme.TEXT_FAINT, center=True)
                self.row_buttons.append(("up", index, up))
                self.row_buttons.append(("down", index, down))

            remove = pygame.Rect(row.right - 34, row.y + 8, 26, 26)
            hovered = remove.collidepoint(mouse_pos)
            rounded_panel(self.screen, remove, theme.DANGER if hovered else theme.PANEL, radius=6)
            cross = theme.BG_DEEP if hovered else theme.TEXT_DIM
            cx, cy = remove.center
            pygame.draw.line(self.screen, cross, (cx - 5, cy - 5), (cx + 5, cy + 5), 2)
            pygame.draw.line(self.screen, cross, (cx + 5, cy - 5), (cx - 5, cy + 5), 2)
            self.row_buttons.append(("remove", index, remove))

        self.screen.set_clip(None)

        content_h = len(files) * row_h + 16
        if content_h > body.height:
            track = pygame.Rect(body.right - 7, body.y + 6, 4, body.height - 12)
            pygame.draw.rect(self.screen, theme.PANEL_LIGHT, track, border_radius=2)
            bar_h = max(30, int(track.height * body.height / content_h))
            max_scroll = content_h - body.height
            pos = (offset / max_scroll) * (track.height - bar_h) if max_scroll > 0 else 0
            pygame.draw.rect(self.screen, self.accent, (track.x, track.y + pos, 4, bar_h), border_radius=2)

    def option_row(self, rect, y, label, hint=None):
        draw_text(self.screen, label, (rect.x + 18, y), 14, theme.TEXT)
        if hint:
            draw_text(self.screen, hint, (rect.right - 18, y + 7), 13, self.accent, right=True)
        return y + 24

    def draw_options(self, rect, mouse_pos):
        rounded_panel(self.screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        draw_text(self.screen, {"compress": "壓縮設定", "split": "拆分設定", "merge": "合併設定"}[self.tab],
                  (rect.x + 18, rect.y + 14), 15, theme.TEXT, bold=True)
        pygame.draw.line(self.screen, theme.PANEL_EDGE, (rect.x + 12, rect.y + 44),
                         (rect.right - 12, rect.y + 44))
        y = rect.y + 60
        if self.tab == "compress":
            self.draw_compress_options(rect, y, mouse_pos)
        elif self.tab == "split":
            self.draw_split_options(rect, y, mouse_pos)
        else:
            self.draw_merge_options(rect, y, mouse_pos)

    def draw_compress_options(self, rect, y, mouse_pos):
        inner = rect.width - 36
        y = self.option_row(rect, y, "壓縮方式")
        self.c_mode.draw(self.screen, pygame.Rect(rect.x + 18, y, inner, 34), mouse_pos)
        y += 44

        mode = self.c_mode.value
        note = {"lossless": "只重新打包,畫質完全不變",
                "jpeg": "DCT 傅立葉轉換,適合彩色圖片",
                "jpeg2000": "小波轉換,比 JPEG 再小約 20%,但較慢",
                "grayscale": "轉為灰階,彩圖會變黑白",
                "bw": "CCITT G4 · 黑白掃描文件專用"}[mode]
        draw_text(self.screen, note, (rect.x + 18, y), 12,
                  theme.DANGER if mode == "bw" else theme.TEXT_FAINT)
        y += 20
        if mode == "bw":
            draw_text(self.screen, "非黑白的圖會自動跳過,不會被破壞", (rect.x + 18, y), 12, theme.DANGER)
            y += 20
        y += 6

        rows = ((self.c_quality, "影像品質", lambda v: f"{int(v)}", mode in ("lossless", "bw")),
                (self.c_maxdim, "解析度上限", lambda v: f"{int(v)} px", mode == "lossless"))
        for slider, label, fmt, disabled in rows:
            draw_text(self.screen, label, (rect.x + 18, y), 14, theme.TEXT_FAINT if disabled else theme.TEXT)
            draw_text(self.screen, "-" if disabled else fmt(slider.value), (rect.right - 18, y + 7), 13,
                      theme.TEXT_FAINT if disabled else self.accent, right=True)
            y += 24
            slider.draw(self.screen, pygame.Rect(rect.x + 18, y + 4, inner, 16), mouse_pos)
            y += 32

        pygame.draw.line(self.screen, theme.PANEL_EDGE, (rect.x + 18, y), (rect.right - 18, y))
        y += 16

        for toggle, label, hint in ((self.c_bookmarks, "保留書籤", "章節目錄"),
                                    (self.c_links, "保留導向", "內文超連結")):
            draw_text(self.screen, label, (rect.x + 18, y + 2), 14, theme.TEXT)
            draw_text(self.screen, hint, (rect.x + 18, y + 22), 12, theme.TEXT_FAINT)
            toggle.draw(self.screen, (rect.right - 60, y + 6), mouse_pos)
            y += 46

    def draw_split_options(self, rect, y, mouse_pos):
        inner = rect.width - 36
        total, ranges = self.split_preview()

        draw_text(self.screen, "全部拆開", (rect.x + 18, y + 2), 14, theme.TEXT)
        draw_text(self.screen, "每一頁存成一個檔案", (rect.x + 18, y + 22), 12, theme.TEXT_FAINT)
        self.s_everypage.draw(self.screen, (rect.right - 60, y + 6), mouse_pos)
        y += 48

        if self.s_everypage.value:
            y = self.option_row(rect, y, "輸出格式")
            self.s_format.draw(self.screen, pygame.Rect(rect.x + 18, y, inner, 32), mouse_pos)
            y += 42
            note = "建議 PDF:保留文字與向量,檔案更小" if self.s_format.value == "pdf" \
                else "圖片會失去文字,且通常比 PDF 更大"
            draw_text(self.screen, note, (rect.x + 18, y), 12, theme.TEXT_FAINT)
            y += 24
            if self.s_format.value in ("png", "jpg"):
                draw_text(self.screen, "解析度", (rect.x + 18, y), 14, theme.TEXT)
                draw_text(self.screen, f"{int(self.s_dpi.value)} dpi", (rect.right - 18, y + 7),
                          13, self.accent, right=True)
                y += 24
                self.s_dpi.draw(self.screen, pygame.Rect(rect.x + 18, y + 4, inner, 16), mouse_pos)
                y += 30
            if total:
                draw_text(self.screen, f"將產生 {total} 個檔案", (rect.x + 18, y + 6), 14, self.accent, bold=True)
            return

        count = int(self.s_parts.value)
        draw_text(self.screen, "拆成幾份", (rect.x + 18, y), 14, theme.TEXT)
        draw_text(self.screen, f"{count} 份", (rect.right - 18, y + 7), 13, self.accent, right=True)
        y += 24
        self.s_parts.draw(self.screen, pygame.Rect(rect.x + 18, y + 4, inner, 16), mouse_pos)
        y += 34

        draw_text(self.screen, "各份比例", (rect.x + 18, y), 14, theme.TEXT)
        if total is None:
            draw_text(self.screen, "拖入檔案後顯示頁數", (rect.right - 18, y + 7), 12,
                      theme.TEXT_FAINT, right=True)
        else:
            draw_text(self.screen, f"共 {total} 頁", (rect.right - 18, y + 7), 13, theme.TEXT_DIM, right=True)
        y += 26

        weights = self.split_weights()
        weight_sum = sum(weights) or 1
        for i in range(count):
            share = weights[i] / weight_sum * 100
            pages = f"{ranges[i][1]} 頁" if ranges and i < len(ranges) else "-"
            draw_text(self.screen, f"第 {i + 1} 份", (rect.x + 18, y), 13, theme.TEXT_DIM)
            draw_text(self.screen, f"{share:.0f}%   {pages}", (rect.right - 18, y + 6), 13,
                      self.accent, right=True, bold=True)
            y += 20
            self.s_weights[i].draw(self.screen, pygame.Rect(rect.x + 18, y + 4, inner, 14), mouse_pos)
            y += 26

        if ranges:
            y += 6
            preview = " | ".join(f"{s + 1}-{s + c}" for s, c in ranges[:4])
            if len(ranges) > 4:
                preview += " ..."
            draw_text(self.screen, f"頁碼範圍  {preview}", (rect.x + 18, y), 12, theme.TEXT_FAINT)

    def draw_merge_options(self, rect, y, mouse_pos):
        inner = rect.width - 36
        files = self.current_files
        draw_text(self.screen, "用左側清單的 ▲▼ 調整合併順序", (rect.x + 18, y), 13, theme.TEXT_DIM)
        y += 30

        total_pages = sum(f.pages for f in files if f.pages and f.pages > 0)
        total_size = sum(f.size for f in files)
        box = pygame.Rect(rect.x + 18, y, inner, 62)
        rounded_panel(self.screen, box, theme.BG_DEEP, radius=8, alpha=180)
        draw_text(self.screen, f"{len(files)}", (box.x + 24, box.y + 12), 20, self.accent, bold=True, center=True)
        draw_text(self.screen, "檔案", (box.x + 24, box.y + 38), 11, theme.TEXT_FAINT, center=True)
        draw_text(self.screen, f"{total_pages}", (box.centerx, box.y + 12), 20, self.accent, bold=True, center=True)
        draw_text(self.screen, "總頁數", (box.centerx, box.y + 38), 11, theme.TEXT_FAINT, center=True)
        draw_text(self.screen, ops.human_size(total_size), (box.right - 40, box.y + 12), 15,
                  self.accent, bold=True, center=True)
        draw_text(self.screen, "合計大小", (box.right - 40, box.y + 38), 11, theme.TEXT_FAINT, center=True)
        y += 78

        draw_text(self.screen, "合併後壓縮", (rect.x + 18, y + 2), 14, theme.TEXT)
        draw_text(self.screen, "用 JPEG 重新編碼圖片", (rect.x + 18, y + 22), 12, theme.TEXT_FAINT)
        self.m_compress.draw(self.screen, (rect.right - 60, y + 6), mouse_pos)
        y += 48

        if self.m_compress.value:
            draw_text(self.screen, "影像品質", (rect.x + 18, y), 14, theme.TEXT)
            draw_text(self.screen, f"{int(self.m_quality.value)}", (rect.right - 18, y + 7), 13,
                      self.accent, right=True)
            y += 24
            self.m_quality.draw(self.screen, pygame.Rect(rect.x + 18, y + 4, inner, 16), mouse_pos)

    def draw_footer(self, rect, mouse_pos):
        rounded_panel(self.screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        done, total, message, lines, error, _ = self.runner.snapshot()
        running = self.runner.running

        bar = pygame.Rect(rect.x + 18, rect.y + 16, rect.width - 268, 22)
        if running:
            ratio = done / total if total else 0
            ProgressBar(self.accent).draw(self.screen, bar, ratio, f"{int(ratio * 100)}%")
            draw_text(self.screen, widgets.clip_text(message, 12, bar.width), (bar.x, bar.bottom + 8), 12,
                      theme.TEXT_DIM)
        else:
            shown = self.result_lines or lines
            if error:
                draw_text(self.screen, f"錯誤: {error}", (bar.x, bar.y + 2), 13, theme.DANGER)
            elif shown:
                for i, line in enumerate(shown[:3]):
                    draw_text(self.screen, widgets.clip_text(line, 13, bar.width), (bar.x, bar.y + i * 19), 13,
                              theme.TEXT if i == 0 else theme.TEXT_DIM)
            else:
                draw_text(self.screen, self.status, (bar.x, bar.y + 2), 13, theme.TEXT_DIM)
                draw_text(self.screen, "輸出位置: output\\", (bar.x, bar.y + 22), 12, theme.TEXT_FAINT)

        side = pygame.Rect(rect.right - 238, rect.y + 18, 104, 38)
        if running:
            self.btn_cancel.draw(self.screen, side, mouse_pos)
        else:
            self.btn_output.draw(self.screen, side, mouse_pos)

        self.btn_run.accent = self.accent
        self.btn_run.enabled = bool(self.current_files) and not running
        self.btn_run.label = "處理中..." if running else "開始執行"
        self.btn_run.draw(self.screen, pygame.Rect(rect.right - 122, rect.y + 18, 104, 38), mouse_pos)

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, mouse_pos):
        if event.type == pygame.DROPFILE:
            self.add_files([event.file])
        elif event.type == pygame.MOUSEWHEEL:
            self.scroll[self.tab] = max(0, self.scroll[self.tab] - event.y * 40)

        for slider in self.active_sliders():
            slider.handle(event, mouse_pos)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.handle_click(mouse_pos)

    def active_sliders(self):
        if self.tab == "compress":
            return [self.c_quality, self.c_maxdim]
        if self.tab == "split":
            if self.s_everypage.value:
                return [self.s_dpi]
            return [self.s_parts] + self.s_weights[:int(self.s_parts.value)]
        return [self.m_quality] if self.m_compress.value else []

    def handle_click(self, mouse_pos):
        for key, rect in self.tab_rects:
            if rect.collidepoint(mouse_pos):
                self.tab = key
                self.result_lines = []
                return

        for action, index, rect in self.row_buttons:
            if not rect.collidepoint(mouse_pos):
                continue
            files = self.current_files
            if action == "remove":
                files.pop(index)
            elif action == "up" and index > 0:
                files[index - 1], files[index] = files[index], files[index - 1]
            elif action == "down" and index < len(files) - 1:
                files[index + 1], files[index] = files[index], files[index + 1]
            return

        if self.current_files and self.btn_clear.clicked(mouse_pos, True):
            self.files[self.tab] = []
            self.scroll[self.tab] = 0
            return

        if self.tab == "compress":
            self.c_mode.clicked(mouse_pos, True)
            self.c_bookmarks.clicked(mouse_pos, True)
            self.c_links.clicked(mouse_pos, True)
        elif self.tab == "split":
            self.s_everypage.clicked(mouse_pos, True)
            if self.s_everypage.value:
                self.s_format.clicked(mouse_pos, True)
        else:
            self.m_compress.clicked(mouse_pos, True)

        if self.runner.running:
            if self.btn_cancel.clicked(mouse_pos, True):
                self.runner.request_cancel()
                self.status = "取消中..."
        elif self.btn_output.clicked(mouse_pos, True):
            paths.OUTPUT_DIR.mkdir(exist_ok=True)
            os.startfile(paths.OUTPUT_DIR)
        elif self.btn_run.clicked(mouse_pos, True):
            self.start_job()
