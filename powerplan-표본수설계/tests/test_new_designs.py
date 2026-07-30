"""새로 추가한 설계들 — 수식을 **테스트 안에서 독립적으로 다시 계산**해 대조한다.

여기서 쓰는 오라클:

- 생존분석  : Schoenfeld 사건 수 공식과 지수 생존모형 사건확률을 수치적분으로 재유도
- 반복측정  : 복합대칭 공분산행렬에서 ANCOVA 잔차분산을 몬테카를로로 재추정
- McNemar   : Connor(1987) 표본수 공식을 직접 계산
- 비율 NI/동등 : 정규근사 표본수 공식을 직접 계산
- kappa     : Fleiss·Cohen·Everitt(1969)의 일반 델타법 분산식을 2×2에 직접 대입
"""

from __future__ import annotations

import math
import random

import pytest

from powerplan.designs import (
    EquivalenceProportions,
    LogRankSurvival,
    McNemarPaired,
    NonInferiorityProportions,
    RepeatedMeasuresT,
    TwoSampleT,
    exponential_event_prob,
)
from powerplan.precision import kappa_ci_width, kappa_plan, kappa_variance_unit
from powerplan.solve import smallest_unit
from powerplan.special import norm_cdf, norm_ppf
from powerplan.validate import PowerPlanError

Z975 = norm_ppf(0.975)
Z95 = norm_ppf(0.95)
Z9875 = norm_ppf(0.9875)
Z80 = norm_ppf(0.80)
Z90 = norm_ppf(0.90)


# ==========================================================================
# 생존분석 (로그순위)
# ==========================================================================
def _event_prob_by_quadrature(median, accrual, followup, panels=200_000):
    """지수 생존 + 균등 등록의 사건확률을 사다리꼴 적분으로 독립 계산."""
    lam = math.log(2.0) / median
    if accrual <= 0:
        return 1.0 - math.exp(-lam * followup)
    step = accrual / panels
    acc = 0.0
    for i in range(panels + 1):
        a = i * step
        w = 0.5 if i in (0, panels) else 1.0
        acc += w * math.exp(-lam * (accrual + followup - a))
    return 1.0 - acc * step / accrual


@pytest.mark.parametrize("median, accrual, followup", [
    (12.0, 18.0, 12.0), (24.0, 0.0, 36.0), (6.0, 6.0, 0.0), (100.0, 12.0, 6.0),
])
def test_exponential_event_prob_matches_quadrature(median, accrual, followup):
    got = exponential_event_prob(median, accrual, followup)
    want = _event_prob_by_quadrature(median, accrual, followup)
    assert abs(got - want) < 1e-8
    assert 0.0 < got < 1.0


def test_exponential_event_prob_edge_cases():
    # 추적기간 0 + 등록기간 0 → 아무도 추적되지 않는다
    assert exponential_event_prob(12.0, 0.0, 0.0) == 0.0
    # 아주 긴 추적 → 거의 모두 사건
    assert exponential_event_prob(1.0, 0.0, 100.0) > 0.999
    with pytest.raises(PowerPlanError):
        exponential_event_prob(0.0, 1.0, 1.0)
    with pytest.raises(PowerPlanError):
        exponential_event_prob(12.0, -1.0, 1.0)


@pytest.mark.parametrize("hr, power, expected_events", [
    (0.7, 0.80, 247), (0.7, 0.90, 331), (0.5, 0.80, 66), (0.75, 0.80, 380),
])
def test_schoenfeld_required_events(hr, power, expected_events):
    """E = (z_{1−α/2} + z_{1−β})² / (π₁π₂ ln²HR) — 손으로 계산한 값과 대조."""
    design = LogRankSurvival(hr, 0.05, 2, 1.0, event_rate=1.0)
    want = (Z975 + norm_ppf(power)) ** 2 / (0.25 * math.log(hr) ** 2)
    assert abs(design.required_events(power) - want) < 1e-9
    assert math.ceil(want - 1e-9) == expected_events


