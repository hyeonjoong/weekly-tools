"""Distribution CDFs against known reference values (offline)."""

import math

from table1.special import (
    betainc,
    chi2_sf,
    f_sf,
    gammainc_upper,
    norm_cdf,
    norm_sf,
    t_cdf,
    t_ppf,
    t_sf_two_sided,
)


def test_norm_cdf_known():
    assert abs(norm_cdf(0.0) - 0.5) < 1e-12
    assert abs(norm_cdf(1.959963984540054) - 0.975) < 1e-9
    assert abs(norm_cdf(-1.959963984540054) - 0.025) < 1e-9


def test_t_two_sided_known():
    # t=2.228139 with df=10 -> two-sided p = 0.05 (t_{0.975,10}).
    assert abs(t_sf_two_sided(2.2281388519649385, 10) - 0.05) < 1e-9


def test_chi2_sf_known():
    # chi-square 3.841459 with df=1 -> upper tail 0.05.
    assert abs(chi2_sf(3.841458820694124, 1) - 0.05) < 1e-9
    # df=1, x=6.6667 -> ~0.00982 (used in a chi-square example test).
    assert abs(chi2_sf(6.666666666666667, 1) - 0.009823274) < 1e-6


def test_f_sf_known():
    # Ground-truth values from scipy.stats.f.sf.
    assert abs(f_sf(4.0, 1, 10) - 0.07338803477074037) < 1e-9
    assert abs(f_sf(2.5, 3, 20) - 0.08884375193768922) < 1e-9
    assert abs(f_sf(100.0, 2, 20) - 3.855432894295319e-11) < 1e-16


def test_betainc_known():
    # scipy.special.betainc
    assert abs(betainc(2.0, 3.0, 0.4) - 0.5247999999999999) < 1e-12
    assert abs(betainc(0.5, 0.5, 0.3) - 0.36901011956554536) < 1e-12


def test_norm_sf_and_t_cdf_ppf():
    assert abs(norm_sf(1.959963984540054) - 0.025) < 1e-9
    assert abs(t_cdf(1.5, 8) - 0.9139983540240444) < 1e-9
    assert abs(t_cdf(-2.0, 15) - 0.031972503642360074) < 1e-9
    # t_ppf inverts t_cdf.
    assert abs(t_ppf(0.975, 10) - 2.228138851986274) < 1e-9


def test_extreme_tail_no_underflow():
    """Regression: gammainc_upper must not collapse to 0.0 in the deep tail.

    The old ``1 - gammainc_lower`` form lost all significance once P rounded to
    1.0, returning exactly 0.0 for large chi-square. Values from scipy.stats.
    """
    assert abs(chi2_sf(100, 5) - 5.285148360943219e-20) < 1e-25
    assert abs(chi2_sf(200, 10) - 1.6139305336977317e-37) < 1e-42
    assert chi2_sf(300, 3) > 0.0  # was 0.0 before the fix
    assert abs(gammainc_upper(2.5, 40.0) - 8.391825114831597e-16) < 1e-21


def test_sf_monotone():
    prev = 1.0
    for x in [0.5, 1.0, 2.0, 4.0, 8.0]:
        cur = chi2_sf(x, 3)
        assert cur < prev
        prev = cur
