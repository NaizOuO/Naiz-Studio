"""干涉環分析的畫面:左上是強度圖(可縮放、平移、拉比例尺與橫切線),左下是亮度剖面(可縮放、平移、加刪拖峰、
拖中線),右邊是一步一步的設定:照片、比例尺、強度圖、橫切線、找峰、中心、輸出。Ctrl+Z 復原、Ctrl+Y 重做。

座標一律以原圖像素計算;畫面上的縮放只影響顯示。橫切線可以是任意方向,剖面上的位置是「沿線距離」(px)。
"""

import copy
import json
import os
import math
import time
from pathlib import Path

import numpy as np
import pygame

from core import paths, theme, widgets, winfile
from core.plugins import Page
from core.scroll import ScrollView
from core.widgets import Button, SegmentedControl, Slider, TextInput, Toggle, draw_text, rounded_panel

from . import analysis, xlsx

SIDE_W = 340
PLOT_RATIO = 0.36               # 下面剖面圖佔左邊的高度比例
HANDLE = 9                      # 端點、峰標記可以抓的範圍(螢幕像素)
TOOLS = [("view", "檢視"), ("ruler", "比例尺"), ("line", "橫切線")]
TOOL_HINTS = {"view": "滾輪縮放；左鍵、右鍵或中鍵拖曳平移",
              "ruler": "拖尺的兩端對齊照片上刻度尺的兩個刻度；拖中間移動整把尺",
              "line": "按住左鍵拖曳拉出橫切線（任意方向）；拖兩端改位置、拖線的中間移動整條"}
PLOT_HINT = "剖面圖：滾輪縮放、拖曳平移；雙擊加峰、拖峰移動、右鍵刪峰；拖綠色中線可以自己定中心"
DOUBLE_CLICK_MS = 400
HISTORY = 100
SPLITS = [("3", "三條分裂"), ("1", "單條")]
SPLIT_NOTES = {"3": "每級有 3 條（1-、1、1+）：有磁場的 Zeeman 分裂",
               "1": "每級 1 條（1、2、3）：沒加磁場、或沒有分裂時"}
ORDERS = [("1", "1 級"), ("2", "2 級"), ("3", "3 級"), ("4", "4 級")]
SAVE_EVERY = 1.0                # 每隔這麼多秒(有改動時)自動存一次進度
IMAGE_FILTERS = [("圖片 (*.png;*.jpg;*.jpeg;*.tif;*.dng)", "*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.bmp;*.dng")]
LINE_COLOR = (255, 214, 90)
RULER_COLOR = (90, 200, 255)
PEAK_COLOR = (255, 120, 150)
CENTER_COLOR = (120, 230, 140)


def output_dir():
    return paths.OUTPUT_DIR / "zeeman"


