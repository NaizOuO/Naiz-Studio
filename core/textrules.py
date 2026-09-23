"""判斷段落的共用規則:PDF 編輯器的改字(找段落)和文件轉檔的 PDF 轉 Word 都用這裡。"""

import re

# 項目編號:一、 (一) （一） 1. 1、 (1) ① • - 等開頭的行是新的一段
LIST_START = re.compile(r"^\s*([一二三四五六七八九十百]+[、.．]|[(（][一二三四五六七八九十\d]+[)）]|\d+[.、．)）]\s*\S"
                        r"|[①-⑳]|[•●○◆■▪\-–—]\s)")
# 句子結尾的標點:沒寫滿的行以這些結尾,就是一段的最後一行
ENDINGS = "。！？：；.!?:;」』）)"
CJK = ((0x2E80, 0xA4CF), (0xF900, 0xFAFF), (0xFE30, 0xFE4F), (0xFF00, 0xFF60), (0x20000, 0x3FFFF))


def is_cjk(ch):
    code = ord(ch)
    return any(low <= code <= high for low, high in CJK)


def starts_list(text):
    return bool(LIST_START.match(text))


def column_right(rights):
    """大部分文字的右邊界(取 90% 的位置,少數特別長的不算)。"""
    rights = sorted(rights)
    return rights[int(len(rights) * 0.9)] if rights else 0.0


def column_rights(lines):
    """每一行所在那一欄的右邊界:只看左緣和它差不多的行(分欄、表格旁的文字才不會被別欄影響)。
    lines 是 [(左, 右, 字級)],回傳同樣順序的右邊界。"""
    result = []
    for left, _, size in lines:
        result.append(column_right(r for l, r, _ in lines if abs(l - left) <= size * 3))
    return result


def ends_paragraph(text, right, next_right, column, size):
    """上一行是不是一段的結尾。

    自動換行的段落只有最後一行會沒寫滿,所以:
    - 離右邊界還差四個字以上:作者自己換的行
    - 沒寫滿而且以句號、冒號結尾
    - 明顯比下一行短
    """
    if right < column - size * 4:
        return True
    if right < column - size * 2 and text.rstrip()[-1:] in ENDINGS:
        return True
    return right < next_right - size * 2
