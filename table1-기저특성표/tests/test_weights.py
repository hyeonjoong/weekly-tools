"""Weighted (IPTW / survey) summaries and weighted SMD.

The oracle values here were computed offline against numpy/statsmodels and are
pinned as literals so the suite stays dependency-free. The invariants
(equal-weight reduction, scale invariance, monotonicity) are property tests
over randomized inputs.
"""

from __future__ import annotations

import io
import json
import math
import random
import sys

import pytest

from table1.build import Options, _quantile, build_table1
from table1.dataio import Frame
from table1.render import render
from table1.smd import categorical_smd, continuous_smd
from table1.weights import (
    kish_ess,
    weighted_categorical_smd,
    weighted_continuous_smd,
    weighted_mean,
    weighted_quantile,
    weighted_sd,
    weighted_var,
)


# --------------------------------------------------------------------------- #
# property: equal weights must reproduce the unweighted statistics exactly
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("const", [1.0, 0.25, 3.7, 1e6])
def test_equal_weights_reduce_to_unweighted_quantile(const):
    rng = random.Random(7)
    for _ in range(200):
        n = rng.randint(1, 25)
        xs = [rng.gauss(0, 1) for _ in range(n)]
        w = [const] * n
        for q in (0.0, 0.25, 0.5, 0.75, 1.0):
            assert weighted_quantile(xs, w, q) == pytest.approx(
                _quantile(sorted(xs), q), abs=1e-9)


@pytest.mark.parametrize("const", [1.0, 2.5])
def test_equal_weights_reduce_to_ddof1_variance(const):
    rng = random.Random(8)
    for _ in range(200):
        n = rng.randint(2, 30)
        xs = [rng.gauss(3, 2) for _ in range(n)]
        m = sum(xs) / n
        expect = sum((x - m) ** 2 for x in xs) / (n - 1)
        assert weighted_var(xs, [const] * n) == pytest.approx(expect, rel=1e-9)


def test_equal_weights_reduce_to_unweighted_smd():
    rng = random.Random(9)
    for _ in range(200):
        a = [rng.gauss(0, 1) for _ in range(rng.randint(2, 20))]
        b = [rng.gauss(0.5, 1.4) for _ in range(rng.randint(2, 20))]
        got = weighted_continuous_smd(a, [2.0] * len(a), b, [2.0] * len(b))
        assert got == pytest.approx(continuous_smd(a, b), abs=1e-9)


def test_weighted_categorical_smd_matches_unweighted_on_unit_weights():
    rng = random.Random(10)
    for _ in range(300):
        k = rng.randint(2, 5)
        c1 = [rng.randint(0, 20) for _ in range(k)]
        c2 = [rng.randint(0, 20) for _ in range(k)]
        if sum(c1) == 0 or sum(c2) == 0:
            continue
        got = weighted_categorical_smd([float(x) for x in c1],
                                       [float(x) for x in c2])
        exp = categorical_smd(c1, c2)
        if got is None or exp is None:
            continue
        assert got == pytest.approx(exp, abs=1e-9)


# --------------------------------------------------------------------------- #
# property: reliability-weight semantics -> rescaling weights changes nothing
# --------------------------------------------------------------------------- #
def test_weights_are_scale_invariant():
    rng = random.Random(11)
    for _ in range(200):
        n = rng.randint(2, 20)
        xs = [rng.gauss(0, 1) for _ in range(n)]
        w = [rng.uniform(0.1, 5) for _ in range(n)]
        w2 = [x * 13.3 for x in w]
        assert weighted_mean(xs, w) == pytest.approx(weighted_mean(xs, w2), abs=1e-9)
        assert weighted_var(xs, w) == pytest.approx(weighted_var(xs, w2), rel=1e-9)
        assert weighted_quantile(xs, w, 0.5) == pytest.approx(
            weighted_quantile(xs, w2, 0.5), abs=1e-9)


def test_weighted_quantile_is_monotone_and_within_range():
    rng = random.Random(12)
    for _ in range(300):
        n = rng.randint(1, 20)
        xs = [rng.gauss(0, 1) for _ in range(n)]
        w = [rng.uniform(0.01, 50) for _ in range(n)]
        prev = -math.inf
        for i in range(21):
            q = i / 20
            v = weighted_quantile(xs, w, q)
            assert min(xs) - 1e-12 <= v <= max(xs) + 1e-12
            assert v >= prev - 1e-12
            prev = v


