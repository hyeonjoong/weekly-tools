"""줄마다 어느 절(Abstract/Results/…)에 속하는지 붙인다.

지적 목록에 `[L142 Results]` 처럼 절 이름이 붙어 있어야 사람이 바로 그 자리를
찾는다. 그리고 **참고문헌 절의 숫자는 검사 대상이 아니다** — 권·호·페이지·연도가
전부 숫자라서, 이 구분이 없으면 리포트가 참고문헌 소음으로 뒤덮인다.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from .docio import Line, Manuscript

__all__ = ["assign_sections", "SECTION_ORDER"]

SECTION_ORDER = [
    "Title",
    "Abstract",
    "Introduction",
    "Methods",
    "Results",
    "Discussion",
    "Conclusion",
    "References",
    "Other",
]

_HEADING_WORDS: Dict[str, set] = {
    "Abstract": {
        "abstract", "structured abstract", "summary", "초록", "요약", "국문초록", "영문초록",
    },
    "Keywords": {"keywords", "key words", "주제어", "키워드", "중심단어"},
    "Introduction": {"introduction", "background", "서론", "배경", "연구배경"},
    "Methods": {
        "methods", "method", "materials and methods", "methods and materials",
        "patients and methods", "subjects and methods", "study design",
        "방법", "연구방법", "대상 및 방법", "재료 및 방법", "연구대상 및 방법",
    },
    "Results": {"results", "result", "findings", "결과", "연구결과"},
    "Discussion": {"discussion", "논의", "고찰"},
    "Conclusion": {"conclusion", "conclusions", "결론", "요약 및 결론"},
    "References": {
        "references", "reference", "reference list", "bibliography",
        "literature cited", "참고문헌", "인용문헌", "works cited",
    },
}

# 참고문헌 뒤에 오는(= 참고문헌 목록을 끝내는) 절들
_TAIL_PREFIXES = (
    "acknowledg", "funding", "conflict", "competing interest", "declaration",
    "author contribution", "data availability", "ethics", "supplementary",
    "supporting information", "figure legend", "figure caption", "legends",
    "table legends", "appendix", "감사", "부록", "이해상충", "연구윤리",
    "그림 설명", "표 목록",
)

_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$")
_TEX_HEADING = re.compile(r"^\s*\\(?:sub){0,2}section\*?\s*\{(.+?)\}\s*$")
_NUM_PREFIX = re.compile(r"^\s*(?:\d{1,2}(?:\.\d{1,2}){0,3}|[IVXivx]{1,5})[.)]?\s+")

# 실제 작업 중인 원고의 제목에는 메모가 붙어 있다:
#   "References (Vancouver style; 번호 유지)"  "Results (Primary Endpoints)"
_ANNOTATION_SPLITS = (
    re.compile(r"^(.{2,40}?)\s*[(（\[].*$"),
    re.compile(r"^(.{2,40}?)\s*[/／].*$"),
    re.compile(r"^(.{2,40}?)\s*[—–]\s.*$"),
    re.compile(r"^([A-Za-z][A-Za-z\s]{1,38}?)\s+[가-힣].*$"),
)


def _match(candidate: str) -> Optional[str]:
    if not candidate or len(candidate.split()) > 6:
        return None
    low = candidate.lower()
    for key, names in _HEADING_WORDS.items():
        if low in names:
            return key
    for prefix in _TAIL_PREFIXES:
        if low.startswith(prefix):
            return "Other"
    return None


def heading_key(text: str) -> Optional[str]:
    """이 줄이 절 제목이면 그 키, 아니면 ``None``."""
    t = text.strip()
    if not t or len(t) > 90:
        return None
    if re.match(r"^\s*\\begin\{thebibliography\}", t):
        return "References"
    m = _MD_HEADING.match(t)
    if m:
        t = m.group(1).strip()
    else:
        m = _TEX_HEADING.match(t)
        if m:
            t = m.group(1).strip()
    t = t.strip("*_ \t")
    t = _NUM_PREFIX.sub("", t)
    is_sentence = t.rstrip().endswith(".")
    t = t.strip(" :.\t*_")
    if not t:
        return None
    key = _match(t)
    if key:
        return key
    if is_sentence:
        return None  # "Results (Table 2) are shown below." 를 제목으로 오인하지 않는다
    for pattern in _ANNOTATION_SPLITS:
        m = pattern.match(t)
        if m:
            key = _match(m.group(1).strip(" :.\t*_-"))
            if key:
                return key
    return None


def assign_sections(ms: Manuscript) -> Dict[str, int]:
    """각 줄에 ``section`` 을 채우고, 찾은 절 제목 위치를 돌려준다."""
    found: Dict[str, int] = {}
    current = "Title"
    for ln in ms.lines:
        if ln.kind == "footnote":
            ln.section = "Other"
            continue
        key = heading_key(ln.text)
        if key:
            found.setdefault(key, ln.no)
            if key == "Keywords":
                # 키워드 절 자체는 짧고 검사 대상이 아니다. 다음 제목까지 Other.
                current = "Other"
            else:
                current = key
            ln.section = current
            continue
        ln.section = current
    return found


def reference_lines(lines: List[Line]) -> List[Line]:
    return [ln for ln in lines if ln.section == "References"]
