"""시나리오 전수 대조 — 축 B(이상치) × C(결측) × D(검정) × E(로그변환).

**two-group·paired 에서 비교용 효과크기는 검정(D)과 무관하게 고정한다.**
순위검정으로 바꿨다고 효과크기 '등급'이 달라졌다고 말하면 그건 단위를 바꿔
놓고(Hedges g ↔ rank-biserial) 크기를 비교하는 것이다. 이 두 설계에서 D 축은
p 값 기계만 바꾸고, 효과크기를 움직이는 것은 B·C·E 와 leave-one-out 이다.
검정 고유 효과크기(rank-biserial 등)는 표에만 남긴다.

**corr 만 예외다.** Pearson r 과 Spearman ρ 는 같은 [−1, 1] 척도라 직접 비교가
되고, 논문에 적는 값도 쓴 검정의 계수다 (`_run_corr` 의 주석 참조).
"""

import math
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from .effects import (
    D_FAMILY,
    R_FAMILY,
    hedges_g_paired,
    hedges_g_two_group,
    matched_rank_biserial,
    rank_biserial_two_group,
)
from .inference import (
    MWU_EXACT_MAX_CELLS,
    WILCOXON_EXACT_MAX_N,
    SingularModel,
    TestResult,
    ancova_baseline,
    mann_whitney_u,
    paired_t_test,
    pearson_r,
    quade_rank_ancova,
    spearman_rho,
    student_t_test,
    welch_t_test,
    wilcoxon_signed_rank,
    mean,
    variance,
)
from .prep import (
    LOG_LEVELS,
    MISSING_LEVELS,
    OUTLIER_LEVELS,
    TEST_LEVELS,
    Prepared,
    SkipScenario,
    prepare,
)
from .spec import Spec, Subject

__all__ = [
    "Axes",
    "ScenarioResult",
    "BASELINE_AXES",
    "grid",
    "grid_description",
    "nonparametric_p_floor",
    "run_scenario",
    "run_grid",
    "effect_family",
]

# 기준선 = 아무것도 흔들지 않은 상태. 사용자가 논문에 쓴 그 분석이다.
BASELINE_AXES = ("없음", "완결자만", "모수", "미적용")


class Axes:
    """시나리오 하나를 정의하는 축 조합."""

    __slots__ = ("outlier", "missing", "test", "log")

    def __init__(self, outlier: str, missing: str, test: str, log: str) -> None:
        self.outlier = outlier
        self.missing = missing
        self.test = test
        self.log = log

    @property
    def key(self) -> Tuple[str, str, str, str]:
        return (self.outlier, self.missing, self.test, self.log)

    @property
    def order(self) -> Tuple[int, int, int, int]:
        """정렬용 고정 순서. **유의성은 여기에 들어가지 않는다.**"""
        return (
            OUTLIER_LEVELS.index(self.outlier),
            MISSING_LEVELS.index(self.missing),
            TEST_LEVELS.index(self.test),
            LOG_LEVELS.index(self.log),
        )

    @property
    def is_baseline(self) -> bool:
        return self.key == BASELINE_AXES

    def label(self, include_missing: bool = True) -> str:
        parts = ["이상치=%s" % self.outlier]
        if include_missing:
            parts.append("결측=%s" % self.missing)
        parts.append("검정=%s" % self.test)
        parts.append("로그=%s" % self.log)
        return " · ".join(parts)

    def __repr__(self) -> str:  # pragma: no cover
        return "Axes(%s)" % ", ".join(self.key)


