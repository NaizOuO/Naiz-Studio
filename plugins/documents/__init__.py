from core.plugins import Tool

from .page import DocumentsPage


class DocumentsTool(Tool):
    id = "documents"
    name = "文件轉檔"
    category = "文件"
    description = "Word、PowerPoint、Excel、PDF 互相轉換"
    accent = (168, 148, 240)

    def create_page(self, app):
        return DocumentsPage(app, self)


TOOLS = [DocumentsTool]
