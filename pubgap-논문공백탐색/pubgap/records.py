"""서지 레코드 → Article 파싱 (PubMed XML / MEDLINE·NBIB / RIS / CSV·TSV).

의존성 없이 표준 라이브러리(xml.etree, csv)만 사용한다. 실제 연구자가 손에 쥐는
파일은 형식이 제각각이므로(efetch XML, PubMed 웹의 NBIB, EndNote/Zotero RIS,
Scopus·Web of Science·Covidence 의 CSV) **내용으로 형식을 자동 판별**하고,
각 형식 안에서도 필드 위치·표기 흔들림을 최대한 관대하게 흡수한다.
"""

from __future__ import annotations

import csv
import gzip
import io
import re
import zlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union
import xml.etree.ElementTree as ET

# 연도 추출. 두 가지를 동시에 만족해야 한다:
#  (1) 4자리 연도가 **더 긴 숫자열의 일부이면 안 된다** — 없으면 '1719878400'(unix
#      epoch)에서 1987 을, 'S32019456'(accession)에서 2019 를 뽑아 존재하지 않는
#      발행연도를 만들어 낸다(그 값이 그대로 추세검정·Theil–Sen·구간분할에 들어간다).
#  (2) 그런데 MEDLINE 의 `DEP`(전자출판일)와 일부 RIS 의 `DA` 는 **YYYYMMDD** 8자리다.
#      단순히 경계만 요구하면 이 필드가 통째로 해석 불가가 되어 연도가 유실된다.
# 그래서 '경계 있는 4자리' 또는 '경계 있는 YYYYMMDD' 를 모두 인정하고, 어느 쪽이든
# 앞 4자리를 연도로 쓴다. (YYYYMM 6자리는 accession 과 구분이 안 되므로 받지 않는다.)
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?:(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01]))?(?!\d)")


# 발행연도로 인정할 범위. 데이터에서 온 연도도 반드시 걸러야 한다 — XML 의
# `<Year>99999</Year>`(오타·손상) 하나가 조밀 시계열을 10만 원소로 부풀리고
# Mann–Kendall(O(n²))을 사실상 멈추게 만든다(실측: 20201 → 22초, 99999 → 45초+).
MIN_PLAUSIBLE_YEAR = 1500
MAX_PLAUSIBLE_YEAR = 2200


def plausible_year(value) -> Optional[int]:
    """정수/문자열 → 그럴듯한 발행연도. 범위를 벗어나면 None."""
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if MIN_PLAUSIBLE_YEAR <= year <= MAX_PLAUSIBLE_YEAR else None


def _year_from(text: str):
    """문자열에서 그럴듯한 4자리 발행연도를 뽑는다. 없으면 None."""
    m = _YEAR_RE.search(text or "")
    return plausible_year(m.group(1)) if m else None


_ENTITY_DECL_RE = re.compile(r"<!ENTITY", re.IGNORECASE)
_ENTITY_DECL_BYTES_RE = re.compile(rb"<!ENTITY", re.IGNORECASE)
# RIS 레코드의 필수 시작 태그. NBIB 와 구분하는 유일하게 신뢰할 만한 신호.
# 표준은 'TY  - '(공백2)지만 내보내기 도구에 따라 공백 수가 흔들리므로 관대하게 본다.
_RIS_TY_RE = re.compile(r"^TY\s{1,3}-", re.MULTILINE)
# RIS 필드 줄: 2자 태그 + 공백 + '-' (값은 비어 있을 수 있음).
_RIS_FIELD_RE = re.compile(r"^([A-Z][A-Z0-9])\s{1,3}-\s?(.*)$")
# NBIB(MEDLINE) 필드 줄: 좌측정렬 4칸 태그 + '- '. 형식 판별용(파싱은 아래 전용 루프).
_NBIB_FIELD_RE = re.compile(r"^([A-Z][A-Z0-9]{0,3})\s*-[ \t]", re.MULTILINE)


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


_CONTROL_WS_RE = re.compile(r"[\r\n\t\x0b\x0c]+")


def clean_value(text: str) -> str:
    """서지 값에서 제어 문자를 공백으로 정규화한다.

    주제어에 CR/LF 가 들어오면(따옴표 안에 개행을 담은 CSV 등) Markdown 표가 통째로
    쪼개지고, 검증 URL 에도 %0D 로 실려 나간다. 렌더러마다 이스케이프하는 대신
    **입력 경계에서 한 번** 정리한다 — 제어 문자가 든 주제어는 어떤 소비자에게도
    쓸모가 없다.
    """
    if not text:
        return ""
    return _CONTROL_WS_RE.sub(" ", text).strip()


