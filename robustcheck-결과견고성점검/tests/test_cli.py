"""CLI — 종료코드 규칙과 산출물."""

import os

import pytest

from conftest import EXAMPLES, make_rows, write_csv
from robustcheck.cli import main
from robustcheck.report import OUT_ISSUES, OUT_REPORT, OUT_SCENARIOS, OUT_SUBJECTS


def run(args):
    return main(args)


def robust(*extra):
    return [os.path.join(EXAMPLES, "견고_예제.csv"), "--design", "two-group",
            "--group", "arm", "--value", "isi_week4"] + list(extra)


def fragile(*extra):
    return [os.path.join(EXAMPLES, "취약_예제.csv"), "--design", "two-group",
            "--group", "arm", "--value", "isi_week4"] + list(extra)


# ---------------------------------------------- exit 2 — 주 분석 미지정/오류


def test_no_arguments_is_exit_2(capsys):
    assert run([]) == 2
    assert "--design" in capsys.readouterr().err


def test_missing_design_is_exit_2(capsys):
    assert run([os.path.join(EXAMPLES, "견고_예제.csv")]) == 2
    err = capsys.readouterr().err
    assert "statwise" in err


def test_two_group_without_group_is_exit_2(capsys):
    assert run([os.path.join(EXAMPLES, "견고_예제.csv"), "--design", "two-group",
                "--value", "isi_week4", "--no-files"]) == 2
    assert "--group" in capsys.readouterr().err


def test_two_group_without_value_is_exit_2():
    assert run([os.path.join(EXAMPLES, "견고_예제.csv"), "--design", "two-group",
                "--group", "arm", "--no-files"]) == 2


def test_paired_without_pre_is_exit_2():
    assert run([os.path.join(EXAMPLES, "견고_예제.csv"), "--design", "paired",
                "--post", "isi_week4", "--no-files"]) == 2


def test_corr_without_y_is_exit_2():
    assert run([os.path.join(EXAMPLES, "견고_예제.csv"), "--design", "corr",
                "--x", "rmssd_ms", "--no-files"]) == 2


def test_missing_file_is_exit_2():
    assert run(["없는파일.csv", "--design", "two-group", "--group", "arm",
                "--value", "v", "--no-files"]) == 2


def test_missing_column_is_exit_2(capsys):
    assert run(robust("--no-files")[:1] + ["--design", "two-group", "--group",
                                           "arm", "--value", "없는열",
                                           "--no-files"]) == 2
    assert "없는열" in capsys.readouterr().err


def test_bad_alpha_is_exit_2():
    assert run(robust("--alpha", "1.5", "--no-files")) == 2
    assert run(robust("--alpha", "0", "--no-files")) == 2


def test_covariate_with_paired_is_exit_2(capsys):
    assert run([os.path.join(EXAMPLES, "견고_예제.csv"), "--design", "paired",
                "--pre", "isi_baseline", "--post", "isi_week4",
                "--covariate-baseline", "rmssd_ms", "--no-files"]) == 2
    assert "근사하지 않고" in capsys.readouterr().err


def test_covariate_with_corr_is_exit_2():
    assert run([os.path.join(EXAMPLES, "견고_예제.csv"), "--design", "corr",
                "--x", "rmssd_ms", "--y", "isi_week4",
                "--covariate-baseline", "isi_baseline", "--no-files"]) == 2


def test_bad_timepoint_syntax_is_exit_2(capsys):
    assert run(robust("--timepoint", "timepoint", "--no-files")) == 2
    assert "열=값" in capsys.readouterr().err


def test_negative_loo_budget_is_exit_2():
    assert run(robust("--loo-budget", "-1", "--no-files")) == 2


def test_three_groups_is_exit_2(tmp_path, capsys):
    rows = make_rows(9)
    rows[0][1] = "third"
    path = write_csv(tmp_path / "a.csv", rows)
    assert run([path, "--design", "two-group", "--group", "arm",
                "--value", "isi_week4", "--no-files"]) == 2
    assert "statwise" in capsys.readouterr().err


