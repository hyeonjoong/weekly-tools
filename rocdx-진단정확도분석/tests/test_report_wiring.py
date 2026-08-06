"""Tests for the layers a mutation run found unguarded.

Three things the earlier suite could not see: PPV/NPV arithmetic on a table
where they are *not* numerically equal to sensitivity/specificity, the
confidence level actually flowing through every interval, and the report
rendering the caveats that the analysis produced. These are the tool's honesty
guarantees, so they are asserted on the rendered text, not on the objects.
"""

import csv
import math
import os
import random

import pytest

from rocdx.analyze import analyze, bootstrap_selected_point, load_dataset, percentile_ci
from rocdx.cli import main
from rocdx.delong import auc_ci, estimate_auc
from rocdx.loader import read_table
from rocdx.report import (
    ascii_curve,
    auc_grade,
    conf_level,
    format_report,
    markdown_report,
    points_csv_rows,
)
from rocdx.roc import Point, metrics_at, roc_points, youden_point

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "examples")
SEPSIS = os.path.join(EXAMPLES, "sepsis_biomarker.csv")


def write_csv(tmp_path, name, text, encoding="utf-8"):
    p = tmp_path / name
    p.write_bytes(text.encode(encoding))
    return str(p)


def marker_csv(tmp_path, name="m.csv", n=160, sep=",", encoding="utf-8", seed=31,
               shift=1.4):
    rng = random.Random(seed)
    rows = ["id" + sep + "marker" + sep + "rival" + sep + "dx"]
    for i in range(n):
        pos = i % 2 == 0
        m = rng.gauss(shift if pos else 0.0, 1.0)
        r = rng.gauss(0.5 * shift if pos else 0.0, 1.0)
        rows.append(sep.join([str(i), f"{m:.3f}", f"{r:.3f}", "Yes" if pos else "No"]))
    return write_csv(tmp_path, name, "\n".join(rows) + "\n", encoding)


# --- PPV / NPV arithmetic (asymmetric table: PPV != sens, NPV != spec) --------

def test_ppv_and_npv_use_the_column_totals_not_the_row_totals():
    # tp=30 fp=25 fn=5 tn=140 → sens .857, spec .848, PPV .545, NPV .966
    m = metrics_at(Point(1.0, tp=30, fp=25, fn=5, tn=140))
    assert m.sens == pytest.approx(30 / 35)
    assert m.spec == pytest.approx(140 / 165)
    assert m.ppv == pytest.approx(30 / 55)
    assert m.npv == pytest.approx(140 / 145)
    assert m.ppv != pytest.approx(m.sens)
    assert m.npv != pytest.approx(m.spec)
    assert m.ppv_ci[0] < m.ppv < m.ppv_ci[1]
    assert m.npv_ci[0] < m.npv < m.npv_ci[1]


def test_bayes_npv_at_an_assumed_prevalence():
    # sens .90, spec .90 at 1% prevalence:
    # NPV = .9*.99 / (.9*.99 + .1*.01) = 0.998878...
    m = metrics_at(Point(1.0, tp=90, fp=10, fn=10, tn=90), prevalence=0.01)
    assert m.npv == pytest.approx(0.9 * 0.99 / (0.9 * 0.99 + 0.1 * 0.01), abs=1e-9)
    assert m.npv > 0.998
    # and at a high prevalence NPV must fall well below the sample value
    m2 = metrics_at(Point(1.0, tp=90, fp=10, fn=10, tn=90), prevalence=0.80)
    assert m2.npv == pytest.approx(0.9 * 0.2 / (0.9 * 0.2 + 0.1 * 0.8), abs=1e-9)
    assert m2.npv < 0.75


def test_prevalence_adjusted_accuracy_is_the_weighted_average_not_the_balanced_one():
    # sens .95, spec .70 — the two formulas differ only when sens != spec.
    m = metrics_at(Point(1.0, tp=95, fp=30, fn=5, tn=70), prevalence=0.10)
    assert m.sens == pytest.approx(0.95) and m.spec == pytest.approx(0.70)
    assert m.accuracy == pytest.approx(0.95 * 0.10 + 0.70 * 0.90)
    assert m.balanced_accuracy == pytest.approx((0.95 + 0.70) / 2)
    assert m.accuracy != pytest.approx(m.balanced_accuracy)


