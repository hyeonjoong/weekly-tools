"""축 B(이상치) · C(결측) · E(로그변환) 의 실제 동작."""

import math

import pytest

from robustcheck.prep import (
    MIN_N_FOR_OUTLIER_RULE,
    MIN_N_PER_GROUP,
    OUTLIER_LEVELS,
    PIPELINE_ORDER,
    SkipScenario,
    _outlier_mask,
    max_possible_z,
    prepare,
    quantile,
    sd_rule_note,
)
from robustcheck.spec import Spec, Subject


def subject(sid, group=None, **fields):
    return Subject(sid, group, fields, 0)


def two_group_subjects(values_a, values_b, cov_a=None, cov_b=None):
    out = []
    for i, v in enumerate(values_a):
        fields = {"v": v}
        if cov_a is not None:
            fields["c"] = cov_a[i]
        out.append(Subject("A%d" % i, "active", fields, i))
    for i, v in enumerate(values_b):
        fields = {"v": v}
        if cov_b is not None:
            fields["c"] = cov_b[i]
        out.append(Subject("B%d" % i, "sham", fields, 100 + i))
    return out


TWO_GROUP = Spec("two-group", value="v", group="g")
PAIRED = Spec("paired", pre="pre", post="post")
CORR = Spec("corr", x="x", y="y")
LEVELS = ("active", "sham")


# ------------------------------------------------------------------ 분위수


@pytest.mark.parametrize("q,expected", [
    (0.0, 1.0), (0.25, 1.75), (0.5, 2.5), (0.75, 3.25), (1.0, 4.0),
])
def test_quantile_linear_interpolation(q, expected):
    assert quantile([1.0, 2.0, 3.0, 4.0], q) == pytest.approx(expected)


def test_quantile_single_value():
    assert quantile([7.0], 0.25) == 7.0


def test_quantile_empty_raises():
    with pytest.raises(ValueError):
        quantile([], 0.5)


# --------------------------------------------------------------- 이상치 규칙


def test_outlier_none_never_excludes():
    values = [1.0, 2.0, 3.0, 1000.0]
    assert _outlier_mask(values, "없음") == [False] * 4


def test_outlier_3sd_flags_extreme_point():
    values = [10.0] * 20 + [10000.0]
    mask = _outlier_mask(values, "±3SD")
    assert mask[-1] is True
    assert sum(mask) == 1


def test_outlier_3sd_needs_enough_points():
    assert _outlier_mask([1.0, 2.0, 900.0], "±3SD") == [False, False, False]
    assert MIN_N_FOR_OUTLIER_RULE == 4


def test_outlier_3sd_with_zero_variance_excludes_nobody():
    assert _outlier_mask([5.0] * 8, "±3SD") == [False] * 8


def test_outlier_iqr_flags_both_tails():
    values = [-500.0] + [10.0, 11.0, 12.0, 13.0, 14.0] + [500.0]
    mask = _outlier_mask(values, "IQR1.5")
    assert mask[0] and mask[-1]
    assert sum(mask) == 2


def test_outlier_iqr_with_zero_iqr_excludes_nobody():
    assert _outlier_mask([3.0] * 10, "IQR1.5") == [False] * 10


def test_outlier_unknown_rule_raises():
    with pytest.raises(ValueError):
        _outlier_mask([1.0, 2.0, 3.0, 4.0], "무슨규칙")


def test_outlier_levels_start_with_none():
    assert OUTLIER_LEVELS[0] == "없음"


# ------------------------------------------------------- 결측 처리 (축 C)


def test_completers_only_drops_incomplete_pairs():
    subjects = [subject("S1", pre=10.0, post=8.0),
                subject("S2", pre=11.0, post=None),
                subject("S3", pre=12.0, post=9.0),
                subject("S4", pre=13.0, post=10.0)]
    prepared = prepare(subjects, PAIRED, (), "없음", "완결자만", "미적용")
    assert prepared.ids == ["S1", "S3", "S4"]
    assert ("S2", "결측(post)") in prepared.excluded


def test_locf_carries_baseline_forward():
    subjects = [subject("S%d" % i, pre=10.0 + i, post=None if i == 2 else 5.0 + i)
                for i in range(1, 6)]
    prepared = prepare(subjects, PAIRED, (), "없음", "LOCF", "미적용")
    assert prepared.imputed == 1
    idx = prepared.ids.index("S2")
    assert prepared.post[idx] == prepared.pre[idx] == 12.0


