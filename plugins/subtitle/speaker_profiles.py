"""判斷誰說話的「區分方式」設定檔。預設是「自動分群」(每多一句就把目前所有句子重新分群,不用設門檻,
實測會議錄音、Discord 多人通話都最好或接近最好);也可以改用門檻比對,自己調門檻(例如背景有遊戲聲時)。

每個設定檔存成「setting\\subtitle_speakers」資料夾裡的一個 .json(和專有名詞同一套,見 core/profiles.py),內容是一項:
- method:cluster 自動分群 / plain 一般比對 / centered 扣掉共同音色
- same:同一人門檻(門檻比對才用;比這個像就算同一人,調高會分得比較細)
- split:一句裡換人的門檻(前後兩段比這個不像就切開;調高會切得比較多)
config.json 的 subtitle_speakers 只記目前選哪一個。
"""

from core.profiles import ProfileStore

from . import speakers

KEY = "subtitle_speakers"
GENERAL = "自動分群"
METHODS = [("cluster", "自動分群", "每多一句就把目前所有句子重新分群，自己判斷有幾個人；最近 10 秒內的顏色可能被修正"),
           ("plain", "一般比對", "直接比聲音特徵，比門檻像就是同一人；適合乾淨的錄音、會議"),
           ("centered", "扣掉共同音色", "先扣掉大家共同的部分（通話軟體的壓縮、同一支麥克風）再比門檻")]
METHOD_NAMES = {key: name for key, name, _ in METHODS}
# 預設的設定檔(數值是實測會議錄音、Discord 多人通話後的建議值)
DEFAULTS = {
    GENERAL: {"method": "cluster", "same": speakers.SAME, "split": 0.50},
    "一般比對": {"method": "plain", "same": speakers.SAME, "split": speakers.SPLIT},
    "Discord 通話": {"method": "centered", "same": speakers.CENTERED_SAME, "split": 0.50},
}


def _number(value, default):
    try:
        return round(min(1.0, max(-1.0, float(value))), 2)
    except (TypeError, ValueError):
        return default


def _clean(item):
    method = item.get("method") if item.get("method") in METHOD_NAMES else "plain"
    return {"method": method, "same": _number(item.get("same"), speakers.SAME),
            "split": _number(item.get("split"), speakers.SPLIT)}


STORE = ProfileStore("subtitle_speakers",
                     "即時字幕判斷誰說話的區分方式：method=cluster 自動分群／plain 一般比對／centered 扣掉共同音色、"
                     "same=同一人門檻（門檻比對才用，調高分得比較細）、split=一句裡換人的門檻（調高切得比較多）", _clean)


def load(config, create=False):
    """create:把還沒有檔案的預設設定檔寫進資料夾(按「編輯」時;沒用到這個功能就不建立檔案)。
    「自動分群」一定在(它是預設),而且排第一個。"""
    profiles = {name: items[0] for name, items in STORE.load().items() if items}
    missing = {name: dict(value) for name, value in DEFAULTS.items()
               if name not in profiles and (name == GENERAL or not profiles)}
    profiles = dict(missing, **profiles)
    profiles = dict(sorted(profiles.items(), key=lambda item: item[0] != GENERAL))
    if create and missing:
        try:
            STORE.save({name: [value] for name, value in profiles.items()})
        except OSError:
            pass
    raw = config.get(KEY) if isinstance(config.get(KEY), dict) else {}
    active = str(raw.get("active", GENERAL))
    return {"active": active if active in profiles else GENERAL, "profiles": profiles}


def save(data):
    """存檔:設定檔寫進資料夾;回傳要寫進 config.json 的部分(只有目前選哪一個)。"""
    STORE.save({name: [value] for name, value in data["profiles"].items()}, data.pop("removed", ()))
    return {"說明": "即時字幕判斷誰說話目前用的區分方式；設定檔在「setting\\subtitle_speakers」資料夾",
            "active": data["active"]}


def resolve(data):
    """目前要用的設定檔:(名稱, 內容)。"""
    profiles = data["profiles"]
    if data["active"] in profiles:
        return data["active"], profiles[data["active"]]
    return GENERAL, profiles.get(GENERAL, dict(DEFAULTS[GENERAL]))


def mode(value):
    """給引擎的設定。"""
    return {"method": value["method"], "same": value["same"], "split": value["split"]}