def test_youden_j_value_is_sens_plus_spec_minus_one():
    pt = Point(1.0, tp=30, fp=25, fn=5, tn=140)
    assert pt.youden == pytest.approx(30 / 35 + 140 / 165 - 1.0)
    assert 0.0 < pt.youden < 1.0


# --- alpha flows all the way through -----------------------------------------

def test_conf_level_text():
    assert conf_level(0.05) == "95%"
    assert conf_level(0.01) == "99%"
    assert conf_level(0.005) == "99.5%"
    assert conf_level(0.001) == "99.9%"
    assert conf_level(0.10) == "90%"


def test_auc_interval_actually_uses_alpha():
    rng = random.Random(2)
    pos = [rng.gauss(1, 1) for _ in range(50)]
    neg = [rng.gauss(0, 1) for _ in range(50)]
    a95 = estimate_auc(pos, neg, alpha=0.05)
    a99 = estimate_auc(pos, neg, alpha=0.01)
    assert a99.ci[0] < a95.ci[0] and a99.ci[1] > a95.ci[1]
    # and the algebra matches an independent computation
    assert a99.ci == pytest.approx(auc_ci(a95.auc, a95.se, 0.01, "logit"))


def test_report_labels_match_the_requested_alpha(tmp_path):
    path = marker_csv(tmp_path)
    ds = load_dataset(read_table(path), "marker", "dx")
    text99 = format_report(analyze(ds, direction="higher", alpha=0.01), show_curve=False)
    assert "99% CI" in text99
    assert "95% CI" not in text99
    text95 = format_report(analyze(ds, direction="higher"), show_curve=False)
    assert "95% CI" in text95


def test_alpha_reaches_the_operating_point_intervals(tmp_path):
    ds = load_dataset(read_table(marker_csv(tmp_path)), "marker", "dx")
    wide = next(sp for sp in analyze(ds, direction="higher", alpha=0.01).selected
                if sp.key == "youden").metrics
    narrow = next(sp for sp in analyze(ds, direction="higher", alpha=0.05).selected
                  if sp.key == "youden").metrics
    assert wide.sens_ci[0] < narrow.sens_ci[0]
    assert wide.sens_ci[1] > narrow.sens_ci[1]


def test_ci_method_reaches_the_auc_interval(tmp_path):
    ds = load_dataset(read_table(marker_csv(tmp_path, shift=2.6)), "marker", "dx")
    logit = analyze(ds, direction="higher", ci_method="logit")
    wald = analyze(ds, direction="higher", ci_method="wald")
    assert logit.auc.ci != wald.auc.ci
    assert "logit" in format_report(logit, show_curve=False)
    assert "Wald" in format_report(wald, show_curve=False)


# --- bootstrap internals ------------------------------------------------------

def test_percentile_ci_returns_the_exact_efron_order_statistics():
    vals = [float(i) for i in range(1000)]     # 0 .. 999
    lo, hi = percentile_ci(vals, 0.05)
    # floor(1001*0.025) = 25 -> index 24 ; ceil(1001*0.975) = 976 -> index 975
    assert (lo, hi) == (24.0, 975.0)
    lo90, hi90 = percentile_ci(vals, 0.10)
    assert (lo90, hi90) == (49.0, 950.0)
    assert hi90 - lo90 < hi - lo          # 90% must be narrower than 95%


def test_percentile_ci_refuses_when_there_are_too_few_draws():
    assert percentile_ci([float(i) for i in range(30)], 0.05) is None   # need >= 39
    assert percentile_ci([float(i) for i in range(60)], 0.05) is not None


def test_bootstrap_depends_on_the_seed():
    rng = random.Random(8)
    scores = [rng.gauss(0, 1) for _ in range(120)]
    positive = [i % 2 == 0 for i in range(120)]
    sel = lambda pts: youden_point(pts)  # noqa: E731
    a = bootstrap_selected_point(scores, positive, sel, 120, seed=1, alpha=0.05)
    b = bootstrap_selected_point(scores, positive, sel, 120, seed=2, alpha=0.05)
    assert a.sens_ci != b.sens_ci or a.youden_corrected != b.youden_corrected


