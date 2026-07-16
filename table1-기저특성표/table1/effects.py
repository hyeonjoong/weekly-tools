"""Between-group effect sizes with 95% confidence intervals — pure stdlib.

A Table 1's p-value tells you *whether* two groups differ; a comparative
(non-randomized) manuscript also wants *by how much*, with a confidence
interval. This module computes an absolute effect for the two-group case,
chosen to stay coherent with the hypothesis test that was already selected:

    - parametric mean comparison (Student / Welch t) -> difference in means
      with a t-based CI using the same standard error and degrees of freedom
      as the reported p-value;
    - nonparametric comparison (Mann-Whitney U) -> Hodges-Lehmann median
      shift with a distribution-free (Moses) CI;
    - binary categorical -> risk (proportion) difference with Newcombe's
      score CI (no zero-cell blow-ups).

The estimate is always ``group1 - group2`` in the table's group order, so a
positive value means the first group is higher. Effects are defined only for
exactly two groups; multi-level categoricals have no single scalar effect
(the multivariate SMD already summarizes their balance) and return None.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .special import norm_sf, t_ppf

__all__ = ["Effect", "mean_difference", "hodges_lehmann", "risk_difference",
           "mean_ci", "median_ci", "proportion_ci"]

# Hodges-Lehmann forms all n1*n2 pairwise differences. Cap the product so a
# pathologically large pair of groups cannot allocate a multi-gigabyte list;
# real Table-1 groups are far below this.
_HL_MAX_PAIRS = 4_000_000


@dataclass
class Effect:
    estimate: float
    lo: float
    hi: float
    kind: str            # "mean_diff" | "hl_shift" | "risk_diff"
    conf: float = 0.95
    # For a risk difference, which level the difference refers to (the "index"
    # level) and the level it is measured against. None for mean/HL effects.
    index_level: Optional[str] = None
    reference_level: Optional[str] = None


def _z_crit(conf: float) -> float:
    """Two-sided normal critical value for the given confidence level."""
    alpha = 1.0 - conf
    # invert norm_sf(z) = alpha/2 by bisection (norm_sf is monotone decreasing)
    lo, hi = 0.0, 40.0
    target = alpha / 2.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if norm_sf(mid) > target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def mean_difference(a: Sequence[float], b: Sequence[float], *, kind: str,
                    conf: float = 0.95) -> Optional[Effect]:
    """Difference in means (a - b) with a t-based CI.

    ``kind`` selects the standard error / df: "student" (pooled, equal
    variance) or "welch" (unequal variance). Returns None if either group has
    < 2 observations or the standard error is zero (no spread).
    """
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return None
    m1 = sum(a) / n1
    m2 = sum(b) / n2
    v1 = sum((x - m1) ** 2 for x in a) / (n1 - 1)
    v2 = sum((x - m2) ** 2 for x in b) / (n2 - 1)
    diff = m1 - m2
    if kind == "student":
        df = n1 + n2 - 2
        sp2 = ((n1 - 1) * v1 + (n2 - 1) * v2) / df
        se = math.sqrt(sp2 * (1.0 / n1 + 1.0 / n2))
    else:  # welch
        se2_1, se2_2 = v1 / n1, v2 / n2
        se = math.sqrt(se2_1 + se2_2)
        denom = se2_1 ** 2 / (n1 - 1) + se2_2 ** 2 / (n2 - 1)
        df = (se2_1 + se2_2) ** 2 / denom if denom > 0 else float(n1 + n2 - 2)
    if se == 0.0 or df <= 0:
        return None
    tcrit = t_ppf(1.0 - (1.0 - conf) / 2.0, df)
    half = tcrit * se
    return Effect(diff, diff - half, diff + half, "mean_diff", conf)


def hodges_lehmann(a: Sequence[float], b: Sequence[float], *,
                   conf: float = 0.95) -> Optional[Effect]:
    """Hodges-Lehmann median shift (a - b) with a distribution-free CI.

    The point estimate is the median of all pairwise differences x_i - y_j;
    the CI uses the normal approximation to the Mann-Whitney null (Moses'
    interval) with the SAME tie correction the tool's Mann-Whitney p-value
    uses, so heavily-tied data is not over-covered. This matches the shift a
    widely-used ``wilcox.test(conf.int=TRUE)`` reports for larger samples.
    Returns None for tiny groups (< 2 each) or if the pairwise product would
    be too large to materialize.
    """
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return None
    if n1 * n2 > _HL_MAX_PAIRS:
        return None
    diffs = sorted(x - y for x in a for y in b)
    npair = len(diffs)

    def _median_sorted(s: List[float]) -> float:
        m = len(s)
        mid = m // 2
        return s[mid] if m % 2 else 0.5 * (s[mid - 1] + s[mid])

    est = _median_sorted(diffs)
    z = _z_crit(conf)
    # Tie-corrected null variance of the Mann-Whitney U (identical to the term
    # used in tests_stat.mann_whitney_u), so ties widen the rank K correctly
    # instead of leaving the interval conservatively wide.
    n = n1 + n2
    counts: dict = {}
    for v in list(a) + list(b):
        counts[v] = counts.get(v, 0) + 1
    tie = sum(c ** 3 - c for c in counts.values() if c > 1)
    var = (n1 * n2 / 12.0) * ((n + 1) - tie / (n * (n - 1)))
    if var < 0:
        var = 0.0
    # Rank K such that the K-th smallest / K-th largest pairwise difference are
    # the CI bounds (normal approximation to the U null distribution).
    k = (n1 * n2) / 2.0 - z * math.sqrt(var)
    kf = int(math.floor(k))
    if kf < 0:
        kf = 0
    if kf >= npair:
        # CI wider than the data support -> fall back to the full range.
        return Effect(est, diffs[0], diffs[-1], "hl_shift", conf)
    lo = diffs[kf]
    hi = diffs[npair - 1 - kf]
    if lo > hi:
        lo, hi = hi, lo
    return Effect(est, lo, hi, "hl_shift", conf)


def _wilson(x: int, n: int, z: float):
    """Wilson score interval (lo, hi) for a binomial proportion."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = x / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / denom
    return (center - half, center + half)


