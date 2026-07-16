"""Direct unit tests for the group-comparison tests.

Ground-truth statistics/p-values were computed with SciPy (ttest_ind,
mannwhitneyu asymptotic+continuity, f_oneway, kruskal, levene center='median')
and are hard-coded here so the suite stays fully offline.
"""

import math

import pytest

from table1.tests_stat import (
    kruskal_wallis,
    levene,
    mann_whitney_u,
    one_way_anova,
    students_t,
    welch_t,
    variance,
)


def test_students_t_equal_var():
    r = students_t([10, 11, 12, 13, 14, 15, 16, 17],
                   [12, 13, 14, 15, 16, 17, 18, 19])
    assert abs(r.statistic - (-1.6329931618554523)) < 1e-9
    assert abs(r.pvalue - 0.12475042359839987) < 1e-9
    assert r.df == 14.0 and r.kind == "student"


def test_welch_t_unequal_var():
    r = welch_t([1, 2, 3, 4, 5], [10, 12, 14, 16, 18, 20, 22])
    assert abs(r.statistic - (-7.305369330337213)) < 1e-9
    assert abs(r.pvalue - 8.151393524738383e-05) < 1e-12
    assert abs(r.df - 8.037105751391467) < 1e-9


def test_mann_whitney_with_ties():
    r = mann_whitney_u([1, 2, 2, 3, 4], [2, 3, 3, 4, 5])
    assert abs(r.u1 - 6.5) < 1e-9
    assert abs(r.pvalue - 0.23736860507756152) < 1e-9


def test_mann_whitney_all_identical():
    # Zero variance in ranks -> z=0, p=1.0 (documented degenerate path).
    r = mann_whitney_u([5, 5, 5], [5, 5, 5])
    assert r.zscore == 0.0
    assert r.pvalue == 1.0


def test_one_way_anova_three_groups():
    r = one_way_anova([[1, 2, 3, 4, 5], [3, 4, 5, 6, 7], [5, 6, 7, 8, 9]])
    assert abs(r.statistic - 8.0) < 1e-9
    assert abs(r.pvalue - 0.0061963977594369675) < 1e-9
    assert r.df_between == 2.0 and r.df_within == 12.0


def test_kruskal_with_ties():
    r = kruskal_wallis([[1, 2, 2, 3], [2, 3, 4, 4], [3, 3, 5, 6]])
    assert abs(r.statistic - 5.327490774907753) < 1e-9
    assert abs(r.pvalue - 0.06968672855485203) < 1e-9


def test_levene_median_equal():
    # Identical spreads -> statistic 0, p 1.
    r = levene([[1, 2, 3, 4, 5], [3, 4, 5, 6, 7], [5, 6, 7, 8, 9]])
    assert abs(r.statistic) < 1e-12
    assert abs(r.pvalue - 1.0) < 1e-12


def test_levene_median_unequal():
    r = levene([[20, 21, 22, 23, 24, 25, 26, 27, 28, 29],
                [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]])
    assert abs(r.pvalue - 0.00016020103368554375) < 1e-9
    assert r.pvalue < 0.05  # correctly flags unequal variance


def test_variance_ddof_guard():
    with pytest.raises(ValueError):
        variance([1.0], ddof=1)


def test_too_few_observations():
    with pytest.raises(ValueError):
        students_t([1.0], [2.0, 3.0])
    with pytest.raises(ValueError):
        welch_t([1.0], [2.0, 3.0])
