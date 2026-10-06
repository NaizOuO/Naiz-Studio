"""敏感詞過濾(v1.18.6,直播用):字幕視窗和 OBS 瀏覽器來源上的敏感詞換成「[filter]」;
程式裡的字幕紀錄和存下來的 SRT/TXT 保留原文(只對外遮蔽)。

分類照 Twitch 內建過濾(AutoMod)公開的分類:歧視用語、色情、攻擊與霸凌、髒話,每類可以單獨開關;
詞庫是自己整理的中英日常見說法(Twitch 的詞庫沒有公開),只收不容易誤遮的詞(例如不收單一個「幹」,
「幹嘛」「幹部」才不會被遮)。也可以自己加詞。

設定檔和專有名詞同一套(setting\\subtitle_filters 裡一個設定檔一個 .json,見 core/profiles.py),
內容是 [{"category": 分類, "on": 開關} 或 {"word": 自己加的詞, "on": 開關}]。

比對方式:
- 英文:只比完整的單字(「class」裡的「ass」不會被遮),不分大小寫;結尾加 * 代表開頭一樣就算(fuck* 也遮 fucking)
- 中文、日文:句子裡有就遮,字和字之間有空白也算;繁體、簡體都會比對
"""

import re

from core import transcribe
from core.profiles import ProfileStore

KEY = "subtitle_filter"
NONE = ""                   # 「不使用」
TEMPLATE = "Twitch 範本"
MASK = "[filter]"

# (代號, 名稱, 說明, 詞)
CATEGORIES = [
    ("slurs", "歧視用語", "針對種族、性別、性傾向、身心障礙的貶低說法", [
        # 英文
        "nigger*", "nigga*", "faggot*", "fag", "fags", "retard", "retards", "retarded", "tranny", "trannies",
        "chink", "chinks", "spic", "spics", "kike", "kikes", "gook", "gooks", "coon", "coons", "wetback*",
        "dyke", "dykes", "shemale*",
        # 中文
        "支那", "黑鬼", "娘炮", "人妖", "死gay", "智障", "低能兒", "弱智", "殘廢", "中國豬", "台巴子", "蝗蟲",
        "番仔", "阿六仔", "死胖子",
        # 日文
        "ニガー", "シナ人", "支那人", "チョン", "ガイジ", "池沼", "ホモ野郎", "オカマ", "キチガイ", "気違い", "土人",
    ]),
    ("sexual", "色情", "性行為、性器、色情內容的說法", [
        "porn*", "blowjob*", "handjob*", "cumshot*", "cum", "pussy", "pussies", "cock", "cocks", "dildo*",
        "orgasm*", "rape", "raped", "rapist*", "raping", "boobs", "tits", "titties", "horny", "masturbat*",
        "jerk off", "anal sex",
        "做愛", "打炮", "約炮", "口交", "肛交", "乳交", "射精", "自慰", "打手槍", "雞巴", "機巴", "陰莖", "陰道",
        "陰蒂", "奶子", "色情片", "A片", "強姦", "輪姦", "性交", "援交", "淫蕩", "騷貨", "內射",
        "セックス", "フェラ", "オナニー", "ちんこ", "ちんぽ", "まんこ", "中出し", "レイプ", "手コキ", "パイズリ",
        "射精", "潮吹き", "淫乱",
    ]),
    ("hostility", "攻擊與霸凌", "叫人去死、威脅、羞辱的說法", [
        "kill yourself", "kill urself", "kys", "go die", "i will kill you", "i'll kill you", "die in a fire",
        "neck yourself",
        "去死", "你去死", "給我去死", "去自殺", "自殺吧", "殺了你", "我要殺了你", "宰了你", "砍死你", "打死你",
        "死全家", "全家死光", "腦殘", "廢物",
        "死ね", "氏ね", "殺すぞ", "ぶっ殺す", "ぶっころ", "消えろ", "自殺しろ",
    ]),
    ("profanity", "髒話", "罵人的髒話（不含「幹嘛」這類一般用法）", [
        "fuck*", "motherfuck*", "fck", "fuk", "shit", "shits", "shitty", "bullshit", "bitch*", "asshole*",
        "bastard*", "dickhead*", "cunt*", "wanker*", "twat*",
        "幹你娘", "幹您娘", "幹你老師", "幹你老母", "幹林老師", "操你媽", "操你娘", "操你祖宗", "你媽的", "他媽的",
        "媽的", "靠北", "靠腰", "靠杯", "機掰", "雞掰", "機歪", "王八蛋", "混帳東西", "狗屎", "婊子", "賤人",
        "屌你", "老雞掰", "肏",
        "クソ野郎", "くたばれ", "ファック", "ビッチ", "畜生", "ちくしょう",
    ]),
]
CATEGORY_NAMES = {key: name for key, name, _, _ in CATEGORIES}
CATEGORY_NOTES = {key: note for key, _, note, _ in CATEGORIES}
_LATIN = re.compile(r"^[A-Za-z0-9' *]+$")


