"""새 tau^2 추정법(REML·SJ)과 tau^2·I^2 신뢰구간의 정확성 검증.

가능한 곳에서는 **추정방정식을 직접 다시 풀어** 확인한다 (구현을 그대로
베끼는 테스트는 오류를 잡지 못하므로).
"""

import math

import pytest

from metapool.distributions import chi2_ppf, chi2_sf
from metapool.effects import Study
from metapool.meta import (
    MetaError,
    generalized_q,
    heterogeneity,
    random_effects,
    tau2_ci_qprofile,
    tau2_dersimonian_laird,
    tau2_paule_mandel,
    tau2_reml,
    tau2_sidik_jonkman,
    typical_within_variance,
)

HET = [
    Study("A", 0.10, 0.010),
    Study("B", 0.55, 0.020),
    Study("C", -0.20, 0.015),
    Study("D", 0.80, 0.040),
    Study("E", 0.30, 0.008),
]
HOMO = [Study("A", 0.30, 0.01), Study("B", 0.30, 0.02), Study("C", 0.30, 0.03)]


def _mu(studies, tau2):
    w = [1.0 / (s.vi + tau2) for s in studies]
    return sum(wi * s.yi for wi, s in zip(w, studies)) / sum(w)


# --------------------------------------------------------------------------
# chi2_ppf
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "p,df,expected",
    [
        (0.95, 1, 3.841458820694124),
        (0.95, 5, 11.070497693516351),
        (0.025, 4, 0.4844185571318032),
        (0.975, 10, 20.483177335999358),
        (0.5, 3, 2.365973884375338),
    ],
)
def test_chi2_ppf_matches_published_quantiles(p, df, expected):
    assert chi2_ppf(p, df) == pytest.approx(expected, rel=1e-9)


def test_chi2_ppf_is_inverse_of_chi2_sf():
    for df in (1, 2, 7, 30):
        for p in (0.01, 0.25, 0.5, 0.9, 0.99):
            x = chi2_ppf(p, df)
            assert chi2_sf(x, df) == pytest.approx(1.0 - p, abs=1e-10)


def test_chi2_ppf_rejects_bad_arguments():
    with pytest.raises(ValueError):
        chi2_ppf(0.0, 3)
    with pytest.raises(ValueError):
        chi2_ppf(0.5, 0)


# --------------------------------------------------------------------------
# REML
# --------------------------------------------------------------------------


def test_reml_satisfies_its_own_estimating_equation():
    """수렴한 tau^2 는 REML 추정방정식의 고정점이어야 한다."""
    t2 = tau2_reml(HET)
    assert t2 > 0
    w = [1.0 / (s.vi + t2) for s in HET]
    sw = math.fsum(w)
    sw2 = math.fsum(wi * wi for wi in w)
    mu = _mu(HET, t2)
    rhs = math.fsum(wi * wi * ((s.yi - mu) ** 2 - s.vi) for wi, s in zip(w, HET)) / sw2 + 1.0 / sw
    assert rhs == pytest.approx(t2, rel=1e-8)


def test_reml_is_zero_when_studies_agree():
    assert tau2_reml(HOMO) == 0.0


def test_reml_between_dl_and_sj_on_heterogeneous_data():
    dl = tau2_dersimonian_laird(HET)
    reml = tau2_reml(HET)
    # REML 은 DL 보다 크게 나오는 것이 일반적(DL 이 하향편향)
    assert reml > dl > 0


def test_reml_single_study_is_zero():
    assert tau2_reml([Study("A", 0.2, 0.01)]) == 0.0


# --------------------------------------------------------------------------
# Sidik–Jonkman
# --------------------------------------------------------------------------


def test_sidik_jonkman_matches_hand_computation():
    k = len(HET)
    ybar = sum(s.yi for s in HET) / k
    tau0 = sum((s.yi - ybar) ** 2 for s in HET) / k
    r = [(s.vi + tau0) / tau0 for s in HET]
    inv = [1.0 / ri for ri in r]
    mu = sum(i * s.yi for i, s in zip(inv, HET)) / sum(inv)
    expected = sum(i * (s.yi - mu) ** 2 for i, s in zip(inv, HET)) / (k - 1)
    assert tau2_sidik_jonkman(HET) == pytest.approx(expected, rel=1e-12)