def test_weighted_mean_shifts_toward_heavily_weighted_values():
    # A weight of 99 on the value 10 must pull the mean far above the
    # unweighted mean of [0, 10].
    assert weighted_mean([0.0, 10.0], [1.0, 99.0]) == pytest.approx(9.9)
    assert weighted_mean([0.0, 10.0], [1.0, 1.0]) == pytest.approx(5.0)


# --------------------------------------------------------------------------- #
# Kish effective sample size
# --------------------------------------------------------------------------- #
def test_kish_ess_equals_n_for_equal_weights():
    for n in (1, 5, 40):
        assert kish_ess([3.3] * n) == pytest.approx(float(n))


def test_kish_ess_shrinks_with_unequal_weights():
    assert kish_ess([1, 1, 1, 1]) == pytest.approx(4.0)
    assert kish_ess([1, 1, 1, 7]) < 4.0
    # pinned oracle: (10)^2 / (1+1+1+49) = 100/52
    assert kish_ess([1, 1, 1, 7]) == pytest.approx(100.0 / 52.0)


def test_kish_ess_empty_is_zero():
    assert kish_ess([]) == 0.0
    assert kish_ess([0.0, -1.0]) == 0.0


# --------------------------------------------------------------------------- #
# pinned numeric oracles (computed offline with numpy)
# --------------------------------------------------------------------------- #
def test_weighted_mean_var_pinned_oracle():
    xs = [1.0, 2.0, 3.0, 4.0]
    w = [1.0, 2.0, 3.0, 4.0]
    # mean = (1+4+9+16)/10 = 3.0
    assert weighted_mean(xs, w) == pytest.approx(3.0)
    # sum w = 10, sum w^2 = 30 -> factor = 10/(100-30) = 1/7
    # ss = 1*4 + 2*1 + 3*0 + 4*1 = 10 -> var = 10/7
    assert weighted_var(xs, w) == pytest.approx(10.0 / 7.0)
    assert weighted_sd(xs, w) == pytest.approx(math.sqrt(10.0 / 7.0))


def test_weighted_var_undefined_cases():
    assert math.isnan(weighted_var([1.0], [1.0]))          # n < 2
    assert math.isnan(weighted_var([], []))
    # all the mass on one observation after zero-weights are dropped
    assert math.isnan(weighted_var([1.0, 2.0], [0.0, 5.0]))


def test_non_positive_and_nonfinite_weights_are_dropped():
    # zero/negative/NaN weights contribute nothing; the surviving pair decides
    assert weighted_mean([1.0, 2.0, 99.0], [1.0, 1.0, 0.0]) == pytest.approx(1.5)
    assert weighted_mean([1.0, 2.0, 99.0], [1.0, 1.0, -3.0]) == pytest.approx(1.5)
    assert weighted_mean([1.0, 2.0, 99.0],
                         [1.0, 1.0, float("nan")]) == pytest.approx(1.5)
    assert math.isnan(weighted_mean([1.0], [0.0]))


def test_length_mismatch_raises():
    with pytest.raises(ValueError):
        weighted_mean([1.0, 2.0], [1.0])
    with pytest.raises(ValueError):
        weighted_categorical_smd([1.0, 2.0], [1.0])


def test_weighted_smd_degenerate_cases():
    assert weighted_continuous_smd([1.0], [1.0], [2.0, 3.0], [1.0, 1.0]) is None
    # both groups constant and equal -> 0; constant and different -> inf
    assert weighted_continuous_smd([5.0, 5.0], [1.0, 2.0],
                                   [5.0, 5.0], [1.0, 1.0]) == 0.0
    assert weighted_continuous_smd([5.0, 5.0], [1.0, 2.0],
                                   [9.0, 9.0], [1.0, 1.0]) == float("inf")
    assert weighted_categorical_smd([0.0, 0.0], [1.0, 1.0]) is None


# --------------------------------------------------------------------------- #
# builder integration
# --------------------------------------------------------------------------- #
def _wframe():
    header = ["arm", "age", "sex", "w"]
    rows = [
        ["device", "50", "M", "1"],
        ["device", "60", "F", "3"],
        ["sham", "40", "M", "2"],
        ["sham", "70", "F", "2"],
    ]
    return Frame(header, rows)


