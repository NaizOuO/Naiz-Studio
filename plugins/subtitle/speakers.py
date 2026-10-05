"""即時字幕的「判斷誰說話」:每句定稿時算那一句的聲音特徵,判斷是第幾個人。顏色只是區分,不標「說話者 1」;
使用者可以在字幕紀錄裡改某個人的顏色。兩種做法(見 speaker_profiles.py):

- Clusterer(自動分群,預設):每多一句就把目前所有句子重新分群,不用設門檻、自己判斷有幾個人;
  最近 RECOLOR 秒內的句子顏色可以跟著修正,更早的固定不動。實測會議錄音、Discord 多人通話都最好或接近最好,
  常剛好分出正確的人數。
- Tracker(門檻比對):每個人一個平均聲音特徵,比門檻像就是同一人;可以「扣掉共同音色」。
"""

import math
from collections import Counter
from operator import mul

import numpy as np

# 預設的顏色(依序給新出現的人):(名稱, RGB)。名稱用在純文字紀錄「(藍色)」
COLORS = [("藍色", (88, 150, 255)), ("橘色", (255, 150, 70)), ("綠色", (90, 200, 120)),
          ("粉紅色", (240, 110, 170)), ("紫色", (165, 125, 245)), ("黃色", (235, 200, 60)),
          ("青色", (70, 200, 210)), ("紅色", (235, 85, 85))]
SAME = 0.46             # 和某個人的聲音特徵相似度高於這個:算同一個人。AMI 四人會議在乾淨、通話(壓成 Opus)、
                        # 配樂三種音質校準:乾淨準確 98.7%/同一人集中 93.4%、通話 98.7%/92.1%(配樂下怎麼調都只有約五成)
MIN_SECONDS = 1.0       # 這麼短的句子聲音特徵不可靠:跟著最像的人,但不拿來修正那個人的特徵
WARM = 5                # 扣掉共同音色:前幾句還算不出可靠的全部平均,先用一般比對
CENTERED_SAME = 0.05    # 扣掉共同音色時的同一人門檻(實測 Discord 多人通話的建議值)
LONGEST = 8.0           # 很長的句子只取前面這麼多秒算(夠判斷了,也不會算太久)
MAX_SPEAKERS = len(COLORS)
# 一句裡換人(快速對話中間沒停頓,被當成一句):每 HOP 秒取 WINDOW 秒算一次聲音特徵,找出換人的地方切開
WINDOW, HOP = 1.5, 0.75
SPLIT = 0.46            # 前後兩邊的平均聲音特徵相似度低於這個:算換人(同上校準:乾淨抓到 65%、不會切錯;
                        # 通話抓到 69%、同一人被切開 6%;再高抓得多但切錯的也變多)
SIDE = 2                # 切開的兩邊至少要有幾段(2 段約 2.25 秒)
# 自動分群
RECOLOR = 10.0          # 重新分群後,最近這麼多秒內的句子顏色可以修正(更早的固定,畫面不會一直變)
SINGLE = 0.03           # 分群的輪廓係數低於這個:分不出來,當成同一個人(只有一個人在講)
MERGE = 0.80            # 兩群的中心比這個像就合併(同一個人被硬拆開:單人解說兩群中心 0.96、
                        # 真的不同人實測 0.53～0.74)
MOST = 6                # 最多分幾群
HISTORY = 300           # 最多拿最近幾句來分群(太多會算太久)
FIRST_SAME = 0.6        # 句子還少(不到 4 句)不能分群時,先用門檻比對
MAX_CUTS = 3            # 一句最多切幾刀


def _dot(a, b):
    return sum(map(mul, a, b))


def _normalize(vector):
    norm = math.sqrt(_dot(vector, vector)) or 1.0
    return [x / norm for x in vector]


