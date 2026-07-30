"""Binary-endpoint statistics, validated against scipy / statsmodels.

Reference values were produced with scipy 1.17.1 (``fisher_exact``,
``chi2_contingency``, ``contingency.association``) and statsmodels 0.14.6
(``proportion_confint``, ``confint_proportions_2indep``, ``Table2x2``) and are
hard-coded so the suite stays dependency-free.

Note on the odds ratio: the values below are the *sample* odds ratio with the
Woolf logit interval (statsmodels ``Table2x2``), which is what this package
computes — not scipy's conditional-MLE ``contingency.odds_ratio``, whose point
estimate and interval differ.
"""

import json
import math

import pytest

from statwise.binary import (BinaryGroup, chi_square_contingency, compare_binary,
                             cramers_v, fisher_exact_2x2,
                             number_needed_to_treat, odds_ratio,
                             risk_difference, risk_ratio, wilson_interval)
from statwise.report import binary_to_dict, render_binary_json, render_binary_text


# --------------------------------------------------------------------------
# omnibus tests vs scipy
# --------------------------------------------------------------------------

@pytest.mark.parametrize("a,b,c,d,expected", [
    # scipy.stats.fisher_exact([[a, b], [c, d]])[1]
    (10, 40, 22, 30, 0.01921553506462474),
    (3, 17, 12, 8, 0.007911693673651405),
    (0, 10, 5, 5, 0.032507739938080496),
    (1, 1, 1, 1, 1.0),
    (5, 0, 0, 5, 0.007936507936507938),
    (20, 30, 20, 30, 1.0),
])
def test_fisher_exact_matches_scipy(a, b, c, d, expected):
    assert fisher_exact_2x2(a, b, c, d) == pytest.approx(expected, rel=1e-9)


def test_fisher_is_symmetric_under_transposition():
    """Swapping rows or columns cannot change a two-sided Fisher p-value."""
    a, b, c, d = 7, 13, 15, 9
    p = fisher_exact_2x2(a, b, c, d)
    assert fisher_exact_2x2(c, d, a, b) == pytest.approx(p, rel=1e-12)
    assert fisher_exact_2x2(b, a, d, c) == pytest.approx(p, rel=1e-12)
    assert fisher_exact_2x2(d, c, b, a) == pytest.approx(p, rel=1e-12)


def test_fisher_degenerate_margin_is_one():
    assert fisher_exact_2x2(0, 5, 0, 7) == 1.0
    assert fisher_exact_2x2(4, 0, 6, 0) == 1.0


def test_fisher_rejects_negative_cells():
    with pytest.raises(ValueError):
        fisher_exact_2x2(-1, 5, 3, 2)


def test_chi_square_matches_scipy_2x2():
    # scipy.stats.chi2_contingency([[10, 40], [22, 30]], correction=False)
    chi2, df, p, min_exp = chi_square_contingency([[10, 40], [22, 30]])
    assert chi2 == pytest.approx(5.89162087912088, rel=1e-10)
    assert df == 1.0
    assert p == pytest.approx(0.015213091608169905, rel=1e-9)
    assert min_exp == pytest.approx(15.686274509803921, rel=1e-12)


def test_chi_square_yates_matches_scipy():
    _, _, p, _ = chi_square_contingency([[10, 40], [22, 30]], correction=True)
    # scipy.stats.chi2_contingency(..., correction=True)
    assert p == pytest.approx(0.02684019263970746, rel=1e-9)


def test_chi_square_kx2_matches_scipy():
    chi2, df, p, _ = chi_square_contingency([[10, 30], [20, 20], [25, 15]])
    assert chi2 == pytest.approx(11.748251748251748, rel=1e-10)
    assert df == 2.0
    assert p == pytest.approx(0.00281125050761305, rel=1e-8)


def test_chi_square_recomputed_by_hand():
    """Recompute X^2 = sum (O-E)^2/E from first principles."""
    table = [[12, 38], [27, 25]]
    n = 102
    rows = [50, 52]
    cols = [39, 63]
    expect = 0.0
    for i in range(2):
        for j in range(2):
            e = rows[i] * cols[j] / n
            expect += (table[i][j] - e) ** 2 / e
    chi2, _, _, _ = chi_square_contingency(table)
    assert chi2 == pytest.approx(expect, rel=1e-12)


def test_chi_square_empty_column_is_independent():
    chi2, df, p, min_exp = chi_square_contingency([[0, 10], [0, 12]])
    assert chi2 == 0.0
    assert p == 1.0
    assert min_exp == 0.0


def test_chi_square_rejects_ragged_and_empty_tables():
    with pytest.raises(ValueError):
        chi_square_contingency([[1, 2], [3]])
    with pytest.raises(ValueError):
        chi_square_contingency([])
    with pytest.raises(ValueError):
        chi_square_contingency([[0, 0], [0, 0]])


