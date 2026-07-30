"""수치 특수함수 검증 — 고정밀(mpmath로 미리 계산한) 기준값과 대조.

기준값은 40자리 정밀도로 계산해 하드코딩했으므로 이 테스트는 완전히 오프라인이다.
"""

import math

import pytest

from powerplan.special import (
    betainc,
    bisect_increasing,
    gauss_legendre,
    log_beta,
    norm_cdf,
    norm_pdf,
    norm_ppf,
)

# Φ(z) 기준값 (mpmath, 40자리 → 배정도로 반올림)
NORM_CDF_CASES = [
    (-8.0, 6.220960574271786e-16),
    (-3.0, 0.001349898031630095),
    (-1.959963984540054, 0.02500000000000000),
    (-0.5, 0.3085375387259869),
    (0.0, 0.5),
    (1.0, 0.8413447460685429),
    (1.6448536269514722, 0.9500000000000000),
    (2.5758293035489004, 0.9950000000000000),
    (6.0, 0.9999999990134124),
]

# I_x(a, b) 기준값 (mpmath betainc regularized)
BETAINC_CASES = [
    (0.5, 0.5, 0.3, 0.36901011956554538),
    (2.0, 3.0, 0.7, 0.9163),
    (50.0, 0.5, 0.9, 0.0012041498325598114),
    (1.0, 1.0, 0.42, 0.42),
    (0.1, 0.1, 0.001, 0.2542416565886082),
]


@pytest.mark.parametrize("z,expected", NORM_CDF_CASES)
def test_norm_cdf_matches_high_precision(z, expected):
    assert norm_cdf(z) == pytest.approx(expected, rel=1e-13, abs=1e-300)


def test_norm_cdf_symmetry_and_bounds():
    for z in (0.1, 1.3, 2.7, 5.5, 30.0):
        assert norm_cdf(z) + norm_cdf(-z) == pytest.approx(1.0, abs=1e-15)
    assert norm_cdf(float("-inf")) == 0.0
    assert norm_cdf(float("inf")) == 1.0
    assert math.isnan(norm_cdf(float("nan")))


def test_norm_pdf_known_values():
    assert norm_pdf(0.0) == pytest.approx(0.3989422804014327, rel=1e-15)
    assert norm_pdf(1.0) == pytest.approx(0.2419707245191434, rel=1e-15)


@pytest.mark.parametrize("p,expected", [
    (0.025, -1.959963984540054),
    (0.05, -1.6448536269514722),
    (0.5, 0.0),
    (0.8, 0.8416212335729143),
    (0.975, 1.959963984540054),
    (0.995, 2.5758293035489004),
    (1e-8, -5.6120012441747887),
])
def test_norm_ppf_matches_high_precision(p, expected):
    assert norm_ppf(p) == pytest.approx(expected, rel=1e-13, abs=1e-13)


def test_norm_ppf_round_trip():
    for p in (1e-6, 0.001, 0.02, 0.25, 0.5, 0.75, 0.99, 1 - 1e-6):
        assert norm_cdf(norm_ppf(p)) == pytest.approx(p, rel=1e-12)


def test_norm_ppf_edges():
    assert norm_ppf(0.0) == float("-inf")
    assert norm_ppf(1.0) == float("inf")
    assert math.isnan(norm_ppf(float("nan")))


@pytest.mark.parametrize("a,b,x,expected", BETAINC_CASES)
def test_betainc_matches_high_precision(a, b, x, expected):
    assert betainc(a, b, x) == pytest.approx(expected, rel=1e-13)


def test_betainc_identities():
    # I_x(a,b) = 1 − I_{1−x}(b,a)
    for a, b, x in [(2.0, 5.0, 0.3), (0.7, 0.2, 0.85), (100.0, 3.0, 0.95)]:
        assert betainc(a, b, x) == pytest.approx(1.0 - betainc(b, a, 1.0 - x), abs=1e-13)
    assert betainc(1.0, 1.0, 0.37) == pytest.approx(0.37, abs=1e-15)
    assert betainc(3.0, 2.0, 0.0) == 0.0
    assert betainc(3.0, 2.0, 1.0) == 1.0


def test_betainc_monotone_in_x():
    prev = -1.0
    for i in range(1, 100):
        cur = betainc(2.5, 4.5, i / 100.0)
        assert cur >= prev
        prev = cur


def test_betainc_rejects_bad_parameters():
    with pytest.raises(ValueError):
        betainc(0.0, 1.0, 0.5)
    with pytest.raises(ValueError):
        betainc(1.0, -2.0, 0.5)


def test_betainc_x1m_preserves_precision():
    """1−x가 극히 작을 때 x1m을 넘기면 정밀도가 유지된다 (df=1e6 t분포 상황)."""
    df = 1.0e6
    t = 0.3
    denom = df + t * t
    naive = betainc(0.5 * df, 0.5, df / denom)
    exact_arg = betainc(0.5 * df, 0.5, df / denom, (t * t) / denom)
    # mpmath 기준값: I_x(5e5, 0.5) at x = 1e6/(1e6+0.09)
    reference = 0.7641772179789942
    assert exact_arg == pytest.approx(reference, rel=1e-14)
    assert abs(naive - reference) > 1e-13  # 넘기지 않으면 정밀도가 실제로 나빠진다


def test_log_beta_stable_for_large_arguments():
    # log B(a, 0.5) 기준값 (mpmath, 40자리)
    assert log_beta(1000.0, 0.5) == pytest.approx(-2.8813876965715768, rel=1e-14)
    assert log_beta(500000.0, 0.5) == pytest.approx(-5.9888164957774643, rel=1e-14)
    assert log_beta(2.0, 3.0) == pytest.approx(math.log(1.0 / 12.0), rel=1e-14)
    assert log_beta(0.5, 0.5) == pytest.approx(math.log(math.pi), rel=1e-14)
    assert log_beta(3.0, 7.0) == pytest.approx(log_beta(7.0, 3.0), rel=1e-15)


def test_gauss_legendre_integrates_polynomials_exactly():
    nodes, weights = gauss_legendre(20)
    assert len(nodes) == len(weights) == 20
    assert math.fsum(weights) == pytest.approx(2.0, rel=1e-15)
    # 20점 규칙은 39차까지 정확 → x^2, x^8, x^38 모두 정확해야 한다
    for power in (0, 2, 8, 38):
        got = math.fsum(w * x ** power for x, w in zip(nodes, weights))
        want = 2.0 / (power + 1)
        assert got == pytest.approx(want, rel=1e-12)
    # 홀수차는 0
    for power in (1, 3, 7):
        got = math.fsum(w * x ** power for x, w in zip(nodes, weights))
        assert got == pytest.approx(0.0, abs=1e-14)


def test_gauss_legendre_nodes_sorted_and_symmetric():
    nodes, weights = gauss_legendre(9)
    assert list(nodes) == sorted(nodes)
    for i in range(9):
        assert nodes[i] == pytest.approx(-nodes[8 - i], abs=1e-15)
        assert weights[i] == pytest.approx(weights[8 - i], rel=1e-15)
    with pytest.raises(ValueError):
        gauss_legendre(1)


def test_bisect_increasing_basic():
    root = bisect_increasing(lambda x: x * x, 2.0, 0.0, 10.0)
    assert root == pytest.approx(math.sqrt(2.0), rel=1e-12)
    # 목표가 구간 밖이면 경계를 돌려준다
    assert bisect_increasing(lambda x: x, 100.0, 0.0, 1.0) == 1.0
    assert bisect_increasing(lambda x: x, -100.0, 0.0, 1.0) == 0.0
