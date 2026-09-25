"""裁切頁面:在頁面上拖曳框線決定要留下的範圍,套用到選取的頁面;也可以自動去掉白邊、還原原本大小。

存檔時只改 PDF 的裁切範圍(CropBox),內容都還在,之後還原也不會少東西。
"""

import pygame
from PIL import Image, ImageChops

from core import pdfium, theme, widgets
from core.widgets import Button, draw_text, rounded_panel

from . import annots, geometry, model

HANDLE = 10
MIN_SIZE = 20.0             # 裁切後至少留這麼大(點)
TRIM_PAD = 6.0              # 自動去白邊時,內容外面多留這麼多(點)
BAR_H = 48


class CropMode:
    def __init__(self, page, accent):
        self.page = page
        self.accent = accent
        self.active = False
        self.index = 0
        self.box = (0.0, 0.0, 0.0, 0.0)     # 頁面座標
        self._drag = None
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.btn_auto = Button("自動去白邊", filled=False, size=13)
        self.btn_reset = Button("還原原本大小", filled=False, size=13)
        self.btn_cancel = Button("取消", filled=False, size=13)
        self.btn_apply = Button("套用", accent=accent, size=13)

    def targets(self):
        return self.page.selected_indexes()

    def start(self):
        indexes = self.targets()
        if not indexes:
            self.page.notify("先在左邊選要裁切的頁面", theme.WARN)
            return
        self.page.annot.finish_editing()
        self.page.annot.cancel_action()
        self.page.annot.set_tool("select")
        self.index = self.page.current_index() if self.page.current_index() in indexes else indexes[0]
        width, height = self.page.pages[self.index].size
        self.box = (0.0, 0.0, width, height)
        self.active = True

    def stop(self):
        self.active = False
        self._drag = None

    def apply(self):
        indexes = self.targets()
        pages = model.crop(self.page.pages, indexes, self.box)
        self.stop()
        self.page._change(pages, message=f"已裁切 {len(indexes)} 頁")

    def reset(self):
        indexes = [i for i in self.targets() if self.page.pages[i].full]
        self.stop()
        if not indexes:
            self.page.notify("選取的頁面沒有裁切過", theme.TEXT_DIM)
            return
        self.page._change(model.uncrop(self.page.pages, indexes), message=f"已還原 {len(indexes)} 頁的大小")

    def auto(self):
        """找出頁面上有內容的範圍(不是白色的地方),多留一點邊。"""
        ref = self.page.pages[self.index]
        doc = self.page.docs.get(ref.source) if ref.kind == "pdf" else None
        if doc is None:
            return
        scale = 1.0
        try:
            image = pdfium.render(doc, ref.index, scale, box=geometry.user_box(ref),
                                  hidden=annots.hidden_origins(ref))
        except Exception:
            return
        # 畫出來的是頁面原本的方向(沒有編輯時另外轉的角度),和頁面座標一樣
        difference = ImageChops.difference(image.convert("L"), Image.new("L", image.size, 255))
        found = difference.point(lambda v: 255 if v > 12 else 0).getbbox()
        boxes = [tuple(v / scale for v in found)] if found is not None else []
        # 編輯器裡加的註解還沒畫進 PDF,也要算進去
        boxes += [annots.bounds(a) for a in ref.annots if a.kind not in ("other", annots.PAGE_IMAGE)]
        if not boxes:
            self.page.notify("這一頁是空白的", theme.WARN)
            return
        left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
        right, bottom = max(b[2] for b in boxes), max(b[3] for b in boxes)
        width, height = ref.size
        self.box = (max(0.0, left - TRIM_PAD), max(0.0, top - TRIM_PAD),
                    min(width, right + TRIM_PAD), min(height, bottom + TRIM_PAD))

    # ------------------------------------------------------------ 事件

    def _handles(self, mapper):
        x0, y0, x1, y1 = self.box
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        points = {"nw": (x0, y0), "n": (cx, y0), "ne": (x1, y0), "e": (x1, cy), "se": (x1, y1), "s": (cx, y1),
                  "sw": (x0, y1), "w": (x0, cy)}
        return {name: mapper.point(point) for name, point in points.items()}

    def handle_event(self, event, pos) -> bool:
        """回傳 True 代表事件被裁切用掉了。"""
        if not self.active:
            return False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.stop()
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.apply()
            return True
        mapper = self.page.annot.mapper(self.index)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for button, action in ((self.btn_auto, self.auto), (self.btn_reset, self.reset),
                                   (self.btn_cancel, self.stop), (self.btn_apply, self.apply)):
                if button.clicked(pos, True):
                    action()
                    return True
            if self.rect.collidepoint(pos):
                return True
            if mapper is None or not self.page.view_rect.collidepoint(pos):
                return False
            for name, point in self._handles(mapper).items():
                if abs(pos[0] - point[0]) <= HANDLE and abs(pos[1] - point[1]) <= HANDLE:
                    self._drag = dict(kind=name, start=mapper.to_page(pos), box=self.box)
                    return True
            point = mapper.to_page(pos)
            width, height = self.page.pages[self.index].size
            if not (0 <= point[0] <= width and 0 <= point[1] <= height):
                index = self.page.annot.page_at(pos)
                if index is not None and index in self.targets():
                    self.index = index          # 點別張選取的頁面:改在那一頁上調整
                    other = self.page.pages[index].size
                    self.box = (min(self.box[0], other[0]), min(self.box[1], other[1]),
                                min(self.box[2], other[0]), min(self.box[3], other[1]))
                return True
            x0, y0, x1, y1 = self.box
            kind = "move" if x0 <= point[0] <= x1 and y0 <= point[1] <= y1 else "new"
            self._drag = dict(kind=kind, start=point, box=self.box)
            return True
        if event.type == pygame.MOUSEMOTION and self._drag is not None and mapper is not None:
            self._drag_to(mapper.to_page(pos))
            return True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._drag is not None:
            x0, y0, x1, y1 = self.box
            if x1 - x0 < MIN_SIZE or y1 - y0 < MIN_SIZE:
                self.box = self._drag["box"]        # 只點一下、沒拉出範圍:維持原本的框
            self._drag = None
            return True
        return event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP) and self.page.view_rect.collidepoint(pos)

    def _drag_to(self, point):
        drag = self._drag
        width, height = self.page.pages[self.index].size
        px = max(0.0, min(width, point[0]))
        py = max(0.0, min(height, point[1]))
        x0, y0, x1, y1 = drag["box"]
        kind = drag["kind"]
        if kind == "move":
            dx = max(-x0, min(width - x1, point[0] - drag["start"][0]))
            dy = max(-y0, min(height - y1, point[1] - drag["start"][1]))
            self.box = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
            return
        if kind == "new":
            sx, sy = drag["start"]
            x0, x1 = sorted((sx, px))
            y0, y1 = sorted((sy, py))
        else:
            if "w" in kind:
                x0 = min(px, x1 - MIN_SIZE)
            if "e" in kind:
                x1 = max(px, x0 + MIN_SIZE)
            if "n" in kind:
                y0 = min(py, y1 - MIN_SIZE)
            if "s" in kind:
                y1 = max(py, y0 + MIN_SIZE)
        self.box = (x0, y0, x1, y1)

    # ------------------------------------------------------------ 繪製

    def draw_page(self, screen, index, mapper):
        if not self.active or index not in self.targets():
            return
        page_rect = mapper.box((0, 0) + tuple(self.page.pages[index].size))
        width, height = self.page.pages[index].size
        box = (min(self.box[0], width), min(self.box[1], height), min(self.box[2], width), min(self.box[3], height))
        area = mapper.box(box)
        # 要裁掉的部分蓋暗
        veil = pygame.Surface(page_rect.size, pygame.SRCALPHA)
        veil.fill((10, 12, 16, 150))
        veil.fill((0, 0, 0, 0), area.move(-page_rect.x, -page_rect.y))
        screen.blit(veil, page_rect.topleft)
        pygame.draw.rect(screen, self.accent, area, 2)
        if index != self.index:
            return
        for x, y in self._handles(mapper).values():
            handle = pygame.Rect(0, 0, HANDLE, HANDLE)
            handle.center = (round(x), round(y))
            pygame.draw.rect(screen, (255, 255, 255), handle)
            pygame.draw.rect(screen, self.accent, handle, 1)

    def draw_bar(self, screen, area, mouse_pos):
        if not self.active:
            return
        rect = pygame.Rect(area.x + 16, area.y + 10, area.width - 44, BAR_H)
        self.rect = rect
        rounded_panel(screen, rect, theme.PANEL_LIGHT, radius=10, alpha=250, border=self.accent)
        count = len(self.targets())
        hint = f"拖曳框線調整要留下的範圍，會套用到選取的 {count} 頁；內容不會被刪掉，之後可以還原"
        right = rect.right - 10
        buttons = [(self.btn_apply, 70), (self.btn_cancel, 64), (self.btn_reset, 112), (self.btn_auto, 100)]
        for button, width in buttons:
            button.draw(screen, pygame.Rect(right - width, rect.y + 8, width, 32), mouse_pos)
            right -= width + 8
        self.btn_reset.enabled = any(self.page.pages[i].full for i in self.targets())
        room = right - rect.x - 24
        draw_text(screen, widgets.clip_text(hint, 13, room), (rect.x + 14, rect.centery - 9), 13, theme.TEXT)
