"""CSV loading tests (long + wide, missing data handling)."""

import pytest

from statwise import dataio


def test_parse_float():
    assert dataio.parse_float("3.5") == 3.5
    assert dataio.parse_float("  -2 ") == -2.0
    assert dataio.parse_float("") is None
    assert dataio.parse_float("NA") is None
    assert dataio.parse_float("n/a") is None
    assert dataio.parse_float("abc") is None


def test_parse_float_messy_cells():
    assert dataio.parse_float('"12.5"') == 12.5     # stray quotes
    assert dataio.parse_float("1,234.5") == 1234.5  # US thousands separator
    assert dataio.parse_float("1,234,567") == 1234567.0
    assert dataio.parse_float("1,000") == 1000.0
    assert dataio.parse_float("42%") == 42.0        # trailing percent
    assert dataio.parse_float("1e9") == 1e9
    assert dataio.parse_float(".5") == 0.5
    assert dataio.parse_float("-") is None          # dash NA
    assert dataio.parse_float("MISSING") is None
    assert dataio.parse_float("#N/A") is None


def test_parse_float_rejects_ambiguous_and_nonfinite():
    # European decimal comma must NOT be silently read as a US thousands sep
    # (would turn 1.5 into 15) — reject rather than corrupt.
    assert dataio.parse_float("1,5") is None
    assert dataio.parse_float("0,5") is None
    assert dataio.parse_float("12,34") is None
    assert dataio.parse_float("1.234,56") is None
    assert dataio.parse_float("1,2,3") is None
    # non-finite and other junk
    assert dataio.parse_float("inf") is None
    assert dataio.parse_float("-inf") is None
    assert dataio.parse_float("Infinity") is None
    assert dataio.parse_float("nan") is None
    assert dataio.parse_float("1_000") is None      # python underscore literal
    assert dataio.parse_float("１２３") is None        # full-width digits


def test_load_long(tmp_path):
    p = tmp_path / "long.csv"
    p.write_text("score,arm\n12.1,ctrl\n13.4,tx\n,tx\n11.0,ctrl\n9.9,\n",
                 encoding="utf-8")
    data = dataio.load_long(str(p), "score", "arm")
    d = dict(data)
    assert d["ctrl"] == [12.1, 11.0]
    assert d["tx"] == [13.4]  # blank value dropped; row with blank group dropped
    # group order preserved (first seen)
    assert [g for g, _ in data] == ["ctrl", "tx"]


def test_load_long_drops_na_group_labels(tmp_path):
    p = tmp_path / "long.csv"
    p.write_text("score,arm\n1.0,ctrl\n2.0,NA\n3.0,ctrl\n4.0,n/a\n",
                 encoding="utf-8")
    data = dataio.load_long(str(p), "score", "arm")
    assert dict(data) == {"ctrl": [1.0, 3.0]}  # NA-like group labels dropped


def test_load_long_missing_column(tmp_path):
    p = tmp_path / "long.csv"
    p.write_text("score,arm\n12.1,ctrl\n", encoding="utf-8")
    with pytest.raises(ValueError):
        dataio.load_long(str(p), "value", "arm")


def test_load_wide(tmp_path):
    p = tmp_path / "wide.csv"
    p.write_text("control,treatment\n12.1,13.4\n11.8,14.0\n,14.9\n",
                 encoding="utf-8")
    data = dataio.load_wide(str(p))
    d = dict(data)
    assert d["control"] == [12.1, 11.8]
    assert d["treatment"] == [13.4, 14.0, 14.9]


def test_load_wide_subset(tmp_path):
    p = tmp_path / "wide.csv"
    p.write_text("a,b,c\n1,2,3\n4,5,6\n", encoding="utf-8")
    data = dataio.load_wide(str(p), ["a", "c"])
    assert [g for g, _ in data] == ["a", "c"]


def test_load_wide_duplicate_columns_rejected(tmp_path):
    p = tmp_path / "dup.csv"
    p.write_text("g,g\n1,2\n3,4\n5,6\n", encoding="utf-8")
    with pytest.raises(ValueError):
        dataio.load_wide(str(p))


