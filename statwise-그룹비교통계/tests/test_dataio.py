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