def nonparametric_p_floor(design: str, n_a: int, n_b: int,
                          exact: bool = True) -> float:
    """비모수 **정확검정**이 원리적으로 도달할 수 있는 최소 양측 p.

    Mann–Whitney 는 2 / C(n1+n2, n1), Wilcoxon 은 2^(1−n) 아래로 내려갈 수 없다.
    n1=n2=3 이면 최소 p = .100 이라 alpha = .05 에서는 **무슨 자료를 넣어도**
    유의해질 수 없다 — 완전히 분리된 두 군에도 "① 유의 → 비유의"가 뜬다.
    그런 시나리오는 자료의 취약성이 아니라 검정의 한계이므로 사유를 남기고
    건너뛴다(축 B 의 `sd_rule_note` 와 같은 처리).

    `exact=False` 는 동점·0차이 때문에 **정규근사** 분기를 타는 경우다. 그때는
    이 하한이 성립하지 않는다 — 실제로 n=3/3 동점 자료에서 p = .047 이 나온다.
    하한을 그대로 적용하면 계산할 수 있었던 시나리오를 거짓 사유로 지운다.
    """
    if not exact:
        return 0.0
    if design == "two-group":
        total = n_a + n_b
        if n_a < 1 or n_b < 1:
            return 1.0
        combinations = math.comb(total, n_a)
        if combinations.bit_length() >= 1024:
            # 배정도로 표현조차 안 될 만큼 크다 = 하한이 사실상 0 이다.
            # 나누려 들면 OverflowError 로 시나리오가 통째로 사라진다(실측 n≥1030).
            return 0.0
        return 2.0 / combinations
    if design == "paired":
        if n_a < 1:
            return 1.0
        if n_a > 1000:
            return 0.0
        return 2.0 ** (1 - n_a)
    return 0.0   # corr(Spearman)은 t 근사라 이런 하한이 없다


def _mwu_will_be_exact(a: Sequence[float], b: Sequence[float]) -> bool:
    """`mann_whitney_u` 가 정확분포 분기를 탈 조건 (동점 없음 + 칸 수 상한)."""
    pooled = list(a) + list(b)
    if len(set(pooled)) != len(pooled):
        return False
    return len(a) * len(b) <= MWU_EXACT_MAX_CELLS


def _wilcoxon_will_be_exact(diffs: Sequence[float]) -> bool:
    """`wilcoxon_signed_rank` 가 정확분포 분기를 탈 조건 (0·동점 없음 + n 상한)."""
    if any(d == 0.0 for d in diffs):
        return False
    magnitudes = [abs(d) for d in diffs]
    if len(set(magnitudes)) != len(magnitudes):
        return False
    return len(diffs) <= WILCOXON_EXACT_MAX_N


def effect_family(design: str) -> str:
    return R_FAMILY if design == "corr" else D_FAMILY


class ScenarioResult:
    """시나리오 1개의 결과. 건너뛴 것도 결과다 — 사유가 남는다."""

    __slots__ = (
        "axes", "computed", "skip_reason", "skip_detail", "test", "effect",
        "native_effect", "native_effect_name", "n", "n_a", "n_b",
        "excluded", "notes", "imputed", "ids",
    )

    def __init__(self, axes: Axes) -> None:
        self.axes = axes
        self.computed = False
        self.skip_reason = ""
        self.skip_detail = ""
        self.test: Optional[TestResult] = None
        self.effect = float("nan")
        self.native_effect = float("nan")
        self.native_effect_name = ""
        self.n = 0
        self.n_a = 0
        self.n_b = 0
        self.excluded: List[Tuple[str, str]] = []
        self.notes: List[str] = []
        self.imputed = 0
        self.ids: List[str] = []

    @property
    def p(self) -> float:
        return self.test.p if self.test is not None else float("nan")

    def __repr__(self) -> str:  # pragma: no cover
        if not self.computed:
            return "ScenarioResult(%r, 건너뜀=%s)" % (self.axes.key, self.skip_reason)
        return "ScenarioResult(%r, p=%.4g, effect=%.4g)" % (
            self.axes.key, self.p, self.effect)


