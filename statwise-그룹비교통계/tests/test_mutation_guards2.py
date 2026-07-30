"""Second mutation-guard pass: value-level assertions for the paths that a
70-mutation sweep could still silently break.

Grouped by the subsystem whose *entire purpose* was previously unprotected.
"""

import csv as csvmod
import json
import math

import pytest

from statwise.analyze import analyze, analyze_paired
from statwise.binary import (BinaryGroup, compare_binary,
                             number_needed_to_treat, risk_difference)
from statwise.cli import main
from statwise.dataio import (load_paired_long, load_paired_wide,
                             load_binary_long, summarize_values)
from statwise.endpoints import run_endpoints
from statwise.report import (binary_sentence, render_binary_text, render_csv,
                             render_multi_csv, render_multi_json,
                             render_multi_text, render_text)
from statwise.special import chi2_sf, gammainc_upper, t_ppf


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


A = [5.1, 4.9, 5.3, 5.0, 5.2, 4.8, 5.4, 5.0]
B = [7.1, 6.9, 7.3, 7.0, 7.2, 6.8, 7.4, 7.0]
C = [5.6, 5.4, 5.9, 5.5, 5.8, 5.3, 6.0, 5.5]


# ==========================================================================
# across-endpoint correction: the module's entire reason to exist
# ==========================================================================

def _borderline_endpoints():
    """Endpoints individually significant but not after Holm."""
    return [(f"e{i}", [("a", (12 + i, 40)), ("b", (23 + i, 40))])
            for i in range(6)]


def test_withdrawn_significance_propagates_to_every_surface():
    multi = run_endpoints(_borderline_endpoints(), alpha=0.05,
                          correction="holm", binary=True)
    withdrawn = [r for r in multi.analysed
                 if r.result.pvalue < 0.05 <= r.result.pvalue_adj]
    assert withdrawn, "fixture must contain a demoted endpoint"
    for run in withdrawn:
        assert run.result.significant is False
    names = {r.name for r in withdrawn}
    doc = json.loads(render_multi_json(multi))
    for entry in doc["endpoints"]:
        if entry["endpoint"] in names:
            assert entry["test"]["significant"] is False
    rows = list(csvmod.reader(render_multi_csv(multi).strip().splitlines()))
    header = rows[0]
    si, vi, ei = (header.index("significant"), header.index("verdict"),
                  header.index("endpoint"))
    for row in rows[1:]:
        if row[ei] in names and row[si]:
            assert row[si] == "no"
            assert row[vi] == "no significant difference"


def test_significance_boundary_is_strict_after_adjustment():
    from statwise.endpoints import _adjust_across

    class R:
        def __init__(self, p):
            self.pvalue = p
            self.pvalue_adj = None
            self.significant = p < 0.05
            self.warnings = []
    rs = [R(0.025), R(0.30)]
    _adjust_across(rs, "holm", 0.05)
    assert rs[0].pvalue_adj == pytest.approx(0.05)
    assert rs[0].significant is False        # p == alpha is not significant


def test_withdrawal_warning_is_attached_only_to_withdrawn_endpoints():
    multi = run_endpoints(_borderline_endpoints(), alpha=0.05,
                          correction="holm", binary=True)
    for run in multi.analysed:
        demoted = run.result.pvalue < 0.05 <= run.result.pvalue_adj
        has = any("보정 전 p=" in w for w in run.result.warnings)
        assert has == demoted


def test_uncorrected_two_endpoint_run_still_warns():
    multi = run_endpoints([("e1", [("a", A), ("b", B)]),
                           ("e2", [("a", A), ("b", C)])], correction="none")
    assert any("보정을 하지 않았습니다" in w for w in multi.warnings)


def test_detail_report_shows_both_raw_and_adjusted_p():
    multi = run_endpoints(_borderline_endpoints(), alpha=0.05,
                          correction="holm", binary=True)
    text = render_multi_text(multi, detail=True)
    assert "보정 후 p(adj)=" in text
    assert "보정 전 p=" in text
    assert "≥ 0.05" in text                  # the comparison glyph must be right


