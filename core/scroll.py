"""可捲動區域的共用滑鼠行為:滾輪、拖曳捲動條、中鍵自動捲動,以及可選的按住左鍵框選。

用法:每幀先 layout(範圍, 內容總高),依 view.scroll 畫內容,最後 view.draw();
事件先交給 view.handle_event(),回傳 True 代表已經處理;每幀呼叫 view.update(滑鼠位置)。
需要框選時傳入 marquee 物件,它要提供 marquee_begin / marquee_update / marquee_click。
"""

import pygame

from . import paths, theme

BAR_SPACE = 18
DRAG_THRESHOLD = 6
DEAD_ZONE = 12
EDGE_ZONE = 28
ICON_SIZE = 34

_icon = None
_icon_loaded = False


def _autoscroll_icon():
    global _icon, _icon_loaded
    if not _icon_loaded:
        _icon_loaded = True
        path = paths.IMAGES_DIR / "ui_autoscroll.png"
        if path.exists():
            try:
                _icon = pygame.transform.smoothscale(pygame.image.load(str(path)).convert_alpha(),
                                                     (ICON_SIZE, ICON_SIZE))
            except Exception:
                _icon = None
    return _icon


class ScrollView:
    def __init__(self, accent=theme.ACCENT, wheel_step=40, marquee=None):
        self.accent = accent
        self.wheel_step = wheel_step
        self.marquee = marquee
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.content_h = 0
        self.max_scroll = 0
        self.scroll = 0
        self.bar_drag = None
        self.drag = None
        self.auto = None

    # ------------------------------------------------------------ 版面

    def layout(self, rect, content_h):
        self.rect = pygame.Rect(rect)
        self.content_h = content_h
        self.max_scroll = max(0, content_h - self.rect.height)
        self.scroll = max(0, min(self.scroll, self.max_scroll))

    def clear(self):
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.content_h = 0
        self.max_scroll = 0

    def to_content(self, pos):
        return pos[0], pos[1] - self.rect.y + self.scroll

    def set_scroll(self, value):
        self.scroll = int(max(0, min(value, self.max_scroll)))

    def bar_geometry(self):
        """回傳 (軌道, 拖曳塊);內容沒超出範圍時兩者都是 None。"""
        if not self.max_scroll:
            return None, None
        track = pygame.Rect(self.rect.right - 9, self.rect.y, 7, self.rect.height)
        bar_h = min(track.height, max(36, int(self.rect.height * self.rect.height / max(1, self.content_h))))
        top = track.y + round(self.scroll / self.max_scroll * (track.height - bar_h))
        return track, pygame.Rect(track.x, top, track.width, bar_h)

    def _bar_hit(self):
        return pygame.Rect(self.rect.right - BAR_SPACE, self.rect.y, BAR_SPACE, self.rect.height)

    def _drag_bar_to(self, screen_y):
        track, thumb = self.bar_geometry()
        if track is None:
            return
        span = max(1, track.height - thumb.height)
        self.set_scroll((screen_y - self.bar_drag - track.y) / span * self.max_scroll)

    # ------------------------------------------------------------ 狀態

    def reset(self):
        self.bar_drag = None
        self.drag = None
        self.stop_autoscroll()

    def start_autoscroll(self, pos):
        self.auto = {"anchor": pos, "pressed": pygame.time.get_ticks(), "moved": False, "carry": 0.0}
        # 系統游標藏起來,改畫自動捲動圖示跟著滑鼠走
        pygame.mouse.set_visible(False)

    def stop_autoscroll(self):
        if self.auto is not None:
            self.auto = None
            pygame.mouse.set_visible(True)

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, pos):
        if self.auto:
            if event.type == pygame.MOUSEBUTTONUP and event.button == 2:
                held = pygame.time.get_ticks() - self.auto["pressed"]
                # 按住移動後放開就結束;只是點一下的話保持自動捲動,等下一次點擊才結束
                if self.auto["moved"] or held > 350:
                    self.stop_autoscroll()
                return True
            if event.type == pygame.MOUSEBUTTONDOWN and event.button in (1, 2, 3):
                self.stop_autoscroll()
                return True

        if event.type == pygame.MOUSEWHEEL:
            if self.rect.collidepoint(pos) and self.max_scroll:
                self.set_scroll(self.scroll - event.y * self.wheel_step)
                return True
            return False

        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 2 and self.rect.collidepoint(pos) and self.max_scroll:
                self.start_autoscroll(pos)
                return True
            if event.button != 1:
                return False
            _, thumb = self.bar_geometry()
            if thumb is not None and self._bar_hit().collidepoint(pos):
                if thumb.top <= pos[1] <= thumb.bottom:
                    self.bar_drag = pos[1] - thumb.y
                else:
                    self.bar_drag = thumb.height // 2
                    self._drag_bar_to(pos[1])
                return True
            if self.marquee is not None and self.rect.collidepoint(pos):
                start = self.to_content(pos)
                self.drag = {"origin": pos, "start": start, "active": False,
                             "state": self.marquee.marquee_begin(start)}
                return True
            return False

        if event.type == pygame.MOUSEMOTION:
            if self.bar_drag is not None:
                self._drag_bar_to(pos[1])
                return True
            if self.drag:
                ox, oy = self.drag["origin"]
                if not self.drag["active"] and abs(pos[0] - ox) + abs(pos[1] - oy) > DRAG_THRESHOLD:
                    self.drag["active"] = True
                if self.drag["active"]:
                    self.marquee.marquee_update(self.drag["state"], self.drag["start"], self.to_content(pos))
                return True
            return False

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.bar_drag is not None:
                self.bar_drag = None
                return True
            if self.drag:
                drag, self.drag = self.drag, None
                if not drag["active"]:
                    self.marquee.marquee_click(drag["state"])
                return True
        return False

    def update(self, mouse):
        if self.auto:
            dy = mouse[1] - self.auto["anchor"][1]
            if abs(dy) > DEAD_ZONE:
                self.auto["moved"] = True
                step = (abs(dy) - DEAD_ZONE) * 0.18 + self.auto["carry"]
                whole = int(step)
                self.auto["carry"] = step - whole
                self.set_scroll(self.scroll + (whole if dy > 0 else -whole))
        if self.drag and self.drag["active"]:
            # 框選時滑鼠貼近上下邊緣就自動捲動,才框得到畫面外的項目
            if mouse[1] < self.rect.y + EDGE_ZONE:
                self.set_scroll(self.scroll - 14)
            elif mouse[1] > self.rect.bottom - EDGE_ZONE:
                self.set_scroll(self.scroll + 14)
            self.marquee.marquee_update(self.drag["state"], self.drag["start"], self.to_content(mouse))

    # ------------------------------------------------------------ 繪製

    def draw(self, surface, mouse_pos):
        if self.drag and self.drag["active"]:
            x1, x2 = sorted((self.drag["start"][0], mouse_pos[0]))
            y1, y2 = sorted((self.rect.y + self.drag["start"][1] - self.scroll, mouse_pos[1]))
            band = pygame.Rect(x1, y1, x2 - x1, y2 - y1).clip(self.rect)
            if band.width and band.height:
                layer = pygame.Surface(band.size, pygame.SRCALPHA)
                layer.fill((*self.accent, 45))
                surface.blit(layer, band.topleft)
                pygame.draw.rect(surface, self.accent, band, 1)

        track, thumb = self.bar_geometry()
        if track is not None:
            hot = self.bar_drag is not None or self._bar_hit().collidepoint(mouse_pos)
            pygame.draw.rect(surface, theme.PANEL_LIGHT, track, border_radius=3)
            color = tuple(min(255, c + 35) for c in self.accent) if hot else self.accent
            pygame.draw.rect(surface, color, thumb, border_radius=3)

        if self.auto:
            pygame.draw.circle(surface, self.accent, self.auto["anchor"], 4, 1)
            icon = _autoscroll_icon()
            if icon is not None:
                tinted = icon.copy()
                tinted.fill((*self.accent, 255), special_flags=pygame.BLEND_RGBA_MULT)
                surface.blit(tinted, tinted.get_rect(center=mouse_pos))
            else:
                mx, my = mouse_pos
                pygame.draw.circle(surface, theme.BG_DEEP, (mx, my), 15)
                pygame.draw.circle(surface, self.accent, (mx, my), 15, 2)
                pygame.draw.polygon(surface, self.accent, [(mx, my - 10), (mx - 5, my - 4), (mx + 5, my - 4)])
                pygame.draw.polygon(surface, self.accent, [(mx, my + 10), (mx - 5, my + 4), (mx + 5, my + 4)])
