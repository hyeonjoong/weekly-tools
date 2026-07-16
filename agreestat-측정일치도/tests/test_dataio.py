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


# --------------------------------------------------------------------------
# Non-finite handling (inf / 1e999) — must be dropped, not silently used
# --------------------------------------------------------------------------
def test_parse_float_rejects_nonfinite():
    assert dataio.parse_float("inf") is None
    assert dataio.parse_float("-inf") is None
    assert dataio.parse_float("Infinity") is None
    assert dataio.parse_float("1e999") is None  # overflows to inf
    assert dataio.parse_float("nan") is None


def test_load_pairs_drops_and_counts_nonfinite(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("a,b\n1,2\ninf,3\n4,-inf\n1e999,5\n6,7\n", encoding="utf-8")
    d = dataio.load_pairs(str(p), "a", "b")
    assert d.a == [1.0, 6.0]
    assert d.nonfinite == 3       # three rows had an inf-family value
    assert d.dropped == 3


# --------------------------------------------------------------------------
# Encoding auto-detection (Korean Excel exports are usually CP949)
# --------------------------------------------------------------------------
def test_load_pairs_cp949(tmp_path):
    p = tmp_path / "d.csv"
    p.write_bytes("센서,밴드\n14.2,14.0\n15.1,14.8\n".encode("cp949"))
    d = dataio.load_pairs(str(p))
    assert d.name_a == "센서" and d.name_b == "밴드"
    assert d.a == [14.2, 15.1]


def test_load_pairs_utf16(tmp_path):
    p = tmp_path / "d.csv"
    p.write_bytes("a,b\n1,2\n3,4\n".encode("utf-16"))
    d = dataio.load_pairs(str(p))
    assert d.a == [1.0, 3.0]


def test_load_pairs_explicit_encoding(tmp_path):
    p = tmp_path / "d.csv"
    p.write_bytes("a,b\n1,2\n3,4\n".encode("cp949"))
    d = dataio.load_pairs(str(p), encoding="cp949")
    assert d.a == [1.0, 3.0]


def test_load_pairs_bad_explicit_encoding_raises(tmp_path):
    p = tmp_path / "d.csv"
    p.write_bytes("센서,밴드\n1,2\n".encode("utf-8"))
    with pytest.raises(ValueError):
        dataio.load_pairs(str(p), encoding="ascii")


# --------------------------------------------------------------------------
# Auto-detect: skip sequential id columns; count NA labels correctly
# --------------------------------------------------------------------------
def test_autodetect_skips_sequential_id(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("patient_id,glucose_a,glucose_b\n"
                 "1001,95,97\n1002,110,108\n1003,88,90\n1004,100,101\n",
                 encoding="utf-8")
    d = dataio.load_pairs(str(p))
    assert d.name_a == "glucose_a" and d.name_b == "glucose_b"
    assert any("3개 이상" in n for n in d.notes)


def test_autodetect_integer_measurements_not_flagged_as_id(tmp_path):
    # non-monotonic distinct integers are a real measurement, not an id
    p = tmp_path / "d.csv"
    p.write_text("a,b\n95,97\n110,108\n88,90\n100,101\n", encoding="utf-8")
    d = dataio.load_pairs(str(p))
    assert d.name_a == "a" and d.name_b == "b"
    assert d.notes == []  # only 2 numeric cols, neither monotonic


def test_autodetect_sparse_na_column(tmp_path):
    # a >50%-missing numeric column should still auto-detect (NA not counted)
    p = tmp_path / "d.csv"
    p.write_text("a,b\n1.0,10\nNA,11\nn/a,12\nnull,13\n.,14\n5,15\n6,16\n",
                 encoding="utf-8")
    d = dataio.load_pairs(str(p))
    assert d.name_a == "a" and d.name_b == "b"


def test_malformed_csv_raises_valueerror(tmp_path):
    # unterminated quote producing a huge single field -> csv.Error -> ValueError
    p = tmp_path / "d.csv"
    p.write_text('a,b\n"' + "x" * (20 * 1024 * 1024) + "\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        dataio.load_pairs(str(p), "a", "b")


def test_parse_float_rejects_huge_finite():
    # 1e308 is finite but its square overflows the variance sum -> reject
    assert dataio.parse_float("1e308") is None
    assert dataio.parse_float("1e150") is not None   # boundary: still allowed
    assert dataio.parse_float("-1e200") is None


def test_load_pairs_drops_huge_finite(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("a,b\n1e308,1\n2,3\n4,5\n6,7\n", encoding="utf-8")
    d = dataio.load_pairs(str(p), "a", "b")
    assert d.a == [2.0, 4.0, 6.0]
    assert d.nonfinite == 1  # counted as abnormal, like inf


def test_autodetect_skips_descending_id(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("id,x,y\n1004,95,97\n1003,110,108\n1002,88,90\n1001,100,101\n",
                 encoding="utf-8")
    d = dataio.load_pairs(str(p))
    assert d.name_a == "x" and d.name_b == "y"
    assert any("식별자(ID)로 보고" in n for n in d.notes)


def test_latin1_fallback_emits_note(tmp_path):
    # bytes that fail utf-8/cp949/euc-kr but decode as latin-1
    p = tmp_path / "d.csv"
    p.write_bytes(b"a\xffb,c\xffd\n1,2\n3,4\n5,6\n")
    d = dataio.load_pairs(str(p), None, None)
    assert any("latin-1" in n for n in d.notes)


def test_bomless_utf16_emits_note(tmp_path):
    # UTF-16 LE without a BOM -> NUL-heavy content heuristic fires
    p = tmp_path / "d.csv"
    p.write_bytes("a,b\n1,2\n3,4\n5,6\n".encode("utf-16-le"))
    d = dataio.load_pairs(str(p))
    assert d.a == [1.0, 3.0, 5.0]
    assert any("BOM 없는 UTF-16" in n for n in d.notes)
