"""影音工具的畫面:影片、音訊兩個分頁。把檔案拖進視窗轉檔、壓縮,或把影片轉成 GIF。"""

import os
import queue
import shutil
import threading
from pathlib import Path

import pygame

from core import deps, large_files, paths, theme, widgets
from core.plugins import Page
from core.scroll import BAR_SPACE, ScrollView
from core.widgets import (Button, ChoiceGrid, ProgressBar, SegmentedControl, Slider, TextInput, Toggle, draw_text,
                          rounded_panel)

from . import formats as F
from . import ops
from .advanced import AdvancedDialog, summary

TABS = [("video", "影片"), ("audio", "音訊")]
TAB_COLORS = {"video": (255, 180, 84), "audio": (126, 211, 165)}
ROW_H = 72
THUMB = (64, 48)
MODES = [("level", "畫質等級"), ("size", "目標大小")]
SIZE_PRESETS = [("10", "10 MB"), ("25", "25 MB"), ("50", "50 MB"), ("100", "100 MB")]
DEVICES = [("auto", "自動"), ("gpu", "顯示卡"), ("cpu", "處理器")]
DEVICE_NOTES = {"auto": "有支援的顯示卡時用顯示卡，沒有時用處理器",
                "gpu": "轉得快，同樣畫質下檔案通常比較大",
                "cpu": "轉得比較慢，同樣畫質下檔案比較小"}
DEPTHS = [("16", "16 位元"), ("24", "24 位元")]
DEPTH_NOTES = {"16": "和 CD 一樣，一般聆聽足夠", "24": "錄音室常用，檔案大一半，適合後製"}
LARGE_SUBTITLE = "以下檔案轉檔時會花比較多時間或磁碟空間"
LARGE_NOTES = ("轉檔期間電腦可能變慢，檔案越大花的時間越久",
               "中途可以取消，取消不會留下轉到一半的檔案")
STATUS_COLORS = {"error": theme.DANGER, "cancelled": theme.TEXT_FAINT}


def output_dir():
    return paths.OUTPUT_DIR / "media"


class Item:
    def __init__(self, path: Path):
        self.path = path
        self.size = path.stat().st_size if path.exists() else 0
        self.info = None        # 讀完後是 dict;讀不了時是錯誤訊息
        self.thumb_data = None
        self.thumb = None
        self.status = "waiting"
        self.progress = 0.0
        self.message = ""
        self.out_size = 0
        self.target = ""

    @property
    def readable(self):
        return isinstance(self.info, dict)

    def detail(self):
        if self.info is None:
            return f"讀取中... · {deps.human_size(self.size)}"
        if not self.readable:
            return f"無法讀取：{self.info}"
        info = self.info
        parts = [self.path.suffix[1:].upper() or "?"]
        if info.get("width"):
            parts.append(f"{info['width']}×{info['height']}")
        elif info.get("sample_rate"):
            parts.append(f"{info['sample_rate']} Hz")
        if info.get("duration"):
            parts.append(ops.human_time(info["duration"]))
        if not info.get("audio_codec") and info.get("width"):
            parts.append("沒有聲音")
        parts.append(deps.human_size(self.size))
        return " · ".join(parts)

    def result(self):
        """(文字, 顏色);還沒轉檔時回傳 None。"""
        if self.status == "running":
            return f"轉檔中 {int(self.progress * 100)}%", None
        if self.status == "error":
            return f"失敗：{self.message}", theme.DANGER
        if self.status == "cancelled":
            return "已取消", theme.TEXT_FAINT
        if self.status == "done":
            change = (self.out_size - self.size) / max(1, self.size) * 100
            text = (f"{deps.human_size(self.size)} → {self.target} {deps.human_size(self.out_size)}"
                    f"（{change:+.0f}%）")
            if self.message:
                text += f" · {self.message}"
            return text, None if change <= 0 else theme.WARN
        return None


