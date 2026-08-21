"""전체 조립 — 기준선 → 시나리오 전수 → 뒤집힘 판정 → leave-one-out → 등급.

여기서 나오는 `Analysis` 객체 하나가 리포트·CSV·종료코드의 유일한 출처다.
같은 입력이면 언제나 같은 `Analysis` 가 나온다(난수 없음, 정렬 안정).
"""

import math
from typing import Dict, List, Optional, Sequence, Tuple

from .loo import (
    DEFAULT_LOO_BUDGET,
    DEFAULT_LOO_MAX_N,
    LooRun,
    plan_loo,
    run_loo,
)
from .scenarios import (
    Axes,
    ScenarioResult,
    baseline_of,
    effect_family,
    grid,
    grid_description,
    run_grid,
)
from .effects import sign_flip_floor
from .spec import Dataset, MIN_VALID_N, Spec
from .verdict import (
    CRITICAL,
    Flip,
    Verdict,
    WARNING,
    judge_flips,
    order_key,
    severity_of,
)

__all__ = ["Judged", "Analysis", "analyse", "MIN_COMPUTED_SCENARIOS"]

# 계산된 시나리오가 이보다 적으면 "흔들어 봤다"고 말할 수 없다 → 판정불가.
MIN_COMPUTED_SCENARIOS = 5


class Judged:
    """시나리오 결과 + 기준선 대비 판정."""

    __slots__ = ("result", "flips")

    def __init__(self, result: ScenarioResult, flips: List[Flip]) -> None:
        self.result = result
        self.flips = flips

    @property
    def severity(self) -> str:
        return severity_of(self.flips)

    @property
    def axes(self) -> Axes:
        return self.result.axes

    @property
    def sort_key(self):
        return order_key(self.severity, self.result.computed, self.axes.order)


class Analysis:
    """리포트가 필요로 하는 모든 사실. 여기 없는 값은 리포트에도 없다."""

    __slots__ = (
        "dataset", "spec", "judged", "baseline", "verdict", "loo_baseline",
        "loo_extra", "loo_notes", "coverage", "equal_var", "undecidable_reason",
        "valid_n", "grid_text", "duplicate_note", "log_axis_used",
        "writes_files",
    )

    def __init__(self) -> None:
        self.dataset: Optional[Dataset] = None
        self.spec: Optional[Spec] = None
        self.judged: List[Judged] = []
        self.baseline: Optional[ScenarioResult] = None
        self.verdict: Optional[Verdict] = None
        self.loo_baseline: Optional[LooRun] = None
        self.loo_extra: List[LooRun] = []
        self.loo_notes: List[str] = []
        self.coverage: Dict[str, int] = {}
        self.equal_var = False
        self.undecidable_reason = ""
        self.valid_n = 0
        self.grid_text = ""
        self.duplicate_note = ""
        self.log_axis_used = True
        # --no-files 로 돌리면 "시나리오표.csv 참조"라고 안내해 봐야 그 파일이 없다.
        self.writes_files = True

    # ------------------------------------------------------------- 파생값

    @property
    def total(self) -> int:
        return len(self.judged)

    @property
    def computed(self) -> int:
        return sum(1 for j in self.judged if j.result.computed)

    @property
    def skipped(self) -> int:
        return self.total - self.computed

    @property
    def ordered(self) -> List[Judged]:
        """뒤집힘 여부 → 축 순서. **유의성 순이 아니다.**"""
        return sorted(self.judged, key=lambda j: j.sort_key)

    @property
    def flipped(self) -> List[Judged]:
        return [j for j in self.ordered
                if j.result.computed and j.flips and not j.axes.is_baseline]

    @property
    def criticals(self) -> List[Judged]:
        return [j for j in self.flipped if j.severity == CRITICAL]

    @property
    def warnings(self) -> List[Judged]:
        return [j for j in self.flipped if j.severity == WARNING]

    @property
    def silent_effect_shifts(self) -> List[Judged]:
        """뒤집힘으로 세지는 **않지만** 숨기면 안 되는 효과크기 이동.

        기준선도 시나리오도 비유의라 ③④ 판정을 하지 않은 경우인데, 효과크기가
        부호까지 바뀔 만큼 움직인 것들. "방향이 유지되었다"고 말하면 거짓말이
        되므로 리포트가 개수와 조합을 밝힌다.
        """
        base = self.baseline
        if base is None or not base.computed or self.undecidable_reason:
            return []
        if math.isnan(base.p) or base.p < self.spec.alpha:
            return []
        family = effect_family(self.spec.design)
        floor = sign_flip_floor(family)
        out: List[Judged] = []
        for judged in self.ordered:
            r = judged.result
            if r.axes.is_baseline or not r.computed or judged.flips:
                continue
            if math.isnan(r.p) or r.p < self.spec.alpha:
                continue
            if math.isnan(r.effect) or math.isnan(base.effect):
                continue
            if ((base.effect > 0) != (r.effect > 0)
                    and min(abs(base.effect), abs(r.effect)) >= floor):
                out.append(judged)
        return out

    @property
    def solo_flippers(self):
        return self.loo_baseline.solo_flippers if self.loo_baseline else []

    @property
    def loo_warned(self):
        return self.loo_baseline.warned if self.loo_baseline else []

    @property
    def exit_code(self) -> int:
        if self.undecidable_reason:
            return 3
        if self.verdict and self.verdict.total_critical > 0:
            return 1
        return 0


