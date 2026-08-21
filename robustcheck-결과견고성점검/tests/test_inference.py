"""자체 구현 통계량이 scipy 와 일치하는지 — 하드코딩된 기대값 대조.

기대값은 `tests/_scipy_expected.py` 에 float 리터럴로 박혀 있어, 이 테스트는
scipy 가 설치돼 있지 않아도 돈다(완전 오프라인).
"""

import math

import pytest

import _scipy_expected as EXPECTED
from robustcheck.distributions import (
    betainc,
    normal_cdf,
    normal_sf,
    normal_two_sided,
    student_t_cdf,
    student_t_two_sided,
)
from robustcheck.inference import (
    MWU_EXACT_MAX_CELLS,
    SingularModel,
    ancova_baseline,
    mann_whitney_u,
    mean,
    paired_t_test,
    pearson_r,
    quade_rank_ancova,
    rankdata,
    spearman_rho,
    stdev,
    student_t_test,
    variance,
    welch_t_test,
    wilcoxon_signed_rank,
)

TOL = 1e-9


def close(a, b, tol=TOL):
    return abs(a - b) <= tol * max(1.0, abs(b))


# ------------------------------------------------------------ 분포 함수


@pytest.mark.parametrize("a,b,x,expected", EXPECTED.BETAINC)
def test_betainc_matches_scipy(a, b, x, expected):
    assert close(betainc(a, b, x), expected)


@pytest.mark.parametrize("t,df,expected", EXPECTED.T_TAIL)
def test_student_t_two_sided_matches_scipy(t, df, expected):
    assert close(student_t_two_sided(t, df), expected)


@pytest.mark.parametrize("z,expected", EXPECTED.NORMAL_TAIL)
def test_normal_two_sided_matches_scipy(z, expected):
    assert close(normal_two_sided(z), expected)


def test_betainc_endpoints():
    assert betainc(2.0, 3.0, 0.0) == 0.0
    assert betainc(2.0, 3.0, 1.0) == 1.0


def test_betainc_rejects_bad_parameters():
    with pytest.raises(ValueError):
        betainc(0.0, 1.0, 0.5)
    with pytest.raises(ValueError):
        betainc(1.0, -1.0, 0.5)


def test_betainc_symmetry():
    assert close(betainc(2.0, 5.0, 0.3), 1.0 - betainc(5.0, 2.0, 0.7), 1e-12)


def test_normal_cdf_symmetry():
    for z in (0.3, 1.0, 2.5, 4.0):
        assert close(normal_cdf(-z), normal_sf(z), 1e-14)


def test_normal_cdf_center():
    assert close(normal_cdf(0.0), 0.5, 1e-15)


def test_normal_sf_far_tail_does_not_underflow_to_zero():
    assert 0.0 < normal_sf(8.0) < 1e-14


def test_student_t_cdf_is_monotone():
    values = [student_t_cdf(t, 7) for t in (-4, -2, -0.5, 0, 0.5, 2, 4)]
    assert values == sorted(values)


def test_student_t_cdf_center_is_half():
    assert close(student_t_cdf(0.0, 5), 0.5, 1e-12)


def test_student_t_two_sided_infinite_statistic():
    assert student_t_two_sided(float("inf"), 5) == 0.0


def test_student_t_rejects_zero_df():
    assert math.isnan(student_t_two_sided(1.0, 0))


def test_distribution_nan_propagates():
    assert math.isnan(normal_cdf(float("nan")))
    assert math.isnan(normal_two_sided(float("nan")))
    assert math.isnan(student_t_two_sided(float("nan"), 5))


# --------------------------------------------------------------- t 검정


@pytest.mark.parametrize("a,b,t,p", EXPECTED.WELCH)
def test_welch_matches_scipy(a, b, t, p):
    result = welch_t_test(a, b)
    assert close(result.statistic, t)
    assert close(result.p, p)


@pytest.mark.parametrize("a,b,t,p", EXPECTED.STUDENT)
def test_student_matches_scipy(a, b, t, p):
    result = student_t_test(a, b)
    assert close(result.statistic, t)
    assert close(result.p, p)


@pytest.mark.parametrize("pre,post,t,p", EXPECTED.PAIRED)
def test_paired_matches_scipy(pre, post, t, p):
    result = paired_t_test(pre, post)
    assert close(result.statistic, t)
    assert close(result.p, p)


def test_welch_df_between_group_sizes():
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [2.0, 4.0, 6.0, 8.0, 10.0, 12.0]
    assert 4.0 <= welch_t_test(a, b).df <= 9.0


