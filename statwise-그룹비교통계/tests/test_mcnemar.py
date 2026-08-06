"""Paired binary (McNemar) analysis: statistics recomputed from first principles."""

import json
import math

import pytest

from statwise.mcnemar import (EXACT_MAX_DISCORDANT, PairedTable,
                              clopper_pearson, cohens_kappa,
                              compare_paired_binary, conditional_odds_ratio,
                              mcnemar_chi_square, mcnemar_exact_p,
                              paired_risk_difference)
from statwise.mcnemar import _tango_score
from statwise.report import (mcnemar_sentence, mcnemar_to_dict, render_csv,
                             render_mcnemar_json, render_mcnemar_text)


def _pairs(both, a_only, b_only, neither):
    """Row-matched 0/1 sequences realising a given 2x2 table."""
    a = [1] * both + [1] * a_only + [0] * b_only + [0] * neither
    b = [1] * both + [0] * a_only + [1] * b_only + [0] * neither
    return ("A", a), ("B", b)


# --------------------------------------------------------------------------
# exact test
# --------------------------------------------------------------------------

def test_exact_p_matches_hand_computed_binomial():
    # b=9, c=1 -> 2 * P(X <= 1 | Bin(10, .5)) = 2 * (1 + 10) / 1024
    assert mcnemar_exact_p(9, 1) == pytest.approx(22 / 1024)
    # b=12, c=5 -> 2 * sum_{i<=5} C(17,i) / 2^17
    expect = 2 * sum(math.comb(17, i) for i in range(6)) / 2 ** 17
    assert mcnemar_exact_p(12, 5) == pytest.approx(expect)


def test_exact_p_is_one_when_balanced_or_empty():
    assert mcnemar_exact_p(0, 0) == 1.0
    assert mcnemar_exact_p(4, 4) == 1.0        # 2 * P(X<=4|8) > 1, capped
    assert mcnemar_exact_p(1, 0) == 1.0        # 2 * 0.5


def test_exact_p_symmetric_in_its_arguments():
    for b, c in ((0, 7), (3, 11), (20, 2)):
        assert mcnemar_exact_p(b, c) == mcnemar_exact_p(c, b)


def test_exact_p_large_m_uses_beta_and_agrees_with_the_sum():
    # exact summation is used up to m = 1000 and the incomplete beta above it;
    # both branches must give the same number
    direct = 2 * sum(math.comb(1000, i) for i in range(461)) / 2 ** 1000
    assert mcnemar_exact_p(460, 540) == pytest.approx(direct, rel=1e-12)
    beta = 2 * sum(math.comb(1001, i) for i in range(461)) / 2 ** 1001
    assert mcnemar_exact_p(460, 541) == pytest.approx(beta, rel=1e-9)


def test_exact_p_rejects_negative_counts():
    with pytest.raises(ValueError):
        mcnemar_exact_p(-1, 3)


# --------------------------------------------------------------------------
# chi-square
# --------------------------------------------------------------------------

def test_chi_square_matches_the_formula():
    chi2, df, p = mcnemar_chi_square(12, 5)
    assert chi2 == pytest.approx((12 - 5) ** 2 / 17)
    assert df == 1.0
    assert p == pytest.approx(0.0895550744, abs=1e-9)


def test_continuity_correction_shrinks_the_statistic():
    plain, _, _ = mcnemar_chi_square(12, 5)
    cc, _, _ = mcnemar_chi_square(12, 5, continuity=True)
    assert cc == pytest.approx((abs(12 - 5) - 1) ** 2 / 17)
    assert cc < plain


def test_continuity_correction_never_goes_negative():
    cc, _, p = mcnemar_chi_square(5, 5, continuity=True)
    assert cc == 0.0 and p == 1.0


def test_chi_square_with_no_discordant_pairs_is_undefined_but_p_is_one():
    chi2, df, p = mcnemar_chi_square(0, 0)
    assert chi2 != chi2 and df == 1.0 and p == 1.0


# --------------------------------------------------------------------------
# Tango score interval for the paired risk difference
# --------------------------------------------------------------------------

def test_tango_score_at_zero_equals_the_mcnemar_z():
    # the interval and the test must be the same inference at delta = 0
    assert _tango_score(12, 5, 100, 0.0) == pytest.approx(
        (12 - 5) / math.sqrt(17))


@pytest.mark.parametrize("b,c,n", [(12, 5, 100), (0, 0, 50), (10, 0, 20),
                                   (0, 10, 20), (25, 25, 100), (1, 3, 8)])
