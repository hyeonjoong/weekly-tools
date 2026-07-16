"""Group-comparison statistical tests — pure standard library.

Each function returns a small dataclass with the statistic, degrees of freedom
(where applicable) and a two-sided p-value. p-values are computed from the
exact Student-t / F / normal / chi-square distributions implemented in
``special`` and match SciPy to ~1e-9 (Mann-Whitney/Kruskal-Wallis use the
tie-corrected normal / chi-square asymptotic approximation).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence

from .special import chi2_sf, f_sf, norm_sf, t_sf_two_sided

__all__ = [
    "TTestResult",
    "MannWhitneyResult",
    "AnovaResult",
    "KruskalResult",
    "LeveneResult",
    "students_t",
    "welch_t",
    "mann_whitney_u",
    "one_way_anova",
    "kruskal_wallis",
    "levene",
    "mean",
    "variance",
]


def mean(x: Sequence[float]) -> float:
    return sum(x) / len(x)


def variance(x: Sequence[float], ddof: int = 1) -> float:
    """Sample variance with ``ddof`` delta degrees of freedom (default 1)."""
    n = len(x)
    if n - ddof <= 0:
        raise ValueError("not enough observations for the requested ddof")
    m = mean(x)
    return sum((v - m) ** 2 for v in x) / (n - ddof)


@dataclass
class TTestResult:
    statistic: float
    df: float
    pvalue: float
    kind: str  # "student" or "welch"


@dataclass
class MannWhitneyResult:
    statistic: float  # U1 (U for the first group); u1/u2 both exposed below
    u1: float
    u2: float
    zscore: float
    pvalue: float
    method: str = "asymptotic"  # "exact" or "asymptotic"


@dataclass
class AnovaResult:
    statistic: float
    df_between: float
    df_within: float
    pvalue: float
    ss_between: float
    ss_within: float
    ss_total: float


@dataclass
class KruskalResult:
    statistic: float
    df: float
    pvalue: float


@dataclass
class LeveneResult:
    statistic: float
    df_between: float
    df_within: float
    pvalue: float


def _check_two(a: Sequence[float], b: Sequence[float]) -> None:
    if len(a) < 2 or len(b) < 2:
        raise ValueError("each group needs at least 2 observations")


def students_t(a: Sequence[float], b: Sequence[float]) -> TTestResult:
    """Independent two-sample Student's t-test (assumes equal variances)."""
    _check_two(a, b)
    n1, n2 = len(a), len(b)
    m1, m2 = mean(a), mean(b)
    v1, v2 = variance(a), variance(b)
    df = n1 + n2 - 2
    sp2 = ((n1 - 1) * v1 + (n2 - 1) * v2) / df
    se = math.sqrt(sp2 * (1.0 / n1 + 1.0 / n2))
    t = (m1 - m2) / se
    return TTestResult(t, float(df), t_sf_two_sided(t, df), "student")


def welch_t(a: Sequence[float], b: Sequence[float]) -> TTestResult:
    """Welch's unequal-variance two-sample t-test."""
    _check_two(a, b)
    n1, n2 = len(a), len(b)
    m1, m2 = mean(a), mean(b)
    v1, v2 = variance(a), variance(b)
    se2_1, se2_2 = v1 / n1, v2 / n2
    se = math.sqrt(se2_1 + se2_2)
    t = (m1 - m2) / se
    df = (se2_1 + se2_2) ** 2 / (
        se2_1 ** 2 / (n1 - 1) + se2_2 ** 2 / (n2 - 1)
    )
    return TTestResult(t, df, t_sf_two_sided(t, df), "welch")


