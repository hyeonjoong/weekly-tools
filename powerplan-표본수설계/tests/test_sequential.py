"""군차별설계(중간분석) — 공표된 경계 상수·팽창계수와 대조하고 자기일관성을 확인한다.

핵심 검증 전략:

1. **공표값 대조** — Pocock(1977)의 상수 경계와 O'Brien–Fleming(1979)의 C/√t 경계는
   교과서에 4자리까지 실려 있다. 우리 재귀적분으로 같은 문제를 풀어 그 값이 나오는지 본다.
2. **자기일관성** — 소비함수로 정한 경계에서 귀무가설 이탈확률이 정확히 α여야 하고,
   역산한 이동모수에서 검정력이 정확히 목표값이어야 한다.
3. **환원** — 중간분석이 없으면(K=1) 경계 = z_{1−α/2}, 팽창계수 = 1이어야 한다.
4. **몬테카를로** — 독립증분 정규 랜덤워크를 직접 시뮬레이션해 경계 통과율을 확인한다.
"""

from __future__ import annotations

import math
import random

import pytest

from powerplan import sequential as S
from powerplan.sequential import (
    MAX_INTERIM,
    check_timing,
    power_from_fixed,
    sequential_plan,
)
from powerplan.special import bisect_increasing, norm_ppf
from powerplan.validate import PowerPlanError

Z975 = norm_ppf(0.975)
Z90 = norm_ppf(0.90)
Z80 = norm_ppf(0.80)


def _equal_timing(k: int) -> tuple[float, ...]:
    return tuple((i + 1) / k for i in range(k))


def _type1(bounds, timing, two_sided=True) -> float:
    up, down = S._crossing(bounds, timing, 0.0, two_sided)
    return math.fsum(up) + math.fsum(down)


# --------------------------------------------------------------------------
# 1) 공표된 고전 경계 상수 재현
# --------------------------------------------------------------------------
@pytest.mark.parametrize("k, published", [(2, 2.178), (3, 2.289), (4, 2.361), (5, 2.413)])
def test_pocock_constant_matches_published(k, published):
    """Pocock(1977) 상수 경계 — 양측 α=0.05."""
    timing = _equal_timing(k)
    c = bisect_increasing(lambda x: -_type1((x,) * k, timing), -0.05, 1.0, 5.0, tol=1e-13)
    assert abs(c - published) < 5e-4


@pytest.mark.parametrize("k, published_first", [(2, 2.797), (3, 3.471), (4, 4.049),
                                                (5, 4.562)])
def test_obrien_fleming_constant_matches_published(k, published_first):
    """O'Brien–Fleming(1979) 경계 c_k = C/√t_k — 양측 α=0.05의 첫 경계."""
    timing = _equal_timing(k)

    def bounds(c):
        return tuple(c / math.sqrt(t) for t in timing)

    c = bisect_increasing(lambda x: -_type1(bounds(x), timing), -0.05, 0.5, 5.0, tol=1e-13)
    assert abs(c / math.sqrt(timing[0]) - published_first) < 1e-3


@pytest.mark.parametrize("k, published", [(2, 1.110), (3, 1.166), (4, 1.202), (5, 1.229)])
def test_pocock_inflation_factor_matches_published(k, published):
    """Pocock 설계의 표본수 팽창계수 (양측 0.05, 검정력 0.80) — 고전 표."""
    timing = _equal_timing(k)
    c = bisect_increasing(lambda x: -_type1((x,) * k, timing), -0.05, 1.0, 5.0, tol=1e-13)
    drift = S._drift_for_power((c,) * k, timing, 0.80, True)
    assert abs((drift / (Z975 + Z80)) ** 2 - published) < 1e-3


