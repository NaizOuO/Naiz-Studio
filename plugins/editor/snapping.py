"""PDF 編輯器的按住 Ctrl 移動:只往水平或垂直走,並吸附到其他物件的邊與中線(畫虛線對齊線)。

這裡是 AnnotController 的一部分(mixin),self 就是 AnnotController。
"""

import math

import pygame

from . import annots, geometry

SNAP_PX = 6                 # 按住 Ctrl 移動時,離其他物件的邊或中線這麼近(畫面像素)就對齊


class SnapMixin:
    def _snap_targets(self, index, moving):
        """可以對齊的物件範圍:其他註解、原檔的圖片、每一段文字,以及頁面本身。"""
        ref = self.pages[index]
        boxes = [annots.bounds(a) for a in ref.annots if a.uid != moving.uid and a.kind != "other"]
        to_page = geometry.ref_from_user(ref)
        if not ref.base_rotation % 360:
            boxes += [geometry.transform_box(to_page, p.bounds()) for p in self.paragraphs_of(index)]
        width, height = ref.size
        return boxes, (0.0, 0.0, width, height)

    def _align(self, index, annot, dx, dy):
        """按住 Ctrl 移動:只往水平或垂直其中一個方向走,並吸附到附近物件的邊或中線。回傳 (dx, dy, 對齊線)。"""
        if abs(dx) >= abs(dy):
            dy = 0.0
        else:
            dx = 0.0
        boxes, page_box = self._snap_targets(index, annot)
        x0, y0, x1, y1 = annots.bounds(annot)
        x0, x1, y0, y1 = x0 + dx, x1 + dx, y0 + dy, y1 + dy
        limit = SNAP_PX / self.scale()
        guides = []
        axis = 0 if dy == 0.0 else 1
        mine = (x0, (x0 + x1) / 2, x1) if axis == 0 else (y0, (y0 + y1) / 2, y1)
        best = None
        for box in boxes + [page_box]:
            theirs = (box[0], (box[0] + box[2]) / 2, box[2]) if axis == 0 else (box[1], (box[1] + box[3]) / 2, box[3])
            for a in mine:
                for b in theirs:
                    if abs(b - a) <= limit and (best is None or abs(b - a) < abs(best[0])):
                        best = (b - a, b, box)
        if best is not None:
            shift, line, box = best
            if axis == 0:
                dx += shift
                guides.append(((line, min(y0, box[1])), (line, max(y1, box[3]))))
            else:
                dy += shift
                guides.append(((min(x0, box[0]), line), (max(x1, box[2]), line)))
        return dx, dy, guides

    def _dashed(self, screen, a, b):
        """對齊線:虛線。"""
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length < 1:
            return
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        for start in range(0, int(length), 8):
            end = min(length, start + 5)
            pygame.draw.line(screen, self.accent, (a[0] + ux * start, a[1] + uy * start),
                             (a[0] + ux * end, a[1] + uy * end), 1)
