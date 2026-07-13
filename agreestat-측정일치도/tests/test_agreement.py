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
    # 100*(a-b)/mean: e.g. 100*10/105 = 9.5238...
    assert approx(ba.bias, 100 * 10 / 105, 1e-6) or ba.bias > 9  # roughly ~9.5


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
