"""오케스트레이션(analyze_rr) 및 예제 데이터 대비 테스트."""

import math
import os

import pytest

from hrvkit import analyze_rr, render_text
from hrvkit.dataio import load_series

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


def _load(name):
    return load_series(os.path.join(EXAMPLES, name))


def test_analyze_basic_shape():
    rng_series = [800 + 20 * math.sin(i / 3.0) for i in range(120)]
    res = analyze_rr(rng_series)
    assert res.n_input == 120
    assert "rmssd" in res.time
    assert "hf_power" in res.freq
    assert "sd1" in res.poincare
    d = res.to_dict()
    assert d["time_domain"]["rmssd"] == res.time["rmssd"]
    txt = render_text(res)
    assert "HRV" in txt and "RMSSD" in txt


def test_artifacts_reported():
    rr = [800, 810, 2500, 805, 795, 815, 790, 808, 800, 812]
    res = analyze_rr(rr, clean_method="interpolate")
    assert res.n_artifacts >= 1
    assert res.pct_artifacts > 0


def test_all_identical_does_not_crash():
    res = analyze_rr([800.0] * 60)
    assert res.time["sdnn"] == pytest.approx(0.0)
    assert res.poincare["sd1"] == pytest.approx(0.0)
    # 분산 0 → 대역 파워 0, LF/HF는 정의불가(inf) 이어도 크래시 없음
    assert res.freq["total_power"] == pytest.approx(0.0)
    render_text(res)  # 렌더링도 예외 없이


def test_one_beat_raises():
    with pytest.raises(ValueError):
        analyze_rr([800.0])


def test_empty_raises():
    with pytest.raises(ValueError):
        analyze_rr([])


def test_examples_exist():
    for name in ("resting.csv", "slow_breathing.csv"):
        assert os.path.exists(os.path.join(EXAMPLES, name)), name


def test_slow_breathing_shows_more_parasympathetic_than_resting():
    """느린 호흡 예제가 안정 예제보다 RMSSD/HF/SD1이 높고 LF/HF가 낮아야 함."""
    rr_rest, _ = _load("resting.csv")
    rr_slow, _ = _load("slow_breathing.csv")
    rest = analyze_rr(rr_rest)
    slow = analyze_rr(rr_slow)

    assert slow.time["rmssd"] > rest.time["rmssd"]
    assert slow.time["sdnn"] > rest.time["sdnn"]
    assert slow.poincare["sd1"] > rest.poincare["sd1"]
    assert slow.freq["hf_nu"] > rest.freq["hf_nu"]
    assert slow.freq["lf_hf_ratio"] < rest.freq["lf_hf_ratio"]


def test_examples_have_expected_units_and_columns():
    rr_rest, m_rest = _load("resting.csv")
    rr_slow, m_slow = _load("slow_breathing.csv")
    assert m_rest["unit"] == "ms"
    assert m_slow["column"] == "rr_ms"  # time+value 형식에서 값 열 선택
    assert 250 <= len(rr_rest) <= 400
    assert 250 <= len(rr_slow) <= 400
