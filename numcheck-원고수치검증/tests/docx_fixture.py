"""테스트·예제용 최소 .docx 생성기 (표준 라이브러리만).

python-docx 를 쓰지 않는 이유는 하나다 — numcheck 자체가 외부 의존성 0 이므로
테스트도 그래야 한다. 여기서 만드는 파일은 Word 가 실제로 여는 유효한 .docx 다.
"""

from __future__ import annotations

import zipfile
from typing import Iterable, List, Optional, Sequence, Union

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def paragraph(text: str) -> str:
    return f'<w:p><w:r><w:t xml:space="preserve">{_escape(text)}</w:t></w:r></w:p>'


def deleted_paragraph(text: str) -> str:
    """추적 변경으로 '삭제된' 문단 — 최종본에는 없는 글자다."""
    return (f'<w:p><w:del w:id="1" w:author="a"><w:r>'
            f'<w:delText xml:space="preserve">{_escape(text)}</w:delText>'
            f"</w:r></w:del></w:p>")


def table(rows: Sequence[Sequence[str]]) -> str:
    out = ["<w:tbl>"]
    for row in rows:
        out.append("<w:tr>")
        for cell in row:
            out.append(f"<w:tc>{paragraph(cell)}</w:tc>")
        out.append("</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


Block = Union[str, Sequence[Sequence[str]]]


def build_docx(path, blocks: Iterable[Block]) -> str:
    """``blocks`` 의 각 항목이 문자열이면 문단, 리스트면 표로 들어간다.

    이미 ``<w:p>``/``<w:tbl>`` 로 시작하는 문자열은 그대로 쓴다(원시 XML).
    """
    body: List[str] = []
    for block in blocks:
        if isinstance(block, str):
            body.append(block if block.startswith("<w:") else paragraph(block))
        else:
            body.append(table(block))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>' + "".join(body) + "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("word/document.xml", document)
    return str(path)
