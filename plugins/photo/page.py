"""圖片工具:把圖片拖進來,依模式編輯。

- 調整:裁切、比例、旋轉、翻轉、四點校正、擴展畫布(和「圖片轉檔」的編輯視窗共用同一套程式,這裡直接嵌在頁面上)
- 色彩:參考 Word,校正(銳利／柔化、亮度×對比)、色彩(飽和度、色調、重新著色)都是縮圖直接點選,微調用滑桿
- 效果:美術效果(鉛筆素描、油畫、馬賽克等)縮圖點選,可調強度;滑鼠移到縮圖上,大預覽會先顯示套用後的樣子
- 掃描:類似掃描 App,自動找出照片裡文件的四個角拉正,去陰影變白底(增強、灰階、黑白),多張依順序合成 PDF
- 高清:用 AI(Real-ESRGAN,第一次使用時下載)讓模糊的圖變清楚,預設尺寸不變,也可以放大 2～4 倍;可以拖曳分隔線比較前後
- 去背:用 AI(BiRefNet,第一次使用時下載)或單色背景找出主體,背景變透明、白色、其他顏色或模糊;
  AI 沒抓好的地方用「保留」「移除」筆刷在預覽上修正。遮罩記在原圖上,之後再旋轉、裁切也會跟著走
- 改檔名:依樣式批次改名,預設另外輸出一份,也可以直接改原檔(可以復原)
每張圖的編輯各自記住,可以復原、重做;輸出一律另存到 output\\photo\\,不會覆蓋原圖。
"""

import os
import queue
import threading
import time
from pathlib import Path

import pygame
from PIL import Image

from core import paths, theme, widgets
from core.drag_sort import DragSort, move_items
from core.plugins import Page
from core.scroll import BAR_SPACE, ScrollView
from core.files import free_path
from core.widgets import Button, Dropdown, SegmentedControl, Slider, TextInput, Toggle, draw_text, rounded_panel

from ..images import cutout, looks, ops, scan, upscale
from ..images.editor import ImageEditor, _blit_checker
from ..images.page import Item
from . import rename

MODES = [("adjust", "調整"), ("color", "色彩"), ("effect", "效果"), ("scan", "掃描"), ("hd", "高清"),
         ("cutout", "去背"), ("rename", "改檔名")]
BRUSHES = [("off", "不修正"), ("keep", "保留"), ("remove", "移除")]
CUT_BACKGROUNDS = [("clear", "透明"), ("white", "白色"), ("color", "顏色"), ("blur", "模糊")]
CUT_BACKGROUND_NOTES = {"clear": "背景變透明；存成原格式時，JPG 會自動改存 PNG", "white": "背景換成白色",
                        "color": "背景換成下面選的顏色", "blur": "保留原本的背景但變模糊，像手機的人像模式"}
BRUSH_COLORS = {"keep": (70, 210, 120), "remove": (235, 70, 90)}
HD_VIEWS = [("fit", "整張"), ("actual", "放大檢視")]
SCAN_BAR = 52          # 掃描畫面下方放「調整四個角/預覽結果」按鈕的高度
SCAN_OUTPUTS = [("pdf", "合成 PDF"), ("jpg", "每張 JPG"), ("png", "每張 PNG")]
SCAN_PAGES = [("a4", "A4"), ("fit", "依圖片大小")]
SCAN_PAGE_NOTES = {"a4": "每張放進 A4 頁面，列印剛好", "fit": "頁面和拉正後的圖片一樣大"}
COLOR_TABS = [("correct", "校正"), ("color", "色彩"), ("fine", "微調")]
SIDE_PAD = 14               # 右邊設定區底板的內距
HIDDEN = pygame.Rect(-10000, -10000, 0, 0)     # 捲到看不見的設定:移到畫面外,不會被點到
LIST_W = 250
ROW_H = 62
THUMB = 44
HEADER_H = 50
FOOTER_H = 74
SIDE_W = 300
COLOR_ROWS = [("brightness", "亮度"), ("contrast", "對比"), ("saturation", "飽和度"), ("warmth", "色溫"),
              ("sharpness", "銳利度")]
COLOR_NOTES = {"warmth": "往右偏暖(黃)、往左偏冷(藍)", "sharpness": "往右更清楚，往左變柔和"}
SHARP_STEPS = [(-100, "柔化 50%"), (-50, "柔化 25%"), (0, "不變"), (50, "銳利化 25%"), (100, "銳利化 50%")]
LIGHT_STEPS = [-40, -20, 0, 20, 40]
SATURATION_STEPS = [(-100, "飽和度 0%"), (-67, "飽和度 33%"), (-33, "飽和度 66%"), (0, "飽和度 100%"),
                    (33, "飽和度 133%"), (67, "飽和度 166%"), (100, "飽和度 200%")]
WARMTH_STEPS = [(-100, "偏冷 100%"), (-67, "偏冷 67%"), (-33, "偏冷 33%"), (0, "不變"), (33, "偏暖 33%"),
                (67, "偏暖 67%"), (100, "偏暖 100%")]
SAVE_FORMATS = [("keep", "原格式"), ("jpg", "JPG"), ("png", "PNG"), ("webp", "WebP"), ("gif", "GIF"),
                ("anim", "合成 GIF")]
GIF_DELAYS = [("50", "每格 0.05 秒"), ("100", "每格 0.1 秒"), ("200", "每格 0.2 秒"), ("500", "每格 0.5 秒"),
              ("1000", "每格 1 秒")]
DIGITS = [("1", "1"), ("2", "01"), ("3", "001"), ("4", "0001")]
RENAME_MODES = [("copy", "另外輸出"), ("rename", "直接改原檔")]
RENAME_NOTES = {"copy": "複製一份新名字的檔案到 output\\photo\\，原本的檔案不動",
                "rename": "直接把原本的檔案改名；改錯可以按「復原改名」改回來"}
HISTORY_LIMIT = 100


def output_dir():
    return paths.OUTPUT_DIR / "photo"


def _fit(image, size):
    """縮圖:維持比例放進 size,不裁切。"""
    image = image.copy()
    image.thumbnail(size, Image.Resampling.LANCZOS)
    return image


