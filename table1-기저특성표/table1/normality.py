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

from .special import norm_cdf

__all__ = ["shapiro_wilk"]

# Normal-order-statistic quantile via the Beasley-Springer-Moro / AS 241 rational
# approximation (ppnd16). Accurate to ~1e-15, matching scipy's _ppnd.
_A = [
    3.3871328727963666080,
    1.3314166789178437745e2,
    1.9715909503065514427e3,
    1.3731693765509461125e4,
    4.5921953931549871457e4,
    6.7265770927008700853e4,
    3.3430575583588128105e4,
    2.5090809287301226727e3,
]
_B = [
    4.2313330701600911252e1,
    6.8718700749205790830e2,
    5.3941960214247511077e3,
    2.1213794301586595867e4,
    3.9307895800092710610e4,
    2.8729085735721942674e4,
    5.2264952788528545610e3,
]
_C = [
    1.42343711074968357734,
    4.63033784615654529590,
    5.76949722146069140550,
    3.64784832476320460504,
    1.27045825245236838258,
    2.41780725177450611770e-1,
    2.27238449892691845833e-2,
    7.74545014278341407640e-4,
]
_D = [
    2.05319162663775882187,
    1.67638483018380384940,
    6.89767334985100004550e-1,
    1.48103976427480074590e-1,
    1.51986665636164571966e-2,
    5.47593808499534494600e-4,
    1.05075007164441684324e-9,
]
_E = [
    6.65790464350110377720,
    5.46378491116411436990,
    1.78482653991729133580,
    2.96560571828504891230e-1,
    2.65321895265761230930e-2,
    1.24266094738807843860e-3,
    2.71155556874348757815e-5,
    2.01033439929228813265e-7,
]
_F = [
    5.99832206555887937690e-1,
    1.36929880922735805310e-1,
    1.48753612908506148525e-2,
    7.86869131145613259100e-4,
    1.84631831751005468180e-5,
    1.42151175831644588870e-7,
    2.04426310338993978564e-15,
]


def _ppnd(p: float) -> float:
    """Inverse standard normal CDF (AS 241)."""
    q = p - 0.5
    if abs(q) <= 0.425:
        r = 0.180625 - q * q
        num = (((((((_A[7] * r + _A[6]) * r + _A[5]) * r + _A[4]) * r + _A[3]) * r
                 + _A[2]) * r + _A[1]) * r + _A[0])
        den = (((((((_B[6] * r + _B[5]) * r + _B[4]) * r + _B[3]) * r + _B[2]) * r
                 + _B[1]) * r + _B[0]) * r + 1.0)
        return q * num / den
    r = p if q < 0 else 1.0 - p
    r = math.sqrt(-math.log(r))
    if r <= 5.0:
        r -= 1.6
        num = (((((((_C[7] * r + _C[6]) * r + _C[5]) * r + _C[4]) * r + _C[3]) * r
                 + _C[2]) * r + _C[1]) * r + _C[0])
        den = (((((((_D[6] * r + _D[5]) * r + _D[4]) * r + _D[3]) * r + _D[2]) * r
                 + _D[1]) * r + _D[0]) * r + 1.0)
    else:
        r -= 5.0
        num = (((((((_E[7] * r + _E[6]) * r + _E[5]) * r + _E[4]) * r + _E[3]) * r
                 + _E[2]) * r + _E[1]) * r + _E[0])
        den = (((((((_F[6] * r + _F[5]) * r + _F[4]) * r + _F[3]) * r + _F[2]) * r
                 + _F[1]) * r + _F[0]) * r + 1.0)
    val = num / den
    return -val if q < 0 else val


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

    # Lower-half expected normal order statistics (all negative). AS R94 uses
    # the antisymmetric weights, so only n2 = n // 2 of them are needed.
    n2 = n // 2
    m = [_ppnd((i + 1 - 0.375) / (n + 0.25)) for i in range(n2)]
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
