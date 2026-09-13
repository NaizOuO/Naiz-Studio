#!/usr/bin/env python
"""PDF Studio - 壓縮 / 拆分 / 合併的圖形介面。"""

import math
import os
import platform
import sys
import threading
from pathlib import Path

if platform.system() == "Windows":
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import pygame

# 打包成 exe 後 __file__ 會指向暫時解壓目錄,設定檔與輸出必須放在 exe 旁邊才找得到
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
    sys.path.insert(0, str(BASE_DIR))

from modules import pdf_ops, tasks, theme, widgets
from modules.widgets import Button, ProgressBar, SegmentedControl, Slider, Toggle, draw_text, rounded_panel

OUTPUT_DIR = BASE_DIR / "output"
TABS = [("compress", "壓縮"), ("split", "拆分"), ("merge", "合併")]


class FileItem:
    def __init__(self, path: Path):
        self.path = path
        self.size = path.stat().st_size if path.exists() else 0
        self.pages = None
        threading.Thread(target=self._load_pages, daemon=True).start()

    def _load_pages(self):
        try:
            self.pages = pdf_ops.page_count(self.path)
        except Exception:
            self.pages = -1

    @property
    def page_label(self):
        if self.pages is None:
            return "讀取中..."
        if self.pages < 0:
            return "無法讀取"
        return f"{self.pages} 頁"


