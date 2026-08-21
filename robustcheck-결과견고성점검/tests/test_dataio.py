"""CSV 읽기 — 인코딩, 결측 토큰, 중복 ID, 망가진 입력."""

import os

import pytest

from conftest import HEADER, make_rows, write_csv
from robustcheck.dataio import (
    InputError,
    MAX_BYTES,
    find_duplicate_ids,
    normalise_id,
    parse_number,
    read_table,
)
from robustcheck.spec import Spec, build_dataset


# ------------------------------------------------------------- parse_number


@pytest.mark.parametrize("text,expected", [
    ("12", 12.0),
    ("12.5", 12.5),
    ("-3.25", -3.25),
    ("+4", 4.0),
    (".5", 0.5),
    ("1e3", 1000.0),
    ("1,234.5", 1234.5),
    (" 7 ", 7.0),
    ("１２．５", 12.5),
    ("－3", -3.0),
])
def test_parse_number_accepts(text, expected):
    assert parse_number(text) == expected


@pytest.mark.parametrize("text", [
    "", " ", "NA", "n/a", "NaN", "None", "null", ".", "-", "--",
    "결측", "미측정", "없음", "#N/A", "abc", "12kg", "1/2", "∞",
])
def test_parse_number_treats_as_missing(text):
    assert parse_number(text) is None


def test_parse_number_rejects_infinity_and_nan_literals():
    assert parse_number("inf") is None
    assert parse_number("-inf") is None
    assert parse_number("nan") is None


def test_parse_number_of_none():
    assert parse_number(None) is None


# ------------------------------------------------------------- normalise_id


@pytest.mark.parametrize("raw,expected", [
    ("S01", "S01"),
    ("  S01  ", "S01"),
    ("Ｓ０１", "S01"),
    ("S 01", "S 01"),
    ("S\t01", "S 01"),
    ("", ""),
])
def test_normalise_id(raw, expected):
    assert normalise_id(raw) == expected


def test_normalise_id_never_fuzzy_matches():
    assert normalise_id("S01") != normalise_id("S02")
    assert normalise_id("S1") != normalise_id("S01")


def test_find_duplicate_ids():
    assert find_duplicate_ids(["a", "b", "a", "c", "a"]) == {"a": 3}
    assert find_duplicate_ids(["a", "b"]) == {}


# ---------------------------------------------------------------- read_table


def test_reads_utf8(tmp_path):
    path = write_csv(tmp_path / "a.csv", make_rows(8))
    table = read_table(path)
    assert table.columns == HEADER
    assert len(table) == 8
    assert table.encoding == "utf-8"


def test_reads_utf8_sig(tmp_path):
    path = write_csv(tmp_path / "a.csv", make_rows(6), encoding="utf-8-sig")
    table = read_table(path)
    assert table.columns[0] == "subject_id"
    assert table.encoding == "utf-8-sig"


def test_reads_cp949(tmp_path):
    header = ["subject_id", "군", "값"]
    rows = [["S1", "치료", 10], ["S2", "대조", 12]]
    path = write_csv(tmp_path / "k.csv", rows, header=header, encoding="cp949")
    table = read_table(path)
    assert table.encoding in ("cp949", "euc-kr")
    assert table.columns == header
    assert table.column("군") == ["치료", "대조"]


def test_reads_crlf(tmp_path):
    path = write_csv(tmp_path / "a.csv", make_rows(5), newline="\r\n")
    assert len(read_table(path)) == 5


def test_reads_tsv(tmp_path):
    path = tmp_path / "a.tsv"
    path.write_text("subject_id\tvalue\nS1\t3\nS2\t4\n", encoding="utf-8")
    table = read_table(str(path))
    assert table.delimiter == "\t"
    assert table.column("value") == ["3", "4"]


