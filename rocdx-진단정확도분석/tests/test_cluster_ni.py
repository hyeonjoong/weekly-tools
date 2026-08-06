"""Clustered data, Holm multiplicity, and the non-inferiority verdict."""

import math
import os

import pytest

from rocdx.analyze import (
    add_comparison,
    analyze,
    bootstrap_curve,
    finalize_comparisons,
    load_dataset,
)
from rocdx.cli import main
from rocdx.delong import compare_paired, noninferiority
from rocdx.loader import LoadError, read_table
from rocdx.report import format_report, markdown_report, paper_sentence
from rocdx.stats_core import holm_adjust

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "examples")


def write_csv(tmp_path, name, text, encoding="utf-8"):
    p = tmp_path / name
    p.write_bytes(text.encode(encoding))
    return str(p)


def clustered(tmp_path, per_patient=4, n_patients=25):
    """Each patient contributes identical duplicated rows — maximal clustering.

    Duplicating a row cannot add information, so an interval that ignores
    clustering will shrink as ``per_patient`` grows while the cluster bootstrap
    stays put. That is the property being tested, and it needs no randomness.
    """
    rows = ["pid,marker,dx"]
    for i in range(n_patients):
        for _ in range(per_patient):
            rows.append(f"C{i},{i},No")
            rows.append(f"P{i},{i + 6},Yes")
    return write_csv(tmp_path, f"cl{per_patient}.csv", "\n".join(rows) + "\n")


# --- Holm --------------------------------------------------------------------

def test_holm_matches_a_hand_computed_example():
    # p = .01, .02, .03, .04 with m = 4: 4*.01, 3*.02, 2*.03, 1*.04
    assert holm_adjust([0.01, 0.02, 0.03, 0.04]) == pytest.approx(
        [0.04, 0.06, 0.06, 0.06])


def test_holm_keeps_input_order_and_is_monotone():
    # sorted: .001 (×3), .02 (×2), .04 (×1) → .003, .04, .04 after monotonicity
    out = holm_adjust([0.04, 0.001, 0.02])
    assert out == pytest.approx([0.04, 0.003, 0.04])
    assert out[1] < out[2] <= out[0]


def test_holm_never_exceeds_one_and_passes_none_through():
    out = holm_adjust([0.9, 0.8, None, float("nan")])
    assert out[0] <= 1.0 and out[1] <= 1.0
    assert out[2] is None and out[3] is None


def test_holm_family_size_excludes_untestable_comparisons():
    """A comparison with no p-value must not inflate the correction."""
    with_none = holm_adjust([0.01, 0.02, None])
    without = holm_adjust([0.01, 0.02])
    assert with_none[:2] == pytest.approx(without)


def test_single_comparison_gets_no_adjusted_p_at_all(tmp_path):
    path = write_csv(tmp_path, "one.csv", "\n".join(
        ["id,a,b,dx"] + [f"c{i},{i},{i * 0.5},No" for i in range(12)]
        + [f"p{i},{i + 4},{i + 9},Yes" for i in range(12)]) + "\n")
    ds = load_dataset(read_table(path), "a", "dx", compare_cols=["b"])
    an = analyze(ds)
    add_comparison(an, "b")
    finalize_comparisons(an)
    assert an.comparison_p_adjusted == {}
    assert "Holm" not in format_report(an, show_curve=False)


def test_two_comparisons_are_holm_adjusted_end_to_end(tmp_path, capsys):
    path = os.path.join(EXAMPLES, "sepsis_biomarker.csv")
    assert main([path, "--score", "crp_mg_L", "--truth", "sepsis",
                 "--compare", "procalcitonin_ng_mL", "--compare", "wbc_10e3_uL",
                 "--no-curve"]) == 0
    out = capsys.readouterr().out
    assert "Holm" in out and "비교 2건" in out


# --- non-inferiority ---------------------------------------------------------

def test_noninferiority_verdict_follows_the_confidence_limit():
    pos_a, neg_a = [5, 6, 7, 8, 9, 10], [1, 2, 3, 4, 5, 6]
    cmp_ = compare_paired(pos_a, neg_a, pos_a, neg_a)   # identical markers
    ni = noninferiority(cmp_, 0.05)
    # Identical markers: diff 0 with zero variance → nothing is estimable.
    assert ni.noninferior is None and ni.p_value is None


