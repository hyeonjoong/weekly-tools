"""Hand-computed checks of the descriptive / test building blocks."""

from __future__ import annotations

import math

import pytest

from longistat.basics import (adjust, benjamini_hochberg, ci_mean, cohen_dz,
                              hedges_g, holm, iqr, mann_whitney, mean, median,
                              paired_t, quantile, ranks, sd, tie_correction,
                              welch_t, wilcoxon_signed_rank, wilson_interval)


def test_quantile_matches_the_type_7_definition():
    xs = [1.0, 2.0, 3.0, 4.0]
    assert math.isclose(quantile(xs, 0.25), 1.75)
    assert math.isclose(quantile(xs, 0.5), 2.5)
    assert math.isclose(quantile(xs, 0.75), 3.25)
    assert quantile([7.0], 0.9) == 7.0
    assert math.isnan(quantile([], 0.5))


def test_sd_and_median_basics():
    assert math.isclose(sd([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]),
                        math.sqrt(32.0 / 7.0))
    assert math.isnan(sd([1.0]))
    assert median([3.0, 1.0, 2.0]) == 2.0
    assert iqr([1.0, 2.0, 3.0, 4.0]) == (1.75, 3.25)


def test_ranks_average_ties():
    assert ranks([10.0, 20.0, 20.0, 30.0]) == [1.0, 2.5, 2.5, 4.0]
    assert tie_correction([1.0, 1.0, 2.0, 2.0, 2.0]) == (8 - 2) + (27 - 3)


def test_ci_mean_hand_computed():
    # n = 5, mean 3, sd sqrt(2.5), t.975(4) = 2.7764451051977987
    lo, hi = ci_mean([2.0, 3.0, 1.0, 4.0, 5.0])
    half = 2.7764451051977987 * math.sqrt(2.5) / math.sqrt(5)
    assert math.isclose(lo, 3.0 - half, rel_tol=1e-9)
    assert math.isclose(hi, 3.0 + half, rel_tol=1e-9)


def test_ci_mean_of_constant_data_is_a_point():
    assert ci_mean([4.0, 4.0, 4.0]) == (4.0, 4.0)


def test_wilson_interval_known_value():
    # Newcombe (1998), 56/70: Wilson 95 % CI = (0.6918, 0.8770)
    lo, hi = wilson_interval(56, 70)
    assert math.isclose(lo, 0.69183, abs_tol=1e-5)
    assert math.isclose(hi, 0.87695, abs_tol=1e-5)
    assert wilson_interval(0, 10)[0] == 0.0
    assert wilson_interval(10, 10)[1] == 1.0


def test_paired_t_hand_computed():
    res = paired_t([2.0, 3.0, 1.0, 4.0, 5.0])
    assert res.n == 5
    assert math.isclose(res.mean_diff, 3.0)
    assert math.isclose(res.sd_diff, math.sqrt(2.5))
    assert math.isclose(res.t, 3.0 / (math.sqrt(2.5) / math.sqrt(5)))
    assert math.isclose(res.dz, 3.0 / math.sqrt(2.5))
    # Hedges correction J = 1 - 3/(4·4 - 1) = 1 - 0.2 = 0.8
    assert math.isclose(res.dz_hedges, 0.8 * res.dz, rel_tol=1e-12)
    assert math.isclose(cohen_dz([2.0, 3.0, 1.0, 4.0, 5.0]), res.dz)


def test_paired_t_with_zero_variance_reports_no_p_value():
    """Identical differences carry no sampling variability — p must be NaN.

    Returning 0.0 here made three identical rows print '< .001 ***'.
    """
    for diffs in ([2.0, 2.0, 2.0], [0.0, 0.0, 0.0]):
        res = paired_t(diffs)
        assert math.isnan(res.t) and math.isnan(res.p)
        assert math.isnan(res.dz)
        assert res.sd_diff == 0.0 and res.ci_low == res.ci_high == diffs[0]


def test_adjustment_passes_uncomputable_p_values_through_as_nan():
    """A NaN must not be sorted into the family or given a real adjusted p."""
    adj = holm([0.01, float("nan"), 0.04])
    assert math.isnan(adj[1])
    assert adj[0] == pytest.approx(0.02) and adj[2] == pytest.approx(0.04)
    bh = benjamini_hochberg([0.01, float("nan"), 0.04])
    assert math.isnan(bh[1])
    assert bh[0] == pytest.approx(0.02) and bh[2] == pytest.approx(0.04)
    assert all(math.isnan(v) for v in adjust([float("nan")] * 3, "holm"))
    assert math.isnan(adjust([0.5, float("nan")], "none")[1])