def test_reads_semicolon_delimited(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("subject_id;value\nS1;3\nS2;4\n", encoding="utf-8")
    assert read_table(str(path)).column("value") == ["3", "4"]


def test_blank_lines_are_dropped(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("subject_id,v\n\nS1,1\n\n\nS2,2\n", encoding="utf-8")
    assert len(read_table(str(path))) == 2


def test_ragged_rows_are_padded(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("subject_id,a,b\nS1,1\nS2,2,3,4\n", encoding="utf-8")
    table = read_table(str(path))
    assert table.column("b") == ["", "3"]


def test_quoted_newline_inside_field(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text('subject_id,note,v\nS1,"line1\nline2",5\n', encoding="utf-8")
    table = read_table(str(path))
    assert len(table) == 1
    assert table.column("v") == ["5"]


def test_null_bytes_are_stripped(tmp_path):
    path = tmp_path / "a.csv"
    path.write_bytes("subject_id,v\nS1,\x001\n".encode("utf-8"))
    assert read_table(str(path)).column("v") == ["1"]


def test_missing_file_raises(tmp_path):
    with pytest.raises(InputError):
        read_table(str(tmp_path / "nope.csv"))


def test_directory_input_raises(tmp_path):
    with pytest.raises(InputError):
        read_table(str(tmp_path))


def test_empty_file_raises(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(InputError):
        read_table(str(path))


def test_header_only_file_raises(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("subject_id,v\n", encoding="utf-8")
    with pytest.raises(InputError):
        read_table(str(path))


def test_duplicate_header_names_rejected(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("subject_id,v,v\nS1,1,2\n", encoding="utf-8")
    with pytest.raises(InputError) as exc:
        read_table(str(path))
    assert "같은 이름의 열" in str(exc.value)


def test_undecodable_bytes_raise(tmp_path):
    """어떤 인코딩으로도 못 읽는 바이트는 **반드시** InputError 여야 한다."""
    path = tmp_path / "a.csv"
    # utf-8 로도 cp949 로도 euc-kr 로도 디코딩되지 않는 바이트열
    path.write_bytes(b"subject_id,v\n\xed\xa0\x80\xfe\xff\x81\x41,1\n")
    with pytest.raises(InputError) as exc:
        read_table(str(path))
    assert "인코딩" in str(exc.value)


def test_ragged_rows_are_counted_not_hidden(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("subject_id,a,b\nS1,1\nS2,2,3,4\nS3,5,6\n", encoding="utf-8")
    assert read_table(str(path)).ragged == 2


def test_well_formed_file_reports_zero_ragged_rows(tmp_path):
    path = write_csv(tmp_path / "a.csv", make_rows(6))
    assert read_table(path).ragged == 0


def test_index_of_missing_column_message(tmp_path):
    path = write_csv(tmp_path / "a.csv", make_rows(4))
    table = read_table(path)
    with pytest.raises(InputError) as exc:
        table.index_of("없는열")
    assert "없는열" in str(exc.value)


def test_max_bytes_is_sane():
    assert MAX_BYTES >= 10 * 1024 * 1024


# ------------------------------------------------------------ build_dataset


def test_duplicate_subject_ids_rejected(tmp_path):
    rows = make_rows(8)
    rows[3][0] = rows[2][0]
    path = write_csv(tmp_path / "a.csv", rows)
    spec = Spec("two-group", value="isi_week4", group="arm")
    with pytest.raises(InputError) as exc:
        build_dataset(read_table(path), spec)
    assert "중복" in str(exc.value)


def test_duplicate_ids_hint_mentions_timepoint(tmp_path):
    header = ["subject_id", "timepoint", "arm", "isi_week4"]
    rows = [["S1", "w0", "active", 10], ["S1", "w4", "active", 8],
            ["S2", "w0", "sham", 11], ["S2", "w4", "sham", 10]]
    path = write_csv(tmp_path / "a.csv", rows, header=header)
    spec = Spec("two-group", value="isi_week4", group="arm")
    with pytest.raises(InputError) as exc:
        build_dataset(read_table(path), spec)
    assert "--timepoint" in str(exc.value)


def test_timepoint_selection_resolves_duplicates(tmp_path):
    header = ["subject_id", "timepoint", "arm", "isi_week4"]
    rows = []
    for i in range(1, 9):
        arm = "active" if i % 2 else "sham"
        rows.append(["S%d" % i, "w0", arm, 20])
        rows.append(["S%d" % i, "w4", arm, 10 + i])
    path = write_csv(tmp_path / "a.csv", rows, header=header)
    spec = Spec("two-group", value="isi_week4", group="arm",
                timepoint=("timepoint", "w4"))
    dataset = build_dataset(read_table(path), spec)
    assert len(dataset) == 8
    assert dataset.n_rows == 8


def test_timepoint_with_no_matching_rows(tmp_path):
    header = ["subject_id", "timepoint", "arm", "isi_week4"]
    path = write_csv(tmp_path / "a.csv",
                     [["S1", "w0", "active", 1]], header=header)
    spec = Spec("two-group", value="isi_week4", group="arm",
                timepoint=("timepoint", "w9"))
    with pytest.raises(InputError):
        build_dataset(read_table(path), spec)


def test_timepoint_column_missing(tmp_path):
    path = write_csv(tmp_path / "a.csv", make_rows(6))
    spec = Spec("two-group", value="isi_week4", group="arm",
                timepoint=("없는열", "w4"))
    with pytest.raises(InputError):
        build_dataset(read_table(path), spec)


def test_missing_id_column_raises(tmp_path):
    path = write_csv(tmp_path / "a.csv", make_rows(6))
    spec = Spec("two-group", value="isi_week4", group="arm", id_col="pid")
    with pytest.raises(InputError) as exc:
        build_dataset(read_table(path), spec)
    assert "--id" in str(exc.value)


def test_blank_ids_are_dropped_and_counted(tmp_path):
    rows = make_rows(8)
    rows[0][0] = ""
    path = write_csv(tmp_path / "a.csv", rows)
    spec = Spec("two-group", value="isi_week4", group="arm")
    dataset = build_dataset(read_table(path), spec)
    assert dataset.dropped_no_id == 1
    assert len(dataset) == 7


def test_three_groups_rejected(tmp_path):
    rows = make_rows(9)
    rows[0][1] = "third"
    path = write_csv(tmp_path / "a.csv", rows)
    spec = Spec("two-group", value="isi_week4", group="arm")
    with pytest.raises(InputError) as exc:
        build_dataset(read_table(path), spec)
    assert "statwise" in str(exc.value)


def test_single_group_rejected(tmp_path):
    rows = [["S%d" % i, "active", 19, 12, 30, 82] for i in range(1, 7)]
    path = write_csv(tmp_path / "a.csv", rows)
    spec = Spec("two-group", value="isi_week4", group="arm")
    with pytest.raises(InputError):
        build_dataset(read_table(path), spec)


def test_group_levels_sorted_deterministically(tmp_path):
    path = write_csv(tmp_path / "a.csv", make_rows(8))
    spec = Spec("two-group", value="isi_week4", group="arm")
    dataset = build_dataset(read_table(path), spec)
    assert dataset.group_levels == ("active", "sham")


def test_reading_does_not_modify_input(tmp_path):
    path = write_csv(tmp_path / "a.csv", make_rows(8))
    before = open(path, "rb").read()
    spec = Spec("two-group", value="isi_week4", group="arm")
    build_dataset(read_table(path), spec)
    assert open(path, "rb").read() == before
