"""테스트용 최소 .docx 작성기 — 워드가 실제로 저장하는 모양을 흉내 낸다.

특히 두 가지를 재현해야 revcheck 의 오탐 방어를 제대로 시험할 수 있다.
    1. **런 쪼개짐**: 워드는 한 문장을 ``<w:r>`` 여러 개로 쪼개 저장한다
       (맞춤법 검사·언어 태그·서식 때문에). 인용 대조가 여기서 무너지기 쉽다.
    2. **변경내용 추적**: ``<w:ins>`` / ``<w:del>`` + ``<w:delText>``.

외부 의존성 없이 zipfile 로 직접 만든다(python-docx 를 쓰지 않는다).
"""

from __future__ import annotations

import zipfile
from typing import Iterable, List, Optional, Sequence

W_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
)

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def esc(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _run(text: str, italic: bool = False, tag: str = "w:t") -> str:
    props = "<w:rPr><w:i/></w:rPr>" if italic else ""
    return f'<w:r>{props}<{tag} xml:space="preserve">{esc(text)}</{tag}></w:r>'


def _chunks(text: str, count: int) -> List[str]:
    """텍스트를 count 조각으로 쪼갠다(워드의 런 쪼개짐 흉내)."""
    if count <= 1 or not text:
        return [text]
    size = max(1, len(text) // count)
    out = [text[i : i + size] for i in range(0, len(text), size)]
    return out


def p(
    text: str,
    style: Optional[str] = None,
    italic: bool = False,
    split: int = 1,
) -> str:
    """평범한 문단. ``split`` 을 올리면 런이 그만큼 쪼개진다."""
    ppr = f'<w:pPr><w:pStyle w:val="{esc(style)}"/></w:pPr>' if style else ""
    runs = "".join(_run(chunk, italic) for chunk in _chunks(text, split))
    return f"<w:p>{ppr}{runs}</w:p>"


def p_tracked(
    before: str = "", inserted: str = "", deleted: str = "", after: str = ""
) -> str:
    """변경내용 추적이 켜진 문단: 삽입은 ``w:ins``, 삭제는 ``w:del``+``w:delText``."""
    parts = []
    if before:
        parts.append(_run(before))
    if deleted:
        parts.append(
            f'<w:del w:id="1" w:author="reviewer" w:date="2026-01-01T00:00:00Z">'
            f'<w:r><w:delText xml:space="preserve">{esc(deleted)}</w:delText></w:r>'
            f"</w:del>"
        )
    if inserted:
        parts.append(
            f'<w:ins w:id="2" w:author="author" w:date="2026-01-02T00:00:00Z">'
            f'<w:r><w:t xml:space="preserve">{esc(inserted)}</w:t></w:r>'
            f"</w:ins>"
        )
    if after:
        parts.append(_run(after))
    return "<w:p>" + "".join(parts) + "</w:p>"


def tbl(rows: Sequence[Sequence[str]]) -> str:
    """표. 셀 하나가 문단 하나를 담는다."""
    xml = ["<w:tbl>"]
    for row in rows:
        xml.append("<w:tr>")
        for cell in row:
            xml.append(f"<w:tc>{p(cell)}</w:tc>")
        xml.append("</w:tr>")
    xml.append("</w:tbl>")
    return "".join(xml)


def document_xml(blocks: Iterable[str]) -> str:
    body = "".join(blocks)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {W_NS}><w:body>{body}</w:body></w:document>"
    )


def write_docx(path, blocks: Iterable[str], extra_parts: Optional[dict] = None) -> str:
    """블록 목록을 .docx 파일로 저장하고 경로를 돌려준다."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("word/document.xml", document_xml(blocks))
        for name, data in (extra_parts or {}).items():
            zf.writestr(name, data)
    return str(path)


def simple_docx(path, paragraphs: Sequence[str], split: int = 1) -> str:
    """문자열 목록 → .docx (``## `` 로 시작하면 제목 스타일)."""
    blocks = []
    for text in paragraphs:
        if text.startswith("## "):
            blocks.append(p(text[3:], style="Heading1"))
        else:
            blocks.append(p(text, split=split))
    return write_docx(path, blocks)