def test_bootstrap_keeps_both_groups_in_every_resample():
    """Stratified resampling: a resample must never lose a whole class."""
    rng = random.Random(9)
    scores = [rng.gauss(0, 1) for _ in range(40)]
    positive = [i < 4 for i in range(40)]   # only 4 cases: unstratified would drop them
    summary = bootstrap_selected_point(scores, positive, lambda pts: youden_point(pts),
                                       200, seed=3, alpha=0.05)
    assert summary is not None
    assert summary.n_effective == 200       # no draw was skipped for a missing class


def test_bootstrap_optimism_is_suppressed_for_too_few_draws():
    rng = random.Random(10)
    scores = [rng.gauss(0, 1) for _ in range(80)]
    positive = [i % 2 == 0 for i in range(80)]
    few = bootstrap_selected_point(scores, positive, lambda pts: youden_point(pts),
                                   10, seed=1, alpha=0.05)
    assert few is not None
    assert few.youden_corrected is None and few.optimism_youden is None


# --- report rendering ---------------------------------------------------------

def test_report_renders_every_warning(tmp_path):
    path = write_csv(tmp_path, "low.csv", "\n".join(
        ["id,marker,dx"] + [f"{i},{i % 4},{'Yes' if i < 6 else 'No'}" for i in range(20)]
    ) + "\n")
    an = analyze(load_dataset(read_table(path), "marker", "dx"), prevalence=0.02)
    assert an.warnings
    text = format_report(an, show_curve=False)
    for w in an.warnings:
        assert w[:25] in text, f"warning not rendered: {w[:40]}"
    md = markdown_report(an)
    for w in an.warnings:
        assert w[:25] in md


def test_markdown_marks_data_chosen_cutoffs_and_keeps_the_caveat(tmp_path):
    ds = load_dataset(read_table(marker_csv(tmp_path)), "marker", "dx")
    md = markdown_report(analyze(ds, direction="higher", cutoffs=[1.0]))
    assert "[데이터에서 선택]" in md
    assert "[사전 지정]" in md
    assert "낙관적으로 부풀려져" in md
    assert md.count("\n|") >= 4


def test_markdown_flags_assumed_prevalence_in_the_ppv_columns(tmp_path):
    ds = load_dataset(read_table(marker_csv(tmp_path)), "marker", "dx")
    md = markdown_report(analyze(ds, direction="higher", prevalence=0.02))
    assert "PPV (유병률 0.02 가정)" in md


def test_auc_grade_wording():
    assert "매우 우수" in auc_grade(0.95, (0.90, 0.98))
    assert "우수" in auc_grade(0.85, (0.80, 0.90))
    assert "보통" in auc_grade(0.75, (0.70, 0.80))
    assert "낮음" in auc_grade(0.65, (0.60, 0.70))
    assert "거의 없음" in auc_grade(0.55, (0.52, 0.58))
    # an interval that still covers chance gets no band at all
    assert "입증하지 못했" in auc_grade(0.55, (0.45, 0.65))
    # below 0.5 the marker is inverted, not useless
    assert "방향이 반대" in auc_grade(0.10, (0.05, 0.20))
    assert "계산 불가" in auc_grade(float("nan"))


def test_cutoff_is_printed_precisely_enough_to_reproduce_the_2x2(tmp_path):
    """A rounded cut-off must not change the counts it is quoted with."""
    rng = random.Random(12)
    lines = ["id,platelet,itp"]
    for i in range(120):
        pos = i % 2 == 0
        lines.append(f"{i},{int(rng.gauss(150000 if pos else 250000, 40000))},"
                     f"{'Yes' if pos else 'No'}")
    path = write_csv(tmp_path, "plt.csv", "\n".join(lines) + "\n")
    an = analyze(load_dataset(read_table(path), "platelet", "itp"), direction="lower")
    sp = next(s for s in an.selected if s.key == "youden")
    text = format_report(an, show_curve=False)
    printed = text.split("절단점 (cut-off) : platelet <= ")[1].split("\n")[0].strip()
    value = float(printed)
    # Re-apply the printed rule to the raw data.
    ds = an.dataset
    tp = sum(1 for s, p in zip(ds.scores, ds.positive) if p and s <= value)
    fp = sum(1 for s, p in zip(ds.scores, ds.positive) if not p and s <= value)
    assert (tp, fp) == (sp.metrics.point.tp, sp.metrics.point.fp)


