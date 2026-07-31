"""Distribution functions — pure standard library, no numpy/scipy.

Everything the tool needs for p-values and bootstrap confidence intervals is
implemented from first principles so ``medpath`` runs on any Python 3.9+.

* ``norm_cdf`` / ``norm_ppf`` — standard normal, used for Sobel/delta-method
  p-values and for the bias-corrected (BC/BCa) bootstrap interval.
* ``betainc`` — regularized incomplete beta, the engine behind the t and F
  tails (accurate to ~1e-14 in the ranges used here).
* ``gammainc_upper`` — regularized upper incomplete gamma, for the chi-square
  tail used by the Breusch–Pagan heteroscedasticity test.

The continued-fraction routines follow the classic *Numerical Recipes*
algorithms (``betacf``, ``gser``/``gcf``).
"""

from __future__ import annotations

import math

__all__ = [
    "norm_cdf",
    "norm_sf",
    "norm_ppf",
    "betainc",
    "t_sf",
    "t_sf_two_sided",
    "t_ppf",
    "f_sf",
    "chi2_sf",
]

_EPS = 3.0e-16
_FPMIN = 1e-300
_MAXIT = 500


# --------------------------------------------------------------------------
# Normal
# --------------------------------------------------------------------------
def norm_cdf(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def norm_sf(x: float) -> float:
    """Standard normal survival function, 1 - CDF(x)."""
    return 0.5 * math.erfc(x / math.sqrt(2.0))


# Acklam's rational approximation for the inverse normal CDF; a single
# Halley refinement step against erfc brings it to ~1e-15.
_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
      1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
      6.680131188771972e+01, -1.328068155288572e+01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
      -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
      3.754408661907416e+00)
_P_LOW = 0.02425


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (quantile function).

    ``p`` must be strictly inside (0, 1); the caller is expected to clamp,
    because +/-inf is never a useful bootstrap percentile.
    """
    if not (0.0 < p < 1.0):
        raise ValueError("norm_ppf requires 0 < p < 1 (got %r)" % (p,))
    if p < _P_LOW:
        q = math.sqrt(-2.0 * math.log(p))
        x = ((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]
        x /= (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0
    elif p <= 1.0 - _P_LOW:
        q = p - 0.5
        r = q * q
        x = (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q
        x /= ((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1.0
    else:
        q = math.sqrt(-2.0 * math.log1p(-p))
        x = -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5])
        x /= (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0
    # One Halley step on F(x) - p = 0.
    e = norm_cdf(x) - p
    if abs(x) < 37.0:
        u = e * math.sqrt(2.0 * math.pi) * math.exp(x * x / 2.0)
        x -= u / (1.0 + x * u / 2.0)
    return x


# --------------------------------------------------------------------------
# Incomplete beta -> Student t and F tails
# --------------------------------------------------------------------------
def _betacf(a: float, b: float, x: float) -> float:
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, _MAXIT + 1):
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
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log1p(-x))
    front = math.exp(lbeta)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_sf(t: float, df: float) -> float:
    """Upper-tail probability P(T > t) for Student's t with ``df`` df."""
    if df <= 0 or not math.isfinite(t):
        return float("nan")
    half = 0.5 * betainc(0.5 * df, 0.5, df / (df + t * t))
    return half if t > 0 else 1.0 - half


def t_sf_two_sided(t: float, df: float) -> float:
    """Two-sided p-value for Student's t."""
    if df <= 0 or not math.isfinite(t):
        return float("nan")
    return betainc(0.5 * df, 0.5, df / (df + t * t))


def t_ppf(p: float, df: float) -> float:
    """Inverse Student-t CDF, by bisection on the (monotone) two-sided tail.

    Bisection is slower than a rational approximation but is used only a
    handful of times per run (confidence-interval multipliers), and it is
    exact to 1e-12 without extra coefficient tables to get wrong.
    """
    if not (0.0 < p < 1.0):
        raise ValueError("t_ppf requires 0 < p < 1")
    if df <= 0:
        raise ValueError("t_ppf requires df > 0")
    if p == 0.5:
        return 0.0
    # Normal quantile is a good bracket seed; widen until it brackets.
    hi = max(1.0, abs(norm_ppf(p)) * 2.0 + 4.0)
    target = p
    lo = -hi
    while t_sf(hi, df) > 1.0 - target:
        hi *= 2.0
        if hi > 1e12:
            break
    while t_sf(lo, df) < 1.0 - target:
        lo *= 2.0
        if lo < -1e12:
            break
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        # CDF(mid) = 1 - sf(mid)
        if 1.0 - t_sf(mid, df) < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-13 * max(1.0, abs(mid)):
            break
    return 0.5 * (lo + hi)


def f_sf(f: float, df1: float, df2: float) -> float:
    """Upper-tail probability P(F > f)."""
    if f <= 0 or df1 <= 0 or df2 <= 0 or not math.isfinite(f):
        return float("nan") if not math.isfinite(f) else 1.0
    return betainc(0.5 * df2, 0.5 * df1, df2 / (df2 + df1 * f))


# --------------------------------------------------------------------------
# Incomplete gamma -> chi-square tail
# --------------------------------------------------------------------------
def _gser(a: float, x: float) -> float:
    ap = a
    total = 1.0 / a
    delta = total
    for _ in range(_MAXIT):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * _EPS:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a: float, x: float) -> float:
    b = x + 1.0 - a
    c = 1.0 / _FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, _MAXIT + 1):
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


def gammainc_upper(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x)."""
    if x < 0.0 or a <= 0.0:
        raise ValueError("gammainc_upper requires a > 0 and x >= 0")
    if x == 0.0:
        return 1.0
    if x < a + 1.0:
        return 1.0 - _gser(a, x)
    return _gcf(a, x)


def chi2_sf(x: float, df: float) -> float:
    """Upper-tail probability P(X^2 > x) for a chi-square with ``df`` df."""
    if df <= 0:
        return float("nan")
    if x <= 0:
        return 1.0
    return gammainc_upper(0.5 * df, 0.5 * x)
