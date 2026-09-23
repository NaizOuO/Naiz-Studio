"""文字框用的字型。

三種來源:
- 開源字型包:需要時才下載到 fonts/downloads/(和其他元件一樣先詢問,並以 SHA-256 驗證)
- 電腦已安裝的字型:依字型檔裡的嵌入權限(OS/2 fsType)決定能不能用
- 自己加入的字型:複製到 fonts/custom/

也負責缺字時改用中文字型補、文字排版(自動換行),以及畫面預覽。排版用的字寬和儲存時寫進 PDF 的一致。
"""

import json
import logging
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont

from core import deps, paths
from core.files import atomic_path

from .annots import BASELINE, LINE_HEIGHT

logging.getLogger("fontTools").setLevel(logging.ERROR)     # 讀到格式不太標準的字型時的提醒,對使用者沒有意義

FONT_EXTS = {".ttf", ".otf", ".ttc", ".otc"}
WEIGHT = 400
PIL_CACHE = 16
_windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
_local = os.environ.get("LOCALAPPDATA")
SYSTEM_DIRS = [_windir / "Fonts"] + ([Path(_local) / "Microsoft" / "Windows" / "Fonts"] if _local else [])
FALLBACK_SYSTEM = ["msjh.ttc", "mingliu.ttc", "kaiu.ttf"]     # 中文補字依序找這些(微軟正黑體、細明體、標楷體)


def downloads_dir():
    return paths.FONTS_DIR / "downloads"


def custom_dir():
    return paths.FONTS_DIR / "custom"


# ------------------------------------------------------------ 開源字型包

_GOOGLE = "https://raw.githubusercontent.com/google/fonts/{commit}/ofl/{folder}/{file}"
_LXGW = "https://github.com/lxgw/LxgwWenkaiTC/releases/download/v1.522/{file}"
_OFL = "開源字型（SIL Open Font License），可以自由嵌入 PDF"


def _dep(key, name, url, file, size_text, sha):
    return deps.Dependency(id=f"font_{key}", name=name, purpose=_OFL, size_text=size_text, url=url,
                           files={file: None}, sha256=sha, location="fonts")


def _google(key, name, commit, folder, file, size_text, sha):
    url = _GOOGLE.format(commit=commit, folder=folder, file=file.replace("[", "%5B").replace("]", "%5D"))
    return _dep(key, name, url, file, size_text, sha)


_LIBERATION_FILES = [f"Liberation{family}-{style}.ttf" for family in ("Sans", "Serif")
                     for style in ("Regular", "Bold", "Italic", "BoldItalic")]
LIBERATION = deps.Dependency(
    id="font_liberation", name="Liberation 字型", purpose=_OFL, size_text="約 2.3 MB",
    url="https://github.com/liberationfonts/liberation-fonts/files/7261482/liberation-fonts-ttf-2.1.5.tar.gz",
    files={name: name for name in _LIBERATION_FILES},
    sha256="7191c669bf38899f73a2094ed00f7b800553364f90e2637010a69c0e268f25d0", location="fonts")
_CARLITO = "3dd78844021e948ceb633d1dcee3f7885561b5d9"
_CARLITO_SHA = {"Regular": ("0.6", "f6418f708baede9789daef5d458c0f53d2a888af9820e8062934e504fedc6595"),
                "Bold": ("0.7", "bb5d20f79b82599ec72983597437373a80f2d2085fa91fc144fd74e876a594db"),
                "Italic": ("0.6", "0b019225e58d702bfedcbd35c21696769f8ee115cb6343f84c2f240312450d1c"),
                "BoldItalic": ("0.8", "b32928186c119599e03ca6a1ffc680fdcb7fac95772f4b95d989cf6cd3861517")}
_STYLE_NAMES = {"Regular": "", "Bold": " 粗體", "Italic": " 斜體", "BoldItalic": " 粗斜體"}

