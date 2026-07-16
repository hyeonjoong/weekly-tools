"""Weighted summary statistics and weighted SMD — pure standard library.

A Table 1 for a propensity-score / IPTW analysis must be *weighted*: after
inverse-probability-of-treatment weighting (or survey sampling), the balance a
reader cares about is balance in the **weighted** pseudo-population, not the
raw one. This module supplies the weighted analogues of everything the
unweighted builder computes:

    - ``weighted_mean`` / ``weighted_var`` / ``weighted_sd``
    - ``weighted_quantile`` (median / IQR / min / max)
    - ``kish_ess`` — Kish's effective sample size, the honest "n" of a
      weighted group
    - ``weighted_continuous_smd`` / ``weighted_categorical_smd`` — the
      weighted SMD of Austin & Stuart (2015)

Design decisions worth knowing
------------------------------
**Weight semantics.** Weights are treated as *reliability* (precision) weights,
not frequency counts: doubling every weight does not change any estimate or its
spread. This is the right reading for IPTW/survey weights, where a weight of
2.5 means "this subject represents 2.5 subjects in the target population" but
carries only one subject's worth of information.

**Variance.** ``weighted_var`` uses the standard unbiased reliability-weight
estimator that Austin & Stuart (2015) specify for the weighted SMD:

    s_w^2 = ( sum(w) / ( sum(w)^2 - sum(w^2) ) ) * sum( w_i * (x_i - m_w)^2 )

With all weights equal this reduces *exactly* to the ordinary ddof=1 sample
variance (sum(w)=n and sum(w)^2-sum(w^2) = n(n-1), so the factor is 1/(n-1)),
which is the invariant the tests pin.

**Quantiles.** ``weighted_quantile`` generalizes the type-7 (numpy/R default)
quantile that the unweighted builder uses, so an equally-weighted table is
numerically identical to the unweighted one. Weights are normalized to sum to
n, and sorted point i (1-based) is placed at probability

    p_i = ( cumsum(w_hat)_i - (w_hat_i + 1) / 2 ) / (n - 1)

which collapses to the type-7 positions (i-1)/(n-1) when every w_hat_i is 1.
The quantile is then a linear interpolation of the inverse CDF through those
points, clamped to the extreme order statistics outside [p_1, p_n]. The p_i are
strictly increasing for positive weights (consecutive gaps are
(w_hat_i + w_hat_{i-1})/2 > 0), so the interpolation is always well defined.
This is a documented convention, not a match to any particular external
package — see 방법론 노트 in the README.

Reference:
    Austin P.C., Stuart E.A. (2015). "Moving towards best practice when using
    inverse probability of treatment weighting (IPTW) using the propensity
    score to estimate causal treatment effects in observational studies."
    Statistics in Medicine 34(28): 3661-3679.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from .smd import MAX_SMD_LEVELS, _invert

__all__ = [
    "weighted_mean",
    "weighted_var",
    "weighted_sd",
    "weighted_quantile",
    "kish_ess",
    "weighted_continuous_smd",
    "weighted_categorical_smd",
]


def _clean(values: Sequence[float], w: Sequence[float]
           ) -> Tuple[List[float], List[float]]:
    """Drop non-positive / non-finite weights, pairing values with weights.

    A zero weight contributes nothing to any weighted estimate, so dropping it
    is exact rather than approximate; a negative or non-finite weight is not
    meaningful and is rejected upstream by the CLI, but we defend here too so
    no caller can produce a silently corrupt mean.
    """
    if len(values) != len(w):
        raise ValueError("values and weights must be the same length")
    xs: List[float] = []
    ws: List[float] = []
    for x, wi in zip(values, w):
        if not math.isfinite(wi) or wi <= 0.0:
            continue
        xs.append(float(x))
        ws.append(float(wi))
    return xs, ws


def weighted_mean(values: Sequence[float], w: Sequence[float]) -> float:
    """Weighted mean; NaN if no positive weight remains."""
    xs, ws = _clean(values, w)
    sw = math.fsum(ws)
    if not xs or sw <= 0.0:
        return float("nan")
    return math.fsum(x * wi for x, wi in zip(xs, ws)) / sw


def weighted_var(values: Sequence[float], w: Sequence[float]) -> float:
    """Unbiased reliability-weight variance (Austin & Stuart 2015).

    Returns NaN when fewer than 2 positively-weighted observations remain, or
    when the weights are so concentrated that sum(w)^2 == sum(w^2) (all the
    mass on a single observation), where the estimator is undefined.
    """
    xs, ws = _clean(values, w)
    if len(xs) < 2:
        return float("nan")
    sw = math.fsum(ws)
    sw2 = math.fsum(wi * wi for wi in ws)
    denom = sw * sw - sw2
    if denom <= 0.0:
        return float("nan")
    m = math.fsum(x * wi for x, wi in zip(xs, ws)) / sw
    ss = math.fsum(wi * (x - m) ** 2 for x, wi in zip(xs, ws))
    return (sw / denom) * ss


def weighted_sd(values: Sequence[float], w: Sequence[float]) -> float:
    v = weighted_var(values, w)
    if math.isnan(v):
        return float("nan")
    return math.sqrt(max(0.0, v))


def kish_ess(w: Sequence[float]) -> float:
    """Kish's effective sample size: (sum w)^2 / sum(w^2).

    Equals n exactly when the weights are all equal, and shrinks as the weights
    become more unequal — the honest "how much information is really here"
    denominator for a weighted group. Returns 0.0 when no positive weight
    remains.
    """
    ws = [float(x) for x in w if math.isfinite(x) and x > 0.0]
    if not ws:
        return 0.0
    sw = math.fsum(ws)
    sw2 = math.fsum(x * x for x in ws)
    if sw2 <= 0.0:
        return 0.0
    return (sw * sw) / sw2


def weighted_quantile(values: Sequence[float], w: Sequence[float], q: float
                      ) -> float:
    """Type-7-consistent weighted quantile (see the module docstring).

    Reduces exactly to the unweighted type-7 quantile when all weights are
    equal. ``q`` is clamped to [0, 1]; NaN is returned for an empty input.
    """
    xs, ws = _clean(values, w)
    if not xs:
        return float("nan")
    n = len(xs)
    if n == 1:
        return xs[0]
    q = min(1.0, max(0.0, q))
    # The 0th/100th percentile of a sample IS its minimum/maximum, whatever the
    # weights: no weighting scheme can make some other value the smallest. Handle
    # the endpoints before interpolating, because p_1 goes NEGATIVE whenever the
    # first point's weight is below average (and p_n falls below 1 when the last
    # point's is), which would otherwise put q=0 strictly inside the grid and
    # return an interior value for the minimum.
    if q <= 0.0:
        return min(xs)
    if q >= 1.0:
        return max(xs)

    order = sorted(range(n), key=lambda i: xs[i])
    sx = [xs[i] for i in order]
    sw_ = [ws[i] for i in order]

    total = math.fsum(sw_)
    if total <= 0.0:
        return float("nan")
    # Normalize the weights to sum to n, so equal weights become exactly 1 and
    # the positions below collapse to the type-7 grid (i-1)/(n-1).
    wh = [x * n / total for x in sw_]

    probs: List[float] = []
    run = 0.0
    for i in range(n):
        run += wh[i]
        probs.append((run - (wh[i] + 1.0) / 2.0) / (n - 1))

    # Outside the interpolation range, clamp to the extreme order statistics.
    if q <= probs[0]:
        return sx[0]
    if q >= probs[-1]:
        return sx[-1]
    # probs is strictly increasing, so a simple scan/bisect is well defined.
    lo = 0
    hi = n - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if probs[mid] <= q:
            lo = mid
        else:
            hi = mid
    span = probs[hi] - probs[lo]
    if span <= 0.0:
        return sx[lo]
    frac = (q - probs[lo]) / span
    return sx[lo] + frac * (sx[hi] - sx[lo])


# --------------------------------------------------------------------------- #
# weighted SMD (Austin & Stuart 2015)
# --------------------------------------------------------------------------- #
def weighted_continuous_smd(a: Sequence[float], wa: Sequence[float],
                            b: Sequence[float], wb: Sequence[float]
                            ) -> Optional[float]:
    """Absolute weighted SMD for a continuous variable across two groups.

    d = |m_w1 - m_w2| / sqrt( (s_w1^2 + s_w2^2) / 2 )

    Returns None if either group has < 2 positively-weighted observations or a
    weighted variance is undefined. If the pooled weighted spread is zero, the
    SMD is 0 when the weighted means coincide and inf otherwise (mirroring the
    unweighted ``smd.continuous_smd`` convention).
    """
    xa, ua = _clean(a, wa)
    xb, ub = _clean(b, wb)
    if len(xa) < 2 or len(xb) < 2:
        return None
    m1 = weighted_mean(xa, ua)
    m2 = weighted_mean(xb, ub)
    v1 = weighted_var(xa, ua)
    v2 = weighted_var(xb, ub)
    if any(math.isnan(v) for v in (m1, m2, v1, v2)):
        return None
    denom = math.sqrt((v1 + v2) / 2.0)
    if denom == 0.0:
        return 0.0 if m1 == m2 else float("inf")
    return abs(m1 - m2) / denom


def weighted_categorical_smd(wcounts1: Sequence[float],
                             wcounts2: Sequence[float]) -> Optional[float]:
    """Weighted multivariate (Yang & Dalton) SMD from per-level weight sums.

    ``wcounts1``/``wcounts2`` are the summed weights per level, in a common
    level order. The proportions are the weighted level proportions; the rest
    of the computation is identical to the unweighted multivariate SMD, so the
    binary case reduces to the familiar (p1-p2)/sqrt((p1q1+p2q2)/2).
    """
    if len(wcounts1) != len(wcounts2):
        raise ValueError("count vectors must share the same levels")
    k = len(wcounts1)
    n1 = math.fsum(wcounts1)
    n2 = math.fsum(wcounts2)
    if k < 2 or n1 <= 0.0 or n2 <= 0.0:
        return None
    if k > MAX_SMD_LEVELS:
        return None
    p1 = [c / n1 for c in wcounts1]
    p2 = [c / n2 for c in wcounts2]

    if k == 2:
        denom = math.sqrt((p1[0] * (1 - p1[0]) + p2[0] * (1 - p2[0])) / 2.0)
        if denom == 0.0:
            return 0.0 if p1[0] == p2[0] else float("inf")
        return abs(p1[0] - p2[0]) / denom

    d = k - 1
    diff = [p1[i] - p2[i] for i in range(d)]

    def cov(p: Sequence[float]) -> List[List[float]]:
        return [[(p[i] * (1 - p[i]) if i == j else -p[i] * p[j])
                 for j in range(d)] for i in range(d)]

    s1 = cov(p1)
    s2 = cov(p2)
    s = [[(s1[i][j] + s2[i][j]) / 2.0 for j in range(d)] for i in range(d)]
    inv = _invert(s)
    if inv is None:
        return None
    quad = 0.0
    for i in range(d):
        for j in range(d):
            quad += diff[i] * inv[i][j] * diff[j]
    if quad < 0:
        quad = 0.0
    return math.sqrt(quad)
