"""설계별 검정력·표본수 검증.

세 가지 방식으로 교차검증한다:

1. **공개 기준값** — G*Power 3.1 / Cohen(1988) 표의 표본수와 비교
2. **몬테카를로** — 실제로 데이터를 생성해 검정을 반복하고 기각률을 센다
   (비중심 t·TOST 적분이 옳은지 독립적으로 확인, 고정 시드로 재현 가능)
3. **손계산 근사식** — 정규근사 공식과 큰 표본에서 일치하는지 확인
"""

import math
import random

import pytest

from powerplan.designs import (
    CorrelationTest,
    EquivalenceT,
    NonInferiorityT,
    OneSampleT,
    OneWayAnova,
    PairedT,
    TwoProportions,
    TwoSampleT,
)
from powerplan.distributions import t_ppf
from powerplan.solve import smallest_unit
from powerplan.validate import PowerPlanError

# --- G*Power 3.1 (정확법) 기준: (설계, 목표검정력, 기대 단위표본수) ---
GPOWER_CASES = [
    ("두 군 d=0.5 양측 α.05", TwoSampleT(0.5), 0.80, 64),
    ("두 군 d=0.8", TwoSampleT(0.8), 0.80, 26),
    ("두 군 d=0.2", TwoSampleT(0.2), 0.80, 394),
    ("두 군 d=0.5 검정력 90%", TwoSampleT(0.5), 0.90, 86),
    ("두 군 d=0.5 검정력 95%", TwoSampleT(0.5), 0.95, 105),
    ("두 군 d=0.5 단측", TwoSampleT(0.5, sides=1), 0.80, 51),
    ("두 군 d=0.5 α=0.01", TwoSampleT(0.5, alpha=0.01), 0.80, 96),
    ("대응표본 dz=0.5", PairedT(0.5), 0.80, 34),
    ("대응표본 dz=0.3", PairedT(0.3), 0.80, 90),
    ("대응표본 dz=0.8 검정력 90%", PairedT(0.8), 0.90, 19),
    ("단일표본 d=0.5", OneSampleT(0.5), 0.80, 34),
    ("ANOVA k=3 f=0.25", OneWayAnova(0.25, 3), 0.80, 53),
    ("ANOVA k=4 f=0.40", OneWayAnova(0.40, 4), 0.80, 19),
    ("ANOVA k=3 f=0.40", OneWayAnova(0.40, 3), 0.80, 22),
    ("비율 .30 vs .50", TwoProportions(0.30, 0.50), 0.80, 93),
    ("비율 .10 vs .20", TwoProportions(0.10, 0.20), 0.80, 199),
]


@pytest.mark.parametrize("label,design,power,expected", GPOWER_CASES,
                         ids=[c[0] for c in GPOWER_CASES])
def test_sample_size_matches_published_values(label, design, power, expected):
    assert smallest_unit(design, power) == expected


def test_correlation_matches_published_values():
    """Fisher z 근사는 정확법보다 0~1명 크게 나온다 (보수적) — 그 성질을 고정."""
    exact_gpower = {0.1: 782, 0.2: 194, 0.3: 84, 0.4: 46, 0.5: 29, 0.6: 19, 0.7: 13}
    for r, exact in exact_gpower.items():
        got = smallest_unit(CorrelationTest(r), 0.80)
        assert exact <= got <= exact + 1, (r, got, exact)
    # 편향보정을 쓰면 정확법과 ±1 이내
    for r, exact in exact_gpower.items():
        got = smallest_unit(CorrelationTest(r, bias_correct=True), 0.80)
        assert abs(got - exact) <= 1, (r, got, exact)


def test_achieved_power_at_published_sample_sizes():
    """G*Power가 보고하는 실제 검정력과 소수 3자리까지 일치."""
    assert TwoSampleT(0.5).power(64) == pytest.approx(0.8015, abs=5e-4)
    assert TwoSampleT(0.8).power(26) == pytest.approx(0.8075, abs=5e-4)
    assert PairedT(0.5).power(34) == pytest.approx(0.8077, abs=5e-4)
    assert OneWayAnova(0.25, 3).power(53) == pytest.approx(0.8049, abs=5e-4)
    assert TwoProportions(0.30, 0.50).power(93) == pytest.approx(0.8000, abs=1e-3)


def test_two_sample_normal_approximation_agrees_for_large_n():
    """n이 크면 정확 검정력이 정규근사식 n = 2(z_α+z_β)²/d² 와 맞아야 한다."""
    from powerplan.special import norm_ppf
    for d, power in ((0.2, 0.80), (0.1, 0.90)):
        approx_n = 2.0 * (norm_ppf(1 - 0.025) + norm_ppf(power)) ** 2 / (d * d)
        exact_n = smallest_unit(TwoSampleT(d), power)
        assert abs(exact_n - approx_n) / approx_n < 0.02


