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


def test_arbitrary_alpha_is_supported():
    # Non-tabulated alpha/power used to be rejected; they are now computed from
    # the inverse-normal CDF, which is what Bonferroni planning requires.
    assert power.n_for_correlation(0.30, alpha=0.123) < power.n_for_correlation(0.30)
    assert power.n_for_correlation(0.30, power=0.85) > power.n_for_correlation(0.30)
    # alpha = 0.05/7 (7 primary comparisons) must still be a real number.
    assert power.n_for_correlation(0.30, alpha=0.05 / 7) > power.n_for_correlation(0.30)


def test_out_of_range_alpha_power_raise():
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            power.n_for_correlation(0.30, alpha=bad)
        with pytest.raises(ValueError):
            power.n_for_correlation(0.30, power=bad)
    with pytest.raises(ValueError):
        power.norm_ppf(0.0)
    with pytest.raises(ValueError):
        power.norm_ppf(1.0)


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


def test_regression_change_uses_added_predictor_df():
    # Incremental-R^2 test: numerator df = k_tested (not the full model). With
    # k_tested=2 the numerator df is smaller than an overall k=3 test, so N is
    # smaller at the same f2.
    n_change = power.required_total_n(
        {"type": "regression_change", "f2": 0.15, "k_tested": 2, "k_control": 1}
    )
    n_overall = power.required_total_n({"type": "regression", "f2": 0.15, "k": 3})
    assert n_change == 68
    assert n_change < n_overall  # 68 < 77


def test_regression_change_more_controls_need_more_n():
    # Adding covariates (k_control) costs residual df -> a bit more N.
    n0 = power.required_total_n(
        {"type": "regression_change", "f2": 0.15, "k_tested": 3, "k_control": 0}
    )
    n2 = power.required_total_n(
        {"type": "regression_change", "f2": 0.15, "k_tested": 3, "k_control": 2}
    )
    assert n2 >= n0


def test_two_group_allocation_balanced_matches_legacy():
    # allocation=0.5 must reproduce the exact 2*per-group value.
    assert power.required_total_n({"type": "two_group", "d": 0.5}) == 126
    assert power.required_total_n(
        {"type": "two_group", "d": 0.5, "allocation": 0.5}
    ) == 126


def test_two_group_allocation_unbalanced_needs_more():
    n50 = power.required_total_n({"type": "two_group", "d": 0.5, "allocation": 0.5})
    n30 = power.required_total_n({"type": "two_group", "d": 0.5, "allocation": 0.3})
    n20 = power.required_total_n({"type": "two_group", "d": 0.5, "allocation": 0.2})
    assert n20 > n30 > n50


def test_scale_effect_all_types():
    assert power.scale_effect({"type": "correlation", "r": 0.3}, 2)["r"] == 0.6
    assert power.scale_effect({"type": "correlation", "r": 0.6}, 3)["r"] == 0.999  # capped
    assert power.scale_effect({"type": "two_group", "d": 0.5}, 0.5)["d"] == 0.25
    assert power.scale_effect({"type": "paired", "d": 0.4}, 2)["d"] == 0.8
    assert power.scale_effect({"type": "regression", "f2": 0.1, "k": 3}, 2)["f2"] == 0.2
    # Exploratory is returned unchanged; original dict not mutated.
    src = {"type": "correlation", "r": 0.3}
    power.scale_effect(src, 2)
    assert src["r"] == 0.3


def test_scale_effect_smaller_effect_needs_more_n():
    base = {"type": "correlation", "r": 0.3}
    assert power.required_total_n(power.scale_effect(base, 2.0 / 3.0)) > \
        power.required_total_n(base)


def test_effect_magnitude():
    assert power.effect_magnitude({"type": "correlation", "r": 0.3}) == 0.3
    assert power.effect_magnitude({"type": "two_group", "d": 0.5}) == 0.5
    assert power.effect_magnitude({"type": "regression", "f2": 0.15}) == 0.15
    assert power.effect_magnitude({"type": "exploratory"}) is None
