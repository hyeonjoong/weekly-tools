"""stats.chi2_sf 를 표로 알려진 카이제곱 임계값에 대해 검증."""
import math

from factorscan.stats import chi2_sf


def test_chi2_sf_known_critical_values():
    # 상측 5% 임계값: 표준 카이제곱 분포표
    assert abs(chi2_sf(3.8415, 1) - 0.05) < 1e-3
    assert abs(chi2_sf(5.9915, 2) - 0.05) < 1e-3
    assert abs(chi2_sf(11.0705, 5) - 0.05) < 1e-3
    assert abs(chi2_sf(18.307, 10) - 0.05) < 1e-3


def test_chi2_sf_upper_1pct():
    assert abs(chi2_sf(6.635, 1) - 0.01) < 1e-3
    assert abs(chi2_sf(23.209, 10) - 0.01) < 1e-3


def test_chi2_sf_boundaries():
    assert chi2_sf(0.0, 3) == 1.0
    assert chi2_sf(-5.0, 3) == 1.0
    # df=2 는 지수분포: P(X>x) = exp(-x/2)
    assert abs(chi2_sf(4.0, 2) - math.exp(-2.0)) < 1e-9
    assert abs(chi2_sf(1.0, 2) - math.exp(-0.5)) < 1e-9


def test_chi2_sf_monotone_and_range():
    prev = 1.0
    for x in [1, 2, 5, 10, 20, 50]:
        v = chi2_sf(x, 4)
        assert 0.0 <= v <= 1.0
        assert v < prev
        prev = v