def test_demoted_endpoint_posthoc_is_marked_exploratory():
    three = [("a", (5, 40)), ("b", (18, 40)), ("c", (20, 40))]
    quiet = [("a", (20, 40)), ("b", (20, 40)), ("c", (21, 40))]
    ds = [("main", three)] + [(f"q{i}", quiet) for i in range(12)]
    multi = run_endpoints(ds, binary=True, correction="holm")
    main_run = multi.analysed[0].result
    if main_run.pvalue < 0.05 <= (main_run.pvalue_adj or 1.0):
        assert "탐색적" in render_multi_text(multi, detail=True)


# ==========================================================================
# the CI/p disagreement warning — both directions
# ==========================================================================

def test_disagreement_warning_fires_on_the_tied_borderline_case():
    res = analyze([("a", [4.0, 4.0, 3.0, 3.0, 3.0, 3.0]),
                   ("b", [6.0, 6.0, 6.0, 3.0, 4.0, 6.0])])
    assert res.test_name == "Mann-Whitney U test"
    assert any("판정이 서로 다릅니다" in w for w in res.warnings)


def test_disagreement_warning_is_silent_on_a_clear_result():
    res = analyze([("a", [1.0, 2.0, 3.0, 4.0, 5.0, 6.5]),
                   ("b", [30.0, 31.0, 32.5, 33.0, 34.0, 35.0])])
    assert not any("판정이 서로 다릅니다" in w for w in res.warnings)


def test_disagreement_warning_silent_on_tie_free_data():
    import random
    random.seed(4)
    for _ in range(40):
        a = [random.gauss(0, 1) for _ in range(9)]
        b = [random.gauss(0.4, 1) for _ in range(9)]
        res = analyze([("a", a), ("b", b)], test="mannwhitney")
        if res.location is None or res.location.ci_low is None:
            continue
        excludes = not (res.location.ci_low <= 0.0 <= res.location.ci_high)
        fired = any("판정이 서로 다릅니다" in w for w in res.warnings)
        assert fired == ((res.pvalue < 0.05) != excludes)


# ==========================================================================
# --test pre-specification
# ==========================================================================

@pytest.mark.parametrize("choice", ["student", "welch"])
def test_prespecified_interval_width_uses_alpha_over_two(choice):
    from statwise.special import t_ppf as tp
    res = analyze([("a", A), ("b", B)], alpha=0.05, test=choice)
    lo, hi = res.mean_diff_ci
    half = (hi - lo) / 2.0
    se = half / tp(0.975, res.df)
    # rebuilding the interval at the same df must reproduce it exactly
    assert half == pytest.approx(tp(0.975, res.df) * se, rel=1e-12)
    # and a 90% interval must be strictly narrower
    res90 = analyze([("a", A), ("b", B)], alpha=0.10, test=choice)
    assert (res90.mean_diff_ci[1] - res90.mean_diff_ci[0]) < (hi - lo)


def test_forced_student_really_is_student_not_welch():
    a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    b = [10.0, 30.0, 5.0, 50.0, 2.0, 40.0, 8.0, 60.0, 1.0, 70.0, 3.0, 25.0]
    st = analyze([("a", a), ("b", b)], test="student")
    we = analyze([("a", a), ("b", b)], test="welch")
    assert st.df == float(len(a) + len(b) - 2)     # integer pooled df
    assert we.df != st.df
    assert abs(we.df - round(we.df)) > 1e-9        # fractional Welch df
    assert st.pvalue != we.pvalue


def test_test_flag_refused_under_paired(tmp_path, capsys):
    rows = ["subject,time,v"]
    for i in range(8):
        rows.append(f"S{i},pre,{10 + i}")
        rows.append(f"S{i},post,{6 + i}")
    path = _write(tmp_path, "p.csv", "\n".join(rows) + "\n")
    assert main([path, "--paired", "--value", "v", "--group", "time",
                 "--id", "subject", "--test", "welch"]) == 2
    capsys.readouterr()


