"""파일 읽기 — 인코딩·구분자·헤더·숫자 표기.

여기서 조용히 틀리면 그 뒤의 모든 것이 틀린다. cp949 를 잘못 읽으면 피험자
ID 가 깨져 "매칭 안 됨"이 되고, `12,5` 를 125로 읽으면 통계가 통째로 바뀐다.
"""

from __future__ import annotations

import datetime as _dt
import os

import pytest

from conftest import write_bytes, write_rows, write_text, write_xlsx
from joinaudit.dataio import (LoadError, is_missing, load_table,
                              normalize_numeric_columns, parse_number)


# --------------------------------------------------------------------------
# 인코딩
# --------------------------------------------------------------------------

def test_utf8_bom_is_stripped_from_first_header(tmp_path):
    path = write_rows(str(tmp_path / "a.csv"),
                      [["subject_id", "v"], ["S01", "1"]], encoding="utf-8-sig")
    frame = load_table(path)
    assert frame.header == ["subject_id", "v"]      # BOM 이 열 이름에 남지 않는다
    assert frame.encoding == "utf-8-sig"


def test_cp949_korean_headers_and_ids(tmp_path):
    path = write_rows(str(tmp_path / "k.csv"),
                      [["피험자번호", "총수면시간"], ["에스공일", "412"]],
                      encoding="cp949")
    frame = load_table(path)
    assert frame.encoding == "cp949"
    assert frame.header == ["피험자번호", "총수면시간"]
    assert frame.rows[0][0] == "에스공일"


def test_utf16_with_bom(tmp_path):
    path = write_rows(str(tmp_path / "u.csv"),
                      [["subject_id", "v"], ["S01", "3"]], encoding="utf-16")
    frame = load_table(path)
    assert frame.encoding.startswith("utf-16")
    assert frame.rows == [["S01", "3"]]


def test_utf16le_without_bom(tmp_path):
    raw = "subject_id,v\nS01,3\n".encode("utf-16-le")
    path = write_bytes(str(tmp_path / "u2.csv"), raw)
    frame = load_table(path)
    assert frame.header == ["subject_id", "v"]


# --------------------------------------------------------------------------
# 구분자 / 헤더
# --------------------------------------------------------------------------

def test_tsv_by_extension(tmp_path):
    path = write_rows(str(tmp_path / "a.tsv"),
                      [["subject_id", "v"], ["S01", "1"]], delimiter="\t")
    frame = load_table(path)
    assert frame.delimiter == "\t"
    assert frame.header == ["subject_id", "v"]


def test_semicolon_delimiter_is_detected(tmp_path):
    rows = [["subject_id", "v", "w"]] + [[f"S{i:02d}", i, i * 2] for i in range(1, 9)]
    path = write_rows(str(tmp_path / "a.csv"), rows, delimiter=";")
    frame = load_table(path)
    assert frame.delimiter == ";"
    assert len(frame.header) == 3


def test_leading_notice_rows_are_skipped(tmp_path):
    rows = [["2026년 3월 측정 결과", "", ""],
            ["", "", ""],
            ["subject_id", "date", "v"],
            ["S01", "2026-03-01", "1"],
            ["S02", "2026-03-01", "2"]]
    path = write_rows(str(tmp_path / "a.csv"), rows)
    frame = load_table(path)
    assert frame.header == ["subject_id", "date", "v"]
    assert frame.header_row_index == 2
    # 사람이 파일에서 보는 행 번호로 되돌릴 수 있어야 한다.
    assert frame.source_line(0) == 4


def test_duplicate_column_names_are_disambiguated_not_dropped(tmp_path):
    path = write_rows(str(tmp_path / "a.csv"),
                      [["id", "비고", "비고"], ["S01", "a", "b"]])
    frame = load_table(path)
    assert frame.header == ["id", "비고", "비고.1"]
    assert frame.rows[0] == ["S01", "a", "b"]
    assert any(n.kind == "renamed" for n in frame.notes)


def test_row_longer_than_header_keeps_the_extra_value(tmp_path):
    path = write_text(str(tmp_path / "a.csv"), "id,v\nS01,1,999\n")
    frame = load_table(path)
    assert "999" in frame.rows[0]          # 조용히 잘라 버리지 않는다


def test_explicit_header_row_out_of_range_is_an_error(tmp_path):
    path = write_rows(str(tmp_path / "a.csv"), [["id"], ["S01"]])
    with pytest.raises(LoadError):
        load_table(path, header_row=99)


def test_missing_file_and_directory_are_errors(tmp_path):
    with pytest.raises(LoadError):
        load_table(str(tmp_path / "nope.csv"))
    with pytest.raises(LoadError):
        load_table(str(tmp_path))


def test_empty_file_is_an_error(tmp_path):
    path = write_text(str(tmp_path / "a.csv"), "   \n")
    with pytest.raises(LoadError):
        load_table(path)


# --------------------------------------------------------------------------
# 숫자 표기
# --------------------------------------------------------------------------