def test_survival_n_delivers_required_events():
    """구한 표본수의 기대 사건 수가 Schoenfeld 필요 사건 수 이상이어야 한다."""
    design = LogRankSurvival(0.7, 0.05, 2, 1.0, median1=12.0, accrual=18.0, followup=12.0)
    n1 = smallest_unit(design, 0.80)
    events = design.events_for(n1)
    assert events >= design.required_events(0.80) - 1.0
    assert events < design.required_events(0.80) + 3.0


def test_survival_event_rate_one_makes_n_equal_events():
    """--event-rate 1 이면 '총 N = 필요 사건 수'라 사건 수만 알고 싶을 때 쓸 수 있다."""
    design = LogRankSurvival(0.7, 0.05, 2, 1.0, event_rate=1.0)
    n1 = smallest_unit(design, 0.80)
    assert n1 * 2 == 248            # 247건 → 군당 124명(짝수로 올림)
    assert abs(design.events_for(n1) - 248) < 1e-9


def test_survival_power_formula_independent():
    design = LogRankSurvival(0.65, 0.05, 2, 2.0, event_rate=0.4)
    n1 = 150.0
    # --event-rate는 대조군 비율 → 중재군은 비례위험으로 1 − (1−p)^HR
    p2 = 1.0 - (1.0 - 0.4) ** 0.65
    events = n1 * 0.4 + 2.0 * n1 * p2
    pi1, pi2 = 1 / 3, 2 / 3
    z = abs(math.log(0.65)) * math.sqrt(events * pi1 * pi2)
    want = norm_cdf(z - Z975) + norm_cdf(-z - Z975)
    assert abs(design.power(n1) - want) < 1e-12
    assert design.prob2 == pytest.approx(p2)


def test_survival_hr_below_one_lengthens_treatment_median():
    design = LogRankSurvival(0.6, median1=12.0, accrual=12.0, followup=12.0)
    assert design.median2 == pytest.approx(12.0 / 0.6)
    assert design.median2 > design.median1


def test_survival_hr_below_one_gives_lower_event_probability():
    design = LogRankSurvival(0.6, median1=12.0, accrual=12.0, followup=12.0)
    assert design.prob2 < design.prob1
    assert design.prob_event_pooled == pytest.approx((design.prob1 + design.prob2) / 2)


def test_survival_hr_equivalent_directions_need_same_events():
    """HR과 1/HR은 같은 사건 수를 요구한다 (log HR의 절댓값만 쓴다)."""
    a = LogRankSurvival(0.7, event_rate=1.0)
    b = LogRankSurvival(1 / 0.7, event_rate=1.0)
    assert abs(a.required_events(0.8) - b.required_events(0.8)) < 1e-9


def test_survival_null_effect_gives_alpha():
    design = LogRankSurvival(1.0 + 1e-9, 0.05, 2, 1.0, event_rate=1.0)
    assert abs(design.power(1000) - 0.05) < 1e-6


def test_survival_longer_followup_reduces_n():
    short = LogRankSurvival(0.7, median1=12.0, accrual=12.0, followup=6.0)
    long_ = LogRankSurvival(0.7, median1=12.0, accrual=12.0, followup=36.0)
    assert smallest_unit(long_, 0.8) < smallest_unit(short, 0.8)


def test_survival_unequal_allocation_needs_more_events():
    equal = LogRankSurvival(0.7, ratio=1.0, event_rate=1.0)
    lopsided = LogRankSurvival(0.7, ratio=3.0, event_rate=1.0)
    assert lopsided.required_events(0.8) > equal.required_events(0.8)


def test_survival_scaled_scales_log_hr():
    design = LogRankSurvival(0.7, event_rate=0.5)
    weaker = design.scaled(0.8)
    assert abs(math.log(weaker.hr) - 0.8 * math.log(0.7)) < 1e-12
    assert weaker.event_rate == 0.5


def test_survival_power_is_monotone_in_n():
    design = LogRankSurvival(0.7, median1=12.0, accrual=12.0, followup=12.0)
    powers = [design.power(n) for n in (10, 50, 100, 300, 1000)]
    assert all(a < b for a, b in zip(powers, powers[1:]))