# --------------------------------------------------------------------------
# 2) 소비함수 기반 설계의 자기일관성
# --------------------------------------------------------------------------
@pytest.mark.parametrize("spending", ["obf", "pocock", "linear"])
@pytest.mark.parametrize("interim", [1, 2, 4])
@pytest.mark.parametrize("sides", [1, 2])
def test_alpha_is_exactly_spent(spending, interim, sides):
    seq = sequential_plan(interim, 0.05, sides, 0.90, spending)
    assert abs(seq["achieved_alpha"] - 0.05) < 1e-6
    assert abs(seq["achieved_power"] - 0.90) < 1e-6
    # 누적 소비는 증가하고 마지막에 정확히 α
    cum = seq["cumulative_alpha"]
    assert all(a < b + 1e-15 for a, b in zip(cum, cum[1:]))
    assert abs(cum[-1] - 0.05) < 1e-12


def test_single_look_reduces_to_fixed_design():
    """중간분석 0회는 지원하지 않지만, 정보비율이 (1.0,) 하나면 고정설계와 같아야 한다."""
    bounds = S._solve_bounds((1.0,), 0.05, "obf", True)
    assert abs(bounds[0] - Z975) < 1e-6
    drift = S._drift_for_power(bounds, (1.0,), 0.90, True)
    assert abs(drift - (Z975 + Z90)) < 1e-5


def test_last_look_timing_equal_one_is_no_interim():
    """중간분석 시점이 1.0에 가까우면 팽창계수가 1에 수렴한다."""
    seq = sequential_plan(1, 0.05, 2, 0.90, "obf", (0.999, 1.0))
    assert abs(seq["inflation"] - 1.0) < 5e-3


def test_obf_spends_less_early_than_pocock():
    obf = sequential_plan(2, 0.05, 2, 0.90, "obf")
    pocock = sequential_plan(2, 0.05, 2, 0.90, "pocock")
    assert obf["cumulative_alpha"][0] < pocock["cumulative_alpha"][0]
    assert obf["bounds"][0] > pocock["bounds"][0]
    # 최종 경계는 반대로 OBF가 더 낮다 (α를 아껴 뒀으므로)
    assert obf["bounds"][-1] < pocock["bounds"][-1]
    # 그래서 최대 표본수는 OBF가 작고, 기대 표본수는 Pocock이 작다
    assert obf["inflation"] < pocock["inflation"]
    assert obf["expected_fraction_h1"] > pocock["expected_fraction_h1"]


def test_more_looks_costs_more_maximum_sample_size():
    factors = [sequential_plan(k, 0.05, 2, 0.90, "pocock")["inflation"] for k in (1, 2, 3, 4)]
    assert all(a < b for a, b in zip(factors, factors[1:]))
    assert factors[0] > 1.0


def test_inflation_is_modest_for_obf():
    """OBF는 '거의 공짜' — 중간분석 4회에도 표본수가 5% 미만 증가."""
    seq = sequential_plan(4, 0.05, 2, 0.90, "obf")
    assert 1.0 < seq["inflation"] < 1.05


def test_expected_sample_size_is_smaller_under_h1():
    seq = sequential_plan(2, 0.05, 2, 0.90, "pocock")
    assert seq["expected_fraction_h1"] < seq["expected_fraction_h0"] < 1.0
    assert 0.0 < seq["expected_fraction_h1"] < 1.0


def test_stop_probabilities_sum_to_power_and_alpha():
    seq = sequential_plan(2, 0.05, 2, 0.90, "obf")
    assert abs(math.fsum(seq["stop_prob_h1"]) - 0.90) < 1e-3
    assert abs(math.fsum(seq["stop_prob_h0"]) - 0.05) < 1e-6
    assert abs(seq["cumulative_stop_h1"][-1] - math.fsum(seq["stop_prob_h1"])) < 1e-12


def test_nominal_p_matches_bounds():
    for sides in (1, 2):
        seq = sequential_plan(2, 0.05, sides, 0.90, "obf")
        for b, p in zip(seq["bounds"], seq["nominal_p"]):
            from powerplan.special import norm_cdf
            assert abs(p - (1.0 - norm_cdf(b)) * sides) < 1e-12


