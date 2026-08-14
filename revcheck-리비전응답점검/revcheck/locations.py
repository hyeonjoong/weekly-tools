"""위치 참조(``p. 7, lines 210–214``) 추출과 검증.

**.docx 에는 줄 번호가 파일 안에 존재하지 않는다.** 워드가 화면에 보여 주는 줄은
글꼴·여백·용지에 따라 달라지는 렌더링 결과일 뿐이다. 그래서 .docx 에서는
**추정하지 않고 '확인불가'로 강등**하고 건수만 보고한다.
여기서 정직하게 지는 것이, 그럴듯한 숫자를 지어내는 것보다 낫다.

``.md``/``.tex``/``.txt`` 는 줄 번호가 실재하므로 두 가지를 본다.
    (a) 개정본의 줄 수 범위 안인가
    (b) 그 줄이 실제로 old→new 에서 **바뀐 구간**인가
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .normalize import canonical

__all__ = ["LocRef", "extract_locations", "verify_locations"]

# "p. 5, lines 132-140" 처럼 페이지와 줄이 붙어 나오는 경우를 먼저 잡는다.
_RE_PAGE_LINES = re.compile(
    r"\bp{1,2}\.?\s*(\d{1,4})\s*[,;]?\s*(?:lines?|ll?\.)\s*(\d{1,6})"
    r"(?:\s*[-–—~]\s*(\d{1,6}))?",
    re.IGNORECASE,
)
_RE_LINES = re.compile(
    r"\b(?:lines?|ll\.)\s*(\d{1,6})(?:\s*[-–—~]\s*(\d{1,6}))?", re.IGNORECASE
)
# ``L210`` / ``l. 210``. 저자 이니셜 뒤의 연도("Smith L. 2020")를 줄 번호로 읽지
# 않도록, 네 자리 연도로 보이는 값은 제외한다.
_RE_LINE_SHORT = re.compile(r"\bL\.?\s?(\d{1,6})(?:\s*[-–—~]\s*(\d{1,6}))?\b")


def _looks_like_year(value: int) -> bool:
    return 1900 <= value <= 2100
_RE_LINE_KO = re.compile(r"(\d{1,6})\s*(?:[-–—~]\s*(\d{1,6})\s*)?(?:번째\s*)?줄")
# "p." 나 "page" 로 시작해야 한다. "p = 0.05" 를 페이지로 읽으면 안 되므로
# 점 또는 page 단어를 반드시 요구한다.
_RE_PAGE = re.compile(r"\bp{1,2}\.\s*(\d{1,4})\b|\bpages?\s+(\d{1,4})\b", re.IGNORECASE)
_RE_PAGE_KO = re.compile(r"(\d{1,4})\s*쪽")


@dataclass
class LocRef:
    comment_label: str
    raw: str
    page: Optional[int] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    status: str = ""  # 일치 / 변경없음 / 범위초과 / 확인불가
    reason: str = ""

    @property
    def has_lines(self) -> bool:
        return self.line_start is not None


def _add_span(spans: List[range], start: int, end: int) -> bool:
    for span in spans:
        if start < span.stop and end > span.start:
            return False
    spans.append(range(start, end))
    return True


def extract_locations(comments: Sequence) -> List[LocRef]:
    """응답 본문에서 위치 참조를 뽑는다(코멘트 원문이 아니라 응답 쪽만)."""
    refs: List[LocRef] = []
    for comment in comments:
        text = canonical(" ".join(p.text for p in comment.body_paras))
        spans: List[range] = []
        for m in _RE_PAGE_LINES.finditer(text):
            if not _add_span(spans, m.start(), m.end()):
                continue
            end = int(m.group(3)) if m.group(3) else int(m.group(2))
            refs.append(
                LocRef(comment.label, m.group(0), int(m.group(1)), int(m.group(2)), end)
            )
        for pattern in (_RE_LINES, _RE_LINE_SHORT, _RE_LINE_KO):
            for m in pattern.finditer(text):
                start = int(m.group(1))
                if pattern is _RE_LINE_SHORT and _looks_like_year(start):
                    continue  # "Smith L. 2020" 은 위치 참조가 아니다
                if not _add_span(spans, m.start(), m.end()):
                    continue
                end = int(m.group(2)) if m.group(2) else start
                refs.append(LocRef(comment.label, m.group(0).strip(), None, start, end))
        for m in _RE_PAGE.finditer(text):
            if not _add_span(spans, m.start(), m.end()):
                continue
            page = m.group(1) or m.group(2)
            refs.append(LocRef(comment.label, m.group(0).strip(), int(page)))
        for m in _RE_PAGE_KO.finditer(text):
            if not _add_span(spans, m.start(), m.end()):
                continue
            refs.append(LocRef(comment.label, m.group(0).strip(), int(m.group(1))))
    return refs


def verify_locations(
    refs: Sequence[LocRef], new_doc, changes: Sequence, tolerance: int = 3
) -> List[LocRef]:
    """줄 번호가 실재하는 포맷에서만 검증한다. .docx 는 확인불가로 남긴다."""
    changed_spans = [
        (c.line_start, c.line_end)
        for c in changes
        if c.kind != "삭제" and c.line_start and c.line_end
    ]
    for ref in refs:
        if not ref.has_lines:
            ref.status = "확인불가"
            ref.reason = "페이지 번호는 원고 파일에 존재하지 않습니다"
            continue
        if not new_doc.has_line_numbers:
            ref.status = "확인불가"
            ref.reason = ".docx 에는 줄 번호가 없습니다"
            continue
        start, end = ref.line_start, ref.line_end or ref.line_start
        if start < 1 or start > new_doc.total_lines:
            ref.status = "범위초과"
            ref.reason = f"개정본은 총 {new_doc.total_lines}줄입니다"
            continue
        if not changed_spans:
            ref.status = "변경없음"
            ref.reason = "개정본에서 바뀐 문단이 없습니다"
            continue
        hit = any(
            start - tolerance <= span_end and end + tolerance >= span_start
            for span_start, span_end in changed_spans
        )
        ref.status = "일치" if hit else "변경없음"
        if not hit:
            ref.reason = "그 줄 부근은 제출본과 달라지지 않았습니다"
    return list(refs)