def test_wilcoxon_exact_hand_computed():
    # 4 positive differences: T+ = 10, T- = 0, only one subset sums to 0
    res = wilcoxon_signed_rank([1.0, 2.0, 3.0, 4.0])
    assert res["n"] == 4 and res["w"] == 0.0
    assert math.isclose(res["p"], 2.0 / 16.0)
    assert math.isclose(res["r"], 1.0)
    assert "정확검정" in res["method"]


def test_wilcoxon_drops_zero_differences_and_reports_them():
    res = wilcoxon_signed_rank([0.0, 1.0, 2.0, 3.0, 4.0])
    assert res["n"] == 4 and res["n_zero"] == 1
    res_all_zero = wilcoxon_signed_rank([0.0, 0.0])
    assert res_all_zero["p"] == 1.0 and res_all_zero["n"] == 0


def test_wilcoxon_switches_to_the_approximation_when_tied():
    res = wilcoxon_signed_rank([1.0, 1.0, -1.0, 2.0, 3.0, -4.0, 5.0])
    assert "정규근사" in res["method"]


def test_mann_whitney_exact_hand_computed():
    res = mann_whitney([1.0, 2.0, 3.0], [4.0, 5.0, 6.0])
    assert res["u"] == 0.0 and res["u1"] == 0.0
    assert math.isclose(res["p"], 2.0 / 20.0)          # C(6,3) = 20
    assert math.isclose(res["r"], -1.0)
    assert "정확검정" in res["method"]


def test_mann_whitney_ties_use_the_corrected_normal_approximation():
    res = mann_whitney([1.0, 2.0, 2.0, 3.0], [2.0, 3.0, 4.0, 5.0])
    assert "정규근사" in res["method"]
    assert 0.0 <= res["p"] <= 1.0


def test_welch_t_hand_computed():
    a = [1.0, 2.0, 3.0, 4.0]          # mean 2.5, var 5/3
    b = [3.0, 4.0, 5.0, 6.0]          # mean 4.5, var 5/3
    res = welch_t(a, b)
    se = math.sqrt(5 / 3 / 4 + 5 / 3 / 4)
    assert math.isclose(res.diff, -2.0)
    assert math.isclose(res.t, -2.0 / se, rel_tol=1e-12)
    assert math.isclose(res.df, 6.0, rel_tol=1e-9)


def test_hedges_g_is_the_bias_corrected_cohen_d():
    a = [1.0, 2.0, 3.0, 4.0]
    b = [3.0, 4.0, 5.0, 6.0]
    g, (lo, hi) = hedges_g(a, b)
    sp = math.sqrt(5 / 3)
    d = -2.0 / sp
    j = 1.0 - 3.0 / (4.0 * 6 - 1.0)
    assert math.isclose(g, j * d, rel_tol=1e-12)
    assert lo < g < hi


def test_holm_and_bh_are_monotone_and_bounded():
    ps = [0.001, 0.01, 0.02, 0.6]
    h = holm(ps)
    assert h == pytest.approx([0.004, 0.03, 0.04, 0.6])
    bh = benjamini_hochberg(ps)
    assert bh == pytest.approx([0.004, 0.02, 0.0266666667, 0.6])
    assert all(a <= b for a, b in zip(sorted(h), sorted(h)[1:]))
    assert adjust(ps, "none") == ps
    assert holm([]) == [] and benjamini_hochberg([]) == []


def test_holm_never_lets_a_smaller_raw_p_get_a_bigger_adjusted_p():
    ps = [0.04, 0.03, 0.05, 0.001, 0.9]
    adj = holm(ps)
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    vals = [adj[i] for i in order]
    assert vals == sorted(vals)


def test_two_sample_needs_two_observations_per_arm():
    with pytest.raises(ValueError):
        welch_t([1.0], [1.0, 2.0])
    with pytest.raises(ValueError):
        paired_t([1.0])
    with pytest.raises(ValueError):
        mann_whitney([], [1.0])


def test_mean_of_empty_is_nan():
    assert math.isnan(mean([]))