# (代號, 顯示名稱, 說明, 下載元件, 檔名)
PACKS = [
    ("noto_sans_tc", "Noto Sans TC 黑體", "繁體中文，適合簡報、螢幕閱讀",
     _google("noto_sans_tc", "Noto Sans TC 黑體", "3be1884c48c3e45b52ecc725676a08f87776373e", "notosanstc",
             "NotoSansTC[wght].ttf", "約 11.4 MB", "864727d210d54f2537bbe23b3a839436c3992af72de9322af5270897246bd44f"),
     "NotoSansTC[wght].ttf"),
    ("noto_serif_tc", "Noto Serif TC 明體", "繁體中文，適合文書、報告",
     _google("noto_serif_tc", "Noto Serif TC 明體", "8b0a1d0f5983c89bc2b93f1b5fb55f9e252744b5", "notoseriftc",
             "NotoSerifTC[wght].ttf", "約 16.1 MB", "0077e18f57c6908f4a000969880940bdb0dad057c0e8d98b49dc364c3d1b09c6"),
     "NotoSerifTC[wght].ttf"),
    ("lxgw_wenkai_tc", "霞鶩文楷 TC", "繁體中文楷體，風格接近標楷體",
     _dep("lxgw_wenkai_tc", "霞鶩文楷 TC", _LXGW.format(file="LXGWWenKaiTC-Regular.ttf"), "LXGWWenKaiTC-Regular.ttf",
          "約 14.6 MB", "b1a0795862c1415bf3f393ea50b2a4ea6275012cf5bad3f94feeb1222f555731"),
     "LXGWWenKaiTC-Regular.ttf"),
    ("lxgw_wenkai_tc_medium", "霞鶩文楷 TC 粗體", "較粗的楷體，適合標題",
     _dep("lxgw_wenkai_tc_medium", "霞鶩文楷 TC 粗體", _LXGW.format(file="LXGWWenKaiTC-Medium.ttf"),
          "LXGWWenKaiTC-Medium.ttf", "約 14.3 MB", "94ca2870022fb8e4f90e2887524603690598142d17b620dfae318f1818ba8e17"),
     "LXGWWenKaiTC-Medium.ttf"),
    ("noto_sans_sc", "Noto Sans SC 黑体", "簡體中文黑體",
     _google("noto_sans_sc", "Noto Sans SC 黑体", "a85815a42757630ce188fdad368c2dfc444d4773", "notosanssc",
             "NotoSansSC[wght].ttf", "約 16.9 MB", "a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da"),
     "NotoSansSC[wght].ttf"),
    ("noto_serif_sc", "Noto Serif SC 宋体", "簡體中文宋體（明體）",
     _google("noto_serif_sc", "Noto Serif SC 宋体", "8b0a1d0f5983c89bc2b93f1b5fb55f9e252744b5", "notoserifsc",
             "NotoSerifSC[wght].ttf", "約 24.0 MB", "050080d9255a86808f2945bffac582b31ef32bc36411ce29563b4961670c66f9"),
     "NotoSerifSC[wght].ttf"),
] + [
    (f"carlito_{style.lower()}", f"Carlito{_STYLE_NAMES[style]}", "英文，字寬和 Calibri 一樣，適合簡報",
     _google(f"carlito_{style.lower()}", f"Carlito{_STYLE_NAMES[style]}", _CARLITO, "carlito", f"Carlito-{style}.ttf",
             f"約 {_CARLITO_SHA[style][0]} MB", _CARLITO_SHA[style][1]),
     f"Carlito-{style}.ttf")
    for style in ("Regular", "Bold", "Italic", "BoldItalic")
] + [
    (f"liberation_{family.lower()}_{style.lower()}", f"Liberation {family}{_STYLE_NAMES[style]}",
     "英文，字寬和 Arial 一樣" if family == "Sans" else "英文，字寬和 Times New Roman 一樣，適合文書、論文",
     LIBERATION, f"Liberation{family}-{style}.ttf")
    for family in ("Sans", "Serif") for style in ("Regular", "Bold", "Italic", "BoldItalic")
] + [
    ("noto_sans_mono", "Noto Sans Mono", "英文等寬字型，適合程式碼、數據",
     _google("noto_sans_mono", "Noto Sans Mono", "097bc1b8c04c3224087c2ae95f7a923859b778cf", "notosansmono",
             "NotoSansMono[wdth,wght].ttf", "約 1.6 MB",
             "2cb2adb378a8f574213e23df697050b83c54c27df465a2015552740b2769a081"),
     "NotoSansMono[wdth,wght].ttf"),
]


# ------------------------------------------------------------ 字型

