"""精修(更精確的辨識):初版逐字稿做完後,在背後再分析一次,修正同音錯字(例如「牢淘」→「老套」)。使用者只會看到比較久、比較準。

做法:
1. 每一條字幕的聲音給 Qwen3-ASR 再聽一次,得到第二份結果
2. 和初版逐字對照,只挑「字數一樣、換了幾個字」的地方當候選(第一版只修同音錯字,不增刪字)
3. 讓 Qwen3-ASR 聽聲音替「原本的句子」和「換掉之後的句子」打分數:回答開頭先寫好候選的前 k 個字,
   看模型認為下一個字是候選的下一個字的機率,全部加起來。只比字數一樣的句子(不會偏好字少的),明顯比較好才換。
   連同前後字幕一起聽(有前後文,開頭的字才不會聽錯),只修這一條裡面的字。
   (實驗 E11:正確的「有一點老套」-8.5,Whisper 的「有一點牢淘」-43.4;同音錯字都差很多)
- Qwen3-ASR 用簡體訓練:打分時句子轉簡體、拿掉標點(標點沒有聲音);換上去的字轉回輸出的用字
- 只換文字,字幕的時間不動
"""

import base64
import difflib
import json
import re
import urllib.request
import wave

from core import transcribe

from plugins.subtitle import asr

PAD = 0.25                  # 聲音前後多留幾秒(不超過前後字幕)
MARGIN = 6.0                # 換掉後的總分要比原本好這麼多才換(用有人工字幕的素材校準,見 testfile/tests/refine_eval.py)
LONGEST_SPAN = 3            # 一處最多換幾個字
WINDOW = 13.0               # 連同前後字幕一起聽,最長幾秒(Qwen3-ASR 一次最多 15 秒)
JOIN_GAP = 1.5              # 前後字幕停頓不超過這麼久才一起聽(停很久的多半是另一段話)
TOP = 20                    # 每次看前幾名的機率
_DROP = re.compile(r"[\W_]+")
SURE = 0.6                  # Whisper 對這幾個字的把握都高於這個:不修(初版多半是對的)
# 第一版只修同音錯字:不換語助詞(Qwen3-ASR 偏好大陸寫法,「喔」常被換成「哦」),
# 也不換同音、要看句意決定寫法的字(「他／它」「得／的」)
PARTICLES = set("啊阿啦了喔哦噢齁欸诶呢嘛么吧呀哇耶咧呗吗嗯呃唉哎嘿哈啰咯")
STYLE = set("他她它的得地像象在再那哪做作着著")
_INITIALS = ("zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l", "g", "k", "h", "j", "q", "x", "z", "c", "s",
             "r", "y", "w")
_FUZZY = {"zh": "z", "ch": "c", "sh": "s", "n": "l"}     # 台灣口音常混用的聲母、韻母當成一樣


def _sound(syllable):
    initial = next((i for i in _INITIALS if syllable.startswith(i)), "")
    final = syllable[len(initial):]
    for a, b in (("ang", "an"), ("eng", "en"), ("ing", "in")):
        if final.endswith(a):
            final = final[:-len(a)] + b
            break
    return _FUZZY.get(initial, initial), final


def _sounds(ch):
    from pypinyin import Style, pinyin

    return {_sound(s) for s in pinyin(ch, style=Style.NORMAL, heteronym=True)[0]}


def sounds_alike(old, new):
    """換的字是不是同音或近音:每個字同音(不看聲調、破音字的每種讀音都算),最多一個字只有聲母一樣。
    實驗:不同音的候選(「有人坐→你們說」「是阿→知道」)幾乎都是改錯。"""
    if len(old) != len(new) or set(old + new) & (PARTICLES | STYLE):
        return False
    near = 0
    for a, b in zip(old, new):
        if a == b:
            continue
        sa, sb = _sounds(a), _sounds(b)
        if sa & sb:
            continue
        if any(x[0] and x[0] == y[0] for x in sa for y in sb):
            near += 1
            continue
        return False
    return near <= 1


