"""단일군 비율(prop1)·2×2 교차설계(crossover)·진단정확도(diag)와 정밀도 역방향 모드.

세 설계 모두 "임상 현장에서 가장 흔한데 이 툴에 없던" 것들이라 오라클을 따로 세운다:

- prop1 : 이항분포를 직접 열거해 정확검정의 크기·검정력을 재계산
- crossover : 대응표본 t 검정과의 관계(σ_diff = σ_w√2)로 교차검증
- diag : 필요한 것이 '전체 n'이 아니라 '질환자 n'이라는 점을 유병률을 흔들어 확인
"""

from __future__ import annotations

import io
import json
import math
from contextlib import redirect_stderr, redirect_stdout

import pytest

from powerplan.cli import main
from powerplan.designs import CrossoverT, OneSampleProportion, PairedT, binomial_sf
from powerplan.precision import (
    diagnostic_plan,
    icc_plan,
    kappa_plan,
    loa_plan,
    proportion_half_width,
)
from powerplan.solve import smallest_unit
from powerplan.special import norm_ppf
from powerplan.validate import PowerPlanError

Z975 = norm_ppf(0.975)


def run(*argv):
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        code = main(list(argv))
    return code, buf.getvalue(), err.getvalue()


def run_json(*argv):
    code, out, err = run(*argv, "--format", "json")
    assert code == 0, err
    return json.loads(out)


