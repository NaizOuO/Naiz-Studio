from core.plugins import Tool

from .page import CircuitPage


class CircuitTool(Tool):
    id = "circuit"
    name = "電路圖"
    min_app = "1.14.4"
    version = "1.0.0"
    author = "Naiz"
    description = "畫類比電路圖，輸出圖片，或產生可以貼進筆記的 Python 程式碼"
    accent = (250, 196, 84)

    def create_page(self, app):
        return CircuitPage(app, self)


TOOLS = [CircuitTool]