def _rankdata(values: Sequence[float]) -> List[float]:
    """Average ranks (1-based), assigning tied values their mean rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    n = len(values)
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # mean of ranks i+1 .. j+1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _tie_term(values: Sequence[float]) -> float:
    """Sum of (t^3 - t) over tie groups, used for tie corrections."""
    counts: dict = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return sum(c ** 3 - c for c in counts.values() if c > 1)


def mann_whitney_u(a: Sequence[float], b: Sequence[float]) -> MannWhitneyResult:
    """Mann-Whitney U test (asymptotic, tie- and continuity-corrected).

    Matches ``scipy.stats.mannwhitneyu(..., method='asymptotic',
    use_continuity=True)``. The normal approximation is reliable for group
    sizes >= ~8; for tiny groups treat the p-value as approximate.
    """
    _check_two(a, b)
    n1, n2 = len(a), len(b)
    combined = list(a) + list(b)
    ranks = _rankdata(combined)
    r1 = sum(ranks[:n1])
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1

    n = n1 + n2
    mu = n1 * n2 / 2.0
    tie = _tie_term(combined)
    sigma2 = (n1 * n2 / 12.0) * ((n + 1) - tie / (n * (n - 1)))
    sigma = math.sqrt(sigma2) if sigma2 > 0 else 0.0

    u = max(u1, u2)  # use the larger U so the tail is the upper one
    if sigma == 0.0:
        z, p = 0.0, 1.0
    else:
        z = (u - mu - 0.5) / sigma  # continuity correction
        p = 2.0 * norm_sf(z)
        p = min(1.0, p)
    return MannWhitneyResult(u1, u1, u2, z, p)


def one_way_anova(groups: Sequence[Sequence[float]]) -> AnovaResult:
    """One-way ANOVA across k >= 2 independent groups."""
    groups = [list(g) for g in groups]
    if len(groups) < 2:
        raise ValueError("need at least 2 groups")
    if any(len(g) < 1 for g in groups):
        raise ValueError("every group needs at least 1 observation")
    n = sum(len(g) for g in groups)
    k = len(groups)
    if n - k <= 0:
        raise ValueError("not enough observations for the within-group df")
    grand = sum(sum(g) for g in groups) / n
    means = [mean(g) for g in groups]  # hoist: one O(n) pass per group, not per element
    ss_between = sum(len(g) * (mg - grand) ** 2 for g, mg in zip(groups, means))
    ss_within = sum(sum((v - mg) ** 2 for v in g) for g, mg in zip(groups, means))
    ss_total = ss_between + ss_within
    df_b = k - 1
    df_w = n - k
    ms_b = ss_between / df_b
    ms_w = ss_within / df_w
    if ms_w == 0:
        # No within-group variance. If groups also coincide (SS_between == 0)
        # the F ratio is 0/0 -> undefined; otherwise the separation is perfect.
        if ms_b == 0:
            f = float("nan")
            p = float("nan")
        else:
            f = math.inf
            p = 0.0
    else:
        f = ms_b / ms_w
        p = f_sf(f, df_b, df_w)
    return AnovaResult(f, float(df_b), float(df_w), p, ss_between, ss_within, ss_total)


def kruskal_wallis(groups: Sequence[Sequence[float]]) -> KruskalResult:
    """Kruskal-Wallis H test (tie-corrected chi-square approximation)."""
    groups = [list(g) for g in groups]
    if len(groups) < 2:
        raise ValueError("need at least 2 groups")
    combined: List[float] = []
    for g in groups:
        combined.extend(g)
    n = len(combined)
    ranks = _rankdata(combined)
    idx = 0
    rank_sums = []
    for g in groups:
        rs = sum(ranks[idx:idx + len(g)])
        rank_sums.append(rs)
        idx += len(g)
    h = 12.0 / (n * (n + 1)) * sum(
        rs ** 2 / len(g) for rs, g in zip(rank_sums, groups)
    ) - 3.0 * (n + 1)
    tie = _tie_term(combined)
    correction = 1.0 - tie / (n ** 3 - n)
    if correction == 0:
        correction = 1.0
    h /= correction
    df = len(groups) - 1
    return KruskalResult(h, float(df), chi2_sf(h, df))


def levene(groups: Sequence[Sequence[float]]) -> LeveneResult:
    """Levene's test for equal variances (Brown-Forsythe, median-centered).

    Matches ``scipy.stats.levene(..., center='median')``.
    """
    groups = [list(g) for g in groups]
    if len(groups) < 2:
        raise ValueError("need at least 2 groups")

    def _median(x: List[float]) -> float:
        s = sorted(x)
        m = len(s)
        mid = m // 2
        return s[mid] if m % 2 else (s[mid - 1] + s[mid]) / 2.0

    # Hoist the median: compute it once per group rather than once per element
    # (the naive comprehension made Levene O(n^2 log n) and hung on >10k rows).
    z_groups = []
    for g in groups:
        med = _median(g)
        z_groups.append([abs(v - med) for v in g])
    # Levene's statistic is an ANOVA F on the absolute deviations.
    res = one_way_anova(z_groups)
    return LeveneResult(res.statistic, res.df_between, res.df_within, res.pvalue)