def test_noninferiority_is_declared_when_the_limit_clears_the_margin(tmp_path):
    path = write_csv(tmp_path, "ni.csv", "\n".join(
        ["id,a,b,dx"]
        + [f"c{i},{i},{i + 0.3},No" for i in range(60)]
        + [f"p{i},{i + 30},{i + 30.4},Yes" for i in range(60)]) + "\n")
    ds = load_dataset(read_table(path), "a", "dx", compare_cols=["b"])
    an = analyze(ds)
    add_comparison(an, "b")
    finalize_comparisons(an, ni_margin=0.05)
    ni = an.noninferiority["b"]
    assert ni.noninferior is True
    assert ni.lower_limit > -0.05
    assert ni.p_value < 0.025
    txt = format_report(an, show_curve=False)
    assert "비열등" in txt and "사전에" in txt


def test_noninferiority_fails_loudly_for_a_clearly_worse_marker(tmp_path):
    """A useless comparator must not make the *index* test fail, but a useless
    index test against a good comparator must fail non-inferiority."""
    rows = ["id,a,b,dx"]
    for i in range(40):
        rows.append(f"c{i},{i % 7},{i},No")        # a is noise, b separates
    for i in range(40):
        rows.append(f"p{i},{i % 7},{i + 45},Yes")
    path = write_csv(tmp_path, "worse.csv", "\n".join(rows) + "\n")
    ds = load_dataset(read_table(path), "a", "dx", compare_cols=["b"])
    an = analyze(ds, direction="higher")
    add_comparison(an, "b", direction="higher")
    finalize_comparisons(an, ni_margin=0.05)
    ni = an.noninferiority["b"]
    assert ni.noninferior is False
    assert "비열등성을 입증하지 못했습니다" in format_report(an, show_curve=False)


def test_noninferiority_margin_must_be_positive():
    pos_a, neg_a = [5, 6, 7], [1, 2, 3]
    cmp_ = compare_paired(pos_a, neg_a, [4, 5, 6], [2, 1, 3])
    with pytest.raises(ValueError):
        noninferiority(cmp_, 0.0)


def test_cli_rejects_ni_margin_without_a_comparator(tmp_path, capsys):
    path = write_csv(tmp_path, "x.csv", "\n".join(
        ["id,a,dx"] + [f"c{i},{i},No" for i in range(8)]
        + [f"p{i},{i + 5},Yes" for i in range(8)]) + "\n")
    assert main([path, "--score", "a", "--truth", "dx", "--ni-margin", "0.05"]) == 2
    assert "--compare" in capsys.readouterr().err
    assert main([path, "--score", "a", "--truth", "dx", "--ni-margin", "1.5"]) == 2


# --- clustering --------------------------------------------------------------

def test_cluster_column_is_carried_and_counted(tmp_path):
    ds = load_dataset(read_table(clustered(tmp_path)), "marker", "dx",
                      cluster_col="pid")
    assert ds.cluster_name == "pid"
    assert len(ds.clusters) == len(ds.scores)
    assert ds.n_clusters == 50            # 25 cases + 25 controls
    assert ds.max_cluster_size == 4


def test_blank_cluster_ids_become_singleton_clusters(tmp_path):
    path = write_csv(tmp_path, "blank.csv", "\n".join([
        "pid,marker,dx", "A,1,No", ",2,No", ",3,No", "B,7,Yes", "B,8,Yes",
    ]) + "\n")
    ds = load_dataset(read_table(path), "marker", "dx", cluster_col="pid")
    assert ds.n_clusters == 4             # A, blank, blank, B
    assert any("빈 행" in n for n in ds.notes)


def test_duplicate_ids_warn_even_without_the_cluster_flag(tmp_path):
    ds = load_dataset(read_table(clustered(tmp_path)), "marker", "dx",
                      cluster_col="pid")
    an = analyze(ds)
    assert any("중복" in w and "--cluster" in w for w in an.warnings)
    assert an.curve_boot is None


def test_cluster_bootstrap_does_not_shrink_when_rows_are_duplicated(tmp_path):
    """The honesty property: duplicating rows must not buy precision."""
    widths = {}
    for k in (1, 4):
        ds = load_dataset(read_table(clustered(tmp_path, per_patient=k)),
                          "marker", "dx", cluster_col="pid")
        an = analyze(ds, n_boot=400, seed=11, cluster=True)
        lo, hi = an.curve_boot.auc_ci
        widths[k] = hi - lo
        # the naive DeLong interval, for contrast
        widths[f"delong{k}"] = an.auc.ci[1] - an.auc.ci[0]
    assert widths[4] == pytest.approx(widths[1], abs=0.05)
    # ...while the interval that assumes independence does shrink.
    assert widths["delong4"] < widths["delong1"] * 0.75


