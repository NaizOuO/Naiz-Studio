"""電路圖:從左邊選元件,在格子上拖曳放置,元件的端點接在一起就連通;輸出圖片,或產生可以貼進筆記的 Python 程式碼。

畫的內容會自動存在本地(circuits\\autosave.json),關掉程式再打開還在。
"""

import os
import time

import pygame

from core import files, imageclip, paths, theme, widgets, winfile
from core.contextmenu import ContextMenu
from core.plugins import Page
from core.scroll import BAR_SPACE, ScrollView
from core.widgets import Button, ChoiceGrid, SegmentedControl, Slider, TextInput, draw_text, rounded_panel

from . import codegen, draw, model, parts

PALETTE_W = 214
SIDE_W = 276
ROW_H = 34
DEFAULT_LEN = 2.0           # 點一下(沒有拖曳)放兩端元件時的長度
HANDLE_PX = 8
TERMINAL_PX = 7             # 選取工具按在元件接點附近多少像素內,直接拉出導線
AUTOSAVE_DELAY = 1.0
RESISTOR_STYLES = [("zigzag", "鋸齒(美式)"), ("box", "方框(歐式)")]
RESOLUTIONS = [(100, "一般"), (200, "高"), (300, "超高")]     # 每單位(兩格)幾個像素
ALIGNS = [("left", "靠左"), ("hcenter", "置中"), ("right", "靠右"), ("hspread", "↔ 等距"),
          ("top", "靠上"), ("vcenter", "置中"), ("bottom", "靠下"), ("vspread", "↕ 等距")]
BACKUP_KEEP = 20            # 自動備份最多留幾份
BACKUP_EVERY = 120          # 距離上次備份多久(秒)才再備份一次
DIRECTIONS = [(False, "順時針"), (True, "逆時針")]
FILE_FILTERS = [("電路圖 (*.json)", "*.json")]
TOOL_HINTS = {
    "select": "拖曳移動(接著的導線會跟著)；拖導線只移那一段；從端點拉出導線；R 旋轉、X 水平翻轉、Y 垂直翻轉",
    "wire": "按住拖曳畫一段導線；或點一下開始、每點一下轉一個彎，點到接點、雙擊或按 Esc 結束",
    "two": "按住從起點拖到終點放置；只點一下會沿上一個元件的方向接下去",
    "multi": "點一下放置；放之前可以按 R 旋轉、X 水平翻轉、Y 垂直翻轉；Esc 回到選取",
    "span": "從 + 端拖到 − 端(電流是從起點指向終點)",
    "box": "按住拖曳畫出框",
    "rect": "按住拖曳畫出方塊，放好後可以打字；四個角可以拉大小",
    "loop": "拖曳出迴路的範圍；選取後可以拉起點、箭頭與四個角，調整形狀與方向",
    "text": "點一下放文字，接著直接打字；支援 LaTeX",
}
DEFAULT_TEXT = "文字"


def output_dir():
    return paths.OUTPUT_DIR / "circuit"


def autosave_path():
    return paths.APP_DIR / "circuits" / "autosave.json"


def defaults_path():
    return paths.APP_DIR / "circuits" / "defaults.json"


def files_dir():
    """電路圖檔案(另存、開啟)放這裡;輸出的圖片在 output\\circuit,兩邊分開不會混在一起。"""
    return paths.APP_DIR / "circuits"


