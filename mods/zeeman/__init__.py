from core.plugins import Tool

from .page import ZeemanPage


class ZeemanTool(Tool):
    id = "zeeman"
    name = "干涉環分析"
    min_app = "1.18.6"
    version = "1.0.0"
    author = "Naiz"
    description = "Fabry–Perot 干涉環照片：校正比例尺、拉橫切線、自動找峰與中心，輸出各峰位置（mm）"
    accent = (240, 96, 120)

    def create_page(self, app):
        return ZeemanPage(app, self)


TOOLS = [ZeemanTool]
