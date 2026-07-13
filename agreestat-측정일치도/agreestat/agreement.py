"""Method-comparison (agreement) statistics — pure standard library.

Implements, from first principles:

* **Bland-Altman** bias, SD of differences, 95% limits of agreement (LoA), and
  confidence intervals for the bias and each LoA; a proportional-bias regression
  (differences on means); and an optional percentage variant.
* **ICC(2,1)** (two-way random, absolute agreement) and **ICC(3,1)** (two-way
  mixed, consistency), single measures, from the two-way ANOVA mean squares,
  each with an exact F-based 95% CI (Shrout & Fleiss 1979; McGraw & Wong 1996).
* **Lin's concordance correlation coefficient (CCC)** with its
  z-transform confidence interval (Lin 1989, 2000).
* **Within-subject CV** and the **repeatability coefficient** from replicate
  measurements (Bland & Altman 1996).
* **Pearson r** (with Fisher-z CI) and the **paired-difference t-test** for
  context — reported alongside a reminder that correlation is not agreement.

All p-values / quantiles come from :mod:`agreestat.special`; no numpy/scipy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .special import f_ppf, f_sf, norm_ppf, t_ppf, t_sf_two_sided

__all__ = [
    "mean",
    "variance",
    "BlandAltmanResult",
    "ICCResult",
    "CCCResult",
    "RepeatabilityResult",
    "PearsonResult",
    "PairedTResult",
    "bland_altman",
    "two_way_ms",
    "icc",
    "ccc",
    "repeatability",
    "pearson",
    "paired_t",
    "interpret_icc",
    "interpret_ccc",
]

_Z_LOA = 1.96  # normal multiplier defining the 95% limits of agreement


def mean(x: Sequence[float]) -> float:
    return sum(x) / len(x)


def variance(x: Sequence[float], ddof: int = 1) -> float:
    """Sample variance with ``ddof`` delta degrees of freedom (default 1)."""
    n = len(x)
    if n - ddof <= 0:
        raise ValueError("not enough observations for the requested ddof")
    m = mean(x)
    return sum((v - m) ** 2 for v in x) / (n - ddof)


def _sd(x: Sequence[float], ddof: int = 1) -> float:
    return math.sqrt(variance(x, ddof))


# --------------------------------------------------------------------------
# Bland-Altman
# --------------------------------------------------------------------------
@dataclass
class BlandAltmanResult:
    n: int
    mode: str  # "absolute" or "percent"
    unit: str  # "" for absolute, "%" for percent
    bias: float
    sd_diff: float
    loa_lower: float
    loa_upper: float
    bias_ci: Tuple[float, float]
    loa_lower_ci: Tuple[float, float]
    loa_upper_ci: Tuple[float, float]
    prop_slope: float
    prop_intercept: float
    prop_pvalue: float
    prop_bias: bool
    alpha: float


def _ols_slope_test(x: Sequence[float], y: Sequence[float]
                    ) -> Tuple[float, float, float]:
    """OLS of y on x; return (slope, intercept, two-sided p for slope=0)."""
    n = len(x)
    mx, my = mean(x), mean(y)
    sxx = sum((xi - mx) ** 2 for xi in x)
    if sxx == 0.0:
        return 0.0, my, float("nan")  # no spread in x -> slope undefined
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    slope = sxy / sxx
    intercept = my - slope * mx
    if n <= 2:
        return slope, intercept, float("nan")
    sse = sum((yi - (intercept + slope * xi)) ** 2 for xi, yi in zip(x, y))
    mse = sse / (n - 2)
    if mse <= 0.0:
        # Perfect linear fit: slope is exact. Non-zero slope -> p≈0.
        return slope, intercept, 0.0 if slope != 0.0 else 1.0
    se_slope = math.sqrt(mse / sxx)
    t = slope / se_slope
    return slope, intercept, t_sf_two_sided(t, n - 2)


def bland_altman(a: Sequence[float], b: Sequence[float], alpha: float = 0.05,
                 mode: str = "absolute") -> BlandAltmanResult:
    """Bland-Altman analysis of A vs B (differences A - B).

    ``mode='percent'`` computes differences as 100*(A-B)/((A+B)/2), for data
    whose error scales with magnitude.
    """
    n = len(a)
    if n < 2:
        raise ValueError("need at least 2 paired observations")
    means = [(x + y) / 2.0 for x, y in zip(a, b)]
    if mode == "percent":
        for m in means:
            if m == 0.0:
                raise ValueError(
                    "percentage Bland-Altman undefined: a pair has mean 0")
        diffs = [100.0 * (x - y) / m for x, y, m in zip(a, b, means)]
        unit = "%"
    elif mode == "absolute":
        diffs = [x - y for x, y in zip(a, b)]
        unit = ""
    else:
        raise ValueError("mode must be 'absolute' or 'percent'")

    bias = mean(diffs)
    sd = _sd(diffs)
    loa_lower = bias - _Z_LOA * sd
    loa_upper = bias + _Z_LOA * sd

    tcrit = t_ppf(1.0 - alpha / 2.0, n - 1)
    se_bias = sd / math.sqrt(n)
    bias_ci = (bias - tcrit * se_bias, bias + tcrit * se_bias)
    # Variance of an estimated limit (Bland & Altman 1999):
    #   Var(LoA) = s^2 * [1/n + z^2 / (2(n-1))]
    se_loa = sd * math.sqrt(1.0 / n + _Z_LOA ** 2 / (2.0 * (n - 1)))
    loa_lower_ci = (loa_lower - tcrit * se_loa, loa_lower + tcrit * se_loa)
    loa_upper_ci = (loa_upper - tcrit * se_loa, loa_upper + tcrit * se_loa)

    slope, intercept, p_slope = _ols_slope_test(means, diffs)
    prop_bias = (p_slope == p_slope) and (p_slope < alpha)  # not NaN and sig

    return BlandAltmanResult(
        n=n, mode=mode, unit=unit, bias=bias, sd_diff=sd,
        loa_lower=loa_lower, loa_upper=loa_upper, bias_ci=bias_ci,
        loa_lower_ci=loa_lower_ci, loa_upper_ci=loa_upper_ci,
        prop_slope=slope, prop_intercept=intercept, prop_pvalue=p_slope,
        prop_bias=prop_bias, alpha=alpha)


# --------------------------------------------------------------------------
# ICC (two-way, single measures)
# --------------------------------------------------------------------------
@dataclass
class MeanSquares:
    n: int          # subjects (rows)
    k: int          # measurements per subject (columns)
    msr: float      # between-subjects (rows)
    msc: float      # between-measurements (columns)
    mse: float      # residual (error)
    ssr: float
    ssc: float
    sse: float
    sst: float


@dataclass
class ICCResult:
    model: str            # "ICC(2,1)" or "ICC(3,1)"
    description: str
    value: float
    ci_lower: float
    ci_upper: float
    f: float
    df1: float
    df2: float
    pvalue: float
    interpretation: str


def two_way_ms(rows: Sequence[Sequence[float]]) -> MeanSquares:
    """Two-way ANOVA mean squares for an n-subjects x k-measurements table."""
    n = len(rows)
    if n < 2:
        raise ValueError("need at least 2 subjects for ICC")
    k = len(rows[0])
    if k < 2:
        raise ValueError("need at least 2 measurements per subject")
    if any(len(r) != k for r in rows):
        raise ValueError("all subjects must have the same number of measurements")

    grand = sum(sum(r) for r in rows) / (n * k)
    row_means = [sum(r) / k for r in rows]
    col_means = [sum(rows[i][j] for i in range(n)) / n for j in range(k)]

    ssr = k * sum((rm - grand) ** 2 for rm in row_means)
    ssc = n * sum((cm - grand) ** 2 for cm in col_means)
    sst = sum((rows[i][j] - grand) ** 2 for i in range(n) for j in range(k))
    sse = sst - ssr - ssc
    if sse < 0.0 and sse > -1e-9 * (sst + 1.0):
        sse = 0.0  # clamp tiny negative round-off

    df_r = n - 1
    df_c = k - 1
    df_e = (n - 1) * (k - 1)
    return MeanSquares(n, k, ssr / df_r, ssc / df_c, sse / df_e,
                       ssr, ssc, sse, sst)


def interpret_icc(value: float) -> str:
    """Koo & Li (2016) reliability thresholds."""
    if value != value:  # NaN
        return "판정 불가 / undefined"
    if value < 0.5:
        return "poor / 낮음"
    if value < 0.75:
        return "moderate / 보통"
    if value < 0.9:
        return "good / 좋음"
    return "excellent / 매우 좋음"


def icc(rows: Sequence[Sequence[float]], alpha: float = 0.05
        ) -> Tuple[ICCResult, ICCResult, MeanSquares]:
    """Return (ICC(2,1), ICC(3,1), MeanSquares) for a subject x method table."""
    ms = two_way_ms(rows)
    n, k = ms.n, ms.k
    msr, msc, mse = ms.msr, ms.msc, ms.mse

    df1 = n - 1
    df2 = (n - 1) * (k - 1)
    # Consistency F (used by both models for the significance test).
    f = msr / mse if mse > 0 else float("inf")
    p = f_sf(f, df1, df2) if math.isfinite(f) else 0.0

    # ---- ICC(3,1): consistency, two-way mixed, single measures ----
    denom3 = msr + (k - 1) * mse
    v3 = (msr - mse) / denom3 if denom3 != 0 else float("nan")
    if math.isfinite(f) and denom3 != 0:
        fl = f / f_ppf(1.0 - alpha / 2.0, df1, df2)
        fu = f * f_ppf(1.0 - alpha / 2.0, df2, df1)
        lo3 = (fl - 1.0) / (fl + (k - 1))
        hi3 = (fu - 1.0) / (fu + (k - 1))
    else:
        lo3 = hi3 = float("nan")
    icc31 = ICCResult(
        "ICC(3,1)", "two-way mixed, consistency, single measures",
        v3, lo3, hi3, f, float(df1), float(df2), p, interpret_icc(v3))

    # ---- ICC(2,1): absolute agreement, two-way random, single measures ----
    denom2 = msr + (k - 1) * mse + (k / n) * (msc - mse)
    v2 = (msr - mse) / denom2 if denom2 != 0 else float("nan")
    if math.isfinite(v2) and 0.0 < v2 < 1.0 and mse > 0:
        a = (k * v2) / (n * (1.0 - v2))
        bb = 1.0 + (k * v2 * (n - 1)) / (n * (1.0 - v2))
        vnum = (a * msc + bb * mse) ** 2
        vden = (a * msc) ** 2 / (k - 1) + (bb * mse) ** 2 / ((n - 1) * (k - 1))
        v = vnum / vden if vden > 0 else float("nan")
        if math.isfinite(v) and v > 0:
            fu_c = f_ppf(1.0 - alpha / 2.0, df1, v)
            fl_c = f_ppf(1.0 - alpha / 2.0, v, df1)
            common = k * msc + (k * n - k - n) * mse
            lo2 = n * (msr - fu_c * mse) / (fu_c * common + n * msr)
            hi2 = n * (fl_c * msr - mse) / (common + n * fl_c * msr)
        else:
            lo2 = hi2 = float("nan")
    else:
        lo2 = hi2 = float("nan")
    icc21 = ICCResult(
        "ICC(2,1)", "two-way random, absolute agreement, single measures",
        v2, lo2, hi2, f, float(df1), float(df2), p, interpret_icc(v2))

    return icc21, icc31, ms


# --------------------------------------------------------------------------
# Lin's concordance correlation coefficient
# --------------------------------------------------------------------------
@dataclass
class CCCResult:
    value: float
    ci_lower: float
    ci_upper: float
    pearson_r: float
    bias_correction: float  # Cb = CCC / r (accuracy)
    interpretation: str
    alpha: float


def interpret_ccc(value: float) -> str:
    """McBride (2005) strength-of-agreement grades for the CCC."""
    if value != value:
        return "판정 불가 / undefined"
    if value < 0.90:
        return "poor / 낮음"
    if value < 0.95:
        return "moderate / 보통"
    if value < 0.99:
        return "substantial / 상당함"
    return "almost perfect / 거의 완벽"


def ccc(a: Sequence[float], b: Sequence[float], alpha: float = 0.05) -> CCCResult:
    """Lin's concordance correlation coefficient with z-transform CI."""
    n = len(a)
    if n < 2:
        raise ValueError("need at least 2 paired observations")
    ma, mb = mean(a), mean(b)
    # Population (1/n) moments, per Lin (1989).
    sa2 = sum((x - ma) ** 2 for x in a) / n
    sb2 = sum((y - mb) ** 2 for y in b) / n
    sab = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / n

    denom = sa2 + sb2 + (ma - mb) ** 2
    if denom == 0.0:
        raise ValueError("CCC undefined: both methods are constant and equal")
    value = 2.0 * sab / denom

    if sa2 <= 0.0 or sb2 <= 0.0:
        # A constant method has no variance -> Pearson r and the CI undefined.
        return CCCResult(value, float("nan"), float("nan"), float("nan"),
                         float("nan"), interpret_ccc(value), alpha)

    r = sab / math.sqrt(sa2 * sb2)
    cb = value / r if r != 0.0 else float("nan")

    lo = hi = float("nan")
    if n > 2 and abs(value) < 1.0 and r != 0.0:
        u = (mb - ma) / (sa2 * sb2) ** 0.25
        p = value
        try:
            var_z = (
                (1.0 - r ** 2) * p ** 2 / ((1.0 - p ** 2) * r ** 2)
                + 2.0 * p ** 3 * (1.0 - p) * u ** 2 / (r * (1.0 - p ** 2) ** 2)
                - 0.5 * p ** 4 * u ** 4 / (r ** 2 * (1.0 - p ** 2) ** 2)
            ) / (n - 2)
        except ZeroDivisionError:
            var_z = float("nan")
        if var_z == var_z and var_z >= 0.0:
            se_z = math.sqrt(var_z)
            z = math.atanh(value)
            zc = norm_ppf(1.0 - alpha / 2.0)
            lo = math.tanh(z - zc * se_z)
            hi = math.tanh(z + zc * se_z)

    return CCCResult(value, lo, hi, r, cb, interpret_ccc(value), alpha)