def test_bom_and_blank_lines(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_text("﻿score,arm\n\n1.0,x\n2.0,y\n", encoding="utf-8")
    data = dataio.load_long(str(p), "score", "arm")
    assert dict(data) == {"x": [1.0], "y": [2.0]}


def test_empty_file(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        dataio.load_wide(str(p))


def test_semicolon_delimiter_autodetected(tmp_path):
    p = tmp_path / "semi.csv"
    p.write_text("a;b\n1;2\n3;4\n5;6\n", encoding="utf-8")
    data = dict(dataio.load_wide(str(p)))
    assert data["a"] == [1.0, 3.0, 5.0]
    assert data["b"] == [2.0, 4.0, 6.0]


def test_tab_delimiter_forced(tmp_path):
    p = tmp_path / "tab.tsv"
    p.write_text("score\tarm\n1\tx\n2\ty\n", encoding="utf-8")
    data = dict(dataio.load_long(str(p), "score", "arm", delimiter="\t"))
    assert data == {"x": [1.0], "y": [2.0]}


def test_cp949_korean_encoding(tmp_path):
    p = tmp_path / "korean.csv"
    # Korean group labels written in cp949 (typical Excel-Korea export)
    p.write_bytes("점수,군\n1,대조\n2,실험\n3,대조\n".encode("cp949"))
    notes = []
    data = dict(dataio.load_long(str(p), "점수", "군", notes=notes))
    assert data == {"대조": [1.0, 3.0], "실험": [2.0]}
    assert any("인코딩" in n for n in notes)


def test_load_paired_long(tmp_path):
    p = tmp_path / "pl.csv"
    p.write_text(
        "sid,time,val\n"
        "s1,pre,10\ns1,post,8\n"
        "s2,pre,12\ns2,post,9\n"
        "s3,pre,14\ns3,post,11\n"
        "s4,pre,16\n",            # s4 has no post -> dropped
        encoding="utf-8")
    (la, va), (lb, vb) = dataio.load_paired_long(str(p), "val", "time", "sid")
    assert la == "pre" and lb == "post"
    assert va == [10.0, 12.0, 14.0]
    assert vb == [8.0, 9.0, 11.0]


def test_load_paired_long_baseline_pins_direction(tmp_path):
    p = tmp_path / "pl.csv"
    # rows deliberately ordered post-before-pre for the first subject
    p.write_text(
        "sid,time,val\n"
        "s1,post,8\ns1,pre,10\n"
        "s2,post,9\ns2,pre,12\n"
        "s3,post,11\ns3,pre,14\n",
        encoding="utf-8")
    # without baseline: first-seen level (post) is condition A
    (la, _), (lb, _) = dataio.load_paired_long(str(p), "val", "time", "sid")
    assert (la, lb) == ("post", "pre")
    # with baseline=pre: pre becomes the reference (condition B), so diff = post-pre
    (la2, _), (lb2, _) = dataio.load_paired_long(
        str(p), "val", "time", "sid", baseline="pre")
    assert lb2 == "pre" and la2 == "post"


def test_load_paired_long_bad_baseline(tmp_path):
    p = tmp_path / "pl.csv"
    p.write_text("sid,time,val\ns1,pre,10\ns1,post,8\n", encoding="utf-8")
    with pytest.raises(ValueError):
        dataio.load_paired_long(str(p), "val", "time", "sid", baseline="nope")


def test_load_paired_long_requires_two_levels(tmp_path):
    p = tmp_path / "pl3.csv"
    p.write_text("sid,time,val\ns1,a,1\ns1,b,2\ns1,c,3\n", encoding="utf-8")
    with pytest.raises(ValueError):
        dataio.load_paired_long(str(p), "val", "time", "sid")


def test_load_paired_wide_rowwise(tmp_path):
    p = tmp_path / "pw.csv"
    p.write_text("pre,post\n10,8\n12,9\n,7\n14,11\n", encoding="utf-8")
    (la, va), (lb, vb) = dataio.load_paired_wide(str(p), ["pre", "post"])
    # row with blank pre is dropped for both (row-wise matching)
    assert va == [10.0, 12.0, 14.0]
    assert vb == [8.0, 9.0, 11.0]


def test_load_paired_wide_needs_two_columns(tmp_path):
    p = tmp_path / "pw3.csv"
    p.write_text("a,b,c\n1,2,3\n4,5,6\n", encoding="utf-8")
    with pytest.raises(ValueError):
        dataio.load_paired_wide(str(p))
