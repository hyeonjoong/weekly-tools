"""End-to-end behaviour: cleaning, direction, bootstrap honesty, CLI, reports."""

import csv
import math
import os
import random

import pytest

from rocdx.analyze import (
    add_comparison,
    analyze,
    bootstrap_selected_point,
    load_dataset,
    orient,
    percentile_ci,
)
from rocdx.cli import main
from rocdx.loader import LoadError, read_table
from rocdx.report import (
    ascii_curve,
    format_report,
    markdown_report,
    paper_sentence,
    points_csv_rows,
)
from rocdx.roc import roc_points, youden_point

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "examples")


def write_csv(tmp_path, name, text, encoding="utf-8"):
    p = tmp_path / name
    p.write_bytes(text.encode(encoding))
    return str(p)


def toy(tmp_path, name="toy.csv"):
    """A small, fully hand-checkable dataset with one messy row of each kind."""
    return write_csv(tmp_path, name, "\n".join([
        "id,marker,rival,dx",
        "1,1.0,9.0,No",
        "2,2.0,8.0,No",
        "3,3.0,7.0,No",
        "4,4.0,6.0,Yes",
        "5,5.0,5.0,Yes",
        "6,6.0,4.0,Yes",
        "7,,3.0,Yes",        # missing marker  → dropped
        "8,7.0,2.0,",        # missing outcome → dropped
    ]) + "\n")


# --- cleaning -----------------------------------------------------------------

def test_rows_with_missing_values_are_dropped_and_counted(tmp_path):
    ds = load_dataset(read_table(toy(tmp_path)), "marker", "dx")
    assert len(ds.scores) == 6
    assert ds.n_pos == 3 and ds.n_neg == 3
    assert ds.n_dropped == 2
    assert sum(ds.drop_reasons.values()) == 2
    assert ds.positive_label == "Yes" and ds.negative_label == "No"


def test_comparator_rows_are_dropped_pairwise_so_both_markers_share_subjects(tmp_path):
    path = write_csv(tmp_path, "c.csv", "\n".join([
        "id,a,b,dx",
        "1,1,1,No", "2,2,,No", "3,3,3,Yes", "4,4,4,Yes",
    ]) + "\n")
    ds = load_dataset(read_table(path), "a", "dx", compare_cols=["b"])
    assert len(ds.scores) == 3
    assert len(ds.extra["b"]) == 3


def test_detection_limit_values_are_used_and_reported(tmp_path):
    path = write_csv(tmp_path, "d.csv", "\n".join([
        "id,marker,dx", "1,<0.05,No", "2,0.2,No", "3,1.0,Yes", "4,2.0,Yes",
    ]) + "\n")
    ds = load_dataset(read_table(path), "marker", "dx")
    assert ds.scores[0] == pytest.approx(0.05)
    assert any("검출한계" in n for n in ds.notes)


def test_analysis_refuses_a_single_class(tmp_path):
    path = write_csv(tmp_path, "one.csv", "id,marker,dx\n1,1,Yes\n2,2,Yes\n")
    with pytest.raises(LoadError):
        load_dataset(read_table(path), "marker", "dx")


def test_analysis_refuses_impossible_parameters(tmp_path):
    ds = load_dataset(read_table(toy(tmp_path)), "marker", "dx")
    with pytest.raises(LoadError):
        analyze(ds, alpha=0.0)
    with pytest.raises(LoadError):
        analyze(ds, prevalence=1.5)


# --- direction ----------------------------------------------------------------

def test_orient_lower_is_worse_flips_and_gives_the_mirrored_auc():
    scores = [1, 2, 3, 4, 5, 6]
    positive = [True, True, True, False, False, False]  # low values = disease
    raw, flipped, how = orient(scores, positive, "higher")
    assert not flipped and how == "user"
    low, flipped, how = orient(scores, positive, "lower")
    assert flipped and low == [-1, -2, -3, -4, -5, -6]
    auto, flipped, how = orient(scores, positive, "auto")
    assert flipped and how == "auto"