def test_points_csv_columns_are_what_the_header_says(tmp_path):
    an = analyze(load_dataset(read_table(marker_csv(tmp_path)), "marker", "dx"),
                 direction="higher")
    rows = points_csv_rows(an)
    header = rows[0]
    assert header[6:9] == ["sensitivity", "specificity", "one_minus_specificity"]
    for r in rows[1:]:
        tp, fp, fn, tn = (int(x) for x in r[2:6])
        assert float(r[6]) == pytest.approx(tp / (tp + fn))
        assert float(r[7]) == pytest.approx(tn / (tn + fp))
        assert float(r[8]) == pytest.approx(1.0 - tn / (tn + fp))
        assert float(r[9]) == pytest.approx(float(r[6]) + float(r[7]) - 1.0)
    # the trivial endpoint has no finite cut-off and must not print "inf"
    assert rows[1][0] == ""


def test_ascii_curve_marks_the_selected_point(tmp_path):
    an = analyze(load_dataset(read_table(marker_csv(tmp_path)), "marker", "dx"),
                 direction="higher")
    best = next(sp for sp in an.selected if sp.key == "youden")
    art = ascii_curve(an.points, marks=[(best.metrics.point, "Y")])
    assert "Y" in art
    assert "Y" not in ascii_curve(an.points)


def test_trivial_endpoint_cutoffs_are_described_in_words(tmp_path):
    """An "everybody/nobody positive" point must not print a +inf cut-off."""
    # The largest value belongs to a control, so specificity 1.0 is reachable
    # only at the "nobody called positive" endpoint.
    path = write_csv(tmp_path, "endpoint.csv", "\n".join([
        "id,marker,dx", "1,1,Yes", "2,2,No", "3,3,Yes", "4,4,Yes", "5,9,No",
    ]) + "\n")
    an = analyze(load_dataset(read_table(path), "marker", "dx"),
                 direction="higher", min_spec=1.0)
    text = format_report(an, show_curve=False)
    cut_lines = [ln for ln in text.splitlines() if "절단점 (cut-off)" in ln]
    assert cut_lines
    for line in cut_lines:
        assert "∞" not in line
    # a useless marker under a 100%-specificity floor lands on the trivial point
    assert any("모두 음성으로 판정" in ln for ln in cut_lines)


# --- CLI flag wiring ----------------------------------------------------------

def test_cli_compare_actually_runs_a_comparison(capsys):
    without = main([SEPSIS, "--score", "crp_mg_L", "--truth", "sepsis", "--no-curve"])
    out_without = capsys.readouterr().out
    with_cmp = main([SEPSIS, "--score", "crp_mg_L", "--truth", "sepsis", "--no-curve",
                     "--compare", "procalcitonin_ng_mL"])
    out_with = capsys.readouterr().out
    assert without == 0 and with_cmp == 0
    assert "두 검사 비교" not in out_without
    assert "두 검사 비교" in out_with
    assert "procalcitonin_ng_mL: AUC" in out_with


def test_cli_bootstrap_flag_is_wired(capsys):
    main([SEPSIS, "--score", "crp_mg_L", "--truth", "sepsis", "--no-curve"])
    assert "부트스트랩" not in capsys.readouterr().out
    main([SEPSIS, "--score", "crp_mg_L", "--truth", "sepsis", "--no-curve",
          "--bootstrap", "60"])
    out = capsys.readouterr().out
    assert "부트스트랩 (60/60회" in out


def test_cli_direction_flag_is_wired(capsys):
    main([SEPSIS, "--score", "crp_mg_L", "--truth", "sepsis", "--no-curve",
          "--direction", "lower"])
    out = capsys.readouterr().out
    assert "값이 낮을수록 질환" in out
    assert "crp_mg_L <=" in out


