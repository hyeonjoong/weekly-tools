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


def n_total_two_group(
    d: float, alpha: float = 0.05, power: float = 0.80, allocation: float = 0.5
) -> int:
    """Total N for a two-group mean comparison with a possibly unbalanced split.

    ``allocation`` is the fraction of the sample in group 1 (0<alloc<1). The
    normal approximation::

        N_total = (z_a + z_b)^2 / (p (1 - p)) / d^2

    reduces to ``4 (z_a+z_b)^2 / d^2`` = 2 * per-group at p=0.5. Unbalanced
    designs need more: a 30/70 split inflates N by 1/(4·0.3·0.7) ≈ 1.19×.
    """
    d = abs(float(d))
    if d <= 0:
        raise ValueError("d must be > 0")
    if not 0.0 < allocation < 1.0:
        raise ValueError("allocation must satisfy 0 < allocation < 1")
    za = _z(_Z_ALPHA_TWO_SIDED, alpha, "alpha")
    zb = _z(_Z_POWER, power, "power")
    return math.ceil((za + zb) ** 2 / (allocation * (1.0 - allocation)) / d ** 2)


def n_for_regression_change(
    f2: float, k_tested: int, k_control: int,
    alpha: float = 0.05, power: float = 0.80,
) -> int:
    """Total N for an *incremental*-R^2 test (hierarchical regression / ΔR^2).

    Sizes the test of whether ``k_tested`` added predictors explain variance
    beyond ``k_control`` covariates already in the model. The numerator df is
    the number of *added* predictors (not the full model), which is the correct
    test for an incremental-validity hypothesis::

        numerator df = k_tested
        denominator df = N - (k_tested + k_control) - 1
        non-centrality lambda = f2 * N     (f2 on the R^2 *increment*)

    Uses the same exact non-central F machinery as :func:`n_for_regression`.
    """
    f2 = float(f2)
    k_tested, k_control = int(k_tested), int(k_control)
    if f2 <= 0:
        raise ValueError("f2 must be > 0")
    if k_tested < 1 or k_control < 0:
        raise ValueError("k_tested must be >= 1 and k_control >= 0")
    _z(_Z_ALPHA_TWO_SIDED, alpha, "alpha")
    _z(_Z_POWER, power, "power")
    k_full = k_tested + k_control
    for n in range(k_full + 2, 1_000_000):
        d2 = n - k_full - 1
        f_crit = _f_quantile(1.0 - alpha, k_tested, d2)
        pw = 1.0 - _ncf_cdf(f_crit, k_tested, d2, f2 * n)
        if pw >= power:
            return n
    raise ValueError("Required N exceeds 1e6; check f2/effect size.")


def scale_effect(effect: dict, factor: float) -> dict:
    """Return a copy of ``effect`` with its magnitude scaled by ``factor``.

    Used for effect-size sensitivity: ``factor<1`` assumes a *smaller* true
    effect (→ larger required N), ``factor>1`` a larger one. Correlation r is
    capped below 1. Exploratory effects are returned unchanged.
    """
    factor = float(factor)
    if factor <= 0:
        raise ValueError("factor must be > 0")
    e = dict(effect)
    etype = e.get("type")
    if etype == "correlation":
        e["r"] = min(abs(e["r"]) * factor, 0.999)
    elif etype in ("two_group", "paired"):
        e["d"] = abs(e["d"]) * factor
    elif etype in ("regression", "regression_change"):
        e["f2"] = e["f2"] * factor
    return e


def effect_magnitude(effect: dict):
    """The headline magnitude of an effect spec (r / d / f2), or ``None``."""
    etype = effect.get("type")
    if etype == "correlation":
        return effect["r"]
    if etype in ("two_group", "paired"):
        return effect["d"]
    if etype in ("regression", "regression_change"):
        return effect["f2"]
    return None


