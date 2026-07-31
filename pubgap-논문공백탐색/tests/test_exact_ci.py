"""정확 신뢰구간(Clopper–Pearson / Garwood 포아송) — 교과서 값과 대조.

여기 쓰인 기대값은 scipy 없이도 검증 가능한 **닫힌 형태**나 널리 인용되는 표준값이다:
  - CP 상한(k=0): 1 − (α/2)^(1/n)
  - CP 하한(k=n): (α/2)^(1/n)
  - 포아송 정확구간(k=0): (0, 3.6889), (k=5): (1.6235, 11.6683)
"""

import math

import pytest

from pubgap.analyze import (
    clopper_pearson,
    lift_ci,
    poisson_count_ci,
    reg_inc_beta,
)


def test_clopper_pearson_zero_successes_matches_closed_form():
    for n in (1, 5, 10, 37, 250):
        lo, hi = clopper_pearson(0, n)
        assert lo == 0.0
        assert hi == pytest.approx(1 - 0.025 ** (1 / n), abs=1e-9)


def test_clopper_pearson_all_successes_matches_closed_form():
    for n in (1, 5, 10, 37, 250):
        lo, hi = clopper_pearson(n, n)
        assert hi == 1.0
        assert lo == pytest.approx(0.025 ** (1 / n), abs=1e-9)


def test_clopper_pearson_known_values():
    # 널리 인용되는 표준 예: 3/12, 5/10, 1/2.
    assert clopper_pearson(3, 12) == pytest.approx((0.05486, 0.57186), abs=1e-4)
    assert clopper_pearson(5, 10) == pytest.approx((0.18709, 0.81291), abs=1e-4)
    assert clopper_pearson(1, 2) == pytest.approx((0.01258, 0.98742), abs=1e-4)


def test_clopper_pearson_is_symmetric_under_success_failure_swap():
    for k, n in ((2, 9), (7, 20), (13, 41)):
        lo, hi = clopper_pearson(k, n)
        lo2, hi2 = clopper_pearson(n - k, n)
        assert lo == pytest.approx(1 - hi2, abs=1e-10)
        assert hi == pytest.approx(1 - lo2, abs=1e-10)


def test_clopper_pearson_contains_the_point_estimate_and_is_monotone():
    prev_lo = prev_hi = None
    for k in range(0, 21):
        lo, hi = clopper_pearson(k, 20)
        assert lo <= k / 20 <= hi
        assert 0.0 <= lo <= hi <= 1.0
        if prev_lo is not None:  # k 가 커지면 구간도 위로 이동한다
            assert lo >= prev_lo - 1e-12
            assert hi >= prev_hi - 1e-12
        prev_lo, prev_hi = lo, hi


def test_clopper_pearson_alpha_widens_the_interval():
    lo95, hi95 = clopper_pearson(4, 20, alpha=0.05)
    lo99, hi99 = clopper_pearson(4, 20, alpha=0.01)
    assert lo99 < lo95 and hi99 > hi95


def test_clopper_pearson_rejects_impossible_inputs():
    with pytest.raises(ValueError):
        clopper_pearson(5, 3)
    with pytest.raises(ValueError):
        clopper_pearson(-1, 3)
    with pytest.raises(ValueError):
        clopper_pearson(1, 3, alpha=0.0)
    # n=0 은 '정보 없음' — 오류가 아니라 (0,1) 을 돌려준다.
    assert clopper_pearson(0, 0) == (0.0, 1.0)


def test_poisson_exact_ci_known_values():
    assert poisson_count_ci(0) == pytest.approx((0.0, 3.68888), abs=1e-4)
    assert poisson_count_ci(5) == pytest.approx((1.62348, 11.66833), abs=1e-4)
    assert poisson_count_ci(10) == pytest.approx((4.79539, 18.39036), abs=1e-4)


def test_poisson_ci_matches_direct_cdf_inversion():
    """정의(Garwood)를 **직접** 이분법으로 역산한 값과 일치해야 한다.

    구현은 불완전 감마 역함수를 쓰고, 여기서는 포아송 CDF 합산을 그대로 뒤집는다 —
    서로 다른 경로가 같은 답을 내는지 확인한다.
    """
    from math import exp, lgamma, log

    def poisson_cdf(k: int, lam: float) -> float:
        return sum(exp(-lam + i * log(lam) - lgamma(i + 1)) for i in range(k + 1))

    def invert(k: int, target: float) -> float:
        lo, hi = 1e-12, 1e4
        for _ in range(300):
            mid = 0.5 * (lo + hi)
            if poisson_cdf(k, mid) > target:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    for k in (1, 3, 12, 47):
        lo, hi = poisson_count_ci(k)
        assert lo == pytest.approx(invert(k - 1, 0.975), rel=1e-9)
        assert hi == pytest.approx(invert(k, 0.025), rel=1e-9)


def test_poisson_exact_ci_covers_the_count_and_grows_monotonically():
    prev = (0.0, 0.0)
    for k in range(0, 60):
        lo, hi = poisson_count_ci(k)
        assert lo <= k <= hi
        assert lo >= prev[0] and hi > prev[1]
        prev = (lo, hi)


def test_poisson_ci_is_stable_for_large_counts():
    lo, hi = poisson_count_ci(100_000)
    # 큰 k 에서는 정규근사 k ± 1.96√k 에 수렴해야 한다.
    assert lo == pytest.approx(100_000 - 1.96 * math.sqrt(100_000), rel=2e-3)
    assert hi == pytest.approx(100_000 + 1.96 * math.sqrt(100_000), rel=2e-3)


def test_lift_ci_scales_with_expected():
    lo, hi = lift_ci(0, 4.0)
    assert lo == 0.0
    assert hi == pytest.approx(3.68888 / 4.0, abs=1e-5)
    # 기대값이 0 이면 비를 정의할 수 없다.
    assert lift_ci(0, 0.0) == (None, None)
    assert lift_ci(3, -1.0) == (None, None)


def test_lift_ci_brackets_the_point_estimate():
    for obs, exp in ((0, 2.0), (1, 8.0), (7, 3.5), (40, 41.0)):
        lo, hi = lift_ci(obs, exp)
        assert lo <= obs / exp <= hi


def test_reg_inc_beta_edges_and_symmetry():
    assert reg_inc_beta(2, 3, 0.0) == 0.0
    assert reg_inc_beta(2, 3, 1.0) == 1.0
    # I_x(a,b) = 1 − I_{1−x}(b,a)
    for a, b, x in ((2, 3, 0.4), (5, 5, 0.5), (0.5, 7, 0.9), (30, 2, 0.97)):
        assert reg_inc_beta(a, b, x) == pytest.approx(
            1 - reg_inc_beta(b, a, 1 - x), abs=1e-12
        )


def test_reg_inc_beta_matches_binomial_cdf():
    """I_{1−p}(n−k, k+1) = P(X ≤ k) for X~Bin(n,p) — 직접 합산과 대조."""
    from math import comb

    n, p = 12, 0.37
    for k in range(0, n):
        direct = sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))
        assert reg_inc_beta(n - k, k + 1, 1 - p) == pytest.approx(direct, abs=1e-12)


def test_reg_inc_beta_at_the_recursion_boundary_terminates():
    """a=b, x=0.5 는 예전 구현에서 무한 재귀를 일으켰다(회귀 방지)."""
    for a in (1, 2, 5, 50):
        assert reg_inc_beta(a, a, 0.5) == pytest.approx(0.5, abs=1e-9)