def test_weighted_group_mean_uses_weights():
    t = build_table1(_wframe(), Options(group_col="arm", weight_col="w"))
    age = [r for r in t.rows if r.name == "age"][0]
    # device: (50*1 + 60*3)/4 = 57.5   sham: (40*2 + 70*2)/4 = 55
    assert age.per_group[0].mean == pytest.approx(57.5)
    assert age.per_group[1].mean == pytest.approx(55.0)
    # n stays the RAW count, not the weight sum
    assert age.per_group[0].n == 2
    assert age.per_group[0].wsum == pytest.approx(4.0)


def test_weighted_mode_suppresses_p_and_effect_and_padjust():
    t = build_table1(_wframe(), Options(group_col="arm", weight_col="w",
                                        effect=True, padjust="holm"))
    assert t.meta["weighted"] is True
    assert t.meta["show_pvalue"] is False
    assert t.meta["effect"] is False
    assert t.meta["padjust"] == "none"
    for r in t.rows:
        assert r.pvalue is None
        assert r.p_adjusted is None
        assert r.effect is None
        assert r.test_name == "—"
    # and the reason is stated, not silent
    assert any("설계기반" in w or "design-based" in w for w in t.warnings)


def test_weighted_smd_differs_from_unweighted():
    f = _wframe()
    w = build_table1(f, Options(group_col="arm", weight_col="w"))
    u = build_table1(f, Options(group_col="arm"))
    aw = [r for r in w.rows if r.name == "age"][0].smd
    au = [r for r in u.rows if r.name == "age"][0].smd
    assert aw != pytest.approx(au)


# --------------------------------------------------------------------------- #
# UNEQUAL-weight end-to-end oracles.
#
# An equal-weight fixture cannot distinguish "weighted" from "unweighted" — the
# two agree by construction — so every wiring bug between build/render and
# weights.py is invisible to it. These fixtures use deliberately lopsided
# weights and pin values computed independently with numpy, so that swapping any
# weighted statistic for its unweighted counterpart, or feeding raw counts where
# weighted ones belong, fails loudly.
# --------------------------------------------------------------------------- #
def _uneq_frame():
    """arm a: x=[10,20,30] w=[1,1,6];  arm b: x=[10,20,30] w=[6,1,1].

    Both arms hold the SAME values, so every unweighted statistic is identical
    between them (SMD would be exactly 0) — only the weights separate them.
    """
    header = ["arm", "x", "w"]
    rows = [
        ["a", "10", "1"], ["a", "20", "1"], ["a", "30", "6"],
        ["b", "10", "6"], ["b", "20", "1"], ["b", "30", "1"],
    ]
    return Frame(header, rows)


def test_unequal_weights_pin_mean_sd_median_iqr():
    """Kills: _summ silently using the unweighted sd/median/q1/q3."""
    t = build_table1(_uneq_frame(), Options(group_col="arm", weight_col="w"))
    x = [r for r in t.rows if r.name == "x"][0]
    a, b = x.per_group
    # numpy oracles (Austin & Stuart variance; type-7-generalized quantiles)
    assert a.mean == pytest.approx(26.25)
    assert b.mean == pytest.approx(13.75)
    assert a.sd == pytest.approx(10.9192842820, abs=1e-9)
    assert a.median == pytest.approx(27.1428571429, abs=1e-9)
    assert a.q1 == pytest.approx(23.3333333333, abs=1e-9)
    assert a.q3 == pytest.approx(30.0, abs=1e-9)
    assert b.median == pytest.approx(12.8571428571, abs=1e-9)
    # and they must NOT equal the unweighted values for the same cells
    assert a.sd != pytest.approx(10.0)      # unweighted sd of [10,20,30]
    assert a.median != pytest.approx(20.0)  # unweighted median
    assert a.ess == pytest.approx(1.6842105263, abs=1e-9)


def test_unequal_weights_pin_continuous_smd():
    """Kills: continuous SMD computed from unweighted values (would be 0 here)."""
    t = build_table1(_uneq_frame(), Options(group_col="arm", weight_col="w"))
    x = [r for r in t.rows if r.name == "x"][0]
    assert x.smd == pytest.approx(1.1447636747, abs=1e-9)
    # the unweighted SMD of these two identical groups is exactly 0
    u = build_table1(_uneq_frame(), Options(group_col="arm",
                                            var_cols=["x"]))
    assert [r for r in u.rows if r.name == "x"][0].smd == pytest.approx(0.0)


