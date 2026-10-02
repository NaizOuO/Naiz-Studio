"""即時字幕的流程:擷取聲音 → 人聲偵測 → 每隔一小段重新辨識「這句開始到現在」(字會一直長出來)→
停頓就定稿 → 翻譯(講到一半先試翻,講完再翻一次完整的)。

排程用「聲音的時間」而不是時鐘:每收到 step 秒的新聲音就辨識一次;電腦慢、辨識跟不上時,下一次自然涵蓋
更長的聲音,不會越積越多。實測(RTX 5060 Ti、turbo、qwen3:8b):說完到翻譯出來平均約 1.3 秒。
"""

import difflib
import math
import queue
import re
import threading
import time
from array import array
from dataclasses import dataclass, field

from . import asr, glossary as names
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
BLIND_WINDOW = 4.0          # 配樂、爆炸很大聲時人聲偵測常抓不到台詞:每這麼多秒直接聽一次
BLIND_LOUD = 500            # 聲音大小(RMS)超過這個才直接聽(安靜時不聽,免得對著靜音亂猜)
MIN_CONFIDENCE = -0.5       # 直接聽的結果:Whisper 的把握(平均對數機率)要高於這個才算數
BLIND_SHIFT = 1.5           # 直接聽到東西時,多聽前面這麼多秒再聽一次確認


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
    translated_at: float = 0.0
    same: bool = False          # 原文就是目標語言,沒有翻譯
    segments: list = field(default_factory=list)    # 上一次辨識的分段(找出已經講完的句子)


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