class TabState:
    """一個分頁自己的檔案清單與設定。"""

    def __init__(self, kind, accent):
        self.kind = kind
        self.accent = accent
        self.items = []
        self.list_view = ScrollView(accent=accent)
        self.settings_view = ScrollView(accent=accent, indicator=True)
        self.list_area = pygame.Rect(0, 0, 0, 0)
        self.settings_area = pygame.Rect(0, 0, 0, 0)
        self.row_buttons = []
        self.controls = []
        self.sliders = []
        self.adv = ops.Advanced()
        video = kind == "video"
        formats = F.VIDEO_FORMATS if video else F.AUDIO_FORMATS
        self.fmt = ChoiceGrid([(f.key, f.label) for f in formats], columns=6, index=0 if video else 1, accent=accent)
        self.mode = SegmentedControl(MODES, accent=accent)
        self.level_key = "balance"
        self.level = SegmentedControl(F.LEVELS, index=1, accent=accent)
        self.target = TextInput("25" if video else "5", accent=accent, size=14)
        self.target_error = ""
        self.size_preset = SegmentedControl(SIZE_PRESETS, accent=accent)
        self.device = SegmentedControl(DEVICES, accent=accent)
        self.gif_fps = Slider(5, 30, 12, accent=accent)
        self.gif_width = Slider(120, 1280, 480, step=20, accent=accent)
        self.dither = SegmentedControl(F.DITHERS, accent=accent)
        self.loop = Toggle(True, accent=accent)
        self.depth = SegmentedControl(DEPTHS, accent=accent)
        self.btn_advanced = Button("進階設定", accent=accent, filled=False, size=13)

    @property
    def custom_quality(self):
        if self.kind == "video":
            return self.adv.custom_video
        return self.adv.audio_bitrate is not None

    def target_mb(self):
        try:
            value = float(self.target.text.strip())
        except ValueError:
            return None
        return value if 0 < value <= 100000 else None

    def settings(self):
        target = self.target_mb() or 1.0
        if self.kind == "video":
            return ops.VideoSettings(fmt=self.fmt.value, mode=self.mode.value, level=self.level_key, target_mb=target,
                                     device=self.device.value, gif_fps=int(self.gif_fps.value),
                                     gif_width=int(self.gif_width.value), gif_dither=self.dither.value,
                                     gif_loop=self.loop.value, adv=self.adv.copy())
        return ops.AudioSettings(fmt=self.fmt.value, mode=self.mode.value, level=self.level_key, target_mb=target,
                                 depth=int(self.depth.value), adv=self.adv.copy())


