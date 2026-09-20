"""PDF 編輯器的標記、註解:主畫面上方的工具列、在頁面上新增與修改註解、直接在頁面上打字。

所有修改都放回頁面清單,和旋轉、刪除頁面共用同一條復原紀錄。
"""

import math
from dataclasses import replace

import pygame

from core import pdfium, theme, widgets
from core.widgets import Dropdown, draw_text, rounded_panel

from . import annot_view, annots, fonts, geometry, model, pdfwrite
from .font_picker import FontPicker
from .textarea import TextEditor, simple_layout

BAR_H = 86
TOOLS = [("select", "選取"), ("highlight", "螢光筆"), ("underline", "底線"), ("strike", "刪除線"), ("textbox", "文字框"),
         ("note", "便利貼"), ("line", "直線"), ("arrow", "箭頭"), ("rect", "方框"), ("ellipse", "圓形"), ("ink", "手繪")]
COLORS = [(255, 214, 0), (120, 220, 90), (80, 190, 255), (255, 120, 190), (230, 40, 40), (255, 140, 0),
          (40, 110, 230), (20, 20, 20)]
WIDTH_OPTIONS = [(f"{v:g}", f"{v:g} pt") for v in (1, 2, 3, 5, 8)]
BORDER_OPTIONS = [("0", "無外框")] + [(f"{v:g}", f"外框 {v:g} pt") for v in (1, 2, 3)]
OPACITY_OPTIONS = [("1", "0%"), ("0.75", "25%"), ("0.5", "50%"), ("0.3", "70%")]
SIZE_OPTIONS = [(f"{v:g}", f"{v:g} pt") for v in (8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 64, 72)]
DEFAULTS = {
    "highlight": dict(opacity=1.0), "underline": dict(opacity=1.0), "strike": dict(opacity=1.0),
    "textbox": dict(width=0.0, font="", font_size=14.0, opacity=1.0), "note": {},
    "line": dict(width=2.0, opacity=1.0), "arrow": dict(width=2.0, opacity=1.0),
    "rect": dict(width=2.0, opacity=1.0, fill=False), "ellipse": dict(width=2.0, opacity=1.0, fill=False),
    "ink": dict(width=2.0, opacity=1.0),
}
DOUBLE_CLICK_MS = 400
TEXTBOX_W = 200.0
NOTE_POPUP_W = 260
HINT = "選擇上方的工具後在頁面上拖曳；點選註解可以移動、改大小、改顏色，按 Delete 刪除，按兩下文字框或便利貼可以修改文字"