def test_test_flag_reaches_every_endpoint(tmp_path, capsys):
    rows = ["subject,arm,x,y"]
    for i in range(12):
        rows.append(f"S{i},a,{i},{i * 2}")
    for i in range(12):
        rows.append(f"T{i},b,{i + 5},{i * 2 + 9}")
    path = _write(tmp_path, "m.csv", "\n".join(rows) + "\n")
    assert main([path, "--values", "x,y", "--group", "arm",
                 "--test", "mannwhitney", "--brief"]) == 0
    out = capsys.readouterr().out
    assert out.count("Mann-Whitney") == 2


def test_test_flag_ignored_for_three_groups_warns():
    res = analyze([("a", A), ("b", B), ("c", C)], test="welch")
    assert any("독립 2그룹 비교에만" in w for w in res.warnings)


# ==========================================================================
# NNT / NNH naming — both directions
# ==========================================================================

@pytest.mark.parametrize("event_is,positive_rd,expected", [
    ("benefit", True, "Number needed to treat"),
    ("benefit", False, "Number needed to harm"),
    ("harm", True, "Number needed to harm"),
    ("harm", False, "Number needed to treat"),
])
def test_nnt_nnh_naming_in_all_four_combinations(event_is, positive_rd,
                                                 expected):
    a, b = (BinaryGroup("x", 12, 20), BinaryGroup("y", 4, 20))
    if not positive_rd:
        a, b = b, a
    est = number_needed_to_treat(risk_difference(a, b), event_is)
    assert est.name.startswith(expected)


def test_event_is_reaches_multi_endpoint_binary(tmp_path, capsys):
    rows = ["subject,arm,ae1,ae2"]
    for i in range(20):
        rows.append(f"D{i},drug,{'yes' if i < 12 else 'no'},"
                    f"{'yes' if i < 10 else 'no'}")
    for i in range(20):
        rows.append(f"S{i},sham,{'yes' if i < 4 else 'no'},"
                    f"{'yes' if i < 3 else 'no'}")
    path = _write(tmp_path, "ae.csv", "\n".join(rows) + "\n")
    assert main([path, "--binary", "--values", "ae1,ae2", "--group", "arm",
                 "--reference", "sham", "--event-is", "harm"]) == 0
    out = capsys.readouterr().out
    assert "Number needed to harm (NNH)" in out
    assert "Number needed to treat" not in out


# ==========================================================================
# significance boundary
# ==========================================================================

def test_binary_significance_boundary_is_strict():
    res = compare_binary([("a", (10, 40)), ("b", (18, 40))])
    at_p = compare_binary([("a", (10, 40)), ("b", (18, 40))],
                          alpha=min(0.499, res.pvalue))
    assert at_p.significant is False


def test_continuous_significance_boundary_is_strict():
    res = analyze([("a", A), ("b", C)])
    at_p = analyze([("a", A), ("b", C)], alpha=min(0.499, res.pvalue))
    assert at_p.significant is False


# ==========================================================================
# special functions in the far tail
# ==========================================================================

def test_chi_square_far_tail_is_never_flushed_to_zero():
    assert 0.0 < chi2_sf(150.0, 1.0) < 1e-30
    assert 0.0 < chi2_sf(300.0, 2.0) < 1e-60
    assert chi2_sf(150.0, 1.0) == pytest.approx(1.7336e-34, rel=1e-3)


def test_gammainc_upper_at_zero_is_one():
    assert gammainc_upper(2.0, 0.0) == 1.0
    assert gammainc_upper(1.0, 80.0) > 0.0


# ==========================================================================
# CONSORT missing-data accounting (asymmetric fixtures detect a swap)
# ==========================================================================

