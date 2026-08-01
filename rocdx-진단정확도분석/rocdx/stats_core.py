"""Small statistical primitives — pure standard library.

Everything the tool needs from a stats package: the standard normal CDF/quantile,
mid-ranks (ties averaged), and the confidence intervals used for proportions,
likelihood ratios and odds ratios. Implemented here so the tool has zero
third-party dependencies and stays reproducible on any Python 3.9+ machine.
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

__all__ = [
    "norm_cdf",
    "norm_sf",
    "norm_ppf",
    "two_sided_p",
    "midranks",
    "wilson_ci",
    "mean",
    "var_ddof1",
    "cov_ddof1",
]


def norm_cdf(z: float) -> float:
    """P(Z <= z) for a standard normal, via the error function."""
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def norm_sf(z: float) -> float:
    """Upper tail P(Z > z). Kept separate from 1-cdf to stay accurate far out."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def two_sided_p(z: float) -> float:
    """Two-sided p-value for a z statistic (accurate deep into the tail)."""
    return min(1.0, 2.0 * norm_sf(abs(z)))


# Coefficients of Peter Acklam's rational approximation to the normal quantile.
_A = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
      1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
_B = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
      6.680131188771972e01, -1.328068155288572e01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
      -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
      3.754408661907416e00)
_P_LOW = 0.02425


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF.

    Acklam's approximation (|error| < 1.15e-9) refined by one Halley step, which
    takes it to machine precision. Raises for p outside (0, 1).
    """
    if not (0.0 < p < 1.0):
        raise ValueError("norm_ppf requires 0 < p < 1")
    if p < _P_LOW:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
            ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    elif p <= 1.0 - _P_LOW:
        q = p - 0.5
        r = q * q
        x = (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / \
            (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
            ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    # Halley refinement.
    e = norm_cdf(x) - p
    u = e * math.sqrt(2.0 * math.pi) * math.exp(x * x / 2.0)
    return x - u / (1.0 + x * u / 2.0)


def midranks(values: Sequence[float]) -> List[float]:
    """1-based ranks with tied values sharing their average rank.

    ``midranks([10, 20, 20, 30]) == [1.0, 2.5, 2.5, 4.0]``. This is the only
    ties handling the DeLong machinery needs, and it is what makes the fast
    O(n log n) variance identical to the O(n^2) definition.
    """
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # average of 1-based ranks i+1 .. j+1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def mean(xs: Sequence[float]) -> float:
    return math.fsum(xs) / len(xs)


def var_ddof1(xs: Sequence[float]) -> float:
    """Sample variance (n-1). Returns 0.0 when fewer than 2 observations."""
    n = len(xs)
    if n < 2:
        return 0.0
    m = mean(xs)
    return math.fsum((x - m) ** 2 for x in xs) / (n - 1)


def cov_ddof1(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Sample covariance (n-1). Returns 0.0 when fewer than 2 observations."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = mean(xs), mean(ys)
    return math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (n - 1)


def wilson_ci(k: int, n: int, alpha: float = 0.05) -> Tuple[float, float]:
    """Wilson score interval for a binomial proportion k/n.

    Chosen over the Wald interval because diagnostic studies routinely produce
    sensitivities at or near 1.0, where Wald collapses to a zero-width interval.
    """
    if n <= 0:
        return (float("nan"), float("nan"))
    z = norm_ppf(1.0 - alpha / 2.0)
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))