# --------------------------------------------------------------------------
# intervals
# --------------------------------------------------------------------------

@pytest.mark.parametrize("e,n,lo,hi", [
    # statsmodels proportion_confint(e, n, 0.05, method='wilson')
    (10, 50, 0.11243750015776109, 0.33037105932225413),
    (0, 20, 0.0, 0.1611251580528194),
    (20, 20, 0.8388748419471804, 1.0),
    (1, 1, 0.2065493143772374, 1.0),
])
def test_wilson_matches_statsmodels(e, n, lo, hi):
    got_lo, got_hi = wilson_interval(e, n)
    assert got_lo == pytest.approx(lo, abs=1e-12)
    assert got_hi == pytest.approx(hi, abs=1e-12)


@pytest.mark.parametrize("n", [1, 3, 7, 20, 137])
def test_wilson_never_leaves_the_unit_interval(n):
    for e in range(n + 1):
        lo, hi = wilson_interval(e, n)
        assert 0.0 <= lo <= e / n <= hi <= 1.0


def test_wilson_rejects_impossible_counts():
    with pytest.raises(ValueError):
        wilson_interval(5, 3)
    assert all(math.isnan(x) for x in wilson_interval(0, 0))


def test_risk_difference_matches_statsmodels_newcombe():
    a, b = BinaryGroup("a", 12, 50), BinaryGroup("b", 5, 50)
    rd = risk_difference(a, b)
    # statsmodels confint_proportions_2indep(12, 50, 5, 50, method='newcomb')
    assert rd.value == pytest.approx(0.14, abs=1e-12)
    assert rd.ci_low == pytest.approx(-0.009397279766490851, abs=1e-9)
    assert rd.ci_high == pytest.approx(0.2855506705896387, abs=1e-9)


def test_odds_ratio_matches_statsmodels():
    a, b = BinaryGroup("a", 12, 50), BinaryGroup("b", 5, 50)
    orr = odds_ratio(a, b)
    assert orr.value == pytest.approx(2.8421052631578947, rel=1e-12)
    assert orr.ci_low == pytest.approx(0.9189198341542734, rel=1e-9)
    assert orr.ci_high == pytest.approx(8.790279659491711, rel=1e-9)


def test_risk_ratio_matches_statsmodels():
    a, b = BinaryGroup("a", 12, 50), BinaryGroup("b", 5, 50)
    rr = risk_ratio(a, b)
    assert rr.value == pytest.approx(2.4, rel=1e-12)
    assert rr.ci_low == pytest.approx(0.9126904183465643, rel=1e-9)
    assert rr.ci_high == pytest.approx(6.311011800074391, rel=1e-9)


def test_effect_measures_invert_when_groups_swap():
    a, b = BinaryGroup("a", 12, 50), BinaryGroup("b", 5, 50)
    fwd_rd, rev_rd = risk_difference(a, b), risk_difference(b, a)
    assert rev_rd.value == pytest.approx(-fwd_rd.value)
    assert rev_rd.ci_low == pytest.approx(-fwd_rd.ci_high, abs=1e-12)
    fwd_or, rev_or = odds_ratio(a, b), odds_ratio(b, a)
    assert rev_or.value == pytest.approx(1.0 / fwd_or.value, rel=1e-12)
    assert rev_or.ci_low == pytest.approx(1.0 / fwd_or.ci_high, rel=1e-9)


def test_zero_cell_uses_haldane_for_the_interval_only():
    a, b = BinaryGroup("a", 0, 20), BinaryGroup("b", 6, 20)
    rr = risk_ratio(a, b)
    assert rr.value == 0.0                 # point estimate stays honest
    assert rr.ci_low is not None and rr.ci_low > 0.0
    assert "Haldane" in rr.note
    orr = odds_ratio(a, b)
    assert orr.value == 0.0
    assert "Haldane" in orr.note


def test_all_events_in_both_arms_is_haldane_corrected():
    """An empty *non-event* cell breaks the Katz variance just like an empty
    event cell, so it must trigger the same correction and the same warning."""
    a, b = BinaryGroup("a", 10, 10), BinaryGroup("b", 8, 8)
    rr = risk_ratio(a, b)
    assert rr.value == pytest.approx(1.0)
    assert rr.ci_low is not None and rr.ci_low < 1.0 < rr.ci_high
    assert "Haldane" in rr.note


def test_full_response_arm_gets_the_same_warning_as_the_odds_ratio():
    """RR used to check only the event column, so a 100%-response arm got an
    uncorrected interval with no note beside an OR that did warn."""
    a, b = BinaryGroup("a", 20, 20), BinaryGroup("b", 5, 20)
    rr, orr = risk_ratio(a, b), odds_ratio(a, b)
    assert "Haldane" in rr.note and "Haldane" in orr.note
    assert rr.ci_low is not None