def _text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    # itertext(): <i>, <sub> 등 인라인 태그가 섞여도 전체 텍스트를 모은다.
    return clean_value("".join(el.itertext()))


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
            year = plausible_year(node.text.strip())
            if year is not None:
                return year
    # MedlineDate 예: "2019 Jan-Feb" → 앞의 4자리 연도
    md = art.find(".//PubDate/MedlineDate")
    if md is not None and md.text:
        return _year_from(md.text)
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
    # 중복 판정은 반드시 set 으로. list 멤버십(`txt not in terms`)은 주제 수에
    # 대해 O(m²) 라, MeSH 를 수천 개 단 레코드에서 파싱만 20초 넘게 걸렸다.
    terms: List[str] = []
    major: List[str] = []
    seen_terms: set = set()
    seen_major: set = set()
    for mh in art.findall(".//MeshHeadingList/MeshHeading"):
        d = mh.find("DescriptorName")
        txt = _text(d)
        if not txt:
            continue
        is_major = (d is not None and d.get("MajorTopicYN") == "Y") or any(
            q.get("MajorTopicYN") == "Y" for q in mh.findall("QualifierName")
        )
        if txt not in seen_terms:
            seen_terms.add(txt)
            terms.append(txt)
        # 같은 descriptor 가 여러 MeshHeading 으로 쪼개져 들어오는 색인이 있다
        # (예: 'Sleep/physiology' 와 '*Sleep'). 뒤늦게 major 신호가 와도 승격한다.
        if is_major and txt not in seen_major:
            seen_major.add(txt)
            major.append(txt)
    return terms, major


def _extract_keywords(art: ET.Element) -> List[str]:
    kws: List[str] = []
    seen: set = set()
    for kw in art.findall(".//KeywordList/Keyword"):
        txt = _text(kw)
        if txt and txt not in seen:
            seen.add(txt)
            kws.append(txt)
    return kws


def _extract_pub_types(art: ET.Element) -> List[str]:
    """PublicationType(연구 설계) 목록. PubMed 는 논문마다 여러 개를 단다.

    예: ['Journal Article', 'Randomized Controlled Trial', 'Multicenter Study'].
    임상 연구자에게 '이 주제에 RCT 가 있는가'는 MeSH 만큼 중요한 정보다.
    """
    pts: List[str] = []
    seen: set = set()
    for pt in art.findall(".//PublicationTypeList/PublicationType"):
        txt = _text(pt)
        if txt and txt not in seen:
            seen.add(txt)
            pts.append(txt)
    return pts


def _strip_namespaces(el: ET.Element) -> None:
    """서브트리의 태그에서 '{ns}' 접두를 제거(제자리 수정).

    아래 추출기들은 전부 `.//MedlineCitation/PMID` 같은 이름 경로를 쓰므로,
    네임스페이스가 붙은 XML 이면 한 필드도 못 찾는다. 레코드 단위로 한 번만 벗긴다.
    """
    for node in el.iter():
        if isinstance(node.tag, str) and "}" in node.tag:
            node.tag = node.tag.rsplit("}", 1)[-1]


def assert_no_internal_entities(payload) -> None:
    """내부 엔티티 선언(<!ENTITY ...>)이 있으면 거부 — 'billion laughs' 확장 DoS 차단.

    정상 PubMed XML 은 외부 DTD 만 참조하고 내부 <!ENTITY> 를 쓰지 않으므로 실제
    입력에는 영향이 없다. 파일 경로와 네트워크 경로가 **같은** 가드를 쓰도록
    여기에 두고 fetch 에서도 호출한다(예전엔 파일 경로에만 있었다).
    """
    if isinstance(payload, (bytes, bytearray)):
        has = _ENTITY_DECL_BYTES_RE.search(payload)
    else:
        has = _ENTITY_DECL_RE.search(payload)
    if has:
        raise ValueError("안전하지 않은 XML: 내부 엔티티 선언(<!ENTITY>)은 허용되지 않습니다.")


def _article_from_element(art: ET.Element) -> Article:
    """<PubmedArticle> 또는 <PubmedBookArticle> 요소 하나 → Article."""
    if isinstance(art.tag, str) and "}" in art.tag:
        _strip_namespaces(art)
    pmid_node = art.find(".//MedlineCitation/PMID")
    if pmid_node is None:  # PubmedBookArticle 은 BookDocument/PMID
        pmid_node = art.find(".//PMID")
    pmid = _text(pmid_node) or "?"
    title = _text(art.find(".//ArticleTitle")) or _text(art.find(".//BookTitle")) or "(no title)"
    mesh, major = _extract_mesh(art)
    return Article(
        pmid=pmid,
        year=_extract_year(art),
        journal=_extract_journal(art),
        title=title,
        mesh=mesh,
        mesh_major=major,
        keywords=_extract_keywords(art),
        pub_types=_extract_pub_types(art),
    )


# efetch XML 을 통째로 DOM 에 올리지 않고 스트리밍으로 훑을 때의 피드 단위(문자).
_XML_FEED_CHUNK = 1 << 18  # 256 KiB
# 레코드 요소 이름 — 논문(PubmedArticle)과 책/챕터(PubmedBookArticle) 둘 다 받는다.
_XML_RECORD_TAGS = ("PubmedArticle", "PubmedBookArticle")


