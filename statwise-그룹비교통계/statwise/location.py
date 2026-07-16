"""Distribution-free location estimates and confidence intervals.

Non-parametric tests (Mann-Whitney, Wilcoxon signed-rank) give a p-value and a
standardized effect, but reviewers and CONSORT/ICH-E9 reporting expect an
*estimate of the location difference with a confidence interval*.  The
Hodges-Lehmann estimator fills that gap:

* Two independent samples (Mann-Whitney): HL = median of all pairwise
  differences ``a_i - b_j``.  The distribution-free CI is the pair of order
  statistics of those differences whose indices come from the Mann-Whitney U
  null distribution (Hollander & Wolfe, 4.17) — this is exactly the set of
  shifts not rejected by the test (test inversion).
* Paired (Wilcoxon signed-rank): HL = median of the Walsh averages
  ``(d_i + d_j)/2`` for ``i <= j``; the CI order-statistic indices come from
  the signed-rank W+ null distribution.

Both use the exact null distribution for small samples (reusing ``exact``) and
the normal approximation otherwise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from . import exact
from .special import norm_cdf

__all__ = [
    "LocationEstimate",
    "hodges_lehmann_independent",
    "hodges_lehmann_paired",
]

# Enumerating all pairwise differences / Walsh averages is O(n^2); cap so a
# pathologically large input can't allocate a giant list. Above the cap we still
# return the estimate via a (sampled) fallback is NOT done — instead we skip the
# CI gracefully. These caps are generous for clinical sample sizes.
_MAX_PAIRS = 4_000_000  # ~2800 x 2800 independent, or n(n+1)/2 with n~2800


@dataclass
class LocationEstimate:
    name: str
    estimate: float
    ci_low: Optional[float]
    ci_high: Optional[float]
    method: str  # "exact" or "asymptotic"
    conf: float


def _z_for_ci(conf: float) -> float:
    target = 1.0 - (1.0 - conf) / 2.0
    lo, hi = 0.0, 10.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if norm_cdf(mid) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _median_sorted(s: Sequence[float]) -> float:
    m = len(s)
    mid = m // 2
    return s[mid] if m % 2 else (s[mid - 1] + s[mid]) / 2.0


def _mwu_trim_index(n1: int, n2: int, alpha: float) -> Optional[int]:
    """Number of extreme pairwise differences to trim at each end for the CI.

    Returns ``k`` such that the CI is ``(d_(k+1), d_(N-k))`` (1-based order
    statistics, N = n1*n2).  Uses the exact U null distribution when small,
    else the normal approximation.  Returns None if no non-degenerate CI at
    this level (k would leave nothing).
    """
    n = n1 * n2
    half = alpha / 2.0
    if n1 <= exact.MWU_EXACT_MAX_N and n2 <= exact.MWU_EXACT_MAX_N:
        pmf = exact.mannwhitney_u_pmf(n1, n2)
        # largest k with P(U <= k-1) <= alpha/2   (cumulative from the low tail)
        cum = 0.0
        k = 0
        for u in range(len(pmf)):
            # P(U <= u-1) is cum BEFORE adding pmf[u]
            if cum <= half:
                k = u  # d_(k+1)=d_(u+1); trimming u points means index u
            else:
                break
            cum += pmf[u]
        # k counts how many low-tail points satisfy P(U<=k-1)<=alpha/2
    else:
        z = _z_for_ci(1.0 - alpha)  # == z_{1-alpha/2}
        mu = n / 2.0
        sigma = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12.0)
        k = int(math.floor(mu - z * sigma))
        if k < 0:
            k = 0
    if k <= 0 or 2 * k >= n:
        return None
    return k


def _signed_rank_trim_index(n: int, alpha: float) -> Optional[int]:
    """Trim index for the Walsh-average CI (paired), from the W+ null dist."""
    m = n * (n + 1) // 2
    half = alpha / 2.0
    if n <= exact.SIGNED_RANK_EXACT_MAX_N:
        pmf = exact.signed_rank_pmf(n)
        cum = 0.0
        k = 0
        for w in range(len(pmf)):
            if cum <= half:
                k = w
            else:
                break
            cum += pmf[w]
    else:
        z = _z_for_ci(1.0 - alpha)
        mu = n * (n + 1) / 4.0
        sigma = math.sqrt(n * (n + 1) * (2 * n + 1) / 24.0)
        k = int(math.floor(mu - z * sigma))
        if k < 0:
            k = 0
    if k <= 0 or 2 * k >= m:
        return None
    return k


def hodges_lehmann_independent(a: Sequence[float], b: Sequence[float],
                               conf: float = 0.95) -> LocationEstimate:
    """Hodges-Lehmann median shift ``a - b`` with a distribution-free CI."""
    n1, n2 = len(a), len(b)
    if n1 < 1 or n2 < 1:
        raise ValueError("both groups need at least one observation")
    if n1 * n2 > _MAX_PAIRS:
        # too large to enumerate; estimate via medians, skip CI
        sa = sorted(float(x) for x in a)
        sb = sorted(float(x) for x in b)
        est = _median_sorted(sa) - _median_sorted(sb)
        return LocationEstimate("Hodges-Lehmann median difference", est,
                                None, None, "skipped (n too large)", conf)
    diffs = sorted(float(x) - float(y) for x in a for y in b)
    est = _median_sorted(diffs)
    alpha = 1.0 - conf
    method = "exact" if (n1 <= exact.MWU_EXACT_MAX_N
                         and n2 <= exact.MWU_EXACT_MAX_N) else "asymptotic"
    k = _mwu_trim_index(n1, n2, alpha)
    if k is None:
        return LocationEstimate("Hodges-Lehmann median difference", est,
                                None, None, method, conf)
    n = n1 * n2
    lo = diffs[k - 1]        # d_(k)   (1-based) -> index k-1
    hi = diffs[n - k]        # d_(N-k+1) -> index N-k
    return LocationEstimate("Hodges-Lehmann median difference", est, lo, hi,
                            method, conf)


def hodges_lehmann_paired(diffs: Sequence[float],
                          conf: float = 0.95) -> LocationEstimate:
    """Hodges-Lehmann pseudo-median of paired differences with a CI.

    ``diffs`` are the within-pair differences ``a_i - b_i`` (zeros included:
    Walsh averages are formed from all differences).
    """
    d = [float(v) for v in diffs]
    n = len(d)
    if n < 1:
        raise ValueError("need at least one difference")
    if n * (n + 1) // 2 > _MAX_PAIRS:
        s = sorted(d)
        return LocationEstimate("Hodges-Lehmann median difference",
                                _median_sorted(s), None, None,
                                "skipped (n too large)", conf)
    walsh = sorted((d[i] + d[j]) / 2.0 for i in range(n) for j in range(i, n))
    est = _median_sorted(walsh)
    alpha = 1.0 - conf
    method = "exact" if n <= exact.SIGNED_RANK_EXACT_MAX_N else "asymptotic"
    k = _signed_rank_trim_index(n, alpha)
    if k is None:
        return LocationEstimate("Hodges-Lehmann median difference", est,
                                None, None, method, conf)
    m = len(walsh)
    lo = walsh[k - 1]
    hi = walsh[m - k]
    return LocationEstimate("Hodges-Lehmann median difference", est, lo, hi,
                            method, conf)
