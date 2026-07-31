"""Descriptive statistics, ranks and the two- / paired-sample tests — stdlib only.

Small building blocks shared by the post-hoc, responder and reporting layers.
Exact permutation distributions are used for Wilcoxon signed-rank and
Mann–Whitney whenever the sample is small and untied — clinical pilot studies
live exactly in that regime and the normal approximation is visibly off there.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

from .special import norm_ppf, norm_sf, t_ppf, t_sf_two_sided

__all__ = [
    "mean", "sd", "sem", "median", "quantile", "iqr",
    "ci_mean", "ranks", "tie_correction",
    "paired_t", "welch_t", "student_t",
    "wilcoxon_signed_rank", "mann_whitney",
    "hedges_g", "cohen_dz", "wilson_interval", "PairedResult", "TwoSampleResult",
]

_EXACT_SIGNRANK_MAX = 50        # DP is O(n^3); n=50 costs <1 ms
# 50 vs 50 costs ~9 ms.  Beyond that the exact and the tie-free normal
# approximation agree to the 4th decimal (measured: .101648 vs .101596 at
# 158 vs 158), so the extra 300 ms per call buys nothing.
_EXACT_MWU_MAX_CELLS = 2_500


# --------------------------------------------------------------------------
# descriptives
# --------------------------------------------------------------------------

def mean(xs: Sequence[float]) -> float:
    if not xs:
        return float("nan")
    return math.fsum(xs) / len(xs)


def sd(xs: Sequence[float]) -> float:
    """Sample standard deviation (n−1); NaN for n < 2."""
    n = len(xs)
    if n < 2:
        return float("nan")
    m = mean(xs)
    var = math.fsum((x - m) ** 2 for x in xs) / (n - 1)
    return math.sqrt(max(0.0, var))


def sem(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return float("nan")
    return sd(xs) / math.sqrt(n)


def quantile(xs: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile (R type 7 / numpy default)."""
    if not xs:
        return float("nan")
    ys = sorted(xs)
    if len(ys) == 1:
        return ys[0]
    pos = (len(ys) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ys[int(pos)]
    return ys[lo] + (ys[hi] - ys[lo]) * (pos - lo)


def median(xs: Sequence[float]) -> float:
    return quantile(xs, 0.5)


def iqr(xs: Sequence[float]) -> Tuple[float, float]:
    return quantile(xs, 0.25), quantile(xs, 0.75)


def ci_mean(xs: Sequence[float], alpha: float = 0.05
            ) -> Tuple[float, float]:
    """Two-sided t-based confidence interval for the mean."""
    n = len(xs)
    if n < 2:
        return float("nan"), float("nan")
    s = sem(xs)
    if not math.isfinite(s):
        return float("nan"), float("nan")
    if s == 0.0:
        m = mean(xs)
        return m, m
    crit = t_ppf(1.0 - alpha / 2.0, n - 1)
    m = mean(xs)
    return m - crit * s, m + crit * s


def wilson_interval(successes: int, n: int, alpha: float = 0.05
                    ) -> Tuple[float, float]:
    """Wilson score interval for a proportion (well behaved at 0 % and 100 %)."""
    if n <= 0:
        return float("nan"), float("nan")
    z = norm_ppf(1.0 - alpha / 2.0)
    p = successes / n
    den = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    lo = max(0.0, centre - half)
    hi = min(1.0, centre + half)
    # Rounding leaves ~1e-17 residue at the boundaries; 0/n and n/n must print
    # as exactly 0 % and 100 %.
    if successes == 0:
        lo = 0.0
    if successes == n:
        hi = 1.0
    return lo, hi


# --------------------------------------------------------------------------
# ranks
# --------------------------------------------------------------------------

def ranks(xs: Sequence[float]) -> List[float]:
    """Average ranks (1-based), ties shared."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for m in range(i, j + 1):
            out[order[m]] = avg
        i = j + 1
    return out


def tie_correction(xs: Sequence[float]) -> float:
    """Σ (t³ − t) over tied groups."""
    counts: dict = {}
    for x in xs:
        counts[x] = counts.get(x, 0) + 1
    return float(sum(c ** 3 - c for c in counts.values() if c > 1))


# --------------------------------------------------------------------------
# parametric tests
# --------------------------------------------------------------------------

@dataclass
class PairedResult:
    n: int
    mean_diff: float
    sd_diff: float
    ci_low: float
    ci_high: float
    t: float
    df: float
    p: float
    dz: float
    dz_hedges: float
    dz_ci: Tuple[float, float]


def paired_t(diffs: Sequence[float], alpha: float = 0.05) -> PairedResult:
    """Paired-samples t-test on already-formed differences."""
    n = len(diffs)
    if n < 2:
        raise ValueError("대응 t-검정에는 최소 2쌍이 필요합니다.")
    m = mean(diffs)
    s = sd(diffs)
    if s == 0.0:
        # Every subject changed by exactly the same amount.  There is no
        # sampling variability to test against, so a p-value does not exist —
        # reporting 0.0 here previously earned the row three significance stars
        # off n = 3 identical differences.
        nan = float("nan")
        return PairedResult(n, m, 0.0, m, m, nan, n - 1, nan, nan, nan,
                            (nan, nan))
    se = s / math.sqrt(n)
    t = m / se
    p = t_sf_two_sided(t, n - 1)
    crit = t_ppf(1.0 - alpha / 2.0, n - 1)
    dz = m / s
    j = 1.0 - 3.0 / (4.0 * (n - 1) - 1.0)
    se_dz = math.sqrt(1.0 / n + dz * dz / (2.0 * n))
    z = norm_ppf(1.0 - alpha / 2.0)
    return PairedResult(
        n=n, mean_diff=m, sd_diff=s, ci_low=m - crit * se, ci_high=m + crit * se,
        t=t, df=float(n - 1), p=p, dz=dz, dz_hedges=j * dz,
        dz_ci=(dz - z * se_dz, dz + z * se_dz))


@dataclass
class TwoSampleResult:
    name: str
    n1: int
    n2: int
    mean1: float
    mean2: float
    diff: float
    ci_low: float
    ci_high: float
    t: float
    df: float
    p: float
    g: float
    g_ci: Tuple[float, float]


def _two_sample(a: Sequence[float], b: Sequence[float], welch: bool,
                alpha: float) -> TwoSampleResult:
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        raise ValueError("두 표본 비교에는 각 군 최소 2명이 필요합니다.")
    m1, m2 = mean(a), mean(b)
    v1, v2 = sd(a) ** 2, sd(b) ** 2
    diff = m1 - m2
    if welch:
        se = math.sqrt(v1 / n1 + v2 / n2)
        if se == 0.0:
            df = float(n1 + n2 - 2)
        else:
            df = (v1 / n1 + v2 / n2) ** 2 / (
                (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))
        label = "Welch t-검정"
    else:
        sp2 = ((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2)
        se = math.sqrt(sp2 * (1.0 / n1 + 1.0 / n2))
        df = float(n1 + n2 - 2)
        label = "Student t-검정"
    if se == 0.0:
        # Both arms are constant: no variance, so no test statistic exists.
        t = p = float("nan")
        lo = hi = diff
    else:
        t = diff / se
        p = t_sf_two_sided(t, df)
        crit = t_ppf(1.0 - alpha / 2.0, df)
        lo, hi = diff - crit * se, diff + crit * se
    g, g_ci = hedges_g(a, b, alpha)
    return TwoSampleResult(label, n1, n2, m1, m2, diff, lo, hi, t, df, p, g, g_ci)


def welch_t(a: Sequence[float], b: Sequence[float], alpha: float = 0.05
            ) -> TwoSampleResult:
    return _two_sample(a, b, True, alpha)


def student_t(a: Sequence[float], b: Sequence[float], alpha: float = 0.05
              ) -> TwoSampleResult:
    return _two_sample(a, b, False, alpha)


def hedges_g(a: Sequence[float], b: Sequence[float], alpha: float = 0.05
             ) -> Tuple[float, Tuple[float, float]]:
    """Hedges' g (bias-corrected Cohen's d) with an approximate normal CI."""
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return float("nan"), (float("nan"), float("nan"))
    v1, v2 = sd(a) ** 2, sd(b) ** 2
    sp = math.sqrt(((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2))
    if sp == 0.0:
        return float("nan"), (float("nan"), float("nan"))
    d = (mean(a) - mean(b)) / sp
    dof = n1 + n2 - 2
    j = 1.0 - 3.0 / (4.0 * dof - 1.0)
    g = j * d
    se = math.sqrt((n1 + n2) / (n1 * n2) + g * g / (2.0 * (n1 + n2)))
    z = norm_ppf(1.0 - alpha / 2.0)
    return g, (g - z * se, g + z * se)


def cohen_dz(diffs: Sequence[float]) -> float:
    s = sd(diffs)
    if not math.isfinite(s) or s == 0.0:
        return float("nan")
    return mean(diffs) / s


# --------------------------------------------------------------------------
# rank tests
# --------------------------------------------------------------------------

@lru_cache(maxsize=128)
def _signrank_counts(n: int) -> Tuple[int, ...]:
    """Number of rank-sum subsets for each value 0..n(n+1)/2 (exact null).

    Cached: the table depends only on n, and one analysis of a many-visit study
    asked for the same n tens of thousands of times (91 % of total runtime).
    """
    total = n * (n + 1) // 2
    dp = [0] * (total + 1)
    dp[0] = 1
    for r in range(1, n + 1):
        for s in range(total, r - 1, -1):
            if dp[s - r]:
                dp[s] += dp[s - r]
    return tuple(dp)


def wilcoxon_signed_rank(diffs: Sequence[float], alpha: float = 0.05
                         ) -> dict:
    """Two-sided Wilcoxon signed-rank test.

    Zero differences are dropped (Wilcoxon's original handling) and reported.
    Exact null distribution when there are no ties among |d| and n ≤ 40;
    otherwise the tie- and continuity-corrected normal approximation.
    """
    nonzero = [d for d in diffs if d != 0.0]
    n_zero = len(diffs) - len(nonzero)
    n = len(nonzero)
    if n == 0:
        return {"n": 0, "n_zero": n_zero, "w": float("nan"),
                "p": 1.0, "method": "모든 차이가 0", "r": 0.0,
                "median_diff": 0.0}
    absd = [abs(d) for d in nonzero]
    rk = ranks(absd)
    t_plus = math.fsum(r for r, d in zip(rk, nonzero) if d > 0)
    t_minus = math.fsum(r for r, d in zip(rk, nonzero) if d < 0)
    w = min(t_plus, t_minus)
    denom = t_plus + t_minus
    r_rb = (t_plus - t_minus) / denom if denom > 0 else 0.0

    has_ties = tie_correction(absd) > 0
    if not has_ties and n <= _EXACT_SIGNRANK_MAX:
        counts = _signrank_counts(n)
        total = float(1 << n)
        cum = float(sum(counts[: int(round(w)) + 1]))
        p = min(1.0, 2.0 * cum / total)
        method = "Wilcoxon 부호순위 (정확검정)"
    else:
        mu = n * (n + 1) / 4.0
        var = n * (n + 1) * (2 * n + 1) / 24.0 - tie_correction(absd) / 48.0
        if var <= 0:
            p = 1.0
        else:
            z = (abs(t_plus - mu) - 0.5) / math.sqrt(var)
            p = min(1.0, 2.0 * norm_sf(max(0.0, z)))
        method = "Wilcoxon 부호순위 (정규근사, 동점·연속성 보정)"
    return {"n": n, "n_zero": n_zero, "w": w, "p": p, "method": method,
            "r": r_rb, "median_diff": median(nonzero)}


@lru_cache(maxsize=128)
def _mwu_counts(n1: int, n2: int) -> Tuple[int, ...]:
    """Exact null counts of Mann–Whitney U (index = U value) for untied samples.

    The generating function of U is the Gaussian binomial coefficient

        [n1+n2, n1]_q = ∏_{i=1..n1} (1 − q^{n2+i}) / (1 − q^i),

    built here in exact integer arithmetic: multiplying by ``(1 − q^m)`` is one
    subtraction pass and dividing by ``(1 − q^i)`` is a strided prefix sum, so
    the whole table costs O(n1 · n1·n2) instead of the O((n1·n2)²) of the naive
    recursion.
    """
    top = n1 * n2
    poly = [0] * (top + 1)
    poly[0] = 1
    for i in range(1, n1 + 1):
        m = n2 + i
        if m <= top:                       # multiply by (1 - q^m)
            for u in range(top, m - 1, -1):
                poly[u] -= poly[u - m]
        for u in range(i, top + 1):        # divide by (1 - q^i)
            poly[u] += poly[u - i]
    return tuple(poly)


def mann_whitney(a: Sequence[float], b: Sequence[float]) -> dict:
    """Two-sided Mann–Whitney U test with exact p for small untied samples."""
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        raise ValueError("두 군 모두 관측값이 필요합니다.")
    allv = list(a) + list(b)
    rk = ranks(allv)
    r1 = math.fsum(rk[:n1])
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    u = min(u1, u2)
    r_rb = 2.0 * u1 / (n1 * n2) - 1.0

    ties = tie_correction(allv)
    if ties == 0 and n1 * n2 <= _EXACT_MWU_MAX_CELLS:
        counts = _mwu_counts(min(n1, n2), max(n1, n2))
        total = float(sum(counts))
        cum = float(sum(counts[: int(round(u)) + 1]))
        p = min(1.0, 2.0 * cum / total)
        method = "Mann–Whitney U (정확검정)"
    else:
        n = n1 + n2
        mu = n1 * n2 / 2.0
        var = n1 * n2 / 12.0 * ((n + 1) - ties / (n * (n - 1.0)))
        if var <= 0:
            p = 1.0
        else:
            z = (abs(u1 - mu) - 0.5) / math.sqrt(var)
            p = min(1.0, 2.0 * norm_sf(max(0.0, z)))
        method = "Mann–Whitney U (정규근사, 동점·연속성 보정)"
    return {"u": u, "u1": u1, "u2": u2, "p": p, "method": method,
            "r": r_rb, "n1": n1, "n2": n2}


def _testable(pvals: Sequence[float]) -> List[int]:
    """Indices of p-values that exist.

    A test that could not be computed (zero variance, n < 2) yields NaN.  Such
    entries must not enter the multiplicity family — sorting NaN is undefined
    and would hand an uncomputable comparison a real adjusted p-value.
    """
    return [i for i, p in enumerate(pvals)
            if isinstance(p, float) and math.isfinite(p)]


def holm(pvals: Sequence[float]) -> List[float]:
    """Holm–Bonferroni step-down adjusted p-values (monotone, capped at 1)."""
    idx = _testable(pvals)
    adj = [float("nan")] * len(pvals)
    m = len(idx)
    if m == 0:
        return adj
    running = 0.0
    for rank, i in enumerate(sorted(idx, key=lambda i: pvals[i])):
        running = max(running, (m - rank) * pvals[i])
        adj[i] = min(1.0, running)
    return adj


def benjamini_hochberg(pvals: Sequence[float]) -> List[float]:
    """Benjamini–Hochberg FDR adjusted p-values."""
    idx = _testable(pvals)
    adj = [float("nan")] * len(pvals)
    m = len(idx)
    if m == 0:
        return adj
    running = 1.0
    for rank, i in enumerate(sorted(idx, key=lambda i: pvals[i], reverse=True)):
        running = min(running, m * pvals[i] / (m - rank))
        adj[i] = min(1.0, running)
    return adj


def adjust(pvals: Sequence[float], method: str) -> List[float]:
    if method == "none":
        return [min(1.0, p) if isinstance(p, float) and math.isfinite(p)
                else float("nan") for p in pvals]
    if method == "bh":
        return benjamini_hochberg(pvals)
    return holm(pvals)


__all__ += ["holm", "benjamini_hochberg", "adjust"]
