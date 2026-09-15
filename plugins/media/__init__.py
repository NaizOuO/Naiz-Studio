from core import deps
from core.plugins import Tool

from .page import MediaPage


class MediaTool(Tool):
    id = "media"
    name = "影音轉檔"
    category = "影音"
    description = "影片、音訊轉檔與壓縮，影片轉 GIF"
    accent = (255, 180, 84)
    requires = (deps.FFMPEG,)

    def create_page(self, app):
        return MediaPage(app, self)


TOOLS = [MediaTool]