def test_cli_no_curve_flag_is_wired(capsys):
    main([SEPSIS, "--score", "crp_mg_L", "--truth", "sepsis"])
    assert "ROC 곡선" in capsys.readouterr().out
    main([SEPSIS, "--score", "crp_mg_L", "--truth", "sepsis", "--no-curve"])
    assert "ROC 곡선" not in capsys.readouterr().out


def test_cli_alpha_flag_is_wired(capsys):
    main([SEPSIS, "--score", "crp_mg_L", "--truth", "sepsis", "--no-curve",
          "--alpha", "0.01"])
    out = capsys.readouterr().out
    assert "99% CI" in out and "95% CI" not in out


def test_cli_sep_and_encoding_flags_are_wired(tmp_path, capsys):
    path = marker_csv(tmp_path, name="semi.csv", sep=";", encoding="cp949")
    code = main([path, "--score", "marker", "--truth", "dx", "--no-curve",
                 "--sep", ";", "--encoding", "cp949"])
    out = capsys.readouterr().out
    assert code == 0 and "AUC" in out
    assert "구분자 ';'" in out


def test_cli_accepts_a_literal_backslash_t_as_the_separator(tmp_path, capsys):
    path = marker_csv(tmp_path, name="tabs.tsv", sep="\t")
    code = main([path, "--score", "marker", "--truth", "dx", "--no-curve",
                 "--sep", "\\t"])
    assert code == 0 and "AUC" in capsys.readouterr().out


def test_cli_rejects_a_multi_character_separator(tmp_path, capsys):
    path = marker_csv(tmp_path)
    code = main([path, "--score", "marker", "--truth", "dx", "--sep", "::"])
    assert code == 2
    assert "한 글자" in capsys.readouterr().err


def test_cli_reports_a_wrong_encoding_instead_of_crashing(tmp_path, capsys):
    path = marker_csv(tmp_path, name="u.csv", encoding="utf-8")
    write_csv(tmp_path, "u.csv", "id,점수,진단\n1,3.2,양성\n2,1.1,음성\n")
    code = main([str(tmp_path / "u.csv"), "--score", "점수", "--truth", "진단",
                 "--encoding", "cp949"])
    assert code == 2
    assert "인코딩" in capsys.readouterr().err


def test_cli_reports_an_unknown_encoding_name(tmp_path, capsys):
    code = main([marker_csv(tmp_path), "--score", "marker", "--truth", "dx",
                 "--encoding", "ansi"])
    assert code == 2
    assert "알 수 없는 인코딩" in capsys.readouterr().err


def test_cli_rejects_a_non_finite_cutoff(tmp_path, capsys):
    code = main([marker_csv(tmp_path), "--score", "marker", "--truth", "dx",
                 "--cutoff", "nan"])
    assert code == 2
    assert "유한한 숫자" in capsys.readouterr().err


def test_cli_explains_when_the_comparator_emptied_the_dataset(tmp_path, capsys):
    path = write_csv(tmp_path, "cmp.csv", "\n".join([
        "id,marker,other,dx", "1,1,,No", "2,2,,No", "3,3,,Yes", "4,4,,Yes",
    ]) + "\n")
    code = main([path, "--score", "marker", "--truth", "dx", "--compare", "other"])
    err = capsys.readouterr().err
    assert code == 2
    assert "비교 검사값 없음" in err


def test_cli_does_not_dump_the_whole_file_when_a_column_is_missing(tmp_path, capsys):
    header = ",".join(f"col{i}" for i in range(60))
    rows = "\n".join(",".join(str(i) for i in range(60)) for _ in range(5))
    path = write_csv(tmp_path, "wide.csv", header + "\n" + rows + "\n")
    code = main([path, "--score", "nope", "--truth", "col1"])
    err = capsys.readouterr().err
    assert code == 2
    assert "col59" not in err          # the list is truncated
    assert "총 60개" in err


# --- data handling honesty ----------------------------------------------------

def test_mixed_percent_and_plain_numbers_raise_a_visible_warning(tmp_path):
    path = write_csv(tmp_path, "pct.csv", "\n".join([
        "id,prob,dx", "1,87%,Yes", "2,91,Yes", "3,12%,No", "4,20,No", "5,78%,Yes",
        "6,33,No",
    ]) + "\n")
    ds = load_dataset(read_table(path), "prob", "dx")
    assert any("퍼센트 표기" in n for n in ds.notes)
    assert any("섞여 있습니다" in n for n in ds.notes)


