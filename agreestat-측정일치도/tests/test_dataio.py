"""CSV loading tests (explicit columns, auto-detect, subject id, missing data)."""

import pytest

from agreestat import dataio


def test_parse_float():
    assert dataio.parse_float("3.5") == 3.5
    assert dataio.parse_float("  -2 ") == -2.0
    assert dataio.parse_float("") is None
    assert dataio.parse_float("NA") is None
    assert dataio.parse_float("n/a") is None
    assert dataio.parse_float("abc") is None


def test_load_pairs_explicit(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("sensor,band\n14.2,14.0\n15.1,14.8\n11.9,12.3\n",
                 encoding="utf-8")
    d = dataio.load_pairs(str(p), "sensor", "band")
    assert d.a == [14.2, 15.1, 11.9]
    assert d.b == [14.0, 14.8, 12.3]
    assert d.subjects is None
    assert d.name_a == "sensor" and d.name_b == "band"
    assert d.n == 3


def test_load_pairs_autodetect(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    d = dataio.load_pairs(str(p))
    assert d.name_a == "a" and d.name_b == "b"
    assert d.a == [1.0, 3.0]


def test_load_pairs_autodetect_skips_subject(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("subject,x,y\nS1,1,2\nS2,3,4\n", encoding="utf-8")
    d = dataio.load_pairs(str(p), subject_col="subject")
    assert d.name_a == "x" and d.name_b == "y"
    assert d.subjects == ["S1", "S2"]


def test_load_pairs_drops_missing_pairwise(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("x,y\n1,2\n,5\n3,\n4,6\nabc,7\n", encoding="utf-8")
    d = dataio.load_pairs(str(p), "x", "y")
    assert d.a == [1.0, 4.0]
    assert d.b == [2.0, 6.0]
    assert d.dropped == 3


def test_load_pairs_subject(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("id,x,y\nA,1,2\nA,3,4\nB,5,6\n", encoding="utf-8")
    d = dataio.load_pairs(str(p), "x", "y", "id")
    assert d.subjects == ["A", "A", "B"]


def test_load_pairs_missing_column(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("x,y\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        dataio.load_pairs(str(p), "x", "z")


def test_load_pairs_same_column_rejected(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("x,y\n1,2\n3,4\n", encoding="utf-8")
    with pytest.raises(ValueError):
        dataio.load_pairs(str(p), "x", "x")


def test_load_pairs_bom_and_blank_lines(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("﻿x,y\n\n1,2\n3,4\n", encoding="utf-8")
    d = dataio.load_pairs(str(p), "x", "y")
    assert d.a == [1.0, 3.0]


def test_empty_file(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        dataio.load_pairs(str(p))


def test_autodetect_failure(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("name,city\nfoo,bar\nbaz,qux\n", encoding="utf-8")
    with pytest.raises(ValueError):
        dataio.load_pairs(str(p))
