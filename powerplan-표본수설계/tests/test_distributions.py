"""분포함수 검증 — mpmath 40자리로 미리 계산한 기준값과 대조 (오프라인).

비중심 t/F는 이 툴의 심장이므로, 기준값 대조 + 항등식 + 단조성 + 극단값까지
모두 확인한다.
"""

import math

import pytest

from powerplan.distributions import (
    chi_expect,
    chi_expectation,
    f_cdf,
    f_ppf,
    ncf_sf,
    nct_cdf,
    nct_ncp_ci,
    nct_sf,
    t_cdf,
    t_ppf,
    t_sf,
)

# (t, df, P(T ≤ t))
T_CDF_CASES = [
    (-8.0, 1, 0.039583424160565542),
    (-2.5, 5, 0.027245049671188121),
    (0.0, 7, 0.5),
    (1.0, 2000, 0.84128426094962836),
    (1.96, 126, 0.97389850651710218),
    (2.5, 10, 0.9842765778816956),
    (0.3, 1e6, 0.6179113910105029),
]

# (t, df, ncp, P(T' ≤ t))
NCT_CASES = [
    (2.0, 126, 4.0, 0.023393851350137398),
    (-1.5, 10, -2.0, 0.69521455262395769),
    (0.0, 5, 1.0, 0.15865525393145705),
    (3.0, 30, 2.8, 0.56449746376363008),
    (1.9, 1000, 0.5, 0.91898314033934946),
    (12.0, 3, 8.0, 0.71799441224878901),
]

# (x, df1, df2, ncp, P(F' > x))
NCF_CASES = [
    (3.0537, 2, 156, 9.9375, 0.80492231690918804),
    (2.7, 3, 68, 11.52, 0.80489524751757516),
    (4.0, 1, 20, 5.0, 0.59857661980139141),
]


@pytest.mark.parametrize("t,df,expected", T_CDF_CASES)
def test_t_cdf_matches_high_precision(t, df, expected):
    assert t_cdf(t, df) == pytest.approx(expected, rel=1e-13)


def test_t_cdf_known_closed_forms():
    # df=1은 Cauchy: F(t) = 1/2 + arctan(t)/π
    for t in (-3.0, -0.5, 0.7, 4.0):
        assert t_cdf(t, 1) == pytest.approx(0.5 + math.atan(t) / math.pi, rel=1e-14)
    # df=2: F(t) = 1/2 (1 + t/√(2+t²))
    for t in (-2.0, 0.3, 5.0):
        assert t_cdf(t, 2) == pytest.approx(0.5 * (1 + t / math.sqrt(2 + t * t)), rel=1e-14)


def test_t_cdf_symmetry_and_edges():
    for df in (1, 3.5, 30, 1e5):
        for t in (0.4, 1.7, 6.0):
            assert t_cdf(t, df) + t_cdf(-t, df) == pytest.approx(1.0, abs=1e-14)
        assert t_cdf(0.0, df) == pytest.approx(0.5, abs=1e-15)
        assert t_cdf(float("inf"), df) == 1.0
        assert t_cdf(float("-inf"), df) == 0.0
        assert math.isnan(t_cdf(float("nan"), df))
    assert t_sf(2.0, 10) == pytest.approx(1.0 - t_cdf(2.0, 10), abs=1e-15)
    with pytest.raises(ValueError):
        t_cdf(0.0, 0.0)
    with pytest.raises(ValueError):
        t_cdf(0.0, -3.0)


@pytest.mark.parametrize("p,df,expected", [
    (0.975, 1, 12.706204736174698),
    (0.975, 10, 2.2281388519649385),
    (0.95, 126, 1.6570369819907131),
    (0.975, 2000, 1.961150826099438),
    (0.025, 30, -2.0422724563012377),
])
def test_t_ppf_matches_tables(p, df, expected):
    """교과서 t 임계값과 대조 (df=1, 10, 30은 통계표 그대로)."""
    assert t_ppf(p, df) == pytest.approx(expected, rel=1e-10)


def test_t_ppf_round_trip_and_edges():
    for df in (1, 4, 63, 5000):
        for p in (0.001, 0.025, 0.4, 0.9, 0.999):
            assert t_cdf(t_ppf(p, df), df) == pytest.approx(p, rel=1e-9)
    assert t_ppf(0.5, 12) == 0.0
    assert t_ppf(0.0, 12) == float("-inf")
    assert t_ppf(1.0, 12) == float("inf")


