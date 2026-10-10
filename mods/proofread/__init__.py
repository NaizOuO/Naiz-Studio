from core import deps
from core.plugins import Tool

from .page import ProofreadPage


class ProofreadTool(Tool):
    id = "proofread"
    name = "字幕校對"
    min_app = "1.20.0"
    version = "1.0.1"
    author = "Naiz"
    description = "邊看影片邊校對逐字稿：改字、對時間、分說話者，輸出 SRT、DaVinci 每人一軌、YouTube 彩色字幕"
    accent = (130, 200, 255)
    requires = (deps.FFMPEG,)

    def create_page(self, app):
        return ProofreadPage(app, self)


TOOLS = [ProofreadTool]
