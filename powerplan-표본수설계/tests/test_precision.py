"""정밀도 기준 표본수(ICC·Bland–Altman LoA) 검증 — 손계산과 대조."""

import math

import pytest

from powerplan.precision import icc_ci_width, icc_plan, loa_half_width, loa_plan
from powerplan.special import norm_ppf
from powerplan.validate import PowerPlanError


def _bonett(icc, width, k, alpha=0.05):
    """Bonett(2002) 공식을 테스트 안에서 독립적으로 다시 계산."""
    z = norm_ppf(1 - alpha / 2)
    num = 8 * z * z * (1 - icc) ** 2 * (1 + (k - 1) * icc) ** 2
    return num / (k * (k - 1) * width * width) + 1


@pytest.mark.parametrize("icc,width,k", [
    (0.8, 0.2, 2), (0.8, 0.15, 2), (0.6, 0.2, 3), (0.9, 0.1, 2), (0.7, 0.25, 4),
])
def test_icc_plan_matches_hand_computed_formula(icc, width, k):
    plan = icc_plan(icc, width, k)
    assert plan["n"] == math.ceil(_bonett(icc, width, k) - 1e-9)
    assert plan["n_exact"] == pytest.approx(_bonett(icc, width, k), rel=1e-12)
    assert plan["total_measurements"] == plan["n"] * k


def test_icc_plan_specific_values():
    # 손계산: 8·1.959964²·(0.2)²·(1.8)² / (2·1·0.04) + 1 = 50.79 → 51
    assert icc_plan(0.8, 0.2, 2)["n"] == 51
    # k=3: 8·3.8416·0.16·4.84 / (3·2·0.04) + 1 = 100.2 → 101
    assert icc_plan(0.6, 0.2, 3)["n"] == 101


def test_icc_achieved_width_is_at_or_below_target():
    for icc in (0.5, 0.7, 0.85, 0.95):
        for width in (0.1, 0.2, 0.3):
            for k in (2, 3, 5):
                plan = icc_plan(icc, width, k)
                assert plan["achieved_width"] <= width + 1e-12
                # 한 명 줄이면 목표를 넘어선다 (최소성)
                if plan["n"] > 2:
                    assert icc_ci_width(plan["n"] - 1, icc, k) > width - 1e-12


def test_icc_more_raters_needs_fewer_subjects():
    ns = [icc_plan(0.8, 0.15, k)["n"] for k in (2, 3, 4, 5)]
    assert ns == sorted(ns, reverse=True)


def test_icc_higher_icc_needs_fewer_subjects():
    ns = [icc_plan(icc, 0.15, 2)["n"] for icc in (0.5, 0.7, 0.9)]
    assert ns == sorted(ns, reverse=True)


def test_icc_narrower_width_needs_more_subjects():
    ns = [icc_plan(0.8, w, 2)["n"] for w in (0.3, 0.2, 0.1, 0.05)]
    assert ns == sorted(ns)


def test_icc_validation():
    with pytest.raises(PowerPlanError, match="icc"):
        icc_plan(0.0, 0.2)
    with pytest.raises(PowerPlanError, match="icc"):
        icc_plan(1.0, 0.2)
    with pytest.raises(PowerPlanError, match="width"):
        icc_plan(0.8, 0.0)
    with pytest.raises(PowerPlanError, match="width"):
        icc_plan(0.8, 1.5)
    with pytest.raises(PowerPlanError, match="raters"):
        icc_plan(0.8, 0.2, 1)
    with pytest.raises(PowerPlanError, match="alpha"):
        icc_plan(0.8, 0.2, 2, alpha=0.0)
    with pytest.raises(PowerPlanError):
        icc_ci_width(1, 0.8, 2)


def test_icc_extremely_narrow_width_raises_rather_than_hanging():
    with pytest.raises(PowerPlanError, match="width"):
        icc_plan(0.5, 1e-6)


def test_loa_half_width_matches_bland_altman_formula():
    """반폭 = t_{0.975,n−1}·s·√(1/n + 1.96²/(2(n−1)))."""
    from powerplan.distributions import t_ppf
    for n, s in ((30, 2.0), (100, 1.0), (183, 2.0)):
        z = 1.959963984540054
        expected = t_ppf(0.975, n - 1) * s * math.sqrt(1.0 / n + z * z / (2 * (n - 1)))
        assert loa_half_width(n, s) == pytest.approx(expected, rel=1e-12)


def test_loa_plan_is_minimal_and_sufficient():
    for s, target in ((2.0, 0.5), (1.0, 0.2), (5.0, 2.0), (0.5, 0.05)):
        plan = loa_plan(s, target)
        n = plan["n"]
        assert loa_half_width(n, s) <= target + 1e-12
        assert loa_half_width(n - 1, s) > target - 1e-12
        assert plan["achieved_half_width"] == pytest.approx(loa_half_width(n, s), rel=1e-12)


def test_loa_plan_specific_value():
    # s=2, 목표 반폭 0.5 → n=183 (손계산 확인)
    assert loa_plan(2.0, 0.5)["n"] == 183


def test_loa_scale_invariance():
    """s와 목표 반폭을 같은 배수로 키우면 필요한 n은 같다."""
    base = loa_plan(2.0, 0.5)["n"]
    assert loa_plan(20.0, 5.0)["n"] == base
    assert loa_plan(0.2, 0.05)["n"] == base


def test_loa_expected_limits_reported():
    plan = loa_plan(3.0, 1.0)
    low, high = plan["expected_loa"]
    assert low == pytest.approx(-1.959963984540054 * 3.0)
    assert high == pytest.approx(1.959963984540054 * 3.0)
    assert plan["ratio_to_sd"] == pytest.approx(1.0 / 3.0)


def test_loa_validation():
    with pytest.raises(PowerPlanError, match="sd-diff"):
        loa_plan(0.0, 0.5)
    with pytest.raises(PowerPlanError, match="half-width"):
        loa_plan(2.0, -1.0)
    with pytest.raises(PowerPlanError, match="alpha"):
        loa_plan(2.0, 0.5, alpha=1.0)
    with pytest.raises(PowerPlanError):
        loa_half_width(1, 2.0)


def test_loa_impossible_target_raises():
    with pytest.raises(PowerPlanError, match="half-width"):
        loa_plan(2.0, 1e-9)


def test_precision_plans_carry_notes_and_references():
    for plan in (icc_plan(0.8, 0.2), loa_plan(2.0, 0.5)):
        assert plan["kind"] == "precision"
        assert len(plan["notes"]) >= 3
        assert any("Bland" in r or "Bonett" in r for r in plan["references"])
