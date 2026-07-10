"""Effect-size formulas checked by hand and against known conventions."""

import math

import pytest

from statwise import effects


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_cohens_d_handmath():
    a = [1.0, 2.0, 3.0, 4.0, 5.0]  # mean 3, var 2.5
    b = [3.0, 4.0, 5.0, 6.0, 7.0]  # mean 5, var 2.5
    es = effects.cohens_d(a, b, hedges=False)
    # pooled sd = sqrt(2.5) ; d = (3-5)/sqrt(2.5) = -2/1.5811 = -1.264911
    assert approx(es.value, -2.0 / math.sqrt(2.5))
    assert es.name == "Cohen's d"


def test_hedges_correction():
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [3.0, 4.0, 5.0, 6.0, 7.0]
    d = effects.cohens_d(a, b, hedges=False).value
    g = effects.cohens_d(a, b, hedges=True).value
    j = 1.0 - 3.0 / (4.0 * 10 - 9.0)
    assert approx(g, d * j)
    assert abs(g) < abs(d)  # correction shrinks toward 0


def test_cohens_d_ci_contains_point():
    a = [10, 12, 11, 13, 9, 14, 10, 12]
    b = [15, 14, 16, 13, 17, 15, 14, 16]
    es = effects.cohens_d(a, b, hedges=True)
    assert es.ci_low < es.value < es.ci_high


def test_rank_biserial_and_cliff_agree():
    a = [10, 12, 11, 13, 9, 14, 10, 12]
    b = [15, 14, 16, 13, 17, 15, 14, 16]
    rb = effects.rank_biserial(a, b)
    cd = effects.cliffs_delta(a, b)
    assert approx(rb.value, cd.value, 1e-9)


def test_cliffs_delta_extremes():
    # complete separation -> delta = -1 (all a < all b)
    assert approx(effects.cliffs_delta([1, 2, 3], [4, 5, 6]).value, -1.0)
    assert approx(effects.cliffs_delta([4, 5, 6], [1, 2, 3]).value, 1.0)
    # identical distributions -> 0
    assert approx(effects.cliffs_delta([1, 2, 3], [1, 2, 3]).value, 0.0)


def test_eta_squared():
    es = effects.eta_squared([[1, 2], [3, 4]], ss_between=24.0, ss_total=30.0)
    assert approx(es.value, 0.8)


def test_eta_squared_h_nonnegative():
    es = effects.eta_squared_h(h=27.2, n=36, k=3)
    # (27.2 - 3 + 1)/(36-3) = 25.2/33
    assert approx(es.value, 25.2 / 33.0)
    assert es.name == "eta-squared (H)"
    # clamps at 0
    assert effects.eta_squared_h(h=0.1, n=36, k=3).value == 0.0


def test_magnitude_labels():
    assert effects.EffectSize("d", 0.9).name == "d"
    assert effects.cohens_d([1, 2, 3, 4], [1, 2, 3, 4], hedges=False).magnitude \
        == "negligible"


def test_errors():
    with pytest.raises(ValueError):
        effects.cohens_d([1.0], [2.0, 3.0])
    with pytest.raises(ValueError):
        effects.eta_squared([], 1.0, 0.0)