def _values(annot):
    return dict(color=annot.color, opacity=annot.opacity, width=annot.width, fill=annot.fill, font=annot.font,
                font_size=annot.font_size)


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
        self.bar_rect = pygame.Rect(0, 0, 0, 0)
        self.tool_rects, self.swatch_rects, self.fill_rects = [], [], []
        self.font_rect = pygame.Rect(0, 0, 0, 0)
        self.width_menu = Dropdown(WIDTH_OPTIONS, accent=self.accent, size=13)
        self.opacity_menu = Dropdown(OPACITY_OPTIONS, accent=self.accent, size=13)
        self.size_menu = Dropdown(SIZE_OPTIONS, accent=self.accent, size=13)
        self._menus = []            # 這一幀畫出來的下拉選單:[(選單, 設定名稱)]
        self._last_press = (-10000, None)
        self._ime_rect = None
        self._note_popup = None

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
        """文字框的高度跟著內容走。"""
        if annot.kind != "textbox":
            return annot
        _, result = pdfwrite.text_layout(annot)
        if result is None:
            return annot
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
        kind, _ = self.target()
        if kind is None:
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
            if self.target()[0] == "textbox":
                self.apply_setting(font=face_id)
            else:
                self.settings["textbox"]["font"] = face_id

        self.picker.open(fonts.CATALOG.resolve(current).id if fonts.CATALOG.resolve(current) else current, pick)

    def set_tool(self, key):
        self.finish_editing()
        self.cancel_action()
        self.tool = key
        if key != "select":
            self.selected = None

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
        if annot.kind == "textbox":
            return pdfwrite.text_layout(annot)[1]
        return simple_layout(text, theme.font(14), NOTE_POPUP_W - 24)

    def _edit_index(self, pos, inside_only):
        editing = self.editing
        annot = editing["annot"]
        if annot.kind == "textbox":
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
            if self.bar_rect.collidepoint(pos) or any(menu.is_open for menu, _ in self._menus):
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
        for color, rect in self.swatch_rects:
            if rect.collidepoint(pos):
                self.apply_setting(color=color)
                return
        for fill, rect in self.fill_rects:
            if rect.collidepoint(pos):
                self.apply_setting(fill=fill)
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
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER) and annot.kind in ("textbox", "note"):
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
            if double and annot.kind in ("textbox", "note"):
                self.start_editing(index, annot)
            elif annots.editable(annot):
                self.action = dict(type="move", index=index, annot=annot, start=point, preview=annot, moved=False)
            return True
        if index is None:
            return True
        point = self.mapper(index).to_page(pos)
        self.selected = None
        if self.tool in annots.MARKUP:
            self._press_markup(index, point)
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
            if self.tool == "highlight":
                # 沒有文字的地方(例如掃描的頁面)改成拖曳出一塊範圍
                self.action = dict(type="create", kind="highlight", index=index, start=point, current=point,
                                   stroke=[point])
            else:
                self.page.notify("這裡沒有可以選取的文字；底線、刪除線要在文字上拖曳", theme.WARN)
            return
        self.action = dict(type="markup", index=index, lookup=lookup, start=start, rects=(), moved=False)

    def _update_markup(self, point):
        action = self.action
        ref = self.pages[action["index"]]
        end = action["lookup"].index_at(*geometry.apply(geometry.ref_to_user(ref), point))
        if end is None:
            return
        to_page = geometry.ref_from_user(ref)
        action["rects"] = tuple(geometry.transform_box(to_page, rect) for rect in action["lookup"].rects(action["start"], end))

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
        if kind == "highlight":
            return make("highlight", rects=(box,), **style) if big_enough else None
        if kind in ("line", "arrow"):
            return make(kind, points=(action["start"], action["current"]), **style) \
                if math.hypot(x1 - x0, y1 - y0) * scale >= 6 else None
        if kind in ("rect", "ellipse"):
            return make(kind, box=box, **style) if big_enough else None
        if kind == "ink":
            return make("ink", points=(tuple(action["stroke"]),), **style)
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
        self.cache.clear()
        self.picker.close()

    def deactivate(self):
        self.finish_editing()
        self.cancel_action()
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
        for key, label in TOOLS:
            box = pygame.Rect(x, y, theme.font(13).size(label)[0] + 22, 30)
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

    def _draw_settings(self, row, mouse_pos):
        screen = self.page.screen
        self.swatch_rects, self.fill_rects, self._menus = [], [], []
        self.font_rect = pygame.Rect(0, 0, 0, 0)
        kind, values = self.target()
        cy = row.centery
        if kind is None:
            draw_text(screen, widgets.clip_text(HINT, 12, row.width), (row.x, cy - 8), 12, theme.TEXT_FAINT)
            return
        x = row.x
        if row.width >= 800:    # 視窗窄時省略種類名稱,選取框已經看得出是哪一個註解
            x = self._label(annots.LABELS[kind], row.x, cy, self.accent).right + 16
        palette = list(COLORS) if tuple(values["color"]) in COLORS else [tuple(values["color"])] + COLORS
        for color in palette:
            swatch = pygame.Rect(x, cy - 9, 18, 18)
            pygame.draw.rect(screen, color, swatch, border_radius=5)
            if tuple(values["color"]) == color:
                pygame.draw.rect(screen, self.accent, swatch.inflate(6, 6), 2, border_radius=7)
            else:
                pygame.draw.rect(screen, theme.PANEL_EDGE, swatch, 1, border_radius=5)
            self.swatch_rects.append((color, swatch))
            x = swatch.right + 5
        x += 8
        if kind in ("rect", "ellipse"):
            for fill, label in ((False, "外框"), (True, "填色")):
                box = pygame.Rect(x, cy - 14, 48, 28)
                active = bool(values["fill"]) == fill
                rounded_panel(screen, box, tuple(int(c * 0.3) for c in self.accent) if active else theme.BG_DEEP,
                              radius=7, border=self.accent if active else theme.PANEL_EDGE)
                draw_text(screen, label, box.center, 13, self.accent if active else theme.TEXT_DIM, center=True)
                self.fill_rects.append((fill, box))
                x = box.right + 4
            x += 8
        if kind == "textbox":
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
            base = BORDER_OPTIONS if kind == "textbox" else WIDTH_OPTIONS
            self._sync(self.width_menu, base, values["width"], lambda v: f"{v:g} pt")
            self.width_menu.enabled = not (kind in ("rect", "ellipse") and values["fill"])
            menu_w = 100 if kind == "textbox" else 84
            if kind != "textbox":
                x = self._label("粗細", x, cy).right + 6
            self.width_menu.draw(screen, pygame.Rect(x, cy - 15, menu_w, 30), mouse_pos)
            self._menus.append((self.width_menu, "width"))
            x += menu_w + 12
        if kind != "note":
            x = self._label("透明度", x, cy).right + 6
            self._sync(self.opacity_menu, OPACITY_OPTIONS, values["opacity"], lambda v: f"{round((1 - v) * 100)}%")
            self.opacity_menu.draw(screen, pygame.Rect(x, cy - 15, 76, 30), mouse_pos)
            self._menus.append((self.opacity_menu, "opacity"))

    def draw_menus(self, mouse_pos):
        for menu, _ in self._menus:
            menu.draw_menu(self.page.screen, mouse_pos)

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
                                               self.settings[self.tool]["color"])
            elif action["type"] == "create":
                if action["kind"] == "textbox":
                    (x0, y0), (x1, y1) = action["start"], action["current"]
                    pygame.draw.rect(screen, self.accent, mapper.box((min(x0, x1), min(y0, y1), max(x0, x1),
                                                                      max(y0, y1))), 1)
                else:
                    preview = self._created(action)
                    if preview is not None:
                        annot_view.draw_annot(screen, mapper, preview, self.cache)
        found = self.selected_annot()
        if self.editing is not None and self.editing["page_uid"] == ref.uid:
            if self.editing["annot"].kind == "textbox":
                self._draw_text_editing(mapper)
        elif found is not None and found[0] == index:
            shown = next((a for a in items if a.uid == found[1].uid), found[1])
            annot_view.draw_selection(screen, mapper, shown, self.accent,
                                      handles=annots.editable(shown) and action is None)

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
