import os
import pytest

from rsalink import RsalinkError
from rsalink.parse import parse_resp, parse_rr, parse_timestamps, parse_range, parse_mmss


def _w(tmp_path, name, text, enc="utf-8"):
    p = tmp_path / name
    p.write_bytes(text.encode(enc))
    return str(p)


def test_waveform_seconds_fs(tmp_path):
    lines = ["timestamp,value"] + [f"{i*0.1:.3f},{i%5}" for i in range(100)]
    p = _w(tmp_path, "r.csv", "\n".join(lines))
    w = parse_resp(p)
    assert w.source == "waveform"
    assert abs(w.fs_est - 10.0) < 1e-6
    assert w.irregular_frac == 0.0
    assert w.n_gaps == 0
    assert len(w.t) == 100


def test_waveform_irregular_and_gap(tmp_path):
    ts = [i * 0.1 for i in range(50)] + [5.0 + 2.0 + i * 0.1 for i in range(50)]  # 2 s 갭
    lines = ["timestamp,value"] + [f"{t:.3f},1" for t in ts]
    w = parse_resp(_w(tmp_path, "r.csv", "\n".join(lines)))
    assert w.n_gaps == 1
    assert abs(w.gap_total_s - 2.1) < 1e-6
    assert w.irregular_frac == pytest.approx(1 / 99)


def test_events_single_col_hms(tmp_path):
    lines = ["timestamp", "09:00:00.000", "09:00:05.000", "09:00:10.500", "09:00:15.000"]
    e = parse_resp(_w(tmp_path, "ev.csv", "\n".join(lines)))
    assert e.source == "events"
    assert e.t == [0.0, 5.0, 10.5, 15.0]


def test_iso_timestamps_and_tz_mixed():
    t, info = parse_timestamps(["2026-01-01T09:00:00", "2026-01-01T09:00:00.500", "2026-01-01 09:00:01"], "x")
    assert t == [0.0, 0.5, 1.0]
    assert info.kind == "iso"
    with pytest.raises(RsalinkError):
        parse_timestamps(["2026-01-01T09:00:00", "2026-01-01T09:00:01+09:00"], "x")


def test_epoch_and_backwards():
    t, info = parse_timestamps(["1700000000", "1700000000.5", "1700000001"], "x")
    assert t == [0.0, 0.5, 1.0] and info.kind == "epoch"
    with pytest.raises(RsalinkError, match="역행"):
        parse_timestamps(["0", "1", "0.5"], "x")


def test_rr_unit_auto_ms_s_bpm(tmp_path):
    r = parse_rr(_w(tmp_path, "a.csv", "rr\n1000\n900\n1100\n"))
    assert r.unit_detected == "ms" and r.rr_ms == [1000.0, 900.0, 1100.0]
    r = parse_rr(_w(tmp_path, "b.csv", "ibi\n1.0\n0.9\n1.1\n"))
    assert r.unit_detected == "s" and r.rr_ms == pytest.approx([1000.0, 900.0, 1100.0])
    r = parse_rr(_w(tmp_path, "c.csv", "hr\n60\n75\n50\n"))
    assert r.unit_detected == "bpm" and r.rr_ms == pytest.approx([1000.0, 800.0, 1200.0])


def test_rr_exclusion_and_beat_times(tmp_path):
    r = parse_rr(_w(tmp_path, "a.csv", "rr\n1000\n250\n1000\n2500\n1000\n"))
    assert r.n_total == 5 and r.n_excluded == 2
    assert r.excluded_frac == pytest.approx(0.4)
    assert r.rr_ms == [1000.0, 1000.0, 1000.0]
    # 제외된 박동도 시간은 흘러야 한다(중앙값 1000 ms 로 진행)
    assert r.t_beat == pytest.approx([1.0, 3.0, 5.0])


def test_rr_col_override_and_cp949(tmp_path):
    text = "시각,RR간격\n0,1000\n1,900\n2,1100\n"
    p = _w(tmp_path, "k.csv", text, enc="cp949")
    r = parse_rr(p, rr_col="RR간격")
    assert r.rr_ms == [1000.0, 900.0, 1100.0]
    r2 = parse_rr(p, rr_col="1")
    assert r2.rr_ms == [1000.0, 900.0, 1100.0]
    with pytest.raises(RsalinkError):
        parse_rr(p, rr_col="없는열")


def test_utf8_sig_and_headerless(tmp_path):
    p = _w(tmp_path, "s.csv", "1000\n900\n1100\n", enc="utf-8-sig")
    r = parse_rr(p)
    assert r.rr_ms == [1000.0, 900.0, 1100.0]


def test_missing_and_dir(tmp_path):
    with pytest.raises(RsalinkError):
        parse_rr(str(tmp_path / "nope.csv"))
    with pytest.raises(RsalinkError):
        parse_rr(str(tmp_path))


def test_ranges():
    assert parse_range("0:00-5:00") == (0.0, 300.0)
    assert parse_mmss("1:30:00") == 5400.0
    assert parse_mmss("90") == 90.0
    with pytest.raises(RsalinkError):
        parse_range("5:00-1:00")
