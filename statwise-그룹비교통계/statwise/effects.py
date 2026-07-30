"""Effect-size measures with confidence intervals — pure standard library.

All formulas are the conventional ones reported in clinical / psychology
papers. Confidence intervals for Cohen's d use the standard large-sample
normal approximation of its standard error (Hedges & Olkin, 1985).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence

from .special import norm_ppf
from .tests_stat import _rankdata, mean, variance

__all__ = [
    "EffectSize",
    "cohens_d",
    "cohens_dz",
    "rank_biserial",
    "matched_rank_biserial",
    "eta_squared",
    "eta_squared_h",
    "cliffs_delta",
]


@dataclass
class EffectSize:
    name: str
    value: float
    ci_low: float | None = None
    ci_high: float | None = None
    magnitude: str = ""
    #: coverage of (ci_low, ci_high); tracks --alpha so the printed label can
    #: never disagree with the interval it labels.
    conf: float = 0.95


def _d_magnitude(d: float) -> str:
    ad = abs(d)
    if ad < 0.2:
        return "negligible"
    if ad < 0.5:
        return "small"
    if ad < 0.8:
        return "medium"
    return "large"


def cohens_d(a: Sequence[float], b: Sequence[float], hedges: bool = True,
             ci: float = 0.95) -> EffectSize:
    """Cohen's d (pooled SD). If ``hedges`` apply the small-sample correction (g).

    CI uses the Hedges-Olkin normal approximation:
        SE(d) = sqrt((n1+n2)/(n1*n2) + d^2 / (2*(n1+n2))).
    """
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        raise ValueError("each group needs at least 2 observations")
    v1, v2 = variance(a), variance(b)
    sp = math.sqrt(((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2))
    if sp == 0:
        raise ValueError("pooled standard deviation is zero")
    d = (mean(a) - mean(b)) / sp
    name = "Cohen's d"
    if hedges:
        j = 1.0 - 3.0 / (4.0 * (n1 + n2) - 9.0)
        d *= j
        name = "Hedges' g"
    se = math.sqrt((n1 + n2) / (n1 * n2) + d * d / (2.0 * (n1 + n2)))
    z = _z_for_ci(ci)
    return EffectSize(name, d, d - z * se, d + z * se, _d_magnitude(d), ci)


def cohens_dz(a: Sequence[float], b: Sequence[float], ci: float = 0.95
              ) -> EffectSize:
    """Cohen's d_z for paired data: mean(diff) / SD(diff).

    This is the standardized *paired* effect size (the effect size that pairs
    with a paired t-test), not the between-group d.  CI uses the standard
    normal approximation SE(d_z) = sqrt(1/n + d_z^2/(2n)).
    """
    if len(a) != len(b):
        raise ValueError("paired data must have equal length")
    diffs = [float(x) - float(y) for x, y in zip(a, b)]
    n = len(diffs)
    if n < 2:
        raise ValueError("need at least 2 pairs")
    md = mean(diffs)
    sd = math.sqrt(variance(diffs))
    if sd == 0:
        raise ValueError("standard deviation of the differences is zero")
    dz = md / sd
    se = math.sqrt(1.0 / n + dz * dz / (2.0 * n))
    z = _z_for_ci(ci)
    return EffectSize("Cohen's dz", dz, dz - z * se, dz + z * se,
                      _d_magnitude(dz), ci)


#: d_z scales with the pre-post correlation, so Cohen's between-subject
#: small/medium/large benchmarks do not transfer to it. The label is kept for
#: continuity with the rest of the report but must be qualified in the output.
DZ_MAGNITUDE_CAVEAT = (
    "Cohen's dz의 크기 라벨은 집단 간 d 기준(0.2/0.5/0.8)을 빌려 쓴 것입니다. "
    "dz는 전·후 상관에 따라 커지므로 집단 간 효과크기와 직접 비교하지 마세요.")


def matched_rank_biserial(w_plus: float, w_minus: float) -> EffectSize:
    """Matched-pairs rank-biserial correlation for the signed-rank test.

    r = (W+ - W-) / (W+ + W-), ranging -1..1; positive when ``a`` tends to
    exceed ``b`` (Kerby, 2014).
    """
    total = w_plus + w_minus
    r = 0.0 if total == 0 else (w_plus - w_minus) / total
    ar = abs(r)
    mag = "large" if ar >= 0.474 else "medium" if ar >= 0.33 else "small" \
        if ar >= 0.147 else "negligible"
    return EffectSize("matched rank-biserial r", r, None, None, mag)


def _z_for_ci(ci: float) -> float:
    """Two-sided normal critical value for a given confidence level."""
    if not 0.0 < ci < 1.0:
        raise ValueError("confidence level must be in (0, 1)")
    return norm_ppf(1.0 - (1.0 - ci) / 2.0)


def rank_biserial(a: Sequence[float], b: Sequence[float]) -> EffectSize:
    """Rank-biserial correlation for Mann-Whitney (r = 2*U1/(n1*n2) - 1).

    Ranges from -1 to 1 and is positive when group ``a`` tends to exceed group
    ``b`` (sign-consistent with the reported U1). Equals Cliff's delta.
    """
    n1, n2 = len(a), len(b)
    combined = list(a) + list(b)
    ranks = _rankdata(combined)
    r1 = sum(ranks[:n1])
    u1 = r1 - n1 * (n1 + 1) / 2.0
    r = 2.0 * u1 / (n1 * n2) - 1.0  # = -(1 - 2U1/(n1 n2)); positive when a > b
    ar = abs(r)
    mag = "large" if ar >= 0.474 else "medium" if ar >= 0.33 else "small" \
        if ar >= 0.147 else "negligible"
    return EffectSize("rank-biserial r", r, None, None, mag)


def _cliffs_delta_ci(a: Sequence[float], b: Sequence[float], delta: float,
                     ci: float) -> tuple:
    """Cliff's (1993) consistent variance estimate for delta, on the logit-free
    normal scale. Returns (lo, hi) clamped to [-1, 1], or (None, None) when the
    variance is not estimable (n < 2 in either group, or a degenerate delta)."""
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return (None, None)
    # d_ij = sign(a_i - b_j); row and column means of the dominance matrix
    di = []
    for x in a:
        di.append(sum((x > y) - (x < y) for y in b) / n2)
    dj = []
    for y in b:
        dj.append(sum((x > y) - (x < y) for x in a) / n1)
    s2i = sum((v - delta) ** 2 for v in di) / (n1 - 1)
    s2j = sum((v - delta) ** 2 for v in dj) / (n2 - 1)
    var = (s2i / n1) + (s2j / n2)
    if var <= 0:
        return (None, None)
    z = _z_for_ci(ci)
    half = z * math.sqrt(var)
    return (max(-1.0, delta - half), min(1.0, delta + half))


def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> EffectSize:
    """Cliff's delta = P(a>b) - P(a<b), computed exactly over all pairs."""
    gt = lt = 0
    for x in a:
        for y in b:
            if x > y:
                gt += 1
            elif x < y:
                lt += 1
    delta = (gt - lt) / (len(a) * len(b))
    ad = abs(delta)
    mag = "large" if ad >= 0.474 else "medium" if ad >= 0.33 else "small" \
        if ad >= 0.147 else "negligible"
    return EffectSize("Cliff's delta", delta, None, None, mag)