def _uneq_cat_frame():
    """arm a: M(w=1), F(w=9);  arm b: M(w=8), F(w=2).

    Each arm has exactly one M and one F row, so on RAW counts both arms are
    50/50 and the categorical SMD is 0. Only the weights separate them.
    """
    header = ["arm", "sex", "w"]
    rows = [
        ["a", "M", "1"], ["a", "F", "9"],
        ["b", "M", "8"], ["b", "F", "2"],
    ]
    return Frame(header, rows)


def test_unequal_weights_pin_categorical_smd():
    """Kills: weighted categorical SMD fed RAW counts (would be exactly 0)."""
    t = build_table1(_uneq_cat_frame(), Options(group_col="arm", weight_col="w"))
    sex = [r for r in t.rows if r.name == "sex"][0]
    assert sex.smd == pytest.approx(1.9798989873, abs=1e-9)
    assert sex.smd != pytest.approx(0.0)


def test_rendered_weighted_categorical_cell_uses_weighted_count_and_percent():
    """Kills: _cat_cell_for using the raw count or the raw percent basis.

    On raw counts every cell here would read '1 (50.0)'; the weighted table must
    show the summed weight and the weighted percent.
    """
    opt = Options(group_col="arm", weight_col="w")
    t = build_table1(_uneq_cat_frame(), opt)
    md = render(t, opt, fmt="md")
    lines = [ln for ln in md.splitlines() if ln.startswith("|  M")
             or ln.startswith("|  F")]
    body = "\n".join(lines)
    # arm a: M weight 1 of 10 -> 1.0 (10.0);  arm b: M weight 8 of 10 -> 8.0 (80.0)
    assert "1.0 (10.0)" in body
    assert "8.0 (80.0)" in body
    assert "9.0 (90.0)" in body
    assert "2.0 (20.0)" in body
    assert "(50.0)" not in body          # the raw-count percent
    assert "| 1 (" not in body           # a raw integer count


def test_rendered_weighted_overall_cell_uses_weighted_totals():
    opt = Options(group_col="arm", weight_col="w")
    t = build_table1(_uneq_cat_frame(), opt)
    md = render(t, opt, fmt="md")
    m_line = [ln for ln in md.splitlines() if ln.startswith("|  M")][0]
    # Overall M weight = 1+8 = 9 of 20 -> 9.0 (45.0)
    assert "9.0 (45.0)" in m_line


def test_rendered_weighted_row_percent_basis():
    """Kills: --pct row using the raw level total under weighting."""
    opt = Options(group_col="arm", weight_col="w", pct="row")
    t = build_table1(_uneq_cat_frame(), opt)
    md = render(t, opt, fmt="md")
    m_line = [ln for ln in md.splitlines() if ln.startswith("|  M")][0]
    # row basis: M total weight = 9; arm a share = 1/9 = 11.1%, arm b = 8/9 = 88.9%
    assert "1.0 (11.1)" in m_line
    assert "8.0 (88.9)" in m_line


def test_rendered_header_pins_the_ess_value():
    """Kills: _ess_suffix rendering weight_sums (4.0) instead of ESS (1.6)."""
    opt = Options(group_col="arm", weight_col="w")
    t = build_table1(_wframe(), opt)
    md = render(t, opt, fmt="md")
    # device weights [1,3] -> ESS = 4^2/(1+9) = 1.6 ; weight sum would be 4.0
    assert "device (n=2, ESS=1.6)" in md
    assert "sham (n=2, ESS=2.0)" in md
    assert "ESS=4.0" not in md


def test_overall_ess_is_pooled_not_summed():
    """Kills: meta['ess_overall'] computed as the sum of per-group ESS.

    Pooling over all four weights [1,3,2,2] gives 64/18 = 3.5556; naively adding
    the per-group ESS gives 1.6 + 2.0 = 3.6.
    """
    opt = Options(group_col="arm", weight_col="w")
    t = build_table1(_wframe(), opt)
    assert t.meta["ess_overall"] == pytest.approx(64.0 / 18.0, abs=1e-9)
    assert t.meta["ess_overall"] != pytest.approx(sum(t.meta["ess"]))
    assert f"ESS={64.0 / 18.0:.1f}" in render(t, opt, fmt="md")