@dataclass(eq=False)
class FontFace:
    id: str                 # pack:代號、file:完整路徑#第幾個、custom:檔名#第幾個
    name: str
    group: str              # pack / system / custom
    path: Path
    index: int = 0
    embed: str = "ok"       # ok 可以嵌入;preview 只能預覽列印;no 不允許嵌入
    note: str = ""
    dependency: object = None

    @property
    def installed(self):
        return self.path.is_file()

    @property
    def usable(self):
        return self.installed and self.embed != "no"


def embed_status(fs_type):
    """字型檔的嵌入權限。同時設定多種時以限制最少的為準。"""
    fs_type = int(fs_type or 0)
    if fs_type & 0x0200:        # 只允許嵌入點陣圖,沒辦法用在 PDF 文字
        return "no"
    level = fs_type & 0x000F
    if level == 0 or level & 0x8:
        return "ok"
    if level & 0x4:
        return "preview"
    return "no"


def _best_name(name_table):
    records = {}
    for record in name_table.names:
        if record.platformID != 3:
            continue
        try:
            records[(record.nameID, record.langID)] = record.toUnicode()
        except Exception:
            continue
    for lang in (0x0404, 0x0C04, 0x1004, 0x0804, 0x0409):
        if (4, lang) in records:
            return records[(4, lang)]
    for lang in (0x0404, 0x0C04, 0x1004, 0x0804, 0x0409):
        if (1, lang) in records:
            style = records.get((2, lang), "")
            return f"{records[(1, lang)]} {style}".strip() if style not in ("Regular", "標準", "") else records[(1, lang)]
    full = [text for (name_id, _), text in records.items() if name_id == 4]
    return full[0] if full else ""


def read_faces(path):
    """字型檔裡每個字型的 (第幾個, 名稱, fsType);讀不了時回傳空清單。"""
    from fontTools.ttLib import TTCollection, TTFont

    path = Path(path)
    try:
        if path.suffix.lower() in (".ttc", ".otc"):
            collection = TTCollection(str(path), lazy=True)
            fonts = list(collection.fonts)
        else:
            fonts = [TTFont(str(path), lazy=True)]
        result = []
        for index, font in enumerate(fonts):
            if "glyf" not in font and "CFF " not in font and "CFF2" not in font:
                continue
            name = _best_name(font["name"]) if "name" in font else ""
            fs_type = font["OS/2"].fsType if "OS/2" in font else 0
            result.append((index, name or path.stem, int(fs_type)))
        for font in fonts:
            font.close()
        return result
    except Exception:
        return []


class FontData:
    """一個字型實際載入後的資料:有哪些字、每個字多寬、畫面預覽用的 Pillow 字型。"""

    def __init__(self, face):
        from fontTools.ttLib import TTFont

        self.face = face
        font = TTFont(str(face.path), fontNumber=face.index, lazy=True)
        try:
            self.cmap = set((font.getBestCmap() or {}).keys())
            self.axes = [(axis.axisTag, axis.defaultValue, axis.minValue, axis.maxValue)
                         for axis in font["fvar"].axes] if "fvar" in font else []
        finally:
            font.close()
        self._lock = threading.Lock()
        self._pil = {}
        self._advances = {}
        self._measure = self._load(1000)

    def variation(self):
        """可變字型固定用一般字重(400);其他軸用預設值。"""
        return {tag: (max(low, min(high, WEIGHT)) if tag == "wght" else default)
                for tag, default, low, high in self.axes}

    def _load(self, size):
        font = ImageFont.truetype(str(self.face.path), size, index=self.face.index)
        if self.axes:
            values = self.variation()
            font.set_variation_by_axes([values[tag] for tag, _, _, _ in self.axes])
        return font

    def pil(self, size):
        size = max(1, int(round(size)))
        with self._lock:
            font = self._pil.get(size)
            if font is None:
                if len(self._pil) >= PIL_CACHE:
                    self._pil.pop(next(iter(self._pil)))
                font = self._pil[size] = self._load(size)
            return font

    def has(self, ch):
        return ord(ch) in self.cmap

    def advance(self, ch):
        """字寬,單位是字型大小的倍數。"""
        with self._lock:
            value = self._advances.get(ch)
            if value is None:
                value = self._advances[ch] = self._measure.getlength(ch) / 1000
            return value


_data = {}
_data_lock = threading.Lock()


def data(face):
    with _data_lock:
        item = _data.get(face.id)
        if item is None or item.face.path != face.path:
            item = _data[face.id] = FontData(face)
        return item


