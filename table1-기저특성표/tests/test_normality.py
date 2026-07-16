"""Shapiro-Wilk against scipy.stats.shapiro ground truth (offline).

Covers the three code paths in shapiro_wilk: the n==3 exact boundary, the
n<=11 polynomial regime, and the n>11 log regime.
"""

import pytest

from table1.normality import shapiro_wilk


def _approx(sample, w_exp, p_exp, wtol=1e-6, ptol=1e-6):
    w, p = shapiro_wilk(sample)
    assert abs(w - w_exp) < wtol, (w, w_exp)
    assert abs(p - p_exp) < ptol, (p, p_exp)


def test_n3_boundary():
    _approx([1.0, 2.0, 10.0], 0.832191780821918, 0.1939175214814532)


def test_small_regime_n_le_11():
    _approx([2, 4, 4, 4, 5], 0.8282725252518615, 0.13502259259489838)


def test_small_regime_rejects():
    _approx([1, 2, 3, 4, 5, 6, 7, 8, 9, 20],
            0.8213614758314228, 0.026320922322774998)


def test_large_regime_n_gt_11():
    _approx([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21],
            0.9668963633332522, 0.8757314438730024)


def test_large_regime_n20():
    _approx(list(range(1, 21)), 0.9603751832429884, 0.5513717457916771)


def test_p_in_unit_interval():
    for sample in ([1, 2, 3], list(range(50)), [0, 0, 0, 0, 1, 2, 3, 100]):
        _w, p = shapiro_wilk(sample)
        assert 0.0 <= p <= 1.0


def test_raises_on_too_few():
    with pytest.raises(ValueError):
        shapiro_wilk([1.0, 2.0])


def test_raises_on_zero_variance():
    with pytest.raises(ValueError):
        shapiro_wilk([5.0, 5.0, 5.0])


# --------------------------------------------------------------------------- #
# Large-n regime vs SciPy (values hardcoded from scipy.stats.shapiro, offline).
# These pin the n>11 ln(n) polynomial branch at n=100 and n=1000, plus the
# n=11 boundary of the small-n branch that was previously untested.
# --------------------------------------------------------------------------- #
def test_boundary_n11():
    _approx(list(range(1, 12)), 0.9683912804626188, 0.869842328207451)


def test_large_regime_n100():
    _approx(list(range(1, 101)), 0.9547247449577697, 0.0017217221937626645)


def test_large_regime_n1000_gaussian():
    import random
    random.seed(12345)
    g = [round(random.gauss(0, 1), 4) for _ in range(1000)]
    w, p = shapiro_wilk(g)
    # SciPy 1.17.1 on the identical seeded sample: W=0.9991137, p=0.9251073
    assert abs(w - 0.9991136651512775) < 1e-6
    assert abs(p - 0.9251072946724398) < 1e-5
