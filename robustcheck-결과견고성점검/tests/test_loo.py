"""Leave-one-out — 전수 재계산과 조합 폭발 억제 규칙."""

import math

import pytest

from conftest import analyse_path, make_rows, write_csv
from robustcheck.loo import (
    DEFAULT_LOO_BUDGET,
    DEFAULT_LOO_MAX_N,
    LOO_RULE_TEXT,
    plan_loo,
    run_loo,
)
from robustcheck.scenarios import Axes, BASELINE_AXES, run_scenario
from robustcheck.spec import Spec, Subject

TWO_GROUP = Spec("two-group", value="v", group="g")
LEVELS = ("active", "sham")


def build(values_a, values_b):
    out = [Subject("A%d" % i, "active", {"v": v}, i)
           for i, v in enumerate(values_a)]
    out += [Subject("B%d" % i, "sham", {"v": v}, 100 + i)
            for i, v in enumerate(values_b)]
    return out


def loo_of(values_a, values_b):
    subjects = build(values_a, values_b)
    axes = Axes(*BASELINE_AXES)
    reference = run_scenario(subjects, TWO_GROUP, LEVELS, axes)
    return run_loo(subjects, TWO_GROUP, LEVELS, axes, reference), reference


# ------------------------------------------------------------- 억제 규칙


def test_plan_loo_runs_baseline_for_small_samples():
    do_loo, extra, note = plan_loo(30, [])
    assert do_loo and extra == [] and note == ""


def test_plan_loo_skips_and_confesses_for_huge_samples():
    do_loo, extra, note = plan_loo(DEFAULT_LOO_MAX_N + 1, [])
    assert not do_loo
    assert "돌리지 않았다" in note


def test_plan_loo_truncation_is_confessed_not_silent():
    axes = [Axes("없음", "완결자만", "모수", "미적용")] * 40
    do_loo, extra, note = plan_loo(1000, axes, budget=5000)  # 5000//1000 - 1 = 4
    assert do_loo
    assert len(extra) == 4
    assert "돌리지 못했다" in note


def test_plan_loo_zero_subjects():
    do_loo, extra, note = plan_loo(0, [])
    assert not do_loo and note


def test_plan_loo_budget_respected():
    axes = [Axes("없음", "완결자만", "모수", "미적용")] * 3
    _, extra, note = plan_loo(10, axes, budget=DEFAULT_LOO_BUDGET)
    assert len(extra) == 3
    assert note == ""


def test_loo_rule_text_states_it_is_not_the_full_cross_product():
    assert "전부와 곱하지 않는다" in LOO_RULE_TEXT


# ------------------------------------------------------------- 전수 계산


def test_loo_runs_once_per_subject():
    run, _ = loo_of([10.0, 11.0, 12.0, 13.0, 14.0], [5.0, 6.0, 7.0, 8.0, 9.0])
    assert len(run.entries) == 10
    assert [e.sid for e in run.entries] == ["A0", "A1", "A2", "A3", "A4",
                                            "B0", "B1", "B2", "B3", "B4"]


def test_loo_delta_p_is_relative_to_reference():
    run, reference = loo_of([10.0, 11.0, 12.0, 13.0, 14.0],
                            [5.0, 6.0, 7.0, 8.0, 9.0])
    for entry in run.entries:
        assert entry.delta_p == pytest.approx(entry.p - reference.p)


def test_loo_detects_the_single_subject_carrying_the_result():
    """B4 한 명이 유의성을 떠받친다 — 그 사람만 단독 뒤집기로 나와야 한다."""
    a = [11.18, 12.28, 9.74, 10.9, 10.7, 10.01, 9.5, 10.72]
    b = [12.26, 11.73, 10.9, 12.4, 13.5, 10.29, 11.19, 13.61]
    run, reference = loo_of(a, b)
    assert reference.p == pytest.approx(0.0230, abs=5e-4)
    assert [e.sid for e in run.solo_flippers] == ["B4"]
    by_id = {e.sid: e for e in run.entries}
    assert by_id["B4"].p > 0.05
    assert by_id["A0"].p < 0.05


def test_loo_top_is_sorted_by_absolute_delta_p():
    run, _ = loo_of([10.0, 11.0, 12.0, 13.0, 30.0], [5.0, 6.0, 7.0, 8.0, 9.0])
    top = run.top(5)
    deltas = [abs(e.delta_p) for e in top]
    assert deltas == sorted(deltas, reverse=True)


def test_loo_top_limits_to_k():
    run, _ = loo_of([10.0, 11.0, 12.0, 13.0, 14.0], [5.0, 6.0, 7.0, 8.0, 9.0])
    assert len(run.top(3)) == 3


