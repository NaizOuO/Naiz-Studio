"""即時字幕的流程:擷取聲音 → 人聲偵測 → 每隔一小段重新辨識「這句開始到現在」(字會一直長出來)→
停頓就定稿 → 翻譯(講到一半先試翻,講完再翻一次完整的)。

排程用「聲音的時間」而不是時鐘:每收到 step 秒的新聲音就辨識一次;電腦慢、辨識跟不上時,下一次自然涵蓋
更長的聲音,不會越積越多。實測(RTX 5060 Ti、turbo、qwen3:8b):說完到翻譯出來平均約 1.3 秒。
"""

import difflib
import http.client
import itertools
import math
import queue
import re
import threading
import time
from array import array
from dataclasses import dataclass, field

from . import asr, glossary as names, speakers as voices
from . import translate as ollama
from .vad import FRAME, RATE

SPEECH_ON = 0.5             # 人聲機率超過這個算開始說話
SPEECH_OFF = 0.35           # 低於這個算停下來
PAUSE = 0.5                 # 停頓這麼久(秒)就定稿
SHORT_PAUSE = 0.9           # 才講一點點(「それから」「所以」)就停的話,要停更久才定稿,讓它和後面接成一句
SHORT_SPEECH = 1.5
MAX_SENTENCE = 12.0         # 一句最長幾秒;超過就在最安靜的地方切開
MIN_SPEECH = 0.35           # 比這個短的聲音(咳嗽、雜音)不辨識
FRAME_SECONDS = FRAME / RATE
# Whisper 對著靜音、音樂常冒出的句子:很短的片段辨識出這些就丟掉
HALLUCINATIONS = {
    "you", "thank you", "thank you.", "i'm going to go.", "i'm going to go", "thanks for watching!", "thanks for watching.", "bye.", "bye", "okay.",
    "ご視聴ありがとうございました", "ご視聴ありがとうございました。", "おやすみなさい", "おやすみなさい。",
    "字幕由amara.org社区提供", "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目", "謝謝觀看", "謝謝大家",
    "시청해주셔서 감사합니다", "시청해 주셔서 감사합니다.",
}
_REPEATED = re.compile(r"(.{2,12}?)\1{3,}")
LOCK_LANGUAGE = 3.0         # 自動判斷語言:這句講超過這麼多秒才固定語言(太短的片段常判斷錯)
# 自動判斷語言時延續前面的語言:判成別的語言的機率要 ≥ STICKY_SURE、而且前面語言的機率 < STICKY_KEEP 才換
# (實測:動畫喊叫判錯時最高的語言機率都 < 0.75;真的改說中文時中文機率 0.95 以上)
STICKY_SURE = 0.8
STICKY_KEEP = 0.15
SPACED = {"en", "es", "fr", "de", "ru", "vi"}       # 用空格分詞的語言:固定的部分要停在單字之間
_CLAUSE = re.compile(r"[，。、！？；,.!?;]")
PARTIAL_GAP = 0.6           # 講到一半的翻譯最少隔這麼久才更新一次(秒)
PARTIAL_GROW = 2            # 原文多了這麼多字才再翻一次
# 中文:Whisper 不加提示時常常是簡體、沒有標點;給一句繁體、有標點的提示,辨識結果就會照這個樣子
ZH_PROMPT = "以下是台灣繁體中文的句子，有標點符號。"
# 口語的提示:Whisper 預設會把「嗯、那個」和結巴的重複刪掉(像書面稿);給一段口語的例子,它就會照實寫出來
# 語助詞(長的在前,先比對「えーと」再比對「え」);整句只有這些的話接到上一句後面
# (「對」「うん」「yeah」這類回答有意思,不算)
FILLERS = sorted({"嗯", "啊", "呃", "欸", "誒", "唉", "哦", "喔", "噢", "那個", "えーと", "えっと", "えー", "え", "あの",
                  "あのー", "まあ", "んー", "ん", "はぁ", "ああ", "あ", "um", "uh", "hmm", "mm", "er", "ah"},
                 key=len, reverse=True)
MERGE_GAP = 1.5             # 只有語助詞的一句,離上一句這麼近(秒)才接過去
MERGE_LIMIT = 40            # 上一句已經這麼長就不再接(字幕一行放不下)
SPOKEN_PROMPTS = {"zh": "嗯，那個，我、我覺得……", "ja": "えーと、あの、まあ、そ、そうですね。",
                  "en": "Um, uh, I- I mean, like, you know."}
_HALF_PUNCT = {",": "，", "?": "？", "!": "！", ":": "：", ";": "；"}
_CJK = r"[\u3400-\u9fff\uf900-\ufaff]"
_KANA = re.compile(r"[\u3040-\u30ff]")
# Whisper 對音效、配樂寫的說明(「*Gunshot*」「[音楽]」「(笑)」「♪」),不是台詞
_TAGS = re.compile(r"\*[^*]*\*|\[[^\]]*\]|\([^)]*\)|（[^）]*）|【[^】]*】|[♪♫#]+")
_SENTENCE_END = re.compile(r"[。？！.?!…][」』\"']?$")
SPLIT_AFTER = 1.0           # 一口氣講好幾句時,前面講完的句子(前後兩次辨識都一樣)至少這麼長才先定稿
SETTLE_SHORT = 0.6          # 定稿那次辨識的字數不到畫面上那句的這個比例,當作漏字,用畫面上那句
# 長句不被壓縮(v1.18.3):講完一句(句號、問號)就切成新的一行,每次辨識的聲音才不會太長(太長容易漏字、鬼打牆)
SPLIT_BY_WORDS = True       # Whisper 常把好幾句放在同一段:用每個字的時間,在段落裡的句號後面也切開
AGREE_PLAIN = True          # 判斷「這句講完了」時忽略標點(中文前後兩次的，。常不一樣,完全一樣才算的話很少切得開)
SETTLE_FINAL = True         # 定稿以定稿那次為主(實測多人對話時,把講到一半固定的字硬接上去反而較差)
BLIND_WINDOW = 4.0          # 配樂、爆炸很大聲時人聲偵測常抓不到台詞:每這麼多秒直接聽一次
BLIND_LOUD = 500            # 聲音大小(RMS)超過這個才直接聽(安靜時不聽,免得對著靜音亂猜)
GAIN_TARGET = 0.5           # 自動收音:把最近的峰值放大到這麼大(約 -6 dB)
GAIN_MAX = 32.0             # 最多放大幾倍(Whisper 本身不怕小聲,主要是讓人聲偵測抓得到)
GAIN_CEILING = 0.95         # 自動放大後的峰值不超過這個(突然很大聲時馬上降,不破音)
GAIN_FLOOR = 0.0005         # 比這個小的片段當作安靜,不拿來估計(不然安靜時會越放越大)
GAIN_WINDOW = 250           # 看最近幾段聲音(每段約 20 毫秒,約 5 秒)
MIN_CONFIDENCE = -0.5       # 直接聽的結果:Whisper 的把握(平均對數機率)要高於這個才算數
BLIND_SHIFT = 1.5           # 直接聽到東西時,多聽前面這麼多秒再聽一次確認
BLIND_STEP = 2.0            # 直接聽時最後一段像是還沒唱完(講完):先不定稿,隔這麼久從那段開頭再聽一次
BLIND_LONGEST = 8.0         # 直接聽的一段最長幾秒;再長就不等了,整段定稿
BLIND_EDGE = 0.5            # 最後一段結束在聽的範圍最後這麼多秒內:當作還沒唱完(講完)
HICCUP_LIMIT = 60.0         # 暫時連不上本地辨識伺服器:連續這麼多秒都連不上才算中斷(之前字幕不停,等一下再辨識;
                            # 實測 Windows 偶爾會擋所有新的本地連線 3～18 秒)
REPEAT_GAP = 0.3            # 和上一句的時間重疊(或只隔這麼久)、字又已經在上一句裡:同一段聲音聽了兩次,不要
                            # (隔久一點的可能是真的又講一次,例如別人跟著重複)
OVERLAP_BACK = 0.4          # 句子被切開後(太長、一口氣講好幾句),下一句往前多聽這麼多秒:
                            # 切點的時間常差 0.5 秒左右,從字的中間開始聽時第一個字會被吃掉;多聽到的重複字再去掉


