"""테스트·예제용 최소 XLSX 작성기 (표준 라이브러리만).

openpyxl 없이 숨김 시트·숨김 열/행·셀 주석·docProps·정의된 이름을 갖춘
XLSX 를 만들 수 있어야, 파서가 그것들을 잡는지 실제로 검증할 수 있습니다.
"""

from __future__ import annotations

import datetime as _dt
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

_EXCEL_EPOCH = _dt.datetime(1899, 12, 30)


def to_serial(value: _dt.datetime) -> float:
    """datetime 을 엑셀 날짜 일련번호로 바꿉니다."""
    delta = value - _EXCEL_EPOCH
    return delta.days + delta.seconds / 86400.0


def col_letters(index: int) -> str:
    letters = ""
    index += 1
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


@dataclass
class Sheet:
    """시트 하나."""

    name: str
    rows: List[List[object]]
    hidden: bool = False
    hidden_columns: Sequence[int] = field(default_factory=tuple)
    hidden_rows: Sequence[int] = field(default_factory=tuple)  # 1-기반 엑셀 행 번호
    date_columns: Sequence[int] = field(default_factory=tuple)
    comments: Sequence[Tuple[str, str, str]] = field(default_factory=tuple)  # (ref, author, text)
    use_inline_strings: bool = False


def build_xlsx(
    path: Path,
    sheets: Sequence[Sheet],
    creator: str = "",
    last_modified_by: str = "",
    company: str = "",
    defined_names: Optional[Dict[str, str]] = None,
) -> Path:
    """XLSX 파일을 만듭니다."""
    defined_names = defined_names or {}
    shared: List[str] = []
    shared_index: Dict[str, int] = {}

    def share(text: str) -> int:
        if text not in shared_index:
            shared_index[text] = len(shared)
            shared.append(text)
        return shared_index[text]

    sheet_xml: List[str] = []
    for sheet in sheets:
        sheet_xml.append(_sheet_xml(sheet, share))

    parts: Dict[str, str] = {}
    parts["[Content_Types].xml"] = _content_types(len(sheets), any(s.comments for s in sheets))
    parts["_rels/.rels"] = _root_rels()
    parts["docProps/core.xml"] = _core_props(creator, last_modified_by)
    parts["docProps/app.xml"] = _app_props(company)
    parts["xl/workbook.xml"] = _workbook_xml(sheets, defined_names)
    parts["xl/_rels/workbook.xml.rels"] = _workbook_rels(len(sheets))
    parts["xl/styles.xml"] = _styles_xml()
    parts["xl/sharedStrings.xml"] = _shared_strings_xml(shared)
    for i, xml in enumerate(sheet_xml, start=1):
        parts[f"xl/worksheets/sheet{i}.xml"] = xml
    comment_no = 0
    for i, sheet in enumerate(sheets, start=1):
        if not sheet.comments:
            continue
        comment_no += 1
        parts[f"xl/worksheets/_rels/sheet{i}.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" '
            f'Target="../comments{comment_no}.xml"/></Relationships>'
        )
        parts[f"xl/comments{comment_no}.xml"] = _comments_xml(sheet.comments)

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in parts.items():
            zf.writestr(name, content)
    return path


def _content_types(n_sheets: int, has_comments: bool) -> str:
    base = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
    )
    for i in range(1, n_sheets + 1):
        base += (
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    if has_comments:
        base += (
            '<Override PartName="/xl/comments1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml"/>'
        )
    return base + "</Types>"


def _root_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        "</Relationships>"
    )


def _core_props(creator: str, last_modified_by: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:creator>{escape(creator)}</dc:creator>"
        f"<cp:lastModifiedBy>{escape(last_modified_by)}</cp:lastModifiedBy>"
        "</cp:coreProperties>"
    )


def _app_props(company: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        f"<Company>{escape(company)}</Company>"
        "</Properties>"
    )


