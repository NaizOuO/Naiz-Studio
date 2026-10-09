"""字幕校對的畫面:上面開檔與輸出,左邊影片(下面是播放列和這一句的編輯區),右邊字幕清單,最下面是波形時間軸。

影片只是用來對時間(邊看邊聽),重點是改逐字稿:改字、調開始結束、分說話者;細緻的剪輯交給剪輯軟體。
進度自動存在 mods_data\\proofread,關掉再開會接著做;Ctrl+Z 復原、Ctrl+Y 重做。
"""

import bisect
import copy
import itertools
import json
import os
import re
import time
from pathlib import Path

import numpy as np
import pygame

from core import paths, theme, widgets, winfile
from core.contextmenu import ContextMenu
from core.dialog import Dialog
from core.files import free_path
from core.plugins import Page
from core.scroll import ScrollView
from core.tasks import TaskRunner
from core.widgets import Button, TextInput, Toggle, draw_text, rounded_panel

from . import media, subs

ROW_H = 44
EDIT_H = 190
HISTORY = 100
SAVE_EVERY = 1.0
DOUBLE_CLICK_MS = 400
EDGE_PX = 7                     # 時間軸上字幕條的邊緣:這麼近就是拖邊緣改時間
SNAP_PX = 9                     # 拖曳時離人聲邊界、播放位置這麼近就貼上去
MIN_SPAN, MAX_SPAN = 3.0, 1800.0
NUDGE = 0.1                     # 「−」「＋」一次移動幾秒
HIDDEN = pygame.Rect(-10000, -10000, 0, 0)
LINE_MARK = "｜"                # 編輯框是一行:字幕裡的換行用這個符號表示
MEDIA_FILTERS = [("影片或聲音", ";".join(f"*{ext}" for ext in sorted(media.VIDEO_EXTS | media.AUDIO_EXTS)))]
SUB_FILTERS = [("字幕檔 (*.srt)", "*.srt")]
PLAYHEAD = (255, 90, 90)
# 輸出:格式和內容分開選,可以自由組合
FORMATS = [("srt", "字幕檔 SRT", "YouTube、剪輯軟體、播放器都能用"),
           ("srv3", "YouTube 彩色字幕 .srv3", "在 YouTube 工作室上傳；電腦版看得到顏色，手機 App 不一定"),
           ("txt", "純文字 TXT", "[時間] 內容，方便閱讀和複製")]
OPTIONS = [("label", "句子前面加標籤", "標籤在下面改，例如把「灰色：」改成「Naiz：」"),
           ("color", "加上顏色", "SRT 用文字顏色標記，有些播放器看得到；DaVinci 讀不到，請用下一項"),
           ("each", "每個說話者分開存", "DaVinci：每人一個檔案，匯入後每人一條字幕軌，在軌道上整條改顏色")]
RULER_H = 18                    # 時間軸上面的刻度列:在這裡拖曳是移動播放位置
SHIFTS = [("-1", -1.0), ("-0.1", -0.1), ("+0.1", 0.1), ("+1", 1.0)]


def output_dir():
    return paths.OUTPUT_DIR / "proofread"


def parse_time(text):
    """「1:02:03.5」「02:03.5」「123.4」都看得懂;看不懂回傳 None。"""
    text = text.strip().replace("，", ".").replace(",", ".").replace("：", ":")
    if not text:
        return None
    try:
        parts = [float(part) for part in text.split(":")]
    except ValueError:
        return None
    if len(parts) > 3 or any(part < 0 for part in parts):
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def _join(a, b):
    """合併兩句的文字:英文字母或數字相接時中間加空白,中文直接接。"""
    if a and b and a[-1].isascii() and a[-1].isalnum() and b[0].isascii() and b[0].isalnum():
        return f"{a} {b}"
    return a + b


def _lighter(color, amount=40):
    return tuple(min(255, c + amount) for c in color)