def _clean(item):
    category = str(item.get("category", "")).strip()
    if category:
        return {"category": category, "on": bool(item.get("on", True))} if category in CATEGORY_NAMES else None
    word = str(item.get("word", "")).strip()
    if not word:
        return None
    return {"word": word, "on": bool(item.get("on", True))}


STORE = ProfileStore("subtitle_filters", "即時字幕的敏感詞過濾：category=內建分類的開關、word=自己加的詞、on=是否啟用",
                     _clean)


def template():
    """範本:四類全開,沒有自己加的詞。"""
    return [{"category": key, "on": True} for key, _, _, _ in CATEGORIES]


def load(config):
    """{active, profiles};範本一定在(還沒改過的話只在記憶體裡,改了才存成檔案)。"""
    raw = config.get(KEY) if isinstance(config.get(KEY), dict) else {}
    profiles = STORE.load()
    if TEMPLATE not in profiles:
        profiles = {TEMPLATE: template(), **profiles}
    for items in profiles.values():
        have = {item["category"] for item in items if "category" in item}
        items[:0] = [{"category": key, "on": False} for key, _, _, _ in CATEGORIES if key not in have]
    active = str(raw.get("active", NONE))
    return {"active": active if active in profiles else NONE, "profiles": profiles}


def save(data):
    """存檔:設定檔寫進資料夾;回傳要寫進 config.json 的部分(只有目前用哪一個)。"""
    STORE.save(data["profiles"], data.pop("removed", ()))
    return {"說明": "即時字幕目前使用的敏感詞過濾設定檔；設定檔本身在「setting\\subtitle_filters」資料夾",
            "active": data["active"]}


def words(items):
    """設定檔裡要遮的詞:開著的分類的詞 + 自己加的詞。"""
    result = []
    on = {item["category"] for item in items if "category" in item and item["on"]}
    for key, _, _, terms in CATEGORIES:
        if key in on:
            result += terms
    result += [item["word"] for item in items if "word" in item and item["on"]]
    return result


def _pattern(word):
    word = word.strip()
    if _LATIN.match(word):
        prefix = word.endswith("*")
        body = r"\s+".join(re.escape(part) for part in word.rstrip("*").split())
        # 英文:前後不能接著字母、數字(只比完整的單字)
        return rf"(?<![A-Za-z0-9]){body}{'[A-Za-z]*' if prefix else ''}(?![A-Za-z0-9])"
    # 中文、日文:繁簡都比對,字和字之間可以有空白
    forms = {word, transcribe.convert_script(word, "cn"), transcribe.convert_script(word, "tw")}
    return "|".join(r"\s*".join(re.escape(ch) for ch in form) for form in sorted(forms, key=len, reverse=True)
                    if form)


class Masker:
    """把一份詞表編成一個比對規則;mask(文字) 把敏感詞換成 MASK。"""

    def __init__(self, terms):
        parts = [_pattern(w) for w in sorted(set(terms), key=len, reverse=True) if w.strip()]
        self.regex = re.compile("|".join(f"(?:{p})" for p in parts), re.IGNORECASE) if parts else None

    def mask(self, text):
        if not text or self.regex is None:
            return text
        return self.regex.sub(MASK, text)


def masker(data):
    """目前選的設定檔的 Masker;不使用時回傳 None。"""
    items = data["profiles"].get(data["active"]) if data["active"] else None
    return Masker(words(items)) if items is not None else None