def _workbook_xml(sheets: Sequence[Sheet], defined_names: Dict[str, str]) -> str:
    out = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>',
    ]
    for i, sheet in enumerate(sheets, start=1):
        state = ' state="hidden"' if sheet.hidden else ""
        out.append(f'<sheet name="{escape(sheet.name)}" sheetId="{i}"{state} r:id="rId{i}"/>')
    out.append("</sheets>")
    if defined_names:
        out.append("<definedNames>")
        for name, ref in defined_names.items():
            out.append(f'<definedName name="{escape(name)}">{escape(ref)}</definedName>')
        out.append("</definedNames>")
    out.append("</workbook>")
    return "".join(out)


def _workbook_rels(n_sheets: int) -> str:
    out = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ]
    for i in range(1, n_sheets + 1):
        out.append(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
        )
    out.append(
        f'<Relationship Id="rId{n_sheets + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
        f'<Relationship Id="rId{n_sheets + 2}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    )
    out.append("</Relationships>")
    return "".join(out)


def _styles_xml() -> str:
    # xf 0 = 일반, xf 1 = 날짜(numFmtId 14), xf 2 = 날짜+시각(사용자 서식)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="1"><numFmt numFmtId="165" formatCode="yyyy-mm-dd hh:mm"/></numFmts>'
        '<cellXfs count="3">'
        '<xf numFmtId="0"/><xf numFmtId="14" applyNumberFormat="1"/><xf numFmtId="165" applyNumberFormat="1"/>'
        "</cellXfs></styleSheet>"
    )


def _shared_strings_xml(shared: Sequence[str]) -> str:
    out = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(shared)}" uniqueCount="{len(shared)}">',
    ]
    for text in shared:
        out.append(f"<si><t xml:space=\"preserve\">{escape(text)}</t></si>")
    out.append("</sst>")
    return "".join(out)


def _sheet_xml(sheet: Sheet, share) -> str:
    out = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
    ]
    if sheet.hidden_columns:
        out.append("<cols>")
        for index in sheet.hidden_columns:
            out.append(f'<col min="{index + 1}" max="{index + 1}" hidden="1" width="9"/>')
        out.append("</cols>")
    out.append("<sheetData>")
    for r, row in enumerate(sheet.rows, start=1):
        hidden = ' hidden="1"' if r in sheet.hidden_rows else ""
        out.append(f'<row r="{r}"{hidden}>')
        for c, value in enumerate(row):
            ref = f"{col_letters(c)}{r}"
            out.append(_cell_xml(ref, value, c in sheet.date_columns and r > 1, sheet.use_inline_strings, share))
        out.append("</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


def _cell_xml(ref: str, value, is_date: bool, inline: bool, share) -> str:
    if value is None or value == "":
        return f'<c r="{ref}"/>'
    if isinstance(value, _dt.datetime):
        style = 2 if (value.hour or value.minute) else 1
        return f'<c r="{ref}" s="{style}"><v>{to_serial(value):.10f}</v></c>'
    if isinstance(value, _dt.date):
        return f'<c r="{ref}" s="1"><v>{to_serial(_dt.datetime(value.year, value.month, value.day)):.0f}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        style = ' s="1"' if is_date else ""
        return f'<c r="{ref}"{style}><v>{value}</v></c>'
    text = str(value)
    if inline:
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'
    return f'<c r="{ref}" t="s"><v>{share(text)}</v></c>'


def _comments_xml(comments: Sequence[Tuple[str, str, str]]) -> str:
    authors: List[str] = []
    for _, author, _text in comments:
        if author not in authors:
            authors.append(author)
    out = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<comments xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><authors>',
    ]
    for author in authors:
        out.append(f"<author>{escape(author)}</author>")
    out.append("</authors><commentList>")
    for ref, author, text in comments:
        out.append(
            f'<comment ref="{escape(ref)}" authorId="{authors.index(author)}">'
            f"<text><r><t xml:space=\"preserve\">{escape(text)}</t></r></text></comment>"
        )
    out.append("</commentList></comments>")
    return "".join(out)