def test_f_cdf_relates_to_t_squared():
    """F(1, df) = t(df)² → P(F ≤ x) = P(|T| ≤ √x)."""
    for df in (5, 40, 300):
        for x in (0.5, 2.0, 7.3):
            expect = 1.0 - 2.0 * t_cdf(-math.sqrt(x), df)
            assert f_cdf(x, 1, df) == pytest.approx(expect, rel=1e-12)


def test_f_ppf_matches_tables():
    # 통계표 F(0.95; df1, df2)
    assert f_ppf(0.95, 2, 156) == pytest.approx(3.0537, rel=1e-4)
    assert f_ppf(0.95, 3, 68) == pytest.approx(2.7395, rel=1e-4)
    assert f_ppf(0.95, 1, 10) == pytest.approx(4.9646, rel=1e-4)
    assert f_ppf(0.99, 4, 20) == pytest.approx(4.4307, rel=1e-4)
    for df1, df2 in ((1, 5), (3, 60), (10, 2000)):
        for p in (0.5, 0.9, 0.99):
            assert f_cdf(f_ppf(p, df1, df2), df1, df2) == pytest.approx(p, rel=1e-9)
    assert f_ppf(0.0, 2, 5) == 0.0
    assert f_ppf(1.0, 2, 5) == float("inf")
    assert f_cdf(0.0, 2, 5) == 0.0
    assert f_cdf(float("inf"), 2, 5) == 1.0


@pytest.mark.parametrize("t,df,ncp,expected", NCT_CASES)
def test_nct_cdf_matches_high_precision(t, df, ncp, expected):
    assert nct_cdf(t, df, ncp) == pytest.approx(expected, rel=1e-11, abs=1e-13)


def test_nct_reduces_to_central_when_ncp_zero():
    for df in (1, 2.5, 12, 500, 1e6):
        for t in (-3.0, -0.2, 0.0, 1.5, 4.0):
            assert nct_cdf(t, df, 0.0) == pytest.approx(t_cdf(t, df), abs=1e-15)


def test_nct_large_df_approaches_normal():
    """df → ∞ 이면 T' → N(ncp, 1)."""
    from powerplan.special import norm_cdf
    for ncp in (-2.5, 0.7, 3.0):
        for t in (-1.0, 0.5, 4.0):
            assert nct_cdf(t, 5e7, ncp) == pytest.approx(norm_cdf(t - ncp), abs=1e-6)


def test_nct_sf_complements_cdf():
    for df in (2, 17, 240):
        for ncp in (-3.0, 0.0, 1.4, 6.0):
            for t in (-2.0, 0.0, 2.5):
                assert nct_sf(t, df, ncp) + nct_cdf(t, df, ncp) == pytest.approx(1.0, abs=1e-12)


def test_nct_monotone_in_t_and_ncp():
    prev = -1.0
    for i in range(-40, 41):
        cur = nct_cdf(i / 8.0, 15, 1.5)
        assert cur >= prev - 1e-15
        prev = cur
    prev = 2.0
    for i in range(-30, 31):  # ncp가 커지면 cdf는 감소
        cur = nct_cdf(1.2, 15, i / 5.0)
        assert cur <= prev + 1e-15
        prev = cur


def test_nct_edges_and_nan():
    assert nct_cdf(float("inf"), 10, 2.0) == 1.0
    assert nct_cdf(float("-inf"), 10, 2.0) == 0.0
    assert math.isnan(nct_cdf(float("nan"), 10, 2.0))
    assert math.isnan(nct_cdf(1.0, 10, float("nan")))
    assert 0.0 <= nct_cdf(1.0, 1, 40.0) <= 1.0


@pytest.mark.parametrize("x,df1,df2,ncp,expected", NCF_CASES)
def test_ncf_sf_matches_high_precision(x, df1, df2, ncp, expected):
    assert ncf_sf(x, df1, df2, ncp) == pytest.approx(expected, rel=1e-11)


def test_ncf_reduces_to_central_when_ncp_zero():
    for df1, df2 in ((1, 10), (3, 45), (7, 500)):
        for x in (0.3, 1.0, 4.5):
            assert ncf_sf(x, df1, df2, 0.0) == pytest.approx(1.0 - f_cdf(x, df1, df2), abs=1e-14)


