"""Shapiro-Wilk normality test — pure standard library.

Implements Royston's (1992) AS R94 algorithm, the same routine SciPy and R use.
Valid for sample sizes 3 <= n <= 5000. Returns the W statistic and p-value.

Reference:
    Royston, P. (1992). "Approximating the Shapiro-Wilk W-test for
    non-normality." Statistics and Computing, 2, 117-119.
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

from .special import norm_cdf, norm_ppf

__all__ = ["shapiro_wilk"]


def _poly(coef: Sequence[float], x: float) -> float:
    """Evaluate polynomial: coef[0] + coef[1]*x + coef[2]*x^2 + ..."""
    result = coef[0]
    power = 1.0
    for c in coef[1:]:
        power *= x
        result += c * power
    return result


def shapiro_wilk(x: Sequence[float]) -> Tuple[float, float]:
    """Return (W, p-value) for the Shapiro-Wilk test of normality.

    Raises ValueError if n < 3 or all values are identical.
    """
    n = len(x)
    if n < 3:
        raise ValueError("Shapiro-Wilk requires at least 3 observations")
    xs: List[float] = sorted(float(v) for v in x)
    rng = xs[-1] - xs[0]
    if rng == 0.0:
        raise ValueError("all observations are identical (zero variance)")
    # W is scale-invariant, so centre and rescale before any squaring. Working
    # on the raw values overflowed above ~1e155 and underflowed to a zero
    # denominator below ~1e-162, both of which escaped as a raw traceback on
    # ordinary CSV input (large counts, tiny concentrations).
    centre = xs[len(xs) // 2]
    xs = [(v - centre) / rng for v in xs]
    if xs[-1] - xs[0] <= 0.0:
        raise ValueError(
            "값의 크기 차이가 배정밀도로 표현되지 않아 정규성 검정을 "
            "수행할 수 없습니다")

    # Lower-half expected normal order statistics (all negative). AS R94 uses
    # the antisymmetric weights, so only n2 = n // 2 of them are needed.
    n2 = n // 2
    m = [norm_ppf((i + 1 - 0.375) / (n + 0.25)) for i in range(n2)]
    summ2 = 2.0 * sum(v * v for v in m)
    ssumm2 = math.sqrt(summ2)
    rsn = 1.0 / math.sqrt(n)
    a = [0.0] * n2  # positive top-half weights, a[0] largest

    c1 = [0.0, 0.221157, -0.147981, -2.071190, 4.434685, -2.706056]
    c2 = [0.0, 0.042981, -0.293762, -1.752461, 5.682633, -3.582633]

    if n == 3:
        # Exact weight for n = 3 (AS R94 boundary case).
        a[0] = math.sqrt(0.5)
        mean = sum(xs) / n
        w1 = a[0] * (xs[2] - xs[0])
        w = w1 * w1 / sum((v - mean) ** 2 for v in xs)
        w = min(1.0, w)
        pi6 = 6.0 / math.pi
        stqr = math.asin(math.sqrt(0.75))
        p = pi6 * (math.asin(math.sqrt(w)) - stqr)
        return w, max(0.0, min(1.0, p))

    a1 = _poly(c1, rsn) - m[0] / ssumm2
    if n > 5:
        a2 = _poly(c2, rsn) - m[1] / ssumm2
        i1 = 2
        fac = math.sqrt((summ2 - 2.0 * m[0] ** 2 - 2.0 * m[1] ** 2)
                        / (1.0 - 2.0 * a1 ** 2 - 2.0 * a2 ** 2))
        a[0] = a1
        a[1] = a2
    else:
        i1 = 1
        fac = math.sqrt((summ2 - 2.0 * m[0] ** 2) / (1.0 - 2.0 * a1 ** 2))
        a[0] = a1

    for i in range(i1, n2):
        a[i] = -m[i] / fac

    # W statistic from antisymmetric weights on paired differences.
    mean = sum(xs) / n
    w1 = sum(a[i] * (xs[n - 1 - i] - xs[i]) for i in range(n2))
    w_den = sum((v - mean) ** 2 for v in xs)
    w = w1 * w1 / w_den
    if w > 1.0:
        w = 1.0

    # P-value (Royston 1992 approximations; n == 3 handled above).
    y = math.log(1.0 - w)
    if n <= 11:
        gamma = _poly([-2.273, 0.459, 0.0], n)
        m_mu = _poly([0.5440, -0.39978, 0.025054, -6.714e-4, 0.0], n)
        s_sigma = math.exp(_poly([1.3822, -0.77857, 0.062767, -0.0020322, 0.0], n))
        y = -math.log(gamma - y)
    else:
        ln_n = math.log(n)
        m_mu = _poly([-1.5861, -0.31082, -0.083751, 0.0038915, 0.0], ln_n)
        s_sigma = math.exp(_poly([-0.4803, -0.082676, 0.0030302, 0.0, 0.0], ln_n))

    z = (y - m_mu) / s_sigma
    p = 1.0 - norm_cdf(z)
    return w, max(0.0, min(1.0, p))
