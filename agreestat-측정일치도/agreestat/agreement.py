"""Method-comparison (agreement) statistics — pure standard library.

Implements, from first principles:

* **Bland-Altman** bias, SD of differences, 95% limits of agreement (LoA), and
  confidence intervals for the bias and each LoA; a proportional-bias regression
  (differences on means); and an optional percentage variant.
* **ICC(2,1)** (two-way random, absolute agreement) and **ICC(3,1)** (two-way
  mixed, consistency), single measures, from the two-way ANOVA mean squares,
  ICC(3,1)'s CI is the exact F-based interval (Shrout & Fleiss 1979); ICC(2,1)'s
  is McGraw & Wong's (1996) ICC(A,1) interval, which uses a Satterthwaite
  synthesised denominator df and is therefore an approximation.
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
    "RegressionLoA",
    "RepeatedMeasuresBA",
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
    "repeated_measures_ba",
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
_SQRT_HALF_PI = math.sqrt(math.pi / 2.0)  # 1.2533..., E|N(0,s)| = s*sqrt(2/pi)


@dataclass
class RegressionLoA:
    """Mean-dependent (regression-based) LoA for proportional bias (B&A 1999 §3)."""
    available: bool
    note: str = ""
    diff_intercept: float = float("nan")   # b0 in D(m) = b0 + b1*m
    diff_slope: float = float("nan")       # b1
    sd_intercept: float = float("nan")     # c0 in s(m) = 1.253*(c0 + c1*m)
    sd_slope: float = float("nan")         # c1
    factor: float = _SQRT_HALF_PI
    mean_min: float = float("nan")
    mean_max: float = float("nan")
    loa_at_min: Tuple[float, float] = (float("nan"), float("nan"))
    loa_at_max: Tuple[float, float] = (float("nan"), float("nan"))
    fit_at_min: float = float("nan")
    fit_at_max: float = float("nan")
    sd_negative_warning: bool = False


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
    n_outside: int = 0        # points falling outside the 95% LoA
    pct_outside: float = 0.0  # 100 * n_outside / n
    se_loa: float = float("nan")            # SE of each estimated limit
    loa_ci_halfwidth: float = float("nan")  # tcrit * se_loa
    reg_loa: Optional["RegressionLoA"] = None
    means: List[float] = field(default_factory=list)
    diffs: List[float] = field(default_factory=list)


def _regression_loa(means: Sequence[float], diffs: Sequence[float],
                    slope: float, intercept: float) -> RegressionLoA:
    """Regression-based LoA (Bland & Altman 1999 §3): D(m)=b0+b1*m, and the
    residual SD modelled linearly in m via 1.253*|residual| regressed on m."""
    n = len(means)
    if n < 3:
        return RegressionLoA(False, "need >=3 points for regression LoA")
    resid = [d - (intercept + slope * m) for d, m in zip(diffs, means)]
    c1, c0, _p = _ols_slope_test(means, [abs(r) for r in resid])
    if c1 != c1:  # NaN -> no spread in means
        return RegressionLoA(False, "mean has no spread")
    m_min, m_max = min(means), max(means)
    warn = False
    out = {}
    for tag, m in (("min", m_min), ("max", m_max)):
        fit = intercept + slope * m
        s = _SQRT_HALF_PI * (c0 + c1 * m)
        if s < 0.0:
            s = 0.0
            warn = True
        out[tag] = (fit, (fit - _Z_LOA * s, fit + _Z_LOA * s))
    return RegressionLoA(
        True, "", intercept, slope, c0, c1, _SQRT_HALF_PI, m_min, m_max,
        out["min"][1], out["max"][1], out["min"][0], out["max"][0], warn)


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

    loa_ci_halfwidth = tcrit * se_loa

    slope, intercept, p_slope = _ols_slope_test(means, diffs)
    prop_bias = (p_slope == p_slope) and (p_slope < alpha)  # not NaN and sig

    reg_loa = _regression_loa(means, diffs, slope, intercept) if prop_bias else None

    n_outside = sum(1 for d in diffs if d < loa_lower or d > loa_upper)
    pct_outside = 100.0 * n_outside / n

    return BlandAltmanResult(
        n=n, mode=mode, unit=unit, bias=bias, sd_diff=sd,
        loa_lower=loa_lower, loa_upper=loa_upper, bias_ci=bias_ci,
        loa_lower_ci=loa_lower_ci, loa_upper_ci=loa_upper_ci,
        prop_slope=slope, prop_intercept=intercept, prop_pvalue=p_slope,
        prop_bias=prop_bias, alpha=alpha,
        n_outside=n_outside, pct_outside=pct_outside,
        se_loa=se_loa, loa_ci_halfwidth=loa_ci_halfwidth, reg_loa=reg_loa,
        means=list(means), diffs=list(diffs))


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
    # The McGraw & Wong exact CI is valid for the whole admissible range,
    # including non-positive point estimates (verified vs pingouin); only
    # exclude v2==1 (Satterthwaite denominator blows up) and mse==0.
    if math.isfinite(v2) and v2 < 1.0 and mse > 0:
        a = (k * v2) / (n * (1.0 - v2))
        bb = 1.0 + (k * v2 * (n - 1)) / (n * (1.0 - v2))
        vnum = (a * msc + bb * mse) ** 2
        vden = (a * msc) ** 2 / (k - 1) + (bb * mse) ** 2 / ((n - 1) * (k - 1))
        v = vnum / vden if vden > 0 else float("nan")
        if math.isfinite(v) and v > 1e-6:
            fu_c = f_ppf(1.0 - alpha / 2.0, df1, v)
            fl_c = f_ppf(1.0 - alpha / 2.0, v, df1)
            common = k * msc + (k * n - k - n) * mse
            lo2 = n * (msr - fu_c * mse) / (fu_c * common + n * msr)
            hi2 = n * (fl_c * msr - mse) / (common + n * fl_c * msr)
            # Guard the degenerate strongly-negative-ICC region where the
            # Satterthwaite df collapses and the "CI" would exclude the point
            # estimate or pinch to a point (pingouin returns NaN here too).
            if not (math.isfinite(lo2) and math.isfinite(hi2)
                    and lo2 < hi2 and lo2 - 1e-9 <= v2 <= hi2 + 1e-9):
                lo2 = hi2 = float("nan")
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
# Repeated-measures Bland-Altman (Bland & Altman 2007)
# --------------------------------------------------------------------------
@dataclass
class RepeatedMeasuresBA:
    """LoA accounting for within-subject correlation (Bland & Altman 2007).

    When each subject contributes several paired measurements, the naive LoA
    treat correlated rows as independent (too-narrow CI, biased SD on
    unbalanced designs). This variance-components version is the correct one.
    """
    available: bool
    note: str = ""
    n_subjects: int = 0
    n_pairs: int = 0
    n_replicated_subjects: int = 0      # subjects contributing >=2 pairs
    bias: float = float("nan")
    sd_diff: float = float("nan")       # sqrt(var_between + var_within)
    var_between: float = float("nan")
    var_within: float = float("nan")
    m0: float = float("nan")
    loa_lower: float = float("nan")
    loa_upper: float = float("nan")
    loa_lower_ci: Tuple[float, float] = (float("nan"), float("nan"))
    loa_upper_ci: Tuple[float, float] = (float("nan"), float("nan"))
    var_between_clamped: bool = False


def repeated_measures_ba(a: Sequence[float], b: Sequence[float],
                         subjects: Optional[Sequence[str]],
                         mode: str = "absolute", alpha: float = 0.05
                         ) -> RepeatedMeasuresBA:
    """Repeated-measures LoA (Bland & Altman 2007, 'true value varies' case)."""
    if subjects is None:
        return RepeatedMeasuresBA(False, "no subject-id column supplied")

    means = [(x + y) / 2.0 for x, y in zip(a, b)]
    if mode == "percent":
        if any(m == 0.0 for m in means):
            return RepeatedMeasuresBA(
                False, "percentage undefined: a pair has mean 0")
        all_diffs = [100.0 * (x - y) / m for x, y, m in zip(a, b, means)]
    else:
        all_diffs = [x - y for x, y in zip(a, b)]

    by_subj: Dict[str, List[float]] = {}
    for s, d in zip(subjects, all_diffs):
        by_subj.setdefault(s, []).append(d)

    n = len(by_subj)
    N = len(all_diffs)
    n_rep = sum(1 for v in by_subj.values() if len(v) >= 2)
    if n_rep == 0:
        return RepeatedMeasuresBA(
            False, "no subject has replicate measurements", n, N)
    if n < 2:
        return RepeatedMeasuresBA(
            False, "need >=2 subjects for repeated-measures LoA", n, N)

    grand = sum(all_diffs) / N
    ssb = sum(len(v) * (mean(v) - grand) ** 2 for v in by_subj.values())
    ssw = sum(sum((d - mean(v)) ** 2 for d in v) for v in by_subj.values())
    df_b = n - 1
    df_w = N - n
    msb = ssb / df_b
    msw = ssw / df_w if df_w > 0 else float("nan")
    sum_mi2 = sum(len(v) ** 2 for v in by_subj.values())
    m0 = (N - sum_mi2 / N) / (n - 1)

    var_within = msw if msw == msw else 0.0
    var_between = (msb - var_within) / m0 if m0 > 0 else float("nan")
    clamped = False
    if var_between == var_between and var_between < 0.0:
        var_between = 0.0
        clamped = True

    total_var = var_between + var_within
    sd_diff = math.sqrt(total_var) if total_var >= 0 else float("nan")
    loa_lower = grand - _Z_LOA * sd_diff
    loa_upper = grand + _Z_LOA * sd_diff

    # CI on the LoA uses the SUBJECT count (not N) — approximate.
    se_loa = sd_diff * math.sqrt(1.0 / n + _Z_LOA ** 2 / (2.0 * (n - 1)))
    tcrit = t_ppf(1.0 - alpha / 2.0, n - 1)
    loa_lower_ci = (loa_lower - tcrit * se_loa, loa_lower + tcrit * se_loa)
    loa_upper_ci = (loa_upper - tcrit * se_loa, loa_upper + tcrit * se_loa)

    return RepeatedMeasuresBA(
        True, "", n, N, n_rep, grand, sd_diff, var_between, var_within, m0,
        loa_lower, loa_upper, loa_lower_ci, loa_upper_ci, clamped)


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
