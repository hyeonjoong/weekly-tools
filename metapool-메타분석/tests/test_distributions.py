"""분포 함수를 알려진 참값과 대조한다 (R/scipy 표준값, 오프라인 상수)."""

import math

import pytest

from metapool.distributions import (
    chi2_sf,
    normal_cdf,
    normal_ppf,
    normal_sf,
    t_cdf,
    t_ppf,
    t_sf,
)


def test_normal_cdf_known_points():
    assert normal_cdf(0.0) == pytest.approx(0.5, abs=1e-15)
    assert normal_cdf(1.0) == pytest.approx(0.8413447460685429, abs=1e-14)
    assert normal_cdf(-1.96) == pytest.approx(0.024997895148220435, abs=1e-14)


def test_normal_sf_is_accurate_in_far_tail():
    # 1 - cdf 로 계산하면 소실되는 영역
    assert normal_sf(8.0) == pytest.approx(6.220960574271835e-16, rel=1e-9)


def test_normal_ppf_matches_standard_quantiles():
    assert normal_ppf(0.975) == pytest.approx(1.959963984540054, abs=1e-12)
    assert normal_ppf(0.995) == pytest.approx(2.5758293035489004, abs=1e-12)
    assert normal_ppf(0.5) == pytest.approx(0.0, abs=1e-12)


def test_normal_ppf_round_trip():
    for p in (0.001, 0.05, 0.3, 0.5, 0.84, 0.999):
        assert normal_cdf(normal_ppf(p)) == pytest.approx(p, abs=1e-12)


def test_normal_ppf_rejects_out_of_range():
    with pytest.raises(ValueError):
        normal_ppf(0.0)
    with pytest.raises(ValueError):
        normal_ppf(1.0)


def test_t_ppf_matches_published_table():
    # R: qt(0.975, df)
    assert t_ppf(0.975, 1) == pytest.approx(12.706204736432095, abs=1e-9)
    assert t_ppf(0.975, 5) == pytest.approx(2.570581835636197, abs=1e-9)
    assert t_ppf(0.975, 10) == pytest.approx(2.2281388519862735, abs=1e-9)
    assert t_ppf(0.975, 30) == pytest.approx(2.0422724563012373, abs=1e-9)


def test_t_cdf_and_sf():
    # R: pt(2, 5) = 0.9490302
    assert t_cdf(2.0, 5) == pytest.approx(0.9490302605850709, abs=1e-12)
    assert t_sf(2.0, 5) == pytest.approx(1 - 0.9490302605850709, abs=1e-12)
    assert t_cdf(0.0, 7) == pytest.approx(0.5, abs=1e-14)
    # 자유도가 크면 정규분포에 수렴
    assert t_cdf(1.96, 1_000_000) == pytest.approx(normal_cdf(1.96), abs=1e-6)


def test_t_cdf_symmetry():
    for t in (0.3, 1.0, 2.5, 6.0):
        assert t_cdf(-t, 9) == pytest.approx(1 - t_cdf(t, 9), abs=1e-13)


def test_chi2_sf_known_points():
    # 카이제곱 임계값: qchisq(0.95, 1) = 3.841459
    assert chi2_sf(3.841458820694124, 1) == pytest.approx(0.05, abs=1e-12)
    assert chi2_sf(5.991464547107979, 2) == pytest.approx(0.05, abs=1e-12)
    assert chi2_sf(16.918977604620448, 9) == pytest.approx(0.05, abs=1e-12)


def test_chi2_sf_df2_is_exponential():
    # df=2 이면 상측확률이 정확히 exp(-x/2)
    for x in (0.5, 3.0, 32.0):
        assert chi2_sf(x, 2) == pytest.approx(math.exp(-x / 2.0), rel=1e-12)


def test_chi2_sf_edge_cases():
    assert chi2_sf(0.0, 3) == 1.0
    assert chi2_sf(-1.0, 3) == 1.0
    with pytest.raises(ValueError):
        chi2_sf(1.0, 0)
