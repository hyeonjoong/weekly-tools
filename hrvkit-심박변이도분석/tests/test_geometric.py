"""기하학적 시간영역 지표(HTI, TINN) 및 로버스트 통계 검증."""

import math

import pytest

from hrvkit.timedomain import geometric_indices, time_domain, _HIST_BIN_MS


def test_hti_all_identical_is_one():
    g = geometric_indices([800.0] * 100)
    assert g["hti"] == pytest.approx(1.0)
    assert g["tinn"] == pytest.approx(0.0)


def test_hti_equals_n_over_peak():
    # 90개는 한 빈(800~), 10개는 멀리 떨어진 빈(1200)에 → peak=90, HTI=100/90
    nn = [800.0] * 90 + [1200.0] * 10
    g = geometric_indices(nn)
    assert g["hti"] == pytest.approx(100.0 / 90.0, rel=1e-9)


def test_hti_larger_for_more_spread():
    tight = [800 + (i % 3) for i in range(300)]      # 좁은 분포
    wide = [800 + (i % 60) for i in range(300)]      # 넓은 분포
    assert geometric_indices(wide)["hti"] > geometric_indices(tight)["hti"]


def test_tinn_positive_for_spread_distribution():
    import random
    rng = random.Random(0)
    nn = [800 + rng.gauss(0, 40) for _ in range(1000)]
    g = geometric_indices(nn)
    assert g["tinn"] > 0
    # 삼각형 밑변은 대략 분포 폭 규모(수백 ms)
    assert 50 < g["tinn"] < 600


def test_bin_width_is_task_force_standard():
    assert _HIST_BIN_MS == pytest.approx(1000.0 / 128.0)


def test_time_domain_includes_geometric_and_robust():
    td = time_domain([800, 810, 790, 820, 795, 805, 815, 788, 802, 799])
    for k in ("hti", "tinn", "median_nn", "mad_nn"):
        assert k in td
    assert td["median_nn"] > 0
    assert td["mad_nn"] >= 0


def test_mad_robust_to_outlier():
    base = [800.0] * 50
    td_clean = time_domain(base + [810.0])
    td_outlier = time_domain(base + [5000.0])   # 큰 이상값
    # MAD(중앙값 기반)는 이상값에 거의 불변, SDNN은 크게 변함
    assert td_outlier["mad_nn"] == pytest.approx(td_clean["mad_nn"], abs=1.0)
    assert td_outlier["sdnn"] > 5 * td_clean["sdnn"]
