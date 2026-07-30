"""Group-comparison statistical tests — pure standard library.

Each function returns a small dataclass with the statistic, degrees of freedom
(where applicable) and a two-sided p-value. p-values are computed from the
exact Student-t / F / normal / chi-square distributions implemented in
``special`` and match SciPy to ~1e-9 (Mann-Whitney/Kruskal-Wallis use the
tie-corrected normal / chi-square asymptotic approximation).
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from typing import List, Sequence

from . import exact
from .special import chi2_sf, f_sf, norm_sf, t_sf_two_sided

__all__ = [
    "TTestResult",
    "MannWhitneyResult",
    "AnovaResult",
    "WelchAnovaResult",
    "KruskalResult",
    "LeveneResult",
    "students_t",
    "welch_t",
    "mann_whitney_u",
    "one_way_anova",
    "welch_anova",
    "kruskal_wallis",
    "levene",
    "mean",
    "variance",
]


def mean(x: Sequence[float]) -> float:
    return sum(x) / len(x)


def variance(x: Sequence[float], ddof: int = 1) -> float:
    """Sample variance with ``ddof`` delta degrees of freedom (default 1).

    Deviations are rescaled by the largest one before squaring, so the textbook
    ``sum((v - m) ** 2)`` -- which overflows above ~1e154 -- no longer raises out
    of the CLI. The rescaling does *not* rescue underflow: below ~1e-162 the
    double-precision variance genuinely is 0, and it still returns 0 (the caller
    then reports zero variance rather than crashing). ``fsum`` also keeps the sum
    exact for long columns.
    """
    n = len(x)
    if n - ddof <= 0:
        raise ValueError("not enough observations for the requested ddof")
    m = mean(x)
    dev = [v - m for v in x]
    scale = max((abs(d) for d in dev), default=0.0)
    if scale == 0.0:
        return 0.0
    ss = math.fsum((d / scale) ** 2 for d in dev)
    return (ss / (n - ddof)) * scale * scale


@dataclass
class TTestResult:
    statistic: float
    df: float
    pvalue: float
    kind: str  # "student" or "welch"


@dataclass
class MannWhitneyResult:
    statistic: float  # U (smaller of U1, U2 by convention -> we report U1)
    u1: float
    u2: float
    zscore: float
    pvalue: float
    method: str = "asymptotic"  # "asymptotic" or "exact"


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
class WelchAnovaResult:
    statistic: float
    df_between: float
    df_within: float  # fractional (Welch-Satterthwaite)
    pvalue: float


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
    if se == 0.0:
        raise ValueError(
            "두 그룹 모두 분산이 0이라 t-검정을 정의할 수 없습니다 "
            "(값이 모두 같거나, 크기가 너무 작아 분산이 배정밀도에서 "
            "0으로 사라졌을 수 있습니다 — 단위를 키워 보세요).")
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
    if se == 0.0:
        raise ValueError(
            "두 그룹 모두 분산이 0이라 Welch t-검정을 정의할 수 없습니다 "
            "(값이 모두 같거나, 크기가 너무 작아 분산이 배정밀도에서 "
            "0으로 사라졌을 수 있습니다 — 단위를 키워 보세요).")
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


def mann_whitney_u(a: Sequence[float], b: Sequence[float],
                   method: str = "auto") -> MannWhitneyResult:
    """Mann-Whitney U test.

    ``method='auto'`` computes the **exact** permutation p-value when the
    combined sample has no ties and both groups are small
    (<= ``exact.MWU_EXACT_MAX_N``); otherwise it uses the tie- and
    continuity-corrected normal approximation, matching
    ``scipy.stats.mannwhitneyu(..., method='asymptotic', use_continuity=True)``.
    Pass ``method='asymptotic'`` or ``'exact'`` to force one.  The exact test is
    only valid without ties; a forced ``'exact'`` on tied data falls back to the
    approximation rather than report a wrong p-value.
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

    has_ties = tie > 0
    use_exact = (method == "exact" or
                 (method == "auto" and not has_ties
                  and n1 <= exact.MWU_EXACT_MAX_N
                  and n2 <= exact.MWU_EXACT_MAX_N))
    if method == "exact" and has_ties:
        use_exact = False

    # z is always reported (reference); it drives the p-value only when asymptotic.
    if sigma == 0.0:
        z = 0.0
    else:
        u_big = max(u1, u2)
        z = (u_big - mu - 0.5) / sigma  # continuity correction

    if use_exact:
        p = exact.mannwhitney_exact_p(u1, n1, n2)
        return MannWhitneyResult(u1, u1, u2, z, p, "exact")

    if sigma == 0.0:
        p = 1.0
    else:
        p = min(1.0, 2.0 * norm_sf(z))
    return MannWhitneyResult(u1, u1, u2, z, p, "asymptotic")


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
    grand = math.fsum(math.fsum(g) for g in groups) / n
    # F is a ratio of mean squares, so a common rescaling of every observation
    # leaves it unchanged. Rescaling by the largest deviation keeps the sums of
    # squares inside double range for data around 1e300 (raw counts, ng/mL),
    # which otherwise raised OverflowError straight out of the CLI.
    spread = max((abs(v - grand) for g in groups for v in g), default=0.0)
    if not math.isfinite(spread):
        raise ValueError("group values are not finite")
    unit = spread if spread > 0.0 else 1.0
    scaled = [[(v - grand) / unit for v in g] for g in groups]
    # Hoist the per-group mean: evaluating mean(g) inside the inner loop made
    # this O(n^2) and turned a 200k-row file into a ~50-minute apparent hang.
    means = [mean(g) for g in scaled]
    ss_between = math.fsum(len(g) * m * m for g, m in zip(scaled, means))
    ss_within = math.fsum(
        math.fsum((v - m) ** 2 for v in g) for g, m in zip(scaled, means))
    # F and eta-squared are ratios, so BOTH are formed from the scaled sums --
    # restoring first and dividing after was a real regression: at unit ~1e-161
    # the restored values become subnormal, lose their mantissa, and F came back
    # as inf with p printed as <0.001 where the truth was F = 14.38, p = 1.9e-07.
    scaled_b, scaled_w = ss_between, ss_within
    scaled_total = scaled_b + scaled_w
    # Restore to the caller's units only for the *reported* sums of squares, and
    # only when every restored value (including their sum, which can overflow on
    # its own) stays finite and normal.
    factor = unit * unit
    restored = None
    if math.isfinite(factor) and factor > 0.0:
        rb, rw = scaled_b * factor, scaled_w * factor
        rt = rb + rw
        # Require *normal* floats, not merely non-zero: a subnormal result has
        # already lost most of its mantissa, which showed up as eta-squared
        # drifting from 0.2915 to 0.3000 at a scale of 1e-162.
        tiny = sys.float_info.min

        def _usable(restored_v: float, scaled_v: float) -> bool:
            if scaled_v == 0.0:
                return restored_v == 0.0
            return math.isfinite(restored_v) and restored_v >= tiny
        if (_usable(rb, scaled_b) and _usable(rw, scaled_w)
                and _usable(rt, scaled_total)):
            restored = (rb, rw, rt)
    ss_between, ss_within, ss_total = restored or (scaled_b, scaled_w,
                                                   scaled_total)
    df_b = k - 1
    df_w = n - k
    ms_b = scaled_b / df_b
    ms_w = scaled_w / df_w
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


