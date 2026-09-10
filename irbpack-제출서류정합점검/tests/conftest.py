"""테스트 공용 도구 — 전부 오프라인. 네트워크·외부 파일에 의존하지 않는다."""

import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from irbpack.docread import read_document                     # noqa: E402
from irbpack.items import extract_all                          # noqa: E402
from irbpack.compare import compare                            # noqa: E402
from irbpack.roles import assign_roles                         # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")

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


def _escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def paragraph_xml(text):
    return '<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % _escape(text)


def table_xml(rows):
    parts = ["<w:tbl>"]
    for row in rows:
        parts.append("<w:tr>")
        for cell in row:
            parts.append("<w:tc>%s</w:tc>" % paragraph_xml(cell))
        parts.append("</w:tr>")
    parts.append("</w:tbl>")
    return "".join(parts)


def make_docx(path, blocks, extra_files=None, body_xml=None):
    """blocks: ('p', 문단) 또는 ('t', [[셀...]]) 목록. body_xml 을 주면 그대로 쓴다."""
    if body_xml is None:
        body_xml = "".join(
            paragraph_xml(payload) if kind == "p" else table_xml(payload)
            for kind, payload in blocks
        )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>%s</w:body></w:document>" % body_xml
    )
    directory = os.path.dirname(str(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("word/document.xml", document)
        for name, payload in (extra_files or {}).items():
            archive.writestr(name, payload)
    return str(path)


def write_md(path, lines):
    directory = os.path.dirname(str(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as handle:
        handle.write("\n\n".join(lines) + "\n")
    return str(path)


def load_packet(directory):
    """폴더 → (문서 목록, mentions, 비교 결과). 역할 자동판별을 그대로 쓴다."""
    paths = sorted(
        os.path.join(str(directory), name)
        for name in os.listdir(str(directory))
        if not name.startswith(".")
    )
    documents = [read_document(path) for path in paths]
    documents, _ = assign_roles(documents)
    mentions = []
    for doc in documents:
        mentions.extend(extract_all(doc))
    return documents, mentions, compare(documents, mentions)


def values_of(mentions, item, facet=None):
    return sorted(set(mention.value for mention in mentions
                      if mention.item == item and (facet is None or mention.facet == facet)))


def finding_items(result):
    return sorted(finding.item for finding in result.findings)


def severity_of(result, item):
    for finding in result.findings:
        if finding.item == item:
            return finding.severity
    return None


# ---------------------------------------------------------------- 표준 합성 패킷

PROTOCOL_LINES = [
    "연구계획서",
    "Version No: 1.2",
    "| 연구제목 | (국문) 가상 수면음향 연구 |",
    "| 연구책임자 | 김하늘 교수 (가상병원 수면의학과) |",
    "| 연구 실시기관 | 가상병원 수면의학과 |",
    "| 연구 대상자 수 | 총 60명 (중재군 30명, 대조군 30명) |",
    "| 검사/방문일정 | 3회 방문 |",
    "대상자는 만 20~65세 성인이다.",
    "각 방문의 검사 소요시간은 약 60~90분이다.",
    "경제적 보상: 방문당 교통비 실비 30,000 원을 지급한다.",
    "연구 관련 기록은 종료 시점부터 3년간 보관 후 파기한다.",
    "연구 관련 문의 전화: 02-1234-5678",
]

CONSENT_LINES = [
    "연구대상자 설명문 및 동의서",
    "Version No: 1.2",
    "연구제목: 가상 수면음향 연구",
    "연구책임자: 김하늘 교수 (가상병원 수면의학과)",
    "귀하는 본 연구에 참여할 것을 권유 받았습니다. 참여는 자발적인 결정입니다.",
    "본 연구에는 만 20~65세 성인 총 60명이 참여합니다(중재군 30명, 대조군 30명).",
    "귀하는 3회 방문하시게 됩니다.",
    "검사 소요시간은 약 60~90분입니다.",
    "연구 참여 보상으로 방문당 교통비 실비 30,000 원이 지급됩니다.",
    "연구 관련 기록은 종료 시점부터 3년간 보관 후 파기됩니다.",
    "| 연구책임자 | 김하늘 교수 (가상병원) 전화: 02-1234-5678 |",
]


@pytest.fixture
def clean_packet(tmp_path):
    """치명 0건이 나와야 하는 최소 패킷 (프로토콜 + 동의서)."""
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2_2026-09-01.md", PROTOCOL_LINES)
    write_md(directory / "ICF_성인용_v1.2_2026-09-01.md", CONSENT_LINES)
    return directory


@pytest.fixture
def examples_dir():
    return EXAMPLES
