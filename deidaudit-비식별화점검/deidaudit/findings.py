"""발견 항목(Finding)과 심각도 정의.

이 툴이 만들어 내는 모든 지적은 Finding 하나로 표현됩니다. 증거 문자열은
`evidence` 에 **이미 마스킹된 형태로만** 담습니다 — 원문을 담는 경로는
존재하지 않습니다(리포트 자체가 유출 경로가 되지 않도록).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

CRITICAL = "치명"
WARNING = "경고"
INFO = "정보"

_SEVERITY_ORDER = {CRITICAL: 0, WARNING: 1, INFO: 2}


@dataclass(frozen=True)
class Finding:
    """한 건의 지적.

    Attributes:
        severity: 치명 / 경고 / 정보.
        kind: 유형 이름(예: "휴대전화", "숨김 시트").
        file: 입력 파일 표시명.
        sheet: 시트 이름(CSV 는 빈 문자열).
        column: 열 이름(열 단위가 아니면 빈 문자열).
        row: 1부터 시작하는 데이터 행 번호(행 단위가 아니면 None).
        evidence: **마스킹된** 증거 문자열.
        note: 사람이 읽을 부연 설명.
    """

    severity: str
    kind: str
    file: str
    sheet: str = ""
    column: str = ""
    row: Optional[int] = None
    evidence: str = ""
    note: str = ""

    @property
    def location(self) -> str:
        parts = [self.file]
        if self.sheet:
            parts.append("!" + self.sheet)
        loc = "".join(parts)
        if self.column:
            loc += f"  {self.column} 열"
        if self.row is not None:
            loc += f" {self.row}행"
        return loc


@dataclass
class FindingSet:
    """Finding 들의 모음. 정렬·집계 편의를 제공합니다."""

    items: List[Finding] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.items.append(finding)

    def extend(self, findings) -> None:
        self.items.extend(findings)

    def __len__(self) -> int:  # pragma: no cover - 자명
        return len(self.items)

    def __iter__(self):  # pragma: no cover - 자명
        return iter(self.items)

    def by_severity(self, severity: str) -> List[Finding]:
        return [f for f in self.items if f.severity == severity]

    @property
    def critical_count(self) -> int:
        return len(self.by_severity(CRITICAL))

    @property
    def warning_count(self) -> int:
        return len(self.by_severity(WARNING))

    def sorted_items(self) -> List[Finding]:
        return sorted(
            self.items,
            key=lambda f: (
                _SEVERITY_ORDER.get(f.severity, 9),
                f.file,
                f.sheet,
                f.kind,
                f.column,
                f.row if f.row is not None else -1,
            ),
        )

    def grouped(self):
        """(심각도, 유형, 파일, 시트, 열) 로 묶은 요약 목록을 돌려줍니다.

        Returns:
            list[dict]: 각 항목은 count/first_evidence/rows 를 포함.
        """
        groups = {}
        order = []
        for f in self.sorted_items():
            key = (f.severity, f.kind, f.file, f.sheet, f.column)
            if key not in groups:
                groups[key] = {
                    "severity": f.severity,
                    "kind": f.kind,
                    "file": f.file,
                    "sheet": f.sheet,
                    "column": f.column,
                    "count": 0,
                    "first_evidence": f.evidence,
                    "note": f.note,
                    "rows": [],
                }
                order.append(key)
            g = groups[key]
            g["count"] += 1
            if f.row is not None:
                g["rows"].append(f.row)
            if not g["first_evidence"] and f.evidence:
                g["first_evidence"] = f.evidence
            if not g["note"] and f.note:
                g["note"] = f.note
        return [groups[k] for k in order]
