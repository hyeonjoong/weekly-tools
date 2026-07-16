"""Unit / property / regression tests for method-comparison regression."""

import math
import random

import pytest

from agreestat import regression as R
from agreestat.regression import deming, passing_bablok


# --------------------------------------------------------------------------
# Deming — closed form verified by hand and property
# --------------------------------------------------------------------------
def test_deming_perfect_line():
    # y = 2x + 1 exactly -> slope 2, intercept 1, no bias flags (CI width 0)
    x = [1, 2, 3, 4, 5, 6]
    y = [2 * xi + 1 for xi in x]
    d = deming(x, y, lam=1.0)
    assert d.available
    assert abs(d.slope - 2.0) < 1e-9
    assert abs(d.intercept - 1.0) < 1e-9
    # perfect fit -> jackknife variance 0 -> CI collapses to the point estimate
    assert abs(d.slope_ci[0] - 2.0) < 1e-9 and abs(d.slope_ci[1] - 2.0) < 1e-9


def test_deming_orthogonal_matches_first_principal_axis():
    # For lam=1 the Deming slope equals the orthogonal-regression slope
    #   b = [(Syy-Sxx) + sqrt((Syy-Sxx)^2 + 4 Sxy^2)] / (2 Sxy)
    random.seed(3)
    x = [random.gauss(10, 3) for _ in range(30)]
    y = [1.2 * xi - 0.5 + random.gauss(0, 1) for xi in x]
    mx = sum(x) / len(x)
    my = sum(y) / len(y)
    sxx = sum((xi - mx) ** 2 for xi in x)
    syy = sum((yi - my) ** 2 for yi in y)
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    b_ref = ((syy - sxx) + math.sqrt((syy - sxx) ** 2 + 4 * sxy ** 2)) / (2 * sxy)
    d = deming(x, y, lam=1.0)
    assert abs(d.slope - b_ref) < 1e-12
    assert abs(d.intercept - (my - b_ref * mx)) < 1e-12


def test_deming_lambda_changes_slope_monotonically():
    # lam = Var(err_x)/Var(err_y): lam->0 (x error-free) -> OLS y-on-x slope
    # (Sxy/Sxx, the smallest for positive correlation); lam->inf (y error-free)
    # -> inverse-OLS slope (Syy/Sxy, the largest). So the slope increases in lam.
    random.seed(5)
    x = [random.gauss(50, 10) for _ in range(40)]
    y = [0.9 * xi + 3 + random.gauss(0, 5) for xi in x]
    d_small = deming(x, y, lam=0.25).slope
    d_one = deming(x, y, lam=1.0).slope
    d_big = deming(x, y, lam=4.0).slope
    assert d_small != d_one != d_big
    mx = sum(x) / len(x); my = sum(y) / len(y)
    sxx = sum((xi - mx) ** 2 for xi in x)
    syy = sum((yi - my) ** 2 for yi in y)
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    ols_yx = sxy / sxx        # lam -> 0 limit
    inv_ols = syy / sxy       # lam -> inf limit
    assert d_small < d_one < d_big
    assert ols_yx < d_small and d_big < inv_ols


def test_deming_needs_three_points():
    assert not deming([1, 2], [1, 2]).available


def test_deming_constant_method_unavailable():
    d = deming([5, 5, 5, 5], [1, 2, 3, 4])
    assert not d.available and "variance" in d.note.lower()


def test_deming_rejects_bad_lambda():
    with pytest.raises(ValueError):
        deming([1, 2, 3], [1, 2, 3], lam=0.0)
    with pytest.raises(ValueError):
        deming([1, 2, 3], [1, 2, 3], lam=float("inf"))


def test_deming_negative_correlation_slope_negative():
    random.seed(7)
    x = [random.gauss(20, 5) for _ in range(25)]
    y = [-1.3 * xi + 40 + random.gauss(0, 2) for xi in x]
    d = deming(x, y, lam=1.0)
    assert d.available and d.slope < 0


# --------------------------------------------------------------------------
# Passing–Bablok — hand-computed reference
# --------------------------------------------------------------------------
def test_passing_bablok_hand_example():
    # points (1,1),(2,2),(3,3),(4,5): pairwise slopes [1,1,1,4/3,3/2,2]
    # even N=6, K=0 -> shifted median = mean(S[3],S[4]) = mean(1, 4/3) = 7/6
    x = [1, 2, 3, 4]
    y = [1, 2, 3, 5]
    pb = passing_bablok(x, y)
    assert pb.available
    assert abs(pb.slope - 7.0 / 6.0) < 1e-12
    # intercept = median(y_i - slope*x_i)
    resid = sorted(yi - pb.slope * xi for xi, yi in zip(x, y))
    med = 0.5 * (resid[1] + resid[2])
    assert abs(pb.intercept - med) < 1e-12
    assert pb.n_slopes == 6 and pb.k_offset == 0