class Tracker:
    """認得的人:每個人一個平均聲音特徵。assign() 回傳這句是第幾個人(0 起算),判斷不了時 None。
    centered=True 是「扣掉共同音色」:比對前把新的一句和每個人的平均都減掉「目前聽到的全部平均」
    (Discord 這類通話的壓縮音色、同一支麥克風的聲音大家都有,扣掉後才比得出差別);
    前 WARM 句還算不出可靠的全部平均,先用一般比對(門檻 SAME)。"""

    def __init__(self, same=SAME, centered=False):
        self.same = same
        self.centered = centered
        self.centers = []
        self.counts = []
        self._total = None              # 聽到的全部聲音特徵加總(扣掉共同音色用)
        self._seen = 0

    def assign(self, vector, seconds):
        if vector is None:
            return None
        self._total = list(vector) if self._total is None else [a + b for a, b in zip(self._total, vector)]
        self._seen += 1
        centered = self.centered and self._seen > WARM
        same = self.same if (centered or not self.centered) else SAME
        if self.centers:
            view = self._view if centered else (lambda v: v)
            probe = view(vector)
            scores = [_dot(probe, view(center)) for center in self.centers]
            best = max(range(len(scores)), key=scores.__getitem__)
            if scores[best] >= same or len(self.centers) >= MAX_SPEAKERS or seconds < MIN_SECONDS:
                if seconds >= MIN_SECONDS:
                    self._update(best, vector)
                return best
        elif seconds < MIN_SECONDS:
            return None             # 第一句就很短:先不認人,免得用不可靠的特徵當成某個人的代表
        self.centers.append(list(vector))
        self.counts.append(1)
        return len(self.centers) - 1

    def _view(self, vector):
        mean = [t / self._seen for t in self._total]
        return _normalize([v - m for v, m in zip(vector, mean)])

    def _update(self, index, vector):
        # 平均特徵:越多句越穩定,但最多用最近 20 句的份量(同一個人聲音也會隨情緒改變)
        weight = min(self.counts[index], 20)
        center = [(c * weight + v) / (weight + 1) for c, v in zip(self.centers[index], vector)]
        self.centers[index] = _normalize(center)
        self.counts[index] += 1


class Clusterer:
    """自動分群:add() 加一句的聲音特徵,重新分群後回傳這句是第幾個人,和要改顏色的舊句子 [(句子, 新的編號)]。
    編號照上一次的分群對應(重疊最多的配對),顏色才不會每句亂換。"""

    def __init__(self):
        self.vectors, self.items, self.labels = [], [], []      # items:每句的 (句子, 結束秒數)

    def add(self, vector, seconds, item=None, end=0.0):
        if vector is None:
            return None, []
        vector = np.asarray(vector, dtype=np.float32)
        if seconds < MIN_SECONDS:
            # 很短的句子聲音特徵不可靠:跟著最像的那群,不拿來分群
            if not self.vectors:
                return None, []
            sims = np.array(self.vectors) @ vector
            return self.labels[int(np.argmax(sims))], []
        self.vectors.append(vector)
        self.items.append((item, end))
        self.labels.append(None)
        if len(self.vectors) > HISTORY:
            self.vectors, self.items, self.labels = self.vectors[-HISTORY:], self.items[-HISTORY:], self.labels[-HISTORY:]
        fresh = self._cluster(np.array(self.vectors))
        mapped = self._stable(fresh)
        changed = []
        for index, (old, new) in enumerate(zip(self.labels[:-1], mapped[:-1])):
            item, when = self.items[index]
            if old != new and end - when <= RECOLOR:
                self.labels[index] = new
                changed.append((item, new))
        self.labels[-1] = mapped[-1]
        return mapped[-1], changed

    def _cluster(self, x):
        n = len(x)
        if n < 4:
            # 還不能分群:和前面每句比,夠像就同一人(門檻比對)
            labels = []
            for index in range(n):
                sims = [float(x[index] @ x[j]) for j in range(index)]
                best = int(np.argmax(sims)) if sims else -1
                labels.append(labels[best] if sims and sims[best] >= FIRST_SAME else (max(labels) + 1 if labels else 0))
            return labels
        sim = x @ x.T
        best = None
        for k in range(2, min(MOST, n - 1) + 1):
            labels = max((_kmeans(x, k, seed) for seed in range(3)), key=lambda r: r[1])[0]
            score = _silhouette(sim, labels, k)
            if best is None or score > best[0] + 0.02:
                best = (score, labels)
        if best[0] < SINGLE:
            return [0] * n
        return _merge_close(x, [int(v) for v in best[1]])

    def _stable(self, fresh):
        """新的分群編號對到上一次的編號(重疊最多的優先),對不到的給新編號。"""
        previous = self.labels
        pairs = Counter((new, old) for new, old in zip(fresh, previous) if old is not None)
        mapping, used = {}, set()
        for (new, old), _ in pairs.most_common():
            if new not in mapping and old not in used:
                mapping[new] = old
                used.add(old)
        next_id = max([old for old in previous if old is not None] + [-1]) + 1
        result = []
        for new in fresh:
            if new not in mapping:
                mapping[new] = next_id
                next_id += 1
            result.append(min(mapping[new], MAX_SPEAKERS - 1))
        return result


