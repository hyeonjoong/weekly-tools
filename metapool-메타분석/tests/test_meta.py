"""합성·이질성·하위군 계산을 손으로 계산한 값과 대조한다."""

import math

import pytest

from metapool.distributions import chi2_sf, t_ppf
from metapool.effects import Study
from metapool.meta import (
    MetaError,
    fixed_effect,
    heterogeneity,
    prediction_interval,
    random_effects,
    subgroup_analysis,
    tau2_dersimonian_laird,
    tau2_paule_mandel,
)

# y = 0.5/0.3/0.7, se = 0.1/0.2/0.5 → w = 100/25/4
HOMOGENEOUS = [
    Study("A", 0.5, 0.01),
    Study("B", 0.3, 0.04),
    Study("C", 0.7, 0.25),
]

# se가 모두 0.1이고 효과가 흩어진 자료 → tau^2 > 0
HETEROGENEOUS = [
    Study("A", 0.1, 0.01),
    Study("B", 0.9, 0.01),
    Study("C", 0.5, 0.01),
]


def test_fixed_effect_hand_computed():
    p = fixed_effect(HOMOGENEOUS)
    # sum w = 129, sum w*y = 50 + 7.5 + 2.8 = 60.3
    assert p.estimate == pytest.approx(60.3 / 129.0, rel=1e-14)
    assert p.se == pytest.approx(1.0 / math.sqrt(129.0), rel=1e-14)
    assert p.stat == pytest.approx((60.3 / 129.0) * math.sqrt(129.0), rel=1e-13)
    assert p.k == 3
    assert p.ci_method == "z"


def test_fixed_effect_ci_is_estimate_plus_minus_1_96_se():
    p = fixed_effect(HOMOGENEOUS)
    half = 1.959963984540054 * p.se
    assert p.ci_low == pytest.approx(p.estimate - half, rel=1e-12)
    assert p.ci_high == pytest.approx(p.estimate + half, rel=1e-12)


def test_fixed_effect_weights_are_inverse_variance():
    p = fixed_effect(HOMOGENEOUS)
    assert p.weights == pytest.approx([100.0, 25.0, 4.0], rel=1e-14)
    assert p.weight_percent[0] == pytest.approx(100 * 100.0 / 129.0, rel=1e-13)


def test_pooled_estimate_lies_between_smallest_and_largest_study():
    for data in (HOMOGENEOUS, HETEROGENEOUS):
        est = fixed_effect(data).estimate
        assert min(s.yi for s in data) <= est <= max(s.yi for s in data)


def test_cochran_q_hand_computed():
    het = heterogeneity(HOMOGENEOUS)
    # Q = sum(w*y^2) - (sum w*y)^2 / sum w = 29.21 - 3636.09/129
    expected_q = 29.21 - (60.3 ** 2) / 129.0
    assert het.q == pytest.approx(expected_q, rel=1e-12)
    assert het.q == pytest.approx(1.0232558139534866, rel=1e-12)
    assert het.df == 2
    # Q < df → I^2 = 0
    assert het.i2 == pytest.approx(0.0, abs=1e-15)
    assert het.tau2 == pytest.approx(0.0, abs=1e-15)


def test_heterogeneous_q_i2_tau2_hand_computed():
    het = heterogeneity(HETEROGENEOUS)
    # est = 0.5, Q = 100*(0.16 + 0.16 + 0) = 32
    assert het.q == pytest.approx(32.0, rel=1e-12)
    assert het.df == 2
    assert het.p == pytest.approx(math.exp(-16.0), rel=1e-10)  # df=2 → exp(-Q/2)
    assert het.i2 == pytest.approx((32.0 - 2.0) / 32.0 * 100.0, rel=1e-12)  # 93.75%
    assert het.h2 == pytest.approx(16.0, rel=1e-12)
    # C = 300 - 30000/300 = 200 → tau^2 = (32-2)/200 = 0.15
    assert het.tau2 == pytest.approx(0.15, rel=1e-12)
    assert het.tau == pytest.approx(math.sqrt(0.15), rel=1e-12)


def test_dersimonian_laird_is_never_negative():
    assert tau2_dersimonian_laird(HOMOGENEOUS) == 0.0


def test_random_effects_weights_use_tau2():
    p = random_effects(HETEROGENEOUS, knapp_hartung=False)
    assert p.tau2 == pytest.approx(0.15, rel=1e-12)
    assert p.weights == pytest.approx([1 / 0.16] * 3, rel=1e-12)
    assert p.estimate == pytest.approx(0.5, rel=1e-12)
    assert p.se_model == pytest.approx(1.0 / math.sqrt(3 / 0.16), rel=1e-12)
    assert p.ci_method == "z"


def test_random_effects_is_more_conservative_than_fixed_when_heterogeneous():
    fe = fixed_effect(HETEROGENEOUS)
    re = random_effects(HETEROGENEOUS, knapp_hartung=False)
    assert re.se > fe.se


def test_random_effects_equals_fixed_when_tau2_is_zero():
    fe = fixed_effect(HOMOGENEOUS)
    re = random_effects(HOMOGENEOUS, knapp_hartung=False)
    assert re.estimate == pytest.approx(fe.estimate, rel=1e-14)
    assert re.se == pytest.approx(fe.se, rel=1e-14)


