"""錄音轉逐字稿:把錄音或影片拖進視窗,用本機語音辨識依序產生字幕檔與純文字逐字稿。"""

import os
import threading
from pathlib import Path

import pygame

from core import corrections, deps, paths, theme, transcribe, widgets
from core.corrections_dialog import CorrectionsDialog
from core.plugins import Page
from core.scroll import BAR_SPACE, ScrollView
from core.widgets import Button, ProgressBar, SegmentedControl, Toggle, draw_text, rounded_panel

ROW_H = 72
MEDIA_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus", ".wma", ".amr",
              ".mp4", ".mkv", ".mov", ".webm", ".avi", ".wmv", ".flv"}
OUTPUTS = [("both", "SRT + TXT"), ("srt", "只要 SRT"), ("txt", "只要 TXT")]
OUTPUT_NOTES = {
    "both": "字幕檔有時間軸，純文字方便閱讀與複製",
    "srt": "有時間軸的字幕檔，可以直接掛在影片上",
    "txt": "沒有時間軸的純文字，方便閱讀與複製",
}
SPEAKER_OPTIONS = [("off", "不區分"), ("0", "自動"), ("2", "2 人"), ("3", "3 人"), ("4", "4 人"), ("5", "5 人")]
SPEAKER_NOTES = {"off": "只轉成文字，不標示是誰說的", "0": "自動判斷人數，聲音相近時可能判錯"}
SPEAKER_NOTES.update({value: "已知人數時選這個，結果最準" for value, _ in SPEAKER_OPTIONS[2:]})
STATUS_TEXT = {"waiting": "等待中", "running": "", "done": "完成", "error": "失敗", "cancelled": "已取消"}


def output_dir():
    return paths.OUTPUT_DIR / "transcripts"


def free_base(folder: Path, stem: str) -> str:
    """找一個 .srt 和 .txt 都還沒被用過的檔名,不覆蓋之前的逐字稿。"""
    base, number = stem, 2
    while (folder / f"{base}.srt").exists() or (folder / f"{base}.txt").exists():
        base = f"{stem} ({number})"
        number += 1
    return base


class Item:
    def __init__(self, path: Path):
        self.path = path
        self.size = path.stat().st_size if path.exists() else 0
        self.status = "waiting"
        self.ratio = None
        self.phase = ""
        self.message = ""
        self.files = []
        self.script = "tw"
        self.cancel_event = threading.Event()


