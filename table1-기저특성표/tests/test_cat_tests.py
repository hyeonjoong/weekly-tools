"""Categorical tests: chi-square and Fisher exact against known values."""

import math

import pytest

from table1.cat_tests import (
    chi_square,
    expected_counts,
    fisher_exact_2x2,
    min_expected,
)


def test_chi_square_statistic():
    # [[10,20],[20,10]] with uniform 15 expected -> chi2 = 6.6667, df=1.
    res = chi_square([[10, 20], [20, 10]])
    assert res.df == 1
    assert abs(res.statistic - 6.666666666666667) < 1e-9
    assert abs(res.min_expected - 15.0) < 1e-9
    assert abs(res.pvalue - 0.009823274) < 1e-6


def test_expected_counts():
    exp = expected_counts([[10, 20], [20, 10]])
    assert all(abs(c - 15.0) < 1e-9 for row in exp for c in row)


def test_chi_square_drops_empty_rows():
    # An all-zero level must not add degrees of freedom.
    res = chi_square([[5, 7], [0, 0], [8, 6]])
    assert res.df == 1  # 2 non-empty rows, 2 cols -> (2-1)(2-1)


def test_chi_square_drops_empty_columns():
    # An all-zero GROUP (column) must be dropped too, matching the collapsed
    # table exactly. SciPy chi2_contingency([[5,7],[8,6]], correction=False)
    # -> stat 0.6190476190476191, p 0.43140141694717793.
    res = chi_square([[5, 0, 7], [8, 0, 6]])
    assert res.df == 1
    assert abs(res.statistic - 0.6190476190476191) < 1e-9
    assert abs(res.pvalue - 0.43140141694717793) < 1e-9
    ref = chi_square([[5, 7], [8, 6]])
    assert abs(res.statistic - ref.statistic) < 1e-12
    assert abs(res.pvalue - ref.pvalue) < 1e-12


def test_chi_square_requires_2x2_min():
    with pytest.raises(ValueError):
        chi_square([[5, 3]])  # single row


def test_fisher_tea_tasting():
    # Fisher's classic lady-tasting-tea 2x2 -> two-sided p = 0.4857142857.
    res = fisher_exact_2x2([[3, 1], [1, 3]])
    assert abs(res.pvalue - 0.4857142857142857) < 1e-9
    assert abs(res.oddsratio - 9.0) < 1e-9


def test_fisher_symmetric_and_bounded():
    # p-value is invariant to swapping columns (and rows) of a 2x2.
    for tab in ([[8, 2], [1, 5]], [[0, 10], [9, 1]], [[7, 7], [7, 7]]):
        (a, b), (c, d) = tab
        p = fisher_exact_2x2(tab).pvalue
        assert 0.0 <= p <= 1.0
        assert abs(p - fisher_exact_2x2([[b, a], [d, c]]).pvalue) < 1e-12
        assert abs(p - fisher_exact_2x2([[c, d], [a, b]]).pvalue) < 1e-12


def test_fisher_known_pvalue():
    # scipy.stats.fisher_exact([[8,2],[1,5]], alternative='two-sided') -> 0.03497...
    res = fisher_exact_2x2([[8, 2], [1, 5]])
    assert abs(res.pvalue - 0.034965034965034975) < 1e-9
    assert abs(res.oddsratio - 20.0) < 1e-9


def test_fisher_zero_margin():
    res = fisher_exact_2x2([[0, 0], [5, 4]])
    assert res.pvalue == 1.0
    assert math.isnan(res.oddsratio)


def test_min_expected_small():
    assert min_expected([[1, 9], [9, 1]]) == 5.0
