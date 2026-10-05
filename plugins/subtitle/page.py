"""即時字幕:左邊設定(聲音來源、原文語言、辨識模型、翻譯、字幕樣式),右邊是這次的字幕紀錄;
按「開始字幕」後字幕浮在所有視窗最上層。字幕只能從這裡停止(關掉 Naiz Studio 也會一起關)。"""

import atexit
import os
import threading
import time

import pygame

from core import diarize, paths, theme, widgets
from core.contextmenu import ContextMenu
from core.plugins import Page
from core.scroll import ScrollView
from core.widgets import Button, Dropdown, SegmentedControl, Slider, Toggle, draw_text, rounded_panel

from ..editor import fonts
from ..editor.font_picker import FontPicker
from ..editor.palette import ColorPalette
from . import asr, furigana, glossary, hardware, speaker_profiles, speakers
from .translator_dialog import TranslatorDialog
from . import translate as ollama
from .engine import Engine, Settings
from .glossary_dialog import GlossaryDialog
from .speaker_dialog import SpeakerDialog
from .overlay import ALIGNS, MODES, STYLE, Overlay

SOURCES = [("system", "電腦播放的聲音"), ("app", "單一程式"), ("mic", "麥克風")]
SOURCE_NOTES = {"system": "YouTube、B站、遊戲、Discord 等電腦正在播放的聲音都會翻譯",
                "app": "只翻譯選的程式，例如只翻 Discord、不翻遊戲音樂",
                "mic": "翻譯麥克風收到的聲音"}
# 收音靈敏度:電腦聲音開得小時字幕可能出不來,放大後再給人聲偵測和辨識
GAINS = [("auto", "自動"), ("1", "原本音量"), ("2", "放大 2 倍"), ("4", "放大 4 倍")]
GAIN_NOTES = {"auto": "聲音太小時自動放大，電腦音量開得小也收得到（建議）",
              "1": "照原本的音量，不放大",
              "2": "固定放大 2 倍，聲音很小、自動還收不到時用",
              "4": "固定放大 4 倍，聲音非常小時用；太大聲時可能破音"}
COUNTS = [("1", "1 句"), ("2", "2 句"), ("3", "3 句")]
FADES = [("0", "一直顯示"), ("4", "4 秒"), ("8", "8 秒"), ("15", "15 秒")]
# 字幕紀錄存成什麼檔案(和錄音轉逐字稿一樣)
OUTPUTS = [("both", "SRT + TXT"), ("srt", "只要 SRT"), ("txt", "只要 TXT"), ("off", "不存檔")]
OUTPUT_NOTES = {"both": "字幕檔有時間軸，純文字方便閱讀與複製",
                "srt": "有時間軸的字幕檔，可以直接掛在影片上或丟進剪輯軟體",
                "txt": "沒有時間軸的純文字，方便閱讀與複製；也會記下字幕進行中改過的設定",
                "off": "不存成檔案，只留在右邊的字幕紀錄（停止後還能複製，關掉就沒了）"}
SETTINGS_W = 440
HIDDEN = pygame.Rect(-10000, -10000, 0, 0)     # 這一幀沒畫出來(或捲到看不見)的設定:移到畫面外,不會被點到
CONFIG_KEY = "subtitle"
FULLSCREEN_NOTE = "「獨佔全螢幕」的遊戲蓋不上字幕，請在遊戲設定改成「無邊框視窗」"