@pytest.mark.parametrize("kwargs, match", [
    ({"hr": 1.0, "event_rate": 0.5}, "1이면"),
    ({"hr": 0.0, "event_rate": 0.5}, "hr"),
    ({"hr": 0.7}, "median1"),
    ({"hr": 0.7, "median1": 12.0, "event_rate": 0.5}, "하나"),
    ({"hr": 0.7, "event_rate": 0.0}, "event-rate"),
    ({"hr": 0.7, "event_rate": 1.5}, "event-rate"),
    ({"hr": 0.7, "median1": -1.0, "followup": 3.0}, "median1"),
    ({"hr": 0.7, "median1": 12.0}, "추적"),
])
def test_survival_rejects_bad_input(kwargs, match):
    with pytest.raises(PowerPlanError, match=match):
        LogRankSurvival(**kwargs)


# ==========================================================================
# 반복측정 (Frison & Pocock)
# ==========================================================================
def _simulate_design_factor(post, baseline, rho, analysis, trials=60_000, seed=5):
    """복합대칭 자료를 직접 생성해 관심 추정량의 분산을 재는 독립 오라클.

    X_ij = √ρ·u_i + √(1−ρ)·e_ij 로 만들면 모든 쌍의 상관이 정확히 ρ가 된다.
    ANCOVA는 모집단 회귀계수 β = Cov(P̄,B̄)/Var(B̄)를 쓴 조정 추정량의 분산으로 본다.
    """
    rng = random.Random(seed)
    a, b = math.sqrt(rho), math.sqrt(1.0 - rho)
    post_vals, base_vals = [], []
    for _ in range(trials):
        u = rng.gauss(0.0, 1.0)
        p = sum(a * u + b * rng.gauss(0.0, 1.0) for _ in range(post)) / post
        post_vals.append(p)
        if baseline:
            q = sum(a * u + b * rng.gauss(0.0, 1.0) for _ in range(baseline)) / baseline
            base_vals.append(q)
    mean_p = sum(post_vals) / trials
    var_p = sum((x - mean_p) ** 2 for x in post_vals) / (trials - 1)
    if analysis == "post":
        return var_p
    mean_b = sum(base_vals) / trials
    var_b = sum((x - mean_b) ** 2 for x in base_vals) / (trials - 1)
    cov = sum((x - mean_p) * (y - mean_b)
              for x, y in zip(post_vals, base_vals)) / (trials - 1)
    if analysis == "change":
        return var_p + var_b - 2.0 * cov
    beta = cov / var_b
    return var_p - beta * beta * var_b


@pytest.mark.parametrize("post, baseline, rho, analysis", [
    (3, 1, 0.6, "post"), (3, 1, 0.6, "change"), (3, 1, 0.6, "ancova"),
    (4, 2, 0.5, "ancova"), (2, 3, 0.7, "change"), (1, 1, 0.8, "ancova"),
])
def test_design_factor_matches_simulation_average(post, baseline, rho, analysis):
    """--estimand average: 관심 추정량은 사후 p회의 평균."""
    design = RepeatedMeasuresT(0.5, post, baseline, rho, analysis, estimand="average")
    want = _simulate_design_factor(post, baseline, rho, analysis)
    assert abs(design.design_factor - want) < 0.02 * max(want, 0.05)


@pytest.mark.parametrize("post, baseline, rho, analysis", [
    (3, 1, 0.6, "post"), (3, 1, 0.6, "change"), (3, 1, 0.6, "ancova"),
    (4, 2, 0.5, "ancova"), (2, 3, 0.7, "change"),
])
def test_design_factor_matches_simulation_last(post, baseline, rho, analysis):
    """--estimand last(기본): 관심 추정량은 **마지막 방문 한 시점**의 차이.

    복합대칭·완전자료에서는 사후 측정을 몇 번 하든 마지막 시점의 분산이 σ² 그대로다.
    시뮬레이션에서도 post=1로 두고 잰 값과 같아야 한다.
    """
    design = RepeatedMeasuresT(0.5, post, baseline, rho, analysis, estimand="last")
    want = _simulate_design_factor(1, baseline, rho, analysis)
    assert abs(design.design_factor - want) < 0.02 * max(want, 0.05)


