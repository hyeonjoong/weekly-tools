"""CLI 통합 — 번들 예제 파일로 끝에서 끝까지 돌려본다."""

import csv
import json
import os

import pytest

from sleepdiary.cli import main

MINIMAL = (
    "subject,period,lights_off,sol,waso,final_awake,out_of_bed\n"
    "S1,base,23:00,20,30,07:00,07:10\n"
    "S1,base,23:30,25,20,07:10,07:20\n"
    "S2,base,22:40,40,50,06:30,06:45\n"
    "S2,base,23:10,35,45,06:50,07:00\n"
    "S1,post,23:10,10,10,07:00,07:10\n"
    "S1,post,23:20,12,8,07:05,07:15\n"
    "S2,post,22:50,15,20,06:40,06:50\n"
    "S2,post,23:00,18,25,06:45,06:55\n"
)


@pytest.fixture
def minimal_csv(tmp_path):
    path = tmp_path / "min.csv"
    path.write_text(MINIMAL, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------- 기본 동작

def test_runs_on_the_bundled_example_and_prints_a_report(trial_csv, capsys):
    assert main([trial_csv]) == 0
    out = capsys.readouterr().out
    assert "수면일기 지표 보고서" in out
    assert "집단 요약" in out
    assert "자료 품질" in out
    assert "논문용 문장 초안" in out


def test_bundled_example_reports_the_rows_it_excluded(trial_csv, capsys):
    main([trial_csv])
    out = capsys.readouterr().out
    assert "제외된 밤" in out
    assert "집계에 넣지 않았습니다" in out


def test_runs_on_the_korean_cp949_semicolon_example(korean_csv, capsys):
    assert main([korean_csv]) == 0
    out = capsys.readouterr().out
    assert "cp949" in out or "euc-kr" in out
    assert "환자01" in out


def test_list_columns_shows_the_mapping_and_exits(trial_csv, capsys):
    assert main([trial_csv, "--list-columns"]) == 0
    out = capsys.readouterr().out
    assert "자동인식 결과" in out
    assert "sleep_latency_min" in out


def test_missing_file_exits_nonzero_with_a_message(tmp_path, capsys):
    assert main([str(tmp_path / "nope.csv")]) == 2
    assert "오류" in capsys.readouterr().err


def test_no_arguments_prints_help(capsys):
    assert main([]) == 2
    assert "usage" in capsys.readouterr().out.lower()


# ---------------------------------------------------------------- 시기 비교

def test_compare_periods_produces_a_paired_test(minimal_csv, capsys):
    assert main([minimal_csv, "--compare-periods", "base", "post"]) == 0
    out = capsys.readouterr().out
    assert "시기 비교" in out
    assert "대응 t검정" in out
    assert "Wilcoxon" in out
    assert "다중비교 보정은 적용하지 않았습니다" in out


def test_unknown_period_name_is_an_error_that_lists_the_real_ones(minimal_csv, capsys):
    assert main([minimal_csv, "--compare-periods", "base", "오타"]) == 2
    err = capsys.readouterr().err
    assert "찾지 못했습니다" in err and "post" in err


def test_compare_without_a_period_column_is_refused(tmp_path, capsys):
    path = tmp_path / "np.csv"
    path.write_text("subject,lights_off,sol,waso,final_awake,out_of_bed\n"
                    "S1,23:00,20,30,07:00,07:10\n", encoding="utf-8")
    assert main([str(path), "--compare-periods", "a", "b"]) == 2
    assert "시기" in capsys.readouterr().err


def test_ignore_period_merges_the_groups(minimal_csv, capsys):
    main([minimal_csv, "--ignore-period"])
    out = capsys.readouterr().out
    assert "시기 'base'" not in out


# ---------------------------------------------------------------- 출력 파일

def test_json_output_is_valid_and_carries_the_honest_notes(trial_csv, tmp_path):
    out_path = str(tmp_path / "o.json")
    assert main([trial_csv, "--compare-periods", "baseline", "followup",
                 "--json", out_path, "--quiet"]) == 0
    with open(out_path, encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["tool"] == "sleepdiary"
    assert payload["counts"]["nights_valid"] + payload["counts"]["nights_excluded"] \
        == payload["counts"]["nights_total"]
    assert payload["comparisons"] and payload["comparisons"][0]["n_pairs"] > 0
    assert any("분석 단위는 대상자" in note for note in payload["notes"])
    assert any("PSG" in note for note in payload["notes"])


def test_per_night_csv_has_one_row_per_input_row(trial_csv, tmp_path):
    out_path = str(tmp_path / "n.csv")
    main([trial_csv, "--per-night-csv", out_path, "--quiet"])
    with open(out_path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    with open(trial_csv, encoding="utf-8") as fh:
        n_input = sum(1 for _ in fh) - 1
    assert len(rows) == n_input
    assert {"tst_min", "se_pct", "valid", "errors"} <= set(rows[0])
    assert any(r["valid"] == "False" for r in rows)     # 제외된 밤도 남긴다


def test_per_subject_csv_has_one_row_per_subject_period(minimal_csv, tmp_path):
    out_path = str(tmp_path / "s.csv")
    main([minimal_csv, "--per-subject-csv", out_path, "--quiet"])
    with open(out_path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 4                              # 2명 × 2시기
    assert {(r["subject"], r["period"]) for r in rows} == {
        ("S1", "base"), ("S1", "post"), ("S2", "base"), ("S2", "post")}


def test_written_csv_neutralises_formula_injection(tmp_path):
    path = tmp_path / "evil.csv"
    path.write_text("subject,lights_off,sol,waso,final_awake,out_of_bed\n"
                    '"=HYPERLINK(""http://x"")",23:00,20,30,07:00,07:10\n'
                    '"=HYPERLINK(""http://x"")",23:10,20,30,07:00,07:10\n',
                    encoding="utf-8")
    out_path = str(tmp_path / "n.csv")
    main([str(path), "--per-night-csv", out_path, "--quiet"])
    text = open(out_path, encoding="utf-8-sig").read()
    assert "'=HYPERLINK" in text
    assert "\n=HYPERLINK" not in text and ",=HYPERLINK" not in text


def test_markdown_output_is_a_table(trial_csv, capsys):
    assert main([trial_csv, "--markdown", "--compare-periods",
                 "baseline", "followup"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# 수면일기 지표 요약")
    assert "|---" in out
    assert "PSG와 다를 수 있습니다" in out


def test_quiet_suppresses_the_report_but_still_writes_files(trial_csv, tmp_path, capsys):
    out_path = str(tmp_path / "o.json")
    main([trial_csv, "--quiet", "--json", out_path])
    assert capsys.readouterr().out == ""
    assert os.path.exists(out_path)


# ---------------------------------------------------------------- 옵션

def test_min_nights_drops_thin_subjects_and_says_so(minimal_csv, capsys):
    main([minimal_csv, "--min-nights", "3"])
    out = capsys.readouterr().out
    assert "제외된 대상자" in out


def test_column_override_is_accepted(tmp_path, capsys):
    path = tmp_path / "odd.csv"
    path.write_text("who,off,latency_thing,awake_thing,up,outbed\n"
                    "S1,23:00,20,30,07:00,07:10\n"
                    "S1,23:30,20,30,07:00,07:10\n", encoding="utf-8")
    code = main([str(path), "--subject", "who", "--lights-off", "off",
                 "--sol", "latency_thing", "--waso", "awake_thing",
                 "--final-awake", "up", "--out-of-bed", "outbed"])
    assert code == 0
    assert "latency_thing" in capsys.readouterr().out


def test_bad_confidence_level_is_rejected(minimal_csv, capsys):
    assert main([minimal_csv, "--conf", "1.5"]) == 2
    assert "conf" in capsys.readouterr().err


def test_confidence_level_flows_into_the_report(minimal_csv, capsys):
    main([minimal_csv, "--conf", "0.9"])
    assert "90% CI" in capsys.readouterr().out


def test_evening_dated_diary_option_is_reflected_in_the_header(trial_csv, capsys):
    main([trial_csv, "--date-means", "evening"])
    assert "잠자리에 든 저녁" in capsys.readouterr().out
