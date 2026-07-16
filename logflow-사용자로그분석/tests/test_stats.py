import math

import pytest

from logflow.stats import (
    describe,
    median,
    quantile,
    wilson_interval,
    z_for_confidence,
    _inv_norm_cdf,
)


# ---- Wilson score interval ----

def test_wilson_matches_reference_values():
    # 3/6 @95% ≈ (0.1876, 0.8124) — R binom.confint(method="wilson")
    lo, hi = wilson_interval(3, 6)
    assert abs(lo - 0.18761630648265) < 1e-9
    assert abs(hi - 0.81238369351735) < 1e-9


def test_wilson_clamps_at_zero_and_one():
    lo, hi = wilson_interval(0, 10)
    assert lo == 0.0
    assert 0.27 < hi < 0.28
    lo, hi = wilson_interval(10, 10)
    assert abs(hi - 1.0) < 1e-9
    assert 0.72 < lo < 0.73


def test_wilson_zero_total_is_none():
    assert wilson_interval(0, 0) is None


def test_wilson_interval_contains_point_estimate():
    for k, n in [(1, 3), (5, 20), (17, 40), (99, 100)]:
        lo, hi = wilson_interval(k, n)
        assert lo <= k / n <= hi


def test_wilson_wider_at_higher_confidence():
    lo95, hi95 = wilson_interval(5, 20, 0.95)
    lo99, hi99 = wilson_interval(5, 20, 0.99)
    assert (hi99 - lo99) > (hi95 - lo95)


def test_wilson_rejects_bad_inputs():
    with pytest.raises(ValueError):
        wilson_interval(5, 3)      # successes > total
    with pytest.raises(ValueError):
        wilson_interval(-1, 3)
    with pytest.raises(ValueError):
        wilson_interval(1, -3)


# ---- quantile / median / describe ----

def test_quantile_linear_interpolation():
    xs = list(range(1, 11))  # 1..10
    assert quantile(xs, 0.0) == 1.0
    assert quantile(xs, 1.0) == 10.0
    assert quantile(xs, 0.25) == 3.25
    assert quantile(xs, 0.5) == 5.5
    assert abs(quantile(xs, 0.9) - 9.1) < 1e-12


def test_quantile_single_and_empty():
    assert quantile([42], 0.3) == 42.0
    assert quantile([], 0.5) is None


def test_quantile_unsorted_input():
    assert median([3, 1, 2]) == 2.0


def test_quantile_rejects_bad_q():
    with pytest.raises(ValueError):
        quantile([1, 2, 3], 1.5)


def test_describe_fields():
    d = describe([1, 2, 3, 4])
    assert d["n"] == 4
    assert d["mean"] == 2.5
    assert d["median"] == 2.5
    assert d["min"] == 1.0
    assert d["max"] == 4.0
    assert describe([]) is None


# ---- z for confidence / inverse normal ----

def test_z_table_values():
    assert abs(z_for_confidence(0.95) - 1.959963984540054) < 1e-12
    assert abs(z_for_confidence(0.90) - 1.6448536269514722) < 1e-12


def test_z_computed_matches_table_for_off_table_value():
    # 0.95 via inverse-CDF path should equal the table
    assert abs(_inv_norm_cdf(0.975) - z_for_confidence(0.95)) < 1e-6


def test_inv_norm_symmetry():
    assert abs(_inv_norm_cdf(0.5)) < 1e-9
    assert abs(_inv_norm_cdf(0.1) + _inv_norm_cdf(0.9)) < 1e-6


def test_z_rejects_out_of_range():
    with pytest.raises(ValueError):
        z_for_confidence(0.0)
    with pytest.raises(ValueError):
        z_for_confidence(1.0)