def test_last_visit_estimand_ignores_number_of_post_visits():
    """마지막 방문 기준이면 사후 방문 수는 분산 배율을 바꾸지 않는다."""
    factors = {RepeatedMeasuresT(0.4, p, 1, 0.6, "ancova", estimand="last").design_factor
               for p in (1, 2, 5, 20)}
    assert len(factors) == 1


def test_average_estimand_needs_fewer_than_last():
    """사후 평균을 1차 평가변수로 삼으면 표본수가 준다 — 그래서 기본값이 아니다."""
    last = RepeatedMeasuresT(0.4, 3, 1, 0.6, "ancova", estimand="last")
    avg = RepeatedMeasuresT(0.4, 3, 1, 0.6, "ancova", estimand="average")
    assert avg.design_factor < last.design_factor
    assert smallest_unit(avg, 0.8) < smallest_unit(last, 0.8)
    assert "mean of the post-baseline" in avg.test_en
    assert "final visit" in last.test_en


@pytest.mark.parametrize("p, b, r", [(3, 2, 0.6), (4, 1, 0.3), (2, 3, 0.75), (1, 1, 0.5)])
def test_design_factor_closed_forms(p, b, r):
    """Frison & Pocock(1992)의 공표 공식을 그대로 다시 적어 대조."""
    base_var = (1 + (b - 1) * r) / b
    for estimand, post_var in (("average", (1 + (p - 1) * r) / p), ("last", 1.0)):
        kw = {"estimand": estimand}
        assert RepeatedMeasuresT(0.4, p, b, r, "post", **kw).design_factor == pytest.approx(
            post_var)
        assert RepeatedMeasuresT(0.4, p, b, r, "change", **kw).design_factor == pytest.approx(
            post_var + base_var - 2 * r)
        assert RepeatedMeasuresT(0.4, p, b, r, "ancova", **kw).design_factor == pytest.approx(
            post_var - r * r * b / (1 + (b - 1) * r))


def test_repeated_last_visit_matches_ttest2_ancova():
    """마지막 방문 기준 ANCOVA는 사후 측정이 몇 번이든 ttest2 --analysis ancova와 같다."""
    rho = 0.6
    for post in (1, 3, 8):
        rep = RepeatedMeasuresT(0.4, post, 1, rho, "ancova", 0.05, 2, 1.0, "last")
        old = TwoSampleT(0.4, 0.05, 2, 1.0, rho, "ancova")
        assert rep.design_factor == pytest.approx(old.design_factor)
        assert smallest_unit(rep, 0.8) == smallest_unit(old, 0.8)


def test_repeated_reduces_to_ttest2_ancova_when_single_measurements():
    """사후 1회·사전 1회 ANCOVA는 ttest2 --analysis ancova와 정확히 같아야 한다."""
    rho = 0.7
    rep = RepeatedMeasuresT(0.4, 1, 1, rho, "ancova", 0.05, 2, 1.0)
    old = TwoSampleT(0.4, 0.05, 2, 1.0, rho, "ancova")
    assert rep.design_factor == pytest.approx(old.design_factor)
    assert smallest_unit(rep, 0.8) == smallest_unit(old, 0.8)
    assert rep.power(40) == pytest.approx(old.power(40))


def test_repeated_reduces_to_plain_ttest_when_rho_zero_and_single_post():
    rep = RepeatedMeasuresT(0.5, 1, 1, 0.0, "post")
    plain = TwoSampleT(0.5, 0.05, 2, 1.0)
    assert rep.design_factor == pytest.approx(1.0)
    assert smallest_unit(rep, 0.8) == smallest_unit(plain, 0.8)


def test_ancova_never_worse_than_change_or_post():
    for p in (1, 2, 4):
        for b in (1, 3):
            for rho in (0.1, 0.4, 0.8):
                a = RepeatedMeasuresT(0.4, p, b, rho, "ancova").design_factor
                c = RepeatedMeasuresT(0.4, p, b, rho, "change").design_factor
                o = RepeatedMeasuresT(0.4, p, b, rho, "post").design_factor
                assert a <= c + 1e-12
                assert a <= o + 1e-12


