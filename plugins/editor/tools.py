"""PDF 編輯器的標記、註解:主畫面上方的工具列、在頁面上新增與修改註解、直接在頁面上打字。

所有修改都放回頁面清單,和旋轉、刪除頁面共用同一條復原紀錄。
"""

import math
from dataclasses import replace
from pathlib import Path

import pygame

from core import pdfium, theme, widgets, winfile
from core.widgets import Dropdown, draw_text, rounded_panel

from . import annot_view, annots, fonts, geometry, model, paragraphs, pdffonts, pdfwrite, signature
from .font_picker import FontPicker
from .signature import IMAGE_FILTER, SignaturePanel
from .palette import ColorPalette
from .textarea import TextEditor, simple_layout

BAR_H = 86
TOOLS = [("select", "選取"), ("highlight", "螢光筆"), ("underline", "底線"), ("strike", "刪除線"), ("textbox", "文字框"),
         ("replace", "改字"), ("redact", "塗黑"), ("note", "便利貼"), ("line", "直線"), ("arrow", "箭頭"), ("rect", "方框"),
         ("ellipse", "圓形"), ("ink", "手繪"), ("image", "圖片"), ("signature", "簽名")]
WIDTH_OPTIONS = [(f"{v:g}", f"{v:g} pt") for v in (1, 2, 3, 5, 8)]
SHAPE_WIDTH_OPTIONS = [("0", "無線條")] + WIDTH_OPTIONS
BORDER_OPTIONS = [("0", "無外框")] + [(f"{v:g}", f"外框 {v:g} pt") for v in (1, 2, 3)]
OPACITY_OPTIONS = [("1", "0%"), ("0.75", "25%"), ("0.5", "50%"), ("0.3", "70%")]
SIZE_OPTIONS = [(f"{v:g}", f"{v:g} pt") for v in (8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 64, 72)]
DEFAULTS = {
    "highlight": dict(opacity=1.0), "underline": dict(opacity=1.0), "strike": dict(opacity=1.0),
    "textbox": dict(width=0.0, font="", font_size=14.0, opacity=1.0, background=()), "note": {},
    "line": dict(width=2.0, opacity=1.0), "arrow": dict(width=2.0, opacity=1.0),
    "rect": dict(width=2.0, opacity=1.0, background=()), "ellipse": dict(width=2.0, opacity=1.0, background=()),
    "ink": dict(width=2.0, opacity=1.0),
    "replace": dict(font="", font_size=12.0, opacity=1.0, background=(255, 255, 255), width=0.0),
    "image": dict(opacity=1.0), "signature": dict(opacity=1.0), "redact": {},
}
IMAGE_DPI = 150             # 放進來的圖片預設以這個解析度換算大小
SIGNATURE_W = 150.0         # 簽名預設寬度(點)
TOOL_HINTS = {
    "replace": "點一下文字修改整段，拖曳選取只改其中幾個字；清空文字就是刪除。儲存時原字會真正刪掉",
    "redact": "在文字上拖曳選取要塗黑的字，或在其他地方拉出範圍(照片、簽名也行)；儲存時底下的字和圖會真正刪掉",
    "image": "在頁面上點一下放置圖片，或拖曳出想要的大小",
    "signature": "在頁面上點一下放置簽名，或拖曳出想要的大小",
}
DOUBLE_CLICK_MS = 400
TEXTBOX_W = 200.0
NOTE_POPUP_W = 260
HINT = "選擇上方的工具後在頁面上拖曳；點選註解可以移動、改大小、改顏色，按 Delete 刪除，按兩下文字框或便利貼可以修改文字"


def _values(annot):
    return dict(color=annot.color, opacity=annot.opacity, width=annot.width, background=annot.background,
                font=annot.font, font_size=annot.font_size)


# 每一種註解在設定列上有哪些顏色可以調:(設定名稱, 按鈕文字, 可不可以選「無」)
COLOR_SLOTS = {
    "textbox": [("color", "文字", False), ("background", "背景", True)],
    "rect": [("color", "線條", False), ("background", "填滿", True)],
    "ellipse": [("color", "線條", False), ("background", "填滿", True)],
    "replace": [("color", "文字", False), ("background", "底色", False)],
    "image": [], "signature": [],
}


