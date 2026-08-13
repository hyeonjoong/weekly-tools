"""N 합계 · GRIM 적용 · 변화량 · 신뢰구간 · 유의성 문구."""

from __future__ import annotations

from conftest import analyze_text, findings
from numcheck.options import Options
from numcheck.scales import ScaleRegistry, parse_scale_arg


def run(text, **kw):
    return analyze_text("## Results\n" + text + "\n", **kw)


# ── N 합계 ───────────────────────────────────────────────────────────────────


def test_subgroup_sum_mismatch_is_reported():
    got = findings(run("총 48명 (능동자극 24, 대조 23) 이 포함되었다."), item="N 합계")
    assert len(got) == 1
    assert "47" in got[0].recomputed


def test_matching_subgroup_sum_is_silent_but_counted():
    report = run("총 46명 (능동자극 23, 대조 23) 이 포함되었다.")
    assert findings(report, item="N 합계") == []
    assert any(c.item == "N 합계" and c.checked for c in report.claims)


def test_parentheses_with_units_are_not_treated_as_subgroups():
    for text in ("46명 (평균 42.7세, 여성 60%)",
                 "46명 (범위 20-65)",
                 "46명 (95% CI 40 to 52)",
                 "46 patients (mean age 42.7, SD 8.1)"):
        assert findings(run(text + " 였다."), item="N 합계") == []


def test_wildly_different_sum_is_not_a_breakdown():
    report = run("표 3 (항목 2, 항목 5) 를 참조하라.")
    assert findings(report, item="N 합계") == []


def test_table_header_column_sum():
    text = "\n| 지표 | 전체 (N = 48) | 능동 (n = 24) | 대조 (n = 23) |\n"
    got = findings(analyze_text("## Results\n" + text), item="표 열 N 합계")
    assert len(got) == 1


# ── GRIM 적용 ────────────────────────────────────────────────────────────────


def test_grim_fires_on_registered_scale():
    got = findings(run("ISI 평균은 14.37 (N = 23) 이었다."), "치명", "GRIM")
    assert len(got) == 1
    assert "330.51" in got[0].message


def test_grim_stays_silent_on_age_and_bmi():
    """등록된 척도가 아니면 절대 발동하지 않는다 — 이게 5번 실패 지점의 방어선."""
    for text in ("평균 연령은 42.73세 (N = 23) 이었다.",
                 "평균 BMI 는 23.87 (N = 23) 이었다.",
                 "평균 총수면시간은 5.83시간 (N = 23) 이었다."):
        assert findings(run(text), item="GRIM") == []


def test_grim_needs_a_sample_size():
    report = run("ISI 평균은 14.37 이었다.")
    claims = [c for c in report.claims if c.item.startswith("GRIM")]
    assert claims and claims[0].skip_reason == "표본수 없음"


def test_grim_skips_when_it_has_no_power():
    report = run("ISI 평균은 14.4 (N = 23) 이었다.")
    claims = [c for c in report.claims if c.item.startswith("GRIM")]
    assert claims and claims[0].skip_reason == "판별력 없음"


def test_user_defined_scale_is_used():
    registry = ScaleRegistry()
    registry.add(parse_scale_arg("단어인지도=0:100:50", percent_of_count=True))
    got = findings(run("단어인지도 평균은 62.40% (N = 7) 이었다.", registry=registry),
                   "치명", "GRIM")
    assert len(got) == 1


def test_unconfigured_known_scale_gives_info_not_silence():
    got = findings(run("단어인지도 평균은 62.4% (N = 7) 였다."), "정보", "GRIM")
    assert len(got) == 1
    assert "--scale" in got[0].message


def test_grimmer_flags_impossible_sd():
    """평균 12.00 (합계 240, 짝수) · N = 20 · SD 1.06 → 제곱합 후보가 2901 뿐인데
    그 홀짝이 합계와 맞지 않는다. 손으로 검산되는 위반이다."""
    got = findings(run("ISI 평균은 12.00 ± 1.06 (N = 20) 이었다."), item="GRIMMER")
    assert len(got) == 1
    assert got[0].level == "경고"


def test_grimmer_accepts_a_constructible_pair():
    assert findings(run("ISI 평균은 12.00 ± 1.10 (N = 20) 이었다."), item="GRIMMER") == []


def test_grimmer_can_be_switched_off():
    report = run("ISI 평균은 12.00 ± 1.06 (N = 20) 이었다.", strict_grimmer=False)
    assert findings(report, item="GRIMMER") == []


# ── 변화량 ───────────────────────────────────────────────────────────────────


