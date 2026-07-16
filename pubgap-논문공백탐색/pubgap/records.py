"""PubMed efetch(XML) → Article 레코드 파싱.

의존성 없이 표준 라이브러리(xml.etree)만 사용한다. efetch 결과의
필드 위치가 논문마다 조금씩 다르므로(발행연도가 PubDate/ArticleDate/MedlineDate
어디에 있는지 등) 최대한 관대하게 파싱한다.
"""

from __future__ import annotations

import gzip
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import List, Optional, Union
import xml.etree.ElementTree as ET

_YEAR_RE = re.compile(r"(19|20)\d{2}")
_ENTITY_DECL_RE = re.compile(r"<!ENTITY", re.IGNORECASE)
# RIS 레코드의 필수 시작 태그. NBIB 와 구분하는 유일하게 신뢰할 만한 신호.
_RIS_TY_RE = re.compile(r"^TY\s{2}-", re.MULTILINE)
# RIS 필드 줄: 2자 태그 + 공백2 + '-' (값은 비어 있을 수 있음).
_RIS_FIELD_RE = re.compile(r"^([A-Z][A-Z0-9])\s{2}-\s?(.*)$")


@dataclass
class Article:
    """PubMed 논문 한 편의 최소 표현."""

    pmid: str
    year: Optional[int]
    journal: str
    title: str
    mesh: List[str] = field(default_factory=list)
    mesh_major: List[str] = field(default_factory=list)  # 대표(별표) 주제만
    keywords: List[str] = field(default_factory=list)     # 저자 키워드(MeSH 보조)
    pub_types: List[str] = field(default_factory=list)    # PublicationType(연구 설계)

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


def _extract_mesh(art: ET.Element):
    """(모든 descriptor, 대표(major) descriptor) 두 리스트를 반환.

    MeSH heading 은 descriptor 나 qualifier 중 하나라도 MajorTopicYN='Y' 이면
    그 논문의 '대표 주제'로 본다(PubMed 의 별표 표기와 동일).
    """
    terms: List[str] = []
    major: List[str] = []
    for mh in art.findall(".//MeshHeadingList/MeshHeading"):
        d = mh.find("DescriptorName")
        txt = _text(d)
        if not txt or txt in terms:
            continue
        terms.append(txt)
        is_major = (d is not None and d.get("MajorTopicYN") == "Y") or any(
            q.get("MajorTopicYN") == "Y" for q in mh.findall("QualifierName")
        )
        if is_major:
            major.append(txt)
    return terms, major


def _extract_keywords(art: ET.Element) -> List[str]:
    kws: List[str] = []
    for kw in art.findall(".//KeywordList/Keyword"):
        txt = _text(kw)
        if txt and txt not in kws:
            kws.append(txt)
    return kws


def _extract_pub_types(art: ET.Element) -> List[str]:
    """PublicationType(연구 설계) 목록. PubMed 는 논문마다 여러 개를 단다.

    예: ['Journal Article', 'Randomized Controlled Trial', 'Multicenter Study'].
    임상 연구자에게 '이 주제에 RCT 가 있는가'는 MeSH 만큼 중요한 정보다.
    """
    pts: List[str] = []
    for pt in art.findall(".//PublicationTypeList/PublicationType"):
        txt = _text(pt)
        if txt and txt not in pts:
            pts.append(txt)
    return pts


def parse_efetch_xml(xml_text: str) -> List[Article]:
    """efetch(db=pubmed, retmode=xml) 결과 문자열 → Article 리스트.

    빈 문자열/깨진 XML이면 ValueError를 던진다.
    """
    if not xml_text or not xml_text.strip():
        raise ValueError("빈 XML 입력입니다.")
    # 방어: 내부 엔티티 선언(<!ENTITY ...>) 을 막아 'billion laughs' 확장 DoS 를 차단.
    # 정상 PubMed efetch XML 은 외부 DTD 만 참조하고 내부 <!ENTITY> 를 쓰지 않으므로
    # 실제 입력에는 영향이 없다.
    if _ENTITY_DECL_RE.search(xml_text):
        raise ValueError("안전하지 않은 XML: 내부 엔티티 선언(<!ENTITY>)은 허용되지 않습니다.")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"XML 파싱 실패: {exc}") from exc

    articles: List[Article] = []
    for art in root.iter("PubmedArticle"):
        pmid_node = art.find(".//MedlineCitation/PMID")
        pmid = _text(pmid_node) or "?"
        title = _text(art.find(".//ArticleTitle")) or "(no title)"
        mesh, major = _extract_mesh(art)
        articles.append(
            Article(
                pmid=pmid,
                year=_extract_year(art),
                journal=_extract_journal(art),
                title=title,
                mesh=mesh,
                mesh_major=major,
                keywords=_extract_keywords(art),
                pub_types=_extract_pub_types(art),
            )
        )
    return articles


