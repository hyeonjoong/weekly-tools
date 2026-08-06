"""End-to-end CLI tests: every documented flag, and the failure modes.

These drive ``main(argv)`` exactly as the shell does, so they also cover the
argument wiring that unit tests of the statistics can't see.
"""

import csv as csvmod
import json

import pytest

from statwise.cli import main

LONG = "score,arm\n"
WIDE = "control,treatment\n"


def _write(tmp_path, name, text, encoding="utf-8"):
    p = tmp_path / name
    p.write_text(text, encoding=encoding)
    return str(p)


@pytest.fixture
def long_csv(tmp_path):
    rows = ["score,arm"]
    for v in (5.1, 4.9, 5.3, 5.0, 5.2, 4.8, 5.4, 5.0):
        rows.append(f"{v},control")
    for v in (7.1, 6.9, 7.3, 7.0, 7.2, 6.8, 7.4, 7.0):
        rows.append(f"{v},drug")
    return _write(tmp_path, "long.csv", "\n".join(rows) + "\n")


@pytest.fixture
def wide_csv(tmp_path):
    rows = ["low,mid,high"]
    data = [(3, 6, 9), (5, 8, 11), (4, 7, 10), (6, 9, 12), (3, 7, 11),
            (5, 6, 13), (4, 8, 9), (6, 7, 12)]
    for a, b, c in data:
        rows.append(f"{a},{b},{c}")
    return _write(tmp_path, "wide.csv", "\n".join(rows) + "\n")


@pytest.fixture
def paired_csv(tmp_path):
    rows = ["subject,time,isi"]
    pre = [18, 20, 17, 19, 21, 16, 22, 18, 20, 19]
    for i, v in enumerate(pre):
        rows.append(f"S{i:02d},pre,{v}")
        rows.append(f"S{i:02d},post,{v - 5 - (i % 3)}")
    return _write(tmp_path, "paired.csv", "\n".join(rows) + "\n")


@pytest.fixture
def binary_csv(tmp_path):
    rows = ["subject,arm,responder"]
    for i in range(50):
        rows.append(f"P{i:02d},placebo,{'yes' if i < 10 else 'no'}")
    for i in range(52):
        rows.append(f"D{i:02d},drug,{'yes' if i < 22 else 'no'}")
    return _write(tmp_path, "binary.csv", "\n".join(rows) + "\n")


@pytest.fixture
def multi_csv(tmp_path):
    rows = ["subject,arm,isi,psqi"]
    for i in range(12):
        rows.append(f"S{i:02d},drug,{10 + i % 4},{6 + i % 3}")
    for i in range(12):
        rows.append(f"T{i:02d},placebo,{16 + i % 4},{9 + i % 3}")
    return _write(tmp_path, "multi.csv", "\n".join(rows) + "\n")


# --------------------------------------------------------------------------
# core modes
# --------------------------------------------------------------------------

def test_long_format_runs(long_csv, capsys):
    assert main([long_csv, "--value", "score", "--group", "arm"]) == 0
    out = capsys.readouterr().out
    assert "[1] 기술통계" in out
    assert "Student's t-test" in out
    assert "control" in out and "drug" in out


def test_wide_format_runs_with_posthoc(wide_csv, capsys):
    assert main([wide_csv, "--wide"]) == 0
    out = capsys.readouterr().out
    assert "One-way ANOVA" in out
    assert "[5] 사후검정" in out


def test_no_posthoc_flag(wide_csv, capsys):
    assert main([wide_csv, "--wide", "--no-posthoc"]) == 0
    assert "[5] 사후검정" not in capsys.readouterr().out


def test_columns_subset(wide_csv, capsys):
    assert main([wide_csv, "--wide", "--columns", "low,high"]) == 0
    out = capsys.readouterr().out
    assert "mid" not in out.split("[논문용")[0]


