"""Distribution functions validated against reference values from scipy 1.17."""

import math

from agreestat import special as sp


def approx(a, b, tol=1e-8):
    return abs(a - b) <= tol


def test_norm_cdf_known():
    assert approx(sp.norm_cdf(0.0), 0.5)
    assert approx(sp.norm_cdf(1.96), 0.9750021048517795, 1e-10)
    assert approx(sp.norm_sf(1.96), 1 - 0.9750021048517795, 1e-10)


def test_norm_ppf_known():
    assert approx(sp.norm_ppf(0.975), 1.9599639845400545, 1e-10)
    assert approx(sp.norm_ppf(0.995), 2.5758293035489004, 1e-10)
    assert approx(sp.norm_ppf(0.5), 0.0, 1e-12)
    # round-trip
    for p in (0.01, 0.25, 0.5, 0.8, 0.999):
        assert approx(sp.norm_cdf(sp.norm_ppf(p)), p, 1e-10)


def test_t_two_sided_matches_scipy():
    cases = {
        (2.1, 10): 0.0620772442,
        (0.5, 5): 0.6382988716,
        (3.7, 25): 0.0010659105,
        (1.9, 8): 0.0939678964,
    }
    for (t, df), expect in cases.items():
        assert approx(sp.t_sf_two_sided(t, df), expect, 1e-8)


def test_t_ppf_known():
    assert approx(sp.t_ppf(0.975, 10), 2.22813885, 1e-6)
    for df in (5, 12, 30):
        for p in (0.025, 0.5, 0.975):
            assert approx(sp.t_cdf(sp.t_ppf(p, df), df), p, 1e-8)


def test_f_sf_matches_scipy():
    assert approx(sp.f_sf(3.5, 2, 20), 0.0497350221, 1e-8)
    assert approx(sp.f_sf(8.2, 4, 30), 0.0001360194, 1e-8)


def test_f_ppf_known_and_inverse():
    # scipy.stats.f.ppf reference values
    assert approx(sp.f_ppf(0.975, 5, 15), 3.5764153469, 1e-6)
    assert approx(sp.f_ppf(0.975, 15, 5), 6.4277281663, 1e-6)
    # fractional df (used by ICC(2,1) CI)
    assert approx(sp.f_ppf(0.975, 5, 4.785167), 7.4985869, 1e-4)
    for (d1, d2) in [(3, 9), (5, 15), (10, 40)]:
        for p in (0.05, 0.5, 0.95):
            assert approx(sp.f_cdf(sp.f_ppf(p, d1, d2), d1, d2), p, 1e-8)


def test_chi2_sf_matches_scipy():
    assert approx(sp.chi2_sf(5.99, 2), 0.0500366271, 1e-8)
    assert approx(sp.chi2_sf(10.0, 4), 0.040427682, 1e-8)


def test_betainc_bounds():
    assert sp.betainc(2, 3, 0.0) == 0.0
    assert sp.betainc(2, 3, 1.0) == 1.0
    assert approx(sp.betainc(2, 5, 0.3), 1 - sp.betainc(5, 2, 0.7), 1e-12)


def test_gammainc_complement():
    assert approx(sp.gammainc_lower(2.5, 3.0) + sp.gammainc_upper(2.5, 3.0), 1.0, 1e-12)
