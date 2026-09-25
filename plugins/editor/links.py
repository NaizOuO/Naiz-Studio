"""超連結:在頁面上拉出一塊範圍,點下去開網頁或跳到某一頁。

連結的目標記在 Annot.text:網址直接記(https://…、mailto:…);跳頁記成「#page:頁面 uid」,
頁面換了順序連結仍然跟著那一頁,存檔時才換成那一頁在新檔裡的位置。
"""

import re

PAGE_PREFIX = "#page:"
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def parse(text, pages):
    """使用者輸入的網址或頁碼 → 要記下來的目標;看不懂時回傳 None。pages 是目前的頁面清單。"""
    text = text.strip()
    if not text:
        return None
    number = text.removeprefix("第").removesuffix("頁").strip()
    if number.isdigit():
        index = int(number) - 1
        return f"{PAGE_PREFIX}{pages[index].uid}" if 0 <= index < len(pages) else None
    if " " in text:
        return None
    if _SCHEME.match(text):
        return text
    if "@" in text and "." in text.split("@")[-1]:
        return f"mailto:{text}"
    if "." in text:
        return f"https://{text}"
    return None


def page_uid(target):
    """跳頁連結的頁面 uid;網址連結回傳 None。"""
    if target.startswith(PAGE_PREFIX):
        try:
            return int(target[len(PAGE_PREFIX):])
        except ValueError:
            return None
    return None


def describe(target, pages):
    """顯示在設定列、對話框裡的文字:網址本身,或「第 N 頁」。"""
    uid = page_uid(target)
    if uid is None:
        return target
    index = next((i for i, page in enumerate(pages) if page.uid == uid), None)
    return f"第 {index + 1} 頁" if index is not None else "(頁面已刪除)"


def editable_text(target, pages):
    """修改連結時輸入框裡預先放的文字。"""
    uid = page_uid(target)
    if uid is None:
        return target
    index = next((i for i, page in enumerate(pages) if page.uid == uid), None)
    return str(index + 1) if index is not None else ""
