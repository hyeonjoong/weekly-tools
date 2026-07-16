"""Agreement statistics validated by hand math and documented worked examples."""

import math

import pytest

from agreestat import agreement as A


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


# --------------------------------------------------------------------------
# Bland-Altman
# --------------------------------------------------------------------------
def test_bland_altman_handmath():
    # a-b = [-1, 1, 1, -1] -> bias 0; sample sd = sqrt(4/3)
    a = [10, 12, 14, 16]
    b = [11, 11, 13, 17]
    ba = A.bland_altman(a, b)
    assert approx(ba.bias, 0.0)
    assert approx(ba.sd_diff, math.sqrt(4.0 / 3.0))
    assert approx(ba.loa_lower, -1.96 * math.sqrt(4.0 / 3.0))
    assert approx(ba.loa_upper, 1.96 * math.sqrt(4.0 / 3.0))
    # CI ordering
    assert ba.bias_ci[0] < ba.bias < ba.bias_ci[1]
    assert ba.loa_lower_ci[0] < ba.loa_lower < ba.loa_lower_ci[1]


def test_bland_altman_bias_offset():
    a = [12, 13, 14, 15, 16]
    b = [10, 11, 12, 13, 14]  # a is exactly +2
    ba = A.bland_altman(a, b)
    assert approx(ba.bias, 2.0)
    assert approx(ba.sd_diff, 0.0)
    # zero-variance differences -> LoA collapse to bias
    assert approx(ba.loa_lower, 2.0)
    assert approx(ba.loa_upper, 2.0)


def test_bland_altman_proportional_bias_detected():
    # difference grows with magnitude -> significant positive slope
    a = [10, 22, 33, 46, 58, 71, 84, 96, 110, 121]
    b = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    ba = A.bland_altman(a, b)
    assert ba.prop_slope > 0
    assert ba.prop_pvalue < 0.05
    assert ba.prop_bias is True


def test_bland_altman_percent_mode():
    a = [110, 220, 330]
    b = [100, 200, 300]  # each 10% high
    ba = A.bland_altman(a, b, mode="percent")
    assert ba.unit == "%"
    # 100*(a-b)/mean: e.g. 100*10/105 = 9.5238...  (exact, no escape hatch)
    assert approx(ba.bias, 100 * 10 / 105, 1e-9)


def test_bland_altman_percent_denominator_is_pair_mean():
    # Lock the denominator to the pair mean, not method B: 100*(150-50)/100 = 100,
    # whereas dividing by B would give 200.
    ba = A.bland_altman([150, 150], [50, 50], mode="percent")
    assert approx(ba.bias, 100.0, 1e-9)


def test_bland_altman_outside_loa_count():
    # 9 tight pairs + 1 gross outlier -> exactly one point outside the LoA
    a = [10, 10, 10, 10, 10, 10, 10, 10, 10, 10]
    b = [10, 10, 10, 10, 10, 10, 10, 10, 10, 40]
    ba = A.bland_altman(a, b)
    assert ba.n_outside == 1
    assert approx(ba.pct_outside, 10.0, 1e-9)


def test_bland_altman_percent_zero_mean_guarded():
    with pytest.raises(ValueError):
        A.bland_altman([1.0, -1.0], [-1.0, 1.0], mode="percent")


# --------------------------------------------------------------------------
# ICC — Shrout & Fleiss (1979) worked example
# --------------------------------------------------------------------------
SHROUT_FLEISS = [[9, 2, 5, 8], [6, 1, 3, 2], [8, 4, 6, 8],
                 [7, 1, 2, 6], [10, 5, 6, 9], [6, 2, 4, 7]]


def test_icc_known_point_estimates():
    icc21, icc31, ms = A.icc(SHROUT_FLEISS)
    # exact hand values: ICC(2,1)=184/635, ICC(3,1)=920/1287
    assert approx(icc21.value, 184.0 / 635.0, 1e-9)
    assert approx(icc31.value, 920.0 / 1287.0, 1e-9)
    # mean squares
    assert approx(ms.msr, 11.2416667, 1e-5)
    assert approx(ms.msc, 32.4861111, 1e-5)
    assert approx(ms.mse, 1.0194444, 1e-5)
    assert approx(icc21.f, 11.0272, 1e-3)  # MSR/MSE, psych rounds to 11.03


