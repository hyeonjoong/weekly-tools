"""지저분한 실측 CSV 강건성 — 인코딩·구분자·소수점 쉼표·박동 발생시각."""

import pytest

from hrvkit import dataio
from hrvkit.dataio import load_series, parse_float, _sniff_delimiter


def test_parse_float_decimal_comma():
    assert parse_float("0,82", decimal_comma=True) == pytest.approx(0.82)
    assert parse_float("812,5", decimal_comma=True) == pytest.approx(812.5)
    assert parse_float("1.234,5", decimal_comma=True) == pytest.approx(1234.5)
    # decimal_comma=False 이면 쉼표는 파싱 실패
    assert parse_float("0,82") is None


def test_parse_float_extra_na_labels():
    for tok in ("-", "--", "?", "NONE", "null"):
        assert parse_float(tok) is None


def test_sniff_semicolon():
    lines = ["a;b;c", "1;2;3", "4;5;6"]
    assert _sniff_delimiter(lines) == ";"


def test_sniff_tab():
    lines = ["a\tb", "1\t2", "3\t4"]
    assert _sniff_delimiter(lines) == "\t"


def test_sniff_defaults_comma_for_single_column():
    assert _sniff_delimiter(["812", "798", "805"]) == ","


def test_european_csv_semicolon_and_decimal_comma(tmp_path):
    p = tmp_path / "eu.csv"
    p.write_text("time_s;rr_ms\n0,0;812,5\n0,81;798,2\n1,61;805,0\n",
                 encoding="utf-8")
    rr, meta = load_series(str(p))
    assert rr == pytest.approx([812.5, 798.2, 805.0])
    assert meta["delimiter"] == ";"
    assert meta["decimal_comma"] is True
    assert meta["column"] == "rr_ms"


def test_tab_delimited(tmp_path):
    p = tmp_path / "t.tsv"
    p.write_text("time\trr\n0\t812\n1\t798\n", encoding="utf-8")
    rr, meta = load_series(str(p))
    assert rr == [812.0, 798.0]
    assert meta["delimiter"] == "\t"


def test_cp949_korean_header(tmp_path):
    p = tmp_path / "k.csv"
    p.write_bytes("심박간격\n812\n798\n805\n".encode("cp949"))
    rr, meta = load_series(str(p))
    assert rr == [812.0, 798.0, 805.0]
    assert meta["column"] == "심박간격"


def test_latin1_fallback(tmp_path):
    p = tmp_path / "l.csv"
    # latin-1 전용 바이트(0xE9 = é)를 헤더에 포함
    p.write_bytes(b"r\xe9\n812\n798\n")
    rr, meta = load_series(str(p))
    assert rr == [812.0, 798.0]


def test_beat_timestamps_seconds(tmp_path):
    p = tmp_path / "ts.csv"
    p.write_text("t\n0.0\n0.8\n1.62\n2.4\n3.25\n", encoding="utf-8")
    rr, meta = load_series(str(p), beat_times=True)
    assert rr == pytest.approx([800.0, 820.0, 780.0, 850.0])
    assert meta["beat_times"] is True
    assert meta["unit"] == "s"


def test_beat_timestamps_unsorted_handled(tmp_path):
    p = tmp_path / "ts.csv"
    p.write_text("t\n0.0\n1.62\n0.8\n2.4\n", encoding="utf-8")
    rr, meta = load_series(str(p), beat_times=True)
    # 정렬 후 차분: 0.8, 0.82, 0.78 → ms
    assert all(x > 0 for x in rr)
    assert len(rr) == 3


def test_timestamps_autodetect_flag(tmp_path):
    p = tmp_path / "ts.csv"
    p.write_text("t\n0.0\n0.8\n1.62\n2.4\n3.25\n5.0\n", encoding="utf-8")
    _, meta = load_series(str(p))   # beat_times 미지정
    assert meta["looks_like_timestamps"] is True


def test_normal_rr_not_flagged_as_timestamps(tmp_path):
    p = tmp_path / "rr.csv"
    p.write_text("rr_ms\n812\n798\n805\n790\n810\n", encoding="utf-8")
    _, meta = load_series(str(p))
    assert meta["looks_like_timestamps"] is False


def test_backward_compat_meta_keys(tmp_path):
    p = tmp_path / "rr.csv"
    p.write_text("rr_ms\n812\n798\n805\n", encoding="utf-8")
    _, meta = load_series(str(p))
    for k in ("unit", "unit_source", "column", "n_raw", "n_dropped"):
        assert k in meta
