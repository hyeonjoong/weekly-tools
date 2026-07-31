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
- Two independent proportions (binary endpoint, e.g. response rate): the standard
  pooled/unpooled normal approximation
  ``N = (z_a sqrt(p̄q̄/(w1 w2)) + z_b sqrt(p1q1/w1 + p2q2/w2))^2 / (p1-p2)^2``.
  For 30% vs 50% at alpha=.05/power=.80 this gives 186 total = 93 per group,
  matching the classic Fleiss table without continuity correction.
- Time-to-event (log-rank / Cox): Schoenfeld's formula
  ``events = (z_a + z_b)^2 / (w1 w2 (ln HR)^2)``, converted to subjects by the
  expected event probability. HR=0.70 at alpha=.05/power=.80 needs 247 events —
  the number every oncology protocol quotes.

Note: the correlation and two-group forms are normal approximations that can
land ~1 subject/group below exact non-central-t tools (e.g. G*Power gives
64/group for d=0.5). These are *planning* estimates only; the report says so and
recommends confirming final power in a dedicated tool. Because the danger for a
feasibility check is *under*-stating the required N, we always round up (ceil)
and pair every number with conservative (small-to-medium) effect-size priors.

Alpha / power are accepted as *any* value in (0, 1): the normal quantiles come
from an inverse-normal-CDF implementation (Acklam's rational approximation plus
one Halley refinement against ``math.erfc``, applied to the *reflected* problem
above the median so the residual never cancels), which round-trips through the
CDF to ~1e-15 and reproduces the classic table constants at 0.05/0.80.
This matters in practice because multiplicity-corrected planning needs values
like alpha=0.05/7=0.00714 that no hard-coded table can supply.
"""
from __future__ import annotations

import math

# Reference standard-normal quantiles for the most common settings. These are no
# longer used for lookup (see :func:`norm_ppf`) but are kept as the transparent,
# hand-checkable constants that the implementation is verified against.
_Z_ALPHA_TWO_SIDED = {0.05: 1.959963985, 0.01: 2.575829304, 0.10: 1.644853627}
_Z_POWER = {0.80: 0.841621234, 0.90: 1.281551566, 0.95: 1.644853627}

# Acklam's rational approximation to the inverse standard-normal CDF.
_PPF_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
          1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
_PPF_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
          6.680131188771972e+01, -1.328068155288572e+01)
_PPF_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
          -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
_PPF_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
          3.754408661907416e+00)
_PPF_PLOW = 0.02425

# Largest sample size we will report. Beyond this the 'study' is fiction, and
# huge N feeds non-centrality parameters that make the F machinery crawl.
_MAX_N = 1_000_000
# Non-centrality beyond which the mixture is both unsummable and pointless.
_MAX_LAMBDA = 1e9


def norm_cdf(x: float) -> float:
    """Standard normal CDF via ``math.erfc`` (full double precision)."""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF for ``0 < p < 1``.

    Acklam's rational approximation (|error| < 1.15e-9) refined by one Halley
    step against :func:`norm_cdf`, which lifts accuracy to ~1e-15 (measured as
    ``norm_cdf(norm_ppf(p)) == p``) — enough that ``norm_ppf(0.975)`` reproduces
    1.959963985 and ``norm_ppf(0.80)`` 0.841621234, the constants these formulas
    are classically tabulated with. Note that for p given as a double very close
    to 1, the *input* already carries a representation error that no algorithm
    can recover; that is why accuracy is stated as a CDF round-trip.
    """
    p = float(p)
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be strictly between 0 and 1 (got {p!r})")
    if p < _PPF_PLOW:
        q = math.sqrt(-2.0 * math.log(p))
        x = ((((_PPF_C[0] * q + _PPF_C[1]) * q + _PPF_C[2]) * q + _PPF_C[3]) * q
             + _PPF_C[4]) * q + _PPF_C[5]
        x /= (((_PPF_D[0] * q + _PPF_D[1]) * q + _PPF_D[2]) * q + _PPF_D[3]) * q + 1.0
    elif p <= 1.0 - _PPF_PLOW:
        q = p - 0.5
        r = q * q
        x = (((((_PPF_A[0] * r + _PPF_A[1]) * r + _PPF_A[2]) * r + _PPF_A[3]) * r
              + _PPF_A[4]) * r + _PPF_A[5]) * q
        x /= ((((_PPF_B[0] * r + _PPF_B[1]) * r + _PPF_B[2]) * r + _PPF_B[3]) * r
              + _PPF_B[4]) * r + 1.0
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((_PPF_C[0] * q + _PPF_C[1]) * q + _PPF_C[2]) * q + _PPF_C[3]) * q
               + _PPF_C[4]) * q + _PPF_C[5])
        x /= (((_PPF_D[0] * q + _PPF_D[1]) * q + _PPF_D[2]) * q + _PPF_D[3]) * q + 1.0
    # One Halley refinement. `norm_cdf(x) - p` cancels catastrophically when both
    # are ~1, so above the median we refine the REFLECTED problem (-x against
    # 1-p) where the residual is computed against a small number and keeps its
    # significant digits. Without this the upper tail — the only tail _z_alpha
    # ever asks for — kept the raw Acklam error (~1e-9) instead of reaching the
    # ~1e-15 this function advertises.
    if p > 0.5:
        return -_halley(-x, 1.0 - p)
    return _halley(x, p)


