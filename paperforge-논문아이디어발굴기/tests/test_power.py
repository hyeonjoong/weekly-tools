"""Sample-size numbers checked against published tables / hand computation."""
import math

import pytest

from paperforge import power


def test_correlation_r030_matches_table():
    # Classic result: r=.30, alpha=.05 two-sided, power=.80 -> N≈85.
    assert power.n_for_correlation(0.30) == 85


def test_correlation_r050():
    # r=.50 -> Fisher-z approx 29.01, rounded up to 30.
    assert power.n_for_correlation(0.50) == 30


def test_correlation_hand_computation():
    # Reproduce the formula by hand for r=0.30.
    za, zb = 1.959963985, 0.841621234
    c = 0.5 * math.log((1 + 0.30) / (1 - 0.30))
    expected = math.ceil(((za + zb) / c) ** 2 + 3)
    assert power.n_for_correlation(0.30) == expected == 85


def test_two_means_d050_per_group():
    # Cohen's d=0.5: normal approx 62.79 -> 63/group (G*Power exact: 64).
    assert power.n_per_group_two_means(0.5) == 63


def test_two_means_d080_per_group():
    # d=0.8: normal approx 24.53 -> 25 per group.
    assert power.n_per_group_two_means(0.8) == 25


def test_required_total_n_two_group_is_double():
    per = power.n_per_group_two_means(0.5)
    assert power.required_total_n({"type": "two_group", "d": 0.5}) == 2 * per == 126


def test_regression_exact_noncentral_f():
    # Exact non-central F (matches G*Power "R^2 deviation from zero").
    # f2=0.15 (medium): N grows with the number of predictors k.
    assert power.n_for_regression(0.15, 1) == 55
    assert power.n_for_regression(0.15, 3) == 77
    assert power.n_for_regression(0.15, 5) == 92
    # A naive z-approximation that ignores numerator df would give ~57 for k=3
    # and dangerously under-state N — guard against regressing to it.
    assert power.n_for_regression(0.15, 3) > 70


def test_regression_small_and_large_effects():
    # Independently checked against G*Power.
    assert power.n_for_regression(0.02, 1) == 395   # small effect, 1 predictor
    assert power.n_for_regression(0.35, 3) == 36     # large effect, 3 predictors
    # More predictors at the same effect size require a larger sample.
    assert power.n_for_regression(0.15, 4) > power.n_for_regression(0.15, 2)


def test_regression_higher_power_needs_more():
    assert power.n_for_regression(0.15, 3, power=0.90) > power.n_for_regression(0.15, 3)


def test_power_090_needs_more_than_080():
    assert power.n_for_correlation(0.30, power=0.90) > power.n_for_correlation(0.30)


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        power.n_for_correlation(0)
    with pytest.raises(ValueError):
        power.n_for_correlation(1.0)
    with pytest.raises(ValueError):
        power.n_per_group_two_means(0)
    with pytest.raises(ValueError):
        power.n_for_regression(0.15, 0)
    with pytest.raises(ValueError):
        power.required_total_n({"type": "nonsense"})


def test_unsupported_alpha_raises():
    with pytest.raises(ValueError):
        power.n_for_correlation(0.30, alpha=0.123)


def test_paired_needs_far_fewer_than_two_group():
    # Within-subject d_z=0.5: (2.801585)^2/0.25 + 1 = 32.39 -> 33 subjects.
    assert power.n_for_paired(0.5) == 33
    assert power.n_for_paired(0.5) < power.required_total_n(
        {"type": "two_group", "d": 0.5}
    )


def test_exploratory_effect_has_no_target():
    assert power.required_total_n({"type": "exploratory"}) is None


def test_paired_via_required_total_n():
    assert power.required_total_n({"type": "paired", "d": 0.5}) == 33


def test_alpha010_power095_combo():
    # Independently checked: r=0.30, alpha=.10, power=.95 -> 116.
    assert power.n_for_correlation(0.30, alpha=0.10, power=0.95) == 116
