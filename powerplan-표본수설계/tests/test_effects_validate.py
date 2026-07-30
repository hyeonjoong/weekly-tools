"""효과크기 변환·라벨과 입력 검증 헬퍼 검증."""

import math

import pytest

from powerplan.effects import (
    cohen_d,
    cohen_f_from_means,
    fisher_z,
    hedges_correction,
    label_d,
    label_f,
    label_r,
    pooled_sd,
)
from powerplan.validate import (
    PowerPlanError,
    as_float,
    as_int,
    in_unit_open,
    positive,
    probability,
)


def test_cohen_d_definition():
    assert cohen_d(8.0, 5.0, 6.0) == pytest.approx(0.5)
    assert cohen_d(5.0, 8.0, 6.0) == pytest.approx(-0.5)
    assert cohen_d(3.0, 3.0, 2.0) == 0.0
    with pytest.raises(PowerPlanError, match="sd"):
        cohen_d(8.0, 5.0, 0.0)
    with pytest.raises(PowerPlanError, match="sd"):
        cohen_d(8.0, 5.0, -1.0)


def test_pooled_sd_definition():
    # n1=n2일 때는 두 분산의 평균의 제곱근
    assert pooled_sd(3.0, 10, 5.0, 10) == pytest.approx(math.sqrt((9 + 25) / 2))
    # 가중: n이 큰 쪽 SD로 끌린다
    value = pooled_sd(2.0, 100, 8.0, 4)
    assert 2.0 < value < 8.0
    assert value == pytest.approx(math.sqrt((99 * 4 + 3 * 64) / 102))
    with pytest.raises(PowerPlanError):
        pooled_sd(2.0, 1, 3.0, 10)
    with pytest.raises(PowerPlanError):
        pooled_sd(0.0, 10, 3.0, 10)


def test_cohen_f_from_means_definition():
    # 평균 8,6,5 · SD 6 → σ_m = √(Σ(m−m̄)²/3), f = σ_m/6
    means = [8.0, 6.0, 5.0]
    grand = sum(means) / 3
    sigma_m = math.sqrt(sum((m - grand) ** 2 for m in means) / 3)
    assert cohen_f_from_means(means, 6.0) == pytest.approx(sigma_m / 6.0, rel=1e-15)
    # 두 군일 때 f = |d|/2
    assert cohen_f_from_means([8.0, 5.0], 6.0) == pytest.approx(abs(cohen_d(8, 5, 6)) / 2)
    # 모든 평균이 같으면 f = 0
    assert cohen_f_from_means([4.0, 4.0, 4.0], 2.0) == 0.0
    # 문자열도 받는다 (CLI에서 쉼표 분리해 넘김)
    assert cohen_f_from_means(["8", "6", "5"], 6.0) == pytest.approx(sigma_m / 6.0)
    with pytest.raises(PowerPlanError, match="2개 이상"):
        cohen_f_from_means([5.0], 2.0)
    with pytest.raises(PowerPlanError, match="sd"):
        cohen_f_from_means([8.0, 5.0], 0.0)
    with pytest.raises(PowerPlanError, match="means"):
        cohen_f_from_means(["8", "여덟"], 6.0)


def test_fisher_z():
    assert fisher_z(0.0) == 0.0
    assert fisher_z(0.5) == pytest.approx(0.5493061443340549, rel=1e-15)
    assert fisher_z(-0.3) == pytest.approx(-math.atanh(0.3), rel=1e-15)
    with pytest.raises(PowerPlanError):
        fisher_z(1.0)
    with pytest.raises(PowerPlanError):
        fisher_z(-1.0)


def test_hedges_correction_properties():
    """J = Γ(df/2)/(√(df/2)Γ((df−1)/2)) — 1보다 작고 df→∞에서 1로 수렴."""
    assert hedges_correction(2.0) < 1.0
    assert hedges_correction(10.0) == pytest.approx(1 - 3 / (4 * 10 - 1), abs=2e-3)
    assert hedges_correction(1e6) == pytest.approx(1.0, abs=1e-6)
    values = [hedges_correction(df) for df in (2, 5, 10, 50, 500)]
    assert values == sorted(values)  # df가 커지면 보정이 약해진다
    # 정확식 검증: df=30 → Γ(15)/(√15·Γ(14.5))
    exact = math.exp(math.lgamma(15.0) - 0.5 * math.log(15.0) - math.lgamma(14.5))
    assert hedges_correction(30.0) == pytest.approx(exact, rel=1e-15)
    with pytest.raises(PowerPlanError):
        hedges_correction(1.0)


def test_effect_labels_follow_cohen_bands():
    assert label_d(0.1) == "매우 작음/very small"
    assert label_d(0.3) == "작음/small"
    assert label_d(0.5) == "중간/medium"
    assert label_d(0.9) == "큼/large"
    assert label_d(-0.9) == "큼/large"          # 부호 무관
    assert label_f(0.05) == "매우 작음/very small"
    assert label_f(0.25) == "중간/medium"
    assert label_f(0.5) == "큼/large"
    assert label_r(0.05) == "매우 작음/very small"
    assert label_r(0.3) == "중간/medium"
    assert label_r(0.7) == "큼/large"


def test_validate_as_float():
    assert as_float("x", "3.5") == 3.5
    assert as_float("x", 2) == 2.0
    for bad in ("abc", None, float("nan"), float("inf"), "", [1]):
        with pytest.raises(PowerPlanError, match="x"):
            as_float("x", bad)


def test_validate_as_int():
    assert as_int("n", "10") == 10
    assert as_int("n", 10.0) == 10
    with pytest.raises(PowerPlanError, match="정수"):
        as_int("n", 10.5)
    with pytest.raises(PowerPlanError, match="이상"):
        as_int("n", 1, minimum=2)


def test_validate_positive_probability_unit():
    assert positive("x", 0.1) == 0.1
    with pytest.raises(PowerPlanError):
        positive("x", 0.0)
    assert probability("p", 0.5) == 0.5
    with pytest.raises(PowerPlanError):
        probability("p", 0.0)
    with pytest.raises(PowerPlanError):
        probability("p", 1.0)
    assert in_unit_open("r", -0.9) == -0.9
    with pytest.raises(PowerPlanError):
        in_unit_open("r", 1.0)