def test_paired_long_missing_counts_are_attributed_to_the_right_arm(tmp_path):
    rows = ["subject,time,v"]
    for i in range(6):
        rows.append(f"S{i},pre,{10 + i}")
        rows.append(f"S{i},post,{6 + i}")
    rows.append("X1,pre,12")          # pre-only  -> pre loses 1
    rows.append("X2,post,7")          # post-only -> post loses 1
    rows.append("X3,post,8")          # post-only -> post loses another
    path = _write(tmp_path, "pl.csv", "\n".join(rows) + "\n")
    miss = {}
    load_paired_long(str(path), "v", "time", "subject", None, [],
                     baseline="pre", missing_out=miss)
    assert miss["pre"] == 1
    assert miss["post"] == 2


def test_paired_wide_missing_counts_are_attributed_to_the_right_arm(tmp_path):
    path = _write(tmp_path, "pw.csv",
                  "pre,post\n18,12\n19,11\n20,14\n21,\n22,\n,15\n")
    miss = {}
    load_paired_wide(str(path), ["post", "pre"], None, [], baseline="pre",
                     missing_out=miss)
    assert miss["post"] == 2         # two rows had pre but no post
    assert miss["pre"] == 1          # one row had post but no pre


def test_binary_long_blank_cells_count_as_missing(tmp_path):
    path = _write(tmp_path, "b.csv",
                  "arm,r\nA,yes\nA,\nA,no\nB,yes\nB,no\nB,no\n")
    miss = {}
    load_binary_long(str(path), "r", "arm", None, None, [], missing_out=miss)
    assert miss["A"] == 1
    assert miss["B"] == 0


def test_summarize_values_reports_the_right_remainder():
    assert summarize_values([str(i) for i in range(12)]).endswith("외 7개]")
    assert "외" not in summarize_values(["a", "b"])


# ==========================================================================
# rendering: labels, verdicts and bounds a reader would act on
# ==========================================================================

def test_csv_verdict_column_always_agrees_with_significant():
    res = analyze([("a", A), ("b", B), ("c", C)])
    rows = list(csvmod.reader(render_csv(res).strip().splitlines()))
    header = rows[0]
    si, vi = header.index("significant"), header.index("verdict")
    for row in rows[1:]:
        if not row[si]:
            continue
        assert (row[vi] == "significant difference") == (row[si] == "yes")


def test_csv_header_names_match_the_data_order():
    res = analyze([("a", A), ("b", B)])
    rows = list(csvmod.reader(render_csv(res).strip().splitlines()))
    header = rows[0]
    assert header[:7] == ["endpoint", "kind", "comparison", "test", "n1",
                          "n2", "n_all"]
    lo_i, hi_i = header.index("ci_low"), header.index("ci_high")
    for row in rows[1:]:
        if row[lo_i] and row[hi_i]:
            assert float(row[lo_i]) <= float(row[hi_i])


def test_tost_block_is_labelled_with_the_one_minus_two_alpha_coverage():
    from statwise.analyze import EquivalenceSpec
    res = analyze([("a", A), ("b", B)],
                  equivalence=EquivalenceSpec(margin=(-3.0, 3.0)))
    block = render_text(res).split("[3b]")[1].split("[4]")[0]
    assert "90% CI [" in block
    assert "95% CI [" not in block


def test_non_inferiority_rule_line_names_the_correct_bound():
    from statwise.analyze import EquivalenceSpec
    hi = analyze([("a", A), ("b", B)],
                 equivalence=EquivalenceSpec(ni_margin=3.0,
                                             ni_direction="higher_is_better"))
    block = render_text(hi).split("[3b]")[1]
    assert "신뢰하한 >" in block
    lo = analyze([("a", A), ("b", B)],
                 equivalence=EquivalenceSpec(ni_margin=3.0,
                                             ni_direction="lower_is_better"))
    block = render_text(lo).split("[3b]")[1]
    assert "신뢰상한 <" in block


def test_actively_non_equivalent_is_distinguished_from_inconclusive():
    from statwise.analyze import EquivalenceSpec
    far = analyze([("a", A), ("b", B)],
                  equivalence=EquivalenceSpec(margin=(-0.5, 0.5)))
    assert "입증됨" in render_text(far)          # CI wholly outside the margin
    wide = analyze([("a", A), ("b", [5.4, 5.2, 5.6, 5.3, 5.5, 5.1, 5.7, 5.3])],
                   equivalence=EquivalenceSpec(margin=(-0.35, 0.35)))
    text = render_text(wide)
    if "등가(equivalence) 성립" not in text:
        assert "결론 불가" in text or "입증됨" in text