def test_duplicate_ids_is_exit_2(tmp_path, capsys):
    rows = make_rows(8)
    rows[1][0] = rows[0][0]
    path = write_csv(tmp_path / "a.csv", rows)
    assert run([path, "--design", "two-group", "--group", "arm",
                "--value", "isi_week4", "--no-files"]) == 2
    assert "1행 = 1피험자" in capsys.readouterr().err


# ------------------------------------------------- exit 3 — 판정불가 우선


def test_undecidable_is_exit_3():
    assert run([os.path.join(EXAMPLES, "판정불가_예제.csv"), "--design",
                "two-group", "--group", "arm", "--value", "isi_week4",
                "--no-files"]) == 3


def test_undecidable_beats_flip_exit_code(tmp_path):
    """유효 N 이 모자라면 뒤집힘 건수는 의미가 없다 — 3 이 1보다 우선한다."""
    rows = [["S1", "active", 19, 10, 30, 82],
            ["S2", "active", 20, 11, 31, 83],
            ["S3", "active", 21, 12, 32, 84],
            ["S4", "sham", 19, 18, 33, 79],
            ["S5", "sham", 20, 19, 34, 80]]
    path = write_csv(tmp_path / "a.csv", rows)
    assert run([path, "--design", "two-group", "--group", "arm",
                "--value", "isi_week4", "--no-files"]) == 3


def test_undecidable_report_is_still_printed(capsys):
    run([os.path.join(EXAMPLES, "판정불가_예제.csv"), "--design", "two-group",
         "--group", "arm", "--value", "isi_week4", "--no-files"])
    out = capsys.readouterr().out
    assert "[커버리지 자백]" in out
    assert "판정불가" in out


# -------------------------------------------------- exit 0 / 1 — 본 판정


def test_robust_example_is_exit_0():
    assert run(robust("--no-files")) == 0


def test_fragile_example_is_exit_1():
    assert run(fragile("--no-files")) == 1


def test_paired_design_runs(capsys):
    assert run([os.path.join(EXAMPLES, "견고_예제.csv"), "--design", "paired",
                "--pre", "isi_baseline", "--post", "isi_week4",
                "--no-files"]) == 0
    out = capsys.readouterr().out
    assert "대응 t" in out
    assert "36개 중" in out


def test_corr_design_runs(capsys):
    assert run([os.path.join(EXAMPLES, "견고_예제.csv"), "--design", "corr",
                "--x", "rmssd_ms", "--y", "isi_week4", "--no-files"]) == 0
    assert "Pearson r" in capsys.readouterr().out


def test_covariate_design_runs(capsys):
    assert run(robust("--covariate-baseline", "isi_baseline", "--no-files")) == 0
    out = capsys.readouterr().out
    assert "ANCOVA(기저보정) t" in out
    assert "기저보정=isi_baseline" in out


def test_equal_var_switches_the_parametric_test(capsys):
    run(robust("--equal-var", "--no-files"))
    assert "Student t" in capsys.readouterr().out


def test_quiet_prints_only_the_verdict(capsys):
    run(robust("--quiet", "--no-files"))
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert out[0].startswith("판정:")


def test_alpha_changes_the_verdict(capsys):
    """alpha 를 .01 로 낮추면 기준선(p=.027)이 비유의가 되어 판정이 달라진다."""
    assert run(fragile("--no-files", "--quiet")) == 1
    loose = capsys.readouterr().out
    assert run(fragile("--alpha", "0.01", "--no-files", "--quiet")) == 0
    strict = capsys.readouterr().out
    assert loose != strict
    assert "취약" in loose and "취약" not in strict


# ---------------------------------------------------------------- 산출물


def test_writes_all_four_files(tmp_path):
    out = tmp_path / "결과"
    assert run(robust("--out-dir", str(out))) == 0
    assert sorted(os.listdir(out)) == sorted(
        [OUT_REPORT, OUT_SCENARIOS, OUT_SUBJECTS, OUT_ISSUES])


def test_outputs_are_utf8_sig(tmp_path):
    out = tmp_path / "결과"
    run(robust("--out-dir", str(out)))
    with open(os.path.join(out, OUT_SCENARIOS), "rb") as fh:
        assert fh.read(3) == b"\xef\xbb\xbf"