def test_auto_direction_warns_that_it_inflates_the_auc(tmp_path):
    path = write_csv(tmp_path, "low.csv", "\n".join([
        "id,marker,dx", "1,1,Yes", "2,2,Yes", "3,3,Yes", "4,4,No", "5,5,No", "6,6,No",
    ]) + "\n")
    ds = load_dataset(read_table(path), "marker", "dx")
    an = analyze(ds, direction="auto")
    assert an.flipped and an.auc.auc == pytest.approx(1.0)
    assert any("자동으로 뒤집" in w for w in an.warnings)


def test_flipped_cutoff_is_reported_in_the_users_own_units(tmp_path):
    path = write_csv(tmp_path, "low2.csv", "\n".join([
        "id,mmse,dx", "1,18,Yes", "2,20,Yes", "3,22,Yes", "4,26,No", "5,28,No", "6,30,No",
    ]) + "\n")
    an = analyze(load_dataset(read_table(path), "mmse", "dx"), direction="lower")
    best = next(sp for sp in an.selected if sp.key == "youden")
    value, op = an.cutoff_in_original_units(best.metrics.point.threshold)
    assert op == "<="
    assert 22 <= value <= 26
    assert f"mmse <= {value:g}" in format_report(an)


def test_explicit_direction_does_not_flip_even_when_the_auc_is_below_half(tmp_path):
    path = write_csv(tmp_path, "low3.csv", "\n".join([
        "id,marker,dx", "1,1,Yes", "2,2,Yes", "3,4,No", "4,5,No",
    ]) + "\n")
    an = analyze(load_dataset(read_table(path), "marker", "dx"), direction="higher")
    assert not an.flipped
    assert an.auc.auc == pytest.approx(0.0)


# --- analysis contents --------------------------------------------------------

def test_perfect_marker_gives_auc_one_and_a_clean_cutoff(tmp_path):
    an = analyze(load_dataset(read_table(toy(tmp_path)), "marker", "dx"))
    assert an.auc.auc == pytest.approx(1.0)
    best = next(sp for sp in an.selected if sp.key == "youden")
    assert best.metrics.sens == 1.0 and best.metrics.spec == 1.0
    value, op = an.cutoff_in_original_units(best.metrics.point.threshold)
    assert (value, op) == (4.0, ">=")


def test_requested_operating_points_appear_and_infeasible_ones_are_marked(tmp_path):
    ds = load_dataset(read_table(toy(tmp_path)), "marker", "dx")
    an = analyze(ds, min_spec=0.5, min_sens=1.5, cutoffs=[2.0])
    keys = {sp.key for sp in an.selected}
    assert {"youden", "topleft", "min_spec", "min_sens", "cutoff:2.0"} <= keys
    infeasible = next(sp for sp in an.selected if sp.key == "min_sens")
    assert not infeasible.feasible
    fixed = next(sp for sp in an.selected if sp.key == "cutoff:2.0")
    assert fixed.data_chosen is False
    # marker >= 2.0 → all 3 positives caught, 2 of 3 negatives flagged
    assert fixed.metrics.point.tp == 3 and fixed.metrics.point.fp == 2


def test_user_cutoff_is_flipped_into_the_oriented_scale(tmp_path):
    path = write_csv(tmp_path, "low4.csv", "\n".join([
        "id,mmse,dx", "1,18,Yes", "2,20,Yes", "3,22,Yes", "4,26,No", "5,28,No", "6,30,No",
    ]) + "\n")
    an = analyze(load_dataset(read_table(path), "mmse", "dx"), direction="lower",
                 cutoffs=[24.0])
    sp = next(s for s in an.selected if s.key.startswith("cutoff:"))
    pt = sp.metrics.point
    assert (pt.tp, pt.fp, pt.fn, pt.tn) == (3, 0, 0, 3)  # mmse <= 24 catches all cases


def test_warnings_fire_for_small_groups_and_few_distinct_values(tmp_path):
    an = analyze(load_dataset(read_table(toy(tmp_path)), "marker", "dx"))
    assert any("표본이 매우 작습니다" in w for w in an.warnings)


