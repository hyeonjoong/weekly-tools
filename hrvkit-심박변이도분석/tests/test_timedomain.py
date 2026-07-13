"""시간영역 지표 — 손으로 검산 가능한 작은 시리즈로 검증."""

import math

import pytest

from hrvkit.timedomain import time_domain


# 손 계산 대상 시리즈: [800, 900, 780, 810, 760]
#   diffs        = [100, -120, 30, -50]
#   mean_nn      = 4050 / 5           = 810.0
#   rmssd        = sqrt(27800/4)      = 83.36666
#   sdnn(ddof=1) = sqrt(11600/4)      = 53.85165
#   sdsd(ddof=1) = sqrt(27400/3)      = 95.56852
#   nn50 (>50)   = {100,120}          -> 2  -> pnn50 = 50%
#   nn20 (>20)   = {100,120,30,50}    -> 4  -> pnn20 = 100%
SERIES = [800.0, 900.0, 780.0, 810.0, 760.0]


def test_hand_computed_time_domain():
    r = time_domain(SERIES)
    assert r["n_beats"] == 5
    assert r["mean_nn"] == pytest.approx(810.0)
    assert r["rmssd"] == pytest.approx(math.sqrt(27800 / 4), rel=1e-12)
    assert r["sdnn"] == pytest.approx(math.sqrt(11600 / 4), rel=1e-12)
    assert r["sdsd"] == pytest.approx(math.sqrt(27400 / 3), rel=1e-12)
    assert r["nn50"] == 2
    assert r["pnn50"] == pytest.approx(50.0)
    assert r["nn20"] == 4
    assert r["pnn20"] == pytest.approx(100.0)
    assert r["cvnn"] == pytest.approx(r["sdnn"] / r["mean_nn"], rel=1e-12)


def test_mean_hr_is_mean_of_instantaneous():
    r = time_domain(SERIES)
    expected = sum(60000.0 / x for x in SERIES) / len(SERIES)
    assert r["mean_hr"] == pytest.approx(expected, rel=1e-12)
    assert r["min_hr"] == pytest.approx(60000.0 / max(SERIES))
    assert r["max_hr"] == pytest.approx(60000.0 / min(SERIES))


def test_zero_variance_series():
    r = time_domain([800.0] * 10)
    assert r["sdnn"] == pytest.approx(0.0)
    assert r["rmssd"] == pytest.approx(0.0)
    assert r["sdsd"] == pytest.approx(0.0)
    assert r["pnn50"] == pytest.approx(0.0)
    assert r["cvnn"] == pytest.approx(0.0)


def test_needs_two_beats():
    with pytest.raises(ValueError):
        time_domain([800.0])
