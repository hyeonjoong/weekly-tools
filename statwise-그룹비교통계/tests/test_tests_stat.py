"""Group-comparison tests validated against scipy reference values + hand math."""

import math

import pytest

from statwise import exact
from statwise import tests_stat as T

A = [10.5, 12.1, 11.3, 13.0, 9.8, 14.2, 10.0, 12.5, 11.1, 13.7, 9.2, 12.9,
     10.7, 11.9, 13.3]
B = [13.1, 14.5, 12.8, 15.0, 11.9, 16.1, 13.4, 14.9, 12.2, 15.5, 13.8, 14.0,
     16.5, 12.6, 15.2, 13.9, 14.3, 15.8]


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_mean_variance_handmath():
    x = [2.0, 4.0, 6.0, 8.0]
    assert approx(T.mean(x), 5.0)
    # sample variance: sum((x-5)^2)=9+1+1+9=20; /(n-1)=20/3
    assert approx(T.variance(x), 20.0 / 3.0)


def test_students_t_handmath():
    # two groups with known means/vars
    a = [1.0, 2.0, 3.0, 4.0, 5.0]  # mean 3, var 2.5
    b = [3.0, 4.0, 5.0, 6.0, 7.0]  # mean 5, var 2.5
    r = T.students_t(a, b)
    # pooled var = 2.5, se = sqrt(2.5*(1/5+1/5)) = sqrt(1.0) = 1.0; t = (3-5)/1 = -2
    assert approx(r.statistic, -2.0)
    assert r.df == 8
    assert r.kind == "student"


def test_students_t_scipy_ref():
    r = T.students_t(A, B)
    assert approx(r.statistic, -4.881040539687108, 1e-6)
    assert approx(r.pvalue, 3.0196238358124152e-05, 1e-9)


def test_welch_t_scipy_ref():
    r = T.welch_t(A, B)
    assert approx(r.statistic, -4.82896419167477, 1e-6)
    assert approx(r.df, 28.420789154967615, 1e-4)
    assert approx(r.pvalue, 4.2754201360674104e-05, 1e-9)


def test_mann_whitney_scipy_ref():
    # A and B share the value 11.9, so 'auto' uses the asymptotic path; assert
    # it explicitly to pin the reference regardless of the tie heuristic.
    r = T.mann_whitney_u(A, B, method="asymptotic")
    # scipy mannwhitneyu(A,B, method='asymptotic', use_continuity=True)
    assert approx(r.u1, 31.5, 1e-9)
    assert approx(r.pvalue, 0.0001958702856142994, 1e-6)
    assert r.method == "asymptotic"


def test_mann_whitney_exact_small_no_ties():
    # no ties + small -> auto selects exact, matching scipy method='exact'
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [6.0, 7.0, 8.0, 9.0, 10.0]
    r = T.mann_whitney_u(a, b)
    assert r.method == "exact"
    assert approx(r.pvalue, 0.007936507936507936, 1e-10)


def test_mann_whitney_exact_forced_falls_back_on_ties():
    # ties present -> forced 'exact' must not lie; falls back to asymptotic
    a = [1.0, 2.0, 2.0, 3.0]
    b = [2.0, 3.0, 4.0, 5.0]
    r = T.mann_whitney_u(a, b, method="exact")
    assert r.method == "asymptotic"


def test_welch_anova_ref():
    g1 = [2.0, 4.0, 6.0, 8.0, 10.0]
    g2 = [3.0, 3.5, 4.0, 4.5, 5.0]
    g3 = [10.0, 20.0, 30.0, 40.0, 50.0]
    r = T.welch_anova([g1, g2, g3])
    assert approx(r.statistic, 6.820792194515976, 1e-6)
    assert r.df_between == 2
    assert approx(r.df_within, 5.658382587649298, 1e-6)
    assert approx(r.pvalue, 0.031075997836605765, 1e-9)


def test_welch_anova_zero_variance_rejected():
    with pytest.raises(ValueError):
        T.welch_anova([[5.0, 5.0, 5.0], [1.0, 2.0, 3.0]])