def test_more_post_measurements_reduce_n():
    ns = [smallest_unit(RepeatedMeasuresT(0.4, p, 1, 0.6, "ancova", estimand="average"), 0.8)
          for p in (1, 2, 4, 8)]
    assert all(a >= b for a, b in zip(ns, ns[1:]))
    assert ns[0] > ns[-1]


def test_repeated_uses_one_covariate_degree_of_freedom():
    design = RepeatedMeasuresT(0.5, 2, 1, 0.5, "ancova")
    plain = RepeatedMeasuresT(0.5, 2, 1, 0.5, "post")
    # 같은 배율이라면 ANCOVA가 자유도를 1 잃으므로 검정력이 아주 조금 낮다
    same = RepeatedMeasuresT(0.5, 2, 0, 0.0, "post")
    assert design.design_factor < plain.design_factor
    assert same.power(30) > 0.0


@pytest.mark.parametrize("kwargs, match", [
    ({"d": 0.0, "post": 2, "baseline": 1, "rho": 0.5}, "0이 아닌"),
    ({"d": 0.4, "post": 0, "baseline": 1, "rho": 0.5}, "post"),
    ({"d": 0.4, "post": 2, "baseline": 0, "rho": 0.5, "analysis": "ancova"}, "사전 측정"),
    ({"d": 0.4, "post": 2, "baseline": 1, "rho": 1.0}, "rho"),
    ({"d": 0.4, "post": 2, "baseline": 1, "rho": -0.2}, "rho"),
    ({"d": 0.4, "post": 2, "baseline": 1, "rho": 0.5, "analysis": "mmrm"}, "analysis"),
    ({"d": 0.4, "post": 2000, "baseline": 1, "rho": 0.5}, "1000"),
])
def test_repeated_rejects_bad_input(kwargs, match):
    with pytest.raises(PowerPlanError, match=match):
        RepeatedMeasuresT(**kwargs)


def test_repeated_plan_lines_count_measurements():
    design = RepeatedMeasuresT(0.4, 3, 1, 0.6, "ancova")
    lines = dict(design.plan_lines({"n1": 20, "n2": 20, "total": 40}))
    assert "160" in lines["총 측정 횟수"]        # (3+1)회 × 40명
    assert "마지막 방문" in lines["1차 평가 시점"]


# ==========================================================================
# McNemar (대응 비율)
# ==========================================================================
def _connor_n(p01, p10, alpha, sides, power):
    psi = p10 / p01
    pd = p01 + p10
    z_a = norm_ppf(1.0 - alpha / sides)
    z_b = norm_ppf(power)
    num = (z_a * (psi + 1.0) + z_b * math.sqrt((psi + 1.0) ** 2 - (psi - 1.0) ** 2 * pd)) ** 2
    return num / ((psi - 1.0) ** 2 * pd)


@pytest.mark.parametrize("p01, p10, power", [
    (0.05, 0.15, 0.80), (0.10, 0.20, 0.90), (0.02, 0.08, 0.80), (0.20, 0.30, 0.80),
])
def test_mcnemar_matches_connor_formula(p01, p10, power):
    design = McNemarPaired(p01, p10, 0.05, 2)
    want = math.ceil(_connor_n(p01, p10, 0.05, 2, power) - 1e-9)
    assert abs(smallest_unit(design, power) - want) <= 1


def test_mcnemar_power_inverts_connor():
    design = McNemarPaired(0.05, 0.15, 0.05, 2)
    n = _connor_n(0.05, 0.15, 0.05, 2, 0.80)
    assert abs(design.power(n) - 0.80) < 1e-9


def test_mcnemar_null_effect_gives_half_alpha():
    """불일치 오즈비가 1에 수렴하면 (방향성 있는) 검정력은 α/2로 간다."""
    design = McNemarPaired(0.10, 0.10 + 1e-9, 0.05, 2)
    assert abs(design.power(10_000) - 0.025) < 1e-4