def test_paired_long(paired_csv, capsys):
    rc = main([paired_csv, "--paired", "--value", "isi", "--group", "time",
               "--id", "subject", "--baseline", "pre"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "차이 = (post − pre)" in out
    assert "Paired t-test" in out or "Wilcoxon" in out


def test_json_output_parses(long_csv, capsys):
    assert main([long_csv, "--value", "score", "--group", "arm",
                 "--format", "json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["schema"] == "statwise/analysis/1"
    assert d["test"]["significant"] is True
    assert d["groups"][0]["n"] == 8


def test_csv_output_is_parseable(long_csv, capsys):
    assert main([long_csv, "--value", "score", "--group", "arm",
                 "--format", "csv"]) == 0
    rows = list(csvmod.reader(capsys.readouterr().out.strip().splitlines()))
    assert rows[0][0] == "endpoint"
    assert rows[1][1] == "continuous"
    assert float(rows[1][12]) < 0.05          # pvalue column


def test_output_file_is_written(long_csv, tmp_path, capsys):
    dest = tmp_path / "out.csv"
    assert main([long_csv, "--value", "score", "--group", "arm",
                 "--format", "csv", "-o", str(dest)]) == 0
    assert dest.exists()
    text = dest.read_text(encoding="utf-8-sig")
    assert text.startswith("endpoint,kind")
    assert "저장했습니다" in capsys.readouterr().err


def test_output_to_unwritable_path_fails_cleanly(long_csv, tmp_path, capsys):
    dest = tmp_path / "nope" / "out.txt"
    rc = main([long_csv, "--value", "score", "--group", "arm", "-o", str(dest)])
    assert rc == 2
    assert "결과 파일" in capsys.readouterr().err


# --------------------------------------------------------------------------
# equivalence / non-inferiority flags
# --------------------------------------------------------------------------

def test_equivalence_margin_flag(long_csv, capsys):
    assert main([long_csv, "--value", "score", "--group", "arm",
                 "--equivalence-margin", "0.5"]) == 0
    out = capsys.readouterr().out
    assert "[3b] 등가성 검정" in out
    assert "p(TOST)" in out


def test_asymmetric_margin_flag(long_csv, capsys):
    assert main([long_csv, "--value", "score", "--group", "arm",
                 "--reference", "control",
                 "--equivalence-margin", "-3.0,0.5"]) == 0
    assert "[-3.000, 0.500]" in capsys.readouterr().out


def test_asymmetric_margin_requires_a_pinned_direction(long_csv, capsys):
    """An asymmetric margin is meaningless if row order decides the sign."""
    rc = main([long_csv, "--value", "score", "--group", "arm",
               "--equivalence-margin", "-3.0,0.5"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "비대칭" in err and "--reference" in err


def test_symmetric_margin_needs_no_reference(long_csv, capsys):
    assert main([long_csv, "--value", "score", "--group", "arm",
                 "--equivalence-margin", "3.0"]) == 0


def test_ni_margin_flag(long_csv, capsys):
    assert main([long_csv, "--value", "score", "--group", "arm",
                 "--reference", "control",
                 "--ni-margin", "3", "--ni-direction", "lower_is_better"]) == 0
    out = capsys.readouterr().out
    assert "[3b] 비열등성 검정" in out
    assert "낮을수록 좋음" in out


def test_both_margin_flags_rejected(long_csv, capsys):
    rc = main([long_csv, "--value", "score", "--group", "arm",
               "--equivalence-margin", "1", "--ni-margin", "1"])
    assert rc == 2
    assert "동시에" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["0", "abc", "2,1", "1,2,3"])
def test_bad_margin_is_an_input_error(long_csv, bad, capsys):
    assert main([long_csv, "--value", "score", "--group", "arm",
                 "--equivalence-margin", bad]) == 2
    assert "입력 오류" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_bad_ni_margin_is_an_input_error(long_csv, bad, capsys):
    assert main([long_csv, "--value", "score", "--group", "arm",
                 "--reference", "control", "--ni-direction", "higher_is_better",
                 "--ni-margin", bad]) == 2


def test_ni_margin_requires_a_pinned_direction(long_csv, capsys):
    """One-sided margins flip verdict with CSV row order; refuse without a reference."""
    rc = main([long_csv, "--value", "score", "--group", "arm",
               "--ni-margin", "3", "--ni-direction", "higher_is_better"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--reference" in err and "행 순서" in err


# --------------------------------------------------------------------------
# binary mode
# --------------------------------------------------------------------------

def test_binary_long(binary_csv, capsys):
    assert main([binary_csv, "--binary", "--value", "responder",
                 "--group", "arm"]) == 0
    out = capsys.readouterr().out
    assert "[1] 반응률" in out
    assert "Risk difference" in out
    assert "Odds ratio" in out


def test_binary_reference_flips_the_contrast(binary_csv, capsys):
    main([binary_csv, "--binary", "--value", "responder", "--group", "arm",
          "--reference", "placebo", "--format", "json"])
    d = json.loads(capsys.readouterr().out)
    rd = [e for e in d["estimates"] if e["name"].startswith("Risk diff")][0]
    assert rd["value"] > 0            # drug − placebo
    assert d["groups"][0]["label"] == "drug"


def test_binary_event_value_override(tmp_path, capsys):
    path = _write(tmp_path, "b.csv",
                  "arm,outcome\n" + "\n".join(
                      ["a,improved"] * 8 + ["a,stable"] * 12 +
                      ["b,improved"] * 3 + ["b,stable"] * 17) + "\n")
    assert main([path, "--binary", "--value", "outcome", "--group", "arm",
                 "--event-value", "improved"]) == 0
    out = capsys.readouterr().out
    assert "IMPROVED" in out


def test_binary_unmappable_values_ask_for_event_value(tmp_path, capsys):
    path = _write(tmp_path, "b.csv",
                  "arm,outcome\na,improved\na,stable\nb,improved\nb,stable\n")
    assert main([path, "--binary", "--value", "outcome", "--group", "arm"]) == 2
    assert "--event-value" in capsys.readouterr().err


def test_binary_counts_table(tmp_path, capsys):
    path = _write(tmp_path, "counts.csv",
                  "arm,responders,total\nplacebo,12,50\ndrug,27,52\n")
    assert main([path, "--binary", "--events-col", "responders",
                 "--n-col", "total", "--group", "arm"]) == 0
    out = capsys.readouterr().out
    assert "12" in out and "27" in out


def test_binary_counts_needs_all_three_columns(tmp_path, capsys):
    path = _write(tmp_path, "counts.csv",
                  "arm,responders,total\nplacebo,12,50\ndrug,27,52\n")
    assert main([path, "--binary", "--events-col", "responders"]) == 2
    assert "입력 오류" in capsys.readouterr().err


def test_binary_forced_fisher(binary_csv, capsys):
    assert main([binary_csv, "--binary", "--value", "responder",
                 "--group", "arm", "--binary-test", "fisher"]) == 0
    assert "Fisher's exact test" in capsys.readouterr().out


def test_binary_paired_needs_two_conditions(binary_csv, capsys):
    # the independent-arms fixture has an 'arm' column with two levels but no
    # repeated subject, so the McNemar path must fail on the pairing, not on
    # a blanket "not supported" refusal (which it used to give)
    rc = main([binary_csv, "--binary", "--paired", "--value", "responder",
               "--group", "arm", "--id", "subject"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "McNemar 검정이 필요하며 현재 지원하지 않습니다" not in err


def test_binary_rejects_equivalence_margin(binary_csv, capsys):
    assert main([binary_csv, "--binary", "--value", "responder",
                 "--group", "arm", "--equivalence-margin", "0.1"]) == 2
    assert "지원하지 않습니다" in capsys.readouterr().err


# --------------------------------------------------------------------------
# multi-endpoint mode
# --------------------------------------------------------------------------

def test_multi_endpoint_summary(multi_csv, capsys):
    assert main([multi_csv, "--values", "isi,psqi", "--group", "arm",
                 "--brief"]) == 0
    out = capsys.readouterr().out
    assert "다중 엔드포인트" in out
    assert "isi" in out and "psqi" in out
    assert "p(adj)" in out


def test_multi_endpoint_json(multi_csv, capsys):
    assert main([multi_csv, "--values", "isi,psqi", "--group", "arm",
                 "--format", "json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["schema"] == "statwise/multi/1"
    assert len(d["endpoints"]) == 2


def test_multi_endpoint_needs_group(multi_csv, capsys):
    assert main([multi_csv, "--values", "isi,psqi"]) == 2
    assert "--group" in capsys.readouterr().err


def test_multi_endpoint_needs_two_columns(multi_csv, capsys):
    assert main([multi_csv, "--values", "isi", "--group", "arm"]) == 2
    assert "2개 이상" in capsys.readouterr().err


def test_multi_endpoint_unknown_column(multi_csv, capsys):
    assert main([multi_csv, "--values", "isi,nope", "--group", "arm"]) == 2
    assert "nope" in capsys.readouterr().err


def test_multi_endpoint_rejects_paired(multi_csv, capsys):
    assert main([multi_csv, "--values", "isi,psqi", "--group", "arm",
                 "--paired", "--id", "subject"]) == 2


def test_endpoint_correction_none_warns(multi_csv, capsys):
    assert main([multi_csv, "--values", "isi,psqi", "--group", "arm",
                 "--endpoint-correction", "none", "--brief"]) == 0
    assert "보정" in capsys.readouterr().out


# --------------------------------------------------------------------------
# option validation and messy input
# --------------------------------------------------------------------------

@pytest.mark.parametrize("flag,value", [("--alpha", "0"), ("--alpha", "0.5"),
                                        ("--alpha", "-0.1"),
                                        ("--alpha-norm", "1.0")])
def test_alpha_out_of_range_rejected(long_csv, flag, value, capsys):
    assert main([long_csv, "--value", "score", "--group", "arm",
                 flag, value]) == 2
    assert "입력 오류" in capsys.readouterr().err


def test_missing_file(capsys):
    assert main(["/definitely/not/here.csv", "--wide"]) == 2
    assert "입력 오류" in capsys.readouterr().err


def test_long_flags_without_group_is_an_input_error(long_csv, capsys):
    assert main([long_csv, "--value", "score"]) == 2
    err = capsys.readouterr().err
    assert "입력 오류" in err and "--group" in err


def test_unknown_reference_group(long_csv, capsys):
    assert main([long_csv, "--value", "score", "--group", "arm",
                 "--reference", "nope"]) == 2
    assert "nope" in capsys.readouterr().err


def test_reference_fixes_the_sign(long_csv, capsys):
    main([long_csv, "--value", "score", "--group", "arm",
          "--reference", "control", "--format", "json"])
    d = json.loads(capsys.readouterr().out)
    assert d["groups"][0]["label"] == "drug"
    assert d["mean_diff"] > 0


def test_semicolon_delimiter_autodetected(tmp_path, capsys):
    path = _write(tmp_path, "semi.csv",
                  "score;arm\n" + "\n".join(
                      [f"{v};a" for v in (1, 2, 3, 4, 5)] +
                      [f"{v};b" for v in (6, 7, 8, 9, 10)]) + "\n")
    assert main([path, "--value", "score", "--group", "arm"]) == 0
    assert "[1] 기술통계" in capsys.readouterr().out


def test_forced_tab_delimiter(tmp_path, capsys):
    path = _write(tmp_path, "tabs.tsv",
                  "score\tarm\n" + "\n".join(
                      [f"{v}\ta" for v in (1, 2, 3, 4, 5)] +
                      [f"{v}\tb" for v in (6, 7, 8, 9, 10)]) + "\n")
    assert main([path, "--value", "score", "--group", "arm",
                 "--delimiter", "\\t"]) == 0
    assert "[1] 기술통계" in capsys.readouterr().out


def test_cp949_korean_file_is_decoded(tmp_path, capsys):
    text = "점수,군\n" + "\n".join(
        [f"{v},대조군" for v in (1, 2, 3, 4, 5)] +
        [f"{v},치료군" for v in (6, 7, 8, 9, 10)]) + "\n"
    path = tmp_path / "cp949.csv"
    path.write_bytes(text.encode("cp949"))
    assert main([str(path), "--value", "점수", "--group", "군"]) == 0
    out = capsys.readouterr().out
    assert "대조군" in out and "치료군" in out
    assert "인코딩" in out


def test_missing_cells_are_counted(tmp_path, capsys):
    path = _write(tmp_path, "miss.csv",
                  "score,arm\n5.1,a\nNA,a\n4.9,a\n5.3,a\n"
                  ",b\n5.0,b\n5.2,b\nxx,b\n4.8,b\n")
    assert main([path, "--value", "score", "--group", "arm"]) == 0
    out = capsys.readouterr().out
    assert "miss" in out


def test_too_few_groups_reports_a_hint(tmp_path, capsys):
    path = _write(tmp_path, "one.csv", "score,arm\n1,a\n2,a\n3,a\n")
    assert main([path, "--value", "score", "--group", "arm"]) == 2
    assert "2개 미만" in capsys.readouterr().err


def test_empty_file(tmp_path, capsys):
    assert main([_write(tmp_path, "empty.csv", ""), "--wide"]) == 2
    assert "입력 오류" in capsys.readouterr().err


def test_header_only_file(tmp_path, capsys):
    assert main([_write(tmp_path, "h.csv", "a,b\n"), "--wide"]) == 2


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "statwise" in capsys.readouterr().out


def test_non_utf8_binary_garbage_does_not_crash(tmp_path, capsys):
    path = tmp_path / "junk.csv"
    path.write_bytes(b"\xff\xfe\x00scor\x00e,ar\x00m\n\x001,a\n")
    rc = main([str(path), "--value", "score", "--group", "arm"])
    assert rc == 2                      # a clean input error, not a traceback


# --------------------------------------------------------------------------
# flags that a mode would otherwise silently ignore (round-1 hardening)
# --------------------------------------------------------------------------

def test_reference_under_paired_is_refused_not_ignored(paired_csv, capsys):
    """Silently ignoring --reference would hand back an unpinned sign."""
    rc = main([paired_csv, "--paired", "--value", "isi", "--group", "time",
               "--id", "subject", "--reference", "pre"])
    assert rc == 2
    assert "--baseline" in capsys.readouterr().err


def test_baseline_without_paired_is_refused(long_csv, capsys):
    rc = main([long_csv, "--value", "score", "--group", "arm",
               "--baseline", "control"])
    assert rc == 2
    assert "--reference" in capsys.readouterr().err


def test_value_and_values_together_refused(multi_csv, capsys):
    rc = main([multi_csv, "--value", "isi", "--values", "isi,psqi",
               "--group", "arm"])
    assert rc == 2
    assert "--values" in capsys.readouterr().err


@pytest.mark.parametrize("flag,value", [("--event-value", "yes"),
                                        ("--events-col", "k"),
                                        ("--n-col", "n")])
def test_binary_only_flags_refused_without_binary(long_csv, flag, value, capsys):
    rc = main([long_csv, "--value", "score", "--group", "arm", flag, value])
    assert rc == 2
    assert "--binary" in capsys.readouterr().err


def test_brief_without_values_refused(long_csv, capsys):
    assert main([long_csv, "--value", "score", "--group", "arm", "--brief"]) == 2


def test_effect_size_ci_tracks_alpha(long_csv, capsys):
    """Every interval on screen must carry the same coverage as --alpha."""
    main([long_csv, "--value", "score", "--group", "arm", "--alpha", "0.01"])
    out = capsys.readouterr().out
    assert "99% CI" in out
    assert "95% CI" not in out          # no stale hardcoded coverage anywhere


def test_effect_size_ci_widens_as_alpha_shrinks(long_csv, capsys):
    main([long_csv, "--value", "score", "--group", "arm", "--alpha", "0.01",
          "--format", "json"])
    strict = json.loads(capsys.readouterr().out)["effects"][0]
    main([long_csv, "--value", "score", "--group", "arm", "--alpha", "0.10",
          "--format", "json"])
    loose = json.loads(capsys.readouterr().out)["effects"][0]
    assert strict["conf"] == pytest.approx(0.99)
    assert loose["conf"] == pytest.approx(0.90)
    assert (strict["ci_high"] - strict["ci_low"]
            > loose["ci_high"] - loose["ci_low"])


# --------------------------------------------------------------------------
# CLI surface that had no coverage at all (round-1 hardening)
# --------------------------------------------------------------------------

def test_correction_flag_changes_the_posthoc_label(wide_csv, capsys):
    main([wide_csv, "--wide", "--correction", "bh"])
    assert "Benjamini-Hochberg FDR" in capsys.readouterr().out
    main([wide_csv, "--wide", "--correction", "holm"])
    assert "Holm-Bonferroni" in capsys.readouterr().out


def test_correction_flag_reaches_the_adjusted_p(wide_csv, capsys):
    main([wide_csv, "--wide", "--correction", "holm", "--format", "json"])
    holm = json.loads(capsys.readouterr().out)["pairwise"]
    main([wide_csv, "--wide", "--correction", "bh", "--format", "json"])
    bh = json.loads(capsys.readouterr().out)["pairwise"]
    assert all(b["pvalue_adj"] <= h["pvalue_adj"] + 1e-12
               for h, b in zip(holm, bh))
    assert any(b["pvalue_adj"] < h["pvalue_adj"] - 1e-12
               for h, b in zip(holm, bh))


def test_binary_wide_layout(tmp_path, capsys):
    path = _write(tmp_path, "bw.csv",
                  "sham,device\n" + "\n".join(
                      ["no,yes"] * 8 + ["no,no"] * 4 + ["yes,yes"] * 4) + "\n")
    assert main([path, "--binary", "--wide"]) == 0
    out = capsys.readouterr().out
    assert "[1] 반응률" in out
    assert "sham" in out and "device" in out


def test_multi_endpoint_binary(tmp_path, capsys):
    rows = ["subject,arm,resp,ae"]
    for i in range(30):
        arm = "drug" if i % 2 else "placebo"
        rows.append(f"S{i:02d},{arm},{'yes' if i % 3 else 'no'},"
                    f"{'yes' if i % 7 == 0 else 'no'}")
    path = _write(tmp_path, "mb.csv", "\n".join(rows) + "\n")
    assert main([path, "--binary", "--values", "resp,ae", "--group", "arm",
                 "--brief"]) == 0
    out = capsys.readouterr().out
    assert "resp" in out and "ae" in out


def test_multi_endpoint_csv_carries_endpoint_names(multi_csv, capsys):
    assert main([multi_csv, "--values", "isi,psqi", "--group", "arm",
                 "--format", "csv"]) == 0
    rows = list(csvmod.reader(capsys.readouterr().out.strip().splitlines()))
    assert rows[0][0] == "endpoint"
    assert {r[0] for r in rows[1:]} == {"isi", "psqi"}


def test_binary_csv_output(binary_csv, capsys):
    assert main([binary_csv, "--binary", "--value", "responder",
                 "--group", "arm", "--format", "csv"]) == 0
    rows = list(csvmod.reader(capsys.readouterr().out.strip().splitlines()))
    kinds = {r[1] for r in rows[1:]}
    assert "binary" in kinds and "binary-effect" in kinds
    # every group's events/n must survive into the machine-readable output
    assert "placebo=10/50" in rows[1][6] and "drug=22/52" in rows[1][6]


def test_csv_keeps_all_group_sizes_for_k_groups(wide_csv, capsys):
    main([wide_csv, "--wide", "--format", "csv"])
    rows = list(csvmod.reader(capsys.readouterr().out.strip().splitlines()))
    assert rows[1][6] == "low=8|mid=8|high=8"


def test_csv_marks_the_coverage_of_each_interval(long_csv, capsys):
    main([long_csv, "--value", "score", "--group", "arm",
          "--equivalence-margin", "3", "--format", "csv"])
    rows = list(csvmod.reader(capsys.readouterr().out.strip().splitlines()))
    header = rows[0]
    conf_i, kind_i, verdict_i = (header.index("ci_conf"), header.index("kind"),
                                 header.index("verdict"))
    omnibus = [r for r in rows[1:] if r[kind_i] == "continuous"][0]
    tost_row = [r for r in rows[1:] if r[kind_i] == "tost"][0]
    assert float(omnibus[conf_i]) == pytest.approx(0.95)
    assert float(tost_row[conf_i]) == pytest.approx(0.90)   # (1-2a), not (1-a)
    assert "equivalence" in tost_row[verdict_i]


def test_paired_wide_with_columns(tmp_path, capsys):
    path = _write(tmp_path, "pw.csv",
                  "pre,post\n18,12\n19,11\n20,14\n17,13\n21,10\n22,15\n")
    assert main([path, "--paired", "--wide", "--columns", "post,pre"]) == 0
    assert "차이 = (post − pre)" in capsys.readouterr().out


def test_paired_wide_baseline_without_columns_is_honoured(tmp_path, capsys):
    """--baseline used to be a silent no-op here, handing back the wrong sign."""
    path = _write(tmp_path, "pw2.csv",
                  "pre,post\n18,12\n19,11\n20,14\n17,13\n21,10\n22,15\n")
    assert main([path, "--paired", "--wide", "--baseline", "pre"]) == 0
    out = capsys.readouterr().out
    assert "차이 = (post − pre)" in out
    assert "= -7.000" in out          # ISI fell by 7; the sign must be negative


def test_paired_wide_rejects_unknown_baseline(tmp_path, capsys):
    path = _write(tmp_path, "pw3.csv", "pre,post\n18,12\n19,11\n20,14\n")
    assert main([path, "--paired", "--wide", "--baseline", "nope"]) == 2
    assert "nope" in capsys.readouterr().err


def test_multi_endpoint_detail_mode(multi_csv, capsys):
    assert main([multi_csv, "--values", "isi,psqi", "--group", "arm"]) == 0
    out = capsys.readouterr().out
    assert out.count("### 엔드포인트") == 2
    assert "[1] 기술통계" in out


def test_output_refuses_to_clobber_without_overwrite(long_csv, tmp_path, capsys):
    dest = tmp_path / "existing.txt"
    dest.write_text("precious data", encoding="utf-8")
    rc = main([long_csv, "--value", "score", "--group", "arm", "-o", str(dest)])
    assert rc == 2
    assert "--overwrite" in capsys.readouterr().err
    assert dest.read_text(encoding="utf-8") == "precious data"


def test_output_overwrite_flag_allows_it(long_csv, tmp_path):
    dest = tmp_path / "existing.txt"
    dest.write_text("old", encoding="utf-8")
    assert main([long_csv, "--value", "score", "--group", "arm",
                 "-o", str(dest), "--overwrite"]) == 0
    assert "기술통계" in dest.read_text(encoding="utf-8")


def test_output_file_is_not_world_readable(long_csv, tmp_path):
    import stat
    dest = tmp_path / "private.txt"
    main([long_csv, "--value", "score", "--group", "arm", "-o", str(dest)])
    mode = stat.S_IMODE(dest.stat().st_mode)
    assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0


def test_overwrite_without_output_refused(long_csv, capsys):
    assert main([long_csv, "--value", "score", "--group", "arm",
                 "--overwrite"]) == 2


# --------------------------------------------------------------------------
# privacy: error messages must not dump the column they were pointed at
# --------------------------------------------------------------------------

def test_binary_error_does_not_dump_every_identifier(tmp_path, capsys):
    rows = ["subject,arm,patient_id"]
    for i in range(40):
        rows.append(f"S{i:02d},{'a' if i % 2 else 'b'},KIM-{1000 + i}")
    path = _write(tmp_path, "ids.csv", "\n".join(rows) + "\n")
    assert main([path, "--binary", "--value", "patient_id",
                 "--group", "arm"]) == 2
    err = capsys.readouterr().err
    assert err.count("KIM-") <= 5           # a bounded sample, not the column
    assert "KIM-1039" not in err


def test_too_many_groups_error_does_not_dump_every_group(tmp_path, capsys):
    rows = ["score,pid"] + [f"{i},KIM-{2000 + i}" for i in range(40)]
    path = _write(tmp_path, "many.csv", "\n".join(rows) + "\n")
    assert main([path, "--value", "score", "--group", "pid"]) == 2
    err = capsys.readouterr().err
    assert err.count("KIM-") <= 5


def test_saved_report_does_not_carry_raw_data_in_failure_text(tmp_path):
    rows = ["subject,arm,good,bad"]
    for i in range(12):
        rows.append(f"S{i:02d},{'a' if i % 2 else 'b'},{i}," +
                    ("5" if i == 0 else ""))
    path = _write(tmp_path, "leak.csv", "\n".join(rows) + "\n")
    dest = tmp_path / "deliverable.txt"
    main([path, "--values", "good,bad", "--group", "arm", "--brief",
          "-o", str(dest)])
    text = dest.read_text(encoding="utf-8")
    assert "분석 불가" in text or "good" in text
    assert len(text) < 20000            # no unbounded dump of the input


# --------------------------------------------------------------------------
# --test pre-specification and --event-is
# --------------------------------------------------------------------------

@pytest.mark.parametrize("choice,expected", [
    ("student", "Student's t-test"),
    ("welch", "Welch's t-test"),
    ("mannwhitney", "Mann-Whitney U test"),
])
def test_prespecified_test_is_used_regardless_of_assumptions(long_csv, choice,
                                                             expected, capsys):
    assert main([long_csv, "--value", "score", "--group", "arm",
                 "--test", choice]) == 0
    out = capsys.readouterr().out
    assert expected in out
    assert "pre-specified" in out


def test_auto_selection_discloses_that_it_was_data_driven(long_csv, capsys):
    main([long_csv, "--value", "score", "--group", "arm"])
    assert "사전 지정이 아님" in capsys.readouterr().out


def test_test_flag_refused_for_binary_and_paired(long_csv, capsys):
    assert main([long_csv, "--value", "score", "--group", "arm",
                 "--binary", "--test", "welch"]) == 2


def test_event_is_names_nnh_on_a_harm_endpoint(tmp_path, capsys):
    rows = ["subject,arm,ae"]
    for i in range(20):
        rows.append(f"D{i:02d},device,{'yes' if i < 12 else 'no'}")
    for i in range(20):
        rows.append(f"S{i:02d},sham,{'yes' if i < 4 else 'no'}")
    path = _write(tmp_path, "ae.csv", "\n".join(rows) + "\n")
    assert main([path, "--binary", "--value", "ae", "--group", "arm",
                 "--reference", "sham", "--event-is", "harm"]) == 0
    out = capsys.readouterr().out
    assert "Number needed to harm (NNH)" in out
    assert "Number needed to treat" not in out


def test_event_is_defaults_to_a_neutral_label(binary_csv, capsys):
    main([binary_csv, "--binary", "--value", "responder", "--group", "arm"])
    out = capsys.readouterr().out
    assert "NNT/NNH" in out


def test_event_is_refused_without_binary(long_csv, capsys):
    assert main([long_csv, "--value", "score", "--group", "arm",
                 "--event-is", "harm"]) == 2
