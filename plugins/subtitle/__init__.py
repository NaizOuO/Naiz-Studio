from core.plugins import Tool


class SubtitleTool(Tool):
    id = "subtitle"
    name = "即時字幕"
    category = "影音"
    description = "影片、遊戲、通話的聲音即時轉成字幕並翻譯"
    accent = (126, 170, 246)

    def create_page(self, app):
        # 畫面程式等到打開工具才載入(字幕視窗程式也會載入這個套件,保持輕量)
        from .page import SubtitlePage

        return SubtitlePage(app, self)


TOOLS = [SubtitleTool]