def test_passing_bablok_perfect_line():
    # y = 3x - 2 is a perfect fit but NOT the identity -> both biases are real:
    # slope 3 (!=1) and intercept -2 (!=0), degenerate CI = the point estimate.
    x = list(range(1, 11))
    y = [3 * xi - 2 for xi in x]
    pb = passing_bablok(x, y)
    assert pb.available
    assert abs(pb.slope - 3.0) < 1e-12
    assert abs(pb.intercept + 2.0) < 1e-12
    assert pb.proportional_bias is True
    assert pb.constant_bias is True


def test_passing_bablok_identity_no_bias():
    # y = x exactly -> slope 1, intercept 0, no proportional/constant bias
    x = list(range(1, 11))
    y = list(range(1, 11))
    pb = passing_bablok(x, y)
    assert pb.available
    assert abs(pb.slope - 1.0) < 1e-12
    assert abs(pb.intercept) < 1e-12
    assert pb.proportional_bias is False
    assert pb.constant_bias is False


def test_passing_bablok_excludes_slope_minus_one():
    # a pair with slope exactly -1 must be dropped, not counted in K
    x = [0.0, 1.0, 2.0, 3.0]
    y = [0.0, -1.0, 5.0, 6.0]  # (0,0)-(1,-1) has slope -1 -> excluded
    pb = passing_bablok(x, y)
    # total pairs = 6; one has slope -1 -> 5 usable slopes
    assert pb.n_slopes == 5


def test_passing_bablok_detects_proportional_bias():
    random.seed(11)
    x = [random.gauss(50, 15) for _ in range(60)]
    y = [1.25 * xi + random.gauss(0, 3) for xi in x]  # 25% proportional bias
    pb = passing_bablok(x, y)
    assert pb.available
    assert pb.slope_ci[0] > 1.0  # CI clearly above 1
    assert pb.proportional_bias is True


def test_passing_bablok_needs_three_points():
    assert not passing_bablok([1, 2], [1, 2]).available


def test_passing_bablok_all_identical_unavailable():
    pb = passing_bablok([2, 2, 2], [2, 2, 2])
    assert not pb.available


# --------------------------------------------------------------------------
# Cross-consistency: on clean data PB and Deming agree closely
# --------------------------------------------------------------------------
def test_pb_and_deming_agree_on_clean_data():
    random.seed(13)
    x = [random.gauss(30, 8) for _ in range(80)]
    y = [1.05 * xi - 1.0 + random.gauss(0, 1.5) for xi in x]
    pb = passing_bablok(x, y)
    d = deming(x, y, lam=1.0)
    assert abs(pb.slope - d.slope) < 0.05
    assert abs(pb.intercept - d.intercept) < 2.0


def test_bias_flags_none_when_ci_unavailable():
    # n=3 is enough for a point estimate but the PB rank CI is out of range
    pb = passing_bablok([1.0, 2.0, 3.1], [1.0, 2.1, 2.9])
    assert pb.available
    # CI unavailable -> bias flags None (cannot decide), never a bogus bool
    if pb.slope_ci[0] != pb.slope_ci[0]:  # NaN
        assert pb.proportional_bias is None
        assert pb.constant_bias is None


def test_deming_slope_scale_invariant_to_units():
    # multiplying both methods by a constant scales intercept but not slope
    random.seed(17)
    x = [random.gauss(40, 10) for _ in range(30)]
    y = [0.98 * xi + 2 + random.gauss(0, 2) for xi in x]
    d1 = deming(x, y, lam=1.0)
    # NOTE: scaling x and y by the same factor keeps lam=1 orthogonality only if
    # both axes scale equally; slope is preserved, intercept scales.
    x2 = [10 * xi for xi in x]
    y2 = [10 * yi for yi in y]
    d2 = deming(x2, y2, lam=1.0)
    assert abs(d1.slope - d2.slope) < 1e-9
    assert abs(d2.intercept - 10 * d1.intercept) < 1e-6


# --------------------------------------------------------------------------
# Round-1 hardening: finiteness guards, size caps, notes
# --------------------------------------------------------------------------
def test_deming_rejects_nonfinite_input():
    d = deming([1.0, 2.0, float("nan"), 4.0], [2.0, 4.0, 6.0, 8.0])
    assert not d.available and "비유한" in d.note
    d2 = deming([1.0, 2.0, float("inf"), 4.0], [2.0, 4.0, 6.0, 8.0])
    assert not d2.available


