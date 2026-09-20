"""PDF 編輯器的頁面繪製:在背景把頁面畫成圖片,畫面只拿已經畫好的。

記憶體用量有上限,最久沒看的先丟掉;放大很多倍時只畫看得到的範圍,避免超大頁面吃掉大量記憶體。
"""

import threading
from collections import OrderedDict

import pygame
from PIL import Image

from core import pdfium

from . import annots, ops

CACHE_BYTES = 200 * 1024 * 1024
MAX_PIXELS = 8_000_000      # 整頁畫出來超過這麼多像素(約 30MB)時,改成只畫看得到的範圍;A4 放大到 300% 左右開始
IMAGE_CACHE = 4


def identity(ref):
    """同一頁、同一個方向的圖可以互相替代(例如還沒畫好的大圖先用縮圖放大頂著)。"""
    return ref.kind, ref.source, ref.index, ref.rotation, ref.size


class PageRenderer:
    def __init__(self, docs):
        self.docs = docs            # 路徑 → PDFium 文件,由編輯器開關
        self._cond = threading.Condition()
        self._wanted = []
        self._busy = None
        self._ready = {}
        self._failed = set()
        self._surfaces = OrderedDict()
        self._bytes = 0
        self._generation = 0
        self._images = OrderedDict()
        threading.Thread(target=self._worker, daemon=True).start()

    @staticmethod
    def key(ref, scale, crop=None, extra=()):
        # 被改過或刪掉的原註解不給 PDFium 畫(extra 是正在拖曳、編輯的);藏起來的註解不同時要重畫
        hidden = annots.hidden_origins(ref)
        if extra:
            hidden = tuple(sorted(set(hidden) | set(extra)))
        return identity(ref) + (round(scale, 4), crop, hidden)

    def clear(self):
        """換檔案時清掉所有圖;還在畫的舊圖畫完也不會放進來。"""
        with self._cond:
            self._generation += 1
            self._wanted.clear()
            self._ready.clear()
            self._failed.clear()
        self._surfaces.clear()
        self._bytes = 0
        self._images.clear()

    def get(self, key):
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
        self._bytes += width * height * 4
        while self._bytes > CACHE_BYTES and len(self._surfaces) > 1:
            _, old = self._surfaces.popitem(last=False)
            self._bytes -= old.get_width() * old.get_height() * 4
        return surface

    def fallback(self, ref):
        """已經畫好、同一頁同方向的圖裡最大的一張(不含只畫局部的);沒有時回傳 None。"""
        ident = identity(ref)
        best = None
        for key, surface in self._surfaces.items():
            if key[:len(ident)] == ident and key[len(ident) + 1] is None:
                if best is None or surface.get_width() > best.get_width():
                    best = surface
        return best

    def failed(self, key):
        with self._cond:
            return key in self._failed

    def want(self, requests):
        """requests:[(key, ref, scale, crop)],排越前面越先畫;不在清單裡的就不畫了。"""
        with self._cond:
            self._wanted = [request for request in requests
                            if request[0] not in self._surfaces and request[0] not in self._ready
                            and request[0] not in self._failed and request[0] != self._busy]
            if self._wanted:
                self._cond.notify()

    def _image(self, path):
        image = self._images.get(path)
        if image is None:
            image = ops.load_image(path)
            self._images[path] = image
            while len(self._images) > IMAGE_CACHE:
                self._images.popitem(last=False)
        self._images.move_to_end(path)
        return image

    def _draw(self, ref, scale, crop, hidden=()):
        if ref.kind == "pdf":
            return pdfium.render(self.docs[ref.source], ref.index, scale, rotation=ref.rotation,
                                 crop=crop or (0, 0, 0, 0), hidden=hidden)
        shown_w, shown_h = ref.shown_size
        left, bottom, right, top = crop or (0, 0, 0, 0)
        size = (max(1, round((shown_w - left - right) * scale)), max(1, round((shown_h - top - bottom) * scale)))
        if ref.kind == "blank":
            return Image.new("RGB", size, (255, 255, 255))
        image = self._image(ref.source)
        if ref.rotation:
            image = image.rotate(-ref.rotation, expand=True)
        if crop:
            # 裁切量是點,換成原圖的像素
            px = image.width / shown_w
            image = image.crop((round(left * px), round(top * px), round(image.width - right * px),
                                round(image.height - bottom * px)))
        return image.resize(size, Image.Resampling.LANCZOS if size[0] < image.width else Image.Resampling.BILINEAR)

    def _worker(self):
        while True:
            with self._cond:
                while not self._wanted:
                    self._cond.wait()
                key, ref, scale, crop = self._wanted.pop(0)
                self._busy = key
                generation = self._generation
            try:
                image = self._draw(ref, scale, crop, key[-1])
                result = (image.width, image.height, image.convert("RGB").tobytes())
                with self._cond:
                    if generation == self._generation:
                        self._ready[key] = result
            except Exception:
                with self._cond:
                    if generation == self._generation:
                        self._failed.add(key)
            finally:
                with self._cond:
                    self._busy = None
