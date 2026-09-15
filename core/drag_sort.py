"""清單拖曳排序:按住項目拖動一小段距離後開始拖曳,放開時把項目移到新位置;選了多個項目會一起移動。

用法:
1. 畫清單時用 set_slots() 交出這一幀畫出來的項目位置 [(索引, 畫面範圍)] 和項目總數
2. 在項目上按下左鍵時呼叫 press(pos, 要一起移動的索引)
3. 事件交給 handle();回傳 True 代表這次放開是拖曳(不要當成點擊)
4. 每幀呼叫 update(清單範圍),回傳需要自動捲動的距離;拖曳中呼叫 draw() 畫插入位置
5. 放開時會呼叫 on_drop(索引清單, 插入位置),用 move_items() 算出新順序
"""

import pygame

from . import theme

THRESHOLD = 6      # 滑鼠移動超過幾像素才算開始拖曳,只是點一下不會誤判
EDGE = 32          # 拖到清單上下邊緣多近開始自動捲動
AUTO_SPEED = 14


def move_items(items, indexes, insert_at):
    """把 indexes 這些項目(保持原本先後)移到原清單第 insert_at 個位置之前。回傳 (新清單, 移動後的索引)。"""
    chosen = sorted(set(i for i in indexes if 0 <= i < len(items)))
    picked = set(chosen)
    moving = [items[i] for i in chosen]
    rest = [item for i, item in enumerate(items) if i not in picked]
    position = max(0, min(len(rest), insert_at - sum(1 for i in chosen if i < insert_at)))
    return rest[:position] + moving + rest[position:], list(range(position, position + len(moving)))


class DragSort:
    def __init__(self, accent=theme.ACCENT, on_drop=None):
        self.accent = accent
        self.on_drop = on_drop
        self.slots = []
        self.count = 0
        self.origin = None
        self.indexes = []
        self.active = False
        self.insert_at = None
        self.pos = (0, 0)

    def set_slots(self, slots, count):
        self.slots = sorted(slots, key=lambda slot: slot[0])
        self.count = count

    def press(self, pos, indexes):
        self.origin = pos
        self.pos = pos
        self.indexes = list(indexes)
        self.active = False
        self.insert_at = None

    def cancel(self):
        self.origin = None
        self.active = False
        self.insert_at = None

    def dragging(self, index):
        return self.active and index in self.indexes

    def _insert_index(self, pos):
        if not self.slots:
            return None
        for index, rect in self.slots:
            if pos[1] < rect.centery:
                return index
        last_index, _ = self.slots[-1]
        return last_index + 1

    def handle(self, event, pos):
        if self.origin is None:
            return False
        if event.type == pygame.MOUSEMOTION:
            self.pos = pos
            if not self.active and abs(pos[0] - self.origin[0]) + abs(pos[1] - self.origin[1]) > THRESHOLD:
                self.active = True
            if self.active:
                self.insert_at = self._insert_index(pos)
            return self.active
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            was_drag = self.active
            if was_drag and self.insert_at is not None and self.on_drop is not None:
                self.on_drop(list(self.indexes), self.insert_at)
            self.cancel()
            return was_drag
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE and self.active:
            self.cancel()
            return True
        return False

    def update(self, area):
        """每幀呼叫:清單捲動後重新計算插入位置;游標貼近清單上下邊緣時回傳自動捲動的距離(負數往上)。
        位置用最後一次滑鼠移動事件的座標,和放開時算的插入位置一致。"""
        if not self.active:
            return 0
        self.insert_at = self._insert_index(self.pos)
        if self.pos[1] < area.y + EDGE:
            return -AUTO_SPEED
        if self.pos[1] > area.bottom - EDGE:
            return AUTO_SPEED
        return 0

    def draw(self, surface, clip):
        """畫插入位置的線,以及游標旁邊「移動 N 項」的提示。"""
        if not self.active or self.insert_at is None or not self.slots:
            return
        rects = dict(self.slots)
        if self.insert_at in rects:
            y = rects[self.insert_at].top - 3
            left, right = rects[self.insert_at].left, rects[self.insert_at].right
        else:
            last = self.slots[-1][1]
            y, left, right = last.bottom + 3, last.left, last.right
        if clip.top - 4 <= y <= clip.bottom + 4:
            previous = surface.get_clip()
            surface.set_clip(clip)
            pygame.draw.line(surface, self.accent, (left + 6, y), (right - 6, y), 3)
            pygame.draw.circle(surface, self.accent, (left + 6, y), 5)
            pygame.draw.circle(surface, self.accent, (right - 6, y), 5)
            surface.set_clip(previous)
        from .widgets import draw_text, rounded_panel   # 避免和 widgets 互相匯入

        label = f"移動 {len(self.indexes)} 項"
        box = pygame.Rect(self.pos[0] + 14, self.pos[1] + 10, theme.font(12).size(label)[0] + 18, 24)
        rounded_panel(surface, box, theme.PANEL_LIGHT, radius=6, alpha=235, border=self.accent)
        draw_text(surface, label, box.center, 12, theme.TEXT, center=True)