class Catalog:
    def __init__(self):
        self._packs = [FontFace(f"pack:{key}", name, "pack", downloads_dir() / file, note=note, dependency=dep)
                       for key, name, note, dep, file in PACKS]
        self.system = []
        self.scan_state = "idle"        # idle / scanning / ready
        self._custom_cache = {}
        self._lock = threading.Lock()

    def packs(self):
        for face in self._packs:        # 測試或設定可能換過資料夾位置
            face.path = downloads_dir() / face.path.name
        return list(self._packs)

    # ------------------------------------------------------------ 電腦已安裝的字型

    def start_scan(self):
        with self._lock:
            if self.scan_state != "idle":
                return
            self.scan_state = "scanning"
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        cache_path = paths.FONTS_DIR / "system_fonts.json"
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        faces, fresh, seen = [], {}, set()
        for folder in SYSTEM_DIRS:
            try:
                entries = sorted(Path(folder).iterdir())
            except OSError:
                continue
            for path in entries:
                if path.suffix.lower() not in FONT_EXTS or not path.is_file():
                    continue
                key = str(path).lower()
                if key in seen:
                    continue
                seen.add(key)
                stat = path.stat()
                stamp = [stat.st_size, int(stat.st_mtime)]
                entry = cache.get(str(path))
                if entry is None or entry.get("stamp") != stamp:
                    entry = {"stamp": stamp, "faces": read_faces(path)}
                fresh[str(path)] = entry
                for index, name, fs_type in entry["faces"]:
                    faces.append(FontFace(f"file:{path}#{index}", name, "system", path, index, embed_status(fs_type)))
        faces.sort(key=lambda face: face.name.lower())
        if fresh != cache:
            try:
                paths.FONTS_DIR.mkdir(parents=True, exist_ok=True)
                with atomic_path(cache_path) as temp:
                    Path(temp).write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
        with self._lock:
            self.system = faces
            self.scan_state = "ready"

    # ------------------------------------------------------------ 自己加入的字型

    def custom(self):
        folder = custom_dir()
        try:
            entries = sorted(p for p in folder.iterdir() if p.suffix.lower() in FONT_EXTS and p.is_file())
        except OSError:
            return []
        faces = []
        for path in entries:
            stat = path.stat()
            stamp = (stat.st_size, int(stat.st_mtime))
            cached = self._custom_cache.get(path.name)
            if cached is None or cached[0] != stamp:
                cached = self._custom_cache[path.name] = (stamp, read_faces(path))
            for index, name, fs_type in cached[1]:
                faces.append(FontFace(f"custom:{path.name}#{index}", name, "custom", path, index,
                                      embed_status(fs_type)))
        return faces

    def add_custom(self, source):
        """把字型檔複製到 fonts/custom/;回傳加入的字型。不是能用的字型檔時丟 ValueError。"""
        source = Path(source)
        if source.suffix.lower() not in FONT_EXTS:
            raise ValueError(f"「{source.name}」不是字型檔（支援 .ttf、.otf、.ttc）")
        found = read_faces(source)
        if not found:
            raise ValueError(f"「{source.name}」讀不到字型，可能已損壞")
        folder = custom_dir()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / source.name
        if target.exists() and target.stat().st_size != source.stat().st_size:
            number = 2
            while (folder / f"{source.stem} ({number}){source.suffix}").exists():
                number += 1
            target = folder / f"{source.stem} ({number}){source.suffix}"
        if not target.exists() or target.resolve() != source.resolve():
            shutil.copyfile(source, target)
        return [face for face in self.custom() if face.path == target]

    # ------------------------------------------------------------ 查詢

    def get(self, face_id):
        if not face_id:
            return None
        if face_id.startswith("pdf:"):
            return self._pdf_face(face_id)
        if face_id.startswith("pack:"):
            return next((face for face in self.packs() if face.id == face_id), None)
        if face_id.startswith("custom:"):
            return next((face for face in self.custom() if face.id == face_id), None)
        if face_id.startswith("file:"):
            with self._lock:
                face = next((face for face in self.system if face.id == face_id), None)
            if face is not None:
                return face
            path_text, _, index = face_id[5:].rpartition("#")
            path = Path(path_text)
            for number, name, fs_type in read_faces(path) if path.is_file() else []:
                if str(number) == index:
                    return FontFace(face_id, name, "system", path, number, embed_status(fs_type))
        return None

    def _pdf_face(self, face_id):
        """從 PDF 取出的原字型(見 pdffonts),存在 fonts\\pdf\\。"""
        face = self._custom_cache.get(face_id)
        if face is None:
            path = paths.FONTS_DIR / "pdf" / face_id[4:]
            if not path.is_file():
                return None
            found = read_faces(path)
            name = found[0][1] if found else path.stem
            face = self._custom_cache[face_id] = FontFace(face_id, f"{name}(原檔字型)", "pdf", path)
        return face

    def system_file(self, filename, index=0):
        for folder in SYSTEM_DIRS:
            path = Path(folder) / filename
            if path.is_file():
                return self.get(f"file:{path}#{index}")
        return None

    def fallback(self):
        """缺字時用來補的中文字型:已下載的 Noto Sans TC,沒有的話用電腦內建的中文字型。"""
        pack = self.get("pack:noto_sans_tc")
        if pack is not None and pack.installed:
            return pack
        for filename in FALLBACK_SYSTEM:
            face = self.system_file(filename)
            if face is not None and face.usable:
                return face
        return None

    def default(self):
        """新文字框預設的字型。"""
        return self.fallback() or next((face for face in self.packs() if face.installed), None)

    def resolve(self, face_id):
        """註解記錄的字型;找不到(例如換了電腦)或不能嵌入時改用預設字型。"""
        face = self.get(face_id)
        if face is not None and face.usable:
            return face
        return self.default()