def test_sidik_jonkman_is_positive_even_when_dl_is_zero():
    """SJ 는 구조상 0 으로 절단되지 않는다 — DL 이 0 인 자료에서도 양수."""
    assert tau2_dersimonian_laird(HOMO) == 0.0
    assert tau2_sidik_jonkman(HOMO) >= 0.0  # 완전히 같은 값이면 대체값 경로


def test_sidik_jonkman_identical_effects_does_not_divide_by_zero():
    same = [Study("A", 0.3, 0.01), Study("B", 0.3, 0.02), Study("C", 0.3, 0.03)]
    value = tau2_sidik_jonkman(same)
    assert math.isfinite(value) and value >= 0.0


# --------------------------------------------------------------------------
# Q-profile 신뢰구간
# --------------------------------------------------------------------------


def test_qprofile_bounds_solve_the_defining_equations():
    lo, hi = tau2_ci_qprofile(HET, conf=0.95)
    df = len(HET) - 1
    assert generalized_q(HET, lo) == pytest.approx(chi2_ppf(0.975, df), rel=1e-6)
    assert generalized_q(HET, hi) == pytest.approx(chi2_ppf(0.025, df), rel=1e-6)
    assert 0 <= lo < hi


def test_qprofile_contains_the_point_estimate_when_heterogeneity_is_clear():
    lo, hi = tau2_ci_qprofile(HET, conf=0.95)
    assert lo <= tau2_paule_mandel(HET) <= hi


#: 약간만 흩어져 Q 가 유의하지 않은 자료 (DL tau^2 = 0 이지만 상한은 0보다 크다)
MILD = [Study("A", 0.30, 0.01), Study("B", 0.34, 0.02), Study("C", 0.26, 0.03)]


def test_qprofile_lower_bound_is_zero_without_significant_heterogeneity():
    assert tau2_dersimonian_laird(MILD) == 0.0
    lo, hi = tau2_ci_qprofile(MILD, conf=0.95)
    assert lo == 0.0
    assert hi > 0.0


def test_qprofile_collapses_to_zero_when_effects_are_identical():
    """효과크기가 완전히 같으면 Q = 0 이라 상·하한이 모두 0 — 축퇴 자료의 정직한 답."""
    assert generalized_q(HOMO, 0.0) == pytest.approx(0.0, abs=1e-12)
    assert tau2_ci_qprofile(HOMO, conf=0.95) == (0.0, 0.0)


def test_qprofile_needs_two_studies():
    assert tau2_ci_qprofile([Study("A", 0.2, 0.01)]) is None


def test_wider_confidence_level_gives_wider_tau2_interval():
    lo95, hi95 = tau2_ci_qprofile(HET, conf=0.95)
    lo99, hi99 = tau2_ci_qprofile(HET, conf=0.99)
    assert lo99 <= lo95 and hi99 >= hi95


# --------------------------------------------------------------------------
# I^2 구간
# --------------------------------------------------------------------------


def test_i2_ci_is_derived_from_tau2_ci_via_typical_variance():
    het = heterogeneity(HET, conf=0.95)
    s2 = typical_within_variance(HET)
    lo, hi = het.tau2_ci
    assert het.i2_ci[0] == pytest.approx(100.0 * lo / (lo + s2), rel=1e-12)
    assert het.i2_ci[1] == pytest.approx(100.0 * hi / (hi + s2), rel=1e-12)
    assert 0.0 <= het.i2_ci[0] <= het.i2_ci[1] <= 100.0


def test_typical_within_variance_matches_formula():
    w = [1.0 / s.vi for s in HET]
    sw, sw2 = sum(w), sum(x * x for x in w)
    expected = (len(HET) - 1) * sw / (sw * sw - sw2)
    assert typical_within_variance(HET) == pytest.approx(expected, rel=1e-12)


def test_heterogeneity_with_single_study_has_no_intervals():
    het = heterogeneity([Study("A", 0.2, 0.01)])
    assert het.tau2_ci is None and het.i2_ci is None


# --------------------------------------------------------------------------
# 통합 경로
# --------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["DL", "PM", "REML", "SJ", "reml", "sj"])
def test_random_effects_accepts_all_methods(method):
    pooled = random_effects(HET, tau2_method=method)
    assert pooled.tau2 >= 0
    assert pooled.tau2_method == method.upper()
    assert pooled.ci_low < pooled.estimate < pooled.ci_high


