"""테스트용 최소 docx 작성기 (표준 라이브러리). examples/_make_examples.py 와 같은 구조."""
from __future__ import annotations

import zipfile
from typing import List, Sequence, Union
from xml.sax.saxutils import escape

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
Row = Sequence[str]
Block = Union[str, List[Row]]


def p_xml(text: str, heading: bool = False, ins: str = "", dele: str = "") -> str:
    style = '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if heading else ""
    runs = '<w:r><w:t xml:space="preserve">{}</w:t></w:r>'.format(escape(text))
    if ins:
        runs += '<w:ins w:id="1" w:author="x"><w:r><w:t xml:space="preserve">{}</w:t></w:r></w:ins>'.format(escape(ins))
    if dele:
        runs += '<w:del w:id="2" w:author="x"><w:r><w:delText xml:space="preserve">{}</w:delText></w:r></w:del>'.format(escape(dele))
    return "<w:p>{}{}</w:p>".format(style, runs)


def tbl_xml(rows: List[Row]) -> str:
    out = ["<w:tbl>"]
    for row in rows:
        out.append("<w:tr>")
        for cell in row:
            out.append("<w:tc>{}</w:tc>".format(p_xml(cell)))
        out.append("</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def write_docx(path: str, blocks: Sequence[Block], raw_body: str = "") -> str:
    body = [raw_body] if raw_body else []
    for b in blocks:
        if isinstance(b, str):
            body.append(p_xml(b.lstrip("#").strip(), heading=b.startswith("#")))
        else:
            body.append(tbl_xml(b))
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="{}"><w:body>{}<w:sectPr/></w:body></w:document>').format(W_NS, "".join(body))
    ctypes = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
              '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
              '<Default Extension="xml" ContentType="application/xml"/>'
              '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
              '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ctypes)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", doc)
    return path
