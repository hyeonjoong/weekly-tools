"""선정/제외기준 재점검.

프로토콜 JSON 의 단순 규칙을 피험자 CSV 에 그대로 다시 적용해, *무작위배정된*
피험자 중 위반자를 색출한다. (스크린 실패자가 기준에 안 맞는 것은 당연하므로
점검 대상이 아니다.)

원칙: 항목 열이 CSV 에 없거나 값이 비어 있거나 해석이 안 되면 위반이 아니라
'판정불가'다. 없는 열을 위반으로 세지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .protocol import Criterion, Protocol
from .tables import PLAIN_NUM as _PLAIN_NUM
from .tables import Subject


@dataclass
class CriteriaFinding:
    subject: str
    criterion: Criterion
    actual: str
    verdict: str          # "위반" | "판정불가"
    detail: str


@dataclass
class CriteriaResult:
    findings: List[CriteriaFinding] = field(default_factory=list)
    missing_columns: List[str] = field(default_factory=list)  # CSV 에 아예 없는 항목
    n_checked: int = 0    # 피험자 × 기준 판정 건수
    skipped: Optional[str] = None  # 통째로 건너뛴 사유

    @property
    def violations(self) -> List[CriteriaFinding]:
        return [f for f in self.findings if f.verdict == "위반"]

    @property
    def unjudgeable(self) -> List[CriteriaFinding]:
        return [f for f in self.findings if f.verdict == "판정불가"]

    def violators(self) -> List[str]:
        out = []
        for f in self.violations:
            if f.subject not in out:
                out.append(f.subject)
        return out


def _compare(raw: str, crit: Criterion) -> Optional[bool]:
    """조건 성립 여부. 해석 불가면 None."""
    text = raw.strip()
    if not text:
        return None
    if isinstance(crit.value, bool):
        return None  # 프로토콜 검증에서 이미 막지만, 방어적으로
    if isinstance(crit.value, (int, float)):
        # float() 만으로 거르면 'nan'/'inf'/'1_0' 이 통과한다. nan 은 모든 비교가
        # False 라서 선정기준에서 '위반'으로 둔갑하고(제외기준에서는 조용히 통과),
        # '1_0' 은 10.0 이 된다 — 없는 위반을 만들어 PP 집합까지 흔든다.
        # pandas 가 결측을 'nan' 으로 내보내므로 실제로 자주 만난다. (B11 과 같은 종류)
        if not _PLAIN_NUM.match(text):
            return None
        try:
            actual = float(text)
        except ValueError:
            return None
        target = float(crit.value)
        return {
            ">=": actual >= target, "<=": actual <= target,
            ">": actual > target, "<": actual < target,
            "==": actual == target, "!=": actual != target,
        }[crit.op]
    # 문자열 기준: ==/!= 만 의미가 있다
    if crit.op == "==":
        return text == str(crit.value)
    if crit.op == "!=":
        return text != str(crit.value)
    return None  # 문자열에 대소 비교는 하지 않는다


def recheck(subjects: Optional[List[Subject]], protocol: Protocol) -> CriteriaResult:
    """선정/제외기준을 무작위배정자에게 다시 적용해 위반자를 색출한다.

    항목 열이 없거나 값이 비었거나 해석이 안 되면 위반이 아니라 판정불가다.
    (스크린 실패자가 기준에 안 맞는 것은 당연하므로 대상에서 뺀다.)
    """
    res = CriteriaResult()
    crits = protocol.inclusion + protocol.exclusion
    if not crits:
        res.skipped = "프로토콜에 선정/제외기준이 없음"
        return res
    if subjects is None:
        res.skipped = "피험자.csv 없음"
        return res

    randomized = [s for s in subjects if s.randomized and not s.duplicated]
    if not randomized:
        res.skipped = "무작위배정된 피험자가 없음"
        return res

    present_cols = set()
    for s in randomized:
        present_cols.update(s.extras.keys())
    for crit in crits:
        if crit.item not in present_cols:
            if crit.item not in res.missing_columns:
                res.missing_columns.append(crit.item)

    for s in randomized:
        for crit in crits:
            if crit.item in res.missing_columns:
                continue  # 열 자체가 없음 — 피험자별로 반복하지 않고 열 단위로 자백
            raw = s.extras.get(crit.item, "")
            met = _compare(raw, crit)
            if met is None:
                res.findings.append(CriteriaFinding(
                    subject=s.sid, criterion=crit, actual=raw, verdict="판정불가",
                    detail=(f"{crit.item} 값 없음" if not raw.strip()
                            else f"{crit.item} = {raw!r} 해석 불가"),
                ))
                continue
            res.n_checked += 1
            violated = (not met) if crit.kind == "선정" else met
            if violated:
                label = "선정기준" if crit.kind == "선정" else "제외기준"
                res.findings.append(CriteriaFinding(
                    subject=s.sid, criterion=crit, actual=raw, verdict="위반",
                    detail=f"{crit.item} = {raw} ({label} {crit.op} {crit.value})",
                ))
    return res
