"""집계 — 분석 단위가 '밤'이 아니라 '사람'이라는 점을 지키는지 확인."""

import pytest

from sleepdiary.aggregate import (
    compare_periods,
    period_levels,
    summarize_by_subject,
    summarize_group,
)
from sleepdiary.nightly import build_night

COLS = {
    "subject": "subject", "date": "date", "period": "period",
    "bedtime": "bedtime", "lights_off": "lights_off", "sol": "sol",
    "waso": "waso", "awakenings": "awakenings",
    "final_awake": "final_awake", "out_of_bed": "out_of_bed",
}


def make(subject, period, lights_off="23:00", final_awake="07:00",
         sol="20", waso="30", out_of_bed=None, bedtime=None, date="", row_no=2):
    row = {"subject": subject, "period": period, "date": date,
           "bedtime": bedtime or lights_off, "lights_off": lights_off,
           "sol": sol, "waso": waso, "awakenings": "2",
           "final_awake": final_awake, "out_of_bed": out_of_bed or final_awake}
    return build_night(row, COLS, row_no)


# ------------------------------------------------------- 대상자별 요약

def test_a_subject_with_many_nights_is_one_row_not_many():
    nights = [make("S1", "base") for _ in range(7)]
    summaries = summarize_by_subject(nights)
    assert len(summaries) == 1
    assert summaries[0].n_nights == 7


def test_subject_mean_is_the_mean_of_that_subjects_nights():
    nights = [make("S1", "base", sol="10"), make("S1", "base", sol="30")]
    s = summarize_by_subject(nights)[0]
    assert s.value("sol_min") == pytest.approx(20.0)


def test_invalid_nights_are_counted_as_excluded_and_left_out_of_the_mean():
    nights = [make("S1", "base", sol="10"),
              make("S1", "base", sol="-5"),        # 음수 → 오류
              make("S1", "base", sol="30")]
    s = summarize_by_subject(nights)[0]
    assert s.n_nights == 2 and s.n_excluded == 1
    assert s.value("sol_min") == pytest.approx(20.0)


def test_warned_nights_are_kept_in_the_mean_and_counted():
    nights = [make("S1", "base", sol="10"), make("S1", "base", sol="300")]
    s = summarize_by_subject(nights)[0]
    assert s.n_nights == 2 and s.n_warned == 1
    assert s.value("sol_min") == pytest.approx(155.0)


def test_periods_are_separate_rows_unless_told_otherwise():
    nights = [make("S1", "base"), make("S1", "post")]
    assert len(summarize_by_subject(nights, by_period=True)) == 2
    assert len(summarize_by_subject(nights, by_period=False)) == 1


def test_subject_midsleep_uses_a_circular_mean_across_midnight():
    """중앙수면시각이 자정 양쪽에 걸치면 산술평균은 정오로 튄다 — 원형평균이어야 한다."""
    nights = [make("S1", "base", lights_off="19:50", final_awake="03:50", sol="0", waso="0"),
              make("S1", "base", lights_off="20:10", final_awake="04:10", sol="0", waso="0")]
    per_night = [n.midsleep for n in nights]
    assert per_night == pytest.approx([1430.0, 10.0])       # 23:50 과 00:10
    s = summarize_by_subject(nights)[0]
    assert s.value("midsleep_min") == pytest.approx(0.0, abs=1e-6)   # 00:00
    # 산술평균이었다면 720분(정오)이 나온다 — 그 값이 아님을 못 박는다
    assert abs(s.value("midsleep_min") - 720.0) > 700


def test_group_midsleep_also_uses_a_circular_mean_across_midnight():
    """대상자 단계뿐 아니라 집단 단계에서도 원형평균이어야 한다."""
    nights = [make("A", "b", lights_off="19:50", final_awake="03:50", sol="0", waso="0"),
              make("B", "b", lights_off="20:10", final_awake="04:10", sol="0", waso="0")]
    group = summarize_group(summarize_by_subject(nights), "b")
    assert group.metrics["midsleep_min"]["mean"] == pytest.approx(0.0, abs=1e-6)
    assert abs(group.metrics["midsleep_min"]["mean"] - 720.0) > 700


def test_regularity_comes_from_midsleep_not_from_some_other_clock_metric():
    """규칙성의 출처가 바뀌어도 눈치채지 못하면 안 되므로 값을 못 박는다."""
    nights = [make("S1", "b", lights_off="23:00", sol="0", final_awake="07:00"),
              make("S1", "b", lights_off="23:00", sol="60", final_awake="05:00")]
    s = summarize_by_subject(nights)[0]
    assert s.regularity == s.metrics["midsleep_min"]["sd"]
    # 이 자료에서는 입면시각 SD와 수면중앙시각 SD가 서로 다르다
    assert s.metrics["onset_min"]["sd"] != pytest.approx(s.regularity)


def test_date_span_covers_the_first_and_last_night():
    import datetime
    nights = [make("S1", "b", date="2026-03-05"), make("S1", "b", date="2026-03-01"),
              make("S1", "b", date="2026-03-09")]
    s = summarize_by_subject(nights)[0]
    # 기본은 '기상한 아침' 해석이라 밤이 시작된 날은 하루 전
    assert s.date_span == (datetime.date(2026, 2, 28), datetime.date(2026, 3, 8))


