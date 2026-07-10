"""Special functions and statistical distribution CDFs — pure standard library.

Everything here is implemented from first principles (no numpy/scipy) so the tool
runs anywhere Python 3.9+ is installed. The continued-fraction routines for the
regularized incomplete beta/gamma functions follow the classic algorithms in
*Numerical Recipes* (betacf/gammp) and are accurate to ~1e-10 in the ranges used
for p-value computation.
"""

from __future__ import annotations

import math

__all__ = [
    "norm_cdf",
    "norm_sf",
    "betainc",
    "gammainc_lower",
    "gammainc_upper",
    "t_sf_two_sided",
    "t_cdf",
    "t_ppf",
    "f_sf",
    "chi2_sf",
]

_EPS = 1e-14
_FPMIN = 1e-300


def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def norm_sf(x: float) -> float:
    """Standard normal survival function, 1 - CDF(x)."""
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b), 0 <= x <= 1."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _gser(a: float, x: float) -> float:
    """Series representation of the lower regularized incomplete gamma P(a, x)."""
    if x <= 0.0:
        return 0.0
    ap = a
    total = 1.0 / a
    delta = total
    for _ in range(1000):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * _EPS:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a: float, x: float) -> float:
    """Continued fraction for the upper regularized incomplete gamma Q(a, x)."""
    b = x + 1.0 - a
    c = 1.0 / _FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = b + an / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def gammainc_lower(a: float, x: float) -> float:
    """Lower regularized incomplete gamma function P(a, x)."""
    if x < 0.0 or a <= 0.0:
        raise ValueError("gammainc_lower requires x >= 0 and a > 0")
    if x == 0.0:
        return 0.0
    if x < a + 1.0:
        return _gser(a, x)
    return 1.0 - _gcf(a, x)


def gammainc_upper(a: float, x: float) -> float:
    """Upper regularized incomplete gamma function Q(a, x) = 1 - P(a, x)."""
    return 1.0 - gammainc_lower(a, x)


def t_sf_two_sided(t: float, df: float) -> float:
    """Two-sided p-value P(|T| >= |t|) for a Student-t with df degrees of freedom."""
    if df <= 0:
        raise ValueError("degrees of freedom must be positive")
    x = df / (df + t * t)
    return betainc(df / 2.0, 0.5, x)


def t_cdf(t: float, df: float) -> float:
    """Student-t cumulative distribution function."""
    p = 0.5 * t_sf_two_sided(t, df)
    return 1.0 - p if t > 0 else p


def t_ppf(p: float, df: float) -> float:
    """Inverse Student-t CDF via bisection (accurate to ~1e-10)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    lo, hi = -1e6, 1e6
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def f_sf(f: float, df1: float, df2: float) -> float:
    """Upper-tail p-value P(F >= f) for an F-distribution."""
    if f <= 0:
        return 1.0
    x = df2 / (df2 + df1 * f)
    return betainc(df2 / 2.0, df1 / 2.0, x)


def chi2_sf(chisq: float, df: float) -> float:
    """Upper-tail p-value P(X >= chisq) for a chi-square distribution."""
    if chisq <= 0:
        return 1.0
    return gammainc_upper(df / 2.0, chisq / 2.0)