def test_power_is_monotone_in_n_and_effect():
    design = TwoSampleT(0.5)
    powers = [design.power(n) for n in range(2, 200)]
    assert all(b >= a - 1e-12 for a, b in zip(powers, powers[1:]))
    assert design.power(50) < TwoSampleT(0.7).power(50)
    # α가 작아지면 검정력도 작아진다
    assert TwoSampleT(0.5, alpha=0.01).power(64) < TwoSampleT(0.5).power(64)
    # 단측이 양측보다 검정력이 높다
    assert TwoSampleT(0.5, sides=1).power(40) > TwoSampleT(0.5, sides=2).power(40)


def test_power_at_null_effect_equals_alpha_ish():
    """효과크기가 0에 가까우면 검정력은 α로 수렴한다 (검정의 크기 확인)."""
    tiny = TwoSampleT(1e-9)
    assert tiny.power(500) == pytest.approx(0.05, abs=1e-3)
    # 단측 검정의 크기는 α 자체 (α/2가 아니다)
    tiny_one_sided = TwoSampleT(1e-9, sides=1)
    assert tiny_one_sided.power(500) == pytest.approx(0.05, abs=1e-3)
    assert TwoSampleT(1e-9, alpha=0.01).power(500) == pytest.approx(0.01, abs=1e-3)


def test_sign_of_effect_does_not_change_power():
    for n in (10, 40, 200):
        assert TwoSampleT(-0.5).power(n) == pytest.approx(TwoSampleT(0.5).power(n), abs=1e-14)
        assert PairedT(-0.4).power(n) == pytest.approx(PairedT(0.4).power(n), abs=1e-14)
        assert CorrelationTest(-0.3).power(n) == pytest.approx(
            CorrelationTest(0.3).power(n), abs=1e-14)


def test_unequal_allocation_costs_total_n():
    """같은 검정력이면 1:1이 총 N을 최소화한다."""
    balanced = TwoSampleT(0.5)
    n1 = smallest_unit(balanced, 0.80)
    total_balanced = balanced.allocation(n1)["total"]
    for ratio in (1.5, 2.0, 3.0):
        design = TwoSampleT(0.5, ratio=ratio)
        alloc = design.allocation(smallest_unit(design, 0.80))
        assert alloc["total"] > total_balanced
        assert alloc["n2"] >= math.ceil(ratio * alloc["n1"]) - 1


def test_allocation_and_power_of_allocation_are_consistent():
    for design in (TwoSampleT(0.5, ratio=1.5), PairedT(0.4), OneWayAnova(0.3, 4),
                   TwoProportions(0.2, 0.4), CorrelationTest(0.35),
                   NonInferiorityT(3, 8), EquivalenceT(5, 8)):
        unit = smallest_unit(design, 0.80)
        alloc = design.allocation(unit)
        assert design.power_of_allocation(alloc) >= 0.80
        # 하나 줄이면 목표에 못 미쳐야 한다 (최소성)
        if unit > design.min_unit:
            assert design.power_of_allocation(design.allocation(unit - 1)) < 0.80
        assert alloc["total"] >= unit


def test_anova_two_groups_matches_two_sample_t():
    """k=2인 ANOVA는 양측 t 검정과 동일한 검정력을 준다 (f = d/2)."""
    for d in (0.4, 0.6, 1.0):
        for n in (10, 30, 80):
            anova = OneWayAnova(d / 2.0, 2).power(n)
            ttest = TwoSampleT(d).power(n)
            assert anova == pytest.approx(ttest, abs=1e-9)


def test_proportions_effect_metrics():
    design = TwoProportions(0.30, 0.50)
    effect = design.effect()
    assert effect["value"] == pytest.approx(0.20)
    assert effect["risk_ratio"] == pytest.approx(0.5 / 0.3)
    assert effect["odds_ratio"] == pytest.approx((0.5 / 0.5) / (0.3 / 0.7))
    assert effect["cohen_h"] == pytest.approx(
        2 * math.asin(math.sqrt(0.5)) - 2 * math.asin(math.sqrt(0.3)))


def test_proportions_continuity_is_conservative():
    plain = smallest_unit(TwoProportions(0.30, 0.50), 0.80)
    corrected = smallest_unit(TwoProportions(0.30, 0.50, continuity=True), 0.80)
    assert corrected > plain
    # 연속성 보정 결과도 목표 검정력을 실제로 만족해야 한다
    design = TwoProportions(0.30, 0.50, continuity=True)
    assert design.power(corrected) >= 0.80


def test_proportions_symmetric_in_group_order():
    for n in (20, 60, 200):
        assert TwoProportions(0.3, 0.5).power(n) == pytest.approx(
            TwoProportions(0.5, 0.3).power(n), abs=1e-12)


