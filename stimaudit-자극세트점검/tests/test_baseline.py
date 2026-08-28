"""버전 대조 — 짝짓기는 추측하지 않습니다."""
from __future__ import annotations

import os

import pytest

from stimaudit import analyze, baseline, wavread
from tests.conftest import fade, sine_rms

FS48 = 48000


def _m(tmp_path, name, level, seconds=1.0, freq=400.0, bits=24, fs=FS48):
    p = os.path.join(str(tmp_path), name)
    wavread.write_wav(p, [fade(sine_rms(freq, seconds, level, fs), fs, ms=100.0)], fs, bits)
    return analyze.analyze_file(wavread.probe(p))


def test_matches_by_name(tmp_path):
    cur = {"a.wav": _m(tmp_path, "cur_a.wav", -20.0)}
    old = {"a.wav": _m(tmp_path, "old_a.wav", -23.0)}
    rows, unmatched, leftover = baseline.compare(cur, old)
    assert unmatched == [] and leftover == []
    assert rows[0].lufs_delta == pytest.approx(3.0, abs=0.1)
    assert "음량 +3.0 LU" in rows[0].summary()


def test_pairs_override_name_matching(tmp_path):
    cur = {"새이름.wav": _m(tmp_path, "c.wav", -20.0)}
    old = {"옛이름.wav": _m(tmp_path, "o.wav", -23.0)}
    rows, unmatched, leftover = baseline.compare(cur, old, {"새이름.wav": "옛이름.wav"})
    assert len(rows) == 1 and unmatched == [] and leftover == []
    assert rows[0].baseline_name == "옛이름.wav"


def test_unmatched_is_reported_not_guessed(tmp_path):
    """이름 유사도로 추측하면 엉뚱한 두 파일을 '달라졌다'고 보고합니다."""
    cur = {"싱잉볼_bi.wav": _m(tmp_path, "c.wav", -20.0)}
    old = {"bi.wav": _m(tmp_path, "o.wav", -23.0)}
    rows, unmatched, leftover = baseline.compare(cur, old)
    assert rows == []
    assert unmatched == ["싱잉볼_bi.wav"]
    assert leftover == ["bi.wav"]


def test_no_change_summary(tmp_path):
    """앞뒤에 **같은 객체**를 넘기면 `summary()` 가 상수여도 통과합니다.

    같은 파형을 별도로 두 번 써서 서로 다른 FileMetrics 로 비교합니다.
    """
    a = _m(tmp_path, "before.wav", -23.0)
    b = _m(tmp_path, "after.wav", -23.0)
    assert a is not b
    rows, _, _ = baseline.compare({"x.wav": a}, {"x.wav": b})
    assert rows[0].summary() == "변화 없음"
    # 그리고 실제로 달라지면 "변화 없음"이 아니어야 합니다.
    c = _m(tmp_path, "louder.wav", -20.0)
    rows2, _, _ = baseline.compare({"x.wav": c}, {"x.wav": b})
    assert rows2[0].summary() != "변화 없음"


def test_duration_change_reported(tmp_path):
    cur = {"a.wav": _m(tmp_path, "c.wav", -23.0, seconds=2.0)}
    old = {"a.wav": _m(tmp_path, "o.wav", -23.0, seconds=1.0)}
    rows, _, _ = baseline.compare(cur, old)
    assert "길이 1.0 → 2.0초" in rows[0].summary()


def test_format_change_reported(tmp_path):
    cur = {"a.wav": _m(tmp_path, "c.wav", -23.0, bits=16, fs=44100)}
    old = {"a.wav": _m(tmp_path, "o.wav", -23.0, bits=24, fs=48000)}
    rows, _, _ = baseline.compare(cur, old)
    assert rows[0].format_changed is True
    assert "포맷 변경" in rows[0].summary()


def test_clipping_change_reported(tmp_path):
    x = fade(sine_rms(400.0, 1.0, -12.0, FS48), FS48, ms=100.0)
    for i in range(20000, 20040):
        x[i] = 1.0
    p = os.path.join(str(tmp_path), "clip.wav")
    wavread.write_wav(p, [x], FS48, 24)
    cur = {"a.wav": analyze.analyze_file(wavread.probe(p))}
    old = {"a.wav": _m(tmp_path, "o.wav", -12.0)}
    rows, _, _ = baseline.compare(cur, old)
    assert "클리핑 0 → 1곳" in rows[0].summary()


def test_lufs_delta_none_when_silent(tmp_path):
    p = os.path.join(str(tmp_path), "q.wav")
    wavread.write_wav(p, [[0.0] * FS48], FS48, 16)
    silent = analyze.analyze_file(wavread.probe(p))
    rows, _, _ = baseline.compare({"a.wav": silent}, {"a.wav": _m(tmp_path, "o.wav", -23.0)})
    assert rows[0].lufs_delta is None


def test_empty_baseline(tmp_path):
    cur = {"a.wav": _m(tmp_path, "a.wav", -23.0)}
    rows, unmatched, leftover = baseline.compare(cur, {})
    assert rows == [] and unmatched == ["a.wav"] and leftover == []
