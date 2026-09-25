"""主程式的版本;每次發布前更新。擴充模組用它判斷主程式夠不夠新。"""

VERSION = "1.14.4"


def parse(text):
    """「1.14.4」「v1.14」換成可以比較大小的 (1, 14, 4);看不懂的回傳 (0,)。"""
    try:
        return tuple(int(part) for part in str(text).lstrip("vV").split("."))
    except ValueError:
        return (0,)


def at_least(required):
    return not required or parse(VERSION) >= parse(required)