def grid(spec: Spec, use_log: bool = True) -> List[Axes]:
    """축 조합 전수. paired 만 결측 축 3종이고 나머지는 1종이다.

    `use_log=False`(CLI `--no-log`)는 로그변환 축을 통째로 뺀다. ISI(0–28 정수
    척도)처럼 로그변환이 애초에 말이 안 되는 지표에서는 그 축이 리포트 소음의
    대부분을 만들고, 사용자는 매번 그걸 머리로 걸러내야 한다.
    **조용한 절삭이 아니다** — `grid_description()` 이 사실을 그대로 인쇄한다.
    """
    missing_levels = MISSING_LEVELS if spec.design == "paired" else ("완결자만",)
    log_levels = LOG_LEVELS if use_log else (LOG_LEVELS[0],)
    out: List[Axes] = []
    for outlier in OUTLIER_LEVELS:
        for missing in missing_levels:
            for test in TEST_LEVELS:
                for log in log_levels:
                    out.append(Axes(outlier, missing, test, log))
    return out


def grid_description(spec: Spec, use_log: bool = True) -> str:
    missing_n = len(MISSING_LEVELS) if spec.design == "paired" else 1
    suffix = "" if spec.design == "paired" else "(설계상 완결자만 — 축 C 는 paired 전용)"
    log_n = len(LOG_LEVELS) if use_log else 1
    log_suffix = "" if use_log else "(--no-log 로 제외함)"
    return "이상치 %d × 결측 %d%s × 검정 %d × 로그 %d%s = %d" % (
        len(OUTLIER_LEVELS), missing_n, suffix, len(TEST_LEVELS), log_n,
        log_suffix, len(OUTLIER_LEVELS) * missing_n * len(TEST_LEVELS) * log_n,
    )


# ------------------------------------------------------------- 검정 실행


def _run_two_group(prepared: Prepared, spec: Spec, axes: Axes,
                   equal_var: bool) -> Tuple[TestResult, float, float, str]:
    a, b = prepared.a, prepared.b
    # 공변량이 있으면 비모수 축은 Quade 순위 ANCOVA(순위 잔차 t)라 아래 하한이
    # 성립하지 않는다 — Mann–Whitney 의 하한을 들이대면 멀쩡한 시나리오가 지워진다.
    if axes.test == "비모수" and not spec.covariate:
        floor = nonparametric_p_floor(
            "two-group", len(a), len(b),
            exact=_mwu_will_be_exact(a, b))
        # 유의 판정이 `p < alpha` 이므로 floor == alpha 도 도달 불가능이다.
        if floor >= spec.alpha:
            raise SkipScenario(
                "비모수 검정력 부족",
                "n=%d/%d 에서 도달 가능한 최소 양측 p = %.3f ≥ alpha %.3f"
                % (len(a), len(b), floor, spec.alpha))
    if spec.covariate:
        # 비교용 효과크기는 언제나 보정된 모형에서 뽑는다(D 축과 무관).
        fit = ancova_baseline(a, prepared.cov_a, b, prepared.cov_b)
        n = len(a) + len(b)
        df = n - 3
        j = 1.0 - 3.0 / (4.0 * df - 1.0) if 4.0 * df - 1.0 > 0 else 1.0
        effect = j * fit.extra["보정된차이"] / (fit.extra["MSE"] ** 0.5)
        if axes.test == "모수":
            return fit, effect, effect, "보정 Hedges g"
        quade = quade_rank_ancova(a, prepared.cov_a, b, prepared.cov_b)
        return quade, effect, quade.extra["순위잔차차이"], "순위잔차 평균차"
    effect = hedges_g_two_group(a, b)
    if axes.test == "모수":
        test = student_t_test(a, b) if equal_var else welch_t_test(a, b)
        return test, effect, effect, "Hedges g"
    return mann_whitney_u(a, b), effect, rank_biserial_two_group(a, b), "rank-biserial"


def _run_paired(prepared: Prepared, spec: Spec,
                axes: Axes) -> Tuple[TestResult, float, float, str]:
    pre, post = prepared.pre, prepared.post
    if axes.test == "비모수":
        diffs = [q - p for p, q in zip(pre, post)]
        nonzero = [d for d in diffs if d != 0.0]
        floor = nonparametric_p_floor(
            "paired", len(nonzero), 0,
            exact=_wilcoxon_will_be_exact(diffs))
        if floor >= spec.alpha:
            raise SkipScenario(
                "비모수 검정력 부족",
                "0 이 아닌 차이 %d쌍에서 도달 가능한 최소 양측 p = %.3f ≥ alpha %.3f"
                % (len(nonzero), floor, spec.alpha))
    effect = hedges_g_paired(pre, post)
    if axes.test == "모수":
        return paired_t_test(pre, post), effect, effect, "Hedges g(d_z)"
    return (wilcoxon_signed_rank(pre, post), effect,
            matched_rank_biserial(pre, post), "matched rank-biserial")


