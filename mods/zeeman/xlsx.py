"""寫出 Excel 檔(.xlsx),不需要另外安裝套件:xlsx 就是一個 zip,裡面放幾個 XML。
只支援這個模組要用的:好幾個工作表、文字和數字、公式(Formula)、粗體列、欄寬。"""

import zipfile
from xml.sax.saxutils import escape


class Formula:
    """Excel 公式(不含開頭的 =);value 是先算好的結果,打開時 Excel 會重算。"""

    def __init__(self, text, value=None):
        self.text, self.value = text, value


def column(index):
    return _column(index)


def _column(index):
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _cell(ref, value, bold):
    style = ' s="1"' if bold else ""
    if isinstance(value, Formula):
        cached = f"<v>{value.value}</v>" if isinstance(value.value, (int, float)) and value.value == value.value else ""
        return f'<c r="{ref}"{style}><f>{escape(value.text)}</f>{cached}</c>'
    if isinstance(value, Bold):
        return _cell(ref, value.value, True)
    if isinstance(value, bool):
        value = str(value)
    if isinstance(value, (int, float)) and value == value:             # value == value:不是 NaN
        return f'<c r="{ref}"{style}><v>{value}</v></c>'
    text = escape(str(value)) if value is not None else ""
    if text == "":
        return ""
    return f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{text}</t></is></c>'


class Bold:
    """這一格用粗體(例如表中間的小標題)。"""

    def __init__(self, value):
        self.value = value


def _sheet(rows):
    widths = {}
    lines = []
    for r, row in enumerate(rows):
        cells = []
        for c, value in enumerate(row):
            shown = value.value if isinstance(value, (Formula, Bold)) else value
            text = "" if shown is None else (f"{shown:.6g}" if isinstance(shown, float) else str(shown))
            widths[c] = max(widths.get(c, 6), min(40, sum(2 if ord(ch) > 0x2E7F else 1 for ch in text) + 2))
            cell = _cell(f"{_column(c)}{r + 1}", value, r == 0)
            if cell:
                cells.append(cell)
        lines.append(f'<row r="{r + 1}">{"".join(cells)}</row>')
    cols = "".join(f'<col min="{c + 1}" max="{c + 1}" width="{w}" customWidth="1"/>' for c, w in sorted(widths.items()))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
            'state="frozen"/></sheetView></sheetViews>'
            f'{"<cols>" + cols + "</cols>" if cols else ""}<sheetData>{"".join(lines)}</sheetData></worksheet>')


def write(path, sheets):
    """sheets:[(工作表名稱, [[第一列(標題)], [資料列]...])];數字存成數字(Excel 可以直接算、畫圖)。"""
    names = [escape(name[:31]) for name, _ in sheets]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                    '<Default Extension="xml" ContentType="application/xml"/>'
                    '<Override PartName="/xl/workbook.xml" '
                    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                    '<Override PartName="/xl/styles.xml" '
                    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                    + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
                              'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                              for i in range(len(sheets))) + '</Types>')
        zf.writestr("_rels/.rels",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                    'Target="xl/workbook.xml"/></Relationships>')
        zf.writestr("xl/workbook.xml",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                    + "".join(f'<sheet name="{name}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                              for i, name in enumerate(names)) + '</sheets><calcPr fullCalcOnLoad="1"/></workbook>')
        zf.writestr("xl/_rels/workbook.xml.rels",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    + "".join(f'<Relationship Id="rId{i + 1}" '
                              'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                              f'Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(sheets)))
                    + f'<Relationship Id="rId{len(sheets) + 1}" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
                    'Target="styles.xml"/></Relationships>')
        zf.writestr("xl/styles.xml",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                    '<fonts count="2"><font><sz val="11"/><name val="Microsoft JhengHei"/></font>'
                    '<font><b/><sz val="11"/><name val="Microsoft JhengHei"/></font></fonts>'
                    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
                    '<fill><patternFill patternType="gray125"/></fill></fills>'
                    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                    '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
                    '</styleSheet>')
        for i, (_, rows) in enumerate(sheets):
            zf.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet(rows))