def test_input_file_is_not_modified(tmp_path):
    source = write_csv(tmp_path / "data.csv", make_rows(20))
    before = open(source, "rb").read()
    run([source, "--design", "two-group", "--group", "arm", "--value",
         "isi_week4", "--out-dir", str(tmp_path / "out")])
    assert open(source, "rb").read() == before


def test_no_files_writes_nothing(tmp_path):
    out = tmp_path / "결과"
    run(robust("--out-dir", str(out), "--no-files"))
    assert not out.exists()


def test_out_dir_pointing_at_a_file_is_exit_2(tmp_path, capsys):
    blocker = tmp_path / "blocked"
    blocker.write_text("x", encoding="utf-8")
    assert run(robust("--out-dir", str(blocker))) == 2
    assert "산출물" in capsys.readouterr().err


def test_report_file_contains_sentence_drafts(tmp_path):
    out = tmp_path / "결과"
    run(robust("--out-dir", str(out)))
    with open(os.path.join(out, OUT_REPORT), encoding="utf-8-sig") as fh:
        text = fh.read()
    assert "Sensitivity analysis" in text
    assert "민감도 분석" in text


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        run(["--version"])
    assert exc.value.code == 0
    assert "robustcheck" in capsys.readouterr().out


def test_help_flag(capsys):
    with pytest.raises(SystemExit):
        run(["--help"])
    assert "--design" in capsys.readouterr().out


# ------------------------------------------------ joinaudit merged.csv 연동


def test_joinaudit_merged_csv_runs_without_extra_arguments(merged_csv, capsys):
    """joinaudit 산출물(피험자당 1행, UTF-8 BOM)을 스키마 변경 없이 받는다."""
    assert run([merged_csv, "--design", "two-group", "--group", "isi_arm",
                "--value", "isi_isi_total", "--no-files"]) == 0
    out = capsys.readouterr().out
    assert "인코딩 utf-8-sig" in out
    assert "24행 / 24명" in out
    assert "판정: 견고" in out


def test_joinaudit_long_format_is_rejected_with_a_hint(tmp_path, capsys):
    header = ["subject_id", "timepoint", "isi_arm", "isi_isi_total"]
    rows = []
    for i in range(1, 9):
        arm = "치료" if i % 2 else "대조"
        rows.append(["S%02d" % i, "week0", arm, 20])
        rows.append(["S%02d" % i, "week4", arm, 10 + i])
    path = write_csv(tmp_path / "merged.csv", rows, header=header,
                     encoding="utf-8-sig")
    assert run([path, "--design", "two-group", "--group", "isi_arm",
                "--value", "isi_isi_total", "--no-files"]) == 2
    err = capsys.readouterr().err
    assert "--timepoint" in err
    assert "week0" in err or "week4" in err


def test_timepoint_makes_long_format_work(tmp_path, capsys):
    def capsys_text():
        return capsys.readouterr().out

    header = ["subject_id", "timepoint", "isi_arm", "isi_isi_total"]
    rows = []
    for i in range(1, 15):
        arm = "치료" if i % 2 else "대조"
        rows.append(["S%02d" % i, "week0", arm, 20])
        rows.append(["S%02d" % i, "week4", arm, 10 + (i % 5)])
    path = write_csv(tmp_path / "merged.csv", rows, header=header,
                     encoding="utf-8-sig")
    code = run([path, "--design", "two-group", "--group", "isi_arm",
                "--value", "isi_isi_total", "--timepoint", "timepoint=week4",
                "--no-files"])
    assert code in (0, 1)
    out = capsys_text()
    assert "--timepoint timepoint=week4" in out


def test_cp949_input_runs(tmp_path, capsys):
    header = ["subject_id", "군", "값"]
    rows = [["S%d" % i, "치료" if i % 2 else "대조", 10 + (i % 4)]
            for i in range(1, 15)]
    path = write_csv(tmp_path / "k.csv", rows, header=header, encoding="cp949")
    code = run([path, "--design", "two-group", "--group", "군", "--value", "값",
                "--no-files"])
    assert code in (0, 1)
    assert "인코딩 cp949" in capsys.readouterr().out
