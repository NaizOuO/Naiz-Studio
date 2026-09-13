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

    def handle_event(self, event, mouse_pos):
        pass

    def update(self):
        pass

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


def load_tools(folder, namespace):
    """回傳 (工具清單, [(資料夾名, 錯誤訊息)]);單一插件壞掉不會影響其他插件。"""
    tools, errors = [], []
    folder = Path(folder)
    if not folder.is_dir():
        return tools, errors
    _ensure_namespace(namespace)

    for entry in sorted(folder.iterdir()):
        init = entry / "__init__.py"
        if not entry.is_dir() or not init.is_file():
            continue
        module_name = f"{namespace}.{entry.name}"
        for key in [k for k in sys.modules if k == module_name or k.startswith(module_name + ".")]:
            del sys.modules[key]
        try:
            spec = importlib.util.spec_from_file_location(
                module_name, init, submodule_search_locations=[str(entry)])
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            tools.extend(tool_cls() for tool_cls in getattr(module, "TOOLS", []))
        except Exception:
            sys.modules.pop(module_name, None)
            errors.append((entry.name, traceback.format_exc()))
    return tools, errors
