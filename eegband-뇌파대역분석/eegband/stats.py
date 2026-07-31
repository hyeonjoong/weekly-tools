"""Summary statistics and trend tests for epoch-wise endpoints — standard library.

Everything here operates on the *series of per-epoch values* of one endpoint (e.g.
absolute SWA per 30 s epoch) and answers the two questions a sleep/pharma analyst
actually asks of it:

  * **How much and how variable?** mean, sample SD, SEM, t-based 95% CI, median/IQR
    /range — plus an **autocorrelation-adjusted** CI, because consecutive EEG epochs
    are strongly serially correlated and the naive CI is therefore too narrow.
  * **Is it drifting?** a nonparametric **Mann–Kendall** trend test (tie-corrected,
    continuity-corrected normal approximation) with **Theil–Sen** slope — the classic
    way to quantify homeostatic SWA decline across a night, or a drug's time course,
    without assuming normality or a linear model.

All estimators are deterministic and dependency-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

__all__ = [
    "t_crit",
    "quantile",
    "summary_stats",
    "lag1_autocorr",
    "effective_n",
    "TrendResult",
    "mann_kendall",
    "theil_sen_slope",
    "trend",
    "MAX_EXACT_TREND_N",
    "t_quantile",
    "student_t_sf",
    "ContrastResult",
    "welch_ttest",
    "bh_fdr",
]

# Two-sided 97.5% Student-t critical values for df = 1..30; expansion beyond.
_T975 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080,
    22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
    29: 2.045, 30: 2.042,
}

_Z975 = 1.959963984540054  # standard-normal 0.975 quantile

# Mann–Kendall / Theil–Sen are O(n²); above this many epochs they are skipped rather
# than making the tool hang (a whole night of 30 s epochs is ~1000).
MAX_EXACT_TREND_N = 1500


def _safe_fsum(terms) -> float:
    """``math.fsum`` that returns NaN instead of raising on an overflowed series.

    Squared deviations of a recording with amplitudes around 1e150 overflow to ``inf``;
    ``math.fsum`` then raises ``ValueError: -inf + inf`` and the whole run dies with a
    traceback. A variance that overflowed is simply undefined — return NaN and let the
    caller report it as such (``analyze`` already warns that the spectrum overflowed).
    """
    try:
        total = math.fsum(terms)
    except (ValueError, OverflowError):
        return float("nan")
    return total


def t_crit(df: int) -> float:
    """Two-sided 95% (0.975 one-tail) Student-t critical value for ``df`` d.o.f.

    Exact 3-dp table for df<=30; for df>30 a Cornish–Fisher expansion of the t
    quantile around the normal (accurate to <1e-3 for df>30), so there is no
    discontinuity at df=31 and CIs for many-epoch (full-night) recordings stay
    correct rather than collapsing to the normal 1.96.
    """
    if df <= 0:
        return float("nan")
    if df <= 30:
        return _T975[df]
    z = _Z975
    g1 = (z ** 3 + z) / 4.0
    g2 = (5 * z ** 5 + 16 * z ** 3 + 3 * z) / 96.0
    g3 = (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / 384.0
    g4 = (79 * z ** 9 + 776 * z ** 7 + 1482 * z ** 5
          - 1920 * z ** 3 - 945 * z) / 92160.0
    return z + g1 / df + g2 / df ** 2 + g3 / df ** 3 + g4 / df ** 4


def quantile(sorted_vals: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile (type-7, matches numpy.quantile default)."""
    n = len(sorted_vals)
    if n == 0:
        raise ValueError("quantile of an empty sequence")
    if not (0.0 <= q <= 1.0):
        raise ValueError(f"q must be in [0, 1], got {q!r}")
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def lag1_autocorr(vals: Sequence[float]) -> Optional[float]:
    """Lag-1 autocorrelation ρ̂ of a series (None when undefined).

    ρ̂ = Σ(xᵢ−x̄)(xᵢ₊₁−x̄) / Σ(xᵢ−x̄)². Returns None for n < 3 or a constant series
    (no variance ⇒ no correlation to estimate).
    """
    n = len(vals)
    if n < 3:
        return None
    mean = math.fsum(vals) / n
    denom = _safe_fsum((v - mean) * (v - mean) for v in vals)
    if not math.isfinite(denom) or denom <= 0:
        return None
    num = _safe_fsum((vals[i] - mean) * (vals[i + 1] - mean) for i in range(n - 1))
    if not math.isfinite(num):
        return None
    return num / denom