def test_cluster_bootstrap_reports_its_unit_and_warns_about_delong(tmp_path):
    ds = load_dataset(read_table(clustered(tmp_path)), "marker", "dx",
                      cluster_col="pid")
    an = analyze(ds, n_boot=200, seed=5, cluster=True)
    b = an.curve_boot
    assert b.kind == "cluster" and b.n_clusters == 50 and b.max_cluster_size == 4
    txt = format_report(an, show_curve=False)
    assert "군집 보정 AUC" in txt
    assert "독립 단위 (cluster)" in txt
    assert any("좁습니다" in w for w in an.warnings)
    assert "군집" in paper_sentence(an)


def test_all_singleton_clusters_are_reported_as_nothing_to_correct(tmp_path):
    ds = load_dataset(read_table(clustered(tmp_path, per_patient=1)), "marker", "dx",
                      cluster_col="pid")
    an = analyze(ds, n_boot=100, seed=1, cluster=True)
    assert any("모두 서로 달라" in w for w in an.warnings)


def test_cluster_flag_without_the_column_or_bootstrap_is_refused(tmp_path, capsys):
    path = clustered(tmp_path)
    assert main([path, "--score", "marker", "--truth", "dx", "--cluster"]) == 2
    assert "--cluster-col" in capsys.readouterr().err
    assert main([path, "--score", "marker", "--truth", "dx",
                 "--cluster-col", "pid", "--cluster"]) == 2
    assert "--bootstrap" in capsys.readouterr().err


def test_cluster_column_is_never_used_as_a_marker(tmp_path):
    """Naming the cluster column must not change the AUC or the sample."""
    ds_plain = load_dataset(read_table(clustered(tmp_path)), "marker", "dx")
    ds_cl = load_dataset(read_table(clustered(tmp_path)), "marker", "dx",
                         cluster_col="pid")
    assert ds_plain.scores == ds_cl.scores
    assert analyze(ds_plain).auc.auc == analyze(ds_cl).auc.auc


def test_bootstrap_curve_needs_two_clusters(tmp_path):
    scores = [1.0, 2.0, 3.0, 4.0]
    pos = [False, False, True, True]
    assert bootstrap_curve(scores, pos, 100, 1, 0.05,
                           clusters=["A", "A", "A", "A"]) is None
    assert bootstrap_curve(scores, pos, 0, 1, 0.05) is None


def test_bootstrap_curve_is_reproducible_and_seed_dependent(tmp_path):
    # Overlapping groups: a perfectly separated sample would give AUC 1 in every
    # resample and the seed could not possibly matter.
    scores = [float(i) for i in range(30)]
    pos = [i % 3 == 0 for i in range(30)]
    a = bootstrap_curve(scores, pos, 200, 1, 0.05, fpr_range=(0.0, 0.2))
    b = bootstrap_curve(scores, pos, 200, 1, 0.05, fpr_range=(0.0, 0.2))
    c = bootstrap_curve(scores, pos, 200, 2, 0.05, fpr_range=(0.0, 0.2))
    assert a.auc_ci == b.auc_ci and a.pauc_ci == b.pauc_ci
    assert a.auc_ci != c.auc_ci or a.pauc_ci != c.pauc_ci


def test_bundled_clustered_example_runs_end_to_end(capsys):
    path = os.path.join(EXAMPLES, "lesion_multi_reader.csv")
    assert main([path, "--score", "초음파점수", "--truth", "조직검사",
                 "--positive-label", "악성", "--cluster-col", "환자ID",
                 "--cluster", "--bootstrap", "300", "--no-curve"]) == 0
    out = capsys.readouterr().out
    assert "군집 보정 AUC" in out
    assert "독립 단위 (cluster)  : 환자ID" in out


def test_paper_draft_does_not_call_clustered_rows_people(tmp_path):
    """"92명" for 92 lesions from 42 patients would mislead a reviewer."""
    ds = load_dataset(read_table(clustered(tmp_path)), "marker", "dx",
                      cluster_col="pid")
    txt = paper_sentence(analyze(ds))
    assert "분석 가능한 200건" in txt
    assert "50개 단위" in txt
    assert "200명" not in txt
    assert "independent units" in txt