CATALOG = Catalog()

# PDF 裡的字型名稱(PostScript 名稱)→ 電腦上的字型檔;改字時用來找一個和原字最像的字型
_PDF_FONT_FILES = {
    "microsoftjhenghei": ("msjh.ttc", 0), "microsoftjhengheiregular": ("msjh.ttc", 0),
    "microsoftjhengheibold": ("msjhbd.ttc", 0), "microsoftjhengheiui": ("msjh.ttc", 1),
    "mingliu": ("mingliu.ttc", 0), "pmingliu": ("mingliu.ttc", 1), "細明體": ("mingliu.ttc", 0),
    "新細明體": ("mingliu.ttc", 1), "dfkaishusbestdbf": ("kaiu.ttf", 0), "dfkaisb": ("kaiu.ttf", 0),
    "biaukai": ("kaiu.ttf", 0), "標楷體": ("kaiu.ttf", 0), "kaiu": ("kaiu.ttf", 0),
    "simsun": ("simsun.ttc", 0), "nsimsun": ("simsun.ttc", 1), "arial": ("arial.ttf", 0),
    "arialmt": ("arial.ttf", 0), "arialbold": ("arialbd.ttf", 0), "arialboldmt": ("arialbd.ttf", 0),
    "helvetica": ("arial.ttf", 0), "helveticabold": ("arialbd.ttf", 0),
    "timesnewroman": ("times.ttf", 0), "timesnewromanpsmt": ("times.ttf", 0), "times": ("times.ttf", 0),
    "timesroman": ("times.ttf", 0), "timesnewromanbold": ("timesbd.ttf", 0),
    "timesnewromanpsboldmt": ("timesbd.ttf", 0), "calibri": ("calibri.ttf", 0), "calibribold": ("calibrib.ttf", 0),
    "cambria": ("cambria.ttc", 0), "couriernew": ("cour.ttf", 0), "couriernewpsmt": ("cour.ttf", 0),
    "courier": ("cour.ttf", 0), "verdana": ("verdana.ttf", 0), "tahoma": ("tahoma.ttf", 0),
    "segoeui": ("segoeui.ttf", 0), "georgia": ("georgia.ttf", 0),
}


def _plain(name):
    return "".join(ch for ch in name.lower() if ch.isalnum() or ord(ch) > 127)