def _clock(seconds):
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _srt_time(seconds):
    ms = int(round(seconds * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def output_dir():
    return paths.OUTPUT_DIR / "subtitles"


class SubtitleFontPicker(FontPicker):
    """字型選單和 PDF 編輯器共用;字幕不用嵌進 PDF,所以不檢查嵌入權限。"""

    def choose(self, face):
        if not face.installed:
            if face.dependency is None:
                self.message = (f"找不到「{face.name}」的字型檔", theme.DANGER)
                return
            self.page.app.consent.open(face.name, [face.dependency], on_done=lambda: self.choose(face))
            return
        action = self.on_pick
        self.close()
        if action is not None:
            action(face.id)


class SubtitlePage(Page):
    def __init__(self, app, tool):
        super().__init__(app, tool)
        accent = tool.accent
        model, translator, partial, self.advice = hardware.recommend()
        self.recommended = (model, translator)
        saved = app.config.get(CONFIG_KEY) if isinstance(app.config.get(CONFIG_KEY), dict) else {}
        self.prefs = {"source": "system", "language": "auto", "model": model, "translate": True,
                      "translator": translator, "target": "zh-TW", "partial": partial, "gain": "auto",
                      "output": "both", "speakers": False, **STYLE}
        self.prefs.update({k: v for k, v in saved.items() if k in self.prefs})

        self.source = SegmentedControl(SOURCES, accent=accent)
        self.source.index = [k for k, _ in SOURCES].index(self.prefs["source"])
        self.programs = []                  # [(pid, 名稱)]
        self.program_pick = Dropdown([("", "（讀取中）")], accent=accent, size=13)
        self.btn_refresh = Button("重新整理", filled=False, size=12)
        self.gain = SegmentedControl(GAINS, accent=accent)
        self.gain.index = [k for k, _ in GAINS].index(self.prefs["gain"]) if self.prefs["gain"] in dict(GAINS) else 0
        self.language = Dropdown(asr.LANGUAGES, accent=accent, size=13)
        self.language.set_value(self.prefs["language"])
        self.names = glossary.load(app.config)          # 專有名詞表(好幾個設定檔,選一個用)
        self.names_pick = Dropdown([(glossary.NONE, "不使用")], accent=accent, size=13)
        self.btn_names = Button("編輯", filled=False, size=12)
        self.names_dialog = GlossaryDialog(lambda: self.screen, accent, self._names_changed)
        self.model_rows = []                # 這一幀畫出來的辨識模型:[(範圍, 代號)]
        self.translate_on = Toggle(self.prefs["translate"], accent=accent)
        self.furigana_on = Toggle(self.prefs["furigana"], accent=accent)
        self.speakers_on = Toggle(self.prefs["speakers"], accent=accent)
        # 判斷誰說話的區分方式(設定檔,""是自動)
        self.speaker_data = speaker_profiles.load(app.config)
        self.speaker_pick = Dropdown([(speaker_profiles.GENERAL, speaker_profiles.GENERAL)], accent=accent, size=13)
        self.btn_speaker_edit = Button("編輯", filled=False, size=12)
        self.speaker_dialog = SpeakerDialog(lambda: self.screen, accent, self._speaker_profiles_changed)
        self.speaker_colors = {}            # 使用者改過的顏色:{第幾個人: RGB}(這次字幕有效)
        self.menu = ContextMenu(accent)     # 字幕紀錄的右鍵選單
        self.target = Dropdown([(k, n) for k, n, _, _ in ollama.TARGETS], accent=accent, size=13)
        self.target.set_value(self.prefs["target"])
        self.btn_translator = Button("選擇翻譯模型", filled=False, size=13)     # 打開翻譯模型的管理視窗
        self.translator_dialog = TranslatorDialog(self)
        self.btn_ollama = Button("打開 Ollama", accent=accent, filled=False, size=13)
        self.mode = SegmentedControl(MODES, accent=accent)
        self.mode.index = [k for k, _ in MODES].index(self.prefs["mode"])
        self.size = Slider(20, 60, self.prefs["size"], step=2, accent=accent)
        self.size_original = Slider(12, 60, self.prefs["size_original"], step=2, accent=accent)
        self.opacity = Slider(0, 100, self.prefs["opacity"], step=5, accent=accent)
        self.align = SegmentedControl(ALIGNS, accent=accent)
        self.align.index = [k for k, _ in ALIGNS].index(self.prefs["align"])
        self.outline = Slider(0, 8, self.prefs["outline"], accent=accent)
        self.count = SegmentedControl(COUNTS, accent=accent)
        self.count.index = max(0, min(2, int(self.prefs["count"]) - 1))
        self.fade = SegmentedControl(FADES, accent=accent)
        self.fade.index = next((i for i, (k, _) in enumerate(FADES) if int(k) == int(self.prefs["fade"])), 2)
        self.output = SegmentedControl(OUTPUTS, accent=accent)
        self.output.index = [k for k, _ in OUTPUTS].index(self.prefs["output"]) \
            if self.prefs["output"] in dict(OUTPUTS) else 0
        self.palette = ColorPalette(accent)                 # 和 PDF 編輯器同一套顏色選單
        self.font_picker = SubtitleFontPicker(self, accent)
        self.btn_font = Button("", filled=False, size=13)
        self.swatches = []                  # 這一幀畫出來的顏色按鈕:[(範圍, 設定名稱)]
        self.btn_copy_all = Button("複製全部", filled=False, size=12)
        self.btn_open = Button("開啟資料夾", filled=False, size=12)
        self.line_rects = []                # 這一幀畫出來的字幕紀錄:[(範圍, 那一句)]
        self.session = None                 # 這次字幕自動存檔:{base, saved}
        self._notice_until = 0.0            # 「已複製」這類提示幾秒後消失
        self._saved_at = 0.0
        self.btn_start = Button("開始字幕", accent=accent, size=15)
        self.btn_adjust = Button("調整位置", filled=False, size=14)
        self.btn_clear = Button("清除", filled=False, size=12)
        self.settings_view = ScrollView(accent=accent, indicator=True)
        self.lines_view = ScrollView(accent=accent, indicator=True)
        self.settings_area = pygame.Rect(0, 0, 0, 0)
        self.lines_area = pygame.Rect(0, 0, 0, 0)
        self.follow = True                  # 字幕紀錄自動捲到最新

        self.engine = None
        self.overlay = Overlay(on_moved=self._moved)
        self.adjusting = False
        self.notice = ("", theme.TEXT_DIM)
        # 翻譯引擎的狀態:checking / stopped(有 Ollama 但沒開)/ running(Ollama 有開,或沒有 Ollama 時用內建引擎)
        self.ollama_state = "checking"
        self.ollama_models = []
        self._ollama_checked = 0.0
        self._ollama_busy = False
        self.pull = None                    # 下載翻譯模型中:{name, done, total, error, cancel}
        self._dirty = False
        self._pushed = 0.0
        self._programs_busy = False
        self._refresh_ollama()
        atexit.register(self._on_exit)

    def _on_exit(self):
        """關閉 Naiz Studio 時字幕還在跑:把翻譯模型從顯示卡移掉(辨識程式和字幕視窗會自己結束)。"""
        engine = self.engine
        if engine is not None and engine.settings.translate and engine.state in ("loading", "running"):
            ollama.unload(engine.settings.translator)

    # ------------------------------------------------------------ 設定

    def _save(self):
        self.app.save_setting(CONFIG_KEY, dict(self.prefs))

    def _moved(self, x, y):
        self.prefs["x"], self.prefs["y"] = x, y
        self._save()

    def _display_mode(self):
        """實際的顯示方式:翻譯關掉時一定只顯示原文(選的「只顯示翻譯」「原文和翻譯」先記著,打開翻譯再用)。"""
        return self.prefs["mode"] if self.prefs["translate"] else "original"

    def _style(self):
        style = {key: self.prefs[key] for key in STYLE}
        style["mode"] = self._display_mode()
        backup = fonts.CATALOG.fallback()             # 缺字時補字:和 PDF 編輯器一樣(Noto Sans TC 或電腦內建的中文字型)
        if backup is not None:
            style["fallback_path"], style["fallback_index"] = str(backup.path), backup.index
        return style

    @property
    def running(self):
        return self.engine is not None and self.engine.state in ("loading", "running")

    def _refresh_ollama(self, force=False):
        if self._ollama_busy or (not force and time.monotonic() - self._ollama_checked < 3):
            return
        self._ollama_busy = True

        def work():
            try:
                if not ollama.running():
                    self.ollama_state = "stopped"
                    self.ollama_models = []
                else:
                    self.ollama_models = ollama.models()
                    self.ollama_state = "running"
            except Exception:
                self.ollama_state = "stopped"
            self._ollama_checked = time.monotonic()
            self._ollama_busy = False

        threading.Thread(target=work, daemon=True).start()

    def _refresh_programs(self):
        if self._programs_busy:
            return
        self._programs_busy = True

        def work():
            try:
                from . import capture             # 只在背景執行緒載入(會設定這個執行緒的 COM 模式)

                self.programs = capture.audio_programs()
            except Exception:
                self.programs = []
            self._programs_busy = False

        threading.Thread(target=work, daemon=True).start()

    def _speaker_options(self):
        return [(name, name, speaker_profiles.METHOD_NAMES[value["method"]])
                for name, value in self.speaker_data["profiles"].items()]

    def _speaker_choice(self):
        """目前要用的區分方式:(名稱, 內容)。"""
        return speaker_profiles.resolve(self.speaker_data)

    def _speaker_profiles_changed(self, data):
        """區分方式的設定檔改了:存檔;字幕進行中的話下一句開始用新的。"""
        self.speaker_data = data
        try:
            self.app.save_setting(speaker_profiles.KEY, speaker_profiles.save(data))
        except OSError as exc:
            self.notice = (f"區分方式的設定檔存不了：{exc}", theme.WARN)
            self._notice_until = time.monotonic() + 5
        self._sync_speaker_mode()

    def _sync_speaker_mode(self):
        """字幕進行中:選的設定檔、聲音來源(自動時)變了,或設定檔的數值改了,就換引擎用的區分方式並記一行。"""
        engine = self.engine
        if not (self.running and engine is not None and self.prefs["speakers"]):
            return
        name, value = self._speaker_choice()
        mode = speaker_profiles.mode(value)
        if engine.settings.speaker_mode != mode:
            engine.settings.speaker_mode = mode
            engine.notice(f"判斷誰說話的區分方式改用「{name}」（下一句開始套用）")

    def _names_options(self):
        return [(glossary.NONE, "不使用")] + [(name, name, f"{len(terms)} 個")
                                            for name, terms in self.names["profiles"].items()]

    def _names_changed(self, data):
        """專有名詞改了:存檔;字幕進行中的話馬上用新的名詞。"""
        self.names = data
        try:
            self.app.save_setting(glossary.KEY, glossary.save(data))
        except OSError as exc:
            self.notice = (f"專有名詞存不了：{exc}", theme.WARN)
            self._notice_until = time.monotonic() + 5
        if self.engine is not None:
            self.engine.settings.glossary = glossary.terms(data)

    def _label(self, key, value):
        """設定值給人看的名稱(寫進字幕紀錄)。"""
        if key == "source":
            return dict(SOURCES).get(value, value)
        if key == "model":
            return next((name for k, name, _, _ in asr.MODELS if k == value), value)
        if key == "language":
            return dict(asr.LANGUAGES).get(value, value)
        if key == "target":
            return ollama.TARGET_NAMES.get(value, value)
        if key in ("translate", "speakers"):
            return "開" if value else "關"
        if key == "gain":
            return dict(GAINS).get(value, value)
        if key == "pid":
            return next((name for pid, name in self.programs if str(pid) == str(value)), str(value))
        return str(value)

    def _change(self, key, value):
        """改一項設定:記下來;字幕進行中的話交給引擎套用(字幕紀錄會記一行)。
        換辨識模型、聲音來源會短暫中斷,其他的下一句就套用。"""
        old = self.prefs.get(key)
        if old == value:
            return
        engine = self.engine
        if self.running and engine is not None and engine.state == "loading":
            self.notice = ("字幕還在載入，載好後再改", theme.WARN)
            self._notice_until = time.monotonic() + 3
            return
        if self.running and key == "translate" and value and not self._translate_ready():
            return
        self.prefs[key] = value
        self._save()
        if not self.running or engine is None:
            return
        titles = {"source": "聲音來源", "model": "辨識模型", "language": "原文語言", "translate": "翻譯",
                  "target": "翻成", "translator": "翻譯模型", "gain": "收音靈敏度", "speakers": "判斷誰說話"}
        changes = {key: value}
        if key == "source":
            if value == "app":
                pid = int(self.program_pick.value or 0) or None
                if pid is None:                 # 還沒選程式:選好再換過去
                    self.notice = ("選好要翻譯的程式後就會換過去", self.tool.accent)
                    self._notice_until = time.monotonic() + 4
                    return
                changes["pid"] = pid
            else:
                changes["pid"] = None
        summary = f"{titles.get(key, key)} {self._label(key, old)} → {self._label(key, value)}"
        if key == "source" and value == "app":
            summary += f"（{self._label('pid', changes['pid'])}）"
        engine.reconfigure(changes, summary)

    def _change_program(self, pid):
        """單一程式模式下換程式(字幕進行中的話換過去)。"""
        engine = self.engine
        if not (self.running and engine is not None and engine.state == "running"):
            return
        if self.prefs["source"] != "app" or not pid:
            return
        old = engine.settings.pid
        if old == pid and engine.settings.source == "app":
            return
        old_text = self._label("pid", old) if engine.settings.source == "app" and old \
            else self._label("source", engine.settings.source)
        engine.reconfigure({"source": "app", "pid": pid}, f"聲音來源 {old_text} → {self._label('pid', pid)}")

    def _translate_ready(self):
        if self.ollama_state != "running":
            self.notice = ("Ollama 沒有在執行：請先打開 Ollama", theme.WARN)
        elif self.prefs["translator"] not in [name for name, _ in self.ollama_models]:
            self.notice = ("請先選好翻譯模型（按設定區的「翻譯模型」選擇或下載）", theme.WARN)
        else:
            return True
        self._notice_until = time.monotonic() + 4
        return False

    # ------------------------------------------------------------ 開始、停止

    def start(self):
        if self.running:
            return
        prefs = self.prefs
        if prefs["source"] == "app" and not self.program_pick.value:
            self.notice = ("請先選要翻譯哪個程式（沒有出現的話，讓它先播放聲音再按「重新整理」）", theme.WARN)
            return
        missing = asr.required(prefs["model"])
        if missing:
            self.app.consent.open("即時字幕", missing, on_done=self.start)
            return
        if prefs["translate"]:
            if self.ollama_state != "running":
                self.notice = ("Ollama 沒有在執行：請先打開 Ollama，或把「翻譯」關掉只顯示原文", theme.WARN)
                return
            if prefs["translator"] not in [name for name, _ in self.ollama_models]:
                self.notice = ("請先選好翻譯模型（按設定區的「翻譯模型」選擇或下載）", theme.WARN)
                return
            missing = ollama.missing(prefs["translator"])       # 內建引擎的執行檔被刪掉時
            if missing:
                self.app.consent.open("即時字幕的翻譯", missing, on_done=self.start)
                return
        settings = Settings(source=prefs["source"], pid=int(self.program_pick.value or 0) or None,
                            model=prefs["model"], language=prefs["language"], translate=prefs["translate"],
                            translator=prefs["translator"], target=prefs["target"], partial=prefs["partial"],
                            step=0.3 if asr.gpu() else 1.5, glossary=glossary.terms(self.names),
                            gain=prefs["gain"], speakers=prefs["speakers"],
                            speaker_mode=speaker_profiles.mode(self._speaker_choice()[1]))
        self.speaker_colors = {}            # 每次開始字幕重新認人,上次改的顏色不沿用
        self.engine = Engine(settings, on_update=self._changed)
        if prefs["speakers"]:
            self.engine.notice(f"判斷誰說話：用「{self._speaker_choice()[0]}」")      # 字幕紀錄記一行用哪個區分方式
        self.engine.start()
        base, number = time.strftime("字幕 %Y-%m-%d %H%M%S"), 2
        while (output_dir() / f"{base}.txt").exists() or (output_dir() / f"{base}.srt").exists():
            base = f"{time.strftime('字幕 %Y-%m-%d %H%M%S')} ({number})"
            number += 1
        self.session = {"base": base, "saved": 0}
        self.overlay.start(self._style())
        self.overlay.lines([])
        self.notice = ("", theme.TEXT_DIM)
        self.follow = True

    def stop(self):
        if self.adjusting:
            self._toggle_adjust()
        if self.engine is not None:
            engine = self.engine
            self._save_transcript(force=True)
            threading.Thread(target=engine.stop, daemon=True).start()
        self.overlay.stop()

    def shutdown(self):
        """畫面出錯被丟掉前:停止字幕、關掉字幕視窗和辨識程式。"""
        self.adjusting = False
        if self.engine is not None and self.engine.state in ("loading", "running"):
            self._save_transcript(force=True)
            self.engine.stop()
        self.overlay.stop()

    def _transcript_lines(self):
        engine = self.engine
        return [line for line in (engine.lines if engine else []) if line.final and line.original]

    def _save_transcript(self, force=False):
        """每多一句定稿就存一次(當機也不會全部不見):output\\subtitles\\字幕 日期 時間.txt / .srt。"""
        lines = self._transcript_lines()
        # 句數、每句是誰、顏色有變才重存(判斷誰說話是定稿後才算好,改顏色也要重存)
        marks = tuple(self.color_of(line.speaker) for line in lines) if self.prefs["speakers"] else ()
        signature = (len(lines), marks)
        if self.session is None or not lines or (not force and signature == self.session["saved"]) \
                or self.prefs["output"] == "off":
            return
        stamp = time.strftime("%Y-%m-%d %H:%M")
        text = [f"Naiz Studio 即時字幕 {stamp}", ""]
        srt = []
        number = 0
        for line in lines:
            if line.notice:                     # 變更設定的說明:只寫進 txt(srt 常直接丟進剪輯軟體,不放)
                text.append(f"[{_clock(line.start)}] ── {line.original}")
                continue
            number += 1
            translated = "" if line.same else line.translation
            original = line.translation if line.same and line.translation else line.original
            color = self.color_of(line.speaker) if self.prefs["speakers"] else None
            who = f"({speakers.color_name(color)}) " if color else ""
            text.append(f"[{_clock(line.start)}] {who}{original}")
            if translated:
                text.append(f"        {translated}")
            body = "\n".join(part for part in (translated, original) if part)
            srt.append(f"{number}\n{_srt_time(line.start)} --> {_srt_time(max(line.end, line.start + 0.5))}\n{body}\n")
        try:
            folder = output_dir()
            folder.mkdir(parents=True, exist_ok=True)
            output = self.prefs["output"]
            if output in ("both", "txt"):
                (folder / f"{self.session['base']}.txt").write_text("\n".join(text) + "\n", encoding="utf-8")
            if output in ("both", "srt"):
                (folder / f"{self.session['base']}.srt").write_text("\n".join(srt), encoding="utf-8")
            self.session["saved"] = signature
        except OSError as exc:
            self.notice = (f"字幕紀錄存不了：{exc}", theme.WARN)

    def _copy(self, lines, what):
        parts = []
        for line in lines:
            if line.notice:
                continue
            if line.same or not line.translation:
                parts.append(line.translation or line.original)
            else:
                parts.append(f"{line.original}\n{line.translation}")
        widgets.copy_to_clipboard("\n\n".join(parts))
        self.notice = (f"已複製{what}", self.tool.accent)
        self._notice_until = time.monotonic() + 3

    def _changed(self):
        self._dirty = True

    def _toggle_adjust(self):
        self.adjusting = not self.adjusting
        if not self.overlay.alive:
            self.overlay.start(self._style())
        self.overlay.adjust(self.adjusting)
        if not self.adjusting and not self.running:
            self.overlay.stop()

    # ------------------------------------------------------------ 每一幀

    def deactivate(self):
        for dropdown in (self.program_pick, self.language, self.names_pick, self.speaker_pick, self.target):
            dropdown.close()
        self.settings_view.reset()
        self.lines_view.reset()

    def update(self):
        if self._notice_until and time.monotonic() > self._notice_until:
            self.notice, self._notice_until = ("", theme.TEXT_DIM), 0.0
        mouse = pygame.mouse.get_pos()
        self.settings_view.update(mouse)
        self.lines_view.update(mouse)
        if not self.running:
            self._refresh_ollama()
        self._sync_speaker_mode()
        self.background()

    def background(self):
        """把新的字送到字幕視窗、自動存檔。回首頁或在別的工具時也繼續(字幕照常顯示,只能回到這裡停止)。"""
        engine = self.engine
        if engine is not None and self._dirty and time.monotonic() - self._pushed > 0.05:
            self._dirty = False
            self._pushed = time.monotonic()
            items = []
            for line, idle in engine.recent(int(self.prefs["count"])):
                age = idle if line.final else None
                color = self.color_of(line.speaker) if self.prefs["speakers"] else None
                if line.same:
                    items.append(("", line.translation or line.original, line.final, age, (), color))
                else:
                    ruby = furigana.annotate(line.original) if self.prefs["furigana"] else ()
                    items.append((line.original, line.translation, line.final, age, ruby, color))
            self.overlay.lines(items)
        if engine is not None and self.session is not None and time.monotonic() - self._saved_at > 1:
            self._saved_at = time.monotonic()
            self._save_transcript()
        if engine is not None and engine.state == "error" and self.overlay.alive and not self.adjusting:
            self.overlay.stop()

    # ------------------------------------------------------------ 繪製

    def draw(self, rect, mouse_pos):
        margin = 20
        footer_h = 74
        body = pygame.Rect(rect.x + margin, rect.y + margin, rect.width - margin * 2,
                           rect.height - margin * 3 - footer_h)
        width = min(SETTINGS_W, body.width // 2)
        self._draw_settings(pygame.Rect(body.x, body.y, width, body.height), mouse_pos)
        self._draw_lines(pygame.Rect(body.x + width + margin, body.y, body.width - width - margin, body.height),
                         mouse_pos)
        self._draw_footer(pygame.Rect(rect.x + margin, rect.bottom - footer_h - margin, rect.width - margin * 2,
                                      footer_h), mouse_pos)
        for dropdown in (self.program_pick, self.language, self.names_pick, self.speaker_pick, self.target):
            dropdown.draw_menu(self.screen, mouse_pos)
        self.palette.draw(self.screen, mouse_pos)
        self.menu.draw(self.screen, mouse_pos)

    def _controls(self):
        buttons = [self.btn_refresh, self.btn_ollama, self.btn_font, self.btn_names, self.btn_translator,
                   self.btn_speaker_edit]
        return buttons, [self.source, self.gain, self.mode, self.align, self.count, self.fade, self.output], \
            [self.program_pick, self.language, self.names_pick, self.speaker_pick, self.target], \
            [self.size, self.size_original, self.outline, self.opacity]

    def _draw_settings(self, rect, mouse_pos):
        screen = self.screen
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        area = rect.inflate(-28, -24)
        self.settings_area = area
        view = self.settings_view
        # 先把所有設定移到畫面外;這一幀真的畫出來的才會回到原位
        # (否則切換來源、關掉翻譯後,看不見的選單還留在原位,點別的地方會打開它)
        buttons, segments, dropdowns, sliders = self._controls()
        for control in buttons + dropdowns + sliders + [self.translate_on, self.furigana_on, self.speakers_on]:
            control.rect = HIDDEN.copy()
        for control in segments:
            control.rects = []
        self.model_rows, self.swatches = [], []
        screen.set_clip(area)
        bottom = self._setting_rows(area.x, area.y - view.scroll, area.width, mouse_pos)
        screen.set_clip(None)
        self._clip_controls(area)
        view.layout(rect.inflate(-4, -16), bottom - (area.y - view.scroll) + 12)
        view.draw(screen, mouse_pos)

    def _clip_controls(self, area):
        """捲到設定區外面的設定不能被點到;沒畫出來還開著的下拉選單收起來。"""
        def cut(rect):
            return rect.clip(area) if rect.colliderect(area) else HIDDEN.copy()

        buttons, segments, dropdowns, sliders = self._controls()
        for control in buttons + sliders + [self.translate_on, self.furigana_on, self.speakers_on]:
            control.rect = cut(control.rect)
        for control in segments:
            control.rects = [cut(rect) for rect in control.rects]
        for dropdown in dropdowns:
            if not dropdown.rect.colliderect(area):
                dropdown.rect = HIDDEN.copy()
                dropdown.close()
        self.model_rows = [(cut(rect), key) for rect, key in self.model_rows if rect.colliderect(area)]
        self.swatches = [(cut(rect), key) for rect, key in self.swatches if rect.colliderect(area)]

    @staticmethod
    def _set_options(dropdown, options, value=None):
        """選項真的變了才換(換選項會把選單收起來;每一幀都換的話選單會打不開)。"""
        if [tuple(option) for option in dropdown.options] != [tuple(option) for option in options]:
            dropdown.set_options(options, value)

    def _heading(self, text, x, y, hint=""):
        draw_text(self.screen, text, (x, y), 14, theme.TEXT, bold=True)
        if hint:
            draw_text(self.screen, hint, (x + 90, y + 2), 12, theme.TEXT_FAINT)
        return y + 26

    def _setting_rows(self, x, y, inner, mouse_pos):
        screen = self.screen
        accent = self.tool.accent
        prefs = self.prefs
        for control, key, options in ((self.source, "source", SOURCES), (self.gain, "gain", GAINS),
                                      (self.output, "output", OUTPUTS), (self.mode, "mode", MODES),
                                      (self.align, "align", ALIGNS)):
            keys = [k for k, _ in options]
            if prefs[key] in keys:
                control.index = keys.index(prefs[key])
        self.count.index = max(0, min(2, int(prefs["count"]) - 1))
        self.fade.index = next((i for i, (k, _) in enumerate(FADES) if int(k) == int(prefs["fade"])), 2)
        hint = "進行中也能改，會記在字幕紀錄" if self.running else ""
        draw_text(screen, "即時字幕", (x, y), 16, theme.TEXT, bold=True)
        draw_text(screen, hint or hardware.describe(), (x + inner, y + 10), 11,
                  accent if hint else theme.TEXT_FAINT, right=True)
        y += 34

        y = self._heading("聲音來源", x, y)
        self.source.draw(screen, pygame.Rect(x, y, inner, 30), mouse_pos)
        y += 36
        draw_text(screen, widgets.clip_text(SOURCE_NOTES[prefs["source"]], 12, inner), (x, y), 12, theme.TEXT_FAINT)
        y += 22
        if prefs["source"] == "app":
            if not self.programs and not self._programs_busy:
                self._refresh_programs()
            options = [(str(pid), name) for pid, name in self.programs] or [("", "（目前沒有程式在播放聲音）")]
            self._set_options(self.program_pick, options)
            self.program_pick.draw(screen, pygame.Rect(x, y, inner - 96, 30), mouse_pos)
            self.btn_refresh.draw(screen, pygame.Rect(x + inner - 88, y, 88, 30), mouse_pos)
            y += 40
        draw_text(screen, "收音靈敏度", (x, y + 7), 13, theme.TEXT)
        self.gain.draw(screen, pygame.Rect(x + 90, y, inner - 90, 30), mouse_pos)
        y += 36
        note = GAIN_NOTES[prefs["gain"]]
        engine = self.engine
        if self.running and engine is not None and engine.state == "running":
            note = f"目前放大 {engine.gain:.1f} 倍" + ("（聲音很小，建議把電腦音量開大一點）" if engine.gain >= 7.5 else "")
        draw_text(screen, widgets.clip_text(note, 12, inner), (x, y), 12, theme.TEXT_FAINT)
        y += 28

        draw_text(screen, "原文語言", (x, y + 6), 14, theme.TEXT, bold=True)
        if not self.language.is_open:
            self.language.set_value(prefs["language"])        # 畫面一律跟著設定值
        if not self.target.is_open:
            self.target.set_value(prefs["target"])
        self.language.draw(screen, pygame.Rect(x + inner - 170, y, 170, 30), mouse_pos)
        y += 40
        if prefs["language"] == "auto":
            draw_text(screen, "知道是什麼語言的話直接指定，比較快也比較不會判斷錯", (x, y), 12, theme.TEXT_FAINT)
            y += 22

        draw_text(screen, "專有名詞", (x, y + 6), 14, theme.TEXT, bold=True)
        self._set_options(self.names_pick, self._names_options(), self.names["active"])
        if not self.names_pick.is_open:
            self.names_pick.set_value(self.names["active"])
        self.btn_names.draw(screen, pygame.Rect(x + inner - 170 - 64, y, 56, 30), mouse_pos)
        self.names_pick.draw(screen, pygame.Rect(x + inner - 170, y, 170, 30), mouse_pos)
        y += 36
        draw_text(screen, widgets.clip_text("人名、招式名等固定的翻法，辨識時也比較不會聽錯；字幕進行中也能改", 12, inner),
                  (x, y), 12, theme.TEXT_FAINT)
        y += 28
        draw_text(screen, "判斷誰說話", (x, y + 3), 14, theme.TEXT, bold=True)
        self.speakers_on.value = prefs["speakers"]
        self.speakers_on.draw(screen, (x + inner - 42, y + 2), mouse_pos)
        y += 28
        error = self.engine.speaker_error if self.engine is not None else ""
        if prefs["speakers"] and not error:
            draw_text(screen, "區分方式", (x + 12, y + 6), 13, theme.TEXT)
            self._set_options(self.speaker_pick, self._speaker_options(), self.speaker_data["active"])
            if not self.speaker_pick.is_open:
                self.speaker_pick.set_value(self.speaker_data["active"])
            self.btn_speaker_edit.draw(screen, pygame.Rect(x + inner - 170 - 64, y, 56, 30), mouse_pos)
            self.speaker_pick.draw(screen, pygame.Rect(x + inner - 170, y, 170, 30), mouse_pos)
            y += 36
            name, value = self._speaker_choice()
            method = speaker_profiles.METHOD_NAMES[value["method"]]
            if value["method"] == "cluster":
                choice = "自己判斷有幾個人，最近 10 秒內的顏色可能會修正"
            else:
                choice = f"{method}：同一人門檻 {value['same']:.2f}、換人門檻 {value['split']:.2f}"
            draw_text(screen, widgets.clip_text(choice, 12, inner - 12), (x + 12, y), 12, theme.TEXT_FAINT)
            y += 22
        if error:
            note, color = error, theme.WARN
        elif prefs["speakers"]:
            note, color = "不同人用不同顏色；字幕紀錄裡點一句可以複製或改那個人的顏色", theme.TEXT_FAINT
        else:
            missing = [dep for dep in diarize.DEPS if not dep.installed()]
            note = "不同人說話時用不同顏色標示" + ("（需要下載約 51 MB 的元件）" if missing else "")
            color = theme.TEXT_FAINT
        draw_text(screen, widgets.clip_text(note, 12, inner), (x, y), 12, color)
        y += 28

        y = self._heading("辨識模型", x, y, "把聲音轉成文字")
        for key, name, dep, note in asr.MODELS:
            row = pygame.Rect(x, y, inner, 44)
            chosen = key == prefs["model"]
            hover = row.collidepoint(mouse_pos)
            rounded_panel(screen, row, tuple(int(c * 0.25) for c in accent) if chosen else
                          (theme.PANEL_LIGHT if hover else theme.BG_DEEP), radius=8, alpha=220,
                          border=accent if chosen else None)
            draw_text(screen, name, (row.x + 10, row.y + 5), 13, accent if chosen else theme.TEXT, bold=True)
            tag = "建議" if key == self.recommended[0] else ("" if dep.installed() else f"需下載 {dep.size_text}")
            if tag:
                draw_text(screen, tag, (row.right - 10, row.y + 13), 11, accent if tag == "建議" else theme.TEXT_FAINT,
                          right=True)
            draw_text(screen, widgets.clip_text(note, 11, inner - 20), (row.x + 10, row.y + 25), 11, theme.TEXT_FAINT)
            self.model_rows.append((row, key))
            y += 48
        if not asr.gpu() and prefs["model"] in ("turbo", "large"):
            draw_text(screen, "沒有 NVIDIA 顯示卡時這個模型跟不上即時，建議用「輕量」", (x, y), 12, theme.WARN)
            y += 20
        elif asr.gpu():
            draw_text(screen, "玩遊戲時字幕會和遊戲搶顯示卡；卡頓的話換小一點的模型", (x, y), 12, theme.TEXT_FAINT)
            y += 20
        y += 10

        draw_text(screen, "翻譯", (x, y + 3), 14, theme.TEXT, bold=True)
        self.translate_on.value = prefs["translate"]
        self.translate_on.draw(screen, (x + 50, y), mouse_pos)
        if prefs["translate"]:
            draw_text(screen, "翻成", (x + inner - 210, y + 6), 13, theme.TEXT_DIM)
            self.target.draw(screen, pygame.Rect(x + inner - 170, y, 170, 30), mouse_pos)
        y += 40
        if prefs["translate"]:
            y = self._ollama_rows(x, y, inner, mouse_pos)
        else:
            draw_text(screen, "只顯示原文", (x, y), 12, theme.TEXT_FAINT)
            y += 22
        y += 8

        y = self._heading("字幕樣式", x, y)
        mode = self._display_mode()
        if prefs["translate"]:
            self.mode.draw(screen, pygame.Rect(x, y, inner, 30), mouse_pos)     # 沒翻譯就只有原文,不用選
            y += 38
        draw_text(screen, "日文標讀音（振假名）", (x, y + 3), 13, theme.TEXT)
        self.furigana_on.value = prefs["furigana"]
        self.furigana_on.draw(screen, (x + inner - 42, y + 2), mouse_pos)
        y += 26
        if not prefs["furigana"]:
            note, color = "原文是日文時，在漢字上方用小字標出讀音", theme.TEXT_FAINT
        elif mode == "translation":
            note, color = "目前只顯示翻譯，看不到原文的讀音；改成「原文和翻譯」或「只顯示原文」", theme.WARN
        else:
            note, color = "讀音由字典判斷，少數要看上下文的詞可能標錯（例如「辛い」）", theme.TEXT_FAINT
        draw_text(screen, widgets.clip_text(note, 12, inner), (x, y), 12, color)
        y += 26
        draw_text(screen, "對齊", (x, y + 6), 13, theme.TEXT)
        self.align.draw(screen, pygame.Rect(x + 80, y, inner - 80, 28), mouse_pos)
        y += 36
        draw_text(screen, "字型", (x, y + 6), 13, theme.TEXT)
        self.btn_font.label = widgets.clip_text(self.prefs.get("font_name") or "微軟正黑體", 13, inner - 100)
        self.btn_font.draw(screen, pygame.Rect(x + 80, y, inner - 80, 30), mouse_pos)
        y += 38
        for label, slider, text, color_key in (("字的大小", self.size, str(int(self.size.value)), "color"),
                                               ("外框粗細", self.outline, str(int(self.outline.value)), "outline_color"),
                                               ("底色深淺", self.opacity, f"{int(self.opacity.value)}%", "bg")):
            draw_text(screen, label, (x, y + 3), 13, theme.TEXT)
            draw_text(screen, text, (x + inner - 40, y + 12), 13, theme.TEXT_DIM, right=True)
            slider.draw(screen, pygame.Rect(x + 80, y + 9, inner - 170, 14), mouse_pos)
            # 原文和翻譯一起顯示時,原文共用文字的顏色(稍微淡一點):色塊拉長到「原文大小」那一列,看得出是一起的
            tall = slider is self.size and mode == "both"
            swatch = pygame.Rect(x + inner - 30, y + 1, 30, 24 + (32 if tall else 0))
            pygame.draw.rect(screen, tuple(self.prefs[color_key]), swatch, border_radius=5)
            pygame.draw.rect(screen, self.tool.accent if swatch.collidepoint(mouse_pos) else theme.PANEL_EDGE, swatch, 2,
                             border_radius=5)
            self.swatches.append((swatch, color_key))
            y += 32
            if slider is self.size and mode == "both":
                # 原文和翻譯一起顯示時,原文另外調大小(只顯示其中一種時用上面的「字的大小」)
                draw_text(screen, "原文大小", (x, y + 3), 13, theme.TEXT)
                draw_text(screen, str(int(self.size_original.value)), (x + inner - 40, y + 12), 13, theme.TEXT_DIM,
                          right=True)
                self.size_original.draw(screen, pygame.Rect(x + 80, y + 9, inner - 170, 14), mouse_pos)
                y += 32
        draw_text(screen, "右邊的色塊依序是：文字（原文和翻譯共用）、外框、底色的顏色" if mode == "both"
                  else "右邊的色塊依序是：文字、外框、底色的顏色", (x, y), 11, theme.TEXT_FAINT)
        y += 24
        draw_text(screen, "畫面上", (x, y + 6), 13, theme.TEXT)
        self.count.draw(screen, pygame.Rect(x + 80, y, inner - 80, 28), mouse_pos)
        y += 34
        draw_text(screen, "沒人說話", (x, y + 6), 13, theme.TEXT)
        self.fade.draw(screen, pygame.Rect(x + 80, y, inner - 80, 28), mouse_pos)
        y += 34
        fade_note = "字幕一直留在畫面上" if prefs["fade"] in (0, "0") else \
            f"講完 {int(prefs['fade'])} 秒都沒人說話，字幕就慢慢淡出"
        draw_text(screen, fade_note, (x, y), 12, theme.TEXT_FAINT)
        y += 22
        for line in widgets.wrap_text(FULLSCREEN_NOTE, 12, inner, max_lines=2):
            draw_text(screen, line, (x, y), 12, theme.TEXT_FAINT)
            y += 18
        y += 12

        y = self._heading("字幕紀錄", x, y, "自動存在 output\\subtitles")
        self.output.draw(screen, pygame.Rect(x, y, inner, 30), mouse_pos)
        y += 36
        draw_text(screen, widgets.clip_text(OUTPUT_NOTES[prefs["output"]], 12, inner), (x, y), 12, theme.TEXT_FAINT)
        y += 22
        return y + 8

    def _ollama_rows(self, x, y, inner, mouse_pos):
        screen = self.screen
        accent = self.tool.accent
        state = self.ollama_state
        if state == "checking":
            draw_text(screen, "檢查翻譯模型中…", (x, y), 12, theme.TEXT_FAINT)
            return y + 22
        if state == "stopped":
            draw_text(screen, "Ollama 已安裝但沒有在執行", (x, y + 6), 12, theme.WARN)
            self.btn_ollama.draw(screen, pygame.Rect(x + inner - 130, y, 130, 30), mouse_pos)
            return y + 40
        draw_text(screen, "翻譯模型", (x, y + 6), 13, theme.TEXT)
        installed = {name for name, _ in self.ollama_models}
        current = self.prefs["translator"]
        # 外觀像下拉選單(左邊是目前的模型,右邊「更換 ›」),按下去打開管理視窗
        box = pygame.Rect(x + 80, y, inner - 80, 30)
        self.btn_translator.rect = box
        hover = box.collidepoint(mouse_pos)
        rounded_panel(screen, box, theme.BG_DEEP, radius=8, alpha=220, border=accent if hover else theme.PANEL_EDGE)
        name = current if current in installed else "選擇翻譯模型"
        draw_text(screen, widgets.clip_text(name, 13, box.width - 80), (box.x + 12, box.centery - theme.font(13).get_height() // 2),
                  13, theme.TEXT if current in installed else theme.WARN)
        draw_text(screen, "更換 ›", (box.right - 12, box.centery), 12,
                  accent if hover else theme.TEXT_DIM, right=True)
        y += 36
        if self.pull is not None:
            done, total = self.pull["done"], self.pull["total"]
            text = self.pull["error"] or f"下載 {self.pull['name']} 中 " + (f"{done / total:.0%}" if total else "…")
            color = theme.WARN if self.pull["error"] else accent
        elif current not in installed:
            text, color = "還沒有選好翻譯模型：按上面的按鈕選擇或下載", theme.WARN
        else:
            reason = ollama.not_recommended(current)
            note = next((note for name, _, _, note in ollama.SUGGESTED if name == current), "")
            text, color = (f"不建議：{reason}", theme.WARN) if reason else (note or "自己另外下載的模型", theme.TEXT_FAINT)
        draw_text(screen, widgets.clip_text(text, 12, inner), (x, y), 12, color)
        y += 24
        return y

    def _draw_lines(self, rect, mouse_pos):
        screen = self.screen
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        engine = self.engine
        lines = [line for line in (engine.lines if engine else []) if line.original]
        draw_text(screen, f"字幕紀錄 ({sum(line.final and not line.notice for line in lines)})", (rect.x + 16, rect.y + 13), 15,
                  theme.TEXT, bold=True)
        right = rect.right - 14
        self.btn_open.draw(screen, pygame.Rect(right - 84, rect.y + 10, 84, 24), mouse_pos)
        right -= 92
        if lines:
            self.btn_copy_all.draw(screen, pygame.Rect(right - 72, rect.y + 10, 72, 24), mouse_pos)
            right -= 80
        if lines and not self.running:
            self.btn_clear.draw(screen, pygame.Rect(right - 52, rect.y + 10, 52, 24), mouse_pos)
        else:
            self.btn_clear.rect = pygame.Rect(0, 0, 0, 0)
        pygame.draw.line(screen, theme.PANEL_EDGE, (rect.x + 12, rect.y + 44), (rect.right - 12, rect.y + 44))
        area = pygame.Rect(rect.x, rect.y + 45, rect.width, rect.height - 45)
        self.lines_area = area
        if not lines:
            cx = area.centerx
            if self.running:
                text = engine.message if engine.state == "loading" else "等待有人說話…"
                draw_text(screen, text, (cx, area.centery - 10), 15, theme.TEXT_DIM, center=True)
            else:
                draw_text(screen, "選好設定後按「開始字幕」", (cx, area.centery - 22), 16, theme.TEXT_DIM, center=True)
                hint = "字幕會浮在所有視窗上層，滑鼠點得到後面的東西；這裡會留下完整紀錄"
                for number, row in enumerate(widgets.wrap_text(hint, 13, area.width - 40, max_lines=3)):
                    draw_text(screen, row, (cx, area.centery + 6 + number * 20), 13, theme.TEXT_FAINT, center=True)
            return
        width = area.width - 90
        blocks = []
        for line in lines:
            if line.notice:                     # 變更設定的說明:小字、不能點
                original, translated = widgets.wrap_text(f"── {line.original}", 12, width), []
            elif line.same:
                original, translated = [], widgets.wrap_text(line.translation or line.original, 15, width)
            else:
                original = widgets.wrap_text(line.original, 12, width)
                translated = widgets.wrap_text(line.translation, 15, width) if line.translation else []
            blocks.append((line, original, translated, 12 + len(original) * 18 + len(translated) * 22))
        content = sum(block[3] for block in blocks) + 10
        view = self.lines_view
        view.layout(area, content)
        if self.follow:
            view.set_scroll(view.max_scroll)
        screen.set_clip(area)
        y = area.y + 6 - view.scroll
        self.line_rects = []
        for line, original, translated, height in blocks:
            if y + height >= area.y and y <= area.bottom:
                row_rect = pygame.Rect(area.x + 6, y - 3, area.width - 20, height)
                color = self.color_of(line.speaker) if self.prefs["speakers"] and not line.notice else None
                if color:
                    # 判斷誰說話:淡淡的底色 + 左邊一條,同一個人同一個顏色
                    rounded_panel(screen, row_rect, color, radius=6, alpha=36)
                    pygame.draw.rect(screen, color, (row_rect.x, row_rect.y + 4, 3, row_rect.height - 8),
                                     border_radius=2)
                if line.final and not line.notice:
                    self.line_rects.append((row_rect.clip(area), line))
                    if row_rect.collidepoint(mouse_pos) and area.collidepoint(mouse_pos):
                        if color:
                            pygame.draw.rect(screen, color, row_rect, 1, border_radius=6)     # 外框:點了會改這個人
                            hint = "點一下：複製或改這個人的顏色"
                        else:
                            rounded_panel(screen, row_rect, theme.PANEL_LIGHT, radius=6, alpha=160)
                            hint = "點一下複製"
                        draw_text(screen, hint, (row_rect.right - 8, y + 9), 11, theme.TEXT_FAINT, right=True)
                dim = self.tool.accent if line.notice else (theme.TEXT_FAINT if line.final else theme.TEXT_DIM)
                draw_text(screen, _clock(line.start), (area.x + 16, y + 2), 12, theme.TEXT_FAINT)
                row_y = y
                for row in original:
                    draw_text(screen, row, (area.x + 72, row_y), 12, dim)
                    row_y += 18
                for row in translated:
                    draw_text(screen, row, (area.x + 72, row_y), 15, theme.TEXT if line.final else theme.TEXT_DIM)
                    row_y += 22
            y += height
        screen.set_clip(None)
        view.draw(screen, mouse_pos)

    def _draw_footer(self, rect, mouse_pos):
        screen = self.screen
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        engine = self.engine
        text, color = self.notice
        if not text and engine is not None:
            if engine.state == "error":
                text, color = engine.message, theme.DANGER
            elif engine.state == "loading":
                text, color = engine.message, theme.TEXT_DIM
            elif engine.state == "running":
                parts = [engine.message]
                if engine.delays:
                    parts.append(f"說完到翻譯好約 {sum(engine.delays) / len(engine.delays):.1f} 秒")
                if engine.costs:
                    parts.append(f"辨識每次 {sum(engine.costs) / len(engine.costs):.2f} 秒")
                text, color = "，".join(parts), theme.TEXT_DIM
        if not text:
            text, color = self.advice, theme.TEXT_FAINT
        right = rect.right - 18
        self.btn_start.label = "停止字幕" if self.running else "開始字幕"
        self.btn_start.draw(screen, pygame.Rect(right - 130, rect.y + 18, 130, 38), mouse_pos)
        right -= 142
        self.btn_adjust.label = "完成調整" if self.adjusting else "調整位置"
        self.btn_adjust.draw(screen, pygame.Rect(right - 110, rect.y + 18, 110, 38), mouse_pos)
        right -= 122
        lines = widgets.wrap_text(text, 13, right - rect.x - 18, max_lines=2)
        for number, line in enumerate(lines):
            draw_text(screen, line, (rect.x + 18, rect.y + (27 if len(lines) == 1 else 17) + number * 20), 13, color)

    # ------------------------------------------------------------ 事件

    def _set_style(self, key, value):
        self.prefs[key] = value
        self._save()
        if self.overlay.alive:
            self.overlay.style(self._style())

    def _pick_font(self, face_id):
        face = fonts.CATALOG.get(face_id)
        if face is None:
            return
        self.prefs.update(font=face.id, font_name=face.name, font_path=str(face.path), font_index=face.index)
        self._save()
        if self.overlay.alive:
            self.overlay.style(self._style())

    def modal_open(self):
        return self.font_picker.is_open or self.names_dialog.is_open or self.translator_dialog.is_open \
            or self.speaker_dialog.is_open

    def draw_modal(self, mouse_pos):
        if self.translator_dialog.is_open:
            self.translator_dialog.draw(mouse_pos)
        elif self.speaker_dialog.is_open:
            self.speaker_dialog.draw(mouse_pos)
        elif self.names_dialog.is_open:
            self.names_dialog.update()
            self.names_dialog.draw(mouse_pos)
        else:
            self.font_picker.draw(mouse_pos)

    def handle_modal_event(self, event, mouse_pos):
        if self.translator_dialog.is_open:
            self.translator_dialog.handle_event(event, mouse_pos)
        elif self.speaker_dialog.is_open:
            self.speaker_dialog.handle_event(event, mouse_pos)
        elif self.names_dialog.is_open:
            self.names_dialog.handle_event(event, mouse_pos)
        else:
            self.font_picker.handle_event(event, mouse_pos)

    def handle_event(self, event, mouse_pos):
        if self.menu.handle_event(event, mouse_pos):
            return
        if self.palette.handle_event(event, mouse_pos):
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3 and self.lines_area.collidepoint(mouse_pos):
            hit = next(((rect, line) for rect, line in self.line_rects if rect.collidepoint(mouse_pos)), None)
            if hit is not None:
                self._line_menu(*hit, mouse_pos)
            return
        pairs = ((self.program_pick, None), (self.language, "language"), (self.names_pick, "names"),
                 (self.speaker_pick, "speaker_profile"), (self.target, "target"))
        opened = [pair for pair in pairs if pair[0].is_open]
        # 有選單開著時只交給它(點在外面就只是收起來),不會同時打開另一個
        for dropdown, key in opened or pairs:
            before = dropdown.value
            if dropdown.handle(event, mouse_pos):
                if key == "names" and dropdown.value != before:
                    self.names["active"] = dropdown.value       # 進行中也能換,馬上生效
                    self._names_changed(self.names)
                    return
                if key == "speaker_profile":
                    if dropdown.value != before:
                        self.speaker_data["active"] = dropdown.value    # 進行中也能換,下一句開始套用
                        self._speaker_profiles_changed(self.speaker_data)
                    return
                if key is None and dropdown.value != before:
                    self._change_program(int(dropdown.value or 0) or None)     # 單一程式:換程式
                elif key and dropdown.value != before and dropdown.value:
                    self._change(key, dropdown.value)
                return
        # 捲動條在設定區右邊緣外一點;拖曳捲動條、中鍵自動捲動時滑鼠移出範圍也要繼續交給它(才收得到放開)
        settings, lines = self.settings_view, self.lines_view
        if (self.settings_area.collidepoint(mouse_pos) or settings.grabbing(mouse_pos)) \
                and settings.handle_event(event, mouse_pos):
            return
        if (self.lines_area.collidepoint(mouse_pos) or lines.grabbing(mouse_pos)) \
                and lines.handle_event(event, mouse_pos):
            self.follow = lines.scroll >= lines.max_scroll - 4
            return
        for slider, key in ((self.size, "size"), (self.size_original, "size_original"), (self.opacity, "opacity"),
                            (self.outline, "outline")):
            if slider.handle(event, mouse_pos):
                self.prefs[key] = int(slider.value)
                if self.overlay.alive:
                    self.overlay.style(self._style())
                if event.type == pygame.MOUSEBUTTONDOWN:
                    self._save()
                return
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._save()
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        self._click(mouse_pos)

    def _click(self, pos):
        prefs = self.prefs
        if self.btn_start.clicked(pos, True):
            self.stop() if self.running else self.start()
            return
        if self.btn_adjust.clicked(pos, True):
            self._toggle_adjust()
            return
        if self.btn_clear.clicked(pos, True) and not self.running:
            self.engine = None
            self.session = None
            return
        if self.btn_copy_all.clicked(pos, True):
            self._copy(self._transcript_lines(), "全部字幕")
            return
        if self.btn_open.clicked(pos, True):
            output_dir().mkdir(parents=True, exist_ok=True)
            os.startfile(output_dir())
            return
        if self.lines_area.collidepoint(pos):
            hit = next(((rect, line) for rect, line in self.line_rects if rect.collidepoint(pos)), None)
            if hit is not None:
                rect, line = hit
                if self.prefs["speakers"] and line.speaker is not None:
                    self._line_menu(rect, line, pos)    # 開了判斷誰說話:點一句打開選單(複製、改顏色)
                else:
                    self._copy([line], "這一句")
            return
        in_settings = self.settings_area.collidepoint(pos)
        if not in_settings:
            return
        if self.mode.clicked(pos, True):
            self._set_style("mode", self.mode.value)
            return
        if self.count.clicked(pos, True):
            self._set_style("count", int(self.count.value))
            self._dirty = True
            return
        if self.fade.clicked(pos, True):
            self._set_style("fade", int(self.fade.value))
            return
        if self.output.clicked(pos, True):
            self.prefs["output"] = self.output.value
            self._save()
            self._save_transcript(force=True)
            return
        if self.gain.clicked(pos, True):
            self._change("gain", self.gain.value)
            if self.engine is not None and self.running:
                self.engine.settings.gain = self.prefs["gain"]
            return
        if self.align.clicked(pos, True):
            self._set_style("align", self.align.value)
            return
        if self.btn_speaker_edit.clicked(pos, True):
            # 重新讀資料夾(別人給的設定檔放進去就看得到);第一次編輯時建立預設的設定檔
            self.speaker_data = speaker_profiles.load(self.app.config, create=True)
            self.speaker_dialog.open(self.speaker_data)
            return
        if self.btn_names.clicked(pos, True):
            self.names = glossary.load(self.app.config)     # 重新讀資料夾:別人給的設定檔放進去就看得到
            self.names_dialog.open(self.names)
            return
        if self.btn_font.clicked(pos, True):
            self.font_picker.open(prefs.get("font", ""), self._pick_font)
            return
        for rect, key in self.swatches:
            if rect.collidepoint(pos):
                self.palette.open(rect, tuple(prefs[key]), lambda color, key=key: self._set_style(key, list(color)))
                return
        if self.btn_translator.clicked(pos, True):
            self.translator_dialog.open()
            return
        if self.btn_ollama.clicked(pos, True):
            if ollama.launch():
                self.notice = ("正在打開 Ollama…", theme.TEXT_DIM)
                self._ollama_checked = time.monotonic() - 1
            return
        if self.source.clicked(pos, True):
            if self.source.value == "app":
                self._refresh_programs()
            self._change("source", self.source.value)
        elif self.btn_refresh.clicked(pos, True):
            self._refresh_programs()
        elif self.speakers_on.clicked(pos, True):
            self.speakers_on.value = prefs["speakers"]
            self._set_speakers(not prefs["speakers"])
        elif self.furigana_on.clicked(pos, True):
            self.furigana_on.value = prefs["furigana"]
            self._set_furigana(not prefs["furigana"])
        elif self.translate_on.clicked(pos, True):
            self._change("translate", self.translate_on.value)
            self.translate_on.value = prefs["translate"]
            if self.overlay.alive:
                self.overlay.style(self._style())       # 翻譯開關會改變顯示方式(關掉時只顯示原文)
        else:
            row = next((key for rect, key in self.model_rows if rect.collidepoint(pos)), None)
            if row is not None:
                missing = asr.required(row)
                if missing and self.running:            # 字幕進行中換成還沒下載的模型:先問要不要下載
                    self.app.consent.open("辨識模型", missing, on_done=lambda: self._change("model", row))
                else:
                    self._change("model", row)

    def _set_speakers(self, on):
        """打開時元件還沒下載:先詢問,下載好才打開。字幕進行中也能開關(下一句開始判斷)。"""
        if on and any(not dep.installed() for dep in diarize.DEPS):
            self.app.consent.open("判斷誰說話", diarize.DEPS, on_done=lambda: self._set_speakers(True))
            return
        self._change("speakers", on)
        self._dirty = True

    def color_of(self, speaker):
        """第幾個人的顏色(使用者改過的優先);判斷不了的句子沒有顏色。"""
        if speaker is None:
            return None
        return tuple(self.speaker_colors.get(speaker, speakers.COLORS[speaker % len(speakers.COLORS)][1]))

    def _line_menu(self, rect, line, pos):
        """字幕紀錄裡一句的選單(左鍵、右鍵都是這個):複製這一句、改這個人的顏色。"""
        can_color = self.prefs["speakers"] and line.speaker is not None
        self.menu.open(pos, [("複製這一句", "", True, lambda: self._copy([line], "這一句")),
                             ("改這個人的顏色", "", can_color, lambda: self._recolor(line, rect))])

    def _recolor(self, line, anchor):
        """改這個人的顏色:同一個人的每一句(字幕視窗、字幕紀錄、存檔)一起換。"""
        def pick(color, speaker=line.speaker):
            if color is not None:
                self.speaker_colors[speaker] = tuple(color)
                self._dirty = True
                self._save_transcript(force=True)
        self.palette.open(anchor, self.color_of(line.speaker), pick)

    def _set_furigana(self, on):
        """打開時字典還沒下載:先詢問,下載好才打開。"""
        if on and not furigana.available():
            self.app.consent.open("日文標讀音", [furigana.DICTIONARY], on_done=lambda: self._set_furigana(True))
            return
        self.prefs["furigana"] = on
        if self.overlay.alive:
            self.overlay.style(self._style())
        self._dirty = True                  # 字幕視窗上的字馬上重畫
        self._save()

    def _pull(self, name):
        if self.pull is not None:
            self.notice = (f"正在下載 {self.pull['name']}，下載完才能再下載其他模型", theme.WARN)
            self._notice_until = time.monotonic() + 3
            return
        self.notice = (f"開始下載 {name}，下載完會自動選用（進度顯示在翻譯模型下面）", self.tool.accent)
        self._notice_until = time.monotonic() + 4
        job = {"name": name, "done": 0, "total": 0, "error": "", "cancel": threading.Event()}
        self.pull = job

        def work():
            try:
                if ollama.pull(name, lambda done, total: job.update(done=done, total=total), job["cancel"]) is False:
                    self.notice = (f"已取消下載 {name}", theme.TEXT_DIM)       # 沒下載完的部分已刪掉
                    self._notice_until = time.monotonic() + 3
                else:
                    self.prefs["translator"] = name
                    self._save()
                self.pull = None
            except Exception as exc:
                job["error"] = f"下載失敗：{exc}"[:40]
                time.sleep(4)
                self.pull = None
            self._refresh_ollama(force=True)

        threading.Thread(target=work, daemon=True).start()