# --------------------------------------------------------------------------- #
# MEDLINE / NBIB (PubMed "Save → PubMed format", 인용 관리자 내보내기) 파서
# --------------------------------------------------------------------------- #
def parse_medline_nbib(text: str) -> List[Article]:
    """MEDLINE/NBIB 텍스트 → Article 리스트.

    PubMed 웹의 'Save → Format: PubMed' 및 대부분의 인용 관리자가 내보내는
    포맷이다. 한 논문은 빈 줄로 구분되며, 각 줄은 `TAG - value`(4자 태그 + '-').
    앞 6칸 공백으로 시작하는 줄은 직전 필드의 이어짐이다.

    쓰는 태그: PMID, DP/DEP(연도), TA/JT(저널), TI(제목), MH(MeSH; '*' 는 대표),
    OT(저자 키워드), PT(연구 설계/publication type).
    """
    if not text or not text.strip():
        raise ValueError("빈 MEDLINE/NBIB 입력입니다.")

    articles: List[Article] = []
    records: List[List[tuple]] = []  # 각 레코드: [(tag, value), ...]
    current: List[list] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        if not line.strip():
            if current:
                records.append(current)
                current = []
            continue
        if len(line) >= 6 and line[:4].strip() and line[4] == "-" and line[5] in " \t":
            # 표준은 'TAG - value'(공백)지만, 탭으로 구분된 변종도 관대하게 받는다.
            tag = line[:4].strip()
            val = line[6:].strip()
            current.append([tag, val])
        elif line.startswith("      ") and current:  # 6칸 이어짐
            current[-1][1] += " " + line.strip()
        # 그 외(형식 밖) 줄은 무시
    if current:
        records.append(current)

    for rec in records:
        fields: dict = {}
        mh_list: List[str] = []
        ot_list: List[str] = []
        pt_list: List[str] = []
        for tag, val in rec:
            if tag == "MH":
                mh_list.append(val)
            elif tag == "OT":
                ot_list.append(val)
            elif tag == "PT":  # 반복 태그 — 한 논문에 설계가 여러 개 붙는다
                pt_list.append(val)
            else:
                fields.setdefault(tag, val)  # 첫 값 사용

        if not rec:
            continue

        pmid = fields.get("PMID", "?").strip() or "?"
        title = fields.get("TI") or "(no title)"
        journal = fields.get("TA") or fields.get("JT") or "(unknown journal)"
        year = _nbib_year(fields)
        mesh, major = _nbib_mesh(mh_list)
        keywords: List[str] = []
        for kw in ot_list:
            k = kw.strip()
            if k and k not in keywords:
                keywords.append(k)
        pub_types: List[str] = []
        for pt in pt_list:
            p = pt.strip()
            if p and p not in pub_types:
                pub_types.append(p)

        articles.append(
            Article(
                pmid=pmid,
                year=year,
                journal=journal,
                title=title,
                mesh=mesh,
                mesh_major=major,
                keywords=keywords,
                pub_types=pub_types,
            )
        )
    return articles


def _nbib_year(fields: dict) -> Optional[int]:
    for tag in ("DP", "DEP", "EDAT", "MHDA"):
        v = fields.get(tag)
        if v:
            m = _YEAR_RE.search(v)
            if m:
                return int(m.group(0))
    return None


def _nbib_mesh(mh_values: List[str]):
    """MEDLINE MH 값들 → (descriptor 리스트, 대표 descriptor 리스트).

    'Heart Rate/*physiology' → descriptor 'Heart Rate', qualifier physiology 가
    대표('*'). '*Sleep' → descriptor 자체가 대표. descriptor 는 첫 '/' 앞부분.
    """
    terms: List[str] = []
    major: List[str] = []
    for raw in mh_values:
        val = raw.strip()
        if not val:
            continue
        is_major = "*" in val
        descriptor = val.split("/", 1)[0]
        descriptor = descriptor.lstrip("*").strip()
        if not descriptor or descriptor in terms:
            # 이미 있으면 대표 여부만 승격
            if descriptor and is_major and descriptor not in major:
                major.append(descriptor)
            continue
        terms.append(descriptor)
        if is_major:
            major.append(descriptor)
    return terms, major