def agree(previous, current, spaced):
    """前後兩次辨識都一樣的開頭(之後不會再變);用空格分詞的語言停在單字之間。"""
    size = 0
    for a, b in zip(previous, current):
        if a != b:
            break
        size += 1
    common = current[:size]
    if spaced and size < len(current) and common and not common.endswith(" "):
        common = common[:common.rfind(" ") + 1] if " " in common else ""
    return common


def merge(committed, hypothesis):
    """畫面上的原文 = 已固定的部分 + 最新辨識結果裡接在它後面的部分(固定的字不會再被改掉)。"""
    if not committed or hypothesis.startswith(committed):
        return hypothesis
    matcher = difflib.SequenceMatcher(None, committed, hypothesis, autojunk=False)
    blocks = [b for b in matcher.get_matching_blocks() if b.size]
    if not blocks:
        return committed
    block = max(blocks, key=lambda b: b.a + b.size)
    rest = len(committed) - (block.a + block.size)          # 固定的部分後面還沒對上的字數
    return committed + hypothesis[block.b + block.size + rest:]


def settle(displayed, committed, final):
    """定稿的原文。定稿時會用整句的聲音重新辨識一次,但這次有時反而漏掉很多字(配樂、講很快時):
    畫面上已經固定的開頭不改,只換後面不確定的字;定稿那次明顯比畫面上的短很多時,用畫面上那句。"""
    if not displayed:
        return final
    if degenerate(final) and not degenerate(displayed):
        return displayed                        # 定稿那次鬼打牆:用畫面上那句
    text = final if SETTLE_FINAL else (merge(committed, final) if committed else final)
    if len(text) < len(displayed) * SETTLE_SHORT:
        return displayed
    return text


def displayed_prefix(displayed, finished):
    """拆成上下句時,前半句在畫面上那句裡對應的部分(finished 是最新辨識出的前半句)。
    對應不起來(畫面上那句和最新辨識差太多)時回傳 None:這次先不拆,等辨識穩定。"""
    if not displayed:
        return finished
    if displayed.startswith(finished):
        return finished
    matcher = difflib.SequenceMatcher(None, displayed, finished, autojunk=False)
    blocks = [b for b in matcher.get_matching_blocks() if b.size]
    if not blocks or sum(b.size for b in blocks) < len(finished) * 0.7:
        return None
    end = blocks[-1].a + blocks[-1].size
    while end < len(displayed) and _CLAUSE.match(displayed[end]):        # 句尾的標點一起帶走
        end += 1
    return displayed[:end].rstrip()


def trim_overlap(previous, text, spaced):
    """下一句的開頭和上一句結尾一樣的部分去掉(句子切開後往前多聽了一點、或切點不準,同一段話出現兩次)。
    中日文至少 2 個字、用空格分詞的語言至少 1 個字詞(只有一個字詞時要 4 個字母以上,the、and 這類不算);比對時不看標點和大小寫。
    整句都重複時回傳空字串。"""
    if not previous or not text:
        return text
    if spaced:
        before = re.findall(r"\w+", previous.lower())
        words = list(re.finditer(r"\w+", text))
        for size in range(min(len(before), len(words), 15), 0, -1):
            if [w.group().lower() for w in words[:size]] == before[-size:]:
                if size == 1 and len(before[-1]) < 4:
                    break                   # 只重疊一個很短的字(the、and)常是真的,不去掉
                return text[words[size - 1].end():].lstrip(" ,.;:!?-")
        return text
    before = "".join(ch for ch in previous.lower() if ch.isalnum())
    places = [index for index, ch in enumerate(text) if ch.isalnum()]
    head = "".join(text[index] for index in places).lower()
    for size in range(min(len(before), len(head), 40), 1, -1):       # 講完一句就切時,重疊的常是整個子句
        if before[-size:] == head[:size]:
            return text[places[size - 1] + 1:].lstrip("、，。！？…,.!? 　")
    return text


def stable_translation(text):
    """講到一半的翻譯裡可以先固定的部分:到最後一個逗號、句號為止;沒有的話留到倒數第三個字。"""
    marks = list(_CLAUSE.finditer(text))
    if marks and marks[-1].end() >= len(text) * 0.4:
        return text[:marks[-1].end()]
    return text[:-3] if len(text) > 6 else ""


def same_language(language, target):
    """原文已經是目標語言:不用翻(中文只轉繁簡用字)。"""
    return bool(language) and (language == target or (language == "zh" and target.startswith("zh")))


@dataclass
class Line:
    id: int
    start: float                # 從開始字幕算起的秒數
    end: float
    original: str = ""
    translation: str = ""
    final: bool = False
    language: str = ""
    committed: str = ""         # 已經固定、不會再變的原文開頭
    previous: str = ""          # 上一次的辨識結果(用來比對哪些字穩定了)
    source: str = ""            # 上一次拿去翻譯的原文
    translated_from: str = ""   # 目前這個翻譯是哪一段原文完整翻好的(定稿時原文沒變就不用重翻)
    translated_at: float = 0.0
    same: bool = False          # 原文就是目標語言,沒有翻譯
    segments: list = field(default_factory=list)    # 上一次辨識的分段(找出已經講完的句子)
    notice: bool = False        # 字幕紀錄裡的說明行(變更設定、重新開始),不是有人說的話
    speaker: int = None         # 判斷誰說話:第幾個人(0 起算);沒開、還沒判斷完、判斷不了時是 None
    words: list = field(default_factory=list)       # 最近一次辨識每個字的時間 [(開始秒, 結束秒, 字)](一句裡換人時切開用)


@dataclass
class Settings:
    source: str = "system"      # system / app / mic
    pid: int = None             # 單一程式時的程式
    model: str = "turbo"        # 辨識模型(asr.MODELS)
    language: str = "auto"      # 原文語言
    translate: bool = True
    translator: str = ollama.DEFAULT_MODEL
    target: str = "zh-TW"
    partial: bool = True        # 講到一半就先試翻(電腦慢時關掉,只翻完整的句子)
    step: float = 0.3           # 每收到這麼多秒的新聲音就重新辨識一次
    glossary: list = field(default_factory=list)    # 專有名詞 [(原文, 譯名)];字幕進行中改了也會馬上用
    verbatim: bool = True       # 辨識時保留語助詞、結巴(給 Whisper 口語的提示)
    gain: str = "auto"          # 收音靈敏度:auto 自動放大太小的聲音,或固定倍數 "1" "2" "4"
    speakers: bool = False      # 判斷誰說話(每句算聲音特徵,不同人不同顏色)
    # 判斷誰說話的區分方式(speaker_profiles.mode):method cluster 自動分群/plain 一般比對/centered 扣掉共同音色、
    # same 同一人門檻(門檻比對才用)、split 一句裡換人的門檻
    speaker_mode: dict = field(default_factory=lambda: {"method": "cluster", "same": voices.SAME, "split": 0.50})
    extra: dict = field(default_factory=dict)


def tidy_chinese(text, target):
    """中文原文:轉成台灣繁體(翻成簡體中文時轉簡體)、標點改全形、中文字之間的空格改成逗號。"""
    from core import transcribe

    text = transcribe.convert_script(text, "cn" if target == "zh-CN" else "tw")
    text = re.sub(r"(?<!\d)\s*([,?!:;])\s*|([?!;])", lambda m: _HALF_PUNCT[m.group(1) or m.group(2)], text)
    text = re.sub(rf"(?<={_CJK})\.(?!\d)", "。", text)
    text = re.sub(rf"(?<={_CJK})\s+(?={_CJK})", "，", text)
    return text.strip()


def untranslated(text, source, language, target):
    """翻成中文卻沒翻(模型偷懶照抄原文):日文還留著假名,其他語言整句沒有中文字。要重翻一次。"""
    if not target.startswith("zh") or not text:
        return False
    if language == "ja":
        return len(_KANA.findall(text)) >= 2
    return not re.search(_CJK, text) and any(ch.isalpha() for ch in source)


def plausible(text, speech_seconds):
    """辨識結果像不像真的有人說的話:很短的片段辨識成常見幻聽句就丟掉;重複的字串收斂。"""
    # 同一段重複 4 次以上:長的是幻聽(整句一直重複)只留一次;很短的是結巴(「我、我、我、我」)留兩次
    text = _REPEATED.sub(lambda m: m.group(1) * (2 if len(m.group(1)) <= 3 else 1), _TAGS.sub("", text).strip())
    if not text or all(not ch.isalnum() for ch in text) or ZH_PROMPT[:6] in text:
        return ""
    if speech_seconds < 2.5 and text.lower().strip(" .!。！") in {h.strip(" .!。！") for h in HALLUCINATIONS}:
        return ""
    return text


