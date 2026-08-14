"""참고문헌 · 그림 · 표 증감 대조와 분량 변화.

응답서가 "새 참고문헌 3편을 추가했다"고 썼는데 실제로는 2편만 들어간 경우를 잡는다.
추가된 문헌은 ``추가문헌.csv`` 로 내보내며, **열 스키마는 citecheck 입력과 같다** —
새로 넣은 문헌이야말로 DOI 검증을 아직 안 한 문헌이기 때문이다.

여기서 하지 않는 것: DOI 실존 확인(citecheck), 인용↔목록 정합(draftcheck).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .normalize import canonical, norm_compare

__all__ = [
    "Reference",
    "InventoryDiff",
    "CITECHECK_HEADER",
    "collect_references",
    "collect_labels",
    "diff_inventory",
    "claimed_counts",
    "reference_rows",
    "mentioned_labels",
]

# draftcheck 의 references.csv 와 **같은 열 이름** — citecheck 가 그대로 받는다.
CITECHECK_HEADER = [
    "Study ID", "Authors", "Year", "Title", "Journal", "Article DOI", "PMID", "parse_ok",
]

_RE_ENTRY_NUM = re.compile(r"^\s*[\[(]?(\d{1,3})[\])]?[.)]?\s+")
_RE_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
_RE_DOI = re.compile(r"\b10\.\d{4,9}/[^\s,;\"'<>]+", re.IGNORECASE)
_RE_PMID = re.compile(r"\bPMID\s*[:.]?\s*(\d{4,9})\b", re.IGNORECASE)
_RE_FIGURE = re.compile(r"\b(?:figure|fig)\.?\s*(\d{1,3})\b|그림\s*(\d{1,3})", re.IGNORECASE)
_RE_TABLE = re.compile(r"\b(?:table|tbl)\.?\s*(\d{1,3})\b|[^가-힣]표\s*(\d{1,3})", re.IGNORECASE)

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5,
}

_RE_CLAIM_REF_EN = re.compile(
    r"\b(?:added|inserted|cited)\s+(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)"
    r"\s+(?:new\s+|additional\s+)?(?:references?|citations?|refs?)\b",
    re.IGNORECASE,
)
_RE_CLAIM_REF_KO = re.compile(
    r"(?:참고문헌|문헌|레퍼런스)\s*(\d{1,2}|한|두|세|네|다섯)\s*(?:편|개)?\s*(?:을|를)?\s*"
    r"(?:새로\s*)?(?:추가|인용|보강)"
)
_RE_CLAIM_REF_KO2 = re.compile(
    r"(\d{1,2}|한|두|세|네|다섯)\s*(?:편|개)의?\s*(?:새\s*)?(?:참고문헌|문헌|논문)\s*(?:을|를)?\s*(?:추가|인용)"
)


@dataclass
class Reference:
    raw: str
    key: str
    number: Optional[int] = None
    authors: str = ""
    year: str = ""
    title: str = ""
    journal: str = ""
    doi: str = ""
    pmid: str = ""
    parse_ok: bool = False
    ident: str = ""  # DOI/PMID 기반 식별자(있을 때만)
    text_key: str = ""  # 본문 기반 보조 키


@dataclass
class InventoryDiff:
    old_refs: List[Reference] = field(default_factory=list)
    new_refs: List[Reference] = field(default_factory=list)
    added_refs: List[Reference] = field(default_factory=list)
    removed_refs: List[Reference] = field(default_factory=list)
    old_figures: List[int] = field(default_factory=list)
    new_figures: List[int] = field(default_factory=list)
    old_tables: List[int] = field(default_factory=list)
    new_tables: List[int] = field(default_factory=list)
    found_section: bool = True
    skipped_entries: int = 0  # 연도·DOI 가 없어 문헌으로 세지 않은 줄

    @property
    def added_figures(self) -> List[int]:
        return sorted(set(self.new_figures) - set(self.old_figures))

    @property
    def added_tables(self) -> List[int]:
        return sorted(set(self.new_tables) - set(self.old_tables))

    @property
    def removed_figures(self) -> List[int]:
        return sorted(set(self.old_figures) - set(self.new_figures))

    @property
    def removed_tables(self) -> List[int]:
        return sorted(set(self.old_tables) - set(self.new_tables))


def _text_key(text: str, year: str) -> str:
    """DOI 를 뺀 본문으로 만드는 보조 키(DOI 를 덧붙인 것은 새 문헌이 아니다)."""
    stripped = _RE_DOI.sub("", text)
    stripped = re.sub(r"\bdoi\s*:?\s*", "", stripped, flags=re.IGNORECASE)
    return norm_compare(re.sub(r"\s+", " ", stripped))[:100] + "|" + year


def _parse_reference(raw: str) -> Reference:
    text = canonical(raw)
    number = None
    m = _RE_ENTRY_NUM.match(text)
    if m:
        number = int(m.group(1))
        text = text[m.end():]
    doi_m = _RE_DOI.search(text)
    doi = doi_m.group(0).rstrip(".,;") if doi_m else ""
    pmid_m = _RE_PMID.search(text)
    pmid = pmid_m.group(1) if pmid_m else ""
    year_m = _RE_YEAR.search(text)
    year = year_m.group(1) if year_m else ""
    authors = title = journal = ""
    segments = [seg.strip() for seg in re.split(r"\.\s+", text) if seg.strip()]
    if year_m and text[max(0, year_m.start() - 1):year_m.start()] == "(":
        # APA: "Nakamura, T., & Ito, Y. (2021). 제목. 저널, 30(4), e13268."
        authors = text[: year_m.start() - 1].strip(" .,;()")
        tail = text[year_m.end():].lstrip(" ).,;")
        pieces = [seg.strip() for seg in re.split(r"\.\s+", tail) if seg.strip()]
        title = pieces[0] if pieces else ""
        journal = pieces[1] if len(pieces) > 1 else ""
    elif year_m and segments:
        head = segments[0]
        if _RE_YEAR.search(head):
            # 저자-연도 방식: "Kim H, Park J. 2019. 제목. 저널 ..."
            authors = head[: _RE_YEAR.search(head).start()].strip(" .,;()") or head
            title = segments[1] if len(segments) > 1 else ""
            journal = segments[2] if len(segments) > 2 else ""
        else:
            # 밴쿠버 방식: "저자. 제목. 저널. 2019;28(3):e12812." — 연도가 뒤에 온다.
            authors = head
            title = segments[1] if len(segments) > 1 else ""
            journal = ""
            for seg in segments[2:]:
                if _RE_YEAR.search(seg):
                    journal = seg[: _RE_YEAR.search(seg).start()].strip(" .,;:()")
                    break
            if not journal and len(segments) > 2:
                journal = segments[2]
        journal = _RE_DOI.sub("", journal)
        journal = re.sub(r"\s*doi\s*:?\s*$", "", journal, flags=re.IGNORECASE)
        journal = journal.strip(" .,;:()")
    ident = ("doi:" + doi.lower().rstrip(".")) if doi else (("pmid:" + pmid) if pmid else "")
    text_key = _text_key(text, year)
    key = ident or text_key
    return Reference(
        raw=text,
        key=key,
        number=number,
        authors=authors,
        year=year,
        title=title,
        journal=journal,
        doi=doi,
        pmid=pmid,
        parse_ok=_parse_looks_sane(authors, year, title),
        ident=ident,
        text_key=text_key,
    )


_LETTERS = re.compile(r"[A-Za-z가-힣]{3,}")


def _parse_looks_sane(authors: str, year: str, title: str) -> bool:
    """열을 제대로 갈랐는지 **정직하게** 표시한다.

    ``Title`` 칸에 ``(2021)`` 이나 권/호가 들어갔는데도 ``yes`` 라고 하면,
    citecheck 로 넘어간 뒤에야 문제가 드러난다.
    """
    if not (year and authors and title):
        return False
    if not _LETTERS.search(authors) or not _LETTERS.search(title):
        return False
    if _RE_YEAR.search(title):
        return False
    return len(title) >= 8


def collect_references(doc) -> Tuple[List[Reference], bool, int]:
    """참고문헌 절의 항목들을 뽑는다. (항목들, 절을 찾았는지, 건너뛴 줄 수)

    참고문헌으로 인정하려면 **연도나 DOI/PMID 가 있어야 한다.** 원고는 참고문헌
    뒤에 표·그림 캡션·판권 문구를 붙이는 일이 흔하고, 그것들을 문헌으로 세면
    "참고문헌 6편 추가" 같은 거짓 경고가 나온다.
    """
    entries = [
        p for p in doc.paras if p.section == "References" and p.kind != "heading"
    ]
    if not entries:
        return [], False, 0
    refs: List[Reference] = []
    skipped = 0
    for para in entries:
        text = para.text.strip()
        if len(text) < 12:  # "References" 밑의 빈 줄·구분선
            continue
        if not (_RE_YEAR.search(text) or _RE_DOI.search(text) or _RE_PMID.search(text)):
            skipped += 1
            continue
        refs.append(_parse_reference(text))
    return refs, True, skipped


def collect_labels(doc) -> Tuple[List[int], List[int]]:
    """본문에 등장하는 Figure / Table 번호 목록."""
    figures: List[int] = []
    tables: List[int] = []
    for para in doc.paras:
        if para.section == "References":
            continue
        text = " " + canonical(para.text)
        for m in _RE_FIGURE.finditer(text):
            figures.append(int(m.group(1) or m.group(2)))
        for m in _RE_TABLE.finditer(text):
            tables.append(int(m.group(1) or m.group(2)))
    return sorted(set(figures)), sorted(set(tables))


def diff_inventory(old_doc, new_doc) -> InventoryDiff:
    old_refs, old_found, old_skipped = collect_references(old_doc)
    new_refs, new_found, new_skipped = collect_references(new_doc)
    added, removed = _match_references(old_refs, new_refs)
    old_figs, old_tabs = collect_labels(old_doc)
    new_figs, new_tabs = collect_labels(new_doc)
    return InventoryDiff(
        old_refs=old_refs,
        new_refs=new_refs,
        added_refs=added,
        removed_refs=removed,
        old_figures=old_figs,
        new_figures=new_figs,
        old_tables=old_tabs,
        new_tables=new_tabs,
        found_section=old_found and new_found,
        skipped_entries=old_skipped + new_skipped,
    )


def _match_references(old_refs, new_refs):
    """제출본↔개정본 문헌을 **두 단계**로 짝짓는다.

    ① DOI/PMID 가 양쪽에 있으면 그것으로 ② 남은 것끼리는 정규화된 본문 키로.
    한쪽에만 DOI 를 덧붙인 경우(리비전에서 흔하다)를 '1편 삭제 + 1편 추가'로
    세지 않기 위해서다.
    """
    remaining_old = list(old_refs)
    added = []
    for ref in new_refs:
        match = None
        for candidate in remaining_old:
            if ref.ident and candidate.ident and ref.ident == candidate.ident:
                match = candidate
                break
        if match is None:
            for candidate in remaining_old:
                if ref.text_key and ref.text_key == candidate.text_key:
                    match = candidate
                    break
        if match is None:
            added.append(ref)
        else:
            remaining_old.remove(match)
    return added, remaining_old


def _as_int(token: str) -> Optional[int]:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)


def claimed_counts(response_text: str) -> List[int]:
    """응답서가 말한 '추가한 참고문헌 편 수' 주장들."""
    text = canonical(response_text)
    claims: List[int] = []
    for pattern in (_RE_CLAIM_REF_EN, _RE_CLAIM_REF_KO, _RE_CLAIM_REF_KO2):
        for m in pattern.finditer(text):
            value = _as_int(m.group(1))
            if value is not None:
                claims.append(value)
    return claims


def reference_rows(refs: Sequence[Reference]) -> List[List[str]]:
    """``추가문헌.csv`` 의 행 — citecheck 입력 스키마 그대로."""
    rows: List[List[str]] = [list(CITECHECK_HEADER)]
    for i, ref in enumerate(refs, start=1):
        study_id = str(ref.number) if ref.number else f"NEW{i}"
        rows.append(
            [
                study_id,
                ref.authors,
                ref.year,
                ref.title or ref.raw,
                ref.journal,
                ref.doi,
                ref.pmid,
                "yes" if ref.parse_ok else "no",
            ]
        )
    return rows


def mentioned_labels(text: str) -> Tuple[set, set]:
    """텍스트에 등장하는 (Figure 번호 집합, Table 번호 집합)."""
    body = " " + canonical(text)
    figures = {int(m.group(1) or m.group(2)) for m in _RE_FIGURE.finditer(body)}
    tables = {int(m.group(1) or m.group(2)) for m in _RE_TABLE.finditer(body)}
    return figures, tables
