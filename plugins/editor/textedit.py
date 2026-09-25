"""PDF 編輯器的文字編輯:改字(點文字改整段、拖曳選字)、文字框與便利貼的打字、段落與原字型。

這裡是 AnnotController 的一部分(mixin),self 就是 AnnotController。
"""

from dataclasses import replace

import pygame

from core import pdfium, theme

from . import annots, fonts, geometry, paragraphs, pdffonts, pdfwrite
from .textarea import TextEditor, simple_layout

NOTE_POPUP_W = 260


class TextEditMixin:
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

    def _on_text(self, index, point):
        """點的地方是不是原檔的文字(剛好在字上,或在某一段文字的範圍裡)。"""
        ref = self.pages[index]
        doc = self.page.docs.get(ref.source) if ref.kind == "pdf" else None
        if doc is None:
            return False
        if self.paragraph_at(index, point) is not None:
            return True
        lookup = pdfium.TextLookup(doc, ref.index)
        try:
            return lookup.on_text(*geometry.apply(geometry.ref_to_user(ref), point))
        finally:
            lookup.close()

    def _make_replace(self, index, action):
        """選好的原字 → 改字:字級、顏色、字型盡量和原字一樣,底色取原字周圍的顏色,新文字的底線對齊原字。"""
        lookup, ref = action["lookup"], self.pages[index]
        first, last = sorted((action["start"], action["end"]))
        text = "".join(pdffonts.symbol_text(ch) for ch in lookup.text_of(first, last))
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

    def _text_fonts(self, ref, lookup, chars):
        """這些字要用的字型設定:中文字、英數字各自沿用原檔字型(和 Word 一樣分開);回傳 dict,找不到字型時回傳 None。"""
        symbols = []
        for name in dict.fromkeys(c.font for c in chars if pdffonts.symbol_text(c.text) != c.text):
            face = pdffonts.face_for(ref.source, self._source_pdf(ref), ref.index, name) \
                if self._source_pdf(ref) is not None else None
            if face is not None:
                symbols.append(face.id)     # 數學符號這類認不出的字,用原檔的符號字型畫
        found = paragraphs.script_fonts([c for c in chars if pdffonts.symbol_text(c.text) == c.text])
        faces = {}
        for script, (name, sample) in found.items():
            _, _, _, serif, bold, _ = lookup.char_style(sample)
            faces[script] = self._fonts_for(ref, name, serif, bold, script == "cjk")
        main = faces.get("cjk") or faces.get("latin")
        if main is None or main[0] is None:
            return None
        style = dict(font=main[0].id, fallback=main[1], symbols=",".join(symbols))
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
        made = annots.create("replace", rects=rects, box=box,
                             text="".join(pdffonts.symbol_text(ch) for ch in paragraph.text),
                             align=paragraph.align, line_height=paragraph.pitch, offsets=paragraph.offsets,
                             wrap=len(paragraph.lines) > 1, **style)    # 只有一行的(標題、項目)加字時往右延伸
        if made.wrap:
            # 原檔常用字距微調把字排得比字型原本的寬度緊;框至少要放得下原本的每一行,換行位置才會和原檔一樣
            widest = 0.0
            for number, line in enumerate(paragraph.lines):
                sample = replace(made, text="".join(pdffonts.symbol_text(c.text) for c in line.chars).strip(),
                                 box=(box[0], box[1], box[0] + 100000.0, box[3]), align="", offsets=(0.0, 0.0))
                indent = paragraph.offsets[0] if number == 0 else paragraph.offsets[1]
                widest = max(widest, pdfwrite.text_layout(sample)[1].width + indent)
            needed = box[0] + widest + annots.TEXT_PAD * 2 + size * 0.05
            if needed > box[2]:
                made = replace(made, box=(box[0], box[1], needed, box[3]))
        return self.fit(made)

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
                                  hidden=annots.hidden_origins(ref), box=geometry.user_box(ref))
        except Exception:
            return None
        colors = image.getcolors(image.width * image.height)
        return max(colors)[1] if colors else None

    def _draw_paragraph_hover(self, index, mapper):
        """選取工具:滑鼠移到文字上時框出點下去會修改的那一段(滑鼠在註解上時不框)。"""
        mouse = pygame.mouse.get_pos()
        if not mapper.rect.collidepoint(mouse) or not self.page.view_rect.collidepoint(mouse):
            return
        point = mapper.to_page(mouse)
        other = self._annot_at(index, point, images=False)
        if other is not None and other.kind != "replace":
            return
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
