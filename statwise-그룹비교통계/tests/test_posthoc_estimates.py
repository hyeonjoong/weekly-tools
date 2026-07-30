"""Value-level guards for the post-hoc difference + interval.

Mutation testing found nine independent bugs in this one feature that the whole
suite missed — sign inversions, a 68% interval labelled 95%, a Welch standard
error with the group sizes swapped, and the wrong estimand under a rank test.
Every assertion here pins a number, not a shape.
"""

import math

import pytest

from statwise.analyze import analyze
from statwise.location import hodges_lehmann_independent
from statwise.report import render_text
from statwise.special import t_ppf
from statwise.tests_stat import mean, variance

LOW = [3.0, 5.0, 4.0, 6.0, 3.0, 5.0, 4.0, 6.0, 4.0, 5.0]
MID = [6.0, 8.0, 7.0, 9.0, 7.0, 6.0, 8.0, 7.0, 8.0, 7.0]
HIGH = [9.0, 11.0, 10.0, 12.0, 11.0, 13.0, 9.0, 12.0, 10.0, 11.0]
# deliberately unbalanced and heteroscedastic, so a swapped Welch SE shows up
UNEQ_A = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
UNEQ_B = [10.0, 30.0, 5.0, 50.0, 2.0, 40.0, 8.0, 60.0, 1.0, 70.0, 3.0, 25.0,
          15.0, 55.0]


def _res():
    return analyze([("low", LOW), ("mid", MID), ("high", HIGH)], alpha=0.05)


def _pair(res, a, b):
    return next(pw for pw in res.pairwise if pw.a == a and pw.b == b)


# --------------------------------------------------------------------------
# sign and labelling
# --------------------------------------------------------------------------

def test_difference_is_first_group_minus_second():
    pw = _pair(_res(), "low", "mid")
    assert pw.diff == pytest.approx(mean(LOW) - mean(MID))
    assert pw.diff < 0                      # low really is lower than mid


def test_pair_labels_match_the_input_order():
    res = _res()
    assert [(pw.a, pw.b) for pw in res.pairwise] == [
        ("low", "mid"), ("low", "high"), ("mid", "high")]


def test_group_sizes_are_not_transposed():
    res = analyze([("a", UNEQ_A), ("b", UNEQ_B)], test="welch")
    res3 = analyze([("a", UNEQ_A), ("b", UNEQ_B), ("c", MID)])
    for pw in res3.pairwise:
        if pw.a == "a" and pw.b == "b":
            assert pw.n_a == len(UNEQ_A) and pw.n_b == len(UNEQ_B)
    assert res.groups[0].n == len(UNEQ_A)


def test_every_pairwise_difference_matches_its_group_means():
    res = _res()
    by_label = {g.label: g.values for g in res.groups}
    for pw in res.pairwise:
        assert pw.diff == pytest.approx(
            mean(by_label[pw.a]) - mean(by_label[pw.b]), rel=1e-12)


# --------------------------------------------------------------------------
# interval width: half the mutations here produced a mislabelled coverage
# --------------------------------------------------------------------------

def _student_half_width(a, b, alpha):
    n1, n2 = len(a), len(b)
    df = n1 + n2 - 2
    sp2 = ((n1 - 1) * variance(a) + (n2 - 1) * variance(b)) / df
    se = math.sqrt(sp2 * (1.0 / n1 + 1.0 / n2))
    return t_ppf(1.0 - alpha / 2.0, df) * se


def test_student_posthoc_interval_width_is_recomputed_by_hand():
    pw = _pair(_res(), "low", "mid")
    lo, hi = pw.diff_ci
    assert (hi - lo) / 2.0 == pytest.approx(
        _student_half_width(LOW, MID, 0.05), rel=1e-12)
    assert lo == pytest.approx(pw.diff - _student_half_width(LOW, MID, 0.05))


def test_posthoc_interval_uses_alpha_over_two_not_alpha():
    """A 90% interval printed as '95% CI' is the classic off-by-a-tail bug."""
    pw = _pair(_res(), "low", "mid")
    half = (pw.diff_ci[1] - pw.diff_ci[0]) / 2.0
    n1, n2 = len(LOW), len(MID)
    df = n1 + n2 - 2
    sp2 = ((n1 - 1) * variance(LOW) + (n2 - 1) * variance(MID)) / df
    se = math.sqrt(sp2 * (1.0 / n1 + 1.0 / n2))
    assert half == pytest.approx(t_ppf(0.975, df) * se, rel=1e-12)
    assert half != pytest.approx(t_ppf(0.95, df) * se, rel=1e-6)
    assert half != pytest.approx(se, rel=1e-6)       # not a bare SE either


def test_posthoc_interval_tracks_alpha():
    wide = _pair(analyze([("low", LOW), ("mid", MID), ("high", HIGH)],
                         alpha=0.01), "low", "mid")
    narrow = _pair(analyze([("low", LOW), ("mid", MID), ("high", HIGH)],
                           alpha=0.10), "low", "mid")
    assert (wide.diff_ci[1] - wide.diff_ci[0]) > \
        (narrow.diff_ci[1] - narrow.diff_ci[0])