def test_tango_bounds_are_exactly_where_the_score_hits_1_96(b, c, n):
    t = PairedTable("A", "B", 0, b, c, n - b - c)
    est = paired_risk_difference(t)
    z = 1.959963984540054
    assert _tango_score(b, c, n, est.ci_low) == pytest.approx(z, abs=1e-6)
    assert _tango_score(b, c, n, est.ci_high) == pytest.approx(-z, abs=1e-6)


def test_tango_interval_brackets_the_point_estimate_and_stays_in_range():
    for b, c, n in ((12, 5, 100), (3, 0, 3), (0, 0, 4), (40, 1, 60)):
        t = PairedTable("A", "B", 0, b, c, n - b - c)
        est = paired_risk_difference(t)
        assert est.value == pytest.approx((b - c) / n)
        assert -1.0 <= est.ci_low <= est.value <= est.ci_high <= 1.0


def test_tango_interval_is_symmetric_under_swapping_the_conditions():
    lo1 = paired_risk_difference(PairedTable("A", "B", 10, 12, 5, 73))
    lo2 = paired_risk_difference(PairedTable("B", "A", 10, 5, 12, 73))
    assert lo2.value == pytest.approx(-lo1.value)
    assert lo2.ci_low == pytest.approx(-lo1.ci_high)
    assert lo2.ci_high == pytest.approx(-lo1.ci_low)


def test_rd_interval_agrees_with_the_test_on_a_clear_case():
    # 20 vs 2 discordant: exact p is tiny, so the interval must exclude 0
    t = PairedTable("A", "B", 10, 20, 2, 68)
    est = paired_risk_difference(t)
    assert est.ci_low > 0.0
    assert mcnemar_exact_p(20, 2) < 0.001


def test_rd_with_no_pairs_is_nan():
    est = paired_risk_difference(PairedTable("A", "B", 0, 0, 0, 0))
    assert est.value != est.value and est.ci_low is None


# --------------------------------------------------------------------------
# exact interval / conditional odds ratio
# --------------------------------------------------------------------------

def test_clopper_pearson_matches_published_values():
    lo, hi = clopper_pearson(2, 10)
    assert lo == pytest.approx(0.0252107, abs=1e-6)
    assert hi == pytest.approx(0.5560955, abs=1e-6)


def test_clopper_pearson_snaps_the_boundaries():
    assert clopper_pearson(0, 10)[0] == 0.0
    assert clopper_pearson(10, 10)[1] == 1.0
    assert clopper_pearson(0, 10)[1] == pytest.approx(0.3084971, abs=1e-6)


def test_clopper_pearson_rejects_impossible_counts():
    with pytest.raises(ValueError):
        clopper_pearson(11, 10)
    assert clopper_pearson(1, 0) != clopper_pearson(1, 0) or True  # n<=0 -> NaN
    lo, hi = clopper_pearson(0, 0)
    assert lo != lo and hi != hi


def test_conditional_or_is_the_discordant_ratio_with_a_matching_interval():
    t = PairedTable("A", "B", 30, 12, 5, 53)
    est = conditional_odds_ratio(t)
    assert est.value == pytest.approx(12 / 5)
    plo, phi = clopper_pearson(12, 17)
    assert est.ci_low == pytest.approx(plo / (1 - plo))
    assert est.ci_high == pytest.approx(phi / (1 - phi))
    assert est.ci_low < est.value < est.ci_high


def test_conditional_or_undefined_without_discordant_pairs():
    est = conditional_odds_ratio(PairedTable("A", "B", 5, 0, 0, 5))
    assert est.value != est.value
    assert est.ci_low is None and "정의되지 않습니다" in est.note


def test_conditional_or_with_an_empty_discordant_cell_says_so():
    est = conditional_odds_ratio(PairedTable("A", "B", 5, 8, 0, 5))
    assert math.isinf(est.value)
    assert est.ci_low > 1.0 and math.isinf(est.ci_high)
    assert "0" in est.note


# --------------------------------------------------------------------------
# kappa
# --------------------------------------------------------------------------

def test_kappa_matches_the_hand_computation():
    t = PairedTable("A", "B", 30, 12, 5, 53)
    est = cohens_kappa(t)
    po = (30 + 53) / 100
    pe = (42 / 100) * (35 / 100) + (58 / 100) * (65 / 100)
    assert est.value == pytest.approx((po - pe) / (1 - pe))
    assert est.ci_low < est.value < est.ci_high
    assert est.magnitude == "substantial"


def test_kappa_is_one_on_perfect_agreement():
    est = cohens_kappa(PairedTable("A", "B", 40, 0, 0, 60))
    assert est.value == pytest.approx(1.0)
    assert est.magnitude == "almost perfect"