def plain(text):
    """比對用:只留字母和數字(不看標點、空白、大小寫)。"""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def degenerate(text):
    """鬼打牆:同樣的幾個字一直重複(每次略有不同,整句重複的偵測抓不到)。三個字一組,不重複的組數不到一半就算。"""
    letters = plain(text)
    if len(letters) < 12:
        return False
    grams = [letters[i:i + 3] for i in range(len(letters) - 2)]
    return len(set(grams)) < len(grams) * 0.5


def split_segments(segments, words):
    """Whisper 常把好幾句放在同一段(「今天天氣很好。我們出去走走。」):用每個字的時間,在段落裡的句號、
    問號、驚嘆號後面再切開,「這句講完了」才判斷得到。字拼起來和段落對不上(中文字被拆成半個字等)時照原本的段落。"""
    if not words:
        return segments
    out = []
    for start, end, text in segments:
        inner = text.strip()
        marks = [m.end() for m in re.finditer(r"[。？！.?!…]+[」』\"']?", inner)]
        inside = [w for w in words if w[0] >= start - 0.05 and w[1] <= end + 0.05 and w[2].strip()]
        if not inside or not marks or marks == [len(inner)] or plain("".join(w[2] for w in inside)) != plain(inner):
            out.append((start, end, text))
            continue
        piece, piece_start = "", None
        for word_start, word_end, word in inside:
            piece_start = word_start if piece_start is None else piece_start
            piece += word
            if _SENTENCE_END.search(piece.strip()):
                out.append((piece_start, word_end, piece.strip()))
                piece, piece_start = "", None
        if piece.strip():
            out.append((piece_start, end, piece.strip()))
    return out


def sentences(segments, spaced):
    """把 Whisper 的分段接成一句一句:[(開始秒, 結束秒, 文字)];句號、問號、驚嘆號結尾才算一句講完
    (Whisper 有時把一句切成兩段,例如「awesome in」「space.」)。最後一句可能還沒講完。"""
    result, current = [], None
    for start, end, text in segments:
        text = text.strip()
        if not text:
            continue
        if current is None:
            current = [start, end, text]
        else:
            current[1] = end
            current[2] += (" " if spaced else "") + text
        if _SENTENCE_END.search(current[2]):
            result.append(tuple(current))
            current = None
    if current is not None:
        result.append(tuple(current))
    return result


def filler_only(text, language):
    """整句只有語助詞(「嗯。」「えーと…」「Uh.」)。"""
    rest = text.lower()
    for word in FILLERS:
        rest = re.sub(rf"\b{re.escape(word)}\b", "", rest) if word.isascii() else rest.replace(word, "")
    return bool(text.strip()) and not any(ch.isalnum() for ch in rest)


def shared_words(a, b, spaced):
    """兩次辨識有沒有共同的一段話:用空格分詞的語言要連續 2 個字詞一樣,中日文要連續 3 個字一樣。"""
    def units(text):
        return re.findall(r"\w+", text.lower()) if spaced else [ch for ch in text if ch.isalnum()]

    x, y = units(a), units(b)
    match = difflib.SequenceMatcher(None, x, y, autojunk=False).find_longest_match(0, len(x), 0, len(y))
    return match.size >= (2 if spaced else 3)


def split_text(text, words, start, end, times, spaced):
    """把一句照時間切開(一句裡換人):有每個字的時間就切在最近的字與字之間(優先句號、逗號後面),
    再照字數比例換算成原文的位置(原文經過整理,和辨識結果的字可能有點不同),最後對齊到附近的標點或空格。
    回傳 [(開始秒, 結束秒, 文字)];切出來太短(不到 2 個字)的那刀不切。"""
    words = [(a, b, w.strip()) for a, b, w in words if start - 0.5 <= a <= end + 0.5 and w.strip()]
    total = sum(len(w) for _, _, w in words)
    marks = []                                  # (時間, 在原文的比例)
    for moment in times:
        if words and total:
            best = None
            for index in range(1, len(words)):
                gap = (words[index - 1][1] + words[index][0]) / 2
                cost = abs(gap - moment) - (0.4 if _CLAUSE.search(words[index - 1][2][-1:]) else 0)
                if best is None or cost < best[0]:
                    best = (cost, index, gap)
            if best is None or abs(best[2] - moment) > 1.5:
                continue
            marks.append((best[2], sum(len(w) for _, _, w in words[:best[1]]) / total))
        else:
            marks.append((moment, (moment - start) / max(0.1, end - start)))
    pieces, begin, at = [], 0, start
    for moment, ratio in sorted(marks):
        position = _snap(text, round(ratio * len(text)), spaced)
        head = text[begin:position].strip()
        if position <= begin or sum(ch.isalnum() for ch in head) < 2 \
                or sum(ch.isalnum() for ch in text[position:]) < 2:
            continue
        pieces.append((at, moment, head))
        begin, at = position, moment
    pieces.append((at, end, text[begin:].strip()))
    return pieces