def test_heavy_ties_warning(tmp_path):
    rng = random.Random(1)
    lines = ["id,marker,dx"]
    for i in range(120):
        pos = i % 3 == 0
        lines.append(f"{i},{rng.choice([1, 2, 3, 4, 5, 6, 7])},{'Yes' if pos else 'No'}")
    path = write_csv(tmp_path, "ties.csv", "\n".join(lines) + "\n")
    an = analyze(load_dataset(read_table(path), "marker", "dx"), direction="higher")
    assert any("동점" in w for w in an.warnings)


def test_prevalence_override_changes_ppv_and_is_flagged(tmp_path):
    ds = load_dataset(read_table(toy(tmp_path)), "marker", "dx")
    base = analyze(ds)
    adj = analyze(ds, prevalence=0.01)
    b = next(sp for sp in base.selected if sp.key == "youden").metrics
    a = next(sp for sp in adj.selected if sp.key == "youden").metrics
    assert a.prevalence_source == "user" and b.prevalence_source == "sample"
    assert a.ppv <= b.ppv
    assert any("유병률" in w for w in adj.warnings)


def test_paired_comparison_is_attached_and_symmetric_in_magnitude(tmp_path):
    ds = load_dataset(read_table(toy(tmp_path)), "marker", "dx", compare_cols=["rival"])
    an = analyze(ds, direction="higher")
    cmp_ = add_comparison(an, "rival", direction="higher")
    assert cmp_.auc_a == pytest.approx(1.0)
    assert cmp_.auc_b == pytest.approx(0.0)   # rival runs the other way
    assert cmp_.diff == pytest.approx(1.0)
    assert an.comparisons == [cmp_]


def test_comparison_of_an_unloaded_column_raises(tmp_path):
    an = analyze(load_dataset(read_table(toy(tmp_path)), "marker", "dx"))
    with pytest.raises(LoadError):
        add_comparison(an, "rival")


# --- bootstrap honesty --------------------------------------------------------

def _noise_dataset(seed=3, n=120):
    rng = random.Random(seed)
    scores = [rng.gauss(0, 1) for _ in range(n)]
    positive = [i % 2 == 0 for i in range(n)]
    return scores, positive


def test_bootstrap_detects_optimism_in_a_useless_marker():
    """A pure-noise marker still shows J > 0 in-sample; the correction removes it."""
    scores, positive = _noise_dataset()
    apparent = youden_point(roc_points(scores, positive)).youden
    summary = bootstrap_selected_point(
        scores, positive, lambda pts: youden_point(pts), n_boot=200, seed=7, alpha=0.05)
    assert summary is not None
    assert apparent > 0.1                     # in-sample it looks like something
    assert summary.optimism_youden > 0.05     # and the bootstrap says why
    assert summary.youden_corrected < apparent


def test_bootstrap_interval_is_wider_than_the_fixed_cutoff_interval(tmp_path):
    rng = random.Random(19)
    lines = ["id,marker,dx"]
    for i in range(160):
        pos = i % 2 == 0
        lines.append(f"{i},{rng.gauss(1.0 if pos else 0.0, 1.0):.3f},"
                     f"{'Yes' if pos else 'No'}")
    path = write_csv(tmp_path, "boot.csv", "\n".join(lines) + "\n")
    an = analyze(load_dataset(read_table(path), "marker", "dx"), direction="higher",
                 n_boot=300, seed=5)
    sp = next(s for s in an.selected if s.key == "youden")
    assert sp.bootstrap is not None
    wilson_w = sp.metrics.sens_ci[1] - sp.metrics.sens_ci[0]
    boot_w = sp.bootstrap.sens_ci[1] - sp.bootstrap.sens_ci[0]
    assert boot_w > wilson_w