def test_locf_cannot_impute_when_baseline_missing():
    subjects = [subject("S1", pre=None, post=5.0)] + [
        subject("S%d" % i, pre=10.0, post=5.0 + i) for i in range(2, 6)]
    prepared = prepare(subjects, PAIRED, (), "없음", "LOCF", "미적용")
    assert "S1" not in prepared.ids
    assert any("LOCF 불가" in reason for _, reason in prepared.excluded)


def test_mean_imputation_uses_observed_column_means():
    subjects = [subject("S1", pre=10.0, post=None),
                subject("S2", pre=20.0, post=4.0),
                subject("S3", pre=30.0, post=8.0),
                subject("S4", pre=40.0, post=12.0)]
    prepared = prepare(subjects, PAIRED, (), "없음", "평균대체", "미적용")
    assert prepared.post[prepared.ids.index("S1")] == pytest.approx(8.0)
    assert prepared.imputed == 1


def test_mean_imputation_drops_subject_missing_both():
    subjects = [subject("S1", pre=None, post=None)] + [
        subject("S%d" % i, pre=10.0 * i, post=5.0 * i) for i in range(2, 6)]
    prepared = prepare(subjects, PAIRED, (), "없음", "평균대체", "미적용")
    assert "S1" not in prepared.ids
    assert any("두 시점" in reason for _, reason in prepared.excluded)


def test_mean_imputation_with_fully_missing_column_skips():
    subjects = [subject("S%d" % i, pre=10.0, post=None) for i in range(1, 6)]
    with pytest.raises(SkipScenario) as exc:
        prepare(subjects, PAIRED, (), "없음", "평균대체", "미적용")
    assert exc.value.reason == "결측 100%"


def test_missing_axis_is_paired_only():
    subjects = two_group_subjects([1.0, 2.0, 3.0], [4.0, 5.0, 6.0])
    with pytest.raises(SkipScenario) as exc:
        prepare(subjects, TWO_GROUP, LEVELS, "없음", "LOCF", "미적용")
    assert exc.value.reason == "설계상 미적용"


def test_all_missing_leaves_nothing():
    subjects = [subject("S%d" % i, "active", v=None) for i in range(1, 5)]
    with pytest.raises(SkipScenario) as exc:
        prepare(subjects, TWO_GROUP, LEVELS, "없음", "완결자만", "미적용")
    assert exc.value.reason == "결측 100%"


# ------------------------------------------------------- 로그변환 (축 E)


def test_log_transform_applies_to_all_analysis_columns():
    subjects = two_group_subjects([math.e] * 4, [math.e ** 2] * 4)
    prepared = prepare(subjects, TWO_GROUP, LEVELS, "없음", "완결자만", "적용")
    assert prepared.a == pytest.approx([1.0] * 4)
    assert prepared.b == pytest.approx([2.0] * 4)


def test_log_transform_skipped_when_zero_present():
    subjects = two_group_subjects([1.0, 2.0, 0.0, 4.0], [1.0, 2.0, 3.0, 4.0])
    with pytest.raises(SkipScenario) as exc:
        prepare(subjects, TWO_GROUP, LEVELS, "없음", "완결자만", "적용")
    assert exc.value.reason == "로그변환 불가"
    assert "0 이하" in exc.value.detail


def test_log_transform_skipped_when_negative_present():
    subjects = two_group_subjects([-1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0])
    with pytest.raises(SkipScenario):
        prepare(subjects, TWO_GROUP, LEVELS, "없음", "완결자만", "적용")


def test_log_transform_checks_covariate_too():
    spec = Spec("two-group", value="v", group="g", covariate="c")
    subjects = two_group_subjects([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0],
                                  cov_a=[1.0, 1.0, 1.0, -5.0],
                                  cov_b=[1.0, 1.0, 1.0, 1.0])
    with pytest.raises(SkipScenario):
        prepare(subjects, spec, LEVELS, "없음", "완결자만", "적용")


def test_log_is_skipped_only_after_missing_handling():
    """결측으로 빠질 사람의 음수는 로그변환을 막지 않는다 (순서가 중요하다)."""
    subjects = [subject("S1", pre=-5.0, post=None),
                subject("S2", pre=10.0, post=5.0),
                subject("S3", pre=12.0, post=6.0),
                subject("S4", pre=14.0, post=7.0)]
    prepared = prepare(subjects, PAIRED, (), "없음", "완결자만", "적용")
    assert prepared.ids == ["S2", "S3", "S4"]