def test_welch_posthoc_standard_error_pairs_each_variance_with_its_own_n():
    """v1/n2 + v2/n1 is only correct when the groups are the same size."""
    res = analyze([("a", UNEQ_A), ("b", UNEQ_B),
                   ("c", [v + 100.0 for v in UNEQ_A])])
    pw = next((p for p in res.pairwise if p.a == "a" and p.b == "b"), None)
    if pw is None or pw.diff_ci is None or not pw.test.startswith("Welch"):
        pytest.skip("this fixture did not route to pairwise Welch")
    n1, n2 = len(UNEQ_A), len(UNEQ_B)
    v1, v2 = variance(UNEQ_A), variance(UNEQ_B)
    right = math.sqrt(v1 / n1 + v2 / n2)
    wrong = math.sqrt(v1 / n2 + v2 / n1)
    half = (pw.diff_ci[1] - pw.diff_ci[0]) / 2.0
    df = (v1 / n1 + v2 / n2) ** 2 / (
        (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))
    assert half == pytest.approx(t_ppf(0.975, df) * right, rel=1e-9)
    assert half != pytest.approx(t_ppf(0.975, df) * wrong, rel=1e-3)


# --------------------------------------------------------------------------
# rank-based post-hoc must report the Hodges-Lehmann shift it claims to
# --------------------------------------------------------------------------

def _skewed():
    return analyze([
        ("a", [1.0, 1.2, 1.4, 1.1, 1.3, 1.5, 40.0, 1.25, 1.35, 1.45]),
        ("b", [2.0, 2.2, 2.4, 2.1, 2.3, 2.5, 60.0, 2.25, 2.35, 2.45]),
        ("c", [5.0, 5.2, 5.4, 5.1, 5.3, 5.5, 90.0, 5.25, 5.35, 5.45])])


def test_rank_posthoc_reports_hodges_lehmann_not_a_median_difference():
    res = _skewed()
    if not res.test_name.startswith("Kruskal") or not res.pairwise:
        pytest.skip("fixture did not route to Kruskal-Wallis with post-hoc")
    by_label = {g.label: g.values for g in res.groups}
    for pw in res.pairwise:
        expected = hodges_lehmann_independent(
            by_label[pw.a], by_label[pw.b], conf=0.95).estimate
        assert pw.diff == pytest.approx(expected, rel=1e-12)
        assert pw.diff_label == "Hodges-Lehmann shift"


def test_rank_posthoc_difference_inverts_with_group_order():
    res = _skewed()
    if not res.pairwise:
        pytest.skip("no post-hoc")
    by_label = {g.label: g.values for g in res.groups}
    for pw in res.pairwise:
        reverse = hodges_lehmann_independent(
            by_label[pw.b], by_label[pw.a], conf=0.95).estimate
        assert pw.diff == pytest.approx(-reverse, rel=1e-12)


def test_t_based_posthoc_is_labelled_a_mean_difference():
    for pw in _res().pairwise:
        assert pw.diff_label == "mean difference"


# --------------------------------------------------------------------------
# the rendered table
# --------------------------------------------------------------------------

def test_posthoc_header_states_the_configured_coverage():
    text = render_text(_res())
    block = text.split("[5] 사후검정")[1]
    assert "95% CI" in block
    assert "47%" not in block and "5% CI" not in block.replace("95% CI", "")


def test_posthoc_header_coverage_follows_alpha():
    res = analyze([("low", LOW), ("mid", MID), ("high", HIGH)], alpha=0.01)
    if res.pairwise:
        assert "99% CI" in render_text(res).split("[5] 사후검정")[1]


def test_posthoc_significance_uses_the_adjusted_p():
    res = _res()
    for pw in res.pairwise:
        assert pw.significant == (pw.pvalue_adj < res.alpha)
        assert pw.pvalue_adj >= pw.pvalue_raw - 1e-12


def test_table_reports_the_rendered_difference_not_the_effect_size():
    text = render_text(_res())
    row = [ln for ln in text.split("[5] 사후검정")[1].splitlines()
           if ln.strip().startswith("low vs mid")][0]
    pw = _pair(_res(), "low", "mid")
    assert f"{pw.diff:.2f}" in row
    assert f"{pw.n_a}/{pw.n_b}" in row


def test_interval_p_disagreement_is_disclosed():
    """A per-comparison interval next to an adjusted p can disagree; say so."""
    import random
    random.seed(11)
    groups = []
    for label, shift in (("A", 0.0), ("B", 0.95), ("C", 1.55), ("D", 2.3)):
        groups.append((label, [random.gauss(shift, 1.0) for _ in range(14)]))
    res = analyze(groups)
    conflicts = [pw for pw in res.pairwise
                 if pw.diff_ci and
                 (not (pw.diff_ci[0] <= 0 <= pw.diff_ci[1])) != pw.significant]
    if conflicts:
        assert any("비보정" in w or "동시신뢰구간" in w for w in res.warnings)
    assert "비보정" in render_text(res)
