"""표본수 탐색과 현실 보정(탈락·군집·다중비교) 검증."""

import math

import pytest

from powerplan.designs import OneWayAnova, PairedT, TwoProportions, TwoSampleT
from powerplan.solve import Adjustments, MAX_UNIT, make_plan, smallest_unit
from powerplan.validate import PowerPlanError


def test_dropout_inflation_is_exact():
    """분석 n 64 → 탈락 15% → ceil(64/0.85) = 76."""
    plan = make_plan(TwoSampleT(0.5), target_power=0.80,
                     adjustments=Adjustments(dropout=0.15))
    assert plan["analysis"]["allocation"]["n1"] == 64
    assert plan["enrollment"]["allocation"]["n1"] == math.ceil(64 / 0.85) == 76
    assert plan["enrollment"]["allocation"]["total"] == 152


@pytest.mark.parametrize("dropout,expected", [(0.0, 64), (0.1, 72), (0.2, 80), (0.5, 128)])
def test_dropout_table(dropout, expected):
    plan = make_plan(TwoSampleT(0.5), target_power=0.80,
                     adjustments=Adjustments(dropout=dropout))
    assert plan["enrollment"]["allocation"]["n1"] == expected


def test_design_effect_formula_and_clusters():
    """유효 100 → ×DE 1.45 = 분석 145 → ÷0.9 = 162 → 군집 17개 = 모집 170."""
    adj = Adjustments(dropout=0.1, cluster_size=10, cluster_icc=0.05)
    assert adj.design_effect == pytest.approx(1.45)
    assert adj.inflation == pytest.approx(1.45 / 0.9)
    plan = make_plan(TwoSampleT(0.4), target_power=0.80, adjustments=adj)
    # 검정력 계산이 요구하는 '유효' 표본수 (개인배정 기준)
    assert plan["effective"]["allocation"]["n1"] == 100
    # 군집설계에서 실제로 분석해야 하는 인원 = 유효 × DE
    assert plan["analysis"]["allocation"]["n1"] == math.ceil(100 * 1.45) == 145
    # 모집 = 분석 ÷ (1 − 탈락률), 다시 군집 단위로 올림
    assert plan["enrollment"]["before_cluster_rounding"]["n1"] == math.ceil(145 / 0.9) == 162
    assert plan["enrollment"]["clusters"]["n1"] == math.ceil(162 / 10) == 17
    assert plan["enrollment"]["allocation"]["n1"] == 170
    assert plan["enrollment"]["allocation"]["total"] == 340


def test_cluster_enrollment_is_rounded_to_whole_clusters():
    """ICC가 0이어도 군집은 쪼갤 수 없으므로 모집 인원은 군집 단위로 올라간다."""
    adj = Adjustments(cluster_size=25, cluster_icc=0.0)
    assert adj.design_effect == 1.0
    plan = make_plan(TwoSampleT(0.5), target_power=0.80, adjustments=adj)
    assert plan["analysis"]["allocation"]["n1"] == 64
    assert plan["enrollment"]["clusters"]["n1"] == 3
    assert plan["enrollment"]["allocation"]["n1"] == 75      # 3 군집 × 25명
    # 군집설계가 아니면 분석 = 모집
    plain = make_plan(TwoSampleT(0.5), target_power=0.80)
    assert plain["enrollment"]["allocation"] == plain["analysis"]["allocation"]


def test_cluster_requires_both_parameters():
    with pytest.raises(PowerPlanError, match="함께"):
        Adjustments(cluster_size=10)
    with pytest.raises(PowerPlanError, match="함께"):
        Adjustments(cluster_icc=0.05)


def test_adjustment_validation():
    with pytest.raises(PowerPlanError, match="dropout"):
        Adjustments(dropout=1.0)
    with pytest.raises(PowerPlanError, match="dropout"):
        Adjustments(dropout=-0.1)
    with pytest.raises(PowerPlanError, match="dropout"):
        Adjustments(dropout=15)  # 퍼센트로 잘못 적은 경우
    with pytest.raises(PowerPlanError, match="cluster-icc"):
        Adjustments(cluster_size=5, cluster_icc=1.0)
    with pytest.raises(PowerPlanError, match="cluster-size"):
        Adjustments(cluster_size=0, cluster_icc=0.1)
    with pytest.raises(PowerPlanError, match="comparisons"):
        Adjustments(comparisons=0)
    with pytest.raises(PowerPlanError, match="alpha-method"):
        Adjustments(alpha_method="hochberg")


def test_alpha_adjustment_methods():
    adj = Adjustments(comparisons=4)
    alpha, info = adj.adjusted_alpha(0.05)
    assert alpha == pytest.approx(0.0125)
    assert info["method"] == "bonferroni"
    sidak = Adjustments(comparisons=4, alpha_method="sidak")
    alpha_s, info_s = sidak.adjusted_alpha(0.05)
    assert alpha_s == pytest.approx(1 - 0.95 ** 0.25)
    assert alpha_s > 0.0125  # Šidák이 Bonferroni보다 덜 보수적
    assert info_s["method"] == "sidak"
    # 보정 없음
    assert Adjustments(comparisons=1).adjusted_alpha(0.05) == (0.05, None)
    assert Adjustments(comparisons=5, alpha_method="none").adjusted_alpha(0.05) == (0.05, None)


def test_alpha_adjustment_increases_sample_size():
    plain = smallest_unit(TwoSampleT(0.5, alpha=0.05), 0.80)
    adjusted = smallest_unit(TwoSampleT(0.5, alpha=0.05 / 3), 0.80)
    assert adjusted > plain