# --------------------------------------------------------------------------
# 3) 몬테카를로 교차검증 (독립증분 랜덤워크)
# --------------------------------------------------------------------------
def _simulate(bounds, timing, drift, trials, seed, two_sided=True):
    rng = random.Random(seed)
    cs = [b * math.sqrt(t) for b, t in zip(bounds, timing)]
    steps = [t - p for t, p in zip(timing, (0.0,) + tuple(timing[:-1]))]
    hits = 0
    for _ in range(trials):
        s = 0.0
        for c, step in zip(cs, steps):
            s += rng.gauss(drift * step, math.sqrt(step))
            if s >= c:
                hits += 1
                break
            if two_sided and s <= -c:
                break
    return hits / trials


def test_monte_carlo_type1_and_power():
    seq = sequential_plan(2, 0.05, 2, 0.90, "pocock")
    trials = 200_000
    # 상측만 세면 귀무가설에서 α/2
    mc_null = _simulate(seq["bounds"], seq["timing"], 0.0, trials, seed=11)
    se = math.sqrt(0.025 * 0.975 / trials)
    assert abs(mc_null - 0.025) < 4 * se

    mc_alt = _simulate(seq["bounds"], seq["timing"], seq["drift"], trials, seed=23)
    se = math.sqrt(0.9 * 0.1 / trials)
    assert abs(mc_alt - 0.90) < 4 * se


# --------------------------------------------------------------------------
# 4) power_from_fixed
# --------------------------------------------------------------------------
def test_power_from_fixed_is_monotone_and_bounded():
    seq = sequential_plan(2, 0.05, 2, 0.90, "obf")
    values = [power_from_fixed(seq, p) for p in (0.2, 0.5, 0.8, 0.9, 0.99)]
    assert all(a < b for a, b in zip(values, values[1:]))
    assert all(0.0 <= v <= 1.0 for v in values)
    # 목표 검정력 자리에서는 거의 같은 값 (팽창계수를 곱하기 전이므로 약간 낮다)
    assert abs(power_from_fixed(seq, 0.90) - 0.90) < 0.01


def test_power_from_fixed_at_alpha_returns_alpha():
    """효과가 0이면 고정설계 검정력은 α/2(단측 통과)로, 군차별설계도 같아야 한다."""
    seq = sequential_plan(2, 0.05, 2, 0.90, "obf")
    assert abs(power_from_fixed(seq, 0.025) - 0.025) < 1e-4


def test_power_from_fixed_handles_extremes():
    seq = sequential_plan(1, 0.05, 2, 0.80, "obf")
    assert power_from_fixed(seq, 0.0) >= 0.0
    assert power_from_fixed(seq, 1.0) <= 1.0


# --------------------------------------------------------------------------
# 5) 입력 검증
# --------------------------------------------------------------------------
def test_check_timing_defaults_are_equally_spaced():
    assert check_timing(2, None) == (1 / 3, 2 / 3, 1.0)
    assert check_timing(1, (0.5,)) == (0.5, 1.0)
    assert check_timing(1, (0.5, 1.0)) == (0.5, 1.0)


@pytest.mark.parametrize("interim, bad", [
    (2, (0.5, 0.4, 1.0)),      # 순서가 뒤집힘
    (2, (0.5, 0.5, 1.0)),      # 같은 시점 두 번
    (1, (1.5, 1.0)),           # 1을 넘는 정보비율
    (1, (0.0, 1.0)),           # 0 시점
    (1, (-0.1, 1.0)),          # 음수
    (1, (0.3, 0.9)),           # 마지막이 1.0이 아님
    (1, (float("nan"), 1.0)),  # NaN
])
def test_check_timing_rejects_bad_input(interim, bad):
    with pytest.raises(PowerPlanError):
        check_timing(interim, bad)