def test_noninferiority_reduces_to_normal_approximation():
    """마진/SD가 작아 n이 커지면 정규근사 n = 2(z_α+z_β)²σ²/M² 와 근접."""
    from powerplan.special import norm_ppf
    margin, sd = 1.0, 10.0
    approx = 2.0 * (norm_ppf(0.975) + norm_ppf(0.80)) ** 2 * sd ** 2 / margin ** 2
    exact = smallest_unit(NonInferiorityT(margin, sd), 0.80)
    assert abs(exact - approx) / approx < 0.01


def test_noninferiority_direction_handling():
    """낮을수록 좋은 지표에서는 diff의 부호 해석이 뒤집힌다."""
    higher = NonInferiorityT(3, 8, diff=1.0)          # 높을수록 좋음, 실제로 1 우세
    lower = NonInferiorityT(3, 8, diff=1.0, lower_is_better=True)  # 낮을수록 좋음 → 1 열세
    assert smallest_unit(higher, 0.80) < smallest_unit(lower, 0.80)
    # 실제 차이가 마진을 넘으면 입증 불가 → 명확한 오류
    with pytest.raises(PowerPlanError, match="마진"):
        NonInferiorityT(3, 8, diff=-3.5)
    with pytest.raises(PowerPlanError, match="마진"):
        NonInferiorityT(3, 8, diff=3.5, lower_is_better=True)


def test_equivalence_tost_is_symmetric_in_diff_sign():
    for n in (20, 45, 90):
        assert EquivalenceT(5, 8, 2).power(n) == pytest.approx(
            EquivalenceT(5, 8, -2).power(n), abs=1e-12)


def test_equivalence_needs_more_n_than_superiority_style_test():
    """동등성은 마진 안에 들어야 하므로 같은 마진의 비열등성보다 n이 크다."""
    equiv_n = smallest_unit(EquivalenceT(5, 8, alpha=0.05), 0.80)
    noninf_n = smallest_unit(NonInferiorityT(5, 8, alpha=0.05), 0.80)
    assert equiv_n > noninf_n


def test_equivalence_rejects_diff_outside_margin():
    with pytest.raises(PowerPlanError, match="동등성 마진"):
        EquivalenceT(5, 8, 5.0)
    with pytest.raises(PowerPlanError, match="동등성 마진"):
        EquivalenceT(5, 8, -6.0)


def test_equivalence_exact_is_below_normal_approximation_and_converges():
    """z 기반 정규근사는 σ 추정을 무시해 검정력을 과대평가한다.

    정확 TOST(t 기반) ≤ 정규근사이고, n이 커지면 둘이 수렴해야 한다.
    """
    from powerplan.special import norm_cdf, norm_ppf
    for margin, sd, n in ((5, 8, 45), (4, 6, 30), (10, 20, 60)):
        se = sd * math.sqrt(2.0 / n)
        approx = max(0.0, 2 * norm_cdf(margin / se - norm_ppf(0.95)) - 1)
        exact = EquivalenceT(margin, sd, 0.0).power(n)
        assert exact <= approx + 1e-9
        assert exact > approx - 0.05          # 그래도 크게 벗어나지 않는다
    # n이 크면 수렴
    big_n = 4000
    se = 8 * math.sqrt(2.0 / big_n)
    approx = 2 * norm_cdf(2.0 / se - norm_ppf(0.95)) - 1
    assert EquivalenceT(2, 8, 0.0).power(big_n) == pytest.approx(approx, abs=2e-3)


def test_designs_reject_invalid_parameters():
    with pytest.raises(PowerPlanError):
        TwoSampleT(0.0)
    with pytest.raises(PowerPlanError):
        TwoSampleT(float("nan"))
    with pytest.raises(PowerPlanError):
        TwoSampleT(0.5, alpha=0.0)
    with pytest.raises(PowerPlanError):
        TwoSampleT(0.5, alpha=0.6)
    with pytest.raises(PowerPlanError):
        TwoSampleT(0.5, sides=3)
    with pytest.raises(PowerPlanError):
        TwoSampleT(0.5, ratio=0.0)
    with pytest.raises(PowerPlanError):
        TwoSampleT(0.5, ratio=1000.0)
    with pytest.raises(PowerPlanError):
        TwoProportions(0.0, 0.5)
    with pytest.raises(PowerPlanError):
        TwoProportions(0.4, 0.4)
    with pytest.raises(PowerPlanError):
        TwoProportions(0.4, 1.0)
    with pytest.raises(PowerPlanError):
        OneWayAnova(0.25, 1)
    with pytest.raises(PowerPlanError):
        OneWayAnova(0.0, 3)
    with pytest.raises(PowerPlanError):
        OneWayAnova(0.25, 2.5)
    with pytest.raises(PowerPlanError):
        CorrelationTest(1.0)
    with pytest.raises(PowerPlanError):
        CorrelationTest(0.0)
    with pytest.raises(PowerPlanError):
        NonInferiorityT(-1, 8)
    with pytest.raises(PowerPlanError):
        NonInferiorityT(3, 0)
    with pytest.raises(PowerPlanError):
        EquivalenceT(3, 8, float("inf"))


