"""QR least squares and the Gram cache, checked against exact rational OLS."""

import math
from random import Random

import pytest

from helpers import exact_ols, exact_ols_with_intercept, simple_slope
from medpath.linalg import GramCache, SingularDesignError, qr_lstsq


def _design(cols, n):
    return [[1.0] + [c[i] for c in cols] for i in range(n)]


def test_exact_fit_recovers_line_with_zero_residual():
    x = [0.0, 1.0, 2.0, 3.0, 4.0]
    y = [2.0 + 3.0 * xi for xi in x]
    res = qr_lstsq(_design([x], 5), y)
    assert res.beta[0] == pytest.approx(2.0, abs=1e-12)
    assert res.beta[1] == pytest.approx(3.0, abs=1e-12)
    assert res.rss == pytest.approx(0.0, abs=1e-20)


def test_slope_matches_textbook_formula():
    x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    y = [2.1, 3.9, 6.2, 7.8, 10.1, 12.2]
    res = qr_lstsq(_design([x], 6), y)
    assert res.beta[1] == pytest.approx(simple_slope(x, y), abs=1e-12)


def test_multiple_regression_matches_exact_rational_solution():
    rng = Random(4)
    n = 40
    x1 = [round(rng.gauss(0, 1), 3) for _ in range(n)]
    x2 = [round(rng.gauss(5, 2), 3) for _ in range(n)]
    x3 = [float(rng.randint(0, 1)) for _ in range(n)]
    y = [round(1.5 + 2 * a - 0.7 * b + 3 * c + rng.gauss(0, 0.5), 3)
         for a, b, c in zip(x1, x2, x3)]
    res = qr_lstsq(_design([x1, x2, x3], n), y)
    want = exact_ols_with_intercept([x1, x2, x3], y)
    for got, exp in zip(res.beta, want):
        assert got == pytest.approx(float(exp), rel=1e-11, abs=1e-11)


def test_xtx_inverse_is_the_real_inverse():
    rng = Random(9)
    n = 25
    x1 = [round(rng.gauss(0, 1), 3) for _ in range(n)]
    x2 = [round(rng.gauss(0, 3), 3) for _ in range(n)]
    X = _design([x1, x2], n)
    y = [round(rng.gauss(0, 1), 3) for _ in range(n)]
    res = qr_lstsq(X, y)
    p = 3
    xtx = [[sum(X[i][r] * X[i][c] for i in range(n)) for c in range(p)]
           for r in range(p)]
    prod = [[sum(xtx[i][k] * res.xtx_inv[k][j] for k in range(p)) for j in range(p)]
            for i in range(p)]
    for i in range(p):
        for j in range(p):
            assert prod[i][j] == pytest.approx(1.0 if i == j else 0.0, abs=1e-9)


def test_badly_scaled_columns_still_solve():
    """Column scaling matters: age in years next to a value of order 1e6."""
    rng = Random(3)
    n = 50
    small = [round(rng.gauss(50, 8), 3) for _ in range(n)]
    huge = [round(rng.gauss(2.5e6, 4e5), 1) for _ in range(n)]
    y = [0.3 * a + 2e-6 * b + rng.gauss(0, 0.1) for a, b in zip(small, huge)]
    res = qr_lstsq(_design([small, huge], n), y)
    want = exact_ols_with_intercept([small, huge], y)
    for got, exp in zip(res.beta, want):
        assert got == pytest.approx(float(exp), rel=1e-8, abs=1e-9)


def test_collinear_design_raises_named_error():
    n = 12
    x = [float(i) for i in range(n)]
    dup = [2.0 * v for v in x]
    y = [float(i % 3) for i in range(n)]
    with pytest.raises(SingularDesignError) as exc:
        qr_lstsq(_design([x, dup], n), y, ["(절편)", "x", "dup"])
    assert "dup" in str(exc.value) or "공선성" in str(exc.value)


def test_more_parameters_than_rows_raises():
    with pytest.raises(SingularDesignError):
        qr_lstsq([[1.0, 2.0, 3.0], [1.0, 4.0, 5.0]], [1.0, 2.0])