def test_check_timing_wrong_count():
    with pytest.raises(PowerPlanError, match="timing"):
        check_timing(2, (0.5, 1.0, 1.0, 1.0))


@pytest.mark.parametrize("kwargs", [
    {"interim": 0}, {"interim": -1}, {"interim": MAX_INTERIM + 1}, {"interim": 1.5},
    {"spending": "haybittle"}, {"alpha": 0.9}, {"alpha": 0.0}, {"target_power": 1.0},
    {"target_power": 0.0},
])
def test_sequential_plan_rejects_bad_input(kwargs):
    base = {"interim": 1, "alpha": 0.05, "sides": 2, "target_power": 0.9,
            "spending": "obf"}
    base.update(kwargs)
    with pytest.raises(PowerPlanError):
        sequential_plan(base["interim"], base["alpha"], base["sides"],
                        base["target_power"], base["spending"])


def test_power_below_alpha_is_rejected():
    with pytest.raises(PowerPlanError, match="검정력"):
        sequential_plan(1, 0.05, 2, 0.01, "obf")


def test_spending_function_endpoints():
    for kind in ("obf", "pocock", "linear"):
        assert S._spent(0.0, 0.05, kind) == 0.0
        assert abs(S._spent(1.0, 0.05, kind) - 0.05) < 1e-15
        # 단조증가
        values = [S._spent(t / 10, 0.05, kind) for t in range(11)]
        assert all(a <= b + 1e-15 for a, b in zip(values, values[1:]))


def test_unknown_spending_function_raises():
    with pytest.raises(PowerPlanError):
        S._spent(0.5, 0.05, "nope")


def test_unequal_timing_changes_boundaries():
    early = sequential_plan(1, 0.05, 2, 0.90, "pocock", (0.25, 1.0))
    late = sequential_plan(1, 0.05, 2, 0.90, "pocock", (0.75, 1.0))
    assert early["bounds"][0] > late["bounds"][0]
    # 늦게 볼수록 조기중단 확률이 크다
    assert early["stop_prob_h1"][0] < late["stop_prob_h1"][0]
    for seq in (early, late):
        assert abs(seq["achieved_alpha"] - 0.05) < 1e-6


def test_one_sided_and_two_sided_bounds_differ():
    one = sequential_plan(1, 0.025, 1, 0.90, "obf")
    two = sequential_plan(1, 0.05, 2, 0.90, "obf")
    # 단측 0.025와 양측 0.05는 같은 최종 임계값을 주지만 소비함수 모양이 달라
    # 경계가 완전히 같지는 않다 — 둘 다 자기일관적이면 된다
    assert abs(one["achieved_alpha"] - 0.025) < 1e-6
    assert abs(two["achieved_alpha"] - 0.05) < 1e-6
    assert one["bounds"][-1] > 1.9


def test_grid_resolution_is_converged():
    """격자를 4배로 늘려도 경계·이동모수가 1e-6 이내로 같아야 한다."""
    timing = (0.5, 1.0)
    coarse = S._solve_bounds(timing, 0.05, "obf", True, npts=S._GRID)
    fine = S._solve_bounds(timing, 0.05, "obf", True, npts=321)
    assert all(abs(a - b) < 1e-7 for a, b in zip(coarse, fine))
    d1 = S._drift_for_power(fine, timing, 0.9, True, npts=S._GRID)
    d2 = S._drift_for_power(fine, timing, 0.9, True, npts=321)
    # 표본수 팽창계수는 이동모수의 제곱비 — 상대오차 1e-6이면 100만 명에서 1명 미만
    assert abs(d1 - d2) / d2 < 1e-6


def test_plan_is_cached_and_immutable_enough():
    a = sequential_plan(2, 0.05, 2, 0.90, "obf")
    b = sequential_plan(2, 0.05, 2, 0.90, "obf")
    assert a is b            # lru_cache
    assert isinstance(a["bounds"], tuple)
