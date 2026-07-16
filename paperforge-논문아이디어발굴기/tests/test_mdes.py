"""Sensitivity / minimum-detectable-effect (MDES) tests — hand-checked inverses."""
import math

import pytest

from paperforge import power


def test_mdes_correlation_inverts_required_n():
    # At the required N for r=0.30, the MDES must round back to ~0.30.
    n = power.n_for_correlation(0.30)  # 85
    r = power.mdes_correlation(n)
    assert 0.29 < r < 0.31
    # Closed-form check: r = tanh((za+zb)/sqrt(n-3)).
    za, zb = 1.959963985, 0.841621234
    expected = math.tanh((za + zb) / math.sqrt(n - 3))
    assert math.isclose(r, expected, rel_tol=1e-9)


def test_mdes_two_group_inverts_required_n():
    total = power.required_total_n({"type": "two_group", "d": 0.5})  # 126
    d = power.mdes_two_group(total)
    assert 0.49 < d < 0.51


def test_mdes_paired_inverts_required_n():
    n = power.n_for_paired(0.5)  # 33
    d = power.mdes_paired(n)
    assert 0.49 < d < 0.51


def test_mdes_regression_inverts_required_n():
    for k in (1, 3, 5):
        n = power.n_for_regression(0.15, k)
        f2 = power.mdes_regression(n, k)
        # n is the ceil, so the exact detectable f2 is slightly <= 0.15.
        assert 0.14 < f2 <= 0.152


def test_mdes_monotone_in_n():
    # More subjects -> smaller detectable effect, every metric.
    assert power.mdes_correlation(200) < power.mdes_correlation(50)
    assert power.mdes_two_group(400) < power.mdes_two_group(40)
    assert power.mdes_paired(200) < power.mdes_paired(20)
    assert power.mdes_regression(400, 3) < power.mdes_regression(40, 3)


def test_mdes_higher_power_needs_larger_effect():
    # At fixed N, demanding 0.90 power raises the smallest detectable effect.
    assert power.mdes_correlation(90, power=0.90) > power.mdes_correlation(90)
    assert power.mdes_regression(90, 3, power=0.90) > power.mdes_regression(90, 3)


def test_mdes_guards_small_samples():
    with pytest.raises(ValueError):
        power.mdes_correlation(3)
    with pytest.raises(ValueError):
        power.mdes_two_group(3)
    with pytest.raises(ValueError):
        power.mdes_paired(1)
    with pytest.raises(ValueError):
        power.mdes_regression(4, 3)  # need n >= k + 2 = 5


def test_mdes_unsupported_alpha_raises():
    with pytest.raises(ValueError):
        power.mdes_correlation(90, alpha=0.123)


def test_detectable_effect_dispatch():
    assert power.detectable_effect({"type": "correlation", "r": 0.3}, 90)["metric"] == "r"
    assert power.detectable_effect({"type": "two_group", "d": 0.5}, 90)["metric"] == "d"
    assert power.detectable_effect({"type": "paired", "d": 0.5}, 90)["metric"] == "d_z"
    reg = power.detectable_effect({"type": "regression", "f2": 0.15, "k": 3}, 90)
    assert reg["metric"] == "f2"


def test_detectable_effect_none_cases():
    # Unknown N, exploratory design, or sub-minimum N all yield None (not error).
    assert power.detectable_effect({"type": "correlation", "r": 0.3}, None) is None
    assert power.detectable_effect({"type": "exploratory"}, 90) is None
    assert power.detectable_effect({"type": "correlation", "r": 0.3}, 3) is None
    assert power.detectable_effect({"type": "regression", "f2": 0.15, "k": 5}, 6) is None


def test_detectable_effect_unknown_type_raises():
    with pytest.raises(ValueError):
        power.detectable_effect({"type": "nonsense"}, 90)


def test_ncf_cdf_accurate_at_large_lambda():
    # Regression guard for the Poisson-truncation bug: at large lambda the
    # non-central F CDF must stay accurate (mode-centered summation), not
    # collapse toward 0. Cross-check monotonicity and a hand anchor.
    # CDF must be non-increasing in lambda at fixed x (more noncentrality
    # shifts mass right, lowering P(F<=x)).
    x, d1, d2 = 50.0, 5.0, 1.0
    vals = [power._ncf_cdf(x, d1, d2, lam) for lam in (0, 100, 5000, 9800, 20000)]
    assert all(vals[i] >= vals[i + 1] - 1e-12 for i in range(len(vals) - 1))
    # At lambda=20000 the mode is j=10000, far beyond any naive 5000-cap; the
    # CDF is tiny but strictly positive, never exactly 0 by premature stop.
    assert 0.0 < vals[-1] < 1e-10


def test_mdes_regression_corner_reaches_target_power():
    # The d2=1 / alpha=0.01 corner that the truncation bug corrupted: the
    # returned f2, fed forward, must actually hit the requested power. (The
    # even-more-degenerate k=5,n=7 corner is covered cheaply by the direct
    # _ncf_cdf large-lambda test above; we skip it here to keep the suite fast.)
    for (n, k, alpha, target) in [(3, 1, 0.01, 0.95), (5, 3, 0.01, 0.80)]:
        f2 = power.mdes_regression(n, k, alpha, target)
        d2 = n - k - 1
        f_crit = power._f_quantile(1.0 - alpha, k, d2)
        achieved = 1.0 - power._ncf_cdf(f_crit, k, d2, f2 * n)
        assert math.isclose(achieved, target, abs_tol=1e-4)


def test_n_for_regression_extreme_f2_not_understated():
    # Extreme (unrealistic) f2 must still return an N whose power >= target and
    # whose N-1 power < target — i.e. no premature min-N return from truncation.
    n = power.n_for_regression(4000, 3, 0.01, 0.95)
    d2 = n - 3 - 1
    f_crit = power._f_quantile(0.99, 3, d2)
    assert 1.0 - power._ncf_cdf(f_crit, 3, d2, 4000 * n) >= 0.95
    if n - 1 > 3 + 1:
        d2m = n - 1 - 3 - 1
        fcm = power._f_quantile(0.99, 3, d2m)
        assert 1.0 - power._ncf_cdf(fcm, 3, d2m, 4000 * (n - 1)) < 0.95


def test_detectable_effect_regression_caps_absurd_f2():
    # At n = k + 2 only an implausible effect (f2 > 9) is detectable -> None.
    assert power.detectable_effect({"type": "regression", "k": 3}, 5, 0.01, 0.95) is None
    # A comfortable N still returns a sane f2.
    got = power.detectable_effect({"type": "regression", "k": 3}, 90)
    assert got is not None and got["value"] < 1.0


def test_mdes_regression_reaches_target_power():
    # The returned f2, fed forward through the exact power curve, must hit power.
    n, k, alpha, target = 90, 3, 0.05, 0.80
    f2 = power.mdes_regression(n, k, alpha, target)
    d2 = n - k - 1
    f_crit = power._f_quantile(1.0 - alpha, k, d2)
    achieved = 1.0 - power._ncf_cdf(f_crit, k, d2, f2 * n)
    assert math.isclose(achieved, target, abs_tol=1e-4)