def test_weighted_quantile_pinned_oracle_unequal_weights():
    """A pinned oracle for unequal weights — every other quantile test either
    uses equal weights (and delegates to the code's own _quantile sibling) or
    only checks properties."""
    xs = [10.0, 20.0, 30.0]
    w = [1.0, 1.0, 6.0]
    assert weighted_quantile(xs, w, 0.5) == pytest.approx(27.1428571429, abs=1e-9)
    assert weighted_quantile(xs, w, 0.25) == pytest.approx(23.3333333333, abs=1e-9)
    assert weighted_quantile(xs, w, 0.75) == pytest.approx(30.0, abs=1e-9)
    assert weighted_quantile(xs, w, 0.0) == pytest.approx(10.0)
    assert weighted_quantile(xs, w, 1.0) == pytest.approx(30.0)


def test_weighted_multilevel_smd_unequal_weights_through_builder():
    """The k>2 Yang-Dalton Mahalanobis path, reached with genuinely unequal
    weights (not just unit weights)."""
    header = ["arm", "site", "w"]
    rows = [["a", "A", "1"], ["a", "B", "5"], ["a", "C", "4"],
            ["b", "A", "7"], ["b", "B", "2"], ["b", "C", "1"]]
    t = build_table1(Frame(header, rows), Options(group_col="arm",
                                                  weight_col="w"))
    site = [r for r in t.rows if r.name == "site"][0]
    # raw counts are 1/1/1 in both arms -> unweighted SMD would be exactly 0
    assert site.smd is not None
    assert site.smd > 0.5
    u = build_table1(Frame(header, rows), Options(group_col="arm",
                                                  var_cols=["site"]))
    assert [r for r in u.rows if r.name == "site"][0].smd == pytest.approx(0.0)


def test_zero_weight_rows_are_dropped_from_the_quantile_normalization():
    """A zero-weighted observation must not influence a quantile at all.

    ``weighted_quantile`` normalizes the weights to sum to ``n = len(xs)``, so
    merely *keeping* a zero-weight point (weight 0, contributing nothing to any
    sum) still perturbs every other point's grid position. This input is one
    where dropping vs keeping genuinely disagree — many do not, which is why a
    casually chosen example fails to pin the behavior.
    """
    xs = [11.0, 44.0, 12.0]
    w = [2.0, 1.0, 0.0]
    # Correct: the zero-weight 12.0 is dropped -> quantiles of [11,44] w=[2,1].
    assert weighted_quantile(xs, w, 0.5) == pytest.approx(
        weighted_quantile([11.0, 44.0], [2.0, 1.0], 0.5))
    assert weighted_quantile(xs, w, 0.5) == pytest.approx(22.0)
    assert weighted_quantile(xs, w, 0.25) == pytest.approx(13.75)
    # Keeping it would give 11.5 / 11.0 instead.
    assert weighted_quantile(xs, w, 0.5) != pytest.approx(11.5)
    assert weighted_mean([10.0, 30.0, 999.0], [1.0, 1.0, 0.0]) == pytest.approx(20.0)
    assert kish_ess([1.0, 1.0, 0.0]) == pytest.approx(2.0)


def test_equal_weights_column_reproduces_unweighted_table():
    """The strongest end-to-end invariant: a constant weight column must give
    the same summaries as no weighting at all."""
    header = ["arm", "age", "sex", "w"]
    rows = [
        ["device", "50", "M", "2"], ["device", "61", "F", "2"],
        ["device", "55", "F", "2"], ["sham", "40", "M", "2"],
        ["sham", "70", "F", "2"], ["sham", "58", "M", "2"],
    ]
    f = Frame(header, rows)
    w = build_table1(f, Options(group_col="arm", weight_col="w"))
    u = build_table1(f, Options(group_col="arm", var_cols=["age", "sex"]))
    wa = [r for r in w.rows if r.name == "age"][0]
    ua = [r for r in u.rows if r.name == "age"][0]
    for gi in range(2):
        assert wa.per_group[gi].mean == pytest.approx(ua.per_group[gi].mean)
        assert wa.per_group[gi].sd == pytest.approx(ua.per_group[gi].sd)
        assert wa.per_group[gi].median == pytest.approx(ua.per_group[gi].median)
        assert wa.per_group[gi].q1 == pytest.approx(ua.per_group[gi].q1)
        assert wa.per_group[gi].q3 == pytest.approx(ua.per_group[gi].q3)
    assert wa.smd == pytest.approx(ua.smd)
    # ESS == n exactly under equal weights
    assert w.meta["ess"][0] == pytest.approx(3.0)