def test_empty_design_raises():
    with pytest.raises(SingularDesignError):
        qr_lstsq([], [])


def test_all_zero_column_raises():
    with pytest.raises(SingularDesignError):
        qr_lstsq([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]], [1.0, 2.0, 3.0])


# --------------------------------------------------------------------------
# GramCache
# --------------------------------------------------------------------------
def _cache_fixture(n=30, seed=17):
    rng = Random(seed)
    ones = [1.0] * n
    x = [float(i % 2) for i in range(n)]
    m = [round(2.0 * xi + rng.gauss(0, 1), 4) for xi in x]
    cov = [round(rng.gauss(50, 9), 2) for _ in range(n)]
    y = [round(1.0 + 0.5 * mi + 0.3 * xi + 0.01 * c + rng.gauss(0, 0.7), 4)
         for xi, mi, c in zip(x, m, cov)]
    return GramCache([ones, x, m, cov, y]), (ones, x, m, cov, y), n


def test_gram_cache_solve_matches_qr():
    cache, (ones, x, m, cov, y), n = _cache_fixture()
    acc = cache.full_acc()
    got = cache.solve(acc, [0, 1, 2, 3], 4)
    want = qr_lstsq(_design([x, m, cov], n), y).beta
    for g, w in zip(got, want):
        assert g == pytest.approx(w, rel=1e-9, abs=1e-10)


def test_gram_cache_submodel_solve_matches_qr():
    """Every sub-equation comes out of the same accumulator."""
    cache, (ones, x, m, cov, y), n = _cache_fixture()
    acc = cache.full_acc()
    got = cache.solve(acc, [0, 1, 3], 2)          # m ~ x + cov
    want = qr_lstsq(_design([x, cov], n), m).beta
    for g, w in zip(got, want):
        assert g == pytest.approx(w, rel=1e-9, abs=1e-10)


def test_weighted_acc_with_unit_weights_equals_full_acc():
    cache, _, n = _cache_fixture()
    w = [(i, 1.0) for i in range(n)]
    for a, b in zip(cache.weighted_acc(w), cache.full_acc()):
        assert a == pytest.approx(b, rel=1e-12, abs=1e-12)


def test_acc_minus_row_equals_refit_without_that_row():
    cache, (ones, x, m, cov, y), n = _cache_fixture()
    acc = cache.full_acc()
    drop = 7
    got = cache.solve(cache.acc_minus_row(acc, drop), [0, 1, 2, 3], 4)
    keep = [i for i in range(n) if i != drop]
    want = qr_lstsq(_design([[x[i] for i in keep], [m[i] for i in keep],
                             [cov[i] for i in keep]], n - 1),
                    [y[i] for i in keep]).beta
    for g, w in zip(got, want):
        assert g == pytest.approx(w, rel=1e-8, abs=1e-9)


def test_duplicated_rows_are_equivalent_to_integer_weights():
    """A weight of 2 must equal literally repeating the row."""
    cache, (ones, x, m, cov, y), n = _cache_fixture()
    weights = [(i, 2.0 if i < 5 else 1.0) for i in range(n)]
    got = cache.solve(cache.weighted_acc(weights), [0, 1, 2, 3], 4)
    idx = list(range(n)) + list(range(5))
    want = qr_lstsq(_design([[x[i] for i in idx], [m[i] for i in idx],
                             [cov[i] for i in idx]], len(idx)),
                    [y[i] for i in idx]).beta
    for g, w in zip(got, want):
        assert g == pytest.approx(w, rel=1e-8, abs=1e-9)


def test_gram_cache_returns_none_for_degenerate_subset():
    """A resample containing one X level only must fail softly, not crash."""
    cache, _, n = _cache_fixture()
    only_even = [(i, 1.0) for i in range(0, n, 2)]   # x is constant there
    assert cache.solve(cache.weighted_acc(only_even), [0, 1, 2, 3], 4) is None


def test_gram_cache_rejects_all_zero_column():
    with pytest.raises(SingularDesignError):
        GramCache([[1.0, 1.0], [0.0, 0.0]])