# ==========================================================================
# binomial_sf — 나머지 계산이 전부 여기 얹혀 있다
# ==========================================================================
@pytest.mark.parametrize("n, p", [(10, 0.3), (25, 0.5), (60, 0.85), (200, 0.05)])
def test_binomial_sf_matches_direct_enumeration(n, p):
    for k in (0, 1, n // 3, n // 2, n, n + 1):
        want = math.fsum(math.comb(n, i) * p ** i * (1 - p) ** (n - i)
                         for i in range(max(k, 0), n + 1))
        assert binomial_sf(k, n, p) == pytest.approx(want, abs=1e-12)


def test_binomial_sf_edges():
    assert binomial_sf(0, 10, 0.4) == 1.0
    assert binomial_sf(11, 10, 0.4) == 0.0
    assert binomial_sf(5, 10, 1e-300) == pytest.approx(0.0)


def test_binomial_sf_is_stable_for_large_n():
    """로그 공간 누적 — n이 커도 언더플로/오버플로가 없어야 한다."""
    value = binomial_sf(5000, 10_000, 0.5)
    assert 0.49 < value < 0.51


# ==========================================================================
# prop1 — 정확 이항검정
# ==========================================================================
def test_prop1_critical_value_controls_alpha_exactly():
    """임계값에서의 실제 1종오류율이 α를 넘으면 안 된다 (정확검정의 요점)."""
    for p0 in (0.3, 0.45, 0.7):
        for n in (20, 50, 95, 200):
            design = OneSampleProportion(p0 + 0.15, p0, 0.025, 1)
            crit = design.critical_value(n)
            assert crit is not None
            assert binomial_sf(crit, n, p0) <= 0.025 + 1e-12
            # 한 명 낮추면 α를 넘어야 한다 (임계값이 최소여야 한다)
            assert binomial_sf(crit - 1, n, p0) > 0.025


def test_prop1_power_is_the_exact_binomial_tail():
    design = OneSampleProportion(0.60, 0.45, 0.025, 1)
    n = 95
    crit = design.critical_value(n)
    assert crit == 53
    assert design.power(n) == pytest.approx(binomial_sf(53, 95, 0.60))


def test_prop1_sample_size_is_stable_against_the_sawtooth():
    """정확검정의 검정력은 톱니 모양 — 고른 n 근처가 안정적으로 목표를 넘어야 한다."""
    design = OneSampleProportion(0.60, 0.45, 0.025, 1)
    n = smallest_unit(design, 0.80)
    assert all(design.power(n + k) >= 0.80 for k in range(4))


def test_prop1_needs_more_than_the_normal_approximation():
    """정확검정은 이산성 때문에 정규근사보다 보수적이다 (그 반대면 위험하다)."""
    p0, p1 = 0.45, 0.60
    approx = ((norm_ppf(0.975) * math.sqrt(p0 * (1 - p0))
               + norm_ppf(0.8) * math.sqrt(p1 * (1 - p1))) / (p1 - p0)) ** 2
    exact = smallest_unit(OneSampleProportion(p1, p0, 0.025, 1), 0.80)
    assert exact >= math.ceil(approx)
    assert exact < math.ceil(approx) * 1.3


def test_prop1_lower_is_better_direction():
    """부작용률처럼 '낮을수록 좋은' 지표도 다뤄야 한다."""
    design = OneSampleProportion(0.05, 0.15, 0.025, 1)
    n = smallest_unit(design, 0.80)
    crit = design.critical_value(n)
    assert crit is not None
    # 이 수 '이하'면 성공 — 귀무가설에서 그럴 확률이 α 이하여야 한다
    assert 1.0 - binomial_sf(crit + 1, n, 0.15) <= 0.025 + 1e-12
    assert design.power(n) >= 0.80


def test_prop1_power_grows_with_the_gap():
    ns = [smallest_unit(OneSampleProportion(0.45 + gap, 0.45, 0.025, 1), 0.8)
          for gap in (0.05, 0.10, 0.20)]
    assert ns[0] > ns[1] > ns[2]


def test_prop1_reports_the_decision_rule():
    _, out, _ = run("prop1", "--p1", "0.60", "--p0", "0.45", "--power", "0.8")
    assert "성공 판정 기준" in out and "53명 이상" in out
    assert "실제 유의수준" in out


def test_prop1_rejects_equal_proportions():
    with pytest.raises(PowerPlanError, match="같으면"):
        OneSampleProportion(0.5, 0.5)


def test_prop1_does_not_offer_interim():
    """정확 이항검정에는 α 소비함수를 쓸 수 없다 — 옵션 자체가 없어야 한다."""
    from powerplan.designs import OneSampleProportion as _OSP
    assert _OSP.supports_sequential is False
    assert "Simon" in _OSP.sequential_reason


def test_prop1_scaled_moves_p1_toward_the_goal():
    design = OneSampleProportion(0.60, 0.45, 0.025, 1)
    assert design.scaled(0.5).p1 == pytest.approx(0.525)
    assert design.scaled(0.5).p0 == 0.45


# ==========================================================================
# crossover — 대응표본과의 관계로 교차검증
# ==========================================================================
def test_crossover_matches_paired_t_with_sigma_diff():
    """σ_diff = σ_w√2 이므로 대응표본 dz = diff/(σ_w√2)와 총 N이 거의 같아야 한다."""
    diff, sd_within = 3.0, 6.0
    cross = CrossoverT(diff, sd_within)
    paired = PairedT(diff / (sd_within * math.sqrt(2.0)))
    total_cross = 2 * smallest_unit(cross, 0.8)
    total_paired = smallest_unit(paired, 0.8)
    assert abs(total_cross - total_paired) <= 2


def test_crossover_variance_is_sigma_w_squared_over_n():
    """Var(δ̂) = σ_w²/n (n = 순서당) — 비중심모수로 확인."""
    from powerplan.distributions import nct_cdf, nct_sf, t_ppf
    design = CrossoverT(2.0, 5.0)
    n = 40
    ncp = 2.0 / (5.0 / math.sqrt(n))
    df = 2 * n - 2
    tc = t_ppf(0.975, df)
    want = nct_sf(tc, df, ncp) + nct_cdf(-tc, df, ncp)
    assert design.power(n) == pytest.approx(want, abs=1e-12)


def test_crossover_beats_parallel_design():
    """같은 효과에서 교차설계가 평행설계보다 인원이 적어야 한다 (그것이 이유다)."""
    from powerplan.designs import TwoSampleT
    sd_between = 12.0
    sd_within = 6.0
    diff = 3.0
    cross_total = 2 * smallest_unit(CrossoverT(diff, sd_within), 0.8)
    parallel_total = 2 * smallest_unit(TwoSampleT(diff / sd_between), 0.8)
    assert cross_total < parallel_total


def test_crossover_allocation_and_enrollment_are_consistent():
    plan = run_json("crossover", "--diff", "3", "--sd-within", "6", "--power", "0.8",
                    "--dropout", "0.15")
    analysis = plan["analysis"]["allocation"]
    enroll = plan["enrollment"]["allocation"]
    assert analysis["total"] == 2 * analysis["n_per_sequence"]
    assert enroll["total"] == 2 * enroll["n_per_sequence"]
    assert enroll["n_per_sequence"] == math.ceil(analysis["n_per_sequence"] / 0.85)


def test_crossover_note_explains_within_subject_sd():
    _, out, _ = run("crossover", "--diff", "3", "--sd-within", "6", "--power", "0.8")
    flat = " ".join(out.split())
    assert "개인 내" in flat
    assert "8.485" in flat          # σ_diff = 6·√2
    assert "이월효과" in flat


@pytest.mark.parametrize("kwargs", [
    {"diff": 0.0, "sd_within": 5.0}, {"diff": 3.0, "sd_within": 0.0},
    {"diff": 3.0, "sd_within": -1.0},
])
def test_crossover_rejects_bad_input(kwargs):
    with pytest.raises(PowerPlanError):
        CrossoverT(**kwargs)


# ==========================================================================
# diag — 유병률 보정이 핵심
# ==========================================================================
def test_diag_wald_mode_matches_the_buderer_closed_form():
    """--method wald는 고전 Buderer 공식을 그대로 재현해야 한다."""
    plan = diagnostic_plan(0.90, 0.85, 0.20, 0.05, method="wald")
    want_disease = Z975 ** 2 * 0.9 * 0.1 / 0.05 ** 2
    assert plan["required_disease"] == pytest.approx(want_disease)
    assert plan["required_disease_wald"] == pytest.approx(want_disease)
    assert plan["n"] >= math.ceil(want_disease / 0.20 - 1e-9)
    assert plan["n"] <= math.ceil(want_disease / 0.20 - 1e-9) + 5
    assert plan["binding"] == "민감도"


def test_diag_default_uses_wilson_and_needs_more():
    """Wald 구간은 p가 1에 가까울수록 실제보다 좁다 — 기본값은 Wilson이어야 한다."""
    from powerplan.precision import wilson_half_width
    for sens in (0.90, 0.95, 0.99):
        wilson = diagnostic_plan(sens, 0.85, 0.2, 0.05)
        wald = diagnostic_plan(sens, 0.85, 0.2, 0.05, method="wald")
        assert wilson["method"] == "wilson"
        assert wilson["n"] > wald["n"]
        # Wald로 잡은 인원은 실제(Wilson) 목표를 만족하지 못한다
        assert wilson_half_width(wald["n_disease"], sens, 0.05) > 0.05
        assert wilson_half_width(wilson["n_disease"], sens, 0.05) <= 0.05 + 1e-9


def test_diag_note_does_not_claim_wald_is_conservative():
    notes = " ".join(diagnostic_plan(0.95, 0.85, 0.2, 0.05)["notes"])
    assert "좁게" in notes                      # Wald가 좁다는 올바른 방향
    assert "좁아집니다(보수적)" not in notes


def test_diag_total_scales_inversely_with_prevalence():
    """유병률이 절반이면 등록 인원은 두 배 — 이 나눗셈이 이 설계의 핵심이다."""
    a = diagnostic_plan(0.90, 0.85, 0.20, 0.05, method="wald")["n"]
    b = diagnostic_plan(0.90, 0.85, 0.10, 0.05, method="wald")["n"]
    assert b == pytest.approx(2 * a, rel=0.01)


def test_diag_specificity_can_be_the_binding_constraint():
    plan = diagnostic_plan(0.95, 0.60, 0.60, 0.05, method="wald")
    assert plan["binding"] == "특이도"
    assert plan["required_healthy"] > plan["required_disease"]


@pytest.mark.parametrize("method", ["wilson", "wald"])
def test_diag_achieved_half_width_meets_the_target(method):
    for prev in (0.05, 0.2, 0.5, 0.9):
        for sens in (0.75, 0.9, 0.97):
            plan = diagnostic_plan(sens, 0.85, prev, 0.05, method=method)
            assert plan["achieved_half_width"] <= 0.05 + 1e-9
            assert plan["achieved_half_width_spec"] <= 0.05 + 1e-9


def test_diag_case_control_numbers_are_much_smaller():
    plan = diagnostic_plan(0.90, 0.85, 0.05, 0.05, method="wald")
    case_control = plan["required_disease"] + plan["required_healthy"]
    assert case_control < plan["n"] / 5


def test_proportion_half_width_inverts_itself():
    for p in (0.5, 0.85, 0.95):
        n = Z975 ** 2 * p * (1 - p) / 0.05 ** 2
        assert proportion_half_width(n, p) == pytest.approx(0.05)


def test_diag_cli_reports_both_arms():
    _, out, _ = run("diag", "--sens", "0.9", "--spec", "0.85", "--prevalence", "0.2",
                    "--half-width", "0.05", "--method", "wald")
    flat = " ".join(out.split())
    assert "695명" in flat
    assert "질환자 / 비질환자" in flat
    assert "사례-대조" in flat


@pytest.mark.parametrize("kwargs", [
    {"sens": 0.0, "spec": 0.85, "prevalence": 0.2, "half_width": 0.05},
    {"sens": 0.9, "spec": 1.0, "prevalence": 0.2, "half_width": 0.05},
    {"sens": 0.9, "spec": 0.85, "prevalence": 0.0, "half_width": 0.05},
    {"sens": 0.9, "spec": 0.85, "prevalence": 0.2, "half_width": 0.0},
    {"sens": 0.9, "spec": 0.85, "prevalence": 0.2, "half_width": 1.5},
    {"sens": 0.9, "spec": 0.85, "prevalence": 1e-9, "half_width": 0.01},
])
def test_diag_rejects_bad_input(kwargs):
    with pytest.raises(PowerPlanError):
        diagnostic_plan(**kwargs)


# ==========================================================================
# 정밀도 설계의 역방향 모드 (--n → 폭)
# ==========================================================================
@pytest.mark.parametrize("argv, key", [
    (("icc", "--icc", "0.8", "--width", "0.15"), "achieved_width"),
    (("kappa", "--kappa", "0.7", "--width", "0.2"), "achieved_width"),
    (("loa", "--sd-diff", "2.0", "--half-width", "0.5"), "achieved_half_width"),
    (("diag", "--sens", "0.9", "--spec", "0.85", "--prevalence", "0.2",
      "--half-width", "0.05"), "achieved_half_width"),
])
def test_reverse_mode_reports_the_precision_at_a_given_n(argv, key):
    solved = run_json(*argv)
    given = run_json(*argv, "--n", "40")
    assert given["n"] == 40
    assert given["given_n"] is True
    assert solved.get("given_n") is False
    # 목표보다 적은 인원이면 폭이 더 넓어야 한다
    if solved["n"] > 40:
        assert given[key] > solved[key]
    assert "확보 가능한 40명" in " ".join(given["notes"])


def test_reverse_mode_says_whether_the_target_is_met():
    _, out, _ = run("icc", "--icc", "0.8", "--width", "0.15", "--n", "40")
    assert "미달" in out
    _, out, _ = run("icc", "--icc", "0.8", "--width", "0.15", "--n", "400")
    assert "충족" in out


def test_reverse_mode_rejects_tiny_n():
    code, _, err = run("kappa", "--kappa", "0.7", "--width", "0.2", "--n", "1")
    assert code == 2 and "--n" in err


def test_reverse_mode_keeps_the_standard_notes():
    plan = run_json("kappa", "--kappa", "0.7", "--width", "0.2", "--n", "100")
    notes = " ".join(plan["notes"])
    assert "3범주" in notes           # 원래 한계 설명이 사라지면 안 된다
    assert "확보 가능한" in notes


# ==========================================================================
# 새 설계 전체 스모크 (세 형식 + 프로토콜 문장)
# ==========================================================================
NEW = [
    ("prop1", "--p1", "0.6", "--p0", "0.45", "--power", "0.8"),
    ("prop1", "--p1", "0.05", "--p0", "0.15", "--power", "0.9", "--dropout", "0.1"),
    ("crossover", "--diff", "3", "--sd-within", "6", "--power", "0.8"),
    ("crossover", "--diff", "3", "--sd-within", "6", "--n", "30"),
    ("diag", "--sens", "0.9", "--spec", "0.85", "--prevalence", "0.2",
     "--half-width", "0.05"),
]


@pytest.mark.parametrize("argv", NEW)
@pytest.mark.parametrize("fmt", ["text", "md", "json"])
def test_new_designs_render(argv, fmt):
    code, out, err = run(*argv, "--format", fmt)
    assert code == 0, err
    assert out.strip()
    assert "None" not in out
    if fmt == "json":
        json.loads(out)


@pytest.mark.parametrize("argv", NEW)
def test_new_designs_have_sentences_and_provenance(argv):
    plan = run_json(*argv)
    assert plan["sentences"]["kr"] and plan["sentences"]["en"]
    assert plan["provenance"]["command"].startswith("powerplan " + argv[0])