def test_weight_column_excluded_from_auto_variables():
    t = build_table1(_wframe(), Options(group_col="arm", weight_col="w"))
    assert "w" not in [r.name for r in t.rows]


def test_rows_with_unusable_weights_are_dropped_and_warned():
    header = ["arm", "age", "w"]
    rows = [
        ["device", "50", "1"], ["device", "60", "0"],      # zero weight
        ["device", "55", ""],                               # missing weight
        ["sham", "40", "-2"],                               # negative weight
        ["sham", "70", "2"], ["sham", "65", "abc"],         # non-numeric
    ]
    t = build_table1(Frame(header, rows), Options(group_col="arm",
                                                  weight_col="w"))
    assert t.group_sizes == [1, 1]
    assert any("제외" in w or "excluded" in w for w in t.warnings)


def test_weight_column_errors():
    with pytest.raises(ValueError):
        build_table1(_wframe(), Options(group_col="arm", weight_col="nope"))
    with pytest.raises(ValueError):
        build_table1(_wframe(), Options(group_col="arm", weight_col="arm"))
    # no usable weight anywhere
    f = Frame(["arm", "age", "w"], [["a", "1", "0"], ["b", "2", "x"]])
    with pytest.raises(ValueError):
        build_table1(f, Options(group_col="arm", weight_col="w"))


def test_weighted_categorical_counts_and_percent():
    t = build_table1(_wframe(), Options(group_col="arm", weight_col="w"))
    sex = [r for r in t.rows if r.name == "sex"][0]
    lv = {l.label: l for l in sex.levels}
    # device: M weight 1, F weight 3 -> denom 4
    assert lv["M"].wcounts[0] == pytest.approx(1.0)
    assert lv["F"].wcounts[0] == pytest.approx(3.0)
    assert sex.wdenom_per_group[0] == pytest.approx(4.0)
    # raw counts untouched
    assert lv["M"].counts[0] == 1


def test_weighted_render_shows_ess_and_no_p_column():
    opt = Options(group_col="arm", weight_col="w")
    t = build_table1(_wframe(), opt)
    md = render(t, opt, fmt="md")
    assert "ESS=" in md
    assert "| p값 |" not in md
    assert "| SMD |" in md
    assert "가중 분석" in md


def test_weighted_json_exposes_weighted_fields():
    opt = Options(group_col="arm", weight_col="w")
    t = build_table1(_wframe(), opt)
    obj = json.loads(render(t, opt, fmt="json"))
    assert obj["meta"]["weighted"] is True
    assert obj["meta"]["weight_col"] == "w"
    assert obj["meta"]["ess"][0] == pytest.approx(4.0 ** 2 / (1 + 9))
    age = [r for r in obj["rows"] if r["name"] == "age"][0]
    assert age["groups"][0]["weight_sum"] == pytest.approx(4.0)
    assert age["p_value"] is None
    sex = [r for r in obj["rows"] if r["name"] == "sex"][0]
    assert sex["weighted_denom_per_group"][0] == pytest.approx(4.0)


def test_json_meta_never_leaks_per_row_weights():
    """meta is serialized to the user; a per-row weight vector is row-level data
    and must not appear there.

    Asserted as a SHAPE property, not a banned key name: guarding only the
    historical name 'all_weights' would let the identical defect back in under
    any other key.
    """
    header = ["arm", "age", "w"]
    rows = [["a", "50", "1.5"], ["a", "60", "2.5"], ["a", "55", "3.5"],
            ["b", "40", "4.5"], ["b", "70", "5.5"], ["b", "65", "6.5"]]
    opt = Options(group_col="arm", weight_col="w")
    t = build_table1(Frame(header, rows), opt)
    obj = json.loads(render(t, opt, fmt="json"))
    raw_weights = [1.5, 2.5, 3.5, 4.5, 5.5, 6.5]
    n_groups = len(t.groups)

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            # Nothing in meta is per-row: every list is per-group at most.
            assert len(node) <= n_groups, f"meta holds a row-length list: {node}"
            for v in node:
                assert v not in raw_weights or len(node) <= n_groups
                walk(v)

    walk(obj["meta"])
    # and no individual raw weight is echoed as a scalar
    for k, v in obj["meta"].items():
        if isinstance(v, float):
            assert v not in raw_weights, f"meta['{k}'] is a raw row weight"
    assert isinstance(obj["meta"]["ess_overall"], float)