def test_bootstrap_is_reproducible_for_a_fixed_seed():
    scores, positive = _noise_dataset(seed=42)
    kw = dict(selector=lambda pts: youden_point(pts), n_boot=100, seed=11, alpha=0.05)
    a = bootstrap_selected_point(scores, positive, **kw)
    b = bootstrap_selected_point(scores, positive, **kw)
    assert a.sens_ci == b.sens_ci and a.youden_corrected == b.youden_corrected


def test_bootstrap_is_skipped_when_a_group_is_too_small():
    assert bootstrap_selected_point([1, 2, 3], [True, False, False],
                                    lambda pts: youden_point(pts), 100, 1, 0.05) is None


def test_bootstrap_is_not_run_for_user_supplied_cutoffs(tmp_path):
    an = analyze(load_dataset(read_table(toy(tmp_path)), "marker", "dx"),
                 cutoffs=[2.0], n_boot=50)
    fixed = next(sp for sp in an.selected if sp.key.startswith("cutoff:"))
    assert fixed.bootstrap is None


def test_percentile_ci_edges():
    assert percentile_ci([1.0] * 5, 0.05) is None          # too few draws
    lo, hi = percentile_ci([float(i) for i in range(100)], 0.05)
    assert lo <= 5 and hi >= 94


# --- report -------------------------------------------------------------------

def test_report_states_direction_dropped_rows_and_the_selection_caveat(tmp_path):
    an = analyze(load_dataset(read_table(toy(tmp_path)), "marker", "dx"),
                 direction="higher")
    text = format_report(an)
    assert "값이 높을수록 질환" in text
    assert "제외된 행" in text and "2 / 8" in text
    assert "낙관적으로 부풀려집니다" in text
    assert "AUC" in text and "Mann-Whitney" in text


def strong_marker_csv(tmp_path, name="strong.csv", n=160, seed=19):
    """A dataset big and clean enough that discrimination really is demonstrated."""
    rng = random.Random(seed)
    lines = ["id,marker,dx"]
    for i in range(n):
        pos = i % 2 == 0
        lines.append(f"{i},{rng.gauss(1.6 if pos else 0.0, 1.0):.3f},"
                     f"{'Yes' if pos else 'No'}")
    return write_csv(tmp_path, name, "\n".join(lines) + "\n")


def test_paper_sentence_keeps_the_overfitting_caveat(tmp_path):
    an = analyze(load_dataset(read_table(strong_marker_csv(tmp_path)), "marker", "dx"),
                 direction="higher")
    s = paper_sentence(an)
    assert "본 자료에서 선택" in s
    assert "AUC" in s
    assert "Youden" in s


def test_paper_sentence_refuses_to_claim_performance_for_an_unproven_marker(tmp_path):
    """A pure-noise marker must not produce a publishable-looking sentence."""
    rng = random.Random(4)
    lines = ["id,marker,dx"]
    for i in range(120):
        lines.append(f"{i},{rng.gauss(0, 1):.3f},{'Yes' if i % 2 == 0 else 'No'}")
    path = write_csv(tmp_path, "noise.csv", "\n".join(lines) + "\n")
    an = analyze(load_dataset(read_table(path), "marker", "dx"), direction="higher")
    s = paper_sentence(an)
    assert "입증하지 못하였다" in s
    assert "Youden 지수를 최대화하는" not in s
    assert "not demonstrated" in s


def test_paper_sentence_reports_enrolled_and_analysable_counts(tmp_path):
    an = analyze(load_dataset(read_table(toy(tmp_path)), "marker", "dx"))
    s = paper_sentence(an)
    assert "총 8명 중 분석 가능한 6명" in s


def test_markdown_report_is_a_table(tmp_path):
    an = analyze(load_dataset(read_table(toy(tmp_path)), "marker", "dx"))
    md = markdown_report(an)
    assert md.count("|") > 10
    assert "AUC" in md


def test_ascii_curve_draws_something_bounded(tmp_path):
    an = analyze(load_dataset(read_table(toy(tmp_path)), "marker", "dx"))
    art = ascii_curve(an.points, width=20, height=10)
    lines = art.splitlines()
    assert any("*" in ln for ln in lines)
    assert all(len(ln) < 200 for ln in lines)