def match_pdf_font(name, serif=False, bold=False, cjk=True):
    """找和 PDF 裡的字型最像、而且可以嵌入的字型;找不到時依有沒有襯線挑中文字型。回傳 FontFace 或 None。"""
    base = name.split("+", 1)[1] if len(name) > 7 and name[6] == "+" else name
    plain = _plain(base.replace(",", "-"))
    candidates = [plain]
    for suffix in ("regular", "normal", "roman", "book", "medium", "light", "italic", "oblique"):
        if plain.endswith(suffix) and len(plain) > len(suffix):
            candidates.append(plain[: -len(suffix)])
    for key in candidates:
        found = _PDF_FONT_FILES.get(key)
        if found is not None:
            face = CATALOG.system_file(*found)
            if face is not None and face.usable:
                return face
    with CATALOG._lock:
        system = list(CATALOG.system)
    for face in [pack for pack in CATALOG.packs() if pack.installed] + system + CATALOG.custom():
        if face.usable and _plain(face.name) in candidates:
            return face
    if cjk:
        for filename, index in ((("mingliu.ttc", 1), ("kaiu.ttf", 0)) if serif else
                                (("msjhbd.ttc", 0) if bold else ("msjh.ttc", 0),)):
            face = CATALOG.system_file(filename, index)
            if face is not None and face.usable:
                return face
    else:
        face = CATALOG.system_file("times.ttf" if serif else "arial.ttf")
        if face is not None and face.usable:
            return face
    return CATALOG.default()


# ------------------------------------------------------------ 排版

@dataclass
class Line:
    start: int          # 這一行第一個字在全文的位置
    end: int            # 這一行結束的位置(不含換行字元)
    runs: list          # [(FontFace, 文字, x)],x 單位是點
    xs: list            # 每個字左邊的 x,最後多一個是行尾;長度 end - start + 1

    @property
    def width(self):
        return self.xs[-1]