def effective_n(n: int, rho: Optional[float]) -> float:
    """Effective sample size of ``n`` AR(1)-correlated observations.

    ``n_eff = n·(1−ρ)/(1+ρ)`` (the standard first-order correction for the variance
    of a mean of serially correlated data). Negative ρ would *increase* n_eff, which
    would understate uncertainty, so it is floored at the naive ``n``; the result is
    clamped to [2, n]. With ρ = 0 this returns n exactly.
    """
    if n < 2:
        return float(n)
    if rho is None or not math.isfinite(rho) or rho <= 0.0:
        return float(n)
    rho = min(rho, 0.999)
    n_eff = n * (1.0 - rho) / (1.0 + rho)
    return max(2.0, min(float(n), n_eff))


def summary_stats(vals: Sequence[float]) -> Dict[str, float]:
    """Descriptive statistics of an endpoint across epochs.

    mean, sample SD (n−1), SEM, t-based 95% CI on the mean, median, quartiles, range,
    plus the lag-1 autocorrelation and an **autocorrelation-adjusted** 95% CI that
    uses the effective sample size ``n_eff = n(1−ρ)/(1+ρ)`` (SEM_adj = SD/√n_eff,
    t with df = n_eff−1). For strongly autocorrelated epoch series the adjusted CI is
    much wider — and much closer to honest — than the naive one.

    With a **single** observation the spread statistics are ``NaN``, not 0: one epoch
    carries no information about variability, and "± 0.000 (SD)" with a zero-width CI
    is fabricated precision that downstream pooling (inverse-variance weighting, a
    meta-analysis) would take at face value. ``adjusted`` is 1.0 only when an
    autocorrelation adjustment was actually applied (needs n ≥ 3 and ρ̂ > 0).
    """
    n = len(vals)
    if n == 0:
        raise ValueError("summary_stats of an empty sequence")
    nan = float("nan")
    mean = math.fsum(vals) / n
    if n > 1:
        var = _safe_fsum((v - mean) * (v - mean) for v in vals) / (n - 1)
        sd = math.sqrt(var)
        sem = sd / math.sqrt(n)
        half = t_crit(n - 1) * sem
    else:
        sd = sem = half = nan
    rho = lag1_autocorr(vals)
    n_eff = effective_n(n, rho)
    adjusted = (rho is not None and math.isfinite(rho) and rho > 0.0 and n > 1)
    if n > 1 and n_eff > 1:
        sem_adj = sd / math.sqrt(n_eff)
        # df is fractional; use the (conservative) floor, never below 1.
        df_adj = max(1, int(math.floor(n_eff - 1)))
        half_adj = t_crit(df_adj) * sem_adj
    else:
        sem_adj = half_adj = nan
    s = sorted(vals)
    return {
        "n": float(n), "mean": mean, "sd": sd, "sem": sem,
        "ci_lo": mean - half, "ci_hi": mean + half,
        "median": quantile(s, 0.5), "q1": quantile(s, 0.25),
        "q3": quantile(s, 0.75), "min": s[0], "max": s[-1],
        "rho1": nan if rho is None else rho,
        "n_eff": n_eff, "sem_adj": sem_adj,
        "ci_lo_adj": mean - half_adj, "ci_hi_adj": mean + half_adj,
        "adjusted": 1.0 if adjusted else 0.0,
    }


@dataclass
class TrendResult:
    """Mann–Kendall trend test + Theil–Sen slope of an epoch series."""

    n: int
    s: int                 # Mann–Kendall S statistic (Σ sign(xⱼ−xᵢ), i<j)
    var_s: float           # tie-corrected variance of S
    z: float               # continuity-corrected normal statistic
    p: float               # two-sided p-value (normal approximation)
    tau: float             # Kendall's tau-b
    slope: float           # Theil–Sen slope, per unit of x (per second if x=time)
    slope_lo: float        # 95% CI of the slope (Sen/Gilbert rank interval)
    slope_hi: float
    x_unit: str = "epoch"  # what one unit of x means ("sec" when times are given)
    exact: bool = True     # False when n exceeded MAX_EXACT_TREND_N (test skipped)