def test_scaled_preserves_design_type_and_settings():
    for design in (TwoSampleT(0.5, 0.01, 1, 2.0), PairedT(0.4, 0.02, 1),
                   OneSampleT(0.4), OneWayAnova(0.3, 5, 0.01),
                   CorrelationTest(0.3, 0.05, 1, True), TwoProportions(0.2, 0.4),
                   NonInferiorityT(3, 8, 1.0, 0.025, 2.0), EquivalenceT(5, 8, 1.0)):
        bigger = design.scaled(1.5)
        assert type(bigger) is type(design)
        assert bigger.alpha == design.alpha
        # 효과가 커지면 필요한 표본수는 줄어든다
        assert smallest_unit(bigger, 0.80) <= smallest_unit(design, 0.80)


def test_minimum_units_and_degenerate_power():
    assert TwoSampleT(0.5).power(1) == 0.0     # df < 1
    assert PairedT(0.5).power(1) == 0.0
    assert CorrelationTest(0.5).power(3) == 0.0
    assert OneWayAnova(0.5, 3).power(1) == 0.0


def test_unreachable_power_raises_clear_error():
    with pytest.raises(PowerPlanError, match="검정력"):
        smallest_unit(TwoSampleT(0.5), 0.80, cap=10)
    with pytest.raises(PowerPlanError, match="0과 1 사이"):
        smallest_unit(TwoSampleT(0.5), 1.0)
    with pytest.raises(PowerPlanError):
        smallest_unit(TwoSampleT(0.5), 0.0)


# --------------------------------------------------------------------------
# 몬테카를로 교차검증 (고정 시드, 순수 표준 라이브러리)
# --------------------------------------------------------------------------
def _mc_two_sample(n, d, alpha, reps, seed):
    rng = random.Random(seed)
    df = 2 * n - 2
    se_unit = math.sqrt(2.0 / n)
    crit = t_ppf(1.0 - alpha / 2.0, df)
    hits = 0
    for _ in range(reps):
        diff = rng.gauss(d, se_unit)
        s = math.sqrt(rng.gammavariate(df / 2.0, 2.0) / df)
        if abs(diff / (s * se_unit)) > crit:
            hits += 1
    return hits / reps


def _mc_tost(n, margin, sd, diff, alpha, reps, seed):
    rng = random.Random(seed)
    df = 2 * n - 2
    se_unit = math.sqrt(2.0 / n)
    crit = t_ppf(1.0 - alpha, df)
    hits = 0
    for _ in range(reps):
        observed = rng.gauss(diff, sd * se_unit)
        s = sd * math.sqrt(rng.gammavariate(df / 2.0, 2.0) / df)
        se = s * se_unit
        if (observed + margin) / se > crit and (observed - margin) / se < -crit:
            hits += 1
    return hits / reps


@pytest.mark.parametrize("n,d", [(64, 0.5), (26, 0.8), (10, 0.9)])
def test_two_sample_power_matches_monte_carlo(n, d):
    reps = 60_000
    mc = _mc_two_sample(n, d, 0.05, reps, seed=20260730 + n)
    exact = TwoSampleT(d).power(n)
    tol = 4.0 * math.sqrt(max(mc, 1e-6) * (1 - mc) / reps)  # 4 표준오차
    assert abs(exact - mc) < tol, (exact, mc, tol)


@pytest.mark.parametrize("n,margin,sd,diff", [(45, 5, 8, 0), (30, 5, 8, 0), (60, 5, 8, 2)])
def test_tost_power_matches_monte_carlo(n, margin, sd, diff):
    reps = 60_000
    mc = _mc_tost(n, margin, sd, diff, 0.05, reps, seed=771 + n)
    exact = EquivalenceT(margin, sd, diff).power(n)
    tol = 4.0 * math.sqrt(max(mc, 1e-6) * (1 - mc) / reps)
    assert abs(exact - mc) < tol, (exact, mc, tol)


def test_two_sample_type_one_error_is_calibrated_monte_carlo():
    """효과가 0일 때 기각률이 α와 같아야 한다 (검정의 크기)."""
    reps = 60_000
    mc = _mc_two_sample(30, 0.0, 0.05, reps, seed=99)
    tol = 4.0 * math.sqrt(0.05 * 0.95 / reps)
    assert abs(mc - 0.05) < tol