def test_deming_output_finiteness_guard_on_overflow():
    x = [1e150, 9e149, 5e149, 8e149, 3e149]
    y = [1.0e150, 9.1e149, 5.2e149, 7.9e149, 3.1e149]
    d = deming(x, y)
    assert not d.available  # must NOT return inf/nan as available=True


def test_deming_huge_lambda_does_not_emit_inf():
    x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    y = [1.1, 2.0, 2.9, 4.2, 5.1, 5.8]
    d = deming(x, y, lam=1e300)
    if d.available:
        assert math.isfinite(d.slope) and math.isfinite(d.intercept)


def test_passing_bablok_rejects_nonfinite_input():
    pb = passing_bablok([1.0, 2.0, float("inf"), 4.0], [2.0, 4.0, 6.0, 8.0])
    assert not pb.available and "비유한" in pb.note
    pb2 = passing_bablok([1.0, 2.0, 3.0], [2.0, float("nan"), 6.0])
    assert not pb2.available


def test_passing_bablok_size_cap():
    n = 3001
    x = [float(i) for i in range(n)]
    y = [1.01 * xi + 0.5 for xi in x]
    pb = passing_bablok(x, y)
    assert not pb.available and "너무" in pb.note


def test_passing_bablok_anticorrelation_note_not_misleading():
    pb = passing_bablok([1.0, 2.0, 3.0, 4.0, 5.0], [5.0, 4.0, 3.0, 2.0, 1.0])
    assert not pb.available
    assert "identical" not in pb.note.lower()
    assert "−1" in pb.note or "음의" in pb.note


def test_deming_lambda1_unchanged_after_convention_flip():
    random.seed(41)
    x = [random.gauss(30, 6) for _ in range(30)]
    y = [1.1 * xi - 2 + random.gauss(0, 2) for xi in x]
    mx = sum(x) / len(x); my = sum(y) / len(y)
    sxx = sum((xi - mx) ** 2 for xi in x)
    syy = sum((yi - my) ** 2 for yi in y)
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    b_orth = ((syy - sxx) + math.sqrt((syy - sxx) ** 2 + 4 * sxy ** 2)) / (2 * sxy)
    assert abs(deming(x, y, lam=1.0).slope - b_orth) < 1e-12


# --------------------------------------------------------------------------
# Decision-point predicted bias (CLSI EP09)
# --------------------------------------------------------------------------
def test_deming_decision_point_bias_matches_formula():
    random.seed(51)
    x = [random.gauss(40, 10) for _ in range(30)]
    y = [1.1 * xi - 3 + random.gauss(0, 2) for xi in x]
    xc = 45.0
    d = deming(x, y, lam=1.0, decision_point=xc)
    assert d.decision_point == xc
    expected = d.intercept + (d.slope - 1.0) * xc
    assert abs(d.bias_at_dp - expected) < 1e-12
    # CI finite and brackets the point estimate
    lo, hi = d.bias_at_dp_ci
    assert math.isfinite(lo) and math.isfinite(hi)
    assert lo <= d.bias_at_dp <= hi


def test_passing_bablok_decision_point_point_estimate():
    random.seed(52)
    x = [random.gauss(40, 10) for _ in range(40)]
    y = [1.1 * xi - 3 + random.gauss(0, 2) for xi in x]
    xc = 50.0
    pb = passing_bablok(x, y, decision_point=xc)
    assert pb.decision_point == xc
    assert abs(pb.bias_at_dp - (pb.intercept + (pb.slope - 1.0) * xc)) < 1e-12


def test_decision_point_none_leaves_bias_nan():
    d = deming([1, 2, 3, 4, 5], [1.1, 2.0, 3.2, 3.9, 5.1])
    assert d.decision_point is None
    assert d.bias_at_dp != d.bias_at_dp  # NaN


def test_deming_decision_point_overflow_keeps_point_estimate():
    # a huge Xc overflows the CI variance (**2); the point estimate stays finite
    # and available, only the CI is skipped (no OverflowError escapes).
    x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    y = [1.04 * xi + 0.1 for xi in x]
    d = deming(x, y, decision_point=1e200)
    assert d.available
    assert math.isfinite(d.bias_at_dp)      # point estimate finite
    assert d.bias_at_dp_ci[0] != d.bias_at_dp_ci[0]  # CI skipped -> NaN
