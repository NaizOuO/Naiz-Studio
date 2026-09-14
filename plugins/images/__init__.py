from core.plugins import Tool

from .page import ImagePage


class ImageTool(Tool):
    id = "images"
    name = "圖片工具"
    category = "影像"
    description = "轉換格式、壓縮圖片、合成 PDF，支援 HEIC 和 SVG"
    accent = (122, 162, 255)

    def create_page(self, app):
        return ImagePage(app, self)


TOOLS = [ImageTool]
