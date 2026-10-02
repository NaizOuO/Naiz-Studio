"""專有名詞表:人名、地名、招式名等固定的翻法,可以存好幾個設定檔(例如一部動畫一個、一款遊戲一個),
使用者選要用哪一個。存在 config.json 的 subtitle_glossary。

用在兩個地方:
- 翻譯:這句(和前面幾句)出現的名詞,告訴翻譯模型一定要這樣翻;模型還是照抄原文時直接換成譯名
- 辨識:語言確定時把名詞的原文當成提示給 Whisper,名字比較不會聽錯(例如「ジェバンニ」聽成「ジェ番」)
"""

KEY = "subtitle_glossary"
NONE = ""                   # 「不使用」
MAX_PROMPT_TERMS = 40       # Whisper 的提示有長度上限,名詞太多時只給前面這些


def load(config):
    raw = config.get(KEY) if isinstance(config.get(KEY), dict) else {}
    profiles = {}
    for name, terms in (raw.get("profiles") if isinstance(raw.get("profiles"), dict) else {}).items():
        name = str(name).strip()
        if not name:
            continue
        profiles[name] = [{"source": str(t.get("source", "")).strip(), "target": str(t.get("target", "")).strip(),
                           "on": bool(t.get("on", True))}
                          for t in (terms if isinstance(terms, list) else [])
                          if isinstance(t, dict) and str(t.get("source", "")).strip()]
    active = str(raw.get("active", NONE))
    return {"active": active if active in profiles else NONE, "profiles": profiles}


def stored(data):
    """寫回 config.json 的樣子(加一行說明,使用者打開設定檔時看得懂)。"""
    return {"說明": "即時字幕的專有名詞表：source=原文、target=固定的翻法、on=是否啟用；active=目前使用的設定檔",
            "active": data["active"],
            "profiles": {name: [{"source": t["source"], "target": t["target"], "on": t["on"]} for t in terms]
                         for name, terms in data["profiles"].items()}}


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