def test_change_score_mismatch():
    got = findings(run("ISI 는 18.4 → 11.2 로 낮아졌다 (변화 -5.2)."), "치명", "변화량")
    assert len(got) == 1


def test_change_score_within_rounding_is_silent():
    assert findings(run("ISI 는 18.4 → 11.2 로 낮아졌다 (변화 -7.2)."), item="변화량") == []


def test_sign_convention_is_forgiven():
    assert findings(run("ISI 는 18.4 → 11.2 로 낮아졌다 (감소 7.2)."), item="변화량") == []


def test_timepoint_label_between_arrow_and_value():
    """'18.4 → 12주 11.2' 에서 12 를 사후값으로 읽으면 헛된 치명이 나온다."""
    assert findings(run("ISI 는 기저 18.4 → 12주 11.2 (변화 -7.2) 였다."),
                    item="변화량") == []


def test_english_from_to_form():
    got = findings(run("ISI fell from 18.4 to 11.2 (change -5.2)."), "치명", "변화량")
    assert len(got) == 1


# ── 신뢰구간 ─────────────────────────────────────────────────────────────────


def test_point_estimate_outside_its_own_interval():
    got = findings(run("차이는 -6.8 (95% CI -5.9 to -1.1) 이었다."), "치명", "신뢰구간")
    assert len(got) == 1


def test_point_estimate_inside_is_silent():
    assert findings(run("차이는 -3.5 (95% CI -5.9 to -1.1) 이었다."),
                    item="신뢰구간") == []


def test_swapped_bounds():
    got = findings(run("차이는 -3.5 (95% CI -1.1 to -5.9) 이었다."), "치명", "신뢰구간")
    assert len(got) == 1
    assert "뒤바" in got[0].message


def test_ci_p_contradiction_includes_null():
    got = findings(run("평균 차이는 1.2 (95% CI -0.4 to 2.8) 였다, p = .03."),
                   "경고", "CI–p")
    assert len(got) == 1


def test_ci_p_contradiction_excludes_null():
    got = findings(run("평균 차이는 2.0 (95% CI 1.1 to 2.9) 였다, p = .21."),
                   "경고", "CI–p")
    assert len(got) == 1


def test_ci_p_agreement_is_silent():
    assert findings(run("평균 차이는 2.0 (95% CI 1.1 to 2.9) 였다, p = .004."),
                    item="CI–p") == []


def test_ratio_measures_use_one_as_the_null():
    assert findings(run("교차비(odds ratio)는 2.1 (95% CI 1.2 to 3.7) 였다, p = .01."),
                    item="CI–p") == []
    got = findings(run("교차비(odds ratio)는 1.5 (95% CI 0.8 to 2.8) 였다, p = .01."),
                   "경고", "CI–p")
    assert len(got) == 1


def test_unknown_null_value_is_skipped_not_guessed():
    report = run("유병률은 47.9 (95% CI 33.3 to 62.8) 였다, p = .03.")
    assert findings(report, item="CI–p") == []
    assert any(c.item == "CI–p 정합" and not c.checked for c in report.claims)


# ── 유의성 문구 ──────────────────────────────────────────────────────────────


def test_significant_word_with_nonsignificant_p():
    got = findings(run("수면 잠복기는 유의하게 감소하였다 (p = .074)."), "치명", "유의성")
    assert len(got) == 1


def test_nonsignificant_word_with_significant_p():
    got = findings(run("두 군의 차이는 유의하지 않았다 (p = .002)."), "치명", "유의성")
    assert len(got) == 1


def test_agreement_is_silent():
    for text in ("차이는 유의하였다 (p = .002).",
                 "차이는 유의하지 않았다 (p = .21).",
                 "the difference was not significant (p = .21).",
                 "the difference was significant (p = .002)."):
        assert findings(run(text), item="유의성") == []


def test_clinically_significant_is_not_a_statistical_claim():
    assert findings(run("임상적으로 유의미한 개선이 있었다 (p = .30)."), item="유의성") == []
    assert findings(run("a clinically significant change was seen (p = .30)."),
                    item="유의성") == []


def test_two_p_values_in_one_sentence_are_skipped():
    text = "A 는 유의하였고 (p = .002) B 는 그렇지 않았다 (p = .30)."
    assert findings(run(text), item="유의성") == []


def test_boundary_p_is_not_judged():
    assert findings(run("차이는 유의하였다 (p = .05)."), item="유의성") == []


def test_custom_alpha():
    got = findings(run("차이는 유의하였다 (p = .03).", alpha=0.01), "치명", "유의성")
    assert len(got) == 1