def _snap(text, position, spaced):
    """對齊到附近的標點後面(空格分詞的語言對齊到空格);附近沒有就照原位置(中日文)。"""
    radius = max(2, len(text) // 8)
    candidates = [j for j in range(max(1, position - radius), min(len(text), position + radius) + 1)
                  if _CLAUSE.match(text[j - 1]) or text[j - 1] in "…」』"]
    if candidates:
        return min(candidates, key=lambda j: abs(j - position))
    if spaced:
        spaces = [j for j in range(1, len(text)) if text[j - 1] == " "]
        if spaces:
            return min(spaces, key=lambda j: abs(j - position))
    return max(0, min(len(text), position))


def finished_sentences(previous, current, spaced):
    """前後兩次辨識都一樣、已經講完的句子(最後一句可能還在講,不算):回傳 (句數, 結束秒數)。
    AGREE_PLAIN:這次講完的句子去掉標點後,和上一次辨識的開頭一樣、而且上一次在那之後還有字,就算講完
    (前後兩次的句子邊界常不同:上次「今天很好，我們走吧。」這次「今天很好。我們走吧。」)。"""
    old, new = sentences(previous, spaced), sentences(current, spaced)
    before = plain("".join(segment[2] for segment in previous))
    count, end, joined = 0, 0.0, ""
    for index, b in enumerate(new[:-1]):
        if not _SENTENCE_END.search(b[2]):
            break
        if AGREE_PLAIN:
            joined += plain(b[2])
            same = before.startswith(joined) and len(before) > len(joined)
        else:
            same = index < len(old) and old[index][2] == b[2]
        if not same:
            break
        count, end = index + 1, b[1]
    return count, end


class Engine:
    """start() 之後在背景執行;on_update() 在字幕內容改變時被呼叫(從背景執行緒)。"""

    def __init__(self, settings, on_update=None, capture_factory=None, vad_factory=None, server=None,
                 translator=None):
        self.settings = settings
        self.on_update = on_update or (lambda: None)
        self._capture_factory = capture_factory
        self._vad_factory = vad_factory
        self.server = server
        self._translate = translator or ollama.translate
        self.lines = []
        self._carry = None                  # 被切開的上一句:(那一行, 下一句的開始秒數),下一句的開頭要去掉重複
        self._held = None                   # 直接聽時還沒唱完(講完)、先顯示的那句(還沒定稿)
        self._hiccup_since = None           # 從什麼時候開始連不上本地辨識伺服器
        self._voices = queue.Queue()        # 要判斷誰說話的句子:(那一行, 聲音)
        self._split_ids = itertools.count(1_000_000)    # 一句裡換人切出來的新行(在另一個執行緒產生,另外編號)
        self.tracker = voices.Tracker()     # 認得的人(整個字幕過程沿用,中途改設定也不會重新認人)
        self.clusterer = voices.Clusterer() # 自動分群
        self.speaker_error = ""
        self.state, self.message = "idle", ""
        self.device = ""
        self.costs = []                     # 最近幾次辨識花的時間
        self.delays = []                    # 每句說完到翻譯好的時間
        self._audio = bytearray()           # 從 _offset 開始的聲音
        self._offset = 0                    # _audio 第一個取樣是開始後的第幾個取樣
        self._probs = []                    # 每 32 毫秒的人聲機率(從開始算)
        self._lock = threading.Lock()
        self._new_audio = threading.Event()
        self._stop = threading.Event()
        self._jobs = queue.Queue()
        self._partial = None                # 最新一筆「講到一半」的翻譯工作(只留最新的)
        self._partial_lock = threading.Lock()
        self._next_id = 0
        self._capture = None
        self._vad = None
        self.started_at = None
        self._paused = threading.Event()    # 字幕進行中換辨識模型、聲音來源:聲音照收,辨識先停
        self._reconfiguring = threading.Lock()
        self.gain = 1.0                     # 目前放大的倍數(自動時會跟著聲音大小變)
        self.level = 0.0                    # 最近的音量(0～1,放大前),畫面上可以顯示
        self._peaks = []                    # 最近每段聲音的峰值(自動放大用)

    # ------------------------------------------------------------ 開始、停止

    def start(self):
        self.state, self.message = "loading", "準備中"
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()
        self._new_audio.set()
        self._jobs.put(None)
        self._voices.put(None)
        if self._capture is not None:
            self._capture.stop()
        if self.server is not None:
            self.server.stop()
        if self.settings.translate:
            threading.Thread(target=ollama.unload, args=(self.settings.translator,), daemon=True).start()
        if self.state not in ("error",):
            self.state, self.message = "stopped", "已停止"
        self.on_update()

    @property
    def running(self):
        return self.state == "running"

    def _fail(self, text):
        self.state, self.message = "error", text
        self._stop.set()
        if self.server is not None:
            self.server.stop()
        self.on_update()

    def _run(self):
        s = self.settings
        try:
            if self._vad is None:
                from .vad import Vad
                self._vad = (self._vad_factory or Vad)()
            if self.server is None:
                self.message = "載入辨識模型" + ("（第一次用顯示卡約需 30 秒）" if asr.gpu() else "")
                self.on_update()
                self.server = asr.Server(s.model)
                self.server.start(cancel=self._stop)
            if s.translate:
                self.message = "載入翻譯模型"
                self.on_update()
                if not ollama.running():
                    raise RuntimeError("Ollama 沒有在執行，請先打開 Ollama")
                ollama.preload(s.translator, cancel=self._stop.is_set)     # 按停止時內建引擎不用等載入完
            if self._stop.is_set():
                return
            threading.Thread(target=self._translate_loop, daemon=True).start()
            threading.Thread(target=self._speaker_loop, daemon=True).start()
            if self._capture_factory is not None:
                self._capture = self._capture_factory(self._feed)
            else:
                from .capture import Capture
                self._capture = Capture(s.source, self._feed, s.pid)
            self.started_at = time.monotonic()
            self._capture.start()
            if hasattr(self._capture, "started"):
                self._capture.started.wait(5)
            if getattr(self._capture, "error", ""):
                raise RuntimeError(self._capture.error)
            self.state, self.message = "running", "字幕進行中"
            self.on_update()
            self._listen()
        except Exception as exc:
            if not self._stop.is_set():        # 使用者按了停止(載入到一半被中斷)不算出錯
                self._fail(str(exc) or type(exc).__name__)

    # ------------------------------------------------------------ 聲音

    def _feed(self, pcm):
        pcm = self._amplify(pcm)
        probs = self._vad.feed(pcm)
        with self._lock:
            self._audio += pcm
            self._probs += probs
        self._new_audio.set()

    def _amplify(self, pcm):
        """收音靈敏度:聲音太小時放大(人聲偵測和辨識對太小的聲音不靈敏)。
        自動:看最近幾秒的峰值,放大到峰值約 -6 dB,最多 8 倍;安靜時不會越放越大(只看夠響的片段)。"""
        if not pcm:
            return pcm
        import numpy

        samples = numpy.frombuffer(pcm, numpy.int16).astype(numpy.float32)
        peak = float(numpy.abs(samples).max()) / 32768
        self.level = peak
        setting = self.settings.gain
        if setting == "auto":
            if peak > GAIN_FLOOR:                       # 只用有聲音的片段估計(安靜時保持原本的倍數)
                self._peaks = (self._peaks + [peak])[-GAIN_WINDOW:]
            if self._peaks:
                loud = sorted(self._peaks)[int(len(self._peaks) * 0.9)]
                wanted = min(GAIN_MAX, max(1.0, GAIN_TARGET / max(loud, 1e-4)))
                # 慢慢調,音量突然變大(爆炸聲)時馬上降,免得破音
                self.gain = wanted if wanted < self.gain else self.gain + (wanted - self.gain) * 0.05
            if peak * self.gain > GAIN_CEILING:      # 這一段放大後會破音:馬上降到剛好不破
                self.gain = max(1.0, GAIN_CEILING / max(peak, 1e-4))
        else:
            try:
                self.gain = max(0.25, float(setting))
            except ValueError:
                self.gain = 1.0
        if abs(self.gain - 1.0) < 0.05:
            return pcm
        return numpy.clip(samples * self.gain, -32768, 32767).astype(numpy.int16).tobytes()

    def _now(self):
        """目前收到的聲音長度(秒,從開始算)。"""
        with self._lock:
            return (self._offset + len(self._audio) // 2) / RATE

    def _pcm(self, start, end):
        with self._lock:
            a = max(0, int(start * RATE) - self._offset) * 2
            b = max(0, int(end * RATE) - self._offset) * 2
            return bytes(self._audio[a:b])

    def _drop_before(self, seconds):
        """丟掉已經定稿的舊聲音,記憶體不會一直長。"""
        with self._lock:
            keep_from = int(seconds * RATE) - self._offset
            if keep_from > RATE * 2:
                cut = keep_from - RATE                       # 多留一秒
                del self._audio[:cut * 2]
                self._offset += cut

    def _speech(self, start, end):
        """這段時間裡有人說話的秒數。"""
        with self._lock:
            frames = self._probs[int(start / FRAME_SECONDS):int(end / FRAME_SECONDS)]
        return sum(p > SPEECH_ON for p in frames) * FRAME_SECONDS

    def _quiet_since(self, end):
        """從 end 往回數,連續安靜了幾秒。"""
        with self._lock:
            frames = self._probs[:int(end / FRAME_SECONDS)]
        quiet = 0
        for p in reversed(frames):
            if p >= SPEECH_OFF:
                break
            quiet += 1
        return quiet * FRAME_SECONDS

    def _first_speech(self, start, end):
        with self._lock:
            frames = self._probs[int(start / FRAME_SECONDS):int(end / FRAME_SECONDS)]
        for index, p in enumerate(frames):
            if p > SPEECH_ON:
                return start + index * FRAME_SECONDS
        return None

    def _sentence_cut(self, line, sentence, now):
        """一句太長時切在哪:最近一次辨識裡,最後一個講完的句子的結尾(至少 2 秒、離現在至少 0.5 秒);沒有的話 None。"""
        if line is None or not line.segments:
            return None
        groups = sentences(line.segments, line.language in SPACED)[:-1]      # 最後一句可能還在講
        ends = [sentence + g[1] for g in groups if _SENTENCE_END.search(g[2]) and g[1] >= 2.0]
        ends = [t for t in ends if t <= now - 0.5]
        return ends[-1] if ends else None

    def _quietest(self, start, end):
        """start～end 之間最安靜的地方(一句太長要切開時切在這裡)。"""
        with self._lock:
            first = int(start / FRAME_SECONDS)
            frames = self._probs[first:int(end / FRAME_SECONDS)]
        if not frames:
            return end
        # 連續三格平均最小的位置,避免切在單一雜訊上
        best, where = 2.0, len(frames) - 1
        for index in range(1, len(frames) - 1):
            value = sum(frames[index - 1:index + 2]) / 3
            if value < best:
                best, where = value, index
        return (first + where) * FRAME_SECONDS

    # ------------------------------------------------------------ 辨識

    def _listen(self):
        s = self.settings
        sentence = None                 # 目前這句的開始時間;None 是還沒有人說話
        scanned = 0.0
        last = 0.0
        blind_at = 0.0                  # 上一次「直接聽」聽到哪裡
        hold = None                     # 直接聽時還沒唱完(講完)的那段從哪裡開始,下次從這裡接著聽
        language = s.language if s.language != "auto" else ""
        line = None
        while not self._stop.is_set():
            self._new_audio.wait(0.5)
            self._new_audio.clear()
            if getattr(self._capture, "error", ""):
                self._fail(self._capture.error)
                return
            now = self._now()
            if self._paused.is_set():
                continue                    # 換辨識模型、聲音來源中:聲音先存著,好了再一起辨識
            if sentence is None:
                begin = self._first_speech(scanned, now)
                scanned = max(scanned, now - 0.1)
                if begin is None:
                    if now - blind_at >= (BLIND_STEP if hold is not None else BLIND_WINDOW):
                        try:
                            hold = self._blind(hold if hold is not None else max(blind_at, now - BLIND_WINDOW), now)
                        except Exception as exc:
                            if self._paused.is_set():
                                continue            # 正在換辨識模型:舊的被關掉了,不算出錯
                            if self._hiccup(exc):
                                continue
                            self._fail(f"語音辨識中斷：{exc}")
                            return
                        self._recovered()
                        blind_at = now
                    keep = min(now - BLIND_WINDOW, hold if hold is not None else now)
                    self._drop_before(keep - BLIND_SHIFT - 1)
                    continue
                sentence, last = max(0.0, begin - 0.2), begin
                if hold is not None:
                    # 直接聽時還沒唱完的那段接著聽(偵測到人聲了);先顯示的那句改由這句重新辨識
                    sentence, hold = min(sentence, hold), None
                    if self._held is not None and not self._held.final and self._held in self.lines:
                        self.lines.remove(self._held)
                    self._held = None
                if self._carry is not None and self._carry[1] is None:
                    self._carry = (self._carry[0], sentence)
                language = s.language if s.language != "auto" else ""
                line = None
            quiet = self._quiet_since(now)
            length = now - sentence
            spoken = self._speech(sentence, now)
            done = quiet >= (SHORT_PAUSE if spoken < SHORT_SPEECH else PAUSE) and spoken > 0
            too_long = length >= MAX_SENTENCE
            if not done and not too_long and now - last < s.step:
                continue
            last = now
            end = now
            if too_long and not done:
                # 一句太長要切開:優先切在句子結尾(最近一次辨識的句號、問號),找不到才切在最近 4 秒內最安靜的地方
                # (有配樂時最安靜的地方常在句子中間,切下來的前半句會只剩一兩個字)
                end = self._sentence_cut(line, sentence, now) or self._quietest(now - 4, now)
            elif done:
                end = now - quiet + 0.2
            speech = self._speech(sentence, end)
            if speech < MIN_SPEECH:
                if done or too_long:
                    sentence = None
                    scanned = end
                    if line is not None and not line.final:
                        self.lines.remove(line)
                        self.on_update()
                continue
            final = done or too_long
            if s.language == "auto" and (final or speech < LOCK_LANGUAGE):
                language = ""               # 還沒講多少、或要定稿了:用整段聲音重新判斷語言
            started = time.perf_counter()
            try:
                audio = self._pcm(sentence, end)
                text, detected, _ = self.server.transcribe(audio, language or "auto",
                                                           self._prompt(language))
                if not language:
                    sticky = self._sticky_language(detected)
                    if sticky:
                        # 前面幾句都是同一種語言:這句的判斷沒把握(動畫裡的喊叫常被判成韓文)就照前面的語言
                        detected = sticky
                        text, _, _ = self.server.transcribe(audio, sticky, self._prompt(sticky))
                    elif final and detected == "zh":
                        # 自動判斷出是中文:加上提示再辨識一次(繁體、有標點)
                        text, _, _ = self.server.transcribe(audio, "zh", self._prompt("zh"))
            except Exception as exc:
                if self._paused.is_set():
                    continue                    # 正在換辨識模型:舊的被關掉了,這次不算,換好後重新辨識
                if self._hiccup(exc):
                    continue                    # 這句先不辨識,聲音留著,等一下連上了再辨識
                self._fail(f"語音辨識中斷：{exc}")
                return
            self._recovered()
            self.costs = (self.costs + [time.perf_counter() - started])[-20:]
            # 跟不上時自動拉長間隔(例如只用處理器)
            s.step = max(s.step, min(3.0, self.costs[-1] * 1.5))
            language = language or detected
            text = self._clean(text, speech, language)
            segments = getattr(self.server, "last", {}).get("segments", [])
            words = getattr(self.server, "last", {}).get("words", [])
            if SPLIT_BY_WORDS:
                segments = split_segments(segments, words)
            spaced = language in SPACED
            carry = self._carry[0] if self._carry is not None and self._carry[1] == sentence else None
            if carry is not None and text:
                text = trim_overlap(carry.original, text, spaced)
                if segments:
                    first = segments[0]
                    segments = [(first[0], first[1], trim_overlap(carry.original, first[2], spaced))] + segments[1:]
            if text:
                if line is None:
                    line = Line(self._next_id, sentence, end, language=language)
                    self._next_id += 1
                    self.lines.append(line)
                line.words = [(sentence + a, sentence + b, w) for a, b, w in words]
                if final and line.original and line.language and line.language != language \
                        and len(text) < len(line.original) * SETTLE_SHORT:
                    # 定稿時語言判斷改了、字又少很多(日文長句變成英文「Wait.」):多半是判斷錯,照畫面上那句
                    text, language = line.original, line.language
                # 畫面上已經顯示的原文(同一種語言時才拿來比;語言判斷改了的話整句換掉是對的)
                displayed = line.original if line.language == language else ""
                line.end, line.language = end, language
                if final:
                    groups = sentences(segments, spaced) if end - sentence > 4 else []
                    joined = "".join(g[2] for g in groups)
                    if displayed and (len(joined) < len(displayed) * SETTLE_SHORT or degenerate(joined)):
                        groups = []                     # 定稿那次漏掉很多字或鬼打牆:不拆,用畫面上那句
                    if len(groups) > 1:
                        self._finish_groups(line, groups, sentence, speech)    # 一口氣講好幾句:一句一行
                    else:
                        if too_long and not done:
                            # 被切開的前半句:和畫面上那句對應的部分(後半句會變成下一句,不能整句留著);
                            # 定稿那次辨識和畫面對不上時,用最近一次辨識裡切點之前的那幾句
                            planned = (" " if spaced else "").join(
                                g[2] for g in sentences(line.segments, spaced) if sentence + g[1] <= end + 0.05)
                            shown = displayed_prefix(displayed, text) or displayed_prefix(displayed, planned) or planned
                            line.original = settle(shown or "", "", text)
                        else:
                            line.original = settle(displayed, line.committed, text)
                        self._commit(line)
                else:
                    # 一口氣講好幾句(對話很快、沒停頓):前面講完、前後兩次辨識都一樣的句子先定稿,後面的當新的一句
                    count, cut = finished_sentences(line.segments, segments, spaced)
                    line.segments = segments
                    finished = (" " if spaced else "").join(g[2] for g in sentences(segments, spaced)[:count])
                    finished = self._clean(finished, speech, language) if count else ""
                    # 前半句照畫面上顯示的字切(用最新辨識的字的話,看過的內容會被換掉);對不起來就先不拆
                    first_half = displayed_prefix(displayed, finished) if finished else None
                    if first_half and cut >= SPLIT_AFTER and end - (sentence + cut) >= 0.5:
                        line.original, line.end = first_half, sentence + cut
                        self._commit(line)
                        self.on_update()
                        # 下一句從切點前一點開始聽(切點不準時第一個字才不會被吃掉),重複的字之後去掉
                        next_start = max(sentence, sentence + cut - OVERLAP_BACK)
                        self._carry = (line, next_start)
                        # 畫面上切點後面的字馬上放進新的一行(不然要等下一次辨識才出現,字會消失一下)
                        rest = displayed[len(first_half):].lstrip("，、。！？… 　,.!?") \
                            if displayed.startswith(first_half) else ""
                        following = None
                        if rest:
                            following = Line(self._next_id, next_start, end, original=rest, language=language)
                            self._next_id += 1
                            self.lines.append(following)
                        sentence, line, last = next_start, following, now
                        continue
                    # 前後兩次一樣的開頭固定下來;畫面上只有後面不確定的字會變
                    agreed = agree(line.previous, text, language in SPACED)
                    if agreed.startswith(line.committed) and len(agreed) > len(line.committed):
                        line.committed = agreed
                    line.previous = text
                    line.original = merge(line.committed, text)
                    grown = len(line.original) - len(line.source) >= PARTIAL_GROW
                    if s.translate and s.partial and grown and time.monotonic() - line.translated_at >= PARTIAL_GAP:
                        self._queue_translation(line, final=False)
                self.on_update()
            elif final and line is not None and not line.final:
                self.lines.remove(line)
                self.on_update()
            if final:
                sentence = None
                scanned = blind_at = end
                if too_long and not done and line is not None and line.final:
                    # 還在講就被切開:下一句從切點前一點開始聽,重複的字之後去掉
                    scanned = max(0.0, end - OVERLAP_BACK)
                    self._carry = (line, None)
                elif done:
                    self._carry = None
                line = None
                self._drop_before(scanned)

    def _hiccup(self, exc):
        """暫時連不上本地辨識伺服器(Windows 偶爾不給連線,WinError 10013):伺服器還在、也還沒連續連不上太久,
        就稍等再辨識,字幕不停(那幾秒的聲音還留著,連上後照樣辨識)。"""
        if not isinstance(exc, (OSError, http.client.HTTPException)) or not getattr(self.server, "alive", False):
            return False
        now = time.monotonic()
        if self._hiccup_since is None:
            self._hiccup_since = now
        if now - self._hiccup_since > HICCUP_LIMIT:
            return False
        self._stop.wait(0.5)
        return True

    def _recovered(self):
        if self._hiccup_since is not None:
            gap = time.monotonic() - self._hiccup_since
            self._hiccup_since = None
            if gap >= 2:
                self.notice(f"語音辨識暫時連不上，字幕延遲了 {round(gap)} 秒")

    def _prompt(self, language):
        """給 Whisper 的提示(只在知道語言時給,不然會把判斷語言帶偏):中文要繁體有標點、口語的例子、專有名詞。"""
        if not language:
            return ""
        s = self.settings
        parts = [ZH_PROMPT] if language == "zh" else []
        if s.verbatim and language in SPOKEN_PROMPTS:
            parts.append(SPOKEN_PROMPTS[language])
        if s.model in asr.NO_PUNCTUATION:
            parts = []          # 中文(台灣)本來就繁體、不會照提示加標點;實測不給提示錯字略少(專有名詞照給)
        terms = names.prompt(s.glossary)
        if terms:
            parts.append(terms)
        return " ".join(parts)

    def _echoed_prompt(self, text):
        """對著安靜或雜音時 Whisper 偶爾會把提示照抄出來:這種結果不要。"""
        terms = names.prompt(self.settings.glossary)
        # 口語的例子要整段一樣才算(「えーと、あの」本來就可能是真的有人這樣說)
        return bool(text) and ((len(terms) >= 8 and terms[:8] in text) or
                               any(p.rstrip("。.…") in text for p in SPOKEN_PROMPTS.values()))

    def _recent_language(self):
        """最近幾句大多是哪種語言(至少 3 句、六成以上一樣才算);沒有的話回傳空字串。"""
        recent = [line.language for line in self.lines[-6:] if line.final and line.language]
        if len(recent) < 3:
            return ""
        top = max(set(recent), key=recent.count)
        return top if recent.count(top) >= max(3, len(recent) * 0.6) else ""

    def _sticky_language(self, detected):
        """自動判斷語言時,這句判出來的語言和前面不一樣:判斷很有把握(真的換語言說話)才換,
        否則回傳前面的語言(要照它重新辨識);不用換的話回傳空字串。"""
        recent = self._recent_language()
        if not recent or detected == recent:
            return ""
        chances = getattr(self.server, "last", {}).get("languages") or {}
        if not chances:
            return ""
        sure = chances.get(detected, 0) >= STICKY_SURE and chances.get(recent, 0) < STICKY_KEEP
        return "" if sure else recent

    def _clean(self, text, speech, language):
        text = "" if self._echoed_prompt(text) else plausible(text, speech)
        if text and language == "zh":
            text = tidy_chinese(text, self.settings.target)
        return text

    def _commit(self, line):
        """這句定稿、送去翻譯。只有語助詞(「嗯。」「えーと」)又緊接在上一句後面時,接到上一句後面,
        不另外佔一行(字幕才不會一行一個「嗯」,太零碎)。"""
        line.final = True
        previous = next((l for l in reversed(self.lines)
                         if l.final and l is not line and l.original and not l.notice), None)
        carry = self._carry
        if previous is not None and carry is not None and carry[0] is previous and carry[1] is not None \
                and abs(line.start - carry[1]) < 0.05:
            line.original = trim_overlap(previous.original, line.original, line.language in SPACED)
            if not any(ch.isalnum() for ch in line.original):
                if line in self.lines:
                    self.lines.remove(line)     # 整句都是上一句的結尾:不要
                return
        if self._repeated(line, previous):
            if line in self.lines:
                self.lines.remove(line)
            return
        if self._fragment_of(previous, line):
            # 上一句結尾多收到這句的開頭(「你不知道，」),這句又完整講了一次:上一句那個碎片拿掉
            if previous in self.lines:
                self.lines.remove(previous)
        if previous is not None and filler_only(line.original, line.language) \
                and line.start - previous.end <= MERGE_GAP and len(previous.original) < MERGE_LIMIT:
            joiner = " " if previous.language in SPACED else ""
            previous.original = f"{previous.original}{joiner}{line.original}"
            previous.end = max(previous.end, line.end)
            if line in self.lines:
                self.lines.remove(line)
            self._queue_translation(previous, final=True)
            return
        self._finalize(line)

    @staticmethod
    def _fragment_of(previous, line):
        """上一句只是這句開頭的一小段(8 個字以內)、時間又重疊:同一段話被拆成碎片又完整聽了一次。"""
        if previous is None or line.start > previous.end + REPEAT_GAP:
            return False
        head = plain(previous.original)
        return 2 <= len(head) <= 8 and plain(line.original).startswith(head) and len(plain(line.original)) > len(head)

    @staticmethod
    def _repeated(line, previous):
        """同一段聲音聽了兩次(人聲偵測和直接聽都聽到、切開的地方重疊):時間和上一句重疊、字也已經在上一句裡。"""
        if previous is None or line.start > previous.end + REPEAT_GAP:
            return False
        words = "".join(ch for ch in line.original.lower() if ch.isalnum())
        return len(words) >= 2 and words in "".join(ch for ch in previous.original.lower() if ch.isalnum())

    def _finish_groups(self, line, groups, sentence, speech):
        """定稿時這段有好幾句:第一句用原本那一行,其他每句各一行,一句一句翻。"""
        first = True
        for start, end, text in groups:
            text = self._clean(text, speech, line.language)
            if not text:
                continue
            if first:
                target, first = line, False
                target.end = sentence + end
            else:
                target = Line(self._next_id, sentence + start, sentence + end, language=line.language,
                              words=line.words)
                self._next_id += 1
                self.lines.append(target)
            target.original = text
            self._commit(target)
        if first:                                   # 每句都被濾掉了
            line.original, line.final = "", True
            self.lines.remove(line)

    def _blind(self, start, end):
        """配樂、爆炸很大聲時人聲偵測常抓不到台詞:這段夠大聲又沒偵測到人聲,就直接聽一次,
        Whisper 很有把握、語言也對的才當成台詞(對著配樂常會亂猜「*Gunshot*」「I'm going to go.」)。
        回傳下次要從哪裡接著聽:最後一段像是還沒唱完(講完)時先顯示、不定稿,下次從那段開頭再聽
        (歌曲常整首都偵測不到人聲,固定每 4 秒切一次會切在字中間);None 是這段都處理完了。"""
        held, self._held = self._held, None
        resume = self._blind_listen(start, end, held)
        if held is not None and not held.final and held is not self._held:
            # 這次沒接上(聽不清楚、判斷成不是台詞):上次確認過、已經顯示的字照樣定稿
            previous = next((l for l in reversed(self.lines)
                             if l.final and l is not held and l.original and not l.notice), None)
            if self._repeated(held, previous):
                if held in self.lines:
                    self.lines.remove(held)
            else:
                held.final = True
                self._finalize(held)
            self.on_update()
        return resume

    def _blind_listen(self, start, end, held):
        s = self.settings
        audio = self._pcm(start, end)
        if len(audio) < RATE or self._speech(start, end) >= MIN_SPEECH:
            return None
        samples = array("h", audio)[::4]
        if math.sqrt(sum(x * x for x in samples) / max(1, len(samples))) < BLIND_LOUD:
            return None
        fixed = s.language if s.language != "auto" else ""
        text, detected, _ = self.server.transcribe(audio, fixed or "auto", self._prompt(fixed))
        confidence = getattr(self.server, "last", {}).get("confidence", 0.0)
        segments = [g for g in getattr(self.server, "last", {}).get("segments", []) if g[2].strip()]
        words = [(start + a, start + b, w) for a, b, w in getattr(self.server, "last", {}).get("words", [])]
        language = fixed or detected
        recent = next((l.language for l in reversed(self.lines) if l.final and l.language), "")
        if confidence < MIN_CONFIDENCE or (not fixed and recent and detected != recent):
            return None
        text = self._clean(text, 0, language)
        spaced = language in SPACED
        size = len(text.split()) if spaced else sum(ch.isalnum() for ch in text)
        if size < (2 if spaced else 3):
            return None
        # 多聽前面一點再聽一次(後面不切掉,短句才不會被切斷):真的台詞兩次會聽到一樣的字詞,
        # 對著配樂亂猜的每次都不一樣
        again, _, _ = self.server.transcribe(self._pcm(start - BLIND_SHIFT, end), language,
                                             self._prompt(language))
        if not shared_words(text, self._clean(again, 0, language), spaced):
            return None
        resume, tail_text = None, ""
        if segments and end - start < BLIND_LONGEST:
            tail = segments[-1]
            if len(segments) >= 2 and tail[0] >= 1.0:
                # 好幾段:前面的定稿,最後一段先顯示,下次從它開頭再聽(可能還沒唱完)
                text = self._clean((" " if spaced else "").join(g[2].strip() for g in segments[:-1]), 0, language)
                tail_text, resume = self._clean(tail[2].strip(), 0, language), start + tail[0]
            elif tail[1] >= end - start - BLIND_EDGE:
                # 只有一段而且唱到最後:整段先顯示、不定稿,下次聽長一點
                text, tail_text, resume = "", text, start
        previous = next((l for l in reversed(self.lines) if l.final and l.original and not l.notice), None)
        if previous is not None and start < previous.end + 2.0:
            # 剛講完的那句又被直接聽到一次(聽的範圍包含了它的結尾):重複的部分去掉
            def fresh(words):
                words = trim_overlap(previous.original, words, spaced)
                return "" if "".join(ch for ch in words.lower() if ch.isalnum()) in \
                    "".join(ch for ch in previous.original.lower() if ch.isalnum()) else words
            text = fresh(text) if text else ""
            tail_text = fresh(tail_text) if tail_text else ""
        if text:
            line = held if held is not None else Line(self._next_id, start, end, language=language)
            if held is None:
                self._next_id += 1
                self.lines.append(line)
            line.original, line.end, line.language, line.final = text, resume or end, language, True
            line.words = words
            held = None
            self._finalize(line)
            self.on_update()
        if tail_text:
            line = held if held is not None else Line(self._next_id, resume, end, language=language)
            if held is None:
                self._next_id += 1
                self.lines.append(line)
            line.original, line.end, line.language = tail_text, end, language
            line.words = words
            self._held = line
            if s.translate and s.partial:
                self._queue_translation(line, final=False)
            self.on_update()
        return resume

    # ------------------------------------------------------------ 判斷誰說話

    def _finalize(self, line):
        """定稿的句子送去翻譯。開了判斷誰說話:先交給另一個執行緒認人(一句裡換人就切開成好幾行),
        認完再翻譯(多約 0.1～0.3 秒);聲音要現在取,之後就被丟掉了。"""
        line.segments = []                  # 定稿後用不到了(長時間字幕才不會一直累積)
        if self.settings.speakers and not self.speaker_error:
            audio = self._pcm(line.start, line.end)
            if audio:
                self._voices.put((line, audio))
                return
        line.words = []
        self._queue_translation(line, final=True)

    def _speaker_loop(self):
        extractor = None
        while not self._stop.is_set():
            job = self._voices.get()
            if job is None:
                break
            line, audio = job
            parts = [(line, audio)]
            if not self.speaker_error:
                try:
                    if extractor is None:
                        import tempfile
                        from pathlib import Path

                        from core import diarize

                        # 模型要放在純英文的路徑才開得了(程式資料夾可能有中文)
                        extractor = diarize.Extractor(Path(tempfile.gettempdir()) / "naiz-speaker", threads=2)
                    mode = self.settings.speaker_mode       # 字幕進行中換了也是下一句開始套用
                    method = mode.get("method", "centered" if mode.get("centered") else "plain")
                    self.tracker.same, self.tracker.centered = mode["same"], method == "centered"
                    parts = self._split_speakers(extractor, line, audio, mode["split"])
                    for part, sound in parts:
                        vector = extractor.embed_pcm16(sound[:int(voices.LONGEST * RATE) * 2])
                        seconds = len(sound) / 2 / RATE
                        if method == "cluster":
                            # 自動分群:重新分群後,最近 10 秒內的舊句子顏色可能跟著修正
                            part.speaker, changed = self.clusterer.add(vector, seconds, part, part.end)
                            for other, label in changed:
                                other.speaker = label
                        else:
                            part.speaker = self.tracker.assign(vector, seconds)
                except Exception as exc:
                    self.speaker_error = f"判斷誰說話失敗：{exc}"
            for part, _ in parts:
                part.words = []             # 每個字的時間只有切開時用得到
                self._queue_translation(part, final=True)
            self.on_update()
        if extractor is not None:
            extractor.close()

    def _split_speakers(self, extractor, line, audio, split=voices.SPLIT):
        """一句裡換人(快速對話中間沒停頓,被當成一句):每 0.75 秒取 1.5 秒算聲音特徵,前後差很多的地方切開,
        切在最近的字與字之間。回傳 [(那一行, 那段聲音)];沒換人就是原本那一行。"""
        times = self._handoff(extractor, line, audio)
        if times is None:
            size, hop = int(voices.WINDOW * RATE) * 2, int(voices.HOP * RATE) * 2
            count = (len(audio) - size) // hop + 1 if len(audio) >= size else 0
            if count < voices.SIDE * 2:
                return [(line, audio)]
            vectors = [extractor.embed_pcm16(audio[i * hop:i * hop + size]) for i in range(count)]
            cuts = voices.change_points(vectors, split)
            times = [line.start + k * voices.HOP + (voices.WINDOW - voices.HOP) / 2 for k in cuts]
        pieces = split_text(line.original, line.words, line.start, line.end, times, line.language in SPACED)
        if len(pieces) < 2 or line not in self.lines:
            return [(line, audio)]          # 不用切,或這行已經被拿掉了(重複的碎片):切出來的新行會變成孤兒
        parts = []
        for index, (start, end, text) in enumerate(pieces):
            if index == 0:
                part = line
                part.original, part.end = text, end
            else:
                part = Line(next(self._split_ids), start, end, original=text, final=True, language=line.language,
                            words=line.words)
                try:
                    self.lines.insert(self.lines.index(parts[-1][0]) + 1, part)
                except ValueError:
                    self.lines.append(part)
            sound = audio[int((start - line.start) * RATE) * 2:int((end - line.start) * RATE) * 2]
            parts.append((part, sound))
        return parts

    def _handoff(self, extractor, line, audio):
        """自動分群已經認得兩個人以上時,用「切點兩邊各像誰」找換人的地方(短短一句接話也找得到):
        回傳 [切開的時間] 或 [](沒換人);還沒認得兩個人(或不是自動分群)回傳 None,改用舊的找法。"""
        mode = self.settings.speaker_mode
        if mode.get("method") != "cluster":
            return None
        centers = self.clusterer.centers()
        if len(centers) < 2:
            return None
        seconds = len(audio) / 2 / RATE
        # 長句試的間隔放寬,最多試 HAND_TRIES 次(每次算兩段聲音特徵,太多次翻譯會等太久)
        step = max(voices.HAND_STEP, (seconds - 2 * voices.HAND_EDGE) / voices.HAND_TRIES)
        longest = int(voices.LONGEST * RATE) * 2
        moment, best = voices.HAND_EDGE, None
        while moment <= seconds - voices.HAND_EDGE:
            cut = int(moment * RATE) * 2
            sure = voices.handoff(extractor.embed_pcm16(audio[max(0, cut - longest):cut]),
                                  extractor.embed_pcm16(audio[cut:cut + longest]), centers)
            if sure is not None and (best is None or sure > best[0]):
                best = (sure, moment)           # 好幾個地方都像換人:切在最確定的那裡
            moment += step
        return [line.start + best[1]] if best else []

    # ------------------------------------------------------------ 翻譯

    def _queue_translation(self, line, final):
        s = self.settings
        if not s.translate:
            return
        if same_language(line.language, s.target):
            line.same = True
            line.translation = ollama.clean(line.original, s.target)
            return
        line.same = False
        if final and line.translation and line.translated_from == line.original:
            # 講到一半時已經把這整句翻好了(原文後來沒再變):不重翻,畫面上的翻譯不會再被換掉
            self.delays = (self.delays + [0.0])[-20:]
            return
        if final:
            self._jobs.put((line, line.original, True, time.monotonic()))
        else:
            line.translated_at = time.monotonic()
            with self._partial_lock:
                self._partial = (line, line.original)
            self._jobs.put("partial")

    def _translate_loop(self):
        s = self.settings
        while not self._stop.is_set():
            job = self._jobs.get()
            if job is None:
                return
            if job == "partial":
                with self._partial_lock:
                    pending, self._partial = self._partial, None
                if pending is None:
                    continue
                line, text = pending
                if line.final or self._has_final_waiting():
                    continue                       # 已經有完整的要翻,先翻完整的
                final, queued = False, None
            else:
                line, text, final, queued = job
            # 前面三句的原文和譯文:人名、稱呼才會前後一致(只給原文時同一個人名每句翻得不一樣)
            context = [(l.original, "" if l.same else l.translation)
                       for l in self.lines if l.final and l.id < line.id and not l.notice][-3:]
            language = line.language or s.language
            # 講到一半:接著上次的翻譯繼續翻(已經固定的部分不重翻),原文是接著上次翻的那段長出來的才這樣做。
            # 講完:整句重翻(接著半句的翻譯硬接,容易變成「前半句的翻譯 + 整句的翻譯」);
            # 畫面上先留著舊的翻譯,新的翻好才一次換掉,不會整句消失再長出來
            prefix = ""
            if not final and line.translation and line.source \
                    and text.startswith(line.source[:max(1, len(line.source) - 2)]):
                prefix = stable_translation(line.translation)
            line.source = text
            keep = final and bool(line.translation)
            try:
                def show(partial_text, line=line, final=final):
                    if (final or not line.final) and not keep:
                        line.translation = partial_text
                        self.on_update()

                result = self._translate(s.translator, text, language, s.target, context, glossary=s.glossary,
                                         on_text=show, cancel=lambda: self._stop.is_set(), prefix=prefix)
                if final and untranslated(result, text, language, s.target) and not self._stop.is_set():
                    retry = self._translate(s.translator, text, language, s.target, context, glossary=s.glossary,
                                            cancel=lambda: self._stop.is_set(), strict=True)
                    if not untranslated(retry, text, language, s.target):
                        result = retry
                if final or not line.final:
                    line.translation = result
                    # 定稿時可以沿用的只有「整句一次翻好」的:接著前面續翻的(prefix)是硬接起來的,品質較差,定稿照樣重翻
                    if not prefix and not untranslated(result, text, language, s.target):
                        line.translated_from = text
                if final and queued is not None:
                    self.delays = (self.delays + [time.monotonic() - queued])[-20:]
                self.on_update()
            except Exception as exc:
                self.message = f"翻譯失敗：{exc}"
                self.on_update()

    def _has_final_waiting(self):
        return any(isinstance(job, tuple) for job in list(self._jobs.queue))

    # ------------------------------------------------------------ 給畫面用

    def recent(self, count=2):
        """字幕上要顯示的最後幾句:[(那一句, 講完幾秒了)];還在講的那句是 0 秒。說明行不算。"""
        lines = [line for line in self.lines if line.original and not line.notice]
        now = self._now() if self.started_at is not None else 0
        return [(line, max(0.0, now - line.end) if line.final else 0.0) for line in lines[-count:]]

    # ------------------------------------------------------------ 字幕進行中改設定

    def notice(self, text):
        """在字幕紀錄裡加一行說明(變更設定、重新開始);字幕視窗不顯示。"""
        now = self._now() if self.started_at is not None else 0.0
        line = Line(self._next_id, now, now, original=text, final=True, notice=True)
        self._next_id += 1
        self.lines.append(line)
        self.on_update()

    def reconfigure(self, changes, summary):
        """字幕進行中改設定:changes 是 {Settings 欄位: 新值},summary 是給人看的「辨識模型 推薦 → 最準確」。
        換辨識模型、聲音來源要短暫中斷:聲音照收、辨識先停,重新載入好了再補上這段時間的字幕;
        其他設定下一句就用新的。字幕紀錄裡會記下變更了什麼、說明、什麼時候繼續。"""
        s = self.settings
        old_translator, was_translating = s.translator, s.translate
        restart_model = "model" in changes and changes["model"] != s.model
        restart_source = any(k in changes and changes[k] != getattr(s, k) for k in ("source", "pid"))
        for key, value in changes.items():
            setattr(s, key, value)
        if not (restart_model or restart_source):
            self.notice(f"變更設定：{summary}（下一句開始套用）")
            self._switch_translator(old_translator, was_translating)
            return
        self.notice(f"變更設定：{summary}")
        if restart_model:
            gpu = "（顯示卡約需 30 秒）" if asr.gpu() else ""
            self.notice(f"說明：正在載入新的辨識模型{gpu}，這段時間的聲音會先存著，載入後補上字幕")
        else:
            self.notice("說明：正在換聲音來源，換好就繼續")
        self._paused.set()

        def work():
            began = time.monotonic()
            with self._reconfiguring:
                try:
                    if restart_source:
                        self._restart_capture()
                    if restart_model:
                        self.message = "載入新的辨識模型"
                        self.on_update()
                        old, self.server = self.server, None
                        if old is not None:
                            old.stop()                  # 先關舊的,顯示卡才放得下新的
                        server = asr.Server(s.model)
                        server.start(cancel=self._stop)
                        if self._stop.is_set():
                            server.stop()               # 載入時使用者按了停止:不留下沒人管的辨識程式
                            return
                        self.server = server
                    if self._stop.is_set():
                        return
                    self.costs = []
                    self.state, self.message = "running", "字幕進行中"
                    self._paused.clear()
                    self._new_audio.set()
                    # 字幕紀錄左邊的時間是「開始後過了多久」,這裡不寫時鐘時間,寫中斷了多久
                    self.notice(f"字幕繼續（中斷了 {max(1, round(time.monotonic() - began))} 秒）")
                    self._switch_translator(old_translator, was_translating)
                except Exception as exc:
                    if not self._stop.is_set():
                        self._fail(f"變更設定失敗：{exc}")

        threading.Thread(target=work, daemon=True).start()

    def _restart_capture(self):
        s = self.settings
        if self._capture is not None:
            self._capture.stop()
        if self._capture_factory is not None:
            self._capture = self._capture_factory(self._feed)
        else:
            from .capture import Capture
            self._capture = Capture(s.source, self._feed, s.pid)
        self._capture.start()
        if hasattr(self._capture, "started"):
            self._capture.started.wait(5)
        if getattr(self._capture, "error", ""):
            raise RuntimeError(self._capture.error)

    def _switch_translator(self, old, was_translating):
        """換翻譯模型或打開翻譯:先載入新的(載好前照常用舊的設定翻),再把舊的移出顯示卡。"""
        s = self.settings
        if not s.translate or (s.translator == old and was_translating):
            if was_translating and not s.translate:
                threading.Thread(target=ollama.unload, args=(old,), daemon=True).start()
            return

        def work():
            try:
                ollama.preload(s.translator)
            except Exception as exc:
                self.message = f"翻譯模型載入失敗：{exc}"
                self.on_update()
                return
            if was_translating and old != s.translator:
                ollama.unload(old)

        threading.Thread(target=work, daemon=True).start()