# --------------------------------------------------------------------------
# Repeatability (replicate measurements)
# --------------------------------------------------------------------------
@dataclass
class RepeatabilityResult:
    available: bool
    note: str = ""
    n_subjects: int = 0
    n_replicated: int = 0
    sw_a: float = float("nan")   # within-subject SD, method A
    sw_b: float = float("nan")
    cv_a: float = float("nan")   # within-subject CV %, method A
    cv_b: float = float("nan")
    rc_a: float = float("nan")   # repeatability coefficient, method A
    rc_b: float = float("nan")


def _within_subject_sd(values_by_subject: Dict[str, List[float]]
                       ) -> Tuple[float, int]:
    """Pooled within-subject SD via one-way ANOVA residual (Bland & Altman 1996).

    Returns (sw, n_subjects_with_replicates). sw is NaN if no subject has >1 obs.
    """
    ss = 0.0
    dof = 0
    replicated = 0
    for vals in values_by_subject.values():
        m = len(vals)
        if m < 2:
            continue
        replicated += 1
        mu = sum(vals) / m
        ss += sum((v - mu) ** 2 for v in vals)
        dof += m - 1
    if dof == 0:
        return float("nan"), replicated
    return math.sqrt(ss / dof), replicated


def repeatability(a: Sequence[float], b: Sequence[float],
                  subjects: Optional[Sequence[str]]) -> RepeatabilityResult:
    """Within-subject CV and repeatability coefficient from replicate measures."""
    if subjects is None:
        return RepeatabilityResult(False, "no subject-id column supplied")

    by_a: Dict[str, List[float]] = {}
    by_b: Dict[str, List[float]] = {}
    for s, x, y in zip(subjects, a, b):
        by_a.setdefault(s, []).append(x)
        by_b.setdefault(s, []).append(y)

    sw_a, rep_a = _within_subject_sd(by_a)
    sw_b, rep_b = _within_subject_sd(by_b)
    n_subjects = len(by_a)
    if sw_a != sw_a and sw_b != sw_b:  # both NaN
        return RepeatabilityResult(
            False, "no subject has replicate measurements", n_subjects, 0)

    grand_a = mean(a)
    grand_b = mean(b)
    cv_a = 100.0 * sw_a / grand_a if grand_a != 0 and sw_a == sw_a else float("nan")
    cv_b = 100.0 * sw_b / grand_b if grand_b != 0 and sw_b == sw_b else float("nan")
    # RC = 1.96 * sqrt(2) * sw  (Bland & Altman) = 2.77 * sw
    rc_a = 1.96 * math.sqrt(2.0) * sw_a if sw_a == sw_a else float("nan")
    rc_b = 1.96 * math.sqrt(2.0) * sw_b if sw_b == sw_b else float("nan")
    return RepeatabilityResult(
        True, "", n_subjects, max(rep_a, rep_b), sw_a, sw_b, cv_a, cv_b, rc_a, rc_b)