def test_paper_draft_still_says_people_without_clustering(tmp_path):
    ds = load_dataset(read_table(clustered(tmp_path, per_patient=1)), "marker", "dx")
    txt = paper_sentence(analyze(ds))
    assert "명" in txt and "개 단위" not in txt


def test_markdown_warns_when_clustering_is_ignored(tmp_path):
    ds = load_dataset(read_table(clustered(tmp_path)), "marker", "dx",
                      cluster_col="pid")
    md = markdown_report(analyze(ds))
    assert "--cluster" in md


# --- regression: the honesty properties, pinned numerically -------------------

def test_cluster_bootstrap_actually_resamples(tmp_path):
    """The "duplicating rows doesn't help" test passed on a no-op bootstrap.

    A bootstrap that returned the original sample every time produced a
    zero-width interval, which trivially satisfied "the width did not change".
    So the width must also be *non-degenerate* and of the same order as the
    interval that assumes independence.
    """
    ds = load_dataset(read_table(clustered(tmp_path, per_patient=1)), "marker", "dx",
                      cluster_col="pid")
    an = analyze(ds, n_boot=400, seed=3, cluster=True)
    lo, hi = an.curve_boot.auc_ci
    width = hi - lo
    assert 0.01 < width < 0.5
    delong = an.auc.ci[1] - an.auc.ci[0]
    assert width == pytest.approx(delong, rel=0.5)
    assert an.curve_boot.auc_se is not None and an.curve_boot.auc_se > 0


def test_cluster_resample_draws_as_many_clusters_as_there_are(tmp_path):
    """Drawing half the clusters would inflate every interval."""
    ds = load_dataset(read_table(clustered(tmp_path, per_patient=2)), "marker", "dx",
                      cluster_col="pid")
    an = analyze(ds, n_boot=400, seed=3, cluster=True)
    half = bootstrap_curve(analyze(ds).oriented, ds.positive, 400, 3, 0.05,
                           clusters=list(ds.clusters))
    assert an.curve_boot.auc_se == pytest.approx(half.auc_se, rel=1e-12)
    # a half-size resample would be materially noisier than the full one
    assert an.curve_boot.auc_se < 0.2


def test_pauc_interval_is_labelled_as_a_cluster_bootstrap_when_it_is_one(tmp_path):
    ds = load_dataset(read_table(clustered(tmp_path)), "marker", "dx",
                      cluster_col="pid")
    an = analyze(ds, pauc_min_spec=0.8, n_boot=300, seed=6, cluster=True)
    assert an.pauc.ci_source == "cluster-bootstrap"
    assert "군집 부트스트랩 백분위" in format_report(an, show_curve=False)


def test_noninferiority_lower_limit_is_the_exact_normal_limit(tmp_path):
    path = write_csv(tmp_path, "ni2.csv", "\n".join(
        ["id,a,b,dx"]
        + [f"c{i},{i},{i + 0.3},No" for i in range(60)]
        + [f"p{i},{i + 30},{i + 30.4},Yes" for i in range(60)]) + "\n")
    ds = load_dataset(read_table(path), "a", "dx", compare_cols=["b"])
    an = analyze(ds)
    c = add_comparison(an, "b")
    finalize_comparisons(an, ni_margin=0.05)
    ni = an.noninferiority["b"]
    assert ni.lower_limit == pytest.approx(c.diff - 1.959963985 * c.se_diff, rel=1e-9)
    assert ni.lower_limit == pytest.approx(c.ci[0], rel=1e-12)
    assert ni.alpha_one_sided == pytest.approx(0.025)
    assert ni.superior is False                      # non-inferior but not better
    assert "넘어 우월" not in format_report(an, show_curve=False)


def test_superiority_is_declared_only_when_the_limit_clears_zero(tmp_path):
    path = write_csv(tmp_path, "sup.csv", "\n".join(
        ["id,a,b,dx"]
        + [f"c{i},{i},{i % 5},No" for i in range(40)]
        + [f"p{i},{i + 45},{i % 5},Yes" for i in range(40)]) + "\n")
    ds = load_dataset(read_table(path), "a", "dx", compare_cols=["b"])
    an = analyze(ds, direction="higher")
    add_comparison(an, "b", direction="higher")
    finalize_comparisons(an, ni_margin=0.05)
    ni = an.noninferiority["b"]
    assert ni.noninferior is True and ni.superior is True
    assert ni.lower_limit > 0
    txt = format_report(an, show_curve=False)
    assert "넘어 우월합니다" in txt
    assert "우월성도 확인되었다" in paper_sentence(an)