class CircuitPage(Page):
    def __init__(self, app, tool):
        super().__init__(app, tool)
        accent = tool.accent
        self.accent = accent
        self.elements = []
        self.history, self.future = [], []
        self._pending = None            # 操作開始前的樣子;真的有改變才放進復原清單
        self.version = 0
        self.selected = set()
        self.tool_id = "select"
        self.ghost_rot, self.ghost_mirror = 0, False
        self.last_dir = (1.0, 0.0)
        self.scale = 64.0               # 每單位幾個像素(一單位是兩格)
        self.origin = None              # 畫布左上角對應的座標,第一次畫時才決定
        self.canvas = pygame.Rect(0, 0, 0, 0)
        self.drag = None
        self.space_held = False
        self.clipboard = []
        self.paste_count = 0
        self.notice, self.notice_time = "", 0
        self._layer = (None, None)
        self._ghost_layer = (None, None)
        self._grid = (None, None)
        self._icons = {}
        self._changed_at = None
        self.path = None

        self.style = ChoiceGrid([(key, draw.STYLES[key]["name"]) for key in draw.STYLE_ORDER], 2, accent=accent)
        self.resistor = SegmentedControl(RESISTOR_STYLES, accent=accent)
        self.resolution = SegmentedControl(RESOLUTIONS, index=1, accent=accent)
        self.text_scale = Slider(70, 160, 100, step=5, accent=accent)     # 字級(%)
        self.line_scale = Slider(50, 200, 100, step=10, accent=accent)    # 線寬(%)
        self.btn_default = Button("設為預設", filled=False, size=12)
        self.btn_batch = Button("批次匯出", filled=False, size=13)
        self.align_buttons = [(key, Button(label, filled=False, size=12)) for key, label in ALIGNS]
        self._static = None             # 拖曳時不動的那些元件先畫好的圖
        self._backup_at = 0.0
        self.direction = SegmentedControl(DIRECTIONS, accent=accent)
        self.square = Slider(0, 100, 0, step=5, accent=accent)
        self.caption = TextInput(placeholder="圖說(可不填)", accent=accent, size=14)
        self.name_input = TextInput(placeholder="例如 R_1", accent=accent, size=14)
        self.value_input = TextInput(placeholder="例如 10 k\\Omega", accent=accent, size=14)
        self.search = TextInput(placeholder="搜尋", accent=accent, size=13)
        self.inputs = [self.name_input, self.value_input, self.caption, self.search]
        self._editing = None

        self.btn_code = Button("複製 Python 程式碼", accent=accent, size=14)
        self.btn_image = Button("複製圖片", filled=False, size=13)
        self.btn_png = Button("存成 PNG", filled=False, size=13)
        self.btn_svg = Button("存成 SVG", filled=False, size=13)
        self.btn_open = Button("開啟", filled=False, size=13)
        self.btn_save = Button("另存", filled=False, size=13)
        self.btn_new = Button("清空", filled=False, size=13)
        self.btn_output = Button("輸出資料夾", filled=False, size=13)
        self.btn_rotate = Button("旋轉 R", filled=False, size=13)
        self.btn_flip_h = Button("水平翻轉 X", filled=False, size=13)
        self.btn_flip_v = Button("垂直翻轉 Y", filled=False, size=13)
        self.btn_side = Button("標籤換邊 F", filled=False, size=13)
        self.btn_reset_label = Button("標籤回到原位", filled=False, size=13)
        self.btn_delete = Button("刪除", accent=theme.DANGER, filled=False, size=13)
        self.canvas_buttons = []

        self.palette_view = ScrollView(accent=accent)
        self.palette_area = pygame.Rect(0, 0, 0, 0)
        self.palette_rows = []
        self.side_view = ScrollView(accent=accent, indicator=True)
        self.side_area = pygame.Rect(0, 0, 0, 0)
        self.menu = ContextMenu(accent)
        self._last_click = (0, None)
        self._load_autosave()

    # ------------------------------------------------------------ 資料與復原

    def _load_autosave(self):
        try:
            elements, settings = model.loads(autosave_path().read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            self._apply_settings(self._load_defaults())
            return
        self.elements = elements
        self._apply_settings(settings)
        self.path = settings.get("path") and os.path.exists(settings["path"]) and settings["path"] or None

    def _apply_settings(self, settings):
        for slider, key in ((self.text_scale, "text_scale"), (self.line_scale, "line_scale")):
            if isinstance(settings.get(key), (int, float)):
                slider.value = max(slider.min, min(slider.max, int(settings[key])))
        for control, key in ((self.style, "style"), (self.resistor, "resistor"), (self.resolution, "resolution")):
            keys = [k for k, _ in control.options]
            if settings.get(key) in keys:
                control.index = keys.index(settings[key])
        self.caption.set_text(settings.get("caption", ""))

    def settings(self):
        return dict(style=self.style.value, resistor=self.resistor.value, caption=self.caption.text,
                    resolution=self.resolution.value, text_scale=self.text_scale.value,
                    line_scale=self.line_scale.value)

    def apply_look(self):
        """字級、線寬交給 model(畫圖、產生程式碼都照這個)。"""
        model.set_look(self.text_scale.value / 100, self.line_scale.value / 100)

    def _load_defaults(self):
        """「設為預設」存的風格、字級、線寬;新的空白圖用它,全書就能一致。"""
        try:
            import json
            return json.loads(defaults_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_defaults(self):
        import json

        values = {key: value for key, value in self.settings().items() if key != "caption"}
        try:
            defaults_path().parent.mkdir(parents=True, exist_ok=True)
            files.write_bytes(defaults_path(), json.dumps(values, ensure_ascii=False, indent=1).encode("utf-8"))
        except OSError as error:
            self.say(f"存檔失敗：{error}")
            return
        self.say("已設為預設：之後清空重畫時會用這組風格、字級、線寬與解析度")

    def begin(self):
        """操作開始:記下目前的樣子,有改變時才放進復原清單。"""
        if self._pending is None:
            self._pending = model.clone(self.elements)

    def changed(self):
        if self._pending is not None:
            self.history.append(self._pending)
            del self.history[:-200]
            self.future.clear()
            self._pending = None
        self.version += 1
        self._changed_at = time.monotonic()

    def end(self):
        self._pending = None

    def edit(self, action):
        """一次完成的修改(按鈕、快捷鍵)。"""
        self.begin()
        action()
        self.changed()

    def undo(self):
        if self.history:
            self.future.append(model.clone(self.elements))
            self.elements = self.history.pop()
            self._after_restore()

    def redo(self):
        if self.future:
            self.history.append(model.clone(self.elements))
            self.elements = self.future.pop()
            self._after_restore()

    def _after_restore(self):
        self.selected = {i for i in self.selected if i < len(self.elements)}
        self.version += 1
        self._changed_at = time.monotonic()
        self._sync_inputs()

    def update(self):
        if self._changed_at and time.monotonic() - self._changed_at > AUTOSAVE_DELAY:
            self._changed_at = None
            self._autosave()

    def _autosave(self):
        settings = dict(self.settings(), path=str(self.path) if self.path else "")
        try:
            autosave_path().parent.mkdir(parents=True, exist_ok=True)
            data = model.dumps(self.elements, settings).encode("utf-8")
            files.write_bytes(autosave_path(), data)
            self._backup(data)
        except OSError:
            pass

    def _backup(self, data):
        """每隔一段時間另外留一份備份(circuits\\backup),清空或誤刪後還能從「開啟」找回來;只留最近幾份。"""
        if not self.elements or time.monotonic() - self._backup_at < BACKUP_EVERY:
            return
        self._backup_at = time.monotonic()
        folder = autosave_path().parent / "backup"
        folder.mkdir(exist_ok=True)
        files.write_bytes(folder / time.strftime("電路圖 %Y-%m-%d %H%M%S.json"), data)
        for old in sorted(folder.glob("*.json"))[:-BACKUP_KEEP]:
            old.unlink(missing_ok=True)

    def deactivate(self):
        self._finish_chain()
        self.drag = None
        self.space_held = False
        for box in self.inputs:
            box.blur()
        self._editing = None
        self._autosave()

    def say(self, text):
        self.notice, self.notice_time = text, time.monotonic()

    # ------------------------------------------------------------ 座標

    def to_screen(self, point):
        ox, oy = self.origin
        return (self.canvas.x + (point[0] - ox) * self.scale, self.canvas.y + (oy - point[1]) * self.scale)

    def to_world(self, pos):
        ox, oy = self.origin
        return (ox + (pos[0] - self.canvas.x) / self.scale, oy - (pos[1] - self.canvas.y) / self.scale)

    def fit_view(self):
        box = model.all_bounds(self.elements)
        if box is None or not self.canvas.width:
            self.scale = 64.0
            self.origin = (-2.0, self.canvas.height / self.scale / 2 + 1.5)
            return
        pad = 1.0
        width, height = box[2] - box[0] + pad * 2, box[3] - box[1] + pad * 2
        self.scale = max(24.0, min(140.0, self.canvas.width / width, (self.canvas.height - 60) / height))
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        self.origin = (cx - self.canvas.width / 2 / self.scale, cy + self.canvas.height / 2 / self.scale)

    def zoom(self, factor, pos):
        before = self.to_world(pos)
        self.scale = max(20.0, min(240.0, self.scale * factor))
        after = self.to_world(pos)
        self.origin = (self.origin[0] + before[0] - after[0], self.origin[1] + before[1] - after[1])

    # ------------------------------------------------------------ 放置

    @property
    def part_kind(self):
        if self.tool_id in ("select", "wire"):
            return self.tool_id
        return parts.PARTS[self.tool_id]["kind"]

    def set_tool(self, tool_id):
        self._finish_chain()
        self.tool_id = tool_id
        self.ghost_rot, self.ghost_mirror = 0, False
        self.drag = None
        if tool_id != "select":
            self.selected.clear()
            self._sync_inputs()

    def _new_part(self, a=None, b=None, at=None):
        kind = self.tool_id
        shape = parts.PARTS[kind]["kind"]
        fields = dict(label=DEFAULT_TEXT if shape == "text" else model.next_label(self.elements, kind))
        if shape == "text":
            fields.update(at=list(at))
        elif at is not None:
            fields.update(at=list(at), rot=self.ghost_rot, mirror=self.ghost_mirror)
        else:
            fields.update(a=list(a), b=list(b))
        return model.new(kind, **fields)

    def ghost(self, pos):
        """滑鼠停在畫布上、還沒按下時,要放的元件的預覽。"""
        if self.tool_id in ("select", "wire") or not self.canvas.collidepoint(pos) or self.space_held:
            return None
        point = model.snap_point(self.to_world(pos))
        kind = self.part_kind
        if kind in ("multi", "text"):
            return self._new_part(at=point)
        if kind in ("box", "loop", "rect"):
            return None                             # 直接拖出範圍,不先放一個預設大小的框
        end = [point[0] + self.last_dir[0] * DEFAULT_LEN, point[1] + self.last_dir[1] * DEFAULT_LEN]
        return self._new_part(a=point, b=model.snap_point(end))

    def _wire_points(self, a, b):
        if a[0] != b[0] and a[1] != b[1]:
            corner = [b[0], a[1]] if abs(b[0] - a[0]) >= abs(b[1] - a[1]) else [a[0], b[1]]
            return [list(a), corner, list(b)]
        return [list(a), list(b)]

    def _connection_at(self, point, skip=()):
        """point 是不是某個元件或導線端點的接點(放導線時點到就結束)。"""
        for index, el in enumerate(self.elements):
            if index in skip:
                continue
            ends = [el["pts"][0], el["pts"][-1]] if el["kind"] == "wire" and el["pts"] else model.connection_points(el)
            if any(model.same(p, point) for p in ends):
                return True
        return False

    # ------------------------------------------------------------ 選取與編輯

    def _hit(self, world):
        """滑鼠點到的元件(也算點到它的標籤)。"""
        tolerance = max(0.1, 7 / self.scale)
        for index in range(len(self.elements) - 1, -1, -1):
            el = self.elements[index]
            if model.hit(el, world, tolerance=tolerance) or (el["kind"] != "wire" and model.label_at(el, world)):
                return index
        return None

    def _handle_at(self, pos):
        for index in self.selected:
            for name, point in model.handles(self.elements[index]):
                sx, sy = self.to_screen(point)
                if abs(sx - pos[0]) <= HANDLE_PX and abs(sy - pos[1]) <= HANDLE_PX:
                    return index, name
        return None

    def _terminal_at(self, pos):
        """滑鼠在哪個元件接點上(用來直接拉出導線)。"""
        for el in self.elements:
            for point in model.connection_points(el):
                sx, sy = self.to_screen(point)
                if abs(sx - pos[0]) <= TERMINAL_PX and abs(sy - pos[1]) <= TERMINAL_PX:
                    return [point[0], point[1]]
        return None

    def selection(self):
        return [self.elements[i] for i in sorted(self.selected) if i < len(self.elements)]

    def _sync_inputs(self):
        """選取改變時,名稱、數值欄顯示選取的元件。"""
        self._editing = None
        chosen = self.selection()
        for box, key in ((self.name_input, "label"), (self.value_input, "value")):
            box.blur()
            box.set_text(chosen[0].get(key, "") if len(chosen) == 1 and key in chosen[0] else "")
        if len(chosen) == 1 and chosen[0]["kind"] == "loop":
            self.square.value = int(round(chosen[0].get("square", 0.0) * 100))
            self.direction.index = 1 if chosen[0].get("flip") else 0

    def select(self, indices):
        self.selected = set(indices)
        self._sync_inputs()

    def delete_selected(self):
        if not self.selected:
            return
        chosen = self.selected
        self.edit(lambda: setattr(self, "elements", [el for i, el in enumerate(self.elements) if i not in chosen]))
        self.select(())

    def _transform_selected(self, action):
        chosen = self.selection()
        if chosen:
            center = model.own_center(chosen)
            self.edit(lambda: self._keep_connected(lambda: [action(el, center) for el in chosen]))

    def _ends(self, index):
        el = self.elements[index]
        if el["kind"] == "wire":
            return [tuple(el["pts"][0]), tuple(el["pts"][-1])] if el["pts"] else []
        return model.connection_points(el)

    def _keep_connected(self, action):
        """旋轉、翻轉、方向鍵移動選取的元件:接在上面的導線跟著調整,直接接在一起的元件補一條導線。"""
        moving = set(self.selected)
        follow = model.attachments(self.elements, moving)
        bridges = model.touching(self.elements, moving)
        before = {i: self._ends(i) for i in moving}
        wires = {i: [list(p) for p in self.elements[i]["pts"]] for i, _, _ in follow}
        action()
        mapping = []
        for i in moving:
            mapping += list(zip(before[i], self._ends(i)))

        def moved(point):
            return next((new for old, new in mapping if model.same(old, point)), None)

        ends = {}
        for index, end, _ in follow:
            ends.setdefault(index, []).append(end)
        for index, which in ends.items():
            path = wires[index]
            for end in which:
                target = moved(path[end])
                if target is None:
                    continue
                ordered = path if end == -1 else list(reversed(path))
                routed = model.route(ordered[0], target, ordered)
                path = routed if end == -1 else list(reversed(routed))
            self.elements[index]["pts"] = model.clean_wire(path)
        for point in bridges:
            target = moved(point)
            if target is not None and not model.same(target, point):
                self.elements.append(model.new("wire", pts=model.clean_wire(self._wire_points(point, target))))

    def rotate_selected(self):
        self._transform_selected(model.rotate)

    def flip_selected(self, horizontal=True):
        self._transform_selected(lambda el, center: model.flip(el, center, horizontal))

    def swap_label_side(self):
        chosen = [el for el in self.selection() if "flip" in el and el["kind"] != "loop"]
        if chosen:
            self.edit(lambda: [el.update(flip=not el.get("flip")) for el in chosen])

    def reset_labels(self):
        chosen = [el for el in self.selection() if el.get("offsets")]
        if chosen:
            self.edit(lambda: [el.pop("offsets") for el in chosen])

    def _ghost_turn(self, action):
        """放置前的預覽跟著轉、翻。"""
        if action == "rotate":
            self.ghost_rot = (self.ghost_rot + 1) % 4
        else:
            self.ghost_rot = (-self.ghost_rot) % 4 if action == "flip_h" else (2 - self.ghost_rot) % 4
            self.ghost_mirror = not self.ghost_mirror

    def turn(self, action):
        """R、X、Y:正在放元件時轉預覽(兩端元件是轉放置的方向),否則轉選取的元件。"""
        if self.part_kind == "multi":
            self._ghost_turn(action)
        elif self.part_kind in ("two", "span"):
            dx, dy = self.last_dir
            self.last_dir = {"rotate": (-dy, dx), "flip_h": (-dx, dy), "flip_v": (dx, -dy)}[action]
        elif action == "rotate":
            self.rotate_selected()
        else:
            self.flip_selected(action == "flip_h")

    def align(self, how):
        """對齊或等距排列選取的元件(以元件本體的範圍計算,移動量對齊格點;接著的導線會跟著)。"""
        chosen = sorted(self.selected)
        boxes = {i: model.bounds(self.elements[i]) for i in chosen}
        boxes = {i: box for i, box in boxes.items() if box}
        if len(boxes) < 2:
            return
        moves = {}
        if how in ("left", "right", "hcenter", "top", "bottom", "vcenter"):
            pick = {"left": lambda b: b[0], "right": lambda b: b[2], "hcenter": lambda b: (b[0] + b[2]) / 2,
                    "top": lambda b: b[3], "bottom": lambda b: b[1], "vcenter": lambda b: (b[1] + b[3]) / 2}[how]
            values = [pick(box) for box in boxes.values()]
            target = min(values) if how in ("left", "bottom") else max(values) if how in ("right", "top") else \
                (min(values) + max(values)) / 2
            for i, box in boxes.items():
                shift = model.snap(target - pick(box))
                moves[i] = (shift, 0) if how in ("left", "right", "hcenter") else (0, shift)
        else:
            horizontal = how == "hspread"
            axis = 0 if horizontal else 1
            order = sorted(boxes, key=lambda i: (boxes[i][axis] + boxes[i][axis + 2]) / 2)
            first = (boxes[order[0]][axis] + boxes[order[0]][axis + 2]) / 2
            last = (boxes[order[-1]][axis] + boxes[order[-1]][axis + 2]) / 2
            step = (last - first) / (len(order) - 1)
            for rank, i in enumerate(order):
                center = (boxes[i][axis] + boxes[i][axis + 2]) / 2
                shift = model.snap(first + step * rank - center)
                moves[i] = (shift, 0) if horizontal else (0, shift)
        if not any(dx or dy for dx, dy in moves.values()):
            return

        def apply():
            for i, (dx, dy) in moves.items():
                model.move(self.elements[i], dx, dy)
        self.edit(lambda: self._keep_connected(apply))

    def nudge(self, dx, dy):
        chosen = self.selection()
        if chosen:
            self.edit(lambda: self._keep_connected(lambda: [model.move(el, dx, dy) for el in chosen]))

    def copy_selected(self):
        self.clipboard = model.clone(self.selection())
        self.paste_count = 0

    def paste(self, at=None):
        """貼上:滑鼠在畫布上時貼在滑鼠的位置(左上角對齊滑鼠),否則貼在原本那組的右邊,不會疊在一起。
        名稱自動換成還沒用過的編號。"""
        if not self.clipboard:
            return
        self.paste_count += 1
        pasted = model.clone(self.clipboard)
        box = model.all_bounds(pasted, include_labels=False)
        mouse = pygame.mouse.get_pos() if at is None else at
        if box and self.canvas.collidepoint(mouse):
            target = model.snap_point(self.to_world(mouse))
            dx, dy = target[0] - model.snap(box[0]), target[1] - model.snap(box[3])
        else:
            width = model.snap(box[2] - box[0]) + 1 if box else 1
            dx, dy = width * self.paste_count, 0
        named = list(self.elements)
        for el in pasted:
            model.move(el, dx, dy)
            if el.get("label") and parts.PREFIX.get(el["kind"]):
                el["label"] = model.next_label(named, el["kind"])
            named.append(el)
        start = len(self.elements)
        self.edit(lambda: self.elements.extend(pasted))
        self.select(range(start, len(self.elements)))

    def clear_all(self):
        if self.elements:
            self.edit(lambda: setattr(self, "elements", []))
            self.select(())
            self.path = None
            self._apply_settings(self._load_defaults())
            self.say("已清空，可以按 Ctrl+Z 復原")

    # ------------------------------------------------------------ 輸出

    def _need_content(self):
        if not self.elements:
            self.say("畫布上還沒有元件")
            return False
        return True

    def code(self):
        return codegen.generate(self.elements, self.resistor.value, self.caption.text, self.style.value)

    def copy_code(self):
        if self._need_content():
            widgets.copy_to_clipboard(self.code())
            self.say("已複製 Python 程式碼，可以直接貼進筆記")

    def png(self):
        return draw.render_png(self.elements, self.style.value, self.resistor.value, scale=self.resolution.value)

    def copy_image(self):
        if self._need_content():
            ok = imageclip.copy_image(self.png())
            self.say("已複製圖片" if ok else "剪貼簿正在被別的程式使用，請再試一次")

    def export(self, ext):
        if not self._need_content():
            return
        stem = self.path and os.path.splitext(os.path.basename(self.path))[0] or "電路圖"
        folder = output_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = files.free_path(folder, stem, ext)
            if ext == ".png":
                files.write_bytes(target, self.png())
            else:
                svg = draw.render_svg(self.elements, self.style.value, self.resistor.value)
                files.write_bytes(target, svg.encode("utf-8"))
        except OSError as error:
            self.say(f"存檔失敗：{error}")
            return
        self.say(f"已存成 {target.name}")

    def batch_export(self, folder=None):
        """把一個資料夾裡的電路圖全部輸出成 PNG 與 SVG(用目前的風格、字級、線寬、解析度,全書一致)。"""
        if folder is None:
            chosen = winfile.ask_open("選一個電路圖：會匯出同一個資料夾裡的全部電路圖", FILE_FILTERS,
                                      initial_dir=files_dir())
            if not chosen:
                return
            folder = chosen.parent
        from pathlib import Path

        folder = Path(folder)
        target = output_dir() / folder.name
        done, failed = 0, []
        for source in sorted(folder.glob("*.json")):
            if source.name in ("autosave.json", "defaults.json"):
                continue
            try:
                elements, _ = model.loads(source.read_text(encoding="utf-8"))
                if not elements:
                    continue
                target.mkdir(parents=True, exist_ok=True)
                files.write_bytes(target / f"{source.stem}.png",
                                  draw.render_png(elements, self.style.value, self.resistor.value,
                                                  scale=self.resolution.value))
                files.write_bytes(target / f"{source.stem}.svg",
                                  draw.render_svg(elements, self.style.value, self.resistor.value).encode("utf-8"))
                done += 1
            except (OSError, ValueError, UnicodeDecodeError, KeyError):
                failed.append(source.name)
        note = f"已匯出 {done} 張到 output\\circuit\\{folder.name}"
        self.say(note + (f"；{len(failed)} 個檔案讀不了" if failed else ""))
        return done, failed

    def open_file(self, path=None):
        path = path or winfile.ask_open("開啟電路圖", FILE_FILTERS, initial_dir=files_dir())
        if not path:
            return
        try:
            elements, settings = model.loads(open(path, encoding="utf-8").read())
        except (OSError, ValueError, UnicodeDecodeError):
            self.say("這個檔案不是電路圖")
            return
        self.edit(lambda: setattr(self, "elements", elements))
        self._apply_settings(settings)
        self.path = str(path)
        self.select(())
        self.fit_view()
        self.say(f"已開啟 {os.path.basename(path)}(可以按 Ctrl+Z 回到開啟前)")

    def save_file(self):
        if not self._need_content():
            return
        files_dir().mkdir(parents=True, exist_ok=True)
        name = os.path.basename(self.path) if self.path else "電路圖.json"
        path = winfile.ask_save("儲存電路圖", name, FILE_FILTERS, "json", initial_dir=files_dir())
        if not path:
            return
        try:
            files.write_bytes(path, model.dumps(self.elements, self.settings()).encode("utf-8"))
        except OSError as error:
            self.say(f"存檔失敗：{error}")
            return
        self.path = str(path)
        self.say(f"已儲存 {path.name}")

    # ------------------------------------------------------------ 事件

    def _typing(self):
        return any(box.focused for box in self.inputs)

    def matching_parts(self):
        """搜尋框篩選後的元件;名稱或英文代號含有搜尋的字就列出。"""
        query = self.search.text.strip().lower()
        result = []
        for title, kinds in parts.CATEGORIES:
            found = [kind for kind in kinds if not query or query in parts.PARTS[kind]["name"].lower()
                     or query in kind or query in title]
            if found:
                result.append((title, found))
        return result

    def handle_event(self, event, mouse_pos):
        if self.menu.handle_event(event, mouse_pos):
            return
        if event.type == pygame.DROPFILE:
            if event.file.lower().endswith(".json"):
                self.open_file(event.file)
            return
        enter = event.type == pygame.KEYDOWN and event.key in (pygame.K_RETURN, pygame.K_KP_ENTER)
        if enter and self.search.focused:          # 搜尋後按 Enter 直接選第一個元件
            found = self.matching_parts()
            if found:
                self.set_tool(found[0][1][0])
            self.search.blur()
            return
        for box, key in ((self.name_input, "label"), (self.value_input, "value")):
            if box.handle(event, mouse_pos):
                chosen = self.selection()
                if len(chosen) == 1 and key in chosen[0]:
                    if self._editing != (id(chosen[0]), key):
                        self.begin()
                        self._editing = (id(chosen[0]), key)
                    chosen[0][key] = box.text
                    self.changed()
        if self.caption.handle(event, mouse_pos):
            self._changed_at = time.monotonic()
        if self.search.handle(event, mouse_pos):
            self.palette_view.set_scroll(0)
        if self.square.handle(event, mouse_pos):
            self._set_loop(square=self.square.value / 100)
            return
        for slider in (self.text_scale, self.line_scale):
            if slider.handle(event, mouse_pos):
                self.apply_look()
                self.version += 1
                self._changed_at = time.monotonic()
                return
        if event.type == pygame.KEYUP and event.key == pygame.K_SPACE:
            self.space_held = False
        if event.type == pygame.KEYDOWN:
            if self._typing():
                if event.key in (pygame.K_RETURN, pygame.K_ESCAPE, pygame.K_KP_ENTER):
                    for box in self.inputs:
                        box.blur()
                return
            self._key(event)
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and not self.canvas.collidepoint(mouse_pos):
            self._editing = None
        if self.palette_view.handle_event(event, mouse_pos) or self.side_view.handle_event(event, mouse_pos):
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self._click_panels(mouse_pos):
                return
        self._canvas_event(event, mouse_pos)

    def _set_loop(self, **fields):
        chosen = [el for el in self.selection() if el["kind"] == "loop"]
        if chosen:
            if self._editing != ("loop", id(chosen[0])):
                self.begin()
                self._editing = ("loop", id(chosen[0]))
            for el in chosen:
                el.update(fields)
            self.changed()

    def _key(self, event):
        ctrl = event.mod & pygame.KMOD_CTRL
        shift = event.mod & pygame.KMOD_SHIFT
        key = event.key
        if ctrl and key == pygame.K_z:
            self.redo() if shift else self.undo()
        elif ctrl and key == pygame.K_y:
            self.redo()
        elif ctrl and key == pygame.K_a:
            self.set_tool("select")
            self.select(range(len(self.elements)))
        elif ctrl and key == pygame.K_c:
            self.copy_selected()
        elif ctrl and key == pygame.K_x:
            self.copy_selected()
            self.delete_selected()
        elif ctrl and key == pygame.K_v:
            self.paste()
        elif ctrl and key == pygame.K_d:
            self.copy_selected()
            self.paste(at=(-1, -1))      # 複製一份:放在原本那組旁邊
        elif ctrl and key == pygame.K_s:
            self.save_file()
        elif ctrl and key == pygame.K_f:
            self.search.focus()
            self.search.select_all()
        elif ctrl and key == pygame.K_r:
            self.turn("rotate")
        elif ctrl:
            return
        elif key == pygame.K_SPACE:
            self.space_held = True
        elif key == pygame.K_ESCAPE:
            if self.drag and self.drag["mode"] == "chain":
                self._finish_chain()
            elif self.tool_id != "select":
                self.set_tool("select")
            else:
                self.select(())
        elif key in (pygame.K_DELETE, pygame.K_BACKSPACE):
            self.delete_selected()
        elif key == pygame.K_r:
            self.turn("rotate")
        elif key in (pygame.K_x, pygame.K_m):
            self.turn("flip_h")
        elif key == pygame.K_y:
            self.turn("flip_v")
        elif key == pygame.K_f:
            self.swap_label_side()
        elif key == pygame.K_v:
            self.set_tool("select")
        elif key == pygame.K_w:
            self.set_tool("wire")
        elif key == pygame.K_t:
            self.set_tool("text")
        elif key == pygame.K_HOME:
            self.fit_view()
        elif key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN):
            step = parts.GRID
            self.nudge({pygame.K_LEFT: -step, pygame.K_RIGHT: step}.get(key, 0),
                       {pygame.K_UP: step, pygame.K_DOWN: -step}.get(key, 0))

    def _click_panels(self, pos):
        """左邊元件清單、右邊設定、畫布上的按鈕;回傳 True 代表點到了。"""
        if self.palette_area.collidepoint(pos):
            for tool_id, row in self.palette_rows:
                if row.collidepoint(pos):
                    self.set_tool("select" if self.tool_id == tool_id and tool_id != "select" else tool_id)
                    return True
            return True
        for action, rect in self.canvas_buttons:
            if rect.collidepoint(pos):
                action()
                return True
        if not self.side_area.collidepoint(pos):
            return False
        if self.resolution.clicked(pos, True):
            self._changed_at = time.monotonic()
            return True
        if self.style.clicked(pos, True) or self.resistor.clicked(pos, True):
            self.version += 1
            self._changed_at = time.monotonic()
            return True
        if self.direction.clicked(pos, True):
            self._editing = None
            self._set_loop(flip=self.direction.value)
            return True
        for key, button in self.align_buttons:
            if len(self.selected) > 1 and button.clicked(pos, True):
                self.align(key)
                return True
        actions = [(self.btn_default, self.save_defaults), (self.btn_batch, self.batch_export),
                   (self.btn_code, self.copy_code), (self.btn_image, self.copy_image),
                   (self.btn_png, lambda: self.export(".png")), (self.btn_svg, lambda: self.export(".svg")),
                   (self.btn_open, self.open_file), (self.btn_save, self.save_file), (self.btn_new, self.clear_all),
                   (self.btn_output, self.open_output), (self.btn_rotate, self.rotate_selected),
                   (self.btn_flip_h, lambda: self.flip_selected(True)),
                   (self.btn_flip_v, lambda: self.flip_selected(False)), (self.btn_side, self.swap_label_side),
                   (self.btn_reset_label, self.reset_labels), (self.btn_delete, self.delete_selected)]
        for button, action in actions:
            if button.clicked(pos, True):
                action()
                return True
        return True

    def open_output(self):
        output_dir().mkdir(parents=True, exist_ok=True)
        os.startfile(output_dir())

    def _canvas_event(self, event, pos):
        if event.type == pygame.MOUSEWHEEL and self.canvas.collidepoint(pos):
            self.zoom(1.15 ** event.y, pos)
            return
        pan = event.type == pygame.MOUSEBUTTONDOWN and (event.button == 2 or event.button == 1 and self.space_held)
        if pan and self.canvas.collidepoint(pos):
            self.drag = dict(mode="pan", start=pos, origin=self.origin, back=self.drag)
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3 and self.canvas.collidepoint(pos):
            if self.drag and self.drag["mode"] == "chain":
                self._finish_chain()
                return
            self._context_menu(pos)
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.canvas.collidepoint(pos):
            self._press(pos)
        elif event.type == pygame.MOUSEMOTION and self.drag:
            self._motion(pos)
        elif event.type == pygame.MOUSEBUTTONUP and event.button in (1, 2) and self.drag:
            self._release(pos)

    def _context_menu(self, pos):
        if self.tool_id != "select":
            self.set_tool("select")
            return
        index = self._hit(self.to_world(pos))
        if index is not None:
            if index not in self.selected:
                self.select([index])
            items = [("旋轉", "R", True, self.rotate_selected),
                     ("水平翻轉", "X", True, lambda: self.flip_selected(True)),
                     ("垂直翻轉", "Y", True, lambda: self.flip_selected(False)),
                     ("標籤換邊", "F", True, self.swap_label_side)]
            if any(el.get("offsets") for el in self.selection()):
                items.append(("標籤回到原位", "", True, self.reset_labels))
            items += [("複製一份", "Ctrl+D", True, lambda: (self.copy_selected(), self.paste(at=(-1, -1)))),
                      ("刪除", "Delete", True, self.delete_selected)]
            self.menu.open(pos, items)
        else:
            self.menu.open(pos, [("貼上", "Ctrl+V", bool(self.clipboard), lambda: self.paste(pos)),
                                 ("全部顯示", "Home", bool(self.elements), self.fit_view)])

    def _press(self, pos):
        world = self.to_world(pos)
        point = model.snap_point(world)
        kind = self.part_kind
        now = pygame.time.get_ticks()
        double = now - self._last_click[0] < 400 and self._last_click[1] == pos
        self._last_click = (now, pos)
        if self.drag and self.drag["mode"] == "chain":
            self._chain_click(point, double)
            return
        if kind == "select":
            self._press_select(pos, world, point, double)
            return
        if kind in ("multi", "text"):
            el = self._new_part(at=point)
            self.edit(lambda: self.elements.append(el))
            if kind == "text":                      # 放好就直接打字
                self.set_tool("select")
                self.select([len(self.elements) - 1])
                self.name_input.focus()
                self.name_input.select_all()
            return
        self.begin()
        self.drag = dict(mode="place", start=point, pos=pos, tool=self.tool_id)
        self._motion(pos)

    def _press_select(self, pos, world, point, double):
        handle = self._handle_at(pos)
        if handle:
            self._start_move(point, handle=handle)
            return
        index = self._hit(world)
        role = model.label_at(self.elements[index], world) if index is not None else None
        text_like = index is not None and model.kind_of(self.elements[index]) == "text"
        if index in self.selected and role and len(self.selected) == 1 and not text_like:
            if double:
                self.name_input.focus()
                self.name_input.select_all()
                return
            el = self.elements[index]
            self.begin()
            start = dict(el.get("offsets") or {}).get(role, [0, 0])
            self.drag = dict(mode="label", index=index, role=role, start=world, offset=list(start))
            return
        terminal = self._terminal_at(pos)
        if terminal and (index is None or index not in self.selected):
            self.begin()                                # 從元件的接點直接拉出導線
            self.drag = dict(mode="place", start=terminal, pos=pos, tool="wire")
            return
        shift = pygame.key.get_mods() & pygame.KMOD_SHIFT
        el = self.elements[index] if index is not None else None
        if el and el["kind"] == "wire" and not shift and (index not in self.selected or len(self.selected) == 1):
            segment = model.segment_at(el["pts"], world, max(0.1, 7 / self.scale))
            if segment is not None and not double:      # 拖導線的一段:其他段跟著伸縮,兩端不動
                self.select([index])
                self.begin()
                self.drag = dict(mode="segment", index=index, segment=segment, start=point,
                                 original=[list(p) for p in el["pts"]], delta=(0, 0), moved=False)
                return
        if index is None:
            if not shift:
                self.select(())
            self.drag = dict(mode="marquee", start=pos, base=set(self.selected))
            return
        if shift:
            self.select(self.selected ^ {index})
            return
        if index not in self.selected:
            self.select([index])
        if double and "label" in self.elements[index]:
            self.name_input.focus()
            self.name_input.select_all()
            return
        self._start_move(point, world=world)

    def _start_move(self, point, handle=None, world=None):
        """開始移動選取的元件,或拉某個元件的端點;接在上面的導線會跟著伸縮,
        直接接在一起(中間沒有導線)的元件會補一條導線,不會斷開。"""
        self.begin()
        moving = set(self.selected) if handle is None else {handle[0]}
        if handle is None:
            follow = model.attachments(self.elements, moving)
            bridges = model.touching(self.elements, moving)
        else:
            el = self.elements[handle[0]]
            spot = dict(model.handles(el))[handle[1]]
            follow = [item for item in model.attachments(self.elements, moving) if model.same(item[2], spot)]
            bridges = [q for q in model.touching(self.elements, moving) if model.same(q, spot)]
        first = len(self.elements)
        for q in bridges:
            self.elements.append(model.new("wire", pts=[list(q), list(q)]))
        free = handle is None and all(model.kind_of(self.elements[i]) == "text" for i in moving)
        self.drag = dict(mode="handle" if handle else "move", start=point, handle=handle, free=free,
                         world=world or point,
                         before={i: model.clone([self.elements[i]])[0] for i in moving},
                         follow=follow, wires={i: [list(p) for p in self.elements[i]["pts"]] for i, _, _ in follow},
                         bridges=list(range(first, len(self.elements))), delta=(0, 0), moved=False)

    def _chain_click(self, point, double):
        """點擊式畫導線:每點一下轉一個彎;點到接點、雙擊、點回同一點就結束。"""
        drag = self.drag
        last = drag["pts"][-1]
        if model.same(point, last) or double:
            self._finish_chain()
            return
        route = self._wire_points(last, point)
        drag["pts"] = model.clean_wire(drag["pts"] + route[1:])
        drag["end"] = point
        self.version += 1
        if self._connection_at(point):
            self._finish_chain()

    def _finish_chain(self):
        drag = self.drag
        if not drag or drag.get("mode") != "chain":
            return
        self.drag = None
        pts = model.clean_wire(drag["pts"])
        if len(pts) >= 2:
            self.elements.append(model.new("wire", pts=pts))
            self.changed()
        self.end()
        self.version += 1

    def _motion(self, pos):
        drag = self.drag
        world = self.to_world(pos)
        point = model.snap_point(world)
        mode = drag["mode"]
        if mode == "pan":
            dx, dy = pos[0] - drag["start"][0], pos[1] - drag["start"][1]
            self.origin = (drag["origin"][0] - dx / self.scale, drag["origin"][1] + dy / self.scale)
        elif mode == "segment":
            delta = (round(point[0] - drag["start"][0], 4), round(point[1] - drag["start"][1], 4))
            if delta != drag["delta"]:
                drag["delta"], drag["moved"] = delta, True
                self.elements[drag["index"]]["pts"] = model.drag_segment(drag["original"], drag["segment"], delta)
                self.changed()
        elif mode == "handle" and drag["handle"][1] in ("start", "end"):
            index, name = drag["handle"]            # 迴路電流的起點、箭頭:沿著迴路移動,不用對齊格點
            model.set_handle(self.elements[index], name, world)
            drag["moved"] = True
            drag["delta"] = (1, 1)
            self.changed()
        elif mode in ("move", "handle"):
            delta = (round(point[0] - drag["start"][0], 4), round(point[1] - drag["start"][1], 4))
            if drag.get("free"):                    # 文字:自由移動;按住 Ctrl 時位置貼齊格點
                delta = (round(world[0] - drag["world"][0], 3), round(world[1] - drag["world"][1], 3))
                if pygame.key.get_mods() & pygame.KMOD_CTRL:
                    base = next(iter(drag["before"].values()))["at"]
                    target = model.snap_point((base[0] + delta[0], base[1] + delta[1]))
                    delta = (round(target[0] - base[0], 4), round(target[1] - base[1], 4))
            if delta == drag["delta"]:
                return
            drag["delta"] = delta
            drag["moved"] = True
            self._apply_move(drag, delta)
            self.changed()          # 第一次動時記進復原清單;之後每次都要重畫(拉回原位也要)
        elif mode == "label":
            el = self.elements[drag["index"]]
            offset = [round(drag["offset"][0] + world[0] - drag["start"][0], 3),
                      round(drag["offset"][1] + world[1] - drag["start"][1], 3)]
            if pygame.key.get_mods() & pygame.KMOD_CTRL:    # 按住 Ctrl:標籤貼齊格點
                where = model.geometry(dict(el, offsets={}))["labels"]
                base = next(point for point, _, _, _, role in where if role == drag["role"])
                snapped = model.snap_point((base[0] + offset[0], base[1] + offset[1]))
                offset = [round(snapped[0] - base[0], 3), round(snapped[1] - base[1], 3)]
            el.setdefault("offsets", {})[drag["role"]] = offset
            self.changed()
        elif mode == "marquee":
            x0, y0 = drag["start"]
            area = pygame.Rect(min(x0, pos[0]), min(y0, pos[1]), abs(pos[0] - x0), abs(pos[1] - y0))
            drag["rect"] = area
            if area.width > 3 or area.height > 3:
                a, b = self.to_world(area.topleft), self.to_world(area.bottomright)
                inside = set()
                for index, el in enumerate(self.elements):
                    box = model.bounds(el)
                    if box and a[0] <= box[0] and box[2] <= b[0] and b[1] <= box[1] and box[3] <= a[1]:
                        inside.add(index)
                self.selected = drag["base"] | inside
        elif mode == "place":
            drag["end"] = point
            drag["moved"] = drag.get("moved") or abs(pos[0] - drag["pos"][0]) + abs(pos[1] - drag["pos"][1]) > 4
            self.version += 1
        elif mode == "chain":
            drag["end"] = point
            self.version += 1

    def _apply_move(self, drag, delta):
        dx, dy = delta
        if drag["handle"] is None:
            for index, before in drag["before"].items():
                el = model.clone([before])[0]
                model.move(el, dx, dy)
                self.elements[index] = el
        else:
            index, name = drag["handle"]
            el = model.clone([drag["before"][index]])[0]
            spot = dict(model.handles(el))[name]
            target = [round(spot[0] + dx, 4), round(spot[1] + dy, 4)]
            if el["kind"] == "wire":             # 拉導線的一端:另一端固定,轉角跟著調整
                path = list(reversed(el["pts"])) if name == 0 else el["pts"]
                routed = model.route(path[0], target, path)
                el["pts"] = list(reversed(routed)) if name == 0 else routed
            else:
                model.set_handle(el, name, target)
            self.elements[index] = el
        ends = {}
        for index, end, _ in drag["follow"]:
            ends.setdefault(index, []).append(end)
        for index, which in ends.items():
            original = drag["wires"][index]
            if len(which) == 2:                         # 兩端都跟著動:整條平移
                self.elements[index]["pts"] = [[round(x + dx, 4), round(y + dy, 4)] for x, y in original]
                continue
            path = original if which[0] == -1 else list(reversed(original))
            moved = [round(path[-1][0] + dx, 4), round(path[-1][1] + dy, 4)]
            routed = model.route(path[0], moved, path)
            self.elements[index]["pts"] = routed if which[0] == -1 else list(reversed(routed))
        for index in drag["bridges"]:
            start = self.elements[index]["pts"][0]
            self.elements[index]["pts"] = self._wire_points(start, [round(start[0] + dx, 4), round(start[1] + dy, 4)])

    def _release(self, pos):
        drag, self.drag = self.drag, None
        mode = drag["mode"]
        if mode == "pan":
            self.drag = drag.get("back")                # 點擊式畫導線中途平移畫面,放開後繼續畫
            return
        if mode == "chain":                             # 點擊式畫導線:放開滑鼠還沒結束
            self.drag = drag
            return
        if mode == "segment":
            if drag["delta"] == (0, 0) and drag["moved"] and self.history:
                self.elements = self.history.pop()
            self.version += 1
        elif mode == "handle" and drag["handle"][1] in ("start", "end"):
            self.version += 1
        elif mode in ("move", "handle"):
            self._finish_move(drag)
        elif mode == "marquee":
            self._sync_inputs()
        elif mode == "place":
            if drag["tool"] == "wire" and not drag.get("moved"):
                self.drag = dict(mode="chain", pts=[drag["start"]], end=drag["start"])    # 改成點擊式畫導線
                return
            self._place(drag)
        self.end()

    def _finish_move(self, drag):
        """放開:沒有真的移動就當作沒發生;補的導線長度是 0 的拿掉,導線去掉多餘的轉角。"""
        if drag["delta"] == (0, 0):
            if drag["moved"] and self.history:
                self.elements = self.history.pop()      # 拖出去又拉回原位
            else:
                self.elements = [el for i, el in enumerate(self.elements) if i not in drag["bridges"]]
            self.version += 1
            self.end()
            return
        for index in {i for i, _, _ in drag["follow"]} | set(drag["bridges"]):
            self.elements[index]["pts"] = model.clean_wire(self.elements[index]["pts"])
        if drag["handle"] is not None:
            index, _ = drag["handle"]
            el = self.elements[index]
            if el["kind"] == "wire":
                el["pts"] = model.clean_wire(el["pts"])
            degenerate = (el["kind"] == "wire" and len(el["pts"]) < 2) or ("a" in el and el["a"] == el["b"])
            if degenerate and self.history:             # 端點拖到另一端上(長度變 0):取消這次拖曳
                self.elements = self.history.pop()
        self.elements = [el for el in self.elements if el["kind"] != "wire" or len(el["pts"]) >= 2]
        self.selected = {i for i in self.selected if i < len(self.elements)}
        self.version += 1
        self.end()

    def _place(self, drag):
        start, end = drag["start"], drag.get("end", drag["start"])
        tool = drag["tool"]
        kind = "wire" if tool == "wire" else parts.PARTS[tool]["kind"]
        if kind == "wire":
            if start == end:
                return
            self.elements.append(model.new("wire", pts=self._wire_points(start, end)))
        elif kind in ("box", "loop", "rect"):
            if start[0] == end[0] or start[1] == end[1]:
                self.say("按住拖曳畫出範圍")
                return
            fields = dict(a=start, b=end)
            if kind == "loop":
                fields["label"] = model.next_label(self.elements, tool)
            elif kind == "rect":
                fields["label"] = DEFAULT_TEXT
            self.elements.append(model.new(tool, **fields))
        else:
            if not drag.get("moved") or start == end:
                end = model.snap_point((start[0] + self.last_dir[0] * DEFAULT_LEN,
                                        start[1] + self.last_dir[1] * DEFAULT_LEN))
            else:
                length = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
                self.last_dir = ((end[0] - start[0]) / length, (end[1] - start[1]) / length)
            self.elements.append(self._new_part(a=start, b=end))
        self.changed()

    def _in_progress(self):
        """拖曳中還沒放下的導線或元件。"""
        drag = self.drag
        if not drag or "end" not in drag:
            return None
        if drag["mode"] == "chain":
            pts = model.clean_wire(drag["pts"] + self._wire_points(drag["pts"][-1], drag["end"])[1:])
            return model.new("wire", pts=pts) if len(pts) >= 2 else None
        if drag["mode"] != "place":
            return None
        start, end = drag["start"], drag["end"]
        tool = drag["tool"]
        kind = "wire" if tool == "wire" else parts.PARTS[tool]["kind"]
        if kind == "wire":
            return model.new("wire", pts=self._wire_points(start, end)) if start != end else None
        if kind in ("box", "loop", "rect"):
            return model.new(tool, a=start, b=end) if start[0] != end[0] and start[1] != end[1] else None
        if not drag.get("moved") or start == end:
            return None
        return self._new_part(a=start, b=end)

    # ------------------------------------------------------------ 繪製

    def draw(self, rect, mouse_pos):
        self.apply_look()
        margin = 16
        body = pygame.Rect(rect.x + margin, rect.y + margin, rect.width - margin * 2, rect.height - margin * 2)
        side_w = SIDE_W if body.width > 900 else 240
        palette = pygame.Rect(body.x, body.y, PALETTE_W, body.height)
        side = pygame.Rect(body.right - side_w, body.y, side_w, body.height)
        canvas = pygame.Rect(palette.right + 12, body.y, side.x - 12 - palette.right - 12, body.height)
        first = self.origin is None
        self.canvas = canvas
        if first:
            self.fit_view()
        self.draw_palette(palette, mouse_pos)
        self.draw_canvas(canvas, mouse_pos)
        self.draw_side(side, mouse_pos)
        self.menu.draw(self.screen, mouse_pos)

    def _icon(self, kind, color):
        key = (kind, self.resistor.value, color)
        if key not in self._icons:
            image = draw.part_icon(kind, self.resistor.value, (46, 26), color)
            self._icons[key] = pygame.image.frombytes(image.tobytes(), image.size, "RGBA")
        return self._icons[key]

    def draw_palette(self, rect, mouse_pos):
        screen = self.screen
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        draw_text(screen, "元件", (rect.x + 16, rect.y + 13), 15, theme.TEXT, bold=True)
        self.search.draw(screen, pygame.Rect(rect.x + 60, rect.y + 8, rect.width - 72, 28), mouse_pos)
        pygame.draw.line(screen, theme.PANEL_EDGE, (rect.x + 12, rect.y + 44), (rect.right - 12, rect.y + 44))
        area = pygame.Rect(rect.x, rect.y + 45, rect.width, rect.height - 49)
        self.palette_area = area
        view = self.palette_view
        rows = [] if self.search.text.strip() else [("tool", "select", "選取", "V"), ("tool", "wire", "導線", "W")]
        found = self.matching_parts()
        for title, kinds in found:
            rows.append(("head", title, title, ""))
            rows += [("part", kind, parts.PARTS[kind]["name"], "T" if kind == "text" else "") for kind in kinds]
        height = sum(28 if row[0] == "head" else ROW_H for row in rows) + 12
        view.layout(area, height)
        self.palette_rows = []
        screen.set_clip(area)
        if not found:
            draw_text(screen, "找不到符合的元件", (area.centerx, area.y + 30), 13, theme.TEXT_FAINT, center=True)
        y = area.y + 6 - view.scroll
        for row_kind, key, name, shortcut in rows:
            if row_kind == "head":
                draw_text(screen, name, (area.x + 16, y + 8), 12, theme.TEXT_FAINT, bold=True)
                y += 28
                continue
            row = pygame.Rect(area.x + 8, y, area.width - 8 - BAR_SPACE, ROW_H - 3)
            if area.bottom > row.y and row.bottom > area.y:
                active = self.tool_id == key
                hover = row.collidepoint(mouse_pos) and area.collidepoint(mouse_pos)
                if active or hover:
                    rounded_panel(screen, row, tuple(int(c * 0.28) for c in self.accent) if active
                                  else theme.PANEL_LIGHT, radius=7, border=self.accent if active else None)
                color = self.accent if active else theme.TEXT
                if row_kind == "part":
                    screen.blit(self._icon(key, (225, 229, 238)), (row.x + 6, row.centery - 13))
                else:
                    self._tool_icon(key, (row.x + 29, row.centery), color)
                draw_text(screen, widgets.clip_text(name, 13, row.width - 80), (row.x + 60, row.centery - 9), 13,
                          color)
                if shortcut:
                    draw_text(screen, shortcut, (row.right - 10, row.centery - 9), 13, theme.TEXT_FAINT, right=True)
                self.palette_rows.append((key, row.clip(area)))
            y += ROW_H
        screen.set_clip(None)
        view.draw(screen, mouse_pos)

    def _tool_icon(self, key, center, color):
        x, y = center
        screen = self.screen
        if key == "select":
            pygame.draw.polygon(screen, color, [(x - 6, y - 9), (x - 6, y + 7), (x - 2, y + 3), (x + 2, y + 10),
                                                (x + 4, y + 9), (x, y + 2), (x + 6, y + 2)])
        else:
            pygame.draw.lines(screen, color, False, [(x - 12, y + 6), (x, y + 6), (x, y - 6), (x + 12, y - 6)], 2)
            for px_, py_ in ((x - 12, y + 6), (x + 12, y - 6)):
                pygame.draw.circle(screen, color, (px_, py_), 3)

    def _layer_surface(self, elements, key_extra, cache_attr):
        key = (self.version, key_extra, self.scale, self.origin, self.canvas.size, self.style.value,
               self.resistor.value)
        cached_key, surface = getattr(self, cache_attr)
        if cached_key != key:
            image = draw.render_view(elements, self.style.value, self.resistor.value, self.canvas.size, self.scale,
                                     self.origin)
            surface = pygame.image.frombytes(image.tobytes(), image.size, "RGBA")
            setattr(self, cache_attr, (key, surface))
        return surface

    def draw_canvas(self, rect, mouse_pos):
        screen = self.screen
        look = draw.STYLES[self.style.value]
        paper = look["bg"]
        pygame.draw.rect(screen, paper, rect, border_radius=12)
        screen.set_clip(rect)
        screen.blit(self._grid_surface(rect, paper), rect.topleft)

        in_progress = self._in_progress()
        elements = self.elements + ([in_progress] if in_progress else [])
        moving = self._moving_indices()
        if moving:
            self._draw_split(rect, moving, elements)
        else:
            self._static = None
            extra = repr(in_progress) if in_progress else ""
            screen.blit(self._layer_surface(elements, extra, "_layer"), rect.topleft)

        ghost = None if self.drag else self.ghost(mouse_pos)
        if ghost:
            surface = self._layer_surface([ghost], repr(ghost), "_ghost_layer")
            surface.set_alpha(110)
            screen.blit(surface, rect.topleft)
            surface.set_alpha(None)
        self._draw_open_terminals(elements)
        self._draw_snap_marker(mouse_pos)
        self._draw_selection(mouse_pos)
        if self.drag and self.drag["mode"] == "marquee" and self.drag.get("rect"):
            area = self.drag["rect"]
            fill = pygame.Surface(area.size, pygame.SRCALPHA)
            fill.fill(self.accent + (40,))
            screen.blit(fill, area.topleft)
            pygame.draw.rect(screen, self.accent, area, 1)
        screen.set_clip(None)
        pygame.draw.rect(screen, theme.PANEL_EDGE, rect, 1, border_radius=12)
        self._draw_canvas_bar(rect, mouse_pos)
        if not self.elements and not in_progress:
            dim = (150, 156, 168)
            draw_text(screen, "從左邊選一個元件，在這裡拖曳放置", (rect.centerx, rect.centery - 12), 16, dim,
                      center=True)
            draw_text(screen, "元件的端點接在一起就連通；滾輪縮放，按住中鍵或空白鍵拖曳移動畫面",
                      (rect.centerx, rect.centery + 16), 13, dim, center=True)

    def _moving_indices(self):
        """正在拖曳時,哪些元件會變(被拖的、跟著伸縮的導線);其他的先畫好重用。"""
        drag = self.drag
        if not drag:
            return None
        if drag["mode"] in ("move", "handle"):
            return set(drag["before"]) | {i for i, _, _ in drag["follow"]} | set(drag["bridges"])
        if drag["mode"] in ("segment", "label"):
            return {drag["index"]}
        return None

    def _draw_split(self, rect, moving, elements):
        """拖曳中:不動的元件整張畫一次(拖曳結束前都重用),被拖的元件只畫它們所在的一小塊。"""
        key = (id(self.drag), self.scale, self.origin, self.canvas.size, self.style.value, self.resistor.value,
               model.TEXT_SCALE, model.LINE_SCALE)
        if not self._static or self._static[0] != key:
            still = [el for i, el in enumerate(self.elements) if i not in moving]
            # 交點:和被拖的元件無關的先畫在這層;碰到被拖元件的交點每次在小塊裡重畫
            touched = [p for i in moving if i < len(self.elements) for p in self._ends(i)]
            touched += [tuple(p) for i in moving if i < len(self.elements) and self.elements[i]["kind"] == "wire"
                        for p in self.elements[i]["pts"]]
            fixed = [p for p in model.junctions(self.elements) if not any(model.same(p, q) for q in touched)]
            image = draw.render_view(still, self.style.value, self.resistor.value, self.canvas.size, self.scale,
                                     self.origin, dots=fixed)
            self._static = (key, pygame.image.frombytes(image.tobytes(), image.size, "RGBA"))
        self.screen.blit(self._static[1], rect.topleft)
        active = [el for i, el in enumerate(elements) if i in moving or i >= len(self.elements)]
        box = model.all_bounds(active)
        dots = model.junctions(elements)
        if not box:
            return
        pad = 0.3
        (x0, y0), (x1, y1) = self.to_screen((box[0] - pad, box[3] + pad)), self.to_screen((box[2] + pad, box[1] - pad))
        area = pygame.Rect(int(x0), int(y0), int(x1 - x0) + 1, int(y1 - y0) + 1).clip(rect)
        if area.width <= 0 or area.height <= 0:
            return
        origin = self.to_world(area.topleft)
        inside = [p for p in dots if area.collidepoint(self.to_screen(p))]
        image = draw.render_view(active, self.style.value, self.resistor.value, area.size, self.scale, origin,
                                 dots=inside)
        self.screen.blit(pygame.image.frombytes(image.tobytes(), image.size, "RGBA"), area.topleft)

    def _grid_surface(self, rect, paper):
        """格點畫在一張透明圖上,畫面沒有移動、縮放時直接重用。"""
        step = parts.GRID * self.scale
        x0, y0 = self.origin
        first_x = round((-(x0 % parts.GRID)) * self.scale)
        first_y = round((y0 % parts.GRID) * self.scale)
        key = (round(step, 3), first_x, first_y, rect.size, paper)
        cached_key, surface = self._grid
        if cached_key == key:
            return surface
        surface = pygame.Surface(rect.size, pygame.SRCALPHA)
        if step >= 6:
            color = (70, 76, 90) if sum(paper) < 300 else (205, 210, 220)
            x = first_x
            while x < rect.width:
                y = first_y
                while y < rect.height:
                    surface.fill(color, (int(x), int(y), 2, 2))
                    y += step
                x += step
        self._grid = (key, surface)
        return surface

    def _draw_open_terminals(self, elements):
        """還沒接線的元件接點畫小圈(只在編輯時看得到,輸出的圖沒有)。"""
        color = tuple(int(c * 0.8) for c in self.accent)
        for point in model.open_terminals(elements):
            pygame.draw.circle(self.screen, color, self.to_screen(point), 3, 1)

    def _draw_snap_marker(self, mouse_pos):
        """滑鼠停在元件的接點上時畫一個圈,提示放線、拉線會接上。"""
        if not self.canvas.collidepoint(mouse_pos) or self.space_held:
            return
        if self.tool_id == "select":
            if self.drag or self._handle_at(mouse_pos):
                return
            point = self._terminal_at(mouse_pos)
        else:
            point = model.snap_point(self.to_world(mouse_pos))
            if not self._connection_at(point):
                return
        if point:
            pygame.draw.circle(self.screen, self.accent, self.to_screen(point), 7, 2)

    def _draw_selection(self, mouse_pos):
        screen = self.screen
        hover = None
        if self.tool_id == "select" and not self.drag and self.canvas.collidepoint(mouse_pos):
            hover = self._hit(self.to_world(mouse_pos))
        for index in set(self.selected) | ({hover} if hover is not None else set()):
            if index >= len(self.elements):
                continue
            el = self.elements[index]
            box = model.bounds(el)
            if not box:
                continue
            (x0, y0), (x1, y1) = self.to_screen((box[0], box[3])), self.to_screen((box[2], box[1]))
            area = pygame.Rect(x0 - 5, y0 - 5, x1 - x0 + 10, y1 - y0 + 10)
            selected = index in self.selected
            color = self.accent if selected else tuple(int(c * 0.6) for c in self.accent)
            pygame.draw.rect(screen, color, area, 2 if selected else 1, border_radius=6)
            if selected and len(self.selected) == 1:
                self._draw_label_boxes(el, mouse_pos)
                for name, point in model.handles(el):
                    center = self.to_screen(point)
                    if name in ("start", "end"):        # 迴路電流的起點、箭頭:實心,和四個角分得出來
                        pygame.draw.circle(screen, self.accent, center, 6)
                        pygame.draw.circle(screen, (255, 255, 255), center, 6, 2)
                    else:
                        pygame.draw.circle(screen, (255, 255, 255), center, 6)
                        pygame.draw.circle(screen, self.accent, center, 6, 2)

    def _draw_label_boxes(self, el, mouse_pos):
        """選取的元件:標籤外面畫虛線框,提示可以拖曳。"""
        if el["kind"] == "wire" or model.kind_of(el) == "text":
            return
        world = self.to_world(mouse_pos)
        for where, text, ha, va, role in model.geometry(el)["labels"]:
            if not text:
                continue
            x0, y0, x1, y1 = model.label_box(where, text, ha, va)
            (sx0, sy0), (sx1, sy1) = self.to_screen((x0, y1)), self.to_screen((x1, y0))
            area = pygame.Rect(sx0 - 3, sy0 - 2, sx1 - sx0 + 6, sy1 - sy0 + 4)
            hot = model.label_at(el, world) == role
            color = self.accent if hot else tuple(int(c * 0.6) for c in self.accent)
            for x in range(area.x, area.right, 6):
                pygame.draw.line(self.screen, color, (x, area.y), (min(x + 3, area.right), area.y))
                pygame.draw.line(self.screen, color, (x, area.bottom), (min(x + 3, area.right), area.bottom))
            for y in range(area.y, area.bottom, 6):
                pygame.draw.line(self.screen, color, (area.x, y), (area.x, min(y + 3, area.bottom)))
                pygame.draw.line(self.screen, color, (area.right, y), (area.right, min(y + 3, area.bottom)))

    def _draw_canvas_bar(self, rect, mouse_pos):
        screen = self.screen
        self.canvas_buttons = []
        x = rect.x + 12
        for label, action, enabled in (("復原", self.undo, bool(self.history)), ("重做", self.redo, bool(self.future)),
                                       ("全部顯示", self.fit_view, bool(self.elements))):
            width = theme.font(13).size(label)[0] + 22
            button = pygame.Rect(x, rect.y + 10, width, 28)
            hover = enabled and button.collidepoint(mouse_pos)
            rounded_panel(screen, button, theme.PANEL_LIGHT if hover else theme.PANEL, radius=7, alpha=235,
                          border=self.accent if hover else theme.PANEL_EDGE)
            draw_text(screen, label, button.center, 13, theme.TEXT if enabled else theme.TEXT_FAINT, center=True)
            if enabled:
                self.canvas_buttons.append((action, button))
            x = button.right + 6
        hint = TOOL_HINTS[self.part_kind]
        if self.drag and self.drag["mode"] == "chain":
            hint = "每點一下轉一個彎；點到接點、雙擊、按 Esc 或右鍵結束"
        if self.notice and time.monotonic() - self.notice_time < 5:
            hint, color = self.notice, self.accent
        else:
            color = theme.TEXT_DIM
        text = widgets.clip_text(hint, 12, rect.width - 80)
        bar = pygame.Rect(rect.x + 10, rect.bottom - 36, theme.font(12).size(text)[0] + 24, 26)
        rounded_panel(screen, bar, theme.PANEL, radius=7, alpha=225, border=theme.PANEL_EDGE)
        draw_text(screen, text, (bar.x + 12, bar.y + 5), 12, color)
        zoom = f"{self.scale / 64:.0%}"
        draw_text(screen, zoom, (rect.right - 14, rect.bottom - 30), 12, (150, 156, 168), right=True)

    def draw_side(self, rect, mouse_pos):
        screen = self.screen
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        view = self.side_view
        area = pygame.Rect(rect.x, rect.y + 4, rect.width, rect.height - 8)
        self.side_area = area
        screen.set_clip(area)
        bottom = self._side_rows(rect.x + 16, area.y + 10 - view.scroll, rect.width - 32, mouse_pos)
        screen.set_clip(None)
        view.layout(area, bottom + view.scroll - area.y + 10)
        view.draw(screen, mouse_pos)

    def _button_pairs(self, buttons, x, y, width, mouse_pos):
        """按鈕兩個一排;回傳下一列的 y。"""
        half = (width - 6) // 2
        for index, button in enumerate(buttons):
            column, row = index % 2, index // 2
            button.draw(self.screen, pygame.Rect(x + column * (half + 6), y + row * 36, half, 30), mouse_pos)
        return y + (len(buttons) + 1) // 2 * 36

    def _align_rows(self, x, y, width, mouse_pos):
        draw_text(self.screen, "對齊", (x, y + 2), 13, theme.TEXT_DIM)
        y += 24
        quarter = (width - 18) // 4
        for index, (_, button) in enumerate(self.align_buttons):
            column, row = index % 4, index // 4
            button.draw(self.screen, pygame.Rect(x + column * (quarter + 6), y + row * 34, quarter, 28), mouse_pos)
        return y + 72

    def _slider_row(self, title, slider, x, y, width, mouse_pos):
        draw_text(self.screen, title, (x, y + 3), 13, theme.TEXT_DIM)
        draw_text(self.screen, f"{slider.value}%", (x + width, y + 12), 12, theme.TEXT_FAINT, right=True)
        slider.draw(self.screen, pygame.Rect(x + 48, y + 2, width - 48 - 46, 20), mouse_pos)
        return y + 30

    def _side_rows(self, x, y, width, mouse_pos):
        screen = self.screen
        chosen = self.selection()
        draw_text(screen, "屬性", (x, y), 15, theme.TEXT, bold=True)
        y += 30
        if not chosen:
            for line in ("選取元件後可以改名稱、數值", "名稱、數值支援 LaTeX"):
                draw_text(screen, widgets.clip_text(line, 12, width), (x, y), 12, theme.TEXT_FAINT)
                y += 20
        else:
            if len(chosen) == 1:
                el = chosen[0]
                shape = model.kind_of(el)
                name = "導線" if el["kind"] == "wire" else parts.PARTS[el["kind"]]["name"]
                draw_text(screen, name, (x, y), 13, self.accent, bold=True)
                y += 26
                if "label" in el:
                    fields = [("文字" if shape == "text" else "名稱", self.name_input)]
                    if shape in ("two", "multi"):
                        fields.append(("數值", self.value_input))
                    for title, box in fields:
                        draw_text(screen, title, (x, y + 7), 13, theme.TEXT_DIM)
                        box.draw(screen, pygame.Rect(x + 40, y, width - 40, 32), mouse_pos)
                        y += 40
                if el["kind"] == "loop":
                    draw_text(screen, "形狀", (x, y + 5), 13, theme.TEXT_DIM)
                    draw_text(screen, "橢圓", (x + 40, y + 6), 12, theme.TEXT_FAINT)
                    draw_text(screen, "方形", (x + width, y + 6), 12, theme.TEXT_FAINT, right=True)
                    self.square.draw(screen, pygame.Rect(x + 78, y + 4, width - 78 - 34, 20), mouse_pos)
                    y += 34
                    self.direction.draw(screen, pygame.Rect(x, y, width, 32), mouse_pos)
                    y += 42
            else:
                draw_text(screen, f"已選 {len(chosen)} 個", (x, y), 13, self.accent, bold=True)
                y += 26
            buttons = [self.btn_rotate, self.btn_flip_h, self.btn_flip_v, self.btn_side]
            if any(el.get("offsets") for el in chosen):
                buttons.append(self.btn_reset_label)
            buttons.append(self.btn_delete)
            y = self._button_pairs(buttons, x, y, width, mouse_pos)
            if len(chosen) > 1:
                y = self._align_rows(x, y, width, mouse_pos)
            if len(chosen) == 1 and model.kind_of(chosen[0]) not in ("wire", "text"):
                draw_text(screen, "點選後可以直接拖曳標籤調整位置", (x, y + 2), 12, theme.TEXT_FAINT)
                y += 22
        y += 8
        pygame.draw.line(screen, theme.PANEL_EDGE, (x, y), (x + width, y))
        y += 14

        draw_text(screen, "輸出", (x, y), 15, theme.TEXT, bold=True)
        y += 30
        draw_text(screen, "風格", (x, y), 13, theme.TEXT_DIM)
        y += 22
        y += self.style.draw(screen, pygame.Rect(x, y, width, 0), mouse_pos) + 6
        for line in widgets.wrap_text(draw.STYLES[self.style.value]["note"], 12, width, max_lines=2):
            draw_text(screen, line, (x, y), 12, theme.TEXT_FAINT)
            y += 18
        y += 8
        draw_text(screen, "電阻畫法", (x, y), 13, theme.TEXT_DIM)
        y += 22
        self.resistor.draw(screen, pygame.Rect(x, y, width, 32), mouse_pos)
        y += 42
        y = self._slider_row("字級", self.text_scale, x, y, width, mouse_pos)
        y = self._slider_row("線寬", self.line_scale, x, y, width, mouse_pos)
        self.btn_default.draw(screen, pygame.Rect(x, y, 84, 26), mouse_pos)
        draw_text(screen, "清空重畫時套用", (x + 92, y + 5), 12, theme.TEXT_FAINT)
        y += 36
        draw_text(screen, "圖說", (x, y + 7), 13, theme.TEXT_DIM)
        self.caption.draw(screen, pygame.Rect(x + 40, y, width - 40, 32), mouse_pos)
        y += 42
        self.btn_code.draw(screen, pygame.Rect(x, y, width, 36), mouse_pos)
        y += 42
        for line in widgets.wrap_text("產生 matplotlib 程式碼(含 #| plot)，貼進筆記就能匯出，顏色照選的風格",
                                      12, width, max_lines=2):
            draw_text(screen, line, (x, y), 12, theme.TEXT_FAINT)
            y += 18
        y += 8
        draw_text(screen, "圖片解析度", (x, y), 13, theme.TEXT_DIM)
        y += 22
        self.resolution.draw(screen, pygame.Rect(x, y, width, 32), mouse_pos)
        y += 38
        box = draw.export_bounds(self.elements)
        if box:
            size = f"{round((box[2] - box[0]) * self.resolution.value)} × {round((box[3] - box[1]) * self.resolution.value)}"
            draw_text(screen, f"複製、存成 PNG 時約 {size} 像素", (x, y), 12, theme.TEXT_FAINT)
            y += 24
        third = (width - 12) // 3
        for index, button in enumerate((self.btn_image, self.btn_png, self.btn_svg)):
            button.draw(screen, pygame.Rect(x + index * (third + 6), y, third, 30), mouse_pos)
        y += 42
        pygame.draw.line(screen, theme.PANEL_EDGE, (x, y), (x + width, y))
        y += 14
        draw_text(screen, "檔案", (x, y), 15, theme.TEXT, bold=True)
        y += 30
        for index, button in enumerate((self.btn_open, self.btn_save, self.btn_new)):
            button.draw(screen, pygame.Rect(x + index * (third + 6), y, third, 30), mouse_pos)
        y += 36
        half = (width - 6) // 2
        self.btn_output.draw(screen, pygame.Rect(x, y, half, 30), mouse_pos)
        self.btn_batch.draw(screen, pygame.Rect(x + half + 6, y, half, 30), mouse_pos)
        y += 38
        for line in widgets.wrap_text("畫的內容會自動保留，關掉程式再打開還在；「另存」可以存成檔案分享", 12, width,
                                      max_lines=2):
            draw_text(screen, line, (x, y), 12, theme.TEXT_FAINT)
            y += 18
        return y
