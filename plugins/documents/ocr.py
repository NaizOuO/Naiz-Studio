"""文字辨識(OCR):把掃描檔的頁面圖片辨識成文字,用 Tesseract。

Tesseract 從 UB Mannheim 的 Windows 安裝檔直接解出程式與需要的 DLL,不會真的安裝到系統;
語言檔用官方 tessdata_best(最準、也最慢的版本),放在 bin/tessdata/,重新下載程式時不會被刪掉。
"""

import io
import subprocess
from dataclasses import dataclass

from core import deps

DPI = 300               # 辨識用的解析度;Tesseract 建議 300 dpi,再高也不會更準
TIMEOUT = 300           # 單一頁辨識超過就放棄
LANGUAGES = "chi_tra+eng"
# 版面分析方式 4:「一欄、字級可以不同」。預設的 3 會把行首單獨的「一」當成分隔線丟掉(「一、目的」變「、目的」)
PAGE_MODE = "4"
MIN_LINE_CONFIDENCE = 50    # 整行的信心分數(中位數)低於這個,多半是把圖裡的線條認成字,丟掉
_TESSDATA_COMMIT = "e12c65a915945e4c28e237a9b52bc4a8f39a0cec"
_TESSDATA_URL = f"https://github.com/tesseract-ocr/tessdata_best/raw/{_TESSDATA_COMMIT}/"

# 程式本身只需要這些檔案(其他是訓練模型用的工具);清單是從 tesseract.exe 用到的 DLL 一路追出來的
_KEEP = (
    "tesseract.exe", "libtesseract-5.dll", "libleptonica-6.dll", "libarchive-13.dll", "libb2-1.dll",
    "libbz2-1.dll", "libcrypto-3-x64.dll", "libdeflate.dll", "libexpat-1.dll", "libgcc_s_seh-1.dll",
    "libgif-7.dll", "libiconv-2.dll", "libjbig-0.dll", "libjpeg-8.dll", "libLerc.dll", "liblz4.dll",
    "liblzma-5.dll", "libopenjp2-7.dll", "libpng16-16.dll", "libsharpyuv-0.dll", "libstdc++-6.dll",
    "libtiff-6.dll", "libwebp-7.dll", "libwebpmux-3.dll", "libwinpthread-1.dll", "libzstd.dll", "zlib1.dll",
)

TESSERACT = deps.Dependency(
    id="tesseract",
    name="Tesseract 文字辨識",
    purpose="把掃描檔(整頁都是圖片的 PDF)辨識成可以編輯的文字",
    size_text="約 50 MB",
    url="https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/"
        "tesseract-ocr-w64-setup-5.4.0.20240606.exe",
    sha256="c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9",
    files={"tesseract/tesseract.exe": None},
    folder="tesseract",
    installer="nsis",
    install_size=120 * 1024 * 1024,
    keep=_KEEP,
    check_args=["--version"],
)


def _language(code, label, size_text, sha256):
    return deps.Dependency(
        id=f"tessdata_{code}", name=f"辨識語言：{label}", purpose="文字辨識用的語言資料",
        size_text=size_text, url=f"{_TESSDATA_URL}{code}.traineddata", sha256=sha256,
        files={f"tessdata/{code}.traineddata": None})


LANGUAGE_FILES = [
    _language("chi_tra", "繁體中文", "約 13 MB", "1aa60488574cafa69486d919284f079ca9b68fcc7f6ad8dc1ff1b318dfd97028"),
    _language("eng", "英文", "約 15 MB", "8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba"),
]
DEPENDENCIES = [TESSERACT, *LANGUAGE_FILES]


def ready() -> bool:
    return all(dep.installed() for dep in DEPENDENCIES)


def missing() -> list:
    return [dep for dep in DEPENDENCIES if not dep.installed()]


@dataclass
class Word:
    """辨識出來的一個詞;座標是圖片上的像素。"""
    text: str
    confidence: float
    left: int
    top: int
    right: int
    bottom: int
    line: tuple             # (區塊, 段落, 行),同一行的詞這個值一樣
    line_bottom: int        # 整行的下緣,當成文字底線


def recognize(image) -> list:
    """辨識一張圖片(PIL);回傳 Word 清單,依閱讀順序排好。"""
    buffer = io.BytesIO()
    image.convert("L").save(buffer, "PNG")
    base = TESSERACT.base_dir
    # 語言檔用相對路徑指定:Tesseract 在 Windows 讀不了含中文的完整路徑。
    # 不要加 --dpi:指定後「一」這種又扁又短的字會被當成雜點濾掉
    args = [TESSERACT.path(), "stdin", "stdout", "--tessdata-dir", "tessdata", "-l", LANGUAGES,
            "--psm", PAGE_MODE, "-c", "preserve_interword_spaces=1", "-c", "tessedit_create_tsv=1"]
    try:
        result = deps.run(args, input=buffer.getvalue(), capture_output=True, cwd=base, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("文字辨識太久沒有回應，已停止") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(f"文字辨識失敗：{message[-1] if message else result.returncode}")
    return parse_tsv(result.stdout.decode("utf-8", "replace"))


def parse_tsv(text) -> list:
    lines, words = {}, []
    for row in text.splitlines()[1:]:
        cells = row.split("\t")
        if len(cells) < 12:
            continue
        level = int(cells[0])
        key = (int(cells[2]), int(cells[3]), int(cells[4]))
        left, top, width, height = (int(v) for v in cells[6:10])
        if level == 4:
            lines[key] = top + height
        elif level == 5 and cells[11].strip() and float(cells[10]) >= 0:
            words.append(Word(cells[11].strip(), float(cells[10]), left, top, left + width, top + height, key, 0))
    by_line = {}
    for word in words:
        word.line_bottom = lines.get(word.line, word.bottom)
        by_line.setdefault(word.line, []).append(word.confidence)
    confident = {key for key, values in by_line.items()
                 if sorted(values)[len(values) // 2] >= MIN_LINE_CONFIDENCE}
    return [word for word in words if word.line in confident]


def text_of(words) -> str:
    """把辨識結果排回純文字:同一行的詞接起來,中文之間不加空白。"""
    lines = []
    for word in words:
        if lines and lines[-1][0] == word.line:
            previous = lines[-1][1][-1:]
            joint = "" if previous and (_is_cjk(previous) or _is_cjk(word.text[0])) else " "
            lines[-1][1] += joint + word.text
        else:
            lines.append([word.line, word.text])
    return "\n".join(text for _, text in lines)


def _is_cjk(ch) -> bool:
    code = ord(ch)
    return 0x2E80 <= code <= 0xA4CF or 0xF900 <= code <= 0xFAFF or 0xFF00 <= code <= 0xFF60 or code >= 0x20000
