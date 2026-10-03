"""設定檔資料夾:即時字幕的專有名詞、逐字稿的修正錯字(之後還有敏感詞過濾)的每個設定檔各存成一個 .json,
放在 setting 資料夾裡各功能自己的子資料夾,使用者可以直接打開資料夾複製給別人(匯出),
或把別人給的 .json 放進來(匯入,下次打開編輯視窗或重新開啟程式時讀到)。

檔案內容:{"名稱": "死亡筆記本", "說明": "...", "內容": [...]}。檔名只是名稱換掉不能當檔名的字。
"""

import json
import re
from pathlib import Path

from . import files, paths

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ProfileStore:
    def __init__(self, folder_name, note, clean_item):
        """folder_name:子資料夾名稱;note:寫進檔案的說明;clean_item(dict) 回傳整理好的一項或 None(不要)。"""
        self.folder_name = folder_name
        self.note = note
        self.clean_item = clean_item
        self._files = {}            # 名稱 → 讀進來時的檔案(別人給的檔名和名稱不同時,存回同一個檔)

    @property
    def folder(self):
        return Path(paths.SETTING_DIR) / self.folder_name

    def ensure_folder(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        return self.folder

    def load(self):
        """{名稱: [項目]};讀不懂的檔案略過(不會刪掉)。"""
        profiles = {}
        if not self.folder.is_dir():
            return profiles
        for path in sorted(self.folder.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            name = str(data.get("名稱") or path.stem).strip()
            items = data.get("內容") if isinstance(data.get("內容"), list) else []
            if name and name not in profiles:
                profiles[name] = [item for item in (self.clean_item(i) for i in items if isinstance(i, dict)) if item]
                self._files[name] = path.name
        return profiles

    def save(self, profiles, removed=()):
        """每個設定檔寫成一個檔;removed 是使用者在視窗裡刪掉的設定檔,把它們的檔案刪掉。
        (不會刪其他檔案:程式開著時才放進資料夾、還沒讀進來的設定檔不能被當成刪掉)"""
        folder = self.ensure_folder()
        for name, items in profiles.items():
            path = folder / self._files[name] if name in self._files else self._free_path(folder, name)
            self._files[name] = path.name
            data = {"名稱": name, "說明": self.note, "內容": items}
            # 先寫暫存檔再換掉:寫到一半程式被關掉,原本的設定檔也不會壞掉
            files.write_bytes(path, (json.dumps(data, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
        for name in removed:
            if name in profiles:
                continue
            filename = self._files.pop(name, None)      # 只刪讀進來或寫過的那個檔,不用猜的檔名
            if filename:
                (folder / filename).unlink(missing_ok=True)

    def _free_path(self, folder, name):
        """新設定檔的檔名;已經有同名的檔案(例如內容讀不懂、沒載入的)就加編號,不會蓋掉它。"""
        stem = self.file_stem(name)
        path, number = folder / f"{stem}.json", 2
        while path.exists():
            path, number = folder / f"{stem} ({number}).json", number + 1
        return path

    @staticmethod
    def file_stem(name):
        return _BAD.sub("_", name).strip(" .") or "未命名"