def welch_anova(groups: Sequence[Sequence[float]]) -> WelchAnovaResult:
    """Welch's one-way ANOVA (does not assume equal variances).

    The recommended omnibus test when groups are ~normal but heteroscedastic.
    Matches ``pingouin.welch_anova`` / R's ``oneway.test(var.equal=FALSE)``.
    """
    groups = [list(g) for g in groups]
    k = len(groups)
    if k < 2:
        raise ValueError("need at least 2 groups")
    for g in groups:
        if len(g) < 2:
            raise ValueError("every group needs at least 2 observations")
    ni = [len(g) for g in groups]
    mi = [mean(g) for g in groups]
    vi = [variance(g) for g in groups]
    if any(v == 0 for v in vi):
        raise ValueError("Welch's ANOVA is undefined when a group has zero variance")
    wi = [n / v for n, v in zip(ni, vi)]
    sw = sum(wi)
    xbar = sum(w * m for w, m in zip(wi, mi)) / sw
    num = sum(w * (m - xbar) ** 2 for w, m in zip(wi, mi)) / (k - 1)
    tmp = sum((1.0 - w / sw) ** 2 / (n - 1) for w, n in zip(wi, ni))
    denom = 1.0 + 2.0 * (k - 2) / (k ** 2 - 1) * tmp
    f = num / denom
    df1 = k - 1
    df2 = (k ** 2 - 1) / (3.0 * tmp)
    return WelchAnovaResult(f, float(df1), df2, f_sf(f, df1, df2))


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

    # Same hoist as above: _median(g) sorts the whole group, so calling it once
    # per element was O(n^2 log n) on the default analysis path.
    z_groups = []
    for g in groups:
        med = _median(g)
        z_groups.append([abs(v - med) for v in g])
    # Levene's statistic is an ANOVA F on the absolute deviations.
    res = one_way_anova(z_groups)
    return LeveneResult(res.statistic, res.df_between, res.df_within, res.pvalue)