class TranscriptPage(Page):
    def __init__(self, app, tool):
        super().__init__(app, tool)
        accent = tool.accent
        self.items = []
        self.notice = ""
        self._lock = threading.Lock()
        self.worker = None
        self.stop_event = threading.Event()

        self.model = SegmentedControl(transcribe.MODEL_OPTIONS, index=1, accent=accent)
        self.script = SegmentedControl(transcribe.SCRIPT_OPTIONS, accent=accent)
        self.output = SegmentedControl(OUTPUTS, accent=accent)
        self.speakers = SegmentedControl(SPEAKER_OPTIONS, accent=accent)

        self.list_view = ScrollView(accent=accent)
        self.list_area = pygame.Rect(0, 0, 0, 0)
        self.settings_view = ScrollView(accent=accent, indicator=True)
        self.settings_area = pygame.Rect(0, 0, 0, 0)
        self.row_buttons = []
        self.btn_clear_done = Button("清除已結束", filled=False, size=13)
        self.btn_output = Button("輸出資料夾", filled=False, size=14)
        self.btn_cancel = Button("取消", accent=theme.DANGER, filled=False)
        self.btn_run = Button("開始轉錄", accent=accent)

        self.fix_dialog = CorrectionsDialog(lambda: self.screen, accent, self._finished_files,
                                            transcribe.convert_script)
        self.fix_toggle = Toggle(self.fix_dialog.data["enabled"], accent=accent)
        self.btn_fix = Button("編輯規則", filled=False, size=13)

    # ------------------------------------------------------------ 資料

    @property
    def running(self):
        return self.worker is not None and self.worker.is_alive()

    def count(self, status):
        return sum(item.status == status for item in self.items)

    def _finished_files(self):
        return [(path, item.script) for item in list(self.items) if item.status == "done" for path in item.files]

    def add_files(self, raw_paths):
        queued = {item.path for item in self.items if item.status in ("waiting", "running")}
        added = skipped = 0
        with self._lock:
            for raw in raw_paths:
                path = Path(raw)
                if not path.is_file() or path.suffix.lower() not in MEDIA_EXTS:
                    skipped += 1
                    continue
                if path in queued:
                    continue
                self.items.append(Item(path))
                queued.add(path)
                added += 1
        if skipped:
            self.notice = "有檔案不是支援的錄音或影片格式，已略過"
        elif added:
            self.notice = ""

    def start(self):
        if self.running or not self.count("waiting"):
            return
        model = self.model.value
        speakers = None if self.speakers.value == "off" else int(self.speakers.value)
        missing = [dep for dep in transcribe.required(model, speakers) if not dep.installed()]
        if missing:
            self.app.consent.open(self.tool.name, missing, on_done=self.start)
            return
        self.notice = ""
        self.stop_event.clear()
        settings = (model, self.script.value, self.output.value, speakers)
        self.worker = threading.Thread(target=self._work, args=settings, daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        for item in self.items:
            if item.status == "running":
                item.cancel_event.set()

    def _work(self, model, script, output, speakers):
        folder = output_dir()
        while not self.stop_event.is_set():
            with self._lock:
                item = next((i for i in self.items if i.status == "waiting"), None)
                if item is None:
                    return
                item.status, item.phase, item.ratio = "running", "準備中", None
                item.script = script

            def progress(ratio, text, item=item):
                item.ratio, item.phase = ratio, text

            try:
                srt = transcribe.transcribe(item.path, model, script, progress=progress, speakers=speakers,
                                            cancel=item.cancel_event)
                fixed = 0
                rules = corrections.load()
                if rules["enabled"]:
                    srt, fixed = corrections.apply_srt(srt, rules["rules"],
                                                       lambda text: transcribe.convert_script(text, script))
                folder.mkdir(parents=True, exist_ok=True)
                base = free_base(folder, item.path.stem)
                files = []
                if output in ("both", "srt"):
                    files.append(folder / f"{base}.srt")
                    files[-1].write_text(srt, encoding="utf-8")
                if output in ("both", "txt"):
                    files.append(folder / f"{base}.txt")
                    files[-1].write_text(transcribe.srt_to_text(srt), encoding="utf-8")
                item.files = files
                item.message = "已儲存 " + "、".join(f.suffix[1:].upper() for f in files)
                if fixed:
                    item.message += f"，修正 {fixed} 處錯字"
                item.status = "done"
            except deps.Cancelled:
                item.status = "cancelled"
            except Exception as exc:
                item.message = str(exc) or type(exc).__name__
                item.status = "error"

    def deactivate(self):
        self.list_view.reset()
        self.settings_view.reset()

    def update(self):
        self.list_view.update(pygame.mouse.get_pos())
        self.settings_view.update(pygame.mouse.get_pos())
        if self.fix_dialog.is_open:
            self.fix_dialog.update()

    def modal_open(self):
        return self.fix_dialog.is_open

    def draw_modal(self, mouse_pos):
        self.fix_dialog.draw(mouse_pos)

    def handle_modal_event(self, event, mouse_pos):
        self.fix_dialog.handle_event(event, mouse_pos)

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

    def draw_list(self, rect, mouse_pos):
        screen = self.screen
        accent = self.tool.accent
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        draw_text(screen, f"檔案清單 ({len(self.items)})", (rect.x + 16, rect.y + 13), 15, theme.TEXT, bold=True)
        if any(item.status in ("done", "error", "cancelled") for item in self.items):
            self.btn_clear_done.draw(screen, pygame.Rect(rect.right - 106, rect.y + 9, 90, 26), mouse_pos)
        pygame.draw.line(screen, theme.PANEL_EDGE, (rect.x + 12, rect.y + 44), (rect.right - 12, rect.y + 44))

        area = pygame.Rect(rect.x, rect.y + 45, rect.width, rect.height - 45)
        self.list_area = area
        self.row_buttons = []
        if not self.items:
            self.list_view.clear()
            cx, cy = area.centerx, area.centery - 34
            # 簡單的聲波圖示
            for i, h in enumerate((14, 30, 46, 30, 20, 38, 14)):
                x = cx - 36 + i * 12
                pygame.draw.line(screen, theme.PANEL_EDGE, (x, cy - h // 2), (x, cy + h // 2), 4)
            draw_text(screen, "把錄音或影片拖曳到這個視窗", (cx, area.centery + 30), 16, theme.TEXT_DIM, center=True)
            draw_text(screen, "支援 MP3、M4A、WAV、MP4 等，可一次拖多個", (cx, area.centery + 54), 13,
                      theme.TEXT_FAINT, center=True)
            return

        view = self.list_view
        view.layout(area, len(self.items) * ROW_H + 16)
        screen.set_clip(area)
        for index, item in enumerate(list(self.items)):
            y = area.y + 8 + index * ROW_H - view.scroll
            if y + ROW_H < area.y or y > area.bottom:
                continue
            row = pygame.Rect(area.x + 10, y, area.width - 10 - BAR_SPACE, ROW_H - 8)
            hover = row.collidepoint(mouse_pos) and area.collidepoint(mouse_pos)
            rounded_panel(screen, row, theme.PANEL_LIGHT if hover else theme.BG_DEEP, radius=8, alpha=200)
            color = {"done": theme.ACCENT, "error": theme.DANGER, "cancelled": theme.TEXT_FAINT}.get(item.status, accent)
            pygame.draw.rect(screen, color, (row.x + 10, row.y + 12, 3, 22), border_radius=2)
            draw_text(screen, widgets.clip_text(item.path.name, 14, row.width - 64, bold=True), (row.x + 22, row.y + 8),
                      14, theme.TEXT, bold=True)

            if item.status == "running":
                status_w = 230
                bar = pygame.Rect(row.x + 22, row.y + 40, row.width - 22 - 14 - status_w - 10, 10)
                ProgressBar(accent).draw(screen, bar, item.ratio or 0)
                percent = f" {int(item.ratio * 100)}%" if item.ratio is not None else ""
                draw_text(screen, widgets.clip_text(f"{item.phase}{percent}", 12, status_w),
                          (row.right - 14, bar.centery), 12, accent, right=True)
            else:
                text = f"{deps.human_size(item.size)} · {STATUS_TEXT[item.status]}"
                if item.message and item.status in ("done", "error"):
                    text += f"  {item.message}"
                draw_text(screen, widgets.clip_text(text, 12, row.width - 40), (row.x + 22, row.y + 34), 12,
                          theme.TEXT_DIM if item.status == "waiting" else color)

            action = pygame.Rect(row.right - 34, row.y + 8, 26, 26)
            hovered = action.collidepoint(mouse_pos)
            rounded_panel(screen, action, theme.DANGER if hovered else theme.PANEL, radius=6)
            cross = theme.BG_DEEP if hovered else theme.TEXT_DIM
            ax, ay = action.center
            pygame.draw.line(screen, cross, (ax - 5, ay - 5), (ax + 5, ay + 5), 2)
            pygame.draw.line(screen, cross, (ax + 5, ay - 5), (ax - 5, ay + 5), 2)
            self.row_buttons.append(("cancel" if item.status == "running" else "remove", item, action))
        screen.set_clip(None)
        view.draw(screen, mouse_pos)

    def draw_settings(self, rect, mouse_pos):
        screen = self.screen
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        draw_text(screen, "轉錄設定", (rect.x + 18, rect.y + 14), 15, theme.TEXT, bold=True)
        pygame.draw.line(screen, theme.PANEL_EDGE, (rect.x + 12, rect.y + 44), (rect.right - 12, rect.y + 44))
        # 設定項目變多,視窗矮時可以捲動(共用捲動元件)
        view = self.settings_view
        area = pygame.Rect(rect.x, rect.y + 45, rect.width, rect.height - 49)
        self.settings_area = area
        screen.set_clip(area)
        bottom = self._draw_setting_rows(rect.x + 18, area.y + 15 - view.scroll, rect.width - 36,
                                         rect.right, mouse_pos)
        screen.set_clip(None)
        view.layout(area, bottom + view.scroll - area.y + 8)
        view.draw(screen, mouse_pos)

    def _draw_setting_rows(self, x, y, inner, right, mouse_pos):
        """設定欄的內容;回傳內容底部的 y。"""
        screen = self.screen
        locked = self.running

        for title, control, notes in (("辨識模型", self.model, transcribe.MODEL_NOTES),
                                      ("輸出文字", self.script, transcribe.SCRIPT_NOTES),
                                      ("輸出檔案", self.output, OUTPUT_NOTES),
                                      ("區分說話者", self.speakers, SPEAKER_NOTES)):
            draw_text(screen, title, (x, y), 14, theme.TEXT_FAINT if locked else theme.TEXT)
            y += 22
            control.draw(screen, pygame.Rect(x, y, inner, 32), mouse_pos)
            y += 38
            draw_text(screen, widgets.clip_text(notes[control.value], 12, inner), (x, y), 12, theme.TEXT_FAINT)
            y += 26

        # 修正錯字:總開關 + 編輯規則;規則可以在轉錄中修改,會套用到之後完成的檔案
        data = self.fix_dialog.data
        self.fix_toggle.value = data["enabled"]
        draw_text(screen, "修正錯字", (x, y + 3), 14, theme.TEXT)
        self.fix_toggle.draw(screen, (right - 60, y + 2), mouse_pos)
        active = sum(rule["on"] for rule in data["rules"])
        self.btn_fix.label = f"編輯規則({active})"
        self.btn_fix.draw(screen, pygame.Rect(right - 60 - 12 - 112, y - 2, 112, 30), mouse_pos)
        y += 34
        note = ("轉錄後自動把錯字換成正確的字；只要文字相同就會換，詳見編輯規則" if data["enabled"]
                else "已關閉，轉錄結果不會替換任何字")
        draw_text(screen, widgets.clip_text(note, 12, inner), (x, y), 12, theme.TEXT_FAINT)
        y += 28

        pygame.draw.line(screen, theme.PANEL_EDGE, (x, y), (right - 18, y))
        y += 14
        if locked:
            draw_text(screen, "轉錄中無法變更設定", (x, y), 12, theme.WARN)
            y += 22
        for line in ("目前辨識語言為中文", "全部在本地處理，不會上傳"):
            draw_text(screen, line, (x, y), 12, theme.TEXT_FAINT)
            y += 20
        return y

    def draw_footer(self, rect, mouse_pos):
        screen = self.screen
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        if self.notice:
            summary, color = self.notice, theme.WARN
        elif self.items:
            summary = f"轉錄中 {self.count('running')} · 等待 {self.count('waiting')} · 完成 {self.count('done')}"
            color = theme.TEXT_DIM
        else:
            summary, color = "拖入檔案後按「開始轉錄」", theme.TEXT_DIM
        text_w = rect.width - 268
        draw_text(screen, widgets.clip_text(summary, 13, text_w), (rect.x + 18, rect.y + 18), 13, color)
        draw_text(screen, "輸出位置：output\\transcripts\\", (rect.x + 18, rect.y + 40), 12, theme.TEXT_FAINT)

        side = pygame.Rect(rect.right - 238, rect.y + 18, 104, 38)
        if self.running:
            self.btn_cancel.draw(screen, side, mouse_pos)
        else:
            self.btn_output.draw(screen, side, mouse_pos)
        self.btn_run.enabled = not self.running and bool(self.count("waiting"))
        self.btn_run.label = "轉錄中..." if self.running else "開始轉錄"
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
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return

        if self.list_area.collidepoint(mouse_pos):
            for action, item, rect in self.row_buttons:
                if rect.collidepoint(mouse_pos):
                    if action == "cancel":
                        item.cancel_event.set()
                    else:
                        with self._lock:
                            if item in self.items and item.status != "running":
                                self.items.remove(item)
                    return

        if self.btn_clear_done.clicked(mouse_pos, True):
            with self._lock:
                self.items = [i for i in self.items if i.status in ("waiting", "running")]
            return
        # 設定欄捲動後,被捲到看不見的控制項不能被點到
        in_settings = self.settings_area.collidepoint(mouse_pos)
        if in_settings and self.fix_toggle.clicked(mouse_pos, True):
            self.fix_dialog.data["enabled"] = self.fix_toggle.value
            corrections.save(self.fix_dialog.data)
            return
        if in_settings and self.btn_fix.clicked(mouse_pos, True):
            self.fix_dialog.open()
            return
        if not self.running and in_settings:
            for control in (self.model, self.script, self.output, self.speakers):
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
