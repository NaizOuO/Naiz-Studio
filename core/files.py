"""輸出檔案共用的小工具:不覆蓋既有檔案的命名,以及先寫暫存檔、完成後才換成正式檔名。"""

import os
from contextlib import contextmanager
from pathlib import Path


def free_path(folder: Path, stem: str, ext: str) -> Path:
    """不覆蓋既有檔案:同名時加上 (2)、(3)。"""
    out, number = folder / f"{stem}{ext}", 2
    while out.exists():
        out = folder / f"{stem} ({number}){ext}"
        number += 1
    return out


def free_names(folder: Path, names) -> list:
    """一次輸出好幾個檔案時(例如拆成 part1、part2),整組加上同一個編號,不會有的有編號、有的沒有。"""
    folder = Path(folder)
    names = list(names)
    number = 1
    while True:
        suffix = "" if number == 1 else f" ({number})"
        paths = [folder / f"{Path(name).stem}{suffix}{Path(name).suffix}" for name in names]
        if not any(path.exists() for path in paths):
            return paths
        number += 1


@contextmanager
def atomic_path(target):
    """給一個暫存檔路徑去寫;區塊正常結束才換成正式檔名,中途失敗、取消或程式被關掉都不會留下寫一半的檔案。"""
    target = Path(target)
    temp = target.with_name(target.name + ".part")
    try:
        yield temp
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def write_bytes(target, data: bytes):
    with atomic_path(target) as temp:
        temp.write_bytes(data)
