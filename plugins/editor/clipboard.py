"""PDF 編輯器的複製、剪下、貼上與右鍵選單;圖片同時放進 Windows 剪貼簿,可以和其他程式互相貼上。

這裡是 AnnotController 的一部分(mixin),self 就是 AnnotController。
"""

from dataclasses import replace

import pygame

from core import imageclip, pdfium, theme, widgets

from . import annots, pdfwrite

IMAGE_DPI = 150             # 放進來的圖片預設以這個解析度換算大小
TEXTBOX_W = 200.0
PASTE_OFFSET = 12.0         # 用鍵盤貼上、滑鼠又不在頁面上時,貼在原本位置往右下偏移這麼多(點)


class ClipboardMixin:
    def _context(self, pos):
        """右鍵選單:複製、剪下、貼上、刪除;正在打字時是文字的複製、剪下、貼上。"""
        if self.editing is not None:
            editor = self.editing["editor"]

            def key(code):
                return lambda: editor.handle(pygame.event.Event(pygame.KEYDOWN, key=code, mod=pygame.KMOD_CTRL,
                                                                unicode=""), self._edit_layout())

            start, end = editor.selection
            self.menu.open(pos, [("複製", "Ctrl+C", start != end, key(pygame.K_c)),
                                 ("剪下", "Ctrl+X", start != end, key(pygame.K_x)),
                                 ("貼上", "Ctrl+V", True, key(pygame.K_v))])
            return
        index = self.page_at(pos)
        found = None
        if index is not None:
            annot = self._annot_at(index, self.mapper(index).to_page(pos))
            if annot is not None and annots.editable(annot):
                self.selected = (self.pages[index].uid, annot.uid)
                found = annot
            elif annot is None:
                self.selected = None
        can_copy = found is not None and found.kind not in ("replace", "other")
        self.menu.open(pos, [("複製", "Ctrl+C", can_copy, self.copy_selected),
                             ("剪下", "Ctrl+X", can_copy, lambda: self.copy_selected(cut=True)),
                             ("貼上", "Ctrl+V", True, lambda: self.paste(pos)),
                             ("刪除", "Delete", found is not None, self.delete_selected)])

    def copy_selected(self, cut=False):
        """複製選取的註解;圖片(含原檔的圖片)同時放進 Windows 剪貼簿,可以貼到其他程式。"""
        found = self.selected_annot()
        if found is None:
            return
        index, annot = found
        if annot.kind in ("replace", "other"):
            return
        ref = self.pages[index]
        if annot.kind == annots.PAGE_IMAGE:
            data = self._original_picture(ref, annot)
            if not data:
                self.page.notify("這張圖片讀不出來，沒辦法複製", theme.WARN)
                return
            from PIL import Image
            import io

            with Image.open(io.BytesIO(data)) as picture:
                pixels = picture.size
            annot = annots.Annot("image", box=annot.box, image=data, pixels=pixels,
                                 color=annots.DEFAULT_COLORS["image"])
        if annot.image:
            imageclip.copy_image(annot.image)
        elif annot.text:
            widgets.copy_to_clipboard(annot.text)
        self._clip = dict(annot=annot, page_uid=ref.uid, sequence=imageclip.sequence())
        if cut:
            self.delete_selected()
        else:
            self.page.notify(f"已複製{annots.LABELS[annot.kind]}", theme.ACCENT)

    def _original_picture(self, ref, annot):
        """原檔圖片的原始像素(PNG);讀不到原始資料時(例如圖片在頁面上轉過)改用畫出來的圖。"""
        source = self._source_pdf(ref)
        if source is not None:
            try:
                data = pdfwrite.page_image(source.pages[ref.index], annot.number)
            except Exception:
                data = None
            if data:
                return data
        doc = self.page.docs.get(ref.source) if ref.kind == "pdf" else None
        try:
            return pdfium.image_png(doc, ref.index, annot.number, max_side=None) if doc is not None else b""
        except Exception:
            return b""

    def _paste_place(self, pos):
        """貼上的位置:(頁面索引, 頁面座標);滑鼠在頁面上就貼在滑鼠那裡。"""
        pos = pos or pygame.mouse.get_pos()
        index = self.page_at(pos) if self.page.view_rect.collidepoint(pos) else None
        if index is not None:
            return index, self.mapper(index).to_page(pos)
        return None, None

    def paste(self, pos=None):
        """貼上:剪貼簿還是自己複製的註解就貼那個,否則看剪貼簿裡是圖片還是文字。"""
        if not self.pages:
            return False
        index, point = self._paste_place(pos)
        clip = self._clip
        if clip is not None and clip["sequence"] == imageclip.sequence():
            annot = clip["annot"]
            source = self.index_of(clip["page_uid"])
            if index is None:
                index = source if source is not None else self.page.current_index()
                dx = dy = PASTE_OFFSET
            else:
                x0, y0, x1, y1 = annots.bounds(annot)
                dx, dy = point[0] - (x0 + x1) / 2, point[1] - (y0 + y1) / 2
            copy = annots.moved(replace(annot, uid=next(annots._ids), origin=-1, subtype=""), dx, dy)
            self.add(index, copy)
            self._clip = dict(clip, annot=copy)             # 連續貼上時一個接一個往下排
            self.tool = "select"
            return True
        if index is None:
            index = self.page.current_index()
            width, height = self.pages[index].size
            point = (width / 2, height / 2)
        data = imageclip.paste_image()
        if data:
            from PIL import Image
            import io

            with Image.open(io.BytesIO(data)) as picture:
                pixels = picture.size
            width, height = pixels[0] * 72 / IMAGE_DPI, pixels[1] * 72 / IMAGE_DPI
            page_w, page_h = self.pages[index].size
            fit = min(1.0, page_w * 0.8 / width, page_h * 0.8 / height)
            width, height = width * fit, height * fit
            left = max(0.0, min(page_w - width, point[0] - width / 2))
            top = max(0.0, min(page_h - height, point[1] - height / 2))
            self.add(index, annots.create("image", box=(left, top, left + width, top + height), image=data,
                                          pixels=pixels, **self.style("image")))
            self.tool = "select"
            return True
        text = widgets._clipboard_text().strip()
        if text:
            font = self.textbox_font()
            if font is None:
                return True
            page_w = self.pages[index].size[0]
            left = max(4.0, min(page_w - TEXTBOX_W - 4, point[0]))
            style = dict(self.style("textbox"), font=font)
            box = (left, point[1], left + TEXTBOX_W, point[1] + 20)
            self.add(index, self.fit(annots.create("textbox", box=box, text=text, **style)))
            self.tool = "select"
            return True
        self.page.notify("剪貼簿裡沒有可以貼上的圖片或文字", theme.WARN)
        return True