def test_cli_ni_margin_reaches_the_report(tmp_path, capsys):
    """Every earlier --ni-margin CLI test was a rejection test."""
    path = write_csv(tmp_path, "cli_ni.csv", "\n".join(
        ["id,a,b,dx"]
        + [f"c{i},{i},{i + 0.3},No" for i in range(40)]
        + [f"p{i},{i + 25},{i + 25.4},Yes" for i in range(40)]) + "\n")
    assert main([path, "--score", "a", "--truth", "dx", "--compare", "b",
                 "--ni-margin", "0.05", "--no-curve"]) == 0
    out = capsys.readouterr().out
    assert "비열등성 검정" in out and "0.050" in out
    # and without the flag the section must be absent
    assert main([path, "--score", "a", "--truth", "dx", "--compare", "b",
                 "--no-curve"]) == 0
    assert "비열등성 검정" not in capsys.readouterr().out


def test_cli_seed_changes_the_bootstrap_and_repeats_exactly(tmp_path):
    import json as _json
    path = clustered(tmp_path, per_patient=2)
    def run(seed):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert main([path, "--score", "marker", "--truth", "dx",
                         "--bootstrap", "200", "--seed", str(seed),
                         "--json", "-"]) == 0
        return _json.loads(buf.getvalue())
    a, b, c = run(1), run(1), run(2)
    key = lambda d: [p.get("bootstrap", {}).get("cutoff_ci")
                     for p in d["operating_points"]]
    assert key(a) == key(b)
    assert key(a) != key(c)


def test_holm_family_size_counts_only_testable_comparisons(tmp_path):
    """A comparator with no estimable variance must not inflate the divisor."""
    rows = ["id,a,b1,b2,dx"]
    for i in range(30):
        rows.append(f"c{i},{i},5,{i % 4},No")
    for i in range(30):
        rows.append(f"p{i},{i + 40},5,{i % 4 + 2},Yes")
    path = write_csv(tmp_path, "fam.csv", "\n".join(rows) + "\n")
    ds = load_dataset(read_table(path), "a", "dx", compare_cols=["b1", "b2"])
    an = analyze(ds, direction="higher")
    for c in ("b1", "b2"):
        add_comparison(an, c, direction="higher")
    finalize_comparisons(an)
    untestable = [c for c in an.comparisons if c.p_value is None]
    assert untestable, "fixture should contain one untestable comparison"
    assert an.holm_family_size == len(an.comparisons) - len(untestable)
    assert f"검정 가능한 비교 {an.holm_family_size}건" in format_report(an, show_curve=False)


def test_clustered_warning_does_not_assert_a_direction_it_cannot_know(tmp_path):
    """Repeated measures narrow the DeLong CI; paired case+control widen it."""
    ds_rep = load_dataset(read_table(clustered(tmp_path)), "marker", "dx",
                          cluster_col="pid")
    w_rep = " ".join(analyze(ds_rep).warnings)
    assert "좁습니다" in w_rep

    paired = ["pid,marker,dx"]
    for i in range(25):                       # one case and one control per unit
        paired.append(f"U{i},{i},No")
        paired.append(f"U{i},{i + 5},Yes")
    p2 = write_csv(tmp_path, "paired.csv", "\n".join(paired) + "\n")
    ds_pair = load_dataset(read_table(p2), "marker", "dx", cluster_col="pid")
    w_pair = " ".join(analyze(ds_pair).warnings)
    assert "넓어질 수 있습니다" in w_pair
    assert "좁습니다" not in w_pair


def test_clustered_report_flags_that_cutoff_intervals_stay_row_independent(tmp_path):
    ds = load_dataset(read_table(clustered(tmp_path)), "marker", "dx",
                      cluster_col="pid")
    txt = format_report(analyze(ds, n_boot=200, seed=1, cluster=True),
                        show_curve=False)
    assert "AUC·부분 AUC 구간에만" in txt


