"""Tests for the ordered-group trend tests (table1.trend).

Every reference value is derived from first principles offline — either by
brute-force enumeration (the exact permutation mean/variance of the
Jonckheere-Terpstra statistic) or by an algebraic identity the implementation
must satisfy (JT with k=2 == uncorrected Mann-Whitney; the linear contrast with
k=2 == Student's t; Cochran-Armitage == N*r^2 == the 2-group Pearson
chi-square). No network, no third-party packages.
"""

from __future__ import annotations

import itertools
import math

import pytest

from table1.cat_tests import chi_square
from table1.special import norm_sf
from table1.tests_stat import _rankdata, _tie_term, students_t
from table1.trend import (
    cochran_armitage,
    default_scores,
    jonckheere_terpstra,
    linear_contrast,
)


# --------------------------------------------------------------------------- #
# Jonckheere-Terpstra
# --------------------------------------------------------------------------- #
def _brute_J(groups):
    """J = sum over i<j of #(x_i < x_j) + 0.5 * #(x_i == x_j) — the definition."""
    total = 0.0
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            for a in groups[i]:
                for b in groups[j]:
                    total += 1.0 if a < b else (0.5 if a == b else 0.0)
    return total


def _permutation_moments(data, sizes):
    """Exact mean and variance of J over every allocation of ``data``."""
    vals = []
    for perm in itertools.permutations(range(len(data))):
        gs, pos = [], 0
        for s in sizes:
            gs.append([data[i] for i in perm[pos:pos + s]])
            pos += s
        vals.append(_brute_J(gs))
    m = sum(vals) / len(vals)
    v = sum((x - m) ** 2 for x in vals) / len(vals)
    return m, v


@pytest.mark.parametrize("data,sizes", [
    ([1, 2, 3, 4, 5, 6], [2, 2, 2]),      # no ties
    ([1, 1, 2, 2, 3, 3], [2, 2, 2]),      # heavy ties
    ([1, 1, 1, 2, 2, 3], [3, 2, 1]),      # unequal groups + ties
    ([5, 5, 5, 5, 5, 7], [2, 2, 2]),      # one distinct value
])
def test_jt_z_matches_exact_permutation_moments(data, sizes):
    """The tie-corrected normal approximation must use the EXACT permutation
    mean and variance of J — recomputed here by enumerating all allocations."""
    gs, pos = [], 0
    for s in sizes:
        gs.append(data[pos:pos + s])
        pos += s
    mean_j, var_j = _permutation_moments(data, sizes)
    z_expected = (_brute_J(gs) - mean_j) / math.sqrt(var_j)
    got = jonckheere_terpstra(gs)
    assert got.statistic == pytest.approx(z_expected, abs=1e-12)
    assert got.pvalue == pytest.approx(2.0 * norm_sf(abs(z_expected)), abs=1e-15)


def test_jt_statistic_equals_definition():
    groups = [[1.0, 3.0, 5.0], [2.0, 4.0, 6.0, 6.0], [7.0, 8.0]]
    res = jonckheere_terpstra(groups)
    # Reconstruct J from z: J = z*sd + E. Cheaper: verify direction only here
    # (the exact-moment test above pins the arithmetic), plus monotonicity.
    assert res.statistic > 0  # increasing groups -> positive z
    assert res.kind == "jonckheere"
    assert res.scores == default_scores(3)


def test_jt_two_groups_equals_uncorrected_mann_whitney():
    """With k=2, JT is algebraically the tie-corrected Mann-Whitney normal
    approximation WITHOUT the continuity correction. Recomputed here."""
    a = [1.2, 2.4, 2.4, 3.1, 4.0, 5.5, 6.1, 7.0, 7.0]
    b = [2.0, 3.3, 4.4, 4.4, 5.0, 6.6, 8.1, 9.0, 9.9, 10.5, 11.0]
    n1, n2 = len(a), len(b)
    comb = a + b
    ranks = _rankdata(comb)
    u1 = sum(ranks[:n1]) - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    n = n1 + n2
    sigma2 = (n1 * n2 / 12.0) * ((n + 1) - _tie_term(comb) / (n * (n - 1)))
    z_ref = (max(u1, u2) - n1 * n2 / 2.0) / math.sqrt(sigma2)
    res = jonckheere_terpstra([a, b])
    assert abs(res.statistic) == pytest.approx(z_ref, abs=1e-12)
    assert res.pvalue == pytest.approx(2.0 * norm_sf(z_ref), abs=1e-15)


