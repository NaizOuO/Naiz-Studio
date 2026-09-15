"""PDF 編輯器(v1.11.0 檢視與頁面管理):左邊頁面縮圖,中間所有頁面連續捲動;可以排序、旋轉、刪除、插入、擷取頁面。"""

import threading
from pathlib import Path

import pygame

from core import deps, large_files, paths, theme, widgets, winfile
from core.drag_sort import DragSort, move_items
from core.plugins import Page
from core.scroll import ScrollView
from core.widgets import Button, Dropdown, TextInput, draw_text, rounded_panel

from . import model, ops
from .dialog import Dialog
from .render import MAX_PIXELS, PageRenderer

SIDEBAR_W = 196
THUMB_BOX = (128, 150)
THUMB_SLOT = THUMB_BOX[1] + 32
OPS_H = 176
STATUS_H = 46
MARGIN = 20
PAGE_GAP = 18
POINT_PX = 96 / 72          # 100% 時 1 點畫成多少像素(和一般閱讀器一樣以 96 DPI 顯示實際大小)
ZOOM_OPTIONS = [("fit_width", "適合寬度"), ("fit_page", "整頁"), ("0.5", "50%"), ("0.75", "75%"), ("1", "100%"),
                ("1.25", "125%"), ("1.5", "150%"), ("2", "200%"), ("3", "300%"), ("4", "400%")]
ZOOM_STEPS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
MIN_ZOOM, MAX_ZOOM = 0.1, 4.0
BLANK_SIZES = [("same", "和目前頁面一樣大", False), ("a4", "A4 直式", False), ("a4_land", "A4 橫式", False)]
PDF_FILTER = [("PDF 檔案", "*.pdf")]
MESSAGE_MS = 6000


def output_dir():
    return paths.OUTPUT_DIR / "editor"