def test_t_tests_reject_zero_variance():
    # both groups constant -> SE = 0 -> undefined (must raise, not divide by 0)
    with pytest.raises(ValueError):
        T.students_t([5.0, 5.0, 5.0], [9.0, 9.0, 9.0])
    with pytest.raises(ValueError):
        T.welch_t([5.0, 5.0, 5.0], [9.0, 9.0, 9.0])


def test_mann_whitney_auto_uses_asymptotic_over_size_cap():
    # tie-free but larger than the exact cap -> auto must pick asymptotic
    n = exact.MWU_EXACT_MAX_N + 5
    a = [float(i) for i in range(n)]
    b = [float(i) + 0.5 for i in range(n)]  # interleaved, no ties
    r = T.mann_whitney_u(a, b)
    assert r.method == "asymptotic"


def test_mann_whitney_with_ties():
    at = [1, 2, 2, 3, 3, 3, 4, 5]
    bt = [2, 3, 3, 4, 4, 5, 6, 6]
    r = T.mann_whitney_u(at, bt)
    assert approx(r.u1, 16.5, 1e-9)
    assert approx(r.pvalue, 0.1071234, 1e-6)


def test_anova_handmath():
    # three groups, easy numbers
    g1 = [1.0, 2.0, 3.0]  # mean 2
    g2 = [3.0, 4.0, 5.0]  # mean 4
    g3 = [5.0, 6.0, 7.0]  # mean 6
    r = T.one_way_anova([g1, g2, g3])
    # grand mean 4; SSB = 3*((2-4)^2+(4-4)^2+(6-4)^2)=3*8=24
    # SSW = each group var: sum((x-mean)^2)=2 per group *3 =6
    assert approx(r.ss_between, 24.0)
    assert approx(r.ss_within, 6.0)
    assert r.df_between == 2 and r.df_within == 6
    # F = (24/2)/(6/6) = 12
    assert approx(r.statistic, 12.0)


def test_anova_scipy_ref():
    g1 = [3, 5, 4, 6, 2, 5, 4, 3, 7, 5, 4, 6]
    g2 = [6, 8, 7, 9, 5, 8, 7, 6, 10, 8, 7, 9]
    g3 = [9, 11, 10, 12, 8, 11, 10, 9, 13, 11, 10, 12]
    r = T.one_way_anova([g1, g2, g3])
    assert approx(r.statistic, 51.6521739, 1e-5)
    assert approx(r.pvalue, 6.85598e-11, 1e-14)


def test_kruskal_scipy_ref():
    g1 = [3, 5, 4, 6, 2, 5, 4, 3, 7, 5, 4, 6]
    g2 = [6, 8, 7, 9, 5, 8, 7, 6, 10, 8, 7, 9]
    g3 = [9, 11, 10, 12, 8, 11, 10, 9, 13, 11, 10, 12]
    r = T.kruskal_wallis([g1, g2, g3])
    assert approx(r.statistic, 27.206060606060607, 1e-4)
    assert approx(r.pvalue, 1.2367416937777957e-06, 1e-9)
    assert r.df == 2


def test_levene_scipy_ref():
    g1 = [10.5, 12.1, 11.3, 13.0, 9.8, 14.2]
    g2 = [13.1, 14.5, 12.8, 25.0, 11.9, 6.1]
    r = T.levene([g1, g2])
    # scipy.stats.levene(g1,g2, center='median')
    assert approx(r.statistic, 1.3818148961605825, 1e-5)
    assert approx(r.pvalue, 0.2670205911757835, 1e-6)


def test_anova_zero_within_variance_perfect_separation():
    # zero within-group variance but different means -> perfect separation
    r = T.one_way_anova([[5.0, 5.0], [9.0, 9.0]])
    assert math.isinf(r.statistic)
    assert r.pvalue == 0.0


def test_levene_zero_variance_groups_is_nan():
    # two groups each with zero variance -> equal-variance undefined (0/0)
    r = T.levene([[5.0, 5.0, 5.0], [9.0, 9.0, 9.0]])
    assert math.isnan(r.statistic)
    assert math.isnan(r.pvalue)


def test_errors():
    with pytest.raises(ValueError):
        T.students_t([1.0], [2.0, 3.0])
    with pytest.raises(ValueError):
        T.one_way_anova([[1.0, 2.0]])
