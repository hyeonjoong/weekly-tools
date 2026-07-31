"""Distribution functions vs published table values (and scipy when present)."""

import math

import pytest

from medpath.special import (betainc, chi2_sf, f_sf, gammainc_upper, norm_cdf,
                             norm_ppf, norm_sf, t_ppf, t_sf, t_sf_two_sided)


def test_norm_cdf_known_points():
    assert norm_cdf(0.0) == pytest.approx(0.5, abs=1e-15)
    # Standard reference values.
    assert norm_cdf(1.0) == pytest.approx(0.8413447460685429, abs=1e-14)
    assert norm_cdf(1.959963984540054) == pytest.approx(0.975, abs=1e-13)
    assert norm_cdf(-3.0) == pytest.approx(0.0013498980316301, abs=1e-15)
    assert norm_sf(1.644853626951472) == pytest.approx(0.05, abs=1e-13)


def test_norm_ppf_matches_table_and_round_trips():
    assert norm_ppf(0.975) == pytest.approx(1.959963984540054, abs=1e-11)
    assert norm_ppf(0.95) == pytest.approx(1.6448536269514722, abs=1e-11)
    assert norm_ppf(0.5) == pytest.approx(0.0, abs=1e-12)
    for p in [1e-8, 1e-4, 0.01, 0.2, 0.5, 0.8, 0.99, 1 - 1e-6]:
        assert norm_cdf(norm_ppf(p)) == pytest.approx(p, rel=1e-9, abs=1e-12)


def test_norm_ppf_rejects_out_of_range():
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            norm_ppf(bad)


def test_betainc_boundaries_and_symmetry():
    assert betainc(2.0, 3.0, 0.0) == 0.0
    assert betainc(2.0, 3.0, 1.0) == 1.0
    # I_x(a,b) = 1 - I_{1-x}(b,a)
    for a, b, x in [(0.5, 2.0, 0.3), (3.0, 7.0, 0.62), (10.0, 0.5, 0.9)]:
        assert betainc(a, b, x) == pytest.approx(1 - betainc(b, a, 1 - x), abs=1e-13)
    # I_x(1,1) = x
    assert betainc(1.0, 1.0, 0.37) == pytest.approx(0.37, abs=1e-13)


def test_t_tails_match_classic_table():
    # t_{0.975, df} critical values (Fisher's table).
    for df, crit in [(1, 12.706205), (5, 2.570582), (10, 2.228139),
                     (30, 2.042272), (120, 1.979930)]:
        assert t_sf_two_sided(crit, df) == pytest.approx(0.05, abs=2e-6)
        assert t_ppf(0.975, df) == pytest.approx(crit, abs=1e-5)
    assert t_sf(0.0, 10) == pytest.approx(0.5, abs=1e-14)
    assert t_sf_two_sided(0.0, 10) == pytest.approx(1.0, abs=1e-14)
    # symmetry
    assert t_sf(-1.3, 7) == pytest.approx(1 - t_sf(1.3, 7), abs=1e-13)


def test_t_with_huge_df_approaches_normal():
    assert t_sf(1.96, 5_000_000) == pytest.approx(norm_sf(1.96), abs=1e-6)


def test_f_tail_matches_table():
    # F_{0.95}(df1, df2) critical values.
    for df1, df2, crit in [(1, 10, 4.964603), (3, 10, 3.708265),
                           (5, 20, 2.710890), (2, 100, 3.087296)]:
        assert f_sf(crit, df1, df2) == pytest.approx(0.05, abs=2e-6)
    # F(1, df) tail equals the two-sided t tail with the same df.
    assert f_sf(4.0, 1, 12) == pytest.approx(t_sf_two_sided(2.0, 12), abs=1e-13)


def test_chi2_tail_matches_table():
    for df, crit in [(1, 3.841459), (2, 5.991465), (4, 9.487729), (10, 18.307038)]:
        assert chi2_sf(crit, df) == pytest.approx(0.05, abs=2e-6)
    assert chi2_sf(0.0, 3) == 1.0
    # chi2 with 2 df has closed form exp(-x/2)
    assert chi2_sf(2.5, 2) == pytest.approx(math.exp(-1.25), abs=1e-13)


def test_gammainc_upper_validates_input():
    with pytest.raises(ValueError):
        gammainc_upper(0.0, 1.0)
    with pytest.raises(ValueError):
        gammainc_upper(1.0, -1.0)


def test_t_ppf_validates_input():
    with pytest.raises(ValueError):
        t_ppf(0.0, 5)
    with pytest.raises(ValueError):
        t_ppf(0.5, 0)


scipy = pytest.importorskip("scipy.stats", reason="scipy not installed")


def test_crosscheck_scipy_normal():
    for x in [-4.0, -1.5, 0.0, 0.7, 2.3, 5.0]:
        assert norm_cdf(x) == pytest.approx(float(scipy.norm.cdf(x)), abs=1e-14)
    for p in [1e-6, 0.001, 0.05, 0.5, 0.95, 0.999]:
        assert norm_ppf(p) == pytest.approx(float(scipy.norm.ppf(p)), abs=1e-10)


def test_crosscheck_scipy_t_f_chi2():
    for df in [1, 2, 5, 13, 60, 500]:
        for t in [0.1, 1.0, 2.5, 6.0]:
            assert t_sf_two_sided(t, df) == pytest.approx(
                float(2 * scipy.t.sf(t, df)), rel=1e-10, abs=1e-14)
        assert t_ppf(0.975, df) == pytest.approx(float(scipy.t.ppf(0.975, df)), abs=1e-9)
    for df1 in [1, 3, 8]:
        for df2 in [4, 25, 200]:
            for f in [0.5, 2.0, 7.5]:
                assert f_sf(f, df1, df2) == pytest.approx(
                    float(scipy.f.sf(f, df1, df2)), rel=1e-10, abs=1e-14)
    for df in [1, 3, 12]:
        for x in [0.5, 4.0, 20.0]:
            assert chi2_sf(x, df) == pytest.approx(
                float(scipy.chi2.sf(x, df)), rel=1e-10, abs=1e-14)
