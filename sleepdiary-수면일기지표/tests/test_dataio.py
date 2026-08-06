"""CSV 읽기와 열 자동인식 — 지저분한 임상 파일에서 실제로 터지는 지점들."""

import pytest

from sleepdiary.dataio import (
    DataError,
    normalize,
    read_csv,
    resolve_columns,
    sanitize_cell,
    strip_unit,
)

HEADER = "subject,date,lights_off,sol,waso,final_awake,out_of_bed\n"
ROW = "S1,2026-03-02,23:00,20,30,07:00,07:10\n"


def write(tmp_path, text, name="d.csv", encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return str(path)


# ---------------------------------------------------------------- 읽기

def test_reads_plain_utf8(tmp_path):
    rows, fields, enc = read_csv(write(tmp_path, HEADER + ROW))
    assert len(rows) == 1
    assert fields[0] == "subject"
    assert enc in ("utf-8-sig", "utf-8")


def test_reads_utf8_with_bom_without_a_ghost_column(tmp_path):
    rows, fields, enc = read_csv(write(tmp_path, HEADER + ROW, encoding="utf-8-sig"))
    assert fields[0] == "subject"          # "﻿subject" 가 되면 안 된다
    assert enc == "utf-8-sig"


def test_reads_cp949_korean_headers(tmp_path):
    text = "대상자,소등시각,입면잠복기,중도각성시간,최종기상시각,침대에서나온시각\n" \
           "환자1,23:00,20,30,07:00,07:10\n"
    rows, fields, enc = read_csv(write(tmp_path, text, encoding="cp949"))
    assert "대상자" in fields
    assert enc in ("cp949", "euc-kr")


@pytest.mark.parametrize("delim", [",", ";", "\t", "|"])
def test_detects_the_delimiter(tmp_path, delim):
    text = (HEADER + ROW + ROW + ROW).replace(",", delim)
    rows, fields, _ = read_csv(write(tmp_path, text))
    assert len(fields) == 7 and len(rows) == 3


def test_blank_lines_are_skipped_not_counted_as_nights(tmp_path):
    text = HEADER + ROW + ",,,,,,\n" + ROW + "\n"
    rows, _, _ = read_csv(write(tmp_path, text))
    assert len(rows) == 2


def test_empty_file_and_header_only_file_raise_clear_errors(tmp_path):
    with pytest.raises(DataError, match="빈 파일"):
        read_csv(write(tmp_path, "   \n"))
    with pytest.raises(DataError, match="데이터 행"):
        read_csv(write(tmp_path, HEADER))


def test_missing_file_raises_dataerror_not_oserror(tmp_path):
    with pytest.raises(DataError):
        read_csv(str(tmp_path / "nope.csv"))


def test_whitespace_in_headers_is_trimmed(tmp_path):
    text = " subject , lights_off , final_awake , out_of_bed \nS1,23:00,07:00,07:10\n"
    rows, fields, _ = read_csv(write(tmp_path, text))
    assert "subject" in fields
    assert rows[0]["subject"] == "S1"


# ---------------------------------------------------------------- 열 매핑

def test_resolves_english_and_korean_aliases():
    cols = resolve_columns(["subject", "lights_off", "sol", "waso",
                            "final_awake", "out_of_bed"], {})
    assert cols["subject"] == "subject" and cols["sol"] == "sol"

    cols = resolve_columns(["대상자", "소등시각", "입면잠복기", "중도각성시간",
                            "최종기상시각", "침대에서나온시각"], {})
    assert cols["subject"] == "대상자"
    assert cols["waso"] == "중도각성시간"


def test_unit_suffixes_on_column_names_are_tolerated():
    """실제 파일은 'sleep_latency_min', 'waso_min' 처럼 단위가 붙어 있다."""
    cols = resolve_columns(["subject_id", "sleep_latency_min", "waso_min",
                            "lights_off", "final_awake", "out_of_bed"], {})
    assert cols["sol"] == "sleep_latency_min"
    assert cols["waso"] == "waso_min"


def test_two_candidates_for_one_field_is_an_error_not_a_silent_guess():
    with pytest.raises(DataError, match="여러 개"):
        resolve_columns(["waso", "WASO", "lights_off", "final_awake", "out_of_bed"], {})


def test_user_override_wins_over_autodetection():
    cols = resolve_columns(["waso", "awake_minutes", "lights_off",
                            "final_awake", "out_of_bed"],
                           {"waso": "awake_minutes"})
    assert cols["waso"] == "awake_minutes"


def test_override_naming_a_column_that_does_not_exist_is_rejected():
    with pytest.raises(DataError, match="없습니다"):
        resolve_columns(["lights_off", "final_awake", "out_of_bed"],
                        {"waso": "typo_column"})


def test_missing_required_columns_are_named_in_the_error():
    with pytest.raises(DataError) as exc:
        resolve_columns(["subject", "sol", "waso"], {})
    message = str(exc.value)
    assert "lights_off" in message and "final_awake" in message


def test_bedtime_only_diary_still_works():
    """소등시각 열이 없으면 취침시각으로 대체한다 (많은 일기가 한 시각만 적는다)."""
    cols = resolve_columns(["subject", "bedtime", "waketime"], {})
    assert cols["lights_off"] == "bedtime"
    assert cols["final_awake"] == cols["out_of_bed"] == "waketime"


def test_a_column_is_never_assigned_to_two_different_logical_fields():
    """열 하나가 두 역할을 겸하는 것은 문서화된 대체(취침↔소등)뿐이어야 한다."""
    cols = resolve_columns(["subject", "date", "period", "bedtime", "lights_off",
                            "sol", "waso", "awakenings", "final_awake", "out_of_bed"], {})
    assigned = [v for v in cols.values() if v]
    assert len(assigned) == len(set(assigned)), cols
    assert cols["bedtime"] != cols["lights_off"]
    assert cols["final_awake"] != cols["out_of_bed"]


# ---------------------------------------------------------------- 유틸

def test_normalize_and_strip_unit():
    assert normalize(" Lights-Off (시각) ") == "lightsoff시각"
    assert strip_unit("sleeplatencymin") == "sleeplatency"
    assert strip_unit("min") == "min"        # 다 떼면 원래 값을 유지


@pytest.mark.parametrize("value,expected", [
    ("=SUM(A1:A9)", "'=SUM(A1:A9)"),
    ("+cmd", "'+cmd"),
    ("@x", "'@x"),
    ("-15", "-15"),          # 진짜 음수는 그대로
    ("-1.5e3", "-1.5e3"),
    ("정상값", "정상값"),
    (None, ""),
    (7, "7"),
])
def test_sanitize_cell_blocks_formula_injection_but_keeps_numbers(value, expected):
    assert sanitize_cell(value) == expected