def test_mcnemar_more_discordant_pairs_reduce_n():
    """같은 오즈비라면 불일치 쌍이 많을수록 표본수가 준다."""
    few = McNemarPaired(0.02, 0.06, 0.05, 2)      # ψ=3, π_d=0.08
    many = McNemarPaired(0.10, 0.30, 0.05, 2)     # ψ=3, π_d=0.40
    assert abs(few.odds_ratio - many.odds_ratio) < 1e-12
    assert smallest_unit(many, 0.8) < smallest_unit(few, 0.8)


def test_mcnemar_scaled_keeps_discordant_total():
    design = McNemarPaired(0.05, 0.15, 0.05, 2)
    weaker = design.scaled(0.5)
    assert weaker.discordant == pytest.approx(design.discordant)
    assert weaker.odds_ratio == pytest.approx(design.odds_ratio ** 0.5)


def test_mcnemar_power_is_monotone():
    design = McNemarPaired(0.05, 0.15, 0.05, 2)
    powers = [design.power(n) for n in (10, 50, 100, 200, 500)]
    assert all(a < b for a, b in zip(powers, powers[1:]))


@pytest.mark.parametrize("kwargs, match", [
    ({"p01": 0.0, "p10": 0.2}, "p01"),
    ({"p01": 0.6, "p10": 0.6}, "1보다"),
    ({"p01": 0.1, "p10": 0.1}, "같으면"),
    ({"p01": 0.1, "p10": 1.2}, "p10"),
])
def test_mcnemar_rejects_bad_input(kwargs, match):
    with pytest.raises(PowerPlanError, match=match):
        McNemarPaired(**kwargs)


def test_mcnemar_one_sided_needs_fewer():
    two = McNemarPaired(0.05, 0.15, 0.05, 2)
    one = McNemarPaired(0.05, 0.15, 0.05, 1)
    assert smallest_unit(one, 0.8) < smallest_unit(two, 0.8)


# ==========================================================================
# 이분형 비열등성 / 동등성
# ==========================================================================
def test_noninf_proportions_matches_closed_form():
    """n = (z_{1−α} + z_{1−β})²·[p₁q₁ + p₂q₂] / gap²."""
    p1, p2, margin = 0.70, 0.70, 0.10
    design = NonInferiorityProportions(p1, p2, margin, 0.025, 1.0)
    want = ((Z975 + Z80) ** 2 * (p1 * (1 - p1) + p2 * (1 - p2)) / margin ** 2)
    assert abs(smallest_unit(design, 0.80) - math.ceil(want - 1e-9)) <= 1


def test_noninf_proportions_uses_assumed_difference():
    """중재가 실제로 더 좋으면(양의 차이) 여유가 늘어 표본수가 준다."""
    same = NonInferiorityProportions(0.70, 0.70, 0.10, 0.025)
    better = NonInferiorityProportions(0.70, 0.75, 0.10, 0.025)
    worse = NonInferiorityProportions(0.70, 0.65, 0.10, 0.025)
    assert smallest_unit(better, 0.8) < smallest_unit(same, 0.8) < smallest_unit(worse, 0.8)


def test_noninf_proportions_lower_is_better_flips_direction():
    """사망률처럼 낮을수록 좋은 지표에서는 부호가 반대로 작동해야 한다."""
    higher = NonInferiorityProportions(0.20, 0.25, 0.10, 0.025, 1.0, lower_is_better=True)
    lower = NonInferiorityProportions(0.20, 0.15, 0.10, 0.025, 1.0, lower_is_better=True)
    assert smallest_unit(lower, 0.8) < smallest_unit(higher, 0.8)
    # 같은 입력을 '높을수록 좋은'으로 읽으면 정반대가 된다
    flipped = NonInferiorityProportions(0.20, 0.25, 0.10, 0.025, 1.0, lower_is_better=False)
    assert smallest_unit(flipped, 0.8) < smallest_unit(higher, 0.8)


