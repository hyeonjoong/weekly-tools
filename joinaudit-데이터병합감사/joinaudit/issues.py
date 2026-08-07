"""문제 기록 — `문제목록.csv` 와 화면 요약의 단일 출처.

이 툴의 가치는 병합 결과가 아니라 **"왜 이렇게 됐는지"의 증거**에 있으므로,
발견한 모든 것은 예외 없이 여기에 들어온다. 심각도는 셋뿐이다.

* `심각` — 병합 결과를 그대로 믿으면 안 된다(중복 키, 정규화 충돌 등).
* `경고` — 병합은 됐지만 행이 빠졌거나 값이 의심스럽다.
* `정보` — 알아 두면 좋은 사실. **종료코드에 영향을 주지 않는다.**
  거짓양성으로 사람을 지치게 만들 만한 항목은 전부 여기로 내린다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List

__all__ = ["Issue", "IssueLog", "CRITICAL", "WARNING", "INFO"]

CRITICAL = "심각"
WARNING = "경고"
INFO = "정보"

_ORDER = {CRITICAL: 0, WARNING: 1, INFO: 2}


@dataclass
class Issue:
    """문제 하나. `문제목록.csv` 한 행에 그대로 대응한다."""

    file: str
    kind: str
    message: str
    severity: str = WARNING
    line: str = ""
    key: str = ""
    advice: str = ""
    # 병합 자체를 진행할 수 없게 만드는 문제인가(종료코드 3).
    blocking: bool = False

    def as_row(self) -> List[str]:
        return [self.file, self.line, self.key, self.severity, self.kind,
                self.message, self.advice]


CSV_HEADER = ["파일", "행번호", "키", "심각도", "유형", "설명", "권고"]


class IssueLog:
    """문제를 모으고, 세고, 정렬해서 내보낸다."""

    def __init__(self) -> None:
        self._items: List[Issue] = []

    def add(self, issue: Issue) -> Issue:
        self._items.append(issue)
        return issue

    def extend(self, issues: Iterable[Issue]) -> None:
        self._items.extend(issues)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    @property
    def items(self) -> List[Issue]:
        """심각도 -> 파일 -> 유형 순으로 안정 정렬한 목록."""
        return sorted(self._items,
                      key=lambda i: (_ORDER.get(i.severity, 3), i.file, i.kind))

    def counts(self) -> Dict[str, int]:
        out = {CRITICAL: 0, WARNING: 0, INFO: 0}
        for item in self._items:
            out[item.severity] = out.get(item.severity, 0) + 1
        return out

    def by_kind(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for item in self._items:
            out[item.kind] = out.get(item.kind, 0) + 1
        return out

    def has(self, severity: str) -> bool:
        return any(i.severity == severity for i in self._items)

    @property
    def blocking(self) -> List[Issue]:
        return [i for i in self._items if i.blocking]

    def exit_code(self) -> int:
        """0 문제 없음 / 2 경고·심각 있음 / 3 병합 불가."""
        if self.blocking:
            return 3
        if self.has(CRITICAL) or self.has(WARNING):
            return 2
        return 0