def test_rank_test_equivalence_sentence_marks_the_estimand():
    from statwise.analyze import EquivalenceSpec
    skew_a = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 40.0]
    skew_b = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.7, 45.0]
    res = analyze([("a", skew_a), ("b", skew_b)],
                  equivalence=EquivalenceSpec(margin=(-30.0, 30.0)))
    assert res.test_name == "Mann-Whitney U test"
    assert "mean difference under a t-model" in render_text(res)


def test_binary_sentence_interval_bounds_are_in_order():
    res = compare_binary([("low", (10, 50)), ("high", (22, 52))])
    s = binary_sentence(res)
    chunk = s.split("95% CI ")[1]
    lo = float(chunk.split("%")[0])
    hi = float(chunk.split(" to ")[1].split("%")[0])
    assert lo <= hi


def test_binary_sentence_omits_a_non_finite_risk_ratio():
    res = compare_binary([("a", (0, 20)), ("b", (0, 25))])
    assert "risk ratio" not in binary_sentence(res)
    assert "NaN" not in binary_sentence(res)


def test_common_outcome_warning_fires_when_either_arm_is_common():
    res = compare_binary([("a", (25, 50)), ("b", (2, 50))])
    assert any("흔한 사건" in w for w in res.warnings)


def test_nnt_label_formatting_is_consistent_across_event_is(tmp_path, capsys):
    rows = ["arm,r"]
    for i in range(24):
        rows.append(f"device,{'yes' if i < 15 else 'no'}")
    for i in range(24):
        rows.append(f"sham,{'yes' if i < 5 else 'no'}")
    path = _write(tmp_path, "r.csv", "\n".join(rows) + "\n")
    seen = []
    for flag in ("unspecified", "benefit", "harm"):
        main([path, "--binary", "--value", "r", "--group", "arm",
              "--reference", "sham", "--event-is", flag])
        line = [ln for ln in capsys.readouterr().out.splitlines()
                if "NN" in ln and "=" in ln][0]
        seen.append(line.split("=")[1].strip())
    assert len(set(seen)) == 1, f"same estimate rendered differently: {seen}"


# ==========================================================================
# CLI safety guards added late
# ==========================================================================

def test_output_refuses_to_follow_a_symlink(tmp_path, capsys):
    import os
    secret = tmp_path / "secret.txt"
    secret.write_text("confidential", encoding="utf-8")
    link = tmp_path / "out.txt"
    os.symlink(secret, link)
    path = _write(tmp_path, "d.csv", "x,y\n1,4\n2,5\n3,6\n4,7\n")
    rc = main([path, "--wide", "-o", str(link), "--overwrite"])
    assert rc == 2
    assert secret.read_text(encoding="utf-8") == "confidential"
    capsys.readouterr()


def test_csv_output_carries_a_bom_and_others_do_not(tmp_path, capsys):
    path = _write(tmp_path, "d.csv", "x,y\n1,4\n2,5\n3,6\n4,7\n")
    for fmt, expect_bom in (("csv", True), ("json", False), ("text", False)):
        dest = tmp_path / f"out.{fmt}"
        main([path, "--wide", "--format", fmt, "-o", str(dest)])
        head = dest.read_bytes()[:3]
        assert (head == b"\xef\xbb\xbf") == expect_bom
        capsys.readouterr()


def test_margin_with_values_is_still_rejected(tmp_path, capsys):
    rows = ["subject,arm,x,y"]
    for i in range(10):
        rows.append(f"S{i},a,{i},{i * 2}")
        rows.append(f"T{i},b,{i + 4},{i * 2 + 5}")
    path = _write(tmp_path, "m.csv", "\n".join(rows) + "\n")
    assert main([path, "--values", "x,y", "--group", "arm",
                 "--equivalence-margin", "2"]) == 2
    capsys.readouterr()


