"""Independent cross-checks so the from-scratch numerics aren't only self-checked.

The forward/MDES round-trip tests invert the SAME `_ncf_cdf`, so a subtle bias
would hide. These tests give the non-central F machinery an *external* basis:
scipy when available (skipped otherwise), plus closed-form hand computations
that don't touch `_ncf_cdf` at all.
"""
import math

import pytest

from paperforge import power


# --- Closed-form checks that bypass _ncf_cdf entirely -----------------------

def test_two_group_mdes_closed_form():
    # Independent recomputation (not a loose bound): d = (za+zb)/sqrt(N*p*(1-p)).
    za, zb = 1.959963985, 0.841621234
    for n in (40, 126, 300):
        expected = (za + zb) / math.sqrt(n * 0.5 * 0.5)
        assert math.isclose(power.mdes_two_group(n), expected, rel_tol=1e-12)


def test_paired_mdes_closed_form():
    za, zb = 1.959963985, 0.841621234
    for n in (20, 33, 200):
        expected = (za + zb) / math.sqrt(n - 1)
        assert math.isclose(power.mdes_paired(n), expected, rel_tol=1e-12)


def test_correlation_mdes_closed_form():
    za, zb = 1.959963985, 0.841621234
    for n in (50, 85, 400):
        expected = math.tanh((za + zb) / math.sqrt(n - 3))
        assert math.isclose(power.mdes_correlation(n), expected, rel_tol=1e-12)


def test_unbalanced_two_group_inflation_factor():
    # A 30/70 split must inflate N by 1/(4*0.3*0.7) ≈ 1.190 over balanced.
    bal = power.required_total_n({"type": "two_group", "d": 0.5})
    unb = power.required_total_n({"type": "two_group", "d": 0.5, "allocation": 0.3})
    assert unb > bal
    assert math.isclose(unb / bal, 1.0 / (4 * 0.3 * 0.7), rel_tol=0.03)


# --- scipy oracle (skipped if scipy absent) ---------------------------------

def test_ncf_cdf_matches_scipy_wide_grid():
    scipy_stats = pytest.importorskip("scipy.stats")
    max_err = 0.0
    for d1 in (1, 3, 5, 10):
        for d2 in (1, 2, 5, 30, 100):
            for lam in (0.0, 5.0, 50.0, 500.0, 2000.0, 9800.0, 20000.0, 120000.0):
                fcrit = power._f_quantile(0.95, d1, d2)
                for x in (0.2 * fcrit, fcrit, 3.0 * fcrit):
                    got = power._ncf_cdf(x, d1, d2, lam)
                    exp = scipy_stats.ncf.cdf(x, d1, d2, lam)
                    max_err = max(max_err, abs(got - exp))
    assert max_err < 1e-8, f"max abs err vs scipy = {max_err}"


def test_ncf_cdf_midregime_large_lambda_anchor():
    # The value that a naive j=0 truncation would corrupt: large lambda, CDF not
    # tiny. Anchored to scipy (independent of our summation).
    scipy_stats = pytest.importorskip("scipy.stats")
    for lam in (500.0, 2000.0, 12000.0):
        d1, d2 = 3, 40
        x = power._f_quantile(0.95, d1, d2) * (1 + lam / 500.0)
        got = power._ncf_cdf(x, d1, d2, lam)
        exp = scipy_stats.ncf.cdf(x, d1, d2, lam)
        assert math.isclose(got, exp, abs_tol=1e-9)


def test_n_for_regression_matches_scipy_power():
    # Verify the returned N (and N-1) straddle the target power using scipy's
    # ncf, i.e. our forward search is anchored to an external CDF.
    scipy_stats = pytest.importorskip("scipy.stats")
    for f2, k in [(0.15, 1), (0.15, 3), (0.15, 5), (0.02, 1), (0.35, 3)]:
        n = power.n_for_regression(f2, k)
        d2 = n - k - 1
        fcrit = scipy_stats.f.ppf(0.95, k, d2)
        pw = 1 - scipy_stats.ncf.cdf(fcrit, k, d2, f2 * n)
        assert pw >= 0.80
        if n - 1 > k + 1:
            d2m = n - 1 - k - 1
            fcm = scipy_stats.f.ppf(0.95, k, d2m)
            pwm = 1 - scipy_stats.ncf.cdf(fcm, k, d2m, f2 * (n - 1))
            assert pwm < 0.80