def _halley(x: float, p: float) -> float:
    """One Halley step of ``norm_cdf(x) = p`` (caller keeps p in the lower tail)."""
    err = norm_cdf(x) - p
    # exp(x^2/2) overflows far out in the tail; there the Acklam value already
    # carries all the precision a double can hold, so leave it alone.
    half_sq = x * x / 2.0
    if half_sq > 700.0:
        return x
    u = err * math.exp(half_sq) * math.sqrt(2.0 * math.pi)
    denom = 1.0 + x * u / 2.0
    if denom != 0.0 and math.isfinite(u):
        x -= u / denom
    return x


_TOO_SMALL = (
    "가정 효과크기({kind}={value!r})가 너무 작아 표본수를 계산할 수 없습니다 "
    "— --effect-scale 값이나 템플릿의 effect 크기를 확인하세요."
)
_TOO_LARGE = (
    "가정 효과크기({kind}={value!r})가 비현실적으로 큽니다 "
    "— --effect-scale 값이나 템플릿의 effect 크기를 확인하세요."
)


def _sq(value: float, kind: str) -> float:
    """``value**2`` for an effect size, or a clean error at either extreme.

    Squaring is where a mistyped ``--effect-scale`` exponent bites: 1e-300
    underflows to exactly 0.0 (ZeroDivisionError downstream) and 1e200 overflows
    (``OverflowError: Result too large``). Both used to surface as tracebacks.
    """
    try:
        squared = value ** 2
    except OverflowError:
        raise ValueError(_TOO_LARGE.format(kind=kind, value=value)) from None
    if squared <= 0.0:
        raise ValueError(_TOO_SMALL.format(kind=kind, value=value))
    if not math.isfinite(squared):
        raise ValueError(_TOO_LARGE.format(kind=kind, value=value))
    return squared


def _checked_n(n: float) -> int:
    """Ceil a computed N, refusing values no study could ever run.

    A microscopic effect size produces an astronomically large N; returning a
    300-digit integer wrecks the report table and feeds absurd non-centrality
    parameters into the F machinery downstream. Fail cleanly instead.
    """
    if not math.isfinite(n) or n > _MAX_N:
        raise ValueError(
            f"필요 표본수가 {_MAX_N:,}명을 넘습니다 — 가정 효과크기가 "
            "비현실적으로 작지 않은지 확인하세요."
        )
    # Snap before ceiling, for the same reason rows_to_subjects does: an exact
    # integer result (e.g. events/event_rate = 40.00000000000001) otherwise
    # ceilings to 41, which then breaks the MDES -> required-N round-trip these
    # functions promise. Only values within ~1 ulp of an integer are affected.
    return math.ceil(_snap(n))


def _z_alpha(alpha: float, sided: int = 2) -> float:
    """Critical z for a ``sided``-tailed test at level ``alpha``."""
    alpha = float(alpha)
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be strictly between 0 and 1 (got {alpha!r})")
    if sided not in (1, 2):
        raise ValueError("sided must be 1 (one-tailed) or 2 (two-tailed)")
    return norm_ppf(1.0 - alpha / sided)


def _z_power(power: float) -> float:
    power = float(power)
    if not 0.0 < power < 1.0:
        raise ValueError(f"power must be strictly between 0 and 1 (got {power!r})")
    return norm_ppf(power)


def _z_pair(alpha: float, power: float, sided: int = 2):
    """``(z_alpha, z_power)`` for a *meaningful* test, or raise.

    Every closed form here is built on ``z_alpha + z_power``. Below
    ``power == alpha/sided`` that sum turns negative, and because the sizing
    formulas square it the required N starts *growing* again as the target power
    falls, while the MDES formulas (which do not square) return a **negative**
    minimum detectable effect. Both are nonsense: a test's power can never fall
    below its own type-I error rate, so any target at or under alpha is already
    met by an effect of zero. Reject that region explicitly instead of printing
    ``d≥-0.08``.
    """
    za = _z_alpha(alpha, sided)
    zb = _z_power(power)
    if za + zb <= 0.0:
        raise ValueError(
            f"power={power!r} is at or below the test's own false-positive rate "
            f"(alpha/{sided}={float(alpha) / sided:.4g}); any effect is trivially "
            "'detectable' there. Choose power > alpha/sided."
        )
    return za, zb


def n_for_correlation(r: float, alpha: float = 0.05, power: float = 0.80,
                      sided: int = 2) -> int:
    """Total N to detect a Pearson correlation of magnitude ``r``.

    Uses the Fisher z approximation::

        C = 0.5 * ln((1+|r|)/(1-|r|))
        N = ((z_{1-a/s} + z_{1-b}) / C)^2 + 3

    with ``s = sided`` (2 by default; ``sided=1`` for a directional hypothesis).
    """
    r = abs(float(r))
    if not 0 < r < 1:
        raise ValueError("r must be strictly between 0 and 1")
    za, zb = _z_pair(alpha, power, sided)
    c = 0.5 * math.log((1 + r) / (1 - r))
    # atanh(r) underflows to exactly 0.0 once r <~ 1.1e-16 (because 1.0+r == 1.0
    # in binary), which used to raise a bare ZeroDivisionError. Reachable via a
    # mistyped --effect-scale exponent or a template with r=1e-300.
    if c <= 0.0:
        raise ValueError(_TOO_SMALL.format(kind="r", value=r))
    n = ((za + zb) / c) ** 2 + 3
    return _checked_n(n)