def test_undefined_odds_ratio_gets_no_interval():
    """No events anywhere: the OR is NaN, so an interval around it means nothing."""
    for a, b in [(BinaryGroup("a", 0, 20), BinaryGroup("b", 0, 20)),
                 (BinaryGroup("a", 20, 20), BinaryGroup("b", 20, 20))]:
        orr = odds_ratio(a, b)
        assert orr.value != orr.value          # NaN
        assert orr.ci_low is None and orr.ci_high is None


def test_nnt_inverts_the_risk_difference():
    a, b = BinaryGroup("a", 30, 50), BinaryGroup("b", 15, 50)
    rd = risk_difference(a, b)
    nnt = number_needed_to_treat(rd)
    assert nnt.value == pytest.approx(1.0 / rd.value, rel=1e-12)
    assert nnt.ci_low == pytest.approx(1.0 / rd.ci_high, rel=1e-12)
    assert nnt.ci_high == pytest.approx(1.0 / rd.ci_low, rel=1e-12)


def test_nnt_withholds_the_interval_when_rd_spans_zero():
    a, b = BinaryGroup("a", 25, 50), BinaryGroup("b", 24, 50)
    nnt = number_needed_to_treat(risk_difference(a, b))
    assert nnt.ci_low is None and nnt.ci_high is None
    assert "0을 포함" in nnt.note


def test_nnt_of_zero_risk_difference_is_infinite():
    a, b = BinaryGroup("a", 25, 50), BinaryGroup("b", 25, 50)
    nnt = number_needed_to_treat(risk_difference(a, b))
    assert math.isinf(nnt.value)


def test_cramers_v_matches_scipy_association():
    """3x2 table with N=120 (the table it comes from), vs scipy association()."""
    v = cramers_v(11.748251748251748, 120, 3, 2)
    assert v.value == pytest.approx(0.3128931093873719, rel=1e-12)
    assert v.magnitude == "medium"


def test_cramers_v_uses_min_of_the_two_dimensions():
    """min(r,c)-1 must be used; with a 3x3 table max() would give a different V."""
    v = cramers_v(20.0, 180, 3, 3)
    assert v.value == pytest.approx(0.23570226039551584, rel=1e-12)


def test_cramers_v_is_bounded_and_degenerate_safe():
    assert cramers_v(1e9, 100, 3, 2).value == 1.0
    assert math.isnan(cramers_v(5.0, 0, 3, 2).value)
    assert math.isnan(cramers_v(5.0, 100, 1, 2).value)


# --------------------------------------------------------------------------
# BinaryGroup validation
# --------------------------------------------------------------------------

def test_binary_group_rejects_impossible_counts():
    with pytest.raises(ValueError, match="cannot exceed"):
        BinaryGroup("a", 12, 5)
    with pytest.raises(ValueError, match="negative"):
        BinaryGroup("a", -1, 5)


# --------------------------------------------------------------------------
# compare_binary orchestration
# --------------------------------------------------------------------------

def test_auto_picks_fisher_for_sparse_tables():
    res = compare_binary([("a", (1, 6)), ("b", (5, 6))])
    assert res.test_name == "Fisher's exact test"
    assert "Fisher" in res.reason
    assert res.statistic is None
    assert res.pvalue == pytest.approx(fisher_exact_2x2(1, 5, 5, 1), rel=1e-12)


def test_auto_picks_chi_square_for_large_tables():
    res = compare_binary([("a", (10, 50)), ("b", (22, 52))])
    assert res.test_name == "Chi-square test of independence"
    assert res.pvalue == pytest.approx(0.015213091608169905, rel=1e-8)
    assert res.pvalue_yates == pytest.approx(0.02684019263970746, rel=1e-8)
    assert res.significant is True


@pytest.mark.parametrize("forced,expected", [
    ("fisher", "Fisher's exact test"),
    ("chisq", "Chi-square test of independence"),
    ("chisq-yates", "Chi-square test (Yates-corrected)"),
])
def test_forced_test_choice_is_honoured(forced, expected):
    res = compare_binary([("a", (10, 50)), ("b", (22, 52))], test=forced)
    assert res.test_name == expected


def test_forced_fisher_rejected_for_three_groups():
    with pytest.raises(ValueError, match="2x2"):
        compare_binary([("a", (1, 6)), ("b", (5, 6)), ("c", (3, 6))],
                       test="fisher")


def test_three_groups_get_cramers_v_and_posthoc():
    res = compare_binary([("low", (10, 40)), ("mid", (20, 40)),
                          ("high", (25, 40))])
    assert res.df == 2.0
    assert res.estimates[0].name == "Cramér's V"
    assert res.significant
    assert len(res.pairwise) == 3
    assert all(pw.pvalue_adj >= pw.pvalue_raw - 1e-12 for pw in res.pairwise)