def test_noninf_proportions_null_gap_gives_alpha():
    design = NonInferiorityProportions(0.50, 0.40 + 1e-9, 0.10, 0.025)
    assert abs(design.power(100_000) - 0.025) < 1e-3


def test_noninf_proportions_rejects_impossible():
    with pytest.raises(PowerPlanError, match="넘어섰습니다"):
        NonInferiorityProportions(0.70, 0.55, 0.10, 0.025)
    with pytest.raises(PowerPlanError, match="margin"):
        NonInferiorityProportions(0.70, 0.70, 1.5, 0.025)
    with pytest.raises(PowerPlanError, match="margin"):
        NonInferiorityProportions(0.70, 0.70, 0.0, 0.025)


def test_equiv_proportions_matches_tost_definition():
    p1, p2, margin = 0.60, 0.62, 0.10
    design = EquivalenceProportions(p1, p2, margin, 0.05, 1.0)
    n = 481.0
    se = math.sqrt(p1 * (1 - p1) / n + p2 * (1 - p2) / n)
    want = (norm_cdf((margin - (p2 - p1)) / se - Z95)
            + norm_cdf((margin + (p2 - p1)) / se - Z95) - 1.0)
    assert abs(design.power_of_allocation({"n1": 481, "n2": 481}) - want) < 1e-12
    assert abs(want - 0.80) < 0.01


def test_equiv_proportions_is_more_demanding_than_noninf():
    ni = NonInferiorityProportions(0.60, 0.60, 0.10, 0.05, 1.0)
    eq = EquivalenceProportions(0.60, 0.60, 0.10, 0.05, 1.0)
    assert smallest_unit(eq, 0.8) >= smallest_unit(ni, 0.8)


def test_equiv_proportions_rejects_difference_outside_margin():
    with pytest.raises(PowerPlanError, match="밖입니다"):
        EquivalenceProportions(0.60, 0.75, 0.10, 0.05)


def test_binary_ni_ratio_affects_se_correctly():
    """배분비 1:2에서의 검정력이 정수 배분 계산과 일치해야 한다."""
    design = NonInferiorityProportions(0.7, 0.7, 0.1, 0.025, 2.0)
    n1 = 200
    se = math.sqrt(0.7 * 0.3 / n1 + 0.7 * 0.3 / (2 * n1))
    want = norm_cdf(0.1 / se - Z975)
    assert abs(design.power(n1) - want) < 1e-12
    assert abs(design.power_of_allocation({"n1": n1, "n2": 2 * n1}) - want) < 1e-12


@pytest.mark.parametrize("design, ns", [
    (NonInferiorityProportions(0.7, 0.7, 0.1, 0.025), (10, 50, 100, 300, 1000)),
    (EquivalenceProportions(0.6, 0.6, 0.1, 0.05), (150, 200, 300, 400, 600)),
])
def test_binary_designs_are_monotone(design, ns):
    powers = [design.power(n) for n in ns]
    assert all(a < b for a, b in zip(powers, powers[1:]))
    assert all(0.0 <= p <= 1.0 for p in powers)


def test_equivalence_power_is_zero_when_hopelessly_small():
    """TOST는 표본이 너무 작으면 두 단측검정이 동시에 성립할 수 없어 검정력이 0이다."""
    design = EquivalenceProportions(0.6, 0.6, 0.1, 0.05)
    assert design.power(10) == 0.0
    assert design.power(50) == 0.0


def test_binary_scaled_changes_margin():
    ni = NonInferiorityProportions(0.7, 0.7, 0.1, 0.025)
    assert ni.scaled(1.2).margin == pytest.approx(0.12)
    with pytest.raises(PowerPlanError):
        ni.scaled(20.0)              # 마진 2.0 → 비율차로 불가능


