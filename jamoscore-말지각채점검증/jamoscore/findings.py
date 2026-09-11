"""소견 한 건의 표현과, 리포트가 쓸 수 있는 **유일한 어휘**.

이 툴은 사람의 채점에 옳고 그름을 매기지 않는다. 판정 권한이 없기 때문이다.
그래서 소견 유형을 열거형처럼 고정하고, 문구도 여기에서만 만든다.
새 문구가 필요하면 여기에 추가하고 금지어 테스트를 통과시켜야 한다.
"""

from __future__ import annotations

from typing import List, Optional

from .safeio import mask_identifiers, sanitize_line

CRITICAL = "치명"
WARNING = "경고"
INFO = "정보"

_ORDER = {CRITICAL: 0, WARNING: 1, INFO: 2}

#: 리포트·문서 어디에도 나오면 안 되는 표현(판정하는 말투).
FORBIDDEN_PHRASES = (
    "채점 오류", "오답 처리", "틀렸다", "유의하게 호전", "진단", "환자입니다",
)


class Finding:
    """소견 한 건.

    ``kind`` 는 사람이 읽는 유형 이름이고, ``message`` 는 그 한 건의 설명이다.
    ``evidence`` 는 엑셀에서 바로 찾아갈 수 있는 위치(시트!셀)다.
    """

    __slots__ = ("severity", "kind", "subject", "location", "message",
                 "evidence", "extra")

    def __init__(self, severity: str, kind: str, message: str,
                 subject: str = "", location: str = "", evidence: str = "",
                 extra: Optional[dict] = None):
        if severity not in _ORDER:
            raise ValueError("severity 는 치명·경고·정보 중 하나여야 합니다: %r" % (severity,))
        self.severity = severity
        self.kind = kind
        # 소견은 셀 값을 근거로 그대로 싣는다 — 그게 쓸모의 핵심이다. 다만
        # 전사 칸에 전화번호·주민번호가 적혀 들어오는 일이 실제로 있으므로,
        # 소견이 만들어지는 이 한 곳에서 모양만 남기고 가린다.
        self.subject = _clean(subject)
        self.location = _clean(location)
        self.message = _clean(message)
        self.evidence = _clean(evidence)
        self.extra = extra or {}

    def sort_key(self):
        return (_ORDER[self.severity], self.kind, self.subject, self.location)

    def as_row(self) -> List[str]:
        return [self.severity, self.kind, self.subject, self.location,
                self.message, self.evidence]

    def __repr__(self) -> str:
        return "Finding(%s/%s %s %s)" % (
            self.severity, self.kind, self.subject, self.message[:40])


def _clean(text: str) -> str:
    """줄 위조를 막고(개행 제거) 식별 패턴을 가린다."""
    return mask_identifiers(sanitize_line(text))


CSV_HEADER = ["심각도", "유형", "피험자", "위치", "내용", "근거"]


def count_by_severity(findings: List[Finding]) -> dict:
    out = {CRITICAL: 0, WARNING: 0, INFO: 0}
    for f in findings:
        out[f.severity] += 1
    return out


def sort_findings(findings: List[Finding]) -> List[Finding]:
    return sorted(findings, key=lambda f: f.sort_key())