def test_posthoc_can_be_disabled():
    res = compare_binary([("low", (10, 40)), ("mid", (20, 40)),
                          ("high", (25, 40))], posthoc=False)
    assert res.pairwise == []


def test_bh_correction_is_never_more_conservative_than_holm():
    groups = [("a", (5, 40)), ("b", (15, 40)), ("c", (25, 40)),
              ("d", (30, 40))]
    holm = compare_binary(groups, correction="holm")
    bh = compare_binary(groups, correction="bh")
    for h, b in zip(holm.pairwise, bh.pairwise):
        assert b.pvalue_adj <= h.pvalue_adj + 1e-12


def test_no_events_anywhere_is_flagged_not_crashed():
    res = compare_binary([("a", (0, 20)), ("b", (0, 25))])
    assert res.pvalue == 1.0
    assert not res.significant
    assert any("사건" in w for w in res.warnings)


def test_all_events_everywhere_is_flagged():
    res = compare_binary([("a", (20, 20)), ("b", (25, 25))])
    assert any("사건" in w for w in res.warnings)


def test_compare_binary_validates_inputs():
    with pytest.raises(ValueError, match="at least 2 groups"):
        compare_binary([("a", (5, 10))])
    with pytest.raises(ValueError, match="관측치가 없는"):
        compare_binary([("a", (0, 0)), ("b", (5, 10))])
    with pytest.raises(ValueError):
        compare_binary([("a", (5, 10)), ("b", (5, 10))], test="wat")
    with pytest.raises(ValueError):
        compare_binary([("a", (5, 10)), ("b", (5, 10))], correction="bonf")
    with pytest.raises(ValueError):
        compare_binary([("a", (5, 10)), ("b", (5, 10))], alpha=0.0)


def test_single_subject_arms_do_not_crash():
    """One subject per arm: the only table with these margins, so p must be 1."""
    res = compare_binary([("a", (1, 1)), ("b", (0, 1))])
    assert res.test_name == "Fisher's exact test"
    assert res.pvalue == pytest.approx(1.0)
    assert not res.significant


def test_missing_counts_are_carried_through():
    res = compare_binary([("a", (10, 50)), ("b", (22, 52))],
                         missing={"a": 3})
    assert res.groups[0].n_missing == 3
    assert binary_to_dict(res)["groups"][0]["n_missing"] == 3


def test_alpha_controls_the_reported_interval_width():
    wide = compare_binary([("a", (10, 50)), ("b", (22, 52))], alpha=0.01)
    narrow = compare_binary([("a", (10, 50)), ("b", (22, 52))], alpha=0.10)
    w_rd = wide.estimates[0]
    n_rd = narrow.estimates[0]
    assert w_rd.ci_high - w_rd.ci_low > n_rd.ci_high - n_rd.ci_low
    assert w_rd.conf == pytest.approx(0.99)


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def test_binary_text_report_has_every_section():
    res = compare_binary([("drug", (22, 52)), ("placebo", (10, 50))])
    text = render_binary_text(res)
    for marker in ("[1] 반응률", "[2] 선택된 검정", "[3] 효과 크기",
                   "Wilson", "Risk difference", "Odds ratio",
                   "NNT/NNH", "[논문용 문장"):
        assert marker in text
    assert "RD = p(drug) − p(placebo)" in text


def test_binary_json_is_valid_and_complete():
    res = compare_binary([("drug", (22, 52)), ("placebo", (10, 50))])
    d = json.loads(render_binary_json(res))
    assert d["schema"] == "statwise/binary/1"
    assert d["groups"][0]["events"] == 22
    assert d["test"]["significant"] is True
    names = [e["name"] for e in d["estimates"]]
    assert "Risk difference (RD)" in names
    assert isinstance(d["sentence"], str) and d["sentence"]


def test_binary_json_never_emits_nan_or_infinity():
    res = compare_binary([("a", (0, 20)), ("b", (0, 25))])
    raw = render_binary_json(res)
    assert "NaN" not in raw and "Infinity" not in raw
    json.loads(raw)  # strict parse would fail on NaN/Infinity


def test_posthoc_table_renders_for_three_groups():
    res = compare_binary([("low", (5, 40)), ("mid", (20, 40)),
                          ("high", (30, 40))])
    text = render_binary_text(res)
    assert "[4] 사후검정" in text
    assert "low vs mid" in text


def test_long_group_labels_do_not_break_the_table():
    label = "a" * 60
    res = compare_binary([(label, (10, 50)), ("b", (22, 52))])
    text = render_binary_text(res)
    table = [line for line in text.splitlines()
             if line.startswith("    a") or line.startswith("    b")]
    assert table and max(len(line) for line in table) < 100
    assert "…" in text          # the long label was elided, not wrapped
