"""字幕資料:讀 SRT(含「說話者 N:」標示、即時字幕同名 TXT 裡的顏色),輸出 SRT、YouTube 彩色字幕、TXT。

一條字幕是 {"start": 秒, "end": 秒, "text": 文字(可以有換行), "speaker": 說話者代號}。
說話者是 {"id": 代號, "label": 標籤, "color": [R, G, B]};標籤是輸出時加在句子前面的字,
預設是「顏色：」(例如「灰色：」),使用者可以改成「Naiz：」,冒號也是標籤的一部分。
"""

import re
from pathlib import Path
from xml.sax.saxutils import escape

# 和即時字幕一樣的八個顏色(TXT 裡寫的是這些名字)
COLORS = [("藍色", (88, 150, 255)), ("橘色", (255, 150, 70)), ("綠色", (90, 200, 120)),
          ("粉紅色", (240, 110, 170)), ("紫色", (165, 125, 245)), ("黃色", (235, 200, 60)),
          ("青色", (70, 200, 210)), ("紅色", (235, 85, 85))]
GRAY = ("灰色", (170, 176, 190))        # 還沒分人的字幕
MIC = ("我", (225, 225, 232))           # 即時字幕同時聽麥克風時,自己說的話
NAMED = COLORS + [GRAY, MIC]
MIN_LENGTH = 0.2                        # 一條字幕最短幾秒

_TIME = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})")
_ARROW = re.compile(rf"{_TIME.pattern}\s*-->\s*{_TIME.pattern}")
_SPEAKER = re.compile(r"^(?:說話者|说话者)\s*(\d+)\s*[:：]\s*")
# 整句都包在同一個顏色裡才算這個人的顏色(句子中間一小段紅字不算)
_FONT_COLOR = re.compile(r"^\s*<font[^>]*color\s*=\s*[\"']?#([0-9a-fA-F]{6})[^>]*>(?:(?!</?font).)*</font>\s*$",
                         re.IGNORECASE | re.DOTALL)
_LABEL = re.compile(r"^([^：:\n]{1,20})[：:]\s*")      # 有顏色的字幕句首的名字(「說話者 1：」「Naiz：」)
_TXT_LINE = re.compile(r"^\[(\d+):(\d{2})(?::(\d{2}))?\]\s*(.*)$")
_TXT_WHO = re.compile(r"^\((.+?)\)\s*")
_BAD_NAME = re.compile(r"[\\/:*?\"<>|：]")


def _seconds(match, offset=0):
    h, m, s, ms = (match.group(offset + i) for i in range(1, 5))
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def srt_time(t):
    ms = max(0, int(round(t * 1000)))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def clock(t, decimals=1):
    """畫面上顯示的時間:12:41.3,超過一小時是 1:02:41.3。"""
    t = max(0.0, t)
    whole = int(t)
    frac = f"{t - whole:.{decimals}f}"[1:] if decimals else ""
    h, m, s = whole // 3600, whole // 60 % 60, whole % 60
    return f"{h}:{m:02d}:{s:02d}{frac}" if h else f"{m:02d}:{s:02d}{frac}"


def color_name(rgb):
    """最接近的顏色名稱(自己挑的顏色也找得到一個叫法)。"""
    rgb = tuple(rgb)
    return min(NAMED, key=lambda item: sum((a - b) ** 2 for a, b in zip(item[1], rgb)))[0]


def default_label(rgb):
    return f"{color_name(rgb)}："


def file_name(label):
    """標籤當檔名的一部分:去掉冒號和檔名不能用的字。"""
    name = _BAD_NAME.sub("", label).strip(" :：")
    return name or "說話者"


def read_text(path):
    data = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp950", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def new_speaker(speakers, color=None, label=None):
    """新的說話者:代號不重複,沒指定顏色時挑還沒用過的,標籤預設「顏色：」。"""
    if color is None:
        used = {tuple(s["color"]) for s in speakers}
        color = next((rgb for _, rgb in COLORS if rgb not in used), COLORS[len(speakers) % len(COLORS)][1])
    sid = max((s["id"] for s in speakers), default=0) + 1
    return {"id": sid, "label": label or default_label(color), "color": list(color)}