def finished_sentences(previous, current, spaced):
    """前後兩次辨識都一樣、已經講完的句子(最後一句可能還在講,不算):回傳 (句數, 結束秒數)。"""
    old, new = sentences(previous, spaced), sentences(current, spaced)
    count, end = 0, 0.0
    for index, (a, b) in enumerate(zip(old, new[:-1])):
        if a[2] != b[2] or not _SENTENCE_END.search(b[2]):
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

    # ------------------------------------------------------------ 開始、停止

    def start(self):
        self.state, self.message = "loading", "準備中"
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()
        self._new_audio.set()
        self._jobs.put(None)
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
                ollama.preload(s.translator)
            if self._stop.is_set():
                return
            threading.Thread(target=self._translate_loop, daemon=True).start()
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
        probs = self._vad.feed(pcm)
        with self._lock:
            self._audio += pcm
            self._probs += probs
        self._new_audio.set()

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
        language = s.language if s.language != "auto" else ""
        line = None
        while not self._stop.is_set():
            self._new_audio.wait(0.5)
            self._new_audio.clear()
            if getattr(self._capture, "error", ""):
                self._fail(self._capture.error)
                return
            now = self._now()
            if sentence is None:
                begin = self._first_speech(scanned, now)
                scanned = max(scanned, now - 0.1)
                if begin is None:
                    if now - blind_at >= BLIND_WINDOW:
                        try:
                            self._blind(max(blind_at, now - BLIND_WINDOW), now)
                        except Exception as exc:
                            self._fail(f"語音辨識中斷：{exc}")
                            return
                        blind_at = now
                    self._drop_before(now - BLIND_WINDOW - BLIND_SHIFT - 1)
                    continue
                sentence, last = max(0.0, begin - 0.2), begin
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
                end = self._quietest(now - 4, now)                  # 切在最近 4 秒內最安靜的地方
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
                self._fail(f"語音辨識中斷：{exc}")
                return
            self.costs = (self.costs + [time.perf_counter() - started])[-20:]
            # 跟不上時自動拉長間隔(例如只用處理器)
            s.step = max(s.step, min(3.0, self.costs[-1] * 1.5))
            language = language or detected
            text = self._clean(text, speech, language)
            segments = getattr(self.server, "last", {}).get("segments", [])
            spaced = language in SPACED
            if text:
                if line is None:
                    line = Line(self._next_id, sentence, end, language=language)
                    self._next_id += 1
                    self.lines.append(line)
                line.end, line.language = end, language
                if final:
                    groups = sentences(segments, spaced) if end - sentence > 4 else []
                    if len(groups) > 1:
                        self._finish_groups(line, groups, sentence, speech)    # 一口氣講好幾句:一句一行
                    else:
                        line.original = text
                        self._commit(line)
                else:
                    # 一口氣講好幾句(對話很快、沒停頓):前面講完、前後兩次辨識都一樣的句子先定稿,後面的當新的一句
                    count, cut = finished_sentences(line.segments, segments, spaced)
                    line.segments = segments
                    finished = (" " if spaced else "").join(g[2] for g in sentences(segments, spaced)[:count])
                    finished = self._clean(finished, speech, language) if count else ""
                    if finished and cut >= SPLIT_AFTER and end - (sentence + cut) >= 0.5:
                        line.original, line.end = finished, sentence + cut
                        self._commit(line)
                        self.on_update()
                        sentence, line, last = sentence + cut, None, now
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
                line = None
                self._drop_before(end)

    def _prompt(self, language):
        """給 Whisper 的提示(只在知道語言時給,不然會把判斷語言帶偏):中文要繁體有標點、口語的例子、專有名詞。"""
        if not language:
            return ""
        s = self.settings
        parts = [ZH_PROMPT] if language == "zh" else []
        if s.verbatim and language in SPOKEN_PROMPTS:
            parts.append(SPOKEN_PROMPTS[language])
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
        previous = next((l for l in reversed(self.lines) if l.final and l is not line and l.original), None)
        if previous is not None and filler_only(line.original, line.language) \
                and line.start - previous.end <= MERGE_GAP and len(previous.original) < MERGE_LIMIT:
            joiner = " " if previous.language in SPACED else ""
            previous.original = f"{previous.original}{joiner}{line.original}"
            previous.end = max(previous.end, line.end)
            if line in self.lines:
                self.lines.remove(line)
            self._queue_translation(previous, final=True)
            return
        self._queue_translation(line, final=True)

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
                target = Line(self._next_id, sentence + start, sentence + end, language=line.language)
                self._next_id += 1
                self.lines.append(target)
            target.original = text
            self._commit(target)
        if first:                                   # 每句都被濾掉了
            line.original, line.final = "", True
            self.lines.remove(line)

    def _blind(self, start, end):
        """配樂、爆炸很大聲時人聲偵測常抓不到台詞:這段夠大聲又沒偵測到人聲,就直接聽一次,
        Whisper 很有把握、語言也對的才當成台詞(對著配樂常會亂猜「*Gunshot*」「I'm going to go.」)。"""
        s = self.settings
        audio = self._pcm(start, end)
        if len(audio) < RATE or self._speech(start, end) >= MIN_SPEECH:
            return
        samples = array("h", audio)[::4]
        if math.sqrt(sum(x * x for x in samples) / max(1, len(samples))) < BLIND_LOUD:
            return
        fixed = s.language if s.language != "auto" else ""
        text, detected, _ = self.server.transcribe(audio, fixed or "auto", self._prompt(fixed))
        confidence = getattr(self.server, "last", {}).get("confidence", 0.0)
        language = fixed or detected
        recent = next((l.language for l in reversed(self.lines) if l.final and l.language), "")
        if confidence < MIN_CONFIDENCE or (not fixed and recent and detected != recent):
            return
        text = self._clean(text, 0, language)
        spaced = language in SPACED
        size = len(text.split()) if spaced else sum(ch.isalnum() for ch in text)
        if size < (2 if spaced else 3):
            return
        # 多聽前面一點再聽一次(後面不切掉,短句才不會被切斷):真的台詞兩次會聽到一樣的字詞,
        # 對著配樂亂猜的每次都不一樣
        again, _, _ = self.server.transcribe(self._pcm(start - BLIND_SHIFT, end), language,
                                             self._prompt(language))
        if not shared_words(text, self._clean(again, 0, language), spaced):
            return
        line = Line(self._next_id, start, end, original=text, final=True, language=language)
        self._next_id += 1
        self.lines.append(line)
        self._queue_translation(line, final=True)
        self.on_update()

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
                       for l in self.lines if l.final and l.id < line.id][-3:]
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
        """字幕上要顯示的幾句:最後一句(可能還在長),和它前一句(如果是最近才講完的)。"""
        lines = [line for line in self.lines if line.original]
        if not lines:
            return []
        shown = lines[-count:]
        now = self._now() if self.started_at is not None else 0
        return [line for line in shown if line is lines[-1] or now - line.end < 8]