def test_unweighted_table_has_no_weighted_fields():
    opt = Options(group_col="arm")
    t = build_table1(_wframe(), opt)
    obj = json.loads(render(t, opt, fmt="json"))
    assert obj["meta"]["weighted"] is False
    age = [r for r in obj["rows"] if r["name"] == "age"][0]
    assert age["groups"][0]["weight_sum"] is None
    sex = [r for r in obj["rows"] if r["name"] == "sex"][0]
    assert sex["levels"][0]["weighted_counts"] is None


@pytest.mark.parametrize("fmt", ["md", "csv", "tsv", "json", "html"])
@pytest.mark.parametrize("lang", ["ko", "en"])
def test_weighted_renders_in_every_format_and_language(fmt, lang):
    opt = Options(group_col="arm", weight_col="w", lang=lang)
    t = build_table1(_wframe(), opt)
    out = render(t, opt, fmt=fmt)
    assert out.strip()
    if fmt == "json":
        json.loads(out)


def test_weighted_single_group_descriptive_table():
    """--weights with no --group: a weighted whole-cohort description."""
    opt = Options(group_col=None, weight_col="w", var_cols=["age", "sex"])
    t = build_table1(_wframe(), opt)
    age = [r for r in t.rows if r.name == "age"][0]
    # (50*1 + 60*3 + 40*2 + 70*2)/8 = 56.25
    assert age.overall.mean == pytest.approx(56.25)
    assert render(t, opt, fmt="md").strip()


def test_weighted_three_groups_has_no_smd_column():
    header = ["arm", "age", "w"]
    rows = [["a", "1", "1"], ["a", "2", "2"], ["b", "3", "1"],
            ["b", "9", "1"], ["c", "5", "1"], ["c", "7", "3"]]
    opt = Options(group_col="arm", weight_col="w")
    t = build_table1(Frame(header, rows), opt)
    assert len(t.groups) == 3
    md = render(t, opt, fmt="md")
    assert "| SMD |" not in md   # SMD is a two-group metric


def test_weighted_with_missing_values_pairs_weights_correctly():
    """A dropped value must drop its own weight, not shift the alignment."""
    header = ["arm", "age", "w"]
    rows = [
        ["device", "", "99"],      # missing age, huge weight -> must not count
        ["device", "50", "1"],
        ["device", "60", "1"],
        ["sham", "40", "1"],
        ["sham", "80", "1"],
    ]
    t = build_table1(Frame(header, rows), Options(group_col="arm",
                                                  weight_col="w"))
    age = [r for r in t.rows if r.name == "age"][0]
    # If the 99 weight leaked onto a real value the mean would be far off 55.
    assert age.per_group[0].mean == pytest.approx(55.0)
    assert age.per_group[0].n == 2
    assert age.per_group[0].n_missing == 1


def test_weighted_nonnormal_uses_weighted_median():
    """--nonnormal + --weights must report the WEIGHTED median.

    The weights here are deliberately unequal: with all-ones the weighted and
    unweighted medians coincide, so the test would pass even if the builder
    ignored the weights entirely.
    """
    header = ["arm", "x", "w"]
    rows = [["a", "10", "1"], ["a", "20", "1"], ["a", "30", "6"],
            ["b", "3", "1"], ["b", "4", "1"], ["b", "5", "1"]]
    opt = Options(group_col="arm", weight_col="w", nonnormal=["x"])
    t = build_table1(Frame(header, rows), opt)
    x = [r for r in t.rows if r.name == "x"][0]
    assert x.display == "median"
    # weighted median (numpy oracle), NOT the unweighted 20.0
    assert x.per_group[0].median == pytest.approx(27.1428571429, abs=1e-9)
    assert x.per_group[0].median != pytest.approx(20.0)
