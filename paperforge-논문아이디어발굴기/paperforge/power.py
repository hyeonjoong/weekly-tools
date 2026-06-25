"""Closed-form sample-size approximations (stdlib only).

These are deliberately simple, well-known closed forms so every number can be
hand-checked against published tables. They are *planning* approximations, not a
substitute for a full power analysis (e.g. G*Power), and we say so in the report.

References
---------
- Correlation: Fisher z-transform approximation. For r=0.30, alpha=.05 (two
  sided), power=.80 this yields n≈85, matching standard tables.
- Two-group mean comparison (Cohen's d): the normal approximation
  n_per_group = 2 (z_{1-a/2} + z_{1-b})^2 / d^2. For d=0.5 this yields 63/group.
- Multiple regression (R^2 deviation from zero, Cohen's f^2): solved EXACTLY by
  iterating N against the non-central F power, computed here from scratch with a
  stdlib incomplete-beta routine (no SciPy). This reproduces G*Power, e.g.
  f2=0.15: N=55/68/77/85/92 for k=1..5 predictors. A naive z-approximation that
  ignores the numerator degrees of freedom badly *under*-states N once k>1 (it
  would give ~57 for k=3), so we do not use it.

Note: the correlation and two-group forms are normal approximations that can
land ~1 subject/group below exact non-central-t tools (e.g. G*Power gives
64/group for d=0.5). These are *planning* estimates only; the report says so and
recommends confirming final power in a dedicated tool. Because the danger for a
feasibility check is *under*-stating the required N, we always round up (ceil)
and pair every number with conservative (small-to-medium) effect-size priors.
"""
from __future__ import annotations

import math

# Standard normal quantiles for the alpha/power values we actually use.
# Hard-coded (rather than inverting the normal CDF) so the constants are
# transparent and the results are exactly reproducible across machines.
_Z_ALPHA_TWO_SIDED = {0.05: 1.959963985, 0.01: 2.575829304, 0.10: 1.644853627}
_Z_POWER = {0.80: 0.841621234, 0.90: 1.281551566, 0.95: 1.644853627}


def _z(table: dict, key: float, what: str) -> float:
    # Exact membership only — we advertise a fixed set of supported values, so a
    # near-miss (e.g. alpha=0.0501) should be rejected, not silently snapped.
    if key in table:
        return table[key]
    raise ValueError(f"Unsupported {what}={key}. Supported: {sorted(table)}")


def n_for_correlation(r: float, alpha: float = 0.05, power: float = 0.80) -> int:
    """Total N to detect a Pearson correlation of magnitude ``r``.

    Uses the Fisher z approximation::

        C = 0.5 * ln((1+|r|)/(1-|r|))
        N = ((z_{1-a/2} + z_{1-b}) / C)^2 + 3
    """
    r = abs(float(r))
    if not 0 < r < 1:
        raise ValueError("r must be strictly between 0 and 1")
    za = _z(_Z_ALPHA_TWO_SIDED, alpha, "alpha")
    zb = _z(_Z_POWER, power, "power")
    c = 0.5 * math.log((1 + r) / (1 - r))
    n = ((za + zb) / c) ** 2 + 3
    return math.ceil(n)


def n_per_group_two_means(d: float, alpha: float = 0.05, power: float = 0.80) -> int:
    """Per-group N to detect a standardized mean difference ``d`` (Cohen's d).

    Normal approximation::

        n_per_group = 2 (z_{1-a/2} + z_{1-b})^2 / d^2
    """
    d = abs(float(d))
    if d <= 0:
        raise ValueError("d must be > 0")
    za = _z(_Z_ALPHA_TWO_SIDED, alpha, "alpha")
    zb = _z(_Z_POWER, power, "power")
    n = 2 * (za + zb) ** 2 / d ** 2
    return math.ceil(n)


def n_for_paired(d: float, alpha: float = 0.05, power: float = 0.80) -> int:
    """Number of *subjects* (pairs) for a within-subject / paired comparison.

    Normal approximation on difference scores::

        n_pairs = (z_{1-a/2} + z_{1-b})^2 / d_z^2 + 1

    where ``d_z`` is the standardized mean of the difference scores. For d_z=0.5
    this gives 33 subjects — far fewer than the 126 a between-groups design would
    need, which is the whole point of a repeated-measures design.
    """
    d = abs(float(d))
    if d <= 0:
        raise ValueError("d must be > 0")
    za = _z(_Z_ALPHA_TWO_SIDED, alpha, "alpha")
    zb = _z(_Z_POWER, power, "power")
    n = (za + zb) ** 2 / d ** 2 + 1
    return math.ceil(n)