# --------------------------------------------------------------------------- #
# 견고한 파일 로딩 — gzip / 인코딩 / 포맷 자동 감지 / PMID 중복 제거
# --------------------------------------------------------------------------- #
def decode_bytes(raw: bytes) -> str:
    """바이트 → 텍스트. gzip 자동 해제(이중 압축 포함), UTF-8→latin-1 순으로 디코드."""
    layers = 0
    while raw[:2] == b"\x1f\x8b" and layers < 4:  # gzip 매직 바이트(이중 압축 방어)
        raw = gzip.decompress(raw)
        layers += 1
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def detect_format(text: str) -> str:
    """'xml' | 'ris' | 'nbib' 로 포맷을 추정한다(내용 기반).

    RIS 와 NBIB 는 둘 다 `TAG - value` 꼴이라 태그 모양만으로는 못 가른다
    (예: `TI  - ...` 는 양쪽 모두 유효). 판별은 각 포맷의 **고유 필수 태그**로 한다:
    RIS 레코드는 반드시 `TY  - ` 로 시작하고 `ER  -` 로 끝난다.
    """
    s = text.lstrip("﻿ \t\r\n")
    if s.startswith("<"):
        return "xml"
    if _RIS_TY_RE.search(text):
        return "ris"
    return "nbib"


def parse_records(text: str) -> List[Article]:
    """텍스트 내용으로 XML/RIS/NBIB 를 자동 판별해 파싱한다."""
    if not text or not text.strip():
        raise ValueError("빈 입력입니다.")
    fmt = detect_format(text)
    if fmt == "xml":
        return parse_efetch_xml(text)
    if fmt == "ris":
        return parse_ris(text)
    return parse_medline_nbib(text)


def dedup_articles(articles: List[Article]) -> List[Article]:
    """PMID 기준 중복 제거(첫 등장 유지). '?'(미상) PMID 는 각각 고유로 둔다."""
    seen = set()
    out: List[Article] = []
    for a in articles:
        key = a.pmid
        if key and key != "?":
            if key in seen:
                continue
            seen.add(key)
        out.append(a)
    return out


def load_articles(path: Union[str, Path]) -> List[Article]:
    """파일 경로 → Article 리스트.

    - .gz 자동 해제, UTF-8/latin-1 관대 디코드.
    - XML(efetch) / NBIB(MEDLINE) 자동 판별.
    - PMID 중복 제거.
    빈/깨진 입력이면 ValueError(호출부에서 사용자 메시지로 변환).
    """
    raw = Path(path).read_bytes()
    text = decode_bytes(raw)
    return dedup_articles(parse_records(text))


# --------------------------------------------------------------------------- #
# 분석 전 변환(옵션) — 대표주제 한정 / 키워드 보강 / 연도 필터
# --------------------------------------------------------------------------- #
def apply_major_only(articles: List[Article]) -> List[Article]:
    """각 논문의 분석용 주제(mesh)를 '대표(major) 주제'로 교체한 새 리스트."""
    return [replace(a, mesh=list(a.mesh_major)) for a in articles]


def apply_include_keywords(articles: List[Article]) -> List[Article]:
    """저자 키워드를 분석용 주제(mesh)에 합친 새 리스트.

    MeSH 가 아직 안 붙은 최신 논문에서 주제 신호를 살리기 위한 보완.

    핵심: 다운스트림 집계는 주제를 **정확한 문자열**로 센다. 저자 키워드는 MeSH 와
    대소문자가 다르기 쉬워(예: 키워드 'sleep' vs MeSH 'Sleep'), 그대로 합치면 같은
    개념이 두 주제로 쪼개져 top_mesh·gap 통계가 오염된다. 그래서 **코퍼스 전체에서
    소문자 기준 표준형(canonical surface form)** 을 먼저 만든 뒤 병합한다:
      1) 모든 논문의 MeSH 를 우선 등록(대소문자 무시 → 대표 표기는 MeSH 표기).
      2) 키워드는 같은 소문자 키가 이미 있으면 그 표기로, 없으면 첫 등장 키워드 표기로
         통일 → 'sleep'/'Sleep' 키워드가 하나로 합쳐지고, MeSH 'Sleep' 과도 합쳐진다.
    """
    canon: dict = {}
    for a in articles:  # 1) MeSH 우선
        for m in a.mesh:
            canon.setdefault(m.lower(), m)
    for a in articles:  # 2) 키워드
        for k in a.keywords:
            kl = k.lower()
            if kl:
                canon.setdefault(kl, k)

    out: List[Article] = []
    for a in articles:
        merged = list(a.mesh)
        seen = {m.lower() for m in a.mesh}
        for k in a.keywords:
            kl = k.lower()
            if kl and kl not in seen:
                merged.append(canon[kl])  # 표준형으로 병합
                seen.add(kl)
        out.append(replace(a, mesh=merged))
    return out


def filter_years(
    articles: List[Article],
    min_year: Optional[int] = None,
    max_year: Optional[int] = None,
) -> List[Article]:
    """연도 범위로 필터. 경계가 하나라도 있으면 '연도 미상' 논문은 제외한다."""
    if min_year is None and max_year is None:
        return list(articles)
    out: List[Article] = []
    for a in articles:
        if a.year is None:
            continue
        if min_year is not None and a.year < min_year:
            continue
        if max_year is not None and a.year > max_year:
            continue
        out.append(a)
    return out
