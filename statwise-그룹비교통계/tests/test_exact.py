"""Exact rank-test null distributions validated against scipy 1.17 (method='exact')."""

import math

import pytest

from statwise import exact
from statwise.tests_stat import _rankdata


def approx(a, b, tol=1e-12):
    return abs(a - b) <= tol


def _u1(a, b):
    combined = list(a) + list(b)
    ranks = _rankdata(combined)
    r1 = sum(ranks[:len(a)])
    return r1 - len(a) * (len(a) + 1) / 2.0


def test_mwu_pmf_sums_to_one():
    for n1, n2 in [(3, 3), (4, 5), (6, 6), (2, 9)]:
        pmf = exact.mannwhitney_u_pmf(n1, n2)
        assert len(pmf) == n1 * n2 + 1
        assert approx(sum(pmf), 1.0, 1e-12)
        assert all(p >= 0 for p in pmf)


def test_mwu_pmf_symmetric():
    # U distribution is symmetric about n1*n2/2
    pmf = exact.mannwhitney_u_pmf(4, 5)
    assert all(approx(pmf[u], pmf[-1 - u]) for u in range(len(pmf)))


def test_mwu_exact_matches_scipy_reference():
    # scipy.stats.mannwhitneyu(a, b, method='exact') two-sided p-values
    cases = [
        ([1., 3., 5., 7.], [2., 4., 6., 8., 9.], 0.4126984126984127),
        ([1., 2., 3., 4., 5.], [6., 7., 8., 9., 10.], 0.007936507936507936),
        ([1., 4., 6., 9.], [2., 3., 7., 8.], 1.0),
        (list(range(8)), list(range(100, 107)), 0.0003108003108003108),
    ]
    for a, b, expect in cases:
        p = exact.mannwhitney_exact_p(_u1(a, b), len(a), len(b))
        assert approx(p, expect, 1e-10), (a, b, p, expect)


def test_signed_rank_pmf_sums_to_one():
    for n in [1, 5, 10, 15]:
        pmf = exact.signed_rank_pmf(n)
        assert len(pmf) == n * (n + 1) // 2 + 1
        assert approx(sum(pmf), 1.0, 1e-12)


def test_signed_rank_exact_matches_scipy():
    # all-positive differences, n=10 -> W=0, scipy exact p = 2/2^10... = 0.001953125
    assert approx(exact.signed_rank_exact_p(0, 10), 0.001953125, 1e-12)
    # clean no-tie case (scipy method='exact' W=11, p=0.3828125)
    assert approx(exact.signed_rank_exact_p(11, 8), 0.3828125, 1e-10)


def test_signed_rank_full_range_p_is_one():
    # statistic at the mean -> maximal p (capped at 1)
    n = 6
    max_w = n * (n + 1) // 2
    assert exact.signed_rank_exact_p(max_w // 2, n) <= 1.0
    assert exact.signed_rank_exact_p(0, 1) == pytest.approx(1.0)


def test_mwu_degenerate_sizes():
    # zero-size group -> all mass at U=0
    pmf = exact.mannwhitney_u_pmf(0, 5)
    assert pmf[0] == 1.0 and sum(pmf) == 1.0
