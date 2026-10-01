from core.plugins import Tool

from .page import PhotoPage


class PhotoTool(Tool):
    id = "photo"
    name = "圖片工具"
    category = "影像"
    description = "裁切、旋轉、拉正斜拍照片，調整色彩、高清、去背、批次改檔名"
    accent = (104, 196, 170)

    def create_page(self, app):
        return PhotoPage(app, self)


TOOLS = [PhotoTool]
