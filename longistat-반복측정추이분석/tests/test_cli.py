"""Command-line behaviour: exit codes, validation, output files, formats."""

from __future__ import annotations

import json
import os

import pytest

from longistat.cli import main

LONG = """대상,방문,ISI,군
S1,기저,20,A
S1,4주,14,A
S1,8주,11,A
S2,기저,18,A
S2,4주,13,A
S2,8주,10,A
S3,기저,22,A
S3,4주,15,A
S3,8주,12,A
S4,기저,19,B
S4,4주,18,B
S4,8주,18,B
S5,기저,21,B
S5,4주,20,B
S5,8주,21,B
S6,기저,17,B
S6,4주,17,B
S6,8주,16,B
"""


@pytest.fixture()
def csv_path(tmp_path):
    p = tmp_path / "isi.csv"
    p.write_text(LONG, encoding="utf-8")
    return str(p)


def test_happy_path_prints_a_report(csv_path, capsys):
    code = main([csv_path, "--id", "대상", "--time", "방문", "--value", "ISI",
                 "--group", "군", "--time-order", "기저,4주,8주"])
    out = capsys.readouterr().out
    assert code == 0
    assert "longistat — 반복측정 추이 분석 리포트" in out
    assert "그룹 × 시점" in out


def test_json_and_csv_formats(csv_path, capsys):
    assert main([csv_path, "--id", "대상", "--time", "방문", "--value", "ISI",
                 "--time-order", "기저,4주,8주", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["times"] == ["기저", "4주", "8주"]
    assert main([csv_path, "--id", "대상", "--time", "방문", "--value", "ISI",
                 "--time-order", "기저,4주,8주", "--format", "csv"]) == 0
    assert capsys.readouterr().out.startswith("section,")


def test_output_file_and_overwrite_guard(csv_path, tmp_path, capsys):
    dest = str(tmp_path / "out.txt")
    args = [csv_path, "--id", "대상", "--time", "방문", "--value", "ISI",
            "--time-order", "기저,4주,8주", "-o", dest]
    assert main(args) == 0
    assert os.path.exists(dest)
    capsys.readouterr()
    assert main(args) == 1
    assert "이미 있습니다" in capsys.readouterr().err
    assert main(args + ["--overwrite"]) == 0


def test_output_into_a_missing_folder_fails_clearly(csv_path, tmp_path, capsys):
    dest = str(tmp_path / "nope" / "out.txt")
    code = main([csv_path, "--id", "대상", "--time", "방문", "--value", "ISI",
                 "-o", dest])
    assert code == 1
    assert "저장할 폴더가 없습니다" in capsys.readouterr().err


def test_long_format_requires_its_columns(csv_path, capsys):
    code = main([csv_path, "--id", "대상"])
    assert code == 1
    err = capsys.readouterr().err
    assert "--time" in err and "--value" in err


def test_wide_flags_are_not_mixed_with_long_flags(csv_path, capsys):
    code = main([csv_path, "--wide", "--columns", "a,b", "--time", "방문"])
    assert code == 1
    assert "긴(long) 형식 전용" in capsys.readouterr().err
    code = main([csv_path, "--id", "대상", "--time", "방문", "--value", "ISI",
                 "--columns", "a,b"])
    assert code == 1
    assert "--wide 형식 전용" in capsys.readouterr().err


def test_wide_needs_columns(csv_path, capsys):
    assert main([csv_path, "--wide"]) == 1
    assert "--columns" in capsys.readouterr().err


def test_mcid_requires_direction(csv_path, capsys):
    code = main([csv_path, "--id", "대상", "--time", "방문", "--value", "ISI",
                 "--mcid", "5"])
    assert code == 1
    assert "--direction" in capsys.readouterr().err


def test_numeric_option_validation(csv_path, capsys):
    for extra, needle in (
            (["--alpha", "1.5"], "--alpha"),
            (["--alpha-norm", "0"], "--alpha-norm"),
            (["--mcid", "-1", "--direction", "lower"], "--mcid"),
            (["--reliability", "1.2", "--direction", "lower"], "--reliability"),
            (["--rci-cutoff", "0", "--direction", "lower"], "--rci-cutoff"),
            (["--mcid-percent"], "--mcid-percent"),
            (["--full", "--brief"], "--full")):
        code = main([csv_path, "--id", "대상", "--time", "방문", "--value",
                     "ISI"] + extra)
        assert code == 1, extra
        assert needle in capsys.readouterr().err


def test_missing_file_returns_one(tmp_path, capsys):
    code = main([str(tmp_path / "nope.csv"), "--id", "a", "--time", "b",
                 "--value", "c"])
    assert code == 1
    assert "찾을 수 없습니다" in capsys.readouterr().err


def test_bad_value_column_names_the_row(tmp_path, capsys):
    p = tmp_path / "bad.csv"
    p.write_text("id,t,v\nS1,a,1\nS1,b,오류\n", encoding="utf-8")
    code = main([str(p), "--id", "id", "--time", "t", "--value", "v"])
    assert code == 1
    err = capsys.readouterr().err
    assert "3행" in err and "숫자로 해석할 수 없는" in err


def test_wide_run_on_bundled_example(capsys):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, "examples", "와우핏_단어인지도_wide예시.csv")
    code = main([path, "--wide", "--id", "환자", "--columns", "기저,4주,8주,12주",
                 "--mcid", "10", "--direction", "higher", "--brief"])
    assert code == 0
    out = capsys.readouterr().out
    assert "반응자 분석" in out and "시점(시간)" in out


def test_help_and_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "longistat" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "반복측정" in capsys.readouterr().out


def test_duplicates_flag_is_wired_through(tmp_path, capsys):
    p = tmp_path / "dup.csv"
    p.write_text("id,t,v\nS1,a,1\nS1,a,3\nS1,b,2\nS2,a,2\nS2,b,4\n",
                 encoding="utf-8")
    args = [str(p), "--id", "id", "--time", "t", "--value", "v"]
    assert main(args) == 1
    assert "여러 번" in capsys.readouterr().err
    assert main(args + ["--duplicates", "mean"]) == 0