class AnnotController:
    def __init__(self, page):
        self.page = page
        self.accent = page.tool.accent
        self.tool = "select"
        self.settings = {kind: dict(color=annots.DEFAULT_COLORS[kind], **values) for kind, values in DEFAULTS.items()}
        self.selected = None        # (頁面 uid, 註解 uid)
        self.action = None          # 進行中的滑鼠操作
        self.editing = None         # 正在打字的文字框或便利貼
        self.cache = annot_view.SurfaceCache()
        self.picker = FontPicker(page, self.accent)
        self.palette = ColorPalette(self.accent)
        self._palette_key = ""
        self.bar_rect = pygame.Rect(0, 0, 0, 0)
        self.tool_rects, self.color_buttons = [], []
        self.font_rect = pygame.Rect(0, 0, 0, 0)
        self.width_menu = Dropdown(WIDTH_OPTIONS, accent=self.accent, size=13)
        self.opacity_menu = Dropdown(OPACITY_OPTIONS, accent=self.accent, size=13)
        self.size_menu = Dropdown(SIZE_OPTIONS, accent=self.accent, size=13)
        self._menus = []            # 這一幀畫出來的下拉選單:[(選單, 設定名稱)]
        self._last_press = (-10000, None)
        self._ime_rect = None
        self._note_popup = None
        self.pending = None         # 選好、還沒放到頁面上的圖片或簽名:dict(kind, image, pixels)
        self._paragraph_cache = {}  # (來源, 第幾頁) → 那一頁的段落
        self._sources = {}          # 來源檔 → pikepdf 開啟的檔案(取出原字型用)
        self.signatures = SignaturePanel(page, self.accent)

    # ------------------------------------------------------------ 資料

    @property
    def pages(self):
        return self.page.pages

    def scale(self):
        return self.page.view_scale()

    def index_of(self, uid):
        return next((i for i, page in enumerate(self.pages) if page.uid == uid), None)

    def mapper(self, index):
        ox, oy = self.page.view_offset
        for i, rect in self.page.layout:
            if i == index and i < len(self.pages):
                return annot_view.Mapper(self.pages[index], rect.move(ox, oy), self.scale())
        return None

    def page_at(self, pos):
        for index, rect in self.page.page_hits:
            if rect.collidepoint(pos):
                return index
        return None

    def find(self, pair):
        if pair is None:
            return None
        index = self.index_of(pair[0])
        if index is None:
            return None
        annot = next((a for a in self.pages[index].annots if a.uid == pair[1]), None)
        return (index, annot) if annot is not None else None

    def selected_annot(self):
        return self.find(self.selected)

    def fit(self, annot):
        """文字框、改字的高度跟著內容走;改字打得比框長時把框加寬,不自動換行(換行會疊到下一行的字)。"""
        if annot.kind not in annots.TEXTS:
            return annot
        face, result = pdfwrite.text_layout(annot)
        if result is None:
            return annot
        if annot.kind == "replace" and not annot.wrap:
            natural = fonts.layout(annot.text, face, annot.font_size, None, fonts.CATALOG.fallback()).width
            need = natural + annots.TEXT_PAD * 2 + annot.width * 2 + 1
            if annot.box[2] - annot.box[0] < need:
                annot = replace(annot, box=(annot.box[0], annot.box[1], annot.box[0] + need, annot.box[3]))
                face, result = pdfwrite.text_layout(annot)
        x0, y0, x1, _ = annot.box
        return replace(annot, box=(x0, y0, x1, y0 + pdfwrite.textbox_height(annot, result)))

    # ------------------------------------------------------------ 修改

    def _set(self, index, items, message, select=None):
        if self.page._change(model.set_annots(self.pages, index, items), message=message) and select is not None:
            self.selected = (self.pages[index].uid, select)

    def add(self, index, annot):
        self._set(index, self.pages[index].annots + (annot,), f"已新增{annots.LABELS[annot.kind]}", annot.uid)

    def replace_annot(self, index, old, new, message):
        self._set(index, tuple(new if a.uid == old.uid else a for a in self.pages[index].annots), message, new.uid)

    def delete_selected(self):
        found = self.selected_annot()
        if found is None:
            return False
        index, annot = found
        self.selected = None
        self._set(index, tuple(a for a in self.pages[index].annots if a.uid != annot.uid),
                  f"已刪除{annots.LABELS[annot.kind]}")
        return True

    def target(self):
        """設定列要調整的對象:(種類, 目前的值);沒有對象時回傳 (None, None)。"""
        if self.editing is not None:
            annot = self.editing["annot"]
            return annot.kind, _values(annot)
        found = self.selected_annot()
        if found is not None and annots.editable(found[1]):
            return found[1].kind, _values(found[1])
        if self.tool != "select":
            return self.tool, self.settings[self.tool]
        return None, None

    def apply_setting(self, **values):
        kind, current = self.target()
        if kind is None:
            return
        if kind in ("rect", "ellipse"):
            after = dict(current, **values)
            if not after["width"] and not after["background"]:
                self.page.notify("方框、圓形至少要留下線條或填滿其中一種", theme.WARN)
                return
        self.settings[kind].update({key: value for key, value in values.items()
                                    if key == "color" or key in self.settings[kind]})
        if self.editing is not None:
            self.editing["annot"] = self.fit(annots.styled(self.editing["annot"], **values))
            return
        found = self.selected_annot()
        if found is not None:
            index, annot = found
            new = self.fit(annots.styled(annot, **values))
            if new != annot:
                self.replace_annot(index, annot, new, f"已修改{annots.LABELS[annot.kind]}")

    def style(self, kind):
        return dict(self.settings[kind])

    def textbox_font(self):
        face = fonts.CATALOG.resolve(self.settings["textbox"]["font"])
        if face is None:
            self.page.notify("找不到可以用的字型，請先選擇或下載字型", theme.WARN)
            self.open_picker()
            return None
        self.settings["textbox"]["font"] = face.id
        return face.id

    def open_picker(self):
        _, values = self.target()
        current = (values or {}).get("font") or self.settings["textbox"]["font"]

        def pick(face_id):
            if self.target()[0] in annots.TEXTS:
                self.apply_setting(font=face_id)
            else:
                self.settings["textbox"]["font"] = face_id

        self.picker.open(fonts.CATALOG.resolve(current).id if fonts.CATALOG.resolve(current) else current, pick)

    def set_tool(self, key):
        self.finish_editing()
        self.cancel_action()
        if key == "image":
            self.choose_image()
            return
        if key == "signature":
            self.signatures.open(self.use_signature)
            return
        self.tool = key
        self.pending = None
        if key != "select":
            self.selected = None

    def choose_image(self):
        """選一張圖片,接著在頁面上點一下或拖曳放置。"""
        chosen = winfile.ask_open("插入圖片", IMAGE_FILTER)
        if chosen is None:
            return
        try:
            data, pixels = signature.load_image(chosen)
        except Exception:
            self.page.notify(f"「{Path(chosen).name}」讀不到圖片，可能不是支援的格式或已損壞", theme.DANGER)
            return
        self._hold("image", data, pixels)

    def use_signature(self, data, pixels):
        self._hold("signature", data, pixels)

    def _hold(self, kind, data, pixels):
        self.tool = kind
        self.selected = None
        self.pending = dict(kind=kind, image=data, pixels=pixels)

    def cancel_action(self):
        action, self.action = self.action, None
        if action is not None and action.get("lookup") is not None:
            action["lookup"].close()

    # ------------------------------------------------------------ 打字

    def start_editing(self, index, annot, new=False):
        self.finish_editing()
        self.cancel_action()
        index = self.index_of(self.pages[index].uid) if index < len(self.pages) else None
        if index is None:
            return
        self.editing = dict(page_uid=self.pages[index].uid, annot=self.fit(annot), original=annot,
                            editor=TextEditor(annot.text), new=new)
        self.selected = (self.pages[index].uid, annot.uid)
        self.page.page_input.blur()
        pygame.key.start_text_input()
        self._ime_rect = None

    def finish_editing(self):
        editing, self.editing = self.editing, None
        if editing is None:
            return
        pygame.key.stop_text_input()
        index = self.index_of(editing["page_uid"])
        if index is None:
            return
        text = editing["editor"].text
        annot = self.fit(replace(editing["annot"], text=text))
        pair = (editing["page_uid"], annot.uid)
        if annot.kind == "replace" and editing["new"] and text == editing["original"].text:
            self.selected = None        # 選了字卻沒有改:當作沒做
            return
        if annot.kind == "textbox" and not text.strip():
            if not editing["new"] and self.find(pair) is not None:
                self.selected = pair
                self.delete_selected()
            self.selected = None
            return
        if editing["new"]:
            self.add(index, annot)
        elif self.find(pair) is not None and annot != editing["original"]:
            self.replace_annot(index, editing["original"], annot, f"已修改{annots.LABELS[annot.kind]}")

    def _edit_layout(self, shown=False):
        editor = self.editing["editor"]
        text = editor.shown() if shown else editor.text
        annot = replace(self.editing["annot"], text=text)
        if annot.kind in annots.TEXTS:
            return pdfwrite.text_layout(annot)[1]
        return simple_layout(text, theme.font(14), NOTE_POPUP_W - 24)

    def _edit_index(self, pos, inside_only):
        editing = self.editing
        annot = editing["annot"]
        if annot.kind in annots.TEXTS:
            index = self.index_of(editing["page_uid"])
            mapper = self.mapper(index) if index is not None else None
            layout = self._edit_layout()
            if mapper is None or layout is None:
                return None
            live = self.fit(replace(annot, text=editing["editor"].text))
            point = mapper.to_page(pos)
            if inside_only and not annots.hit(live, point, 4 / self.scale()):
                return None
            inset = annots.TEXT_PAD + live.width
            return layout.index_at(point[0] - live.box[0] - inset, point[1] - live.box[1] - inset)
        if self._note_popup is None:
            return None
        rect, origin = self._note_popup
        if inside_only and not rect.collidepoint(pos):
            return None
        return self._edit_layout().index_at(pos[0] - origin[0], pos[1] - origin[1])

    def _handle_editing(self, event, pos):
        editor = self.editing["editor"]
        if event.type == pygame.KEYDOWN:
            if not editor.composition:
                if event.key == pygame.K_ESCAPE:
                    self.finish_editing()
                    return True
                if event.mod & pygame.KMOD_CTRL and event.key in (pygame.K_s, pygame.K_z, pygame.K_y):
                    self.finish_editing()
                    return False
            editor.handle(event, self._edit_layout())
            return True
        if event.type in (pygame.TEXTINPUT, pygame.TEXTEDITING):
            editor.handle(event, None)
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            index = self._edit_index(pos, inside_only=True)
            if index is not None:
                editor.click(index, bool(pygame.key.get_mods() & pygame.KMOD_SHIFT))
                return True
            if self.bar_rect.collidepoint(pos) or self.palette.is_open \
                    or any(menu.is_open for menu, _ in self._menus):
                return False
            self.finish_editing()
            return False
        if event.type == pygame.MOUSEMOTION and editor.dragging:
            index = self._edit_index(pos, inside_only=False)
            if index is not None:
                editor.drag_to(index)
            return True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and editor.dragging:
            editor.release()
            return True
        return False

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, pos):
        """回傳 True 代表事件被註解工具用掉了。"""
        if self.palette.handle_event(event, pos):
            return True
        if self.editing is not None and self._handle_editing(event, pos):
            return True
        if self._handle_menus(event, pos):
            return True
        if event.type == pygame.KEYDOWN:
            return self._handle_key(event)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.bar_rect.collidepoint(pos):
                self._click_bar(pos)
                return True
            area = self.page.view_rect
            if area.collidepoint(pos) and pos[0] < area.right - 12 and pos[1] < area.bottom - 8:
                return self._press(pos)
            return False
        if self.action is not None:
            if event.type == pygame.MOUSEMOTION:
                self._drag(pos)
                return True
            if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self._release(pos)
                return True
        return False

    def _handle_menus(self, event, pos):
        for menu, key in self._menus:
            if menu.is_open or (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and menu.enabled
                                and menu.rect.collidepoint(pos)):
                before = menu.index
                if menu.handle(event, pos):
                    if menu.index != before:
                        self.apply_setting(**{key: float(menu.value)})
                    return True
        return False

    def _click_bar(self, pos):
        for key, rect in self.tool_rects:
            if rect.collidepoint(pos):
                self.set_tool(key)
                return
        for key, rect, value, allow_none in self.color_buttons:
            if rect.collidepoint(pos):
                self._palette_key = key
                self.palette.open(rect, value, lambda color, key=key: self.apply_setting(**{key: color}),
                                  allow_none=allow_none)
                return
        if self.font_rect.collidepoint(pos):
            self.open_picker()

    def _handle_key(self, event):
        key = event.key
        if key == pygame.K_ESCAPE:
            if self.action is not None:
                self.cancel_action()
            elif self.selected is not None:
                self.selected = None
            elif self.tool != "select":
                self.set_tool("select")
            else:
                return False
            return True
        found = self.selected_annot()
        if found is None or self.action is not None:
            return False
        index, annot = found
        if key in (pygame.K_DELETE, pygame.K_BACKSPACE):
            self.delete_selected()
            return True
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER) and annot.kind in ("textbox", "note", "replace"):
            self.start_editing(index, annot)
            return True
        arrows = {pygame.K_LEFT: (-1, 0), pygame.K_RIGHT: (1, 0), pygame.K_UP: (0, -1), pygame.K_DOWN: (0, 1)}
        if key in arrows and annots.editable(annot) and not event.mod & pygame.KMOD_CTRL:
            step = 10 if event.mod & pygame.KMOD_SHIFT else 1
            ref = self.pages[index]
            dx, dy = arrows[key]
            start = geometry.unshown((0, 0), ref.size, ref.rotation)
            end = geometry.unshown((dx * step, dy * step), ref.size, ref.rotation)
            self.replace_annot(index, annot, annots.moved(annot, end[0] - start[0], end[1] - start[1]),
                               f"已移動{annots.LABELS[annot.kind]}")
            return True
        return False

    def _annot_at(self, index, point):
        tolerance = 5 / self.scale()
        for annot in reversed(self.pages[index].annots):
            if annots.hit(annot, point, tolerance):
                return annot
        return None

    def _press(self, pos):
        now = pygame.time.get_ticks()
        index = self.page_at(pos)
        if self.tool == "select":
            found = self.selected_annot()
            if found is not None and annots.editable(found[1]):
                mapper = self.mapper(found[0])
                handle = annot_view.handle_at(mapper, found[1], pos) if mapper is not None else None
                if handle is not None:
                    self.action = dict(type="resize", index=found[0], annot=found[1], handle=handle, preview=found[1])
                    return True
            if index is None:
                self.selected = None
                return False
            point = self.mapper(index).to_page(pos)
            annot = self._annot_at(index, point)
            if annot is None:
                self.selected = None
                return False
            pair = (self.pages[index].uid, annot.uid)
            double = self._last_press[1] == pair and now - self._last_press[0] < DOUBLE_CLICK_MS
            self._last_press = (now, pair)
            self.selected = pair
            if double and annot.kind in ("textbox", "note", "replace"):
                self.start_editing(index, annot)
            elif annots.editable(annot):
                self.action = dict(type="move", index=index, annot=annot, start=point, preview=annot, moved=False)
            return True
        if index is None:
            return True
        point = self.mapper(index).to_page(pos)
        self.selected = None
        if self.tool == "replace":
            tolerance = 3 / self.scale()
            done = next((a for a in reversed(self.pages[index].annots)
                         if a.kind == "replace" and annots.hit(a, point, tolerance)), None)
            if done is not None:            # 已經改過的段落(還沒存檔):接著編輯它,不要從頁面上的舊字重來
                self.start_editing(index, done)
                return True
        if self.tool in annots.MARKUP or self.tool in ("replace", "redact"):
            self._press_markup(index, point)
        elif self.tool in annots.IMAGES and self.pending is None:
            self.set_tool(self.tool)
        elif self.tool == "note":
            width, height = self.pages[index].size
            size = annots.NOTE_SIZE
            x = max(0.0, min(width - size, point[0] - size / 2))
            y = max(0.0, min(height - size, point[1] - size / 2))
            self.start_editing(index, annots.create("note", box=(x, y, x + size, y + size), **self.style("note")),
                               new=True)
        else:
            self.action = dict(type="create", kind=self.tool, index=index, start=point, current=point, stroke=[point])
        return True

    def _press_markup(self, index, point):
        ref = self.pages[index]
        lookup, start = None, None
        doc = self.page.docs.get(ref.source) if ref.kind == "pdf" else None
        if doc is not None:
            lookup = pdfium.TextLookup(doc, ref.index)
            start = lookup.index_at(*geometry.apply(geometry.ref_to_user(ref), point))
        if start is None:
            if lookup is not None:
                lookup.close()
            if self.tool in ("highlight", "redact"):
                # 沒有文字的地方(例如掃描的頁面、照片)改成拖曳出一塊範圍
                self.action = dict(type="create", kind=self.tool, index=index, start=point, current=point,
                                   stroke=[point])
            elif self.tool == "replace":
                self.page.notify("這裡沒有可以修改的文字；掃描的頁面可以用「文字框」加上背景色蓋住再打字", theme.WARN)
            else:
                self.page.notify("這裡沒有可以選取的文字；底線、刪除線要在文字上拖曳", theme.WARN)
            return
        self.action = dict(type="markup", index=index, lookup=lookup, start=start, end=start, rects=(), moved=False,
                           point=point)

    def _update_markup(self, point, action=None):
        action = action or self.action
        ref = self.pages[action["index"]]
        end = action["lookup"].index_at(*geometry.apply(geometry.ref_to_user(ref), point))
        if end is None:
            return
        action["end"] = end
        to_page = geometry.ref_from_user(ref)
        # 改字用自己算的範圍(PDFium 的字框在某些字型會大很多,會把旁邊的字一起蓋掉)
        found = action["lookup"].line_boxes(action["start"], end) if self.tool in ("replace", "redact") \
            else action["lookup"].rects(action["start"], end)
        action["rects"] = tuple(geometry.transform_box(to_page, rect) for rect in found)

    @staticmethod
    def _snap(start, point):
        dx, dy = point[0] - start[0], point[1] - start[1]
        length = math.hypot(dx, dy)
        angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
        return start[0] + length * math.cos(angle), start[1] + length * math.sin(angle)

    def _drag(self, pos):
        action = self.action
        mapper = self.mapper(action["index"])
        if mapper is None:
            return
        point = mapper.to_page(pos)
        kind = action["type"]
        if kind == "move":
            dx, dy = point[0] - action["start"][0], point[1] - action["start"][1]
            if math.hypot(dx, dy) * self.scale() >= 3:
                action["moved"] = True
            if action["moved"]:
                action["preview"] = annots.moved(action["annot"], dx, dy)
        elif kind == "resize":
            action["preview"] = self.fit(annots.resized(action["annot"], action["handle"], point))
        elif kind == "markup":
            action["moved"] = True
            action["point"] = point
            self._update_markup(point)
        else:
            if action["kind"] in ("line", "arrow") and pygame.key.get_mods() & pygame.KMOD_SHIFT:
                point = self._snap(action["start"], point)
            action["current"] = point
            last = action["stroke"][-1]
            if action["kind"] == "ink" and math.hypot(point[0] - last[0], point[1] - last[1]) * self.scale() >= 2:
                action["stroke"].append(point)

    def _release(self, pos):
        if self.action["type"] == "create":
            self._drag(pos)
        action, self.action = self.action, None
        index = action["index"]
        if index >= len(self.pages):
            return
        kind = action["type"]
        if kind in ("move", "resize"):
            label = annots.LABELS[action["annot"].kind]
            if action["preview"] != action["annot"] and (kind == "resize" or action["moved"]):
                message = f"已移動{label}" if kind == "move" else f"已調整{label}大小"
                self.replace_annot(index, action["annot"], action["preview"], message)
        elif kind == "markup":
            if self.tool == "replace":
                made = self._make_paragraph(index, action) if not action["moved"] else None
                if made is None and not action["moved"]:
                    self._update_markup(action["point"], action)
                if made is None:
                    made = self._make_replace(index, action) if action["rects"] else None
                action["lookup"].close()
                if made is not None:
                    self.start_editing(index, made, new=True)
                return
            action["lookup"].close()
            if action["moved"] and action["rects"]:
                self.add(index, annots.create(self.tool, rects=action["rects"], **self.style(self.tool)))
        else:
            annot = self._created(action, final=True)
            if annot is None:
                return
            if annot.kind == "textbox":
                self.start_editing(index, annot, new=True)
            else:
                self.add(index, annot)
                if annot.kind in annots.IMAGES:
                    self.tool, self.pending = "select", None      # 放好後回到選取,可以直接移動、改大小

    def _make_replace(self, index, action):
        """選好的原字 → 改字:字級、顏色、字型盡量和原字一樣,底色取原字周圍的顏色,新文字的底線對齊原字。"""
        lookup, ref = action["lookup"], self.pages[index]
        first, last = sorted((action["start"], action["end"]))
        text = lookup.text_of(first, last)
        size, color, _, _, _, baseline = lookup.char_style(first)
        size = round(size * 2) / 2 or 12.0
        chosen = self._text_fonts(ref, lookup, [c for c in paragraphs.read_chars(lookup) if first <= c.index <= last])
        if chosen is None:
            self.page.notify("找不到可以用的字型，請先選擇或下載字型", theme.WARN)
            return None
        rects = action["rects"]
        x0 = min(r[0] for r in rects)
        x1 = max(r[2] for r in rects)
        # 原字的底線換成頁面座標(只取第一行的 y)
        first_line = geometry.apply(geometry.ref_from_user(ref), (0.0, baseline))[1] \
            if ref.base_rotation % 180 == 0 else min(r[1] for r in rects) + size * 0.88
        top = first_line - annots.TEXT_PAD - size * fonts.BASELINE
        box = (x0 - annots.TEXT_PAD, top, max(x1, x0 + size) + annots.TEXT_PAD + 1, top + 10)
        style = dict(self.style("replace"), font_size=size, color=tuple(color), **chosen)
        style["background"] = self._paper_color(index, rects) or style["background"]
        return self.fit(annots.create("replace", rects=rects, box=box, text=text, **style))

    # ------------------------------------------------------------ 整段修改

    def _text_fonts(self, ref, lookup, chars):
        """這些字要用的字型設定:中文字、英數字各自沿用原檔字型(和 Word 一樣分開);回傳 dict,找不到字型時回傳 None。"""
        found = paragraphs.script_fonts(chars)
        faces = {}
        for script, (name, sample) in found.items():
            _, _, _, serif, bold, _ = lookup.char_style(sample)
            faces[script] = self._fonts_for(ref, name, serif, bold, script == "cjk")
        main = faces.get("cjk") or faces.get("latin")
        if main is None or main[0] is None:
            return None
        style = dict(font=main[0].id, fallback=main[1])
        if "cjk" in faces and "latin" in faces and faces["latin"][0] is not None:
            style.update(latin=faces["latin"][0].id, latin_fallback=faces["latin"][1])
        return style

    def _fonts_for(self, ref, font_name, serif, bold, cjk):
        """(主字型, 補字字型代號):盡量用 PDF 裡的原字型;原字型只有原檔用到的字,沒有的字用相近的字型補。"""
        matched = fonts.match_pdf_font(font_name, serif, bold, cjk=cjk)
        original = None
        pdf = self._source_pdf(ref)
        if pdf is not None:
            original = pdffonts.face_for(ref.source, pdf, ref.index, font_name)
        if original is not None:
            return original, (matched.id if matched is not None else "")
        return matched, ""

    def _source_pdf(self, ref):
        if ref.kind != "pdf":
            return None
        if ref.source not in self._sources:
            import io

            import pikepdf

            try:
                data = self.page.data.get(ref.source)
                self._sources[ref.source] = pikepdf.open(io.BytesIO(data) if data is not None else ref.source,
                                                         password=self.page.passwords.get(ref.source, ""))
            except Exception:
                self._sources[ref.source] = None
        return self._sources[ref.source]

    def paragraphs_of(self, index):
        ref = self.pages[index]
        doc = self.page.docs.get(ref.source) if ref.kind == "pdf" else None
        if doc is None:
            return []
        key = (ref.source, ref.index)
        if key not in self._paragraph_cache:
            lookup = pdfium.TextLookup(doc, ref.index)
            try:
                self._paragraph_cache[key] = paragraphs.find_paragraphs(lookup)
            finally:
                lookup.close()
        return self._paragraph_cache[key]

    def paragraph_at(self, index, point):
        """頁面座標的這個點落在哪一段文字上。"""
        ref = self.pages[index]
        if ref.base_rotation % 360:
            return None             # 本身轉過的頁面,文字方向和頁面不同,只提供拖曳選字
        x, y = geometry.apply(geometry.ref_to_user(ref), point)
        for paragraph in self.paragraphs_of(index):
            if any(x0 - 1 <= x <= x1 + 1 and y0 - 1 <= y <= y1 + 1 for x0, y0, x1, y1 in paragraph.boxes()):
                return paragraph
        return None

    def _make_paragraph(self, index, action):
        """點一下文字:整段變成可以修改,保留對齊方式、縮排、行距與原字型。"""
        ref = self.pages[index]
        paragraph = self.paragraph_at(index, action["point"])
        if paragraph is None:
            return None
        to_page = geometry.ref_from_user(ref)
        rects = tuple(geometry.transform_box(to_page, box) for box in paragraph.boxes())
        _, color = paragraph.style()
        size = round(paragraph.size * 2) / 2 or 12.0
        chosen = self._text_fonts(ref, action["lookup"], paragraph.chars)
        if chosen is None:
            self.page.notify("找不到可以用的字型，請先選擇或下載字型", theme.WARN)
            return None
        first = paragraph.lines[0]
        left, baseline = geometry.apply(to_page, (paragraph.left, first.baseline))
        right = geometry.apply(to_page, (paragraph.right, first.baseline))[0]
        top = baseline - annots.TEXT_PAD - size * fonts.BASELINE
        # 右邊多留一點:換用的補字字型可能寬一點點,不要為了差一點就多折一行
        box = (left - annots.TEXT_PAD, top, right + annots.TEXT_PAD + size * 0.05, top + 10)
        style = dict(self.style("replace"), font_size=size, color=tuple(color), **chosen)
        style["background"] = self._paper_color(index, rects) or style["background"]
        return self.fit(annots.create("replace", rects=rects, box=box, text=paragraph.text,
                                      align=paragraph.align, line_height=paragraph.pitch, offsets=paragraph.offsets,
                                      wrap=len(paragraph.lines) > 1, **style))    # 只有一行的(標題、項目)加字時往右延伸

    def _paper_color(self, index, rects):
        """原字周圍最常見的顏色(大多是紙的白色,有底色的表格就是那個底色)。"""
        ref = self.pages[index]
        doc = self.page.docs.get(ref.source) if ref.kind == "pdf" else None
        if doc is None:
            return None
        x0 = max(0.0, min(r[0] for r in rects) - 2)
        y0 = max(0.0, min(r[1] for r in rects) - 2)
        x1 = min(ref.size[0], max(r[2] for r in rects) + 2)
        y1 = min(ref.size[1], max(r[3] for r in rects) + 2)
        try:
            image = pdfium.render(doc, ref.index, 2.0, crop=(x0, ref.size[1] - y1, ref.size[0] - x1, y0),
                                  hidden=annots.hidden_origins(ref))
        except Exception:
            return None
        colors = image.getcolors(image.width * image.height)
        return max(colors)[1] if colors else None

    def _created(self, action, final=False):
        """拖曳出來的新註解;final 為 False 時只是畫面預覽。"""
        kind = action["kind"]
        (x0, y0), (x1, y1) = action["start"], action["current"]
        scale = self.scale()
        box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        big_enough = (box[2] - box[0]) * scale >= 4 and (box[3] - box[1]) * scale >= 4
        style = self.style(kind)
        make = annots.create if final else annots.Annot
        if not final:
            style = dict(style, uid=0)
        if kind in ("highlight", "redact"):
            return make(kind, rects=(box,), **style) if big_enough else None
        if kind in ("line", "arrow"):
            return make(kind, points=(action["start"], action["current"]), **style) \
                if math.hypot(x1 - x0, y1 - y0) * scale >= 6 else None
        if kind in ("rect", "ellipse"):
            return make(kind, box=box, **style) if big_enough else None
        if kind == "ink":
            return make("ink", points=(tuple(action["stroke"]),), **style)
        if kind in annots.IMAGES and self.pending is not None:
            return make(kind, box=self._image_box(action, big_enough), image=self.pending["image"],
                        pixels=self.pending["pixels"], **style)
        if kind == "textbox" and final:
            font = self.textbox_font()
            if font is None:
                return None
            style["font"] = font
            page_w = self.pages[action["index"]].size[0]
            if abs(x1 - x0) * scale >= 12:
                left, right, top = box[0], box[2], box[1]
            else:
                left = x0
                right = min(page_w - 4, left + TEXTBOX_W)
                if right - left < TEXTBOX_W / 2:
                    left = max(0.0, right - TEXTBOX_W)
                top = y0 - annots.TEXT_PAD - style["font_size"] * 0.65
            return self.fit(annots.create("textbox", box=(left, max(0.0, top), right, top + 10), **style))
        return None

    def _image_box(self, action, dragged):
        """圖片放置的範圍:有拖曳就照拖的寬度(維持比例),只點一下就用預設大小、以點的位置為中心。"""
        page_w, page_h = self.pages[action["index"]].size
        width_px, height_px = self.pending["pixels"]
        ratio = height_px / width_px if width_px else 1.0
        (x0, y0), (x1, y1) = action["start"], action["current"]
        if dragged:
            width = max(8.0, abs(x1 - x0))
            left = x0 if x1 >= x0 else x0 - width
            top = y0 if y1 >= y0 else y0 - width * ratio
            return left, top, left + width, top + width * ratio
        if self.pending["kind"] == "signature":
            width = SIGNATURE_W
        else:
            width = min(width_px * 72 / IMAGE_DPI, page_w * 0.5)
        width = min(width, page_w * 0.9, page_h * 0.9 / ratio if ratio else page_w)
        height = width * ratio
        left = max(0.0, min(page_w - width, x0 - width / 2))
        top = max(0.0, min(page_h - height, y0 - height / 2))
        return left, top, left + width, top + height

    # ------------------------------------------------------------ 每一幀

    def update(self):
        if self.editing is not None and self.index_of(self.editing["page_uid"]) is None:
            self.editing = None
            pygame.key.stop_text_input()
        if self.editing is None and self.selected is not None and self.find(self.selected) is None:
            self.selected = None

    def reset(self):
        self.cancel_action()
        if self.editing is not None:
            self.editing = None
            pygame.key.stop_text_input()
        self.selected = None
        self.tool = "select"
        self.pending = None
        self._paragraph_cache.clear()
        for pdf in self._sources.values():
            if pdf is not None:
                pdf.close()
        self._sources.clear()
        self.cache.clear()
        self.picker.close()
        self.signatures.close()
        self.palette.close()

    def deactivate(self):
        self.finish_editing()
        self.cancel_action()
        self.palette.close()
        for menu in (self.width_menu, self.opacity_menu, self.size_menu):
            menu.close()

    # ------------------------------------------------------------ 繪製:工具列

    def draw_bar(self, rect, mouse_pos):
        screen = self.page.screen
        self.bar_rect = rect
        rounded_panel(screen, rect, theme.PANEL, radius=0, alpha=240)
        pygame.draw.line(screen, theme.PANEL_EDGE, (rect.x, rect.bottom - 1), (rect.right, rect.bottom - 1))
        x, y = rect.x + 12, rect.y + 8
        self.tool_rects = []
        # 視窗窄時縮小按鈕左右的空白,14 個工具在最小的 960 寬也放得下
        widths = [theme.font(13).size(label)[0] for _, label in TOOLS]
        room = rect.width - 24 - 4 * (len(TOOLS) - 1) - sum(widths)
        pad = max(10, min(22, room // len(TOOLS)))
        for (key, label), text_w in zip(TOOLS, widths):
            box = pygame.Rect(x, y, text_w + pad, 30)
            active = key == self.tool
            if active:
                rounded_panel(screen, box, tuple(int(c * 0.3) for c in self.accent), radius=7, border=self.accent)
            elif box.collidepoint(mouse_pos):
                rounded_panel(screen, box, theme.PANEL_LIGHT, radius=7)
            draw_text(screen, label, box.center, 13, self.accent if active else theme.TEXT, center=True)
            self.tool_rects.append((key, box))
            x = box.right + 4
        self._draw_settings(pygame.Rect(rect.x + 12, rect.y + 46, rect.width - 24, 32), mouse_pos)

    def _label(self, text, x, cy, color=theme.TEXT_DIM):
        return draw_text(self.page.screen, text, (x, cy - theme.font(13).get_height() // 2), 13, color)

    @staticmethod
    def _sync(menu, base, value, label):
        key = f"{value:g}"
        options = base if key in (option[0] for option in base) else base + [(key, label(value))]
        if options != menu.options:
            menu.set_options(options)
        menu.set_value(key)

    def _draw_color_button(self, x, cy, label, key, value, allow_none, mouse_pos):
        screen = self.page.screen
        rect = pygame.Rect(x, cy - 15, theme.font(13).size(label)[0] + 54, 30)
        active = self.palette.is_open and self._palette_key == key
        hover = rect.collidepoint(mouse_pos)
        rounded_panel(screen, rect, theme.BG_DEEP, radius=8, alpha=220,
                      border=self.accent if active else (theme.TEXT_FAINT if hover else theme.PANEL_EDGE))
        self._label(label, rect.x + 10, cy, theme.TEXT)
        swatch = pygame.Rect(rect.right - 28, cy - 9, 18, 18)
        if value:
            pygame.draw.rect(screen, value, swatch, border_radius=4)
        else:
            pygame.draw.rect(screen, theme.PANEL, swatch, border_radius=4)
            pygame.draw.line(screen, theme.DANGER, (swatch.x + 3, swatch.bottom - 3), (swatch.right - 3, swatch.y + 3), 2)
        pygame.draw.rect(screen, theme.PANEL_EDGE, swatch, 1, border_radius=4)
        self.color_buttons.append((key, rect, tuple(value or ()), allow_none))
        return rect

    def _draw_settings(self, row, mouse_pos):
        screen = self.page.screen
        self.color_buttons, self._menus = [], []
        self.font_rect = pygame.Rect(0, 0, 0, 0)
        kind, values = self.target()
        cy = row.centery
        if kind is None:
            draw_text(screen, widgets.clip_text(HINT, 12, row.width), (row.x, cy - 8), 12, theme.TEXT_FAINT)
            return
        x = row.x
        if row.width >= 900:    # 視窗窄時省略種類名稱,選取框已經看得出是哪一個註解
            x = self._label(annots.LABELS[kind], row.x, cy, self.accent).right + 14
        for key, label, allow_none in COLOR_SLOTS.get(kind, [("color", "顏色", False)]):
            x = self._draw_color_button(x, cy, label, key, values[key], allow_none, mouse_pos).right + 6
        x += 6
        if kind in annots.TEXTS:
            face = fonts.CATALOG.resolve(values["font"])
            self.font_rect = pygame.Rect(x, cy - 15, 150, 30)
            hover = self.font_rect.collidepoint(mouse_pos)
            rounded_panel(screen, self.font_rect, theme.BG_DEEP, radius=8, alpha=220,
                          border=theme.TEXT_FAINT if hover else theme.PANEL_EDGE)
            name = face.name if face is not None else "選擇字型"
            self._label(widgets.clip_text(name, 13, 110), self.font_rect.x + 10, cy, theme.TEXT)
            draw_text(screen, "Aa", (self.font_rect.right - 12, cy), 12, theme.TEXT_FAINT, right=True)
            x = self.font_rect.right + 6
            self._sync(self.size_menu, SIZE_OPTIONS, values["font_size"], lambda v: f"{v:g} pt")
            self.size_menu.draw(screen, pygame.Rect(x, cy - 15, 84, 30), mouse_pos)
            self._menus.append((self.size_menu, "font_size"))
            x += 90
        if kind in annots.SHAPES or kind == "textbox":
            base = BORDER_OPTIONS if kind == "textbox" else \
                (SHAPE_WIDTH_OPTIONS if kind in ("rect", "ellipse") else WIDTH_OPTIONS)
            self._sync(self.width_menu, base, values["width"], lambda v: f"{v:g} pt")
            self.width_menu.enabled = True
            menu_w = 100 if kind in ("textbox", "rect", "ellipse") else 84
            if kind != "textbox":
                x = self._label("粗細", x, cy).right + 6
            self.width_menu.draw(screen, pygame.Rect(x, cy - 15, menu_w, 30), mouse_pos)
            self._menus.append((self.width_menu, "width"))
            x += menu_w + 12
        if kind != "note":
            if kind not in ("replace", "redact"):          # 塗黑一定要完全不透明
                x = self._label("透明度", x, cy).right + 6
                self._sync(self.opacity_menu, OPACITY_OPTIONS, values["opacity"],
                           lambda v: f"{round((1 - v) * 100)}%")
                self.opacity_menu.draw(screen, pygame.Rect(x, cy - 15, 76, 30), mouse_pos)
                self._menus.append((self.opacity_menu, "opacity"))
                x += 88
        hint = TOOL_HINTS.get(kind) if kind == self.tool and self.selected is None else None
        if hint and row.right - x > 120:
            draw_text(screen, widgets.clip_text(hint, 12, row.right - x), (x, cy - 8), 12, theme.TEXT_FAINT)

    def draw_menus(self, mouse_pos):
        for menu, _ in self._menus:
            menu.draw_menu(self.page.screen, mouse_pos)
        self.palette.draw(self.page.screen, mouse_pos)

    # ------------------------------------------------------------ 繪製:頁面上的註解

    def extra_hidden(self, ref):
        """正在移動、改大小或打字的原註解:頁面先不畫它,避免新舊兩個疊在一起。"""
        if not ref.originals:
            return ()
        candidates = []
        action = self.action
        if action is not None and action["type"] in ("move", "resize") and action["index"] < len(self.pages) \
                and self.pages[action["index"]].uid == ref.uid and action["preview"] != action["annot"]:
            candidates.append(action["annot"])
        if self.editing is not None and self.editing["page_uid"] == ref.uid:
            candidates.append(self.editing["original"])
        originals = set(ref.originals)
        return tuple(sorted(a.origin for a in candidates if a.origin >= 0 and a in originals))

    def _items(self, index, ref):
        originals = set(ref.originals)
        items = [a for a in ref.annots if not (a.origin >= 0 and a in originals)]
        swap = {}
        action = self.action
        if action is not None and action["type"] in ("move", "resize") and action["index"] == index \
                and action["preview"] != action["annot"]:
            swap[action["annot"].uid] = action["preview"]
        if self.editing is not None and self.editing["page_uid"] == ref.uid:
            live = self.fit(replace(self.editing["annot"], text=self.editing["editor"].shown()))
            swap[live.uid] = live
        result = [swap.pop(a.uid, a) for a in items]
        return result + list(swap.values())

    def draw_thumb(self, index, ref, box, scale):
        mapper = annot_view.Mapper(ref, box, scale)
        for annot in self._items(index, ref):
            annot_view.draw_annot(self.page.screen, mapper, annot, self.cache)

    def draw_page(self, index, rect, scale):
        screen = self.page.screen
        ref = self.pages[index]
        mapper = annot_view.Mapper(ref, rect, scale)
        items = self._items(index, ref)
        for annot in items:
            annot_view.draw_annot(screen, mapper, annot, self.cache)
        action = self.action
        if action is not None and action["index"] == index:
            if action["type"] == "markup":
                annot_view.draw_markup_preview(screen, mapper, self.tool, action["rects"],
                                               self.accent if self.tool in ("replace", "redact")
                                               else self.settings[self.tool]["color"])
            elif action["type"] == "create":
                if action["kind"] == "textbox":
                    (x0, y0), (x1, y1) = action["start"], action["current"]
                    pygame.draw.rect(screen, self.accent, mapper.box((min(x0, x1), min(y0, y1), max(x0, x1),
                                                                      max(y0, y1))), 1)
                else:
                    preview = self._created(action)
                    if preview is not None:
                        annot_view.draw_annot(screen, mapper, preview, self.cache)
        if self.tool == "replace" and action is None and self.editing is None:
            self._draw_paragraph_hover(index, mapper)
        found = self.selected_annot()
        if self.editing is not None and self.editing["page_uid"] == ref.uid:
            if self.editing["annot"].kind in annots.TEXTS:
                self._draw_text_editing(mapper)
        elif found is not None and found[0] == index:
            shown = next((a for a in items if a.uid == found[1].uid), found[1])
            annot_view.draw_selection(screen, mapper, shown, self.accent,
                                      handles=annots.editable(shown) and action is None)

    def _draw_paragraph_hover(self, index, mapper):
        """改字工具:滑鼠移到文字上時框出會被修改的那一段。"""
        mouse = pygame.mouse.get_pos()
        if not mapper.rect.collidepoint(mouse) or not self.page.view_rect.collidepoint(mouse):
            return
        point = mapper.to_page(mouse)
        done = next((a for a in reversed(self.pages[index].annots)
                     if a.kind == "replace" and annots.hit(a, point, 3 / mapper.scale)), None)
        if done is not None:
            pygame.draw.rect(self.page.screen, self.accent, mapper.box(annots.bounds(done)).inflate(6, 6), 1,
                             border_radius=3)
            return
        paragraph = self.paragraph_at(index, point)
        if paragraph is None:
            return
        box = geometry.transform_box(geometry.ref_from_user(self.pages[index]), paragraph.bounds())
        rect = mapper.box(box).inflate(6, 6)
        pygame.draw.rect(self.page.screen, self.accent, rect, 1, border_radius=3)

    def _draw_caret(self, a, b, layout_rows):
        screen = self.page.screen
        if (pygame.time.get_ticks() // 530) % 2 == 0:
            pygame.draw.line(screen, self.accent, a, b, 2)
        rect = pygame.Rect(round(min(a[0], b[0])), round(min(a[1], b[1])), 2,
                           max(4, round(abs(b[1] - a[1]) + abs(b[0] - a[0]))))
        if rect != self._ime_rect:
            self._ime_rect = rect
            pygame.key.set_text_input_rect(rect)     # 讓輸入法的選字清單出現在游標旁邊

    def _draw_text_editing(self, mapper):
        screen = self.page.screen
        editor = self.editing["editor"]
        live = self.fit(replace(self.editing["annot"], text=editor.shown()))
        _, layout = pdfwrite.text_layout(live)
        pygame.draw.rect(screen, self.accent, mapper.box(live.box).inflate(6, 6), 1)
        if layout is None:
            return
        inset = annots.TEXT_PAD + live.width
        left, top, line_h = live.box[0] + inset, live.box[1] + inset, layout.line_height

        def segment(row, start, end):
            line = layout.lines[row]
            x0, x1 = line.xs[start - line.start], line.xs[end - line.start]
            return left + x0, top + row * line_h, left + x1, top + (row + 1) * line_h

        if editor.composition:
            start, end = editor.cursor, editor.cursor + len(editor.composition)
            for row, line in enumerate(layout.lines):
                a, b = max(start, line.start), min(end, line.end)
                if a < b:
                    x0, _, x1, y1 = segment(row, a, b)
                    pygame.draw.line(screen, self.accent, mapper.point((x0, y1 - 1)), mapper.point((x1, y1 - 1)), 1)
        else:
            start, end = editor.selection
            for row, line in enumerate(layout.lines):
                a, b = max(start, line.start), min(end, line.end)
                if a < b:
                    area = mapper.box(segment(row, a, b)).clip(screen.get_clip())
                    if area.width > 0 and area.height > 0:
                        patch = pygame.Surface(area.size, pygame.SRCALPHA)
                        patch.fill(self.accent + (90,))
                        screen.blit(patch, area.topleft)
        x, row = layout.caret(editor.caret_index())
        self._draw_caret(mapper.point((left + x, top + row * line_h)), mapper.point((left + x, top + (row + 1) * line_h)),
                         layout.lines)

    def draw_view_overlay(self):
        """便利貼的內容視窗:選取時顯示內容,打字時可以直接編輯。"""
        self._note_popup = None
        screen = self.page.screen
        editing = self.editing
        if editing is not None and editing["annot"].kind == "note":
            index = self.index_of(editing["page_uid"])
            note, editor = editing["annot"], editing["editor"]
            text = editor.shown()
        else:
            found = self.selected_annot()
            if found is None or found[1].kind != "note" or self.action is not None or not found[1].text:
                return
            index, note = found
            editor, text = None, note.text
        mapper = self.mapper(index) if index is not None else None
        if mapper is None:
            return
        font = theme.font(14)
        layout = simple_layout(text, font, NOTE_POPUP_W - 24)
        note_rect = mapper.box(note.box)
        height = 36 + len(layout.lines) * layout.line_height + (34 if editor is not None else 12)
        area = self.page.view_rect
        rect = pygame.Rect(note_rect.right + 10, note_rect.y, NOTE_POPUP_W, height)
        if rect.right > area.right - 14:
            rect.right = note_rect.x - 10
        rect.clamp_ip(area.inflate(-8, -8))
        rounded_panel(screen, rect, (52, 48, 36), radius=10, alpha=248, border=self.accent)
        draw_text(screen, "便利貼", (rect.x + 12, rect.y + 9), 13, theme.TEXT, bold=True)
        ox, oy = rect.x + 12, rect.y + 34
        for row, line in enumerate(layout.lines):
            draw_text(screen, text[line.start:line.end], (ox, oy + row * layout.line_height), 14, theme.TEXT)
        if editor is None:
            return
        self._note_popup = (rect, (ox, oy))
        if not text:
            draw_text(screen, "輸入便利貼內容", (ox, oy), 14, theme.TEXT_FAINT)
        draw_text(screen, "按 Esc 或點其他地方完成", (ox, rect.bottom - 24), 12, theme.TEXT_FAINT)
        if not editor.composition:
            start, end = editor.selection
            for row, line in enumerate(layout.lines):
                a, b = max(start, line.start), min(end, line.end)
                if a < b:
                    patch = pygame.Surface((max(1, round(line.xs[b - line.start] - line.xs[a - line.start])),
                                            layout.line_height), pygame.SRCALPHA)
                    patch.fill(self.accent + (90,))
                    screen.blit(patch, (ox + line.xs[a - line.start], oy + row * layout.line_height))
        x, row = layout.caret(editor.caret_index())
        self._draw_caret((ox + x, oy + row * layout.line_height), (ox + x, oy + (row + 1) * layout.line_height),
                         layout.lines)