def test_knapp_hartung_uses_t_distribution_and_own_se():
    data = [
        Study("A", 0.1, 0.01),
        Study("B", 0.9, 0.01),
        Study("C", 0.5, 0.04),
        Study("D", 0.3, 0.01),
    ]
    p = random_effects(data, knapp_hartung=True)
    w = [1.0 / (s.vi + p.tau2) for s in data]
    total = math.fsum(w)
    est = math.fsum(wi * s.yi for wi, s in zip(w, data)) / total
    se_hk = math.sqrt(math.fsum(wi * (s.yi - est) ** 2 for wi, s in zip(w, data)) / (3 * total))
    assert p.se == pytest.approx(se_hk, rel=1e-12)
    assert p.se_model == pytest.approx(1.0 / math.sqrt(total), rel=1e-12)
    assert p.df == 3.0
    assert p.ci_method == "HK"
    half = t_ppf(0.975, 3.0) * se_hk
    assert p.ci_high - p.ci_low == pytest.approx(2 * half, rel=1e-11)


def test_paule_mandel_satisfies_its_estimating_equation():
    tau2 = tau2_paule_mandel(HETEROGENEOUS)
    assert tau2 > 0
    w = [1.0 / (s.vi + tau2) for s in HETEROGENEOUS]
    mu = math.fsum(wi * s.yi for wi, s in zip(w, HETEROGENEOUS)) / math.fsum(w)
    gen_q = math.fsum(wi * (s.yi - mu) ** 2 for wi, s in zip(w, HETEROGENEOUS))
    assert gen_q == pytest.approx(len(HETEROGENEOUS) - 1, abs=1e-8)


def test_paule_mandel_is_zero_when_no_heterogeneity():
    assert tau2_paule_mandel(HOMOGENEOUS) == 0.0


def test_unknown_tau2_method_raises():
    with pytest.raises(MetaError):
        random_effects(HETEROGENEOUS, tau2_method="SJ")


def test_prediction_interval_hand_computed():
    p = random_effects(HETEROGENEOUS, knapp_hartung=False)
    lo, hi = prediction_interval(p)
    half = t_ppf(0.975, 1.0) * math.sqrt(0.15 + p.se_model ** 2)
    assert lo == pytest.approx(p.estimate - half, rel=1e-11)
    assert hi == pytest.approx(p.estimate + half, rel=1e-11)


def test_prediction_interval_uses_model_se_not_hk_se():
    p_hk = random_effects(HETEROGENEOUS, knapp_hartung=True)
    p_z = random_effects(HETEROGENEOUS, knapp_hartung=False)
    assert prediction_interval(p_hk) == pytest.approx(prediction_interval(p_z), rel=1e-12)


def test_prediction_interval_needs_three_studies():
    assert prediction_interval(random_effects(HETEROGENEOUS[:2], knapp_hartung=False)) is None


def test_prediction_interval_is_wider_than_confidence_interval():
    p = random_effects(HETEROGENEOUS, knapp_hartung=False)
    lo, hi = prediction_interval(p)
    assert lo < p.ci_low and hi > p.ci_high


def test_subgroup_q_between_hand_computed():
    data = [
        Study("A1", 0.2, 0.01, subgroup="G1"),
        Study("A2", 0.4, 0.01, subgroup="G1"),
        Study("B1", 0.8, 0.01, subgroup="G2"),
        Study("B2", 1.0, 0.01, subgroup="G2"),
    ]
    results, test = subgroup_analysis(data, knapp_hartung=True)
    assert [r.name for r in results] == ["G1", "G2"]
    assert results[0].pooled.estimate == pytest.approx(0.3, rel=1e-12)
    assert results[1].pooled.estimate == pytest.approx(0.9, rel=1e-12)
    # 각 군: Q=2, df=1, C=100 → tau^2 = 0.01 → w_i = 50, SE_model = 0.1
    assert results[0].pooled.se_model == pytest.approx(0.1, rel=1e-12)
    # Q_between = 100*(0.3-0.6)^2 + 100*(0.9-0.6)^2 = 18
    assert test["q_between"] == pytest.approx(18.0, rel=1e-12)
    assert test["df"] == 1
    assert test["p"] == pytest.approx(chi2_sf(18.0, 1), rel=1e-12)


def test_subgroup_test_is_none_with_single_group():
    data = [Study("A", 0.2, 0.01, subgroup="G1"), Study("B", 0.4, 0.01, subgroup="G1")]
    results, test = subgroup_analysis(data)
    assert len(results) == 1 and test is None


def test_studies_without_subgroup_are_grouped_as_unspecified():
    data = [Study("A", 0.2, 0.01), Study("B", 0.4, 0.01, subgroup="G")]
    results, _ = subgroup_analysis(data)
    assert {r.name for r in results} == {"(미지정)", "G"}


def test_single_study_still_pools():
    p = fixed_effect([Study("solo", 0.4, 0.04)])
    assert p.estimate == pytest.approx(0.4, rel=1e-15)
    assert p.se == pytest.approx(0.2, rel=1e-15)
    het = heterogeneity([Study("solo", 0.4, 0.04)])
    assert het.df == 0 and het.q == 0.0 and het.i2 == 0.0


def test_empty_input_raises():
    with pytest.raises(MetaError):
        fixed_effect([])


def test_zero_variance_study_raises():
    with pytest.raises(MetaError):
        fixed_effect([Study("bad", 0.4, 0.0)])
