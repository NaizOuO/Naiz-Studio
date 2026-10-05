"""翻譯模型的管理視窗:每個模型一列,寫清楚說明、大小、需要的配備和建議/不建議的原因;
可以在這裡選用、下載(配備不夠時先提醒;用內建引擎時先說明要下載什麼)、刪除(按兩次才刪)。
從即時字幕的「翻譯模型」打開。"""

import threading

import pygame

from core import theme, widgets
from core.scroll import BAR_SPACE, ScrollView
from core.widgets import Button, draw_text, rounded_panel

from . import hardware
from . import translate as ollama

ROW_H = 84
WARN_H = 20          # 配備不夠的模型多一行警告
OWN_NOTE = "自己另外下載的模型，沒有實測過翻譯品質"


def _gb(size_bytes):
    return f"{size_bytes / 1e9:.1f} GB" if size_bytes else ""


class TranslatorDialog:
    def __init__(self, page):
        self.page = page
        self.accent = page.tool.accent
        self.is_open = False
        self.view = ScrollView(accent=self.accent)
        self.btn_close = Button("關閉", filled=False, size=14)
        self.list_area = pygame.Rect(0, 0, 0, 0)
        self.hits = []                  # [(範圍, 動作, 模型名稱)]
        self.confirm = None             # 按過一次「刪除」的模型
        self.deleting = None
        self.message, self.message_color = "", theme.TEXT_DIM

    def open(self):
        self.page._refresh_ollama(force=True)
        self.confirm = None
        self.message = ""
        self.view.reset()
        self.is_open = True

    def close(self):
        self.view.reset()
        self.is_open = False

    def _say(self, text, color=theme.TEXT_DIM):
        self.message, self.message_color = text, color

    # ------------------------------------------------------------ 資料

    def rows(self):
        """[{name, installed, size, vram, note, tags}]:建議清單(小到大)在前,自己另外下載的接在後面。"""
        page = self.page
        installed = dict(page.ollama_models)
        catalog = {name: (size, vram, note) for name, size, vram, note in ollama.suggested()}
        names = list(catalog) + [name for name in installed if name not in catalog]
        result = []
        for name in names:
            size, vram, note = catalog.get(name, (_gb(installed.get(name, 0)), 0, OWN_NOTE))
            bad = ollama.not_recommended(name)
            tags = []
            if name == page.prefs["translator"] and name in installed:
                tags.append(("使用中", self.accent))
            if name == page.recommended[1]:
                tags.append(("建議", self.accent))
            if bad:
                tags.append(("不建議", theme.WARN))
            if vram >= ollama.HIGH_END:
                tags.append(("需要高階電腦", theme.WARN))
            result.append({"name": name, "installed": name in installed, "size": size, "vram": vram,
                           "note": bad or note, "bad": bool(bad), "tags": tags})
        return result

    @staticmethod
    def _need(vram):
        return f"需要約 {vram} GB 顯示卡記憶體" if vram else ""

    @staticmethod
    def _warning(vram):
        """這台電腦跑不動時的警告;跑得動回傳空字串。"""
        have = hardware.gpu_memory()
        if vram and have == 0 and vram > 4:
            return "沒有偵測到 NVIDIA 顯示卡，只能用處理器，會非常慢"
        if vram and have and have < vram:
            return f"此電腦的顯示卡約 {have} GB，不夠的部分會改用一般記憶體，翻譯會變慢"
        return ""

    # ------------------------------------------------------------ 動作

    def choose(self, row):
        page = self.page
        page._change("translator", row["name"])
        self._say(f"已改用 {row['name']}" + ("（字幕進行中也會馬上換）" if page.running else ""), self.accent)
        if row["bad"]:
            self._say(f"已改用 {row['name']}；{row['note']}", theme.WARN)

    def download(self, row):
        warning = self._warning(row["vram"])
        items = ollama.missing(row["name"])     # 用內建引擎時要從網路下載的東西
        if not warning and not items:
            self._start_download(row)
            return
        if items:
            # 下載前說明要下載什麼、從哪裡來,同意了才下載
            lines = ["會從網路下載這些到本地："] + [f"・{dep.name}（{dep.size_text}）：{dep.purpose}" for dep in items]
            places = "、".join(dict.fromkeys(dep.place() for dep in items))
            lines += ["", f"下載到程式資料夾內的 {places}。", "翻譯在本地執行，不會上傳；之後可以在這裡刪掉。"]
            if warning:
                lines += ["", f"注意：{self._need(row['vram'])}；{warning}。"]
            title, ok = "下載翻譯模型", "同意並下載"
        else:
            lines = [f"{row['name']}（{row['size']}）{self._need(row['vram'])}。", f"{warning}。", "",
                     "下載後還是可以用，只是講話快的時候字幕可能跟不上；之後可以在這裡刪掉。"]
            title, ok = "這台電腦的配備可能不夠", "仍要下載"
        self.page.app.dialog.open(title, lines, [("cancel", "取消", False), ("ok", ok, True)],
                                  lambda key, _: key == "ok" and self._start_download(row))

    def cancel_download(self, row):
        pull = self.page.pull
        if pull is not None and pull["name"] == row["name"]:
            pull["cancel"].set()
            self._say(f"已取消下載 {row['name']}", theme.TEXT_DIM)

    def _start_download(self, row):
        self.page._pull(row["name"])
        where = "" if ollama.builtin() else "（存在 Ollama 的模型資料夾）"
        self._say(f"開始下載 {row['name']}{where}，下載完會自動選用", self.accent)

    def delete(self, row):
        page = self.page
        name = row["name"]
        if page.running and page.engine is not None and page.engine.settings.translator == name:
            self._say("字幕進行中正在用這個模型，停止字幕後再刪", theme.WARN)
            return
        if self.confirm != name:
            self.confirm = name
            self._say(f"再按一次「確定刪除」就會刪掉 {name}（{row['size']}）", theme.WARN)
            return
        self.confirm = None
        self.deleting = name

        def work():
            try:
                ollama.delete(name)
                self._say(f"已刪除 {name}", self.accent)
            except Exception as exc:
                self._say(f"刪除失敗：{exc}"[:60], theme.WARN)
            self.deleting = None
            page._refresh_ollama(force=True)

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, pos):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.close()
            return
        if self.view.handle_event(event, pos):
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        if self.list_area.collidepoint(pos):
            for rect, action, row in self.hits:
                if rect.collidepoint(pos):
                    if action != "delete":
                        self.confirm = None
                    getattr(self, {"use": "choose", "download": "download", "delete": "delete",
                                   "cancel": "cancel_download"}[action])(row)
                    return
        if self.btn_close.clicked(pos, True):
            self.close()
            return
        self.confirm = None

    # ------------------------------------------------------------ 繪製

    def _button(self, rect, text, mouse_pos, enabled=True, danger=False, filled=False):
        screen = self.page.screen
        hovered = enabled and rect.collidepoint(mouse_pos) and self.list_area.collidepoint(mouse_pos)
        if filled:
            fill = self.accent
        elif danger:
            fill = theme.DANGER
        else:
            fill = theme.PANEL_LIGHT if hovered else theme.PANEL
        edge = theme.DANGER if (hovered and (danger or text == "刪除")) else (self.accent if hovered else theme.PANEL_EDGE)
        rounded_panel(screen, rect, fill, radius=7, border=edge)
        color = theme.BG_DEEP if (filled or danger) else (theme.TEXT_DIM if enabled else theme.TEXT_FAINT)
        draw_text(screen, text, rect.center, 13, color, center=True)

    def draw(self, mouse_pos):
        page = self.page
        screen = page.screen
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 170))
        screen.blit(veil, (0, 0))
        panel_w, panel_h = min(720, width - 60), min(680, height - 60)
        panel = pygame.Rect((width - panel_w) // 2, (height - panel_h) // 2, panel_w, panel_h)
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.PANEL_EDGE)
        x, inner = panel.x + 24, panel_w - 48
        y = panel.y + 20
        draw_text(screen, "翻譯模型", (x, y), 17, theme.TEXT, bold=True)
        draw_text(screen, f"這台電腦：{hardware.describe()}", (x + inner, y + 12), 12, theme.TEXT_DIM, right=True)
        y += 32
        for line, color in (("若已有 Ollama 會以此直接下載模型，若無則會透過網路拉取的方式來取得模型", theme.TEXT_FAINT),
                            ("顯示卡記憶體不夠時，一部分會改用一般記憶體，翻譯會變慢、字幕可能跟不上", theme.TEXT_FAINT)):
            draw_text(screen, widgets.clip_text(line, 12, inner), (x, y), 12, color)
            y += 20
        y += 4
        draw_text(screen, widgets.clip_text(self.message, 12, inner), (x, y), 12, self.message_color)
        y += 24

        rows = self.rows()
        footer_y = panel.bottom - 54
        area = pygame.Rect(x - 6, y, inner + 12, footer_y - 10 - y)
        self.list_area = area
        self.hits = []
        self.view.update(mouse_pos)
        heights = [ROW_H + (WARN_H if self._warning(row["vram"]) else 0) for row in rows]
        self.view.layout(area, sum(heights) + 4)
        pull = page.pull
        screen.set_clip(area)
        ry = area.y + 2 - self.view.scroll
        for row, row_h in zip(rows, heights):
            ry += row_h
            if ry < area.y or ry - row_h > area.bottom:
                continue
            box = pygame.Rect(area.x + 6, ry - row_h, area.width - 6 - BAR_SPACE, row_h - 8)
            current = row["installed"] and row["name"] == page.prefs["translator"]
            rounded_panel(screen, box, theme.BG_DEEP, radius=8, alpha=200,
                          border=self.accent if current else None)
            buttons_w = 196
            text_w = box.width - buttons_w - 24
            title = draw_text(screen, widgets.clip_text(row["name"], 15, text_w - 120), (box.x + 14, box.y + 9), 15,
                              theme.TEXT, bold=True)
            tx = title.right + 10
            for tag, color in row["tags"]:
                chip_w = theme.font(11).size(tag)[0] + 14
                if tx + chip_w > box.x + 14 + text_w:
                    break
                chip = pygame.Rect(tx, box.y + 11, chip_w, 19)
                rounded_panel(screen, chip, tuple(int(c * 0.25) for c in color), radius=9, border=color)
                draw_text(screen, tag, chip.center, 11, color, center=True)
                tx = chip.right + 6
            draw_text(screen, widgets.clip_text(row["note"], 12, text_w), (box.x + 14, box.y + 34), 12,
                      theme.WARN if row["bad"] else theme.TEXT_DIM)
            detail = "・".join(part for part in (row["size"], self._need(row["vram"])) if part)
            draw_text(screen, widgets.clip_text(detail, 12, text_w), (box.x + 14, box.y + 54), 12, theme.TEXT_FAINT)
            warning = self._warning(row["vram"])
            if warning:
                draw_text(screen, widgets.clip_text(warning, 12, text_w), (box.x + 14, box.y + 74), 12, theme.WARN)

            right = box.right - 12
            main = pygame.Rect(right - 96, box.centery - 15, 96, 30)
            side = pygame.Rect(main.x - 92, main.y, 84, 30)
            if pull is not None and pull["name"] == row["name"]:
                done, total = pull["done"], pull["total"]
                text = pull["error"] or (f"下載中 {done / total:.0%}" if total else "準備下載…")
                if pull["error"] or pull["cancel"].is_set():
                    text = pull["error"] or "取消中…"
                    draw_text(screen, widgets.clip_text(text, 12, buttons_w), (right, box.centery), 12,
                              theme.WARN if pull["error"] else theme.TEXT_FAINT, right=True)
                else:
                    draw_text(screen, text, (side.right, box.centery), 12, self.accent, right=True)
                    self._button(main, "取消下載", mouse_pos)
                    self.hits.append((main, "cancel", row))
            elif not row["installed"]:
                self._button(main, "下載", mouse_pos, enabled=pull is None)
                if pull is None:
                    self.hits.append((main, "download", row))
            else:
                if current:
                    self._button(main, "使用中", mouse_pos, enabled=False)
                else:
                    self._button(main, "選用", mouse_pos, filled=not row["bad"])     # 不建議的不要看起來像在推薦
                    self.hits.append((main, "use", row))
                if self.deleting == row["name"]:
                    draw_text(screen, "刪除中…", (side.right, side.centery), 12, theme.TEXT_FAINT, right=True)
                else:
                    confirming = self.confirm == row["name"]
                    self._button(side, "確定刪除" if confirming else "刪除", mouse_pos, danger=confirming)
                    self.hits.append((side, "delete", row))
        screen.set_clip(None)
        self.view.draw(screen, mouse_pos)
        self.btn_close.draw(screen, pygame.Rect(panel.right - 24 - 90, footer_y, 90, 36), mouse_pos)
