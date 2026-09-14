"""在背景執行緒把 PDF 頁面渲染成縮圖;畫面只拿已經算好的,還沒好的先顯示佔位。"""

import threading
from collections import OrderedDict

import pygame

from core import pdfium


class ThumbnailCache:
    def __init__(self, box_size, capacity=800):
        self.box_w, self.box_h = box_size
        self.capacity = capacity
        self._cond = threading.Condition()
        self._wanted = []
        self._busy = None
        self._ready = {}
        self._failed = set()
        self._surfaces = OrderedDict()
        threading.Thread(target=self._worker, daemon=True).start()

    def get(self, path, number):
        key = (str(path), number)
        surface = self._surfaces.get(key)
        if surface is not None:
            self._surfaces.move_to_end(key)
            return surface
        with self._cond:
            raw = self._ready.pop(key, None)
        if raw is None:
            return None
        width, height, data = raw
        surface = pygame.image.frombytes(data, (width, height), "RGB")
        self._surfaces[key] = surface
        while len(self._surfaces) > self.capacity:
            self._surfaces.popitem(last=False)
        return surface

    def want(self, path, numbers):
        """只渲染目前畫面上看得到的頁;快速捲過去沒停留的頁不會浪費時間算。"""
        path = str(path)
        with self._cond:
            self._wanted = [(path, n) for n in numbers
                            if (path, n) not in self._surfaces and (path, n) not in self._ready
                            and (path, n) not in self._failed and (path, n) != self._busy]
            if self._wanted:
                self._cond.notify()

    def _worker(self):
        docs = {}
        while True:
            with self._cond:
                if not self._wanted:
                    # 閒下來就關檔,否則 Windows 會一直佔用這些 PDF,使用者無法刪除或改名
                    for doc in docs.values():
                        pdfium.close(doc)
                    docs.clear()
                while not self._wanted:
                    self._cond.wait()
                key = self._wanted.pop(0)
                self._busy = key

            path, number = key
            try:
                if path not in docs:
                    docs[path] = pdfium.open_document(path)
                doc = docs[path]
                width, height = pdfium.page_size(doc, number - 1)
                # PDFium 算像素時無條件進位,比例稍微縮一點,縮圖才不會比格子大 1 像素
                zoom = min(self.box_w / max(1, width), self.box_h / max(1, height)) * 0.9999
                image = pdfium.render(doc, number - 1, zoom)
                result = (image.width, image.height, image.tobytes())
                with self._cond:
                    self._ready[key] = result
            except Exception:
                with self._cond:
                    self._failed.add(key)
            finally:
                with self._cond:
                    self._busy = None
