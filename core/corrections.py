"""修正錯字:辨識完成後,把常辨識錯的字換成正確的字(不會重新辨識)。
規則可以存好幾個設定檔(例如一門課一個),每個設定檔是「setting\\transcript_corrections」資料夾裡的一個 .json
(方便匯入匯出,見 profiles.py);config.json 的 corrections 只記開關和目前用哪一個(舊版的規則會自動搬過去)。

比對方式:英文不分大小寫、只換完整單字(Map 不會換到 Mapping);中文直接比對。
所有規則一次掃過,換上去的字不會再被別的規則換第二次;同一份檔案重複套用也不會越換越長。
"""

import re

from . import paths, theme
from .profiles import ProfileStore

KEY = "corrections"
DEFAULT_NAME = "預設"         # 舊版的規則、還沒建設定檔就新增規則時,放進這個設定檔
_TIME = re.compile(r"\d+:\d+:\d+[,.]\d+\s*-->")
_LABEL = re.compile(r"^((?:說話者|说话者) \d+:)")


def _clean(rule):
    wrong = str(rule.get("wrong", "")).strip()
    if not wrong:
        return None
    return {"wrong": wrong, "right": str(rule.get("right", "")).strip(), "on": bool(rule.get("on", True))}


STORE = ProfileStore("transcript_corrections", "錄音轉逐字稿的修正錯字規則：wrong=辨識錯的字、right=正確的字、on=是否啟用", _clean)


def load():
    """{"enabled": 開關, "active": 使用中的設定檔(沒有設定檔時是空字串), "profiles": {名稱: [規則]}}"""
    config = theme.load_config(str(paths.SETTING_DIR))
    raw = config.get(KEY) if isinstance(config.get(KEY), dict) else {}
    profiles = STORE.load()
    active = str(raw.get("active", ""))
    data = {"enabled": bool(raw.get("enabled", True)), "active": active, "profiles": profiles}
    # 舊版(v1.18.0 以前)把規則直接存在 config.json:搬進「預設」設定檔
    old = [rule for rule in (_clean(r) for r in raw.get("rules", []) if isinstance(r, dict)) if rule] \
        if isinstance(raw.get("rules"), list) else []
    if "rules" in raw:
        if old:
            target = profiles.setdefault(DEFAULT_NAME, [])
            known = {rule["wrong"].lower() for rule in target}
            target += [rule for rule in old if rule["wrong"].lower() not in known]
            data["active"] = DEFAULT_NAME
        # 設定檔寫好後才把 config.json 裡的舊規則拿掉(刪掉「預設」後才不會又搬回來);寫不進去就保留舊規則,下次再搬
        try:
            save(data)
        except OSError:
            pass
    if data["active"] not in profiles:
        data["active"] = next(iter(profiles), "")
    return data


def save(data):
    STORE.save(data["profiles"], data.pop("removed", ()))
    # config.json 只改這一個鍵再寫回,不動到其他設定
    stored = theme.load_config(str(paths.SETTING_DIR))
    stored[KEY] = {
        "說明": "錄音轉逐字稿的修正錯字：enabled=是否開啟、active=使用中的設定檔；規則在「setting\\transcript_corrections」資料夾",
        "enabled": bool(data.get("enabled", True)),
        "active": data.get("active", ""),
    }
    return theme.save_config(str(paths.SETTING_DIR), stored)


def rules(data):
    """使用中的設定檔裡的規則(包含關掉的)。"""
    return data["profiles"].get(data["active"], [])


def _piece(text):
    start = r"(?<![A-Za-z0-9])" if re.match(r"[A-Za-z0-9]", text) else ""
    end = r"(?![A-Za-z0-9])" if re.search(r"[A-Za-z0-9]$", text) else ""
    return start + re.escape(text) + end


def compile_rules(rules, convert=None):
    """convert:把規則文字轉成和逐字稿相同的繁簡(例如輸出簡體時)。沒有要換的規則回傳 None。"""
    pairs = {}
    for rule in rules:
        if not rule.get("on", True) or not rule.get("wrong"):
            continue
        wrong = convert(rule["wrong"]) if convert else rule["wrong"]
        right = convert(rule["right"]) if convert and rule["right"] else rule["right"]
        pairs.setdefault(wrong.lower(), (wrong, right))
    if not pairs:
        return None
    ordered = sorted(pairs.values(), key=lambda pair: len(pair[0]), reverse=True)   # 長的優先
    pattern = re.compile("|".join(f"(?P<g{i}>{_piece(wrong)})" for i, (wrong, _) in enumerate(ordered)), re.IGNORECASE)
    return pattern, [right for _, right in ordered]


def _substitute(text, compiled):
    if compiled is None or not text:
        return text, 0
    pattern, rights = compiled
    count = 0

    def replace(match):
        nonlocal count
        right = rights[int(match.lastgroup[1:])]
        if right and match.string[match.start():match.start() + len(right)].lower() == right.lower():
            return match.group(0)   # 那裡已經是正確的字(例如規則 p → p-type,再套用一次時)
        count += 1
        return right

    return pattern.sub(replace, text), count


def apply(text, rules, convert=None):
    """回傳 (修正後文字, 換了幾處)。"""
    return _substitute(text, compile_rules(rules, convert))


def apply_srt(content, rules, convert=None):
    """只改字幕文字,不動編號、時間軸和「說話者 N:」標籤。"""
    compiled = compile_rules(rules, convert)
    if compiled is None:
        return content, 0
    total, out = 0, []
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.isdigit() or _TIME.search(line):
            out.append(line)
            continue
        label = _LABEL.match(line)
        prefix = label.group(1) if label else ""
        fixed, count = _substitute(line[len(prefix):], compiled)
        total += count
        out.append(prefix + fixed)
    return "\n".join(out), total
