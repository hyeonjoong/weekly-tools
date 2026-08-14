"""판정 결과의 자료형 — 등급, 지적 한 건, 커버리지 자백, 전체 결과.

등급은 셋뿐이다.
    치명 : 리뷰어가 응답서를 손에 들고 원고를 열었을 때 **저자가 틀렸다고 보일** 것.
    경고 : 사람이 눈으로 한 번 확인해야 하는 것.
    정보 : 알고만 있으면 되는 것.

"확인 불가"는 등급이 아니라 **커버리지 자백**에 들어간다. 확인하지 못한 것을
'이상 없음'으로 표시하지 않는 것이 이 툴의 원칙이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

CRITICAL = "치명"
WARNING = "경고"
INFO = "정보"

SEVERITY_ORDER = (CRITICAL, WARNING, INFO)

# 종료코드 — README 와 사용법.md 에 그대로 적혀 있다.
EXIT_OK = 0
EXIT_CRITICAL = 1
EXIT_WARNING = 2
EXIT_UNDECIDABLE = 3


@dataclass
class Finding:
    """지적 한 건."""

    severity: str
    kind: str  # 코멘트누락 / 인용불일치 / 미신고변경 ... (CSV 의 '유형' 열)
    target: str  # 대상 (코멘트 번호, 문단 번호 등)
    message: str  # 한국어 한 줄 설명
    detail: List[str] = field(default_factory=list)  # 나란히 보여 줄 부가 줄
    advice: str = ""
    # 같은 등급 안에서의 자리. 요약·메타 지적은 뒤로 보낸다(1) — 실제 지적이
    # 먼저 보여야 한다.
    order: int = 0

    def sort_key(self):
        return (SEVERITY_ORDER.index(self.severity), self.kind, self.target)


@dataclass
class CoverageLine:
    """커버리지 자백의 한 줄 — '무엇을 몇 건 중 몇 건 봤는가'."""

    label: str
    total: int
    checked: int
    skipped_reasons: Dict[str, int] = field(default_factory=dict)
    note: str = ""
    custom: str = ""  # 이 줄을 통째로 대신할 문장(자연스러운 한국어를 위해)

    def render(self) -> str:
        if self.custom:
            return f"- {self.custom}"
        skipped = self.total - self.checked
        if self.total == 0:
            head = f"- {self.label}: 없음"
        elif skipped > 0:
            head = f"- {self.label}: {self.total}건 → 확인 {self.checked}건"
            if self.skipped_reasons:
                why = ", ".join(
                    f"{reason} {n}건" for reason, n in sorted(self.skipped_reasons.items())
                )
                head += f" / 건너뜀 {skipped}건 ({why})"
            else:
                head += f" / 건너뜀 {skipped}건"
        else:
            head = f"- {self.label}: {self.total}건 모두 확인했습니다"
        if self.note:
            head += f" — {self.note}"
        return head


@dataclass
class Result:
    """한 번의 점검 결과 전체."""

    findings: List[Finding] = field(default_factory=list)
    coverage: List[CoverageLine] = field(default_factory=list)
    header_lines: List[str] = field(default_factory=list)  # 리포트 첫머리(읽기 상태)
    info_lines: List[str] = field(default_factory=list)  # [정보] 블록의 자유 문장
    notes: List[str] = field(default_factory=list)  # 파서가 남긴 주의사항
    change_rows: List[List[str]] = field(default_factory=list)  # 변경목록.csv
    added_ref_rows: List[List[str]] = field(default_factory=list)  # 추가문헌.csv
    undecidable: Optional[str] = None  # 판정불가 사유 (있으면 종료코드 3)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def by_severity(self, severity: str) -> List[Finding]:
        return [f for f in self.findings if f.severity == severity]

    @property
    def criticals(self) -> List[Finding]:
        return self.by_severity(CRITICAL)

    @property
    def warnings(self) -> List[Finding]:
        return self.by_severity(WARNING)

    def exit_code(self) -> int:
        if self.undecidable:
            return EXIT_UNDECIDABLE
        if self.criticals:
            return EXIT_CRITICAL
        if self.warnings:
            return EXIT_WARNING
        return EXIT_OK