def test_a_decimal_comma_column_is_not_read_as_thousands(tmp_path):
    """The 1000x trap: "1,614" and "1,06" in one column must agree."""
    rows = ["id;creat;dx"]
    vals = [("1,614", "Yes"), ("1,06", "No"), ("1,439", "Yes"), ("1,02", "No"),
            ("1,712", "Yes"), ("0,98", "No")]
    for i, (v, dx) in enumerate(vals):
        rows.append(f"P{i};{v};{dx}")
    path = write_csv(tmp_path, "euro.csv", "\n".join(rows) + "\n")
    ds = load_dataset(read_table(path), "creat", "dx")
    assert max(ds.scores) < 10.0        # not 1614
    assert ds.scores[0] == pytest.approx(1.614)
    assert any("소수점으로 해석" in n for n in ds.notes)


def test_a_thousands_separator_column_is_still_read_as_thousands(tmp_path):
    rows = ["id,wbc,dx"]
    for i, (v, dx) in enumerate([('"12,300"', "Yes"), ('"4,500"', "No"),
                                 ('"18,900"', "Yes"), ('"5,100"', "No")]):
        rows.append(f"P{i},{v},{dx}")
    path = write_csv(tmp_path, "thou.csv", "\n".join(rows) + "\n")
    ds = load_dataset(read_table(path), "wbc", "dx")
    assert max(ds.scores) == pytest.approx(18900.0)


def test_stray_high_byte_does_not_become_utf16_mojibake(tmp_path):
    raw = ("id,marker,dx\n" + "".join(f"{i},{i}.0,{'Yes' if i % 2 else 'No'}\n"
                                      for i in range(1, 41))).encode("ascii")
    raw = raw[:20] + b"\x92" + raw[20:]        # a Windows curly apostrophe
    p = tmp_path / "stray.csv"
    p.write_bytes(raw)
    table = read_table(str(p))
    assert table.headers[:1] == ["id"]
    assert len(table.rows) >= 39


def test_unparsed_examples_are_truncated_before_they_reach_the_report(tmp_path):
    long_cell = "홍길동 010-2345-6789 서울대병원 재검요망"
    path = write_csv(tmp_path, "pii.csv", "\n".join([
        "id,marker,dx", f"1,{long_cell},Yes", "2,2.0,No", "3,3.0,Yes", "4,1.0,No",
    ]) + "\n")
    ds = load_dataset(read_table(path), "marker", "dx")
    note = next(n for n in ds.notes if "읽을 수 없는" in n)
    assert long_cell not in note
    assert "010-2345-6789" not in note


def test_list_columns_hides_cell_values_by_default(tmp_path, capsys):
    path = write_csv(tmp_path, "phi.csv", "\n".join([
        "MRN,name,crp,sepsis",
        "1234567,홍길동,12.3,Yes",
        "7654321,김철수,3.1,No",
    ]) + "\n")
    assert main([path, "--list-columns"]) == 0
    out = capsys.readouterr().out
    assert "MRN" in out and "crp" in out          # names are what the user needs
    assert "1234567" not in out and "홍길동" not in out
    assert main([path, "--list-columns", "--show-samples"]) == 0
    out2 = capsys.readouterr().out
    assert "1234567" in out2                       # opted in, but truncated
    assert "홍길동" in out2


def test_output_survives_an_ascii_only_terminal(tmp_path):
    """LC_ALL=C on a hospital workstation must not crash the report."""
    import subprocess
    import sys as _sys
    env = dict(os.environ, PYTHONIOENCODING="ascii", LC_ALL="C", LANG="C")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run(
        [_sys.executable, "-m", "rocdx.cli", SEPSIS, "--score", "crp_mg_L",
         "--truth", "sepsis", "--no-curve"],
        cwd=root, env=env, capture_output=True)
    assert proc.returncode == 0, proc.stderr[-500:]
    assert b"AUC" in proc.stdout