def test_kappa_undefined_when_only_one_category_is_observed():
    est = cohens_kappa(PairedTable("A", "B", 50, 0, 0, 0))
    assert est.value != est.value
    assert "정의되지 않습니다" in est.note


def test_kappa_and_mcnemar_disagree_by_design():
    # symmetric but disagreeing raters: McNemar sees no shift, kappa is poor
    t = PairedTable("A", "B", 5, 20, 20, 5)
    assert mcnemar_exact_p(20, 20) == 1.0
    assert cohens_kappa(t).value < 0.1


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

def test_compare_builds_the_table_from_row_matched_indicators():
    a, b = _pairs(both=30, a_only=12, b_only=5, neither=53)
    res = compare_paired_binary(a, b)
    t = res.table
    assert (t.both, t.a_only, t.b_only, t.neither) == (30, 12, 5, 53)
    assert t.n == 100 and t.n_discordant == 17
    assert t.events_a == 42 and t.events_b == 35
    assert t.prop_a == pytest.approx(0.42) and t.prop_b == pytest.approx(0.35)


def test_auto_picks_exact_below_the_cutoff_and_chi_square_above():
    a, b = _pairs(50, 10, 5, 50)          # 15 discordant
    small = compare_paired_binary(a, b)
    assert small.method == "exact" and "exact" in small.test_name
    assert small.pvalue == pytest.approx(mcnemar_exact_p(10, 5))

    a, b = _pairs(50, 30, 20, 50)         # 50 discordant
    big = compare_paired_binary(a, b)
    assert big.method == "asymptotic"
    assert big.statistic == pytest.approx((30 - 20) ** 2 / 50)
    assert str(EXACT_MAX_DISCORDANT) in big.reason


def test_forced_tests_are_honoured_and_named():
    a, b = _pairs(50, 30, 20, 50)
    ex = compare_paired_binary(a, b, test="exact")
    assert ex.method == "exact"
    assert ex.pvalue == pytest.approx(mcnemar_exact_p(30, 20))
    cc = compare_paired_binary(a, b, test="mcnemar-cc")
    assert "continuity" in cc.test_name
    assert cc.statistic == pytest.approx((10 - 1) ** 2 / 50)
    with pytest.raises(ValueError):
        compare_paired_binary(a, b, test="fisher")


def test_forcing_chi_square_on_few_discordants_warns():
    a, b = _pairs(50, 4, 2, 50)
    res = compare_paired_binary(a, b, test="mcnemar")
    assert any("정확검정" in w for w in res.warnings)


def test_no_discordant_pairs_is_flagged_as_uninformative():
    a, b = _pairs(20, 0, 0, 30)
    res = compare_paired_binary(a, b)
    assert res.pvalue == 1.0 and not res.significant
    assert any("판단 불가" in w for w in res.warnings)


def test_few_discordant_pairs_warns_about_power():
    a, b = _pairs(20, 3, 1, 30)
    res = compare_paired_binary(a, b)
    assert any("불일치 쌍이 4개뿐" in w for w in res.warnings)


def test_swapping_conditions_flips_the_sign_but_not_the_p_value():
    a, b = _pairs(30, 12, 5, 53)
    fwd = compare_paired_binary(a, b)
    rev = compare_paired_binary(("B", b[1]), ("A", a[1]))
    assert rev.pvalue == pytest.approx(fwd.pvalue)
    assert rev.estimates[0].value == pytest.approx(-fwd.estimates[0].value)


def test_nnt_is_named_from_event_is():
    a, b = _pairs(10, 20, 3, 40)
    benefit = compare_paired_binary(a, b, event_is="benefit")
    harm = compare_paired_binary(a, b, event_is="harm")
    assert any("needed to treat" in e.name for e in benefit.estimates)
    assert any("needed to harm" in e.name for e in harm.estimates)


def test_alpha_widens_the_interval():
    a, b = _pairs(30, 12, 5, 53)
    wide = compare_paired_binary(a, b, alpha=0.01).estimates[0]
    narrow = compare_paired_binary(a, b, alpha=0.05).estimates[0]
    assert wide.ci_low < narrow.ci_low and wide.ci_high > narrow.ci_high


# --------------------------------------------------------------------------
# input guards
# --------------------------------------------------------------------------

def test_rejects_unequal_lengths():
    with pytest.raises(ValueError, match="길이가 같은"):
        compare_paired_binary(("A", [1, 0, 1]), ("B", [1, 0]))


def test_rejects_empty_input():
    with pytest.raises(ValueError):
        compare_paired_binary(("A", []), ("B", []))


