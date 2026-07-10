"""Distribution CDFs validated against reference values from scipy 1.17."""

import math

from statwise import special as sp


def approx(a, b, tol=1e-8):
    return abs(a - b) <= tol


def test_norm_cdf_known():
    assert approx(sp.norm_cdf(0.0), 0.5)
    assert approx(sp.norm_cdf(1.96), 0.9750021048517795, 1e-10)
    assert approx(sp.norm_sf(1.96), 1 - 0.9750021048517795, 1e-10)


def test_t_two_sided_matches_scipy():
    # ss.t.sf(|t|, df) * 2
    cases = {
        (2.1, 10): 0.0620772442,
        (0.5, 5): 0.6382988716,
        (3.7, 25): 0.0010659105,
        (1.9, 8): 0.0939678964,
    }
    for (t, df), expect in cases.items():
        assert approx(sp.t_sf_two_sided(t, df), expect, 1e-8)


def test_f_sf_matches_scipy():
    assert approx(sp.f_sf(3.5, 2, 20), 0.0497350221, 1e-8)
    assert approx(sp.f_sf(8.2, 4, 30), 0.0001360194, 1e-8)


def test_chi2_sf_matches_scipy():
    assert approx(sp.chi2_sf(5.99, 2), 0.0500366271, 1e-8)
    assert approx(sp.chi2_sf(10.0, 4), 0.040427682, 1e-8)


def test_t_ppf_inverse():
    for df in (5, 10, 30):
        for p in (0.025, 0.5, 0.975):
            q = sp.t_ppf(p, df)
            assert approx(sp.t_cdf(q, df), p, 1e-8)


def test_t_ppf_known():
    assert approx(sp.t_ppf(0.975, 10), 2.22813885, 1e-6)


def test_betainc_bounds():
    assert sp.betainc(2, 3, 0.0) == 0.0
    assert sp.betainc(2, 3, 1.0) == 1.0
    # symmetry: I_x(a,b) = 1 - I_{1-x}(b,a)
    assert approx(sp.betainc(2, 5, 0.3), 1 - sp.betainc(5, 2, 0.7), 1e-12)


def test_gammainc_complement():
    assert approx(sp.gammainc_lower(2.5, 3.0) + sp.gammainc_upper(2.5, 3.0), 1.0, 1e-12)
