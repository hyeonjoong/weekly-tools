"""CSV 로딩 & 단위 감지 테스트."""

import pytest

from hrvkit import dataio


def test_parse_float():
    assert dataio.parse_float("812") == 812.0
    assert dataio.parse_float("  0.8 ") == 0.8
    assert dataio.parse_float("") is None
    assert dataio.parse_float("NA") is None
    assert dataio.parse_float("beat") is None


def test_detect_unit():
    assert dataio.detect_unit([0.8, 0.82, 0.79]) == "s"
    assert dataio.detect_unit([72, 75, 68]) == "bpm"
    assert dataio.detect_unit([800, 820, 790]) == "ms"


def test_to_rr_ms():
    assert dataio.to_rr_ms([0.8, 0.9], "s") == pytest.approx([800.0, 900.0])
    assert dataio.to_rr_ms([800, 900], "ms") == [800.0, 900.0]
    assert dataio.to_rr_ms([60, 120], "bpm") == pytest.approx([1000.0, 500.0])
    # bpm의 0/음수는 버림
    assert dataio.to_rr_ms([60, 0, -5], "bpm") == pytest.approx([1000.0])


def test_load_single_column_with_header(tmp_path):
    p = tmp_path / "rr.csv"
    p.write_text("rr_ms\n812\n798\n805\n", encoding="utf-8")
    rr, meta = dataio.load_series(str(p))
    assert rr == [812.0, 798.0, 805.0]
    assert meta["unit"] == "ms"
    assert meta["column"] == "rr_ms"


def test_load_single_column_no_header(tmp_path):
    p = tmp_path / "rr.csv"
    p.write_text("812\n798\n805\n", encoding="utf-8")
    rr, meta = dataio.load_series(str(p))
    assert rr == [812.0, 798.0, 805.0]


def test_load_time_value_picks_value(tmp_path):
    p = tmp_path / "tv.csv"
    p.write_text("time_s,rr_ms\n0.0,812\n0.812,798\n1.61,805\n", encoding="utf-8")
    rr, meta = dataio.load_series(str(p))
    assert rr == [812.0, 798.0, 805.0]
    assert meta["column"] == "rr_ms"


def test_load_bpm_conversion(tmp_path):
    p = tmp_path / "hr.csv"
    p.write_text("hr_bpm\n60\n120\n", encoding="utf-8")
    rr, meta = dataio.load_series(str(p))
    assert meta["unit"] == "bpm"
    assert rr == pytest.approx([1000.0, 500.0])


def test_load_seconds_conversion(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("ibi_s\n0.80\n0.82\n0.79\n", encoding="utf-8")
    rr, meta = dataio.load_series(str(p))
    assert meta["unit"] == "s"
    assert rr == pytest.approx([800.0, 820.0, 790.0])


def test_explicit_unit_override(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("v\n800\n820\n", encoding="utf-8")
    rr, meta = dataio.load_series(str(p), unit="ms")
    assert meta["unit_source"] == "user-specified"


def test_col_by_index_no_header(tmp_path):
    p = tmp_path / "multi.csv"
    p.write_text("0.0,812\n0.8,798\n", encoding="utf-8")
    rr, meta = dataio.load_series(str(p), col="1")
    assert rr == [812.0, 798.0]


def test_bom_and_blank_lines(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_text("﻿rr_ms\n\n812\n798\n", encoding="utf-8")
    rr, meta = dataio.load_series(str(p))
    assert rr == [812.0, 798.0]


def test_empty_file_raises(tmp_path):
    p = tmp_path / "e.csv"
    p.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        dataio.load_series(str(p))


def test_dropped_cells_counted(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("rr_ms\n812\nNA\n805\n", encoding="utf-8")
    rr, meta = dataio.load_series(str(p))
    assert rr == [812.0, 805.0]
    assert meta["n_dropped"] == 1
