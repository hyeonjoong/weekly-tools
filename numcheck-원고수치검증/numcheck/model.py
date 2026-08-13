"""리포트의 자료형 — claim(검사 후보) 과 finding(지적).

설계상 중요한 점 하나: **모든 claim 후보가 기록된다.** 검사한 것도, 건너뛴 것도,
건너뛴 사유까지. 몇 개를 못 봤는지 말하지 않는 체커는 "이상 없음"이라는 말로
거짓말을 하게 되고, 그러면 있으나 마나 한 물건이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

__all__ = ["Claim", "Finding", "Report", "LEVELS", "level_rank", "SKIP_REASONS"]

# 등급 — 판정 근거는 README 에 명시된다.
#   치명  산술이 확실히 어긋난다. 반올림 관례를 모두 허용해도 맞지 않는다.
#   경고  어긋나 보이지만 정당한 사유가 있을 수 있다(단측검정·보정·추정한 분모 등).
#   정보  검사하지 못했지만 사용자가 지정하면 검사할 수 있다.
LEVELS = ("치명", "경고", "정보")


def level_rank(level: str) -> int:
    try:
        return LEVELS.index(level)
    except ValueError:  # pragma: no cover - 방어
        return len(LEVELS)


# 건너뛴 사유의 표준 문구(리포트 집계에 쓰이므로 문자열을 임의로 늘리지 않는다)
SKIP_REASONS = {
    "no_denominator": "분모 없음",
    "unknown_scale": "척도 미상",
    "ambiguous": "표기 불명확",
    "reference": "참고문헌 인용",
    "no_statistic": "검정통계량 없음",
    "no_pvalue": "p 미보고",
    "no_df": "자유도 없음",
    "no_n": "표본수 없음",
    "no_power": "판별력 없음",
    "out_of_range": "값이 범위 밖",
    "derived_mean": "원자료 평균 아님",
    "not_a_proportion": "비율이 아님",
}


@dataclass
class Finding:
    """사람에게 보여줄 지적 한 건."""

    level: str
    line_no: int
    section: str
    item: str            # 항목 이름 (예: "비율 재계산")
    quote: str           # 원문 발췌
    reported: str        # 보고값
    recomputed: str      # 재계산값
    message: str         # 왜 틀렸는지 한 줄 (한국어)
    verdict: str = "불일치"
    downgraded: str = ""  # 치명 → 경고 로 강등한 사유(있으면)
    message_en: str = ""  # --lang en 용. 비어 있으면 한국어를 그대로 보여준다.

    def sort_key(self):
        return (level_rank(self.level), self.line_no, self.item)


@dataclass
class Claim:
    """검사 후보 하나. 검사했든 못 했든 전부 남는다."""

    line_no: int
    section: str
    kind: str            # proportion | pvalue | statistic | nsum | grim | delta | ci | wording
    item: str            # 한글 항목명
    quote: str
    checked: bool = False
    skip_reason: str = ""       # SKIP_REASONS 의 값
    reported: str = ""
    recomputed: str = ""
    verdict: str = ""           # 일치 | 불일치 | 건너뜀
    note: str = ""

    def as_row(self) -> Dict[str, str]:
        return {
            "줄번호": str(self.line_no),
            "절": self.section,
            "항목": self.item,
            "원문": self.quote,
            "처리": "재계산" if self.checked else "건너뜀",
            "사유": self.skip_reason,
            "보고값": self.reported,
            "재계산값": self.recomputed,
            "판정": self.verdict,
            "비고": self.note,
        }


@dataclass
class Report:
    """한 번의 실행 결과 전체."""

    path: str
    fmt: str
    line_label: str = "줄"
    word_count: int = 0
    table_rows: int = 0
    notes: List[str] = field(default_factory=list)
    # 원고 일부를 잘라 냈다면 '검사했는데 없었다' 를 말할 자격이 없다.
    truncated: bool = False
    claims: List[Claim] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)

    # -- 집계 ---------------------------------------------------------------
    @property
    def n_candidates(self) -> int:
        return len(self.claims)

    @property
    def n_checked(self) -> int:
        return sum(1 for c in self.claims if c.checked)

    @property
    def n_skipped(self) -> int:
        return sum(1 for c in self.claims if not c.checked)

    def skip_breakdown(self) -> List[tuple]:
        counts: Dict[str, int] = {}
        for c in self.claims:
            if not c.checked:
                counts[c.skip_reason or "기타"] = counts.get(c.skip_reason or "기타", 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def by_level(self, level: str) -> List[Finding]:
        return [f for f in self.findings if f.level == level]

    def counts(self) -> Dict[str, int]:
        return {level: len(self.by_level(level)) for level in LEVELS}

    def sorted_findings(self) -> List[Finding]:
        return sorted(self.findings, key=lambda f: f.sort_key())

    def exit_code(self, min_checked: int = 5) -> int:
        """0 이상 없음 · 1 치명 · 2 경고만 · 3 원고를 제대로 읽지 못함."""
        if self.n_checked < min_checked:
            return 3
        # 잘라 낸 뒷부분에 오류가 있었는지는 알 수 없다. 노트에 적어 두는
        # 것만으로는 종료코드 0 이 '이상 없음' 이라고 거짓말하는 것을 막지 못한다.
        if self.truncated and not (self.by_level("치명") or self.by_level("경고")):
            return 3
        if self.by_level("치명"):
            return 1
        if self.by_level("경고"):
            return 2
        return 0

    def add(self, claim: Claim, finding: Optional[Finding] = None) -> None:
        self.claims.append(claim)
        if finding is not None:
            self.findings.append(finding)