def ensure_gray(cues, speakers):
    """沒有說話者的字幕歸給「灰色」(每句都有說話者,輸出時才能加標籤、改名字)。"""
    if any(cue["speaker"] is None or cue["speaker"] not in {s["id"] for s in speakers} for cue in cues):
        gray = next((s for s in speakers if tuple(s["color"]) == GRAY[1]), None)
        if gray is None:
            gray = new_speaker(speakers, GRAY[1])
            speakers.insert(0, gray)
        known = {s["id"] for s in speakers}
        for cue in cues:
            if cue["speaker"] not in known:
                cue["speaker"] = gray["id"]
    return speakers


def parse_srt(content):
    """回傳 (字幕條, 說話者);「說話者 N:」開頭的字幕會分好說話者(顏色照順序),沒標的歸「灰色」。"""
    cues = []
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        match = _ARROW.search(lines[i])
        if not match:
            i += 1
            continue
        start, end = _seconds(match), _seconds(match, 4)
        i += 1
        body = []
        while i < len(lines) and lines[i].strip() and not _ARROW.search(lines[i]):
            body.append(lines[i].strip())
            i += 1
        # 下一條的編號被讀進來了(字幕之間沒有空行的檔案):拿掉
        if body and body[-1].isdigit() and i < len(lines) and _ARROW.search(lines[i]):
            body.pop()
        color = _FONT_COLOR.search("\n".join(body))
        text = "\n".join(re.sub(r"</?(?:font|b|i|u)[^>]*>", "", line) for line in body)
        cues.append({"start": start, "end": max(end, start + MIN_LENGTH), "text": text, "speaker": None,
                     "color": tuple(int(color.group(1)[i:i + 2], 16) for i in (0, 2, 4)) if color else None})
    # 有顏色的字幕(錄音轉逐字稿、這裡輸出的 SRT):同一個顏色是同一個人,句首的名字(含改過的)照留。
    # 同一個顏色一半以上的句子開頭都一樣才算名字(只加顏色沒加名字時,句子本身的「注意：」不會被當成名字)
    prefixes = {}
    for cue in cues:
        if cue["color"] is not None:
            label = _LABEL.match(cue["text"])
            prefixes.setdefault(cue["color"], []).append(label.group(1).strip() if label else "")
    names = {}
    for color, found in prefixes.items():
        common = max(set(found), key=found.count)
        names[color] = common if common and found.count(common) * 2 >= len(found) else ""
    speakers = []
    numbers = {}
    for cue in cues:
        color = cue.pop("color")
        if color is not None:
            name = names[color]
            if color not in numbers:
                numbers[color] = len(speakers)
                speakers.append(new_speaker(speakers, color, f"{name}：" if name else None))
            cue["speaker"] = speakers[numbers[color]]["id"]
            label = _LABEL.match(cue["text"])
            if name and label and label.group(1).strip() == name:
                cue["text"] = cue["text"][label.end():]
            continue
        match = _SPEAKER.match(cue["text"])
        if match:
            number = int(match.group(1))
            if number not in numbers:
                numbers[number] = len(speakers)
                speakers.append(new_speaker(speakers))
            cue["speaker"] = speakers[numbers[number]]["id"]
            cue["text"] = cue["text"][match.end():]
    cues.sort(key=lambda cue: cue["start"])
    return cues, ensure_gray(cues, speakers)


def live_colors(txt_content):
    """即時字幕的 TXT:每一句前面的「(藍色)」「(我)」,照順序對應同名 SRT 的每一條;沒有標的是 None。"""
    marks = []
    for line in txt_content.replace("\r\n", "\n").split("\n"):
        match = _TXT_LINE.match(line)
        if not match:
            continue                            # 翻譯(縮排的那一行)、標題
        rest = match.group(4)
        if rest.startswith("──"):
            continue                            # 改設定的說明,SRT 裡沒有
        who = _TXT_WHO.match(rest)
        marks.append(who.group(1) if who else None)
    return marks


def apply_live_colors(cues, marks):
    """把 TXT 的顏色套到字幕上;句數對不上就不套(回傳空清單)。"""
    if len(marks) != len(cues) or not any(marks):
        return []
    palette = dict(NAMED)
    speakers, ids = [], {}
    for cue, mark in zip(cues, marks):
        if mark is None:
            continue
        if mark not in ids:
            speaker = new_speaker(speakers, palette.get(mark), f"{mark}：")
            speakers.append(speaker)
            ids[mark] = speaker["id"]
        cue["speaker"] = ids[mark]
    return speakers