def test_welch_requires_two_per_group():
    with pytest.raises(ValueError):
        welch_t_test([1.0], [1.0, 2.0])


def test_welch_zero_variance_both_groups_raises():
    with pytest.raises(ValueError):
        welch_t_test([3.0, 3.0, 3.0], [3.0, 3.0, 3.0])


def test_student_pooled_variance_zero_raises():
    with pytest.raises(ValueError):
        student_t_test([2.0, 2.0], [2.0, 2.0])


def test_paired_length_mismatch_raises():
    with pytest.raises(ValueError):
        paired_t_test([1.0, 2.0], [1.0])


def test_paired_zero_difference_variance_raises():
    with pytest.raises(ValueError):
        paired_t_test([1.0, 2.0, 3.0], [2.0, 3.0, 4.0])


def test_paired_sign_follows_post_minus_pre():
    result = paired_t_test([10.0, 11.0, 12.0, 13.0], [8.0, 8.0, 9.0, 12.0])
    assert result.statistic < 0


# ------------------------------------------------------------ 비모수 검정


@pytest.mark.parametrize("a,b,u,p", EXPECTED.MWU_EXACT)
def test_mann_whitney_exact_matches_scipy(a, b, u, p):
    result = mann_whitney_u(a, b)
    assert result.method == "정확분포"
    assert close(result.statistic, u)
    assert close(result.p, p)


@pytest.mark.parametrize("a,b,u,p", EXPECTED.MWU_ASYMPTOTIC)
def test_mann_whitney_asymptotic_matches_scipy(a, b, u, p):
    result = mann_whitney_u(a, b)
    assert result.method.startswith("정규근사")
    assert close(result.statistic, u)
    assert close(result.p, p)


@pytest.mark.parametrize("pre,post,stat,p", EXPECTED.WILCOXON_EXACT)
def test_wilcoxon_exact_matches_scipy(pre, post, stat, p):
    result = wilcoxon_signed_rank(pre, post)
    assert result.method == "정확분포"
    assert close(result.statistic, stat)
    assert close(result.p, p)


@pytest.mark.parametrize("pre,post,p", EXPECTED.WILCOXON_ASYMPTOTIC)
def test_wilcoxon_asymptotic_matches_scipy(pre, post, p):
    result = wilcoxon_signed_rank(pre, post)
    assert result.method.startswith("정규근사")
    assert close(result.p, p)


def test_mann_whitney_switches_to_asymptotic_when_cells_exceed_limit():
    a = [float(i) for i in range(25)]
    b = [float(i) + 0.5 for i in range(25)]
    assert 25 * 25 > MWU_EXACT_MAX_CELLS
    assert mann_whitney_u(a, b).method.startswith("정규근사")


def test_mann_whitney_all_identical_raises():
    with pytest.raises(ValueError):
        mann_whitney_u([5.0] * 6, [5.0] * 6)


def test_mann_whitney_perfect_separation_is_significant():
    result = mann_whitney_u([1.0, 2.0, 3.0, 4.0, 5.0], [10.0, 11.0, 12.0, 13.0])
    assert result.p < 0.02


def test_mann_whitney_u_statistic_is_for_first_sample():
    assert mann_whitney_u([1.0, 2.0], [3.0, 4.0]).statistic == 0.0
    assert mann_whitney_u([3.0, 4.0], [1.0, 2.0]).statistic == 4.0


def test_wilcoxon_drops_zero_differences():
    result = wilcoxon_signed_rank([1.0, 2.0, 3.0, 4.0, 5.0],
                                  [1.0, 3.0, 5.0, 7.0, 9.0])
    assert result.extra["버린0"] == 1.0
    assert result.n == 4


def test_wilcoxon_all_zero_differences_raises():
    with pytest.raises(ValueError):
        wilcoxon_signed_rank([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])


def test_wilcoxon_length_mismatch_raises():
    with pytest.raises(ValueError):
        wilcoxon_signed_rank([1.0], [1.0, 2.0])


# ----------------------------------------------------------------- 상관


@pytest.mark.parametrize("x,y,r,p", EXPECTED.PEARSON)
def test_pearson_matches_scipy(x, y, r, p):
    result = pearson_r(x, y)
    assert close(result.statistic, r)
    assert close(result.p, p)


@pytest.mark.parametrize("x,y,rho,p", EXPECTED.SPEARMAN)
def test_spearman_matches_scipy(x, y, rho, p):
    result = spearman_rho(x, y)
    assert close(result.statistic, rho)
    assert close(result.p, p)