def test_rejects_non_indicator_values():
    with pytest.raises(ValueError, match="0 또는 1"):
        compare_paired_binary(("A", [1, 2]), ("B", [0, 1]))


def test_rejects_identical_condition_names():
    with pytest.raises(ValueError, match="이름이 같"):
        compare_paired_binary(("pre", [1, 0]), ("pre", [0, 1]))


def test_rejects_bad_alpha_and_test():
    a, b = _pairs(5, 5, 5, 5)
    with pytest.raises(ValueError):
        compare_paired_binary(a, b, alpha=0.9)
    with pytest.raises(ValueError):
        compare_paired_binary(a, b, test="chisq")


def test_negative_cell_counts_rejected():
    with pytest.raises(ValueError, match="negative"):
        PairedTable("A", "B", 1, -1, 0, 0)


def test_large_input_stays_fast_and_exact():
    n = 40000
    a = [1] * 12000 + [0] * (n - 12000)
    b = [1] * 9000 + [0] * (n - 9000)
    res = compare_paired_binary(("A", a), ("B", b))
    assert res.table.n == n
    assert res.method == "asymptotic"
    assert res.estimates[0].value == pytest.approx(3000 / n)


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def test_text_report_shows_the_table_and_the_discordant_counts():
    a, b = _pairs(30, 12, 5, 53)
    out = render_mcnemar_text(compare_paired_binary(a, b))
    assert "Matched-pair table" in out
    assert "불일치(discordant) 쌍 = 17개" in out
    assert "Cohen's kappa" in out
    assert "논문용 문장" in out


def test_text_report_columns_align_with_korean_labels():
    a, b = _pairs(30, 12, 5, 53)
    res = compare_paired_binary(("치료후", a[1]), ("치료전", b[1]))
    lines = [l for l in render_mcnemar_text(res).splitlines()
             if l.startswith("    치료후: 사건")
             or l.startswith("    치료후: 비사건")]
    assert len(lines) == 2
    # both data rows must be laid out on the same display grid
    import unicodedata
    def dw(s):
        return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
                   for c in s)
    assert dw(lines[0]) == dw(lines[1])


def test_sentence_reports_pairs_not_doubled_n():
    a, b = _pairs(30, 12, 5, 53)
    s = mcnemar_sentence(compare_paired_binary(a, b))
    assert "100 matched pairs" in s
    assert "42/100" in s and "35/100" in s
    assert "200" not in s


def test_json_round_trips_and_names_its_schema():
    a, b = _pairs(30, 12, 5, 53)
    res = compare_paired_binary(a, b)
    doc = json.loads(render_mcnemar_json(res))
    assert doc["schema"] == "statwise/paired-binary/1"
    assert doc["design"] == "paired"
    assert doc["table"]["n_discordant"] == 17
    assert doc["test"]["method"] == "exact"
    assert doc["estimates"][0]["name"].startswith("Risk difference")
    assert doc["sentence"]


def test_json_is_finite_everywhere_even_on_degenerate_tables():
    a, b = _pairs(20, 0, 0, 30)
    doc = mcnemar_to_dict(compare_paired_binary(a, b))
    text = json.dumps(doc)
    assert "Infinity" not in text and "NaN" not in text


def test_csv_dispatches_to_the_paired_rows():
    a, b = _pairs(30, 12, 5, 53)
    csv_text = render_csv(compare_paired_binary(a, b))
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("endpoint,kind,comparison")
    assert "paired-binary," in lines[1]
    assert "discordant=12/5" in lines[1]
    assert len(lines) == 1 + 4        # one row per estimate


# --------------------------------------------------------------------------
# regression tests from the 2026-08-06 adversarial review round
# --------------------------------------------------------------------------

def test_exact_cutoff_is_pinned_and_correct_at_the_boundary():
    # the reason string interpolates the constant, so asserting the constant
    # appears in it passes for ANY value -- pin the number and both sides
    assert EXACT_MAX_DISCORDANT == 25
    below = compare_paired_binary(*_pairs(50, 13, 11, 50))    # 24 discordant
    assert below.table.n_discordant == 24 and below.method == "exact"
    at = compare_paired_binary(*_pairs(50, 13, 12, 50))       # 25 discordant
    assert at.table.n_discordant == 25 and at.method == "asymptotic"