def required_total_n(effect: dict, alpha: float = 0.05, power: float = 0.80):
    """Required *total* N for an effect spec, or ``None`` when not applicable.

    ``effect`` is one of::

        {"type": "correlation", "r": 0.3}
        {"type": "two_group", "d": 0.5}        # total = 2 * per-group
        {"type": "paired", "d": 0.5}           # within-subject; total = n pairs
        {"type": "regression", "f2": 0.15, "k": 3}
        {"type": "exploratory"}                # no closed-form target -> None
    """
    etype = effect.get("type")
    if etype == "correlation":
        return n_for_correlation(effect["r"], alpha, power)
    if etype == "two_group":
        return 2 * n_per_group_two_means(effect["d"], alpha, power)
    if etype == "paired":
        return n_for_paired(effect["d"], alpha, power)
    if etype == "regression":
        return n_for_regression(effect["f2"], effect.get("k", 1), alpha, power)
    if etype == "exploratory":
        return None
    raise ValueError(f"Unknown effect type: {etype!r}")


# --- Exact non-central F machinery for multiple-regression sample size -------
#
# Implemented from scratch (stdlib only) so we don't depend on SciPy. The pieces
# are the textbook ones: a continued-fraction regularized incomplete beta, the
# central F CDF expressed through it, a bisection F-quantile, and the
# non-central F CDF as a Poisson-weighted mixture of central beta CDFs.


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Numerical Recipes)."""
    MAXIT, EPS, FPMIN = 300, 3e-16, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    bt = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _f_cdf(x: float, d1: float, d2: float) -> float:
    """Central F CDF via the incomplete beta."""
    if x <= 0:
        return 0.0
    return _betai(d1 / 2.0, d2 / 2.0, d1 * x / (d1 * x + d2))


def _f_quantile(p: float, d1: float, d2: float) -> float:
    """Inverse central F CDF by bisection (monotone in x)."""
    lo, hi = 1e-9, 1e9
    for _ in range(120):
        mid = 0.5 * (lo + hi)
        if _f_cdf(mid, d1, d2) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _ncf_cdf(x: float, d1: float, d2: float, lam: float) -> float:
    """Non-central F CDF: Poisson(lam/2)-weighted sum of central beta CDFs."""
    if x <= 0:
        return 0.0
    if lam <= 0:
        return _f_cdf(x, d1, d2)
    y = d1 * x / (d1 * x + d2)
    half = lam / 2.0
    total = 0.0
    logw = -half  # log Poisson weight for j = 0
    for j in range(0, 5000):
        total += math.exp(logw) * _betai(d1 / 2.0 + j, d2 / 2.0, y)
        if j > half and math.exp(logw) < 1e-13:
            break
        logw += math.log(half) - math.log(j + 1)
    return total


def n_for_regression(f2: float, k: int, alpha: float = 0.05, power: float = 0.80) -> int:
    """Total N for a multiple-regression R^2 test (Cohen's f^2, k predictors).

    Solves the *exact* power equation by searching for the smallest N at which a
    non-central F test (numerator df = k, denominator df = N - k - 1,
    non-centrality lambda = f2 * N) reaches the target power. This matches
    G*Power's "Linear multiple regression: Fixed model, R^2 deviation from zero".
    For f2=0.15 it returns N = 55, 68, 77, 85, 92 for k = 1..5.
    """
    f2 = float(f2)
    k = int(k)
    if f2 <= 0:
        raise ValueError("f2 must be > 0")
    if k < 1:
        raise ValueError("k must be >= 1")
    # Validate alpha/power against the advertised supported set (consistency with
    # the other estimators); the returned z-values are not otherwise needed here.
    _z(_Z_ALPHA_TWO_SIDED, alpha, "alpha")
    _z(_Z_POWER, power, "power")
    for n in range(k + 2, 1_000_000):
        d2 = n - k - 1
        f_crit = _f_quantile(1.0 - alpha, k, d2)
        pw = 1.0 - _ncf_cdf(f_crit, k, d2, f2 * n)
        if pw >= power:
            return n
    raise ValueError("Required N exceeds 1e6; check f2/effect size.")