def eta_squared(groups: Sequence[Sequence[float]], ss_between: float,
                ss_total: float) -> EffectSize:
    """Eta-squared = SS_between / SS_total for ANOVA."""
    if ss_total == 0:
        raise ValueError("total sum of squares is zero")
    if not (math.isfinite(ss_total) and math.isfinite(ss_between)):
        # ss_between finite over an overflowed total silently reported
        # eta-squared = 0.000 "negligible" where the truth was 0.57 "large"
        raise ValueError(
            "제곱합이 배정밀도 범위를 넘어 효과크기(η²)를 계산할 수 없습니다")
    e = ss_between / ss_total
    mag = "large" if e >= 0.14 else "medium" if e >= 0.06 else "small" \
        if e >= 0.01 else "negligible"
    return EffectSize("eta-squared", e, None, None, mag)


def eta_squared_h(h: float, n: int, k: int) -> EffectSize:
    """Eta-squared for the Kruskal-Wallis H statistic: (H - k + 1)/(n - k).

    This is the rank analogue of ANOVA's eta-squared (not Tomczak's
    epsilon-squared H/(n-1)); its magnitude thresholds match eta-squared.
    """
    denom = n - k
    if denom <= 0:
        raise ValueError("n must exceed the number of groups")
    e = (h - k + 1.0) / denom
    e = max(0.0, e)
    mag = "large" if e >= 0.14 else "medium" if e >= 0.06 else "small" \
        if e >= 0.01 else "negligible"
    return EffectSize("eta-squared (H)", e, None, None, mag)