def test_points_csv_rows_cover_every_operating_point(tmp_path):
    an = analyze(load_dataset(read_table(toy(tmp_path)), "marker", "dx"))
    rows = points_csv_rows(an)
    assert rows[0][0] == "cutoff"
    assert len(rows) == len(an.points) + 1
    for r in rows[1:]:
        sens, spec = float(r[6]), float(r[7])
        assert 0.0 <= sens <= 1.0 and 0.0 <= spec <= 1.0


# --- CLI ----------------------------------------------------------------------

def test_cli_runs_on_the_bundled_example(capsys):
    code = main([os.path.join(EXAMPLES, "sepsis_biomarker.csv"),
                 "--score", "crp_mg_L", "--truth", "sepsis", "--no-curve"])
    out = capsys.readouterr().out
    assert code == 0
    assert "AUC" in out and "민감도" in out


def test_cli_handles_the_korean_cp949_example(capsys):
    code = main([os.path.join(EXAMPLES, "cognitive_screen_kr.csv"),
                 "--score", "인지검사점수", "--truth", "치매진단",
                 "--direction", "lower", "--no-curve"])
    out = capsys.readouterr().out
    assert code == 0
    assert "인지검사점수 <=" in out


def test_cli_paired_comparison_on_the_example(capsys):
    code = main([os.path.join(EXAMPLES, "sepsis_biomarker.csv"),
                 "--score", "procalcitonin_ng_mL", "--truth", "sepsis",
                 "--compare", "crp_mg_L", "--no-curve"])
    out = capsys.readouterr().out
    assert code == 0
    assert "DeLong" in out


def test_cli_list_columns(capsys):
    code = main([os.path.join(EXAMPLES, "sepsis_biomarker.csv"), "--list-columns"])
    out = capsys.readouterr().out
    assert code == 0 and "crp_mg_L" in out


def test_cli_missing_arguments_exit_two(capsys):
    code = main([os.path.join(EXAMPLES, "sepsis_biomarker.csv")])
    assert code == 2
    assert "--score" in capsys.readouterr().err


def test_cli_bad_column_reports_available_columns(capsys):
    code = main([os.path.join(EXAMPLES, "sepsis_biomarker.csv"),
                 "--score", "nope", "--truth", "sepsis"])
    assert code == 2
    assert "crp_mg_L" in capsys.readouterr().err


def test_cli_rejects_a_percentage_typed_as_ninety(capsys):
    code = main([os.path.join(EXAMPLES, "sepsis_biomarker.csv"),
                 "--score", "crp_mg_L", "--truth", "sepsis", "--min-spec", "90"])
    assert code == 2
    assert "0과 1 사이" in capsys.readouterr().err


def test_cli_writes_a_points_csv(tmp_path, capsys):
    out_path = str(tmp_path / "points.csv")
    code = main([os.path.join(EXAMPLES, "sepsis_biomarker.csv"),
                 "--score", "crp_mg_L", "--truth", "sepsis", "--no-curve",
                 "--points-csv", out_path])
    assert code == 0
    capsys.readouterr()
    with open(out_path, encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    assert rows[0][0] == "cutoff" and len(rows) > 10


def test_cli_markdown_mode(capsys):
    code = main([os.path.join(EXAMPLES, "sepsis_biomarker.csv"),
                 "--score", "crp_mg_L", "--truth", "sepsis", "--markdown"])
    out = capsys.readouterr().out
    assert code == 0 and out.lstrip().startswith("###")


def test_cli_prevalence_and_cutoffs(capsys):
    code = main([os.path.join(EXAMPLES, "sepsis_biomarker.csv"),
                 "--score", "crp_mg_L", "--truth", "sepsis", "--no-curve",
                 "--prevalence", "0.02", "--cutoff", "10", "--cutoff", "50"])
    out = capsys.readouterr().out
    assert code == 0
    assert "사용자 지정" in out
    assert "crp_mg_L >= 10" in out and "crp_mg_L >= 50" in out
