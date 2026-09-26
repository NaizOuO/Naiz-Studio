"""插件機制:掃描資料夾,每個子資料夾的 __init__.py 用 TOOLS 清單提供工具。"""

import importlib.util
import sys
import traceback
import types
from pathlib import Path

from . import theme


class Tool:
    """首頁上的一個工具。插件繼承它並填好欄位。"""

    id = ""
    name = ""
    category = "其他"
    description = ""
    accent = theme.ACCENT
    # 需要額外下載的元件(core.deps.Dependency);缺少時開啟前會先詢問使用者
    requires = ()
    # 擴充模組填寫:需要的主程式版本(例如 "1.14.4")、模組自己的版本與作者,首頁卡片會顯示
    min_app = ""
    version = ""
    author = ""
    folder = ""         # 載入時填上:工具所在的資料夾名稱

    def data_dir(self):
        """存設定、快取的資料夾(mods_data\\工具代號);模組移除後還留著,重新安裝不會遺失。"""
        from . import mods

        return mods.data_dir(self.id)

    def create_page(self, app):
        raise NotImplementedError


class Page:
    """工具打開後的畫面,由主程式轉交事件並指定繪製範圍。"""

    def __init__(self, app, tool):
        self.app = app
        self.tool = tool

    @property
    def screen(self):
        return self.app.screen

    # 頁面自己的彈出視窗(例如編輯規則):開著時主程式會把它畫在最上層,事件也只交給它
    def modal_open(self):
        return False

    def draw_modal(self, mouse_pos):
        pass

    def handle_modal_event(self, event, mouse_pos):
        pass

    def handle_event(self, event, mouse_pos):
        pass

    def update(self):
        pass

    def has_unsaved(self):
        """有沒有還沒儲存的變更;關閉程式前會先詢問。"""
        return False

    def leave(self, proceed):
        """要離開這個畫面(回首頁、關閉程式)時呼叫;需要先詢問時自己跳視窗,確定離開後再呼叫 proceed()。"""
        proceed()

    def deactivate(self):
        """離開這個畫面或被浮動視窗蓋住時呼叫,用來結束進行中的拖曳、自動捲動與文字輸入。"""
        pass

    def draw_toolbar(self, rect, mouse_pos):
        pass

    def draw(self, rect, mouse_pos):
        pass


_extensions = {}


def extend(point, item):
    """往擴充點加入項目。公開程式只知道擴充點存在,不知道會被加入什麼。"""
    _extensions.setdefault(point, []).append(item)


def extensions(point):
    return list(_extensions.get(point, []))


def reset_extensions():
    _extensions.clear()


def _ensure_namespace(namespace):
    if namespace not in sys.modules:
        module = types.ModuleType(namespace)
        module.__path__ = []
        sys.modules[namespace] = module


def load_tools(folder, namespace, only=None):
    """回傳 (工具清單, [(資料夾名, 錯誤訊息)]);單一插件壞掉不會影響其他插件。
    only 是允許載入的資料夾名稱(擴充模組只載入已啟用的);資料夾裡有 lib 的話加進 sys.path,
    模組可以把自己需要的套件放在 lib 裡。"""
    tools, errors = [], []
    folder = Path(folder)
    if not folder.is_dir():
        return tools, errors
    _ensure_namespace(namespace)

    for entry in sorted(folder.iterdir()):
        init = entry / "__init__.py"
        if not entry.is_dir() or not init.is_file() or (only is not None and entry.name not in only):
            continue
        lib = entry / "lib"
        if lib.is_dir() and str(lib) not in sys.path:
            sys.path.append(str(lib))               # 放最後:主程式已經有的套件優先,不會被模組換掉
        module_name = f"{namespace}.{entry.name}"
        for key in [k for k in sys.modules if k == module_name or k.startswith(module_name + ".")]:
            del sys.modules[key]
        try:
            spec = importlib.util.spec_from_file_location(
                module_name, init, submodule_search_locations=[str(entry)])
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            for tool_cls in getattr(module, "TOOLS", []):
                tool = tool_cls()
                tool.folder = entry.name
                tools.append(tool)
        except Exception:
            sys.modules.pop(module_name, None)
            errors.append((entry.name, traceback.format_exc()))
    return tools, errors