class ProofreadPage(Page):
    def __init__(self, app, tool):
        super().__init__(app, tool)
        accent = tool.accent
        self.accent = accent
        self._ids = itertools.count(1)
        # 檔案與資料
        self.media_path = None
        self.info = None                    # media.probe 的結果
        self.subtitle_path = None
        self.cues = []                      # 依開始時間排好
        self.speakers = []
        self.sel = set()                    # 選取的字幕(uid)
        self.anchor = None                  # Shift 範圍選取的起點
        self._starts = None
        # 影片與聲音
        self.player = media.Player()
        self.frames = None
        self.wave = None
        self.voice = []
        self.voice_edges = np.zeros(0)
        self.task = TaskRunner()
        self.task_result = None
        self._frame_cache = None
        self._wave_cache = None
        # 時間軸
        self.tl_start, self.tl_span = 0.0, 20.0
        self.tl_area = pygame.Rect(0, 0, 0, 0)
        self.tl_lane = pygame.Rect(0, 0, 0, 0)
        self.snap = Toggle(True, accent=accent)
        self.drag = None
        self.hover_edge = False
        # 元件
        self.btn_media = Button("開啟影片", accent=accent, size=13)
        self.btn_sub = Button("開啟字幕", filled=False, size=13)
        self.btn_undo = Button("復原", filled=False, size=12)
        self.btn_redo = Button("重做", filled=False, size=12)
        self.btn_export = Button("輸出", accent=accent, size=13)
        self.btn_play = Button("播放", filled=False, size=13)
        self.text_in = TextInput("", placeholder="這一句的內容", accent=accent, size=15)
        self.start_in = TextInput("", accent=accent, size=13)
        self.end_in = TextInput("", accent=accent, size=13)
        self.shift_in = TextInput("", placeholder="秒數", accent=accent, size=13)
        self.inputs = [self.text_in, self.start_in, self.end_in, self.shift_in]
        for field in self.inputs:
            field.rect = HIDDEN
        self.edit_buttons = []              # 這一幀畫出來的編輯按鈕:[(Button, 要做的事)]
        self.chip_rects = []                # [(範圍, 說話者 id 或 "add")]
        self.view = ScrollView(accent=accent, wheel_step=ROW_H * 3, marquee=self, indicator=True)
        self.menu = ContextMenu(accent)
        self.dialog = Dialog(lambda: self.screen, accent)
        # 輸出
        self.export_open = False
        self.export_fmt = {key: Toggle(key == "srt", accent) for key, _, _ in FORMATS}
        self.export_opt = {key: Toggle(False, accent) for key, _, _ in OPTIONS}
        self.label_inputs = {}              # 輸出視窗裡每個說話者的標籤:{id: TextInput}
        self._label_session = False         # 這次打開輸出視窗後改過標籤了(只記一次復原點)
        self.btn_do_export = Button("輸出", accent=accent, size=14)
        self.btn_cancel_export = Button("關閉", filled=False, size=14)
        self.btn_open_output = Button("開啟輸出資料夾", filled=False, size=12)
        self.btn_folder = Button("開啟輸出資料夾", filled=False, size=12)
        self.close_rects = {}               # 上面檔名旁邊的「×」:{"media"/"subtitle": 範圍}
        # 狀態
        self.message, self.message_color = "把影片和字幕拖進來，或按上面的按鈕開啟", theme.TEXT_DIM
        self.undo_stack, self.redo_stack = [], []
        self._text_session = None           # 正在改文字的那一句(同一次輸入只記一次復原點)
        self._last_click = (0, None)
        self._last_current = None
        self.list_rect = pygame.Rect(0, 0, 0, 0)
        self.video_rect = pygame.Rect(0, 0, 0, 0)
        self._saved_state = None
        self._saved_at = 0.0
        self._resume_at = 0.0             # 上次播到哪裡(影片準備好後跳過去)
        self._cursor = None
        self._restore_session()

    # ------------------------------------------------------------ 字幕資料

    def _new_uid(self):
        return next(self._ids)

    def _adopt(self, cues):
        for cue in cues:
            cue["uid"] = self._new_uid()
        return cues

    def _changed(self):
        self.cues.sort(key=lambda cue: (cue["start"], cue["end"]))
        self._starts = None
        self._wave_cache = None

    def starts(self):
        if self._starts is None:
            self._starts = [cue["start"] for cue in self.cues]
        return self._starts

    def index_of(self, uid):
        for i, cue in enumerate(self.cues):
            if cue["uid"] == uid:
                return i
        return None

    def cue(self, uid):
        index = self.index_of(uid)
        return None if index is None else self.cues[index]

    def selected(self):
        return [cue for cue in self.cues if cue["uid"] in self.sel]

    def current_at(self, t):
        """t 秒正在顯示的那一句(好幾句重疊時取最後開始的);沒有回傳 None。"""
        i = bisect.bisect_right(self.starts(), t) - 1
        while i >= 0:
            cue = self.cues[i]
            if cue["start"] <= t < cue["end"]:
                return i
            if t - cue["start"] > 30:
                break
            i -= 1
        return None

    def speaker(self, sid):
        return next((s for s in self.speakers if s["id"] == sid), None)

    def color_of(self, cue):
        speaker = self.speaker(cue["speaker"])
        return tuple(speaker["color"]) if speaker else subs.GRAY[1]

    @property
    def duration(self):
        if self.info:
            return self.info["duration"]
        return max((cue["end"] for cue in self.cues), default=0.0) + 5

    # ------------------------------------------------------------ 復原

    def _snapshot(self):
        return copy.deepcopy((self.cues, self.speakers, sorted(self.sel)))

    def remember(self):
        self.undo_stack = (self.undo_stack + [self._snapshot()])[-HISTORY:]
        self.redo_stack = []

    def undo(self, redo=False):
        source, target = (self.redo_stack, self.undo_stack) if redo else (self.undo_stack, self.redo_stack)
        if not source:
            self._say("沒有可以重做的" if redo else "沒有可以復原的")
            return
        self._blur_inputs()
        target.append(self._snapshot())
        self.cues, self.speakers, sel = source.pop()
        self.sel = set(sel)
        self._text_session = None
        self._changed()
        self._sync_inputs()
        self._say("已重做" if redo else "已復原")

    # ------------------------------------------------------------ 開檔

    def _say(self, text, color=theme.TEXT_DIM):
        self.message, self.message_color = text, color

    def add_files(self, files):
        for path in files:
            path = Path(path)
            ext = path.suffix.lower()
            if ext == ".srt":
                self.open_subtitle(path)
            elif ext in media.VIDEO_EXTS or ext in media.AUDIO_EXTS:
                self.open_media(path)
            else:
                self._say(f"不支援 {path.name}：影片、聲音或 .srt 字幕檔才可以", theme.WARN)

    def open_subtitle(self, path, quiet=False):
        try:
            cues, speakers, note = subs.load(path)
        except OSError as exc:
            self._say(f"讀不了字幕：{exc}", theme.WARN)
            return False
        if not cues:
            self._say(f"{Path(path).name} 裡沒有讀到字幕", theme.WARN)
            return False
        self._blur_inputs()
        if self.cues:
            self.remember()                         # 換字幕前的也能 Ctrl+Z 回去
        self.cues, self.speakers = self._adopt(cues), speakers
        self.subtitle_path = Path(path)
        self.sel, self.anchor, self._text_session = set(), None, None
        self._changed()
        self.view.set_scroll(0)
        if not quiet:
            who = f"、{len(speakers)} 位說話者" if len(speakers) > 1 else ""
            self._say(f"讀入 {len(cues)} 句字幕{who}{note}", self.accent)
        return True

    def close_subtitle(self):
        """關掉目前的字幕(Ctrl+Z 可以拿回來),可以再開別的。"""
        if not self.cues and self.subtitle_path is None:
            return
        self._blur_inputs()
        if self.cues:
            self.remember()
        self.cues, self.speakers = [], []
        self.subtitle_path = None
        self.sel, self.anchor = set(), None
        self._changed()
        self._say("已關閉字幕（Ctrl+Z 可以拿回來）；可以再開啟別的字幕檔")

    def close_media(self):
        """關掉目前的影片,可以再開別的。"""
        if self.media_path is None:
            return
        if self.task.running:
            self.task.request_cancel()
        self.task = TaskRunner()
        name = self.media_path.name
        self._close_media()
        self.media_path = None
        self._say(f"已關閉 {name}；字幕還在，可以再開啟別的影片")

    def _matching_subtitle(self, path):
        """影片旁邊或錄音轉逐字稿輸出資料夾裡,同名的 .srt。"""
        for folder in (path.parent, paths.OUTPUT_DIR / "transcripts", paths.OUTPUT_DIR / "subtitles"):
            candidate = folder / f"{path.stem}.srt"
            if candidate.is_file():
                return candidate
        return None

    def open_media(self, path):
        path = Path(path)
        if not path.is_file():
            self._say(f"找不到 {path.name}", theme.WARN)
            return
        if self.task.running:
            self.task.request_cancel()         # 舊的在背景自己結束;結果會因為路徑不同被丟掉
        self.task = TaskRunner()
        self._close_media()
        self.media_path = path
        self.task_result = None
        self._say(f"準備 {path.name}…")
        cache = self.tool.data_dir() / "cache"

        def job(runner):
            info = media.probe(path)
            if not info["audio"]:
                raise RuntimeError("這個檔案沒有聲音")
            runner.report(0, 1000, "抽出聲音")
            sound, wave = media.prepare(path, cache, info["duration"],
                                        lambda r: runner.report(int(r * 1000), 1000, "抽出聲音"),
                                        runner.cancel_event)
            self.task_result = (path, info, sound, wave)

        self.task.start(job)
        if not self.cues:
            found = self._matching_subtitle(path)
            if found is not None:
                self.open_subtitle(found, quiet=True)

    def _close_media(self):
        self.player.unload()
        if self.frames is not None:
            self.frames.close()
        self.frames = None
        self.info = None
        self.wave = None
        self.voice, self.voice_edges = [], np.zeros(0)
        self._frame_cache = self._wave_cache = None

    def _media_ready(self):
        path, info, sound, wave = self.task_result
        self.task_result = None
        if path != self.media_path:
            return
        self.info = info
        self.wave = wave
        self.voice = media.voice_regions(wave)
        self.voice_edges = np.array(sorted({t for region in self.voice for t in region}))
        self.player.load(sound, info["duration"])
        self.player.seek(self._resume_at)
        self._resume_at = 0.0
        if info["video"]:
            self.frames = media.Frames(path, info["video"])
            self.frames.still(self.player.position())
        self.tl_span = min(self.tl_span, max(MIN_SPAN, info["duration"]))
        note = "" if self.player.mixer else "（找不到音效裝置，播放時不會有聲音）"
        subtitle = f"，字幕 {len(self.cues)} 句" if self.cues else "，再開啟字幕檔（.srt）"
        self._say(f"{path.name}：{subs.clock(info['duration'], 0)}{subtitle}{note}",
                  theme.WARN if note else self.accent)

    # ------------------------------------------------------------ 進度存檔

    def _session_path(self):
        return self.tool.data_dir() / "session.json"

    def _session(self):
        return {"說明": "字幕校對的進度（影片、字幕、改過的內容），開啟時自動接著做",
                "media": str(self.media_path) if self.media_path else "",
                "subtitle": str(self.subtitle_path) if self.subtitle_path else "",
                "cues": [{k: cue[k] for k in ("start", "end", "text", "speaker")} for cue in self.cues],
                "speakers": self.speakers,
                "position": round(self.player.position() if self.info else self._resume_at, 2),
                "view": [round(self.tl_start, 2), round(self.tl_span, 2)],
                "export": {key: toggle.value for key, toggle in (self.export_fmt | self.export_opt).items()},
                "snap": self.snap.value}

    def save_session(self, force=False):
        if not force and time.monotonic() - self._saved_at < SAVE_EVERY:
            return
        self._saved_at = time.monotonic()
        try:
            state = json.dumps(self._session(), ensure_ascii=False)
        except (TypeError, ValueError):
            return
        if state == self._saved_state:
            return
        path = self._session_path()
        try:
            temp = path.with_name(path.name + ".tmp")
            temp.write_text(state, encoding="utf-8")
            temp.replace(path)
            self._saved_state = state
        except OSError:
            pass

    def _restore_session(self):
        try:
            data = json.loads(self._session_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        try:
            cues = [{"start": float(c["start"]), "end": float(c["end"]), "text": str(c["text"]),
                     "speaker": c.get("speaker")} for c in data.get("cues", [])]
            speakers = [{"id": int(s["id"]), "color": [int(v) for v in s["color"]][:3],
                         "label": str(s["label"]) if "label" in s else f"{s['name']}："}
                        for s in data.get("speakers", [])]
        except (KeyError, TypeError, ValueError):
            return
        self.cues, self.speakers = self._adopt(cues), subs.ensure_gray(cues, speakers)
        self._changed()
        self.subtitle_path = Path(data["subtitle"]) if data.get("subtitle") else None
        for key, value in (data.get("export") or {}).items():
            toggle = self.export_fmt.get(key) or self.export_opt.get(key)
            if toggle is not None:
                toggle.value = bool(value)
        self.snap.value = bool(data.get("snap", True))
        try:
            self.tl_start, self.tl_span = (float(v) for v in data.get("view", [0, 20]))
        except (TypeError, ValueError):
            pass
        self._resume_at = float(data.get("position") or 0)
        media_path = Path(data["media"]) if data.get("media") else None
        if media_path is not None and media_path.is_file():
            self.open_media(media_path)
            self._say(f"接著上次的進度：{media_path.name}，{len(self.cues)} 句字幕", self.accent)
        elif self.cues:
            missing = f"（找不到影片 {media_path.name}，請重新開啟）" if media_path else ""
            self._say(f"接著上次的進度：{len(self.cues)} 句字幕{missing}", theme.WARN if missing else self.accent)
        self._saved_state = json.dumps(self._session(), ensure_ascii=False)

    # ------------------------------------------------------------ 頁面生命週期

    def update(self):
        if self.task_result is not None:
            self._media_ready()
        done, total, message, _, error, finished = self.task.snapshot()
        if finished and error and not self.task.running and self.info is None and self.media_path is not None:
            if "Cancelled" not in error:
                self._say(f"開不了 {self.media_path.name}：{error.split(': ', 1)[-1]}"[:90], theme.WARN)
            self.media_path = None
            self.task = TaskRunner()
        position = self.player.position()
        if self.player.playing:
            self._follow(position)
        self.save_session()

    def leave(self, proceed):
        self.player.pause()
        self.save_session(force=True)
        proceed()

    def deactivate(self):
        self.player.pause()
        self._blur_inputs()
        self.view.reset()
        self.drag = None
        self.menu.close()
        self._set_cursor(pygame.SYSTEM_CURSOR_ARROW)

    def shutdown(self):
        self.save_session(force=True)
        if self.task.running:
            self.task.request_cancel()
        self._close_media()

    # ------------------------------------------------------------ 播放

    def toggle_play(self):
        if self.info is None:
            self._say("先開啟影片或聲音檔才能播放", theme.WARN)
            return
        if self.player.playing:
            self.player.pause()
            if self.frames:
                self.frames.still(self.player.position())
        else:
            at = self.player.position()
            if at >= self.duration - 0.1:
                at = 0.0
            self.player.play(at)
            if self.frames:
                self.frames.play(at)

    def seek(self, t, play=None):
        if self.info is None:
            return
        t = max(0.0, min(t, self.duration))
        if play and not self.player.playing:
            self.player.play(t)
        else:
            self.player.seek(t)
        if self.frames:
            if self.player.playing:
                self.frames.play(t)
            else:
                self.frames.still(t)

    def _follow(self, position):
        """播放中:時間軸跟著播放位置翻頁,清單捲到正在播的那一句。"""
        if position > self.tl_start + self.tl_span * 0.92 or position < self.tl_start:
            self.tl_start = max(0.0, position - self.tl_span * 0.08)
        current = self.current_at(position)
        if current is not None and current != self._last_current:
            self._last_current = current
            self._scroll_to(current)

    def _scroll_to(self, index):
        if not self.view.rect.height:
            return
        top = index * ROW_H
        if top < self.view.scroll or top + ROW_H > self.view.scroll + self.view.rect.height:
            self.view.set_scroll(top - self.view.rect.height // 3)

    def _show_in_timeline(self, cue):
        if cue["start"] < self.tl_start or cue["end"] > self.tl_start + self.tl_span:
            if cue["end"] - cue["start"] > self.tl_span * 0.8:
                self.tl_span = min(MAX_SPAN, (cue["end"] - cue["start"]) * 1.4)
            self.tl_start = max(0.0, (cue["start"] + cue["end"]) / 2 - self.tl_span / 2)

    # ------------------------------------------------------------ 選取

    def select_only(self, uid, seek=True):
        self._blur_inputs()
        self.sel = {uid} if uid is not None else set()
        self.anchor = uid
        self._sync_inputs()
        cue = self.cue(uid) if uid is not None else None
        if cue is not None:
            self._show_in_timeline(cue)
            if seek:
                self.seek(cue["start"])

    def _toggle(self, uid):
        self._blur_inputs()
        self.sel ^= {uid}
        self.anchor = uid
        self._sync_inputs()

    def _range_to(self, uid):
        self._blur_inputs()
        a = self.index_of(self.anchor) if self.anchor is not None else None
        b = self.index_of(uid)
        if a is None or b is None:
            self.select_only(uid)
            return
        lo, hi = sorted((a, b))
        self.sel = {cue["uid"] for cue in self.cues[lo:hi + 1]}
        self._sync_inputs()

    def step_selection(self, direction):
        if not self.cues:
            return
        chosen = [self.index_of(uid) for uid in self.sel]
        chosen = [i for i in chosen if i is not None]
        if chosen:
            index = (max(chosen) if direction > 0 else min(chosen)) + direction
        else:
            current = self.current_at(self.player.position())
            index = current if current is not None else 0
        index = max(0, min(len(self.cues) - 1, index))
        self.select_only(self.cues[index]["uid"])
        self._scroll_to(index)

    def _blur_inputs(self):
        for field in self.inputs:
            was = field.focused
            field.blur()
            if was:
                self._commit_field(field)
        self._text_session = None

    def _sync_inputs(self):
        chosen = self.selected()
        if len(chosen) == 1:
            cue = chosen[0]
            if not self.text_in.focused:
                self.text_in.set_text(cue["text"].replace("\n", LINE_MARK))
            if not self.start_in.focused:
                self.start_in.set_text(subs.clock(cue["start"], 2))
            if not self.end_in.focused:
                self.end_in.set_text(subs.clock(cue["end"], 2))

    def _commit_field(self, field):
        chosen = self.selected()
        if len(chosen) != 1 or field in (self.text_in, self.shift_in):
            return
        cue = chosen[0]
        value = parse_time(field.text)
        key = "start" if field is self.start_in else "end"
        if value is None:
            self._say("時間看不懂：可以打 12:41.3 或 1:02:41.3", theme.WARN)
        elif abs(value - cue[key]) > 0.0005:
            if (key == "start" and value >= cue["end"] - 0.05) or (key == "end" and value <= cue["start"] + 0.05):
                self._say("開始要比結束早", theme.WARN)
            else:
                self.remember()
                cue[key] = value
                self._changed()
        self._sync_inputs()

    # 以下三個給 ScrollView 的框選呼叫(右邊字幕清單)

    def _row_at(self, content_y):
        index = int(content_y // ROW_H)
        return index if 0 <= index < len(self.cues) else None

    def marquee_begin(self, content_pos):
        ctrl = pygame.key.get_mods() & pygame.KMOD_CTRL
        return {"index": self._row_at(content_pos[1]), "base": set(self.sel) if ctrl else set()}

    def marquee_update(self, state, a, b):
        lo = max(0, int(min(a[1], b[1]) // ROW_H))
        hi = min(len(self.cues) - 1, int(max(a[1], b[1]) // ROW_H))
        self._blur_inputs()
        self.sel = state["base"] | {cue["uid"] for cue in self.cues[lo:hi + 1]}
        self._sync_inputs()

    def marquee_click(self, state):
        index = state["index"]
        if index is None:
            self._blur_inputs()
            self.sel = set()
            return
        uid = self.cues[index]["uid"]
        mods = pygame.key.get_mods()
        now = pygame.time.get_ticks()
        double = self._last_click[1] == uid and now - self._last_click[0] < DOUBLE_CLICK_MS
        self._last_click = (now, uid)
        if mods & pygame.KMOD_CTRL:
            self._toggle(uid)
        elif mods & pygame.KMOD_SHIFT:
            self._range_to(uid)
        elif double:
            self.select_only(uid, seek=False)
            self.seek(self.cues[index]["start"], play=True)
        else:
            self.select_only(uid)

    # ------------------------------------------------------------ 修改字幕

    def shift(self, dt):
        """選取的句子一起前後移動;沒有選取時整份字幕一起移動。"""
        chosen = self.selected() or self.cues
        if not chosen or not dt:
            return
        dt = max(dt, -min(cue["start"] for cue in chosen))
        if abs(dt) < 0.0005:
            self._say("已經在影片最前面，不能再往前", theme.WARN)
            return
        self.remember()
        for cue in chosen:
            cue["start"] += dt
            cue["end"] += dt
        self._changed()
        self._sync_inputs()
        who = f"選取的 {len(chosen)} 句" if self.sel else "整份字幕"
        self._say(f"{who}{'往後' if dt > 0 else '往前'}移 {subs.clock(abs(dt), 2) if abs(dt) >= 60 else f'{abs(dt):.2f} 秒'}")

    def shift_typed(self):
        """照輸入框的秒數移動(可以打 -3.5、+12、1:02.5)。"""
        text = self.shift_in.text.strip()
        sign = -1 if text.startswith(("-", "−", "－")) else 1
        value = parse_time(text.lstrip("+-−－＋ "))
        if value is None:
            self._say("秒數看不懂：往後打 3.5，往前打 -3.5，也可以打 1:02.5", theme.WARN)
            return
        self.shift(sign * value)

    def nudge(self, key, dt):
        chosen = self.selected()
        if len(chosen) != 1:
            return
        cue = chosen[0]
        value = cue[key] + dt
        if key == "start" and not 0 <= value < cue["end"] - 0.05:
            return
        if key == "end" and value <= cue["start"] + 0.05:
            return
        self.remember()
        cue[key] = value
        self._changed()
        self._sync_inputs()

    def set_edge_here(self, key):
        chosen = self.selected()
        if len(chosen) != 1 or self.info is None:
            self._say("先選一句字幕，再把影片播到要的位置", theme.WARN)
            return
        cue, at = chosen[0], self.player.position()
        if (key == "start" and at >= cue["end"] - 0.05) or (key == "end" and at <= cue["start"] + 0.05):
            self._say("播放位置在這一句的另一邊：開始要比結束早", theme.WARN)
            return
        self.remember()
        cue[key] = at
        self._changed()
        self._sync_inputs()
        self._say(f"{'開始' if key == 'start' else '結束'}改成 {subs.clock(at, 2)}")

    def split(self):
        """切成兩句:在播放位置切開時間;正在打字時文字從游標處分開,否則照時間比例分。"""
        chosen = self.selected()
        at = self.player.position()
        if len(chosen) == 1:
            cue = chosen[0]
        else:
            index = self.current_at(at)
            cue = self.cues[index] if index is not None else None
        if cue is None:
            self._say("選一句字幕，或把影片播到一句字幕的中間再切開", theme.WARN)
            return
        if not cue["start"] + 0.1 < at < cue["end"] - 0.1:
            at = (cue["start"] + cue["end"]) / 2
        text = cue["text"]
        if self.text_in.focused and self.selected() == [cue]:
            cut = len(self.text_in.text[:self.text_in.cursor].replace(LINE_MARK, "\n"))
        else:
            cut = round(len(text) * (at - cue["start"]) / (cue["end"] - cue["start"]))
        cut = max(0, min(len(text), cut))
        self._blur_inputs()
        self.remember()
        second = {"start": at, "end": cue["end"], "text": text[cut:].lstrip(), "speaker": cue["speaker"],
                  "uid": self._new_uid()}
        cue["end"], cue["text"] = at, text[:cut].rstrip()
        self.cues.append(second)
        self._changed()
        self.sel, self.anchor = {second["uid"]}, second["uid"]
        self._sync_inputs()
        self._say("切成兩句了；文字分得不對的話直接在下面改")

    def merge(self):
        chosen = self.selected()
        if len(chosen) == 1:
            index = self.index_of(chosen[0]["uid"])
            if index + 1 >= len(self.cues):
                self._say("這是最後一句，沒有下一句可以合併", theme.WARN)
                return
            chosen = [chosen[0], self.cues[index + 1]]
        if len(chosen) < 2:
            self._say("選兩句以上（Ctrl 或 Shift 加選），或選一句和下一句合併", theme.WARN)
            return
        self._blur_inputs()
        self.remember()
        first = chosen[0]
        for other in chosen[1:]:
            first["text"] = _join(first["text"], other["text"])
            first["end"] = max(first["end"], other["end"])
        gone = {cue["uid"] for cue in chosen[1:]}
        self.cues = [cue for cue in self.cues if cue["uid"] not in gone]
        self._changed()
        self.sel, self.anchor = {first["uid"]}, first["uid"]
        self._sync_inputs()
        self._say(f"{len(chosen)} 句合併成一句")

    def delete(self):
        chosen = self.selected()
        if not chosen:
            return
        self._blur_inputs()
        self.remember()
        index = self.index_of(chosen[0]["uid"])
        self.cues = [cue for cue in self.cues if cue["uid"] not in self.sel]
        self._changed()
        self.sel = set()
        if self.cues and index is not None:
            nxt = self.cues[min(index, len(self.cues) - 1)]
            self.sel, self.anchor = {nxt["uid"]}, nxt["uid"]
            self._sync_inputs()
        self._say(f"刪除 {len(chosen)} 句（Ctrl+Z 可以復原）")

    def add_here(self, at=None):
        """在播放位置(或指定的時間)新增一句空白字幕,直接開始打字。"""
        at = self.player.position() if at is None else at
        index = bisect.bisect_right(self.starts(), at)
        end = at + 2.0
        if index < len(self.cues):
            end = min(end, max(at + 0.5, self.cues[index]["start"]))
        speaker = None
        chosen = self.selected()
        if chosen:
            speaker = chosen[0]["speaker"]
        self._blur_inputs()
        self.remember()
        cue = {"start": at, "end": end, "text": "", "speaker": speaker, "uid": self._new_uid()}
        self.cues.append(cue)
        self._changed()
        self.select_only(cue["uid"], seek=False)
        self._text_session = cue["uid"]          # 已經記過復原點
        self.text_in.focus()
        self._scroll_to(self.index_of(cue["uid"]))
        self._say("新增了一句，直接打字；Enter 結束")

    def assign(self, sid):
        chosen = self.selected()
        if not chosen:
            self._say("先選字幕，再點說話者", theme.WARN)
            return
        if all(cue["speaker"] == sid for cue in chosen):
            return
        self.remember()
        for cue in chosen:
            cue["speaker"] = sid
        speaker = self.speaker(sid)
        self._say(f"{len(chosen)} 句改成「{speaker['label'].rstrip('：:') if speaker else ''}」")

    def add_speaker(self):
        self.remember()
        speaker = subs.new_speaker(self.speakers)
        self.speakers.append(speaker)
        if self.sel:
            self.assign(speaker["id"])
            self.undo_stack.pop()                   # 和新增算同一步
        self._say(f"新增說話者「{speaker['label']}」；右鍵可以改標籤和顏色，輸出時也能改標籤")

    def rename_speaker(self, sid):
        speaker = self.speaker(sid)
        if speaker is None:
            return

        def done(key, text):
            if key == "ok" and text.strip() and text != speaker["label"]:
                self.remember()
                speaker["label"] = text.strip()[:40]

        self.dialog.open("說話者的標籤", ["輸出時加在句子前面，冒號也算在裡面，例如「Naiz：」"],
                         [("cancel", "取消", False), ("ok", "確定", True)], done, field="標籤",
                         value=speaker["label"])

    def recolor(self, sid, color):
        speaker = self.speaker(sid)
        if speaker is not None and list(color) != speaker["color"]:
            self.remember()
            if speaker["label"] == subs.default_label(speaker["color"]):
                speaker["label"] = subs.default_label(color)      # 還是預設的「顏色：」就跟著改
            speaker["color"] = list(color)
            self._wave_cache = None

    def remove_speaker(self, sid):
        self.remember()
        self.speakers = [s for s in self.speakers if s["id"] != sid]
        count = sum(1 for cue in self.cues if cue["speaker"] == sid)
        subs.ensure_gray(self.cues, self.speakers)
        self._say(f"刪除這位說話者；{count} 句改回「灰色」")

    def _speaker_menu(self, pos, sid):
        speaker = self.speaker(sid)
        gray = tuple(speaker["color"]) == subs.GRAY[1]
        items = [("改標籤", "", True, lambda: self.rename_speaker(sid))]
        for name, rgb in subs.COLORS + [subs.GRAY]:
            items.append((f"改成{name}", "", list(rgb) != speaker["color"], lambda rgb=rgb: self.recolor(sid, rgb)))
        items.append(("刪除這位說話者", "", not gray, lambda: self.remove_speaker(sid)))
        self.menu.open(pos, items)

    def _cue_menu(self, pos):
        many = len(self.sel) > 1
        items = [("播放這句", "雙擊", not many and self.info is not None,
                  lambda: self.seek(self.selected()[0]["start"], play=True)),
                 ("在播放位置切開", "S", not many, self.split),
                 ("合併" if many else "和下一句合併", "M", True, self.merge),
                 ("刪除", "Delete", True, self.delete)]
        for speaker in self.speakers[:8]:
            items.append((f"改成「{speaker['label'].rstrip('：:')}」", "", True,
                          lambda sid=speaker["id"]: self.assign(sid)))
        self.menu.open(pos, items)

    # ------------------------------------------------------------ 輸出

    def open_export(self):
        if not self.cues:
            self._say("還沒有字幕：先開啟字幕檔（.srt）", theme.WARN)
            return
        self._blur_inputs()
        self.label_inputs = {s["id"]: TextInput(s["label"], placeholder="標籤", accent=self.accent, size=13)
                             for s in self.speakers}
        self._label_session = False
        self.export_open = True

    def _label_changed(self, sid, text):
        speaker = self.speaker(sid)
        if speaker is None or speaker["label"] == text:
            return
        if not self._label_session:
            self.remember()
            self._label_session = True
        speaker["label"] = text

    def open_folder(self):
        folder = output_dir()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def export(self):
        formats = [key for key, _, _ in FORMATS if self.export_fmt[key].value]
        if not formats:
            self._say("至少選一種要輸出的格式", theme.WARN)
            return
        cues = [cue for cue in self.cues if cue["text"].strip()]
        if not cues:
            self._say("沒有可以輸出的字幕", theme.WARN)
            return
        label, color, each = (self.export_opt[key].value for key in ("label", "color", "each"))
        stem = re.sub(r"[\\/:*?\"<>|]", "_", (self.subtitle_path or self.media_path or Path("字幕")).stem)
        groups = [(f"_{subs.file_name(s['label'])}", mine) for s, mine in subs.split_by_speaker(cues, self.speakers)] \
            if each else [("", cues)]
        folder = output_dir()
        written = []
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for suffix, mine in groups:
                for fmt in formats:
                    if fmt == "srt":
                        content = subs.to_srt(mine, self.speakers, label, color)
                    elif fmt == "srv3":
                        content = subs.to_srv3(mine, self.speakers, label, color)
                    else:
                        content = subs.to_txt(mine, self.speakers, label, f"{stem}{suffix}（字幕校對）")
                    target = free_path(folder, f"{stem}{suffix}", f".{fmt}")
                    target.write_text(content, encoding="utf-8")
                    written.append(target)
        except OSError as exc:
            self._say(f"存不了：{exc}"[:90], theme.WARN)
            return
        self.export_open = False
        self._say(f"輸出 {len(written)} 個檔案到 output\\proofread（上面「開啟輸出資料夾」可以打開）", self.accent)

    def _preview(self):
        """輸出視窗裡的範例:第一句照目前的選項會長怎樣。"""
        cue = next((c for c in self.cues if c["text"].strip()), None)
        if cue is None:
            return ""
        speaker = self.speaker(cue["speaker"])
        text = cue["text"].strip().replace("\n", " ／ ")
        if self.export_opt["label"].value and speaker is not None:
            text = speaker["label"] + text
        return text

    # ------------------------------------------------------------ 彈出視窗

    def modal_open(self):
        return self.dialog.is_open or self.export_open

    def draw_modal(self, mouse_pos):
        if self.dialog.is_open:
            self.dialog.draw(mouse_pos)
        elif self.export_open:
            self._draw_export(mouse_pos)

    def handle_modal_event(self, event, pos):
        if self.dialog.is_open:
            self.dialog.handle_event(event, pos)
            return
        typing = next((sid for sid, field in self.label_inputs.items() if field.focused), None)
        for sid, field in self.label_inputs.items():
            if field.handle(event, pos):
                self._label_changed(sid, field.text)
        if typing is not None and event.type in (pygame.KEYDOWN, pygame.TEXTINPUT, pygame.TEXTEDITING):
            return
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.export_open = False
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.export()
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.btn_cancel_export.clicked(pos, True):
                self.export_open = False
            elif self.btn_do_export.clicked(pos, True):
                self.export()
            elif self.btn_open_output.clicked(pos, True):
                self.open_folder()
            else:
                for toggle in (self.export_fmt | self.export_opt).values():
                    if toggle.clicked(pos, True):
                        break

    def _draw_export(self, mouse_pos):
        screen = self.screen
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 170))
        screen.blit(veil, (0, 0))
        shown = self.speakers[:8]
        columns = 2 if len(shown) > 3 else 1
        rows = (len(shown) + columns - 1) // columns
        panel = pygame.Rect(0, 0, 640, 0)
        panel.height = 64 + 26 + len(FORMATS) * 46 + 34 + len(OPTIONS) * 46 + 34 + rows * 42 + 30 + 76
        panel.height = min(panel.height, height - 20)
        panel.center = (width // 2, height // 2)
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.PANEL_EDGE)
        x, y = panel.x + 24, panel.y + 20
        inner = panel.width - 48
        draw_text(screen, "輸出字幕", (x, y), 17, theme.TEXT, bold=True)
        draw_text(screen, "格式和內容可以自由組合；存到 output\\proofread，不會蓋掉原本的字幕檔", (x, y + 30), 12,
                  theme.TEXT_DIM)
        y += 64

        def section(title, y):
            draw_text(screen, title, (x, y), 13, theme.TEXT, bold=True)
            return y + 26

        def toggles(items, store, y):
            for key, title, note in items:
                toggle = store[key]
                toggle.draw(screen, (x, y + 2), mouse_pos)
                draw_text(screen, title, (x + 54, y), 14, theme.TEXT if toggle.value else theme.TEXT_DIM)
                draw_text(screen, widgets.clip_text(note, 12, inner - 54), (x + 54, y + 21), 12, theme.TEXT_FAINT)
                y += 46
            return y

        y = toggles(FORMATS, self.export_fmt, section("格式（可以選好幾種）", y)) + 8
        y = toggles(OPTIONS, self.export_opt, section("內容", y)) + 8
        y = section("說話者的標籤（冒號也算在裡面）", y)
        col_w = inner // columns
        for i, speaker in enumerate(shown):
            cx, cy = x + (i % columns) * col_w, y + (i // columns) * 42
            pygame.draw.circle(screen, tuple(speaker["color"]), (cx + 8, cy + 17), 7)
            field = self.label_inputs.get(speaker["id"])
            if field is not None:
                field.draw(screen, pygame.Rect(cx + 24, cy, col_w - 110, 34), mouse_pos)
            count = sum(1 for cue in self.cues if cue["speaker"] == speaker["id"])
            draw_text(screen, f"{count} 句", (cx + col_w - 80, cy + 9), 12, theme.TEXT_FAINT)
        y += rows * 42 + 4
        preview = self._preview()
        if preview:
            draw_text(screen, widgets.clip_text(f"第一句會是：{preview}", 12, inner), (x, y + 4), 12, theme.TEXT_DIM)
        foot = panel.bottom - 56
        self.btn_open_output.draw(screen, pygame.Rect(x, foot, 130, 36), mouse_pos)
        self.btn_do_export.draw(screen, pygame.Rect(panel.right - 24 - 100, foot, 100, 36), mouse_pos)
        self.btn_cancel_export.draw(screen, pygame.Rect(panel.right - 24 - 210, foot, 100, 36), mouse_pos)

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, pos):
        if event.type == pygame.DROPFILE:
            self.add_files([event.file])
            return
        if self.menu.handle_event(event, pos):
            return
        typing = any(field.focused for field in self.inputs)
        if event.type == pygame.KEYDOWN and not typing and self._key(event):
            return
        if event.type == pygame.KEYDOWN and typing and event.key == pygame.K_ESCAPE:
            self._blur_inputs()
            return
        if event.type == pygame.KEYDOWN and self.shift_in.focused and event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.shift_in.blur()
            self.shift_typed()
            return
        # 輸入框
        for field in self.inputs:
            was = field.focused
            changed = field.handle(event, pos)
            if field is self.text_in and changed:
                self._text_changed()
            if was and not field.focused:
                self._commit_field(field)
                if field is self.text_in:
                    self._text_session = None
            if field.focused and event.type in (pygame.KEYDOWN, pygame.TEXTINPUT, pygame.TEXTEDITING):
                return
        # 右邊清單
        in_list = self.list_rect.collidepoint(pos)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3 and in_list:
            index = self._row_at(self.view.to_content(pos)[1])
            if index is not None:
                if self.cues[index]["uid"] not in self.sel:
                    self.select_only(self.cues[index]["uid"], seek=False)
                self._cue_menu(pos)
            return
        if self.view.grabbing(pos) or self.view.drag or in_list:
            if self.view.handle_event(event, pos):
                return
        # 時間軸
        if self.tl_area.collidepoint(pos) or self.drag is not None:
            if self._timeline_event(event, pos):
                return
        # 影片:點一下播放/暫停
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.video_rect.collidepoint(pos):
            self.toggle_play()
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            for rect, sid in self.chip_rects:
                if rect.collidepoint(pos) and sid not in ("add", None):
                    self._speaker_menu(pos, sid)
                    return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for key, rect in self.close_rects.items():
                if rect.collidepoint(pos):
                    self.close_media() if key == "media" else self.close_subtitle()
                    return
            self._click(pos)

    def _text_changed(self):
        chosen = self.selected()
        if len(chosen) != 1:
            return
        cue = chosen[0]
        if self._text_session != cue["uid"]:
            self.remember()
            self._text_session = cue["uid"]
        cue["text"] = self.text_in.text.replace(LINE_MARK, "\n")

    def _key(self, event):
        """沒有在打字時的快捷鍵;回傳 True 代表用掉了。"""
        ctrl = event.mod & pygame.KMOD_CTRL
        shift = event.mod & pygame.KMOD_SHIFT
        key = event.key
        scancode = getattr(event, "scancode", 0)
        if ctrl and (key == pygame.K_z or scancode == 29):
            self.undo(redo=bool(shift))
        elif ctrl and (key == pygame.K_y or scancode == 28):
            self.undo(redo=True)
        elif ctrl and key == pygame.K_a:
            self.sel = {cue["uid"] for cue in self.cues}
            self._sync_inputs()
        elif ctrl and key == pygame.K_e:
            self.open_export()
        elif key == pygame.K_SPACE:
            self.toggle_play()
        elif key in (pygame.K_UP, pygame.K_DOWN):
            self.step_selection(-1 if key == pygame.K_UP else 1)
        elif key in (pygame.K_LEFT, pygame.K_RIGHT):
            step = (5.0 if shift else 1.0) * (-1 if key == pygame.K_LEFT else 1)
            self.seek(self.player.position() + step)
        elif key == pygame.K_LEFTBRACKET:
            self.set_edge_here("start")
        elif key == pygame.K_RIGHTBRACKET:
            self.set_edge_here("end")
        elif key == pygame.K_s and not ctrl:
            self.split()
        elif key == pygame.K_m and not ctrl:
            self.merge()
        elif key == pygame.K_n and not ctrl:
            self.add_here()
        elif key == pygame.K_DELETE:
            self.delete()
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER) and len(self.sel) == 1:
            self.text_in.focus()
            self.text_in.cursor = self.text_in.anchor = len(self.text_in.text)
        elif key == pygame.K_ESCAPE and self.sel:
            self._clear_sel()
        else:
            return False
        return True

    def _click(self, pos):
        if self.btn_media.clicked(pos, True):
            path = winfile.ask_open("開啟影片或聲音", MEDIA_FILTERS)
            if path:
                self.open_media(path)
        elif self.btn_sub.clicked(pos, True):
            initial = str(self.media_path.parent) if self.media_path else None
            path = winfile.ask_open("開啟字幕檔", SUB_FILTERS, initial)
            if path:
                self.open_subtitle(path)
        elif self.btn_undo.clicked(pos, True):
            self.undo()
        elif self.btn_redo.clicked(pos, True):
            self.undo(redo=True)
        elif self.btn_export.clicked(pos, True):
            self.open_export()
        elif self.btn_folder.clicked(pos, True):
            self.open_folder()
        elif self.btn_play.clicked(pos, True):
            self.toggle_play()
        elif self.snap.clicked(pos, True):
            self._say("拖曳時間會貼齊有人聲的邊界" if self.snap.value else "不貼齊，自由拖曳（按住 Alt 也可以暫時不貼）")
        else:
            for button, action in self.edit_buttons:
                if button.clicked(pos, True):
                    action()
                    return
            for rect, sid in self.chip_rects:
                if rect.collidepoint(pos):
                    if sid == "add":
                        self.add_speaker()
                    else:
                        self.assign(sid)
                    return

    # ------------------------------------------------------------ 時間軸

    def t_to_x(self, t):
        area = self.tl_area
        return area.x + (t - self.tl_start) / self.tl_span * area.width

    def x_to_t(self, x):
        area = self.tl_area
        return self.tl_start + (x - area.x) / max(1, area.width) * self.tl_span

    def _clamp_view(self):
        self.tl_span = max(MIN_SPAN, min(MAX_SPAN, self.tl_span))
        self.tl_start = max(0.0, min(self.tl_start, max(0.0, self.duration - self.tl_span * 0.5)))

    def _block_at(self, pos):
        """時間軸上滑鼠指到的字幕條:(第幾句, "start"/"end"/"body") 或 None。"""
        if not self.tl_lane.collidepoint(pos):
            return None
        best = None
        for i in range(len(self.cues) - 1, -1, -1):
            cue = self.cues[i]
            x1, x2 = self.t_to_x(cue["start"]), self.t_to_x(cue["end"])
            if x2 < self.tl_area.x - EDGE_PX or x1 > self.tl_area.right + EDGE_PX:
                continue
            if abs(pos[0] - x1) <= EDGE_PX and x2 - x1 > EDGE_PX:
                return i, "start"
            if abs(pos[0] - x2) <= EDGE_PX:
                return i, "end"
            if x1 <= pos[0] <= x2 and best is None:
                best = (i, "body")
        return best

    def _snap(self, t, skip_snap=False):
        if not self.snap.value or skip_snap:
            return t
        limit = SNAP_PX / max(1, self.tl_area.width) * self.tl_span
        candidates = [self.player.position()] if self.info else []
        if len(self.voice_edges):
            i = int(np.searchsorted(self.voice_edges, t))
            candidates += [float(v) for v in self.voice_edges[max(0, i - 1):i + 1]]
        best = min(candidates, key=lambda c: abs(c - t), default=None)
        return best if best is not None and abs(best - t) <= limit else t

    def _timeline_event(self, event, pos):
        area = self.tl_area
        if event.type == pygame.MOUSEWHEEL and area.collidepoint(pos):
            if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                self.tl_start -= event.y * self.tl_span * 0.15
            else:
                anchor = self.x_to_t(pos[0])
                self.tl_span *= 0.8 if event.y > 0 else 1.25
                self._clamp_view()
                self.tl_start = anchor - (pos[0] - area.x) / max(1, area.width) * self.tl_span
            self._clamp_view()
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button in (2, 3) and area.collidepoint(pos):
            self.drag = {"kind": "pan", "x": pos[0], "start": self.tl_start, "button": event.button, "moved": False}
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and area.collidepoint(pos):
            self._blur_inputs()
            hit = self._block_at(pos)
            near_head = self.info is not None and abs(pos[0] - self.t_to_x(self.player.position())) <= 5
            if hit is None and (pos[1] < area.y + RULER_H or near_head):
                self.drag = {"kind": "scrub"}
                self.seek(self.x_to_t(pos[0]))
                return True
            if hit is None:
                ctrl = pygame.key.get_mods() & pygame.KMOD_CTRL
                self.drag = {"kind": "box", "x": pos[0], "now": pos[0], "moved": False,
                             "base": set(self.sel) if ctrl else set()}
                return True
            index, part = hit
            cue = self.cues[index]
            mods = pygame.key.get_mods()
            if part == "body" and mods & pygame.KMOD_CTRL:
                self._toggle(cue["uid"])
                return True
            was_selected = cue["uid"] in self.sel
            if part != "body" or not was_selected:
                self.sel, self.anchor = {cue["uid"]}, cue["uid"]
                self._sync_inputs()
                self._scroll_to(index)
            moving = self.selected() if part == "body" else [cue]
            self.drag = {"kind": part, "uid": cue["uid"], "x": pos[0], "moved": False, "remembered": False,
                         "was_selected": was_selected,
                         "orig": {c["uid"]: (c["start"], c["end"]) for c in moving}}
            return True
        if self.drag is None:
            if event.type == pygame.MOUSEMOTION:
                hit = self._block_at(pos)
                self.hover_edge = hit is not None and hit[1] != "body"
            return False
        drag = self.drag
        if event.type == pygame.MOUSEMOTION:
            if drag["kind"] == "pan":
                if abs(pos[0] - drag["x"]) > 3:
                    drag["moved"] = True
                self.tl_start = drag["start"] - (pos[0] - drag["x"]) / max(1, area.width) * self.tl_span
                self._clamp_view()
            elif drag["kind"] == "scrub":
                self.seek(self.x_to_t(pos[0]))
            elif drag["kind"] == "box":
                self._box_to(drag, pos[0])
            else:
                self._drag_cue(drag, pos)
            return True
        if event.type == pygame.MOUSEBUTTONUP:
            self.drag = None
            if drag["kind"] == "pan":
                if not drag["moved"] and drag["button"] == 3:
                    self._timeline_menu(pos)
            elif drag["kind"] == "box" and not drag["moved"]:
                self.seek(self.x_to_t(pos[0]))          # 只是點一下:跳到那裡(選取不變,才能接著按 [ ])
            elif drag["kind"] == "body" and not drag["moved"]:
                cue = self.cue(drag["uid"])
                if cue is not None and drag["was_selected"]:
                    self.sel, self.anchor = {cue["uid"]}, cue["uid"]
                    self._sync_inputs()
            elif drag["kind"] in ("start", "end", "body") and drag["moved"]:
                self._changed()
                self._sync_inputs()
            return True
        return False

    def _box_to(self, drag, x):
        """框選:框到的時間範圍內有碰到的句子都選起來。"""
        drag["now"] = x
        if not drag["moved"] and abs(x - drag["x"]) < 5:
            return
        drag["moved"] = True
        t1, t2 = sorted((self.x_to_t(drag["x"]), self.x_to_t(x)))
        self._blur_inputs()
        self.sel = drag["base"] | {cue["uid"] for cue in self.cues if cue["end"] > t1 and cue["start"] < t2}
        self._sync_inputs()
        if self.sel:
            self._scroll_to(min(i for i, cue in enumerate(self.cues) if cue["uid"] in self.sel))

    def _drag_cue(self, drag, pos):
        dx = pos[0] - drag["x"]
        if not drag["moved"] and abs(dx) < 3:
            return
        if not drag["remembered"]:
            self.remember()
            drag["remembered"] = True
        drag["moved"] = True
        dt = dx / max(1, self.tl_area.width) * self.tl_span
        free = bool(pygame.key.get_mods() & pygame.KMOD_ALT)
        cue = self.cue(drag["uid"])
        if cue is None:
            return
        start0, end0 = drag["orig"][cue["uid"]]
        if drag["kind"] == "start":
            cue["start"] = min(max(0.0, self._snap(start0 + dt, free)), cue["end"] - subs.MIN_LENGTH)
        elif drag["kind"] == "end":
            cue["end"] = max(min(self.duration, self._snap(end0 + dt, free)), cue["start"] + subs.MIN_LENGTH)
        else:
            # 整段移動:拿被拖的這句的開始去貼齊,其他選取的跟著一起移
            target = self._snap(start0 + dt, free)
            dt = max(target - start0, -min(s for s, _ in drag["orig"].values()))
            for uid, (s, e) in drag["orig"].items():
                other = self.cue(uid)
                if other is not None:
                    other["start"], other["end"] = s + dt, e + dt
        self._starts = None
        self._sync_inputs()

    def _timeline_menu(self, pos):
        at = self.x_to_t(pos[0])
        hit = self._block_at(pos)
        items = [("在這裡新增一句", "N", True, lambda: self.add_here(at)),
                 ("播放這裡", "", self.info is not None, lambda: self.seek(at, play=True))]
        if hit is not None:
            uid = self.cues[hit[0]]["uid"]
            if uid not in self.sel:
                self.select_only(uid, seek=False)
            items += [("在播放位置切開", "S", len(self.sel) == 1, self.split),
                      ("合併" if len(self.sel) > 1 else "和下一句合併", "M", True, self.merge),
                      ("刪除", "Delete", True, self.delete)]
        self.menu.open(pos, items)

    # ------------------------------------------------------------ 繪製

    def draw(self, rect, mouse_pos):
        screen = self.screen
        m = 16
        x, w = rect.x + m, rect.width - m * 2
        top = pygame.Rect(x, rect.y + m, w, 36)
        tl_h = max(140, min(210, int(rect.height * 0.26)))
        timeline = pygame.Rect(x, rect.bottom - m - tl_h, w, tl_h)
        body = pygame.Rect(x, top.bottom + 12, w, timeline.y - 12 - top.bottom - 12)
        left_w = int(body.width * 0.56)
        left = pygame.Rect(body.x, body.y, left_w, body.height)
        right = pygame.Rect(left.right + 12, body.y, body.right - left.right - 12, body.height)
        position = self.player.position()
        self._draw_top(screen, top, mouse_pos)
        video = pygame.Rect(left.x, left.y, left.width, max(60, left.height - EDIT_H - 10 - 44))
        self._draw_video(screen, video, position)
        transport = pygame.Rect(left.x, video.bottom + 6, left.width, 34)
        self._draw_transport(screen, transport, position, mouse_pos)
        editor = pygame.Rect(left.x, transport.bottom + 10, left.width, left.bottom - transport.bottom - 10)
        self._draw_editor(screen, editor, mouse_pos)
        self._draw_list(screen, right, position, mouse_pos)
        self._draw_timeline(screen, timeline, position, mouse_pos)
        self.menu.draw(screen, mouse_pos)
        if self.drag is not None and self.drag["kind"] in ("start", "end") or \
                (self.drag is None and self.hover_edge and self.tl_lane.collidepoint(mouse_pos)):
            self._set_cursor(pygame.SYSTEM_CURSOR_SIZEWE)
        else:
            self._set_cursor(pygame.SYSTEM_CURSOR_ARROW)

    def _set_cursor(self, cursor):
        if cursor != self._cursor:
            self._cursor = cursor
            try:
                pygame.mouse.set_cursor(cursor)
            except pygame.error:
                pass

    def _draw_top(self, screen, top, mouse_pos):
        self.btn_media.draw(screen, pygame.Rect(top.x, top.y, 100, top.height), mouse_pos)
        self.btn_sub.draw(screen, pygame.Rect(top.x + 110, top.y, 100, top.height), mouse_pos)
        right = top.right
        self.btn_export.draw(screen, pygame.Rect(right - 92, top.y, 92, top.height), mouse_pos)
        right -= 92 + 16
        self.btn_redo.enabled = bool(self.redo_stack)
        self.btn_undo.enabled = bool(self.undo_stack)
        self.btn_redo.draw(screen, pygame.Rect(right - 60, top.y + 4, 60, top.height - 8), mouse_pos)
        self.btn_undo.draw(screen, pygame.Rect(right - 128, top.y + 4, 60, top.height - 8), mouse_pos)
        right -= 140
        self.btn_folder.draw(screen, pygame.Rect(right - 124, top.y + 4, 124, top.height - 8), mouse_pos)
        right -= 136
        # 目前開著的影片和字幕:旁邊的「×」可以關掉,再開別的
        pills = []
        if self.media_path:
            pills.append(("media", f"影片：{self.media_path.name}"))
        if self.subtitle_path or self.cues:
            name = self.subtitle_path.name if self.subtitle_path else "上次的進度"
            pills.append(("subtitle", f"字幕：{name}（{len(self.cues)} 句）"))
        self.close_rects = {}
        left = top.x + 222
        if not pills:
            draw_text(screen, "還沒有開啟檔案", (left, top.centery - 9), 13, theme.TEXT_FAINT)
            return
        each = (right - left) // len(pills) - 8
        for key, text in pills:
            width = min(each, theme.font(12).size(text)[0] + 48)
            if width < 70:
                break
            pill = pygame.Rect(left, top.y + 4, width, top.height - 8)
            rounded_panel(screen, pill, theme.PANEL, radius=14, border=theme.PANEL_EDGE)
            draw_text(screen, widgets.clip_text(text, 12, width - 44), (pill.x + 12, pill.centery - 8), 12,
                      theme.TEXT_DIM)
            close = pygame.Rect(pill.right - 28, pill.y + 3, 24, pill.height - 6)
            hover = close.collidepoint(mouse_pos)
            if hover:
                rounded_panel(screen, close, theme.PANEL_LIGHT, radius=10)
            draw_text(screen, "×", close.center, 15, theme.DANGER if hover else theme.TEXT_FAINT, center=True)
            self.close_rects[key] = close
            left = pill.right + 8

    def _draw_video(self, screen, area, position):
        self.video_rect = area
        rounded_panel(screen, area, theme.BG_DEEP, radius=10, alpha=240, border=theme.PANEL_EDGE)
        done, total, message, _, error, finished = self.task.snapshot()
        if self.task.running:
            ratio = done / max(1, total)
            draw_text(screen, f"{message}… {ratio * 100:.0f}%", area.center, 14, theme.TEXT_DIM, center=True)
            bar = pygame.Rect(area.centerx - 120, area.centery + 18, 240, 6)
            pygame.draw.rect(screen, theme.PANEL_LIGHT, bar, border_radius=3)
            pygame.draw.rect(screen, self.accent, (bar.x, bar.y, int(bar.width * ratio), bar.height), border_radius=3)
            return
        if self.info is None:
            lines = ["把影片和字幕檔拖進來", "影片用來邊看邊聽、對字幕的時間；輸出的只有字幕"]
            draw_text(screen, lines[0], (area.centerx, area.centery - 12), 15, theme.TEXT_DIM, center=True)
            draw_text(screen, lines[1], (area.centerx, area.centery + 14), 12, theme.TEXT_FAINT, center=True)
            return
        if self.frames is not None:
            frame = self.frames.show(position) if self.player.playing else self.frames.frame
            if frame is not None:
                inner = area.inflate(-4, -4)
                fw, fh = self.frames.size
                scale = min(inner.width / fw, inner.height / fh)
                size = (max(1, int(fw * scale)), max(1, int(fh * scale)))
                key = (id(frame), size)
                if self._frame_cache is None or self._frame_cache[0] != key:
                    image = pygame.image.frombuffer(frame[1], (fw, fh), "RGB")
                    self._frame_cache = (key, pygame.transform.smoothscale(image, size))
                image = self._frame_cache[1]
                screen.blit(image, image.get_rect(center=area.center))
        else:
            draw_text(screen, "只有聲音（沒有畫面）", area.center, 14, theme.TEXT_FAINT, center=True)
        self._draw_caption(screen, area, position)

    def _draw_caption(self, screen, area, position):
        """影片下方疊上這時候的字幕(說話者的顏色),和輸出後看到的差不多。"""
        index = self.current_at(position)
        if index is None:
            return
        cue = self.cues[index]
        color = self.color_of(cue)
        size = max(14, min(24, area.height // 16))
        lines = []
        for part in cue["text"].split("\n"):
            lines += widgets.wrap_text(part, size, area.width - 60, max_lines=2) or [""]
        lines = lines[-3:]
        line_h = theme.font(size).get_height() + 2
        y = area.bottom - 16 - line_h * len(lines)
        width = max(theme.font(size).size(line)[0] for line in lines) + 24
        back = pygame.Rect(area.centerx - width // 2, y - 6, width, line_h * len(lines) + 12)
        rounded_panel(screen, back, (10, 12, 16), radius=8, alpha=170)
        for line in lines:
            draw_text(screen, line, (area.centerx, y + line_h // 2), size, color, center=True)
            y += line_h

    def _draw_transport(self, screen, row, position, mouse_pos):
        self.btn_play.label = "暫停" if self.player.playing else "播放"
        self.btn_play.enabled = self.info is not None
        self.btn_play.draw(screen, pygame.Rect(row.x, row.y, 56, row.height), mouse_pos)
        total = subs.clock(self.duration, 0) if self.info else "--:--"
        draw_text(screen, f"{subs.clock(position, 2)} / {total}", (row.x + 68, row.centery - 9), 14, theme.TEXT)
        left = row.x + 68 + theme.font(14).size(f"{subs.clock(position, 2)} / {total}")[0] + 20
        draw_text(screen, widgets.clip_text(self.message, 12, row.right - left), (row.right, row.centery), 12,
                  self.message_color, right=True)

    def _button(self, screen, label, rect, mouse_pos, action, filled=False, enabled=True):
        button = Button(label, accent=self.accent, filled=filled, size=12)
        button.enabled = enabled
        button.draw(screen, rect, mouse_pos)
        self.edit_buttons.append((button, action))
        return rect.right

    def _draw_shift_row(self, screen, x, y, right, mouse_pos):
        """一起移動:選取的句子(沒有選取時是整份字幕)往前、往後移。"""
        draw_text(screen, "一起移動", (x, y + 7), 12, theme.TEXT_DIM)
        bx = x + 60
        for label, dt in SHIFTS:
            bx = self._button(screen, f"{label} 秒", pygame.Rect(bx, y, 66, 30), mouse_pos,
                              lambda d=dt: self.shift(d)) + 6
        if bx + 150 <= right:
            self.shift_in.draw(screen, pygame.Rect(bx + 6, y - 1, 84, 32), mouse_pos)
            self._button(screen, "移動", pygame.Rect(bx + 96, y, 52, 30), mouse_pos, self.shift_typed)

    def _draw_editor(self, screen, panel, mouse_pos):
        rounded_panel(screen, panel, theme.PANEL, radius=10, alpha=235, border=theme.PANEL_EDGE)
        self.edit_buttons = []
        self.chip_rects = []
        inner = panel.inflate(-28, -24)
        x, y = inner.x, inner.y
        chosen = self.selected()
        for field in self.inputs:
            field.rect = HIDDEN
        if not chosen:
            draw_text(screen, "整份字幕", (x, y + 7), 14, theme.TEXT, bold=True)
            if self.cues:
                self._draw_shift_row(screen, x + 90, y, inner.right, mouse_pos)
            tips = ["點清單或時間軸上的字幕來修改；在時間軸空白處拖曳可以框選好幾句，拖其中一句就一起移動",
                    "空白鍵 播放／暫停　↑↓ 上一句／下一句　←→ 倒退／快轉 1 秒　Ctrl+A 全選",
                    "[ ] 開始／結束設在播放位置　S 切開　M 合併　N 新增　Delete 刪除　Ctrl+Z 復原"]
            for i, tip in enumerate(tips):
                draw_text(screen, widgets.clip_text(tip, 12, inner.width), (x, y + 42 + i * 21), 12,
                          theme.TEXT_DIM if i == 0 else theme.TEXT_FAINT)
            self._draw_chips(screen, x, inner.bottom - 28, inner.right, mouse_pos, None)
            return
        if len(chosen) == 1:
            cue = chosen[0]
            index = self.index_of(cue["uid"])
            draw_text(screen, f"第 {index + 1} 句", (x, y + 8), 14, theme.TEXT, bold=True)
            cx = x + 76
            for label, field, key in (("開始", self.start_in, "start"), ("結束", self.end_in, "end")):
                draw_text(screen, label, (cx, y + 9), 12, theme.TEXT_DIM)
                field.draw(screen, pygame.Rect(cx + 32, y, 104, 34), mouse_pos)
                self._button(screen, "-", pygame.Rect(cx + 140, y + 3, 28, 28), mouse_pos,
                             lambda k=key: self.nudge(k, -NUDGE))
                self._button(screen, "+", pygame.Rect(cx + 171, y + 3, 28, 28), mouse_pos,
                             lambda k=key: self.nudge(k, NUDGE))
                cx += 216
            length = cue["end"] - cue["start"]
            draw_text(screen, f"{length:.1f} 秒", (cx, y + 9), 12, theme.TEXT_FAINT)
            y += 44
            self.text_in.draw(screen, pygame.Rect(x, y, inner.width, 36), mouse_pos)
            y += 40
            draw_text(screen, f"{LINE_MARK} 代表換行", (inner.right, y + 7), 11, theme.TEXT_FAINT, right=True)
            self._draw_chips(screen, x, y + 2, inner.right - 80, mouse_pos, cue["speaker"])
            y = inner.bottom - 30
            bx = x
            playing_ok = self.info is not None
            for label, action, ok in (("開始設在播放位置 [", lambda: self.set_edge_here("start"), playing_ok),
                                      ("結束設在播放位置 ]", lambda: self.set_edge_here("end"), playing_ok),
                                      ("切開 S", self.split, True), ("和下一句合併 M", self.merge, True),
                                      ("刪除", self.delete, True)):
                width = theme.font(12).size(label)[0] + 22
                if bx + width > inner.right:
                    break
                bx = self._button(screen, label, pygame.Rect(bx, y, width, 30), mouse_pos, action,
                                  enabled=ok) + 6
            return
        draw_text(screen, f"已選 {len(chosen)} 句", (x, y + 7), 14, theme.TEXT, bold=True)
        self._draw_shift_row(screen, x + 90, y, inner.right, mouse_pos)
        y += 44
        draw_text(screen, widgets.clip_text("也可以在時間軸上拖其中一句，選取的會一起移動；點下面的說話者，選取的句子一起改成那個人",
                                            12, inner.width), (x, y + 8), 12, theme.TEXT_FAINT)
        y += 40
        self._draw_chips(screen, x, y + 2, inner.right, mouse_pos, None)
        y = inner.bottom - 30
        bx = x
        for label, action in (("合併成一句 M", self.merge), ("刪除", self.delete), ("取消選取 Esc", self._clear_sel)):
            width = theme.font(12).size(label)[0] + 22
            bx = self._button(screen, label, pygame.Rect(bx, y, width, 30), mouse_pos, action) + 6

    def _clear_sel(self):
        self._blur_inputs()
        self.sel = set()

    def _draw_chips(self, screen, x, y, right, mouse_pos, current):
        """說話者:點一下套用到選取的句子,右鍵改標籤、顏色。"""
        draw_text(screen, "說話者", (x, y + 6), 12, theme.TEXT_DIM)
        cx = x + 52
        for speaker in self.speakers:
            label = speaker["label"]
            width = theme.font(12).size(label)[0] + 34
            if cx + width > right - 60:
                draw_text(screen, "…", (cx, y + 6), 12, theme.TEXT_FAINT)
                break
            chip = pygame.Rect(cx, y, width, 28)
            active = speaker["id"] == current
            hover = chip.collidepoint(mouse_pos)
            color = tuple(speaker["color"])
            rounded_panel(screen, chip, tuple(int(c * 0.35) for c in color) if active else theme.PANEL_LIGHT,
                          radius=14, border=color if active or hover else theme.PANEL_EDGE)
            pygame.draw.circle(screen, color, (chip.x + 14, chip.centery), 6)
            draw_text(screen, label, (chip.x + 25, chip.centery - 8), 12, theme.TEXT if active else theme.TEXT_DIM)
            self.chip_rects.append((chip, speaker["id"]))
            cx += width + 6
        add = pygame.Rect(cx, y, 56, 28)
        rounded_panel(screen, add, theme.PANEL_LIGHT if add.collidepoint(mouse_pos) else theme.PANEL, radius=14,
                      border=theme.PANEL_EDGE)
        draw_text(screen, "＋新增", add.center, 12, theme.TEXT_DIM, center=True)
        self.chip_rects.append((add, "add"))

    def _draw_list(self, screen, panel, position, mouse_pos):
        rounded_panel(screen, panel, theme.PANEL, radius=10, alpha=235, border=theme.PANEL_EDGE)
        head = pygame.Rect(panel.x + 14, panel.y + 10, panel.width - 28, 20)
        draw_text(screen, "字幕清單", (head.x, head.y), 13, theme.TEXT, bold=True)
        if self.cues:
            note = f"{len(self.cues)} 句" + (f"・已選 {len(self.sel)}" if self.sel else "")
            draw_text(screen, note, (head.right, head.centery), 12, theme.TEXT_FAINT, right=True)
        area = pygame.Rect(panel.x + 6, panel.y + 38, panel.width - 12, panel.height - 44)
        self.list_rect = area
        self.view.layout(area, len(self.cues) * ROW_H)
        if not self.cues:
            draw_text(screen, "開啟字幕檔（.srt）後在這裡顯示", area.center, 13, theme.TEXT_FAINT, center=True)
            return
        self.view.update(mouse_pos)
        current = self.current_at(position)
        first = max(0, self.view.scroll // ROW_H)
        last = min(len(self.cues), (self.view.scroll + area.height) // ROW_H + 1)
        previous_clip = screen.get_clip()
        screen.set_clip(area)
        text_w = area.width - 92
        for i in range(first, last):
            cue = self.cues[i]
            row = pygame.Rect(area.x, area.y + i * ROW_H - self.view.scroll, area.width - 6, ROW_H - 2)
            chosen = cue["uid"] in self.sel
            if chosen:
                rounded_panel(screen, row, tuple(int(c * 0.28) for c in self.accent), radius=6)
            elif row.collidepoint(mouse_pos) and area.collidepoint(mouse_pos):
                rounded_panel(screen, row, theme.PANEL_LIGHT, radius=6)
            if i == current:
                pygame.draw.rect(screen, PLAYHEAD, (row.x, row.y + 4, 3, row.height - 8))
            draw_text(screen, subs.clock(cue["start"], 1), (row.x + 10, row.y + 6), 12,
                      theme.TEXT if i == current else theme.TEXT_DIM)
            draw_text(screen, f"{cue['end'] - cue['start']:.1f}s", (row.x + 10, row.y + 23), 11, theme.TEXT_FAINT)
            color = self.color_of(cue)
            pygame.draw.rect(screen, color, (row.x + 70, row.y + 7, 4, row.height - 14), border_radius=2)
            parts = cue["text"].split("\n") if cue["text"] else ["（空白）"]
            draw_text(screen, widgets.clip_text(parts[0], 14, text_w), (row.x + 82, row.y + 4 if len(parts) > 1
                      else row.centery - 10), 14, theme.TEXT if cue["text"] else theme.TEXT_FAINT)
            if len(parts) > 1:
                draw_text(screen, widgets.clip_text("／".join(parts[1:]), 12, text_w), (row.x + 82, row.y + 24), 12,
                          theme.TEXT_DIM)
        screen.set_clip(previous_clip)
        self.view.draw(screen, mouse_pos)

    def _draw_timeline(self, screen, panel, position, mouse_pos):
        rounded_panel(screen, panel, theme.PANEL, radius=10, alpha=235, border=theme.PANEL_EDGE)
        inner = panel.inflate(-24, -16)
        draw_text(screen, "時間軸", (inner.x, inner.y + 2), 13, theme.TEXT, bold=True)
        self.snap.draw(screen, (inner.x + 64, inner.y), mouse_pos)
        draw_text(screen, "貼齊人聲", (inner.x + 112, inner.y + 3), 12, theme.TEXT_DIM)
        hint = "空白處拖曳框選・拖上面的刻度或紅線移動播放位置・拖邊緣改時間・拖中間一起移動・滾輪縮放・右鍵拖曳平移"
        draw_text(screen, widgets.clip_text(hint, 12, inner.width - 190), (inner.right, inner.y + 11), 12,
                  theme.TEXT_FAINT, right=True)
        area = pygame.Rect(inner.x, inner.y + 30, inner.width, inner.height - 30)
        self.tl_area = area
        self.tl_lane = pygame.Rect(area.x, area.bottom - 30, area.width, 30)
        rounded_panel(screen, area, theme.BG_DEEP, radius=6, alpha=220)
        if not self.cues and self.wave is None:
            draw_text(screen, "開啟影片後顯示聲音波形，字幕會排在下面", area.center, 12, theme.TEXT_FAINT, center=True)
            return
        self._clamp_view()
        previous_clip = screen.get_clip()
        screen.set_clip(area)
        wave_rect = pygame.Rect(area.x, area.y + RULER_H, area.width, self.tl_lane.y - area.y - RULER_H - 4)
        ruler = pygame.Rect(area.x, area.y, area.width, RULER_H)
        rounded_panel(screen, ruler, theme.PANEL_LIGHT if ruler.collidepoint(mouse_pos) else theme.PANEL, radius=0)
        self._draw_wave(screen, wave_rect)
        self._draw_ruler(screen, area)
        lane = self.tl_lane
        end_t = self.tl_start + self.tl_span
        i = max(0, bisect.bisect_left(self.starts(), self.tl_start - 60))
        while i < len(self.cues) and self.cues[i]["start"] <= end_t:
            cue = self.cues[i]
            i += 1
            if cue["end"] < self.tl_start:
                continue
            x1, x2 = self.t_to_x(cue["start"]), self.t_to_x(cue["end"])
            block = pygame.Rect(int(x1), lane.y + 2, max(2, int(x2 - x1)), lane.height - 4)
            color = self.color_of(cue)
            chosen = cue["uid"] in self.sel
            rounded_panel(screen, block, tuple(int(c * (0.75 if chosen else 0.45)) for c in color), radius=4)
            pygame.draw.rect(screen, (255, 255, 255) if chosen else color, block, 2 if chosen else 1,
                             border_radius=4)
            if block.width > 24:
                label = widgets.clip_text(cue["text"].replace("\n", " ") or "（空白）", 12, block.width - 10)
                draw_text(screen, label, (block.x + 5, block.centery - 8), 12, theme.TEXT)
            # 選取的那句:在波形上畫出範圍,方便對聲音
            if chosen:
                shade = pygame.Surface((block.width, wave_rect.height), pygame.SRCALPHA)
                shade.fill((*color, 38))
                screen.blit(shade, (block.x, wave_rect.y))
        drag = self.drag
        if drag is not None and drag["kind"] == "box" and drag["moved"]:
            x1, x2 = sorted((drag["x"], drag["now"]))
            band = pygame.Rect(x1, area.y, max(1, x2 - x1), area.height).clip(area)
            if band.width:
                layer = pygame.Surface(band.size, pygame.SRCALPHA)
                layer.fill((*self.accent, 45))
                screen.blit(layer, band.topleft)
                pygame.draw.rect(screen, self.accent, band, 1)
        if self.info is not None:
            px = int(self.t_to_x(position))
            pygame.draw.line(screen, PLAYHEAD, (px, area.y), (px, area.bottom), 2)
            pygame.draw.polygon(screen, PLAYHEAD, [(px - 5, area.y), (px + 5, area.y), (px, area.y + 7)])
        if area.collidepoint(mouse_pos) and self.drag is None:
            draw_text(screen, subs.clock(self.x_to_t(mouse_pos[0]), 2), (mouse_pos[0] + 8, area.y + 20), 11,
                      theme.TEXT_DIM)
        screen.set_clip(previous_clip)

    def _draw_ruler(self, screen, area):
        span = self.tl_span
        step = next((s for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600)
                     if span / s <= max(4, area.width // 90)), 600)
        t = (self.tl_start // step) * step
        while t <= self.tl_start + span:
            x = int(self.t_to_x(t))
            if x >= area.x:
                pygame.draw.line(screen, theme.PANEL_EDGE, (x, area.y), (x, area.y + 8))
                draw_text(screen, subs.clock(t, 1 if step < 1 else 0), (x + 3, area.y + 2), 11, theme.TEXT_FAINT)
            t += step

    def _draw_wave(self, screen, rect):
        if self.wave is None or rect.width <= 0 or rect.height <= 4:
            return
        key = (round(self.tl_start, 3), round(self.tl_span, 3), rect.size)
        if self._wave_cache is None or self._wave_cache[0] != key:
            layer = pygame.Surface(rect.size, pygame.SRCALPHA)
            peaks = self.wave[0]
            a = int(self.tl_start * media.BIN_RATE)
            b = int((self.tl_start + self.tl_span) * media.BIN_RATE)
            columns = np.zeros(rect.width, np.float32)
            n = len(peaks)
            starts = np.linspace(a, b, rect.width + 1)[:-1].astype(int)
            inside = (starts >= 0) & (starts < n)
            if inside.any():
                if b - a >= rect.width:
                    # 一格裡有好幾個點:取最大值(最後一格算到看得到的範圍結尾)
                    s0, s1 = max(0, a), min(n, b)
                    columns[inside] = np.maximum.reduceat(peaks[s0:s1], np.clip(starts[inside] - s0, 0, s1 - s0 - 1))
                else:
                    columns[inside] = peaks[starts[inside]]
            loud = max(1e-3, float(np.percentile(peaks, 99.5))) if len(peaks) else 1.0
            mid = rect.height // 2
            color = (110, 125, 150, 255)
            for x, value in enumerate(columns):
                half = int(min(1.0, value / loud) * (mid - 1))
                if half:
                    pygame.draw.line(layer, color, (x, mid - half), (x, mid + half))
            pygame.draw.line(layer, (60, 68, 84, 255), (0, mid), (rect.width, mid))
            self._wave_cache = (key, layer)
        screen.blit(self._wave_cache[1], rect.topleft)
