from core.plugins import Tool

from . import service


class WebTranslateTool(Tool):
    id = "webtrans"
    name = "網頁翻譯"
    min_app = "1.23.2"
    version = "1.0.0"
    author = "Naiz"
    description = "在瀏覽器選字翻譯並記下來，翻譯在本地處理；這裡看紀錄"
    accent = (110, 190, 160)

    def create_page(self, app):
        from .page import WebTranslatePage

        return WebTranslatePage(app, self)


# Naiz Studio 開著就待命:擴充功能隨時都能翻譯,不用先打開這個畫面
service.start()

TOOLS = [WebTranslateTool]
