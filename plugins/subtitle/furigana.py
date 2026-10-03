"""日文字幕的振假名(漢字上方的讀音)。

用 fugashi(MeCab)加 IPADIC 字典斷詞並查讀音;字典第一次開啟這個選項時經同意後下載(約 13 MB,解開 51 MB)。
實測(testfile/furigana_eval):每句約 0.03 毫秒、幾乎不佔記憶體,67 個常見的多音詞對 63 個;
字典分不出要看上下文的讀法(例如「辛い」からい/つらい),一律用最常見的那個。

結果是一段一段的 [(原文, 讀音)],沒有漢字的段落讀音是空字串;把原文接起來就是整句。
"""

import re
from functools import lru_cache

from core import deps

DICTIONARY = deps.Dependency(
    id="ipadic",
    name="日文讀音字典（IPADIC）",
    purpose="在日文字幕的漢字上方標讀音（振假名）",
    size_text="約 13 MB（解開後約 51 MB）",
    url="https://files.pythonhosted.org/packages/e7/4e/c459f94d62a0bef89f866857bc51b9105aff236b83928618315b41a26b7b/"
        "ipadic-1.0.0.tar.gz",
    files={f"ipadic/{name}": f"dicdir/{name}" for name in
           ("sys.dic", "matrix.bin", "char.bin", "unk.dic", "dicrc", "mecabrc", "COPYING")},
    location="models",
    sha256="f5923d31eca6131acaaf18ed28d8998665b1347b640d3a6476f64650e9a71c07",
)
# 字典常讀錯的常見詞:整個詞換成正確的讀音(只在斷詞剛好切在這個詞的頭尾時才換)
FIXES = {"一人": "ひとり", "二人": "ふたり", "何て": "なんて", "一日中": "いちにちじゅう", "一年中": "いちねんじゅう",
         "一晩中": "ひとばんじゅう"}
_KANJI = re.compile(r"[㐀-鿿豈-﫿々〆ヶ]")
_KANA = re.compile(r"[぀-ヿ]")
_tagger = None


def available():
    return DICTIONARY.installed()


def _to_hiragana(text):
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def _get_tagger():
    global _tagger
    if _tagger is None:
        import fugashi

        folder = DICTIONARY.base_dir / "ipadic"
        _tagger = fugashi.GenericTagger(f'-r "{folder / "mecabrc"}" -d "{folder}"')
    return _tagger


def _tokens(text):
    """[(表面, 讀音平假名或空字串)];讀音只給含漢字的詞。"""
    result = []
    for word in _get_tagger()(text):
        surface = word.surface
        fields = word.feature
        reading = fields[7] if len(fields) > 7 and fields[7] != "*" else ""
        result.append((surface, _to_hiragana(reading) if _KANJI.search(surface) else ""))
    return result


def _apply_fixes(tokens):
    """FIXES 裡的詞:剛好由連續幾個詞組成時,合成一個詞並換成正確的讀音。"""
    result, index = [], 0
    while index < len(tokens):
        for word, reading in FIXES.items():
            joined, end = "", index
            while end < len(tokens) and len(joined) < len(word):
                joined += tokens[end][0]
                end += 1
            if joined == word:
                result.append((word, reading))
                index = end
                break
        else:
            result.append(tokens[index])
            index += 1
    return result


def _split(surface, reading):
    """把一個詞的讀音分給漢字的部分,送假名不標:食べる/たべる → [(食, た), (べる, "")]。
    對不上時整個詞標同一個讀音。"""
    if not reading or not _KANJI.search(surface):
        return [(surface, "")]
    parts = re.findall(r"[㐀-鿿豈-﫿々〆ヶ]+|[^㐀-鿿豈-﫿々〆ヶ]+", surface)
    pattern = "".join("(.+?)" if _KANJI.match(part) else re.escape(_to_hiragana(part)) for part in parts)
    match = re.fullmatch(pattern, reading)
    if not match:
        return [(surface, reading)]
    groups = iter(match.groups())
    return [(part, next(groups)) if _KANJI.match(part) else (part, "") for part in parts]


@lru_cache(maxsize=512)
def annotate(text):
    """整句的 [(原文, 讀音)];沒有假名(不是日文)或沒有漢字時回傳空的。字典沒下載時也回傳空的。"""
    if not text or not _KANA.search(text) or not _KANJI.search(text) or not available():
        return ()
    try:
        tokens = _apply_fixes(_tokens(text))
    except Exception:
        return ()
    segments = []
    for surface, reading in tokens:
        for part, ruby in _split(surface, reading):
            if segments and not ruby and not segments[-1][1]:
                segments[-1] = (segments[-1][0] + part, "")       # 沒讀音的接在一起
            else:
                segments.append((part, ruby))
    if "".join(part for part, _ in segments) != text:
        return ()                                                   # 斷詞吃掉了字(例如空白):不標,免得對不上
    return tuple(segments)
