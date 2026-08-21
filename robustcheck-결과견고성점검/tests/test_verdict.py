"""뒤집힘 판정 4종과 취약도 등급."""

import math

import pytest

from robustcheck.effects import D_FAMILY, R_FAMILY
from robustcheck.verdict import (
    CRITICAL,
    MULTIPLICITY_NOTE,
    SEVERITY_RANK,
    Verdict,
    WARNING,
    grade_formula_text,
    judge_flips,
    order_key,
    severity_of,
)

ALPHA = 0.05


def codes(flips):
    return [f.code for f in flips]


# ------------------------------------------------------------- ① 유의 → 비유의


def test_significant_to_nonsignificant_is_critical():
    flips = judge_flips(0.03, -0.7, 0.08, -0.65, ALPHA, D_FAMILY)
    assert codes(flips) == ["①"]
    assert flips[0].severity == CRITICAL


def test_nonsignificant_to_significant_is_warning_only():
    flips = judge_flips(0.08, -0.65, 0.03, -0.7, ALPHA, D_FAMILY)
    assert codes(flips) == ["②"]
    assert flips[0].severity == WARNING


def test_no_significance_change_produces_no_flip():
    assert judge_flips(0.01, -0.7, 0.02, -0.72, ALPHA, D_FAMILY) == []
    assert judge_flips(0.30, -0.1, 0.40, -0.09, ALPHA, D_FAMILY) == []


def test_alpha_boundary_is_strictly_less_than():
    """p = alpha 는 '유의'가 아니다 — 경계 규칙을 못 박아 둔다."""
    flips = judge_flips(0.049, -0.7, 0.05, -0.7, ALPHA, D_FAMILY)
    assert codes(flips) == ["①"]


def test_custom_alpha_is_respected():
    assert judge_flips(0.008, -0.7, 0.02, -0.7, 0.01, D_FAMILY)[0].code == "①"
    assert judge_flips(0.008, -0.7, 0.02, -0.7, 0.05, D_FAMILY) == []


# ---------------------------------------------------------- ③ 부호 반전


def test_sign_reversal_is_critical_when_both_sides_are_at_least_small():
    flips = judge_flips(0.02, -0.6, 0.3, 0.55, ALPHA, D_FAMILY)
    assert "③" in codes(flips)
    assert severity_of(flips) == CRITICAL


def test_sign_reversal_near_zero_is_not_counted():
    """+0.109 → −0.11 은 부호 반전이 아니라 잡음이다."""
    flips = judge_flips(0.6, 0.109, 0.7, -0.11, ALPHA, D_FAMILY)
    assert "③" not in codes(flips)


def test_sign_reversal_r_family_threshold_is_lower():
    assert "③" in codes(judge_flips(0.02, -0.15, 0.3, 0.15, ALPHA, R_FAMILY))
    assert "③" not in codes(judge_flips(0.02, -0.05, 0.3, 0.05, ALPHA, R_FAMILY))


# ------------------------------------------- 귀무 결과에서는 울지 않는다


def test_effect_rules_are_silent_when_neither_side_is_significant():
    """r ≈ 0 자료에서 효과크기가 0 근처를 흔들려도 결론은 바뀌지 않았다."""
    assert judge_flips(0.52, 0.121, 0.48, -0.123, ALPHA, R_FAMILY) == []
    assert judge_flips(0.30, 0.85, 0.40, 0.20, ALPHA, D_FAMILY) == []


def test_effect_rules_wake_up_when_either_side_is_significant():
    assert codes(judge_flips(0.02, 0.85, 0.40, 0.20, ALPHA, D_FAMILY)) == ["①", "④"]
    assert codes(judge_flips(0.30, 0.20, 0.02, 0.85, ALPHA, D_FAMILY)) == ["②", "④"]


def test_significance_rules_still_fire_when_effects_are_tiny():
    assert codes(judge_flips(0.02, 0.01, 0.30, 0.01, ALPHA, D_FAMILY)) == ["①"]


def test_grade_change_is_suppressed_across_a_scale_change():
    """로그변환한 g 와 원척도 g 는 단위가 달라 등급을 비교하면 거짓말이 된다."""
    same = judge_flips(0.02, -0.93, 0.02, -0.61, ALPHA, D_FAMILY, same_scale=True)
    across = judge_flips(0.02, -0.93, 0.02, -0.61, ALPHA, D_FAMILY, same_scale=False)
    assert codes(same) == ["④"]
    assert across == []


def test_sign_reversal_survives_a_scale_change():
    """부호는 척도를 바꿔도 의미가 있다 — ③ 은 살려 둔다."""
    flips = judge_flips(0.02, -0.6, 0.02, 0.55, ALPHA, D_FAMILY, same_scale=False)
    assert codes(flips) == ["③"]


def test_zero_effect_is_never_a_sign_reversal():
    assert "③" not in codes(judge_flips(0.2, 0.0, 0.3, 0.5, ALPHA, D_FAMILY))


# ------------------------------------------------------- ④ 효과크기 등급 변화