def test_regularity_is_zero_for_an_identical_schedule_and_larger_when_it_varies():
    steady = summarize_by_subject([make("S1", "b") for _ in range(5)])[0]
    assert steady.regularity == pytest.approx(0.0, abs=1e-6)
    messy = summarize_by_subject([
        make("S1", "b", lights_off="21:00", final_awake="05:00"),
        make("S1", "b", lights_off="23:00", final_awake="07:00"),
        make("S1", "b", lights_off="02:00", final_awake="10:00"),
    ])[0]
    assert messy.regularity > 60


def test_subject_with_no_valid_nights_still_appears_with_zero_counts():
    s = summarize_by_subject([make("S1", "b", sol="-1")])[0]
    assert s.n_nights == 0 and s.n_excluded == 1
    assert s.value("tst_min") is None


# ------------------------------------------------------- 집단 요약

def test_group_n_is_the_number_of_subjects_not_the_number_of_nights():
    nights = [make(f"S{i}", "base") for i in range(1, 6) for _ in range(7)]
    summaries = summarize_by_subject(nights)
    group = summarize_group(summaries, "base")
    assert group.n_subjects == 5
    assert group.n_nights == 35
    assert group.metrics["tst_min"]["n"] == 5      # 35가 아니라 5


def test_group_mean_is_unweighted_across_subjects():
    """밤이 많은 사람이 집단 평균을 지배하면 안 된다 (사람별 평균을 다시 평균)."""
    many = [make("S1", "b", sol="10") for _ in range(20)]
    few = [make("S2", "b", sol="50")]
    group = summarize_group(summarize_by_subject(many + few), "b")
    assert group.metrics["sol_min"]["mean"] == pytest.approx(30.0)


def test_group_ci_is_present_with_two_or_more_subjects_and_absent_with_one():
    two = summarize_group(summarize_by_subject(
        [make("S1", "b", sol="10"), make("S2", "b", sol="50")]), "b")
    assert two.metrics["sol_min"]["ci_low"] is not None
    one = summarize_group(summarize_by_subject([make("S1", "b")]), "b")
    assert one.metrics["sol_min"]["ci_low"] is None


def test_group_of_nobody_does_not_crash():
    group = summarize_group([], "b")
    assert group.n_subjects == 0
    assert group.metrics["tst_min"]["n"] == 0


# ------------------------------------------------------- 시기 비교

def _two_period_data():
    nights = []
    # 6명, 시기마다 3박씩. followup 에서 SOL 이 사람마다 다르게 줄어든다.
    for i in range(1, 7):
        base_sol = 40 + i * 2
        for _ in range(3):
            nights.append(make(f"S{i}", "base", sol=str(base_sol)))
            nights.append(make(f"S{i}", "post", sol=str(base_sol - 10 - i)))
    return nights


def test_paired_comparison_uses_subject_level_means():
    comps = compare_periods(summarize_by_subject(_two_period_data()),
                            "base", "post", ["sol_min"])
    comp = comps[0]
    assert comp.n_pairs == 6                     # 36박이 아니라 6명
    assert comp.ttest.n == 6 and comp.ttest.df == 5
    assert comp.mean_b < comp.mean_a
    assert comp.ttest.p < 0.01                   # 일관된 감소


def test_difference_is_later_minus_earlier():
    comps = compare_periods(summarize_by_subject(_two_period_data()),
                            "base", "post", ["sol_min"])
    assert comps[0].ttest.mean_diff < 0          # SOL 이 줄었으므로 음수


def test_only_subjects_present_in_both_periods_are_paired():
    nights = _two_period_data()
    nights += [make("S99", "base", sol="60")]    # 한쪽만 있는 사람
    comps = compare_periods(summarize_by_subject(nights), "base", "post", ["sol_min"])
    assert comps[0].n_pairs == 6
    assert "S99" not in comps[0].subjects


def test_comparison_with_no_overlapping_subjects_reports_zero_pairs():
    nights = [make("A", "base"), make("B", "post")]
    comps = compare_periods(summarize_by_subject(nights), "base", "post", ["sol_min"])
    assert comps[0].n_pairs == 0 and comps[0].ttest is None


def test_a_single_paired_subject_gives_no_test_but_no_crash():
    nights = [make("A", "base", sol="40"), make("A", "post", sol="20")]
    comps = compare_periods(summarize_by_subject(nights), "base", "post", ["sol_min"])
    assert comps[0].n_pairs == 1 and comps[0].ttest is None


def test_clock_metrics_are_compared_with_a_circular_difference():
    """23:40 → 00:20 은 +40분 변화이지 -1400분이 아니다."""
    nights = []
    for i in range(1, 5):
        nights.append(make(f"S{i}", "base", lights_off="23:40", final_awake="07:40",
                           sol="0", waso="0"))
        nights.append(make(f"S{i}", "post", lights_off="00:20", final_awake="08:20",
                           sol="0", waso="0"))
    comps = compare_periods(summarize_by_subject(nights), "base", "post",
                            ["lights_off_min"])
    comp = comps[0]
    assert comp.circular is True
    assert comp.ttest.mean_diff == pytest.approx(40.0, abs=1e-6)


def test_period_levels_are_in_order_of_first_appearance():
    nights = [make("S1", "post"), make("S1", "base"), make("S2", "post")]
    assert period_levels(nights) == ["post", "base"]


def test_period_levels_ignores_blank_periods():
    assert period_levels([make("S1", "")]) == []
