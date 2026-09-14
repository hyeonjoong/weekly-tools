"""Word 없이 `.docx` 골격을 직접 만든다 (합성 예제·테스트용).

``zipfile`` 로 최소 OOXML 을 쓰기 때문에 고아 미디어·깨진 참조·외부 링크
이미지를 **원하는 개수만큼 정확히** 심을 수 있다. 실제 원고를 복사해 오지
않고도 회귀 테스트가 가능해진다.
"""

import hashlib
import zipfile
from typing import Dict, Iterable, List, Optional, Sequence

NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
)

IMAGE_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
HEADER_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"
FOOTNOTES_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes"
COMMENTS_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="png" ContentType="image/png"/>'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)

_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rIdDoc" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    '<Relationship Id="rIdCore" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    "</Relationships>"
)


def fake_png(seed: str, size: int = 4096) -> bytes:
    """결정적인 가짜 PNG 바이트. 내용은 해시용일 뿐 디코딩하지 않는다."""
    body = bytearray(b"\x89PNG\r\n\x1a\n")
    block = hashlib.sha256(seed.encode()).digest()
    while len(body) < size:
        body.extend(block)
        block = hashlib.sha256(block).digest()
    return bytes(body[:size])


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _drawing(rel_id: str) -> str:
    return (
        "<w:r><w:drawing><wp:inline><a:graphic><a:graphicData>"
        "<pic:pic><pic:blipFill>"
        f'<a:blip r:embed="{rel_id}"/>'
        "</pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    )


def _paragraph(text: str = "", rel_id: Optional[str] = None, raw: str = "") -> str:
    inner = ""
    if text:
        inner += f'<w:r><w:t xml:space="preserve">{_escape(text)}</w:t></w:r>'
    if rel_id:
        inner += _drawing(rel_id)
    inner += raw
    return f"<w:p>{inner}</w:p>"


def build_docx(
    path,
    paragraphs: Sequence = (),
    images: Optional[Dict[str, bytes]] = None,
    anchored: Sequence[str] = (),
    header_images: Sequence[str] = (),
    footnote_images: Sequence[str] = (),
    external_images: Sequence[str] = (),
    broken_targets: Sequence[str] = (),
    comments: int = 0,
    insertions: int = 0,
    deletions: int = 0,
    title: str = "",
    creator: str = "",
    last_modified_by: str = "",
    extra_media: Sequence[str] = (),
) -> str:
    """합성 `.docx` 를 만든다.

    ``paragraphs`` 의 각 항목은 문자열이거나 ``(문자열, 이미지이름)`` 튜플.
    이미지 이름이 있으면 그 문단에 그림이 앵커된다.
    """
    images = dict(images or {})
    rel_entries: List[str] = []
    body: List[str] = []
    rel_counter = [100]

    def next_id() -> str:
        rel_counter[0] += 1
        return f"rId{rel_counter[0]}"

    anchored_ids: Dict[str, str] = {}
    for name in anchored:
        rel_id = next_id()
        anchored_ids[name] = rel_id
        rel_entries.append(
            f'<Relationship Id="{rel_id}" Type="{IMAGE_TYPE}" Target="media/{name}"/>'
        )

    for item in paragraphs:
        if isinstance(item, tuple):
            text, image_name = item
            body.append(_paragraph(text, anchored_ids.get(image_name)))
        else:
            body.append(_paragraph(item))
    for name in anchored:
        if not any(isinstance(p, tuple) and p[1] == name for p in paragraphs):
            body.append(_paragraph("", anchored_ids[name]))

    referenced_extra = []
    for target in broken_targets:
        rel_id = next_id()
        rel_entries.append(
            f'<Relationship Id="{rel_id}" Type="{IMAGE_TYPE}" Target="media/{target}"/>'
        )
        referenced_extra.append(_drawing(rel_id))
    for url in external_images:
        rel_id = next_id()
        rel_entries.append(
            f'<Relationship Id="{rel_id}" Type="{IMAGE_TYPE}" '
            f'Target="{_escape(url)}" TargetMode="External"/>'
        )
        referenced_extra.append(_drawing(rel_id))
    if referenced_extra:
        body.append("<w:p>" + "".join(referenced_extra) + "</w:p>")

    for _ in range(insertions):
        body.append('<w:p><w:ins w:id="1" w:author="a"><w:r><w:t>추가</w:t></w:r></w:ins></w:p>')
    for _ in range(deletions):
        body.append('<w:p><w:del w:id="2" w:author="a"><w:r><w:delText>삭제</w:delText></w:r></w:del></w:p>')

    header_xml = footnotes_xml = None
    header_rels: List[str] = []
    footnote_rels: List[str] = []
    if header_images:
        parts = []
        for name in header_images:
            rel_id = next_id()
            header_rels.append(
                f'<Relationship Id="{rel_id}" Type="{IMAGE_TYPE}" Target="media/{name}"/>'
            )
            parts.append(_drawing(rel_id))
        header_xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                      f"<w:hdr {NS}><w:p>" + "".join(parts) + "</w:p></w:hdr>")
        rel_id = next_id()
        rel_entries.append(f'<Relationship Id="{rel_id}" Type="{HEADER_TYPE}" Target="header1.xml"/>')
        body.append(f'<w:p><w:r r:id="{rel_id}"><w:t>머리글</w:t></w:r></w:p>')
    if footnote_images:
        parts = []
        for name in footnote_images:
            rel_id = next_id()
            footnote_rels.append(
                f'<Relationship Id="{rel_id}" Type="{IMAGE_TYPE}" Target="media/{name}"/>'
            )
            parts.append(_drawing(rel_id))
        footnotes_xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                         f"<w:footnotes {NS}><w:footnote><w:p>" + "".join(parts)
                         + "</w:p></w:footnote></w:footnotes>")
        rel_id = next_id()
        rel_entries.append(
            f'<Relationship Id="{rel_id}" Type="{FOOTNOTES_TYPE}" Target="footnotes.xml"/>'
        )
        body.append(f'<w:p><w:r r:id="{rel_id}"><w:t>각주</w:t></w:r></w:p>')

    comments_xml = None
    if comments:
        items = "".join(
            f'<w:comment w:id="{i}" w:author="검토자"><w:p><w:r><w:t>코멘트 {i}</w:t></w:r></w:p></w:comment>'
            for i in range(comments)
        )
        comments_xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                        f"<w:comments {NS}>{items}</w:comments>")
        rel_id = next_id()
        rel_entries.append(
            f'<Relationship Id="{rel_id}" Type="{COMMENTS_TYPE}" Target="comments.xml"/>'
        )

    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f"<w:document {NS}><w:body>" + "".join(body) + "</w:body></w:document>")
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + "".join(rel_entries) + "</Relationships>")

    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties '
            'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:title>{_escape(title)}</dc:title>"
            f"<dc:creator>{_escape(creator)}</dc:creator>"
            f"<cp:lastModifiedBy>{_escape(last_modified_by)}</cp:lastModifiedBy>"
            "</cp:coreProperties>")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _ROOT_RELS)
        zf.writestr("word/document.xml", document)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
        zf.writestr("docProps/core.xml", core)
        if header_xml:
            zf.writestr("word/header1.xml", header_xml)
            zf.writestr("word/_rels/header1.xml.rels",
                        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        + "".join(header_rels) + "</Relationships>")
        if footnotes_xml:
            zf.writestr("word/footnotes.xml", footnotes_xml)
            zf.writestr("word/_rels/footnotes.xml.rels",
                        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        + "".join(footnote_rels) + "</Relationships>")
        if comments_xml:
            zf.writestr("word/comments.xml", comments_xml)
        for name, blob in images.items():
            zf.writestr(f"word/media/{name}", blob)
        for name in extra_media:
            zf.writestr(f"word/media/{name}", images.get(name, fake_png(name)))
    return str(path)


def build_corrupt_docx(path) -> str:
    """zip 으로 열리지 않는 가짜 docx (판정 불가 경로 시연용)."""
    with open(path, "wb") as handle:
        handle.write(b"PK\x03\x04 this is not a real zip archive \x00" * 8)
    return str(path)
