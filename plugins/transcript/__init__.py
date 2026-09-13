from core.plugins import Tool

from .page import TranscriptPage


class TranscriptTool(Tool):
    id = "transcript"
    name = "錄音轉逐字稿"
    category = "影音"
    description = "把錄音或影片轉成逐字稿,不用上傳"
    accent = (236, 128, 170)

    def create_page(self, app):
        return TranscriptPage(app, self)


TOOLS = [TranscriptTool]