def test_report_section_numbers_have_no_gaps(tmp_path):
    """--no-curve used to print sections 1, 2, 4."""
    import re
    ds = load_dataset(read_table(clustered(tmp_path)), "marker", "dx")
    for show in (True, False):
        txt = format_report(analyze(ds), show_curve=show)
        nums = [int(m) for m in re.findall(r"^  (\d+)\. ", txt, re.M)]
        assert nums == list(range(1, len(nums) + 1)), (show, nums)


def test_outcome_header_does_not_say_positive_for_a_benign_level(tmp_path):
    """In a pathology column 양성 means benign, so "[양성 = '악성']" contradicted itself."""
    path = write_csv(tmp_path, "path.csv", "\n".join([
        "id,점수,조직검사", "1,1,양성", "2,2,양성", "3,8,악성", "4,9,악성",
    ]) + "\n")
    ds = load_dataset(read_table(path), "점수", "조직검사", positive_label="악성",
                      negative_label="양성")
    txt = format_report(analyze(ds), show_curve=False)
    assert "질환군 = '악성', 비질환군 = '양성'" in txt


def test_single_other_level_does_not_trigger_the_folded_negative_warning(tmp_path):
    """With exactly two clean levels there is nothing ambiguous to warn about."""
    path = write_csv(tmp_path, "two.csv", "\n".join([
        "id,점수,조직검사", "1,1,양성", "2,2,양성", "3,8,악성", "4,9,악성",
    ]) + "\n")
    ds = load_dataset(read_table(path), "점수", "조직검사", positive_label="악성")
    assert not any("모두 비질환군으로 처리" in n for n in ds.notes)
    # but a third level must still be reported
    path3 = write_csv(tmp_path, "three.csv", "\n".join([
        "id,점수,조직검사", "1,1,양성", "2,2,판정보류", "3,8,악성", "4,9,악성",
    ]) + "\n")
    ds3 = load_dataset(read_table(path3), "점수", "조직검사", positive_label="악성")
    assert any("모두 비질환군으로 처리" in n for n in ds3.notes)


# --- regression: round-2 review findings --------------------------------------

def test_duplicate_comparator_columns_do_not_crash(tmp_path):
    """"--compare b1 --compare B1" resolved to one header and raised IndexError."""
    rows = ["id,a,b1,dx"]
    for i in range(20):
        rows.append(f"c{i},{i},{20 - i},No")
    for i in range(20):
        rows.append(f"p{i},{i + 25},{i + 3},Yes")
    path = write_csv(tmp_path, "dup.csv", "\n".join(rows) + "\n")
    for spelling in (["b1", "B1"], ["b1", "b1"], ["b1", "#3"], ["b1", "b 1"]):
        ds = load_dataset(read_table(path), "a", "dx", compare_cols=spelling)
        assert list(ds.extra) == ["b1"]
        assert len(ds.extra["b1"]) == len(ds.scores)
        assert any("중복을 제외" in n for n in ds.notes)
        an = analyze(ds)
        add_comparison(an, "b1")          # would raise before the fix
        finalize_comparisons(an)
        assert an.comparisons[0].n_used == len(ds.scores)
    # the score column given as its own comparator is dropped too
    ds = load_dataset(read_table(path), "a", "dx", compare_cols=["a"])
    assert ds.extra == {}


def test_cli_duplicate_comparator_exits_cleanly(tmp_path, capsys):
    rows = ["id,a,b1,dx"]
    for i in range(20):
        rows.append(f"c{i},{i},{20 - i},No")
    for i in range(20):
        rows.append(f"p{i},{i + 25},{i + 3},Yes")
    path = write_csv(tmp_path, "dup2.csv", "\n".join(rows) + "\n")
    assert main([path, "--score", "a", "--truth", "dx",
                 "--compare", "b1", "--compare", "B1", "--no-curve"]) == 0
    assert "중복을 제외" in capsys.readouterr().out


def test_numeric_cluster_ids_written_two_ways_are_one_unit(tmp_path):
    """A pandas export of an int ID column with a NaN writes 1 and 1.0."""
    rows = ["pid,marker,dx"]
    for i in range(10):
        rows.append(f"{i},{i},No")
        rows.append(f"{i}.0,{i + 5},Yes")
    path = write_csv(tmp_path, "num.csv", "\n".join(rows) + "\n")
    ds = load_dataset(read_table(path), "marker", "dx", cluster_col="pid")
    assert ds.n_clusters == 10            # not 20
    assert ds.max_cluster_size == 2
    assert any("숫자 표기를 통일" in n for n in ds.notes)