def text_at(surface, text, pos, size=16, color=theme.TEXT, bold=False, center=False, right=False):
    """和 draw_text 一樣,但靠右對齊時 pos 的 y 也是文字的上緣(draw_text 靠右時是垂直中間,表格會上下錯開)。"""
    if right:
        pos = (pos[0], pos[1] + theme.font(size, bold).get_height() // 2)
    return draw_text(surface, text, pos, size, color, bold=bold, center=center, right=right)


def _cell(value):
    """CSV 列裡的值:數字字串換回數字(Excel 才能直接算)。"""
    if isinstance(value, str):
        number = _num(value)
        if number is not None and value.strip() not in ("",):
            return number
    return value


def _num(text, default=None):
    try:
        return float(str(text).strip())
    except ValueError:
        return default


def _fmt(value, digits=2):
    return "" if value is None else f"{value:.{digits}f}".rstrip("0").rstrip(".")


class Photo:
    """一張照片的狀態:橫切線、峰、配對、中心、電流(比例尺和找峰參數整批共用,放在 page)。"""

    def __init__(self, path):
        self.path = Path(path)
        self.line = None                # ((x0, y0), (x1, y1));還沒拉時是 None
        self.peaks = []                 # [{"x": 沿線距離 px, "label": 標籤}]
        self.excluded = []              # 不拿來算中心的標籤(例如中心偏掉的那組)
        self.center_manual = None       # 自己拖的中線(沿線距離);None 是自動定位
        self.current = ""               # 電流(使用者輸入)
        self.labels_edited = False

    def snapshot(self):
        return copy.deepcopy((self.line, self.peaks, self.excluded, self.center_manual, self.labels_edited))

    def restore(self, state):
        self.line, self.peaks, self.excluded, self.center_manual, self.labels_edited = copy.deepcopy(state)

    def to_json(self):
        return {"path": str(self.path), "line": self.line, "peaks": self.peaks, "excluded": self.excluded,
                "center_manual": self.center_manual, "current": self.current, "labels_edited": self.labels_edited}

    @classmethod
    def from_json(cls, data):
        photo = cls(data["path"])
        line = data.get("line")
        photo.line = tuple(tuple(point) for point in line) if line else None
        photo.peaks = [{"x": float(p["x"]), "label": str(p.get("label", ""))} for p in data.get("peaks", [])]
        photo.excluded = list(data.get("excluded", []))
        photo.center_manual = data.get("center_manual")
        photo.current = str(data.get("current", ""))
        photo.labels_edited = bool(data.get("labels_edited"))
        return photo


class ZeemanPage(Page):
    def __init__(self, app, tool):
        super().__init__(app, tool)
        accent = tool.accent
        self.accent = accent
        self.photos = []
        self.index = -1
        self.rgb = None
        self.intensity = None
        self.gray = None
        self.surface = None             # 整張強度圖(顯示用)
        self._view_cache = None
        self.zoom, self.offset = 1.0, [0.0, 0.0]
        self.canvas = pygame.Rect(0, 0, 0, 0)
        self.plot = pygame.Rect(0, 0, 0, 0)
        self.tool_pick = SegmentedControl(TOOLS, accent=accent)
        self.drag = None
        self.message, self.message_color = "把照片拖進來，或按「加入照片」", theme.TEXT_DIM
        # 比例尺(整批共用)
        self.ruler = None               # [(x, y), (x, y)] 原圖像素
        self.px_per_mm = None
        self.ruler_mm = TextInput("20", accent=accent, size=14)
        self.manual_scale = TextInput("", placeholder="直接輸入 px/mm", accent=accent, size=14)
        # 強度圖
        self.channel = SegmentedControl([(k, n) for k, n in analysis.CHANNELS], accent=accent)
        self.stretch = Toggle(True, accent=accent)
        # 橫切線
        self.in_x0 = TextInput("", accent=accent, size=13)
        self.in_y0 = TextInput("", accent=accent, size=13)
        self.in_x1 = TextInput("", accent=accent, size=13)
        self.in_y1 = TextInput("", accent=accent, size=13)
        self.in_band = TextInput("30", accent=accent, size=13)
        self.btn_level = Button("拉平", filled=False, size=12)
        self.line_fields = (self.in_x0, self.in_y0, self.in_x1, self.in_y1)
        # 剖面處理與找峰
        self.smooth_on = Toggle(True, accent=accent)
        self.sigma = Slider(0, 10, 2.0, step=0.5, accent=accent)
        self.despike_on = Toggle(True, accent=accent)
        self.dip_width = Slider(3, 60, 15, step=1, accent=accent)
        self.prominence = Slider(1, 80, 15, step=1, accent=accent)
        self.distance = Slider(2, 200, 40, step=1, accent=accent)
        self.per_order = SegmentedControl(SPLITS, accent=accent)
        self.orders = SegmentedControl(ORDERS, index=1, accent=accent)        # 預設 2 級
        self.center_order = SegmentedControl(ORDERS, index=0, accent=accent)  # 用第幾級定中心(預設第 1 級)
        # Zeeman 分析需要的實驗常數
        self.field_k = TextInput("", placeholder="T/A", accent=accent, size=13)
        self.field_b = TextInput("", placeholder="T", accent=accent, size=13)       # B = k·I + b 的截距
        self.use_b = Toggle(True, accent=accent)
        self.etalon_t = TextInput("", placeholder="mm", accent=accent, size=13)
        self.etalon_n = TextInput("1", accent=accent, size=13)
        self.wavelength = TextInput("643.847", accent=accent, size=13)
        self.btn_drop_unlabeled = Button("刪掉沒標籤的峰", filled=False, size=12)
        self.btn_clear_peaks = Button("清除全部峰", filled=False, size=12)
        self.btn_clear_photos = Button("清空清單", filled=False, size=12)
        self.btn_line_all = Button("套用到全部照片", filled=False, size=12)
        self.last_line = None           # 新加入的照片沿用這條橫切線(同一批照片位置差不多)
        self._center_memo = None
        self.btn_undo = Button("復原", filled=False, size=11)
        self.btn_redo = Button("重做", filled=False, size=11)
        self.btn_find = Button("自動找峰", accent=accent, size=13)
        self.btn_relabel = Button("重新標籤", filled=False, size=12)
        self.btn_auto_center = Button("自動定位中線", accent=accent, size=12)
        self.btn_plot_all = Button("顯示全部", filled=False, size=11)
        self.plot_range = None          # 剖面圖放大時看的範圍(沿線距離);None 是整條
        self.undo_stack, self.redo_stack = [], []
        self._last_click = (0, None)
        self.selected = None            # 選中的峰(在 photo.peaks 裡的位置)
        self.label_edit = TextInput("", placeholder="標籤，例如 1-", accent=accent, size=13)
        self.btn_delete_peak = Button("刪除這個峰", filled=False, size=12)
        self.pair_rects = []
        self.peak_rects = []
        # 照片與輸出
        self.btn_add = Button("加入照片", accent=accent, size=13)
        self.btn_remove = Button("移出清單", filled=False, size=12)
        self.btn_prev = Button("‹", filled=False, size=14)
        self.btn_next = Button("›", filled=False, size=14)
        self.current_in = TextInput("", placeholder="例如 2.5 A", accent=accent, size=13)
        self.title_in = TextInput("", placeholder="圖的標題（可以留空）", accent=accent, size=13)
        # 輸出剖面圖的大小和強度範圍(想要強度扁一點:高度調小,或把範圍放大)
        self.out_w = TextInput("1600", accent=accent, size=13)
        self.out_h = TextInput("700", accent=accent, size=13)
        self.y_min = TextInput("", placeholder="自動", accent=accent, size=13)
        self.y_max = TextInput("", placeholder="自動", accent=accent, size=13)
        self.btn_csv = Button("輸出 Excel", accent=accent, size=13)
        self.btn_open_output = Button("開啟輸出資料夾", filled=False, size=12)
        self.btn_png = Button("輸出圖片 PNG", filled=False, size=13)
        self.btn_save_cfg = Button("存設定檔", filled=False, size=12)
        self.btn_load_cfg = Button("讀設定檔", filled=False, size=12)
        self.view = ScrollView(accent=accent)
        self.side = pygame.Rect(0, 0, 0, 0)
        self.inputs = [self.ruler_mm, self.manual_scale, self.in_x0, self.in_y0, self.in_x1, self.in_y1, self.in_band,
                       self.label_edit, self.current_in, self.title_in, self.out_w, self.out_h, self.y_min, self.y_max,
                       self.field_k, self.field_b, self.etalon_t, self.etalon_n, self.wavelength]
        self.sliders = [self.sigma, self.dip_width, self.prominence, self.distance]
        self._profile_cache = None
        self._load_calibration()
        self._saved_state = None
        self._saved_at = 0.0
        self._restore_session()

    # ------------------------------------------------------------ 照片

    @property
    def photo(self):
        return self.photos[self.index] if 0 <= self.index < len(self.photos) else None

    def add_files(self, files):
        added = 0
        for path in files:
            path = Path(path)
            if path.suffix.lower() in analysis.IMAGE_EXTS and path.is_file() \
                    and all(p.path != path for p in self.photos):
                self.photos.append(Photo(path))
                added += 1
        if added:
            self.select(len(self.photos) - added)
            self._say(f"加入 {added} 張照片；比例尺和找峰的設定整批共用", self.accent)
        elif files:
            self._say("只支援 PNG、JPG、TIFF、BMP、DNG", theme.WARN)

    def select(self, index):
        self._sync_current()
        self.index = index
        photo = self.photo
        self.selected = None
        self.rgb = self.intensity = self.gray = self.surface = None
        self._view_cache = self._profile_cache = None
        if photo is None:
            return
        try:
            self.rgb = analysis.load_rgb(photo.path)
        except Exception as exc:
            self._say(f"讀不了 {photo.path.name}：{exc}"[:80], theme.WARN)
            return
        self._rebuild_intensity()
        self.current_in.set_text(photo.current)
        if photo.line is None:
            h, w = self.intensity.shape
            photo.line = self.last_line or ((w * 0.1, h / 2), (w * 0.9, h / 2))
        self.plot_range = None
        if self.ruler is None:
            h, w = self.intensity.shape
            length = (self.px_per_mm or w / 60) * (_num(self.ruler_mm.text, 20) or 20)
            self.ruler = [(w / 2 - length / 2, h * 0.75), (w / 2 + length / 2, h * 0.75)]
        self._sync_line_inputs()
        self._fit()
        if not photo.peaks:
            self.find_peaks()

    def _rebuild_intensity(self):
        self.intensity = analysis.channel(self.rgb, self.channel.value)
        self.gray = analysis.display_gray(self.intensity, self.stretch.value)
        rgb = np.repeat(self.gray[..., None], 3, axis=2)
        self.surface = pygame.surfarray.make_surface(rgb.transpose(1, 0, 2))
        self._view_cache = self._profile_cache = None

    def _session_path(self):
        return self.tool.data_dir() / "session.json"

    def _session(self):
        self._sync_current()
        return {"說明": "干涉環分析的進度（照片清單、每張的橫切線和峰、共用設定），開啟時自動接著做",
                "photos": [photo.to_json() for photo in self.photos], "index": self.index,
                "ruler": self.ruler, "settings": self._settings()}

    def save_session(self, force=False):
        """有改動就存(每秒最多一次):關掉程式或畫面出錯後重開,照片和編輯都還在。"""
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
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name + ".tmp")
            temp.write_text(state, encoding="utf-8")
            temp.replace(path)                  # 寫到一半被關掉也不會壞掉
            self._saved_state = state
        except OSError:
            pass

    def _restore_session(self):
        try:
            data = json.loads(self._session_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self._apply_settings(data.get("settings") or {})
        ruler = data.get("ruler")
        self.ruler = [tuple(point) for point in ruler] if ruler else None
        missing = 0
        for item in data.get("photos", []):
            try:
                photo = Photo.from_json(item)
            except (KeyError, TypeError, ValueError):
                continue
            if photo.path.is_file():
                self.photos.append(photo)
            else:
                missing += 1
        if self.photos:
            self.select(min(max(0, int(data.get("index", 0))), len(self.photos) - 1))
            self._say(f"接著上次的進度：{len(self.photos)} 張照片" + (f"（{missing} 張找不到，略過）" if missing else ""),
                      self.accent)
        self._saved_state = json.dumps(self._session(), ensure_ascii=False)

    def update(self):
        self.save_session()

    def leave(self, proceed):
        self.save_session(force=True)
        proceed()

    def remember(self):
        """改之前先記下這張照片的樣子(Ctrl+Z 回到這裡)。"""
        photo = self.photo
        if photo is not None:
            self.undo_stack = (self.undo_stack + [(self.index, photo.snapshot())])[-HISTORY:]
            self.redo_stack = []

    def undo(self, redo=False):
        source, target = (self.redo_stack, self.undo_stack) if redo else (self.undo_stack, self.redo_stack)
        while source:
            index, state = source.pop()
            if index < len(self.photos):
                if index != self.index:
                    self.select(index)
                photo = self.photo
                target.append((index, photo.snapshot()))
                photo.restore(state)
                self._profile_cache = None
                self.selected = None
                self._sync_line_inputs()
                self._say("已重做" if redo else "已復原")
                return

    def _sync_current(self):
        photo = self.photo
        if photo is not None:
            photo.current = self.current_in.text.strip()

    # ------------------------------------------------------------ 比例尺

    def _calibration_path(self):
        return self.tool.data_dir() / "calibration.json"

    def _load_calibration(self):
        try:
            data = json.loads(self._calibration_path().read_text(encoding="utf-8"))
            self.px_per_mm = float(data["px_per_mm"]) or None
            self.ruler_mm.set_text(_fmt(float(data.get("ruler_mm", 20))))
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def save_calibration(self):
        if not self.px_per_mm:
            self._say("還沒有比例尺", theme.WARN)
            return
        path = self._calibration_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        analysis.write_json(path, {"px_per_mm": self.px_per_mm, "ruler_mm": _num(self.ruler_mm.text, 20)})
        self._say(f"已存比例尺 {self.px_per_mm:.2f} px/mm，下次開啟、其他照片都會用這個值", self.accent)

    def _ruler_changed(self):
        length = _num(self.ruler_mm.text)
        if self.ruler and length and length > 0:
            (x0, y0), (x1, y1) = self.ruler
            self.px_per_mm = math.hypot(x1 - x0, y1 - y0) / length
            self.manual_scale.set_text("")

    # ------------------------------------------------------------ 橫切線與剖面

    def _band(self):
        return max(1, int(_num(self.in_band.text, 30) or 30))

    def _sync_line_inputs(self):
        photo = self.photo
        if photo is None or photo.line is None:
            return
        (x0, y0), (x1, y1) = photo.line
        for field, value in zip(self.line_fields, (x0, y0, x1, y1)):
            if not field.focused:
                field.set_text(str(int(round(value))))

    def _apply_line_inputs(self):
        photo = self.photo
        if photo is None or self.intensity is None:
            return
        h, w = self.intensity.shape
        (x0, y0), (x1, y1) = photo.line
        values = [_num(field.text, old) for field, old in zip(self.line_fields, (x0, y0, x1, y1))]
        x0, x1 = (min(max(0, v), w - 1) for v in (values[0], values[2]))
        y0, y1 = (min(max(0, v), h - 1) for v in (values[1], values[3]))
        if math.hypot(x1 - x0, y1 - y0) >= 10:
            photo.line = ((x0, y0), (x1, y1))
            self._profile_cache = None
            self.plot_range = None

    def profile(self):
        """(x, 原始, 處理後);參數沒變就用上次算好的。"""
        photo = self.photo
        if photo is None or self.intensity is None or photo.line is None:
            return None
        key = (photo.path, photo.line, self._band(), self.channel.value, self.smooth_on.value, self.sigma.value,
               self.despike_on.value, self.dip_width.value)
        if self._profile_cache is None or self._profile_cache[0] != key:
            xs, raw = analysis.profile_line(self.intensity, *photo.line, self._band())
            done = analysis.process(raw, self.sigma.value if self.smooth_on.value else 0, self.dip_width.value,
                                    self.despike_on.value)
            self._profile_cache = (key, (xs, raw, done))
        return self._profile_cache[1]

    def _value_at(self, x):
        prof = self.profile()
        if prof is None:
            return 0.0
        xs, _, done = prof
        i = int(round(x - xs[0]))
        return float(done[max(0, min(len(done) - 1, i))])

    def find_peaks(self):
        prof = self.profile()
        photo = self.photo
        if prof is None:
            return
        xs, _, done = prof
        found = analysis.find_peaks(done, self.prominence.value, self.distance.value)
        photo.peaks = [{"x": round(float(xs[0]) + analysis.refine(done, i), 2), "label": ""} for i in found]
        photo.excluded = []
        photo.center_manual = None
        photo.labels_edited = False
        self.selected = None
        self.relabel()
        photo.peaks = [peak for peak in photo.peaks if peak["label"]] or photo.peaks     # 只留到設定的級數
        self.relabel()
        self._say(f"找到 {len(photo.peaks)} 條線（到第 {self.orders.value} 級）" +
                  ("；剖面圖上雙擊加峰、右鍵刪除、拖曳移動" if found else "，試著把「最小顯著度」調低"),
                  self.accent if found else theme.WARN)

    def _rough_center(self, photo=None):
        """大概的中心(分左右用):自己拖過就用拖的,不然用左右對稱的峰估計(峰的位置沒變就用上次算的)。"""
        photo = photo or self.photo
        if photo is None:
            return None
        if photo.center_manual is not None:
            return photo.center_manual
        key = (tuple(p["x"] for p in photo.peaks), self.distance.value)
        if photo is not self.photo:
            return analysis.pair_peaks(list(key[0]), max(5.0, self.distance.value * 0.5))[0]
        if self._center_memo is None or self._center_memo[0] != key:
            center, _ = analysis.pair_peaks(list(key[0]), max(5.0, self.distance.value * 0.5))
            self._center_memo = (key, center)
        return self._center_memo[1]

    def label_pairs(self, photo=None):
        """[(標籤, 左, 右)]:選的那一級(預設第 1 級的 1-、1、1+)左右都有的那幾組,拿來定中心。"""
        photo = photo or self.photo
        split = self._rough_center(photo)
        if photo is None or split is None:
            return []
        return analysis.label_pairs([(p["x"], p["label"]) for p in photo.peaks], split,
                                    (int(self.center_order.value),))

    def center(self, photo=None):
        """(中心, [每組中點]):第 1 級 1-、1、1+ 左右各一的中點平均(可以排除某幾組);自己拖過中線時用拖的位置。"""
        photo = photo or self.photo
        if photo is None:
            return None, []
        pairs = [pair for pair in self.label_pairs(photo) if pair[0] not in photo.excluded]
        mids = [(a + b) / 2 for _, a, b in pairs]
        if photo.center_manual is not None:
            return photo.center_manual, mids
        if mids:
            return sum(mids) / len(mids), mids
        return self._rough_center(photo), []

    def analyze(self, photo=None):
        """這張照片的 Zeeman 分析(r²、Δ、D、δ),再換成 Δk、Δλ(有填標準具厚度時)。"""
        photo = photo or self.photo
        if photo is None:
            return None
        center, _ = self.center(photo)
        result = analysis.zeeman([(p["x"], p["label"]) for p in photo.peaks], center, self.px_per_mm)
        if result is None:
            return None
        thickness = _num(self.etalon_t.text)            # mm
        index = _num(self.etalon_n.text, 1.0) or 1.0
        wavelength = _num(self.wavelength.text, 643.847)  # nm
        result["dk"] = result["dl"] = None
        if result["ratio"] is not None and thickness:
            result["dk"] = result["ratio"] / (2 * index * thickness / 10)            # cm⁻¹(厚度換成 cm)
            result["dl"] = result["ratio"] * wavelength ** 2 / (2 * index * thickness * 1e6)    # nm
        current = _num(photo.current.replace("A", "").replace("a", ""))
        k = _num(self.field_k.text)
        intercept = (_num(self.field_b.text, 0.0) or 0.0) if self.use_b.value else 0.0
        result["I"] = current
        result["B"] = current * k + intercept if current is not None and k is not None else None
        return result

    def batch_fit(self):
        """全部照片的 Δk 對 B(沒有 B 時對電流)做直線擬合;有 Δk 和 B 時算 g 因子。"""
        points = []
        for photo in self.photos:
            result = self.analyze(photo)
            if result is None:
                continue
            x = result["B"] if result["B"] is not None else result["I"]
            y = result["dk"] if result["dk"] is not None else result["ratio"]
            if x is not None and y is not None:
                points.append((x, y))
        fit = analysis.linear_fit([x for x, _ in points], [y for _, y in points])
        return points, fit

    def relabel(self):
        """依中心往外標籤;第 1 級定出比較準的中心後再標一次。"""
        photo = self.photo
        if photo is None:
            return
        for step in range(2):
            # 第一次從左右對稱估計的中心開始(不受舊標籤影響),第二次用第 1 級定出來的中心
            center = self._rough_center() if step == 0 else self.center()[0]
            if center is None:
                return
            labels = analysis.label_peaks([p["x"] for p in photo.peaks], center, int(self.per_order.value),
                                          int(self.orders.value))
            for peak in photo.peaks:
                peak["label"] = labels.get(peak["x"], "")
        photo.labels_edited = False

    # ------------------------------------------------------------ 畫面座標

    def _fit(self):
        if self.surface is None or not self.canvas.width:
            return
        w, h = self.surface.get_size()
        self.zoom = min(self.canvas.width / w, self.canvas.height / h)
        self.offset = [(w - self.canvas.width / self.zoom) / 2, (h - self.canvas.height / self.zoom) / 2]
        self._view_cache = None

    def to_screen(self, x, y):
        return (self.canvas.x + (x - self.offset[0]) * self.zoom, self.canvas.y + (y - self.offset[1]) * self.zoom)

    def to_image(self, sx, sy):
        return ((sx - self.canvas.x) / self.zoom + self.offset[0], (sy - self.canvas.y) / self.zoom + self.offset[1])

    def _zoom_at(self, pos, factor):
        before = self.to_image(*pos)
        self.zoom = max(0.02, min(16.0, self.zoom * factor))
        self.offset = [before[0] - (pos[0] - self.canvas.x) / self.zoom, before[1] - (pos[1] - self.canvas.y) / self.zoom]
        self._view_cache = None

    def _plot_span(self, xs):
        lo, hi = self.plot_range or (xs[0], xs[-1])
        return lo, max(lo + 5, hi)

    def plot_area(self):
        return pygame.Rect(self.plot.x + 50, self.plot.y + 18, self.plot.width - 70, self.plot.height - 58)

    def plot_x(self, x, xs):
        area = self.plot_area()
        lo, hi = self._plot_span(xs)
        return area.x + (x - lo) / (hi - lo) * area.width

    def plot_to_x(self, sx, xs):
        area = self.plot_area()
        lo, hi = self._plot_span(xs)
        return lo + (sx - area.x) / max(1, area.width) * (hi - lo)

    # ------------------------------------------------------------ 事件

    def _say(self, text, color=theme.TEXT_DIM):
        self.message, self.message_color = text, color

    def handle_event(self, event, pos):
        if event.type == pygame.DROPFILE:
            self.add_files([event.file])
            return
        if event.type == pygame.KEYDOWN:
            typing = any(field.focused for field in self.inputs)
            ctrl = event.mod & pygame.KMOD_CTRL
            is_z = event.key == pygame.K_z or getattr(event, "scancode", 0) == 29     # 29:Z 鍵的位置
            is_y = event.key == pygame.K_y or getattr(event, "scancode", 0) == 28
            if not typing and ctrl and is_z:
                self.undo(redo=bool(event.mod & pygame.KMOD_SHIFT))
                return
            if not typing and ctrl and is_y:
                self.undo(redo=True)
                return
            if not typing and event.key in (pygame.K_LEFT, pygame.K_RIGHT) and self.selected is not None \
                    and self.photo is not None and self.selected < len(self.photo.peaks):
                self.remember()
                step = (10 if event.mod & pygame.KMOD_SHIFT else 1) * (1 if event.key == pygame.K_RIGHT else -1)
                peak = self.photo.peaks[self.selected]
                peak["x"] = round(peak["x"] + step * 0.5, 2)        # 一次 0.5 像素
                return
            if not typing and event.key in (pygame.K_DELETE, pygame.K_BACKSPACE) and self.selected is not None:
                self.delete_peak()
                return
        # 右邊設定區
        in_side = self.side.collidepoint(pos)
        if self.view.grabbing(pos) or (in_side and event.type == pygame.MOUSEWHEEL):
            if self.view.handle_event(event, pos):
                return
        for field in self.inputs:
            if event.type == pygame.MOUSEBUTTONDOWN and not in_side:
                field.blur()
                continue
            before = field.text
            if field.handle(event, pos) or (event.type == pygame.KEYDOWN and field.focused):
                self._field_changed(field, before)
                if event.type == pygame.KEYDOWN:
                    return
        for slider in self.sliders:
            if (in_side or slider.dragging) and slider.handle(event, pos):
                self._profile_cache = None
                return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.btn_plot_all.clicked(pos, True):
                self.plot_range = None
                return
            if self.btn_undo.clicked(pos, True):
                self.undo()
                return
            if self.btn_redo.clicked(pos, True):
                self.undo(redo=True)
                return
        if event.type == pygame.MOUSEBUTTONDOWN and not in_side:
            for field in self.inputs:
                field.blur()                    # 點圖或剖面時離開輸入框,Ctrl+Z 才會給圖用
        if event.type == pygame.MOUSEBUTTONDOWN and in_side and event.button == 1:
            self._side_click(pos)
            return
        # 強度圖
        if self.canvas.collidepoint(pos) or (self.drag and self.drag[0] in ("pan", "ruler", "line", "move")):
            if self._canvas_event(event, pos):
                return
        # 剖面圖
        if self.plot.collidepoint(pos) or (self.drag and self.drag[0] in ("peak", "center", "plotpan")):
            self._plot_event(event, pos)

    def _field_changed(self, field, before):
        if field.text == before:
            return
        if field is self.ruler_mm:
            self._ruler_changed()
        elif field is self.manual_scale:
            value = _num(field.text)
            if value and value > 0:
                self.px_per_mm = value
        elif field in self.line_fields:
            self._apply_line_inputs()
        elif field is self.in_band:
            self._profile_cache = None
        elif field is self.label_edit and self.selected is not None and self.photo is not None:
            self.photo.peaks[self.selected]["label"] = field.text.strip()
            self.photo.labels_edited = True

    def _side_click(self, pos):
        photo = self.photo
        if self.tool_pick.clicked(pos, True):
            self._say(TOOL_HINTS[self.tool_pick.value])
        elif self.btn_add.clicked(pos, True):
            path = winfile.ask_open("加入照片", IMAGE_FILTERS)
            if path:
                self.add_files([path])
        elif self.btn_remove.clicked(pos, True) and photo is not None:
            self.photos.pop(self.index)
            self.select(min(self.index, len(self.photos) - 1))
        elif self.btn_prev.clicked(pos, True) and self.index > 0:
            self.select(self.index - 1)
        elif self.btn_next.clicked(pos, True) and self.index < len(self.photos) - 1:
            self.select(self.index + 1)
        elif self.channel.clicked(pos, True) and self.rgb is not None:
            self._rebuild_intensity()
        elif self.stretch.clicked(pos, True):
            if self.rgb is not None:
                self._rebuild_intensity()
        elif self.smooth_on.clicked(pos, True) or self.despike_on.clicked(pos, True):
            self._profile_cache = None
        elif self.center_order.clicked(pos, True) and photo is not None:
            self.remember()
            photo.excluded = []
            self._say(f"改用第 {self.center_order.value} 級左右對稱的線定中心")
        elif self.per_order.clicked(pos, True) or self.orders.clicked(pos, True):
            self.remember()
            self.find_peaks()                   # 級數改了:重新找,只留到這一級
        elif self.btn_clear_peaks.clicked(pos, True) and photo is not None:
            self.remember()
            photo.peaks, photo.excluded, photo.center_manual = [], [], None
            self.selected = None
            self._say("已清除這張照片的全部峰（Ctrl+Z 可以復原）")
        elif self.btn_clear_photos.clicked(pos, True) and self.photos:
            if getattr(self, "_confirm_clear", False):
                self.photos, self.index = [], -1
                self.select(-1)
                self.undo_stack, self.redo_stack = [], []
                self._confirm_clear = False
                self._say("已清空照片清單")
            else:
                self._confirm_clear = True
                self._say("再按一次「清空清單」就會移除全部照片和它們的編輯", theme.WARN)
            return
        elif self.btn_line_all.clicked(pos, True) and photo is not None:
            for other in self.photos:
                if other is not photo:
                    other.line = photo.line
                    other.peaks = []            # 換了橫切線,峰要重找(切過去時自動找)
            self.last_line = photo.line
            self._say(f"這條橫切線已套用到全部 {len(self.photos)} 張照片", self.accent)
        elif self.btn_drop_unlabeled.clicked(pos, True) and photo is not None:
            self.remember()
            before = len(photo.peaks)
            photo.peaks = [peak for peak in photo.peaks if peak["label"]]
            self.selected = None
            self._say(f"刪掉 {before - len(photo.peaks)} 個沒標籤的峰")
        elif self.btn_find.clicked(pos, True):
            self.remember()
            self.find_peaks()
        elif self.btn_relabel.clicked(pos, True):
            self.remember()
            self.relabel()
        elif self.btn_auto_center.clicked(pos, True) and photo is not None:
            self.remember()
            photo.center_manual = None
            if not photo.labels_edited:
                self.relabel()
            center, _ = self.center()
            self._say("已依左右對稱的峰自動定位中線" if center is not None else "找不到左右對稱的峰，沒辦法自動定位",
                      self.accent if center is not None else theme.WARN)
        elif self.btn_level.clicked(pos, True) and photo is not None:
            self.remember()
            (x0, y0), (x1, y1) = photo.line
            photo.line = ((x0, y0), (x1, y0))
            self._profile_cache, self.plot_range = None, None
            self._sync_line_inputs()
        elif self.btn_delete_peak.clicked(pos, True):
            self.delete_peak()
        if not self.btn_clear_photos.clicked(pos, False):
            self._confirm_clear = False
        if self.btn_csv.clicked(pos, True):
            self.export_csv()
        elif self.use_b.clicked(pos, True):
            self._say("磁場改成 B = k × I + b" if self.use_b.value else "磁場改成 B = k × I（不加截距）")
        elif self.btn_open_output.clicked(pos, True):
            output_dir().mkdir(parents=True, exist_ok=True)
            os.startfile(output_dir())
        elif self.btn_png.clicked(pos, True):
            self.export_png()
        elif self.btn_save_cfg.clicked(pos, True):
            self.save_settings()
        elif self.btn_load_cfg.clicked(pos, True):
            self.load_settings()
        elif self._save_scale_rect.collidepoint(pos):
            self.save_calibration()
        else:
            for rect, name in self.pair_rects:
                if rect.collidepoint(pos) and photo is not None:
                    self.remember()
                    if name in photo.excluded:
                        photo.excluded.remove(name)
                    else:
                        photo.excluded.append(name)
                    return
            for rect, index in self.peak_rects:
                if rect.collidepoint(pos):
                    self._select_peak(index)
                    return

    def _select_peak(self, index):
        self.selected = index
        if index is not None and self.photo is not None:
            self.label_edit.set_text(self.photo.peaks[index]["label"])

    def delete_peak(self):
        photo = self.photo
        if photo is not None and self.selected is not None and self.selected < len(photo.peaks):
            self.remember()
            removed = photo.peaks.pop(self.selected)
            self.selected = None
            self._say(f"已刪除 {_fmt(removed['x'], 1)} px 的峰")

    def _canvas_event(self, event, pos):
        if self.surface is None:
            return False
        tool = self.tool_pick.value
        photo = self.photo
        if event.type == pygame.MOUSEWHEEL:
            self._zoom_at(pygame.mouse.get_pos(), 1.2 if event.y > 0 else 1 / 1.2)
            return True
        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button in (2, 3) or (event.button == 1 and tool == "view"):
                self.drag = ("pan", pos, list(self.offset))
                return True
            if event.button != 1:
                return False
            ix, iy = self.to_image(*pos)
            if tool == "ruler" and self.ruler:
                ends = [self.to_screen(*p) for p in self.ruler]
                for k, (sx, sy) in enumerate(ends):
                    if math.hypot(sx - pos[0], sy - pos[1]) <= HANDLE + 3:
                        self.drag = ("ruler", k)
                        return True
                if self._near_segment(pos, *ends):
                    self.drag = ("ruler", "move", (ix, iy), list(self.ruler))
                    return True
                return True
            if tool == "line" and photo is not None:
                self.remember()
                ends = [self.to_screen(*p) for p in photo.line]
                for k, (sx, sy) in enumerate(ends):
                    if math.hypot(sx - pos[0], sy - pos[1]) <= HANDLE + 3:
                        self.drag = ("line", k)
                        return True
                if self._near_segment(pos, *ends):
                    self.drag = ("line", "move", (ix, iy), photo.line)
                else:
                    self.drag = ("line", "new", (ix, iy))
                    photo.line = ((ix, iy), (ix + 1, iy))
                return True
        if event.type == pygame.MOUSEMOTION and self.drag:
            kind = self.drag[0]
            ix, iy = self.to_image(*pos)
            h, w = self.intensity.shape
            ix, iy = min(max(0, ix), w - 1), min(max(0, iy), h - 1)
            if kind == "pan":
                start, offset = self.drag[1], self.drag[2]
                self.offset = [offset[0] - (pos[0] - start[0]) / self.zoom, offset[1] - (pos[1] - start[1]) / self.zoom]
                self._view_cache = None
            elif kind == "ruler":
                if self.drag[1] == "move":
                    (sx, sy), old = self.drag[2], self.drag[3]
                    self.ruler = [(x + ix - sx, y + iy - sy) for x, y in old]
                else:
                    self.ruler[self.drag[1]] = (ix, iy)
                self._ruler_changed()
            elif kind == "line":
                part = self.drag[1]
                if part == "new":
                    photo.line = (self.drag[2], (ix, iy))
                elif part == "move":
                    (sx, sy), old = self.drag[2], self.drag[3]
                    photo.line = tuple((min(max(0, x + ix - sx), w - 1), min(max(0, y + iy - sy), h - 1))
                                       for x, y in old)
                else:
                    ends = list(photo.line)
                    ends[part] = (ix, iy)
                    photo.line = tuple(ends)
                self._profile_cache, self.plot_range = None, None
                self._sync_line_inputs()
            return True
        if event.type == pygame.MOUSEBUTTONUP and self.drag:
            kind = self.drag[0]
            self.drag = None
            if kind == "line" and photo is not None:
                self.last_line = photo.line
                (x0, y0), (x1, y1) = photo.line
                if math.hypot(x1 - x0, y1 - y0) < 10:
                    photo.line = ((max(0, x0 - 200), y0), (min(self.intensity.shape[1] - 1, x0 + 200), y0))
                self._sync_line_inputs()
                self.find_peaks()
            return True
        return False

    @staticmethod
    def _near_segment(pos, a, b):
        ax, ay = a
        bx, by = b
        length = math.hypot(bx - ax, by - ay) or 1
        t = max(0, min(1, ((pos[0] - ax) * (bx - ax) + (pos[1] - ay) * (by - ay)) / length ** 2))
        return math.hypot(pos[0] - (ax + t * (bx - ax)), pos[1] - (ay + t * (by - ay))) <= HANDLE

    def _plot_event(self, event, pos):
        """滾輪縮放、拖空白處平移;雙擊加峰、拖峰移動、右鍵刪峰;拖綠色中線自己定中心。"""
        prof = self.profile()
        photo = self.photo
        if prof is None:
            return
        xs, _, done = prof
        area = self.plot_area()
        if event.type == pygame.MOUSEWHEEL:
            lo, hi = self._plot_span(xs)
            anchor = self.plot_to_x(pygame.mouse.get_pos()[0], xs)
            factor = 1 / 1.25 if event.y > 0 else 1.25
            lo, hi = anchor - (anchor - lo) * factor, anchor + (hi - anchor) * factor
            if hi - lo >= xs[-1] - xs[0]:
                self.plot_range = None
            else:
                shift = max(0, xs[0] - lo) - max(0, hi - xs[-1])
                self.plot_range = (lo + shift, hi + shift)
            return
        center, _ = self.center()

        def near_peak():
            return next((k for k, peak in enumerate(photo.peaks)
                         if abs(self.plot_x(peak["x"], xs) - pos[0]) <= HANDLE), None)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            self.drag = None
            near = near_peak()
            if near is not None:
                self.selected = near
                self.delete_peak()
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and area.collidepoint(pos):
            now = pygame.time.get_ticks()
            double = now - self._last_click[0] < DOUBLE_CLICK_MS and self._last_click[1] is not None \
                and abs(self._last_click[1][0] - pos[0]) < 6
            self._last_click = (now, pos)
            if double:
                self.drag = None
                self.remember()
                i = analysis.snap(done, int(round(self.plot_to_x(pos[0], xs) - xs[0])))
                added = {"x": round(float(xs[0]) + analysis.refine(done, i), 2), "label": ""}
                photo.peaks.append(added)
                photo.peaks.sort(key=lambda p: p["x"])
                if not photo.labels_edited:
                    self.relabel()
                self._select_peak(photo.peaks.index(added))
                self._say(f"加了一個峰：{_fmt(added['x'], 2)} px")
                return
            near = near_peak()
            center_gap = abs(self.plot_x(center, xs) - pos[0]) if center is not None else 1e9
            if near is not None and abs(self.plot_x(photo.peaks[near]["x"], xs) - pos[0]) < center_gap:
                self.remember()
                self._select_peak(near)
                self.drag = ("peak", photo.peaks[near])
            elif center_gap <= HANDLE:
                self.remember()
                self.drag = ("center",)
            else:
                self.drag = ("plotpan", pos[0], self._plot_span(xs))
            return
        if event.type == pygame.MOUSEMOTION and self.drag:
            x = min(max(xs[0], self.plot_to_x(pos[0], xs)), xs[-1])
            if self.drag[0] == "peak":
                self.drag[1]["x"] = float(round(x, 1))
            elif self.drag[0] == "center":
                photo.center_manual = float(round(x, 1))
            elif self.drag[0] == "plotpan":
                lo, hi = self.drag[2]
                shift = (self.drag[1] - pos[0]) / max(1, area.width) * (hi - lo)
                shift = max(xs[0] - lo, min(xs[-1] - hi, shift))
                if self.plot_range is not None or self.drag[2] != (xs[0], xs[-1]):
                    self.plot_range = (lo + shift, hi + shift)
            return
        if event.type == pygame.MOUSEBUTTONUP and self.drag:
            kind = self.drag[0]
            if kind == "peak":
                moved = self.drag[1]
                photo.peaks.sort(key=lambda p: p["x"])
                if moved in photo.peaks:
                    self._select_peak(photo.peaks.index(moved))
            self.drag = None
            if kind in ("peak", "center") and not photo.labels_edited:
                self.relabel()
            if kind == "center":
                self._say(f"中線定在 {photo.center_manual:.1f} px；按「自動定位中線」可以回到自動", self.accent)

    # ------------------------------------------------------------ 輸出

    def _rows_for(self, photo):
        """這張照片要寫進 CSV 的列(需要先載入它的強度圖)。"""
        center, _ = self.center()
        peaks = [(p["x"], p["label"], self._value_at(p["x"])) for p in photo.peaks]
        return analysis.csv_rows(photo.path.name, photo.current, self.px_per_mm or 0, photo.line, self._band(), center,
                                 peaks)

    def export_csv(self):
        """輸出 Excel(.xlsx),直接存到 output\\zeeman(不跳存檔視窗),檔名有日期時間,不會蓋掉舊的。
        四頁:Zeeman 總表(用公式算,改上面的常數會自動重算)、r 與 r²(公式)、每條線、設定。"""
        if not self.photos:
            self._say("還沒有照片", theme.WARN)
            return
        self._sync_current()
        keep = self.index
        lines, results = [], []
        for k, photo in enumerate(self.photos):
            if k != self.index:             # 還沒看過的照片:用預設的橫切線和找峰參數
                self.select(k)
            result = self.analyze(photo)
            results.append((photo, result))
            r = result["r"] if result else {}
            r2 = result["r2"] if result else {}
            for row in self._rows_for(photo):
                label = row[9]
                row = [_cell(v) for v in row]
                row[9] = label                                  # 標籤保持文字(「1」「2」不要變成數字)
                row += [r.get(label), r2.get(label)]
                lines.append(row)
        if keep != self.index:
            self.select(keep)
        unit = "mm" if self.px_per_mm else "px"
        header = analysis.CSV_HEADER + [f"r({unit})", f"r²({unit}²)"]
        sheets = [("Zeeman 總表", self._summary_sheet(results, unit)),
                  ("r 與 r²", self._radius_sheet(results, unit)),
                  ("每條線", [header] + lines),
                  ("設定", [["項目", "值"]] + [[key, json.dumps(value, ensure_ascii=False)
                                               if isinstance(value, (dict, list, tuple)) else value]
                                              for key, value in self._settings().items()])]
        output_dir().mkdir(parents=True, exist_ok=True)
        path = output_dir() / f"干涉環分析 {time.strftime('%Y-%m-%d %H%M%S')}.xlsx"
        try:
            xlsx.write(path, sheets)
        except OSError as exc:
            self._say(f"存不了 Excel：{exc}"[:70], theme.WARN)
            return
        self.last_export = path
        self._say(f"已輸出 {path.name}（{len(lines)} 條線、{len(self.photos)} 張照片）；按「開啟輸出資料夾」", self.accent)

    def _radius_sheet(self, results, unit):
        """每張照片每個標籤:左側、右側的位置(mm,中心為 0),r 和 r² 用公式算(看得到怎麼算的)。"""
        rows = [["照片", "電流", "標籤", f"左側位置({unit})", f"右側位置({unit})", f"r({unit})", f"r²({unit}²)", "r 的算法"]]
        scale = self.px_per_mm or 1.0
        for photo, result in results:
            if result is None:
                continue
            center, _ = self.center(photo)
            for label in sorted(result["r2"], key=analysis._label_key):
                xs = [p["x"] for p in photo.peaks if p["label"] == label]
                left = [(x - center) / scale for x in xs if x < center]
                right = [(x - center) / scale for x in xs if x > center]
                n = len(rows) + 1
                if len(left) == 1 and len(right) == 1:
                    r = xlsx.Formula(f"(E{n}-D{n})/2", result["r"][label])
                    how = "左右都有：(右 − 左) / 2"
                else:
                    r = xlsx.Formula(f"ABS(SUM(D{n}:E{n}))", result["r"][label])
                    how = "只有一邊：到中心的距離"
                rows.append([photo.path.name, photo.current, label, left[0] if len(left) == 1 else None,
                             right[0] if len(right) == 1 else None, r, xlsx.Formula(f"F{n}^2", result["r2"][label]), how])
        return rows

    def _summary_sheet(self, results, unit):
        """Zeeman 總表:上面是實驗常數,下面每張照片一列;B、Δ 平均、δ、Δk、Δλ、斜率、g 都是 Excel 公式。"""
        k = _num(self.field_k.text)
        thickness = _num(self.etalon_t.text)
        index = _num(self.etalon_n.text, 1.0) or 1.0
        wavelength = _num(self.wavelength.text, 643.847)
        intercept = _num(self.field_b.text, 0.0) or 0.0
        rows = [[xlsx.Bold("實驗常數（改這裡，下面會自動重算）")],
                ["磁場係數 k (T/A)", k], ["截距 b (T)", intercept],
                ["用截距（1 = 用、0 = 不用）", 1 if self.use_b.value else 0],
                ["標準具厚度 t (mm)", thickness], ["折射率 n", index], ["波長 λ (nm)", wavelength],
                [xlsx.Bold("磁場：B = k × I + b（不用截距時 B = k × I）")],
                [xlsx.Bold("公式：Δ = (r²(p+) − r²(p−))/2；D = π 線 r² 對級數的斜率；δ = Δ/D；Δk = δ/(2nt)；Δλ = λ²δ/(2nt)")],
                []]
        orders = int(self.orders.value)
        head = ["照片", "電流 I (A)", "磁場 B (T)", f"D ({unit}²)"] + [f"Δ 第{p}級 ({unit}²)" for p in range(1, orders + 1)] \
            + ["Δ 平均", "δ = Δ/D", "Δk (1/cm)", "Δλ (pm)"]
        rows.append([xlsx.Bold(h) for h in head])
        first = len(rows) + 1
        d_first, d_last = xlsx.column(4), xlsx.column(3 + orders)
        avg, ratio, dk, dl = (xlsx.column(4 + orders + i) for i in range(4))
        for photo, result in results:
            if result is None:
                continue
            n = len(rows) + 1
            current = result["I"]
            row = [photo.path.name, current,
                   xlsx.Formula(f"IF(B{n}=\"\",\"\",B{n}*$B$2+$B$3*$B$4)", result["B"]) if current is not None else None,
                   result["D"]]
            row += [result["delta"].get(p) for p in range(1, orders + 1)]
            row += [xlsx.Formula(f"IF(COUNT({d_first}{n}:{d_last}{n})=0,\"\",AVERAGE({d_first}{n}:{d_last}{n}))"),
                    xlsx.Formula(f"IF(OR({avg}{n}=\"\",D{n}=\"\"),\"\",{avg}{n}/D{n})", result["ratio"]),
                    xlsx.Formula(f"IF(OR({ratio}{n}=\"\",$B$5=\"\"),\"\",{ratio}{n}/(2*$B$6*$B$5/10))", result["dk"]),
                    xlsx.Formula(f"IF(OR({ratio}{n}=\"\",$B$5=\"\"),\"\",{ratio}{n}*$B$7^2/(2*$B$6*$B$5*1000000)*1000)",
                                 result["dl"] * 1000 if result["dl"] is not None else None)]
            rows.append(row)
        last = len(rows)
        rows += [[],
                 [xlsx.Bold("全部照片：Δk 對 B 的直線（要有電流、k、t）")],
                 ["斜率 (1/cm/T)", xlsx.Formula(f"IFERROR(SLOPE({dk}{first}:{dk}{last},C{first}:C{last}),\"\")")],
                 ["截距", xlsx.Formula(f"IFERROR(INTERCEPT({dk}{first}:{dk}{last},C{first}:C{last}),\"\")")],
                 ["g 因子 = 斜率 × hc/μB", xlsx.Formula(f"IF(B{last + 3}=\"\",\"\",B{last + 3}*{analysis.HC_OVER_MUB})")],
                 ["（hc/μB = 2.14195 T·cm）"]]
        return rows

    def export_png(self):
        photo = self.photo
        prof = self.profile()
        if photo is None or prof is None:
            self._say("還沒有照片", theme.WARN)
            return
        output_dir().mkdir(parents=True, exist_ok=True)
        stem = photo.path.stem
        image_path = output_dir() / f"{stem}_強度圖.png"
        plot_path = output_dir() / f"{stem}_亮度剖面.png"
        from PIL import Image, ImageDraw

        base = Image.fromarray(self.gray).convert("RGB")
        pen = ImageDraw.Draw(base)
        band = self._band()
        width = max(2, base.width // 800)
        pen.polygon(self._band_corners(), outline=LINE_COLOR, width=width)
        center, _ = self.center()
        for peak in photo.peaks:
            pen.line(self._tick(peak["x"], band * 2), fill=PEAK_COLOR, width=width)
        if center is not None:
            pen.line(self._tick(center, band * 3), fill=CENTER_COLOR, width=width)
        base.save(image_path)
        size = (int(min(6000, max(400, _num(self.out_w.text, 1600) or 1600))),
                int(min(4000, max(250, _num(self.out_h.text, 700) or 700))))
        surface = pygame.Surface(size)
        self._draw_plot(surface, surface.get_rect(), prof, report=True)
        pygame.image.save(surface, str(plot_path))
        self._say(f"已輸出 {image_path.name}、{plot_path.name}（output\\zeeman）", self.accent)

    def _normal(self):
        (x0, y0), (x1, y1) = self.photo.line
        length = math.hypot(x1 - x0, y1 - y0) or 1.0
        return -(y1 - y0) / length, (x1 - x0) / length

    def _band_corners(self):
        """帶寬範圍的四個角(原圖座標)。"""
        (x0, y0), (x1, y1) = self.photo.line
        nx, ny = self._normal()
        half = self._band() / 2
        return [(x0 + nx * half, y0 + ny * half), (x1 + nx * half, y1 + ny * half),
                (x1 - nx * half, y1 - ny * half), (x0 - nx * half, y0 - ny * half)]

    def _tick(self, t, half):
        """沿線距離 t 那一點、垂直於橫切線的短線(原圖座標)。"""
        x, y = analysis.point_on(self.photo.line, t)
        nx, ny = self._normal()
        return [(x + nx * half, y + ny * half), (x - nx * half, y - ny * half)]

    def _settings(self):
        return {"說明": "干涉環分析的設定檔（比例尺、通道、平滑、去刻度線、找峰參數）",
                "px_per_mm": self.px_per_mm, "ruler_mm": _num(self.ruler_mm.text, 20), "channel": self.channel.value,
                "stretch": self.stretch.value, "band": self._band(), "smooth": self.smooth_on.value,
                "sigma": self.sigma.value, "despike": self.despike_on.value, "dip_width": self.dip_width.value,
                "prominence": self.prominence.value, "distance": self.distance.value,
                "per_order": self.per_order.value, "orders": self.orders.value,
                "center_order": self.center_order.value,
                "constants": {"field_k": self.field_k.text, "field_b": self.field_b.text, "use_b": self.use_b.value,
                              "etalon_t": self.etalon_t.text,
                              "etalon_n": self.etalon_n.text, "wavelength": self.wavelength.text},
                "line": (self.photo.line if self.photo is not None else self.last_line),
                "output": {"width": self.out_w.text, "height": self.out_h.text, "y_min": self.y_min.text,
                           "y_max": self.y_max.text, "title": self.title_in.text}}

    def _apply_settings(self, data):
        self.px_per_mm = data.get("px_per_mm") or self.px_per_mm
        if data.get("ruler_mm"):
            self.ruler_mm.set_text(_fmt(data["ruler_mm"]))
        keys = [k for k, _ in analysis.CHANNELS]
        if data.get("channel") in keys:
            self.channel.index = keys.index(data["channel"])
        self.stretch.value = bool(data.get("stretch", self.stretch.value))
        if data.get("band"):
            self.in_band.set_text(str(int(data["band"])))
        self.smooth_on.value = bool(data.get("smooth", self.smooth_on.value))
        self.despike_on.value = bool(data.get("despike", self.despike_on.value))
        for slider, key in ((self.sigma, "sigma"), (self.dip_width, "dip_width"), (self.prominence, "prominence"),
                            (self.distance, "distance")):
            if key in data:
                slider.value = data[key]
        if "per_order" in data:
            self.per_order.index = 0 if str(data["per_order"]) == "3" else 1
        if str(data.get("orders", "")) in dict(ORDERS):
            self.orders.index = [k for k, _ in ORDERS].index(str(data["orders"]))
        constants = data.get("constants") or {}
        if "use_b" in constants:
            self.use_b.value = bool(constants["use_b"])
        for field, key in ((self.field_k, "field_k"), (self.field_b, "field_b"), (self.etalon_t, "etalon_t"),
                           (self.etalon_n, "etalon_n"),
                           (self.wavelength, "wavelength")):
            if key in constants:
                field.set_text(str(constants[key]))
        if str(data.get("center_order", "")) in dict(ORDERS):
            self.center_order.index = [k for k, _ in ORDERS].index(str(data["center_order"]))
        if data.get("line"):
            self.last_line = tuple(tuple(point) for point in data["line"])
        output = data.get("output") or {}
        for field, key in ((self.out_w, "width"), (self.out_h, "height"), (self.y_min, "y_min"),
                           (self.y_max, "y_max"), (self.title_in, "title")):
            if key in output:
                field.set_text(str(output[key]))

    def save_settings(self):
        """直接存到 output\\zeeman(不跳存檔視窗),檔名有日期時間。"""
        output_dir().mkdir(parents=True, exist_ok=True)
        path = output_dir() / f"干涉環分析設定 {time.strftime('%Y-%m-%d %H%M%S')}.json"
        analysis.write_json(path, self._settings())
        self._say(f"已存設定檔：{path.name}（output\\zeeman）；讀設定檔時選它", self.accent)

    def load_settings(self):
        path = winfile.ask_open("讀設定檔", [("設定檔 (*.json)", "*.json")], str(output_dir()))
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self._say(f"讀不了設定檔：{exc}"[:60], theme.WARN)
            return
        self._apply_settings(data)
        if self.last_line is not None:
            for photo in self.photos:
                photo.line, photo.peaks = self.last_line, []        # 設定檔的橫切線套用到全部照片,峰重找
            if self.photo is not None:
                self._profile_cache = None
                self._sync_line_inputs()
                self.find_peaks()
        if self.rgb is not None:
            self._rebuild_intensity()
        self._say(f"已讀設定檔：{Path(path).name}；按「自動找峰」用新的參數重找", self.accent)

    # ------------------------------------------------------------ 繪製

    def draw(self, rect, mouse_pos):
        screen = self.screen
        margin = 16
        side = pygame.Rect(rect.right - SIDE_W - margin, rect.y + margin, SIDE_W, rect.height - margin * 2)
        left = pygame.Rect(rect.x + margin, rect.y + margin, side.x - rect.x - margin * 2, rect.height - margin * 2)
        plot_h = int(left.height * PLOT_RATIO)
        canvas = pygame.Rect(left.x, left.y, left.width, left.height - plot_h - 10)
        if canvas.size != self.canvas.size:
            self.canvas = canvas
            self._fit()
        self.canvas = canvas
        self.plot = pygame.Rect(left.x, canvas.bottom + 10, left.width, plot_h)
        self._draw_canvas(screen, mouse_pos)
        rounded_panel(screen, self.plot, theme.PANEL, radius=10, alpha=235, border=theme.PANEL_EDGE)
        prof = self.profile()
        if prof is not None:
            self._draw_plot(screen, self.plot, prof)
        else:
            draw_text(screen, "拉好橫切線後，這裡會顯示亮度剖面", self.plot.center, 13, theme.TEXT_FAINT, center=True)
        self._draw_side(screen, side, mouse_pos)

    def _draw_canvas(self, screen, mouse_pos):
        canvas = self.canvas
        rounded_panel(screen, canvas, theme.BG_DEEP, radius=10, alpha=235, border=theme.PANEL_EDGE)
        if self.surface is None:
            draw_text(screen, self.message if not self.photos else "讀取中…", canvas.center, 14, theme.TEXT_DIM,
                      center=True)
            return
        key = (round(self.zoom, 6), round(self.offset[0], 2), round(self.offset[1], 2), canvas.size)
        if self._view_cache is None or self._view_cache[0] != key:
            self._view_cache = (key, self._render_view())
        screen.set_clip(canvas)
        image, at = self._view_cache[1]
        if image is not None:
            screen.blit(image, at)
        photo = self.photo
        # 橫切線與帶寬
        if photo is not None and photo.line is not None:
            corners = [self.to_screen(*p) for p in self._band_corners()]
            box = pygame.Rect(self.canvas)
            veil = pygame.Surface(box.size, pygame.SRCALPHA)
            pygame.draw.polygon(veil, LINE_COLOR + (50,), [(x - box.x, y - box.y) for x, y in corners])
            screen.blit(veil, box.topleft)
            a, b = [self.to_screen(*p) for p in photo.line]
            pygame.draw.line(screen, LINE_COLOR, a, b, 2)
            for p in (a, b):
                pygame.draw.circle(screen, LINE_COLOR, (int(p[0]), int(p[1])), 5)
            draw_text(screen, "起", (a[0] - 14, a[1] - 22), 11, LINE_COLOR)
            center, _ = self.center()
            for k, peak in enumerate(photo.peaks):
                color = (255, 255, 255) if k == self.selected else PEAK_COLOR
                tick = [self.to_screen(*q) for q in self._tick(peak["x"], 18 / self.zoom)]
                pygame.draw.line(screen, color, *tick, 2)
                if peak["label"]:
                    label_at = self.to_screen(*self._tick(peak["x"], 30 / self.zoom)[0])
                    draw_text(screen, peak["label"], label_at, 12, color, center=True)
            if center is not None:
                tick = [self.to_screen(*q) for q in self._tick(center, 30 / self.zoom)]
                pygame.draw.line(screen, CENTER_COLOR, *tick, 2)
        # 比例尺
        if self.ruler and (self.tool_pick.value == "ruler" or self.drag and self.drag[0] == "ruler"):
            a, b = [self.to_screen(*p) for p in self.ruler]
            pygame.draw.line(screen, RULER_COLOR, a, b, 3)
            for p in (a, b):
                pygame.draw.circle(screen, RULER_COLOR, (int(p[0]), int(p[1])), HANDLE, 2)
                pygame.draw.line(screen, RULER_COLOR, (p[0], p[1] - 14), (p[0], p[1] + 14), 1)
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 - 18)
            text = f"{self.ruler_mm.text or '?'} mm" + (f" ＝ {self.px_per_mm:.2f} px/mm" if self.px_per_mm else "")
            draw_text(screen, text, mid, 13, RULER_COLOR, center=True)
        screen.set_clip(None)
        hint = TOOL_HINTS[self.tool_pick.value]
        draw_text(screen, widgets.clip_text(hint, 12, canvas.width - 24), (canvas.x + 12, canvas.bottom - 22), 12,
                  theme.TEXT_DIM)
        if canvas.collidepoint(mouse_pos):
            ix, iy = self.to_image(*mouse_pos)
            draw_text(screen, f"x {ix:.0f}  y {iy:.0f}", (canvas.right - 12, canvas.bottom - 22), 12, theme.TEXT_DIM,
                      right=True)

    def _render_view(self):
        """畫面看得到的那一塊縮放成畫布大小(只縮放看得到的部分,大圖也不會卡)。"""
        w, h = self.surface.get_size()
        left, top = self.to_image(self.canvas.x, self.canvas.y)
        right, bottom = self.to_image(self.canvas.right, self.canvas.bottom)
        crop = pygame.Rect(int(max(0, left)), int(max(0, top)), 0, 0)
        crop.width = int(min(w, math.ceil(right))) - crop.x
        crop.height = int(min(h, math.ceil(bottom))) - crop.y
        if crop.width <= 0 or crop.height <= 0:
            return None, (0, 0)
        part = self.surface.subsurface(crop)
        size = (max(1, round(crop.width * self.zoom)), max(1, round(crop.height * self.zoom)))
        scaled = pygame.transform.smoothscale(part, size) if self.zoom < 1 else pygame.transform.scale(part, size)
        return scaled, self.to_screen(crop.x, crop.y)

    def _draw_plot(self, surface, rect, prof, report=False):
        """亮度剖面:灰色是原始、亮色是處理後;峰的位置標線和標籤;有比例尺和中心時橫軸用 mm。
        report:輸出成圖片用的版本(白底、黑線、使用者自己的標題)。"""
        xs, raw, done = prof
        photo = self.photo
        background = (255, 255, 255) if report else None
        ink = (20, 20, 20) if report else theme.TEXT
        faint = (170, 170, 170) if report else theme.TEXT_FAINT
        main = (0, 0, 0) if report else (240, 240, 240)
        if report:
            surface.fill(background)
        title = self.title_in.text.strip() if report else ""
        top_pad = 50 if title else 18
        area = pygame.Rect(rect.x + 50 + (30 if report else 0), rect.y + top_pad, rect.width - 70 - (50 if report else 0),
                           rect.height - top_pad - (60 if report else 40))
        lo_x, hi_x = (xs[0], xs[-1]) if report else self._plot_span(xs)
        if title:
            draw_text(surface, title, (rect.centerx, rect.y + 24), 24 if report else 14, ink, center=True)
        low, high = float(min(raw.min(), done.min())), float(max(raw.max(), done.max()))
        span = max(1e-6, high - low)
        low, high = low - span * 0.05, high + span * 0.08
        if report:
            low = _num(self.y_min.text, low)                    # 自己指定的強度範圍(留空是自動)
            high = _num(self.y_max.text, high)
            if high <= low:
                high = low + 1

        def px(x):
            return area.x + (x - lo_x) / max(1.0, hi_x - lo_x) * area.width

        def py(v):
            return area.bottom - (v - low) / (high - low) * area.height

        pygame.draw.rect(surface, faint, area, 1)
        center, _ = self.center()
        use_mm = bool(self.px_per_mm and center is not None)
        # 橫軸刻度
        if use_mm:
            lo_mm, hi_mm = (lo_x - center) / self.px_per_mm, (hi_x - center) / self.px_per_mm
            step = _nice_step(hi_mm - lo_mm)
            if report:
                minor = step / 5                    # 細刻度:每格再分 5 小格
                tick = math.ceil(lo_mm / minor) * minor
                while tick <= hi_mm:
                    x = px(center + tick * self.px_per_mm)
                    pygame.draw.line(surface, (225, 225, 225), (x, area.y), (x, area.bottom))
                    pygame.draw.line(surface, faint, (x, area.bottom), (x, area.bottom + 3))
                    tick += minor
            tick = math.ceil(lo_mm / step) * step
            while tick <= hi_mm:
                x = center + tick * self.px_per_mm
                pygame.draw.line(surface, faint, (px(x), area.bottom), (px(x), area.bottom + 5))
                draw_text(surface, _fmt(tick, 2), (px(x), area.bottom + 14), 14 if report else 11, ink, center=True)
                tick += step
            axis = "位置 (mm)，中心為 0"
        else:
            step = _nice_step(hi_x - lo_x)
            tick = math.ceil(lo_x / step) * step
            while tick <= hi_x:
                pygame.draw.line(surface, faint, (px(tick), area.bottom), (px(tick), area.bottom + 5))
                draw_text(surface, str(int(tick)), (px(tick), area.bottom + 14), 14 if report else 11, ink, center=True)
                tick += step
            axis = "沿線位置 (px)"
        draw_text(surface, axis, (area.centerx, area.bottom + (40 if report else 28)), 16 if report else 11, ink,
                  center=True)
        step = _nice_step(high - low)
        tick = math.ceil(low / step) * step
        while tick <= high:
            pygame.draw.line(surface, faint, (area.x - 5, py(tick)), (area.x, py(tick)))
            draw_text(surface, str(int(tick)), (area.x - 8, py(tick)), 14 if report else 11, ink,
                      right=True)
            tick += step
        if report:
            label = theme.font(16).render("強度", True, ink)
            surface.blit(pygame.transform.rotate(label, 90), (rect.x + 12, area.centery - label.get_width() // 2))
        clip = surface.get_clip()
        surface.set_clip(area)
        first = max(0, int(lo_x - xs[0]) - 1)
        last = min(len(xs), int(hi_x - xs[0]) + 2)
        step_px = max(1, (last - first) // max(1, area.width))
        points = [(px(xs[i]), py(raw[i])) for i in range(first, last, step_px)]
        if len(points) > 1 and (self.despike_on.value or self.smooth_on.value):
            pygame.draw.lines(surface, faint, False, points, 1)
        points = [(px(xs[i]), py(done[i])) for i in range(first, last, step_px)]
        if len(points) > 1:
            pygame.draw.lines(surface, main, False, points, 2)
        if center is not None:
            pygame.draw.line(surface, CENTER_COLOR if not report else (40, 160, 60), (px(center), area.y),
                             (px(center), area.bottom), 1 if report else 3)
        for k, peak in enumerate(photo.peaks if photo else []):
            x = px(peak["x"])
            color = (255, 255, 255) if (k == self.selected and not report) else (PEAK_COLOR if not report else (210, 40, 80))
            pygame.draw.line(surface, color, (x, area.y), (x, area.bottom), 1)
            if peak["label"]:
                draw_text(surface, peak["label"], (x, area.y + (14 if report else 10)), 14 if report else 11, color,
                          center=True)
            if report:
                # 每條線標出位置(mm,沒有比例尺時 px),直的寫在線旁邊
                value = (peak["x"] - center) / self.px_per_mm if use_mm else peak["x"]
                text = f"{value:+.3f}" if use_mm else f"{value:.1f}"
                label = pygame.transform.rotate(theme.font(13).render(text, True, color), 90)
                surface.blit(label, (x + 2, area.y + 30))
        surface.set_clip(clip)
        if not report:
            self.btn_undo.enabled, self.btn_redo.enabled = bool(self.undo_stack), bool(self.redo_stack)
            self.btn_undo.draw(surface, pygame.Rect(rect.right - 86 - 120, rect.y + 6, 56, 24), pygame.mouse.get_pos())
            self.btn_redo.draw(surface, pygame.Rect(rect.right - 86 - 60, rect.y + 6, 56, 24), pygame.mouse.get_pos())
            if self.plot_range is not None:
                self.btn_plot_all.draw(surface, pygame.Rect(rect.right - 86, rect.y + 6, 76, 24),
                                       pygame.mouse.get_pos())
            else:
                self.btn_plot_all.rect = pygame.Rect(0, 0, 0, 0)
            draw_text(surface, widgets.clip_text(PLOT_HINT, 11, rect.width - 120), (rect.x + 10, rect.y + 3), 11,
                      theme.TEXT_FAINT)

    def _draw_side(self, screen, side, mouse_pos):
        rounded_panel(screen, side, theme.PANEL, radius=12, alpha=235, border=theme.PANEL_EDGE)
        self.side = side
        area = side.inflate(-28, -24)
        self.view.update(mouse_pos)
        screen.set_clip(area)
        x, inner = area.x, area.width - 12
        y = area.y - self.view.scroll
        photo = self.photo
        self.pair_rects, self.peak_rects = [], []

        def heading(text, y):
            text_at(screen, text, (x, y), 15, theme.TEXT, bold=True)
            return y + 26

        def note(text, y, color=theme.TEXT_FAINT):
            for line in widgets.wrap_text(text, 12, inner, max_lines=3):
                text_at(screen, line, (x, y), 12, color)
                y += 18
            return y

        # 設定檔與輸出(放最上面)
        y = heading("設定檔與輸出", y)
        half = (inner - 8) // 2
        self.btn_load_cfg.draw(screen, pygame.Rect(x, y, half, 30), mouse_pos)
        self.btn_save_cfg.draw(screen, pygame.Rect(x + half + 8, y, half, 30), mouse_pos)
        y += 36
        y = note("設定檔記比例尺、橫切線、通道、平滑與找峰參數；讀進來會套用到全部照片", y) + 4
        self.btn_csv.draw(screen, pygame.Rect(x, y, half, 32), mouse_pos)
        self.btn_png.draw(screen, pygame.Rect(x + half + 8, y, half, 32), mouse_pos)
        y += 38
        self.btn_open_output.draw(screen, pygame.Rect(x, y, inner, 28), mouse_pos)
        y += 34
        self.title_in.draw(screen, pygame.Rect(x, y, inner, 30), mouse_pos)
        y += 36
        text_at(screen, "圖的大小", (x, y + 6), 12, theme.TEXT)
        self.out_w.draw(screen, pygame.Rect(x + 62, y, 62, 28), mouse_pos)
        text_at(screen, "×", (x + 132, y + 5), 13, theme.TEXT_DIM)
        self.out_h.draw(screen, pygame.Rect(x + 146, y, 62, 28), mouse_pos)
        text_at(screen, "px（寬×高）", (x + 214, y + 6), 12, theme.TEXT_DIM)
        y += 34
        text_at(screen, "強度範圍", (x, y + 6), 12, theme.TEXT)
        self.y_min.draw(screen, pygame.Rect(x + 62, y, 62, 28), mouse_pos)
        text_at(screen, "～", (x + 130, y + 5), 13, theme.TEXT_DIM)
        self.y_max.draw(screen, pygame.Rect(x + 146, y, 62, 28), mouse_pos)
        y += 34
        y = note("想讓強度看起來扁一點：把高調小，或把強度範圍放大（例如 0～255）", y) + 2
        y = note("Excel 有四頁：每條線、r 與 r²、Zeeman 總表、設定（全部照片）；PNG 是這張的強度圖和剖面圖；"
                 "都存在 output\\zeeman", y)
        y = note(self.message, y + 2, self.message_color) + 12

        # 1. 照片
        y = heading("1. 照片", y)
        self.btn_add.draw(screen, pygame.Rect(x, y, 110, 30), mouse_pos)
        self.btn_prev.draw(screen, pygame.Rect(x + inner - 76, y, 36, 30), mouse_pos)
        self.btn_next.draw(screen, pygame.Rect(x + inner - 36, y, 36, 30), mouse_pos)
        y += 36
        self.btn_remove.enabled = photo is not None
        self.btn_remove.draw(screen, pygame.Rect(x, y, 110, 28), mouse_pos)
        self.btn_clear_photos.enabled = bool(self.photos)
        self.btn_clear_photos.draw(screen, pygame.Rect(x + 118, y, 110, 28), mouse_pos)
        y += 34
        text = f"{self.index + 1}/{len(self.photos)}  {photo.path.name}" if photo else "還沒有照片（也可以直接拖進來）"
        text_at(screen, widgets.clip_text(text, 12, inner), (x, y), 12, theme.TEXT_DIM)
        y += 22
        text_at(screen, "電流", (x, y + 6), 13, theme.TEXT)
        self.current_in.draw(screen, pygame.Rect(x + 60, y, inner - 60, 30), mouse_pos)
        y += 40
        text_at(screen, "工具", (x, y + 6), 13, theme.TEXT)
        self.tool_pick.draw(screen, pygame.Rect(x + 60, y, inner - 60, 30), mouse_pos)
        y += 42

        # 2. 比例尺
        y = heading("2. 比例尺", y)
        text_at(screen, "尺的長度", (x, y + 6), 13, theme.TEXT)
        self.ruler_mm.draw(screen, pygame.Rect(x + 80, y, 80, 30), mouse_pos)
        text_at(screen, "mm", (x + 168, y + 6), 13, theme.TEXT_DIM)
        y += 38
        text_at(screen, "或直接輸入", (x, y + 6), 13, theme.TEXT)
        self.manual_scale.draw(screen, pygame.Rect(x + 80, y, inner - 80, 30), mouse_pos)
        y += 38
        scale = f"{self.px_per_mm:.2f} px/mm" if self.px_per_mm else "還沒有比例尺"
        text_at(screen, scale, (x, y + 4), 14, self.accent if self.px_per_mm else theme.WARN, bold=True)
        self._save_scale_rect = pygame.Rect(x + inner - 110, y, 110, 28)
        hover = self._save_scale_rect.collidepoint(mouse_pos)
        rounded_panel(screen, self._save_scale_rect, theme.PANEL_LIGHT if hover else theme.PANEL, radius=7,
                      border=self.accent if hover else theme.PANEL_EDGE)
        text_at(screen, "存比例尺", self._save_scale_rect.center, 12, theme.TEXT_DIM, center=True)
        y += 34
        y = note("選上方「比例尺」工具，拖尺的兩端對齊刻度；同一批照片共用，存起來下次也會用", y) + 10

        # 3. 強度圖
        y = heading("3. 強度圖", y)
        self.channel.draw(screen, pygame.Rect(x, y, inner, 30), mouse_pos)
        y += 38
        text_at(screen, "自動對比（只影響顯示）", (x, y + 3), 13, theme.TEXT)
        self.stretch.draw(screen, (x + inner - 42, y + 2), mouse_pos)
        y += 34

        # 4. 橫切線
        y = heading("4. 橫切線", y)
        cols = [("起點 x", self.in_x0), ("起點 y", self.in_y0), ("終點 x", self.in_x1), ("終點 y", self.in_y1),
                ("帶寬", self.in_band)]
        col_w = (inner - 24) // 5
        for k, (label, field) in enumerate(cols):
            cx = x + k * (col_w + 6)
            text_at(screen, label, (cx, y), 11, theme.TEXT_DIM)
            field.draw(screen, pygame.Rect(cx, y + 16, col_w, 28), mouse_pos)
        y += 52
        self.btn_level.draw(screen, pygame.Rect(x, y, 64, 28), mouse_pos)
        self.btn_line_all.draw(screen, pygame.Rect(x + 72, y, 130, 28), mouse_pos)
        y += 34
        y = note("拉平：變成水平線。套用到全部照片：同一批照片用同一條橫切線（新加入的照片也會沿用）", y) + 2
        y = note("可以斜著拉，避開刻度尺；帶寬：取垂直方向這麼多像素的平均亮度，降低雜訊", y) + 10

        # 5. 找峰
        y = heading("5. 剖面與找峰", y)
        rows = [("平滑", self.smooth_on, self.sigma, f"σ {self.sigma.value:g}"),
                ("去刻度線", self.despike_on, self.dip_width, f"窄於 {int(self.dip_width.value)} px")]
        for label, toggle, slider, value in rows:
            text_at(screen, label, (x, y + 3), 13, theme.TEXT)
            toggle.draw(screen, (x + 74, y + 2), mouse_pos)
            slider.draw(screen, pygame.Rect(x + 130, y + 10, inner - 210, 14), mouse_pos)
            text_at(screen, value, (x + inner, y + 4), 12, theme.TEXT_DIM, right=True)
            y += 32
        for label, slider, value, text in (
                ("最小顯著度", self.prominence, f"{int(self.prominence.value)}",
                 "峰要比兩旁的谷高出多少亮度才算；雜訊的小起伏被當成峰時調高"),
                ("最小間距", self.distance, f"{int(self.distance.value)} px",
                 "兩個峰至少隔幾個像素；一條亮紋被當成兩個峰時調高，靠很近的分裂線找不到時調低")):
            text_at(screen, label, (x, y + 3), 13, theme.TEXT)
            slider.draw(screen, pygame.Rect(x + 90, y + 10, inner - 160, 14), mouse_pos)
            text_at(screen, value, (x + inner, y + 4), 12, theme.TEXT_DIM, right=True)
            y = note(text, y + 24) + 6
        text_at(screen, "分裂", (x, y + 6), 13, theme.TEXT)
        self.per_order.draw(screen, pygame.Rect(x + 60, y, inner - 60, 28), mouse_pos)
        y += 34
        y = note(SPLIT_NOTES[self.per_order.value], y) + 4
        text_at(screen, "標到第幾級", (x, y + 6), 13, theme.TEXT)
        self.orders.draw(screen, pygame.Rect(x + 90, y, inner - 90, 28), mouse_pos)
        y += 34
        n = int(self.orders.value)
        names = "、".join(f"{k}-、{k}、{k}+" if self.per_order.value == "3" else str(k) for k in range(1, n + 1))
        y = note(f"每邊從中心往外：{names}；更外面的峰不找、不標", y) + 4
        self.btn_find.draw(screen, pygame.Rect(x, y, 96, 30), mouse_pos)
        self.btn_relabel.draw(screen, pygame.Rect(x + 102, y, 80, 30), mouse_pos)
        self.btn_clear_peaks.draw(screen, pygame.Rect(x + 188, y, inner - 188, 30), mouse_pos)
        y += 36
        self.btn_drop_unlabeled.draw(screen, pygame.Rect(x, y, 130, 28), mouse_pos)
        y += 36
        y = note("剖面圖上：雙擊加峰、拖曳移動、右鍵刪除；滾輪放大；選中的峰可以用左右方向鍵微調（Shift 走 5 px）；"
                 "Ctrl+Z 復原", y) + 10

        # 6. 中心
        y = heading("6. 中心與位置", y)
        center, mids = self.center()
        pairs = self.label_pairs()
        excluded = photo.excluded if photo is not None else []
        text_at(screen, "用哪一級定中心", (x, y + 6), 12, theme.TEXT)
        self.center_order.draw(screen, pygame.Rect(x + 100, y, inner - 100, 28), mouse_pos)
        y += 34
        level = self.center_order.value
        if not pairs:
            y = note(f"找不到左右都有的第 {level} 級；先找峰、加峰，或在剖面圖拖綠色中線", y, theme.WARN) + 6
        else:
            y = note(f"用第 {level} 級左右各一組的中點平均定中心；點一組可以不算它", y) + 2
        for name, a, b in pairs:
            row = pygame.Rect(x, y, inner, 24)
            on = name not in excluded
            box = pygame.Rect(x, y + 4, 16, 16)
            pygame.draw.rect(screen, self.accent if on else theme.PANEL_EDGE, box, 0 if on else 2, border_radius=4)
            mid = (a + b) / 2
            diff = f"（差 {mid - center:+.1f}）" if on and center is not None else ""
            text_at(screen, f"{name}：{a:.1f} 與 {b:.1f}，中點 {mid:.1f}{diff}", (x + 24, y + 4), 12,
                      theme.TEXT if on else theme.TEXT_FAINT)
            self.pair_rects.append((row, name))
            y += 26
        self.btn_auto_center.draw(screen, pygame.Rect(x, y, 120, 30), mouse_pos)
        manual = photo is not None and photo.center_manual is not None
        text_at(screen, "目前：自己拖的中線" if manual else "目前：自動定位", (x + 130, y + 7), 12,
                  theme.WARN if manual else theme.TEXT_DIM)
        y += 38
        if center is not None:
            text = f"中心 = {center:.2f} px" + ("（自己定）" if manual else f"（{len(mids)} 組平均）")
            text_at(screen, text, (x, y + 4), 14, CENTER_COLOR, bold=True)
            y += 28
            if len(mids) > 1:
                y = note(f"各組中點最大差 {max(mids) - min(mids):.2f} px", y)
        y += 6
        if photo is not None and photo.peaks:
            for text, cx, right in (("標籤", x, False), ("沿線 px", x + 110, True), ("位置 mm", x + 210, True),
                                    ("強度", x + inner, True)):
                text_at(screen, text, (cx, y), 11, theme.TEXT_DIM, right=right)
            y += 18
            for k, peak in enumerate(photo.peaks):
                row = pygame.Rect(x - 4, y - 2, inner + 8, 22)
                if k == self.selected:
                    rounded_panel(screen, row, theme.PANEL_LIGHT, radius=5)
                mm = (peak["x"] - center) / self.px_per_mm if center is not None and self.px_per_mm else None
                text_at(screen, peak["label"] or "-", (x, y), 12, theme.TEXT)
                text_at(screen, f"{peak['x']:.1f}", (x + 110, y), 12, theme.TEXT, right=True)
                text_at(screen, "" if mm is None else f"{mm:+.4f}", (x + 210, y), 12, theme.TEXT, right=True)
                text_at(screen, f"{self._value_at(peak['x']):.1f}", (x + inner, y), 12, theme.TEXT_DIM, right=True)
                self.peak_rects.append((row, k))
                y += 22
        if self.selected is not None and photo is not None and self.selected < len(photo.peaks):
            y += 4
            self.label_edit.draw(screen, pygame.Rect(x, y, inner - 110, 30), mouse_pos)
            self.btn_delete_peak.draw(screen, pygame.Rect(x + inner - 102, y, 102, 30), mouse_pos)
            y += 38
        y += 8

        # 7. Zeeman 分析
        y = heading("7. Zeeman 分析", y)
        fields = [("磁場係數 k", self.field_k, "T/A"), ("截距 b", self.field_b, "T"),
                  ("標準具厚度 t", self.etalon_t, "mm"), ("折射率 n", self.etalon_n, "空氣是 1"),
                  ("波長 λ", self.wavelength, "nm")]
        for label, field, hint in fields:
            text_at(screen, label, (x, y + 6), 12, theme.TEXT)
            field.draw(screen, pygame.Rect(x + 96, y, 96, 28), mouse_pos)
            if field is self.field_b:
                self.use_b.draw(screen, (x + 200, y + 3), mouse_pos)
                text_at(screen, "用截距" if self.use_b.value else "不用", (x + 248, y + 6), 12, theme.TEXT_DIM)
            else:
                text_at(screen, hint, (x + 200, y + 6), 12, theme.TEXT_DIM)
            y += 34
        y = note("磁場 B = k × 電流" + (" + b" if self.use_b.value else "（不加截距）"), y) + 4
        result = self.analyze()
        if result is None or not result["r2"]:
            y = note("先找峰、定好中心，這裡會算出每條線的 r² 和分裂量", y, theme.WARN) + 6
        else:
            unit = result["unit"]
            col_r, col_r2 = x + 150, x + inner - 10
            text_at(screen, "標籤", (x, y), 11, theme.TEXT_DIM)
            text_at(screen, f"r（{unit}）", (col_r, y), 11, theme.TEXT_DIM, right=True)
            text_at(screen, f"r²（{unit}²）", (col_r2, y), 11, theme.TEXT_DIM, right=True)
            y += 18
            for label in sorted(result["r2"], key=analysis._label_key):
                text_at(screen, label, (x, y), 12, theme.TEXT)
                text_at(screen, f"{result['r'][label]:.4f}", (col_r, y), 12, theme.TEXT, right=True)
                text_at(screen, f"{result['r2'][label]:.5f}", (col_r2, y), 12, theme.TEXT, right=True)
                y += 20
            lines = [f"第 {p} 級分裂 Δ = (r²({p}+) - r²({p}-))/2 = {d:.5f}" for p, d in result["delta"].items()]
            lines.append(f"相鄰級 r² 差 D（π 線對級數的斜率）= {result['D']:.5f}" if result["D"] else
                         "相鄰級 r² 差 D：要有 2 級以上的 π 線")
            if result["ratio"] is not None:
                lines.append(f"δ = Δ/D = {result['ratio']:.5f}（自由光譜範圍的幾分之幾）")
            if result["dk"] is not None:
                lines.append(f"Δk = δ/(2nt) = {result['dk']:.5f} 1/cm、Δλ = {result['dl'] * 1000:.4f} pm")
            if result["B"] is not None:
                lines.append(f"B = {result['B']:.4f} T")
            for text in lines:
                y = note(text, y, theme.TEXT)
            y += 4
        points, fit = self.batch_fit()
        if fit is not None:
            slope, _, error = fit
            xname = "B" if self.field_k.text.strip() else "電流"
            yname = "Δk" if self.etalon_t.text.strip() else "δ"
            text = f"全部 {len(points)} 張：{yname} 對 {xname} 的斜率 {slope:.5g}" + (f" ± {error:.2g}" if error else "")
            if xname == "B" and yname == "Δk":
                text += f"，g ≈ {slope * analysis.HC_OVER_MUB:.3f}"
            y = note(text, y, CENTER_COLOR)
        y = note("r² 的差不受橫切線位置影響（不用剛好過環心）；只有一邊的線用到中心的距離算 r", y) + 10

        y = note(self.message, y, self.message_color)
        content_h = y + self.view.scroll - area.y + 10
        screen.set_clip(None)
        self.view.layout(area, content_h)
        self.view.draw(screen, mouse_pos)

    _save_scale_rect = pygame.Rect(0, 0, 0, 0)


def _nice_step(span):
    """座標軸刻度間隔:1、2、5 × 10 的次方,大約 6～10 格。"""
    if span <= 0:
        return 1
    raw = span / 8
    power = 10 ** math.floor(math.log10(raw))
    for factor in (1, 2, 5, 10):
        if raw <= factor * power:
            return factor * power
    return 10 * power
