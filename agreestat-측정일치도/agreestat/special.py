"""Statistical distribution functions — pure standard library (no numpy/scipy).

Everything here is implemented from first principles so ``agreestat`` runs
anywhere Python 3.9+ is installed. The continued-fraction routines for the
regularized incomplete beta/gamma functions follow the classic *Numerical
Recipes* algorithms and are accurate to ~1e-10 over the ranges used here. The
normal quantile is Wichura's AS 241 rational approximation (accurate to ~1e-15,
matching SciPy's ``_ppnd``); the Student-t and F quantiles are obtained by
bracketed bisection on their (exact) CDFs.
"""

from __future__ import annotations

import math

__all__ = [
    "norm_cdf",
    "norm_sf",
    "norm_ppf",
    "betainc",
    "gammainc_lower",
    "gammainc_upper",
    "t_sf_two_sided",
    "t_cdf",
    "t_ppf",
    "f_sf",
    "f_cdf",
    "f_ppf",
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


# --- Normal quantile (inverse CDF), Wichura's AS 241 -----------------------
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


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Wichura's AS 241)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
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


# --- Incomplete beta / gamma ------------------------------------------------
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


# --- Student-t --------------------------------------------------------------
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
    """Inverse Student-t CDF via bracketed bisection (accurate to ~1e-10).

    The bracket is expanded outward until it straddles the quantile, so very
    small/large ``p`` or heavy tails (small df) do not clamp at a fixed edge.
    """
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    if p == 0.5:
        return 0.0
    hi = 1.0
    while t_cdf(hi, df) < p and hi < 1e300:
        hi *= 2.0
    lo = -1.0
    while t_cdf(lo, df) > p and lo > -1e300:
        lo *= 2.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# --- F ----------------------------------------------------------------------
def f_sf(f: float, df1: float, df2: float) -> float:
    """Upper-tail p-value P(F >= f) for an F-distribution."""
    if f <= 0:
        return 1.0
    x = df2 / (df2 + df1 * f)
    return betainc(df2 / 2.0, df1 / 2.0, x)


def f_cdf(f: float, df1: float, df2: float) -> float:
    """F-distribution cumulative distribution function."""
    return 1.0 - f_sf(f, df1, df2)


def f_ppf(p: float, df1: float, df2: float) -> float:
    """Inverse F CDF via bracketed bisection (accurate to ~1e-9)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    lo, hi = 0.0, 1.0
    # Expand the upper bracket until it covers the quantile.
    while f_cdf(hi, df1, df2) < p and hi < 1e12:
        hi *= 2.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if f_cdf(mid, df1, df2) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def chi2_sf(chisq: float, df: float) -> float:
    """Upper-tail p-value P(X >= chisq) for a chi-square distribution."""
    if chisq <= 0:
        return 1.0
    return gammainc_upper(df / 2.0, chisq / 2.0)
