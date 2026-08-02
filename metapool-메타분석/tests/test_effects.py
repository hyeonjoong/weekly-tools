"""효과크기 공식을 손으로 계산한 값과 대조한다."""

import math

import pytest

from metapool.effects import (
    EffectError,
    build_studies,
    hedges_g,
    log_odds_ratio,
    log_risk_ratio,
    mean_difference,
    risk_difference,
)


def test_hedges_g_hand_computed():
    # n1=n2=20, 평균차 2, 두 군 SD 모두 2 → d = 1.0
    # df = 38, J = 1 - 3/(4*38-1) = 148/151
    g, var = hedges_g(20, 10.0, 2.0, 20, 8.0, 2.0)
    j = 148.0 / 151.0
    assert g == pytest.approx(j, rel=1e-14)
    # var(d) = (n1+n2)/(n1*n2) + d^2/(2(n1+n2)) = 40/400 + 1/80 = 0.1125
    assert var == pytest.approx(j * j * 0.1125, rel=1e-14)


def test_hedges_g_direction_is_group1_minus_group2():
    g_pos, _ = hedges_g(20, 10.0, 2.0, 20, 8.0, 2.0)
    g_neg, _ = hedges_g(20, 8.0, 2.0, 20, 10.0, 2.0)
    assert g_pos > 0 > g_neg
    assert g_pos == pytest.approx(-g_neg, rel=1e-14)


def test_hedges_g_correction_shrinks_toward_zero():
    small, _ = hedges_g(5, 10.0, 2.0, 5, 8.0, 2.0)
    large, _ = hedges_g(500, 10.0, 2.0, 500, 8.0, 2.0)
    assert abs(small) < abs(large) < 1.0  # 소표본일수록 보정이 크다


def test_mean_difference_hand_computed():
    md, var = mean_difference(20, 10.0, 2.0, 20, 8.0, 3.0)
    assert md == pytest.approx(2.0, rel=1e-15)
    assert var == pytest.approx(4.0 / 20 + 9.0 / 20, rel=1e-15)  # 0.65


def test_log_odds_ratio_hand_computed():
    # a=10, b=90, c=20, d=80 → OR = (10*80)/(90*20) = 4/9
    yi, vi, corrected = log_odds_ratio(10, 100, 20, 100)
    assert corrected is False
    assert yi == pytest.approx(math.log(4.0 / 9.0), rel=1e-14)
    assert vi == pytest.approx(1 / 10 + 1 / 90 + 1 / 20 + 1 / 80, rel=1e-14)


def test_log_risk_ratio_hand_computed():
    # RR = (10/100)/(20/100) = 0.5
    yi, vi, corrected = log_risk_ratio(10, 100, 20, 100)
    assert corrected is False
    assert yi == pytest.approx(math.log(0.5), rel=1e-14)
    assert vi == pytest.approx(1 / 10 - 1 / 100 + 1 / 20 - 1 / 100, rel=1e-14)  # 0.13


def test_risk_difference_hand_computed():
    yi, vi = risk_difference(10, 100, 20, 100)
    assert yi == pytest.approx(-0.1, rel=1e-14)
    assert vi == pytest.approx(0.1 * 0.9 / 100 + 0.2 * 0.8 / 100, rel=1e-14)  # 0.0025


def test_zero_cell_gets_continuity_correction():
    # e1=0 → 네 칸 모두 +0.5 → OR = (0.5*5.5)/(10.5*5.5) = 0.5/10.5
    yi, vi, corrected = log_odds_ratio(0, 10, 5, 10)
    assert corrected is True
    assert yi == pytest.approx(math.log(0.5 / 10.5), rel=1e-14)
    assert vi == pytest.approx(1 / 0.5 + 1 / 10.5 + 1 / 5.5 + 1 / 5.5, rel=1e-14)


def test_double_zero_study_is_rejected():
    with pytest.raises(EffectError):
        log_odds_ratio(0, 20, 0, 20)
    with pytest.raises(EffectError):
        log_risk_ratio(0, 20, 0, 20)


def test_all_events_in_both_arms_is_rejected_for_or():
    with pytest.raises(EffectError):
        log_odds_ratio(20, 20, 20, 20)