def required_total_n(effect: dict, alpha: float = 0.05, power: float = 0.80):
    """Required *total* N for an effect spec, or ``None`` when not applicable.

    ``effect`` is one of::

        {"type": "correlation", "r": 0.3}
        {"type": "two_group", "d": 0.5, "allocation": 0.5}   # total (opt. split)
        {"type": "paired", "d": 0.5}           # within-subject; total = n pairs
        {"type": "regression", "f2": 0.15, "k": 3}           # overall-R^2 test
        {"type": "regression_change", "f2": 0.15,            # incremental-R^2
         "k_tested": 2, "k_control": 1}
        {"type": "exploratory"}                # no closed-form target -> None
    """
    etype = effect.get("type")
    if etype == "correlation":
        return n_for_correlation(effect["r"], alpha, power)
    if etype == "two_group":
        alloc = effect.get("allocation", 0.5)
        if alloc == 0.5:
            # Preserve the exact 2*per-group value (round each group up).
            return 2 * n_per_group_two_means(effect["d"], alpha, power)
        return n_total_two_group(effect["d"], alpha, power, alloc)
    if etype == "paired":
        return n_for_paired(effect["d"], alpha, power)
    if etype == "regression":
        return n_for_regression(effect["f2"], effect.get("k", 1), alpha, power)
    if etype == "regression_change":
        return n_for_regression_change(
            effect["f2"], effect["k_tested"], effect["k_control"], alpha, power
        )
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
    """Non-central F CDF: Poisson(lam/2)-weighted sum of central beta CDFs.

    The Poisson weights peak at the mode j≈lam/2, so we sum *outward from the
    mode* in both directions and stop each side once the log-weight has fallen
    ~50 below the peak (exp(-50)≈2e-22, negligible). Summing from j=0 with a
    fixed iteration cap — as a naive implementation does — silently truncates
    before reaching the mode once lam/2 exceeds the cap, collapsing the CDF
    toward 0 and any 1-CDF power toward a spurious 1. Centering on the mode
    keeps the result accurate for arbitrarily large lam.
    """
    if x <= 0:
        return 0.0
    if lam <= 0:
        return _f_cdf(x, d1, d2)
    y = d1 * x / (d1 * x + d2)
    half = lam / 2.0
    loghalf = math.log(half)
    a_half, b_half = d1 / 2.0, d2 / 2.0
    mode = int(half)

    def _logw(j: int) -> float:
        return -half + j * loghalf - math.lgamma(j + 1)

    peak = _logw(mode)
    total = 0.0

    # Upward from the mode (weights fall monotonically above the mode).
    j = mode
    logw = peak
    while True:
        total += math.exp(logw) * _betai(a_half + j, b_half, y)
        if logw < peak - 50.0 and j > half:
            break
        j += 1
        logw += loghalf - math.log(j)

    # Downward from just below the mode.
    if mode > 0:
        j = mode - 1
        logw = peak - loghalf + math.log(mode)  # = _logw(mode - 1)
        while j >= 0:
            total += math.exp(logw) * _betai(a_half + j, b_half, y)
            if j == 0 or logw < peak - 50.0:
                break
            logw += math.log(j) - loghalf  # -> _logw(j - 1)
            j -= 1

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


# --- Sensitivity analysis: minimum detectable effect (MDES) ------------------
#
# The inverse question of ``required_total_n``: *given the N you already have*,
# what is the smallest effect you could detect at the chosen alpha/power? This
# turns an "underpowered" verdict into an actionable number ("with N=90 you can
# still detect r>=0.29"). Each MDES is the algebraic inverse of the matching
# forward formula, so the two are mutually consistent: feeding an MDES back into
# ``required_total_n`` returns (approximately) the same N.


def mdes_correlation(n: int, alpha: float = 0.05, power: float = 0.80) -> float:
    """Smallest |r| detectable with total sample ``n`` (Fisher-z inverse).

    Inverts ``n = ((z_a + z_b) / atanh(r))^2 + 3``::

        r = tanh((z_a + z_b) / sqrt(n - 3))
    """
    n = int(n)
    if n <= 3:
        raise ValueError("n must be > 3 for a correlation MDES")
    za = _z(_Z_ALPHA_TWO_SIDED, alpha, "alpha")
    zb = _z(_Z_POWER, power, "power")
    return math.tanh((za + zb) / math.sqrt(n - 3))


def mdes_two_group(
    n_total: int, alpha: float = 0.05, power: float = 0.80, allocation: float = 0.5
) -> float:
    """Smallest Cohen's d detectable with ``n_total`` split over 2 groups.

    Inverts ``N_total = (z_a+z_b)^2 / (p(1-p)) / d^2``::

        d = (z_a + z_b) / sqrt(N_total * p * (1 - p))

    which is ``(z_a+z_b) * 2 / sqrt(N_total)`` at the balanced split p=0.5.
    """
    n_total = int(n_total)
    if n_total < 4:
        raise ValueError("n_total must be >= 4 (>= 2 per group)")
    if not 0.0 < allocation < 1.0:
        raise ValueError("allocation must satisfy 0 < allocation < 1")
    za = _z(_Z_ALPHA_TWO_SIDED, alpha, "alpha")
    zb = _z(_Z_POWER, power, "power")
    return (za + zb) / math.sqrt(n_total * allocation * (1.0 - allocation))


def mdes_paired(n: int, alpha: float = 0.05, power: float = 0.80) -> float:
    """Smallest d_z detectable with ``n`` subjects in a within-subject design.

    Inverts ``n = (z_a + z_b)^2 / d_z^2 + 1``::

        d_z = (z_a + z_b) / sqrt(n - 1)
    """
    n = int(n)
    if n <= 1:
        raise ValueError("n must be > 1 for a paired MDES")
    za = _z(_Z_ALPHA_TWO_SIDED, alpha, "alpha")
    zb = _z(_Z_POWER, power, "power")
    return (za + zb) / math.sqrt(n - 1)