def _count_complete(dataset: Dataset) -> int:
    cols = dataset.spec.numeric_columns
    return sum(1 for s in dataset.subjects
               if all(s.get(c) is not None for c in cols))


def analyse(
    dataset: Dataset,
    equal_var: bool = False,
    loo_max_n: int = DEFAULT_LOO_MAX_N,
    loo_budget: int = DEFAULT_LOO_BUDGET,
    use_log: bool = True,
) -> Analysis:
    spec = dataset.spec
    analysis = Analysis()
    analysis.dataset = dataset
    analysis.spec = spec
    analysis.equal_var = equal_var
    analysis.log_axis_used = use_log
    analysis.grid_text = grid_description(spec, use_log)

    results = run_grid(dataset.subjects, spec, dataset.group_levels, equal_var,
                       use_log)
    baseline = baseline_of(results)
    analysis.baseline = baseline
    family = effect_family(spec.design)

    for result in results:
        if baseline is None or not baseline.computed or result.axes.is_baseline:
            flips: List[Flip] = []
        elif not result.computed:
            flips = []
        else:
            flips = judge_flips(
                baseline.p, baseline.effect, result.p, result.effect,
                spec.alpha, family,
                same_scale=result.axes.log == baseline.axes.log)
        analysis.judged.append(Judged(result, flips))

    # 커버리지 자백용 사유별 집계
    coverage: Dict[str, int] = {}
    for j in analysis.judged:
        if not j.result.computed:
            reason = j.result.skip_reason or "사유 미상"
            coverage[reason] = coverage.get(reason, 0) + 1
    analysis.coverage = coverage

    analysis.valid_n = (baseline.n if baseline is not None and baseline.computed
                        else _count_complete(dataset))

    # ---- 판정불가 규칙 (다른 무엇보다 우선한다) --------------------------
    reasons: List[str] = []
    if baseline is None or not baseline.computed:
        if baseline is None:
            detail = "기준선 시나리오 없음"
        else:
            detail = baseline.skip_reason
            if baseline.skip_detail:
                detail += " — %s" % baseline.skip_detail
        reasons.append("기준선(주 분석)을 계산할 수 없음: %s" % detail)
    if analysis.valid_n < MIN_VALID_N:
        reasons.append("유효 N = %d < %d" % (analysis.valid_n, MIN_VALID_N))
    if analysis.computed < MIN_COMPUTED_SCENARIOS:
        reasons.append("계산된 시나리오 %d개 < %d개"
                       % (analysis.computed, MIN_COMPUTED_SCENARIOS))

    if reasons:
        analysis.undecidable_reason = " · ".join(reasons)
        analysis.verdict = Verdict(0, 0, 0, 0, analysis.computed, analysis.total,
                                   analysis.undecidable_reason)
        analysis.loo_notes.append(
            "판정불가이므로 leave-one-out 을 돌리지 않았다(뒤집힘 건수가 의미 없다)."
        )
        return analysis

    # ---- leave-one-out (억제 규칙 적용) ----------------------------------
    flipped_axes = [j.axes for j in analysis.flipped]
    do_loo, extra_axes, note = plan_loo(len(dataset.subjects), flipped_axes,
                                        loo_max_n, loo_budget)
    if note:
        analysis.loo_notes.append(note)
    if do_loo:
        analysis.loo_baseline = run_loo(dataset.subjects, spec, dataset.group_levels,
                                        baseline.axes, baseline, equal_var)
        by_key = {j.axes.key: j.result for j in analysis.judged}
        for axes in extra_axes:
            reference = by_key[axes.key]
            analysis.loo_extra.append(
                run_loo(dataset.subjects, spec, dataset.group_levels, axes,
                        reference, equal_var)
            )

    critical_subjects = len(analysis.solo_flippers)
    warning_subjects = len(analysis.loo_warned)
    analysis.verdict = Verdict(
        critical_scenarios=len(analysis.criticals),
        warning_scenarios=len(analysis.warnings),
        critical_subjects=critical_subjects,
        warning_subjects=warning_subjects,
        computed=analysis.computed,
        total=analysis.total,
    )
    return analysis