def test_cluster_ids_differing_only_in_case_stay_separate(tmp_path):
    """P01 and p01 can be two real patients — folding case would invent a merge."""
    path = write_csv(tmp_path, "case.csv", "\n".join([
        "pid,marker,dx", "P01,1,No", "p01 ,2,No", "P02,8,Yes", "P03,9,Yes",
    ]) + "\n")
    ds = load_dataset(read_table(path), "marker", "dx", cluster_col="pid")
    assert ds.n_clusters == 4


def test_degenerate_cluster_bootstrap_reports_no_interval(tmp_path):
    """Two clusters gave a zero-width "95% CI" that reached the paper draft."""
    rows = ["pid,marker,dx"]
    for i in range(20):
        rows.append(f"{'A' if i % 2 else 'B'},{i},{'Yes' if i % 2 else 'No'}")
    path = write_csv(tmp_path, "two.csv", "\n".join(rows) + "\n")
    ds = load_dataset(read_table(path), "marker", "dx", cluster_col="pid")
    an = analyze(ds, n_boot=300, seed=1, cluster=True)
    assert an.curve_boot.degenerate is True
    assert an.curve_boot.auc_ci is None
    assert any("폭이 0인 구간은" in w for w in an.warnings)
    txt = format_report(an, show_curve=False)
    assert "군집 보정 AUC" not in txt
    assert "군집(pid) 단위 부트스트랩" not in paper_sentence(an)


def test_few_clusters_are_flagged_as_unreliable(tmp_path):
    rows = ["pid,marker,dx"]
    for i in range(8):
        rows.append(f"U{i},{i},No")
        rows.append(f"U{i},{i + 4},Yes")
    path = write_csv(tmp_path, "few.csv", "\n".join(rows) + "\n")
    ds = load_dataset(read_table(path), "marker", "dx", cluster_col="pid")
    an = analyze(ds, n_boot=300, seed=1, cluster=True)
    assert any("군집이 8개뿐입니다" in w for w in an.warnings)


def test_requested_bootstrap_that_yields_nothing_says_why(tmp_path):
    """--cluster --bootstrap 10 used to produce nothing and still exit 0."""
    ds = load_dataset(read_table(clustered(tmp_path)), "marker", "dx",
                      cluster_col="pid")
    an = analyze(ds, pauc_min_spec=0.9, n_boot=10, seed=1, cluster=True)
    assert an.pauc.ci is None
    assert any("최소 39회 필요" in w for w in an.warnings)
    assert not any("--bootstrap 2000 을 함께 지정하면" in w for w in an.warnings)


def test_no_grade_is_awarded_when_there_is_no_interval(tmp_path):
    """"매우 우수" beside a draft saying discrimination was not demonstrated."""
    from rocdx.report import auc_grade
    path = write_csv(tmp_path, "1v1.csv", "id,marker,dx\n1,5,Yes\n2,1,No\n")
    an = analyze(load_dataset(read_table(path), "marker", "dx"))
    assert an.auc.ci is None
    txt = format_report(an, show_curve=False)
    assert "매우 우수" not in txt
    assert "등급을 매길 수 없습니다" in txt
    assert "신뢰구간을 계산할 수 없어" in paper_sentence(an)
    assert "0.5를 포함하므로" not in paper_sentence(an)
    assert "outstanding" not in auc_grade(1.0, None)


def test_operating_point_bootstrap_draws_are_shared_not_repeated(tmp_path):
    """Four selected points used to redraw four identical resample sets."""
    from rocdx.analyze import stratified_resamples
    ds = load_dataset(read_table(clustered(tmp_path, per_patient=1)), "marker", "dx")
    an = analyze(ds, min_spec=0.8, min_sens=0.8, n_boot=60, seed=9)
    boots = [sp.bootstrap for sp in an.selected if sp.bootstrap is not None]
    assert len(boots) == 4
    # Identical draws for every point: same seed, same effective count.
    assert len({b.n_effective for b in boots}) == 1
    # and the shared draws are exactly what the standalone helper produces
    shared = stratified_resamples(ds.positive, 60, 9)
    assert len(shared) == 60 and all(len(r) == len(ds.scores) for r in shared)
    assert stratified_resamples([True, False], 10, 1) is None