def _run_corr(prepared: Prepared, axes: Axes) -> Tuple[TestResult, float, float, str]:
    """corr 만은 **검정 고유 효과크기를 그대로 비교용으로 쓴다.**

    Pearson r 과 Spearman ρ 는 둘 다 [−1, 1] 의 상관계수라 직접 비교가 되고,
    실제로 논문에 적는 값도 쓴 검정의 계수다. 여기서 Pearson 으로 고정하면
    "Spearman 은 단조변환에 불변인데 로그를 씌웠더니 효과크기가 변했다"는
    유령 뒤집힘이 생긴다(p 는 한 자리도 안 바뀌는데).
    """
    x, y = prepared.x, prepared.y
    if axes.test == "모수":
        pearson = pearson_r(x, y)
        return pearson, pearson.statistic, pearson.statistic, "Pearson r"
    spearman = spearman_rho(x, y)
    return spearman, spearman.statistic, spearman.statistic, "Spearman rho"


def run_scenario(
    subjects: Sequence[Subject],
    spec: Spec,
    group_levels: Tuple[str, ...],
    axes: Axes,
    equal_var: bool = False,
) -> ScenarioResult:
    """축 조합 하나를 실제로 계산한다. 못 하면 건너뛴 결과로 돌려준다."""
    result = ScenarioResult(axes)
    try:
        prepared = prepare(subjects, spec, group_levels,
                           axes.outlier, axes.missing, axes.log)
    except SkipScenario as skip:
        result.skip_reason = skip.reason
        result.skip_detail = skip.detail
        return result
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        # 준비 단계(평균·표준편차·분위수)도 극단값에서 실패할 수 있다.
        # 트레이스백으로 죽는 대신 이 시나리오만 사유와 함께 건너뛴다.
        result.skip_reason = "전처리 불가(수치범위 초과 등)"
        result.skip_detail = str(exc)
        return result

    result.excluded = list(prepared.excluded)
    result.notes = list(prepared.notes)
    result.imputed = prepared.imputed
    result.ids = list(prepared.ids)
    result.n = prepared.n
    result.n_a = len(prepared.a)
    result.n_b = len(prepared.b)

    try:
        if spec.design == "two-group":
            test, effect, native, native_name = _run_two_group(
                prepared, spec, axes, equal_var)
        elif spec.design == "paired":
            test, effect, native, native_name = _run_paired(prepared, spec, axes)
        else:
            test, effect, native, native_name = _run_corr(prepared, axes)
    except SkipScenario as skip:
        result.skip_reason = skip.reason
        result.skip_detail = skip.detail
        return result
    except SingularModel as exc:
        result.skip_reason = "공변량 모형 특이"
        result.skip_detail = str(exc)
        return result
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        result.skip_reason = "검정 불가(분산 0·수치범위 초과 등)"
        result.skip_detail = str(exc)
        return result

    result.test = test
    result.effect = effect
    result.native_effect = native
    result.native_effect_name = native_name
    result.computed = True
    return result


def run_grid(
    subjects: Sequence[Subject],
    spec: Spec,
    group_levels: Tuple[str, ...],
    equal_var: bool = False,
    use_log: bool = True,
) -> List[ScenarioResult]:
    """전수 실행. 순서는 축 순서 그대로 — **유의성으로 정렬하지 않는다.**"""
    return [run_scenario(subjects, spec, group_levels, axes, equal_var)
            for axes in grid(spec, use_log)]


def baseline_of(results: Sequence[ScenarioResult]) -> Optional[ScenarioResult]:
    for r in results:
        if r.axes.is_baseline:
            return r
    return None