def _norm_sf(z: float) -> float:
    """Upper-tail probability of the standard normal, P(Z > z)."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def mann_kendall(vals: Sequence[float]) -> Optional[Dict[str, float]]:
    """Mann–Kendall trend test with tie correction (normal approximation).

    Returns dict with s, var_s, z, p (two-sided), tau (tau-b). ``None`` when n < 4
    (the normal approximation is meaningless and no exact table is bundled) or when
    every value is tied (no ranking information at all).

    S = Σ_{i<j} sign(xⱼ − xᵢ);  Var(S) = [n(n−1)(2n+5) − Σ_g t_g(t_g−1)(2t_g+5)]/18
    over groups of ``t_g`` tied values;  z = (S − sign(S))/√Var(S).
    """
    n = len(vals)
    if n < 4:
        return None
    s = 0
    for i in range(n - 1):
        vi = vals[i]
        for j in range(i + 1, n):
            d = vals[j] - vi
            if d > 0:
                s += 1
            elif d < 0:
                s -= 1
    # tie groups
    counts: Dict[float, int] = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    tie_term = math.fsum(t * (t - 1) * (2 * t + 5) for t in counts.values() if t > 1)
    var_s = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0
    if var_s <= 0:
        return None
    if s > 0:
        z = (s - 1) / math.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / math.sqrt(var_s)
    else:
        z = 0.0
    p = 2.0 * _norm_sf(abs(z))
    p = min(1.0, max(0.0, p))
    # tau-b: divide by sqrt((n0 − n1)(n0 − n2)); x (time) has no ties ⇒ n2 = 0.
    n0 = n * (n - 1) / 2.0
    n1 = math.fsum(t * (t - 1) / 2.0 for t in counts.values() if t > 1)
    denom = math.sqrt(max(n0 - n1, 0.0) * n0)
    tau = s / denom if denom > 0 else float("nan")
    return {"s": float(s), "var_s": var_s, "z": z, "p": p, "tau": tau}


def theil_sen_slope(vals: Sequence[float], xs: Optional[Sequence[float]] = None,
                    ) -> Optional[Dict[str, float]]:
    """Theil–Sen (median-of-pairwise-slopes) estimator with a Sen 95% CI.

    ``xs`` defaults to 0, 1, 2, … (per-epoch units). The CI is the classic
    rank-interval consistent with the Mann–Kendall test (Gilbert 1987): with the N
    pairwise slopes sorted and ``C = z₀.₉₇₅·√Var(S)``, the limits are the slopes at
    0-based ranks ``round((N−C)/2)−1`` and ``round((N+C)/2)`` — the same indices
    ``scipy.stats.theilslopes`` uses (Sen 1968 eq. 2.6), so the two agree exactly for
    distinct x. ``None`` when fewer than 2 usable pairs exist.
    """
    n = len(vals)
    if n < 2:
        return None
    if xs is None:
        xs = list(range(n))
    if len(xs) != n:
        raise ValueError("xs and vals must have the same length")
    slopes: List[float] = []
    for i in range(n - 1):
        for j in range(i + 1, n):
            dx = xs[j] - xs[i]
            if dx != 0:
                slopes.append((vals[j] - vals[i]) / dx)
    if not slopes:
        return None
    slopes.sort()
    m = len(slopes)
    med = quantile(slopes, 0.5)
    mk = mann_kendall(vals)
    if mk is None:
        return {"slope": med, "slope_lo": float("nan"), "slope_hi": float("nan")}
    c = _Z975 * math.sqrt(mk["var_s"])
    lo_idx = max(0, min(m - 1, int(round((m - c) / 2.0)) - 1))
    hi_idx = max(0, min(m - 1, int(round((m + c) / 2.0))))
    if hi_idx < lo_idx:
        lo_idx, hi_idx = hi_idx, lo_idx
    return {"slope": med, "slope_lo": slopes[lo_idx], "slope_hi": slopes[hi_idx]}


def trend(vals: Sequence[float], xs: Optional[Sequence[float]] = None,
          x_unit: str = "epoch", max_n: int = MAX_EXACT_TREND_N,
          ) -> Optional[TrendResult]:
    """Mann–Kendall test + Theil–Sen slope of ``vals`` against ``xs``.

    Returns ``None`` when the series is too short (n < 4), fully tied, or longer than
    ``max_n`` (both estimators are O(n²); a whole night of 30 s epochs is ~1000, so
    the cap only bites on pathologically fine epoching — and it is reported rather
    than silently truncated).
    """
    n = len(vals)
    if n < 4:
        return None
    if n > max_n:
        return TrendResult(n=n, s=0, var_s=float("nan"), z=float("nan"),
                           p=float("nan"), tau=float("nan"), slope=float("nan"),
                           slope_lo=float("nan"), slope_hi=float("nan"),
                           x_unit=x_unit, exact=False)
    mk = mann_kendall(vals)
    if mk is None:
        return None
    ts = theil_sen_slope(vals, xs)
    if ts is None:
        return None
    return TrendResult(n=n, s=int(mk["s"]), var_s=mk["var_s"], z=mk["z"], p=mk["p"],
                       tau=mk["tau"], slope=ts["slope"], slope_lo=ts["slope_lo"],
                       slope_hi=ts["slope_hi"], x_unit=x_unit, exact=True)


# ---------------------------------------------------------------------------
# Two-group contrast (baseline vs post) — Welch's t with an AR(1) correction.
# ---------------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's algorithm)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-16:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log1p(-x))
    front = math.exp(lbeta)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + b * math.log1p(-x) + a * math.log(x)) * _betacf(b, a, 1.0 - x) / b


def student_t_sf(t: float, df: float) -> float:
    """Upper-tail probability P(T > t) of Student's t with ``df`` d.o.f.

    Exact (to double precision) via the regularised incomplete beta function, so a
    fractional ``df`` — which Welch's approximation and the AR(1) effective-sample-size
    correction both produce — is handled properly instead of being rounded to a table
    entry.
    """
    if df <= 0 or not math.isfinite(df):
        return float("nan")
    if not math.isfinite(t):
        return 0.0 if t > 0 else 1.0
    x = df / (df + t * t)
    p_tail = 0.5 * _betainc(0.5 * df, 0.5, x)   # P(|T| > |t|) / 2
    return p_tail if t > 0 else 1.0 - p_tail


def t_quantile(p: float, df: float) -> float:
    """Two-sided (1−p) critical value of Student's t, i.e. the ``1 − p/2`` quantile.

    ``t_quantile(0.05, df)`` is the usual 95% CI multiplier. Solved by bisection on
    :func:`student_t_sf`, which keeps fractional ``df`` exact (``t_crit`` is a 3-dp
    table plus an asymptotic expansion, and is kept for the existing call sites).
    """
    if df <= 0 or not math.isfinite(df):
        return float("nan")
    if not (0.0 < p < 1.0):
        raise ValueError(f"p must be in (0, 1), got {p!r}")
    target = p / 2.0
    lo, hi = 0.0, 1.0
    # Grow the bracket until it actually contains the quantile. A fixed cap would
    # silently return the cap itself for heavy tails (df<1) or tiny p, where the true
    # quantile is astronomically large -- a wrong number with no error.
    for _ in range(4000):
        if student_t_sf(hi, df) <= target:
            break
        hi *= 2.0
    else:
        return float("inf")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if student_t_sf(mid, df) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12 * max(1.0, hi):
            break
    return 0.5 * (lo + hi)


@dataclass
class ContrastResult:
    """Welch two-sample contrast between a baseline and a post-baseline series."""
    n_a: int                 # baseline epochs
    n_b: int                 # post epochs
    mean_a: float
    mean_b: float
    sd_a: float
    sd_b: float
    diff: float              # mean_b − mean_a
    pct_change: float        # 100·diff/mean_a (NaN when mean_a <= 0)
    se: float                # SE of the difference
    df: float                # Welch–Satterthwaite d.o.f. (fractional)
    t: float
    p: float                 # two-sided
    ci_lo: float
    ci_hi: float
    hedges_g: float          # bias-corrected standardised mean difference
    n_eff_a: float           # AR(1) effective n actually used
    n_eff_b: float
    adjusted: bool           # True when an autocorrelation correction was applied
    q: float = float("nan")  # Benjamini–Hochberg FDR q-value (filled by the caller)


def welch_ttest(a: Sequence[float], b: Sequence[float],
                adjust_autocorr: bool = True) -> Optional[ContrastResult]:
    """Welch's unequal-variance t-test of ``b`` against ``a`` (b − a).

    Consecutive EEG epochs are strongly serially correlated, so with
    ``adjust_autocorr=True`` (the default) each group's variance of the mean uses its
    **AR(1) effective sample size** ``n_eff = n(1−ρ̂)/(1+ρ̂)`` instead of ``n``. Without
    that, a 30 s-epoch contrast over a few minutes of EEG returns p-values that are
    wrong by orders of magnitude — the epochs are not independent observations.

    Hedges' g uses the pooled SD of the *raw* epoch values with the small-sample
    correction ``1 − 3/(4·df_pool − 1)``; it describes the size of the shift in SD
    units and is deliberately **not** autocorrelation-adjusted (an effect size is a
    property of the distributions, not of how many independent draws were taken).

    Returns None when either group has fewer than 2 values or both are constant.
    """
    xs, ys = [float(v) for v in a], [float(v) for v in b]
    na, nb = len(xs), len(ys)
    if na < 2 or nb < 2:
        return None
    mean_a = math.fsum(xs) / na
    mean_b = math.fsum(ys) / nb
    var_a = _safe_fsum((v - mean_a) * (v - mean_a) for v in xs) / (na - 1)
    var_b = _safe_fsum((v - mean_b) * (v - mean_b) for v in ys) / (nb - 1)
    if not (math.isfinite(var_a) and math.isfinite(var_b)):
        return None            # variance overflowed: the contrast is undefined
    if var_a <= 0 and var_b <= 0:
        return None            # two constant series: no variance to test against
    rho_a = lag1_autocorr(xs) if adjust_autocorr else None
    rho_b = lag1_autocorr(ys) if adjust_autocorr else None
    eff_a = effective_n(na, rho_a) if adjust_autocorr else float(na)
    eff_b = effective_n(nb, rho_b) if adjust_autocorr else float(nb)
    adjusted = bool(adjust_autocorr and (eff_a < na or eff_b < nb))

    sa2 = var_a / eff_a
    sb2 = var_b / eff_b
    se = math.sqrt(sa2 + sb2)
    denom = ((sa2 * sa2) / max(eff_a - 1.0, 1e-12)
             + (sb2 * sb2) / max(eff_b - 1.0, 1e-12))
    df = (((sa2 + sb2) * (sa2 + sb2)) / denom) if denom > 0 else float("nan")
    diff = mean_b - mean_a
    if se > 0 and math.isfinite(df) and df > 0:
        t = diff / se
        p = 2.0 * student_t_sf(abs(t), df)
        half = t_quantile(0.05, df) * se
        ci_lo, ci_hi = diff - half, diff + half
    else:
        t = p = ci_lo = ci_hi = float("nan")

    df_pool = na + nb - 2
    pooled_var = (((na - 1) * var_a + (nb - 1) * var_b) / df_pool
                  if df_pool > 0 else float("nan"))
    if pooled_var and pooled_var > 0:
        d = diff / math.sqrt(pooled_var)
        g = d * (1.0 - 3.0 / (4.0 * df_pool - 1.0)) if df_pool > 1 else d
    else:
        g = float("nan")

    pct = (100.0 * diff / mean_a) if mean_a > 0 else float("nan")
    return ContrastResult(
        n_a=na, n_b=nb, mean_a=mean_a, mean_b=mean_b,
        sd_a=math.sqrt(var_a), sd_b=math.sqrt(var_b), diff=diff, pct_change=pct,
        se=se, df=df, t=t, p=p, ci_lo=ci_lo, ci_hi=ci_hi, hedges_g=g,
        n_eff_a=eff_a, n_eff_b=eff_b, adjusted=adjusted)


def bh_fdr(pvals: Sequence[float]) -> List[float]:
    """Benjamini–Hochberg q-values for a family of p-values.

    Every band and every derived endpoint is tested against baseline in one run, so
    the raw p-values are a multiple-comparison family; reporting them unadjusted would
    manufacture a "significant" band out of five. Non-finite p-values pass through as
    NaN and are excluded from the family size, since a test that could not be computed
    is not a test that was performed.
    """
    def _finite(v) -> bool:
        # Accept any real number, not just `float`: an integer 1 or a numpy float is a
        # perfectly good p-value and dropping it would shrink the family silently.
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False
        try:
            return math.isfinite(float(v))
        except (TypeError, ValueError):
            return False

    pvals = [float(v) if _finite(v) else v for v in pvals]
    idx = [i for i, p in enumerate(pvals) if _finite(p)]
    out = [float("nan")] * len(pvals)
    m = len(idx)
    if m == 0:
        return out
    order = sorted(idx, key=lambda i: pvals[i])
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        q = min(prev, pvals[i] * m / rank)
        out[i] = min(max(q, 0.0), 1.0)
        prev = out[i]
    return out