def test_grade_change_is_warning():
    flips = judge_flips(0.02, -0.85, 0.02, -0.60, ALPHA, D_FAMILY)
    assert codes(flips) == ["④"]
    assert flips[0].severity == WARNING


def test_grade_change_below_min_delta_is_ignored():
    """0.499 → 0.501 로 경계를 스치는 변화는 세지 않는다."""
    assert judge_flips(0.02, 0.499, 0.02, 0.501, ALPHA, D_FAMILY) == []


def test_grade_change_needs_both_conditions():
    # 큰 변화지만 등급은 그대로 (大 → 大)
    assert judge_flips(0.02, 1.5, 0.02, 0.9, ALPHA, D_FAMILY) == []
    # 등급은 바뀌지만 변화폭이 작다
    assert judge_flips(0.02, 0.79, 0.02, 0.81, ALPHA, D_FAMILY) == []


def test_grade_change_r_family_min_delta():
    assert judge_flips(0.02, 0.29, 0.02, 0.31, ALPHA, R_FAMILY) == []
    assert codes(judge_flips(0.02, 0.29, 0.02, 0.45, ALPHA, R_FAMILY)) == ["④"]


def test_multiple_flips_can_co_occur():
    flips = judge_flips(0.02, -0.9, 0.20, 0.30, ALPHA, D_FAMILY)
    assert set(codes(flips)) == {"①", "③", "④"}
    assert severity_of(flips) == CRITICAL


def test_nan_inputs_produce_no_flips():
    assert judge_flips(float("nan"), -0.5, 0.02, -0.5, ALPHA, D_FAMILY) == []
    assert judge_flips(0.02, -0.5, float("nan"), -0.5, ALPHA, D_FAMILY) == []


def test_nan_effect_only_suppresses_effect_rules():
    flips = judge_flips(0.02, float("nan"), 0.20, float("nan"), ALPHA, D_FAMILY)
    assert codes(flips) == ["①"]


def test_flip_detail_contains_both_values():
    detail = judge_flips(0.03, -0.7, 0.08, -0.65, ALPHA, D_FAMILY)[0].detail
    assert ".030" in detail and ".080" in detail


def test_flip_detail_uses_less_than_for_tiny_p():
    detail = judge_flips(0.0001, -0.7, 0.08, -0.7, ALPHA, D_FAMILY)[0].detail
    assert "<.001" in detail


# ------------------------------------------------------------------ 정렬


def test_severity_rank_puts_critical_first():
    assert SEVERITY_RANK[CRITICAL] < SEVERITY_RANK[WARNING] < SEVERITY_RANK[""]
    assert SEVERITY_RANK[""] < SEVERITY_RANK["건너뜀"]


def test_order_key_ignores_p_values_entirely():
    a = order_key(CRITICAL, True, (2, 0, 1, 1))
    b = order_key(WARNING, True, (0, 0, 0, 0))
    assert a < b


def test_order_key_skipped_goes_last():
    assert order_key("", False, (0, 0, 0, 0)) > order_key(WARNING, True, (2, 2, 1, 1))


def test_order_key_is_stable_within_severity():
    keys = [order_key(WARNING, True, (i, 0, 0, 0)) for i in range(3)]
    assert keys == sorted(keys)


# ------------------------------------------------------------------ 등급


def test_grade_robust_when_nothing_flips():
    assert Verdict(0, 0, 0, 0, 12, 12).grade == "견고"


def test_grade_caution_with_warnings_only():
    assert Verdict(0, 2, 0, 0, 12, 12).grade == "주의"
    assert Verdict(0, 0, 0, 3, 12, 12).grade == "주의"


def test_grade_fragile_with_any_critical():
    assert Verdict(1, 0, 0, 0, 12, 12).grade == "취약"
    assert Verdict(0, 5, 1, 9, 12, 12).grade == "취약"


def test_undecidable_beats_everything():
    verdict = Verdict(9, 9, 9, 9, 1, 12, "유효 N = 3 < 6")
    assert verdict.grade == "판정불가"
    assert "유효 N" in verdict.summary()


def test_solo_flipping_subject_alone_makes_it_fragile():
    assert Verdict(0, 0, 1, 0, 12, 12).grade == "취약"


def test_verdict_summary_lists_components():
    text = Verdict(2, 1, 3, 0, 12, 12).summary()
    assert "치명 시나리오 2건" in text
    assert "단독 뒤집기 피험자 3명" in text
    assert "경고 시나리오 1건" in text


def test_robust_summary_is_explicit():
    assert Verdict(0, 0, 0, 0, 12, 12).summary() == "견고 (뒤집힘 0건)"


def test_total_counts():
    verdict = Verdict(2, 3, 4, 5, 12, 12)
    assert verdict.total_critical == 6
    assert verdict.total_warning == 8


def test_grade_formula_text_is_printable_and_complete():
    lines = grade_formula_text()
    joined = "\n".join(lines)
    for grade in ("취약", "주의", "견고", "판정불가"):
        assert grade in joined


def test_multiplicity_note_says_no_correction():
    assert "다중비교 보정을 하지 않았" in MULTIPLICITY_NOTE


def test_severity_of_empty_is_blank():
    assert severity_of([]) == ""