# ==========================================================================
# kappa (범주형 일치도, 정밀도 기준)
# ==========================================================================
def _fleiss_kappa_variance(kappa, prevalence):
    """Fleiss·Cohen·Everitt(1969)의 일반 델타법 분산을 2×2에 직접 대입한 독립 계산."""
    pi = prevalence
    p11 = pi * pi + kappa * pi * (1 - pi)
    p00 = (1 - pi) ** 2 + kappa * pi * (1 - pi)
    p10 = p01 = (1 - kappa) * pi * (1 - pi)
    rows = [p11 + p10, p01 + p00]
    cols = [p11 + p01, p10 + p00]
    pe = rows[0] * cols[0] + rows[1] * cols[1]
    po = p11 + p00
    k = (po - pe) / (1 - pe)
    cells = [[p11, p10], [p01, p00]]
    a = sum(cells[i][i] * (1 - (rows[i] + cols[i]) * (1 - k)) ** 2 for i in range(2))
    b = (1 - k) ** 2 * sum(cells[i][j] * (cols[i] + rows[j]) ** 2
                           for i in range(2) for j in range(2) if i != j)
    c = (k - pe * (1 - k)) ** 2
    return (a + b - c) / (1 - pe) ** 2


@pytest.mark.parametrize("kappa", [0.3, 0.5, 0.7, 0.8, 0.95])
@pytest.mark.parametrize("prevalence", [0.05, 0.2, 0.5, 0.8])
def test_kappa_variance_matches_fleiss(kappa, prevalence):
    got = kappa_variance_unit(kappa, prevalence)
    want = _fleiss_kappa_variance(kappa, prevalence)
    assert abs(got - want) < 1e-12 * max(1.0, abs(want))
    assert got > 0.0


def test_kappa_plan_matches_closed_form():
    plan = kappa_plan(0.7, 0.2, 0.5, 0.05)
    want = 4 * Z975 ** 2 * _fleiss_kappa_variance(0.7, 0.5) / 0.04
    assert plan["n"] == math.ceil(want - 1e-9)
    assert abs(plan["achieved_width"] - 0.2) < 0.002


def test_kappa_achieved_width_meets_target():
    for kappa in (0.4, 0.6, 0.85):
        for prev in (0.1, 0.3, 0.5):
            for width in (0.1, 0.2, 0.3):
                plan = kappa_plan(kappa, width, prev)
                assert plan["achieved_width"] <= width + 1e-12
                assert kappa_ci_width(plan["n"] - 1, kappa, prev) > width - 1e-9


def test_kappa_rare_categories_need_more_subjects():
    balanced = kappa_plan(0.7, 0.2, 0.50)["n"]
    rare = kappa_plan(0.7, 0.2, 0.05)["n"]
    assert rare > 5 * balanced


def test_kappa_higher_agreement_needs_fewer_subjects():
    ns = [kappa_plan(k, 0.2, 0.5)["n"] for k in (0.4, 0.6, 0.8, 0.9)]
    assert all(a > b for a, b in zip(ns, ns[1:]))


def test_kappa_expected_ci_brackets_kappa():
    plan = kappa_plan(0.75, 0.2, 0.4)
    lo, hi = plan["expected_ci"]
    assert lo < 0.75 < hi
    assert hi - lo == pytest.approx(plan["achieved_width"], abs=1e-9)


def test_kappa_plan_notes_flag_multicategory_limitation():
    notes = " ".join(kappa_plan(0.7, 0.2)["notes"])
    assert "3범주" in notes and "이분형" in notes


@pytest.mark.parametrize("kwargs", [
    {"kappa": 0.0, "width": 0.2}, {"kappa": 1.0, "width": 0.2},
    {"kappa": 0.7, "width": 0.0}, {"kappa": 0.7, "width": -1.0},
    {"kappa": 0.7, "width": 2.5}, {"kappa": 0.7, "width": 0.2, "prevalence": 0.0},
    {"kappa": 0.7, "width": 0.2, "prevalence": 1.0},
    {"kappa": 0.7, "width": 0.2, "alpha": 0.0},
    {"kappa": 0.7, "width": 1e-9},
])
def test_kappa_plan_rejects_bad_input(kwargs):
    with pytest.raises(PowerPlanError):
        kappa_plan(**kwargs)


def test_kappa_ci_width_rejects_tiny_n():
    with pytest.raises(PowerPlanError):
        kappa_ci_width(1, 0.7, 0.5)