def test_ncf_matches_nct_for_df1_equals_one():
    """F'(1, df, λ) = T'(df, δ)² with λ = δ² → 상측확률이 일치해야 한다."""
    for df2 in (10, 60, 400):
        for delta in (1.0, 2.5, 4.0):
            for x in (1.0, 3.84, 9.0):
                t = math.sqrt(x)
                expect = nct_sf(t, df2, delta) + nct_cdf(-t, df2, delta)
                assert ncf_sf(x, 1, df2, delta * delta) == pytest.approx(expect, abs=1e-11)


def test_ncf_monotone_and_edges():
    prev = 2.0
    for i in range(1, 60):
        cur = ncf_sf(i / 6.0, 3, 40, 8.0)
        assert cur <= prev + 1e-15
        prev = cur
    assert ncf_sf(0.0, 2, 10, 5.0) == 1.0
    assert ncf_sf(float("inf"), 2, 10, 5.0) == 0.0
    assert math.isnan(ncf_sf(float("nan"), 2, 10, 5.0))
    with pytest.raises(ValueError):
        ncf_sf(1.0, 2, 10, -1.0)


def test_ncf_handles_very_large_ncp():
    """λ가 커도 (급수 항이 많아도) 유한 시간에 정상값을 준다."""
    value = ncf_sf(1.2, 4, 800, 3000.0)
    assert 0.999 < value <= 1.0
    small = ncf_sf(50.0, 4, 800, 3000.0)
    assert 0.0 <= small <= 1.0


def test_chi_expect_known_values():
    # E[χ_1] = √(2/π), E[χ_2] = √(π/2)
    mean1, sd1 = chi_expect(1)
    assert mean1 == pytest.approx(math.sqrt(2.0 / math.pi), rel=1e-14)
    assert sd1 == pytest.approx(math.sqrt(1.0 - 2.0 / math.pi), rel=1e-14)
    mean2, _ = chi_expect(2)
    assert mean2 == pytest.approx(math.sqrt(math.pi / 2.0), rel=1e-14)
    # 큰 df 근사식이 정확식과 이어지는지
    exact = math.sqrt(2.0) * math.exp(math.lgamma(150.5) - math.lgamma(150.0))
    mean300, _ = chi_expect(300)
    assert mean300 == pytest.approx(exact, rel=1e-12)


def test_chi_expectation_normalised():
    """가중치 합이 1이므로 상수함수의 기대값은 그 상수."""
    for df in (1, 3, 50, 1e5):
        assert chi_expectation(df, lambda x: 1.0) == pytest.approx(1.0, rel=1e-14)
    # E[χ²] = df
    for df in (2, 9, 120):
        assert chi_expectation(df, lambda x: x * x) == pytest.approx(df, rel=1e-10)
    # E[χ] 는 chi_expect와 일치
    for df in (1, 5, 80):
        assert chi_expectation(df, lambda x: x) == pytest.approx(chi_expect(df)[0], rel=1e-10)


def test_nct_ncp_ci_is_a_proper_pivot():
    """구간 양 끝에서 P(T' ≤ t_obs)가 정확히 α/2, 1−α/2가 되어야 한다."""
    for t_obs, df in ((2.5, 20), (4.0, 58), (-1.9, 10), (0.0, 15)):
        low, high = nct_ncp_ci(t_obs, df, 0.95)
        assert low < high
        assert nct_cdf(t_obs, df, high) == pytest.approx(0.025, abs=1e-9)
        assert nct_cdf(t_obs, df, low) == pytest.approx(0.975, abs=1e-9)
    # t_obs = 0이면 구간은 0을 중심으로 대칭
    low, high = nct_ncp_ci(0.0, 25, 0.95)
    assert low == pytest.approx(-high, abs=1e-8)
    with pytest.raises(ValueError):
        nct_ncp_ci(2.0, 10, 1.5)


def test_nct_ncp_ci_wider_for_higher_confidence():
    low95, high95 = nct_ncp_ci(3.0, 30, 0.95)
    low99, high99 = nct_ncp_ci(3.0, 30, 0.99)
    assert low99 < low95 < high95 < high99