def mean_ci(values: Sequence[float], *, conf: float = 0.95) -> Optional[Effect]:
    """One-sample t confidence interval for the mean (descriptive tables).

    Returns None if fewer than 2 observations or the spread is zero.
    """
    n = len(values)
    if n < 2:
        return None
    m = sum(values) / n
    v = sum((x - m) ** 2 for x in values) / (n - 1)
    se = math.sqrt(v / n)
    if se == 0.0:
        return None
    t = t_ppf(1.0 - (1.0 - conf) / 2.0, n - 1)
    return Effect(m, m - t * se, m + t * se, "mean_ci", conf)


def median_ci(values: Sequence[float], *, conf: float = 0.95) -> Optional[Effect]:
    """Distribution-free confidence interval for the median (order statistics).

    Uses the normal approximation to the sign-test null: the CI runs from the
    K-th smallest to the K-th largest value, K = floor(n/2 - z*sqrt(n)/2).
    Returns None for fewer than 2 observations; falls back to the full range
    if K underflows (tiny n at high confidence).
    """
    n = len(values)
    if n < 2:
        return None
    s = sorted(values)

    def _median_sorted(x):
        m = len(x)
        mid = m // 2
        return x[mid] if m % 2 else 0.5 * (x[mid - 1] + x[mid])

    est = _median_sorted(s)
    z = _z_crit(conf)
    k = int(math.floor(n / 2.0 - z * math.sqrt(n) / 2.0))
    if k < 0:
        k = 0
    if k >= n - k - 1:  # CI wider than the data support
        return Effect(est, s[0], s[-1], "median_ci", conf)
    return Effect(est, s[k], s[n - 1 - k], "median_ci", conf)


def proportion_ci(x: int, n: int, *, conf: float = 0.95) -> Optional[Effect]:
    """Wilson score confidence interval for a single proportion x/n.

    Matches ``statsmodels.stats.proportion.proportion_confint(method='wilson')``.
    Returns None for a zero denominator.
    """
    if n <= 0:
        return None
    z = _z_crit(conf)
    lo, hi = _wilson(x, n, z)
    return Effect(x / n, lo, hi, "prop_ci", conf)


def risk_difference(x1: int, n1: int, x2: int, n2: int, *,
                    conf: float = 0.95) -> Optional[Effect]:
    """Risk (proportion) difference p1 - p2 with Newcombe's method 10 CI.

    ``x1``/``x2`` are event counts for the index level in groups 1 and 2;
    ``n1``/``n2`` the group denominators. Newcombe (1998) combines the two
    Wilson score intervals, so it behaves well at zero cells and near 0/1.
    Returns None if either denominator is zero.
    """
    if n1 <= 0 or n2 <= 0:
        return None
    p1 = x1 / n1
    p2 = x2 / n2
    diff = p1 - p2
    z = _z_crit(conf)
    l1, u1 = _wilson(x1, n1, z)
    l2, u2 = _wilson(x2, n2, z)
    lo = diff - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = diff + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return Effect(diff, lo, hi, "risk_diff", conf)