class MediaPage(Page):
    def __init__(self, app, tool):
        super().__init__(app, tool)
        self.tab = "video"
        self.tabs = {key: TabState(key, TAB_COLORS[key]) for key, _ in TABS}
        self.tab_rects = []
        self.notice = ""
        self._lock = threading.Lock()
        self.worker = None
        self.worker_tab = None
        self.stop_event = threading.Event()
        self.hardware = None
        self.hardware_ready = threading.Event()
        self._probe_queue = queue.Queue()
        threading.Thread(target=self._probe_worker, daemon=True).start()
        threading.Thread(target=self._detect_hardware, daemon=True).start()
        self.btn_clear = Button("清空清單", filled=False, size=13)
        self.btn_output = Button("輸出資料夾", filled=False, size=14)
        self.btn_cancel = Button("取消", accent=theme.DANGER, filled=False)
        self.btn_run = Button("開始轉換", accent=TAB_COLORS["video"])
        self.advanced = AdvancedDialog(lambda: self.screen, TAB_COLORS["video"])

    # ------------------------------------------------------------ 資料

    @property
    def state(self):
        return self.tabs[self.tab]

    @property
    def accent(self):
        return TAB_COLORS[self.tab]

    @property
    def running(self):
        return self.worker is not None and self.worker.is_alive()

    def _detect_hardware(self):
        try:
            self.hardware = ops.detect_hardware()
        except Exception:
            self.hardware = {}
        self.hardware_ready.set()

    def encoder_for(self, codec_key, device=None):
        device = device or self.tabs["video"].device.value
        hardware = self.hardware or {}
        if device != "cpu" and codec_key in hardware:
            return hardware[codec_key][1]
        return F.CODECS[codec_key].cpu

    def add_files(self, raw_paths):
        state = self.state
        allowed = F.VIDEO_INPUT if state.kind == "video" else F.AUDIO_INPUT
        candidates, skipped = [], 0
        for raw in raw_paths:
            path = Path(raw)
            if path.is_dir():
                candidates += sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in allowed)
            elif path.is_file() and path.suffix.lower() in allowed:
                candidates.append(path)
            else:
                skipped += 1
        with self._lock:
            existing = {item.path for item in state.items}
            for path in candidates:
                if path not in existing:
                    item = Item(path)
                    state.items.append(item)
                    existing.add(path)
                    self._probe_queue.put(item)
        if skipped:
            self.notice = "有檔案不是這個分頁支援的格式，已略過" + ("；聲音檔請用音訊分頁" if state.kind == "video" else "")
        elif candidates:
            self.notice = ""

    def _probe_worker(self):
        while True:
            item = self._probe_queue.get()
            try:
                info = ops.probe(item.path)
                item.thumb_data = ops.thumbnail(item.path, info, THUMB)
                item.info = info
            except Exception as exc:
                item.info = str(exc) or type(exc).__name__

    def estimate(self, item, s):
        if not item.readable:
            return None
        if self.state.kind == "video":
            return ops.estimate_video(item.path, item.info, s, self.hardware or {})
        return ops.estimate_audio(item.path, item.info, s)

    # ------------------------------------------------------------ 轉檔

    def large_rows(self, state, items, s):
        rows, total = [], 0
        for item in items:
            if item.size >= large_files.WARN_BYTES:
                rows.append((item.path.name, f"檔案大小 {deps.human_size(item.size)}，轉檔可能要花比較久"))
            estimate = self.estimate(item, s)
            total += estimate[1] if estimate else 0
            if state.kind == "video" and s.fmt == "gif" and item.info.get("width"):
                try:
                    _, length = ops.time_range(item.info, s.adv)
                except ValueError:
                    continue
                width = min(s.gif_width, item.info["width"])
                height = item.info["height"] * width / item.info["width"]
                frames = int(length * min(s.gif_fps, item.info.get("fps") or s.gif_fps))
                if width * height * frames >= large_files.WARN_PIXELS:
                    rows.append((item.path.name, f"GIF 約 {frames} 格，檔案可能非常大"))
        folder = output_dir()
        existing = next((p for p in (folder, *folder.parents) if p.exists()), None)
        if existing is not None and total:
            free = shutil.disk_usage(existing).free
            if total > free:
                rows.append(("輸出資料夾 output\\media",
                             f"預估最多需要 {deps.human_size(total)}，磁碟只剩 {deps.human_size(free)}"))
        return rows

    def start(self):
        state = self.state
        if self.running or not state.items:
            return
        if state.mode.value == "size" and state.target_mb() is None and not (state.kind == "video"
                                                                              and state.fmt.value == "gif"):
            self.notice = "目標大小要填大於 0 的數字"
            return
        with self._lock:
            items = [item for item in state.items if item.readable]
        if not items:
            self.notice = "清單裡沒有可以轉檔的檔案"
            return
        s = state.settings()
        self.app.large_files.confirm(self.large_rows(state, items, s), lambda: self._begin(state, items, s),
                                     notes=LARGE_NOTES, subtitle=LARGE_SUBTITLE)

    def _begin(self, state, items, s):
        if self.running:
            return
        for item in items:
            item.status, item.message, item.out_size, item.progress = "waiting", "", 0, 0.0
        self.notice = ""
        self.stop_event.clear()
        self.worker_tab = state.kind
        self.worker = threading.Thread(target=self._work, args=(state, items, s), daemon=True)
        self.worker.start()

    def _work(self, state, items, s):
        folder = output_dir()
        if state.kind == "video" and s.fmt != "gif" and s.device != "cpu":
            self.hardware_ready.wait(60)     # 顯示卡偵測還沒做完時先等,才不會全部用處理器轉
        for item in items:
            if self.stop_event.is_set():
                break
            item.status, item.progress = "running", 0.0

            def progress(ratio, it=item):
                it.progress = ratio

            try:
                if state.kind == "video":
                    out, note = ops.convert_video(item.path, item.info, folder, s, self.hardware or {}, progress,
                                                  self.stop_event)
                else:
                    out, note = ops.convert_audio(item.path, item.info, folder, s, progress, self.stop_event)
                item.out_size = out.stat().st_size
                item.target = out.suffix[1:].upper()
                item.message = note
                item.status = "done"
            except ops.Cancelled:
                item.status = "cancelled"
                break
            except (ValueError, RuntimeError) as exc:
                item.status, item.message = "error", str(exc)
            except Exception as exc:
                item.status, item.message = "error", f"轉檔失敗：{exc}"
        for item in items:
            if item.status in ("waiting", "running"):
                item.status = "cancelled"

    def stop(self):
        self.stop_event.set()

    # ------------------------------------------------------------ 進階設定

    def open_advanced(self):
        state = self.state
        first = next((item.info for item in state.items if item.readable and item.info.get("width")), None)
        aspect = first["width"] / max(1, first["height"]) if first else 16 / 9
        self.advanced.accent = state.accent
        self.advanced.open(state.kind, state.adv, state.fmt.value, state.level_key, self.encoder_for, aspect,
                           lambda adv: setattr(state, "adv", adv))

    def modal_open(self):
        return self.advanced.is_open

    def draw_modal(self, mouse_pos):
        self.advanced.draw(mouse_pos)

    def handle_modal_event(self, event, mouse_pos):
        self.advanced.handle_event(event, mouse_pos)

    def deactivate(self):
        for state in self.tabs.values():
            state.list_view.reset()
            state.settings_view.reset()
            state.target.blur()
            for slider in (state.gif_fps, state.gif_width):
                slider.dragging = False

    def update(self):
        mouse = pygame.mouse.get_pos()
        self.state.list_view.update(mouse)
        self.state.settings_view.update(mouse)

    # ------------------------------------------------------------ 繪製

    def draw_toolbar(self, rect, mouse_pos):
        self.tab_rects = []
        x = rect.right
        for key, label in reversed(TABS):
            tab = pygame.Rect(x - 104, rect.y, 104, rect.height)
            x -= 112
            self.tab_rects.append((key, tab))
            active = key == self.tab
            color = TAB_COLORS[key]
            if active:
                rounded_panel(self.screen, tab, tuple(int(c * 0.28) for c in color), radius=8)
                pygame.draw.rect(self.screen, color, (tab.x + 14, tab.bottom - 3, tab.width - 28, 3), border_radius=2)
            elif tab.collidepoint(mouse_pos):
                rounded_panel(self.screen, tab, theme.PANEL_LIGHT, radius=8)
            draw_text(self.screen, label, tab.center, 16, color if active else theme.TEXT_DIM, bold=active, center=True)

    def draw(self, rect, mouse_pos):
        margin = 20
        content_y = rect.y + margin
        footer_h = 74
        content_h = rect.bottom - content_y - footer_h - margin * 2
        list_w = int((rect.width - margin * 3) * 0.54)
        settings = self.state.settings()
        self.draw_list(pygame.Rect(rect.x + margin, content_y, list_w, content_h), mouse_pos, settings)
        self.draw_settings(pygame.Rect(rect.x + margin * 2 + list_w, content_y, rect.width - list_w - margin * 3,
                                       content_h), mouse_pos, settings)
        self.draw_footer(pygame.Rect(rect.x + margin, rect.bottom - footer_h - margin, rect.width - margin * 2,
                                     footer_h), mouse_pos)

    def _draw_empty(self, area):
        screen = self.screen
        cx, cy = area.centerx, area.centery - 34
        if self.state.kind == "video":
            frame = pygame.Rect(cx - 36, cy - 24, 72, 48)
            pygame.draw.rect(screen, theme.PANEL_EDGE, frame, 3, border_radius=8)
            pygame.draw.polygon(screen, theme.PANEL_EDGE, [(cx - 8, cy - 12), (cx - 8, cy + 12), (cx + 14, cy)])
            title, formats = "把影片拖曳到這個視窗", "支援 MP4、MOV、MKV、WebM、AVI、WMV、FLV、MPG、TS、GIF 等"
        else:
            self._draw_note_icon(screen, (cx, cy), theme.PANEL_EDGE, 1.6)
            title, formats = "把聲音檔或影片拖曳到這個視窗", "支援 MP3、M4A、WAV、FLAC、OGG、Opus、WMA、AIFF；影片會取出聲音"
        draw_text(screen, title, (cx, area.centery + 30), 16, theme.TEXT_DIM, center=True)
        draw_text(screen, formats, (cx, area.centery + 54), 13, theme.TEXT_FAINT, center=True)
        draw_text(screen, "可一次拖多個檔案或整個資料夾", (cx, area.centery + 74), 13, theme.TEXT_FAINT, center=True)

    @staticmethod
    def _draw_note_icon(screen, center, color, scale=1.0):
        cx, cy = center
        head = (int(cx - 8 * scale), int(cy + 12 * scale))
        pygame.draw.circle(screen, color, head, int(7 * scale))
        pygame.draw.line(screen, color, (head[0] + int(6 * scale), head[1]), (head[0] + int(6 * scale), int(cy - 16 * scale)),
                         max(2, int(3 * scale)))
        pygame.draw.line(screen, color, (head[0] + int(6 * scale), int(cy - 16 * scale)),
                         (int(cx + 14 * scale), int(cy - 10 * scale)), max(2, int(3 * scale)))

    def draw_list(self, rect, mouse_pos, settings):
        screen = self.screen
        state = self.state
        accent = state.accent
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        draw_text(screen, f"檔案清單 ({len(state.items)})", (rect.x + 16, rect.y + 13), 15, theme.TEXT, bold=True)
        locked = self.running and self.worker_tab == state.kind
        if state.items and not locked:
            self.btn_clear.draw(screen, pygame.Rect(rect.right - 96, rect.y + 9, 80, 26), mouse_pos)
        pygame.draw.line(screen, theme.PANEL_EDGE, (rect.x + 12, rect.y + 44), (rect.right - 12, rect.y + 44))

        area = pygame.Rect(rect.x, rect.y + 45, rect.width, rect.height - 45)
        state.list_area = area
        state.row_buttons = []
        if not state.items:
            state.list_view.clear()
            self._draw_empty(area)
            return
        view = state.list_view
        items = list(state.items)
        view.layout(area, len(items) * ROW_H + 16)
        screen.set_clip(area)
        for index, item in enumerate(items):
            y = area.y + 8 + index * ROW_H - view.scroll
            if y + ROW_H < area.y or y > area.bottom:
                continue
            row = pygame.Rect(area.x + 10, y, area.width - 10 - BAR_SPACE, ROW_H - 8)
            hover = row.collidepoint(mouse_pos) and area.collidepoint(mouse_pos)
            rounded_panel(screen, row, theme.PANEL_LIGHT if hover else theme.BG_DEEP, radius=8, alpha=200)

            box = pygame.Rect(row.x + 8, row.centery - THUMB[1] // 2, THUMB[0], THUMB[1])
            rounded_panel(screen, box, theme.PANEL, radius=6)
            if item.thumb is None and item.thumb_data is not None:
                size, data = item.thumb_data
                item.thumb = pygame.image.frombytes(data, size, "RGBA")
            if item.thumb is not None:
                screen.blit(item.thumb, item.thumb.get_rect(center=box.center))
            elif item.readable:
                self._draw_note_icon(screen, box.center, theme.TEXT_FAINT, 0.8)
            else:
                draw_text(screen, "?" if isinstance(item.info, str) else "...", box.center, 13, theme.TEXT_FAINT,
                          center=True)

            text_x = box.right + 12
            text_w = row.right - text_x - (44 if not locked else 12)
            draw_text(screen, widgets.clip_text(item.path.name, 14, text_w, bold=True), (text_x, row.y + 6), 14,
                      theme.TEXT, bold=True)
            draw_text(screen, widgets.clip_text(item.detail(), 12, text_w), (text_x, row.y + 27), 12,
                      theme.DANGER if isinstance(item.info, str) else theme.TEXT_DIM)
            result = item.result()
            if result is not None:
                text, color = result
                if item.status == "running":
                    bar = pygame.Rect(text_x + 96, row.y + 49, max(20, text_w - 96), 8)
                    ProgressBar(accent).draw(screen, bar, item.progress)
                draw_text(screen, widgets.clip_text(text, 12, text_w if item.status != "running" else 90),
                          (text_x, row.y + 45), 12, color or accent)
            elif item.readable:
                estimate = self.estimate(item, settings)
                if estimate is None:
                    text = self._plan_problem(item, settings)
                    draw_text(screen, widgets.clip_text(text, 12, text_w), (text_x, row.y + 45), 12, theme.WARN)
                else:
                    low, high = estimate
                    text = (f"預估 {deps.human_size(low)} ~ {deps.human_size(high)}" if high > low * 1.25
                            else f"預估約 {deps.human_size(high)}")
                    draw_text(screen, widgets.clip_text(text, 12, text_w), (text_x, row.y + 45), 12, accent)

            if not locked:
                remove = pygame.Rect(row.right - 34, row.y + 8, 26, 26)
                hovered = remove.collidepoint(mouse_pos)
                rounded_panel(screen, remove, theme.DANGER if hovered else theme.PANEL, radius=6)
                cross = theme.BG_DEEP if hovered else theme.TEXT_DIM
                ax, ay = remove.center
                pygame.draw.line(screen, cross, (ax - 5, ay - 5), (ax + 5, ay + 5), 2)
                pygame.draw.line(screen, cross, (ax + 5, ay - 5), (ax - 5, ay + 5), 2)
                state.row_buttons.append((item, remove))
        screen.set_clip(None)
        view.draw(screen, mouse_pos)

    def _plan_problem(self, item, s):
        """沒辦法預估大小時的原因(例如沒有聲音、時間範圍超出)。"""
        try:
            if self.state.kind == "video":
                plan = ops.plan_video(item.path, item.info, s, self.hardware or {})
                if s.mode == "size" and plan.fmt.key != "gif":
                    ops.planned_video_kbps(plan, s, item.info)
            else:
                ops.audio_plan(item.path, item.info, s)
        except (RuntimeError, ValueError) as exc:
            return str(exc)
        return "無法預估大小"

    # 設定欄

    def draw_settings(self, rect, mouse_pos, settings):
        screen = self.screen
        state = self.state
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        draw_text(screen, "轉換設定", (rect.x + 18, rect.y + 14), 15, theme.TEXT, bold=True)
        pygame.draw.line(screen, theme.PANEL_EDGE, (rect.x + 12, rect.y + 44), (rect.right - 12, rect.y + 44))
        view = state.settings_view
        area = pygame.Rect(rect.x, rect.y + 45, rect.width, rect.height - 49)
        state.settings_area = area
        screen.set_clip(area)
        state.controls, state.sliders = [], []
        bottom = self._draw_setting_rows(state, rect.x + 18, area.y + 15 - view.scroll, rect.width - 36, rect.right,
                                         mouse_pos, settings)
        screen.set_clip(None)
        view.layout(area, bottom + view.scroll - area.y + 8)
        view.draw(screen, mouse_pos)

    def _note(self, text, x, y, inner, color=theme.TEXT_FAINT):
        draw_text(self.screen, widgets.clip_text(text, 12, inner), (x, y), 12, color)
        return y + 20

    def _title(self, text, x, y, color):
        draw_text(self.screen, text, (x, y), 14, color)
        return y + 22

    def _segment(self, state, control, x, y, inner, mouse_pos, shown_index=None):
        """shown_index:畫面上要亮起來的選項(-1 表示都不亮),不會改變控制項本身選的值。"""
        real = control.index
        if shown_index is not None:
            control.index = shown_index
        control.draw(self.screen, pygame.Rect(x, y, inner, 32), mouse_pos)
        control.index = real
        state.controls.append(control)
        return y + 40

    def _slider(self, state, title, value_text, slider, x, y, inner, right, mouse_pos, color):
        draw_text(self.screen, title, (x, y), 14, color)
        draw_text(self.screen, value_text, (right - 18, y + 7), 13, slider.accent, right=True)
        slider.draw(self.screen, pygame.Rect(x, y + 28, inner, 16), mouse_pos)
        state.sliders.append(slider)
        return y + 52

    def _draw_setting_rows(self, state, x, y, inner, right, mouse_pos, s):
        screen = self.screen
        locked = self.running and self.worker_tab == state.kind
        color = theme.TEXT_FAINT if locked else theme.TEXT
        video = state.kind == "video"
        fmt_key = state.fmt.value
        fmt = F.VIDEO_FORMAT[fmt_key] if video else F.AUDIO_FORMAT[fmt_key]

        y = self._title("輸出格式", x, y, color)
        y += state.fmt.draw(screen, pygame.Rect(x, y, inner, 0), mouse_pos) + 6
        state.controls.append(state.fmt)
        y = self._note(fmt.note, x, y, inner)
        if video and fmt.warn:
            y = self._note(fmt.warn, x, y, inner, theme.WARN)
        y += 10

        if video and fmt_key == "gif":
            y = self._slider(state, "每秒格數", f"{int(state.gif_fps.value)} 格", state.gif_fps, x, y, inner, right,
                             mouse_pos, color)
            y = self._note("格數越多動作越順，檔案也越大；不會超過原影片", x, y, inner) + 6
            y = self._slider(state, "最大寬度", f"{int(state.gif_width.value)} px", state.gif_width, x, y, inner,
                             right, mouse_pos, color)
            y = self._note("高度依比例自動計算；只縮小不放大", x, y, inner) + 6
            y = self._title("抖色方式", x, y, color)
            y = self._segment(state, state.dither, x, y, inner, mouse_pos)
            y = self._note(F.DITHER_NOTES[state.dither.value], x, y - 6, inner) + 6
            draw_text(screen, "循環播放", (x, y + 2), 14, color)
            draw_text(screen, "播完後從頭重新播放", (x, y + 22), 12, theme.TEXT_FAINT)
            state.loop.draw(screen, (right - 60, y + 6), mouse_pos)
            state.controls.append(state.loop)
            y += 52
        else:
            y = self._draw_quality_rows(state, x, y, inner, mouse_pos, color, s, fmt)
        if video and fmt_key != "gif" and not fmt.target:
            y = self._draw_device_rows(state, x, y, inner, mouse_pos, color, s)

        state.btn_advanced.draw(screen, pygame.Rect(x, y, inner, 32), mouse_pos)
        y += 40
        text = summary(state.adv, state.kind, fmt_key)
        hint = "編碼器、解析度、時間範圍、聲音等" if state.kind == "video" else "位元速率、取樣率、聲道、時間範圍"
        y = self._note(f"已調整：{text}" if text else hint, x, y, inner, state.accent if text else theme.TEXT_FAINT) + 8

        pygame.draw.line(screen, theme.PANEL_EDGE, (x, y), (right - 18, y))
        y += 14
        if locked:
            draw_text(screen, "處理中無法變更設定", (x, y), 12, theme.WARN)
            y += 22
        for line in ("不會覆蓋原檔，同名時自動加上編號", "全部在本地處理，不會上傳"):
            draw_text(screen, line, (x, y), 12, theme.TEXT_FAINT)
            y += 20
        return y

    def _draw_quality_rows(self, state, x, y, inner, mouse_pos, color, s, fmt):
        video = state.kind == "video"
        lossless = not video and fmt.codec and F.AUDIO_CODECS[fmt.codec].lossless
        if lossless:
            y = self._title("位元深度", x, y, color)
            y = self._segment(state, state.depth, x, y, inner, mouse_pos)
            y = self._note(DEPTH_NOTES[state.depth.value], x, y - 6, inner)
            return self._note("無損格式不需要設定品質，音質和原檔一樣", x, y, inner) + 12

        y = self._title("品質", x, y, color)
        y = self._segment(state, state.mode, x, y, inner, mouse_pos)
        if state.mode.value == "level":
            keys = [key for key, _ in F.LEVELS]
            custom = state.custom_quality
            y = self._segment(state, state.level, x, y, inner, mouse_pos, -1 if custom else keys.index(state.level_key))
            if custom:
                y = self._note("目前使用進階設定裡的自訂品質；點選任一等級可以改回來", x, y - 6, inner, state.accent)
            else:
                y = self._note(F.LEVEL_NOTES[state.level_key], x, y - 6, inner)
        else:
            draw_text(self.screen, "每個檔案壓到", (x, y + 8), 13, color)
            state.target.error = bool(state.target.text.strip()) and state.target_mb() is None
            state.target.draw(self.screen, pygame.Rect(x + 96, y, 100, 32), mouse_pos)
            draw_text(self.screen, "MB 以下", (x + 206, y + 8), 13, color)
            y += 40
            presets = [key for key, _ in SIZE_PRESETS]
            shown = presets.index(state.target.text.strip()) if state.target.text.strip() in presets else -1
            y = self._segment(state, state.size_preset, x, y, inner, mouse_pos, shown)
            if video and fmt.key != "keep" and fmt.codecs and not F.CODECS[fmt.codecs[0]].bitrate \
                    and not state.adv.codec:
                y = self._note("這個編碼沒辦法指定大小，會改用畫質等級「平衡」", x, y - 6, inner, theme.WARN)
            elif video:
                y = self._note("依影片長度換算畫質；用處理器轉時會分析兩次，比較花時間", x, y - 6, inner)
                y = self._note("第一次轉完超過目標時，會自動降低畫質重轉一次", x, y, inner)
            else:
                y = self._note("依聲音長度換算位元速率", x, y - 6, inner)
        return y + 10

    def _draw_device_rows(self, state, x, y, inner, mouse_pos, color, s):
        y = self._title("轉檔方式", x, y, color)
        y = self._segment(state, state.device, x, y, inner, mouse_pos)
        y = self._note(DEVICE_NOTES[state.device.value], x, y - 6, inner)
        if self.hardware is None:
            y = self._note("正在偵測顯示卡...", x, y, inner)
        elif not self.hardware:
            if state.device.value != "cpu":
                y = self._note("偵測不到支援的顯示卡，會用處理器轉檔", x, y, inner, theme.WARN)
        else:
            vendors = sorted({ops.VENDOR_LABELS[vendor] for vendor, _ in self.hardware.values()})
            codecs = "、".join(F.CODECS[key].label for key in F.CODECS if key in self.hardware)
            y = self._note(f"偵測到 {'、'.join(vendors)} 顯示卡，可以加速 {codecs}", x, y, inner)
        return y + 10

    def draw_footer(self, rect, mouse_pos):
        screen = self.screen
        state = self.state
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        items = state.items
        count = lambda status: sum(item.status == status for item in items)   # noqa: E731
        color = theme.TEXT_DIM
        if self.notice:
            summary_text, color = self.notice, theme.WARN
        elif self.running and self.worker_tab == state.kind:
            running = next((item for item in items if item.status == "running"), None)
            summary_text = f"轉檔中 {count('done') + count('error')} / {len(items)}"
            if running is not None:
                summary_text += f" · {running.path.name} {int(running.progress * 100)}%"
        elif self.running:
            summary_text = f"「{dict(TABS)[self.worker_tab]}」分頁正在轉檔，完成後才能開始"
        elif count("done") or count("error"):
            done = [item for item in items if item.status == "done"]
            summary_text = f"完成 {count('done')} 個"
            if count("error"):
                summary_text += f" · 失敗 {count('error')} 個"
            if done:
                before = sum(item.size for item in done)
                after = sum(item.out_size for item in done)
                summary_text += f" · 合計 {deps.human_size(before)} → {deps.human_size(after)}"
        elif items:
            summary_text = f"共 {len(items)} 個檔案，按「開始轉換」開始處理"
        else:
            summary_text = "拖入檔案後按「開始轉換」"
        text_w = rect.width - 268
        draw_text(screen, widgets.clip_text(summary_text, 13, text_w), (rect.x + 18, rect.y + 18), 13, color)
        draw_text(screen, "輸出位置：output\\media\\", (rect.x + 18, rect.y + 40), 12, theme.TEXT_FAINT)

        side = pygame.Rect(rect.right - 238, rect.y + 18, 104, 38)
        if self.running and self.worker_tab == state.kind:
            self.btn_cancel.draw(screen, side, mouse_pos)
        else:
            self.btn_output.draw(screen, side, mouse_pos)
        self.btn_run.accent = state.accent
        self.btn_run.enabled = not self.running and any(item.readable for item in items)
        self.btn_run.label = "處理中..." if self.running else "開始轉換"
        self.btn_run.draw(screen, pygame.Rect(rect.right - 122, rect.y + 18, 104, 38), mouse_pos)

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, mouse_pos):
        state = self.state
        if state.items and state.list_view.handle_event(event, mouse_pos):
            return
        if state.settings_view.handle_event(event, mouse_pos):
            return
        if event.type == pygame.DROPFILE:
            self.add_files([event.file])
            return
        locked = self.running and self.worker_tab == state.kind
        editable = state.settings_area.collidepoint(mouse_pos) and not locked
        if state.mode.value == "size" and not locked:
            state.target.handle(event, mouse_pos)
        for slider in state.sliders:
            if event.type == pygame.MOUSEBUTTONDOWN and not editable:
                continue
            if slider.handle(event, mouse_pos):
                return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return

        for key, rect in self.tab_rects:
            if rect.collidepoint(mouse_pos):
                if key != self.tab:
                    self.deactivate()
                    self.tab = key
                    self.notice = ""
                return

        if state.list_area.collidepoint(mouse_pos) and not locked:
            for item, rect in state.row_buttons:
                if rect.collidepoint(mouse_pos):
                    with self._lock:
                        if item in state.items:
                            state.items.remove(item)
                    return
        if state.items and not locked and self.btn_clear.clicked(mouse_pos, True):
            with self._lock:
                state.items = []
            state.list_view.scroll = 0
            self.notice = ""
            return
        if editable:
            if state.btn_advanced.clicked(mouse_pos, True):
                self.open_advanced()
                return
            for control in state.controls:
                if control.clicked(mouse_pos, True):
                    self._control_clicked(state, control)
                    return

        if self.running and self.worker_tab == state.kind:
            if self.btn_cancel.clicked(mouse_pos, True):
                self.stop()
        elif self.btn_output.clicked(mouse_pos, True):
            folder = output_dir()
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)
        elif self.btn_run.clicked(mouse_pos, True):
            self.start()

    def _control_clicked(self, state, control):
        if control is state.level:
            state.level_key = state.level.options[state.level.index][0]
            # 點選等級就不再用進階設定裡的自訂品質
            if state.kind == "video":
                state.adv.quality, state.adv.rate_mode = None, "quality"
            else:
                state.adv.audio_bitrate = None
        elif control is state.size_preset:
            state.target.set_text(state.size_preset.value)
        elif control is state.fmt:
            state.adv.codec = ""        # 換格式後原本選的編碼器可能不能用
            state.adv.audio_codec = ""