def test_parse_number_treats_missing_tokens_as_none():
    for token in ("", " ", "NA", "n/a", ".", "-", "#N/A", "없음"):
        assert parse_number(token) is None
        assert is_missing(token)


def test_parse_number_thousands_separator():
    assert parse_number("1,234") == 1234.0
    assert parse_number("1,234.5") == 1234.5


def test_parse_number_does_not_guess_ambiguous_comma():
    # `1,5` 를 15로 바꾸는 것이 이 함수가 저지를 수 있는 최악의 실수다.
    assert parse_number("1,5") is None
    assert parse_number("1,5", decimal_comma=True) == 1.5


def test_parse_number_rejects_non_finite():
    assert parse_number("inf") is None
    assert parse_number("nan") is None


def test_european_decimal_column_is_normalised(tmp_path):
    rows = [["id", "rmssd"]] + [[f"S{i:02d}", f"3{i},{i}"] for i in range(1, 7)]
    path = write_rows(str(tmp_path / "a.csv"), rows, delimiter=";")
    frame = load_table(path)
    applied = normalize_numeric_columns(frame, exclude=["id"])
    assert [n.kind for n in applied] == ["decimal_comma"]
    assert frame.column("rmssd")[0] == "31.1"


def test_thousands_column_is_normalised(tmp_path):
    rows = [["id", "steps"]] + [[f"S{i:02d}", f"{i},{i:03d}"] for i in range(1, 7)]
    path = write_rows(str(tmp_path / "a.csv"), rows)
    frame = load_table(path)
    normalize_numeric_columns(frame, exclude=["id"])
    assert frame.column("steps")[0] == "1001"


def test_id_column_is_never_touched_by_number_normalisation(tmp_path):
    rows = [["id", "v"]] + [[f"1,{i:03d}", i] for i in range(1, 6)]
    path = write_rows(str(tmp_path / "a.csv"), rows)
    frame = load_table(path)
    normalize_numeric_columns(frame, exclude=["id"])
    assert frame.column("id")[0] == "1,001"


def test_unclassifiable_comma_column_is_left_alone(tmp_path):
    # `1,234.5`(천단위+소수점) 와 `12,5`(유럽식) 는 한 열에 공존할 수 없다.
    # 어느 쪽으로도 확정할 수 없으면 **건드리지 않는 것**이 정답이다.
    rows = [["id", "v"], ["S01", "1,234.5"], ["S02", "12,5"], ["S03", "7"]]
    path = write_rows(str(tmp_path / "a.csv"), rows)
    frame = load_table(path)
    applied = normalize_numeric_columns(frame, exclude=["id"])
    assert applied == []
    assert frame.column("v") == ["1,234.5", "12,5", "7"]


def test_three_digit_group_with_a_two_digit_one_reads_as_european(tmp_path):
    """`1,234` 하나만 보면 모호하지만, 같은 열에 `12,5` 가 있으면 답이 정해진다.

    천단위 구분자는 **언제나 세 자리**로 끊긴다. `12,5` 가 천단위일 수 없으니
    이 열의 쉼표는 소수점이고, 따라서 `1,234` 는 1.234 다. 이 추론은 열 단위로만
    성립하며(셀 하나로는 알 수 없다), 판정 결과는 리포트에 문장으로 남는다.
    """
    rows = [["id", "v"], ["S01", "1,234"], ["S02", "12,5"], ["S03", "7"]]
    path = write_rows(str(tmp_path / "a.csv"), rows)
    frame = load_table(path)
    applied = normalize_numeric_columns(frame, exclude=["id"])
    assert [n.kind for n in applied] == ["decimal_comma"]
    assert frame.column("v") == ["1.234", "12.5", "7"]


# --------------------------------------------------------------------------
# XLSX 경유
# --------------------------------------------------------------------------

def test_xlsx_round_trip_with_date_serial(tmp_path):
    path = str(tmp_path / "a.xlsx")
    write_xlsx(path, [["id", "날짜", "v"],
                      ["S01", _dt.date(2026, 3, 10), 4.5],
                      ["S02", _dt.date(2026, 3, 11), 5.5]])
    frame = load_table(path)
    assert frame.encoding == "xlsx"
    assert frame.header == ["id", "날짜", "v"]
    # 날짜 서식이 걸린 숫자는 ISO 문자열로 되돌아와야 한다.
    assert frame.column("날짜") == ["2026-03-10", "2026-03-11"]


def test_xlsx_sparse_row_keeps_column_alignment(tmp_path):
    path = str(tmp_path / "a.xlsx")
    write_xlsx(path, [["id", "a", "b"],
                      ["S01", None, "두번째열이비었다"],
                      ["S02", "x", "y"]])
    frame = load_table(path)
    # 빈 셀 하나 때문에 값이 왼쪽으로 밀리면 다른 사람의 값이 붙는다.
    assert frame.rows[0] == ["S01", "", "두번째열이비었다"]


def test_legacy_xls_gets_an_actionable_message(tmp_path):
    path = write_bytes(str(tmp_path / "old.xls"),
                       b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    with pytest.raises(LoadError) as exc:
        load_table(path)
    assert ".xlsx" in str(exc.value)