class App:
    def __init__(self):
        pygame.init()
        icon_path = BASE_DIR / "images" / "app_icon.png"
        if icon_path.exists():
            try:
                pygame.display.set_icon(pygame.image.load(str(icon_path)))
            except Exception:
                pass
        info = pygame.display.Info()
        width = min(1240, int(info.current_w * 0.82))
        height = min(780, int(info.current_h * 0.84))
        self.screen = pygame.display.set_mode((width, height), pygame.RESIZABLE)
        pygame.display.set_caption("PDF Studio")
        self.clock = pygame.time.Clock()

        self.config = theme.load_config(str(BASE_DIR))
        image = theme.load_background(str(BASE_DIR), self.config)
        self.background = theme.Background(image, self.config) if image else None

        self.windowed_size = self.screen.get_size()
        self.fullscreen = False

        self.gear_img = None
        gear_path = BASE_DIR / "images" / "ui_gear.png"
        if gear_path.exists():
            try:
                self.gear_img = pygame.transform.smoothscale(
                    pygame.image.load(str(gear_path)).convert_alpha(), (21, 21))
            except Exception:
                pass

        self.tab = "compress"
        self.files = {"compress": [], "split": [], "merge": []}
        self.scroll = {"compress": 0, "split": 0, "merge": 0}
        self.hover_paths = []

        self.runner = tasks.TaskRunner()
        self.status = "把 PDF 拖進視窗即可開始"
        self.result_lines = []

        self.c_mode = SegmentedControl(
            [("lossless", "無損"), ("jpeg", "JPEG"), ("jpeg2000", "JP2000"),
             ("grayscale", "灰階"), ("bw", "黑白")],
            index=1, accent=theme.TAB_COLORS["compress"])
        self.c_quality = Slider(10, 95, 70, accent=theme.TAB_COLORS["compress"])
        self.c_maxdim = Slider(400, 4000, 2000, step=100, accent=theme.TAB_COLORS["compress"])
        self.c_bookmarks = Toggle(True, accent=theme.TAB_COLORS["compress"])
        self.c_links = Toggle(True, accent=theme.TAB_COLORS["compress"])

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

        self.settings_open = False
        self.settings_dirty = False
        self.settings_saved_at = 0
        self.bg_files = []
        self.bg_index = 0
        self.bg_scroll = 0
        self.set_mode = SegmentedControl(
            [("cover", "覆蓋"), ("contain", "完整"), ("stretch", "延展"),
             ("center", "置中"), ("tile", "並排"), ("manual", "自由")],
            index=0, accent=theme.ACCENT)
        self.set_alpha = Slider(0, 255, 90, step=5, accent=theme.ACCENT)
        self.set_x = Slider(0, 100, 50, accent=theme.ACCENT)
        self.set_y = Slider(0, 100, 50, accent=theme.ACCENT)
        self.set_scale = Slider(10, 300, 100, step=5, accent=theme.ACCENT)
        self.btn_save = Button("儲存", accent=theme.ACCENT)
        self.btn_revert = Button("還原", filled=False, size=14)

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

    def add_files(self, paths):
        existing = {item.path for item in self.current_files}
        added = 0
        for raw in paths:
            path = Path(raw)
            if path.suffix.lower() != ".pdf" or not path.is_file() or path in existing:
                continue
            self.current_files.append(FileItem(path))
            added += 1
        if added:
            self.status = f"已加入 {added} 個檔案"
            self.result_lines = []

    # ------------------------------------------------------------ 設定面板

    def open_settings(self):
        self.bg_files = theme.list_images(str(BASE_DIR))
        current = self.config.get("bg_image", "")
        self.bg_index = self.bg_files.index(current) + 1 if current in self.bg_files else 0
        self.set_mode.index = next(
            (i for i, (key, _) in enumerate(self.set_mode.options)
             if key == self.config.get("bg_mode")), 0)
        self.set_alpha.value = int(self.config.get("bg_alpha", 90))
        self.set_x.value = int(self.config["bg_center"].get("x", 50))
        self.set_y.value = int(self.config["bg_center"].get("y", 50))
        if self.config.get("bg_mode") == "manual":
            self.set_x.value = int(self.config["bg_manual"].get("x", 50))
            self.set_y.value = int(self.config["bg_manual"].get("y", 50))
        self.set_scale.value = int(self.config["bg_manual"].get("scale", 100))
        self.bg_scroll = 0
        self.settings_dirty = False
        self.settings_open = True

    def apply_settings(self):
        """把面板上的值寫回 config,背景會在下一幀自動反映。"""
        chosen = "" if self.bg_index == 0 else self.bg_files[self.bg_index - 1]
        mode = self.set_mode.value
        changed = (chosen != self.config.get("bg_image")
                   or mode != self.config.get("bg_mode")
                   or int(self.set_alpha.value) != int(self.config.get("bg_alpha", 90)))

        if chosen != self.config.get("bg_image"):
            self.config["bg_image"] = chosen
            image = theme.load_background(str(BASE_DIR), self.config)
            self.background = theme.Background(image, self.config) if image else None

        self.config["bg_mode"] = mode
        self.config["bg_alpha"] = int(self.set_alpha.value)
        target = "bg_manual" if mode == "manual" else "bg_center"
        before = dict(self.config[target])
        self.config[target]["x"] = int(self.set_x.value)
        self.config[target]["y"] = int(self.set_y.value)
        if mode == "manual":
            self.config["bg_manual"]["scale"] = int(self.set_scale.value)
        if before != self.config[target] or changed:
            self.settings_dirty = True

    def save_settings(self):
        if theme.save_config(str(BASE_DIR), self.config):
            self.settings_dirty = False
            self.settings_saved_at = pygame.time.get_ticks()

    def revert_settings(self):
        self.config = theme.load_config(str(BASE_DIR))
        image = theme.load_background(str(BASE_DIR), self.config)
        self.background = theme.Background(image, self.config) if image else None
        self.open_settings()

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
        return total, pdf_ops.plan_split(total, self.split_weights())

    # ------------------------------------------------------------ 任務

    def start_job(self):
        if self.runner.running or not self.current_files:
            return
        OUTPUT_DIR.mkdir(exist_ok=True)
        self.result_lines = []
        tab = self.tab

        if tab == "compress":
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
                    out = OUTPUT_DIR / f"{item.path.stem}_compressed.pdf"

                    def progress(done, total, msg, i=index, name=item.path.name):
                        runner.report(done, max(1, total), f"[{i}/{len(items)}] {name} · {msg}")

                    try:
                        info = pdf_ops.compress(item.path, out, mode=mode, quality=quality,
                                                max_dim=max_dim, keep_bookmarks=keep_bm,
                                                keep_links=keep_lk, progress=progress,
                                                cancel=runner.cancel_event)
                        note = ""
                        if info.get("unsuitable"):
                            note = f" · 跳過 {info['unsuitable']} 張非黑白圖"
                        runner.log(f"{item.path.name}: {pdf_ops.human_size(info['before'])}"
                                   f" -> {pdf_ops.human_size(info['after'])}"
                                   f" (省 {info['saved_ratio']:.1f}%){note}")
                    except pdf_ops.Cancelled:
                        runner.log("已取消")
                        break
                    except Exception as exc:
                        runner.log(f"{item.path.name}: 失敗 - {exc}")

        elif tab == "split":
            item = self.current_files[0]
            every = self.s_everypage.value
            fmt = self.s_format.value if every else "pdf"
            weights = self.split_weights()
            dpi = int(self.s_dpi.value)

            def job(runner):
                def progress(done, total, msg):
                    runner.report(done, max(1, total), msg)

                try:
                    info = pdf_ops.split(item.path, OUTPUT_DIR, weights=weights,
                                         every_page=every, fmt=fmt, image_dpi=dpi,
                                         progress=progress, cancel=runner.cancel_event)
                    runner.log(f"{item.path.name}: 產生 {info['count']} 個檔案")
                    for path in info["files"][:6]:
                        runner.log(f"  {path.name} ({pdf_ops.human_size(path.stat().st_size)})")
                    if info["count"] > 6:
                        runner.log(f"  ...另外還有 {info['count'] - 6} 個")
                except pdf_ops.Cancelled:
                    runner.log("已取消")
                except Exception as exc:
                    runner.log(f"失敗 - {exc}")

        else:
            items = list(self.current_files)
            do_compress = self.m_compress.value
            quality = int(self.m_quality.value)
            out = OUTPUT_DIR / f"{items[0].path.stem}_merged.pdf"

            def job(runner):
                def progress(done, total, msg):
                    runner.report(done, max(1, total), msg)

                try:
                    info = pdf_ops.merge([i.path for i in items], out,
                                         compress_after=do_compress, quality=quality,
                                         progress=progress, cancel=runner.cancel_event)
                    runner.log(f"合併 {len(items)} 個檔案,共 {info['pages']} 頁")
                    runner.log(f"{out.name}: {pdf_ops.human_size(info['after'])}")
                except pdf_ops.Cancelled:
                    runner.log("已取消")
                except Exception as exc:
                    runner.log(f"失敗 - {exc}")

        self.runner.start(job)
        self.status = "處理中..."

    # ------------------------------------------------------------ 繪製

    def draw_background(self):
        self.screen.fill(theme.BG_DEEP)
        if self.background:
            self.background.draw(self.screen)

    def draw_header(self, width):
        rounded_panel(self.screen, pygame.Rect(0, 0, width, 66), theme.PANEL, radius=0, alpha=235)
        pygame.draw.line(self.screen, theme.PANEL_EDGE, (0, 66), (width, 66))

        pygame.draw.rect(self.screen, self.accent, (22, 22, 5, 22), border_radius=3)
        draw_text(self.screen, "PDF Studio", (38, 20), 21, theme.TEXT, bold=True)

        gear = pygame.Rect(width - 60, 20, 38, 26)
        self.gear_rect = gear
        hot = gear.collidepoint(pygame.mouse.get_pos()) or self.settings_open
        self._draw_gear(gear.center, theme.ACCENT if hot else theme.TEXT_DIM)

        self.tab_rects = []
        x = width - 76
        for key, label in reversed(TABS):
            rect = pygame.Rect(x - 104, 16, 104, 34)
            x -= 112
            self.tab_rects.append((key, rect))
            active = key == self.tab
            color = theme.TAB_COLORS[key]
            if active:
                rounded_panel(self.screen, rect, tuple(int(c * 0.28) for c in color), radius=8)
                pygame.draw.rect(self.screen, color, (rect.x + 14, rect.bottom - 3,
                                                      rect.width - 28, 3), border_radius=2)
            elif rect.collidepoint(pygame.mouse.get_pos()):
                rounded_panel(self.screen, rect, theme.PANEL_LIGHT, radius=8)
            draw_text(self.screen, label, rect.center, 16,
                      color if active else theme.TEXT_DIM, bold=active, center=True)

    def _draw_gear(self, center, color):
        if self.gear_img:
            tinted = self.gear_img.copy()
            tinted.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MULT)
            self.screen.blit(tinted, tinted.get_rect(center=center))
            return
        # 找不到圖檔時的備用畫法
        cx, cy = center
        for i in range(8):
            angle = math.pi * 2 * i / 8
            pygame.draw.circle(self.screen, color,
                               (int(cx + math.cos(angle) * 8), int(cy + math.sin(angle) * 8)), 3)
        pygame.draw.circle(self.screen, color, (cx, cy), 7)
        pygame.draw.circle(self.screen, theme.PANEL, (cx, cy), 3)

    def draw_settings(self, mouse_pos):
        width, height = self.screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 165))
        self.screen.blit(veil, (0, 0))

        panel_w, panel_h = 520, min(660, height - 60)
        panel = pygame.Rect((width - panel_w) // 2, (height - panel_h) // 2, panel_w, panel_h)
        self.settings_rect = panel
        rounded_panel(self.screen, panel, theme.PANEL, radius=14, alpha=250,
                      border=theme.PANEL_EDGE)

        draw_text(self.screen, "背景設定", (panel.x + 22, panel.y + 18), 17, theme.TEXT, bold=True)
        close = pygame.Rect(panel.right - 44, panel.y + 14, 28, 28)
        self.settings_close = close
        hovered = close.collidepoint(mouse_pos)
        rounded_panel(self.screen, close, theme.DANGER if hovered else theme.PANEL_LIGHT, radius=7)
        c = theme.BG_DEEP if hovered else theme.TEXT_DIM
        pygame.draw.line(self.screen, c, (close.centerx - 5, close.centery - 5),
                         (close.centerx + 5, close.centery + 5), 2)
        pygame.draw.line(self.screen, c, (close.centerx + 5, close.centery - 5),
                         (close.centerx - 5, close.centery + 5), 2)
        pygame.draw.line(self.screen, theme.PANEL_EDGE, (panel.x + 16, panel.y + 52),
                         (panel.right - 16, panel.y + 52))

        inner = panel_w - 44
        x = panel.x + 22
        y = panel.y + 66

        draw_text(self.screen, "背景圖片", (x, y), 14, theme.TEXT)
        draw_text(self.screen, f"images 資料夾 · {len(self.bg_files)} 張",
                  (panel.right - 22, y + 7), 12, theme.TEXT_FAINT, right=True)
        y += 24

        list_h = 116
        list_rect = pygame.Rect(x, y, inner, list_h)
        rounded_panel(self.screen, list_rect, theme.BG_DEEP, radius=8, alpha=210,
                      border=theme.PANEL_EDGE)
        options = ["不使用背景"] + self.bg_files
        row_h = 30
        self.bg_rows = []
        self.screen.set_clip(list_rect.inflate(-4, -6))
        for i, name in enumerate(options):
            ry = list_rect.y + 5 + i * row_h - self.bg_scroll
            if ry + row_h < list_rect.y or ry > list_rect.bottom:
                continue
            row = pygame.Rect(list_rect.x + 5, ry, list_rect.width - 10, row_h - 4)
            active = i == self.bg_index
            hover = row.collidepoint(mouse_pos) and list_rect.collidepoint(mouse_pos)
            if active or hover:
                rounded_panel(self.screen, row,
                              tuple(int(c * 0.3) for c in theme.ACCENT) if active
                              else theme.PANEL_LIGHT, radius=6)
            dot = (row.x + 14, row.centery)
            pygame.draw.circle(self.screen, theme.ACCENT if active else theme.PANEL_EDGE, dot, 6)
            if active:
                pygame.draw.circle(self.screen, theme.BG_DEEP, dot, 2)
            draw_text(self.screen, widgets.clip_text(name, 13, row.width - 46),
                      (row.x + 30, row.y + 5), 13,
                      theme.ACCENT if active else theme.TEXT_DIM)
            self.bg_rows.append((i, row))
        self.screen.set_clip(None)

        content_h = len(options) * row_h + 10
        if content_h > list_h:
            track = pygame.Rect(list_rect.right - 7, list_rect.y + 5, 3, list_h - 10)
            pygame.draw.rect(self.screen, theme.PANEL_LIGHT, track, border_radius=2)
            bar = max(24, int(track.height * list_h / content_h))
            span = max(1, content_h - list_h)
            pygame.draw.rect(self.screen, theme.ACCENT,
                             (track.x, track.y + (self.bg_scroll / span) * (track.height - bar),
                              3, bar), border_radius=2)
        y += list_h + 18

        draw_text(self.screen, "填充方式", (x, y), 14, theme.TEXT)
        y += 22
        self.set_mode.draw(self.screen, pygame.Rect(x, y, inner, 32), mouse_pos)
        y += 40
        notes = {"cover": "保持比例放大到填滿,裁掉超出的部分",
                 "contain": "保持比例完整顯示,邊緣可能留白",
                 "stretch": "拉滿整個視窗,比例會被扭曲",
                 "center": "維持原始大小,可調整擺放位置",
                 "tile": "以原始大小重複並排",
                 "manual": "自由調整位置與縮放比例"}
        mode = self.set_mode.value
        draw_text(self.screen, notes[mode], (x, y), 12,
                  theme.WARN if mode == "stretch" else theme.TEXT_FAINT)
        y += 26

        rows = [(self.set_alpha, "透明度", lambda v: f"{int(v)}", True)]
        if mode in ("center", "manual"):
            rows.append((self.set_x, "水平位置", lambda v: f"{int(v)}", True))
            rows.append((self.set_y, "垂直位置", lambda v: f"{int(v)}", True))
        if mode == "manual":
            rows.append((self.set_scale, "縮放", lambda v: f"{int(v)}%", True))

        for slider, label, fmt, _ in rows:
            draw_text(self.screen, label, (x, y), 13, theme.TEXT)
            draw_text(self.screen, fmt(slider.value), (panel.right - 22, y + 6), 13,
                      theme.ACCENT, right=True)
            y += 20
            slider.draw(self.screen, pygame.Rect(x, y + 4, inner, 14), mouse_pos)
            y += 28
        self.settings_sliders = [r[0] for r in rows]

        keys_y = panel.bottom - 108
        if y < keys_y - 8:      # 視窗太矮、滑桿快頂到時就不擠這塊
            pygame.draw.line(self.screen, theme.PANEL_EDGE, (x, keys_y), (panel.right - 22, keys_y))
            draw_text(self.screen, "快捷鍵", (x, keys_y + 12), 12, theme.TEXT_DIM)
            for offset, (key, desc) in enumerate((("F11", "切換全螢幕"), ("Esc", "關閉這個視窗"))):
                kx = x + 60 + offset * 180
                chip = pygame.Rect(kx, keys_y + 8, 40, 20)
                rounded_panel(self.screen, chip, theme.PANEL_LIGHT, radius=5,
                              border=theme.PANEL_EDGE)
                draw_text(self.screen, key, chip.center, 11, theme.TEXT_DIM, center=True)
                draw_text(self.screen, desc, (kx + 48, keys_y + 11), 12, theme.TEXT_FAINT)

        foot_y = panel.bottom - 56
        if self.settings_dirty:
            draw_text(self.screen, "有尚未儲存的變更", (x, foot_y + 12), 12, theme.WARN)
        elif pygame.time.get_ticks() - self.settings_saved_at < 2500:
            draw_text(self.screen, "已儲存到 config.json", (x, foot_y + 12), 12, theme.ACCENT)
        else:
            draw_text(self.screen, "調整後即時預覽,關閉不會自動儲存",
                      (x, foot_y + 12), 12, theme.TEXT_FAINT)

        self.btn_revert.draw(self.screen, pygame.Rect(panel.right - 190, foot_y, 78, 34), mouse_pos)
        self.btn_save.draw(self.screen, pygame.Rect(panel.right - 104, foot_y, 82, 34), mouse_pos)

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
            pygame.draw.rect(self.screen, theme.PANEL_EDGE,
                             (body.centerx - 26, icon_y, 52, 64), 2, border_radius=6)
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
            rounded_panel(self.screen, row, theme.PANEL_LIGHT if hover else theme.BG_DEEP,
                          radius=8, alpha=200)

            pygame.draw.rect(self.screen, self.accent, (row.x + 10, row.y + 14, 3, 22), border_radius=2)
            name = widgets.clip_text(item.path.name, 15, row.width - 180)
            draw_text(self.screen, name, (row.x + 22, row.y + 8), 15, theme.TEXT)
            draw_text(self.screen, f"{item.page_label} · {pdf_ops.human_size(item.size)}",
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
            ratio = body.height / content_h
            bar_h = max(30, int(track.height * ratio))
            max_scroll = content_h - body.height
            pos = (offset / max_scroll) * (track.height - bar_h) if max_scroll > 0 else 0
            pygame.draw.rect(self.screen, self.accent,
                             (track.x, track.y + pos, 4, bar_h), border_radius=2)

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
            draw_text(self.screen, "非黑白的圖會自動跳過,不會被破壞",
                      (rect.x + 18, y), 12, theme.DANGER)
            y += 20
        y += 6

        rows = ((self.c_quality, "影像品質", lambda v: f"{int(v)}", mode in ("lossless", "bw")),
                (self.c_maxdim, "解析度上限", lambda v: f"{int(v)} px", mode == "lossless"))
        for slider, label, fmt, disabled in rows:
            draw_text(self.screen, label, (rect.x + 18, y), 14,
                      theme.TEXT_FAINT if disabled else theme.TEXT)
            draw_text(self.screen, "-" if disabled else fmt(slider.value),
                      (rect.right - 18, y + 7), 13,
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
                draw_text(self.screen, f"將產生 {total} 個檔案", (rect.x + 18, y + 6), 14,
                          self.accent, bold=True)
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
            draw_text(self.screen, f"共 {total} 頁", (rect.right - 18, y + 7), 13,
                      theme.TEXT_DIM, right=True)
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
        draw_text(self.screen, f"{len(files)}", (box.x + 24, box.y + 12), 20, self.accent,
                  bold=True, center=True)
        draw_text(self.screen, "檔案", (box.x + 24, box.y + 38), 11, theme.TEXT_FAINT, center=True)
        draw_text(self.screen, f"{total_pages}", (box.centerx, box.y + 12), 20, self.accent,
                  bold=True, center=True)
        draw_text(self.screen, "總頁數", (box.centerx, box.y + 38), 11, theme.TEXT_FAINT, center=True)
        draw_text(self.screen, pdf_ops.human_size(total_size), (box.right - 40, box.y + 12), 15,
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
        done, total, message, lines, error, finished = self.runner.snapshot()
        running = self.runner.running

        bar = pygame.Rect(rect.x + 18, rect.y + 16, rect.width - 268, 22)
        if running:
            ratio = done / total if total else 0
            ProgressBar(self.accent).draw(self.screen, bar, ratio, f"{int(ratio * 100)}%")
            draw_text(self.screen, widgets.clip_text(message, 12, bar.width),
                      (bar.x, bar.bottom + 8), 12, theme.TEXT_DIM)
        else:
            shown = self.result_lines or lines
            if error:
                draw_text(self.screen, f"錯誤: {error}", (bar.x, bar.y + 2), 13, theme.DANGER)
            elif shown:
                for i, line in enumerate(shown[:3]):
                    draw_text(self.screen, widgets.clip_text(line, 13, bar.width),
                              (bar.x, bar.y + i * 19), 13,
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
        if event.type == pygame.QUIT:
            return False
        if event.type == pygame.VIDEORESIZE and not self.fullscreen:
            self.screen = pygame.display.set_mode((max(960, event.w), max(640, event.h)),
                                                  pygame.RESIZABLE)
            self.windowed_size = self.screen.get_size()
            return True

        if event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
            self.toggle_fullscreen()
            return True

        if self.settings_open:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self.settings_open = False
                return True
            if event.type == pygame.MOUSEWHEEL:
                self.bg_scroll = max(0, self.bg_scroll - event.y * 30)
            for slider in getattr(self, "settings_sliders", []):
                slider.handle(event, mouse_pos)
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self.handle_settings_click(mouse_pos)
            self.apply_settings()
            return True

        if event.type == pygame.DROPFILE:
            self.add_files([event.file])
        elif event.type == pygame.MOUSEWHEEL:
            self.scroll[self.tab] = max(0, self.scroll[self.tab] - event.y * 40)

        for slider in self.active_sliders():
            slider.handle(event, mouse_pos)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.handle_click(mouse_pos)
        return True

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            self.windowed_size = self.screen.get_size()
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            self.screen = pygame.display.set_mode(self.windowed_size, pygame.RESIZABLE)

    def handle_settings_click(self, mouse_pos):
        if self.settings_close.collidepoint(mouse_pos):
            self.settings_open = False
            return
        for index, row in getattr(self, "bg_rows", []):
            if row.collidepoint(mouse_pos):
                self.bg_index = index
                return
        if self.set_mode.clicked(mouse_pos, True):
            return
        if self.btn_save.clicked(mouse_pos, True):
            self.save_settings()
            return
        if self.btn_revert.clicked(mouse_pos, True):
            self.revert_settings()
            return
        if not self.settings_rect.collidepoint(mouse_pos):
            self.settings_open = False

    def active_sliders(self):
        if self.tab == "compress":
            return [self.c_quality, self.c_maxdim]
        if self.tab == "split":
            if self.s_everypage.value:
                return [self.s_dpi]
            return [self.s_parts] + self.s_weights[:int(self.s_parts.value)]
        return [self.m_quality] if self.m_compress.value else []

    def handle_click(self, mouse_pos):
        if getattr(self, "gear_rect", None) and self.gear_rect.collidepoint(mouse_pos):
            self.open_settings()
            return

        for key, rect in getattr(self, "tab_rects", []):
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
        else:
            if self.btn_output.clicked(mouse_pos, True):
                OUTPUT_DIR.mkdir(exist_ok=True)
                os.startfile(OUTPUT_DIR)
            elif self.btn_run.clicked(mouse_pos, True):
                self.start_job()

    # ------------------------------------------------------------ 主迴圈

    def draw_frame(self, mouse_pos=(-100, -100)):
        width, height = self.screen.get_size()
        self.draw_background()
        self.draw_header(width)

        margin = 20
        content_y = 66 + margin
        footer_h = 74
        content_h = height - content_y - footer_h - margin * 2
        list_w = int((width - margin * 3) * 0.54)

        self.draw_file_list(pygame.Rect(margin, content_y, list_w, content_h), mouse_pos)
        self.draw_options(pygame.Rect(margin * 2 + list_w, content_y,
                                      width - list_w - margin * 3, content_h), mouse_pos)
        self.draw_footer(pygame.Rect(margin, height - footer_h - margin,
                                     width - margin * 2, footer_h), mouse_pos)

        if self.settings_open:
            self.draw_settings(mouse_pos)

    def run(self):
        running = True
        while running:
            mouse_pos = pygame.mouse.get_pos()
            for event in pygame.event.get():
                if not self.handle_event(event, mouse_pos):
                    running = False

            if self.runner.finished and not self.runner.running and not self.result_lines:
                _, _, _, lines, error, _ = self.runner.snapshot()
                if lines or error:
                    self.result_lines = lines
                    self.status = "完成" if not error else "發生錯誤"

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
        log_path = BASE_DIR / "error.log"
        try:
            log_path.write_text(detail, encoding="utf-8")
        except Exception:
            pass
        if platform.system() == "Windows":
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(
                    0, f"{detail[-900:]}\n\n完整內容已存到 error.log",
                    "PDF Studio 發生錯誤", 0x10)
            except Exception:
                pass
        raise


if __name__ == "__main__":
    main()
