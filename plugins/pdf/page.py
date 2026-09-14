"""PDF 工具的畫面:壓縮 / 拆分 / 合併三個分頁。"""

import math
import os
import threading
from pathlib import Path

import pygame

from core import paths, tasks, theme, widgets
from core.plugins import Page
from core.scroll import BAR_SPACE, ScrollView
from core.widgets import (Button, ProgressBar, SegmentedControl, Slider, TextInput, Toggle,
                          draw_text, rounded_panel)

from . import ops
from .thumbs import ThumbnailCache

TABS = [("compress", "壓縮"), ("split", "拆分"), ("merge", "合併")]
FILE_ROW_H = 58
PICK_VISIBLE_FILES = 3
TILE_W, THUMB_H, LABEL_H, TILE_GAP = 96, 118, 22, 10
TILE_H = 6 + THUMB_H + LABEL_H
LOADING_PAGES = "頁數還在讀取中"


class FileItem:
    def __init__(self, path: Path):
        self.path = path
        self.size = path.stat().st_size if path.exists() else 0
        self.pages = None
        self.selected = set()
        self.spec_text = ""
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
        self.list_views = {key: ScrollView(accent=theme.TAB_COLORS[key]) for key, _ in TABS}
        self.list_area = pygame.Rect(0, 0, 0, 0)
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
        self.s_method = SegmentedControl([("ratio", "依比例拆分"), ("pick", "選取頁面")], accent=blue)
        self.s_parts = Slider(2, 8, 2, accent=blue, ticks=True)
        self.s_weights = [Slider(5, 95, 50, accent=blue) for _ in range(8)]
        self.s_output = SegmentedControl([("each", "每頁一個檔"), ("combine", "合成一個檔")], accent=blue)
        self.s_format = SegmentedControl([("pdf", "PDF"), ("png", "PNG"), ("jpg", "JPG")], accent=blue)
        self.s_dpi = Slider(72, 300, 150, step=6, accent=blue)
        self.page_input = TextInput(placeholder="例如 1, 3, 5-8", accent=blue)
        self.page_error = ""
        self.btn_select_all = Button("全選", accent=blue, filled=False, size=13)
        self.btn_select_none = Button("清除", accent=blue, filled=False, size=13)
        self.split_index = 0
        self.page_cells = []
        self.grid_view = ScrollView(accent=blue, wheel_step=TILE_H + TILE_GAP, marquee=self)
        self.grid_left = 0
        self.grid_columns = 1
        self.grid_rows = 0
        self.thumbs = ThumbnailCache((TILE_W - 12, THUMB_H))

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

    @property
    def picking(self):
        return self.tab == "split" and self.s_method.value == "pick"

    @property
    def split_item(self):
        files = self.files["split"]
        if not files:
            return None
        self.split_index = max(0, min(self.split_index, len(files) - 1))
        return files[self.split_index]

    @property
    def grid_rect(self):
        return self.grid_view.rect

    def add_files(self, raw_paths):
        was_empty = not self.current_files
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
            if self.tab == "split" and was_empty:
                self.load_spec_into_input()

    def can_run(self):
        if not self.current_files:
            return False
        if self.picking:
            return any(item.selected for item in self.current_files)
        return True

    def split_weights(self):
        count = int(self.s_parts.value)
        return [float(self.s_weights[i].value) for i in range(count)]

    def split_preview(self):
        item = self.split_item
        if item is None or item.pages is None or item.pages < 0:
            return None, []
        return item.pages, ops.plan_split(item.pages, self.split_weights())

    def deactivate(self):
        self.page_input.blur()
        self.grid_view.reset()
        for view in self.list_views.values():
            view.reset()

    # ------------------------------------------------------------ 選頁

    def select_split_file(self, index):
        self.split_index = index
        self.grid_view.scroll = 0
        self.load_spec_into_input()

    def load_spec_into_input(self):
        item = self.split_item
        self.grid_view.reset()
        self.page_input.blur()
        self.page_input.set_text(item.spec_text if item else "")
        self.page_input.error = False
        self.page_error = ""

    def apply_typed_spec(self):
        item = self.split_item
        if item is None:
            return
        text = self.page_input.text
        item.spec_text = text
        self.page_error = ""
        if not text.strip():
            item.selected = set()
        elif item.pages is None or item.pages < 0:
            self.page_error = LOADING_PAGES if item.pages is None else "這份檔案無法讀取"
        else:
            try:
                item.selected = set(ops.parse_page_spec(text, item.pages))
            except ValueError as exc:
                self.page_error = str(exc)
        self.page_input.error = bool(self.page_error)

    def set_selection(self, pages):
        item = self.split_item
        item.selected = set(pages)
        item.spec_text = ops.format_page_spec(item.selected)
        self.page_input.set_text(item.spec_text)
        self.page_input.error = False
        self.page_error = ""

    def pages_in_band(self, corner_a, corner_b):
        """框選範圍(內容座標)碰到的頁。"""
        x1, x2 = sorted((corner_a[0], corner_b[0]))
        y1, y2 = sorted((corner_a[1], corner_b[1]))
        step_x, step_y = TILE_W + TILE_GAP, TILE_H + TILE_GAP
        total = self.split_item.pages
        pages = set()
        for row in range(max(0, int(y1 // step_y)), min(self.grid_rows - 1, int(y2 // step_y)) + 1):
            top = row * step_y
            if y2 < top or y1 > top + TILE_H:
                continue
            for col in range(self.grid_columns):
                left = self.grid_left + col * step_x
                number = row * self.grid_columns + col + 1
                if number <= total and not (x2 < left or x1 > left + TILE_W):
                    pages.add(number)
        return pages

    # 以下三個給 ScrollView 的框選呼叫

    def marquee_begin(self, content_pos):
        page = next(iter(self.pages_in_band(content_pos, content_pos)), None)
        selected = self.split_item.selected
        # 從已勾選的頁開始拖就是取消,否則是新增
        return {"page": page, "mode": "remove" if page in selected else "add", "base": set(selected)}

    def marquee_update(self, state, corner_a, corner_b):
        band = self.pages_in_band(corner_a, corner_b)
        base = state["base"]
        self.set_selection(base | band if state["mode"] == "add" else base - band)

    def marquee_click(self, state):
        if state["page"] is not None:
            self.set_selection(self.split_item.selected ^ {state["page"]})

    # ------------------------------------------------------------ 任務

    def start_job(self):
        if self.runner.running or not self.can_run():
            return
        output_dir = paths.OUTPUT_DIR
        output_dir.mkdir(exist_ok=True)
        self.result_lines = []
        self.page_input.blur()

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
                        runner.log(f"{item.path.name}：失敗 - {exc}")

        elif self.tab == "split" and not self.picking:
            items = list(self.current_files)
            weights = self.split_weights()

            def job(runner):
                for index, item in enumerate(items, start=1):
                    if runner.cancel_event.is_set():
                        break

                    def progress(done, total, msg, i=index, name=item.path.name):
                        runner.report(done, max(1, total), f"[{i}/{len(items)}] {name} · {msg}")

                    try:
                        info = ops.split(item.path, output_dir, weights=weights, progress=progress,
                                         cancel=runner.cancel_event)
                        runner.log(f"{item.path.name}：拆成 {info['count']} 份")
                    except ops.Cancelled:
                        runner.log("已取消")
                        break
                    except Exception as exc:
                        runner.log(f"{item.path.name}：失敗 - {exc}")

        elif self.tab == "split":
            jobs = [(item.path, sorted(item.selected)) for item in self.current_files if item.selected]
            combine = self.s_output.value == "combine"
            fmt = "pdf" if combine else self.s_format.value
            dpi = int(self.s_dpi.value)

            def job(runner):
                for index, (path, pages) in enumerate(jobs, start=1):
                    if runner.cancel_event.is_set():
                        break

                    def progress(done, total, msg, i=index, name=path.name):
                        runner.report(done, max(1, total), f"[{i}/{len(jobs)}] {name} · {msg}")

                    try:
                        info = ops.extract_pages(path, output_dir, pages, combine=combine, fmt=fmt,
                                                 image_dpi=dpi, progress=progress,
                                                 cancel=runner.cancel_event)
                        runner.log(f"{path.name}：取出 {len(pages)} 頁，產生 {info['count']} 個檔案")
                    except ops.Cancelled:
                        runner.log("已取消")
                        break
                    except Exception as exc:
                        runner.log(f"{path.name}：失敗 - {exc}")

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
                    runner.log(f"合併 {len(items)} 個檔案，共 {info['pages']} 頁")
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

        # 頁數讀完之前就先打了頁碼的話,讀完後自動重新檢查一次
        item = self.split_item
        if self.page_error == LOADING_PAGES and item is not None and item.pages is not None:
            self.apply_typed_spec()

        mouse = pygame.mouse.get_pos()
        for key, view in self.list_views.items():
            if key == self.tab:
                view.update(mouse)
            else:
                view.reset()
        if self.picking and item is not None:
            self.grid_view.update(mouse)
        else:
            self.grid_view.reset()

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
        self.page_cells = []
        self.grid_view.clear()

        if not files:
            self.list_views[self.tab].clear()
            self.list_area = pygame.Rect(0, 0, 0, 0)
            icon_y = body.centery - 30
            pygame.draw.rect(self.screen, theme.PANEL_EDGE, (body.centerx - 26, icon_y, 52, 64), 2,
                             border_radius=6)
            draw_text(self.screen, "PDF", (body.centerx, icon_y + 32), 14, theme.TEXT_FAINT, center=True)
            draw_text(self.screen, "把 PDF 檔案拖曳到這個視窗", (body.centerx, body.centery + 52),
                      16, theme.TEXT_DIM, center=True)
            draw_text(self.screen, "支援一次拖多個檔案", (body.centerx, body.centery + 76),
                      13, theme.TEXT_FAINT, center=True)
            return

        if self.picking:
            rows_h = min(len(files), PICK_VISIBLE_FILES) * FILE_ROW_H + 16
            area = pygame.Rect(body.x, body.y, body.width, rows_h)
            self._draw_file_rows(area, mouse_pos)
            self.draw_page_grid(pygame.Rect(body.x, area.bottom, body.width, body.bottom - area.bottom),
                                mouse_pos)
        else:
            self._draw_file_rows(body, mouse_pos)

    def _draw_file_rows(self, area, mouse_pos):
        files = self.current_files
        view = self.list_views[self.tab]
        view.layout(area, len(files) * FILE_ROW_H + 16)
        self.list_area = area

        self.screen.set_clip(area)
        for index, item in enumerate(files):
            y = area.y + 8 + index * FILE_ROW_H - view.scroll
            if y + FILE_ROW_H < area.y or y > area.bottom:
                continue
            row = pygame.Rect(area.x + 10, y, area.width - 10 - BAR_SPACE, FILE_ROW_H - 8)
            hover = row.collidepoint(mouse_pos) and area.collidepoint(mouse_pos)
            is_current = self.tab == "split" and index == self.split_index
            if is_current:
                rounded_panel(self.screen, row, tuple(int(c * 0.22) for c in self.accent), radius=8,
                              border=self.accent)
            else:
                rounded_panel(self.screen, row, theme.PANEL_LIGHT if hover else theme.BG_DEEP, radius=8,
                              alpha=200)

            pygame.draw.rect(self.screen, self.accent, (row.x + 10, row.y + 14, 3, 22), border_radius=2)
            name = widgets.clip_text(item.path.name, 15, row.width - 180)
            draw_text(self.screen, name, (row.x + 22, row.y + 8), 15, theme.TEXT)
            detail = f"{item.page_label} · {ops.human_size(item.size)}"
            if self.picking:
                detail += f" · 已選 {len(item.selected)} 頁"
            draw_text(self.screen, detail, (row.x + 22, row.y + 28), 12,
                      self.accent if self.picking and item.selected else theme.TEXT_DIM)

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
            if self.tab == "split":
                self.row_buttons.append(("select", index, row))
        self.screen.set_clip(None)

        view.draw(self.screen, mouse_pos)

    def draw_page_grid(self, rect, mouse_pos):
        item = self.split_item
        pygame.draw.line(self.screen, theme.PANEL_EDGE, (rect.x + 12, rect.y), (rect.right - 12, rect.y))
        draw_text(self.screen, "勾選頁面", (rect.x + 16, rect.y + 12), 14, theme.TEXT, bold=True)

        if item.pages is None or item.pages < 0:
            draw_text(self.screen, "讀取頁數中..." if item.pages is None else "這份檔案無法讀取",
                      rect.center, 14, theme.TEXT_DIM, center=True)
            return
        draw_text(self.screen, f"已選 {len(item.selected)} / {item.pages} 頁", (rect.right - 16, rect.y + 21),
                  12, self.accent, right=True)

        grid = pygame.Rect(rect.x + 12, rect.y + 42, rect.width - 24, rect.height - 50)
        usable_w = grid.width - BAR_SPACE
        columns = max(1, (usable_w + TILE_GAP) // (TILE_W + TILE_GAP))
        step_y = TILE_H + TILE_GAP
        rows = math.ceil(item.pages / columns)
        self.grid_view.layout(grid, rows * step_y - TILE_GAP)
        scroll = self.grid_view.scroll
        used_w = columns * (TILE_W + TILE_GAP) - TILE_GAP
        left = grid.x + (usable_w - used_w) // 2
        self.grid_left, self.grid_columns, self.grid_rows = left, columns, rows

        first_row = scroll // step_y
        last_row = min(rows, (scroll + grid.height) // step_y + 1)
        visible = []
        self.screen.set_clip(grid)
        for r in range(first_row, last_row):
            y = grid.y + r * step_y - scroll
            for c in range(columns):
                number = r * columns + c + 1
                if number > item.pages:
                    break
                tile = pygame.Rect(left + c * (TILE_W + TILE_GAP), y, TILE_W, TILE_H)
                self._draw_tile(tile, item, number, mouse_pos, grid)
                self.page_cells.append((number, tile))
                visible.append(number)
        self.screen.set_clip(None)
        self.thumbs.want(item.path, visible)

        self.grid_view.draw(self.screen, mouse_pos)

    def _draw_tile(self, tile, item, number, mouse_pos, grid):
        chosen = number in item.selected
        hover = tile.collidepoint(mouse_pos) and grid.collidepoint(mouse_pos)
        rounded_panel(self.screen, tile, theme.PANEL_LIGHT if hover else theme.BG_DEEP, radius=8,
                      border=theme.TEXT_FAINT if hover else theme.PANEL_EDGE)

        box = pygame.Rect(tile.x + 6, tile.y + 6, TILE_W - 12, THUMB_H)
        thumb = self.thumbs.get(item.path, number)
        if thumb:
            self.screen.blit(thumb, thumb.get_rect(center=box.center))
        else:
            pygame.draw.rect(self.screen, theme.PANEL, box, border_radius=4)
            draw_text(self.screen, "...", box.center, 12, theme.TEXT_FAINT, center=True)

        if chosen:
            pygame.draw.rect(self.screen, self.accent, tile, 2, border_radius=8)
            bx, by = tile.right - 12, tile.y + 12
            pygame.draw.circle(self.screen, self.accent, (bx, by), 9)
            pygame.draw.lines(self.screen, theme.BG_DEEP, False,
                              [(bx - 4, by), (bx - 1, by + 3), (bx + 4, by - 3)], 2)
        draw_text(self.screen, str(number), (tile.centerx, tile.bottom - LABEL_H // 2 - 2), 12,
                  self.accent if chosen else theme.TEXT_DIM, bold=chosen, center=True)

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
        note = {"lossless": "只重新打包，畫質完全不變",
                "jpeg": "DCT 傅立葉轉換，適合彩色圖片",
                "jpeg2000": "小波轉換，比 JPEG 再小約 20%，但較慢",
                "grayscale": "轉為灰階，彩圖會變黑白",
                "bw": "CCITT G4 · 黑白掃描文件專用"}[mode]
        draw_text(self.screen, note, (rect.x + 18, y), 12,
                  theme.DANGER if mode == "bw" else theme.TEXT_FAINT)
        y += 20
        if mode == "bw":
            draw_text(self.screen, "非黑白的圖會自動跳過，不會被破壞", (rect.x + 18, y), 12, theme.DANGER)
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
        y = self.option_row(rect, y, "拆分方式")
        self.s_method.draw(self.screen, pygame.Rect(rect.x + 18, y, rect.width - 36, 32), mouse_pos)
        y += 44
        if self.picking:
            self.draw_pick_options(rect, y, mouse_pos)
        else:
            self.draw_ratio_options(rect, y, mouse_pos)

    def draw_ratio_options(self, rect, y, mouse_pos):
        inner = rect.width - 36
        total, ranges = self.split_preview()

        count = int(self.s_parts.value)
        draw_text(self.screen, "拆成幾份", (rect.x + 18, y), 14, theme.TEXT)
        draw_text(self.screen, f"{count} 份", (rect.right - 18, y + 7), 13, self.accent, right=True)
        y += 24
        self.s_parts.draw(self.screen, pygame.Rect(rect.x + 18, y + 4, inner, 16), mouse_pos)
        y += 34

        draw_text(self.screen, "各份比例", (rect.x + 18, y), 14, theme.TEXT)
        if total is None:
            hint = "拖入檔案後顯示頁數"
        elif len(self.current_files) > 1:
            hint = f"預覽目前選取的檔案 · 共 {total} 頁"
        else:
            hint = f"共 {total} 頁"
        draw_text(self.screen, hint, (rect.right - 18, y + 7), 12, theme.TEXT_DIM, right=True)
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
            y += 22

        if ranges:
            y += 6
            preview = " | ".join(f"{s + 1}-{s + c}" for s, c in ranges[:4])
            if len(ranges) > 4:
                preview += " ..."
            draw_text(self.screen, f"頁碼範圍  {preview}", (rect.x + 18, y), 12, theme.TEXT_FAINT)

    def draw_pick_options(self, rect, y, mouse_pos):
        inner = rect.width - 36
        x = rect.x + 18
        item = self.split_item
        loaded = item is not None and item.pages is not None and item.pages > 0

        draw_text(self.screen, "頁碼", (x, y), 14, theme.TEXT)
        if loaded:
            draw_text(self.screen, f"已選 {len(item.selected)} / {item.pages} 頁", (rect.right - 18, y + 7),
                      13, self.accent, right=True)
        y += 24
        self.page_input.draw(self.screen, pygame.Rect(x, y, inner, 36), mouse_pos)
        y += 42
        if self.page_error:
            draw_text(self.screen, widgets.clip_text(self.page_error, 12, inner), (x, y), 12, theme.DANGER)
        else:
            draw_text(self.screen, "用逗號分隔，連續的頁用 - 連接", (x, y), 12, theme.TEXT_FAINT)
        y += 24

        half = (inner - 10) // 2
        self.btn_select_all.enabled = loaded
        self.btn_select_none.enabled = item is not None and bool(item.selected)
        self.btn_select_all.draw(self.screen, pygame.Rect(x, y, half, 30), mouse_pos)
        self.btn_select_none.draw(self.screen, pygame.Rect(x + half + 10, y, half, 30), mouse_pos)
        y += 44
        pygame.draw.line(self.screen, theme.PANEL_EDGE, (x, y), (rect.right - 18, y))
        y += 14

        y = self.option_row(rect, y, "輸出方式")
        self.s_output.draw(self.screen, pygame.Rect(x, y, inner, 32), mouse_pos)
        y += 42
        each = self.s_output.value == "each"
        if each:
            y = self.option_row(rect, y, "輸出格式")
            self.s_format.draw(self.screen, pygame.Rect(x, y, inner, 32), mouse_pos)
            y += 42
            note = "建議 PDF：保留文字與向量，檔案更小" if self.s_format.value == "pdf" \
                else "圖片會失去文字，且通常比 PDF 更大"
            draw_text(self.screen, note, (x, y), 12, theme.TEXT_FAINT)
            y += 22
            if self.s_format.value in ("png", "jpg"):
                draw_text(self.screen, "解析度", (x, y), 14, theme.TEXT)
                draw_text(self.screen, f"{int(self.s_dpi.value)} dpi", (rect.right - 18, y + 7), 13,
                          self.accent, right=True)
                y += 24
                self.s_dpi.draw(self.screen, pygame.Rect(x, y + 4, inner, 16), mouse_pos)
                y += 30
        else:
            draw_text(self.screen, "勾選的頁依原本順序合成一個 PDF", (x, y), 12, theme.TEXT_FAINT)
            y += 22

        if item is None:
            return
        y += 8
        chosen_files = [f for f in self.current_files if f.selected]
        this_count = len(item.selected) if each else int(bool(item.selected))
        draw_text(self.screen, f"目前檔案將產生 {this_count} 個檔案", (x, y), 13, self.accent, bold=True)
        if len(self.current_files) > 1:
            all_count = sum(len(f.selected) for f in chosen_files) if each else len(chosen_files)
            draw_text(self.screen, f"有勾選的 {len(chosen_files)} 份檔案共產生 {all_count} 個檔案",
                      (x, y + 22), 12, theme.TEXT_DIM)

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
                draw_text(self.screen, f"錯誤：{error}", (bar.x, bar.y + 2), 13, theme.DANGER)
            elif shown:
                for i, line in enumerate(shown[:3]):
                    draw_text(self.screen, widgets.clip_text(line, 13, bar.width), (bar.x, bar.y + i * 19), 13,
                              theme.TEXT if i == 0 else theme.TEXT_DIM)
            else:
                status = self.status
                if self.picking and self.current_files and not self.can_run():
                    status = "勾選或輸入要取出的頁面"
                draw_text(self.screen, status, (bar.x, bar.y + 2), 13, theme.TEXT_DIM)
                draw_text(self.screen, "輸出位置：output\\", (bar.x, bar.y + 22), 12, theme.TEXT_FAINT)

        side = pygame.Rect(rect.right - 238, rect.y + 18, 104, 38)
        if running:
            self.btn_cancel.draw(self.screen, side, mouse_pos)
        else:
            self.btn_output.draw(self.screen, side, mouse_pos)

        self.btn_run.accent = self.accent
        self.btn_run.enabled = self.can_run() and not running
        self.btn_run.label = "處理中..." if running else "開始執行"
        self.btn_run.draw(self.screen, pygame.Rect(rect.right - 122, rect.y + 18, 104, 38), mouse_pos)

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, mouse_pos):
        if self.picking and self.split_item is not None:
            if self.page_input.handle(event, mouse_pos):
                self.apply_typed_spec()
            if self.grid_view.rect.width and self.grid_view.handle_event(event, mouse_pos):
                return
        if self.current_files and self.list_views[self.tab].handle_event(event, mouse_pos):
            return

        if event.type == pygame.DROPFILE:
            self.add_files([event.file])

        for slider in self.active_sliders():
            slider.handle(event, mouse_pos)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.handle_click(mouse_pos)

    def active_sliders(self):
        if self.tab == "compress":
            return [self.c_quality, self.c_maxdim]
        if self.tab == "split":
            if not self.picking:
                return [self.s_parts] + self.s_weights[:int(self.s_parts.value)]
            if self.s_output.value == "each" and self.s_format.value in ("png", "jpg"):
                return [self.s_dpi]
            return []
        return [self.m_quality] if self.m_compress.value else []

    def handle_click(self, mouse_pos):
        for key, rect in self.tab_rects:
            if rect.collidepoint(mouse_pos):
                self.deactivate()
                self.tab = key
                self.result_lines = []
                if key == "split":
                    self.load_spec_into_input()
                return

        if self.list_area.collidepoint(mouse_pos):
            for action, index, rect in self.row_buttons:
                if not rect.collidepoint(mouse_pos):
                    continue
                files = self.current_files
                if action == "remove":
                    files.pop(index)
                    if self.tab == "split":
                        if index < self.split_index:
                            self.split_index -= 1
                        self.load_spec_into_input()
                elif action == "up" and index > 0:
                    files[index - 1], files[index] = files[index], files[index - 1]
                elif action == "down" and index < len(files) - 1:
                    files[index + 1], files[index] = files[index], files[index + 1]
                elif action == "select":
                    self.select_split_file(index)
                return

        if self.current_files and self.btn_clear.clicked(mouse_pos, True):
            self.files[self.tab] = []
            self.list_views[self.tab].scroll = 0
            if self.tab == "split":
                self.split_index = 0
                self.grid_view.scroll = 0
                self.load_spec_into_input()
            return

        if self.tab == "compress":
            self.c_mode.clicked(mouse_pos, True)
            self.c_bookmarks.clicked(mouse_pos, True)
            self.c_links.clicked(mouse_pos, True)
        elif self.tab == "split":
            if self.s_method.clicked(mouse_pos, True):
                self.load_spec_into_input()
                return
            if self.picking:
                item = self.split_item
                if self.btn_select_all.clicked(mouse_pos, True):
                    self.set_selection(range(1, item.pages + 1))
                    return
                if self.btn_select_none.clicked(mouse_pos, True):
                    self.set_selection([])
                    return
                if self.s_output.clicked(mouse_pos, True):
                    return
                if self.s_output.value == "each":
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