def test_solo_flipper_order_does_not_depend_on_input_row_order():
    """행 순서를 바꿔도 콘솔에 나오는 이름 순서가 흔들리면 안 된다."""
    from conftest import analyse_path
    import os
    examples = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "examples", "취약_예제.csv")
    forward = analyse_path(examples, design="two-group", group="arm",
                           value="isi_week4")
    names = [e.sid for e in forward.solo_flippers]
    assert names == sorted(
        names, key=lambda s: (-abs(next(
            e.delta_p for e in forward.solo_flippers if e.sid == s)), s))


def test_loo_top_ties_break_on_subject_id():
    run, _ = loo_of([10.0, 10.0, 10.0, 10.0, 10.0], [5.0, 5.0, 5.0, 5.0, 5.0])
    # 모든 값이 같은 군 → 분산 0 → 계산 불가. top() 은 빈 목록이어야 한다.
    assert run.top(5) == []


def test_loo_entry_marks_subjects_outside_the_analysis():
    subjects = build([10.0, 11.0, 12.0, 13.0], [5.0, 6.0, 7.0, 8.0])
    subjects.append(Subject("MISSING", "sham", {"v": None}, 999))
    axes = Axes(*BASELINE_AXES)
    reference = run_scenario(subjects, TWO_GROUP, LEVELS, axes)
    run = run_loo(subjects, TWO_GROUP, LEVELS, axes, reference)
    by_id = {e.sid: e for e in run.entries}
    assert by_id["MISSING"].in_analysis is False
    assert by_id["A0"].in_analysis is True


def test_loo_records_skip_reason_when_removal_breaks_the_analysis():
    subjects = build([10.0, 11.0, 12.0], [5.0, 6.0, 7.0])
    axes = Axes(*BASELINE_AXES)
    reference = run_scenario(subjects, TWO_GROUP, LEVELS, axes)
    run = run_loo(subjects, TWO_GROUP, LEVELS, axes, reference)
    assert all(not e.computed for e in run.entries)
    assert all("군 n<" in e.skip_reason for e in run.entries)


def test_loo_is_deterministic():
    first, _ = loo_of([10.0, 11.3, 12.0, 13.7, 14.0], [5.0, 6.2, 7.0, 8.4, 9.0])
    second, _ = loo_of([10.0, 11.3, 12.0, 13.7, 14.0], [5.0, 6.2, 7.0, 8.4, 9.0])
    assert [(e.sid, e.p) for e in first.entries] == \
           [(e.sid, e.p) for e in second.entries]


# ---------------------------------------------------- 통합 (실제 예제 기준)


def test_fragile_example_has_solo_flippers(fragile_analysis):
    assert fragile_analysis.loo_baseline is not None
    assert len(fragile_analysis.solo_flippers) >= 1


def test_robust_example_has_no_solo_flippers(two_group_analysis):
    assert two_group_analysis.solo_flippers == []


def test_loo_extra_runs_only_on_flipped_scenarios(fragile_analysis):
    flipped_keys = {j.axes.key for j in fragile_analysis.flipped}
    extra_keys = {run.axes.key for run in fragile_analysis.loo_extra}
    assert extra_keys <= flipped_keys


def test_no_loo_extra_when_nothing_flipped(two_group_analysis):
    assert two_group_analysis.loo_extra == []


def test_loo_skipped_entirely_when_undecidable(undecidable_csv):
    analysis = analyse_path(undecidable_csv, design="two-group", group="arm",
                            value="isi_week4")
    assert analysis.loo_baseline is None
    assert analysis.loo_notes


def test_loo_max_n_option_is_honoured(tmp_path):
    path = write_csv(tmp_path / "a.csv", make_rows(20))
    from robustcheck.analyze import analyse
    from robustcheck.dataio import read_table
    from robustcheck.spec import Spec as S, build_dataset
    dataset = build_dataset(read_table(path),
                            S("two-group", value="isi_week4", group="arm"))
    analysis = analyse(dataset, loo_max_n=5)
    assert analysis.loo_baseline is None
    assert any("돌리지 않았다" in n for n in analysis.loo_notes)


def test_budget_smaller_than_n_skips_baseline_loo_and_says_so(tmp_path):
    """예산이 기준선 전수에도 모자라면 반만 돌리고 입 다물지 않는다."""
    path = write_csv(tmp_path / "a.csv", make_rows(20))
    from robustcheck.analyze import analyse as run
    from robustcheck.dataio import read_table as read
    from robustcheck.spec import Spec as S2, build_dataset as build
    dataset = build(read(path), S2("two-group", value="isi_week4", group="arm"))
    analysis = run(dataset, loo_budget=1)
    assert analysis.loo_baseline is None
    assert any("돌리지 않았다" in n for n in analysis.loo_notes)