class PhotoPage(Page):
    def __init__(self, app, tool):
        super().__init__(app, tool)
        accent = tool.accent
        self.items = []
        self.current = None
        self.notice = ("", theme.TEXT_DIM)
        self._lock = threading.Lock()
        self._probe_queue = queue.Queue()
        threading.Thread(target=self._probe_worker, daemon=True).start()
        self.worker = None
        self.history = {}           # 每張圖:{undo, redo, snap(上一個樣子), gesture(上次記錄是第幾次操作)}
        self._gesture = 0           # 每按一次滑鼠、按一次鍵就加一;同一次操作裡的修改(例如拖曳滑桿)算一步復原
        self.comparing = False      # 按住「看原圖」中

        self.mode = SegmentedControl(MODES, accent=accent)
        self.editor = ImageEditor(lambda: self.screen, accent, embedded=True)
        self.btn_undo = Button("復原", filled=False, size=13)
        self.btn_redo = Button("重做", filled=False, size=13)

        self.color_sliders = {key: Slider(-100, 100, 0, accent=accent) for key, _ in COLOR_ROWS}
        self.btn_color_reset = Button("重設色彩", filled=False, size=13)
        self.btn_color_all = Button("套用到全部", filled=False, size=13)
        self.btn_compare = Button("按住看原圖", filled=False, size=13)
        self.color_tab = SegmentedControl(COLOR_TABS, accent=accent)
        self.effect_strength = Slider(0, 100, looks.DEFAULT_STRENGTH, accent=accent)
        self.btn_effect_all = Button("套用到全部", filled=False, size=13)
        self.gallery = []           # 這一幀畫出來的縮圖:[(範圍, 要改的設定, 名稱)]
        self.hover_preset = None    # 滑鼠停在哪個縮圖上:(要改的設定, 名稱);大預覽先顯示套用後的樣子
        self._thumbs = {}
        self.side_views = {}        # 每個模式(色彩分頁)各自的捲動位置:視窗小、設定放不下時可以捲動
        self._side_view = None
        self.side_clip = pygame.Rect(0, 0, 0, 0)
        self.btn_corners = Button("調整四個角", filled=False, size=13)   # 掃描:在結果與對準四角之間切換
        self.scan_output = SegmentedControl(SCAN_OUTPUTS, accent=accent)
        self.scan_page = SegmentedControl(SCAN_PAGES, accent=accent)
        self.btn_detect = Button("自動找邊", filled=False, size=13)
        self.btn_whole = Button("用整張圖", filled=False, size=13)
        self.btn_detect_all = Button("全部自動找邊", filled=False, size=13)
        self.btn_scan_all = Button("濾鏡套用到全部", filled=False, size=13)
        self.btn_export = Button("全部輸出", accent=accent, size=14)
        self.scan_checked = set()   # 掃描模式下已經自動找過邊的圖,不重複找(手動調整過的不會被蓋掉)
        self.hd_model = "photo"
        self.hd_texture = Slider(0, 100, upscale.TEXTURE, step=5, accent=accent)
        self._hd_mix = None         # (哪幾張結果、質感, 混合後):拉質感時才重新混合
        self.hd_strength = Slider(0, 100, 100, step=5, accent=accent)
        self.hd_scale = SegmentedControl(upscale.SCALES, accent=accent)
        self.hd_view = SegmentedControl(HD_VIEWS, accent=accent)
        self.hd_rows = []           # 這一幀畫出來的模型選項:[(範圍, 代號)]
        self.btn_hd_preview = Button("預覽這張", accent=accent, filled=False, size=13)
        self.btn_hd_save_all = Button("全部變清楚並儲存", accent=accent, size=14)
        self.hd_job = None          # 正在預覽:{key, progress, cancel, error}
        self.hd_result = None       # (key, 原圖套用編輯後, [各網路放大後的結果])
        self.hd_split = 0.5         # 比較畫面的分隔線位置(0～1)
        self.hd_center = None       # 放大檢視時畫面中心(處理後的座標)
        self.hd_drag = None
        self.hd_format = SegmentedControl(SAVE_FORMATS[:4], accent=accent)
        self._hd_view = None
        self._compare_area = None
        self.cut_method = "lite"
        self.cut_methods = SegmentedControl([(key, name) for key, name, _ in cutout.METHODS], accent=accent)
        self.btn_cut = Button("自動去背", accent=accent, filled=False, size=13)
        self.btn_cut_remove = Button("移除去背", filled=False, size=13)
        self.btn_cut_strokes = Button("清除筆刷", filled=False, size=13)
        self.btn_cut_all = Button("全部去背", filled=False, size=13)
        self.brush = SegmentedControl(BRUSHES, accent=accent)
        self.brush_size = Slider(4, 80, 18, accent=accent)
        self.cut_shrink = Slider(0, 10, 0, step=0.1, accent=accent)       # 可以調到小數一位;滑鼠滾輪每格 0.1
        self.cut_feather = Slider(0, 10, 0, step=0.1, accent=accent)
        self.cut_tolerance = Slider(5, 120, cutout.TOLERANCE, accent=accent)
        self.cut_background = SegmentedControl(CUT_BACKGROUNDS, accent=accent)
        self.cut_trim = Toggle(False, accent=accent)
        self.swatches = []          # 這一幀畫出來的背景顏色:[(範圍, 顏色)]
        self.stroke = None          # 正在畫的一筆:{keep, radius, points(畫面座標)}
        self.hover_slider = None    # 滑鼠停在哪個去背滑桿上(按 ← → 細調的對象)
        self._preview_rect = None   # 大預覽畫在哪裡(筆刷換算座標用)
        self._mask_queue = queue.Queue()
        self._mask_pending = set()  # 排隊中或計算中的 AI 遮罩
        self._mask_failed = {}      # 算不出來的:{遮罩名字: 原因},按「自動去背」才重試
        self.mask_busy = None       # 正在算哪一張
        threading.Thread(target=self._mask_worker, daemon=True).start()
        self.canvas = pygame.Rect(0, 0, 0, 0)
        self._color_view = None     # (key, surface)
        self._fit_base = None       # (key, 縮到畫面大小的底圖)

        self.pattern = TextInput("{名稱}_{序號}", accent=accent, size=14)
        self.start_number = TextInput("1", accent=accent, size=14)
        self.digits = SegmentedControl(DIGITS, index=2, accent=accent)
        self.rename_mode = SegmentedControl(RENAME_MODES, accent=accent)
        self.token_buttons = [(token, Button(token.strip("{}"), filled=False, size=12)) for token, _ in rename.TOKENS]
        self.rename_view = ScrollView(accent=accent)
        self._preview = (None, [], [])      # (key, 新名字, 問題)
        self._dates = {}
        self.last_rename = None             # 直接改原檔時的 [(舊路徑, 新路徑)],可以復原

        self.save_format = SegmentedControl(SAVE_FORMATS, accent=accent)
        self.gif_delay = Dropdown(GIF_DELAYS, index=1, accent=accent, size=13)
        self.btn_output = Button("輸出資料夾", filled=False, size=13)
        self.btn_save_one = Button("儲存這張", filled=False, size=14)
        self.btn_save_all = Button("全部儲存", accent=accent, size=14)
        self.btn_rename = Button("開始改名", accent=accent, size=14)
        self.btn_undo_rename = Button("復原改名", filled=False, size=13)
        self.btn_clear = Button("清空", filled=False, size=12)

        self.list_view = ScrollView(accent=accent)
        self.list_area = pygame.Rect(0, 0, 0, 0)
        self.row_buttons = []
        self.order_drag = DragSort(accent, on_drop=self._reorder)

    # ------------------------------------------------------------ 清單

    @property
    def running(self):
        return self.worker is not None and self.worker.is_alive()

    def add_files(self, raw_paths):
        candidates, skipped = [], 0
        for raw in raw_paths:
            path = Path(raw)
            if path.is_dir():
                candidates += sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in ops.READ_EXTS)
            elif path.is_file() and path.suffix.lower() in ops.READ_EXTS:
                candidates.append(path)
            else:
                skipped += 1
        added = []
        with self._lock:
            existing = {item.path for item in self.items}
            for path in candidates:
                if path in existing:
                    continue
                item = Item(path)
                self.items.append(item)
                self.history[item] = dict(undo=[], redo=[], snap=item.edit.copy(), gesture=-1)
                existing.add(path)
                added.append(item)
                self._probe_queue.put(item)
        self.notice = ("有檔案不是支援的圖片格式，已略過", theme.WARN) if skipped else ("", theme.TEXT_DIM)
        if added and self.current is None:
            self.select(added[0])

    def _probe_worker(self):
        while True:
            item = self._probe_queue.get()
            try:
                item.info = ops.probe(item.path, (THUMB, THUMB))
            except Exception as exc:
                item.info = ops.describe_error(exc)

    def _commit_warp(self):
        """四點校正調整到一半(拖過角還沒按完成)時先存起來;角擠成一團存不了時回傳 False。"""
        editor = self.editor
        if editor.warping:
            editor._finish_warp()
            if editor.warping:
                self.notice = (editor.message, theme.WARN)
                return False
        return True

    def select(self, item):
        if item is self.current:
            return
        if not self._commit_warp():
            self.editor.warping = False
        self.current = item
        self._color_view = self._fit_base = None
        if item is None:
            self.editor.close()
            return
        self.editor.open(item, lambda: list(self.items), True, self._edited)
        self._sync_color()
        self._sync_cut()

    def remove(self, item):
        with self._lock:
            if item not in self.items:
                return
            index = self.items.index(item)
            self.items.remove(item)
            self.history.pop(item, None)
        if item is self.current:
            self.current = None
            self.select(self.items[min(index, len(self.items) - 1)] if self.items else None)

    def _reorder(self, indexes, insert_at):
        with self._lock:
            self.items, _ = move_items(self.items, indexes, insert_at)

    # ------------------------------------------------------------ 復原、重做

    def _edited(self, item):
        """編輯改變了(調整畫面或色彩滑桿);同一次按下滑鼠裡的修改併成一步復原。"""
        item.thumb = None
        self._color_view = None
        record = self.history.get(item)
        if record is None or item.edit == record["snap"]:
            return
        if record["gesture"] != self._gesture or not record["undo"]:
            record["undo"].append(record["snap"])
            del record["undo"][:-HISTORY_LIMIT]
        record["redo"].clear()
        record["snap"], record["gesture"] = item.edit.copy(), self._gesture

    def _restore(self, item, edit):
        record = self.history[item]
        item.edit = edit.copy()
        record["snap"], record["gesture"] = item.edit.copy(), -1
        self.editor.edit = item.edit
        self.editor.warping, self.editor.warp_points, self.editor.drag = False, None, None
        item.thumb = None
        self._color_view = None
        self._sync_color()
        self._sync_cut()

    def undo(self):
        item = self.current
        record = self.history.get(item)
        if record and record["undo"]:
            record["redo"].append(item.edit.copy())
            self._restore(item, record["undo"].pop())

    def redo(self):
        item = self.current
        record = self.history.get(item)
        if record and record["redo"]:
            record["undo"].append(item.edit.copy())
            self._restore(item, record["redo"].pop())

    # ------------------------------------------------------------ 色彩

    def _sync_color(self):
        values = (self.current.edit.adjust if self.current is not None else None) or (0,) * len(COLOR_ROWS)
        for (key, _), value in zip(COLOR_ROWS, values):
            self.color_sliders[key].value = value
            self.color_sliders[key].dragging = False
        effect = self.current.edit.effect if self.current is not None else None
        if effect:
            self.effect_strength.value = effect[1]

    def _apply_color(self):
        item = self.current
        if item is None:
            return
        values = tuple(int(self.color_sliders[key].value) for key, _ in COLOR_ROWS)
        item.edit.adjust = values if any(values) else None
        self._edited(item)

    def _color_all(self, fields=("adjust", "recolor"), what="色彩"):
        source = self.current.edit
        others = [item for item in self.items if item is not self.current and isinstance(item.info, dict)]
        for item in others:
            for field in fields:
                setattr(item.edit, field, getattr(source, field))
            self._edited(item)
        self.notice = (f"已把{what}套用到其他 {len(others)} 張圖片", self.tool.accent)

    @staticmethod
    def _with(edit, changes):
        """edit 套用 changes(亮度等數值、recolor、effect)後的新編輯,不改到原本的。"""
        edit = edit.copy()
        values = list(edit.adjust or (0,) * len(ops.ADJUST_KEYS))
        for key, value in changes.items():
            if key in ops.ADJUST_KEYS:
                values[ops.ADJUST_KEYS.index(key)] = value
            else:
                setattr(edit, key, value)
        edit.adjust = tuple(values) if any(values) else None
        return edit

    def apply_preset(self, changes):
        item = self.current
        if item is None:
            return
        new = self._with(item.edit, changes)
        for field in ("adjust", "recolor", "effect", "scan"):
            setattr(item.edit, field, getattr(new, field))
        self._sync_color()
        self._edited(item)

    def _apply_strength(self):
        item = self.current
        if item is not None and item.edit.effect:
            item.edit.effect = (item.edit.effect[0], int(self.effect_strength.value))
            self._edited(item)

    # ------------------------------------------------------------ 去背

    @staticmethod
    def _animated(item):
        return isinstance(item.info, dict) and item.info.get("frames", 1) > 1

    def _sync_cut(self):
        """換圖、復原後,設定區顯示這張圖的去背設定;沒有去背時保留上次的選擇(給下一次用)。"""
        self.stroke = None
        cut = self.current.edit.cutout if self.current is not None else None
        if cut is None:
            self.brush.index = 0
            return
        self.cut_method = cut.method
        self.cut_shrink.value, self.cut_feather.value, self.cut_tolerance.value = cut.shrink, cut.feather, cut.tolerance
        self.cut_background.index = [k for k, _ in CUT_BACKGROUNDS].index(cut.background)
        self.cut_trim.value = cut.trim
        for slider in (self.cut_shrink, self.cut_feather, self.cut_tolerance, self.brush_size):
            slider.dragging = False

    def _panel_cut(self, item, method):
        """依設定區目前的選擇做出這張圖的去背設定(修正筆刷沿用這張圖原本的)。"""
        old = item.edit.cutout
        color = old.color if old is not None else cutout.SWATCHES[0]
        return cutout.Cutout(method, cutout.mask_key(item.path, method) if method in cutout.AI_MODELS else None,
                             old.strokes if old is not None else (), round(self.cut_shrink.value, 1),
                             round(self.cut_feather.value, 1), self.cut_background.value, color, self.cut_trim.value,
                             int(self.cut_tolerance.value))

    def _with_cut_engine(self, method, action):
        missing = cutout.required(method)
        if missing:
            self.app.consent.open("圖片去背", missing, on_done=action)
        else:
            action()

    def cut_start(self, method=None):
        """自動去背(或換一種方式重新去背)。"""
        item = self.current
        if item is None or not isinstance(item.info, dict):
            return
        method = method or self.cut_method
        if method in cutout.AI_MODELS and self._animated(item):
            self.notice = ("動畫只能用「單色背景」去背", theme.WARN)
            return
        self.cut_method = method

        def go():
            if item not in self.items:
                return
            new = self._panel_cut(item, method)
            self._mask_failed.pop(new.key, None)
            item.edit.cutout = new
            self._edited(item)
            self.notice = ("", theme.TEXT_DIM)

        self._with_cut_engine(method, go)

    def _set_cut(self, **changes):
        item = self.current
        if item is None or item.edit.cutout is None:
            return
        item.edit.cutout = item.edit.cutout._replace(**changes)
        self._edited(item)

    def cut_remove(self):
        item = self.current
        if item is not None and item.edit.cutout is not None:
            item.edit.cutout = None
            self.brush.index = 0
            self._edited(item)

    def cut_all(self):
        """這張的去背設定套用到全部(修正筆刷不套用);AI 的遮罩在背景依序算。"""
        source = self.current
        if source is None:
            return
        method = source.edit.cutout.method if source.edit.cutout else self.cut_method

        def go():
            done, skipped = 0, 0
            for item in list(self.items):
                if not isinstance(item.info, dict):
                    continue
                if method in cutout.AI_MODELS and self._animated(item):
                    skipped += 1
                    continue
                new = self._panel_cut(item, method)
                if item is not source:
                    new = new._replace(strokes=())
                self._mask_failed.pop(new.key, None)
                item.edit.cutout = new
                self._edited(item)
                done += 1
            text = f"已把去背套用到 {done} 張圖片" + (f"；{skipped} 張動畫不能用 AI 去背" if skipped else "")
            self.notice = (text, theme.WARN if skipped else self.tool.accent)

        self._with_cut_engine(method, go)

    def _need_masks(self):
        """還沒算好的 AI 遮罩排進背景佇列(目前這張優先)。元件沒下載或算失敗的不排。"""
        order = ([self.current] if self.current is not None else []) + list(self.items)
        for item in order:
            cut = item.edit.cutout
            if cut is None or not cut.ai or cut.key in self._mask_pending or cut.key in self._mask_failed:
                continue
            if cutout.stored(cut.key) is None and not cutout.required(cut.method):
                self._mask_pending.add(cut.key)
                self._mask_queue.put((item, cut))

    def _mask_worker(self):
        while True:
            item, cut = self._mask_queue.get()
            self.mask_busy = item
            try:
                if cutout.stored(cut.key) is None:
                    image, _ = ops.load_view(item.path, True, cutout.KEEP_SIDE)
                    cutout.remember(cut.key, cutout.predict(image, cut.method))
            except Exception as exc:
                self._mask_failed[cut.key] = ops.describe_error(exc)
                self.notice = (f"{item.path.name} 去背失敗：{ops.describe_error(exc)}", theme.DANGER)
            finally:
                self._mask_pending.discard(cut.key)
                self.mask_busy = None
                for other in list(self.items):
                    if other.edit.cutout is not None and other.edit.cutout.key == cut.key:
                        other.thumb = None
                self._color_view = None

    def _cut_state(self):
        """目前這張的去背狀態文字(畫面左上角):算遮罩中、失敗、缺元件;沒事時是空字串。"""
        cut = self.current.edit.cutout if self.current is not None else None
        if cut is None or not cut.ai or cutout.stored(cut.key) is not None:
            return ""
        if cut.key in self._mask_failed:
            return f"去背失敗：{self._mask_failed[cut.key]}"
        if cutout.required(cut.method):
            return "缺少去背元件，按「自動去背」下載"
        where = cutout.device()
        return "AI 去背中…" + (f"（用{where}）" if where else "")

    def _commit_stroke(self):
        """畫完一筆:把畫面上的位置換回原圖上的比例位置,加進修正筆刷(一筆是一步復原)。"""
        stroke, self.stroke = self.stroke, None
        rect, small, item = self._preview_rect, self.editor.small, self.current
        if not stroke or rect is None or small is None or item is None or item.edit.cutout is None:
            return
        edit = item.edit
        out_w, out_h = ops.edited_size(small.size, edit)

        def source(point):
            u = (point[0] - rect.x) / max(1, rect.width) * out_w
            v = (point[1] - rect.y) / max(1, rect.height) * out_h
            return ops.to_source((u, v), small.size, edit)

        points = [source(point) for point in stroke["points"]]
        # 半徑也換算:畫面上往右 radius 的點換回原圖量距離(旋轉、校正後的縮放都算進去)
        start = stroke["points"][0]
        edge = source((start[0] + stroke["radius"], start[1]))
        radius = max(0.5, ((edge[0] - points[0][0]) ** 2 + (edge[1] - points[0][1]) ** 2) ** 0.5)
        normalized = tuple((x / small.width, y / small.height) for x, y in points)
        cut = edit.cutout
        self._set_cut(strokes=cut.strokes + ((stroke["keep"], radius / max(small.size), normalized),))

    # ------------------------------------------------------------ 文件掃描

    @staticmethod
    def _find_corners(item, base):
        """在旋轉、翻轉後(還沒校正)的畫面上找文件的四個角。"""
        edit = item.edit
        turned = ops.apply_edit(base, ops.Edit(edit.angle, edit.quarter, edit.flip, fill=edit.fill))
        return scan.find_document(turned)

    def detect(self, item=None, quiet=False):
        """自動找邊並套用;第一次掃描時也把濾鏡設成「增強」。找不到時提示手動拖曳。"""
        item = item or self.current
        editor = self.editor
        if item is None or editor.base is None or editor.item is not item:
            return
        self.scan_checked.add(item)
        found = self._find_corners(item, editor.base)
        if found:
            item.edit.warp, item.edit.crop = found, None
            if editor.warping:
                editor.warp_points = list(found)
        elif not quiet:
            self.notice = ("找不到文件的邊，請拖曳四個角對準文件", theme.WARN)
        if item.edit.scan is None and quiet:
            item.edit.scan = "enhance"
        self._edited(item)
        if not found and quiet:
            self.notice = ("找不到文件的邊，請拖曳四個角對準文件；也可以按「用整張圖」", theme.WARN)
        return found

    def use_whole(self):
        item = self.current
        if item is None:
            return
        self.editor.warping = False
        item.edit.warp = None
        self.scan_checked.add(item)
        self._edited(item)

    def detect_all(self):
        """全部的圖都自動找邊(在背景讀圖,不會卡住畫面);手動調整過的也會重新找。"""
        if self.running or not self._commit_warp():
            return
        items = [item for item in self.items if isinstance(item.info, dict)]
        self.editor.warping = False
        self.notice = (f"找邊中 0 / {len(items)}", theme.TEXT_DIM)

        def work():
            missed = 0
            for number, item in enumerate(items, 1):
                try:
                    base, _ = ops.load_view(item.path, True, 1200)
                    found = self._find_corners(item, base)
                except Exception:
                    found = None
                if found:
                    item.edit.warp, item.edit.crop = found, None
                else:
                    missed += 1
                if item.edit.scan is None:
                    item.edit.scan = "enhance"
                self.scan_checked.add(item)
                self._edited(item)
                self.notice = (f"找邊中 {number} / {len(items)}", theme.TEXT_DIM)
            text = f"已找好 {len(items) - missed} 張的邊" + (f"；{missed} 張找不到，請手動對準四個角" if missed else "")
            self.notice = (text, theme.WARN if missed else self.tool.accent)

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def export_scan(self):
        """依清單順序輸出:合成一份 PDF,或每張存成 JPG/PNG。"""
        if self.running or not self._commit_warp():
            return
        items = [item for item in self.items if isinstance(item.info, dict)]
        if not items:
            return
        edits = [item.edit.copy() for item in items]
        target = self.scan_output.value
        for item in items:
            item.status = "running"
        self.notice = (f"輸出中（{len(items)} 張）", theme.TEXT_DIM)

        def work():
            failed = {}
            try:
                if target == "pdf":
                    out = free_path(output_dir(), time.strftime("掃描 %Y-%m-%d %H%M%S"), ".pdf")
                    settings = ops.Settings(fmt="pdf", pdf_page=self.scan_page.value, compress=True, quality=85)
                    failed = ops.images_to_pdf([item.path for item in items], out, settings, edits=edits)
                    done = f"已合成 output\\photo\\{out.name}（{len(items) - len(failed)} 頁）"
                else:
                    settings = ops.Settings(fmt=target, compress=target == "jpg", quality=90)
                    for index, (item, edit) in enumerate(zip(items, edits)):
                        try:
                            ops.convert(item.path, output_dir(), settings, edit)
                        except Exception as exc:
                            failed[index] = ops.describe_error(exc)
                    done = f"已輸出 {len(items) - len(failed)} 張到 output\\photo\\"
            except Exception as exc:
                for item in items:
                    item.status, item.message = "error", ops.describe_error(exc)
                self.notice = (f"輸出失敗：{ops.describe_error(exc)}", theme.DANGER)
                return
            for index, item in enumerate(items):
                item.status = "error" if index in failed else "done"
                item.message = failed.get(index, "")
            self.notice = (done + (f"，{len(failed)} 張讀不到" if failed else ""),
                           theme.WARN if failed else self.tool.accent)

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    # ------------------------------------------------------------ 高清

    def _hd_key(self, item=None):
        item = item or self.current
        return id(item), str(item.path), repr(item.edit), self.hd_model, int(self.hd_scale.value)

    def _with_engine(self, action):
        """用 AI 模型前先確認元件下載好了;還沒下載時先詢問,下載完再做。"""
        missing = upscale.required(self.hd_model)
        if missing:
            self.app.consent.open("圖片高清", missing, on_done=action)
        else:
            action()

    def hd_preview(self):
        if self.current is None or self.running or self.hd_job is not None:
            return
        self._with_engine(self._start_hd_preview)

    def _start_hd_preview(self):
        item = self.current
        if item is None:
            return
        job = dict(key=self._hd_key(item), progress=0.0, cancel=threading.Event(), error="", stage="讀取原圖")
        model, scale = self.hd_model, int(self.hd_scale.value)
        edit, path = item.edit.copy(), item.path
        self.hd_job = job

        def work():
            try:
                source = upscale.load_edited(path, edit)
                job["stage"] = "處理中"
                result = upscale.upscale_parts(source, model, scale, lambda v: job.update(progress=v), job["cancel"])
            except upscale.Cancelled:
                self.hd_job = None
                return
            except Exception as exc:
                job["error"] = ops.describe_error(exc)
                self.notice = (f"放大失敗：{job['error']}", theme.DANGER)
                self.hd_job = None
                return
            self.hd_result = (job["key"], source, result)
            self.hd_center = None
            self._hd_view = None
            self.hd_job = None

        threading.Thread(target=work, daemon=True).start()

    def hd_cancel(self):
        if self.hd_job is not None:
            self.hd_job["cancel"].set()

    def hd_save(self, items):
        if self.running or self.hd_job is not None:
            return
        items = [item for item in items if isinstance(item.info, dict)]
        if items:
            self._with_engine(lambda: self._start_hd_save(items))

    def _start_hd_save(self, items):
        model, scale, fmt = self.hd_model, int(self.hd_scale.value), self.hd_format.value
        strength, texture = int(self.hd_strength.value), int(self.hd_texture.value)
        jobs = [(item, item.edit.copy(), self._hd_key(item)) for item in items]
        cached = self.hd_result
        for item in items:
            item.status = "running"
        self.notice = (f"處理中 0 / {len(items)}", theme.TEXT_DIM)

        def work():
            done, failed = 0, 0
            for number, (item, edit, key) in enumerate(jobs, 1):
                try:
                    if cached is not None and cached[0] == key:
                        source, parts = cached[1], cached[2]        # 剛剛預覽過的直接用,不用再算一次
                    else:
                        source = upscale.load_edited(item.path, edit)
                        parts = upscale.upscale_parts(source, model, scale)
                    result = upscale.blend(upscale.combine(parts, texture), source, strength)
                    upscale.save(result, item.path, output_dir(), fmt, scale)
                    item.status = "done"
                    done += 1
                except Exception as exc:
                    item.status, item.message = "error", ops.describe_error(exc)
                    failed += 1
                self.notice = (f"處理中 {number} / {len(jobs)}", theme.TEXT_DIM)
            text = f"已處理 {done} 張存到 output\\photo\\" + (f"，{failed} 張失敗" if failed else "")
            self.notice = (text, theme.WARN if failed else self.tool.accent)

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    # ------------------------------------------------------------ 改檔名

    def rename_preview(self):
        """(新名字, 問題);設定或清單沒變時沿用上次算好的。"""
        paths_now = [item.path for item in self.items]
        mode = self.rename_mode.value
        try:
            start = max(0, int(self.start_number.text.strip() or "1"))
        except ValueError:
            start = None
        key = (tuple(paths_now), self.pattern.text, start, self.digits.value, mode)
        if self._preview[0] == key:
            return self._preview[1], self._preview[2]
        if start is None:
            names, issues = [item.path.name for item in self.items], ["起始編號要是數字"] * len(self.items)
        else:
            if "{日期}" in self.pattern.text:
                for path in paths_now:
                    if path not in self._dates:
                        self._dates[path] = rename.taken_date(path)
            names = rename.new_names(paths_now, self.pattern.text, start, int(self.digits.value), self._dates)
            issues = rename.problems(paths_now, names, mode == "rename", None if mode == "rename" else output_dir())
        self._preview = (key, names, issues)
        return names, issues

    def do_rename(self):
        names, issues = self.rename_preview()
        if not self.items or any(issues):
            return
        paths_now = [item.path for item in self.items]
        try:
            if self.rename_mode.value == "copy":
                folder = output_dir() / time.strftime("改檔名 %Y-%m-%d %H%M%S")
                rename.copy_all(paths_now, names, folder)
                self.notice = (f"已輸出 {len(names)} 個檔案到 output\\photo\\{folder.name}\\", self.tool.accent)
                return
            pairs = rename.rename_all(paths_now, names)
        except OSError as error:
            self.notice = (f"改名失敗，已經改的都改回來了：{error}", theme.DANGER)
            return
        self._moved(pairs)
        self.last_rename = pairs
        self.notice = (f"已改好 {len(pairs)} 個檔案的名字；改錯可以按「復原改名」", self.tool.accent)

    def undo_rename(self):
        pairs, self.last_rename = self.last_rename, None
        if not pairs:
            return
        try:
            rename.undo(pairs)
        except OSError as error:
            self.notice = (f"復原失敗：{error}", theme.DANGER)
            return
        self._moved([(new, old) for old, new in pairs])
        self.notice = ("已把名字改回來", self.tool.accent)

    def _moved(self, pairs):
        moved = dict(pairs)
        for item in self.items:
            if item.path in moved:
                item.path = moved[item.path]
        self._dates = {moved.get(path, path): date for path, date in self._dates.items()}

    # ------------------------------------------------------------ 儲存

    def save(self, items):
        if self.running:
            return
        items = [item for item in items if isinstance(item.info, dict)]
        if not items:
            return
        if self.save_format.value == "anim":
            self._save_animation(items)
            return
        settings = ops.Settings(fmt=self.save_format.value)
        edits = {item: item.edit.copy() for item in items}
        for item in items:
            item.status = "running"
        self.notice = (f"儲存中 0 / {len(items)}", theme.TEXT_DIM)

        def work():
            done, failed = 0, 0
            for number, item in enumerate(items, 1):
                try:
                    ops.convert(item.path, output_dir(), settings, edits[item])
                    item.status = "done"
                    done += 1
                except Exception as exc:
                    item.status, item.message = "error", ops.describe_error(exc)
                    failed += 1
                self.notice = (f"儲存中 {number} / {len(items)}", theme.TEXT_DIM)
            text = f"已儲存 {done} 張到 output\\photo\\" + (f"，{failed} 張失敗" if failed else "")
            self.notice = (text, theme.WARN if failed else self.tool.accent)

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _save_animation(self, items):
        """依清單順序把每張圖(套用各自的編輯)當成一格,合成一個 GIF 動畫。"""
        settings = ops.Settings(fmt="gif", gif_combine=True, gif_delay=int(self.gif_delay.value))
        edits = [item.edit.copy() for item in items]
        out = free_path(output_dir(), f"{items[0].path.stem}_動畫", ".gif")
        for item in items:
            item.status = "running"
        self.notice = (f"合成動畫中（{len(items)} 張）", theme.TEXT_DIM)

        def work():
            try:
                failed, frames = ops.images_to_gif([item.path for item in items], out, settings, edits=edits)
            except Exception as exc:
                for item in items:
                    item.status, item.message = "error", ops.describe_error(exc)
                self.notice = (f"合成失敗：{ops.describe_error(exc)}", theme.DANGER)
                return
            for index, item in enumerate(items):
                item.status = "error" if index in failed else "done"
                item.message = failed.get(index, "")
            text = f"已合成 output\\photo\\{out.name}（{frames} 格）" + (f"，{len(failed)} 張讀不到" if failed else "")
            self.notice = (text, theme.WARN if failed else self.tool.accent)

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    # ------------------------------------------------------------ 每一幀

    def deactivate(self):
        self.stroke = None
        threading.Thread(target=cutout.release, daemon=True).start()     # 讓出顯示卡記憶體
        self.list_view.reset()
        self.rename_view.reset()
        self.order_drag.cancel()
        for slider in self.color_sliders.values():
            slider.dragging = False
        for field in (self.pattern, self.start_number):
            field.blur()

    def update(self):
        mouse = pygame.mouse.get_pos()
        delta = self.order_drag.update(self.list_area)
        if delta:
            self.list_view.set_scroll(self.list_view.scroll + delta)
        self.list_view.update(mouse)
        self.rename_view.update(mouse)
        if self._side_view is not None:
            self._side_view.update(mouse)
        if self.current is not None:
            self.editor.update()
        if self.items:
            self._need_masks()

    def _typing(self):
        editor = self.editor
        return (self.pattern.focused or self.start_number.focused or editor.angle_input.focused
                or any(field.focused for field in editor.fields.values()))

    # ------------------------------------------------------------ 繪製

    def draw(self, rect, mouse_pos):
        margin = 20
        top = rect.y + margin
        body = pygame.Rect(rect.x + margin, top, rect.width - margin * 2,
                           rect.height - margin * 3 - FOOTER_H)
        list_rect = pygame.Rect(body.x, body.y, LIST_W, body.height)
        self.draw_list(list_rect, mouse_pos)
        main = pygame.Rect(list_rect.right + margin, body.y, body.right - list_rect.right - margin, body.height)
        self._draw_header(pygame.Rect(main.x, main.y, main.width, HEADER_H - 12), mouse_pos)
        work = pygame.Rect(main.x, main.y + HEADER_H, main.width, main.height - HEADER_H)
        mode = self.mode.value
        if mode == "rename":
            self._draw_rename(work, mouse_pos)
        elif self.current is None:
            rounded_panel(self.screen, work, theme.PANEL, radius=12, alpha=200, border=theme.PANEL_EDGE)
            self._draw_empty(work)
        elif mode == "adjust":
            self.editor.draw_in(work, mouse_pos)
        elif mode == "color":
            self._draw_preview(work, mouse_pos, self._draw_color_side)
        elif mode == "effect":
            self._draw_preview(work, mouse_pos, self._draw_effect_side)
        elif mode == "hd":
            self._draw_hd(work, mouse_pos)
        elif mode == "cutout":
            self._draw_cutout(work, mouse_pos)
        else:
            self._draw_scan(work, mouse_pos)
        self.draw_footer(pygame.Rect(rect.x + margin, rect.bottom - FOOTER_H - margin, rect.width - margin * 2,
                                     FOOTER_H), mouse_pos)
        if self.mode.value != "rename" and self.save_format.value == "anim":
            self.gif_delay.draw_menu(self.screen, mouse_pos)

    def _draw_header(self, rect, mouse_pos):
        screen = self.screen
        tabs_w = min(520, rect.width - 180)
        self.mode.draw(screen, pygame.Rect(rect.x, rect.y, tabs_w, rect.height), mouse_pos)
        if self.mode.value != "rename":
            record = self.history.get(self.current)
            self.btn_redo.enabled = bool(record and record["redo"])
            self.btn_undo.enabled = bool(record and record["undo"])
            self.btn_redo.draw(screen, pygame.Rect(rect.right - 76, rect.y, 76, rect.height), mouse_pos)
            self.btn_undo.draw(screen, pygame.Rect(rect.right - 160, rect.y, 76, rect.height), mouse_pos)
            if self.current is not None and rect.width - tabs_w - 190 > 60:
                name = widgets.clip_text(self.current.path.name, 13, rect.width - tabs_w - 190)
                draw_text(screen, name, (rect.x + tabs_w + 16, rect.centery - 9), 13, theme.TEXT_DIM)

    def _draw_empty(self, area):
        screen = self.screen
        draw_text(screen, "把圖片拖曳到這個視窗", (area.centerx, area.centery - 20), 16, theme.TEXT_DIM, center=True)
        draw_text(screen, "可一次拖多張或整個資料夾；編輯後另存新檔，不會覆蓋原圖",
                  (area.centerx, area.centery + 8), 13, theme.TEXT_FAINT, center=True)

    def draw_list(self, rect, mouse_pos):
        screen = self.screen
        accent = self.tool.accent
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        draw_text(screen, f"圖片 ({len(self.items)})", (rect.x + 14, rect.y + 13), 15, theme.TEXT, bold=True)
        if self.items and not self.running:
            self.btn_clear.draw(screen, pygame.Rect(rect.right - 62, rect.y + 10, 50, 24), mouse_pos)
        pygame.draw.line(screen, theme.PANEL_EDGE, (rect.x + 12, rect.y + 44), (rect.right - 12, rect.y + 44))
        area = pygame.Rect(rect.x, rect.y + 45, rect.width, rect.height - 45)
        self.list_area = area
        self.row_buttons = []
        if not self.items:
            self.list_view.clear()
            draw_text(screen, "拖曳圖片到這裡", area.center, 13, theme.TEXT_FAINT, center=True)
            return
        view = self.list_view
        items = list(self.items)
        view.layout(area, len(items) * ROW_H + 12)
        slots = []
        screen.set_clip(area)
        for index, item in enumerate(items):
            y = area.y + 6 + index * ROW_H - view.scroll
            if y + ROW_H < area.y or y > area.bottom:
                continue
            row = pygame.Rect(area.x + 8, y, area.width - 8 - BAR_SPACE, ROW_H - 6)
            chosen = item is self.current
            hover = row.collidepoint(mouse_pos) and area.collidepoint(mouse_pos)
            rounded_panel(screen, row, theme.PANEL_LIGHT if hover or chosen else theme.BG_DEEP, radius=8, alpha=210,
                          border=accent if chosen else None)
            box = pygame.Rect(row.x + 6, row.centery - THUMB // 2, THUMB, THUMB)
            self._draw_thumb(item, box)
            text_x = box.right + 10
            text_w = row.right - text_x - 34
            draw_text(screen, widgets.clip_text(item.path.name, 13, text_w, bold=True), (text_x, row.y + 9), 13,
                      theme.TEXT, bold=True)
            if isinstance(item.info, str):
                detail, color = "無法讀取", theme.DANGER
            elif item.status == "error":
                detail, color = f"儲存失敗：{item.message}", theme.DANGER
            elif item.info is None:
                detail, color = "讀取中...", theme.TEXT_FAINT
            else:
                width, height = item.info["size"]
                detail = f"{width}×{height}" + (" · 已編輯" if item.edit.active else "")
                detail += " · 已儲存" if item.status == "done" else ""
                color = accent if item.edit.active else theme.TEXT_FAINT
            draw_text(screen, widgets.clip_text(detail, 12, text_w), (text_x, row.y + 31), 12, color)
            if self.order_drag.dragging(index):
                shade = pygame.Surface(row.size, pygame.SRCALPHA)
                shade.fill((20, 24, 30, 150))
                screen.blit(shade, row.topleft)
            slots.append((index, row))
            if hover and not self.running:
                remove = pygame.Rect(row.right - 28, row.y + 6, 22, 22)
                red = remove.collidepoint(mouse_pos)
                rounded_panel(screen, remove, theme.DANGER if red else theme.PANEL, radius=5)
                cross = theme.BG_DEEP if red else theme.TEXT_DIM
                ax, ay = remove.center
                pygame.draw.line(screen, cross, (ax - 4, ay - 4), (ax + 4, ay + 4), 2)
                pygame.draw.line(screen, cross, (ax + 4, ay - 4), (ax - 4, ay + 4), 2)
                self.row_buttons.append(("remove", item, remove))
            self.row_buttons.append(("row", item, row))
        screen.set_clip(None)
        view.draw(screen, mouse_pos)
        self.order_drag.set_slots(slots, len(items))
        self.order_drag.draw(screen, area)

    def _draw_thumb(self, item, box):
        screen = self.screen
        rounded_panel(screen, box, theme.PANEL, radius=6)
        if item.thumb is None and isinstance(item.info, dict) and item.info["thumb"] is not None:
            size, data = item.info["thumb"]
            image = ops.apply_edit(Image.frombytes("RGBA", size, data), item.edit).convert("RGBA")
            image.thumbnail((THUMB, THUMB))
            item.thumb = pygame.image.frombytes(image.tobytes(), image.size, "RGBA")
        if item.thumb is not None:
            screen.blit(item.thumb, item.thumb.get_rect(center=box.center))
        else:
            draw_text(screen, "?" if isinstance(item.info, str) else "...", box.center, 12, theme.TEXT_FAINT,
                      center=True)

    def _split_side(self, work):
        """右邊設定區的底板(有背景圖時才不會透出來)與裡面可以放東西的範圍;左邊是預覽。"""
        panel = pygame.Rect(work.right - SIDE_W - SIDE_PAD * 2, work.y, SIDE_W + SIDE_PAD * 2, work.height)
        rounded_panel(self.screen, panel, theme.PANEL, radius=12, alpha=235, border=theme.PANEL_EDGE)
        self.canvas = pygame.Rect(work.x, work.y, panel.x - 16 - work.x, work.height)
        return panel.inflate(-SIDE_PAD * 2, -SIDE_PAD * 2)

    def _scrolled_side(self, side, draw_side, mouse_pos):
        """畫右邊的設定;放不下時可以捲動,畫在範圍外的被切掉,也不能被點到(不會點到底下的按鈕)。"""
        key = self.mode.value + (":" + self.color_tab.value if self.mode.value == "color" else "")
        view = self.side_views.get(key)
        if view is None:
            view = self.side_views[key] = ScrollView(accent=self.tool.accent, indicator=True)
        screen = self.screen
        previous = screen.get_clip()
        screen.set_clip(side)
        shifted = pygame.Rect(side.x, side.y - view.scroll, side.width, side.height)
        bottom = draw_side(shifted, mouse_pos) or shifted.bottom
        screen.set_clip(previous)
        view.layout(side.inflate(SIDE_PAD * 2 - 4, 0), bottom - shifted.y)
        view.draw(screen, mouse_pos)
        self._side_view, self.side_clip = view, side
        self._clip_side(side)
        return bottom

    def _clip_side(self, clip):
        def cut(rect):
            return rect.clip(clip) if rect.colliderect(clip) else HIDDEN.copy()

        for button in (self.btn_color_reset, self.btn_color_all, self.btn_effect_all, self.btn_compare, self.btn_detect,
                       self.btn_whole, self.btn_detect_all, self.btn_scan_all, self.btn_hd_preview, self.btn_cut,
                       self.btn_cut_remove, self.btn_cut_strokes, self.btn_cut_all):
            button.rect = cut(button.rect)
        for control in (self.color_tab, self.scan_page, self.hd_scale, self.hd_view, self.brush, self.cut_background,
                        self.cut_methods):
            control.rects = [cut(rect) for rect in control.rects]
        for slider in (*self.color_sliders.values(), self.effect_strength, self.hd_strength, self.hd_texture,
                       self.brush_size, self.cut_shrink, self.cut_feather, self.cut_tolerance):
            slider.rect = cut(slider.rect)      # 左右不會被切到,拖曳時換算的位置不變
        self.cut_trim.rect = cut(self.cut_trim.rect)
        self.swatches = [(cut(rect), color) for rect, color in self.swatches if rect.colliderect(clip)]
        self.gallery = [(cut(rect), changes, name) for rect, changes, name in self.gallery if rect.colliderect(clip)]
        self.hd_rows = [(cut(row), key) for row, key in self.hd_rows if row.colliderect(clip)]

    def _draw_preview(self, work, mouse_pos, draw_side, bar=0, view=None):
        """左邊是套用編輯後的大預覽;bar 是底部留給按鈕的高度(預覽不會被蓋住);view 是要顯示的編輯(預設是目前的)。"""
        screen = self.screen
        side = self._split_side(work)
        rounded_panel(screen, self.canvas, theme.BG_DEEP, radius=10, alpha=220)
        self.canvas.height -= bar
        self.gallery = []
        self._scrolled_side(side, draw_side, mouse_pos)
        editor = self.editor
        if editor.error:
            draw_text(screen, f"無法讀取：{editor.error}", self.canvas.center, 14, theme.DANGER, center=True)
            return
        if editor.small is None:
            draw_text(screen, "讀取中...", self.canvas.center, 14, theme.TEXT_DIM, center=True)
            return
        hover = next(((changes, name) for rect, changes, name in self.gallery if rect.collidepoint(mouse_pos)), None)
        self.hover_preset = hover
        if self.comparing:
            edit, tag = self._geometry(), "原圖" if self.mode.value == "cutout" else "原本的色彩"
        elif hover is not None:
            edit, tag = self._with(self.current.edit, hover[0]), f"預覽：{hover[1]}"
        else:
            edit, tag = view or self.current.edit, ""
        surface = self._color_surface(edit)
        rect = surface.get_rect(center=self.canvas.center)
        self._preview_rect = rect
        _blit_checker(screen, rect, self.canvas.topleft)
        screen.blit(surface, rect)
        if tag:
            width = theme.font(12).size(tag)[0] + 20
            label = pygame.Rect(self.canvas.x + 12, self.canvas.y + 12, width, 24)
            rounded_panel(screen, label, theme.PANEL, radius=6, alpha=235)
            draw_text(screen, tag, label.center, 12, theme.TEXT, center=True)

    def _source_key(self):
        """預覽、縮圖快取用的「哪一張圖」:用清單項目和路徑,不用圖片物件的記憶體編號(釋放後會被新的圖重複用到)。"""
        return id(self.current), str(self.current.path), self.editor.small.size

    def _geometry(self):
        """只有形狀的編輯(旋轉、校正、裁切),不含色彩、效果與去背。"""
        edit = self.current.edit.copy()
        edit.adjust = edit.recolor = edit.effect = edit.scan = edit.cutout = None
        return edit

    def _color_surface(self, edit):
        """大預覽:先把底圖縮到畫面大小再套用編輯,拖曳滑桿時才跟得上。"""
        small = self.editor.small
        area = self.canvas.inflate(-32, -32)
        base_key = (self._source_key(), area.size)
        if self._fit_base is None or self._fit_base[0] != base_key:
            base = small.copy()
            scale = min(area.width / base.width, area.height / base.height, 1.0) * 1.4
            base.thumbnail((max(1, int(base.width * scale)), max(1, int(base.height * scale))))
            self._fit_base = (base_key, base)
        base = self._fit_base[1]
        key = (base_key, repr(edit), cutout.generation())
        if self._color_view is None or self._color_view[0] != key:
            image = ops.apply_edit(base, edit).convert("RGBA")
            ratio = min(area.width / image.width, area.height / image.height)
            size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
            image = image.resize(size, Image.Resampling.LANCZOS)
            self._color_view = (key, pygame.image.frombytes(image.tobytes(), image.size, "RGBA"))
        return self._color_view[1]

    def _thumb(self, size, changes):
        """縮圖:目前這張圖(含旋轉、裁切和其他已選的色彩)套用 changes 的樣子。"""
        edit = self._with(self.current.edit, changes)
        if edit.cutout is not None:
            # 有去背:遮罩在原圖上,要從縮小的原圖整個套用一次(背景色不受色彩影響,和存出來的一樣)
            key = (self._source_key(), repr(edit), size, cutout.generation())
            surface = self._thumbs.get(key)
            if surface is None:
                source_key = (self._source_key(), "source", size)
                source = self._thumbs.get(source_key)
                if source is None:
                    source = self._thumbs[source_key] = _fit(self.editor.small, (size[0] * 2, size[1] * 2))
                image = _fit(ops.apply_edit(source, edit).convert("RGBA"), size)
                surface = pygame.image.frombytes(image.tobytes(), image.size, "RGBA")
                if len(self._thumbs) > 400:
                    self._thumbs = {source_key: source}
                self._thumbs[key] = surface
            return surface
        geometry = self._geometry()
        base_key = (self._source_key(), repr(geometry), size)
        key = (base_key, repr(edit))
        surface = self._thumbs.get(key)
        if surface is None:
            base = self._thumbs.get(base_key)
            if base is None:
                image = ops.apply_edit(self.editor.small, geometry).convert("RGBA")
                image = _fit(image, size)
                base = self._thumbs[base_key] = image
            colored = ops.apply_edit(base, ops.Edit(adjust=edit.adjust, recolor=edit.recolor, effect=edit.effect,
                                                    scan=edit.scan))
            colored = colored.convert("RGBA")
            surface = pygame.image.frombytes(colored.tobytes(), colored.size, "RGBA")
            if len(self._thumbs) > 400:
                self._thumbs = {base_key: base}
            self._thumbs[key] = surface
        return surface

    def _draw_gallery(self, x, y, width, columns, cells, cell_h, mouse_pos, labels=False):
        """一格一格的縮圖;cells 是 [(要改的設定, 名稱, 是不是目前的樣子)];回傳下一列的 y。"""
        screen = self.screen
        gap = 5
        cell_w = (width - gap * (columns - 1)) // columns
        label_h = 18 if labels else 0
        for index, (changes, name, chosen) in enumerate(cells):
            row, column = divmod(index, columns)
            rect = pygame.Rect(x + column * (cell_w + gap), y + row * (cell_h + label_h + gap), cell_w, cell_h)
            hover = rect.collidepoint(mouse_pos)
            pygame.draw.rect(screen, theme.BG_DEEP, rect, border_radius=5)
            if self.editor.small is None:
                continue                # 換圖片後新的圖還在讀:先畫空格,讀好才能點
            picture = self._thumb((cell_w - 4, cell_h - 4), changes)
            screen.blit(picture, picture.get_rect(center=rect.center))
            if chosen or hover:
                pygame.draw.rect(screen, self.tool.accent if chosen else theme.TEXT, rect.inflate(2, 2), 2,
                                 border_radius=6)
            if labels:
                draw_text(screen, widgets.clip_text(name, 11, cell_w), (rect.centerx, rect.bottom + 9), 11,
                          self.tool.accent if chosen else theme.TEXT_DIM, center=True)
            self.gallery.append((rect, changes, name))
        rows = (len(cells) + columns - 1) // columns
        return y + rows * (cell_h + label_h + gap)

    def _draw_color_side(self, side, mouse_pos):
        screen = self.screen
        x, y, inner = side.x, side.y, side.width
        edit = self.current.edit
        values = dict(zip(ops.ADJUST_KEYS, edit.adjust or (0,) * len(ops.ADJUST_KEYS)))
        self.color_tab.draw(screen, pygame.Rect(x, y, inner, 30), mouse_pos)
        y += 42
        tab = self.color_tab.value
        if tab == "correct":
            draw_text(screen, "銳利／柔化", (x, y), 13, theme.TEXT, bold=True)
            y += 22
            cells = [({"sharpness": v}, name, values["sharpness"] == v) for v, name in SHARP_STEPS]
            y = self._draw_gallery(x, y, inner, 5, cells, 40, mouse_pos) + 10
            draw_text(screen, "亮度／對比", (x, y), 13, theme.TEXT, bold=True)
            draw_text(screen, "往右越亮，往下對比越強", (x + inner, y + 9), 11, theme.TEXT_FAINT, right=True)
            y += 22
            cells = [({"brightness": b, "contrast": c}, f"亮度 {b:+d}%　對比 {c:+d}%" if b or c else "不變",
                      values["brightness"] == b and values["contrast"] == c)
                     for c in LIGHT_STEPS for b in LIGHT_STEPS]
            y = self._draw_gallery(x, y, inner, 5, cells, 40, mouse_pos)
        elif tab == "color":
            draw_text(screen, "飽和度", (x, y), 13, theme.TEXT, bold=True)
            y += 20
            cells = [({"saturation": v}, name, values["saturation"] == v) for v, name in SATURATION_STEPS]
            y = self._draw_gallery(x, y, inner, 7, cells, 30, mouse_pos) + 8
            draw_text(screen, "色調", (x, y), 13, theme.TEXT, bold=True)
            y += 20
            cells = [({"warmth": v}, name, values["warmth"] == v) for v, name in WARMTH_STEPS]
            y = self._draw_gallery(x, y, inner, 7, cells, 30, mouse_pos) + 8
            draw_text(screen, "重新著色", (x, y), 13, theme.TEXT, bold=True)
            y += 20
            cells = [({"recolor": key}, name, edit.recolor == key) for key, name in looks.RECOLORS]
            y = self._draw_gallery(x, y, inner, 7, cells, 30, mouse_pos)
        else:
            for key, label in COLOR_ROWS:
                slider = self.color_sliders[key]
                draw_text(screen, label, (x, y), 13, theme.TEXT)
                value = int(slider.value)
                draw_text(screen, f"{value:+d}" if value else "0", (x + inner, y + 9), 13,
                          self.tool.accent if value else theme.TEXT_DIM, right=True)
                if key in COLOR_NOTES:
                    draw_text(screen, COLOR_NOTES[key], (x + 56, y + 1), 11, theme.TEXT_FAINT)
                slider.draw(screen, pygame.Rect(x + 8, y + 26, inner - 16, 14), mouse_pos)
                y += 54
        return self._draw_side_footer(side, mouse_pos, self.btn_color_all, bool(edit.adjust or edit.recolor), y)

    def _draw_side_footer(self, side, mouse_pos, apply_all, changed, top):
        """設定區最下面:滑鼠停在縮圖上的名稱、重設、套用到全部、按住看原圖。
        放得下時貼齊底部,放不下時接在內容後面(可以捲動);回傳底部的 y。"""
        screen = self.screen
        x, inner = side.x, side.width
        half = (inner - 12) // 2
        y = max(top + 8, side.bottom - 90)
        name = self.hover_preset[1] if self.hover_preset else ""
        if name:
            draw_text(screen, name, (x + inner // 2, y + 8), 12, theme.TEXT, center=True)
        y += 16
        self.btn_color_reset.label = "移除效果" if self.mode.value == "effect" else "重設色彩"
        self.btn_color_reset.enabled = changed
        self.btn_color_reset.draw(screen, pygame.Rect(x, y, half, 32), mouse_pos)
        apply_all.enabled = len(self.items) > 1
        apply_all.draw(screen, pygame.Rect(x + half + 12, y, half, 32), mouse_pos)
        y += 42
        self.btn_compare.draw(screen, pygame.Rect(x, y, inner, 32), mouse_pos)
        return y + 32

    def _draw_hd(self, work, mouse_pos):
        """高清:左邊是比較畫面(分隔線左邊一般放大、右邊 AI 放大),右邊是模型與倍數。"""
        screen = self.screen
        side = self._split_side(work)
        self.gallery = []
        canvas = self.canvas
        rounded_panel(screen, canvas, theme.BG_DEEP, radius=10, alpha=220)
        result = self.hd_result if self.hd_result and self.hd_result[0] == self._hd_key() else None
        if result is not None:
            self._draw_compare(result, mouse_pos)
        elif self.editor.small is not None:
            surface = self._color_surface(self.current.edit)
            rect = surface.get_rect(center=canvas.center)
            _blit_checker(screen, rect, canvas.topleft)
            screen.blit(surface, rect)
            shade = pygame.Surface(canvas.size, pygame.SRCALPHA)
            shade.fill((8, 10, 14, 120))
            screen.blit(shade, canvas.topleft)
            job = self.hd_job
            if job is not None and job["key"] == self._hd_key():
                bar = pygame.Rect(0, 0, min(360, canvas.width - 80), 10)
                bar.center = (canvas.centerx, canvas.centery + 14)
                pygame.draw.rect(screen, theme.PANEL_LIGHT, bar, border_radius=5)
                pygame.draw.rect(screen, self.tool.accent, (bar.x, bar.y, int(bar.width * job["progress"]), bar.height),
                                 border_radius=5)
                draw_text(screen, f"{job['stage']}… {int(job['progress'] * 100)}%", (canvas.centerx, bar.y - 18), 14,
                          theme.TEXT, center=True)
            else:
                draw_text(screen, "按「預覽這張」看變清楚的效果", canvas.center, 15, theme.TEXT, center=True)
        else:
            draw_text(screen, "讀取中...", canvas.center, 14, theme.TEXT_DIM, center=True)
        self._scrolled_side(side, lambda rect, mouse: self._draw_hd_side(rect, mouse, result is not None), mouse_pos)

    def _compare_rect(self, size):
        """比較畫面要顯示放大後圖片的哪一塊(放大後的座標)和畫在畫面上的大小。"""
        area = self.canvas.inflate(-24, -24)
        width, height = size
        if self.hd_view.value == "fit":
            scale = min(area.width / width, area.height / height)
            return pygame.Rect(0, 0, width, height), (max(1, int(width * scale)), max(1, int(height * scale)))
        view_w, view_h = min(width, area.width), min(height, area.height)
        cx, cy = self.hd_center or (width / 2, height / 2)
        cx = min(max(cx, view_w / 2), width - view_w / 2)
        cy = min(max(cy, view_h / 2), height - view_h / 2)
        self.hd_center = (cx, cy)
        return pygame.Rect(int(cx - view_w / 2), int(cy - view_h / 2), view_w, view_h), (view_w, view_h)

    def _hd_big(self, result):
        """預覽結果依目前的「質感」混合;質感沒變時沿用。"""
        parts = result[2]
        key = (id(parts), int(self.hd_texture.value))
        if self._hd_mix is None or self._hd_mix[0] != key:
            self._hd_mix = (key, upscale.combine(parts, int(self.hd_texture.value)))
        return self._hd_mix[1]

    def _draw_compare(self, result, mouse_pos):
        screen = self.screen
        source, big = result[1], self._hd_big(result)
        view, shown = self._compare_rect(big.size)
        strength = int(self.hd_strength.value)
        key = (id(big), tuple(view), shown, strength)
        if self._hd_view is None or self._hd_view[0] != key:
            factor = big.width / source.width
            box = (view.x / factor, view.y / factor, view.right / factor, view.bottom / factor)
            # 左邊用一般的放大方式(和一般看圖軟體放大時一樣),才看得出 AI 補了多少細節
            plain = source.resize(shown, Image.Resampling.BICUBIC, box=box).convert("RGBA")
            # pygame 的範圍是 (x, y, 寬, 高),Pillow 裁切要 (左, 上, 右, 下)
            sharp = big.crop((view.left, view.top, view.right, view.bottom)).convert("RGBA")
            if sharp.size != shown:
                sharp = sharp.resize(shown, Image.Resampling.LANCZOS)
            if strength < 100:      # 強度:和原圖(一樣的放大方式)混合
                sharp = Image.blend(source.resize(shown, Image.Resampling.LANCZOS, box=box).convert("RGBA"), sharp,
                                    strength / 100)
            self._hd_view = (key, pygame.image.frombytes(plain.tobytes(), plain.size, "RGBA"),
                             pygame.image.frombytes(sharp.tobytes(), sharp.size, "RGBA"))
        _, left, right = self._hd_view
        rect = left.get_rect(center=self.canvas.center)
        _blit_checker(screen, rect, self.canvas.topleft)
        cut = int(rect.width * self.hd_split)
        screen.blit(left, rect.topleft, pygame.Rect(0, 0, cut, rect.height))
        screen.blit(right, (rect.x + cut, rect.y), pygame.Rect(cut, 0, rect.width - cut, rect.height))
        line_x = rect.x + cut
        pygame.draw.line(screen, (255, 255, 255), (line_x, rect.top), (line_x, rect.bottom), 2)
        knob = pygame.Rect(0, 0, 22, 34)
        knob.center = (line_x, rect.centery)
        pygame.draw.rect(screen, (255, 255, 255), knob, border_radius=8)
        for dx in (-4, 4):
            pygame.draw.line(screen, (60, 60, 60), (line_x + dx, knob.y + 10), (line_x + dx, knob.bottom - 10), 2)
        self._compare_area = rect
        before = "原圖" if int(self.hd_scale.value) == 1 else "一般放大"
        for text, x, right_align in ((before, rect.x + 10, False),
                                     (f"AI：{upscale.MODEL_NAMES[self.hd_model]}", rect.right - 10, True)):
            width = theme.font(12).size(text)[0] + 16
            tag = pygame.Rect(x - (width if right_align else 0), rect.y + 10, width, 22)
            rounded_panel(screen, tag, theme.PANEL, radius=6, alpha=230)
            draw_text(screen, text, tag.center, 12, theme.TEXT, center=True)

    def _draw_hd_side(self, side, mouse_pos, has_result):
        screen = self.screen
        x, y, inner = side.x, side.y, side.width
        accent = self.tool.accent
        draw_text(screen, "高清", (x, y), 14, theme.TEXT, bold=True)
        draw_text(screen, "用 AI 讓模糊的圖變清楚", (x + inner, y + 9), 11, theme.TEXT_FAINT, right=True)
        y += 28
        self.hd_rows = []
        for key, name, network, note in upscale.MODELS:
            row = pygame.Rect(x, y, inner, 42)
            chosen = key == self.hd_model
            hover = row.collidepoint(mouse_pos)
            rounded_panel(screen, row, tuple(int(c * 0.25) for c in accent) if chosen else
                          (theme.PANEL_LIGHT if hover else theme.BG_DEEP), radius=8, alpha=220,
                          border=accent if chosen else None)
            draw_text(screen, name, (row.x + 10, row.y + 5), 13, accent if chosen else theme.TEXT, bold=True)
            draw_text(screen, widgets.clip_text(note, 11, inner - 20), (row.x + 10, row.y + 24), 11, theme.TEXT_FAINT)
            self.hd_rows.append((row, key))
            y += 46
        y += 4
        draw_text(screen, "尺寸", (x, y + 6), 13, theme.TEXT)
        self.hd_scale.draw(screen, pygame.Rect(x + 50, y, inner - 50, 28), mouse_pos)
        y += 36
        item = self.current
        if isinstance(item.info, dict):
            width, height = ops.edited_size(item.info["size"], item.edit)
            scale = int(self.hd_scale.value)
            big_w, big_h = upscale.output_size((width, height), scale)
            warn = max(big_w, big_h) > upscale.BIG_SIDE
            size_text = f"{width}×{height} px，大小不變" if scale == 1 else f"{width}×{height} → {big_w}×{big_h} px"
            draw_text(screen, size_text, (x, y), 12, theme.WARN if warn else theme.TEXT_DIM)
            y += 18
            if warn:
                draw_text(screen, "放大後很大，處理會比較久、檔案也很大", (x, y), 11, theme.WARN)
                y += 16
        missing = upscale.required(self.hd_model)
        if missing:
            names = "、".join(f"{dep.name}（{dep.size_text}）" for dep in missing)
            draw_text(screen, widgets.clip_text(f"第一次使用要下載 {names}", 11, inner), (x, y), 11, theme.TEXT_FAINT)
            y += 16
        if self.hd_model == "photo":
            texture = int(self.hd_texture.value)
            draw_text(screen, "質感", (x, y + 4), 13, theme.TEXT)
            draw_text(screen, "銳利", (x + 40, y + 6), 11, theme.TEXT_FAINT)
            draw_text(screen, "自然", (x + inner - 44, y + 13), 11, theme.TEXT_FAINT, right=True)
            draw_text(screen, str(texture), (x + inner, y + 13), 13, accent, right=True)
            self.hd_texture.draw(screen, pygame.Rect(x + 8, y + 30, inner - 16, 14), mouse_pos)
            y += 50
        if self.hd_model != "plain":
            strength = int(self.hd_strength.value)
            draw_text(screen, "強度", (x, y + 4), 13, theme.TEXT)
            draw_text(screen, f"{strength}%", (x + inner, y + 13), 13, accent if strength < 100 else theme.TEXT_DIM,
                      right=True)
            draw_text(screen, "處理過頭就往左拉，越左越接近原圖", (x + 40, y + 6), 11, theme.TEXT_FAINT)
            self.hd_strength.draw(screen, pygame.Rect(x + 8, y + 30, inner - 16, 14), mouse_pos)
            y += 50
        y += 8
        running = self.hd_job is not None
        self.btn_hd_preview.label = "取消" if running else "預覽這張"
        self.btn_hd_preview.enabled = not self.running
        self.btn_hd_preview.draw(screen, pygame.Rect(x, y, inner, 32), mouse_pos)
        y += 44
        if has_result:
            draw_text(screen, "顯示", (x, y + 6), 13, theme.TEXT)
            self.hd_view.draw(screen, pygame.Rect(x + 50, y, inner - 50, 28), mouse_pos)
            y += 36
            hint = "拖曳白色的線比較前後" + ("；拖曳畫面可以移動" if self.hd_view.value == "actual" else "")
            draw_text(screen, hint, (x, y), 11, theme.TEXT_FAINT)
            y += 16
        return y

    def _draw_cutout(self, work, mouse_pos):
        """去背:左邊是結果(修正筆刷時被去掉的地方淡淡顯示原圖),右邊是方式、修正、邊緣與背景。"""
        cut = self.current.edit.cutout
        brushing = cut is not None and self.brush.value != "off"
        view = None
        if brushing:
            view = self.current.edit.copy()
            view.cutout = cut._replace(background="ghost", trim=False)
        self.swatches = []
        self._draw_preview(work, mouse_pos, self._draw_cutout_side, view=view)
        if self.editor.small is None or self.editor.error:
            return
        screen = self.screen
        state = self._cut_state()
        if state:
            width = theme.font(12).size(state)[0] + 20
            label = pygame.Rect(self.canvas.x + 12, self.canvas.bottom - 36, width, 24)
            rounded_panel(screen, label, theme.PANEL, radius=6, alpha=235)
            draw_text(screen, state, label.center, 12, theme.WARN if "失敗" in state or "缺少" in state else theme.TEXT,
                      center=True)
        rect = self._preview_rect
        if not brushing or rect is None:
            return
        color = BRUSH_COLORS[self.brush.value]
        radius = int(self.brush_size.value)
        if self.stroke and len(self.stroke["points"]) > 0:
            layer = pygame.Surface(self.canvas.size, pygame.SRCALPHA)
            points = [(x - self.canvas.x, y - self.canvas.y) for x, y in self.stroke["points"]]
            if len(points) > 1:
                pygame.draw.lines(layer, color + (110,), False, points, radius * 2)
            for point in points:
                pygame.draw.circle(layer, color + (110,), point, radius)
            screen.blit(layer, self.canvas.topleft)
        if rect.collidepoint(mouse_pos):
            pygame.draw.circle(screen, (20, 20, 20), mouse_pos, radius + 1, 1)
            pygame.draw.circle(screen, color, mouse_pos, radius, 2)

    def _draw_cutout_side(self, side, mouse_pos):
        screen = self.screen
        x, y, inner = side.x, side.y, side.width
        accent = self.tool.accent
        item = self.current
        cut = item.edit.cutout
        half = (inner - 12) // 2
        animated = self._animated(item)
        draw_text(screen, "去背", (x, y), 14, theme.TEXT, bold=True)
        draw_text(screen, "找出主體，換掉背景", (x + inner, y + 9), 11, theme.TEXT_FAINT, right=True)
        y += 26
        method = cut.method if cut is not None else self.cut_method
        self.cut_methods.index = [k for k, _, _ in cutout.METHODS].index(method)
        self.cut_methods.draw(screen, pygame.Rect(x, y, inner, 30), mouse_pos)
        y += 36
        note = cutout.METHOD_NOTES[method]
        if animated:
            note = "動畫只能用「單色背景」（AI 去背不支援動畫）"
        missing = cutout.required(method)
        if missing:
            note += "；第一次使用要下載 " + "、".join(f"{dep.name}（{dep.size_text}）" for dep in missing)
        for line in widgets.wrap_text(note, 11, inner, max_lines=3):
            draw_text(screen, line, (x, y), 11, theme.WARN if animated else theme.TEXT_FAINT)
            y += 15
        y += 6
        # 同一個位置依狀態放不同按鈕:沒畫出來的移到畫面外,不會被點到(否則按「移除去背」會變成又去背一次)
        self.btn_cut.rect = HIDDEN.copy()
        self.btn_cut_remove.rect, self.btn_cut_strokes.rect = HIDDEN.copy(), HIDDEN.copy()
        self.brush.rects, self.cut_background.rects, self.swatches = [], [], []
        for slider in (self.brush_size, self.cut_shrink, self.cut_feather, self.cut_tolerance):
            slider.rect = HIDDEN.copy()
        self.cut_trim.rect = HIDDEN.copy()
        if cut is None:
            self.btn_cut.enabled = isinstance(item.info, dict) and not (animated and method in cutout.AI_MODELS)
            self.btn_cut.draw(screen, pygame.Rect(x, y, inner, 32), mouse_pos)
            y += 42
        else:
            self.btn_cut_remove.draw(screen, pygame.Rect(x, y, half, 32), mouse_pos)
            self.btn_cut_strokes.enabled = bool(cut.strokes)
            self.btn_cut_strokes.draw(screen, pygame.Rect(x + half + 12, y, half, 32), mouse_pos)
            y += 42
            draw_text(screen, "修正", (x, y + 6), 13, theme.TEXT)
            self.brush.draw(screen, pygame.Rect(x + 56, y, inner - 56, 28), mouse_pos)
            y += 36
            rows = []
            if self.brush.value != "off":
                rows.append(("筆刷", self.brush_size, str(int(self.brush_size.value))))
            rows += [("往內縮", self.cut_shrink, None), ("柔和", self.cut_feather, None)]
            if cut.method == "color":
                rows.append(("容許差異", self.cut_tolerance, None))
            for label, slider, text in rows:
                value = round(slider.value, 1)
                if text is None:
                    text = f"{value:.1f}" if slider.step < 1 else str(int(value))
                    color = accent if value else theme.TEXT_DIM
                else:
                    color = theme.TEXT_DIM
                draw_text(screen, label, (x, y + 3), 13, theme.TEXT)
                draw_text(screen, text, (x + inner, y + 12), 13, color, right=True)
                slider.draw(screen, pygame.Rect(x + 72, y + 9, inner - 110, 14), mouse_pos)
                y += 30
            if self.brush.value == "off":
                draw_text(screen, "滑鼠停在滑桿上按 ← → 可以每次調 0.1", (x, y), 11, theme.TEXT_FAINT)
            else:
                draw_text(screen, "在預覽上畫：綠色補回，紅色擦掉", (x, y), 11, theme.TEXT_FAINT)
            y += 22
            draw_text(screen, "背景", (x, y + 6), 13, theme.TEXT)
            self.cut_background.draw(screen, pygame.Rect(x + 56, y, inner - 56, 28), mouse_pos)
            y += 34
            for line in widgets.wrap_text(CUT_BACKGROUND_NOTES[cut.background], 11, inner, max_lines=2):
                draw_text(screen, line, (x, y), 11, theme.TEXT_FAINT)
                y += 15
            if cut.background == "color":
                y += 4
                size = (inner - 9 * 6) // 10
                for index, color in enumerate(cutout.SWATCHES):
                    rect = pygame.Rect(x + index * (size + 6), y, size, size)
                    pygame.draw.rect(screen, color, rect, border_radius=5)
                    chosen = tuple(cut.color) == color
                    if chosen or rect.collidepoint(mouse_pos):
                        pygame.draw.rect(screen, accent if chosen else theme.TEXT, rect.inflate(4, 4), 2,
                                         border_radius=6)
                    self.swatches.append((rect, color))
                y += size + 6
            y += 6
            draw_text(screen, "裁到主體", (x, y + 3), 13, theme.TEXT)
            draw_text(screen, "裁掉四周多餘的空白", (x + 70, y + 5), 11, theme.TEXT_FAINT)
            self.cut_trim.value = cut.trim
            self.cut_trim.draw(screen, (x + inner - 42, y), mouse_pos)
            y += 30
        y = max(y + 6, side.bottom - 32)
        self.btn_cut_all.enabled = len(self.items) > 1
        self.btn_cut_all.draw(screen, pygame.Rect(x, y, half, 32), mouse_pos)
        self.btn_compare.draw(screen, pygame.Rect(x + half + 12, y, half, 32), mouse_pos)
        return y + 32

    def _draw_scan(self, work, mouse_pos):
        """掃描:左邊平常是結果(濾鏡、色彩等調整都看得到),按下方的「調整四個角」才換成原圖+四個角;
        右邊是找邊、濾鏡與輸出設定。第一次打開某張圖時自動找邊,找不到就直接讓人對準四角。"""
        editor = self.editor
        ready = editor.base is not None and not editor.error and editor.item is self.current
        if ready and self.current not in self.scan_checked and self.current.edit.warp is None:
            if not self.detect(quiet=True):
                editor._start_warp()
        if not (ready and editor.warping):
            self._draw_preview(work, mouse_pos, self._draw_scan_side, SCAN_BAR)
        else:
            side = self._split_side(work)
            rounded_panel(self.screen, self.canvas, theme.BG_DEEP, radius=10, alpha=220)
            self.canvas.height -= SCAN_BAR
            self.gallery = []
            editor.canvas = self.canvas
            editor._draw_warp(self.screen, mouse_pos)
            bottom = self._scrolled_side(side, self._draw_scan_side, mouse_pos)
            editor.draw_loupe(self.screen, side, bottom)     # 放大鏡放在設定區下方,不擋圖片
            self.hover_preset = next(((changes, name) for rect, changes, name in self.gallery
                                      if rect.collidepoint(mouse_pos)), None)
        # 畫面下方的單獨按鈕:對準四角 ↔ 預覽結果
        self.btn_corners.label = "預覽結果" if editor.warping else "調整四個角"
        self.btn_corners.enabled = ready
        button = pygame.Rect(0, 0, 150, 34)
        button.midbottom = (self.canvas.centerx, self.canvas.bottom + SCAN_BAR - 10)
        self.btn_corners.draw(self.screen, button, mouse_pos)

    def _draw_scan_side(self, side, mouse_pos):
        screen = self.screen
        x, y, inner = side.x, side.y, side.width
        edit = self.current.edit
        half = (inner - 12) // 2
        draw_text(screen, "文件掃描", (x, y), 14, theme.TEXT, bold=True)
        draw_text(screen, "拍斜的講義、白板拉正變清楚", (x + inner, y + 9), 11, theme.TEXT_FAINT, right=True)
        y += 28
        self.btn_detect.enabled = self.editor.base is not None
        self.btn_detect.draw(screen, pygame.Rect(x, y, half, 30), mouse_pos)
        self.btn_whole.enabled = edit.warp is not None or self.editor.warping
        self.btn_whole.draw(screen, pygame.Rect(x + half + 12, y, half, 30), mouse_pos)
        y += 44
        draw_text(screen, "濾鏡", (x, y), 13, theme.TEXT, bold=True)
        y += 22
        cells = [({"scan": key}, name, edit.scan == key) for key, name in scan.FILTERS]
        y = self._draw_gallery(x, y, inner, 4, cells, 56, mouse_pos, labels=True) + 2
        shown = self.hover_preset[0].get("scan", edit.scan) if self.hover_preset else edit.scan
        for line in widgets.wrap_text(scan.FILTER_NOTES[shown], 12, inner, max_lines=2):
            draw_text(screen, line, (x, y), 12, theme.TEXT_FAINT)
            y += 18
        y += 10
        self.btn_detect_all.enabled = len(self.items) > 1 and not self.running
        self.btn_detect_all.draw(screen, pygame.Rect(x, y, half, 30), mouse_pos)
        self.btn_scan_all.enabled = len(self.items) > 1
        self.btn_scan_all.draw(screen, pygame.Rect(x + half + 12, y, half, 30), mouse_pos)
        y += 44
        pygame.draw.line(screen, theme.PANEL_EDGE, (x, y), (x + inner, y))
        y += 12
        draw_text(screen, "合成 PDF 的頁面", (x, y + 6), 13, theme.TEXT)
        self.scan_page.draw(screen, pygame.Rect(x + inner - 170, y, 170, 28), mouse_pos)
        y += 34
        draw_text(screen, SCAN_PAGE_NOTES[self.scan_page.value], (x, y), 12, theme.TEXT_FAINT)
        y += 24
        draw_text(screen, "順序照左邊的清單，拖曳可以調整", (x, y), 12, theme.TEXT_FAINT)
        return y + 18

    def _draw_effect_side(self, side, mouse_pos):
        screen = self.screen
        x, y, inner = side.x, side.y, side.width
        edit = self.current.edit
        draw_text(screen, "美術效果", (x, y), 14, theme.TEXT, bold=True)
        y += 26
        strength = int(self.effect_strength.value)
        cells = [({"effect": (key, strength) if key else None}, name,
                  (edit.effect[0] if edit.effect else None) == key) for key, name in looks.EFFECTS]
        y = self._draw_gallery(x, y, inner, 4, cells, 50, mouse_pos, labels=True) + 8
        draw_text(screen, "強度", (x, y), 13, theme.TEXT)
        draw_text(screen, str(strength), (x + inner, y + 9), 13, self.tool.accent if edit.effect else theme.TEXT_DIM,
                  right=True)
        self.effect_strength.draw(screen, pygame.Rect(x + 8, y + 26, inner - 16, 14), mouse_pos)
        return self._draw_side_footer(side, mouse_pos, self.btn_effect_all, bool(edit.effect), y + 44)

    def _draw_rename(self, work, mouse_pos):
        screen = self.screen
        accent = self.tool.accent
        panel = pygame.Rect(work.x, work.y, 330, work.height)
        rounded_panel(screen, panel, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        x, y, inner = panel.x + 16, panel.y + 14, panel.width - 32
        draw_text(screen, "檔名樣式", (x, y), 14, theme.TEXT, bold=True)
        y += 26
        self.pattern.draw(screen, pygame.Rect(x, y, inner, 34), mouse_pos)
        y += 42
        draw_text(screen, "插入", (x, y + 5), 12, theme.TEXT_DIM)
        bx = x + 36
        for token, button in self.token_buttons:
            button.draw(screen, pygame.Rect(bx, y, 58, 26), mouse_pos)
            bx += 64
        y += 34
        for token, meaning in rename.TOKENS:
            draw_text(screen, f"{token}：{meaning}", (x, y), 11, theme.TEXT_FAINT)
            y += 17
        y += 12
        draw_text(screen, "起始編號", (x, y + 8), 13, theme.TEXT_DIM)
        self.start_number.draw(screen, pygame.Rect(x + 64, y, 70, 32), mouse_pos)
        y += 42
        draw_text(screen, "位數", (x, y + 7), 13, theme.TEXT_DIM)
        self.digits.draw(screen, pygame.Rect(x + 64, y, inner - 64, 30), mouse_pos)
        y += 44
        draw_text(screen, "方式", (x, y), 14, theme.TEXT, bold=True)
        y += 26
        self.rename_mode.draw(screen, pygame.Rect(x, y, inner, 32), mouse_pos)
        y += 40
        for line in widgets.wrap_text(RENAME_NOTES[self.rename_mode.value], 12, inner, max_lines=3):
            draw_text(screen, line, (x, y), 12, theme.TEXT_FAINT)
            y += 18
        y += 8
        draw_text(screen, "順序照左邊的清單，拖曳可以調整", (x, y), 12, theme.TEXT_FAINT)

        table = pygame.Rect(panel.right + 16, work.y, work.right - panel.right - 16, work.height)
        rounded_panel(screen, table, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        draw_text(screen, "預覽", (table.x + 16, table.y + 14), 14, theme.TEXT, bold=True)
        names, issues = self.rename_preview()
        bad = sum(bool(issue) for issue in issues)
        if bad:
            draw_text(screen, f"{bad} 個檔名有問題，改好才能開始", (table.right - 16, table.y + 23), 12, theme.DANGER,
                      right=True)
        area = pygame.Rect(table.x, table.y + 44, table.width, table.height - 44)
        pygame.draw.line(screen, theme.PANEL_EDGE, (area.x + 12, area.y), (area.right - 12, area.y))
        if not self.items:
            self.rename_view.clear()
            draw_text(screen, "先把圖片拖進來", area.center, 13, theme.TEXT_FAINT, center=True)
            return
        row_h = 44
        self.rename_view.layout(area, len(names) * row_h + 12)
        half = (area.width - 40 - BAR_SPACE) // 2
        screen.set_clip(area)
        for index, (item, name, issue) in enumerate(zip(list(self.items), names, issues)):
            y = area.y + 8 + index * row_h - self.rename_view.scroll
            if y + row_h < area.y or y > area.bottom:
                continue
            ox = area.x + 16
            draw_text(screen, widgets.clip_text(item.path.name, 13, half - 20), (ox, y + 4), 13, theme.TEXT_DIM)
            draw_text(screen, "→", (ox + half - 8, y + 4), 13, theme.TEXT_FAINT)
            color = theme.DANGER if issue else (accent if name != item.path.name else theme.TEXT)
            draw_text(screen, widgets.clip_text(name, 13, half - 10, bold=True), (ox + half + 14, y + 4), 13, color,
                      bold=True)
            if issue:
                draw_text(screen, issue, (ox + half + 14, y + 23), 11, theme.DANGER)
        screen.set_clip(None)
        self.rename_view.draw(screen, mouse_pos)

    def draw_footer(self, rect, mouse_pos):
        screen = self.screen
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        text, color = self.notice
        mode = self.mode.value
        if not text:
            if not self.items:
                text, color = "拖入圖片開始編輯", theme.TEXT_DIM
            elif mode == "adjust":
                text, color = self.editor.hint()
            elif mode == "color":
                text, color = "點縮圖套用，滑鼠移到縮圖上可以先看效果；「微調」可以用滑桿細調", theme.TEXT_DIM
            elif mode == "effect":
                text, color = "點縮圖套用美術效果，拖曳「強度」調整程度；選「無」取消", theme.TEXT_DIM
            elif mode == "hd":
                text, color = "選好模型，按「預覽這張」看效果；按「全部變清楚並儲存」處理清單裡全部的圖", theme.TEXT_DIM
            elif mode == "cutout":
                if self.current is not None and self.current.edit.cutout is not None and self.brush.value != "off":
                    text = "在預覽上拖曳：綠色補回被去掉的地方，紅色擦掉多的；選「不修正」結束"
                else:
                    text = "選好方式按「自動去背」；背景透明時，原格式的 JPG 會自動存成 PNG"
                color = theme.TEXT_DIM
            elif mode == "scan":
                text, color = ("拖曳四個角對準文件的邊；點選的角會在右邊放大，拖曳放大圖裡的圖片或按方向鍵微調；"
                               "對好後按「預覽結果」或 Enter，Esc 取消"
                               if self.editor.warping else "邊沒對準時按畫面下方的「調整四個角」；依左邊清單的順序輸出"), \
                    theme.TEXT_DIM
            else:
                text, color = f"共 {len(self.items)} 個檔案", theme.TEXT_DIM
        right = rect.right - 18
        if mode == "hd":
            busy = self.running or self.hd_job is not None
            self.btn_hd_save_all.enabled = bool(self.items) and not busy
            self.btn_hd_save_all.draw(screen, pygame.Rect(right - 150, rect.y + 18, 150, 38), mouse_pos)
            self.btn_save_one.enabled = self.current is not None and not busy
            self.btn_save_one.draw(screen, pygame.Rect(right - 260, rect.y + 18, 100, 38), mouse_pos)
            right -= 272
            self.hd_format.draw(screen, pygame.Rect(right - 250, rect.y + 20, 250, 34), mouse_pos)
            right -= 262
        elif mode == "scan":
            self.btn_export.enabled = bool(self.items) and not self.running
            self.btn_export.draw(screen, pygame.Rect(right - 104, rect.y + 18, 104, 38), mouse_pos)
            right -= 116
            self.scan_output.draw(screen, pygame.Rect(right - 270, rect.y + 20, 270, 34), mouse_pos)
            right -= 282
        elif mode == "rename":
            names, issues = self.rename_preview() if self.items else ([], [])
            self.btn_rename.enabled = bool(self.items) and not any(issues)
            self.btn_rename.draw(screen, pygame.Rect(right - 110, rect.y + 18, 110, 38), mouse_pos)
            right -= 122
            if self.last_rename:
                self.btn_undo_rename.draw(screen, pygame.Rect(right - 96, rect.y + 20, 96, 34), mouse_pos)
                right -= 108
        else:
            animation = self.save_format.value == "anim"
            self.btn_save_all.label = "合成動畫" if animation else "全部儲存"
            self.btn_save_all.enabled = bool(self.items) and not self.running
            self.btn_save_all.draw(screen, pygame.Rect(right - 104, rect.y + 18, 104, 38), mouse_pos)
            if animation:
                # 合成動畫是整份清單一起做,「儲存這張」換成每格時間
                self.btn_save_one.rect = pygame.Rect(0, 0, 0, 0)
                self.gif_delay.draw(screen, pygame.Rect(right - 236, rect.y + 20, 120, 34), mouse_pos)
                right -= 248
            else:
                self.gif_delay.close()
                self.btn_save_one.enabled = self.current is not None and not self.running
                self.btn_save_one.draw(screen, pygame.Rect(right - 214, rect.y + 18, 100, 38), mouse_pos)
                right -= 226
            self.save_format.draw(screen, pygame.Rect(right - 380, rect.y + 20, 380, 34), mouse_pos)
            right -= 392
        self.btn_output.draw(screen, pygame.Rect(right - 96, rect.y + 20, 96, 34), mouse_pos)
        right -= 108
        width = right - rect.x - 18
        lines = widgets.wrap_text(text, 13, width, max_lines=2)
        for number, line in enumerate(lines):
            draw_text(screen, line, (rect.x + 18, rect.y + (27 if len(lines) == 1 else 17) + number * 20), 13, color)

    # ------------------------------------------------------------ 事件

    def modal_open(self):
        return False

    def handle_event(self, event, mouse_pos):
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN):
            self._gesture += 1
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.comparing = False
        if event.type == pygame.DROPFILE:
            self.add_files([event.file])
            return
        if self.mode.value != "rename" and self.save_format.value == "anim" and not self.running \
                and self.gif_delay.handle(event, mouse_pos):
            return
        if event.type == pygame.KEYDOWN and event.mod & pygame.KMOD_CTRL and not self._typing() \
                and self.mode.value != "rename":
            if event.key == pygame.K_z:
                self.undo()
                return
            if event.key == pygame.K_y:
                self.redo()
                return
        if event.type == pygame.KEYDOWN and event.key in (pygame.K_LEFT, pygame.K_RIGHT) \
                and self.mode.value == "cutout" and self.current is not None and self._cutout_event(event, mouse_pos):
            return                  # 滑鼠停在去背的滑桿上按 ← →:細調數值(設定區的捲動不會先接走)
        if self.mode.value == "scan" and self.current is not None and self.editor.handle_loupe(event, mouse_pos):
            return                  # 放大鏡蓋在設定區下方,要比設定先處理
        side_view = self._side_view
        # 捲動條在設定區右邊緣外一點;拖曳捲動條、中鍵自動捲動時滑鼠移出範圍也要繼續交給它
        if self.mode.value in ("color", "effect", "scan", "hd", "cutout") and side_view is not None \
                and (self.side_clip.collidepoint(mouse_pos) or side_view.grabbing(mouse_pos)) \
                and side_view.handle_event(event, mouse_pos):
            return
        if not self.running and self.order_drag.handle(event, mouse_pos):
            return
        if self.items and self.list_view.handle_event(event, mouse_pos):
            return
        mode = self.mode.value
        if mode == "rename":
            if self.items and self.rename_view.handle_event(event, mouse_pos):
                return
            if self.pattern.handle(event, mouse_pos) or self.start_number.handle(event, mouse_pos):
                return
        elif self.current is not None and mode == "adjust":
            self.editor.handle_event(event, mouse_pos)
        elif self.current is not None and mode == "color" and self.color_tab.value == "fine":
            for key, slider in self.color_sliders.items():
                if slider.handle(event, mouse_pos):
                    self._apply_color()
                    return
        elif self.current is not None and mode == "effect":
            if self.effect_strength.handle(event, mouse_pos):
                self._apply_strength()
                return
        elif self.current is not None and mode == "cutout" and self._cutout_event(event, mouse_pos):
            return
        elif self.current is not None and mode == "hd" and self.hd_model != "plain" \
                and self.hd_strength.handle(event, mouse_pos):
            return
        elif self.current is not None and mode == "hd" and self.hd_model == "photo" \
                and self.hd_texture.handle(event, mouse_pos):
            return
        elif self.current is not None and mode == "hd" and self._hd_drag(event, mouse_pos):
            return
        elif self.current is not None and mode == "scan" and self.editor.warping:
            editor = self.editor
            near = event.type != pygame.MOUSEBUTTONDOWN or self.canvas.collidepoint(mouse_pos)
            if near and event.type != pygame.MOUSEWHEEL and editor._handle_warp(event, mouse_pos):
                return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        self._click(mouse_pos)

    def _click(self, pos):
        if self.mode.rects and any(rect.collidepoint(pos) for rect in self.mode.rects):
            self._commit_warp()
            self.editor.warping = False
            self.mode.clicked(pos, True)
            self.notice = ("", theme.TEXT_DIM)
            return
        if self.list_area.collidepoint(pos) and not self.running:
            for action, item, rect in self.row_buttons:
                if not rect.collidepoint(pos):
                    continue
                if action == "remove":
                    self.remove(item)
                else:
                    self.select(item)
                    self.order_drag.press(pos, [self.items.index(item)])
                return
        if self.items and not self.running and self.btn_clear.clicked(pos, True):
            self.items, self.history, self.last_rename = [], {}, None
            self.select(None)
            return
        if self.btn_output.clicked(pos, True):
            output_dir().mkdir(parents=True, exist_ok=True)
            os.startfile(output_dir())
            return
        mode = self.mode.value
        if mode == "rename":
            self._click_rename(pos)
            return
        if mode == "scan":
            self._click_scan(pos)
            return
        if mode == "hd":
            self._click_hd(pos)
            return
        if self.btn_undo.clicked(pos, True):
            self.undo()
        elif self.btn_redo.clicked(pos, True):
            self.redo()
        elif self.save_format.clicked(pos, True):
            pass
        elif self.btn_save_one.clicked(pos, True):
            self.save([self.current])
        elif self.btn_save_all.clicked(pos, True):
            self.save(list(self.items))
        elif mode == "cutout" and self.current is not None:
            self._click_cutout(pos)
        elif mode in ("color", "effect") and self.current is not None:
            hit = next((changes for rect, changes, _ in self.gallery if rect.collidepoint(pos)), None)
            if hit is not None:
                self.apply_preset(hit)
            elif self.color_tab.clicked(pos, True):
                pass
            elif self.btn_color_reset.clicked(pos, True):
                if mode == "color":
                    self.apply_preset({**{key: 0 for key in ops.ADJUST_KEYS}, "recolor": None})
                else:
                    self.apply_preset({"effect": None})
            elif mode == "color" and self.btn_color_all.clicked(pos, True):
                self._color_all()
            elif mode == "effect" and self.btn_effect_all.clicked(pos, True):
                self._color_all(("effect",), "效果")
            elif self.btn_compare.clicked(pos, True):
                self.comparing = True

    def _cutout_event(self, event, pos):
        """去背模式的滑桿與修正筆刷;用掉事件時回傳 True。"""
        cut = self.current.edit.cutout
        if cut is None:
            return False
        if self.brush.value != "off" and self.brush_size.handle(event, pos):
            return True
        pairs = ((self.cut_shrink, "shrink"), (self.cut_feather, "feather"), (self.cut_tolerance, "tolerance"))
        if event.type == pygame.MOUSEMOTION:
            self.hover_slider = next((slider for slider, _ in pairs if slider.rect.inflate(0, 16).collidepoint(pos)),
                                     None)
        for slider, field in pairs:
            if field == "tolerance" and cut.method != "color":
                continue
            nudge = (event.type == pygame.KEYDOWN and event.key in (pygame.K_LEFT, pygame.K_RIGHT)
                     and self.hover_slider is slider)
            if nudge:
                # 細調:滑鼠停在滑桿上按 ← →,每次加減一個單位(往內縮、柔和是 0.1);滾輪留給捲動設定區
                step = slider.step if event.key == pygame.K_RIGHT else -slider.step
                slider.value = min(slider.max, max(slider.min, slider.value + step))
            if nudge or slider.handle(event, pos):
                value = round(slider.value, 1) if slider.step < 1 else int(slider.value)
                slider.value = value
                if getattr(cut, field) != value:
                    self._set_cut(**{field: value})
                return True
        if self.brush.value == "off" or self._preview_rect is None:
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.canvas.collidepoint(pos) \
                and self._preview_rect.inflate(int(self.brush_size.value) * 2, int(self.brush_size.value) * 2) \
                .collidepoint(pos):
            self.stroke = {"keep": self.brush.value == "keep", "radius": int(self.brush_size.value), "points": [pos]}
            return True
        if self.stroke is not None:
            if event.type == pygame.MOUSEMOTION:
                last = self.stroke["points"][-1]
                if abs(pos[0] - last[0]) + abs(pos[1] - last[1]) >= max(2, self.stroke["radius"] // 3):
                    self.stroke["points"].append(pos)
                return True
            if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self.stroke["points"].append(pos)
                self._commit_stroke()
                return True
        return False

    def _click_cutout(self, pos):
        cut = self.current.edit.cutout
        if self.cut_methods.clicked(pos, True):
            chosen = self.cut_methods.value
            if self._animated(self.current) and chosen in cutout.AI_MODELS:
                self.notice = ("動畫只能用「單色背景」去背", theme.WARN)
                return
            self.cut_method = chosen
            if cut is not None and chosen != cut.method:
                self.cut_start(chosen)      # 已經去背:換一種方式重新做,修正筆刷和設定保留
        elif cut is None and self.btn_cut.clicked(pos, True):
            self.cut_start()
        elif self.btn_cut_all.clicked(pos, True):
            self.cut_all()
        elif self.btn_compare.clicked(pos, True):
            self.comparing = True
        elif cut is None:
            return
        elif self.btn_cut_remove.clicked(pos, True):
            self.cut_remove()
        elif self.btn_cut_strokes.clicked(pos, True):
            self._set_cut(strokes=())
        elif self.brush.clicked(pos, True):
            self.stroke = None
        elif self.cut_background.clicked(pos, True):
            self._set_cut(background=self.cut_background.value)
        elif self.cut_trim.clicked(pos, True):
            self._set_cut(trim=self.cut_trim.value)
        else:
            color = next((color for rect, color in self.swatches if rect.collidepoint(pos)), None)
            if color is not None:
                self._set_cut(color=color)

    def _hd_drag(self, event, pos):
        """比較畫面:拖曳分隔線,或在放大檢視時拖曳畫面移動;用掉事件時回傳 True。"""
        area = self._compare_area
        result = self.hd_result if self.hd_result and self.hd_result[0] == self._hd_key() else None
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and result and area \
                and self.canvas.collidepoint(pos):
            line_x = area.x + int(area.width * self.hd_split)
            if abs(pos[0] - line_x) <= 16 or self.hd_view.value == "fit":
                self.hd_drag = ("split", None)
                self.hd_split = min(1.0, max(0.0, (pos[0] - area.x) / max(1, area.width)))
            else:
                self.hd_drag = ("pan", (pos, self.hd_center))
            return True
        if self.hd_drag is not None:
            if event.type == pygame.MOUSEMOTION and area:
                kind, start = self.hd_drag
                if kind == "split":
                    self.hd_split = min(1.0, max(0.0, (pos[0] - area.x) / max(1, area.width)))
                else:
                    (sx, sy), (cx, cy) = start
                    self.hd_center = (cx - (pos[0] - sx), cy - (pos[1] - sy))
                return True
            if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self.hd_drag = None
                return True
        return False

    def _click_hd(self, pos):
        if self.btn_undo.clicked(pos, True):
            self.undo()
        elif self.btn_redo.clicked(pos, True):
            self.redo()
        elif self.hd_format.clicked(pos, True):
            pass
        elif self.btn_hd_save_all.clicked(pos, True):
            self.hd_save(list(self.items))
        elif self.btn_save_one.clicked(pos, True):
            self.hd_save([self.current])
        elif self.current is None:
            return
        elif self.btn_hd_preview.clicked(pos, True):
            if self.hd_job is not None:
                self.hd_cancel()
            else:
                self.hd_preview()
        elif self.hd_scale.clicked(pos, True) or self.hd_view.clicked(pos, True):
            self.hd_center = None
        else:
            for row, key in self.hd_rows:
                if row.collidepoint(pos):
                    self.hd_model = key
                    return

    def _click_scan(self, pos):
        if self.btn_undo.clicked(pos, True):
            self.undo()
        elif self.btn_redo.clicked(pos, True):
            self.redo()
        elif self.scan_output.clicked(pos, True) or self.scan_page.clicked(pos, True):
            pass
        elif self.btn_export.clicked(pos, True):
            self.export_scan()
        elif self.current is None:
            return
        elif self.btn_corners.clicked(pos, True):
            if self.editor.warping:
                self._commit_warp()
            else:
                self.editor._start_warp()
        elif self.btn_detect.clicked(pos, True):
            self.detect()
        elif self.btn_whole.clicked(pos, True):
            self.use_whole()
        elif self.btn_detect_all.clicked(pos, True):
            self.detect_all()
        elif self.btn_scan_all.clicked(pos, True):
            self._color_all(("scan",), "濾鏡")
        else:
            hit = next((changes for rect, changes, _ in self.gallery if rect.collidepoint(pos)), None)
            if hit is not None:
                self._commit_warp()
                self.apply_preset(hit)

    def _click_rename(self, pos):
        for token, button in self.token_buttons:
            if button.clicked(pos, True):
                field = self.pattern
                cursor = field.cursor if field.focused else len(field.text)
                field.set_text(field.text[:cursor] + token + field.text[cursor:])
                return
        if self.digits.clicked(pos, True) or self.rename_mode.clicked(pos, True):
            return
        if self.btn_rename.clicked(pos, True):
            self.do_rename()
        elif self.last_rename and self.btn_undo_rename.clicked(pos, True):
            self.undo_rename()