def test_events_greater_than_n_is_rejected():
    with pytest.raises(EffectError):
        log_odds_ratio(30, 20, 5, 20)
    with pytest.raises(EffectError):
        risk_difference(30, 20, 5, 20)


def test_zero_sd_in_both_arms_is_rejected():
    with pytest.raises(EffectError):
        hedges_g(10, 5.0, 0.0, 10, 3.0, 0.0)
    with pytest.raises(EffectError):
        mean_difference(10, 5.0, 0.0, 10, 3.0, 0.0)


def test_too_small_n_is_rejected_for_smd():
    with pytest.raises(EffectError):
        hedges_g(1, 5.0, 1.0, 1, 3.0, 1.0)


# --------------------------------------------------------------------------
# build_studies
# --------------------------------------------------------------------------


def _rec(row, **kw):
    rec = {"__row__": str(row)}
    rec.update({k: str(v) for k, v in kw.items()})
    return rec


def test_build_studies_generic_uses_se():
    studies, warns = build_studies(
        [_rec(2, study="A", effect=0.5, se=0.1), _rec(3, study="B", effect=0.3, se=0.2)], "generic"
    )
    assert [s.label for s in studies] == ["A", "B"]
    assert studies[0].vi == pytest.approx(0.01, rel=1e-15)
    assert warns == []


def test_build_studies_generic_derives_se_from_ci():
    # 95% CI 폭 = 2 * 1.959964 * se
    studies, _ = build_studies([_rec(2, study="A", effect=0.5, ci_low=0.3, ci_high=0.7)], "generic")
    assert studies[0].sei == pytest.approx(0.2 / 1.959963984540054, rel=1e-12)


def test_build_studies_log_input_takes_log_of_effect_and_ci():
    # OR = 2.0, 95% CI [1.2, 3.3] → 로그 척도 SE = (ln3.3 - ln1.2)/(2*1.959964)
    studies, _ = build_studies(
        [_rec(2, study="A", effect=2.0, ci_low=1.2, ci_high=3.3)], "generic", log_input=True
    )
    assert studies[0].yi == pytest.approx(math.log(2.0), rel=1e-14)
    expected_se = (math.log(3.3) - math.log(1.2)) / (2 * 1.959963984540054)
    assert studies[0].sei == pytest.approx(expected_se, rel=1e-12)


def test_log_input_rejects_bare_se_because_scale_is_ambiguous():
    # 비(ratio) 척도의 SE는 로그 척도로 그대로 옮길 수 없다 — 조용히 틀리느니 거부한다.
    studies, warns = build_studies(
        [_rec(2, study="A", effect=2.0, se=0.1)], "generic", log_input=True
    )
    assert studies == []
    assert "ci_low/ci_high" in warns[0]


def test_log_input_rejects_nonpositive_ci_bound():
    studies, warns = build_studies(
        [_rec(2, study="A", effect=2.0, ci_low=0, ci_high=3.3)], "generic", log_input=True
    )
    assert studies == [] and "0보다 커야" in warns[0]


def test_input_conf_is_independent_of_output_conf():
    """입력 CI가 95%면, 출력 신뢰수준을 99%로 바꿔도 연구별 SE는 그대로여야 한다."""
    rec = [_rec(2, study="A", effect=0.5, ci_low=0.3, ci_high=0.7)]
    a = build_studies(rec, "generic", conf=0.95, input_conf=0.95)[0][0]
    b = build_studies(rec, "generic", conf=0.99, input_conf=0.95)[0][0]
    assert a.sei == pytest.approx(b.sei, rel=1e-15)
    # 입력 구간이 정말 90%였다면 SE는 더 커진다
    c = build_studies(rec, "generic", conf=0.95, input_conf=0.90)[0][0]
    assert c.sei > a.sei


def test_decimal_comma_is_rejected_not_silently_multiplied():
    # "0,5" 를 천 단위 구분자로 보면 5.0 이 되어 10배 틀린다 — 반드시 거부해야 한다.
    studies, warns = build_studies([_rec(2, study="A", effect="0,5", se="0,1")], "generic")
    assert studies == []
    assert "소수점 쉼표" in warns[0]