def parse_efetch_xml(xml_text: str) -> List[Article]:
    """efetch(db=pubmed, retmode=xml) 결과 문자열 → Article 리스트.

    **스트리밍 파싱**: `XMLPullParser` 로 조금씩 먹이면서 레코드 하나를 만들 때마다
    그 서브트리를 `clear()` 한다. 수십만 편(수 GB) XML 에서도 DOM 을 통째로 들고 있지
    않아 메모리가 레코드 수에 선형(빈 껍데기 요소만)으로만 늘어난다.

    빈 문자열/깨진 XML이면 ValueError를 던진다. eFetch 가 200 으로 돌려주는
    `<ERROR>` 본문도 ValueError 로 승격해, '결과 0편'과 '조회 실패'를 구분한다.
    """
    if not xml_text or not xml_text.strip():
        raise ValueError("빈 XML 입력입니다.")
    assert_no_internal_entities(xml_text)

    parser = ET.XMLPullParser(events=("end",))
    articles: List[Article] = []
    errors: List[str] = []

    def _local(tag) -> str:
        # '{ns}PubmedArticle' → 'PubmedArticle'. 네임스페이스가 붙은 변형(일부 저장소
        # 재배포본)에서 레코드를 통째로 못 알아보고 0편을 돌려주던 문제를 막는다.
        return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""

    def _drain() -> None:
        for _event, el in parser.read_events():
            if _local(el.tag) in _XML_RECORD_TAGS:
                articles.append(_article_from_element(el))
                el.clear()  # 서브트리 해제 — 빈 껍데기만 부모에 남는다
            elif _local(el.tag) == "ERROR":
                txt = (el.text or "").strip()
                if txt:
                    errors.append(txt)

    try:
        for i in range(0, len(xml_text), _XML_FEED_CHUNK):
            parser.feed(xml_text[i : i + _XML_FEED_CHUNK])
            _drain()
        parser.close()
        _drain()
    except ET.ParseError as exc:
        raise ValueError(f"XML 파싱 실패: {exc}") from exc

    if not articles and errors:
        raise ValueError(f"PubMed XML 오류 응답: {errors[0]}")
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
            val = clean_value(line[6:])
            current.append([tag, val])
        elif line.startswith("      ") and current:  # 6칸 이어짐
            current[-1][1] += " " + line.strip()
        # 그 외(형식 밖) 줄은 무시
    if current:
        records.append(current)

    for rec in records:
        # 내용이 하나도 없는 레코드(예: 'ER  - ' 줄만 있는 파일)는 유령 논문이 되어
        # "분석 논문 1편" 같은 거짓 결과를 만든다.
        if not any(val.strip() for _tag, val in rec):
            continue
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
            year = _year_from(v)
            if year is not None:
                return year
    return None


def _nbib_mesh(mh_values: List[str]):
    """MEDLINE MH 값들 → (descriptor 리스트, 대표 descriptor 리스트).

    'Heart Rate/*physiology' → descriptor 'Heart Rate', qualifier physiology 가
    대표('*'). '*Sleep' → descriptor 자체가 대표. descriptor 는 첫 '/' 앞부분.
    """
    terms: List[str] = []
    major: List[str] = []
    seen_terms: set = set()
    seen_major: set = set()
    for raw in mh_values:
        val = raw.strip()
        if not val:
            continue
        is_major = "*" in val
        descriptor = val.split("/", 1)[0]
        descriptor = descriptor.lstrip("*").strip()
        if not descriptor:
            continue
        if descriptor not in seen_terms:
            seen_terms.add(descriptor)
            terms.append(descriptor)
        if is_major and descriptor not in seen_major:  # 이미 있으면 대표 여부만 승격
            seen_major.add(descriptor)
            major.append(descriptor)
    return terms, major


# --------------------------------------------------------------------------- #
# RIS (EndNote / Zotero / Mendeley / Scopus / Web of Science 내보내기)
# --------------------------------------------------------------------------- #
# RIS 태그 → 우리 필드. 여러 태그가 같은 필드를 노릴 수 있어 **우선순위 순서**로 둔다.
_RIS_TITLE_TAGS = ("TI", "T1", "BT")
_RIS_JOURNAL_TAGS = ("JO", "JF", "T2", "J2", "JA", "SO")
_RIS_YEAR_TAGS = ("PY", "Y1", "DA", "Y2")
_RIS_ID_TAGS = ("AN", "ID", "C7", "U1")
# RIS 'TY' 코드 → PublicationType 유사 문자열(근거 tier 판정에 재사용).
_RIS_TY_TO_PUBTYPE = {
    "JOUR": "Journal Article",
    "EJOUR": "Journal Article",
    "RPRT": "Technical Report",
    "CHAP": "Book Chapter",
    "BOOK": "Book",
    "CONF": "Congress",
    "CPAPER": "Congress",
    "THES": "Academic Dissertation",
}
# PMID/accession 후보: 숫자만. 옛 PMID 는 한 자리('1')도 있어 하한을 두지 않는다.
_DIGITS_RE = re.compile(r"^\d{1,12}$")


def _ris_records(text: str) -> List[List[Tuple[str, str]]]:
    """RIS 텍스트 → 레코드별 (tag, value) 리스트.

    레코드는 `TY  - ` 로 시작해 `ER  -` 로 끝난다. 실제 내보내기 파일은 ER 이
    빠지거나 레코드 사이 빈 줄이 없는 경우가 흔해, **다음 TY 를 만나면 이전 레코드를
    닫는** 방식으로도 복구한다. 태그 없는 이어짐 줄은 직전 필드에 이어 붙인다.
    """
    records: List[List[Tuple[str, str]]] = []
    current: Optional[List[list]] = None
    for raw in text.splitlines():
        line = raw.rstrip()
        m = _RIS_FIELD_RE.match(line)
        if m:
            tag, val = m.group(1), clean_value(m.group(2))
            if tag == "TY":
                if current:
                    records.append([(t, v) for t, v in current])
                current = [["TY", val]]
                continue
            if tag == "ER":
                if current:
                    records.append([(t, v) for t, v in current])
                current = None
                continue
            if current is None:
                continue  # TY 이전의 헤더 잡음은 버린다
            current.append([tag, val])
        elif current and line.strip() and current:
            # 이어짐 줄(들여쓰기 여부 무관) — 직전 필드에 공백으로 이어 붙인다.
            current[-1][1] = (current[-1][1] + " " + line.strip()).strip()
    if current:
        records.append([(t, v) for t, v in current])
    return records