def test_icc_known_confidence_intervals():
    # matches R psych::ICC to the reported precision
    icc21, icc31, _ = A.icc(SHROUT_FLEISS)
    assert approx(icc21.ci_lower, 0.0188, 1e-3)
    assert approx(icc21.ci_upper, 0.7611, 1e-3)
    assert approx(icc31.ci_lower, 0.3425, 1e-3)
    assert approx(icc31.ci_upper, 0.9459, 1e-3)


def test_icc_interpretation():
    assert A.interpret_icc(0.4).startswith("poor")
    assert A.interpret_icc(0.6).startswith("moderate")
    assert A.interpret_icc(0.8).startswith("good")
    assert A.interpret_icc(0.95).startswith("excellent")


def test_icc_needs_two_subjects():
    with pytest.raises(ValueError):
        A.two_way_ms([[1.0, 2.0]])


def test_icc_consistency_vs_agreement():
    # add a constant offset to method B -> consistency unchanged, agreement drops
    a = [10, 12, 14, 16, 18, 20, 22, 24]
    b = [11, 13, 15, 17, 19, 21, 23, 25]  # b = a + 1 exactly
    icc21, icc31, _ = A.icc([[x, y] for x, y in zip(a, b)])
    # perfect consistency (rank/scale identical)
    assert approx(icc31.value, 1.0, 1e-9)
    # absolute agreement penalised by the systematic +1 offset
    assert icc21.value < icc31.value


# --------------------------------------------------------------------------
# Lin's CCC
# --------------------------------------------------------------------------
def test_ccc_handmath():
    # [1,2,3] vs [2,3,4]: r=1 but offset -> CCC = 4/7
    c = A.ccc([1, 2, 3], [2, 3, 4])
    assert approx(c.value, 4.0 / 7.0, 1e-9)
    assert approx(c.pearson_r, 1.0, 1e-9)
    assert c.value < c.pearson_r  # accuracy penalty