def mdes_regression(n: int, k: int, alpha: float = 0.05, power: float = 0.80) -> float:
    """Smallest Cohen's f^2 detectable with ``n`` cases and ``k`` predictors.

    Power is strictly increasing in f^2 (N, k, alpha fixed), so we bisect the
    exact non-central F power curve for the f^2 that just reaches the target.
    This is the inverse of :func:`n_for_regression` and reproduces it: the MDES
    at N = n_for_regression(f2, k) rounds back to ~f2.
    """
    n = int(n)
    k = int(k)
    if k < 1:
        raise ValueError("k must be >= 1")
    if n < k + 2:
        raise ValueError("n must be >= k + 2 for a regression MDES")
    _z(_Z_ALPHA_TWO_SIDED, alpha, "alpha")
    _z(_Z_POWER, power, "power")
    d2 = n - k - 1
    f_crit = _f_quantile(1.0 - alpha, k, d2)

    def _power(f2: float) -> float:
        return 1.0 - _ncf_cdf(f_crit, k, d2, f2 * n)

    lo, hi = 1e-9, 1.0
    # Expand the upper bracket until it exceeds the target power. This always
    # terminates: because _ncf_cdf sums around the Poisson mode (not from a
    # truncated j=0), lambda -> inf genuinely drives power -> 1 for any d2 >= 1.
    while _power(hi) < power:
        hi *= 2.0
        if hi > 1e9:
            raise ValueError("No detectable f^2 below 1e9; n likely too small.")
    # Bisect to a relative tolerance (converges in ~30 steps even when hi is
    # large); the 200-cap is only a safety backstop.
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _power(mid) < power:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-9 * hi:
            break
    return 0.5 * (lo + hi)


def mdes_regression_change(
    n: int, k_tested: int, k_control: int,
    alpha: float = 0.05, power: float = 0.80,
) -> float:
    """Smallest incremental f^2 detectable for a ΔR^2 test (see
    :func:`n_for_regression_change`). Numerator df = ``k_tested``, denominator
    df = ``n - (k_tested + k_control) - 1``; bisected on the exact power curve.
    """
    n = int(n)
    k_tested, k_control = int(k_tested), int(k_control)
    if k_tested < 1 or k_control < 0:
        raise ValueError("k_tested must be >= 1 and k_control >= 0")
    k_full = k_tested + k_control
    if n < k_full + 2:
        raise ValueError("n must be >= k_tested + k_control + 2")
    _z(_Z_ALPHA_TWO_SIDED, alpha, "alpha")
    _z(_Z_POWER, power, "power")
    d2 = n - k_full - 1
    f_crit = _f_quantile(1.0 - alpha, k_tested, d2)

    def _power(f2: float) -> float:
        return 1.0 - _ncf_cdf(f_crit, k_tested, d2, f2 * n)

    lo, hi = 1e-9, 1.0
    while _power(hi) < power:
        hi *= 2.0
        if hi > 1e9:
            raise ValueError("No detectable f^2 below 1e9; n likely too small.")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _power(mid) < power:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-9 * hi:
            break
    return 0.5 * (lo + hi)


def detectable_effect(effect: dict, n, alpha: float = 0.05, power: float = 0.80):
    """MDES for an effect spec at sample ``n``, as a structured dict or ``None``.

    Returns ``{"metric": "r"|"d"|"d_z"|"f2", "value": float}`` — or ``None`` when
    n is unknown, the design is exploratory, or n is below the minimum the
    formula admits (so callers can render "표본수 미상"/"비적용" uniformly).
    """
    if n is None:
        return None
    etype = effect.get("type")
    try:
        if etype == "correlation":
            return {"metric": "r", "value": mdes_correlation(n, alpha, power)}
        if etype == "two_group":
            alloc = effect.get("allocation", 0.5)
            return {"metric": "d", "value": mdes_two_group(n, alpha, power, alloc)}
        if etype == "paired":
            return {"metric": "d_z", "value": mdes_paired(n, alpha, power)}
        if etype in ("regression", "regression_change"):
            if etype == "regression":
                f2 = mdes_regression(n, int(effect.get("k", 1)), alpha, power)
            else:
                f2 = mdes_regression_change(
                    n, effect["k_tested"], effect["k_control"], alpha, power
                )
            # An MDES this large means the design has essentially no residual df:
            # only an implausible effect (R² > 0.9, i.e. f² > 9) would be
            # detectable. Reporting a four-digit f² misleads, so we treat it as
            # "not meaningfully estimable at this N" (-> None).
            if f2 > 9.0:
                return None
            return {"metric": "f2", "value": f2}
        if etype == "exploratory":
            return None
    except ValueError:
        return None
    raise ValueError(f"Unknown effect type: {etype!r}")