_MESH_MARKER_RE = re.compile(r"^\*|/\*?[a-z][a-z \-]*$")


def _keywords_look_like_mesh(all_kw: Sequence[str]) -> bool:
    """KW 값들이 MeSH 색인 표기(선두 '*' 또는 '/소문자qualifier')를 쓰는지 판단.

    PubMed 가 내보낸 RIS/CSV 는 KW 에 MeSH descriptor 를 그대로 넣어 준다
    (`*Sleep`, `Heart Rate/*physiology`). 반면 저자 키워드는 그런 표기가 없다.
    **레코드 단위가 아니라 코퍼스 단위로** 한 번만 판단해야 한다 — 논문마다 다르게
    해석하면 같은 개념이 어떤 논문에선 주제, 어떤 논문에선 키워드로 갈려 통계가 깨진다.
    """
    if not all_kw:
        return False
    marked = sum(1 for k in all_kw if _MESH_MARKER_RE.search(k))
    return marked / len(all_kw) >= 0.1


def parse_ris(text: str) -> List[Article]:
    """RIS 텍스트 → Article 리스트.

    RIS 에는 MeSH 전용 필드가 없어 **KW(키워드)** 하나에 모든 색인어가 들어온다.
    코퍼스 전체를 보고 KW 가 MeSH 색인 표기(`*Sleep`, `Heart Rate/*physiology`)를
    쓰면 NBIB 와 같은 규칙으로 descriptor/대표주제를 뽑아 `mesh`/`mesh_major` 를
    채우고, 순수 저자 키워드면 `keywords` 에만 담는다(그 경우 `topics_from_keywords()`
    폴백이 주제로 승격한다).
    """
    if not text or not text.strip():
        raise ValueError("빈 RIS 입력입니다.")

    records = _ris_records(text)
    mesh_mode = _keywords_look_like_mesh(
        [v for rec in records for tag, v in rec if tag == "KW" and v]
    )

    articles: List[Article] = []
    for rec in records:
        first: Dict[str, str] = {}
        kw: List[str] = []
        types: List[str] = []
        for tag, val in rec:
            if not val:
                continue
            if tag == "KW":
                kw.append(val)
            elif tag in ("M3", "PT", "DT"):
                types.append(val)
            else:
                first.setdefault(tag, val)

        title = _first_of(first, _RIS_TITLE_TAGS) or "(no title)"
        journal = _first_of(first, _RIS_JOURNAL_TAGS) or "(unknown journal)"
        year = None
        for tag in _RIS_YEAR_TAGS:
            v = first.get(tag)
            if v:
                year = _year_from(v)
                if year is not None:
                    break

        pmid = "?"
        for tag in _RIS_ID_TAGS:
            v = (first.get(tag) or "").strip()
            if _DIGITS_RE.match(v):
                pmid = v
                break
        if pmid == "?" and first.get("DO"):
            pmid = "doi:" + first["DO"].strip()

        if mesh_mode:
            mesh, major = _nbib_mesh(kw)
            keywords = list(mesh)
        else:
            mesh, major = [], []
            keywords = _dedup_keep_order(kw)

        ty = (first.get("TY") or "").strip().upper()
        pub_types = _dedup_keep_order(types)
        mapped = _RIS_TY_TO_PUBTYPE.get(ty)
        if mapped and mapped not in pub_types:
            pub_types.append(mapped)

        articles.append(
            Article(
                pmid=pmid, year=year, journal=journal, title=title,
                mesh=mesh, mesh_major=major, keywords=keywords, pub_types=pub_types,
            )
        )
    return articles


def _first_of(fields: Dict[str, str], tags: Sequence[str]) -> str:
    for t in tags:
        v = (fields.get(t) or "").strip()
        if v:
            return v
    return ""


def _dedup_keep_order(values) -> List[str]:
    out: List[str] = []
    seen = set()
    for v in values:
        s = (v or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


# --------------------------------------------------------------------------- #
# CSV / TSV (Scopus · Web of Science · Covidence · Rayyan · PubMed 웹 CSV …)
# --------------------------------------------------------------------------- #
# 헤더 별칭 → 우리 필드. 비교는 소문자·영숫자만 남긴 정규화 키로 한다
# ('Publication Year' / 'publication_year' / 'Publication-Year' 모두 같은 키).
_CSV_ALIASES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("pmid", ("pmid", "pubmedid", "pubmed", "pmidpubmedid", "medlineid")),
    ("title", ("title", "articletitle", "primarytitle", "documenttitle", "ti")),
    ("journal", ("journal", "journalbook", "sourcetitle", "journaltitle", "source",
                 "publicationtitle", "secondarytitle", "journalname", "publication")),
    ("year", ("year", "publicationyear", "pubyear", "pubdate", "publicationdate",
              "date", "yearpublished", "createdate", "py")),
    ("mesh", ("meshterms", "mesh", "meshheadings", "meshheadinglist", "medicalsubjectheadings")),
    ("keywords", ("keywords", "authorkeywords", "indexkeywords", "keywordsplus",
                  "subjectterms", "descriptors", "tags", "subjects")),
    ("pub_types", ("publicationtype", "publicationtypes", "documenttype", "doctype",
                   "typeofwork", "studydesign", "articletype", "type")),
    ("doi", ("doi", "digitalobjectidentifier")),
)
_CSV_FIELD_BY_KEY: Dict[str, str] = {
    alias: fieldname for fieldname, aliases in _CSV_ALIASES for alias in aliases
}
# 다중값 셀 구분자. 쉼표는 쓰지 않는다 — MeSH descriptor 자체가 쉼표를 포함한다
# (예: 'Sleep Initiation and Maintenance Disorders', 'Aged, 80 and over').
_CSV_MULTI_SPLIT = re.compile(r"\s*[;|]\s*")
_NORM_HEADER_RE = re.compile(r"[^a-z0-9]+")

