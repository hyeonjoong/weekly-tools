"""CSV reading: encodings, delimiters, missing markers, duplicates, ordering."""

from __future__ import annotations

import math

import pytest

from longistat.dataio import (DataError, Panel, load_long, load_wide,
                              order_times, parse_number, read_table)

LONG = """대상,방문,점수,군
S1,기저,10,A
S1,4주,8,A
S2,기저,12,A
S2,4주,9,A
S3,기저,14,B
S3,4주,15,B
"""


def _write(tmp_path, name, text, encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return str(path)


def test_load_long_roundtrip(tmp_path):
    p = load_long(_write(tmp_path, "a.csv", LONG), "대상", "방문", "점수", "군",
                  time_order=["기저", "4주"])
    assert p.subjects == ["S1", "S2", "S3"]
    assert p.times == ["기저", "4주"]
    assert p.groups == ["A", "A", "B"]
    assert p.values[0] == [10.0, 8.0]
    assert p.n_subjects == 3 and p.n_times == 2


def test_missing_markers_become_none(tmp_path):
    text = LONG.replace("S2,4주,9,A", "S2,4주,NA,A")
    p = load_long(_write(tmp_path, "b.csv", text), "대상", "방문", "점수", "군",
                  time_order=["기저", "4주"])
    assert p.values[1] == [12.0, None]
    assert p.complete_rows() == [0, 2]
    assert p.column(1) == [8.0, 15.0]


def test_parse_number_accepts_the_formats_excel_produces():
    assert parse_number(" 12.5 ") == 12.5
    assert parse_number("1,234") == 1234.0
    assert parse_number("1\u00a0234") == 1234.0     # NBSP thousands separator
    assert parse_number("1\u2009234") == 1234.0     # thin space
    assert parse_number("50%") == 50.0
    assert parse_number("") is None
    assert parse_number("N/A") is None
    assert parse_number("결측") is None
    assert parse_number("-3") == -3.0             # '-' alone is missing, '-3' is not
    assert parse_number("\ufeff10") == 10.0        # BOM from a concatenated export
    with pytest.raises(DataError):
        parse_number("열두")
    with pytest.raises(DataError):
        parse_number("inf")
    with pytest.raises(DataError):
        parse_number("nan_")


def test_parse_number_refuses_the_silently_1000x_wrong_readings():
    """Each of these used to parse, with a value 1000x or 10x off."""
    for token in ("1 2", "1 234", "1_000", "12 5"):
        with pytest.raises(DataError, match="공백이나 밑줄"):
            parse_number(token)


def test_comma_is_only_a_thousands_separator_in_a_comma_delimited_file():
    """In a semicolon-delimited European export "12,500" means 12.5."""
    assert parse_number("12,500", thousands_sep=True) == 12500.0
    with pytest.raises(DataError, match="쉼표"):
        parse_number("12,500", thousands_sep=False)


def test_european_semicolon_file_does_not_silently_multiply_by_1000(tmp_path):
    text = "id;visit;score\nS1;t1;13,125\nS1;t2;8,688\nS2;t1;14,250\nS2;t2;9,500\n"
    path = _write(tmp_path, "euro.csv", text)
    with pytest.raises(DataError, match="쉼표"):
        load_long(path, "id", "visit", "score")


def test_nan_or_inf_timepoint_labels_cannot_reorder_the_visits(tmp_path):
    """`str(numpy.nan)` is "nan"; float() used to accept it as a sort key."""
    text = ("id,visit,score\n"
            "S1,3,6\nS1,nan,13\nS1,1,21\nS1,2,16\n"
            "S2,3,7\nS2,nan,12\nS2,1,20\nS2,2,15\n")
    notes = []
    p = load_long(_write(tmp_path, "nan.csv", text), "id", "visit", "score",
                  notes=notes)
    # "nan" is not a number, so the labels are no longer all-numeric: the tool
    # keeps file order AND says so, instead of silently sorting on a NaN key.
    assert p.times == ["3", "nan", "1", "2"]
    assert any("--time-order" in n for n in notes)
    # With the bogus rows removed the ordering is numeric again.
    clean = text.replace("S1,nan,13\n", "").replace("S2,nan,12\n", "")
    notes2 = []
    q = load_long(_write(tmp_path, "clean.csv", clean), "id", "visit", "score",
                  notes=notes2)
    assert q.times == ["1", "2", "3"] and not notes2


def test_labels_are_unicode_normalised_so_one_arm_stays_one_arm(tmp_path):
    """macOS/EDC exports mix NFC and NFD Korean; both render identically."""
    nfc = "\uc2e4\ud5d8"                                    # 실험 (composed)
    nfd = "\u1109\u1175\u11af\u1112\u1165\u11b7"          # 실험 (decomposed)
    assert nfc != nfd
    text = "id,visit,score,arm\n" + "".join(
        f"S{i},{t},{10 + i},{nfc if i % 2 else nfd}\n"
        for i in range(4) for t in ("t1", "t2"))
    p = load_long(_write(tmp_path, "nfd.csv", text), "id", "visit", "score",
                  "arm", time_order=["t1", "t2"])
    assert p.group_labels() == [nfc]
    assert p.n_subjects == 4


def test_utf8_bom_and_cp949_are_both_read(tmp_path):
    notes = []
    p = load_long(_write(tmp_path, "bom.csv", "﻿" + LONG), "대상", "방문",
                  "점수", notes=notes, time_order=["기저", "4주"])
    assert p.times == ["기저", "4주"]
    notes = []
    p2 = load_long(_write(tmp_path, "cp949.csv", LONG, "cp949"), "대상", "방문",
                   "점수", notes=notes, time_order=["기저", "4주"])
    assert p2.subjects == ["S1", "S2", "S3"]
    assert any("cp949" in n for n in notes)


def test_delimiter_is_sniffed(tmp_path):
    notes = []
    p = load_long(_write(tmp_path, "semi.csv", LONG.replace(",", ";")), "대상",
                  "방문", "점수", notes=notes, time_order=["기저", "4주"])
    assert p.n_subjects == 3
    assert any("구분자" in n for n in notes)


def test_tab_delimiter_can_be_forced(tmp_path):
    p = load_long(_write(tmp_path, "t.tsv", LONG.replace(",", "\t")), "대상",
                  "방문", "점수", delimiter="\\t", time_order=["기저", "4주"])
    assert p.n_subjects == 3


def test_duplicate_measurements_are_an_error_by_default(tmp_path):
    text = LONG + "S1,기저,20,A\n"
    path = _write(tmp_path, "dup.csv", text)
    with pytest.raises(DataError, match="여러 번"):
        load_long(path, "대상", "방문", "점수", time_order=["기저", "4주"])
    mean_panel = load_long(path, "대상", "방문", "점수", duplicates="mean",
                           time_order=["기저", "4주"])
    assert mean_panel.values[0][0] == 15.0
    first_panel = load_long(path, "대상", "방문", "점수", duplicates="first",
                            time_order=["기저", "4주"])
    assert first_panel.values[0][0] == 10.0


def test_a_subject_may_not_belong_to_two_groups(tmp_path):
    text = LONG.replace("S1,4주,8,A", "S1,4주,8,B")
    with pytest.raises(DataError, match="엇갈"):
        load_long(_write(tmp_path, "g.csv", text), "대상", "방문", "점수", "군")


def test_unknown_column_names_list_what_is_available(tmp_path):
    with pytest.raises(DataError) as exc:
        load_long(_write(tmp_path, "c.csv", LONG), "대상", "visit", "점수")
    assert "사용 가능한 열" in str(exc.value) and "방문" in str(exc.value)


def test_same_column_twice_is_rejected(tmp_path):
    with pytest.raises(DataError):
        load_long(_write(tmp_path, "d.csv", LONG), "대상", "대상", "점수")


def test_ragged_row_with_real_content_is_an_error(tmp_path):
    text = LONG + "S4,기저,10,A,extra\n"
    with pytest.raises(DataError, match="열 개수"):
        load_long(_write(tmp_path, "r.csv", text), "대상", "방문", "점수")


def test_trailing_empty_cells_are_tolerated(tmp_path):
    text = LONG.replace("S1,기저,10,A", "S1,기저,10,A,")
    p = load_long(_write(tmp_path, "r2.csv", text), "대상", "방문", "점수",
                  time_order=["기저", "4주"])
    assert p.values[0][0] == 10.0


def test_duplicate_header_names_are_renamed(tmp_path):
    text = "id,t,v,v\nS1,a,1,2\nS1,b,3,4\n"
    notes = []
    p = load_long(_write(tmp_path, "h.csv", text), "id", "t", "v", notes=notes)
    assert any("중복된 열 이름" in n for n in notes)
    assert p.values[0] == [1.0, 3.0]


def test_empty_and_headers_only_files_fail_clearly(tmp_path):
    with pytest.raises(DataError, match="비어"):
        read_table(_write(tmp_path, "e.csv", "   \n"), None, [])
    with pytest.raises(DataError, match="데이터 행"):
        read_table(_write(tmp_path, "e2.csv", "a,b,c\n"), None, [])
    with pytest.raises(DataError, match="찾을 수 없"):
        read_table(str(tmp_path / "nope.csv"), None, [])
    with pytest.raises(DataError, match="폴더"):
        read_table(str(tmp_path), None, [])


def test_subject_with_no_measurements_at_all_is_dropped(tmp_path):
    text = LONG + "S9,기저,,B\nS9,4주,,B\n"
    notes = []
    p = load_long(_write(tmp_path, "z.csv", text), "대상", "방문", "점수", "군",
                  notes=notes, time_order=["기저", "4주"])
    assert "S9" not in p.subjects
    assert any("측정값이 하나도 없는" in n for n in notes)


def test_time_ordering_rules():
    notes = []
    assert order_times(["4", "1", "10"], None, notes) == ["1", "4", "10"]
    assert order_times(["v10", "v2", "v1"], None, notes) == ["v1", "v2", "v10"]
    notes = []
    assert order_times(["post", "pre"], None, notes) == ["post", "pre"]
    assert any("--time-order" in n for n in notes)
    assert order_times(["a", "b", "c"], ["c", "a"], notes) == ["c", "a"]
    with pytest.raises(DataError):
        order_times(["a", "b"], ["a", "z"], notes)
    with pytest.raises(DataError):
        order_times(["a", "b"], ["a", "a"], notes)


WIDE = """환자,기저,4주,8주,군
W1,10,8,6,A
W2,12,9,7,A
W3,14,15,16,B
"""


def test_load_wide_roundtrip(tmp_path):
    p = load_wide(_write(tmp_path, "w.csv", WIDE), ["기저", "4주", "8주"],
                  id_col="환자", group_col="군")
    assert p.times == ["기저", "4주", "8주"]
    assert p.subjects == ["W1", "W2", "W3"]
    assert p.values[2] == [14.0, 15.0, 16.0]
    assert p.groups == ["A", "A", "B"]


def test_wide_duplicate_ids_average_all_rows_equally(tmp_path):
    text = WIDE + "W1,20,20,20,A\nW1,30,30,30,A\n"
    p = load_wide(_write(tmp_path, "wd.csv", text), ["기저", "4주", "8주"],
                  id_col="환자", duplicates="mean")
    # (10+20+30)/3, (8+20+30)/3, (6+20+30)/3 — equal weight for all three rows
    assert p.values[0] == [20.0, pytest.approx(58 / 3), pytest.approx(56 / 3)]
    with pytest.raises(DataError, match="여러 행"):
        load_wide(_write(tmp_path, "wd2.csv", text), ["기저", "4주", "8주"],
                  id_col="환자")


def test_wide_rejects_overlapping_and_short_column_lists(tmp_path):
    path = _write(tmp_path, "w2.csv", WIDE)
    with pytest.raises(DataError):
        load_wide(path, ["기저"], id_col="환자")
    with pytest.raises(DataError):
        load_wide(path, ["기저", "기저"], id_col="환자")
    with pytest.raises(DataError, match="--id"):
        load_wide(path, ["환자", "기저"], id_col="환자")


def test_wide_without_id_column_numbers_the_rows(tmp_path):
    p = load_wide(_write(tmp_path, "w3.csv", WIDE), ["기저", "4주", "8주"])
    assert p.subjects == ["row1", "row2", "row3"]


def test_panel_helpers():
    p = Panel(subjects=["a", "b"], times=["t1", "t2"],
              values=[[1.0, 2.0], [3.0, None]], groups=["G", "G"])
    assert p.complete_rows() == [0]
    assert p.complete_case().n_subjects == 1
    assert p.subset_times([1]).times == ["t2"]
    assert p.group_labels() == ["G"]
    with pytest.raises(DataError):
        p.matrix()
