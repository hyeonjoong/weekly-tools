"""Data loading, parsing and type-classification tests."""

import pytest

from table1.dataio import classify, is_missing, load_frame, parse_float


def test_parse_float_variants():
    assert parse_float("3.5") == 3.5
    assert parse_float("  -2 ") == -2.0
    assert parse_float("1,234") == 1234.0            # thousands separator
    assert parse_float("1,234,567.89") == 1234567.89  # multi-group thousands
    # Ambiguous European decimal comma is NOT silently mangled into 15 -> missing.
    assert parse_float("1,5") is None
    assert parse_float("1,23") is None
    assert parse_float("") is None
    assert parse_float("NA") is None
    assert parse_float("n/a") is None
    assert parse_float(".") is None
    assert parse_float("abc") is None
    assert parse_float("inf") is None       # non-finite treated as missing
    assert parse_float("-inf") is None
    assert parse_float("nan") is None


def test_is_missing():
    assert is_missing("")
    assert is_missing("  na ")
    assert is_missing("NULL")
    assert not is_missing("0")
    assert not is_missing("device")


def test_classify():
    assert classify(["1.2", "3.4", "5.6", "7.8"]) == "continuous"
    assert classify(["0", "1", "1", "0"]) == "categorical"          # binary
    assert classify(["M", "F", "F", "M"]) == "categorical"          # text
    assert classify(["", "NA", "."]) == "empty"
    # numeric but >2 distinct with default threshold -> continuous
    assert classify(["1", "2", "3"]) == "continuous"
    # raise threshold so 3 codes count as categorical
    assert classify(["1", "2", "3"], cat_max_levels=3) == "categorical"


def _write(tmp_path, text, name="d.csv", encoding="utf-8"):
    p = tmp_path / name
    p.write_text(text, encoding=encoding)
    return str(p)


def test_load_frame_basic(tmp_path):
    path = _write(tmp_path, "a,b\n1,x\n2,y\n")
    fr = load_frame(path)
    assert fr.header == ["a", "b"]
    assert fr.nrows == 2
    assert fr.column("b") == ["x", "y"]


def test_load_frame_bom_and_blank_lines(tmp_path):
    path = _write(tmp_path, "﻿a,b\n\n1,2\n\n3,4\n")
    fr = load_frame(path)
    assert fr.header == ["a", "b"]
    assert fr.nrows == 2


def test_load_frame_ragged_row(tmp_path):
    path = _write(tmp_path, "a,b,c\n1,2\n3,4,5\n")
    fr = load_frame(path)
    # short row -> missing cells default to '' via Frame.column
    assert fr.column("c") == ["", "5"]


def test_load_frame_semicolon_sniff(tmp_path):
    path = _write(tmp_path, "a;b;c\n1;2;3\n4;5;6\n")
    fr = load_frame(path)
    assert fr.header == ["a", "b", "c"]
    assert fr.column("b") == ["2", "5"]


def test_load_frame_tab_explicit(tmp_path):
    path = _write(tmp_path, "a\tb\n1\t2\n")
    fr = load_frame(path, delimiter="\t")
    assert fr.header == ["a", "b"]


def test_load_frame_duplicate_columns(tmp_path):
    path = _write(tmp_path, "a,a,b\n1,2,3\n")
    with pytest.raises(ValueError, match="중복"):
        load_frame(path)


def test_load_frame_empty(tmp_path):
    path = _write(tmp_path, "   \n")
    with pytest.raises(ValueError):
        load_frame(path)


def test_load_frame_header_only(tmp_path):
    path = _write(tmp_path, "a,b\n")
    with pytest.raises(ValueError):
        load_frame(path)


def test_load_frame_missing_file():
    with pytest.raises(FileNotFoundError):
        load_frame("/no/such/file_xyz.csv")


def test_classify_nonfinite_treated_as_missing():
    # inf/-inf/overflow are non-finite NUMBERS -> skipped as missing, not taken
    # as categorical evidence. A column of finite + non-finite stays continuous.
    assert classify(["1.2", "3.4", "inf", "5.6", "-inf", "7.8", "1e999"]) \
        == "continuous"
    # A genuinely non-numeric token still forces categorical.
    assert classify(["1.2", "3.4", "M", "5.6"]) == "categorical"
    # All non-finite -> effectively empty, not a spurious categorical.
    assert classify(["inf", "-inf", "nan"]) == "empty"


def test_load_frame_embedded_newline_preserved(tmp_path):
    # A quoted field with an embedded newline must survive as one cell (the old
    # splitlines()-first path silently concatenated it).
    path = _write(tmp_path, 'g,note\nA,"line1\nline2"\nB,plain\n')
    fr = load_frame(path)
    assert fr.nrows == 2
    assert fr.column("note")[0] == "line1\nline2"
    assert fr.column("note")[1] == "plain"


def test_load_frame_rejects_multichar_delimiter(tmp_path):
    path = _write(tmp_path, "a,b\n1,2\n")
    with pytest.raises(ValueError, match="구분자"):
        load_frame(path, delimiter=";;")
    with pytest.raises(ValueError, match="구분자"):
        load_frame(path, delimiter="")
