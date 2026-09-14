"""점검 결과 한 건을 표현하는 자료형.

심각도는 세 단계뿐이다. 애매하면 올리지 않고 내린다:
치명 → 경고 → 정보. 대조 자체를 못 했으면 판정이 아니라 '대조불가'로
커버리지 자백에 실린다.
"""

from dataclasses import dataclass, field
from typing import List

CRITICAL = "치명"
WARNING = "경고"
INFO = "정보"

_ORDER = {CRITICAL: 0, WARNING: 1, INFO: 2}


@dataclass
class Finding:
    """점검 한 건. ``detail`` 은 콘솔에서 들여쓰기되어 근거로 출력된다."""

    severity: str
    code: str
    title: str
    detail: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.severity not in _ORDER:
            raise ValueError(f"알 수 없는 심각도: {self.severity}")

    @property
    def rank(self) -> int:
        return _ORDER[self.severity]


def sort_findings(findings: List[Finding]) -> List[Finding]:
    """치명 → 경고 → 정보 순. 같은 심각도 안에서는 발견 순서를 지킨다."""
    return sorted(findings, key=lambda f: f.rank)


def count_by(findings: List[Finding], severity: str) -> int:
    return sum(1 for f in findings if f.severity == severity)