def load(path):
    """讀字幕檔;回傳 (字幕條, 說話者, 說明)。即時字幕有同名 TXT 時帶入顏色。"""
    path = Path(path)
    cues, speakers = parse_srt(read_text(path))
    note = ""
    txt = path.with_suffix(".txt")
    plain = len(speakers) == 1 and tuple(speakers[0]["color"]) == GRAY[1]
    if plain and txt.is_file():
        for cue in cues:
            cue["speaker"] = None
        try:
            found = apply_live_colors(cues, live_colors(read_text(txt)))
        except OSError:
            found = []
        speakers = ensure_gray(cues, found)
        if found:
            note = "，顏色照即時字幕紀錄"
    return cues, speakers, note


# ------------------------------------------------------------ 輸出

def _ordered(cues):
    return sorted((cue for cue in cues if cue["text"].strip()), key=lambda cue: (cue["start"], cue["end"]))


def _hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, int(c))) for c in rgb))


def _body(cue, speaker, label):
    text = cue["text"].strip()
    if label and speaker is not None and speaker["label"]:
        text = speaker["label"] + text
    return text


def to_srt(cues, speakers, label=False, color=False):
    """SRT;label:句子前面加說話者標籤;color:用 <font color> 標顏色(有些播放器看得到)。"""
    by_id = {s["id"]: s for s in speakers}
    blocks = []
    for number, cue in enumerate(_ordered(cues), 1):
        speaker = by_id.get(cue["speaker"])
        text = _body(cue, speaker, label)
        if color and speaker is not None:
            text = f'<font color="{_hex(speaker["color"])}">{text}</font>'
        blocks.append(f"{number}\n{srt_time(cue['start'])} --> {srt_time(cue['end'])}\n{text}\n")
    return "\n".join(blocks)


def to_srv3(cues, speakers, label=False, color=True):
    """YouTube 自己的字幕格式(srv3):color 時每個說話者一個顏色,不加顏色就是 YouTube 預設的白字。"""
    by_id = {s["id"]: s for s in speakers}
    pens = {}
    head = []
    if color:
        for speaker in speakers:
            pens[speaker["id"]] = len(pens) + 1
            head.append(f'<pen id="{pens[speaker["id"]]}" fc="{_hex(speaker["color"])}" fo="254"/>')
    body = []
    for cue in _ordered(cues):
        start = int(round(cue["start"] * 1000))
        length = max(1, int(round(cue["end"] * 1000)) - start)
        text = escape(_body(cue, by_id.get(cue["speaker"]), label))
        pen = pens.get(cue["speaker"])
        inner = f'<s p="{pen}">{text}</s>' if pen else f"<s>{text}</s>"
        body.append(f'<p t="{start}" d="{length}" wp="0" ws="0">{inner}</p>')
    return ('<?xml version="1.0" encoding="utf-8"?>\n<timedtext format="3">\n<head>\n'
            + "".join(line + "\n" for line in head)
            + '<wp id="0" ap="7" ah="50" av="95"/>\n<ws id="0" ju="2"/>\n</head>\n<body>\n'
            + "\n".join(body) + "\n</body>\n</timedtext>\n")


def to_txt(cues, speakers, label=True, title=""):
    """方便閱讀的逐字稿:[時間] 標籤內容。"""
    by_id = {s["id"]: s for s in speakers}
    out = [title, ""] if title else []
    for cue in _ordered(cues):
        text = _body(cue, by_id.get(cue["speaker"]), label).replace("\n", " ／ ")
        out.append(f"[{clock(cue['start'], 0)}] {text}")
    return "\n".join(out) + "\n"


def split_by_speaker(cues, speakers):
    """每個說話者一份:[(說話者, 那個人的字幕)]。"""
    groups = []
    for speaker in speakers:
        mine = [cue for cue in cues if cue["speaker"] == speaker["id"] and cue["text"].strip()]
        if mine:
            groups.append((speaker, mine))
    return groups