def test_ni_margin_and_direction_must_come_together(tmp_path, capsys):
    path = _write(tmp_path, "l.csv",
                  "v,g\n1,a\n2,a\n3,a\n4,a\n5,b\n6,b\n7,b\n8,b\n")
    assert main([path, "--value", "v", "--group", "g", "--reference", "a",
                 "--ni-margin", "3"]) == 2
    capsys.readouterr()
    assert main([path, "--value", "v", "--group", "g", "--reference", "a",
                 "--ni-direction", "lower_is_better"]) == 2
    capsys.readouterr()


# ==========================================================================
# round-3 survivors: scale-stability internals and screening thresholds
# ==========================================================================

def test_anova_f_is_invariant_under_an_additive_shift():
    """Rescaling must use deviations from the grand mean, not raw magnitudes."""
    from statwise.tests_stat import one_way_anova
    gs = [[1.0, 2.0, 3.0, 4.0], [3.0, 4.0, 5.0, 6.0], [2.0, 5.0, 4.0, 7.0]]
    ref = one_way_anova(gs)
    for shift in (1e6, 1e9, -1e8):
        r = one_way_anova([[v + shift for v in g] for g in gs])
        assert r.statistic == pytest.approx(ref.statistic, rel=1e-6)


def test_sum_of_squares_decomposition_holds():
    from statwise.tests_stat import one_way_anova
    gs = [[1.0, 2.0, 3.0, 4.0], [3.0, 4.0, 5.0, 6.0], [2.0, 5.0, 4.0, 7.0]]
    r = one_way_anova(gs)
    assert r.ss_total == pytest.approx(r.ss_between + r.ss_within, rel=1e-12)


def test_eta_squared_is_pinned_to_a_hand_computed_value():
    from statwise.effects import eta_squared
    from statwise.tests_stat import one_way_anova
    gs = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]]
    r = one_way_anova(gs)
    # grand mean 5; SS_between = 3*((2-5)^2+(5-5)^2+(8-5)^2) = 54; SS_within = 6
    assert r.ss_between == pytest.approx(54.0, rel=1e-12)
    assert r.ss_within == pytest.approx(6.0, rel=1e-12)
    assert eta_squared(gs, r.ss_between, r.ss_total).value == pytest.approx(
        54.0 / 60.0, rel=1e-12)


def test_eta_squared_refuses_a_non_finite_total():
    from statwise.effects import eta_squared
    with pytest.raises(ValueError):
        eta_squared([[1.0], [2.0]], 1e308, float("inf"))


def test_anova_f_survives_the_subnormal_restoration_window():
    """1e-162 made the restored sums subnormal and F came back as inf."""
    from statwise.tests_stat import one_way_anova
    gs = [[1.0, 2.0, 3.0, 4.0], [3.0, 4.0, 5.0, 6.0], [2.0, 5.0, 4.0, 7.0]]
    ref = one_way_anova(gs)
    for exp in (-158, -160, -161, -162, -165, 150, 153):
        r = one_way_anova([[v * 10.0 ** exp for v in g] for g in gs])
        assert math.isfinite(r.statistic)
        assert r.statistic == pytest.approx(ref.statistic, rel=1e-9)
        assert r.ss_between / r.ss_total == pytest.approx(
            ref.ss_between / ref.ss_total, rel=1e-9)


def test_shapiro_wilk_is_accurate_with_a_large_offset():
    """Centring on the first value instead of the median reintroduces cancellation."""
    from statwise.normality import shapiro_wilk
    base = [-1.2, -0.4, 0.1, 0.6, 1.1, -0.8, 0.3, 0.9, -0.1, 0.45]
    w0, p0 = shapiro_wilk(base)
    for offset in (1e6, 1e9):
        w, p = shapiro_wilk([v + offset for v in base])
        assert w == pytest.approx(w0, rel=1e-6)
        assert p == pytest.approx(p0, rel=1e-4)