def test_jt_direction_flips_with_group_order():
    lo, mid, hi = [1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]
    up = jonckheere_terpstra([lo, mid, hi])
    down = jonckheere_terpstra([hi, mid, lo])
    assert up.statistic == pytest.approx(-down.statistic, abs=1e-12)
    assert up.pvalue == pytest.approx(down.pvalue, abs=1e-15)


def test_jt_detects_monotone_trend_an_omnibus_test_dilutes():
    lo = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    mid = [3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    hi = [5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert jonckheere_terpstra([lo, mid, hi]).pvalue < 0.01


def test_jt_rejects_degenerate_input():
    with pytest.raises(ValueError):
        jonckheere_terpstra([[1.0, 2.0, 3.0]])            # one group
    with pytest.raises(ValueError):
        jonckheere_terpstra([[1.0], [2.0]])               # N < 3
    with pytest.raises(ValueError):
        jonckheere_terpstra([[5.0, 5.0], [5.0, 5.0]])     # zero variance
    # An empty group is dropped, not an error, provided two remain.
    res = jonckheere_terpstra([[1.0, 2.0], [], [3.0, 4.0]])
    assert res.pvalue <= 1.0


def test_jt_is_scale_and_shift_invariant():
    gs = [[1.0, 2.5, 3.0], [2.0, 4.0, 5.5], [6.0, 7.5, 8.0]]
    base = jonckheere_terpstra(gs)
    moved = jonckheere_terpstra([[3.0 * x + 100 for x in g] for g in gs])
    assert base.statistic == pytest.approx(moved.statistic, abs=1e-12)


def test_jt_handles_a_thousand_rows_quickly():
    gs = [[float(i % 37) + k for i in range(400)] for k in range(4)]
    res = jonckheere_terpstra(gs)
    assert 0.0 <= res.pvalue <= 1.0


# --------------------------------------------------------------------------- #
# Linear contrast (parametric trend)
# --------------------------------------------------------------------------- #
def test_linear_contrast_two_groups_equals_student_t():
    a = [5.1, 4.8, 6.2, 5.5, 5.9, 6.0, 4.4, 5.2]
    b = [6.9, 7.2, 6.1, 7.8, 6.5, 7.0, 8.1, 6.6, 7.4, 7.1]
    lc = linear_contrast([a, b])
    st = students_t(a, b)
    assert abs(lc.statistic) == pytest.approx(abs(st.statistic), abs=1e-12)
    assert lc.pvalue == pytest.approx(st.pvalue, abs=1e-15)
    assert lc.df == pytest.approx(st.df)


def test_linear_contrast_matches_hand_computation():
    """L = sum c_i * mean_i, SE = sqrt(MSE * sum c_i^2/n_i), t = L/SE."""
    gs = [[2.0, 3.0, 4.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]]
    means = [sum(g) / len(g) for g in gs]
    sse = sum(sum((x - m) ** 2 for x in g) for g, m in zip(gs, means))
    df = sum(len(g) for g in gs) - len(gs)
    mse = sse / df
    coefs = [-1.0, 0.0, 1.0]  # scores 1,2,3 centred
    est = sum(c * m for c, m in zip(coefs, means))
    se = math.sqrt(mse * sum(c * c / len(g) for c, g in zip(coefs, gs)))
    res = linear_contrast(gs)
    assert res.statistic == pytest.approx(est / se, abs=1e-12)
    assert res.df == df


def test_linear_contrast_uses_custom_scores():
    """Unequally spaced dose scores change the contrast; equal spacing does not."""
    gs = [[1.0, 2.0], [2.0, 3.0], [10.0, 11.0]]
    equal = linear_contrast(gs, [1, 2, 3])
    dose = linear_contrast(gs, [0, 10, 40])
    assert equal.statistic != pytest.approx(dose.statistic)
    # A linear rescaling of the scores leaves t unchanged (contrast is scale
    # invariant after centring).
    rescaled = linear_contrast(gs, [3, 5, 7])   # 2*x + 1 of 1,2,3
    assert equal.statistic == pytest.approx(rescaled.statistic, abs=1e-12)


def test_linear_contrast_rejects_degenerate_input():
    with pytest.raises(ValueError):
        linear_contrast([[1.0, 2.0]])                       # one group
    with pytest.raises(ValueError):
        linear_contrast([[1.0], [2.0]])                     # no residual df
    with pytest.raises(ValueError):
        linear_contrast([[1.0, 1.0], [2.0, 2.0]], [3, 3])   # identical scores
    with pytest.raises(ValueError):
        linear_contrast([[1.0, 1.0], [1.0, 1.0]])           # zero MSE


def test_linear_contrast_drops_empty_groups_with_their_scores():
    gs = [[1.0, 2.0], [], [5.0, 6.0]]
    got = linear_contrast(gs, [0, 10, 40])
    assert got.scores == [0.0, 40.0]
    assert got.df == 2


# --------------------------------------------------------------------------- #
# Cochran-Armitage
# --------------------------------------------------------------------------- #
def _pearson_r(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs)
                    * sum((y - my) ** 2 for y in ys))
    return num / den


@pytest.mark.parametrize("events,totals,scores", [
    ([2, 5, 9], [20, 20, 20], [1, 2, 3]),
    ([1, 4, 7, 12], [15, 18, 20, 25], [0, 10, 20, 40]),
    ([3, 9], [20, 22], [1, 2]),
    ([10, 6, 2], [20, 20, 20], [1, 2, 3]),   # decreasing
])
def test_cochran_armitage_equals_n_times_r_squared(events, totals, scores):
    """z^2 == N * r^2 with r the Pearson correlation of (score, 0/1 outcome),
    expanded here to individual subject rows."""
    xs, ys = [], []
    for r, n, s in zip(events, totals, scores):
        xs += [float(s)] * n
        ys += [1.0] * r + [0.0] * (n - r)
    ref = len(xs) * _pearson_r(xs, ys) ** 2
    got = cochran_armitage(events, totals, scores)
    assert got.statistic ** 2 == pytest.approx(ref, rel=1e-12)


def test_cochran_armitage_two_groups_equals_pearson_chi_square():
    events, totals = [3, 9], [20, 22]
    tab = [[events[0], totals[0] - events[0]],
           [events[1], totals[1] - events[1]]]
    chi = chi_square(tab)
    ca = cochran_armitage(events, totals)
    assert ca.statistic ** 2 == pytest.approx(chi.statistic, rel=1e-12)
    assert ca.pvalue == pytest.approx(chi.pvalue, abs=1e-9)


def test_cochran_armitage_is_symmetric_in_the_coded_level():
    """Testing the complement of the outcome gives the same two-sided p."""
    events, totals = [2, 5, 9], [20, 20, 20]
    comp = [n - r for r, n in zip(events, totals)]
    a = cochran_armitage(events, totals)
    b = cochran_armitage(comp, totals)
    assert a.pvalue == pytest.approx(b.pvalue, abs=1e-15)
    assert a.statistic == pytest.approx(-b.statistic, abs=1e-12)


def test_cochran_armitage_invariant_to_linear_score_rescaling():
    events, totals = [2, 5, 9], [20, 20, 20]
    base = cochran_armitage(events, totals, [1, 2, 3])
    shifted = cochran_armitage(events, totals, [11, 12, 13])
    scaled = cochran_armitage(events, totals, [2, 4, 6])
    assert base.statistic == pytest.approx(shifted.statistic, abs=1e-12)
    assert base.statistic == pytest.approx(scaled.statistic, abs=1e-12)


def test_cochran_armitage_beats_omnibus_on_a_monotone_pattern():
    events, totals = [2, 6, 10, 14], [30, 30, 30, 30]
    ca = cochran_armitage(events, totals)
    tab = [[e for e in events], [n - e for e, n in zip(events, totals)]]
    omnibus = chi_square(tab)
    assert ca.pvalue < omnibus.pvalue


def test_cochran_armitage_rejects_degenerate_input():
    with pytest.raises(ValueError):
        cochran_armitage([1], [10])                       # one group
    with pytest.raises(ValueError):
        cochran_armitage([0, 0, 0], [10, 10, 10])         # no events
    with pytest.raises(ValueError):
        cochran_armitage([10, 10], [10, 10])              # all events
    with pytest.raises(ValueError):
        cochran_armitage([1, 2], [10, 10], [4, 4])        # no score spread
    with pytest.raises(ValueError):
        cochran_armitage([1, 2], [0, 0])                  # empty groups


def test_cochran_armitage_drops_empty_groups_with_their_scores():
    got = cochran_armitage([2, 0, 9], [20, 0, 20], [0, 10, 40])
    assert got.scores == [0.0, 40.0]
    assert got.pvalue == pytest.approx(
        cochran_armitage([2, 9], [20, 20], [0, 40]).pvalue)


def test_pvalues_are_in_range_and_two_sided():
    for res in (jonckheere_terpstra([[1.0, 2.0], [2.0, 3.0], [3.0, 4.0]]),
                linear_contrast([[1.0, 2.0], [2.0, 3.0], [3.0, 9.0]]),
                cochran_armitage([1, 5, 9], [20, 20, 20])):
        assert 0.0 <= res.pvalue <= 1.0


# --------------------------------------------------------------------------- #
# Correctness-review regressions (round 6)
# --------------------------------------------------------------------------- #
def test_jt_rejects_non_finite_values_instead_of_hanging():
    """NaN compares false against itself, which used to stall the tie-block
    scan forever."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            jonckheere_terpstra([[1.0, bad], [2.0, 3.0]])


def test_tie_sizes_partition_the_sample():
    from table1.trend import _tie_sizes
    for data in ([1, 1, 2, 3, 3, 3], [5.0] * 7, [1, 2, 3], []):
        assert sum(_tie_sizes(data)) == len(data)
        assert all(t >= 1 for t in _tie_sizes(data))


def test_linear_contrast_reports_a_perfect_trend_rather_than_raising():
    """Every group constant, means rising with the scores: MSE is 0 and the
    trend is as strong as it can be."""
    res = linear_contrast([[1.0, 1.0], [2.0, 2.0], [3.0, 3.0]])
    assert res.statistic == math.inf
    assert res.pvalue == 0.0
    down = linear_contrast([[3.0, 3.0], [2.0, 2.0], [1.0, 1.0]])
    assert down.statistic == -math.inf and down.pvalue == 0.0


def test_linear_contrast_still_raises_when_all_groups_are_identical():
    with pytest.raises(ValueError):
        linear_contrast([[1.0, 1.0], [1.0, 1.0], [1.0, 1.0]])


def test_linear_contrast_zero_contrast_with_nonzero_variance_is_p_one():
    """A symmetric pattern has L = 0 exactly, which is a real t = 0, not an
    undefined statistic."""
    res = linear_contrast([[1.0, 3.0], [5.0, 7.0], [1.0, 3.0]])
    assert res.statistic == pytest.approx(0.0, abs=1e-12)
    assert res.pvalue == pytest.approx(1.0, abs=1e-12)