class EditorPage(Page):
    def __init__(self, app, tool):
        super().__init__(app, tool)
        accent = tool.accent
        self.path = None
        self.docs = {}
        self.passwords = {}
        self.history = model.History()
        self.selected = set()
        self.anchor = None
        self.zoom_mode = "fit_width"
        self.zoom = 1.0
        self.scroll_x = 0
        self.message = None             # (文字, 顏色, 時間)
        self.task = None                # 背景工作:(執行緒, 說明文字)
        self._task_result = None
        self._pending_single = None
        self._drop_files = None
        self.view = ScrollView(accent=accent, wheel_step=60)
        self.thumb_view = ScrollView(accent=accent, wheel_step=THUMB_SLOT // 2, marquee=self)
        self.drag = DragSort(accent, on_drop=self._drop_pages)
        self.renderer = PageRenderer(self.docs)
        self.dialog = Dialog(lambda: self.screen, accent)
        self.layout = []                # [(頁面索引, 內容座標的範圍)]
        self.content_w = 0
        self.view_rect = pygame.Rect(0, 0, 0, 0)
        self.sidebar_rect = pygame.Rect(0, 0, 0, 0)
        self.thumb_area = pygame.Rect(0, 0, 0, 0)
        self.thumb_slots = []
        self.page_hits = []

        self.btn_open = Button("開啟", filled=False, size=13)
        self.btn_undo = Button("復原", filled=False, size=13)
        self.btn_redo = Button("重做", filled=False, size=13)
        self.btn_zoom_out = Button("－", filled=False, size=15)
        self.btn_zoom_in = Button("＋", filled=False, size=15)
        self.zoom_menu = Dropdown(ZOOM_OPTIONS, accent=accent, size=13)
        self.page_input = TextInput(accent=accent, size=13)
        self.btn_save = Button("儲存", accent=accent, size=13)
        self.btn_rotate_left = Button("左轉 90°", filled=False, size=13)
        self.btn_rotate_right = Button("右轉 90°", filled=False, size=13)
        self.btn_duplicate = Button("複製", filled=False, size=13)
        self.btn_delete = Button("刪除", filled=False, size=13)
        self.btn_blank = Button("空白頁", filled=False, size=13)
        self.btn_insert = Button("插入檔案", filled=False, size=13)
        self.btn_extract = Button("擷取選取的頁面", filled=False, size=13)
        self.btn_save_as = Button("選擇位置儲存", filled=False, size=13)
        self.btn_empty_open = Button("選擇 PDF 檔案", accent=accent, size=15)

    # ------------------------------------------------------------ 資料

    @property
    def pages(self):
        return self.history.pages

    @property
    def busy(self):
        return self.task is not None

    def selected_indexes(self):
        return [i for i, page in enumerate(self.pages) if page.uid in self.selected]

    def current_index(self):
        """主畫面中間那一頁。"""
        if not self.layout:
            return 0
        middle = self.view.scroll + self.view_rect.height / 2
        for index, rect in self.layout:
            if rect.bottom + PAGE_GAP / 2 >= middle:
                return index
        return self.layout[-1][0]

    def notify(self, text, color=theme.TEXT_DIM):
        self.message = (text, color, pygame.time.get_ticks())

    def has_unsaved(self):
        return self.path is not None and self.history.dirty

    def leave(self, proceed):
        """回首頁、關閉程式前呼叫;確定離開後關掉目前的文件,重新進來時是空白畫面,可以直接拖入新檔。"""
        def close_and_proceed():
            self.close_file()
            proceed()

        self.ask_unsaved(close_and_proceed)

    def ask_unsaved(self, proceed):
        """有未儲存的變更時先詢問(儲存、不儲存、取消),確定後才呼叫 proceed()。"""
        if not self.has_unsaved():
            proceed()
            return

        def choice(key, _):
            if key == "discard":
                proceed()
            elif key == "save":
                self.save(then=proceed)

        self.dialog.open("有尚未儲存的變更", [f"「{Path(self.path).name}」有尚未儲存的變更。",
                                          "選「不儲存」的話，這次的修改會遺失。"],
                         [("cancel", "取消", False), ("discard", "不儲存", False), ("save", "儲存", True)], choice)

    def _change(self, pages, select=None, message=None):
        if self.history.apply(pages):
            if select is not None:
                self.selected = {self.pages[i].uid for i in select}
            if message:
                self.notify(message)
            return True
        return False

    # ------------------------------------------------------------ 開啟

    def _large(self, path, proceed):
        size = Path(path).stat().st_size
        why = large_files.reason(size)
        self.app.large_files.confirm([(Path(path).name, why)] if why else [], proceed)

    def request_open(self, path):
        """開啟另一份 PDF;有未儲存的變更時先詢問。新檔開不了(例如密碼取消)時保留原本的文件。"""
        self.ask_unsaved(lambda: self._large(path, lambda: self.open_file(path)))

    def open_file(self, path, password=None, keep_view=False):
        path = Path(path)
        try:
            doc, pages = ops.open_pdf(path, password)
        except ops.PasswordRequired as exc:
            self._ask_password(path, lambda text: self.open_file(path, text, keep_view), str(exc) if password else "")
            return False
        except ops.Damaged as exc:
            self._ask_repair(path, str(exc))
            return False
        view_state = (self.zoom_mode, self.zoom, self.view.scroll, self.current_index()) if keep_view else None
        self.close_documents()
        self.docs[str(path)] = doc
        if password:
            self.passwords[str(path)] = password
        self.path = str(path)
        self.history.reset(pages)
        self.selected = {pages[0].uid}
        self.anchor = pages[0].uid
        self.thumb_view.scroll = 0
        self.scroll_x = 0
        if view_state:
            self.zoom_mode, self.zoom, self.view.scroll, index = view_state
            if index < len(pages):
                self.selected = {pages[index].uid}
        else:
            self.zoom_mode, self.view.scroll = "fit_width", 0
        return True

    def close_file(self):
        """關掉目前的文件,回到拖入檔案的空白畫面。"""
        self.close_documents()
        self.path = None
        self.history.reset([])
        self.selected, self.anchor = set(), None
        self.layout, self.page_hits, self.thumb_slots = [], [], []
        self.view.scroll = self.thumb_view.scroll = self.scroll_x = 0
        self.zoom_mode = "fit_width"
        self.message = None
        self.deactivate()

    def close_documents(self):
        self.renderer.clear()
        for doc in self.docs.values():
            try:
                from core import pdfium

                pdfium.close(doc)
            except Exception:
                pass
        self.docs.clear()
        self.passwords.clear()

    def _ask_password(self, path, retry, error=""):
        self.dialog.open("需要密碼", [f"「{path.name}」有設定密碼，輸入密碼才能開啟。",
                                   "儲存後的新檔不會保留密碼。"],
                         [("cancel", "取消", False), ("ok", "開啟", True)],
                         lambda key, text: retry(text) if key == "ok" else None, field="密碼", mask=True, error=error)

    def _ask_repair(self, path, reason):
        def choice(key, _):
            if key == "repair":
                self._run(f"修復「{path.name}」中...", lambda: ops.repair(path, output_dir()), self._repaired)

        self.dialog.open("檔案可能損壞", [f"「{path.name}」{reason}。",
                                     "可以嘗試修復：重建檔案的索引，救回還能讀取的頁面。",
                                     f"修復結果會另存到 output\\editor\\，不會改動原檔。"],
                         [("cancel", "取消", False), ("repair", "嘗試修復", True)], choice)

    def _repaired(self, result):
        out, count = result
        if self.open_file(out):
            self.notify(f"修復完成，救回 {count} 頁，已另存成「{out.name}」", theme.ACCENT)

    # ------------------------------------------------------------ 背景工作

    def _run(self, label, work, done):
        def target():
            try:
                self._task_result = ("ok", work(), done)
            except Exception as exc:
                self._task_result = ("error", exc, done)

        self._task_result = None
        thread = threading.Thread(target=target, daemon=True)
        self.task = (thread, label)
        thread.start()

    def update(self):
        mouse = pygame.mouse.get_pos()
        if self.task is not None and not self.task[0].is_alive():
            self.task = None
            status, value, done = self._task_result or ("error", RuntimeError("沒有結果"), None)
            if status == "ok":
                done(value)
            elif isinstance(value, (ops.Damaged, ops.PasswordRequired)):
                self.notify(str(value), theme.DANGER)
            else:
                self.notify(f"失敗：{value}", theme.DANGER)
        self.view.update(mouse)
        self.thumb_view.update(mouse)
        delta = self.drag.update(self.thumb_area)
        if delta:
            self.thumb_view.set_scroll(self.thumb_view.scroll + delta)

    # ------------------------------------------------------------ 頁面操作

    def _insert_at(self):
        indexes = self.selected_indexes()
        return (max(indexes) + 1) if indexes else self.current_index() + 1

    def rotate(self, degrees):
        indexes = self.selected_indexes()
        if indexes:
            self._change(model.rotate(self.pages, indexes, degrees),
                         message=f"已{'右' if degrees == 90 else '左'}轉 {len(indexes)} 頁")

    def delete(self):
        indexes = self.selected_indexes()
        if not indexes:
            return
        try:
            pages = model.delete(self.pages, indexes)
        except ValueError as exc:
            self.notify(str(exc), theme.WARN)
            return
        keep = min(min(indexes), len(pages) - 1)
        self._change(pages, select=[keep], message=f"已刪除 {len(indexes)} 頁")

    def duplicate(self):
        indexes = self.selected_indexes()
        if indexes:
            pages, copies = model.duplicate(self.pages, indexes)
            self._change(pages, select=copies, message=f"已複製 {len(indexes)} 頁")

    def insert_blank(self):
        if not self.pages:
            return

        def choice(key, _):
            if key == "cancel":
                return
            reference = self.pages[self.selected_indexes()[-1] if self.selected_indexes() else self.current_index()]
            size = {"same": reference.shown_size, "a4": model.A4, "a4_land": model.A4[::-1]}[key]
            pages, added = model.insert(self.pages, self._insert_at(), [ops.blank_ref(size)])
            self._change(pages, select=added, message="已插入空白頁")

        self.dialog.open("插入空白頁", ["空白頁會放在選取的頁面後面。"],
                         [("cancel", "取消", False)] + BLANK_SIZES, choice)

    def insert_files(self, files, at):
        """把 PDF(每一頁)和圖片(每張一頁)依序插入到 at 的位置;需要密碼的 PDF 會先詢問。"""
        files = [Path(f) for f in files]
        refs = []

        def step(i, password=None):
            if i == len(files):
                if refs:
                    pages, added = model.insert(self.pages, at, refs)
                    self._change(pages, select=added, message=f"已插入 {len(refs)} 頁")
                return
            path = files[i]
            suffix = path.suffix.lower()
            if suffix in ops.IMAGE_EXTS:
                try:
                    refs.append(ops.image_ref(path))
                except Exception:
                    self.notify(f"「{path.name}」不是能讀取的圖片，已略過", theme.WARN)
                step(i + 1)
            elif suffix == ".pdf":
                key = str(path)
                try:
                    if key in self.docs:
                        doc = self.docs[key]
                        from core import pdfium

                        added = [model.new_ref("pdf", source=key, index=n, size=pdfium.page_size(doc, n),
                                               base_rotation=pdfium.page_rotation(doc, n))
                                 for n in range(pdfium.page_count(doc))]
                    else:
                        doc, added = ops.open_pdf(path, password)
                        self.docs[key] = doc
                        if password:
                            self.passwords[key] = password
                    refs.extend(added)
                except ops.PasswordRequired as exc:
                    self._ask_password(path, lambda text: step(i, text), str(exc) if password else "")
                    return
                except ops.Damaged:
                    self.notify(f"「{path.name}」可能損壞，沒有插入；可以直接開啟它來嘗試修復", theme.WARN)
                step(i + 1)
            else:
                self.notify(f"「{path.name}」不是 PDF 或圖片，已略過", theme.WARN)
                step(i + 1)

        step(0)

    def extract(self):
        indexes = self.selected_indexes()
        if not indexes or self.busy:
            return
        pages = [self.pages[i] for i in indexes]
        out = ops.default_output(output_dir(), self.path, "_extract")
        self._run(f"擷取 {len(pages)} 頁中...", lambda: ops.build(pages, out, self.passwords),
                  lambda path: self.notify(f"已擷取 {len(pages)} 頁，存成「{path.name}」", theme.ACCENT))

    def _drop_pages(self, indexes, insert_at):
        order = sorted(i for i in indexes)
        pages, moved = move_items(self.pages, order, insert_at)
        self._change(pages, select=moved, message=f"已移動 {len(order)} 頁")

    # ------------------------------------------------------------ 儲存

    def save(self, target=None, then=None):
        if self.path is None or self.busy:
            return
        out = Path(target) if target else ops.default_output(output_dir(), self.path)
        sources = {Path(page.source).resolve() for page in self.pages if page.source}
        if out.exists() and out.resolve() in sources:
            self.notify("不能覆蓋正在編輯的原檔，請換一個檔名", theme.DANGER)
            return
        pages = list(self.pages)

        def done(path):
            self.history.mark_saved()
            if self.open_file(path, keep_view=True):
                self.notify(f"已儲存成「{path.name}」，接下來編輯這份新檔", theme.ACCENT)
            if then is not None:
                then()

        self._run("儲存中...", lambda: ops.build(pages, out, self.passwords), done)

    def save_as(self):
        if self.path is None or self.busy:
            return
        default = ops.default_output(output_dir(), self.path)
        output_dir().mkdir(parents=True, exist_ok=True)
        chosen = winfile.ask_save("另存新檔", default.name, PDF_FILTER, "pdf", output_dir())
        if chosen is not None:
            self.save(chosen)

    def ask_open_file(self):
        chosen = winfile.ask_open("開啟 PDF", PDF_FILTER)
        if chosen is not None:
            self.request_open(chosen)

    def ask_insert_file(self):
        filters = [("PDF 或圖片", ";".join(["*.pdf"] + [f"*{ext}" for ext in sorted(ops.IMAGE_EXTS)]))]
        chosen = winfile.ask_open("插入 PDF 或圖片", filters)
        if chosen is not None:
            self.insert_files([chosen], self._insert_at())

    # ------------------------------------------------------------ 縮放與捲動

    def _fit_zoom(self, mode):
        if not self.pages or not self.view_rect.width:
            return 1.0
        usable_w = self.view_rect.width - MARGIN * 2 - 12
        widest = max(page.shown_size[0] for page in self.pages) * POINT_PX
        if mode == "fit_width":
            return max(MIN_ZOOM, min(MAX_ZOOM, usable_w / widest))
        page = self.pages[self.current_index()]
        return max(MIN_ZOOM, min(MAX_ZOOM, usable_w / (page.shown_size[0] * POINT_PX),
                                 (self.view_rect.height - MARGIN * 2) / (page.shown_size[1] * POINT_PX)))

    def set_zoom(self, zoom, anchor=None):
        """改變縮放;anchor 是畫面上的點,縮放後那個位置的內容不會跑掉。"""
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        if abs(zoom - self.zoom) < 1e-6:
            return
        anchor = anchor or self.view_rect.center
        offset_y = anchor[1] - self.view_rect.y
        content_y = self.view.scroll + offset_y
        ratio = zoom / self.zoom
        self.zoom = zoom
        self.view.scroll = int(max(0, content_y * ratio - offset_y))
        self.scroll_x = int(max(0, (self.scroll_x + anchor[0] - self.view_rect.x) * ratio - (anchor[0] - self.view_rect.x)))

    def step_zoom(self, direction, anchor=None):
        self.zoom_mode = "custom"
        steps = ZOOM_STEPS if direction > 0 else list(reversed(ZOOM_STEPS))
        target = next((z for z in steps if (z > self.zoom + 1e-3 if direction > 0 else z < self.zoom - 1e-3)),
                      steps[-1])
        self.set_zoom(target, anchor)

    def scroll_to_page(self, index):
        index = max(0, min(len(self.pages) - 1, index))
        for i, rect in self.layout:
            if i == index:
                self.view.set_scroll(rect.y - MARGIN)
                break
        self.selected = {self.pages[index].uid}
        self.anchor = self.pages[index].uid

    # ------------------------------------------------------------ 繪製

    def draw_toolbar(self, rect, mouse_pos):
        screen = self.screen
        has_doc = self.path is not None
        x, y, h = rect.x, rect.y + 2, rect.height - 4
        self.btn_open.enabled = not self.busy
        self.btn_open.draw(screen, pygame.Rect(x, y, 56, h), mouse_pos)
        x += 64
        self.btn_undo.enabled = has_doc and self.history.can_undo and not self.busy
        self.btn_undo.draw(screen, pygame.Rect(x, y, 56, h), mouse_pos)
        x += 60
        self.btn_redo.enabled = has_doc and self.history.can_redo and not self.busy
        self.btn_redo.draw(screen, pygame.Rect(x, y, 56, h), mouse_pos)
        x += 72
        for button in (self.btn_zoom_out, self.btn_zoom_in):
            button.enabled = has_doc
        self.btn_zoom_out.draw(screen, pygame.Rect(x, y, 32, h), mouse_pos)
        x += 36
        self.zoom_menu.enabled = has_doc
        if self.zoom_mode in ("fit_width", "fit_page"):
            self.zoom_menu.set_value(self.zoom_mode)
            label = None
        else:
            label = f"{round(self.zoom * 100)}%"
        real_options = self.zoom_menu.options
        if label:   # 自訂倍率時框裡顯示目前的百分比
            self.zoom_menu.options = [("custom", label)] + real_options
            self.zoom_menu.index = 0
        self.zoom_menu.draw(screen, pygame.Rect(x, y, 104, h), mouse_pos)
        if label:
            self.zoom_menu.options = real_options
            self.zoom_menu.index = -1
        self._zoom_menu_rect = self.zoom_menu.rect
        x += 108
        self.btn_zoom_in.draw(screen, pygame.Rect(x, y, 32, h), mouse_pos)
        x += 48
        if has_doc:
            if not self.page_input.focused:
                self.page_input.set_text(str(self.current_index() + 1))
            self.page_input.draw(screen, pygame.Rect(x, y, 50, h), mouse_pos)
            draw_text(screen, f"/ {len(self.pages)} 頁", (x + 58, rect.centery - 9), 13, theme.TEXT_DIM)
        save_w = 70
        self.btn_save.enabled = has_doc and not self.busy
        self.btn_save.draw(screen, pygame.Rect(rect.right - save_w, y, save_w, h), mouse_pos)
        self.zoom_menu.draw_menu(screen, mouse_pos)

    def draw(self, rect, mouse_pos):
        screen = self.screen
        body = pygame.Rect(rect.x, rect.y, rect.width, rect.height - STATUS_H)
        if self.path is None:
            self._draw_empty(body, mouse_pos)
        else:
            self.sidebar_rect = pygame.Rect(body.x, body.y, SIDEBAR_W, body.height)
            self.view_rect = pygame.Rect(body.x + SIDEBAR_W, body.y, body.width - SIDEBAR_W, body.height)
            self._draw_view(mouse_pos)
            self._draw_sidebar(mouse_pos)
        self._draw_status(pygame.Rect(rect.x, rect.bottom - STATUS_H, rect.width, STATUS_H), mouse_pos)
        if self.busy:
            veil = pygame.Surface(body.size, pygame.SRCALPHA)
            veil.fill((8, 10, 14, 110))
            screen.blit(veil, body.topleft)
            draw_text(screen, self.task[1], body.center, 16, theme.TEXT, center=True)

    def _draw_empty(self, body, mouse_pos):
        screen = self.screen
        self.sidebar_rect = self.view_rect = pygame.Rect(0, 0, 0, 0)
        cx, cy = body.centerx, body.centery - 50
        sheet = pygame.Rect(cx - 30, cy - 40, 60, 78)
        pygame.draw.rect(screen, theme.PANEL_EDGE, sheet, 3, border_radius=6)
        for i in range(3):
            pygame.draw.line(screen, theme.PANEL_EDGE, (sheet.x + 12, sheet.y + 22 + i * 14),
                             (sheet.right - 12, sheet.y + 22 + i * 14), 3)
        draw_text(screen, "把 PDF 拖曳到這個視窗開始編輯", (cx, cy + 66), 16, theme.TEXT_DIM, center=True)
        draw_text(screen, "可以調整頁面順序、旋轉、刪除、插入空白頁或其他檔案", (cx, cy + 92), 13, theme.TEXT_FAINT,
                  center=True)
        self.btn_empty_open.draw(screen, pygame.Rect(cx - 80, cy + 124, 160, 40), mouse_pos)

    def _layout_pages(self):
        if self.zoom_mode in ("fit_width", "fit_page"):
            self.zoom = self._fit_zoom(self.zoom_mode)
        scale = POINT_PX * self.zoom
        widest = max(page.shown_size[0] for page in self.pages) * scale
        self.content_w = widest + MARGIN * 2
        y = MARGIN
        self.layout = []
        for index, page in enumerate(self.pages):
            width, height = page.shown_size
            w, h = round(width * scale), round(height * scale)
            self.layout.append((index, pygame.Rect(round(MARGIN + (widest - w) / 2), y, w, h)))
            y += h + PAGE_GAP
        return y - PAGE_GAP + MARGIN

    def _draw_view(self, mouse_pos):
        screen = self.screen
        area = self.view_rect
        rounded_panel(screen, area, theme.BG_DEEP, radius=0, alpha=235)
        content_h = self._layout_pages()
        self.view.layout(area, content_h)
        max_x = max(0, self.content_w - (area.width - 12))
        self.scroll_x = max(0, min(self.scroll_x, max_x))
        offset_x = area.x + (max(0, (area.width - 12 - self.content_w) / 2)) - self.scroll_x
        scale = POINT_PX * self.zoom
        selected = set(self.selected_indexes())
        requests, thumb_requests = [], []
        self.page_hits = []
        screen.set_clip(area)
        for index, rect in self.layout:
            screen_rect = rect.move(round(offset_x), area.y - self.view.scroll)
            if screen_rect.bottom < area.y or screen_rect.top > area.bottom:
                continue
            page = self.pages[index]
            self.page_hits.append((index, screen_rect))
            shadow = screen_rect.move(3, 4)
            pygame.draw.rect(screen, (10, 12, 16), shadow)
            pygame.draw.rect(screen, (255, 255, 255), screen_rect)
            full_pixels = screen_rect.width * screen_rect.height
            if full_pixels <= MAX_PIXELS:
                key = self.renderer.key(page, scale)
                surface = self.renderer.get(key)
                if surface is None:
                    requests.append((key, page, scale, None))
                    self._blit_fallback(page, screen_rect)
                else:
                    screen.blit(surface, screen_rect.topleft)
            else:
                # 放很大時只畫看得到的範圍;以 200 點為一格對齊,捲動一點點不用重畫
                visible = screen_rect.clip(area)
                width_pt, height_pt = page.shown_size
                grid = 200
                x0 = max(0, int(((visible.x - screen_rect.x) / scale) // grid * grid))
                y0 = max(0, int(((visible.y - screen_rect.y) / scale) // grid * grid))
                x1 = min(width_pt, (int(((visible.right - screen_rect.x) / scale) // grid) + 1) * grid)
                y1 = min(height_pt, (int(((visible.bottom - screen_rect.y) / scale) // grid) + 1) * grid)
                crop = (x0, height_pt - y1, width_pt - x1, y0)
                key = self.renderer.key(page, scale, crop)
                surface = self.renderer.get(key)
                if surface is None:
                    requests.append((key, page, scale, crop))
                    self._blit_fallback(page, screen_rect)
                else:
                    screen.blit(surface, (screen_rect.x + round(x0 * scale), screen_rect.y + round(y0 * scale)))
            if self.renderer.failed(self.renderer.key(page, scale)):
                draw_text(screen, "這一頁無法顯示", screen_rect.center, 14, theme.DANGER, center=True)
            if index in selected and len(self.pages) > 1:
                pygame.draw.rect(screen, self.tool.accent, screen_rect.inflate(6, 6), 2, border_radius=3)
        screen.set_clip(None)
        self._thumb_requests_main = requests
        self.view.draw(screen, mouse_pos)
        if max_x:
            track = pygame.Rect(area.x + 4, area.bottom - 5, area.width - 20, 3)
            bar_w = max(36, int(track.width * (area.width - 12) / self.content_w))
            left = track.x + int(self.scroll_x / max_x * (track.width - bar_w))
            pygame.draw.rect(screen, theme.PANEL_LIGHT, track, border_radius=2)
            pygame.draw.rect(screen, self.tool.accent, (left, track.y, bar_w, 3), border_radius=2)

    def _blit_fallback(self, page, rect):
        fallback = self.renderer.fallback(page)
        if fallback is not None:
            self.screen.blit(pygame.transform.scale(fallback, rect.size), rect.topleft)
        else:
            draw_text(self.screen, "載入中...", rect.center, 13, theme.TEXT_FAINT, center=True)

    def _thumb_scale(self, page):
        width, height = page.shown_size
        return min(THUMB_BOX[0] / width, THUMB_BOX[1] / height) * 0.9999

    def _draw_sidebar(self, mouse_pos):
        screen = self.screen
        side = self.sidebar_rect
        accent = self.tool.accent
        rounded_panel(screen, side, theme.PANEL, radius=0, alpha=240)
        pygame.draw.line(screen, theme.PANEL_EDGE, (side.right - 1, side.y), (side.right - 1, side.bottom))
        count = len(self.selected_indexes())
        draw_text(screen, f"頁面 ({len(self.pages)})", (side.x + 14, side.y + 12), 14, theme.TEXT, bold=True)
        if count:
            draw_text(screen, f"已選 {count} 頁", (side.right - 14, side.y + 21), 12, accent, right=True)
        area = pygame.Rect(side.x, side.y + 40, side.width, side.height - 40 - OPS_H)
        self.thumb_area = area
        self.thumb_view.layout(area, len(self.pages) * THUMB_SLOT + 10)
        current = self.current_index()
        selected = set(self.selected_indexes())
        first = max(0, self.thumb_view.scroll // THUMB_SLOT)
        last = min(len(self.pages), (self.thumb_view.scroll + area.height) // THUMB_SLOT + 1)
        slots, thumb_requests = [], []
        screen.set_clip(area)
        for index in range(first, last):
            page = self.pages[index]
            slot = pygame.Rect(area.x + 10, area.y + 6 + index * THUMB_SLOT - self.thumb_view.scroll,
                               area.width - 28, THUMB_SLOT - 6)
            slots.append((index, slot))
            dragging = self.drag.dragging(index)
            if index in selected:
                rounded_panel(screen, slot, tuple(int(c * 0.28) for c in accent), radius=8,
                              border=accent)
            elif slot.collidepoint(mouse_pos) and area.collidepoint(mouse_pos):
                rounded_panel(screen, slot, theme.PANEL_LIGHT, radius=8)
            scale = self._thumb_scale(page)
            key = self.renderer.key(page, scale)
            surface = self.renderer.get(key)
            width, height = page.shown_size
            box = pygame.Rect(0, 0, round(width * scale), round(height * scale))
            box.center = (slot.centerx, slot.y + 8 + THUMB_BOX[1] // 2)
            pygame.draw.rect(screen, (255, 255, 255), box)
            if surface is not None:
                screen.blit(surface, surface.get_rect(center=box.center))
            else:
                thumb_requests.append((key, page, scale, None))
            if dragging:
                shade = pygame.Surface(box.size, pygame.SRCALPHA)
                shade.fill((20, 24, 30, 150))
                screen.blit(shade, box.topleft)
            label_color = accent if index == current else theme.TEXT_DIM
            draw_text(screen, str(index + 1), (slot.centerx, slot.bottom - 13), 12, label_color,
                      bold=index == current, center=True)
        screen.set_clip(None)
        self.thumb_slots = slots
        self.drag.set_slots(slots, len(self.pages))
        self.thumb_view.draw(screen, mouse_pos)
        self.drag.draw(screen, area)
        # 主畫面看得到的頁面優先,再來是縮圖
        self.renderer.want(getattr(self, "_thumb_requests_main", []) + thumb_requests)
        self._draw_ops(pygame.Rect(side.x, side.bottom - OPS_H, side.width, OPS_H), mouse_pos, bool(count))

    def _draw_ops(self, rect, mouse_pos, has_selection):
        screen = self.screen
        pygame.draw.line(screen, theme.PANEL_EDGE, (rect.x + 10, rect.y), (rect.right - 10, rect.y))
        x, y = rect.x + 12, rect.y + 10
        half = (rect.width - 24 - 8) // 2
        rows = ((self.btn_rotate_left, self.btn_rotate_right), (self.btn_duplicate, self.btn_delete),
                (self.btn_blank, self.btn_insert))
        for left, right in rows:
            for button, bx in ((left, x), (right, x + half + 8)):
                button.enabled = not self.busy and (has_selection or button in (self.btn_blank, self.btn_insert))
                button.draw(screen, pygame.Rect(bx, y, half, 32), mouse_pos)
            y += 38
        self.btn_extract.enabled = has_selection and not self.busy
        self.btn_extract.draw(screen, pygame.Rect(x, y, rect.width - 24, 32), mouse_pos)

    def _draw_status(self, rect, mouse_pos):
        screen = self.screen
        rounded_panel(screen, rect, theme.PANEL, radius=0, alpha=245)
        pygame.draw.line(screen, theme.PANEL_EDGE, (rect.x, rect.y), (rect.right, rect.y))
        x = rect.x + 16
        if self.path is None:
            draw_text(screen, "還沒有開啟檔案", (x, rect.y + 14), 13, theme.TEXT_FAINT)
            return
        name = draw_text(screen, widgets.clip_text(Path(self.path).name, 13, 260), (x, rect.y + 14), 13, theme.TEXT)
        info = f"{len(self.pages)} 頁"
        if self.history.dirty:
            info += " · 有尚未儲存的變更"
        draw_text(screen, info, (name.right + 12, rect.y + 14), 13,
                  theme.WARN if self.history.dirty else theme.TEXT_DIM)
        right = rect.right - 16
        self.btn_save_as.enabled = not self.busy and winfile.available()
        self.btn_save_as.draw(screen, pygame.Rect(right - 118, rect.y + 7, 118, 32), mouse_pos)
        hint = draw_text(screen, "「儲存」預設存到 output\\editor\\", (right - 130, rect.y + 23), 12,
                         theme.TEXT_FAINT, right=True)
        if self.message is not None:
            text, color, at = self.message
            if pygame.time.get_ticks() - at < MESSAGE_MS:
                width = hint.x - 24 - (name.right + 200)
                if width > 80:
                    draw_text(screen, widgets.clip_text(text, 13, width), (name.right + 200, rect.y + 14), 13, color)
            else:
                self.message = None

    # ------------------------------------------------------------ 框選(縮圖欄的空白處拖曳)

    def pages_in_band(self, a, b):
        y1, y2 = sorted((a[1], b[1]))
        first = max(0, int((y1 - 6) // THUMB_SLOT))
        last = min(len(self.pages) - 1, int((y2 - 6) // THUMB_SLOT))
        return {self.pages[i].uid for i in range(first, last + 1)} if last >= first else set()

    def marquee_begin(self, content_pos):
        return {"base": set(self.selected) if pygame.key.get_mods() & pygame.KMOD_CTRL else set()}

    def marquee_update(self, state, a, b):
        self.selected = state["base"] | self.pages_in_band(a, b)

    def marquee_click(self, state):
        pass

    # ------------------------------------------------------------ 事件

    def modal_open(self):
        return self.dialog.is_open

    def draw_modal(self, mouse_pos):
        self.dialog.draw(mouse_pos)

    def handle_modal_event(self, event, mouse_pos):
        self.dialog.handle_event(event, mouse_pos)

    def deactivate(self):
        self.view.reset()
        self.thumb_view.reset()
        self.drag.cancel()
        self.zoom_menu.close()
        self.page_input.blur()

    def handle_event(self, event, mouse_pos):
        if event.type in (pygame.DROPBEGIN, pygame.DROPFILE, pygame.DROPCOMPLETE):
            self._handle_drop(event, mouse_pos)
            return
        if self.busy:
            return
        if self.zoom_menu.is_open or (event.type == pygame.MOUSEBUTTONDOWN and self.zoom_menu.rect.collidepoint(mouse_pos)):
            before = self.zoom_menu.value if self.zoom_menu.index >= 0 else None
            if self.zoom_menu.handle(event, mouse_pos):
                if self.zoom_menu.index >= 0 and self.zoom_menu.value != before:
                    self._pick_zoom(self.zoom_menu.value)
                return
        # 頁碼輸入框按 Enter 跳頁;要在輸入框自己處理 Enter(結束輸入)之前先接住
        if event.type == pygame.KEYDOWN and self.page_input.focused and event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            text = self.page_input.text.strip()
            self.page_input.blur()
            if text.isdigit():
                self.scroll_to_page(int(text) - 1)
            return
        if self.path is not None:
            self.page_input.handle(event, mouse_pos)
        if self.page_input.focused:
            return
        if self.drag.handle(event, mouse_pos):
            self._pending_single = None
            return
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._pending_single is not None:
            self.selected = {self._pending_single}
            self._pending_single = None
        if event.type == pygame.KEYDOWN:
            self._handle_key(event)
            return
        if event.type == pygame.MOUSEWHEEL:
            self._handle_wheel(event, mouse_pos)
            return
        if self.path is not None and self.sidebar_rect.collidepoint(mouse_pos):
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self._press_thumb(mouse_pos):
                return
            if self.thumb_view.handle_event(event, mouse_pos):
                return
        elif self.path is not None and self.view.handle_event(event, mouse_pos):
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._click(mouse_pos)

    def _pick_zoom(self, value):
        if value in ("fit_width", "fit_page"):
            self.zoom_mode = value
        else:
            self.zoom_mode = "custom"
            self.set_zoom(float(value))

    def _press_thumb(self, pos):
        if not self.thumb_area.collidepoint(pos):
            return False
        hit = next((index for index, slot in self.thumb_slots if slot.collidepoint(pos)), None)
        if hit is None:
            return False
        uid = self.pages[hit].uid
        mods = pygame.key.get_mods()
        if mods & pygame.KMOD_CTRL:
            self.selected ^= {uid}
            self.anchor = uid
        elif mods & pygame.KMOD_SHIFT and self.anchor is not None:
            uids = [page.uid for page in self.pages]
            start = uids.index(self.anchor) if self.anchor in uids else hit
            low, high = sorted((start, hit))
            self.selected = set(uids[low:high + 1])
        else:
            if uid in self.selected:
                self._pending_single = uid     # 放開時沒拖曳才改成只選這一頁,拖曳時整組一起移動
            else:
                self.selected = {uid}
            self.anchor = uid
            self._jump_view(hit)
        if uid in self.selected:
            self.drag.press(pos, self.selected_indexes())
        return True

    def _jump_view(self, index):
        for i, rect in self.layout:
            if i == index:
                top, bottom = self.view.scroll, self.view.scroll + self.view_rect.height
                if rect.y < top or rect.y > bottom - 60:
                    self.view.set_scroll(rect.y - MARGIN)
                break

    def _handle_wheel(self, event, pos):
        mods = pygame.key.get_mods()
        if self.path is None:
            return
        if self.view_rect.collidepoint(pos):
            if mods & pygame.KMOD_CTRL:
                self.step_zoom(1 if event.y > 0 else -1, pos)
            elif mods & pygame.KMOD_SHIFT:
                self.scroll_x = max(0, self.scroll_x - event.y * 60)
            else:
                self.view.handle_event(event, pos)
        elif self.sidebar_rect.collidepoint(pos):
            self.thumb_view.handle_event(event, pos)

    def _handle_key(self, event):
        if self.path is None:
            return
        ctrl = event.mod & pygame.KMOD_CTRL
        key = event.key
        if key == pygame.K_DELETE:
            self.delete()
        elif ctrl and key == pygame.K_z:
            self.history.undo()
            self._keep_selection()
        elif ctrl and key == pygame.K_y:
            self.history.redo()
            self._keep_selection()
        elif ctrl and key == pygame.K_a:
            self.selected = {page.uid for page in self.pages}
        elif ctrl and key == pygame.K_s:
            self.save()
        elif ctrl and key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
            self.step_zoom(1)
        elif ctrl and key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self.step_zoom(-1)
        elif key == pygame.K_PAGEDOWN:
            self.scroll_to_page(self.current_index() + 1)
        elif key == pygame.K_PAGEUP:
            self.scroll_to_page(self.current_index() - 1)
        elif key == pygame.K_HOME:
            self.scroll_to_page(0)
        elif key == pygame.K_END:
            self.scroll_to_page(len(self.pages) - 1)

    def _keep_selection(self):
        uids = {page.uid for page in self.pages}
        self.selected &= uids
        if not self.selected and self.pages:
            self.selected = {self.pages[min(self.current_index(), len(self.pages) - 1)].uid}

    def _click(self, pos):
        if self.path is None:
            if self.btn_empty_open.clicked(pos, True):
                self.ask_open_file()
            elif self.btn_open.clicked(pos, True):
                self.ask_open_file()
            return
        actions = [
            (self.btn_open, self.ask_open_file),
            (self.btn_undo, lambda: (self.history.undo(), self._keep_selection())),
            (self.btn_redo, lambda: (self.history.redo(), self._keep_selection())),
            (self.btn_zoom_out, lambda: self.step_zoom(-1)),
            (self.btn_zoom_in, lambda: self.step_zoom(1)),
            (self.btn_save, self.save),
            (self.btn_save_as, self.save_as),
            (self.btn_rotate_left, lambda: self.rotate(270)),
            (self.btn_rotate_right, lambda: self.rotate(90)),
            (self.btn_duplicate, self.duplicate),
            (self.btn_delete, self.delete),
            (self.btn_blank, self.insert_blank),
            (self.btn_insert, self.ask_insert_file),
            (self.btn_extract, self.extract),
        ]
        for button, action in actions:
            if button.clicked(pos, True):
                action()
                return
        for index, rect in self.page_hits:
            if rect.collidepoint(pos) and self.view_rect.collidepoint(pos):
                self.selected = {self.pages[index].uid}
                self.anchor = self.pages[index].uid
                return

    def _handle_drop(self, event, pos):
        if event.type == pygame.DROPBEGIN:
            self._drop_files = []
            return
        if event.type == pygame.DROPFILE:
            if self._drop_files is not None:
                self._drop_files.append(event.file)
                return
            files = [event.file]
        else:
            files, self._drop_files = self._drop_files or [], None
        if files and not self.busy:
            self.drop(files, pos)

    def drop(self, files, pos):
        """拖進檔案:還沒開檔時開啟第一個 PDF;拖到縮圖欄就插入到放下的位置;拖到主畫面時詢問。"""
        files = [Path(f) for f in files]
        pdfs = [f for f in files if f.suffix.lower() == ".pdf"]
        if self.path is None:
            if not pdfs:
                self.notify("請拖入 PDF 檔案", theme.WARN)
                return
            first = pdfs[0]
            rest = [f for f in files if f != first]

            def after_open():
                if self.open_file(first) and rest:
                    self.insert_files(rest, len(self.pages))

            self._large(first, after_open)
            return
        if self.sidebar_rect.collidepoint(pos):
            at = self.drag._insert_index(pos)
            self.insert_files(files, len(self.pages) if at is None else at)
            return
        if not pdfs:
            self.insert_files(files, self._insert_at())
            return

        def choice(key, _):
            if key == "open":
                self.request_open(pdfs[0])
            elif key == "insert":
                self.insert_files(files, self._insert_at())

        names = "、".join(f.name for f in files[:2]) + (" 等檔案" if len(files) > 2 else "")
        self.dialog.open("要怎麼處理拖進來的檔案？", [f"「{names}」",
                                               "「插入」會放在選取的頁面後面；拖到左邊的縮圖欄可以直接指定位置。"],
                         [("cancel", "取消", False), ("open", "開啟新檔", False), ("insert", "插入到目前文件", True)],
                         choice)