# --------------------------------------------------------------------------
# Pearson r + paired t-test (context only)
# --------------------------------------------------------------------------
@dataclass
class PearsonResult:
    r: float
    ci_lower: float
    ci_upper: float
    n: int


@dataclass
class PairedTResult:
    t: float
    df: int
    pvalue: float
    mean_diff: float


def pearson(a: Sequence[float], b: Sequence[float], alpha: float = 0.05
            ) -> PearsonResult:
    """Pearson correlation with a Fisher-z confidence interval."""
    n = len(a)
    ma, mb = mean(a), mean(b)
    sa = sum((x - ma) ** 2 for x in a)
    sb = sum((y - mb) ** 2 for y in b)
    sab = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    if sa <= 0.0 or sb <= 0.0:
        return PearsonResult(float("nan"), float("nan"), float("nan"), n)
    r = sab / math.sqrt(sa * sb)
    r = max(-1.0, min(1.0, r))
    if n > 3 and abs(r) < 1.0:
        z = math.atanh(r)
        se = 1.0 / math.sqrt(n - 3)
        zc = norm_ppf(1.0 - alpha / 2.0)
        lo = math.tanh(z - zc * se)
        hi = math.tanh(z + zc * se)
    else:
        lo = hi = float("nan")
    return PearsonResult(r, lo, hi, n)


def paired_t(a: Sequence[float], b: Sequence[float]) -> PairedTResult:
    """Paired-difference t-test (equivalent to testing Bland-Altman bias = 0)."""
    n = len(a)
    if n < 2:
        raise ValueError("need at least 2 paired observations")
    diffs = [x - y for x, y in zip(a, b)]
    md = mean(diffs)
    sd = _sd(diffs)
    df = n - 1
    if sd == 0.0:
        # No variability: difference is exactly md.
        t = float("inf") if md != 0.0 else 0.0
        p = 0.0 if md != 0.0 else 1.0
        return PairedTResult(t, df, p, md)
    t = md / (sd / math.sqrt(n))
    return PairedTResult(t, df, t_sf_two_sided(t, df), md)
