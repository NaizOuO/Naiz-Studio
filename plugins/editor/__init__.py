from core.plugins import Tool

from .page import EditorPage


class EditorTool(Tool):
    id = "editor"
    name = "PDF 編輯器"
    category = "文件"
    description = "頁面排序、旋轉、刪除、插入"
    accent = (240, 128, 110)

    def create_page(self, app):
        return EditorPage(app, self)


TOOLS = [EditorTool]
