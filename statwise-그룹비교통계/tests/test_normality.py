"""Shapiro-Wilk validated against scipy.stats.shapiro reference values."""

import pytest

from statwise.normality import shapiro_wilk


def approx(a, b, tol):
    return abs(a - b) <= tol


def test_matches_scipy_reference():
    # (data, expected_W, expected_p) from scipy 1.17
    cases = [
        ([2.1, 2.5, 3.0, 3.3, 3.8, 4.1, 4.5, 5.0, 5.2, 5.9, 6.1, 6.8],
         0.973741, 0.945780),
        ([1, 1, 1, 2, 2, 3, 4, 5, 8, 13, 21, 34], 0.727873, 0.001590),
        ([1.0, 2.0, 9.0], 0.842105, 0.219559),
        ([4.2, 5.1, 5.5, 6.0], 0.976667, 0.882220),
        ([4.2, 5.1, 5.5, 6.0, 7.8], 0.952953, 0.758260),
    ]
    for data, w_exp, p_exp in cases:
        w, p = shapiro_wilk(data)
        assert approx(w, w_exp, 1e-4), (data, w, w_exp)
        assert approx(p, p_exp, 1e-3), (data, p, p_exp)


def test_normal_data_not_rejected():
    data = [2.1, 2.5, 3.0, 3.3, 3.8, 4.1, 4.5, 5.0, 5.2, 5.9, 6.1, 6.8]
    _, p = shapiro_wilk(data)
    assert p > 0.05


def test_skewed_data_rejected():
    data = [1, 1, 1, 2, 2, 3, 4, 5, 8, 13, 21, 34]
    _, p = shapiro_wilk(data)
    assert p < 0.05


def test_errors():
    with pytest.raises(ValueError):
        shapiro_wilk([1.0, 2.0])  # n < 3
    with pytest.raises(ValueError):
        shapiro_wilk([5.0, 5.0, 5.0])  # zero variance
