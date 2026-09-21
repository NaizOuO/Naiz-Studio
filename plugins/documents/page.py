"""文件轉檔的畫面:拖入檔案、選一個輸出格式、開始轉換。"""

import os
from pathlib import Path

import pygame

from core import deps, tasks, theme, widgets
from core.plugins import Page
from core.scroll import ScrollView
from core.widgets import Button, ChoiceGrid, ProgressBar, SegmentedControl, draw_text, rounded_panel

from . import ops

ROW_H = 56
ENGINES = [("auto", "自動"), ("office", "電腦上的 Office"), ("libre", "LibreOffice")]
ENGINE_NOTES = {
    "auto": "有裝 Office 就用它（排版最準）；PDF 轉 Word 用程式自己重建，其他情況用 LibreOffice",
    "office": "只用電腦上已經安裝的 Word、PowerPoint、Excel",
    "libre": "一律用 LibreOffice，不會開到你的 Office；PDF 轉 Word 會變成文字方塊",
}


class DocumentsPage(Page):
    def __init__(self, app, tool):
        super().__init__(app, tool)
        accent = tool.accent
        self.accent = accent
        self.files = []
        self.status = "把文件拖進視窗即可開始"
        self.result_lines = []
        self.runner = tasks.TaskRunner()
        self.list_view = ScrollView(accent=accent)
        self.list_area = pygame.Rect(0, 0, 0, 0)
        self.row_buttons = []

        self.formats = ChoiceGrid([(target.key, target.label) for target in ops.TARGETS], columns=3, accent=accent)
        self.engine = SegmentedControl(ENGINES, accent=accent)
        self.btn_run = Button("開始轉換", accent=accent)
        self.btn_cancel = Button("取消", accent=theme.DANGER, filled=False)
        self.btn_clear = Button("清空清單", filled=False, size=14)
        self.btn_output = Button("輸出資料夾", filled=False, size=14)

    # ------------------------------------------------------------ 資料

    @property
    def target(self):
        return ops.TARGET_BY_KEY[self.formats.value]

    def add_files(self, raw_paths):
        existing = {item["path"] for item in self.files}
        added = 0
        for raw in raw_paths:
            path = Path(raw)
            if path.suffix.lower() not in ops.SOURCE_EXTS or not path.is_file() or path in existing:
                continue
            self.files.append({"path": path, "kind": ops.kind_of(path),
                               "size": path.stat().st_size if path.exists() else 0})
            added += 1
        if added:
            # 一次拖多個檔案時系統會一個一個送進來,講總數才不會只顯示「已加入 1 個」
            self.status = f"共 {len(self.files)} 個檔案，按「開始轉換」開始處理"
            self.result_lines = []

    def usable(self, item):
        """這個檔案能不能轉成目前選的格式;回傳 (可以嗎, 說明)。"""
        target = self.target
        if ops.same_format(item["path"], target):
            return False, "已經是這個格式"
        if item["kind"] not in target.sources:
            return False, f"{ops.KIND_LABELS[item['kind']]}不能轉成「{target.label}」"
        if not ops.pick_engine(item["kind"], target, self.engine.value):
            missing = "電腦上沒有這個 Office 程式" if self.engine.value == "office" else "這個組合做不到"
            return False, missing
        return True, ""

    def ready_files(self):
        return [item["path"] for item in self.files if self.usable(item)[0]]

    def can_run(self):
        return bool(self.ready_files()) and not self.runner.running

    # ------------------------------------------------------------ 執行

    def start_job(self):
        files = self.ready_files()
        if not files or self.runner.running:
            return
        target_key = self.formats.value
        preferred = self.engine.value
        if ops.needs_libreoffice(files, self.target, preferred) and not ops.libre_ready():
            self.app.consent.open("文件轉檔", [ops.LIBREOFFICE], on_done=self.start_job)
            return

        out_dir = ops.output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        self.result_lines = []

        def job(runner):
            def progress(done, total, message):
                runner.report(done, max(1, total), message)

            rows = ops.convert_all(files, target_key, out_dir, preferred, progress, runner.cancel_event)
            for source, produced, message in rows:
                if produced is not None:
                    runner.log(f"{source.name} → {produced.name}（{deps.human_size(produced.stat().st_size)}）")
                else:
                    runner.log(f"{source.name}：{message}")

        self.runner.start(job)
        self.status = "轉換中..."

    def update(self):
        if self.runner.finished and not self.runner.running and not self.result_lines:
            _, _, _, lines, error, _ = self.runner.snapshot()
            if lines or error:
                self.result_lines = lines
                self.status = "完成" if not error else "發生錯誤"
        self.list_view.update(pygame.mouse.get_pos())

    def deactivate(self):
        self.list_view.reset()

    # ------------------------------------------------------------ 繪製

    def draw(self, rect, mouse_pos):
        margin = 20
        footer_h = 74
        content_y = rect.y + margin
        content_h = rect.bottom - content_y - footer_h - margin * 2
        list_w = int((rect.width - margin * 3) * 0.54)
        self.draw_file_list(pygame.Rect(rect.x + margin, content_y, list_w, content_h), mouse_pos)
        self.draw_options(pygame.Rect(rect.x + margin * 2 + list_w, content_y,
                                      rect.width - list_w - margin * 3, content_h), mouse_pos)
        self.draw_footer(pygame.Rect(rect.x + margin, rect.bottom - footer_h - margin,
                                     rect.width - margin * 2, footer_h), mouse_pos)

    def draw_file_list(self, rect, mouse_pos):
        screen = self.screen
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        head = pygame.Rect(rect.x, rect.y, rect.width, 44)
        draw_text(screen, f"檔案清單 ({len(self.files)})", (head.x + 16, head.y + 13), 15, theme.TEXT, bold=True)
        if self.files:
            self.btn_clear.draw(screen, pygame.Rect(head.right - 96, head.y + 9, 80, 26), mouse_pos)
        pygame.draw.line(screen, theme.PANEL_EDGE, (rect.x + 12, rect.y + 44), (rect.right - 12, rect.y + 44))
        if not self.files:
            draw_text(screen, "把 Word、PowerPoint、Excel、PDF 拖曳到這個視窗",
                      (rect.centerx, rect.centery - 12), 16, theme.TEXT_DIM, center=True)
            draw_text(screen, "支援 docx、doc、pptx、ppt、xlsx、xls、odt、odp、ods、rtf、txt、csv、pdf",
                      (rect.centerx, rect.centery + 14), 13, theme.TEXT_FAINT, center=True)
            self.list_area = pygame.Rect(0, 0, 0, 0)
            self.row_buttons = []
            return

        area = pygame.Rect(rect.x + 10, rect.y + 53, rect.width - 20, rect.height - 65)
        self.list_area = area
        self.list_view.layout(area, len(self.files) * ROW_H + 8)
        previous = screen.get_clip()
        screen.set_clip(area)
        self.row_buttons = []
        for index, item in enumerate(self.files):
            row = pygame.Rect(area.x + 4, area.y + 4 + index * ROW_H - self.list_view.scroll, area.width - 20, ROW_H - 8)
            if row.bottom < area.y or row.top > area.bottom:
                continue
            ok, why = self.usable(item)
            hover = row.collidepoint(mouse_pos) and area.collidepoint(mouse_pos)
            rounded_panel(screen, row, theme.PANEL_LIGHT if hover else theme.BG_DEEP, radius=8, alpha=200)
            pygame.draw.rect(screen, self.accent if ok else theme.PANEL_EDGE,
                             (row.x + 10, row.y + 13, 3, 22), border_radius=2)
            name = widgets.clip_text(item["path"].name, 15, row.width - 190)
            draw_text(screen, name, (row.x + 22, row.y + 8), 15, theme.TEXT if ok else theme.TEXT_FAINT)
            detail = f"{ops.KIND_LABELS[item['kind']]} · {deps.human_size(item['size'])}"
            draw_text(screen, detail, (row.x + 22, row.y + 28), 12, theme.TEXT_DIM)
            if ok:
                draw_text(screen, f"→ {self.target.label}", (row.right - 46, row.centery), 13, self.accent, right=True)
            else:
                draw_text(screen, widgets.clip_text(why, 12, 170), (row.right - 46, row.centery), 12, theme.WARN,
                          right=True)
            remove = pygame.Rect(row.right - 34, row.centery - 12, 24, 24)
            hovered = remove.collidepoint(mouse_pos) and area.collidepoint(mouse_pos)
            rounded_panel(screen, remove, theme.DANGER if hovered else theme.PANEL, radius=6)
            cross = theme.BG_DEEP if hovered else theme.TEXT_DIM
            cx, cy = remove.center
            pygame.draw.line(screen, cross, (cx - 5, cy - 5), (cx + 5, cy + 5), 2)
            pygame.draw.line(screen, cross, (cx + 5, cy - 5), (cx - 5, cy + 5), 2)
            self.row_buttons.append((index, remove))
        screen.set_clip(previous)
        self.list_view.draw(screen, mouse_pos)

    def engine_detail(self):
        """電腦上有哪些可以用的程式。"""
        apps = ops.office_apps()
        names = [name for name, key in (("Word", "word"), ("PowerPoint", "ppt"), ("Excel", "excel")) if apps.get(key)]
        detail = ("電腦上可以用：" + "、".join(names)) if names else "電腦上沒有偵測到 Office，會改用 LibreOffice"
        return detail + ("；LibreOffice 已下載" if ops.libre_ready() else "")

    def draw_options(self, rect, mouse_pos):
        screen = self.screen
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        draw_text(screen, "轉換設定", (rect.x + 18, rect.y + 14), 15, theme.TEXT, bold=True)
        pygame.draw.line(screen, theme.PANEL_EDGE, (rect.x + 12, rect.y + 44), (rect.right - 12, rect.y + 44))
        x, inner = rect.x + 18, rect.width - 36
        y = rect.y + 60
        draw_text(screen, "輸出格式", (x, y), 14, theme.TEXT)
        y += 24
        rows = (len(ops.TARGETS) + 2) // 3
        self.formats.draw(screen, pygame.Rect(x, y, inner, rows * 38), mouse_pos)
        y += rows * 38 + 10
        for line in widgets.wrap_text(self.target.note, 12, inner, max_lines=2):
            draw_text(screen, line, (x, y), 12, theme.TEXT_FAINT)
            y += 18
        y += 12

        draw_text(screen, "轉檔方式", (x, y), 14, theme.TEXT)
        y += 24
        self.engine.draw(screen, pygame.Rect(x, y, inner, 34), mouse_pos)
        y += 42
        for line in widgets.wrap_text(ENGINE_NOTES[self.engine.value], 12, inner, max_lines=2):
            draw_text(screen, line, (x, y), 12, theme.TEXT_FAINT)
            y += 18
        for line in widgets.wrap_text(self.engine_detail(), 12, inner, max_lines=2):
            draw_text(screen, line, (x, y), 12, theme.TEXT_FAINT)
            y += 18
        y += 10

        files = self.ready_files()
        if files and ops.needs_libreoffice(files, self.target, self.engine.value) and not ops.libre_ready():
            for line in widgets.wrap_text("這次的轉換需要 LibreOffice，按下開始後會先詢問是否下載（約 357 MB）",
                                          12, inner, max_lines=2):
                draw_text(screen, line, (x, y), 12, theme.WARN)
                y += 18
        elif self.target.key in ("docx", "rtf", "odf") and any(item["kind"] == "pdf" for item in self.files):
            if self.target.key == "docx" and self.engine.value != "libre":
                note = ("PDF 轉 Word 是重新排出來的：文字、字型、顏色、圖片、表格與分欄都會保留，"
                        "圖表會整塊變成圖片；排版可能和原檔有些差距")
            else:
                note = "PDF 轉回可編輯的文件時，版面會跑掉（文字會被切成一塊一塊），適合用來取出內容再自己排版"
            for line in widgets.wrap_text(note, 12, inner, max_lines=3):
                draw_text(screen, line, (x, y), 12, theme.WARN)
                y += 18

    def draw_footer(self, rect, mouse_pos):
        screen = self.screen
        rounded_panel(screen, rect, theme.PANEL, radius=12, alpha=228, border=theme.PANEL_EDGE)
        done, total, message, lines, error, _ = self.runner.snapshot()
        running = self.runner.running

        bar = pygame.Rect(rect.x + 18, rect.y + 16, rect.width - 268, 22)
        if running:
            ratio = done / total if total else 0
            ProgressBar(self.accent).draw(screen, bar, ratio, f"{int(ratio * 100)}%")
            draw_text(screen, widgets.clip_text(message or "轉換中...", 12, bar.width), (bar.x, bar.bottom + 8), 12,
                      theme.TEXT_DIM)
        elif error:
            draw_text(screen, f"錯誤：{error}", (bar.x, bar.y + 2), 13, theme.DANGER)
        elif self.result_lines or lines:
            for index, line in enumerate((self.result_lines or lines)[:2]):
                draw_text(screen, widgets.clip_text(line, 13, bar.width), (bar.x, bar.y + index * 19), 13,
                          theme.TEXT if index == 0 else theme.TEXT_DIM)
        else:
            draw_text(screen, self.status, (bar.x, bar.y + 2), 13, theme.TEXT_DIM)
            draw_text(screen, "輸出位置：output\\documents\\", (bar.x, bar.y + 22), 12, theme.TEXT_FAINT)

        side = pygame.Rect(rect.right - 238, rect.y + 18, 104, 38)
        if running:
            self.btn_cancel.draw(screen, side, mouse_pos)
        else:
            self.btn_output.draw(screen, side, mouse_pos)
        self.btn_run.enabled = self.can_run() and not running
        self.btn_run.label = "處理中..." if running else "開始轉換"
        self.btn_run.draw(screen, pygame.Rect(rect.right - 122, rect.y + 18, 104, 38), mouse_pos)

    # ------------------------------------------------------------ 事件

    def handle_event(self, event, mouse_pos):
        if event.type == pygame.DROPFILE:
            self.add_files([event.file])
            return
        if self.files and self.list_view.handle_event(event, mouse_pos):
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.handle_click(mouse_pos)

    def handle_click(self, mouse_pos):
        if self.list_area.collidepoint(mouse_pos):
            for index, rect in self.row_buttons:
                if rect.collidepoint(mouse_pos):
                    self.files.pop(index)
                    return
        if not self.runner.running and self.btn_output.clicked(mouse_pos, True):
            folder = ops.output_dir()
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)
            return
        if self.files and self.btn_clear.clicked(mouse_pos, True):
            self.files = []
            self.list_view.scroll = 0
            self.result_lines = []
            return
        self.formats.clicked(mouse_pos, True)
        self.engine.clicked(mouse_pos, True)
        if self.runner.running:
            if self.btn_cancel.clicked(mouse_pos, True):
                self.runner.request_cancel()
        elif self.btn_run.clicked(mouse_pos, True) and self.can_run():
            self.start_job()