def test_pipeline_order_is_documented():
    assert "결측" in PIPELINE_ORDER and "로그" in PIPELINE_ORDER
    assert PIPELINE_ORDER.index("결측") < PIPELINE_ORDER.index("로그")
    assert PIPELINE_ORDER.index("로그") < PIPELINE_ORDER.index("이상치")


# ------------------------------------------------------------ 최소 인원


def test_group_below_minimum_skips():
    subjects = two_group_subjects([1.0, 2.0], [3.0, 4.0, 5.0, 6.0])
    with pytest.raises(SkipScenario) as exc:
        prepare(subjects, TWO_GROUP, LEVELS, "없음", "완결자만", "미적용")
    assert "군 n<%d" % MIN_N_PER_GROUP == exc.value.reason


def test_outlier_removal_can_push_group_below_minimum():
    a = [10.0, 10.1, 10.2, 900.0]
    b = [10.0, 10.1, 10.2, 10.3, 10.4, 10.5]
    subjects = two_group_subjects(a, b)
    prepared = prepare(subjects, TWO_GROUP, LEVELS, "없음", "완결자만", "미적용")
    assert prepared.n == 10
    trimmed = prepare(subjects, TWO_GROUP, LEVELS, "IQR1.5", "완결자만", "미적용")
    assert trimmed.n < 10


def test_corr_needs_four_points():
    subjects = [subject("S%d" % i, x=float(i), y=float(i) * 2) for i in range(1, 4)]
    with pytest.raises(SkipScenario):
        prepare(subjects, CORR, (), "없음", "완결자만", "미적용")


def test_corr_outlier_excludes_union_of_x_and_y():
    subjects = [subject("S%d" % i, x=float(i), y=float(i)) for i in range(1, 11)]
    subjects.append(subject("SX", x=1000.0, y=5.0))
    subjects.append(subject("SY", x=5.0, y=-1000.0))
    prepared = prepare(subjects, CORR, (), "IQR1.5", "완결자만", "미적용")
    assert "SX" not in prepared.ids
    assert "SY" not in prepared.ids


def test_paired_outliers_use_difference_scores():
    subjects = [subject("S%d" % i, pre=10.0, post=9.0) for i in range(1, 11)]
    subjects.append(subject("SX", pre=10.0, post=-90.0))
    prepared = prepare(subjects, PAIRED, (), "±3SD", "완결자만", "미적용")
    assert "SX" not in prepared.ids


def test_two_group_outliers_are_computed_within_group():
    """군 간 차이 자체를 이상치로 오인하면 안 된다."""
    a = [100.0, 101.0, 102.0, 103.0, 104.0]
    b = [1.0, 2.0, 3.0, 4.0, 5.0]
    subjects = two_group_subjects(a, b)
    prepared = prepare(subjects, TWO_GROUP, LEVELS, "±3SD", "완결자만", "미적용")
    assert prepared.n == 10


def test_prepared_preserves_input_order():
    subjects = two_group_subjects([1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0])
    prepared = prepare(subjects, TWO_GROUP, LEVELS, "없음", "완결자만", "미적용")
    assert prepared.ids_a == ["A0", "A1", "A2", "A3"]
    assert prepared.ids_b == ["B0", "B1", "B2", "B3"]


def test_excluded_records_subject_ids_and_reasons():
    a = [10.0 + 0.1 * i for i in range(15)] + [900.0]
    b = [10.0 + 0.1 * i for i in range(16)]
    subjects = two_group_subjects(a, b)
    prepared = prepare(subjects, TWO_GROUP, LEVELS, "±3SD", "완결자만", "미적용")
    assert prepared.excluded
    sid, reason = prepared.excluded[0]
    assert sid.startswith("A")
    assert "이상치" in reason


def test_sd_rule_impossibility_is_confessed():
    """n ≤ 10 에서 ±3SD 는 수학적으로 아무도 못 뺀다 — 조용히 넘기지 않는다."""
    subjects = two_group_subjects([10.0, 10.1, 10.2, 10.3, 900.0],
                                  [10.0, 10.1, 10.2, 10.3, 10.4])
    prepared = prepare(subjects, TWO_GROUP, LEVELS, "±3SD", "완결자만", "미적용")
    assert prepared.excluded == []
    assert any("±3SD" in note for note in prepared.notes)


@pytest.mark.parametrize("n,possible", [
    (2, False), (5, False), (10, False), (11, True), (30, True), (100, True),
])
def test_max_possible_z_threshold(n, possible):
    assert (max_possible_z(n) >= 3.0) is possible


def test_sd_rule_note_is_none_for_large_samples():
    assert sd_rule_note(40) is None
    assert sd_rule_note(6) is not None
