"""Tests for a monotone trend across ORDERED groups — pure standard library.

Why this exists
---------------
Plenty of clinical tables do not compare unordered arms. Dose-ranging trials
(placebo / low / high), exposure quartiles, disease-severity strata and
biomarker tertiles all have an *ordered* grouping column, and the question the
reviewer actually asks is "does this characteristic move monotonically across
the levels?" — the classic ``p for trend`` column. An omnibus ANOVA or
Kruskal-Wallis answers a different, weaker question ("are the groups different
in any way at all"), so it throws away the ordering and, with it, power.

Three tests are provided, one per data shape, chosen so the trend test always
belongs to the same family as the test already reported for that row:

``linear_contrast``
    Parametric linear trend: the orthogonal linear contrast of a one-way ANOVA,
    tested with the pooled within-group MSE. Used for rows summarized as
    mean (SD). With two groups it reduces **exactly** to Student's t.

``jonckheere_terpstra``
    Nonparametric ordered-alternatives test (Jonckheere 1954, Terpstra 1952),
    normal approximation with the standard tie correction. Used for rows
    summarized as median [IQR]. With two groups it reduces **exactly** to the
    tie-corrected Mann-Whitney U normal approximation *without* the continuity
    correction — this package's reported Mann-Whitney p applies that
    correction, so the two columns differ slightly at k=2.

``cochran_armitage``
    Trend in a proportion across ordered groups (Cochran 1954, Armitage 1955),
    for binary categorical rows. Its statistic equals ``N * r**2`` where ``r``
    is the Pearson correlation between the group score and the 0/1 outcome —
    an identity the tests assert against numpy-free hand computation.

Scores
------
``linear_contrast`` and ``cochran_armitage`` take explicit numeric scores, one
per group, so a real dose axis (0, 10, 40 mg) can be used instead of equally
spaced ranks. ``jonckheere_terpstra`` is rank-based and uses only the group
*order*, so custom scores do not change it — that asymmetry is documented in
the README rather than hidden.

All p-values are two-sided (a trend in either direction is reportable, and a
one-sided baseline test would be indefensible in a Table 1).
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .special import norm_sf, t_sf_two_sided

__all__ = [
    "TrendResult",
    "jonckheere_terpstra",
    "linear_contrast",
    "cochran_armitage",
    "default_scores",
]


@dataclass
class TrendResult:
    statistic: float          # z (JT, CA) or t (linear contrast)
    pvalue: float             # two-sided
    kind: str                 # "jonckheere" | "linear" | "cochran-armitage"
    df: Optional[float] = None
    scores: List[float] = field(default_factory=list)


def default_scores(k: int) -> List[float]:
    """Equally spaced scores 1..k — the default ordinal dose axis."""
    return [float(i + 1) for i in range(k)]


# --------------------------------------------------------------------------- #
# Jonckheere-Terpstra (nonparametric ordered alternatives)
# --------------------------------------------------------------------------- #
def _tie_sizes(values: Sequence[float]) -> List[int]:
    """Sizes of the tied blocks in the pooled sample (blocks of size 1 kept)."""
    out: List[int] = []
    ordered = sorted(values)
    i = 0
    n = len(ordered)
    while i < n:
        j = i
        while j < n and ordered[j] == ordered[i]:
            j += 1
        out.append(j - i)
        i = j
    return out


def jonckheere_terpstra(groups: Sequence[Sequence[float]]) -> TrendResult:
    """Jonckheere-Terpstra test for an ordered alternative across ``groups``.

    ``groups`` must already be in the intended order (the table's column
    order). The statistic is ``J = sum over i<j of U_ij`` where ``U_ij`` counts
    pairs with ``x_i < x_j`` and credits ties one half. The normal
    approximation uses the tie-corrected variance; with no ties it reduces
    algebraically to the familiar ``[N^2(2N+3) - sum n_i^2 (2 n_i + 3)] / 72``.

    Raises ``ValueError`` when fewer than two non-empty groups remain or the
    variance is zero (e.g. every observation identical), which leaves the
    statistic undefined.
    """
    gs = [sorted(float(v) for v in g) for g in groups if len(g) > 0]
    # NaN compares false against itself, which would stall the tie-block scan in
    # _tie_sizes forever; inf makes the variance meaningless. Every other public
    # entry point in this package raises rather than hangs, so match that.
    if any(not math.isfinite(v) for g in gs for v in g):
        raise ValueError("Jonckheere-Terpstra needs finite values")
    k = len(gs)
    if k < 2:
        raise ValueError("Jonckheere-Terpstra needs at least two non-empty groups")
    sizes = [len(g) for g in gs]
    n_total = sum(sizes)
    if n_total < 3:
        raise ValueError("Jonckheere-Terpstra needs at least three observations")

    # J: for every ordered pair of groups count (x_i < x_j) + 0.5 * ties.
    # Sorted groups + bisect keeps this O(k^2 * n log n) rather than O(N^2),
    # which matters on a 100k-row registry export.
    j_stat = 0.0
    for i in range(k):
        for j in range(i + 1, k):
            later = gs[j]
            m = len(later)
            for x in gs[i]:
                lo = bisect_left(later, x)
                hi = bisect_right(later, x)
                j_stat += (m - hi) + 0.5 * (hi - lo)

    n = float(n_total)
    expected = (n * n - math.fsum(float(s) * s for s in sizes)) / 4.0

    ties = _tie_sizes([v for g in gs for v in g])
    s_n1 = math.fsum(float(s) * (s - 1) * (2 * s + 5) for s in sizes)
    t_n1 = math.fsum(float(t) * (t - 1) * (2 * t + 5) for t in ties)
    var = (n * (n - 1) * (2 * n + 5) - s_n1 - t_n1) / 72.0
    if n_total > 2:
        s_n3 = math.fsum(float(s) * (s - 1) * (s - 2) for s in sizes)
        t_n3 = math.fsum(float(t) * (t - 1) * (t - 2) for t in ties)
        var += s_n3 * t_n3 / (36.0 * n * (n - 1) * (n - 2))
    s_n2 = math.fsum(float(s) * (s - 1) for s in sizes)
    t_n2 = math.fsum(float(t) * (t - 1) for t in ties)
    var += s_n2 * t_n2 / (8.0 * n * (n - 1))

    if not (var > 0) or not math.isfinite(var):
        raise ValueError("Jonckheere-Terpstra variance is zero or undefined")
    z = (j_stat - expected) / math.sqrt(var)
    return TrendResult(statistic=z, pvalue=2.0 * norm_sf(abs(z)),
                       kind="jonckheere",
                       scores=default_scores(k))


# --------------------------------------------------------------------------- #
# Parametric linear contrast (ANOVA trend)
# --------------------------------------------------------------------------- #
def linear_contrast(groups: Sequence[Sequence[float]],
                    scores: Optional[Sequence[float]] = None) -> TrendResult:
    """Linear-trend contrast across ordered groups, pooled-variance t test.

    Contrast coefficients are the centred scores ``c_i = x_i - mean(x)`` (so
    ``sum c_i == 0``); the estimate is ``L = sum c_i * mean_i`` and the standard
    error uses the one-way ANOVA pooled MSE on ``N - k`` degrees of freedom.
    With ``k == 2`` this is algebraically identical to Student's t test.

    ``scores`` defaults to 1..k. Groups with no observations are dropped
    together with their score, because a contrast coefficient cannot be
    attached to an empty arm.
    """
    kept = [(list(g), s) for g, s in
            zip(groups, scores if scores is not None
                else default_scores(len(groups))) if len(g) > 0]
    if len(kept) < 2:
        raise ValueError("linear trend needs at least two non-empty groups")
    vals = [[float(v) for v in g] for g, _ in kept]
    xs = [float(s) for _, s in kept]
    k = len(vals)
    sizes = [len(v) for v in vals]
    n_total = sum(sizes)
    df = n_total - k
    if df < 1:
        raise ValueError("linear trend needs residual degrees of freedom")

    means = [math.fsum(v) / len(v) for v in vals]
    sse = math.fsum(math.fsum((x - m) ** 2 for x in v)
                    for v, m in zip(vals, means))
    mse = sse / df
    xbar = math.fsum(xs) / k
    coefs = [x - xbar for x in xs]
    if not any(c != 0.0 for c in coefs):
        raise ValueError("trend scores are all identical — no linear contrast")
    # t = L / sqrt(MSE * sum c^2/n) is invariant to scaling the coefficients, but
    # c*c overflows for |score| ~ 1e160 (a plausible unit mix-up, and free to
    # guard against). Normalize to max |c| = 1 before squaring.
    scale = max(abs(c) for c in coefs)
    if math.isfinite(scale) and scale > 0:
        coefs = [c / scale for c in coefs]
    est = math.fsum(c * m for c, m in zip(coefs, means))
    denom = math.fsum(c * c / s for c, s in zip(coefs, sizes))
    se = math.sqrt(mse * denom)
    if not math.isfinite(se):
        raise ValueError("linear trend standard error is undefined")
    if se == 0.0:
        # Zero within-group variance. Either the contrast is also zero (the
        # groups are identical -> genuinely undefined) or every group is a
        # constant and the means move along the scores, which is a perfect
        # trend: report it the way one_way_anova reports its F=inf case.
        if est == 0.0:
            raise ValueError("linear trend statistic undefined (zero variance)")
        return TrendResult(statistic=math.inf if est > 0 else -math.inf,
                           pvalue=0.0, kind="linear", df=float(df), scores=xs)
    tstat = est / se
    return TrendResult(statistic=tstat, pvalue=t_sf_two_sided(tstat, df),
                       kind="linear", df=float(df), scores=xs)


# --------------------------------------------------------------------------- #
# Cochran-Armitage (trend in a proportion)
# --------------------------------------------------------------------------- #
def cochran_armitage(events: Sequence[float], totals: Sequence[float],
                     scores: Optional[Sequence[float]] = None) -> TrendResult:
    """Cochran-Armitage test for trend in a proportion across ordered groups.

    ``events[i]`` of ``totals[i]`` subjects in group ``i`` have the outcome.
    With scores ``x_i`` the statistic is

        T = sum r_i (x_i - xbar_w),  Var(T) = pbar (1 - pbar) * S_xx

    where ``xbar_w = sum n_i x_i / N`` and ``S_xx = sum n_i x_i^2 - (sum n_i
    x_i)^2 / N``. ``z**2`` equals ``N * r**2`` with ``r`` the Pearson
    correlation between the score and the 0/1 outcome, and for two groups it
    equals the uncorrected Pearson chi-square — both identities are asserted in
    the tests. The p-value is two-sided.

    Raises ``ValueError`` if no group has observations, the outcome is constant
    (every subject an event or none), or the scores carry no spread.
    """
    kept = [(float(r), float(n), float(s)) for r, n, s in
            zip(events, totals, scores if scores is not None
                else default_scores(len(totals))) if n > 0]
    if len(kept) < 2:
        raise ValueError("Cochran-Armitage needs at least two non-empty groups")
    rs = [r for r, _, _ in kept]
    ns = [n for _, n, _ in kept]
    xs = [s for _, _, s in kept]
    n_total = math.fsum(ns)
    r_total = math.fsum(rs)
    if n_total <= 0:
        raise ValueError("Cochran-Armitage needs at least one observation")
    if r_total <= 0 or r_total >= n_total:
        raise ValueError("Cochran-Armitage undefined for a constant outcome")
    pbar = r_total / n_total
    sum_nx = math.fsum(n * x for n, x in zip(ns, xs))
    xbar_w = sum_nx / n_total
    tstat = math.fsum(r * (x - xbar_w) for r, x in zip(rs, xs))
    sxx = math.fsum(n * x * x for n, x in zip(ns, xs)) - sum_nx * sum_nx / n_total
    var = pbar * (1.0 - pbar) * sxx
    if not (var > 0) or not math.isfinite(var):
        raise ValueError("Cochran-Armitage variance is zero or undefined")
    z = tstat / math.sqrt(var)
    return TrendResult(statistic=z, pvalue=2.0 * norm_sf(abs(z)),
                       kind="cochran-armitage", scores=xs)
