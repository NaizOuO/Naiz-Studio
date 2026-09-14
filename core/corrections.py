"""修正錯字:辨識完成後,把常辨識錯的字換成正確的字(不會重新辨識)。規則存在 config.json 的 corrections。

比對方式:英文不分大小寫、只換完整單字(Map 不會換到 Mapping);中文直接比對。
所有規則一次掃過,換上去的字不會再被別的規則換第二次;同一份檔案重複套用也不會越換越長。
"""

import re

from . import paths, theme

KEY = "corrections"
_TIME = re.compile(r"\d+:\d+:\d+[,.]\d+\s*-->")
_LABEL = re.compile(r"^((?:說話者|说话者) \d+:)")


def load():
    config = theme.load_config(str(paths.APP_DIR))
    raw = config.get(KEY) if isinstance(config.get(KEY), dict) else {}
    rules = []
    for rule in raw.get("rules") if isinstance(raw.get("rules"), list) else []:
        if isinstance(rule, dict) and str(rule.get("wrong", "")).strip():
            rules.append({"wrong": str(rule["wrong"]).strip(), "right": str(rule.get("right", "")).strip(),
                          "on": bool(rule.get("on", True))})
    return {"enabled": bool(raw.get("enabled", True)), "rules": rules}


def save(data):
    # 只改這一個鍵再寫回,不動到其他設定
    stored = theme.load_config(str(paths.APP_DIR))
    stored[KEY] = {
        "說明": "錄音轉逐字稿的修正錯字規則：wrong=辨識錯的字、right=正確的字、on=是否啟用",
        "enabled": bool(data.get("enabled", True)),
        "rules": [{"wrong": r["wrong"], "right": r["right"], "on": bool(r["on"])} for r in data.get("rules", [])],
    }
    return theme.save_config(str(paths.APP_DIR), stored)


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