# 기본 128 KiB 필드 상한은 초록·소속기관이 포함된 실제 내보내기에서 흔히 넘는다.
# 압축폭탄 상한(MAX_DECOMPRESSED_BYTES)이 전체 크기를 이미 막고 있으므로,
# 필드 단위 상한만 현실적인 수준으로 올린다.
csv.field_size_limit(16 * 1024 * 1024)


def _norm_header(name: str) -> str:
    return _NORM_HEADER_RE.sub("", (name or "").strip().lower())


def sniff_delimiter(text: str) -> str:
    """CSV/TSV 구분자 추정. 헤더 줄에서 가장 일관되게 나타나는 후보를 고른다.

    `csv.Sniffer` 는 짧거나 지저분한 파일에서 곧잘 실패하므로, 첫 비어있지 않은
    몇 줄에서 따옴표 밖 구분자 개수를 직접 세어 **줄마다 개수가 일정한** 후보를 우선한다.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()][:20]
    if not lines:
        return ","
    best, best_score = ",", (-1, -1)
    for delim in ("\t", ",", ";", "|"):
        counts = [_count_outside_quotes(ln, delim) for ln in lines]
        if counts[0] < 1:
            continue
        consistent = sum(1 for c in counts if c == counts[0])
        score = (consistent, counts[0])
        if score > best_score:
            best_score = score
            best = delim
    return best


def _count_outside_quotes(line: str, delim: str) -> int:
    n = 0
    in_q = False
    for ch in line:
        if ch == '"':
            in_q = not in_q
        elif ch == delim and not in_q:
            n += 1
    return n


def parse_csv_records(text: str, delimiter: Optional[str] = None) -> List[Article]:
    """CSV/TSV 서지 내보내기 → Article 리스트.

    실제 연구자가 받는 CSV 는 지저분하다. 다음을 모두 흡수한다:
      - 도구별 헤더 이름 차이(Scopus 'Source title' / WoS 'Publication Year' /
        Covidence 'Journal' …) → 정규화 별칭 매핑.
      - 구분자 자동 추정(`,` `\\t` `;` `|`), 따옴표 안의 구분자·개행.
      - 헤더 앞 안내문 줄(Scopus 등) 건너뛰기, 중복 헤더, 열 수가 들쭉날쭉한 행.
      - `'2019.0'`, `'2019년 3월'`, `'Mar-2019'` 같은 연도 표기 → 4자리 연도 추출.
      - 완전히 빈 행/전부 공백인 셀 무시.
    인식 가능한 열이 하나도 없으면 ValueError.
    """
    if not text or not text.strip():
        raise ValueError("빈 CSV 입력입니다.")

    delim = delimiter or sniff_delimiter(text)
    # skipinitialspace: '", "Review"' 처럼 구분자 뒤에 공백이 붙은(엑셀·수기 편집에서
    # 흔한) 파일에서도 따옴표가 제대로 벗겨지도록 한다.
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim, skipinitialspace=True)
    try:
        rows = [r for r in reader if any((c or "").strip() for c in r)]
    except csv.Error as exc:
        # 기본 필드 상한(128 KiB)은 초록·소속기관이 들어간 Scopus/WoS 내보내기에서
        # 곧잘 넘는다. 원시 영문 오류 대신 한국어 안내로 바꾼다.
        raise ValueError(f"CSV 를 읽지 못했습니다 — {exc}") from exc
    if not rows:
        raise ValueError("CSV 에 데이터 행이 없습니다.")

    header_idx, mapping = _find_csv_header(rows)
    if mapping is None:
        raise ValueError(
            "CSV 열 이름을 알아보지 못했습니다. 최소한 제목(Title)·연도(Year)·"
            "키워드(Keywords/MeSH Terms) 중 하나에 해당하는 열이 필요합니다."
        )

    # 내보내기 파일을 이어 붙이면 헤더 줄이 본문 중간에 다시 나타난다. 그대로 두면
    # 'Title'/'MeSH Terms' 같은 헤더 문자열이 논문 한 편과 주제어로 둔갑한다.
    header_row = rows[header_idx]
    body = [r for r in rows[header_idx + 1 :] if r != header_row]
    # 키워드 열이 MeSH 색인 표기를 쓰는지 코퍼스 단위로 한 번 판단(RIS 와 동일 규칙).
    kw_cols = mapping.get("keywords", [])
    kw_mesh_mode = _keywords_look_like_mesh(
        [tok for row in body for tok in _split_multi(_join_cells(row, kw_cols))]
    )

    articles: List[Article] = []
    for row in body:
        art = _article_from_csv_row(row, mapping, kw_mesh_mode=kw_mesh_mode)
        if art is not None:
            articles.append(art)
    if not articles:
        raise ValueError("CSV 에서 읽어들인 레코드가 없습니다.")
    return articles


def _find_csv_header(rows: List[List[str]]) -> Tuple[int, Optional[Dict[str, List[int]]]]:
    """헤더 행을 찾아 (행 인덱스, {필드명: [열 인덱스…]}) 를 돌려준다.

    Scopus 처럼 안내문 몇 줄 뒤에 헤더가 오는 파일이 있어, 앞쪽 몇 행 중
    **가장 많은 필드를 인식한 행**을 헤더로 고른다. 같은 필드에 매핑되는 열이
    여러 개면(예: 'Author Keywords' + 'Index Keywords') 모두 모아 합친다.
    """
    best_idx, best_map, best_hits = 0, None, 0
    for idx, row in enumerate(rows[:10]):
        mapping: Dict[str, List[int]] = {}
        for col, name in enumerate(row):
            fieldname = _CSV_FIELD_BY_KEY.get(_norm_header(name))
            if fieldname:
                mapping.setdefault(fieldname, []).append(col)
        hits = len(mapping)
        # 식별자(doi)만 잡힌 행은 헤더로 보지 않는다 — 내용이 없다.
        useful = hits - (1 if set(mapping) <= {"doi"} else 0)
        if useful > best_hits:
            best_idx, best_map, best_hits = idx, mapping, useful
    if best_hits == 0:
        return 0, None
    return best_idx, best_map


def _cell(row: List[str], idx: int) -> str:
    return clean_value(row[idx] or "") if 0 <= idx < len(row) else ""


def _join_cells(row: List[str], cols: Sequence[int]) -> str:
    parts = [_cell(row, c) for c in cols]
    return "; ".join(p for p in parts if p)


def _article_from_csv_row(
    row: List[str], mapping: Dict[str, List[int]], kw_mesh_mode: bool = False
) -> Optional[Article]:
    title = _join_cells(row, mapping.get("title", [])) or ""
    journal = _join_cells(row, mapping.get("journal", [])) or ""
    year_raw = _join_cells(row, mapping.get("year", []))
    mesh_raw = _join_cells(row, mapping.get("mesh", []))
    kw_raw = _join_cells(row, mapping.get("keywords", []))
    pt_raw = _join_cells(row, mapping.get("pub_types", []))
    pmid = _join_cells(row, mapping.get("pmid", []))
    doi = _join_cells(row, mapping.get("doi", []))

    if not any((title, journal, year_raw, mesh_raw, kw_raw, pmid, doi)):
        return None

    year = _year_from(year_raw) if year_raw else None

    # MeSH 열은 NBIB 규칙(qualifier '/', 대표 '*')을 그대로 적용.
    mesh, major = _nbib_mesh(_split_multi(mesh_raw))
    kw_tokens = _split_multi(kw_raw)
    if kw_mesh_mode:
        # 'MeSH Terms' 전용 열이 없고 키워드 열에 MeSH 색인어가 들어온 내보내기
        # (PubMed 웹 CSV 변형 등) — 키워드도 주제로 승격한다.
        kw_mesh, kw_major = _nbib_mesh(kw_tokens)
        for t in kw_mesh:
            if t not in mesh:
                mesh.append(t)
        for t in kw_major:
            if t not in major:
                major.append(t)
        keywords = kw_mesh
    else:
        keywords = _dedup_keep_order(kw_tokens)
    pub_types = _dedup_keep_order(_split_multi(pt_raw))

    ident = pmid.strip()
    if not _DIGITS_RE.match(ident):
        ident = ("doi:" + doi.strip()) if doi.strip() else "?"

    return Article(
        pmid=ident or "?",
        year=year,
        journal=journal or "(unknown journal)",
        title=title or "(no title)",
        mesh=mesh,
        mesh_major=major,
        keywords=keywords,
        pub_types=pub_types,
    )


def _split_multi(value: str) -> List[str]:
    if not value:
        return []
    return [p.strip() for p in _CSV_MULTI_SPLIT.split(value) if p.strip()]


# --------------------------------------------------------------------------- #
# 견고한 파일 로딩 — gzip / 인코딩 / 포맷 자동 감지 / PMID 중복 제거
# --------------------------------------------------------------------------- #
# 압축 해제 결과의 상한. 279바이트짜리 중첩 gzip 이 50MB 로, 500KB 가 1.6GB 로 부푸는
# 것을 실제로 확인했다(압축폭탄). 서지 내보내기 파일은 아무리 커도 이 한계에 한참 못
# 미치므로, 정상 사용을 막지 않으면서 메모리 고갈만 차단한다.
# 상한을 넉넉히 잡으면 '상한 자체가 DoS' 가 된다: 512MiB 를 허용하면 파싱 단계까지
# 합쳐 4GiB 넘는 RSS 를 썼다(디코드 사본 + splitlines + Article 객체). 실제 서지
# 내보내기는 아무리 커도 수십 MB 이므로 64MiB 면 정상 사용에 전혀 지장이 없다.
MAX_DECOMPRESSED_BYTES = 64 * 1024 * 1024  # 64 MiB
# 압축되지 않은 입력에도 같은 상한을 적용한다(예전엔 .gz 만 막혀 있었다).
MAX_INPUT_BYTES = MAX_DECOMPRESSED_BYTES
_MAX_GZIP_LAYERS = 4
# UTF-16 BOM. latin-1 은 어떤 바이트열도 '성공'하므로, BOM 을 먼저 보지 않으면
# UTF-16 파일이 NUL 이 섞인 mojibake 로 조용히 '파싱 성공' 한다(연도·PMID 전부 유실).
_UTF16_BOMS = ((b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32"),
               (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16"))


def _gunzip_limited(raw: bytes) -> bytes:
    """gzip 해제 — 결과 크기에 상한을 두어 압축폭탄을 막는다.

    청크 단위로 읽어 이어 붙인다. `fh.read(MAX+1)` 한 방으로 읽으면 상한만큼을
    통째로 만든 뒤 버리게 되어, **가드 자체가** 700바이트 입력으로 1GB 를 쓰는
    DoS 수단이 된다.

    손상된 gzip 은 `ValueError` 로 승격한다 — `BadGzipFile`(OSError)·`EOFError` 를
    그대로 두면 CLI 가 rc 3('예기치 못한 오류')로 처리해, 사용자 파일 문제를
    내부 오류처럼 보고하고 영문 원문 메시지를 노출한다.
    """
    chunks: List[bytes] = []
    size = 0
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_DECOMPRESSED_BYTES:
                    raise ValueError(
                        f"압축을 풀면 {MAX_DECOMPRESSED_BYTES // (1024 * 1024)}MB 를 "
                        "넘습니다 — 압축폭탄이거나 서지 파일이 아닙니다."
                    )
                chunks.append(chunk)
    except ValueError:
        raise
    except (OSError, EOFError, zlib.error) as exc:
        # zlib.error 는 OSError 가 아니다('Error -3 while decompressing').
        raise ValueError(f"gzip 파일이 손상됐거나 형식이 아닙니다 — {exc}") from exc
    return b"".join(chunks)


def decode_bytes(raw: bytes) -> str:
    """바이트 → 텍스트. gzip 자동 해제(이중 압축·폭탄 방어), 인코딩 자동 판별.

    인코딩 판별 순서: UTF-16/32 BOM → UTF-8(BOM 포함) → latin-1. latin-1 은 절대
    실패하지 않으므로 마지막에 둔다 — 그 전에 BOM 을 확인하지 않으면 엑셀의
    '유니코드 텍스트'(UTF-16) 내보내기가 NUL 섞인 쓰레기로 '성공적으로' 읽힌다.
    """
    layers = 0
    while raw[:2] == b"\x1f\x8b" and layers < _MAX_GZIP_LAYERS:  # gzip 매직(이중 압축)
        raw = _gunzip_limited(raw)
        layers += 1
    for bom, enc in _UTF16_BOMS:
        if raw.startswith(bom):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                # 'continue' 여야 한다: UTF-16LE 파일은 'ff fe 00 00' 로 시작할 수 있어
                # UTF-32 항목에 먼저 걸린다. break 하면 UTF-16 을 아예 시도하지 못하고
                # latin-1 로 떨어져 NUL 섞인 쓰레기가 된다.
                continue
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def detect_format(text: str, hint: Optional[str] = None) -> str:
    """'xml' | 'ris' | 'nbib' | 'csv' 로 포맷을 추정한다(내용 우선, 확장자는 보조).

    RIS 와 NBIB 는 둘 다 `TAG - value` 꼴이라 태그 모양만으로는 못 가른다
    (예: `TI  - ...` 는 양쪽 모두 유효). 판별은 각 포맷의 **고유 필수 태그**로 한다:
    RIS 레코드는 반드시 `TY  - ` 로 시작하고 `ER  -` 로 끝난다. 태그줄이 거의 없으면
    표(CSV/TSV)로 본다. `hint`('csv'/'tsv'/'nbib'/'ris'/'xml', 보통 파일 확장자)는
    내용 신호가 약할 때만 쓴다 — 잘못 붙은 확장자에 끌려가지 않기 위해서다.
    """
    s = text.lstrip("﻿ \t\r\n")
    if s.startswith("<"):
        return "xml"
    if _RIS_TY_RE.search(text):
        return "ris"

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return hint if hint in ("xml", "ris", "nbib", "csv") else "nbib"
    tagged = sum(1 for ln in lines[:200] if _NBIB_FIELD_RE.match(ln))
    sample = min(len(lines), 200)
    if tagged >= 2 and tagged / sample >= 0.4:
        return "nbib"
    if hint in ("csv", "tsv"):
        return "csv"
    if hint in ("nbib", "ris", "xml"):
        return hint
    # 태그줄이 거의 없는 텍스트: 구분자가 일관되게 보이면 표로 본다.
    if _count_outside_quotes(lines[0], sniff_delimiter(text)) >= 1:
        return "csv"
    return "nbib"


_EXT_HINT = {
    ".xml": "xml", ".nxml": "xml",
    ".ris": "ris", ".bib": "ris",
    ".nbib": "nbib", ".medline": "nbib", ".txt": None,
    ".csv": "csv", ".tsv": "tsv", ".tab": "tsv",
}


def _reject_binaryish(text: str) -> None:
    """NUL 이 섞인 텍스트를 거부한다.

    BOM 이 없는 UTF-16/32 파일은 어떤 인코딩 판별도 통과하지 못하고 latin-1 로
    떨어져 'S\\x00l\\x00e\\x00e\\x00p' 같은 문자열이 된다. CSV 경로는 헤더가
    정규화 후 우연히 매칭돼 **rc 0 으로 조용히 틀린 리포트**를 냈다(유령 논문,
    연도·PMID 전부 유실, 출력에 NUL 바이트). 정상 서지 텍스트에는 NUL 이 없다.
    """
    if "\x00" in text:
        raise ValueError(
            "텍스트에 NUL 바이트가 있습니다 — BOM 없는 UTF-16/32 이거나 서지 파일이 "
            "아닙니다. UTF-8 로 다시 내보내 주세요."
        )


def parse_records(text: str, hint: Optional[str] = None) -> List[Article]:
    """텍스트 내용으로 XML/RIS/NBIB/CSV 를 자동 판별해 파싱한다."""
    if not text or not text.strip():
        raise ValueError("빈 입력입니다.")
    _reject_binaryish(text)
    fmt = detect_format(text, hint=hint)
    if fmt == "xml":
        return parse_efetch_xml(text)
    if fmt == "ris":
        return parse_ris(text)
    if fmt == "csv":
        # 확장자는 **힌트일 뿐**이다. '.tsv' 라는 이유로 구분자를 탭으로 못박으면
        # 잘못 붙은 확장자 하나에 파싱이 통째로 실패한다(같은 바이트가 .csv 로는
        # 잘 읽히는데 .tsv 로는 rc=2). 내용 기반 추정을 우선하고, 추정이 실패했을
        # 때에만 힌트로 되돌아간다.
        delim = sniff_delimiter(text)
        if hint == "tsv" and delim != "\t" and "\t" in text:
            delim = "\t"
        return parse_csv_records(text, delimiter=delim)

    articles = parse_medline_nbib(text)
    if not articles:
        # 'nbib' 는 판별의 마지막 폴백이므로, 여기서 0편이면 사실상 '형식을 모르겠다'
        # 는 뜻이다. 그대로 빈 리스트를 돌려주면 CLI 가 "검색 결과가 없습니다 —
        # 검색어를 바꿔 보세요"라고 안내해, 파일이 깨졌다는 진짜 원인을 숨긴다.
        raise ValueError(
            "입력 형식을 알아보지 못했습니다. 지원 형식: PubMed efetch XML, "
            "MEDLINE/NBIB, RIS, CSV/TSV (각각 .gz 가능). 파일 앞부분이 손상되지 "
            "않았는지, 서지 내보내기 파일이 맞는지 확인하세요."
        )
    return articles


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


def read_source(path: Union[str, Path]) -> Tuple[bytes, str, Optional[str]]:
    """파일을 한 번 읽어 (원본 바이트, 디코드 텍스트, 확장자 힌트) 를 돌려준다.

    CLI 와 `load_articles` 가 **같은 가드**(크기 상한·gzip 해제·인코딩 판별)를 쓰도록
    한 곳에 모았다. 예전에는 CLI 가 직접 `read_bytes()` 를 해서, 문서에 적힌 64MB
    입력 상한이 실제 사용자 경로에는 걸리지 않았다.
    """
    p = Path(path)
    raw = p.read_bytes()
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError(
            f"입력이 {MAX_INPUT_BYTES // (1024 * 1024)}MB 를 넘습니다 — 기간을 나눠 "
            "여러 파일로 분석하세요."
        )
    text = decode_bytes(raw)
    suffixes = [s.lower() for s in p.suffixes if s.lower() != ".gz"]
    return raw, text, (_EXT_HINT.get(suffixes[-1]) if suffixes else None)


def load_articles(path: Union[str, Path]) -> List[Article]:
    """파일 경로 → Article 리스트.

    - .gz 자동 해제, UTF-8/latin-1 관대 디코드.
    - XML(efetch) / NBIB(MEDLINE) / RIS / CSV·TSV 자동 판별(확장자는 보조 힌트).
    - PMID(또는 DOI) 중복 제거.
    빈/깨진 입력이면 ValueError(호출부에서 사용자 메시지로 변환).
    """
    _raw, text, hint = read_source(path)
    return dedup_articles(parse_records(text, hint=hint))


def topics_from_keywords(articles: Sequence[Article]) -> Tuple[List[Article], bool]:
    """MeSH 가 전혀 없는 코퍼스면 저자 키워드를 주제로 승격한 사본을 돌려준다.

    RIS·CSV 내보내기(그리고 아직 색인 전인 최신 PubMed 레코드)는 MeSH 가 비어 있는
    일이 흔하다. 그대로 두면 주제·공백 분석이 **조용히 전부 빈 결과**가 되어, 사용자는
    "이 분야엔 공백이 없다"고 오해한다. 그래서 *한 편도 MeSH 가 없고* 키워드는 있는
    경우에만 키워드를 주제로 올린다(부분적으로 MeSH 가 있으면 손대지 않는다 —
    그때 섞으면 색인된 논문과 안 된 논문의 주제 밀도가 달라져 통계가 왜곡된다).

    반환: (articles, 폴백을 적용했는지). 호출부는 이 사실을 사용자에게 알려야 한다.
    """
    if any(a.mesh for a in articles):
        return list(articles), False
    if not any(a.keywords for a in articles):
        return list(articles), False
    return apply_include_keywords(articles), True


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