@dataclass
class Layout:
    lines: list
    size: float
    line_height: float

    @property
    def width(self):
        return max((line.width for line in self.lines), default=0.0)

    @property
    def height(self):
        return len(self.lines) * self.line_height

    def baseline(self, row):
        return row * self.line_height + BASELINE * self.size

    def line_of(self, index):
        """游標在第幾行:行尾自動換行的位置算下一行開頭。"""
        for row, line in enumerate(self.lines):
            if line.start <= index < line.end or (index == line.end and (row == len(self.lines) - 1
                                                                          or self.lines[row + 1].start != index)):
                return row
        return len(self.lines) - 1

    def caret(self, index):
        """游標的 (x, 行)。"""
        row = self.line_of(index)
        line = self.lines[row]
        return line.xs[max(0, min(len(line.xs) - 1, index - line.start))], row

    def index_at(self, x, y):
        row = max(0, min(len(self.lines) - 1, int(y // self.line_height)))
        line = self.lines[row]
        for i in range(len(line.xs) - 1):
            if x < (line.xs[i] + line.xs[i + 1]) / 2:
                return line.start + i
        return line.end


def _wordish(ch):
    return ch.isascii() and (ch.isalnum() or ch in "'-_")


def _backups(face, fallback):
    items = fallback if isinstance(fallback, (list, tuple)) else [fallback]
    seen, result = {face.id}, []
    for item in items:
        if item is not None and item.id not in seen:
            seen.add(item.id)
            result.append(item)
    return result


def _cjk(ch):
    code = ord(ch)
    return 0x2E80 <= code <= 0xA4CF or 0xF900 <= code <= 0xFAFF or 0xFE30 <= code <= 0xFE4F \
        or 0xFF00 <= code <= 0xFF60 or code >= 0x20000


def pick_faces(text, face, fallback, latin=None):
    """每個字用哪個字型:依序找第一個有這個字的字型(fallback 可以是一個或好幾個;都沒有時仍用主字型)。
    latin 是英數字、符號優先用的字型(一個或好幾個),和 Word 一樣中文、英文可以用不同字型。
    空白也要找有的字型:原檔字型常常沒有空白,直接用會畫出方格。"""
    main = [(face, data(face))] + [(item, data(item)) for item in _backups(face, fallback)]
    western = [(item, data(item)) for item in (latin if isinstance(latin, (list, tuple)) else [latin])
               if item is not None]
    chosen = []
    for ch in text:
        probe = " " if ch in "\t\n" else ch
        candidates = western + main if western and not _cjk(ch) else main
        chosen.append(next((item for item, loaded in candidates if loaded.has(probe)), candidates[0][0]))
    return chosen


def layout(text, face, size, max_width=None, fallback=None, align="", offsets=(0.0, 0.0), line_height=0.0,
           latin=None):
    """排版。align:left、center、right、justify(兩端對齊);offsets:第一行、其他行離左邊多遠(首行縮排、
    凸排);line_height 給 0 時用字級的 LINE_HEIGHT 倍。"""
    faces = pick_faces(text, face, fallback, latin)
    advances = [0.0 if ch == "\n" else data(f).advance(" " if ch == "\t" else ch) * size
                for ch, f in zip(text, faces)]
    lines = arrange(text, advances, faces, max_width, offsets)
    if max_width and (align in ("center", "right", "justify") or any(offsets)):
        _align(lines, text, faces, max_width, align, offsets)
    return Layout(lines, size, line_height or size * LINE_HEIGHT)


def _align(lines, text, faces, width, align, offsets):
    """把每一行依對齊方式與縮排移到該在的位置;兩端對齊時把多出來的寬度平均分給字和字之間。"""
    for row, line in enumerate(lines):
        offset = offsets[0] if row == 0 else offsets[1]
        room = width - offset - line.width
        count = line.end - line.start
        ends_paragraph = row == len(lines) - 1 or line.end < len(text) and text[line.end] == "\n"
        gap = 0.0
        if align == "justify" and not ends_paragraph and count > 1 and room > 0:
            gap = room / (count - 1)
            shift = offset
        elif align == "center":
            shift = offset + max(0.0, room) / 2
        elif align == "right":
            shift = offset + max(0.0, room)
        else:
            shift = offset
        if not shift and not gap:
            continue
        line.xs = [x + shift + gap * min(i, count - 1) for i, x in enumerate(line.xs)]
        if gap:
            line.runs = [(faces[i], text[i].replace("\t", " "), line.xs[i - line.start])
                         for i in range(line.start, line.end)]
        else:
            line.runs = [(f, t, rx + shift) for f, t, rx in line.runs]


def arrange(text, advances, faces=None, max_width=None, offsets=(0.0, 0.0)):
    """依每個字的寬度自動換行,回傳 [Line];faces 是每個字用的字型(不需要時給 None)。
    offsets 是第一行、其他行離左邊的距離,可以放的寬度會跟著變窄。"""
    faces = faces or [None] * len(text)
    breaks, start, i, x = [], 0, 0, 0.0
    while i < len(text):
        ch = text[i]
        limit = max_width - (offsets[0] if not breaks else offsets[1]) if max_width else None
        if ch == "\n":
            breaks.append((start, i))
            i += 1
            start, x = i, 0.0
            continue
        if limit and x + advances[i] > limit and i > start:
            cut = i
            if _wordish(ch) and _wordish(text[i - 1]):
                space = text.rfind(" ", start, i)
                if space >= start:
                    cut = space + 1
            breaks.append((start, cut))
            start = i = cut
            x = 0.0
            continue
        x += advances[i]
        i += 1
    breaks.append((start, len(text)))
    lines = []
    for start, end in breaks:
        xs, runs, x = [0.0], [], 0.0
        for i in range(start, end):
            if runs and runs[-1][0] is faces[i]:
                runs[-1][1] += text[i]
            else:
                runs.append([faces[i], text[i], x])
            x += advances[i]
            xs.append(x)
        lines.append(Line(start, end, [(f, s.replace("\t", " "), rx) for f, s, rx in runs], xs))
    return lines


def preview_font(face, size):
    """字型選單裡的範例文字用;只用 Pillow 開檔,不讀整個字型,比較快。"""
    font = ImageFont.truetype(str(face.path), size, index=face.index)
    try:
        axes = font.get_variation_axes()
    except OSError:
        return font
    values = []
    for axis in axes:
        name = axis.get("name", b"")
        name = name.decode("latin-1", "ignore") if isinstance(name, bytes) else str(name)
        values.append(max(axis["minimum"], min(axis["maximum"], WEIGHT)) if name == "Weight" else axis["default"])
    font.set_variation_by_axes(values)
    return font


def draw_layout(draw, result, origin, scale, fill):
    """用 Pillow 把排好的文字畫出來;origin 是文字區左上角的像素位置。"""
    ox, oy = origin
    for row, line in enumerate(result.lines):
        baseline = oy + result.baseline(row) * scale
        for face, text, x in line.runs:
            if text.strip():
                draw.text((ox + x * scale, baseline), text, font=data(face).pil(result.size * scale), fill=fill,
                          anchor="ls")


def missing_chars(text, face, fallback):
    """主字型和補字字型都沒有的字。"""
    main = data(face)
    backups = [data(item) for item in _backups(face, fallback)]
    return "".join(sorted({ch for ch in text if not ch.isspace() and not main.has(ch)
                           and not any(backup.has(ch) for backup in backups)}))
