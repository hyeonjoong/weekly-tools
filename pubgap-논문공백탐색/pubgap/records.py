"""PubMed efetch(XML) → Article 레코드 파싱.

의존성 없이 표준 라이브러리(xml.etree)만 사용한다. efetch 결과의
필드 위치가 논문마다 조금씩 다르므로(발행연도가 PubDate/ArticleDate/MedlineDate
어디에 있는지 등) 최대한 관대하게 파싱한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional
import xml.etree.ElementTree as ET

_YEAR_RE = re.compile(r"(19|20)\d{2}")


@dataclass
class Article:
    """PubMed 논문 한 편의 최소 표현."""

    pmid: str
    year: Optional[int]
    journal: str
    title: str
    mesh: List[str] = field(default_factory=list)

    def has(self, term: str) -> bool:
        return term in self.mesh


def _text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    # itertext(): <i>, <sub> 등 인라인 태그가 섞여도 전체 텍스트를 모은다.
    return "".join(el.itertext()).strip()


def _extract_year(art: ET.Element) -> Optional[int]:
    """발행연도를 여러 후보 위치에서 관대하게 뽑는다."""
    candidates = [
        ".//Journal/JournalIssue/PubDate/Year",
        ".//ArticleDate/Year",
        ".//PubDate/Year",
        ".//PubMedPubDate[@PubStatus='pubmed']/Year",
    ]
    for path in candidates:
        node = art.find(path)
        if node is not None and node.text and node.text.strip().isdigit():
            return int(node.text.strip())
    # MedlineDate 예: "2019 Jan-Feb" → 앞의 4자리 연도
    md = art.find(".//PubDate/MedlineDate")
    if md is not None and md.text:
        m = _YEAR_RE.search(md.text)
        if m:
            return int(m.group(0))
    return None


def _extract_journal(art: ET.Element) -> str:
    for path in (".//Journal/ISOAbbreviation", ".//Journal/Title", ".//MedlineJournalInfo/MedlineTA"):
        node = art.find(path)
        txt = _text(node)
        if txt:
            return txt
    return "(unknown journal)"


def _extract_mesh(art: ET.Element) -> List[str]:
    terms: List[str] = []
    for mh in art.findall(".//MeshHeadingList/MeshHeading/DescriptorName"):
        txt = _text(mh)
        if txt and txt not in terms:
            terms.append(txt)
    return terms


def parse_efetch_xml(xml_text: str) -> List[Article]:
    """efetch(db=pubmed, retmode=xml) 결과 문자열 → Article 리스트.

    빈 문자열/깨진 XML이면 ValueError를 던진다.
    """
    if not xml_text or not xml_text.strip():
        raise ValueError("빈 XML 입력입니다.")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:  # pragma: no cover - 방어적
        raise ValueError(f"XML 파싱 실패: {exc}") from exc

    articles: List[Article] = []
    for art in root.iter("PubmedArticle"):
        pmid_node = art.find(".//MedlineCitation/PMID")
        pmid = _text(pmid_node) or "?"
        title = _text(art.find(".//ArticleTitle")) or "(no title)"
        articles.append(
            Article(
                pmid=pmid,
                year=_extract_year(art),
                journal=_extract_journal(art),
                title=title,
                mesh=_extract_mesh(art),
            )
        )
    return articles
