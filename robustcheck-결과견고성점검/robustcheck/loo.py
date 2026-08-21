"""Leave-one-out — 피험자를 한 명씩 빼고 다시 계산한다.

**조합 폭발 억제 규칙 (코드와 리포트 양쪽에 명시된다)**:
leave-one-out 은 36개 시나리오 전부와 곱하지 않는다. 주 시나리오(기준선)
1개와, *뒤집힘이 발생한 시나리오*에 대해서만 추가로 돌린다. 그래도 예산을
넘으면 **몇 개를 왜 못 돌렸는지 자백한다** — 조용히 자르지 않는다.
"""

import math
from typing import Dict, List, Optional, Sequence, Tuple

from .scenarios import Axes, ScenarioResult, effect_family, run_scenario
from .spec import Spec, Subject
from .verdict import CRITICAL, Flip, judge_flips, severity_of

__all__ = [
    "LooEntry",
    "LooRun",
    "run_loo",
    "LOO_RULE_TEXT",
    "DEFAULT_LOO_MAX_N",
    "DEFAULT_LOO_BUDGET",
]

# 피험자 수가 이보다 많으면 leave-one-out 은 의미도 없고(1명 빼서 뒤집힐 리
# 없다) 시간만 먹는다. 넘으면 **건너뛰고 그 사실을 자백한다.**
DEFAULT_LOO_MAX_N = 2000
# 총 재계산 횟수 예산. 뒤집힘 시나리오가 많을 때 폭주를 막는다.
DEFAULT_LOO_BUDGET = 20_000

LOO_RULE_TEXT = (
    "leave-one-out 은 주 시나리오(기준선)와 **뒤집힘이 발생한 시나리오**에만 "
    "적용했다. 시나리오 전부와 곱하지 않는다(조합 폭발 억제)."
)


class LooEntry:
    """피험자 1명을 뺐을 때의 결과."""

    __slots__ = ("sid", "computed", "skip_reason", "p", "effect",
                 "delta_p", "delta_effect", "flips", "in_analysis")

    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.computed = False
        self.skip_reason = ""
        self.p = float("nan")
        self.effect = float("nan")
        self.delta_p = float("nan")
        self.delta_effect = float("nan")
        self.flips: List[Flip] = []
        self.in_analysis = True

    @property
    def severity(self) -> str:
        return severity_of(self.flips)

    @property
    def solo_flip(self) -> bool:
        """이 한 명이 단독으로 결론을 뒤집는가 (치명 등급)."""
        return any(f.severity == CRITICAL for f in self.flips)

    def __repr__(self) -> str:  # pragma: no cover
        return "LooEntry(%s, Δp=%.4g)" % (self.sid, self.delta_p)


class LooRun:
    """한 시나리오에 대한 leave-one-out 전수."""

    __slots__ = ("axes", "reference", "entries", "skipped_reason")

    def __init__(self, axes: Axes, reference: ScenarioResult) -> None:
        self.axes = axes
        self.reference = reference
        self.entries: List[LooEntry] = []
        self.skipped_reason = ""

    @property
    def solo_flippers(self) -> List[LooEntry]:
        """영향이 큰 순 — 입력 행 순서에 따라 목록이 흔들리지 않게 고정한다."""
        found = [e for e in self.entries if e.solo_flip]
        found.sort(key=lambda e: (-abs(e.delta_p) if e.computed
                                  and not math.isnan(e.delta_p) else 0.0, e.sid))
        return found

    @property
    def warned(self) -> List[LooEntry]:
        found = [e for e in self.entries if e.flips and not e.solo_flip]
        found.sort(key=lambda e: (-abs(e.delta_p) if e.computed
                                  and not math.isnan(e.delta_p) else 0.0, e.sid))
        return found

    def top(self, k: int = 5) -> List[LooEntry]:
        """|Δp| 가 큰 순. 동점은 subject_id 로 안정 정렬한다."""
        usable = [e for e in self.entries if e.computed and not math.isnan(e.delta_p)]
        usable.sort(key=lambda e: (-abs(e.delta_p), e.sid))
        return usable[:k]


def run_loo(
    subjects: Sequence[Subject],
    spec: Spec,
    group_levels: Tuple[str, ...],
    axes: Axes,
    reference: ScenarioResult,
    equal_var: bool = False,
) -> LooRun:
    """`axes` 시나리오에서 피험자를 한 명씩 빼고 전수 재계산."""
    run = LooRun(axes, reference)
    family = effect_family(spec.design)
    analysed = set(reference.ids)
    for i, subject in enumerate(subjects):
        entry = LooEntry(subject.sid)
        entry.in_analysis = subject.sid in analysed
        reduced = list(subjects[:i]) + list(subjects[i + 1:])
        result = run_scenario(reduced, spec, group_levels, axes, equal_var)
        if not result.computed:
            entry.skip_reason = result.skip_reason
            run.entries.append(entry)
            continue
        entry.computed = True
        entry.p = result.p
        entry.effect = result.effect
        entry.delta_p = result.p - reference.p
        entry.delta_effect = result.effect - reference.effect
        # 같은 시나리오 안에서 한 명만 뺀 것이므로 척도는 언제나 같다.
        entry.flips = judge_flips(reference.p, reference.effect,
                                  result.p, result.effect, spec.alpha, family,
                                  same_scale=True)
        run.entries.append(entry)
    return run


def plan_loo(
    n_subjects: int,
    flipped_axes: Sequence[Axes],
    loo_max_n: int = DEFAULT_LOO_MAX_N,
    budget: int = DEFAULT_LOO_BUDGET,
) -> Tuple[bool, List[Axes], str]:
    """(기준선 LOO 실행 여부, 추가로 돌릴 시나리오, 자백 문구)."""
    if n_subjects > loo_max_n:
        return False, [], (
            "피험자 %d명 > --loo-max-n %d — leave-one-out 을 **돌리지 않았다.**"
            % (n_subjects, loo_max_n)
        )
    if n_subjects <= 0:
        return False, [], "유효 피험자가 없어 leave-one-out 을 돌리지 않았다."
    if budget < n_subjects:
        # 기준선 전수(N회)조차 예산을 넘는다 — 반만 돌리고 입 다무는 대신 멈춘다.
        return False, [], (
            "기준선 leave-one-out 에 %d회가 필요한데 --loo-budget 이 %d회다 — "
            "**돌리지 않았다.**" % (n_subjects, budget)
        )
    allowed = max(0, budget // max(1, n_subjects) - 1)
    extra = list(flipped_axes[:allowed])
    note = ""
    if len(flipped_axes) > len(extra):
        note = (
            "뒤집힘 시나리오 %d개 중 %d개에만 leave-one-out 을 돌렸다 "
            "(재계산 예산 %d회 상한). 나머지 %d개는 돌리지 못했다."
            % (len(flipped_axes), len(extra), budget,
               len(flipped_axes) - len(extra))
        )
    return True, extra, note
