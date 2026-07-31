"""Attained-power ("what power do I actually have?") tests.

The key property is *mutual consistency* with the sizing routines: at
N = required_total_n(effect) the attained power must reach the target, and at
N-1 it must not. Anything else means the two answers disagree in a planning
meeting. Closed-form recomputations pin the absolute values independently.
"""
import math

import pytest

from paperforge import power


Z95, Z80 = 1.9599639845400545, 0.8416212335729143


def test_correlation_power_closed_form():
    # power = Phi(atanh(r)*sqrt(n-3) - z) + Phi(-atanh(r)*sqrt(n-3) - z)
    for r, n in [(0.30, 85), (0.30, 50), (0.50, 30), (0.10, 400)]:
        delta = math.atanh(r) * math.sqrt(n - 3)
        expected = power.norm_cdf(delta - Z95) + power.norm_cdf(-delta - Z95)
        assert math.isclose(
            power.power_for_correlation(r, n), expected, rel_tol=1e-12
        )


def test_paired_and_two_group_power_closed_form():
    delta = 0.5 * math.sqrt(33 - 1)
    expected = power.norm_cdf(delta - Z95) + power.norm_cdf(-delta - Z95)
    assert math.isclose(power.power_for_paired(0.5, 33), expected, rel_tol=1e-12)

    delta = 0.5 * math.sqrt(126 * 0.25)
    expected = power.norm_cdf(delta - Z95) + power.norm_cdf(-delta - Z95)
    assert math.isclose(
        power.power_for_two_group(0.5, 126), expected, rel_tol=1e-12
    )


def test_power_at_required_n_reaches_target_and_below_it_does_not():
    """The forward and attained routines must be each other's inverse."""
    effects = [
        {"type": "correlation", "r": 0.30},
        {"type": "correlation", "r": 0.15},
        {"type": "paired", "d": 0.5},
        {"type": "two_group", "d": 0.5},
        {"type": "two_group", "d": 0.4, "allocation": 0.3},
        {"type": "regression", "f2": 0.15, "k": 3},
        {"type": "regression_change", "f2": 0.15, "k_tested": 2, "k_control": 1},
    ]
    for target in (0.80, 0.90):
        for eff in effects:
            n = power.required_total_n(eff, power=target)
            assert power.attained_power(eff, n) >= target
            # One subject fewer must fall short (the search returns the SMALLEST
            # sufficient N) — except for the *balanced* two-group form, which
            # returns 2*ceil(per-group) and can therefore sit one subject above
            # the strict minimum. That extra subject is deliberate: you cannot
            # recruit half a person into a group.
            balanced_two_group = (
                eff["type"] == "two_group" and eff.get("allocation", 0.5) == 0.5
            )
            below = power.attained_power(eff, n - 1)
            if not balanced_two_group:
                assert below is None or below < target + 1e-9


def test_balanced_two_group_total_is_at_most_one_above_strict_minimum():
    # Documents the per-group rounding above: the balanced total never exceeds
    # the unbalanced (p=0.5) closed form by more than 1 subject.
    for d in (0.2, 0.5, 0.8):
        for target in (0.80, 0.90, 0.95):
            balanced = power.required_total_n({"type": "two_group", "d": d},
                                              power=target)
            strict = power.n_total_two_group(d, power=target, allocation=0.5)
            assert 0 <= balanced - strict <= 1


def test_power_rises_with_n_and_falls_with_stricter_alpha():
    eff = {"type": "correlation", "r": 0.30}
    assert power.attained_power(eff, 40) < power.attained_power(eff, 90)
    assert power.attained_power(eff, 90, alpha=0.01) < power.attained_power(eff, 90)
    assert power.attained_power(eff, 90, sided=1) > power.attained_power(eff, 90)


def test_power_dispatch_and_none_cases():
    assert power.attained_power({"type": "exploratory"}, 90) is None
    assert power.attained_power({"type": "correlation", "r": 0.3}, None) is None
    # Below the formula's minimum n -> None, not a crash.
    assert power.attained_power({"type": "correlation", "r": 0.3}, 3) is None
    assert power.attained_power({"type": "paired", "d": 0.5}, 1) is None
    assert power.attained_power(
        {"type": "regression", "f2": 0.15, "k": 5}, 4
    ) is None
    with pytest.raises(ValueError):
        power.attained_power({"type": "nope"}, 50)


def test_power_bounded_in_unit_interval():
    for n in (5, 50, 5000, 100000):
        pw = power.attained_power({"type": "correlation", "r": 0.6}, n)
        assert 0.0 <= pw <= 1.0
    assert power.attained_power({"type": "correlation", "r": 0.9}, 5000) > 0.999


def test_power_matches_scipy_for_regression():
    scipy_stats = pytest.importorskip("scipy.stats")
    for f2, k, n in [(0.15, 3, 77), (0.02, 1, 300), (0.35, 5, 40)]:
        d2 = n - k - 1
        expected = 1 - scipy_stats.ncf.cdf(
            scipy_stats.f.ppf(0.95, k, d2), k, d2, f2 * n
        )
        got = power.power_for_regression(f2, n, k)
        assert math.isclose(got, expected, abs_tol=1e-9)


def test_power_matches_scipy_for_correlation_via_t_ish_bound():
    # Sanity band rather than an exact match: the Fisher-z normal approximation
    # and scipy's exact non-central t agree to within a few points of power.
    scipy_stats = pytest.importorskip("scipy.stats")
    for r, n in [(0.30, 85), (0.40, 50), (0.20, 200)]:
        nc = r * math.sqrt(n - 2) / math.sqrt(1 - r ** 2)
        crit = scipy_stats.t.ppf(0.975, n - 2)
        exact = 1 - scipy_stats.nct.cdf(crit, n - 2, nc)
        assert abs(power.power_for_correlation(r, n) - exact) < 0.03