def test_pearson_perfect_correlation():
    result = pearson_r([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0])
    assert close(result.statistic, 1.0, 1e-12)
    assert result.p == 0.0


def test_pearson_constant_variable_raises():
    with pytest.raises(ValueError):
        pearson_r([1.0, 1.0, 1.0, 1.0], [1.0, 2.0, 3.0, 4.0])


def test_pearson_requires_three_points():
    with pytest.raises(ValueError):
        pearson_r([1.0, 2.0], [1.0, 2.0])


def test_spearman_is_invariant_to_monotone_transform():
    x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    y = [2.0, 1.0, 4.0, 3.0, 6.0, 5.0]
    plain = spearman_rho(x, y)
    logged = spearman_rho([math.log(v) for v in x], [math.log(v) for v in y])
    assert close(plain.statistic, logged.statistic, 1e-12)
    assert close(plain.p, logged.p, 1e-12)


def test_rankdata_handles_ties_with_midranks():
    assert rankdata([10.0, 20.0, 20.0, 30.0]) == [1.0, 2.5, 2.5, 4.0]


def test_rankdata_is_stable_for_equal_values():
    assert rankdata([5.0, 5.0, 5.0]) == [2.0, 2.0, 2.0]


# ------------------------------------------------------------- 공변량 보정


@pytest.mark.parametrize("ya,ca,yb,cb,t,p,beta", EXPECTED.ANCOVA)
def test_ancova_matches_least_squares(ya, ca, yb, cb, t, p, beta):
    result = ancova_baseline(ya, ca, yb, cb)
    assert close(result.statistic, t)
    assert close(result.p, p)
    assert close(result.extra["보정된차이"], beta)


def test_ancova_group_coefficient_is_a_minus_b():
    ya = [12.0, 13.0, 14.0, 15.0]
    yb = [8.0, 9.0, 10.0, 11.0]
    cov = [20.0, 21.0, 22.0, 23.0]
    result = ancova_baseline(ya, cov, yb, cov)
    assert result.extra["보정된차이"] > 0


def test_ancova_constant_covariate_is_singular():
    with pytest.raises(SingularModel):
        ancova_baseline([1.0, 2.0, 3.0], [5.0] * 3, [2.0, 3.0, 4.0], [5.0] * 3)


def test_ancova_requires_enough_residual_df():
    with pytest.raises(ValueError):
        ancova_baseline([1.0, 2.0], [1.0, 3.0], [2.0], [2.0])


def test_quade_returns_finite_result():
    result = quade_rank_ancova([12.0, 13.0, 14.0, 15.0], [20.0, 21.0, 22.0, 23.0],
                               [8.0, 9.0, 10.0, 18.0], [19.0, 24.0, 25.0, 26.0])
    assert math.isfinite(result.statistic)
    assert 0.0 <= result.p <= 1.0
    assert result.df == 8 - 3


def test_quade_constant_covariate_is_singular():
    with pytest.raises(SingularModel):
        quade_rank_ancova([1.0, 2.0, 3.0], [7.0] * 3, [4.0, 5.0, 6.0], [7.0] * 3)


def test_quade_is_invariant_to_monotone_transform_of_outcome():
    ya, ca = [12.0, 13.0, 14.0, 25.0], [20.0, 21.0, 22.0, 23.0]
    yb, cb = [8.0, 9.0, 10.0, 18.0], [19.0, 24.0, 25.0, 26.0]
    plain = quade_rank_ancova(ya, ca, yb, cb)
    logged = quade_rank_ancova([math.log(v) for v in ya], ca,
                               [math.log(v) for v in yb], cb)
    assert close(plain.p, logged.p, 1e-12)


# ------------------------------------------------------------ 기초 통계


def test_mean_and_variance():
    assert close(mean([1.0, 2.0, 3.0, 4.0]), 2.5, 1e-15)
    assert close(variance([1.0, 2.0, 3.0, 4.0]), 5.0 / 3.0, 1e-15)
    assert close(stdev([1.0, 2.0, 3.0, 4.0]), math.sqrt(5.0 / 3.0), 1e-15)


def test_mean_of_empty_raises():
    with pytest.raises(ValueError):
        mean([])


def test_variance_needs_two_points():
    with pytest.raises(ValueError):
        variance([1.0])


def test_mean_uses_compensated_summation():
    values = [1e16, 1.0, -1e16, 1.0]
    assert close(mean(values), 0.5, 1e-12)
