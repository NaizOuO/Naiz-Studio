"""專有名詞表:人名、地名、招式名等固定的翻法,可以存好幾個設定檔(例如一部動畫一個、一款遊戲一個),
使用者選要用哪一個。每個設定檔存成「setting\\subtitle_glossary」資料夾裡的一個 .json(方便匯入匯出,見 core/profiles.py);
config.json 的 subtitle_glossary 只記目前用哪一個(舊版存在 config.json 裡的會自動搬過去)。

用在兩個地方:
- 翻譯:這句(和前面幾句)出現的名詞,告訴翻譯模型一定要這樣翻;模型還是照抄原文時直接換成譯名
- 辨識:語言確定時把名詞的原文當成提示給 Whisper,名字比較不會聽錯(例如「ジェバンニ」聽成「ジェ番」)
"""

from core.profiles import ProfileStore

KEY = "subtitle_glossary"
NONE = ""                   # 「不使用」
MAX_PROMPT_TERMS = 40       # Whisper 的提示有長度上限,名詞太多時只給前面這些


def _clean(item):
    source = str(item.get("source", "")).strip()
    if not source:
        return None
    return {"source": source, "target": str(item.get("target", "")).strip(), "on": bool(item.get("on", True))}


STORE = ProfileStore("subtitle_glossary", "即時字幕的專有名詞表：source=原文、target=固定的翻法、on=是否啟用", _clean)


def load(config):
    raw = config.get(KEY) if isinstance(config.get(KEY), dict) else {}
    profiles = STORE.load()
    # 舊版(v1.18.0)把設定檔存在 config.json 裡:資料夾還沒有的搬過去
    old = raw.get("profiles") if isinstance(raw.get("profiles"), dict) else {}
    moved = False
    for name, items in old.items():
        name = str(name).strip()
        if name and name not in profiles:
            profiles[name] = [item for item in (_clean(i) for i in items if isinstance(i, dict)) if item]
            moved = True
    if moved:
        STORE.save(profiles)
    active = str(raw.get("active", NONE))
    return {"active": active if active in profiles else NONE, "profiles": profiles}


def save(data):
    """存檔:設定檔寫進資料夾;回傳要寫進 config.json 的部分(只有目前用哪一個)。"""
    STORE.save(data["profiles"], data.pop("removed", ()))
    return {"說明": "即時字幕目前使用的專有名詞設定檔；設定檔本身在「setting\\subtitle_glossary」資料夾",
            "active": data["active"]}


def terms(data):
    """目前設定檔裡啟用的名詞:[(原文, 譯名)]。"""
    return [(t["source"], t["target"]) for t in data["profiles"].get(data["active"], []) if t["on"] and t["source"]]


def relevant(glossary, *texts):
    """這幾段文字裡出現的名詞(長的優先,例如「夜神月」比「月」先)。"""
    joined = "\n".join(texts)
    found = [(source, target) for source, target in glossary if source in joined]
    return sorted(found, key=lambda pair: len(pair[0]), reverse=True)


def prompt(glossary):
    """給 Whisper 的提示:名詞的原文,用頓號隔開。"""
    sources = list(dict.fromkeys(source for source, _ in glossary))[:MAX_PROMPT_TERMS]
    return "、".join(sources)
