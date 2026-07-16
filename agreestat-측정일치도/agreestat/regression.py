"""Method-comparison regression — Deming & Passing–Bablok (pure standard library).

Bland–Altman answers "how far apart are the two methods on average?" but a
method-comparison study (CLSI EP09) also needs a *regression* that accounts for
measurement error in **both** methods to separate two kinds of bias:

* **constant (systematic) bias** — the intercept differs from 0;
* **proportional bias** — the slope differs from 1.

Ordinary least squares is the wrong tool here: it assumes the x-method is
error-free, which biases the slope toward 0 (regression dilution). This module
implements the two standard error-in-both-variables estimators:

* :func:`deming` — Deming regression (Linnet 1990): the maximum-likelihood line
  under normal errors in both methods with a known variance ratio
  ``lam`` = Var(err_x)/Var(err_y) (the standard x-axis-over-y-axis convention;
  x is the reference, y the test). ``lam=1`` is orthogonal regression.
  Closed-form slope; jackknife CIs (df = n-2).
* :func:`passing_bablok` — Passing–Bablok regression (1983): distribution-free
  and robust to outliers. Slope = shifted median of all pairwise slopes; the CI
  is rank-based (Passing & Bablok 1983; MedCalc / analyse-it formulation).

Both regress the **test** method (y) on the **reference** method (x): a slope
whose CI includes 1 means *no proportional bias*, an intercept whose CI includes
0 means *no constant bias*. Optionally (``decision_point=Xc``) they also report
the **predicted systematic bias at a medical decision level** Xc,
``bias(Xc) = intercept + (slope-1)*Xc`` — the actionable EP09 number — with a
Deming jackknife CI. All quantiles come from :mod:`agreestat.special`; no
numpy/scipy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .agreement import mean
from .special import norm_ppf, t_ppf

__all__ = [
    "DemingResult",
    "PassingBablokResult",
    "deming",
    "passing_bablok",
]

_NAN = float("nan")

# Jackknife CI recomputes the fit n times (O(n^2)); beyond this the CI is
# skipped (method-comparison Deming datasets are small; a very large n also
# risks catastrophic cancellation in the leave-one-out centered sums).
_MAX_JACKKNIFE_N = 5000

# Passing–Bablok forms all n(n-1)/2 pairwise slopes (O(n^2) time & memory);
# beyond this it declines rather than risk minutes of runtime / GBs of RAM.
_MAX_PB_N = 3000


def _all_finite(*seqs: Sequence[float]) -> bool:
    return all(math.isfinite(v) for seq in seqs for v in seq)


@dataclass
class DemingResult:
    """Deming regression y = intercept + slope*x (x=reference, y=test)."""
    available: bool
    note: str = ""
    n: int = 0
    lam: float = 1.0
    slope: float = _NAN
    intercept: float = _NAN
    slope_ci: Tuple[float, float] = (_NAN, _NAN)
    intercept_ci: Tuple[float, float] = (_NAN, _NAN)
    proportional_bias: Optional[bool] = None  # slope CI excludes 1
    constant_bias: Optional[bool] = None      # intercept CI excludes 0
    method: str = "Deming"
    # predicted systematic bias at a medical decision level Xc (CLSI EP09):
    #   bias(Xc) = intercept + (slope-1)*Xc   [reference units]
    decision_point: Optional[float] = None
    bias_at_dp: float = _NAN
    bias_at_dp_ci: Tuple[float, float] = (_NAN, _NAN)


@dataclass
class PassingBablokResult:
    """Passing–Bablok regression y = intercept + slope*x (x=reference, y=test)."""
    available: bool
    note: str = ""
    n: int = 0
    n_slopes: int = 0
    k_offset: int = 0
    slope: float = _NAN
    intercept: float = _NAN
    slope_ci: Tuple[float, float] = (_NAN, _NAN)
    intercept_ci: Tuple[float, float] = (_NAN, _NAN)
    proportional_bias: Optional[bool] = None
    constant_bias: Optional[bool] = None
    method: str = "Passing-Bablok"
    decision_point: Optional[float] = None
    bias_at_dp: float = _NAN


# --------------------------------------------------------------------------
# Deming regression
# --------------------------------------------------------------------------
def _deming_slope(sxx: float, syy: float, sxy: float, lam: float) -> float:
    """Closed-form Deming slope (Linnet 1990) for centered sums sxx/syy/sxy.

    ``lam`` is the error-variance ratio Var(err_x)/Var(err_y) in the standard
    (x-axis method over y-axis method) convention. The canonical closed form is
    written in terms of delta = Var(err_y)/Var(err_x) = 1/lam:
        b = [(Syy - delta*Sxx) + sqrt((Syy - delta*Sxx)^2 + 4*delta*Sxy^2)] / (2*Sxy)
    so that lam->0 (x error-free) gives the OLS y-on-x slope Sxy/Sxx and
    lam->inf (y error-free) gives the inverse slope Syy/Sxy. lam=1 is orthogonal.
    """
    delta = 1.0 / lam
    t = syy - delta * sxx
    return (t + math.sqrt(t * t + 4.0 * delta * sxy * sxy)) / (2.0 * sxy)


def _centered_sums(x: Sequence[float], y: Sequence[float]
                   ) -> Tuple[float, float, float, float, float]:
    mx, my = mean(x), mean(y)
    sxx = sum((xi - mx) ** 2 for xi in x)
    syy = sum((yi - my) ** 2 for yi in y)
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    return mx, my, sxx, syy, sxy


def deming(x: Sequence[float], y: Sequence[float], lam: float = 1.0,
           alpha: float = 0.05,
           decision_point: Optional[float] = None) -> DemingResult:
    """Deming regression of test ``y`` on reference ``x``.

    ``lam`` is the assumed error-variance ratio Var(err_x)/Var(err_y) (x is the
    reference, y the test); ``lam=1`` (the default) is orthogonal regression,
    appropriate when the two methods have comparable measurement error. A larger
    ``lam`` attributes more error to x (the reference). Confidence intervals use
    Linnet's leave-one-out jackknife with a t(n-2) critical value.
    """
    x = [float(v) for v in x]
    y = [float(v) for v in y]
    n = len(x)
    if len(y) != n:
        raise ValueError("x and y must have the same length")
    if not (math.isfinite(lam) and lam > 0.0):
        raise ValueError("lam (variance ratio) must be a positive finite number")
    if n < 3:
        return DemingResult(False, "need >=3 pairs for Deming regression", n, lam)
    if not _all_finite(x, y):
        return DemingResult(
            False, "입력에 비유한(NaN/inf) 값이 있어 Deming을 건너뜁니다", n, lam)

    mx, my, sxx, syy, sxy = _centered_sums(x, y)
    if sxx <= 0.0 or syy <= 0.0:
        return DemingResult(
            False, "a method has no variance (constant) — slope undefined", n, lam)
    if sxy == 0.0:
        return DemingResult(
            False, "methods are uncorrelated (Sxy=0) — Deming slope undefined",
            n, lam)

    slope = _deming_slope(sxx, syy, sxy, lam)
    intercept = my - slope * mx
    # Guard against silent inf/nan from overflow on extreme (but sub-cap)
    # magnitudes: `t*t` and `4*lam*sxy*sxy` can overflow to inf without raising.
    if not (math.isfinite(slope) and math.isfinite(intercept)):
        return DemingResult(
            False, "수치 overflow로 Deming 추정이 유한하지 않습니다 "
            "(값의 크기가 지나치게 큽니다)", n, lam)

    slope_ci: Tuple[float, float] = (_NAN, _NAN)
    intercept_ci: Tuple[float, float] = (_NAN, _NAN)
    note = ""
    if n > _MAX_JACKKNIFE_N:
        note = (f"n={n} > {_MAX_JACKKNIFE_N}: jackknife CI 생략 "
                "(점추정만 보고).")
    else:
        slopes: List[float] = []
        intercepts: List[float] = []
        ok = True
        for i in range(n):
            xi = x[:i] + x[i + 1:]
            yi = y[:i] + y[i + 1:]
            mxi, myi, sxxi, syyi, sxyi = _centered_sums(xi, yi)
            if sxxi <= 0.0 or syyi <= 0.0 or sxyi == 0.0:
                ok = False
                break
            si = _deming_slope(sxxi, syyi, sxyi, lam)
            slopes.append(si)
            intercepts.append(myi - si * mxi)
        if ok:
            sbar = mean(slopes)
            ibar = mean(intercepts)
            fac = (n - 1) / n
            var_s = fac * sum((s - sbar) ** 2 for s in slopes)
            var_i = fac * sum((v - ibar) ** 2 for v in intercepts)
            se_s = math.sqrt(var_s)
            se_i = math.sqrt(var_i)
            tcrit = t_ppf(1.0 - alpha / 2.0, n - 2)
            slope_ci = (slope - tcrit * se_s, slope + tcrit * se_s)
            intercept_ci = (intercept - tcrit * se_i, intercept + tcrit * se_i)
        else:
            note = "leave-one-out에서 퇴화(분산 0)해 jackknife CI를 생략했습니다."

    # Predicted systematic bias at a medical decision level Xc (EP09): the
    # jackknife pseudo-fits give a CI directly (it captures slope/intercept
    # covariance, which a naive combination of the two marginal CIs would not).
    bias_dp = _NAN
    bias_dp_ci: Tuple[float, float] = (_NAN, _NAN)
    if decision_point is not None and math.isfinite(decision_point):
        xc = float(decision_point)
        bias_dp = intercept + (slope - 1.0) * xc
        if not math.isfinite(bias_dp):
            bias_dp = _NAN
        elif slope_ci[0] == slope_ci[0]:  # jackknife ran (CI not NaN)
            # A huge Xc can overflow the squared-deviation sum (**2 raises
            # OverflowError). Skip only the CI in that case — keep the point
            # estimate and the rest of the report intact.
            try:
                biases = [ic + (sl - 1.0) * xc
                          for sl, ic in zip(slopes, intercepts)]
                bbar = mean(biases)
                var_b = (n - 1) / n * sum((bv - bbar) ** 2 for bv in biases)
                se_b = math.sqrt(var_b)
                tcrit = t_ppf(1.0 - alpha / 2.0, n - 2)
                ci = (bias_dp - tcrit * se_b, bias_dp + tcrit * se_b)
                if math.isfinite(ci[0]) and math.isfinite(ci[1]):
                    bias_dp_ci = ci
            except OverflowError:
                pass

    prop = _excludes(slope_ci, 1.0)
    const = _excludes(intercept_ci, 0.0)
    return DemingResult(True, note, n, lam, slope, intercept, slope_ci,
                        intercept_ci, prop, const,
                        decision_point=decision_point, bias_at_dp=bias_dp,
                        bias_at_dp_ci=bias_dp_ci)


# --------------------------------------------------------------------------
# Passing–Bablok regression
# --------------------------------------------------------------------------
def _median(vals: Sequence[float]) -> float:
    s = sorted(vals)
    m = len(s)
    if m == 0:
        return _NAN
    if m % 2 == 1:
        return s[m // 2]
    return 0.5 * (s[m // 2 - 1] + s[m // 2])


def passing_bablok(x: Sequence[float], y: Sequence[float],
                   alpha: float = 0.05,
                   decision_point: Optional[float] = None) -> PassingBablokResult:
    """Passing–Bablok regression of test ``y`` on reference ``x``.

    Distribution-free and robust to outliers. The slope is the shifted median of
    the n(n-1)/2 pairwise slopes; the rank-based confidence interval follows
    Passing & Bablok (1983). Slopes of exactly -1 are excluded (per the method);
    the offset ``K`` counts pairwise slopes < -1.
    """
    x = [float(v) for v in x]
    y = [float(v) for v in y]
    n = len(x)
    if len(y) != n:
        raise ValueError("x and y must have the same length")
    if n < 3:
        return PassingBablokResult(False, "need >=3 pairs for Passing–Bablok", n)
    if not _all_finite(x, y):
        return PassingBablokResult(
            False, "입력에 비유한(NaN/inf) 값이 있어 Passing–Bablok을 건너뜁니다", n)
    if n > _MAX_PB_N:
        return PassingBablokResult(
            False, f"n={n} > {_MAX_PB_N}: Passing–Bablok은 O(n²) 쌍별 기울기를 "
            "만들어 너무 큽니다 — Deming을 쓰거나 표본을 요약/부분추출하세요", n)

    slopes: List[float] = []
    k = 0
    for i in range(n):
        xi, yi = x[i], y[i]
        for j in range(i + 1, n):
            dx = xi - x[j]
            dy = yi - y[j]
            if dx == 0.0 and dy == 0.0:
                continue  # identical points contribute no direction
            if dx == 0.0:
                s = math.inf  # vertical pair (tie in x); rare for continuous data
            else:
                s = dy / dx
            if s == -1.0:
                continue  # excluded per Passing & Bablok (1983)
            slopes.append(s)
            if s < -1.0:
                k += 1
    big_n = len(slopes)
    if big_n == 0:
        return PassingBablokResult(
            False, "사용 가능한 쌍별 기울기가 없습니다 (모든 기울기가 −1로 "
            "제외되거나 동일점뿐 — 예: 완전한 음의 대칭 데이터)", n)
    slopes.sort()

    # Shifted median (offset K corrects the slope-space wrap around -1).
    if big_n % 2 == 1:
        r = (big_n + 1) // 2 + k
        slope = _rank(slopes, r)
    else:
        r1 = big_n // 2 + k
        r2 = big_n // 2 + 1 + k
        lo = _rank(slopes, r1)
        hi = _rank(slopes, r2)
        slope = 0.5 * (lo + hi) if (math.isfinite(lo) and math.isfinite(hi)) else _NAN
    if slope != slope or not math.isfinite(slope):
        return PassingBablokResult(
            False, "shifted-median slope not finite (degenerate/negative "
            "relationship — Passing–Bablok assumes a positive association)",
            n, big_n, k)

    intercept = _median([y[i] - slope * x[i] for i in range(n)])

    # Rank-based CI (Passing & Bablok 1983). n = sample size; big_n = #slopes.
    z = norm_ppf(1.0 - alpha / 2.0)
    c_gamma = z * math.sqrt(n * (n - 1) * (2 * n + 5) / 18.0)
    m1 = int(math.floor((big_n - c_gamma) / 2.0 + 0.5))  # round to nearest int
    m2 = big_n - m1 + 1
    lo_rank = m1 + k
    hi_rank = m2 + k
    slope_ci: Tuple[float, float] = (_NAN, _NAN)
    intercept_ci: Tuple[float, float] = (_NAN, _NAN)
    note = ""
    if 1 <= lo_rank <= big_n and 1 <= hi_rank <= big_n:
        s_lo = _rank(slopes, lo_rank)
        s_hi = _rank(slopes, hi_rank)
        if math.isfinite(s_lo) and math.isfinite(s_hi):
            slope_ci = (s_lo, s_hi)
            intercept_ci = (
                _median([y[i] - s_hi * x[i] for i in range(n)]),
                _median([y[i] - s_lo * x[i] for i in range(n)]),
            )
        else:
            note = "슬로프 CI 경계가 무한대(수직쌍) — CI를 생략했습니다."
    else:
        note = (f"표본이 작아 rank 기반 CI 경계가 범위를 벗어납니다 "
                f"(n={n}). CI를 신뢰할 수 없어 생략했습니다.")

    prop = _excludes(slope_ci, 1.0)
    const = _excludes(intercept_ci, 0.0)
    bias_dp = _NAN
    if decision_point is not None and math.isfinite(decision_point):
        bias_dp = intercept + (slope - 1.0) * float(decision_point)
    return PassingBablokResult(True, note, n, big_n, k, slope, intercept,
                               slope_ci, intercept_ci, prop, const,
                               decision_point=decision_point, bias_at_dp=bias_dp)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _rank(sorted_vals: Sequence[float], rank1: int) -> float:
    """1-indexed rank into a sorted list; NaN if out of range."""
    if rank1 < 1 or rank1 > len(sorted_vals):
        return _NAN
    return sorted_vals[rank1 - 1]


def _excludes(ci: Tuple[float, float], value: float) -> Optional[bool]:
    """True if the (finite) CI excludes ``value``; None if the CI is NaN."""
    lo, hi = ci
    if lo != lo or hi != hi:  # NaN
        return None
    return not (lo <= value <= hi)
