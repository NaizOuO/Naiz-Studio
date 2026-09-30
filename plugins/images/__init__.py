from core.plugins import Tool

from .page import ImagePage


class ImageTool(Tool):
    id = "images"
    name = "圖片工具"
    category = "影像"
    description = "裁切、旋轉、拉正斜拍的照片；轉換格式、壓縮、合成 PDF"
    accent = (122, 162, 255)

    def create_page(self, app):
        return ImagePage(app, self)


TOOLS = [ImageTool]