def test_genuine_thousands_separator_still_parses():
    studies, _ = build_studies(
        [_rec(2, study="A", effect="1,234.5", se="10", n="2,000")], "generic"
    )
    assert studies[0].yi == pytest.approx(1234.5, rel=1e-15)
    assert studies[0].n_total == pytest.approx(2000.0)


def test_control_characters_are_stripped_from_labels():
    # ANSI 이스케이프/CR 이 살아 있으면 출력된 숫자를 덮어써 다른 값처럼 보이게 할 수 있다.
    studies, _ = build_studies(
        [_rec(2, study="\x1b[31mKim\x1b[0m\r2021", effect=0.5, se=0.1, subgroup="A\x08B")],
        "generic",
    )
    assert "\x1b" not in studies[0].label and "\r" not in studies[0].label
    assert "Kim" in studies[0].label
    assert studies[0].subgroup is not None and "\x08" not in studies[0].subgroup


def test_zero_cell_with_correction_disabled_is_dropped_not_crashed():
    studies, warns = build_studies(
        [_rec(2, study="A", events1=0, n1=20, events2=6, n2=20)], "or", cc=0.0
    )
    assert studies == []
    assert "--cc 0.5" in warns[0]


def test_log_risk_ratio_zero_non_event_cell_is_corrected():
    # b 칸(비사건)이 0 — metafor to="only0" 과 동일하게 보정해야 한다.
    yi, vi, corrected = log_risk_ratio(50, 50, 40, 50)
    assert corrected is True
    assert yi == pytest.approx(math.log((50.5 / 51.0) / (40.5 / 51.0)), rel=1e-14)
    assert vi == pytest.approx(1 / 50.5 - 1 / 51.0 + 1 / 40.5 - 1 / 51.0, rel=1e-14)


def test_log_risk_ratio_zero_event_cell_hand_computed():
    yi, vi, corrected = log_risk_ratio(0, 20, 6, 20)
    assert corrected is True
    assert yi == pytest.approx(math.log((0.5 / 21.0) / (6.5 / 21.0)), rel=1e-14)
    assert vi == pytest.approx(1 / 0.5 - 1 / 21.0 + 1 / 6.5 - 1 / 21.0, rel=1e-14)


def test_non_integer_counts_are_flagged():
    _, warns = build_studies(
        [_rec(2, study="A", events1=2.7, n1=50, events2=5, n2=50)], "or"
    )
    assert any("정수가 아닙니다" in w for w in warns)


def test_bad_row_is_dropped_with_warning_not_crash():
    studies, warns = build_studies(
        [
            _rec(2, study="좋음", effect=0.5, se=0.1),
            _rec(3, study="나쁨", effect="abc", se=0.1),
            _rec(4, study="음수SE", effect=0.5, se=-1),
        ],
        "generic",
    )
    assert [s.label for s in studies] == ["좋음"]
    assert len(warns) == 2
    assert "나쁨" in warns[0] and "음수SE" in warns[1]


def test_duplicate_labels_are_disambiguated():
    studies, warns = build_studies(
        [_rec(2, study="Kim", effect=0.5, se=0.1), _rec(3, study="Kim", effect=0.3, se=0.1)],
        "generic",
    )
    assert [s.label for s in studies] == ["Kim", "Kim (2)"]
    assert any("중복" in w for w in warns)


def test_missing_label_falls_back_to_row_number():
    studies, _ = build_studies([_rec(7, effect=0.5, se=0.1)], "generic")
    assert studies[0].label == "연구7"


def test_na_values_are_treated_as_missing():
    studies, warns = build_studies([_rec(2, study="A", effect="NA", se=0.1)], "generic")
    assert studies == []
    assert "A" in warns[0]


def test_subgroup_is_carried_through():
    studies, _ = build_studies(
        [_rec(2, study="A", effect=0.5, se=0.1, subgroup="성인")], "generic"
    )
    assert studies[0].subgroup == "성인"


def test_n_total_is_summed_for_two_group_measures():
    studies, _ = build_studies(
        [_rec(2, study="A", n1=20, mean1=10, sd1=2, n2=30, mean2=8, sd2=2)], "smd"
    )
    assert studies[0].n_total == pytest.approx(50.0)
