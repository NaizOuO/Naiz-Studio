"""字幕視窗:另一個程式(Naiz Studio.exe --subtitle-overlay 連接埠 通行碼),浮在所有視窗最上層。

- 透明、每個像素各自的透明度(UpdateLayeredWindow):半透明底、平滑的字邊
- 滑鼠點擊會穿過去、不出現在工作列、不搶焦點(玩遊戲時不會把遊戲切到背景)
- 和 Naiz Studio 用本機連線溝通;Naiz Studio 關掉(連線斷了)字幕也跟著關,只能從 Naiz Studio 關閉
- 「調整位置」時暫時可以用滑鼠拖曳(直接問 Windows 滑鼠位置和按鍵,不靠 SDL 的事件:視窗在 SDL 眼中很小,
  點在範圍外的事件會被忽略),結束後把位置傳回去記住
- 字用 Pillow 畫:外框粗細、顏色、字型(含 .ttc 裡的第幾個)都能調。沒選字型時依文字選:中文微軟正黑體、
  日文(有假名)游ゴシック、韓文 Malgun Gothic;缺字時依序找有這個字的字型補上(例如繁體字型沒有日文的「来」)
注意:「獨佔全螢幕」的遊戲蓋不上任何視窗,遊戲要改成「無邊框視窗」。
"""

import json
import os
import secrets
import socket
import subprocess
import sys
import threading

STYLE = {"size": 34, "opacity": 60, "mode": "both", "x": None, "y": None, "color": [255, 255, 255],
         "bg": [12, 14, 18], "outline": 2, "outline_color": [0, 0, 0], "align": "center",
         "font": "", "font_name": "微軟正黑體", "font_path": "", "font_index": 0,
         "fallback_path": "", "fallback_index": 0,      # 缺字時補字的字型(和 PDF 編輯器一樣)
         "count": 2, "fade": 8, "size_original": 20}     # 畫面上最多幾句;講完幾秒沒人說話就淡出(0 是一直顯示)
FADE_TIME = 0.8             # 淡出花的秒數
MODES = [("translation", "只顯示翻譯"), ("both", "原文和翻譯"), ("original", "只顯示原文")]
ALIGNS = [("left", "靠左"), ("center", "置中"), ("right", "靠右")]
FONTS_DIR = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
FALLBACK_FONT = os.path.join(FONTS_DIR, "msjh.ttc")
JAPANESE_FONTS = [("YuGothM.ttc", 0), ("meiryo.ttc", 0)]
KOREAN_FONTS = [("malgun.ttf", 0)]
# 缺字時依序找:繁中、日文、簡中、韓文、符號
BACKUP_FONTS = [("msjh.ttc", 0), ("YuGothM.ttc", 0), ("meiryo.ttc", 0), ("msyh.ttc", 0), ("malgun.ttf", 0),
                ("seguisym.ttf", 0)]
BAND = 0.8                  # 字幕區寬度:主螢幕寬的 80%(對齊靠左、靠右時以這個範圍為準)


# ------------------------------------------------------------ 主程式這邊

