from core.plugins import Tool

from .page import PdfPage


class PdfTool(Tool):
    id = "pdf"
    name = "PDF 工具"
    category = "文件"
    description = "壓縮、拆分、合併 PDF"
    accent = (78, 201, 176)

    def create_page(self, app):
        return PdfPage(app, self)


TOOLS = [PdfTool]
