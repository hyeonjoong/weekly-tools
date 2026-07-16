"""Equivalence (TOST) and non-inferiority testing on the mean difference.

A conventional significance test can only ever *fail to reject* "no difference";
it can never demonstrate that two treatments are similar.  Pharma trials that
must show a generic matches a reference, or that a cheaper arm is no worse than
standard of care, therefore use one of two designs:

**Equivalence — two one-sided tests (TOST).**  Given an equivalence margin
``(low, high)`` on the mean difference, TOST rejects the *union* null
"the difference is outside the margin" by running two one-sided t-tests:

    H01: diff <= low    tested with  t_low  = (diff - low)  / se   (upper tail)
    H02: diff >= high   tested with  t_high = (diff - high) / se   (lower tail)

Both must reject, so ``p_TOST = max(p_low, p_high)``.  This is exactly
equivalent to the 100(1-2*alpha)% two-sided CI for the difference lying wholly
inside ``(low, high)`` — which is why that CI (90% at alpha=0.05), not the 95%
one, is what regulators ask to see.

**Non-inferiority — one one-sided test.**  Only one bound matters:

    higher_is_better:  H0: diff <= -margin   vs  H1: diff > -margin
    lower_is_better:   H0: diff >= +margin   vs  H1: diff < +margin

Non-inferiority is concluded iff the one-sided 100(1-alpha)% confidence bound on
the difference falls on the good side of the margin.

Both live on the same core: a difference, its standard error, and degrees of
freedom.  Those come from whichever t-model was selected upstream (Student's,
Welch's, or the paired t), so the equivalence analysis stays consistent with the
superiority analysis printed next to it.

The margin is a *clinical* decision, never a statistical one — the caller must
supply it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from . import tests_stat
from .special import t_cdf, t_ppf

__all__ = [
    "EquivalenceResult",
    "tost",
    "noninferiority",
    "tost_independent",
    "tost_paired",
    "noninferiority_independent",
    "noninferiority_paired",
    "parse_margin",
]


@dataclass
class EquivalenceResult:
    """Outcome of a TOST or non-inferiority test on the mean difference."""

    kind: str                     # "tost" or "noninferiority"
    diff: float
    se: float
    df: float
    margin_low: Optional[float]   # None for a one-sided non-inferiority test
    margin_high: Optional[float]
    t_low: Optional[float]
    p_low: Optional[float]
    t_high: Optional[float]
    p_high: Optional[float]
    pvalue: float                 # max(p_low, p_high) for TOST; the one p for NI
    ci_low: Optional[float]       # (1-2a) two-sided CI for TOST;
    ci_high: Optional[float]      # one-sided (1-a) bound for NI (other side None)
    conf: float                   # nominal coverage of the reported interval
    alpha: float
    concluded: bool               # equivalent / non-inferior at alpha
    model: str                    # "student", "welch" or "paired"
    direction: str = ""           # NI only: "higher_is_better"/"lower_is_better"


def _check_se(se: float) -> None:
    if not math.isfinite(se):
        raise ValueError("standard error of the difference is not finite")
    if se <= 0.0:
        raise ValueError(
            "standard error of the difference is zero (both groups are constant); "
            "an equivalence test is undefined")


def tost(diff: float, se: float, df: float, low: float, high: float,
         alpha: float = 0.05, model: str = "welch") -> EquivalenceResult:
    """Two one-sided tests for equivalence of a mean difference.

    Rejects "the difference lies outside ``(low, high)``" at ``alpha`` when both
    one-sided tests reject.  Reports the 100(1-2*alpha)% CI, whose containment in
    the margin is algebraically identical to ``p_TOST < alpha``.
    """
    if not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError("equivalence margins must be finite "
                         "(use noninferiority() for a one-sided margin)")
    if low >= high:
        raise ValueError(
            f"equivalence margin must satisfy low < high (got {low}, {high})")
    if not 0.0 < alpha < 0.5:
        raise ValueError("alpha must be in (0, 0.5)")
    _check_se(se)
    if df <= 0:
        raise ValueError("degrees of freedom must be positive")

    t_low = (diff - low) / se
    p_low = 1.0 - t_cdf(t_low, df)      # H01: diff <= low  -> upper tail
    t_high = (diff - high) / se
    p_high = t_cdf(t_high, df)          # H02: diff >= high -> lower tail
    p = max(p_low, p_high)

    tcrit = t_ppf(1.0 - alpha, df)      # (1-2a) two-sided == (1-a) each side
    ci = (diff - tcrit * se, diff + tcrit * se)
    return EquivalenceResult(
        kind="tost", diff=diff, se=se, df=df, margin_low=low, margin_high=high,
        t_low=t_low, p_low=p_low, t_high=t_high, p_high=p_high, pvalue=p,
        ci_low=ci[0], ci_high=ci[1], conf=1.0 - 2.0 * alpha, alpha=alpha,
        concluded=(p < alpha), model=model)


def noninferiority(diff: float, se: float, df: float, margin: float,
                   direction: str = "higher_is_better", alpha: float = 0.05,
                   model: str = "welch") -> EquivalenceResult:
    """One-sided non-inferiority test on a mean difference.

    ``margin`` is the largest clinically acceptable loss and must be positive;
    ``direction`` says which way the outcome is good:

    ``higher_is_better``
        tests H0: diff <= -margin (the test arm is worse by more than the
        margin) and reports the one-sided lower confidence bound.
    ``lower_is_better``
        tests H0: diff >= +margin and reports the one-sided upper bound.
    """
    if direction not in ("higher_is_better", "lower_is_better"):
        raise ValueError("direction must be 'higher_is_better' or "
                         "'lower_is_better'")
    if not math.isfinite(margin) or margin <= 0:
        raise ValueError(f"non-inferiority margin must be positive (got {margin})")
    if not 0.0 < alpha < 0.5:
        raise ValueError("alpha must be in (0, 0.5)")
    _check_se(se)
    if df <= 0:
        raise ValueError("degrees of freedom must be positive")

    tcrit = t_ppf(1.0 - alpha, df)
    if direction == "higher_is_better":
        bound = -margin
        t_stat = (diff - bound) / se
        p = 1.0 - t_cdf(t_stat, df)     # H0: diff <= -margin -> upper tail
        ci_low, ci_high = diff - tcrit * se, None
        concluded = ci_low > bound
        res_low, res_high = (t_stat, p), (None, None)
        margin_low, margin_high = bound, None
    else:
        bound = margin
        t_stat = (diff - bound) / se
        p = t_cdf(t_stat, df)           # H0: diff >= +margin -> lower tail
        ci_low, ci_high = None, diff + tcrit * se
        concluded = ci_high < bound
        res_low, res_high = (None, None), (t_stat, p)
        margin_low, margin_high = None, bound

    return EquivalenceResult(
        kind="noninferiority", diff=diff, se=se, df=df,
        margin_low=margin_low, margin_high=margin_high,
        t_low=res_low[0], p_low=res_low[1],
        t_high=res_high[0], p_high=res_high[1],
        pvalue=p, ci_low=ci_low, ci_high=ci_high, conf=1.0 - alpha, alpha=alpha,
        concluded=concluded, model=model, direction=direction)


# --------------------------------------------------------------------------
# Convenience wrappers that derive (diff, se, df) from raw samples
# --------------------------------------------------------------------------

def _independent_diff_se_df(a: Sequence[float], b: Sequence[float],
                            model: str) -> Tuple[float, float, float]:
    """(diff, se, df) for a - b under the Student or Welch t-model."""
    if model not in ("student", "welch"):
        raise ValueError("model must be 'student' or 'welch'")
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        raise ValueError("each group needs at least 2 observations")
    v1, v2 = tests_stat.variance(a), tests_stat.variance(b)
    diff = tests_stat.mean(a) - tests_stat.mean(b)
    if model == "student":
        df = float(n1 + n2 - 2)
        sp2 = ((n1 - 1) * v1 + (n2 - 1) * v2) / df
        se = math.sqrt(sp2 * (1.0 / n1 + 1.0 / n2))
    else:
        se2 = v1 / n1 + v2 / n2
        se = math.sqrt(se2)
        if se2 <= 0:
            # keep _check_se the single place that reports this
            return diff, 0.0, float(n1 + n2 - 2)
        df = se2 ** 2 / ((v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))
    return diff, se, df


def _paired_diff_se_df(a: Sequence[float], b: Sequence[float]
                       ) -> Tuple[float, float, float]:
    if len(a) != len(b):
        raise ValueError("paired equivalence needs equal-length conditions")
    n = len(a)
    if n < 2:
        raise ValueError("paired equivalence needs at least 2 matched pairs")
    d = [x - y for x, y in zip(a, b)]
    diff = tests_stat.mean(d)
    se = math.sqrt(tests_stat.variance(d) / n)
    return diff, se, float(n - 1)


def tost_independent(a: Sequence[float], b: Sequence[float], low: float,
                     high: float, alpha: float = 0.05, model: str = "welch"
                     ) -> EquivalenceResult:
    """TOST on the difference of two independent group means (``a - b``)."""
    diff, se, df = _independent_diff_se_df(a, b, model)
    return tost(diff, se, df, low, high, alpha, model=model)


def tost_paired(a: Sequence[float], b: Sequence[float], low: float, high: float,
                alpha: float = 0.05) -> EquivalenceResult:
    """TOST on the mean within-pair difference (``a - b``)."""
    diff, se, df = _paired_diff_se_df(a, b)
    return tost(diff, se, df, low, high, alpha, model="paired")


def noninferiority_independent(a: Sequence[float], b: Sequence[float],
                               margin: float,
                               direction: str = "higher_is_better",
                               alpha: float = 0.05, model: str = "welch"
                               ) -> EquivalenceResult:
    """One-sided non-inferiority test on ``mean(a) - mean(b)``."""
    diff, se, df = _independent_diff_se_df(a, b, model)
    return noninferiority(diff, se, df, margin, direction, alpha, model=model)


def noninferiority_paired(a: Sequence[float], b: Sequence[float], margin: float,
                          direction: str = "higher_is_better",
                          alpha: float = 0.05) -> EquivalenceResult:
    """One-sided non-inferiority test on the mean within-pair difference."""
    diff, se, df = _paired_diff_se_df(a, b)
    return noninferiority(diff, se, df, margin, direction, alpha, model="paired")


def parse_margin(spec: str) -> Tuple[float, float]:
    """Parse an equivalence-margin CLI spec into ``(low, high)``.

    ``"1.5"``      -> (-1.5, 1.5)   symmetric, the usual case
    ``"-1.0,2.0"`` -> (-1.0, 2.0)   asymmetric
    """
    parts = [p.strip() for p in str(spec).split(",")]
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise ValueError(
            f"--equivalence-margin '{spec}' 을(를) 숫자로 읽을 수 없습니다. "
            f"예: '1.5' (=±1.5) 또는 '-1.0,2.0'")
    if any(not math.isfinite(x) for x in nums):
        raise ValueError(f"--equivalence-margin '{spec}' 에 유한하지 않은 값이 있습니다.")
    if len(nums) == 1:
        d = abs(nums[0])
        if d == 0:
            raise ValueError("--equivalence-margin 0 은 의미가 없습니다 "
                             "(등가 구간의 폭이 0).")
        return (-d, d)
    if len(nums) == 2:
        low, high = nums
        if low >= high:
            raise ValueError(
                f"--equivalence-margin '{spec}': low < high 여야 합니다.")
        return (low, high)
    raise ValueError(
        f"--equivalence-margin '{spec}': 값 1개(±Δ) 또는 2개(low,high)만 "
        f"지정할 수 있습니다.")