class Overlay:
    """在 Naiz Studio 裡控制字幕視窗。on_moved(x, y) 在使用者拖曳完位置後被呼叫。
    樣式、調整模式、最後一次的字幕都記在這裡:字幕視窗還沒連上時送的也不會掉,連上後補送。"""

    def __init__(self, on_moved=None):
        self.on_moved = on_moved
        self._server = None
        self._conn = None
        self._proc = None
        self._lock = threading.Lock()
        self._style = {}
        self._adjust = False
        self._last = None

    @property
    def alive(self):
        return self._proc is not None and self._proc.poll() is None

    def start(self, style):
        self._style = dict(style)
        if self.alive:
            self.send({"type": "style", **self._style})
            return
        token = secrets.token_hex(16)
        self._server = socket.socket()
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self._server.settimeout(20)
        port = self._server.getsockname()[1]
        if getattr(sys, "frozen", False):
            args = [sys.executable, "--subtitle-overlay", str(port), token]
        else:
            script = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                  "naiz_studio.py")
            args = [sys.executable, script, "--subtitle-overlay", str(port), token]
        # 打包後的 exe 開自己時要重設 PyInstaller 的環境,否則會跳「parent process has different executable」
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
        self._proc = subprocess.Popen(args, env=env, creationflags=0x08000000,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        server = self._server

        def accept():
            try:
                conn, _ = server.accept()
                conn.settimeout(None)
                reader = conn.makefile("r", encoding="utf-8")
                if reader.readline().strip() != token:            # 只接受自己開的字幕視窗
                    conn.close()
                    return
                with self._lock:
                    self._conn = conn
                # 連上之前送的都補送一次(樣式、調整模式、最後的字幕)
                self.send({"type": "style", **self._style})
                self.send({"type": "adjust", "on": self._adjust})
                if self._last is not None:
                    self.send(self._last)
                for line in reader:
                    message = json.loads(line)
                    if message.get("type") == "moved" and self.on_moved:
                        self.on_moved(message["x"], message["y"])
            except (OSError, ValueError):
                pass

        threading.Thread(target=accept, daemon=True).start()

    def send(self, message):
        kind = message.get("type")
        if kind == "lines":
            self._last = message
        elif kind == "style":
            self._style = {k: v for k, v in message.items() if k != "type"}
        elif kind == "adjust":
            self._adjust = bool(message.get("on"))
        with self._lock:
            conn = self._conn
        if conn is None:
            return
        try:
            conn.sendall((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass

    def style(self, style):
        self.send({"type": "style", **style})

    def lines(self, items):
        """items:[(原文, 翻譯, 是否定稿, 講完幾秒了)];還在講的那句最後一項是 None。"""
        self.send({"type": "lines", "lines": [{"o": o, "t": t, "f": f, "i": i} for o, t, f, i in items]})

    def adjust(self, on):
        self.send({"type": "adjust", "on": bool(on)})

    def stop(self):
        self.send({"type": "quit"})
        self._adjust = False
        self._last = None
        with self._lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass
        if self._proc is not None:
            try:
                self._proc.wait(2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        if self._server is not None:
            self._server.close()
            self._server = None


# ------------------------------------------------------------ 畫字幕(Pillow)

def _script(text):
    """有假名算日文、有韓文字母算韓文(漢字中日共用,看不出來);其他回傳空字串。"""
    if any("\u3040" <= ch <= "\u30ff" for ch in text):
        return "ja"
    if any("\uac00" <= ch <= "\ud7a3" for ch in text):
        return "ko"
    return ""


class Painter:
    """依樣式把字幕畫成 RGBA 圖片;字型載入一次就留著。"""

    def __init__(self):
        self._fonts = {}
        self._cmaps = {}

    def _font(self, path, index, size):
        from PIL import ImageFont

        key = (path, index, size)
        font = self._fonts.get(key)
        if font is None:
            try:
                font = ImageFont.truetype(path, size, index=index)
            except OSError:
                font = ImageFont.truetype(FALLBACK_FONT, size)
            if len(self._fonts) > 24:
                self._fonts.clear()
            self._fonts[key] = font
        return font

    def _cmap(self, path, index):
        key = (path, index)
        if key not in self._cmaps:
            try:
                from fontTools.ttLib import TTFont

                font = TTFont(path, fontNumber=index, lazy=True)
                self._cmaps[key] = set((font.getBestCmap() or {}).keys())
                font.close()
            except Exception:
                self._cmaps[key] = None              # 讀不到就當作什麼字都有
        return self._cmaps[key]

    @staticmethod
    def _chain(text, style):
        """這段文字依序要試的字型:[(路徑, 第幾個)]。選了字型就先用它;沒選的話日文、韓文先用該語言的字型。"""
        system = [(os.path.join(FONTS_DIR, name), index) for name, index in BACKUP_FONTS]
        chain = []
        if style.get("font_path"):
            chain.append((style["font_path"], int(style.get("font_index") or 0)))
        script = style.get("script") or _script(text)
        if script == "ja":
            chain += [(os.path.join(FONTS_DIR, name), index) for name, index in JAPANESE_FONTS]
        elif script == "ko":
            chain += [(os.path.join(FONTS_DIR, name), index) for name, index in KOREAN_FONTS]
        chain.append(system[0])
        if style.get("fallback_path"):
            chain.append((style["fallback_path"], int(style.get("fallback_index") or 0)))
        chain += system[1:]
        result = []
        for item in chain:
            if item not in result and os.path.isfile(item[0]):
                result.append(item)
        return result or [(FALLBACK_FONT, 0)]

    def _runs(self, text, style, size):
        """依字型有沒有這個字切成一段一段:[(字型, 文字)];第一個字型沒有的字往後找有這個字的字型。"""
        chain = self._chain(text, style)
        runs = []
        for ch in text:
            choice = chain[0]
            if not ch.isspace():
                for item in chain:
                    cmap = self._cmap(*item)
                    if cmap is None or ord(ch) in cmap:
                        choice = item
                        break
            font = self._font(choice[0], choice[1], size)
            if runs and runs[-1][0] is font:
                runs[-1] = (font, runs[-1][1] + ch)
            else:
                runs.append((font, ch))
        return runs

    def _width(self, runs):
        return sum(font.getlength(text) for font, text in runs)

    def _wrap(self, text, style, size, width):
        """依寬度換行,每段最多兩行(太長只留最後兩行);標點不放在行首。"""
        rows, current = [], ""
        for ch in text:
            trial = current + ch
            if current and self._width(self._runs(trial, style, size)) > width:
                space = current.rfind(" ")
                if ch.isascii() and ch.isalnum() and space > 0 and current[space + 1:].isascii():
                    rows.append(current[:space])                 # 英文單字不從中間切開
                    current = current[space + 1:] + ch
                elif ch in "，。、！？；：）」』,.!?;:)" and len(current) > 1:
                    rows.append(current[:-1])
                    current = current[-1] + ch
                else:
                    rows.append(current)
                    current = ch.lstrip()
            else:
                current = trial
        if current:
            rows.append(current)
        return rows[-2:]

    def paint(self, lines, style, band_width, adjusting):
        from PIL import Image, ImageDraw

        big = max(10, int(style.get("size", 34)))
        # 原文和翻譯一起顯示時原文用自己的大小;只顯示原文時原文就是主要的字,用大字
        small = max(10, int(style.get("size_original") or big * 0.6))
        outline = max(0, int(style.get("outline", 2)))
        color = tuple(style.get("color") or (255, 255, 255))
        dim = tuple(int(c * 0.82) for c in color)
        edge = tuple(style.get("outline_color") or (0, 0, 0))
        mode = style.get("mode", "both")
        inner = band_width - 48 - outline * 2
        blocks = []
        shown = lines or ([{"o": "字幕會顯示在這裡", "t": "拖曳這個框移動位置", "f": True}] if adjusting else [])
        for item in shown[-max(1, int(style.get("count", 2))):]:
            rows = []
            final = item.get("f", True)
            # 整句先判斷是哪種文字,換行後每一行都用同一套字型(只有一行有假名時字型才不會不一致)
            o_style = dict(style, script=_script(item.get("o") or ""))
            t_style = dict(style, script=_script(item.get("t") or ""))
            if mode == "original" and item.get("o"):
                rows += [(row, big, color if final else dim, o_style)
                         for row in self._wrap(item["o"], o_style, big, inner)]
            if mode == "both" and item.get("o"):
                rows += [(row, small, dim, o_style) for row in self._wrap(item["o"], o_style, small, inner)]
            if mode in ("both", "translation") and item.get("t"):
                rows += [(row, big, color if final else dim, t_style)
                         for row in self._wrap(item["t"], t_style, big, inner)]
            if mode == "translation" and not item.get("t") and item.get("o"):
                rows += [(row, small, dim, o_style) for row in self._wrap(item["o"], o_style, small, inner)]
            if rows:
                blocks.append((rows, float(item.get("a", 1.0))))
        if not blocks and not adjusting:
            return None
        gap, pad_x, pad_y = 10, 20, 10
        heights = [sum(int(size * 1.35) for _, size, _, _ in rows) + pad_y * 2 for rows, _ in blocks]
        height = max(sum(heights) + gap * max(0, len(blocks) - 1), big * 2)
        image = Image.new("RGBA", (band_width, height + 4), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        bg = tuple(style.get("bg") or (12, 14, 18)) + (int(255 * style.get("opacity", 60) / 100),)
        align = style.get("align", "center")
        y = 0
        for (rows, alpha), block_h in zip(blocks, heights):
            widths = [self._width(self._runs(text, row_style, size)) + outline * 2
                      for text, size, _, row_style in rows]
            block_w = int(max(widths)) + pad_x * 2
            left = {"left": 0, "right": band_width - block_w}.get(align, (band_width - block_w) // 2)
            # 每一句畫在自己的一層,淡出時整層一起變透明(底色、字、外框一起淡)
            layer = Image.new("RGBA", (block_w, block_h), (0, 0, 0, 0))
            pen = ImageDraw.Draw(layer)
            if bg[3]:
                pen.rounded_rectangle((0, 0, block_w - 1, block_h - 1), radius=12, fill=bg)
            row_y = pad_y
            for (text, size, fill, row_style), row_w in zip(rows, widths):
                x = {"left": pad_x, "right": block_w - pad_x - row_w}.get(align, (block_w - row_w) / 2)
                x += outline
                for font, part in self._runs(text, row_style, size):
                    pen.text((x, row_y + outline), part, font=font, fill=fill + (255,),
                             stroke_width=outline, stroke_fill=edge + (255,))
                    x += font.getlength(part)
                row_y += int(size * 1.35)
            if alpha < 1:
                layer.putalpha(layer.getchannel("A").point(lambda v, a=alpha: int(v * a)))
            image.alpha_composite(layer, (int(left), y))
            y += block_h + gap
        if adjusting:
            # 調整位置:只畫虛線框和說明,字幕本身不變色
            accent = (104, 196, 170, 255)
            w, h = image.size
            for x in range(0, w, 16):
                draw.line((x, 0, min(x + 8, w - 1), 0), fill=accent, width=3)
                draw.line((x, h - 1, min(x + 8, w - 1), h - 1), fill=accent, width=3)
            for yy in range(0, h, 16):
                draw.line((0, yy, 0, min(yy + 8, h - 1)), fill=accent, width=3)
                draw.line((w - 1, yy, w - 1, min(yy + 8, h - 1)), fill=accent, width=3)
            veil = Image.new("RGBA", image.size, (104, 196, 170, 18))       # 很淡的底,整個框都點得到
            image = Image.alpha_composite(veil, image)
        return image


# ------------------------------------------------------------ 字幕視窗程式

def fading(lines, style, elapsed):
    """依「講完幾秒了」算每句的透明度:超過設定的秒數後在 FADE_TIME 秒內淡出,淡完就不顯示。
    lines 裡每句的 i 是送來時已經講完幾秒(還在講的是 None);elapsed 是送來之後又過了幾秒。"""
    fade = float(style.get("fade") or 0)
    result = []
    for item in lines:
        idle = item.get("i")
        alpha = 1.0
        if fade > 0 and idle is not None:
            over = idle + elapsed - fade
            if over > 0:
                alpha = round(max(0.0, 1 - over / FADE_TIME), 2)
        if alpha > 0:
            result.append(dict(item, a=alpha) if alpha < 1 else item)
    return result


def run(port, token, mouse=None):
    """字幕視窗程式的進入點;出錯時寫進 subtitle_error.log(這個程式沒有畫面可以顯示錯誤)。
    mouse() 回傳 (x, y, 左鍵是否按著);沒給時直接問 Windows(測試時換成模擬的滑鼠,不會動到真的滑鼠)。"""
    try:
        _run(port, token, mouse)
    except Exception:
        import traceback

        from core import paths

        try:
            (paths.APP_DIR / "subtitle_error.log").write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        raise


def _run(port, token, mouse=None):
    import ctypes
    import time
    from ctypes import wintypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)          # 高解析度螢幕上位置和大小才準
    except Exception:
        pass
    os.environ["SDL_VIDEO_WINDOW_POS"] = "-32000,-32000"
    import numpy
    import pygame

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    # 64 位元的控制代碼一定要宣告型別,否則會被截斷成 32 位元而出錯
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_uint]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p, wintypes.HDC,
                                           ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                       wintypes.HANDLE, wintypes.DWORD]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("op", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("alpha", ctypes.c_ubyte),
                    ("format", ctypes.c_ubyte)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    conn = socket.create_connection(("127.0.0.1", port), timeout=10)
    conn.sendall((token + "\n").encode())
    conn.settimeout(None)
    inbox, lock = [], threading.Lock()
    closed = threading.Event()

    def receive():
        try:
            for line in conn.makefile("r", encoding="utf-8"):
                with lock:
                    inbox.append(json.loads(line))
        except (OSError, ValueError):
            pass
        closed.set()                                            # Naiz Studio 關了:字幕跟著關

    threading.Thread(target=receive, daemon=True).start()

    pygame.init()
    pygame.display.set_mode((8, 8), pygame.NOFRAME | pygame.HIDDEN)   # 先隱藏建立,不搶焦點
    pygame.display.set_caption("Naiz Studio 字幕")
    hwnd = pygame.display.get_wm_info()["window"]
    base_style = (user32.GetWindowLongPtrW(hwnd, -20) | 0x80000 | 0x8 | 0x80 | 0x08000000) & ~0x40000

    def clickable(on):
        style = base_style if on else base_style | 0x20             # WS_EX_TRANSPARENT:點擊穿過去
        user32.SetWindowLongPtrW(hwnd, -20, style)
        user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x10 | 0x20)

    clickable(False)
    user32.ShowWindow(hwnd, 4)                                       # 顯示但不啟用

    work = wintypes.RECT()
    user32.SystemParametersInfoW(0x30, 0, ctypes.byref(work), 0)     # 主螢幕扣掉工作列的範圍
    screen_w = work.right - work.left
    band_width = int(screen_w * BAND)

    def present(image, x, y):
        w, h = image.size
        rgba = numpy.asarray(image, numpy.uint8)[::-1]                  # DIB 是由下往上
        alpha = rgba[..., 3:4].astype(numpy.uint16)
        bgra = numpy.empty_like(rgba)
        bgra[..., 0:3] = (rgba[..., 2::-1].astype(numpy.uint16) * alpha // 255).astype(numpy.uint8)
        bgra[..., 3] = rgba[..., 3]
        data = numpy.ascontiguousarray(bgra).tobytes()
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, h, 1, 32, 0, 0, 0, 0, 0, 0)
        screen_dc = user32.GetDC(None)
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(screen_dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        ctypes.memmove(bits, data, len(data))
        old = gdi32.SelectObject(memory_dc, bitmap)
        user32.UpdateLayeredWindow(hwnd, screen_dc, ctypes.byref(wintypes.POINT(int(x), int(y))),
                                   ctypes.byref(wintypes.SIZE(w, h)), memory_dc, ctypes.byref(wintypes.POINT(0, 0)),
                                   0, ctypes.byref(BLENDFUNCTION(0, 0, 255, 1)), 2)
        gdi32.SelectObject(memory_dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)

    style = dict(STYLE)
    painter = Painter()
    lines = []
    received = time.monotonic()         # 收到這批字幕的時間:「講完幾秒了」從這裡繼續算
    adjusting = False
    drag = None
    last_key = None
    size = (1, 1)

    # 位置記的是字幕區「底部中間」那一點:字變多時往上長,底部不動。預設在主螢幕下方
    def default_anchor():
        if style.get("x") is not None and style.get("y") is not None:
            return [int(style["x"]), int(style["y"])]
        return [work.left + screen_w // 2, work.bottom - int((work.bottom - work.top) * 0.08)]

    def windows_mouse():
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y, bool(user32.GetAsyncKeyState(0x01) & 0x8000)

    mouse = mouse or windows_mouse

    anchor = default_anchor()
    from PIL import Image

    while not closed.is_set():
        with lock:
            messages, inbox[:] = list(inbox), []
        for message in messages:
            kind = message.get("type")
            if kind == "quit":
                closed.set()
            elif kind == "lines":
                lines = message["lines"]
                received = time.monotonic()
            elif kind == "style":
                moved = (message.get("x"), message.get("y")) != (style.get("x"), style.get("y"))
                style.update({k: v for k, v in message.items() if k in STYLE})
                if moved and drag is None:
                    anchor = default_anchor()
            elif kind == "adjust":
                on = bool(message.get("on"))
                if on != adjusting:
                    adjusting = on
                    drag = None
                    clickable(adjusting)
                    if not adjusting:
                        style["x"], style["y"] = anchor
                        try:
                            conn.sendall((json.dumps({"type": "moved", "x": anchor[0], "y": anchor[1]}) + "\n").encode())
                        except OSError:
                            pass
        pygame.event.pump()
        if adjusting:
            x, y, pressed = mouse()
            left, top = anchor[0] - size[0] // 2, anchor[1] - size[1]
            inside = left <= x < left + size[0] and top <= y < top + size[1]
            if pressed and drag is None and inside:
                drag = (x - anchor[0], y - anchor[1])
            elif not pressed:
                drag = None
            if drag is not None:
                anchor = [x - drag[0], y - drag[1]]
        visible = fading(lines, style, time.monotonic() - received)
        key = (json.dumps(visible, ensure_ascii=False), json.dumps(style, ensure_ascii=False), adjusting,
               tuple(anchor))
        if key != last_key:
            last_key = key
            image = painter.paint(visible, style, band_width, adjusting) or Image.new("RGBA", (1, 1))
            size = image.size
            present(image, anchor[0] - size[0] // 2, anchor[1] - size[1])
        time.sleep(1 / 60 if drag is not None else 1 / 30)
    pygame.quit()
