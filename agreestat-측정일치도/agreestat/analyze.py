"""Orchestration: run the full agreement analysis and assemble a result object.

Given paired measurements (method A vs method B on the same subjects, optionally
with a subject id), :func:`analyze` computes Bland-Altman, ICC(2,1) & ICC(3,1),
Lin's CCC, repeatability (when replicates exist), and Pearson r / paired t for
context, and collects interpretive warnings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import agreement, regression
from .agreement import (
    BlandAltmanResult,
    CCCResult,
    ICCResult,
    PairedTResult,
    PearsonResult,
    RepeatabilityResult,
    RepeatedMeasuresBA,
    interpret_icc,
)
from .regression import DemingResult, PassingBablokResult

__all__ = ["AnalysisResult", "analyze"]


@dataclass
class AnalysisResult:
    name_a: str
    name_b: str
    n: int
    dropped: int
    alpha: float
    mode: str
    # descriptives
    mean_a: float
    sd_a: float
    mean_b: float
    sd_b: float
    # blocks
    ba: BlandAltmanResult
    icc21: ICCResult
    icc31: ICCResult
    ccc: CCCResult
    repeat: RepeatabilityResult
    pearson: PearsonResult
    paired: PairedTResult
    reported_icc: str = "ICC(2,1)"  # which model to headline
    warnings: List[str] = field(default_factory=list)
    # optional pre-specified clinical acceptance limits for the LoA
    accept_lower: Optional[float] = None
    accept_upper: Optional[float] = None
    interchangeable: Optional[bool] = None
    # repeated-measures LoA (when a subject column with replicates exists)
    rm_ba: Optional[RepeatedMeasuresBA] = None
    # method-comparison regression (CLSI EP09): test A regressed on reference B
    deming: Optional[DemingResult] = None
    passing_bablok: Optional[PassingBablokResult] = None
    # LoA-CI precision / required-n guidance
    precision_target_hw: Optional[float] = None
    precision_required_n: Optional[int] = None
    precision_required_n_approx: Optional[float] = None


def _constant(x: Sequence[float]) -> bool:
    return all(v == x[0] for v in x)


def analyze(a: Sequence[float], b: Sequence[float],
            subjects: Optional[Sequence[str]] = None,
            name_a: str = "A", name_b: str = "B",
            alpha: float = 0.05, mode: str = "absolute",
            dropped: int = 0,
            accept: Optional[Tuple[float, float]] = None,
            nonfinite: int = 0,
            extra_warnings: Optional[Sequence[str]] = None,
            target_loa_hw: Optional[float] = None,
            deming_lambda: float = 1.0,
            decision_point: Optional[float] = None) -> AnalysisResult:
    """Run every agreement statistic and return an :class:`AnalysisResult`.

    ``accept`` = (lower, upper) pre-specified clinically acceptable difference;
    if given, the 95% LoA are compared against it to yield an interchangeability
    verdict. ``nonfinite`` / ``extra_warnings`` surface data-loading notes.
    """
    a = [float(v) for v in a]
    b = [float(v) for v in b]
    n = len(a)
    if len(b) != n:
        raise ValueError("method A and method B must have the same length")
    if n < 2:
        raise ValueError("need at least 2 paired observations")

    warnings: List[str] = list(extra_warnings or [])
    if nonfinite:
        warnings.append(
            f"무한대(inf)·비정상 수치 {nonfinite}쌍을 제외했습니다. "
            "원자료에 inf/1e999 같은 값이 없는지 확인하세요.")
    a_const = _constant(a)
    b_const = _constant(b)
    if a_const:
        warnings.append(f"'{name_a}' 값이 모두 동일합니다(분산 0). "
                        "ICC/CCC/Pearson이 정의되지 않을 수 있습니다.")
    if b_const:
        warnings.append(f"'{name_b}' 값이 모두 동일합니다(분산 0). "
                        "ICC/CCC/Pearson이 정의되지 않을 수 있습니다.")
    if n < 10:
        warnings.append(f"표본이 작습니다(n={n}). 신뢰구간이 넓고 "
                        "LoA/ICC/CCC 추정이 불안정할 수 있습니다.")

    if mode == "percent":
        means = [(x + y) / 2.0 for x, y in zip(a, b)]
        rng = max(means) - min(means) if means else 0.0
        small = [m for m in means if rng > 0 and abs(m) < 0.05 * rng]
        if small:
            warnings.append(
                "백분율(--percent) 모드인데 평균이 0에 가까운 쌍이 있어 "
                "백분율 차이가 과도하게 커질 수 있습니다. 절대(absolute) 모드를 "
                "고려하세요.")
        if any(m < 0 for m in means) and any(m > 0 for m in means):
            warnings.append(
                "백분율(--percent) 모드에서 평균값의 부호가 섞여 있습니다. "
                "백분율 차이는 양의 비율척도를 가정하므로 해석에 주의하세요.")

    ba = agreement.bland_altman(a, b, alpha=alpha, mode=mode)
    if ba.prop_bias:
        warnings.append(
            "비례 편향(proportional bias)이 감지되었습니다: 차이가 측정값 크기에 "
            f"따라 변합니다 (기울기={ba.prop_slope:.4f}, p={_p(ba.prop_pvalue)}). "
            "단일 bias/LoA 해석에 주의하고, 백분율(--percent) 또는 회귀 기반 "
            "LoA를 고려하세요.")

    rows = [[x, y] for x, y in zip(a, b)]
    try:
        icc21, icc31, _ms = agreement.icc(rows, alpha=alpha)
    except (ValueError, OverflowError) as exc:
        warnings.append(f"ICC 계산 불가: {exc}")
        nan = float("nan")
        icc21 = ICCResult("ICC(2,1)", "", nan, nan, nan, nan, nan, nan, nan,
                          "판정 불가 / undefined")
        icc31 = ICCResult("ICC(3,1)", "", nan, nan, nan, nan, nan, nan, nan,
                          "판정 불가 / undefined")

    try:
        ccc_res = agreement.ccc(a, b, alpha=alpha)
    except (ValueError, OverflowError) as exc:
        warnings.append(f"CCC 계산 불가: {exc}")
        nan = float("nan")
        ccc_res = CCCResult(nan, nan, nan, nan, nan, "판정 불가 / undefined", alpha)

    # Koo & Li (2016): grade reliability from the CI *lower bound*, not the
    # point estimate. Warn when the point grade would overstate reliability.
    if (icc21.value == icc21.value and icc21.ci_lower == icc21.ci_lower
            and interpret_icc(icc21.ci_lower) != interpret_icc(icc21.value)):
        warnings.append(
            f"ICC(2,1) 점추정 등급({interpret_icc(icc21.value).split(' / ')[0]})은 "
            f"신뢰구간 하한({_num(icc21.ci_lower)}) 기준 등급"
            f"({interpret_icc(icc21.ci_lower).split(' / ')[0]})보다 높습니다. "
            "Koo & Li(2016)는 신뢰구간 하한으로 판단할 것을 권장합니다.")

    repeat = agreement.repeatability(a, b, subjects)
    rm_ba = agreement.repeated_measures_ba(a, b, subjects, mode=mode, alpha=alpha)
    if rm_ba.available:
        warnings.append(
            "반복측정 데이터가 감지되어 반복측정 보정 LoA(Bland & Altman 2007)를 "
            f"계산했습니다: {_num(rm_ba.loa_lower,2)}{ba.unit} ~ "
            f"{_num(rm_ba.loa_upper,2)}{ba.unit} (naive LoA "
            f"{_num(ba.loa_lower,2)}{ba.unit} ~ {_num(ba.loa_upper,2)}{ba.unit}). "
            "각 행을 독립으로 가정한 naive LoA는 개인당 반복이 있으면 좁게 나오므로 "
            "반복측정 LoA를 보고하세요.")
        if rm_ba.n_replicated_subjects < 2:
            warnings.append(
                f"반복이 있는 피험자가 {rm_ba.n_replicated_subjects}명뿐입니다 — "
                "within-subject 분산 추정이 불안정하니 반복측정 LoA를 신중히 "
                "해석하세요.")
    elif repeat.available:
        warnings.append(
            "반복측정 데이터가 감지되었습니다. 위의 Bland-Altman/ICC는 각 행을 "
            "독립으로 가정합니다 — 개인당 반복이 있으면 LoA가 좁게 나올 수 있으니 "
            "반복측정용 방법(Bland & Altman 2007)도 함께 고려하세요.")

    pear = agreement.pearson(a, b, alpha=alpha)
    paired = agreement.paired_t(a, b)

    # Method-comparison regression (CLSI EP09): regress the TEST method (A, y) on
    # the REFERENCE method (B, x). Slope CI excluding 1 => proportional bias;
    # intercept CI excluding 0 => constant bias. Deming assumes error in both
    # methods (unlike the OLS diff~mean check above); Passing–Bablok is
    # distribution-free and robust to outliers.
    try:
        deming_res = regression.deming(b, a, lam=deming_lambda, alpha=alpha,
                                       decision_point=decision_point)
    except (ValueError, OverflowError) as exc:
        deming_res = DemingResult(False, f"Deming 계산 불가: {exc}")
    pb_res = regression.passing_bablok(b, a, alpha=alpha,
                                       decision_point=decision_point)
    _regression_warnings(deming_res, pb_res, warnings)

    accept_lower = accept_upper = None
    interchangeable: Optional[bool] = None
    if accept is not None:
        accept_lower, accept_upper = accept
        # Judge against the LoA we actually recommend: the repeated-measures LoA
        # when replicates exist (it is the headline), else the naive LoA — so the
        # verdict can never contradict the headlined result.
        if rm_ba.available and math.isfinite(rm_ba.loa_lower):
            lo_j, hi_j, src = rm_ba.loa_lower, rm_ba.loa_upper, "반복측정 95% LoA"
        else:
            lo_j, hi_j, src = ba.loa_lower, ba.loa_upper, "95% LoA"
        if math.isfinite(lo_j) and math.isfinite(hi_j):
            interchangeable = (lo_j >= accept_lower and hi_j <= accept_upper)
            if interchangeable:
                warnings.append(
                    f"{src} [{_num(lo_j,2)}, {_num(hi_j,2)}]"
                    f"{ba.unit}가 허용한계 [{_num(accept_lower,2)}, "
                    f"{_num(accept_upper,2)}]{ba.unit} 안에 있습니다 → "
                    "임상적으로 교환가능(interchangeable) 판정.")
            else:
                warnings.append(
                    f"{src} [{_num(lo_j,2)}, {_num(hi_j,2)}]"
                    f"{ba.unit}가 허용한계 [{_num(accept_lower,2)}, "
                    f"{_num(accept_upper,2)}]{ba.unit}를 벗어납니다 → "
                    "교환가능하다고 볼 수 없습니다.")

    # LoA-CI precision: required n for a target CI half-width of the LoA.
    req_n = req_n_approx = None
    if target_loa_hw is not None and math.isfinite(ba.sd_diff) and ba.sd_diff > 0:
        req_n, req_n_approx = _required_n_for_loa_hw(ba.sd_diff, target_loa_hw)

    return AnalysisResult(
        name_a=name_a, name_b=name_b, n=n, dropped=dropped, alpha=alpha,
        mode=mode,
        mean_a=agreement.mean(a),
        sd_a=math.sqrt(agreement.variance(a)) if not a_const else 0.0,
        mean_b=agreement.mean(b),
        sd_b=math.sqrt(agreement.variance(b)) if not b_const else 0.0,
        ba=ba, icc21=icc21, icc31=icc31, ccc=ccc_res, repeat=repeat,
        pearson=pear, paired=paired, warnings=warnings,
        accept_lower=accept_lower, accept_upper=accept_upper,
        interchangeable=interchangeable, rm_ba=rm_ba,
        precision_target_hw=target_loa_hw,
        precision_required_n=req_n,
        precision_required_n_approx=req_n_approx,
        deming=deming_res, passing_bablok=pb_res)


def _regression_warnings(deming_res: DemingResult, pb_res: PassingBablokResult,
                         warnings: List[str]) -> None:
    """Warn when the error-in-both-variables regression flags a bias the naive
    Bland-Altman single-bias summary would miss."""
    flags = []
    if pb_res is not None and pb_res.available:
        if pb_res.constant_bias:
            flags.append(
                f"상수 편향(constant bias): Passing–Bablok 절편 CI가 0을 "
                f"제외 (절편={_num(pb_res.intercept, 3)})")
        if pb_res.proportional_bias:
            flags.append(
                f"비례 편향(proportional bias): Passing–Bablok 기울기 CI가 1을 "
                f"제외 (기울기={_num(pb_res.slope, 3)})")
    if flags:
        warnings.append(
            "방법비교 회귀(CLSI EP09)에서 계통오차가 감지되었습니다 — "
            + "; ".join(flags) + ". Bland–Altman의 단일 bias 요약만으로는 "
            "이런 크기-의존/상수 편향을 놓칠 수 있으니 회귀 결과도 함께 보고하세요.")


_Z_LOA = 1.96


_MAX_REQUIRED_N = 10_000_000  # beyond this the target is impractical (and the
#                              t_ppf df regime becomes unreliable)


def _required_n_for_loa_hw(sd: float, target_hw: float
                           ) -> Tuple[Optional[int], float]:
    """Smallest n so tcrit*sd*sqrt(1/n + z^2/(2(n-1))) <= target_hw.

    Returns (exact_n or None, normal-approx_n). hw(n) is strictly decreasing;
    seed from the normal approximation, search up to the exact smallest n, then
    step down to guarantee the true minimum. Returns None for exact_n when the
    target would need an impractically large (and numerically unreliable) n.
    """
    from .special import t_ppf
    z = _Z_LOA

    def hw(n: int) -> float:
        return t_ppf(0.975, n - 1) * sd * math.sqrt(
            1.0 / n + z * z / (2.0 * (n - 1)))

    approx = z * z * sd * sd * (1.0 + z * z / 2.0) / (target_hw * target_hw)
    if approx > _MAX_REQUIRED_N:
        return None, approx
    n = max(2, int(approx) - 3)
    while n < _MAX_REQUIRED_N and hw(n) > target_hw:
        n += 1
    if n >= _MAX_REQUIRED_N:
        return None, approx
    while n > 2 and hw(n - 1) <= target_hw:  # step down to the true minimum
        n -= 1
    return n, approx


def _p(p: float) -> str:
    if p != p:
        return "NaN"
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def _num(x: float, d: int = 3) -> str:
    if x != x:
        return "NaN"
    return f"{x:.{d}f}"