def n_per_group_two_means(d: float, alpha: float = 0.05, power: float = 0.80,
                          sided: int = 2) -> int:
    """Per-group N to detect a standardized mean difference ``d`` (Cohen's d).

    Normal approximation::

        n_per_group = 2 (z_{1-a/s} + z_{1-b})^2 / d^2
    """
    d = abs(float(d))
    if d <= 0:
        raise ValueError("d must be > 0")
    za, zb = _z_pair(alpha, power, sided)
    n = 2 * (za + zb) ** 2 / _sq(d, "d")
    return _checked_n(n)


def n_for_paired(d: float, alpha: float = 0.05, power: float = 0.80,
                 sided: int = 2) -> int:
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
    za, zb = _z_pair(alpha, power, sided)
    n = (za + zb) ** 2 / _sq(d, "d") + 1
    return _checked_n(n)


def n_total_two_group(
    d: float, alpha: float = 0.05, power: float = 0.80, allocation: float = 0.5,
    sided: int = 2,
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
    za, zb = _z_pair(alpha, power, sided)
    denom = allocation * (1.0 - allocation) * _sq(d, "d")
    if denom <= 0.0:
        raise ValueError(_TOO_SMALL.format(kind="d/allocation", value=d))
    return _checked_n((za + zb) ** 2 / denom)


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
    if not math.isfinite(f2) or f2 > 1e6:
        raise ValueError(_TOO_LARGE.format(kind="f2", value=f2))
    if k_tested < 1 or k_control < 0:
        raise ValueError("k_tested must be >= 1 and k_control >= 0")
    _z_pair(alpha, power)
    k_full = k_tested + k_control
    return _smallest_n_for_power(
        lambda n: _f_power(f2, n, k_tested, k_full, alpha), k_full + 2, power
    )


# --- Binary endpoints: two independent proportions ---------------------------
#
# The single most common clinical endpoint after "mean difference": response
# rate, remission rate, AE incidence, seroconversion. Sizing it as a Cohen's d
# (what a tool without this family forces you to do) is wrong in both directions
# depending on where the rates sit, so it gets its own closed form.


def _check_proportion(p: float, name: str) -> float:
    p = float(p)
    if p != p or not 0.0 < p < 1.0:
        raise ValueError(f"{name} must be strictly between 0 and 1 (got {p!r})")
    return p


def _prop_terms(p1: float, p2: float, allocation: float):
    """``(pooled_sd_term, unpooled_sd_term)`` for the two-proportion forms.

    Both are the per-``sqrt(N)`` standard errors of the risk difference: the
    pooled one holds under H0 (which is what the test statistic uses), the
    unpooled one under H1 (which is what the alternative distribution has).
    Using both — rather than the pooled term twice — is what makes this agree
    with published tables instead of running ~5% light.
    """
    w1, w2 = allocation, 1.0 - allocation
    pbar = w1 * p1 + w2 * p2
    pooled = math.sqrt(pbar * (1.0 - pbar) / (w1 * w2))
    unpooled = math.sqrt(p1 * (1.0 - p1) / w1 + p2 * (1.0 - p2) / w2)
    return pooled, unpooled


def n_for_two_proportions(
    p1: float, p2: float, alpha: float = 0.05, power: float = 0.80,
    allocation: float = 0.5, sided: int = 2,
) -> int:
    """Total N to detect a difference between response rates ``p1`` and ``p2``.

    ``allocation`` is the fraction of the sample in group 1. No continuity
    correction is applied (it would add ~4 subjects/group at these rates and is
    considered over-conservative for the score test actually used in practice);
    the report says the numbers are planning approximations.
    """
    p1 = _check_proportion(p1, "p1")
    p2 = _check_proportion(p2, "p2")
    if p1 == p2:
        raise ValueError(_TOO_SMALL.format(kind="p1-p2", value=0.0))
    if not 0.0 < allocation < 1.0:
        raise ValueError("allocation must satisfy 0 < allocation < 1")
    za, zb = _z_pair(alpha, power, sided)
    pooled, unpooled = _prop_terms(p1, p2, allocation)
    delta2 = _sq(p1 - p2, "p1-p2")
    return _checked_n((za * pooled + zb * unpooled) ** 2 / delta2)


def power_for_two_proportions(
    p1: float, p2: float, n_total: int, alpha: float = 0.05,
    allocation: float = 0.5, sided: int = 2,
) -> float:
    """Power of the two-proportion test at ``n_total`` (inverse of the above)."""
    p1 = _check_proportion(p1, "p1")
    p2 = _check_proportion(p2, "p2")
    if p1 == p2:
        raise ValueError("p1 and p2 must differ")
    if not 0.0 < allocation < 1.0:
        raise ValueError("allocation must satisfy 0 < allocation < 1")
    n_total = int(n_total)
    if n_total < 4:
        raise ValueError("n_total must be >= 4 (>= 2 per group)")
    za = _z_alpha(alpha, sided)
    pooled, unpooled = _prop_terms(p1, p2, allocation)
    delta = abs(p1 - p2) * math.sqrt(n_total)
    pw = norm_cdf((delta - za * pooled) / unpooled)
    if sided == 2:
        pw += norm_cdf((-delta - za * pooled) / unpooled)
    return pw


def mdes_two_proportions(
    n_total: int, p1: float, alpha: float = 0.05, power: float = 0.80,
    allocation: float = 0.5, sided: int = 2, direction: int = 1,
) -> float:
    """Smallest detectable ``p2`` given the control rate ``p1`` and ``n_total``.

    A risk difference has no single scale-free magnitude — its detectability
    depends on where the baseline rate sits (0.05 vs 0.10 is far harder than
    0.45 vs 0.50 at the same N). So the MDES is expressed as the treatment rate
    you could distinguish from the *observed* control rate, found by bisecting
    the exact power curve. ``direction`` +1 searches upward from ``p1``, -1
    downward. Raises when even ``p2 -> 0/1`` cannot reach the target power.
    """
    p1 = _check_proportion(p1, "p1")
    n_total = int(n_total)
    if n_total < 4:
        raise ValueError("n_total must be >= 4 (>= 2 per group)")
    if direction not in (1, -1):
        raise ValueError("direction must be +1 or -1")
    _z_pair(alpha, power, sided)
    edge = 1.0 - 1e-9 if direction > 0 else 1e-9
    if power_for_two_proportions(p1, edge, n_total, alpha, allocation, sided) < power:
        raise ValueError("no detectable p2 at this N")
    lo, hi = p1, edge  # lo: not detectable, hi: detectable
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if power_for_two_proportions(
            p1, mid, n_total, alpha, allocation, sided
        ) < power:
            lo = mid
        else:
            hi = mid
        if abs(hi - lo) <= 1e-9:
            break
    return hi


# --- Time-to-event endpoints: log-rank / Cox ---------------------------------
#
# Survival is the primary endpoint of most oncology and cardiovascular trials,
# and it is the one family where "how many subjects?" is the *second* question —
# the design is driven by the number of EVENTS, with enrolment following from the
# expected event probability. Both numbers are reported.


def events_for_survival(
    hr: float, alpha: float = 0.05, power: float = 0.80,
    allocation: float = 0.5, sided: int = 2,
) -> int:
    """Number of *events* needed to detect a hazard ratio ``hr`` (Schoenfeld).

    ``events = (z_a + z_b)^2 / (w1 w2 (ln HR)^2)`` — independent of the sample
    size, the accrual pattern and the baseline hazard, which is exactly why the
    log-rank literature quotes events rather than subjects.
    """
    hr = float(hr)
    if hr != hr or hr <= 0.0 or not math.isfinite(hr):
        raise ValueError("hr must be a finite number > 0")
    if hr == 1.0:
        raise ValueError(_TOO_SMALL.format(kind="hr", value=hr))
    if not 0.0 < allocation < 1.0:
        raise ValueError("allocation must satisfy 0 < allocation < 1")
    za, zb = _z_pair(alpha, power, sided)
    loghr2 = _sq(math.log(hr), "ln(hr)")
    return _checked_n((za + zb) ** 2 / (allocation * (1.0 - allocation) * loghr2))


def n_for_survival(
    hr: float, event_rate: float, alpha: float = 0.05, power: float = 0.80,
    allocation: float = 0.5, sided: int = 2,
) -> int:
    """Subjects to enrol so that the required number of events accrues.

    ``event_rate`` is the probability that a randomised subject is *observed* to
    have the event during follow-up (i.e. after censoring), pooled over arms.
    ``N = ceil(events / event_rate)``.
    """
    event_rate = _check_proportion_inclusive(event_rate, "event_rate")
    events = events_for_survival(hr, alpha, power, allocation, sided)
    return _checked_n(events / event_rate)


def _check_proportion_inclusive(p: float, name: str) -> float:
    """0 < p <= 1 — an event rate of exactly 1 (no censoring) is legitimate."""
    p = float(p)
    if p != p or not 0.0 < p <= 1.0:
        raise ValueError(f"{name} must satisfy 0 < {name} <= 1 (got {p!r})")
    return p


def expected_events(n_total, event_rate: float):
    """Events expected from ``n_total`` subjects, floored (``None`` if n unknown).

    Floored deliberately: over-counting events is the direction that overstates
    power, and a fractional event is not a thing a trial can observe.
    """
    if n_total is None:
        return None
    event_rate = _check_proportion_inclusive(event_rate, "event_rate")
    return math.floor(_snap(int(n_total) * event_rate))


def power_for_survival(
    hr: float, n_total: int, event_rate: float, alpha: float = 0.05,
    allocation: float = 0.5, sided: int = 2,
) -> float:
    """Log-rank power from the events ``n_total`` subjects are expected to yield."""
    hr = float(hr)
    if hr != hr or hr <= 0.0 or not math.isfinite(hr):
        raise ValueError("hr must be a finite number > 0")
    if hr == 1.0:
        raise ValueError("hr must differ from 1")
    if not 0.0 < allocation < 1.0:
        raise ValueError("allocation must satisfy 0 < allocation < 1")
    events = expected_events(n_total, event_rate)
    if events is None or events < 1:
        raise ValueError("n_total x event_rate must yield at least one event")
    za = _z_alpha(alpha, sided)
    delta = abs(math.log(hr)) * math.sqrt(
        events * allocation * (1.0 - allocation)
    )
    pw = norm_cdf(delta - za)
    if sided == 2:
        pw += norm_cdf(-delta - za)
    return pw


def mdes_survival(
    n_total: int, event_rate: float, alpha: float = 0.05, power: float = 0.80,
    allocation: float = 0.5, sided: int = 2,
) -> float:
    """Smallest detectable hazard ratio, returned on the ``HR > 1`` side.

    Inverts Schoenfeld: ``|ln HR| = (z_a + z_b) / sqrt(E w1 w2)``. A hazard ratio
    is symmetric on the log scale, so the value ``h`` returned means "HR >= h, or
    equivalently HR <= 1/h, is detectable"; the report prints both.
    """
    events = expected_events(n_total, event_rate)
    if events is None or events < 1:
        raise ValueError("n_total x event_rate must yield at least one event")
    if not 0.0 < allocation < 1.0:
        raise ValueError("allocation must satisfy 0 < allocation < 1")
    za, zb = _z_pair(alpha, power, sided)
    return math.exp((za + zb) / math.sqrt(events * allocation * (1.0 - allocation)))


def scale_effect(effect: dict, factor: float) -> dict:
    """Return a copy of ``effect`` with its magnitude scaled by ``factor``.

    Used for effect-size sensitivity: ``factor<1`` assumes a *smaller* true
    effect (→ larger required N), ``factor>1`` a larger one. Correlation r is
    capped below 1. Exploratory effects are returned unchanged.

    The two clinical families scale on their natural metric, not on the raw
    number: a *risk difference* is scaled around the control rate (and clipped
    inside (0,1), so halving a 0.30→0.90 contrast gives 0.30→0.60 rather than an
    impossible rate), and a *hazard ratio* is scaled on the log scale, so
    factor=0.5 turns HR=0.50 into HR=0.71 — half the effect, not "HR=0.25".
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
    elif etype == "two_proportion":
        p1 = float(e["p1"])
        p2 = p1 + (float(e["p2"]) - p1) * factor
        e["p2"] = min(max(p2, 1e-9), 1.0 - 1e-9)
    elif etype == "survival":
        hr = float(e["hr"])
        if hr > 0.0 and math.isfinite(hr):
            # Clamp the EXPONENT, not just the input: math.exp raises
            # OverflowError past ~709 (an uncaught traceback, since the CLI
            # catches ValueError), and underflows to exactly 0.0 below ~-745,
            # which later surfaced as the misleading "hr must be > 0" — about an
            # hr the user never typed. Reachable with a large --effect-scale on
            # any HR>1 template, and even at the default scale via the 1.5
            # sensitivity factor when a pack declares an absurd hr. Both ends are
            # already far past "no study could distinguish this".
            e["hr"] = math.exp(max(-700.0, min(700.0, math.log(hr) * factor)))
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
    if etype == "two_proportion":
        # The risk difference — the quantity the sizing formula actually squares.
        return abs(float(effect["p2"]) - float(effect["p1"]))
    if etype == "survival":
        return effect["hr"]
    return None


def required_total_n(effect: dict, alpha: float = 0.05, power: float = 0.80,
                     sided: int = 2):
    """Required *total* N for an effect spec, or ``None`` when not applicable.

    ``effect`` is one of::

        {"type": "correlation", "r": 0.3}
        {"type": "two_group", "d": 0.5, "allocation": 0.5}   # total (opt. split)
        {"type": "paired", "d": 0.5}           # within-subject; total = n pairs
        {"type": "regression", "f2": 0.15, "k": 3}           # overall-R^2 test
        {"type": "regression_change", "f2": 0.15,            # incremental-R^2
         "k_tested": 2, "k_control": 1}
        {"type": "two_proportion", "p1": 0.30, "p2": 0.50,   # binary endpoint
         "allocation": 0.5}
        {"type": "survival", "hr": 0.70, "event_rate": 0.6,  # time-to-event
         "allocation": 0.5}
        {"type": "exploratory"}                # no closed-form target -> None

    ``sided`` (1 or 2) applies to the z-based tests only. The regression forms
    are F-tests, which are intrinsically one-tailed *on F* while remaining
    two-sided on each coefficient, so there is no meaningful one-sided variant
    and ``sided`` is ignored for them (documented in the report).
    """
    etype = effect.get("type")
    if etype == "correlation":
        return n_for_correlation(effect["r"], alpha, power, sided)
    if etype == "two_group":
        alloc = effect.get("allocation", 0.5)
        if alloc == 0.5:
            # Preserve the exact 2*per-group value (round each group up).
            return 2 * n_per_group_two_means(effect["d"], alpha, power, sided)
        return n_total_two_group(effect["d"], alpha, power, alloc, sided)
    if etype == "paired":
        return n_for_paired(effect["d"], alpha, power, sided)
    if etype == "regression":
        return n_for_regression(effect["f2"], effect.get("k", 1), alpha, power)
    if etype == "regression_change":
        return n_for_regression_change(
            effect["f2"], effect["k_tested"], effect["k_control"], alpha, power
        )
    if etype == "two_proportion":
        return n_for_two_proportions(
            effect["p1"], effect["p2"], alpha, power,
            effect.get("allocation", 0.5), sided,
        )
    if etype == "survival":
        return n_for_survival(
            effect["hr"], effect["event_rate"], alpha, power,
            effect.get("allocation", 0.5), sided,
        )
    if etype == "exploratory":
        return None
    raise ValueError(f"Unknown effect type: {etype!r}")


def required_events(effect: dict, alpha: float = 0.05, power: float = 0.80,
                    sided: int = 2):
    """Events required for a time-to-event spec, or ``None`` for other families.

    Reported alongside the subject count because a survival trial is powered on
    events: enrolling the N below but following it for half as long leaves the
    study underpowered even though the enrolment target was met.
    """
    if effect.get("type") != "survival":
        return None
    return events_for_survival(
        effect["hr"], alpha, power, effect.get("allocation", 0.5), sided
    )


def _f_power(f2: float, n: int, k_num: int, k_full: int, alpha: float) -> float:
    """Non-central F power at sample ``n`` (numerator df ``k_num``).

    ``k_full`` is the number of predictors in the full model, which fixes the
    denominator df; for an overall-R^2 test the two coincide.
    """
    d2 = n - k_full - 1
    f_crit = _f_quantile(1.0 - alpha, k_num, d2)
    return 1.0 - _ncf_cdf(f_crit, k_num, d2, f2 * n)


def _smallest_n_for_power(power_at, n_min: int, target: float) -> int:
    """Smallest integer ``n >= n_min`` with ``power_at(n) >= target``.

    Power is increasing in n, so we bracket geometrically and then bisect. The
    previous implementation stepped n by 1 from ``n_min``, which is fine at
    N~80 but takes minutes once a small effect pushes N into the tens of
    thousands (``--effect-scale 1e-3`` on an f2=0.15 template needs N~73,000 —
    73 s per call, and evaluate() makes four calls per template). Bracketing
    turns that into ~30 evaluations.
    """
    if power_at(n_min) >= target:
        return n_min
    lo, hi = n_min, n_min + 1
    while hi <= _MAX_N:
        if power_at(hi) >= target:
            break
        lo = hi
        hi = min(hi * 2, _MAX_N + 1)
    else:  # pragma: no cover - defensive
        hi = _MAX_N + 1
    if hi > _MAX_N:
        raise ValueError(
            f"Required N exceeds {_MAX_N:,}; check the effect size (f2)."
        )
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if power_at(mid) >= target:
            hi = mid
        else:
            lo = mid
    return hi


def observation_level(effect: dict) -> bool:
    """True when the effect spec is sized in *rows of analysis*, not subjects.

    Correlation and regression targets count independent observations, so if a
    study contributes several observations per subject the subject requirement
    shrinks (see :func:`rows_to_subjects`). ``paired``/``two_group`` targets are
    already expressed in subjects and must not be rescaled.
    """
    return effect.get("type") in ("correlation", "regression", "regression_change")


def design_effect(repeats: int, icc: float) -> float:
    """Design effect (variance inflation) for ``repeats`` correlated obs/subject.

    The standard multilevel/cluster formula ``DE = 1 + (m - 1) * ICC``. With
    ICC=0 the observations are independent (DE=1, m observations count fully);
    with ICC=1 they carry no new information (DE=m, so m observations count as
    one). Nothing here is specific to paperforge — it is the same correction
    used for cluster-randomised trials.
    """
    repeats = int(repeats)
    icc = float(icc)
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    if repeats > _MAX_N:
        raise ValueError(
            f"repeats must be <= {_MAX_N:,} (got {repeats})"
        )
    if not 0.0 <= icc <= 1.0:
        raise ValueError("icc must satisfy 0 <= icc <= 1")
    return 1.0 + (repeats - 1) * icc


# Exact-integer results (e.g. 11*3/1.1) land a few ulps off in binary floating
# point, so a bare floor/ceil can move the answer by a whole subject. Snap to a
# nearby integer first: without this, 11 subjects x 3 repeats at ICC=0.1 yields
# 29 rows instead of 30, and the report prints "충분 가능" beside a power of 0.79.
_ROUND_TOL = 1e-9


def _snap(x: float) -> float:
    nearest = round(x)
    if abs(x - nearest) <= _ROUND_TOL * max(1.0, abs(x)):
        return float(nearest)
    return x


def rows_to_subjects(n_rows: int, repeats: int, icc: float) -> int:
    """Subjects needed to supply ``n_rows`` *effective* observations.

    ``ceil(n_rows * DE / repeats)`` — e.g. 85 independent observations with 3
    nights per subject and ICC=0.30 needs ceil(85*1.6/3)=46 subjects.
    """
    de = design_effect(repeats, icc)
    return math.ceil(_snap(int(n_rows) * de / int(repeats)))


def subjects_to_rows(n_subjects: int, repeats: int, icc: float) -> int:
    """Effective independent observations contributed by ``n_subjects``.

    Inverse of :func:`rows_to_subjects`, floored — never claim more information
    than the design provides. The ``max`` with the subject count is a belt-and-
    braces floor only: since ``DE <= repeats`` always, ``repeats/DE >= 1`` and
    the floor can never fall below ``n_subjects`` anyway.
    """
    de = design_effect(repeats, icc)
    n_subjects = int(n_subjects)
    return max(n_subjects, math.floor(_snap(n_subjects * int(repeats) / de)))


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
    """Inverse central F CDF by bisection (monotone in x).

    The upper bracket is GROWN until it actually exceeds the quantile. A fixed
    ``hi=1e9`` silently returned 1e9 for small denominator df at tiny alpha
    (``_f_quantile(1-5e-6, 1, 1)`` is ~1.6e10), which understates the critical
    value and therefore *overstates* power — the dangerous direction. Reachable
    via ``--n-tests`` large enough to drive alpha below ~2e-5.
    """
    lo, hi = 1e-9, 1e9
    while _f_cdf(hi, d1, d2) < p and hi < 1e300:
        hi *= 1e3
    while _f_cdf(lo, d1, d2) > p and lo > 1e-300:
        lo /= 1e3
    for _ in range(200):
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
    # The Poisson mixture is summed outward from mode ~ lam/2, so cost grows as
    # sqrt(lam) — at lam ~ 1e12 that is minutes of continued-fraction work, and
    # `mode` eventually overflows math.lgamma outright. Past this point the
    # non-central F has moved so far right that the CDF at any practical x is 0
    # to machine precision (equivalently: power is exactly 1).
    if lam > _MAX_LAMBDA:
        return 0.0
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
    if not math.isfinite(f2) or f2 > 1e6:
        raise ValueError(_TOO_LARGE.format(kind="f2", value=f2))
    if k < 1:
        raise ValueError("k must be >= 1")
    # Validate alpha/power ranges for consistency with the other estimators; the
    # returned z-values are not otherwise needed by the non-central F search.
    _z_pair(alpha, power)
    return _smallest_n_for_power(
        lambda n: _f_power(f2, n, k, k, alpha), k + 2, power
    )


# --- Sensitivity analysis: minimum detectable effect (MDES) ------------------
#
# The inverse question of ``required_total_n``: *given the N you already have*,
# what is the smallest effect you could detect at the chosen alpha/power? This
# turns an "underpowered" verdict into an actionable number ("with N=90 you can
# still detect r>=0.29"). Each MDES is the algebraic inverse of the matching
# forward formula, so the two are mutually consistent: feeding an MDES back into
# ``required_total_n`` returns (approximately) the same N.


def mdes_correlation(n: int, alpha: float = 0.05, power: float = 0.80,
                     sided: int = 2) -> float:
    """Smallest |r| detectable with total sample ``n`` (Fisher-z inverse).

    Inverts ``n = ((z_a + z_b) / atanh(r))^2 + 3``::

        r = tanh((z_a + z_b) / sqrt(n - 3))
    """
    n = int(n)
    if n <= 3:
        raise ValueError("n must be > 3 for a correlation MDES")
    za, zb = _z_pair(alpha, power, sided)
    return math.tanh((za + zb) / math.sqrt(n - 3))


def mdes_two_group(
    n_total: int, alpha: float = 0.05, power: float = 0.80,
    allocation: float = 0.5, sided: int = 2,
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
    za, zb = _z_pair(alpha, power, sided)
    return (za + zb) / math.sqrt(n_total * allocation * (1.0 - allocation))


def mdes_paired(n: int, alpha: float = 0.05, power: float = 0.80,
                sided: int = 2) -> float:
    """Smallest d_z detectable with ``n`` subjects in a within-subject design.

    Inverts ``n = (z_a + z_b)^2 / d_z^2 + 1``::

        d_z = (z_a + z_b) / sqrt(n - 1)
    """
    n = int(n)
    if n <= 1:
        raise ValueError("n must be > 1 for a paired MDES")
    za, zb = _z_pair(alpha, power, sided)
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
    _z_pair(alpha, power)
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
        if hi - lo <= 1e-12 * hi:
            break
    # Return `hi`, the smallest bracketed f2 that actually REACHES the target.
    # The midpoint 0.5*(lo+hi) sits partly in the under-powered half, so
    # power_for_regression(mdes) could come out at 0.7999999 and
    # n_for_regression(mdes) could land one N above the sample it was derived
    # from — breaking the round-trip the docstring promises.
    return hi


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
    _z_pair(alpha, power)
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
        if hi - lo <= 1e-12 * hi:
            break
    # Return `hi`, the smallest bracketed f2 that actually REACHES the target.
    # The midpoint 0.5*(lo+hi) sits partly in the under-powered half, so
    # power_for_regression(mdes) could come out at 0.7999999 and
    # n_for_regression(mdes) could land one N above the sample it was derived
    # from — breaking the round-trip the docstring promises.
    return hi


def detectable_effect(effect: dict, n, alpha: float = 0.05, power: float = 0.80,
                      sided: int = 2):
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
            return {"metric": "r", "value": mdes_correlation(n, alpha, power, sided)}
        if etype == "two_group":
            alloc = effect.get("allocation", 0.5)
            return {"metric": "d",
                    "value": mdes_two_group(n, alpha, power, alloc, sided)}
        if etype == "paired":
            return {"metric": "d_z", "value": mdes_paired(n, alpha, power, sided)}
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
        if etype == "two_proportion":
            p1 = float(effect["p1"])
            direction = 1 if float(effect["p2"]) >= p1 else -1
            p2 = mdes_two_proportions(
                n, p1, alpha, power, effect.get("allocation", 0.5), sided,
                direction,
            )
            # Report the risk difference (the comparable magnitude) but keep the
            # two rates, since "0.30 vs 0.47" is what goes in the protocol.
            return {"metric": "delta_p", "value": abs(p2 - p1),
                    "p1": round(p1, 4), "p2": round(p2, 4)}
        if etype == "survival":
            hr = mdes_survival(
                n, effect["event_rate"], alpha, power,
                effect.get("allocation", 0.5), sided,
            )
            return {"metric": "hr", "value": hr,
                    "hr_protective": round(1.0 / hr, 4),
                    "events": expected_events(n, effect["event_rate"])}
        if etype == "exploratory":
            return None
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    raise ValueError(f"Unknown effect type: {etype!r}")


# --- Attained power at the sample you already have ---------------------------
#
# ``required_total_n`` answers "how many do I need?" and ``detectable_effect``
# answers "what could I see?". The third question a planning meeting always asks
# is "given the N in the freezer and the effect we expect, what power do we
# actually have?" — a number, not a pass/fail flag. These functions answer it by
# evaluating the SAME power curves the sizing routines invert, so at
# N = required_total_n(effect) the attained power is always >= the target.


def power_for_correlation(r: float, n: int, alpha: float = 0.05,
                          sided: int = 2) -> float:
    """Power to detect correlation ``r`` with total sample ``n`` (Fisher z).

    Both rejection tails are counted for a two-sided test; the wrong-direction
    tail is negligible in practice but including it keeps this the exact inverse
    of :func:`n_for_correlation`'s normal approximation.
    """
    r = abs(float(r))
    if not 0 < r < 1:
        raise ValueError("r must be strictly between 0 and 1")
    n = int(n)
    if n <= 3:
        raise ValueError("n must be > 3 for a correlation power calculation")
    za = _z_alpha(alpha, sided)
    delta = 0.5 * math.log((1 + r) / (1 - r)) * math.sqrt(n - 3)
    pw = norm_cdf(delta - za)
    if sided == 2:
        pw += norm_cdf(-delta - za)
    return pw


def power_for_two_group(d: float, n_total: int, alpha: float = 0.05,
                        allocation: float = 0.5, sided: int = 2) -> float:
    """Power for a two-group mean difference ``d`` with ``n_total`` split by
    ``allocation`` (normal approximation, mirroring :func:`n_total_two_group`)."""
    d = abs(float(d))
    if d <= 0:
        raise ValueError("d must be > 0")
    n_total = int(n_total)
    if n_total < 4:
        raise ValueError("n_total must be >= 4 (>= 2 per group)")
    if not 0.0 < allocation < 1.0:
        raise ValueError("allocation must satisfy 0 < allocation < 1")
    za = _z_alpha(alpha, sided)
    _sq(d, "d")
    delta = d * math.sqrt(n_total * allocation * (1.0 - allocation))
    pw = norm_cdf(delta - za)
    if sided == 2:
        pw += norm_cdf(-delta - za)
    return pw


def power_for_paired(d: float, n: int, alpha: float = 0.05,
                     sided: int = 2) -> float:
    """Power for a within-subject d_z with ``n`` subjects (mirrors
    :func:`n_for_paired`)."""
    d = abs(float(d))
    if d <= 0:
        raise ValueError("d must be > 0")
    n = int(n)
    if n <= 1:
        raise ValueError("n must be > 1 for a paired power calculation")
    za = _z_alpha(alpha, sided)
    _sq(d, "d")
    delta = d * math.sqrt(n - 1)
    pw = norm_cdf(delta - za)
    if sided == 2:
        pw += norm_cdf(-delta - za)
    return pw


def power_for_regression(f2: float, n: int, k: int, alpha: float = 0.05) -> float:
    """Exact non-central F power for an overall-R^2 test (mirrors
    :func:`n_for_regression`)."""
    f2 = float(f2)
    k, n = int(k), int(n)
    if f2 <= 0:
        raise ValueError("f2 must be > 0")
    if k < 1:
        raise ValueError("k must be >= 1")
    if n < k + 2:
        raise ValueError("n must be >= k + 2 for a regression power calculation")
    _z_alpha(alpha)
    d2 = n - k - 1
    f_crit = _f_quantile(1.0 - alpha, k, d2)
    return 1.0 - _ncf_cdf(f_crit, k, d2, f2 * n)


def power_for_regression_change(f2: float, n: int, k_tested: int, k_control: int,
                                alpha: float = 0.05) -> float:
    """Exact non-central F power for an incremental-R^2 (ΔR^2) test (mirrors
    :func:`n_for_regression_change`)."""
    f2 = float(f2)
    n, k_tested, k_control = int(n), int(k_tested), int(k_control)
    if f2 <= 0:
        raise ValueError("f2 must be > 0")
    if k_tested < 1 or k_control < 0:
        raise ValueError("k_tested must be >= 1 and k_control >= 0")
    k_full = k_tested + k_control
    if n < k_full + 2:
        raise ValueError("n must be >= k_tested + k_control + 2")
    _z_alpha(alpha)
    d2 = n - k_full - 1
    f_crit = _f_quantile(1.0 - alpha, k_tested, d2)
    return 1.0 - _ncf_cdf(f_crit, k_tested, d2, f2 * n)


def attained_power(effect: dict, n, alpha: float = 0.05, sided: int = 2):
    """Power for an effect spec at sample ``n``, or ``None`` when inapplicable.

    ``None`` is returned for unknown n, exploratory designs, or an n below the
    minimum the formula admits — the same convention as
    :func:`detectable_effect`, so callers render one uniform "—".
    """
    if n is None:
        return None
    etype = effect.get("type")
    try:
        if etype == "correlation":
            return power_for_correlation(effect["r"], n, alpha, sided)
        if etype == "two_group":
            return power_for_two_group(
                effect["d"], n, alpha, effect.get("allocation", 0.5), sided
            )
        if etype == "paired":
            return power_for_paired(effect["d"], n, alpha, sided)
        if etype == "regression":
            return power_for_regression(
                effect["f2"], n, int(effect.get("k", 1)), alpha
            )
        if etype == "regression_change":
            return power_for_regression_change(
                effect["f2"], n, effect["k_tested"], effect["k_control"], alpha
            )
        if etype == "two_proportion":
            return power_for_two_proportions(
                effect["p1"], effect["p2"], n, alpha,
                effect.get("allocation", 0.5), sided,
            )
        if etype == "survival":
            return power_for_survival(
                effect["hr"], n, effect["event_rate"], alpha,
                effect.get("allocation", 0.5), sided,
            )
        if etype == "exploratory":
            return None
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    raise ValueError(f"Unknown effect type: {etype!r}")