def _merge_close(x, labels):
    """中心很像(MERGE 以上)的兩群合併成一群,直到沒有可以合併的;編號重新從 0 排。"""
    labels = list(labels)
    while True:
        groups = sorted(set(labels))
        centers = {g: x[[i for i, label in enumerate(labels) if label == g]].sum(0) for g in groups}
        centers = {g: c / (np.linalg.norm(c) or 1.0) for g, c in centers.items()}
        pairs = [(float(centers[a] @ centers[b]), a, b) for i, a in enumerate(groups) for b in groups[i + 1:]]
        if not pairs or max(pairs)[0] < MERGE:
            break
        _, keep, drop = max(pairs)
        labels = [keep if label == drop else label for label in labels]
    order = {g: i for i, g in enumerate(dict.fromkeys(labels))}
    return [order[label] for label in labels]


def _kmeans(x, k, seed):
    """以方向相似度分 k 群(k-means++ 起點);回傳 (每句的群, 分數)。"""
    rng = np.random.default_rng(seed)
    centers = x[[rng.integers(len(x))]]
    while len(centers) < k:
        far = np.clip(1 - (x @ centers.T).max(1), 0, None)
        total = far.sum()
        centers = np.vstack([centers, x[rng.choice(len(x), p=far / total if total else None)]])
    labels = None
    for _ in range(30):
        new = (x @ centers.T).argmax(1)
        if labels is not None and (new == labels).all():
            break
        labels = new
        for j in range(k):
            members = x[labels == j]
            if len(members):
                center = members.sum(0)
                centers[j] = center / (np.linalg.norm(center) or 1.0)
    return labels, float((x * centers[labels]).sum())


def _silhouette(sim, labels, k):
    """輪廓係數:越接近 1 代表每群內部越像、群與群之間越不像。"""
    distance = 1 - sim
    scores = []
    for i in range(len(labels)):
        own = labels == labels[i]
        own[i] = False
        others = [distance[i, labels == j].mean() for j in range(k) if j != labels[i] and (labels == j).any()]
        if not own.any() or not others:
            scores.append(0.0)
            continue
        a, b = distance[i, own].mean(), min(others)
        scores.append((b - a) / max(a, b))
    return float(np.mean(scores))


def _mean(vectors):
    return _normalize([sum(column) for column in zip(*vectors)])


def change_points(vectors, split, side=SIDE, most=MAX_CUTS):
    """一句裡有沒有換人:vectors 是依時間排的小段聲音特徵(每段 WINDOW 秒、間隔 HOP 秒)。
    找「前面幾段的平均」和「後面幾段的平均」最不像的地方,相似度低於 split 就在那裡切開,兩邊再各自找(最多切 most 刀)。
    兩邊都至少要有 side 段(太短的插話不切,聲音特徵不可靠)。回傳要切在第幾段之前(由小到大)。"""
    usable = [v for v in vectors if v is not None]
    if len(usable) != len(vectors):
        return []

    def search(a, b, budget):
        if budget <= 0 or b - a < side * 2:
            return []
        best, where = 2.0, None
        for k in range(a + side, b - side + 1):
            score = _dot(_mean(vectors[a:k]), _mean(vectors[k:b]))
            if score < best:
                best, where = score, k
        if where is None or best >= split:
            return []
        left = search(a, where, budget - 1)
        return left + [where] + search(where, b, budget - 1 - len(left))

    return search(0, len(vectors), most)


def color_name(rgb):
    """最接近的預設顏色名稱(使用者自己挑的顏色也找得到一個叫法)。"""
    rgb = tuple(rgb)
    return min(COLORS, key=lambda item: sum((a - b) ** 2 for a, b in zip(item[1], rgb)))[0]