def test_compute_power_direction_accounts_for_dropout_and_clusters():
    plan = make_plan(TwoSampleT(0.5), unit=100, adjustments=Adjustments(dropout=0.2))
    # 모집 100명 중 80명만 분석 → 유효 n = 80
    assert plan["given"]["effective_unit"] == pytest.approx(80.0)
    assert plan["achieved_power"] == pytest.approx(TwoSampleT(0.5).power(80.0), abs=1e-12)
    clustered = make_plan(TwoSampleT(0.5), unit=145,
                          adjustments=Adjustments(cluster_size=10, cluster_icc=0.05))
    assert clustered["given"]["effective_unit"] == pytest.approx(145 / 1.45)
    assert clustered["achieved_power"] < TwoSampleT(0.5).power(145)


def test_compute_power_with_target_reports_gap():
    plan = make_plan(TwoSampleT(0.5), target_power=0.80, unit=30)
    assert plan["direction"] == "compute_power"
    assert plan["meets_target"] is False
    assert plan["needed"]["allocation"]["n1"] == 64
    good = make_plan(TwoSampleT(0.5), target_power=0.80, unit=64)
    assert good["meets_target"] is True


def test_make_plan_requires_a_target():
    with pytest.raises(PowerPlanError, match="검정력"):
        make_plan(TwoSampleT(0.5))


def test_make_plan_rejects_too_small_n():
    with pytest.raises(PowerPlanError, match="너무 작습니다"):
        make_plan(TwoSampleT(0.5), unit=1)
    with pytest.raises(PowerPlanError, match="너무 작습니다"):
        make_plan(TwoSampleT(0.5), unit=2, adjustments=Adjustments(dropout=0.9))
    with pytest.raises(PowerPlanError, match="정수"):
        make_plan(TwoSampleT(0.5), unit=10.5)


def test_plan_structure_is_complete():
    plan = make_plan(TwoSampleT(0.5, ratio=2.0), target_power=0.85,
                     adjustments=Adjustments(dropout=0.1, comparisons=2),
                     sensitivity=True,
                     alpha_adjustment={"method": "bonferroni", "comparisons": 2,
                                       "label": "Bonferroni: α/2", "alpha_used": 0.025})
    assert plan["direction"] == "solve_n"
    assert set(plan) >= {"design", "adjustments", "notes", "references", "analysis",
                         "enrollment", "achieved_power", "target_power", "sensitivity"}
    assert plan["design"]["alpha_adjustment"]["alpha_used"] == 0.025
    assert any("Bonferroni" in note for note in plan["notes"])
    assert plan["achieved_power"] >= 0.85
    assert plan["analysis"]["allocation"]["n2"] == 2 * plan["analysis"]["allocation"]["n1"]


def test_sensitivity_table_shapes_and_monotonicity():
    plan = make_plan(TwoSampleT(0.5), target_power=0.80, sensitivity=True)
    sens = plan["sensitivity"]
    assert sens["kind"] == "n_by_power_and_effect"
    assert len(sens["cells"]) == len(sens["rows"])
    for row in sens["cells"]:
        assert len(row) == len(sens["cols"])
        units = [cell["unit"] for cell in row]
        assert units == sorted(units, reverse=True)  # 효과가 크면 n이 작다
    # 목표 검정력이 높아지면 n이 커진다
    column = [row[1]["unit"] for row in sens["cells"]]
    assert column == sorted(column)


def test_sensitivity_power_table():
    plan = make_plan(TwoSampleT(0.5), unit=40, sensitivity=True)
    sens = plan["sensitivity"]
    assert sens["kind"] == "power_by_n"
    powers = [row["power"] for row in sens["rows"]]
    assert powers == sorted(powers)
    assert all(0.0 <= p <= 1.0 for p in powers)


def test_sensitivity_survives_impossible_cells():
    """민감도 표의 한 칸이 계산 불가여도 전체가 죽지 않는다."""
    plan = make_plan(TwoProportions(0.01, 0.02), target_power=0.80, sensitivity=True)
    assert plan["sensitivity"]["cells"]  # 표가 만들어졌다
    plan2 = make_plan(PairedT(0.02), target_power=0.95, sensitivity=True)
    assert plan2["analysis"]["allocation"]["n"] > 1000


def test_anova_plan_inflation_keeps_group_structure():
    plan = make_plan(OneWayAnova(0.25, 3), target_power=0.80,
                     adjustments=Adjustments(dropout=0.2))
    assert plan["analysis"]["allocation"] == {"n_per_group": 53, "k": 3, "total": 159}
    enroll = plan["enrollment"]["allocation"]
    assert enroll["k"] == 3
    assert enroll["n_per_group"] == math.ceil(53 / 0.8) == 67
    assert enroll["total"] == 67 * 3


def test_smallest_unit_is_minimal_and_sufficient():
    for design, power in ((TwoSampleT(0.45), 0.9), (PairedT(0.6), 0.8),
                          (OneWayAnova(0.3, 5), 0.85), (TwoProportions(0.2, 0.35), 0.8)):
        n = smallest_unit(design, power)
        assert design.power_of_allocation(design.allocation(n)) >= power
        assert design.power_of_allocation(design.allocation(n - 1)) < power


def test_cap_is_respected():
    with pytest.raises(PowerPlanError, match="검정력"):
        smallest_unit(TwoSampleT(0.001), 0.80, cap=1000)
    assert MAX_UNIT >= 1_000_000