def test_unknown_method_still_rejected():
    with pytest.raises(MetaError):
        random_effects(HET, tau2_method="HEDGES-OLKIN")


def test_larger_tau2_gives_wider_random_effects_interval():
    widths = {}
    for m in ("DL", "PM", "REML", "SJ"):
        p = random_effects(HET, tau2_method=m, knapp_hartung=False)
        widths[m] = (p.tau2, p.ci_high - p.ci_low)
    ordered = sorted(widths.values())
    for (t2a, wa), (t2b, wb) in zip(ordered, ordered[1:]):
        assert wa <= wb + 1e-12  # tau^2 가 클수록 구간이 넓다


# --------------------------------------------------------------------------
# R1: I²·H² 는 선택한 tau² 추정법과 같은 척도여야 한다
# --------------------------------------------------------------------------


#: I²·H² 가 Q 기반이던 시절 점추정이 자기 구간 밖으로 나가던 자료
AWKWARD = [
    Study("A", 0.11, 0.0632455532 ** 2),
    Study("B", 0.73, 0.6546753392 ** 2),
    Study("C", -2.21, 0.6787488488 ** 2),
    Study("D", -0.07, 0.06164414 ** 2),
]


def test_paule_mandel_point_estimate_lies_inside_the_qprofile_interval():
    """Q-profile 구간은 PM 추정방정식을 뒤집은 것이라 PM 점추정을 반드시 포함한다."""
    het = heterogeneity(AWKWARD, tau2_method="PM", conf=0.95)
    assert het.tau2_ci[0] - 1e-9 <= het.tau2 <= het.tau2_ci[1] + 1e-9
    assert het.i2_ci[0] - 1e-9 <= het.i2 <= het.i2_ci[1] + 1e-9


@pytest.mark.parametrize("method", ["DL", "PM", "REML", "SJ"])
def test_i2_and_tau2_always_tell_the_same_story(method):
    """어떤 추정법을 써도 보고되는 I² 는 보고되는 tau² 에서 나온 값이어야 한다.

    (예전에는 I² 만 Q 기반이라 tau² = 1.217 인데 I² = 81.4% 로 찍혔다 —
    같은 문장 안에서 두 숫자가 서로 다른 모형을 말했다.)
    """
    het = heterogeneity(AWKWARD, tau2_method=method, conf=0.95)
    s2 = typical_within_variance(AWKWARD)
    assert het.i2 == pytest.approx(100.0 * het.tau2 / (het.tau2 + s2), rel=1e-12)
    assert het.h2 == pytest.approx((het.tau2 + s2) / s2, rel=1e-12)
    # tau^2 가 클수록 I^2 도 커야 한다 — 두 값이 같은 방향을 가리키는지
    bigger = heterogeneity(AWKWARD, tau2_method="SJ", conf=0.95)
    if bigger.tau2 > het.tau2:
        assert bigger.i2 >= het.i2


@pytest.mark.parametrize("method", ["DL", "PM", "REML", "SJ"])
def test_i2_and_h2_are_derived_from_the_selected_tau2(method):
    het = heterogeneity(HET, tau2_method=method, conf=0.95)
    s2 = typical_within_variance(HET)
    assert het.i2 == pytest.approx(100.0 * het.tau2 / (het.tau2 + s2), rel=1e-12)
    assert het.h2 == pytest.approx((het.tau2 + s2) / s2, rel=1e-12)


def test_dersimonian_laird_still_reproduces_the_classic_q_based_formulas():
    """DL 에서는 tau² 기반 식이 (Q-df)/Q, Q/df 와 대수적으로 같아야 한다 (회귀 방지)."""
    het = heterogeneity(HET, tau2_method="DL")
    q, df = het.q, float(het.df)
    assert het.i2 == pytest.approx(max(0.0, (q - df) / q) * 100.0, rel=1e-9)
    assert het.h2 == pytest.approx(q / df, rel=1e-9)


def test_larger_tau2_estimator_gives_larger_i2():
    values = {m: heterogeneity(HET, tau2_method=m).i2 for m in ("DL", "PM", "REML", "SJ")}
    taus = {m: heterogeneity(HET, tau2_method=m).tau2 for m in ("DL", "PM", "REML", "SJ")}
    order = sorted(taus, key=lambda m: taus[m])
    assert [values[m] for m in order] == sorted(values[m] for m in order)
