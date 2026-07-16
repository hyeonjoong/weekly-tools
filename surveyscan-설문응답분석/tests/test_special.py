"""특수함수·분포 분위수 테스트.

참조값은 scipy(scipy.special.betainc, scipy.stats.t/f)로 미리 계산해 하드코딩했다.
런타임은 표준 라이브러리만 쓰므로 여기서도 scipy를 import하지 않는다.
"""
import math

import pytest

from surveyscan import special as sp


def test_betainc_known_values():
    assert sp.betainc(2, 3, 0.4) == pytest.approx(0.5247999999999999, abs=1e-12)
    assert sp.betainc(0.5, 0.5, 0.3) == pytest.approx(0.36901011956554536, abs=1e-12)


def test_betainc_boundaries():
    assert sp.betainc(2, 3, 0.0) == 0.0
    assert sp.betainc(2, 3, 1.0) == 1.0
    # x<=0, x>=1 클램프
    assert sp.betainc(1, 1, -0.5) == 0.0
    assert sp.betainc(1, 1, 1.5) == 1.0


def test_betainc_symmetry():
    # I_x(a,b) = 1 - I_{1-x}(b,a)
    assert sp.betainc(2.3, 4.1, 0.37) == pytest.approx(
        1 - sp.betainc(4.1, 2.3, 0.63), abs=1e-12
    )


def test_betainc_uniform_is_identity():
    # a=b=1 이면 I_x(1,1) = x
    for x in (0.1, 0.5, 0.9):
        assert sp.betainc(1, 1, x) == pytest.approx(x, abs=1e-12)


def test_betainc_invalid_params():
    with pytest.raises(ValueError):
        sp.betainc(0, 1, 0.5)
    with pytest.raises(ValueError):
        sp.betainc(1, -1, 0.5)


def test_betaincinv_roundtrip():
    for a, b in [(2, 3), (0.5, 0.5), (5, 2)]:
        for p in (0.05, 0.5, 0.95):
            x = sp.betaincinv(a, b, p)
            assert sp.betainc(a, b, x) == pytest.approx(p, abs=1e-9)


def test_t_ppf_known_values():
    assert sp.t_ppf(0.975, 10) == pytest.approx(2.228138851986274, abs=1e-6)
    assert sp.t_ppf(0.975, 30) == pytest.approx(2.0422724563012378, abs=1e-6)
    assert sp.t_ppf(0.025, 5) == pytest.approx(-2.5705818356363155, abs=1e-6)


def test_t_ppf_symmetry_and_median():
    assert sp.t_ppf(0.5, 7) == 0.0
    assert sp.t_ppf(0.1, 12) == pytest.approx(-sp.t_ppf(0.9, 12), abs=1e-9)


def test_t_cdf_ppf_roundtrip():
    for df in (1, 4, 25, 100):
        for p in (0.01, 0.3, 0.75, 0.99):
            x = sp.t_ppf(p, df)
            assert sp.t_cdf(x, df) == pytest.approx(p, abs=1e-6)


def test_t_cdf_known():
    assert sp.t_cdf(2.0, 10) == pytest.approx(0.9633059826146299, abs=1e-9)
    assert sp.t_cdf(0.0, 10) == 0.5


def test_f_ppf_known_values():
    assert sp.f_ppf(0.975, 4, 20) == pytest.approx(3.51469516225841, abs=1e-6)
    assert sp.f_ppf(0.025, 4, 20) == pytest.approx(0.11682320526524602, abs=1e-6)


def test_f_cdf_ppf_roundtrip():
    for dfn, dfd in [(3, 10), (5, 20), (1, 50)]:
        for p in (0.05, 0.5, 0.95):
            x = sp.f_ppf(p, dfn, dfd)
            assert sp.f_cdf(x, dfn, dfd) == pytest.approx(p, abs=1e-6)


def test_f_cdf_known():
    assert sp.f_cdf(2.5, 5, 20) == pytest.approx(0.9350729538990549, abs=1e-9)
    assert sp.f_cdf(0.0, 5, 20) == 0.0


def test_distribution_invalid_df():
    for fn in (sp.t_cdf, sp.t_ppf):
        with pytest.raises(ValueError):
            fn(0.5, 0)
    with pytest.raises(ValueError):
        sp.f_ppf(0.5, 0, 10)
    with pytest.raises(ValueError):
        sp.f_cdf(1.0, 5, -1)