def _plain(text):
    """打分用:轉簡體、拿掉標點和空白。"""
    return _DROP.sub("", transcribe.convert_script(text, "cn"))


class Scorer:
    """Qwen3-ASR 聽一段聲音,替候選文字打分數;同一段聲音的相同開頭只問一次。"""

    def __init__(self, server):
        self.server = server
        self.audio = None
        self.cache = {}

    def _post(self, path, body):
        request = urllib.request.Request(f"http://127.0.0.1:{self.server.port}{path}", json.dumps(body).encode(),
                                         {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)

    def listen(self, pcm):
        self.audio = base64.b64encode(asr._wav(pcm)).decode()
        self.cache = {}

    def _next(self, prefix):
        """回答已經寫到 prefix,下一個 token 前幾名的 {bytes: logprob}。"""
        if prefix not in self.cache:
            messages = [{"role": "user", "content": [{"type": "input_audio", "input_audio": {
                "data": self.audio, "format": "wav"}}]},
                {"role": "assistant", "content": "language Chinese<asr_text>" + prefix}]
            answer = self._post("/v1/chat/completions", {"messages": messages, "max_tokens": 1, "temperature": 0,
                                                         "logprobs": True, "top_logprobs": TOP})
            content = (answer["choices"][0].get("logprobs") or {}).get("content") or []
            tops = content[0]["top_logprobs"] if content else []
            self.cache[prefix] = {bytes(t["bytes"]): t["logprob"] for t in tops if t.get("bytes") is not None}
        return self.cache[prefix]

    def score(self, text):
        """每個 token 的 logprob 加總(越接近 0 越像聲音裡說的)。只比字數一樣的句子,所以用總和
        (平均會被前後文的長度稀釋)。中文字被拆成好幾個 token 時只算第一個。"""
        tokens = self._post("/tokenize", {"content": text, "with_pieces": True})["tokens"]
        done, total, count = b"", 0.0, 0
        for token in tokens:
            piece = token["piece"]
            raw = piece.encode("utf-8") if isinstance(piece, str) else bytes(piece)
            try:
                prefix = done.decode("utf-8")
            except UnicodeDecodeError:
                prefix = None                    # 停在一個字的中間:沒辦法當成回答的開頭,這個 token 不算
            if prefix is not None:
                tops = self._next(prefix)
                floor = min(tops.values(), default=-20.0) - 1.0
                total += tops.get(raw, floor)
                count += 1
            done += raw
        return total


def candidates(original, heard):
    """初版和 Qwen3-ASR 的結果逐字對照(都是打分用的簡體、沒標點),回傳每一處「字數一樣、換了幾個字、
    發音相近」:[(開始位置, 結束位置, 換成的字)]。"""
    out = []
    matcher = difflib.SequenceMatcher(None, original, heard, autojunk=False)
    for kind, a1, a2, b1, b2 in matcher.get_opcodes():
        if kind == "replace" and a2 - a1 == b2 - b1 <= LONGEST_SPAN:
            span = heard[b1:b2]
            if not re.search(r"[A-Za-z0-9]", original[a1:a2] + span) and sounds_alike(original[a1:a2], span):
                out.append((a1, a2, span))     # 英文、數字不在這次範圍
    return out


def _apply(text, plain_start, plain_end, replacement, script):
    """把打分用字串(拿掉標點)裡的位置換回原本的句子:跳過標點和空白,換上轉成輸出用字的新字。"""
    index, positions = 0, []
    for position, ch in enumerate(text):
        if not _DROP.fullmatch(ch):
            positions.append(position)
            index += 1
    if plain_end > len(positions):
        return text
    chars = list(text)
    replacement = transcribe.convert_script(replacement, script) if script != "none" else replacement
    for offset, position in enumerate(positions[plain_start:plain_end]):
        chars[position] = replacement[offset]
    return "".join(chars)


def _window(cues, index):
    """這條字幕連同前後各一條(中間停頓不長、合起來不超過 WINDOW 秒):Qwen3-ASR 聽得到前後文,開頭的字才不會聽錯。
    回傳 (要聽的字幕條, 這條是第幾個)。"""
    chosen, own = [cues[index]], 0
    for step in (-1, 1):
        other = index + step
        if not 0 <= other < len(cues):
            continue
        near = cues[other]
        gap = cues[index]["start"] - near["end"] if step < 0 else near["start"] - cues[index]["end"]
        span = max(c["end"] for c in chosen + [near]) - min(c["start"] for c in chosen + [near])
        if gap <= JOIN_GAP and span <= WINDOW:
            if step < 0:
                chosen.insert(0, near)
                own = 1
            else:
                chosen.append(near)
    return chosen, own


def refine(cues, audio, rate=16000, script="tw", server=None, progress=None, cancel=None, log=None):
    """cues:初版字幕條;audio:整個檔案 16 位元單聲道 PCM。回傳修好的字幕條(新的 dict,時間不動)。
    log(字幕條, 原本, 換成, 原本分數, 換掉後分數, 有沒有換) 給實驗記錄每一處。"""
    own_server = server is None
    if own_server:
        server = asr.QwenServer()
        server.start(cancel=cancel)
    scorer = Scorer(server)
    out = []
    try:
        for index, cue in enumerate(cues):
            if cancel is not None and cancel.is_set():
                from core import deps
                raise deps.Cancelled()
            if progress:
                progress(index / max(1, len(cues)))
            text = cue["text"]
            plain = _plain(text)
            # 轉簡體後字數變了(少數詞會這樣):位置對不回原本的句子,不修
            if len(plain) < 2 or len(plain) != len(_DROP.sub("", text)):
                out.append(dict(cue))
                continue
            group, own = _window(cues, index)
            texts = [_plain(c["text"]) for c in group]
            offset = sum(len(x) for x in texts[:own])
            whole = "".join(texts)
            first, last = cues.index(group[0]), cues.index(group[-1])
            before = cues[first - 1]["end"] if first else 0.0
            after = cues[last + 1]["start"] if last + 1 < len(cues) else group[-1]["end"] + PAD
            start = max(group[0]["start"] - PAD, before)
            end = min(group[-1]["end"] + PAD, max(after, group[-1]["end"]))
            pcm = audio[int(start * rate) * 2:int(end * rate) * 2]
            heard, _, _ = server.transcribe(pcm, "zh")
            # 只修這一條裡面、Whisper 沒把握的字(前後兩條是給模型聽前後文用的,它們輪到自己時再修)
            sure = cue.get("sure")
            if sure is not None and len(sure) != len(plain):
                out.append(dict(cue))
                continue
            spots = [(a1 - offset, a2 - offset, span) for a1, a2, span in candidates(whole, _plain(heard))
                     if offset <= a1 and a2 <= offset + len(plain)
                     and (sure is None or min(sure[a1 - offset:a2 - offset]) < SURE)]
            if not spots:
                out.append(dict(cue))
                continue
            scorer.listen(pcm)
            base = scorer.score(whole)
            new_text = text
            # 從後面換回來,前面的位置才不會跑掉;每一處各自和原本比
            for a1, a2, span in sorted(spots, reverse=True):
                trial = whole[:offset + a1] + span + whole[offset + a2:]
                score = scorer.score(trial)
                accepted = score - base > MARGIN
                if log:
                    log(cue, plain[a1:a2], span, base, score, accepted, min(sure[a1:a2]) if sure else None)
                if accepted:
                    new_text = _apply(new_text, a1, a2, span, script)
            out.append(dict(cue, text=new_text))
    finally:
        if own_server:
            server.stop()
    return out


def refine_file(script="tw"):
    """給 transcribe.transcribe_cues 的 refine:讀暫存的聲音檔,載入 Qwen3-ASR 精修。"""

    def run(cues, wav, report, cancel):
        report(None, "精修：載入模型")
        with wave.open(str(wav), "rb") as source:
            rate = source.getframerate()
            audio = source.readframes(source.getnframes())
        return refine(cues, audio, rate, script, progress=lambda r: report(r, "精修"), cancel=cancel)

    return run