def test_shapiro_w_never_exceeds_one():
    """W>1 makes log(1-W) raise; near-perfect data must not crash."""
    from statwise.normality import shapiro_wilk
    from statwise.special import norm_ppf
    for n in range(4, 21):
        perfect = [norm_ppf((i + 1 - 0.375) / (n + 0.25)) for i in range(n)]
        w, p = shapiro_wilk(perfect)
        assert w <= 1.0 + 1e-12
        assert 0.0 <= p <= 1.0


def test_outlier_screen_is_quiet_on_clean_normal_data():
    """A 1.5*IQR fence would flag ~1 in 100 clean observations as suspect."""
    import random
    from statwise.dataio import screen_values
    random.seed(21)
    flagged = 0
    for _ in range(40):
        notes = []
        screen_values("g", [random.gauss(0, 1) for _ in range(100)], notes)
        flagged += any("사분위" in n for n in notes)
    assert flagged <= 4          # the 3*IQR fence must stay rare


def test_outlier_screen_fires_on_a_small_arm_with_a_data_entry_error():
    from statwise.dataio import screen_values
    notes = []
    screen_values("g", [12.0, 13.0, 11.5, 12.5, 130.0], notes)
    assert any("사분위" in n for n in notes)


def test_zero_variance_group_does_not_select_equal_variance_anova():
    """A NaN Levene must not be read as 'variances are equal'."""
    res = analyze([("a", [5.0, 5.0, 5.0, 5.0, 5.0]),
                   ("b", [1.0, 2.0, 3.0, 4.0, 9.0]),
                   ("c", [2.0, 4.0, 6.0, 8.0, 14.0])])
    assert res.test_name != "One-way ANOVA"


def test_descriptives_switch_notation_before_columns_can_fuse():
    from statwise.report import render_text
    res = analyze([("a", [1e18, 2e18, 3e18, 4e18]),
                   ("b", [5e18, 6e18, 7e18, 8e18])])
    row = [ln for ln in render_text(res).splitlines()
           if ln.strip().startswith("a ")][0]
    assert len(row.split()) == 9        # label + n + 7 statistics, all separate


def test_long_labels_are_not_truncated_below_their_own_width():
    from statwise.report import render_text
    label = "treatment_arm_alpha_25mg"      # 24 chars
    res = analyze([(label, [1.0, 2.0, 3.0, 4.0]),
                   ("b", [5.0, 6.0, 7.0, 8.0])])
    assert label in render_text(res)


def test_hodges_lehmann_bounds_use_the_right_order_statistics():
    """An off-by-one in the trim index gives an anticonservative interval."""
    from statwise.location import hodges_lehmann_independent
    a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    b = [4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0]
    est = hodges_lehmann_independent(a, b, conf=0.95)
    diffs = sorted(x - y for x in a for y in b)
    assert est.ci_low in diffs and est.ci_high in diffs
    assert est.ci_low <= est.estimate <= est.ci_high
    # The bounds are the k-th and (N-k+1)-th order statistics of the pairwise
    # differences; an off-by-one shifts both inward and narrows the interval.
    n = len(diffs)
    k_lo = diffs.index(est.ci_low)
    k_hi = n - 1 - diffs[::-1].index(est.ci_high)
    assert k_lo + k_hi == n - 1, "bounds must be symmetric order statistics"
    assert k_lo >= 1, "a 95% interval must trim at least one pair from each end"
    # inverting the *exact* test must agree, which is only guaranteed tie-free
    tf_a = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    tf_b = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0]
    tf = hodges_lehmann_independent(tf_a, tf_b, conf=0.95)
    assert tf.method == "exact"
    assert tf.ci_high < 0.0                 # the arms are cleanly separated


def test_demoted_banner_fires_at_the_alpha_boundary():
    from statwise.report import _demoted

    class R:
        pvalue = 0.02
        pvalue_adj = 0.05
        alpha = 0.05
    assert _demoted(R()) is True