def test_ccc_perfect_agreement():
    c = A.ccc([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
    assert approx(c.value, 1.0, 1e-12)


def test_ccc_ci_contains_point():
    a = [10, 12, 11, 13, 9, 14, 10, 12, 11, 13]
    b = [10.5, 12.2, 11.1, 12.8, 9.4, 13.5, 10.2, 12.1, 11.3, 12.9]
    c = A.ccc(a, b)
    assert c.ci_lower < c.value < c.ci_upper


def test_ccc_constant_method_guarded():
    # method B constant -> Pearson/CI undefined but value still returned
    c = A.ccc([1, 2, 3, 4], [5, 5, 5, 5])
    assert c.pearson_r != c.pearson_r  # NaN
    assert c.ci_lower != c.ci_lower


def test_ccc_interpretation():
    assert A.interpret_ccc(0.85).startswith("poor")
    assert A.interpret_ccc(0.93).startswith("moderate")
    assert A.interpret_ccc(0.97).startswith("substantial")
    assert A.interpret_ccc(0.995).startswith("almost perfect")


# --------------------------------------------------------------------------
# Repeatability
# --------------------------------------------------------------------------
def test_repeatability_no_subjects():
    r = A.repeatability([1, 2, 3], [1, 2, 3], None)
    assert r.available is False


def test_repeatability_no_replicates():
    r = A.repeatability([1, 2, 3], [1, 2, 3], ["A", "B", "C"])
    assert r.available is False


def test_repeatability_handmath():
    # subject A: method-a values 10,12 -> within var over (n-1): dev^2 sum=2, dof=1
    # subject B: method-a values 20,24 -> dev^2 sum=8, dof=1 ; pooled ss=10, dof=2
    # sw_a = sqrt(10/2) = sqrt(5)
    a = [10, 12, 20, 24]
    b = [10, 10, 20, 20]  # B has sw=0 within each subject
    r = A.repeatability(a, b, ["A", "A", "B", "B"])
    assert r.available is True
    assert r.n_subjects == 2
    assert approx(r.sw_a, math.sqrt(5.0), 1e-9)
    assert approx(r.sw_b, 0.0, 1e-12)
    # RC = 2.77 * sw
    assert approx(r.rc_a, 1.96 * math.sqrt(2.0) * math.sqrt(5.0), 1e-9)
    # CV = 100 * sw / mean(a); mean(a) = 16.5
    assert approx(r.cv_a, 100.0 * math.sqrt(5.0) / 16.5, 1e-9)


# --------------------------------------------------------------------------
# Pearson + paired t
# --------------------------------------------------------------------------
def test_pearson_perfect():
    p = A.pearson([1, 2, 3, 4, 5], [2, 4, 6, 8, 10])
    assert approx(p.r, 1.0, 1e-12)


def test_pearson_constant_guarded():
    p = A.pearson([1, 2, 3], [5, 5, 5])
    assert p.r != p.r  # NaN


def test_paired_t_handmath():
    # differences all +2, zero variance -> infinite t
    r = A.paired_t([3, 4, 5], [1, 2, 3])
    assert approx(r.mean_diff, 2.0)
    assert math.isinf(r.t)
    assert r.pvalue == 0.0


def test_paired_t_matches_bias():
    a = [10.5, 12.1, 11.3, 13.0, 9.8, 14.2]
    b = [10.1, 12.4, 10.9, 13.3, 9.5, 13.8]
    pt = A.paired_t(a, b)
    ba = A.bland_altman(a, b)
    assert approx(pt.mean_diff, ba.bias, 1e-12)


def test_errors():
    with pytest.raises(ValueError):
        A.bland_altman([1.0], [2.0])
    with pytest.raises(ValueError):
        A.ccc([1.0], [2.0])


# --------------------------------------------------------------------------
# Degenerate ICC/CCC branches (previously uncovered)
# --------------------------------------------------------------------------
def test_icc_negative_estimate_has_finite_ci():
    # near-random data -> negative ICC(2,1); the exact CI is still finite
    # (regression for the relaxed 0<v2<1 guard; matches pingouin [-0.51, 0.47]).
    import random
    random.seed(42)
    rows = [[random.gauss(0, 1), random.gauss(0, 1)] for _ in range(15)]
    icc21, _icc31, _ = A.icc(rows)
    assert icc21.value < 0
    assert math.isfinite(icc21.ci_lower) and math.isfinite(icc21.ci_upper)
    assert icc21.ci_lower < icc21.value < icc21.ci_upper
    assert approx(icc21.ci_lower, -0.512380179284373, 1e-6)
    assert approx(icc21.ci_upper, 0.47194937392139585, 1e-6)


def test_icc_perfect_fit_infinite_f_nan_ci():
    icc21, icc31, ms = A.icc([[1, 1], [2, 2], [3, 3], [4, 4]])
    assert approx(icc31.value, 1.0, 1e-12)
    assert math.isinf(icc21.f)
    assert icc21.ci_lower != icc21.ci_lower  # NaN (mse == 0)


def test_icc_all_constant_nan_value():
    icc21, icc31, _ = A.icc([[5, 5], [5, 5], [5, 5]])
    assert icc21.value != icc21.value  # NaN


def test_icc_negative_valid_ci_still_brackets():
    # a strongly-negative ICC whose exact CI is finite must bracket the estimate
    rows = [[0.10, 5.02], [5.01, 0.11], [2.55, 2.54]]
    icc21, _icc31, _ = A.icc(rows)
    lo, hi = icc21.ci_lower, icc21.ci_upper
    if lo == lo and hi == hi:
        assert lo - 1e-9 <= icc21.value <= hi + 1e-9 and lo < hi


def test_icc_ci_collapse_returns_nan():
    # Fixture where the McGraw-Wong interval would exclude the point estimate
    # (Satterthwaite df collapses) -> guard returns NaN CI, not a bogus pinch.
    rows = [[0.2592, 0.2583], [0.7451, -0.4132], [0.9486, -0.9247]]
    icc21, _icc31, ms = A.icc(rows)
    assert icc21.value == icc21.value        # finite point estimate
    assert ms.mse > 0                        # not the mse==0 precondition
    assert icc21.ci_lower != icc21.ci_lower  # NaN
    assert icc21.ci_upper != icc21.ci_upper


def test_ccc_both_constant_equal_raises():
    with pytest.raises(ValueError):
        A.ccc([2, 2, 2], [2, 2, 2])


# --------------------------------------------------------------------------
# Property / invariance regression tests (deterministic random)
# --------------------------------------------------------------------------
def _rand_pairs(seed, n=25):
    import random
    random.seed(seed)
    a = [random.gauss(50, 10) for _ in range(n)]
    b = [ai * random.uniform(0.9, 1.1) + random.gauss(0, 3) for ai in a]
    return a, b


def test_property_ccc_in_unit_interval_and_bounded_by_r():
    for seed in range(20):
        a, b = _rand_pairs(seed)
        c = A.ccc(a, b)
        assert -1.0 - 1e-9 <= c.value <= 1.0 + 1e-9
        assert abs(c.value) <= abs(c.pearson_r) + 1e-9


def test_property_icc_upper_bound():
    # Both single-measure ICCs are bounded above by 1. (ICC(2,1) <= ICC(3,1) is
    # NOT a universal law: when the between-column MS is below error, absolute
    # agreement can marginally exceed consistency — so we don't assert ordering.)
    for seed in range(20):
        a, b = _rand_pairs(seed)
        icc21, icc31, _ = A.icc([[x, y] for x, y in zip(a, b)])
        assert icc21.value <= 1.0 + 1e-9 and icc31.value <= 1.0 + 1e-9


def test_icc_agreement_below_consistency_with_systematic_offset():
    # With a real systematic offset (B = A + const), absolute agreement is
    # strictly penalised below consistency.
    a = [10, 12, 14, 16, 18, 20, 22, 24]
    b = [x + 2 for x in a]
    icc21, icc31, _ = A.icc([[x, y] for x, y in zip(a, b)])
    assert icc21.value < icc31.value


def test_property_loa_ordering_and_ci_nesting():
    for seed in range(20):
        a, b = _rand_pairs(seed)
        ba = A.bland_altman(a, b)
        assert ba.loa_lower < ba.bias < ba.loa_upper
        assert ba.loa_lower_ci[0] < ba.loa_lower < ba.loa_lower_ci[1]
        assert ba.loa_upper_ci[0] < ba.loa_upper < ba.loa_upper_ci[1]
        assert ba.bias_ci[0] < ba.bias < ba.bias_ci[1]


def test_property_bias_equals_paired_mean_diff():
    for seed in range(20):
        a, b = _rand_pairs(seed)
        assert approx(A.bland_altman(a, b).bias, A.paired_t(a, b).mean_diff, 1e-12)


def test_property_bias_antisymmetry():
    for seed in range(10):
        a, b = _rand_pairs(seed)
        assert approx(A.bland_altman(a, b).bias, -A.bland_altman(b, a).bias, 1e-12)


def test_property_regression_loa_recovers_widening():
    # difference grows with mean -> regression LoA at max mean wider than at min
    a = [10, 22, 33, 46, 58, 71, 84, 96, 110, 121, 133, 145]
    b = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120]
    ba = A.bland_altman(a, b)
    assert ba.prop_bias is True
    rl = ba.reg_loa
    assert rl is not None and rl.available
    # fitted diff line matches the proportional-bias regression
    assert approx(rl.diff_slope, ba.prop_slope, 1e-9)
    assert approx(rl.diff_intercept, ba.prop_intercept, 1e-9)
    assert approx(rl.factor, math.sqrt(math.pi / 2.0), 1e-12)


def test_regression_loa_absent_without_prop_bias():
    a = [10, 12, 14, 16, 11, 13, 9, 15]
    b = [10.1, 12.1, 13.9, 16.1, 11.0, 12.9, 9.1, 15.1]
    ba = A.bland_altman(a, b)
    assert ba.prop_bias is False
    assert ba.reg_loa is None


def test_bland_altman_se_loa_and_halfwidth():
    a = [10, 12, 14, 16, 18, 20]
    b = [11, 11, 15, 15, 19, 19]
    ba = A.bland_altman(a, b)
    n = len(a)
    expected_se = ba.sd_diff * math.sqrt(1.0 / n + 1.96 ** 2 / (2.0 * (n - 1)))
    assert approx(ba.se_loa, expected_se, 1e-12)
    # half-width == half of each LoA CI width
    assert approx(ba.loa_ci_halfwidth,
                  (ba.loa_lower_ci[1] - ba.loa_lower_ci[0]) / 2.0, 1e-9)


# --------------------------------------------------------------------------
# Repeated-measures Bland-Altman (Bland & Altman 2007)
# --------------------------------------------------------------------------
def test_repeated_measures_ba_variance_components():
    # independent recompute of the variance components; subject diff-levels are
    # well separated (0-ish / 2-ish / 4-ish) so between-subject var stays positive
    a = [10, 12, 14, 20, 22, 24, 30, 32, 34]
    b = [10, 12, 13, 18, 20, 21, 26, 28, 29]
    subs = ["A", "A", "A", "B", "B", "B", "C", "C", "C"]
    rm = A.repeated_measures_ba(a, b, subs)
    assert rm.available is True
    assert rm.n_subjects == 3 and rm.n_pairs == 9
    import collections
    by = collections.defaultdict(list)
    for s, x, y in zip(subs, a, b):
        by[s].append(x - y)
    alld = [x - y for x, y in zip(a, b)]
    N, n = len(alld), len(by)
    grand = sum(alld) / N
    ssb = sum(len(v) * (sum(v) / len(v) - grand) ** 2 for v in by.values())
    ssw = sum(sum((d - sum(v) / len(v)) ** 2 for d in v) for v in by.values())
    msb, msw = ssb / (n - 1), ssw / (N - n)
    m0 = (N - sum(len(v) ** 2 for v in by.values()) / N) / (n - 1)
    sb2 = (msb - msw) / m0
    assert approx(rm.var_within, msw, 1e-9)
    assert approx(rm.var_between, sb2, 1e-9)
    assert approx(rm.m0, m0, 1e-9)
    assert approx(rm.sd_diff, math.sqrt(sb2 + msw), 1e-9)
    assert approx(rm.bias, grand, 1e-12)


def test_repeated_measures_ba_not_available_paths():
    assert A.repeated_measures_ba([1, 2], [1, 2], None).available is False
    r = A.repeated_measures_ba([1, 2, 3], [1, 2, 3], ["A", "B", "C"])
    assert r.available is False  # no replicates
    assert r.n_subjects == 3


def test_repeated_measures_ba_percent_mode():
    a = [110, 108, 220, 216, 330, 324]
    b = [100, 100, 200, 200, 300, 300]
    subs = ["A", "A", "B", "B", "C", "C"]
    rm = A.repeated_measures_ba(a, b, subs, mode="percent")
    assert rm.available is True
    # all percentage diffs are positive (a > b) -> positive bias
    assert rm.bias > 0
    assert rm.n_replicated_subjects == 3


def test_repeated_measures_ba_percent_zero_mean_guarded():
    a = [1.0, -1.0, 5.0, 5.0]
    b = [-1.0, 1.0, 5.0, 5.0]  # first subject has a pair with mean 0
    rm = A.repeated_measures_ba(a, b, ["A", "A", "B", "B"], mode="percent")
    assert rm.available is False


def test_repeated_measures_n_replicated_subjects():
    # only one subject has replicates
    a = [10, 12, 20, 30, 40]
    b = [10, 11, 20, 30, 40]
    subs = ["A", "A", "B", "C", "D"]
    rm = A.repeated_measures_ba(a, b, subs)
    assert rm.available is True
    assert rm.n_replicated_subjects == 1


def test_regression_loa_sd_negative_warning():
    # proportional bias with residual scatter shrinking as the mean grows ->
    # the linear residual-SD model extrapolates below zero at the high end
    a = [6.862, 22.335, 30.305, 37.515, 47.239,
         56.149, 65.51, 74.683, 82.708, 91.684]
    b = [13.138, 13.665, 21.695, 30.485, 36.761,
         43.851, 50.49, 57.317, 65.292, 72.316]
    ba = A.bland_altman(a, b)
    assert ba.prop_bias is True
    assert ba.reg_loa.sd_negative_warning is True


def test_repeated_measures_ba_clamps_negative_between_var():
    # within-subject scatter dominates -> MSB < MSW -> between var clamped to 0
    a = [10, 20, 10, 20, 10, 20]
    b = [10, 10, 10, 10, 10, 10]
    subs = ["A", "A", "B", "B", "C", "C"]
    rm = A.repeated_measures_ba(a, b, subs)
    assert rm.available is True
    assert rm.var_between == 0.0
    assert rm.var_between_clamped is True


def test_property_percent_scale_invariance():
    # scaling both methods by a positive constant leaves percent bias/LoA fixed
    for seed in range(10):
        a, b = _rand_pairs(seed)
        a = [abs(x) + 20 for x in a]  # keep means positive for percent mode
        b = [abs(y) + 20 for y in b]
        ba1 = A.bland_altman(a, b, mode="percent")
        ba2 = A.bland_altman([x * 7.0 for x in a], [y * 7.0 for y in b],
                             mode="percent")
        assert approx(ba1.bias, ba2.bias, 1e-9)
        assert approx(ba1.loa_lower, ba2.loa_lower, 1e-9)