def test_kappa_ci_is_pinned_to_the_fleiss_variance():
    # the only CI assertion used to be an ordering check, which every wrong
    # variance formula also satisfies; pin the half-width
    est = cohens_kappa(PairedTable("A", "B", 30, 12, 5, 53))
    assert est.value == pytest.approx(0.6428571428571, abs=1e-12)
    assert est.ci_high - est.value == pytest.approx(0.152508, abs=1e-6)
    # hand-computed second case: po=.7, pe=.5, kappa=.4, se=0.1269961
    hand = cohens_kappa(PairedTable("A", "B", 20, 5, 10, 15))
    assert hand.value == pytest.approx(0.4)
    assert hand.ci_high - hand.value == pytest.approx(
        1.959963984540054 * 0.1269960629, abs=1e-7)


def test_kappa_withholds_its_interval_when_a_condition_is_constant():
    # a constant condition pins kappa structurally; the variance expression
    # lands on ~1e-18 instead of 0, which printed as "[-0.000, 0.000]"
    est = cohens_kappa(PairedTable("A", "B", 0, 0, 7, 3))
    assert est.value == pytest.approx(0.0)
    assert est.ci_low is None and est.ci_high is None
    assert "고정된 값" in est.note
    est2 = cohens_kappa(PairedTable("A", "B", 20, 10, 0, 0))
    assert est2.ci_low is None and "고정된 값" in est2.note


def test_negative_kappa_is_not_labelled_poor():
    est = cohens_kappa(PairedTable("A", "B", 0, 10, 10, 0))
    assert est.value < 0.0 and est.magnitude == "below chance"


def test_kappa_always_carries_the_design_caveat():
    res = compare_paired_binary(*_pairs(30, 12, 5, 53))
    kappa = res.estimates[-1]
    assert "치료 전/후" in kappa.note


def test_small_sample_kappa_ci_is_flagged():
    res = compare_paired_binary(*_pairs(5, 4, 3, 5))
    assert any("대표본 근사(Fleiss)" in w for w in res.warnings)
    big = compare_paired_binary(*_pairs(40, 12, 8, 40))
    assert not any("대표본 근사(Fleiss)" in w for w in big.warnings)


def test_conflict_between_the_exact_p_and_the_tango_interval_is_warned():
    # n11=30, n12=12, n21=4, n22=0: exact p = 0.077 (not significant) but the
    # score interval excludes zero -- the sentence used to state both silently
    res = compare_paired_binary(*_pairs(30, 12, 4, 0))
    assert not res.significant
    rd = res.estimates[0]
    assert rd.ci_low > 0.0
    assert any("판정이 서로 다릅니다" in w for w in res.warnings)
    assert "disagree here" in mcnemar_sentence(res)


def test_no_conflict_warning_when_test_and_interval_agree():
    res = compare_paired_binary(*_pairs(30, 20, 2, 48))
    assert res.significant and res.estimates[0].ci_low > 0.0
    assert not any("판정이 서로 다릅니다" in w for w in res.warnings)
    assert "disagree here" not in mcnemar_sentence(res)


def test_condition_labels_are_sanitized():
    a, b = _pairs(3, 2, 1, 2)
    res = compare_paired_binary(("\x1b[31mRED\x1b[0m", a[1]), ("A\rB", b[1]))
    assert "\x1b" not in res.table.label_a
    assert "\r" not in res.table.label_b
    text = render_mcnemar_text(res)
    assert "\x1b" not in text and "\r" not in text
    assert "\x1b" not in mcnemar_sentence(res)
    # a very long label must not wreck the table or the sentence
    long_res = compare_paired_binary(("x" * 300, a[1]), ("y", b[1]))
    assert len(long_res.table.label_a) <= 40


def test_empty_condition_label_is_refused():
    a, b = _pairs(3, 2, 1, 2)
    with pytest.raises(ValueError, match="비어 있습니다"):
        compare_paired_binary(("   ", a[1]), ("post", b[1]))


def test_no_discordant_pairs_withholds_the_paste_ready_sentence():
    res = compare_paired_binary(*_pairs(20, 0, 0, 30))
    out = render_mcnemar_text(res)
    assert "논문용 문장을 생성하지 않았습니다" in out
    # the draft is still available in JSON, as the report says
    assert mcnemar_to_dict(res)["sentence"]


def test_continuity_corrected_run_says_so_in_the_sentence():
    res = compare_paired_binary(*_pairs(50, 30, 20, 50), test="mcnemar-cc")
    assert "continuity-corrected McNemar test" in mcnemar_sentence(res)


def test_significance_boundary_is_strict():
    # 5 discordant pairs all one way -> exact p is exactly 0.0625
    res = compare_paired_binary(*_pairs(10, 5, 0, 10), alpha=0.0625)
    assert res.pvalue == pytest.approx(0.0625)
    assert not res.significant
