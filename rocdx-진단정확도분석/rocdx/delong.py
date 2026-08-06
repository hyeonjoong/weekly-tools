"""DeLong machinery: AUC variance, confidence intervals and AUC comparison.

The placement-value ("V-statistic") form of DeLong, Delong & Clarke-Pearson
(1988) computed in O(n log n) via mid-ranks (Sun & Xu 2014). Everything here is
exact for tied scores, which matters because clinical markers are rounded to two
decimals and ties are the rule, not the exception.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .stats_core import (
    cov_ddof1,
    midranks,
    norm_ppf,
    two_sided_p,
    var_ddof1,
)

__all__ = [
    "Placements",
    "AucEstimate",
    "AucComparison",
    "placements",
    "delong_variance",
    "auc_ci",
    "estimate_auc",
    "mann_whitney_p",
    "compare_paired",
    "compare_unpaired",
]


@dataclass(frozen=True)
class Placements:
    """AUC plus the per-subject placement values it decomposes into."""

    auc: float
    v10: List[float]  # one per positive case
    v01: List[float]  # one per negative case


@dataclass(frozen=True)
class AucEstimate:
    auc: float
    se: float
    ci: Optional[Tuple[float, float]]
    ci_method: str
    alpha: float
    n_pos: int
    n_neg: int
    p_value: Optional[float]  # H0: AUC = 0.5, Mann-Whitney U (tie-corrected)


@dataclass(frozen=True)
class AucComparison:
    label_a: str
    label_b: str
    auc_a: float
    auc_b: float
    diff: float
    se_diff: float
    ci: Optional[Tuple[float, float]]
    z: Optional[float]
    p_value: Optional[float]   # None when the variance is not estimable
    paired: bool
    n_used: int


def placements(pos_scores: Sequence[float], neg_scores: Sequence[float]) -> Placements:
    """AUC and placement values for one marker.

    ``v10[i]`` is the fraction of negatives that positive ``i`` beats (ties count
    a half); ``v01[j]`` is the fraction of positives that negative ``j`` is below
    (ties count a half). Their means both equal the AUC.
    """
    m, n = len(pos_scores), len(neg_scores)
    if m == 0 or n == 0:
        raise ValueError("placements() needs at least one positive and one negative case")
    combined = list(pos_scores) + list(neg_scores)
    tz = midranks(combined)
    tx = midranks(pos_scores)
    ty = midranks(neg_scores)
    v10 = [(tz[i] - tx[i]) / n for i in range(m)]
    v01 = [1.0 - (tz[m + j] - ty[j]) / m for j in range(n)]
    auc = (math.fsum(tz[:m]) / m - (m + 1) / 2.0) / n
    return Placements(auc=auc, v10=v10, v01=v01)


def delong_variance(pl: Placements) -> float:
    """DeLong variance of the AUC: S10/m + S01/n.

    Returns ``nan`` when either group has a single member: the corresponding
    component variance is not estimable, and silently dropping it would report a
    standard error several times too small.
    """
    m, n = len(pl.v10), len(pl.v01)
    if m < 2 or n < 2:
        return float("nan")
    return var_ddof1(pl.v10) / m + var_ddof1(pl.v01) / n


def auc_ci(auc: float, se: float, alpha: float = 0.05,
           method: str = "logit") -> Optional[Tuple[float, float]]:
    """Confidence interval for an AUC.

    ``logit`` (default) transforms to the log-odds scale before applying the
    normal interval, so the result can never leave (0, 1) and keeps sensible
    coverage for the near-perfect markers that a Wald interval would push above
    1. ``wald`` is the plain symmetric interval, clipped to [0, 1].
    Returns ``None`` when no interval is defined (zero SE, or AUC exactly 0/1
    under the logit transform).
    """
    if not (math.isfinite(auc) and math.isfinite(se)) or se <= 0.0:
        return None
    z = norm_ppf(1.0 - alpha / 2.0)
    if method == "wald":
        return (max(0.0, auc - z * se), min(1.0, auc + z * se))
    if method != "logit":
        raise ValueError("auc_ci method must be 'logit' or 'wald'")
    if not (0.0 < auc < 1.0):
        return None
    l = math.log(auc / (1.0 - auc))
    se_l = se / (auc * (1.0 - auc))
    lo, hi = l - z * se_l, l + z * se_l
    return (1.0 / (1.0 + math.exp(-lo)), 1.0 / (1.0 + math.exp(-hi)))


def mann_whitney_p(pos_scores: Sequence[float], neg_scores: Sequence[float]) -> Optional[float]:
    """Two-sided p for H0: AUC = 0.5, from the tie-corrected Mann-Whitney U.

    The null hypothesis is tested with the *null* variance of U (not the DeLong
    variance, which is estimated under the alternative), with a continuity
    correction. Returns ``None`` if the null variance is zero, which happens only
    when every observed score is identical.
    """
    m, n = len(pos_scores), len(neg_scores)
    if m == 0 or n == 0:
        return None
    combined = list(pos_scores) + list(neg_scores)
    n_all = m + n
    ranks = midranks(combined)
    r_pos = math.fsum(ranks[:m])
    u = r_pos - m * (m + 1) / 2.0

    counts: dict = {}
    for v in combined:
        counts[v] = counts.get(v, 0) + 1
    tie_term = math.fsum(float(t) ** 3 - t for t in counts.values())

    mu = m * n / 2.0
    if n_all < 2:
        return None
    var = (m * n / 12.0) * ((n_all + 1) - tie_term / (n_all * (n_all - 1.0)))
    if var <= 0.0:
        return None
    z = (abs(u - mu) - 0.5) / math.sqrt(var)
    if z < 0.0:
        z = 0.0
    return two_sided_p(z)


def estimate_auc(pos_scores: Sequence[float], neg_scores: Sequence[float],
                 alpha: float = 0.05, ci_method: str = "logit") -> AucEstimate:
    """AUC with DeLong standard error, CI and a Mann-Whitney p-value."""
    pl = placements(pos_scores, neg_scores)
    var = delong_variance(pl)
    if math.isnan(var):
        se = float("nan")
    else:
        se = math.sqrt(var) if var > 0 else 0.0
    return AucEstimate(
        auc=pl.auc,
        se=se,
        ci=auc_ci(pl.auc, se, alpha, ci_method),
        ci_method=ci_method,
        alpha=alpha,
        n_pos=len(pl.v10),
        n_neg=len(pl.v01),
        p_value=mann_whitney_p(pos_scores, neg_scores),
    )


def compare_paired(pos_a: Sequence[float], neg_a: Sequence[float],
                   pos_b: Sequence[float], neg_b: Sequence[float],
                   label_a: str = "A", label_b: str = "B",
                   alpha: float = 0.05) -> AucComparison:
    """DeLong test for two markers measured on the *same* subjects.

    ``pos_a[i]`` and ``pos_b[i]`` must be the two markers for the same positive
    case (likewise for the negatives). The correlation between the markers is
    what makes this test far more powerful than treating the two AUCs as
    independent.
    """
    if len(pos_a) != len(pos_b) or len(neg_a) != len(neg_b):
        raise ValueError("paired comparison needs the same subjects for both markers")
    pa = placements(pos_a, neg_a)
    pb = placements(pos_b, neg_b)
    m, n = len(pa.v10), len(pa.v01)

    if m < 2 or n < 2:
        var = float("nan")
    else:
        s10 = var_ddof1(pa.v10) - 2.0 * cov_ddof1(pa.v10, pb.v10) + var_ddof1(pb.v10)
        s01 = var_ddof1(pa.v01) - 2.0 * cov_ddof1(pa.v01, pb.v01) + var_ddof1(pb.v01)
        var = s10 / m + s01 / n
    return _finish_comparison(pa.auc, pb.auc, var, label_a, label_b, alpha, True, m + n)


def compare_unpaired(pos_a: Sequence[float], neg_a: Sequence[float],
                     pos_b: Sequence[float], neg_b: Sequence[float],
                     label_a: str = "A", label_b: str = "B",
                     alpha: float = 0.05) -> AucComparison:
    """DeLong test for two AUCs from independent samples (variances add)."""
    pa = placements(pos_a, neg_a)
    pb = placements(pos_b, neg_b)
    var = delong_variance(pa) + delong_variance(pb)
    n_used = len(pa.v10) + len(pa.v01) + len(pb.v10) + len(pb.v01)
    return _finish_comparison(pa.auc, pb.auc, var, label_a, label_b, alpha, False, n_used)


def _finish_comparison(auc_a: float, auc_b: float, var: float, label_a: str,
                       label_b: str, alpha: float, paired: bool,
                       n_used: int) -> AucComparison:
    diff = auc_a - auc_b
    se = math.sqrt(var) if (math.isfinite(var) and var > 0) else (
        float("nan") if not math.isfinite(var) else 0.0)
    z: Optional[float]
    p: Optional[float]
    ci: Optional[Tuple[float, float]]
    if math.isfinite(se) and se > 0:
        z = diff / se
        p = two_sided_p(z)
        half = norm_ppf(1.0 - alpha / 2.0) * se
        ci = (diff - half, diff + half)
    elif math.isfinite(se) and diff == 0.0:
        # The two markers order every pair identically: no difference, no test.
        z, p, ci = 0.0, 1.0, None
    else:
        # A zero (or unestimable) variance with a non-zero difference does NOT
        # mean the markers agree — it happens when the per-subject placement
        # differences are constant, e.g. against an all-constant comparator.
        # Reporting p = 1 there would be plainly wrong, so the test is undefined.
        z, p, ci = None, None, None
    return AucComparison(
        label_a=label_a, label_b=label_b, auc_a=auc_a, auc_b=auc_b,
        diff=diff, se_diff=se, ci=ci, z=z, p_value=p, paired=paired, n_used=n_used,
    )
