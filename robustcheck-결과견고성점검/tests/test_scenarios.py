"""시나리오 격자와 실행 — 축 조합, 효과크기 계열, 건너뜀 사유."""

import math

import pytest

from conftest import analyse_path, make_rows, write_csv
from robustcheck.effects import (
    D_FAMILY,
    R_FAMILY,
    effect_grade,
    family_min_delta,
    hedges_g_paired,
    hedges_g_two_group,
    matched_rank_biserial,
    rank_biserial_two_group,
    sign_flip_floor,
)
from robustcheck.prep import LOG_LEVELS, MISSING_LEVELS, OUTLIER_LEVELS, TEST_LEVELS
from robustcheck.scenarios import (
    BASELINE_AXES,
    Axes,
    effect_family,
    grid,
    grid_description,
    run_scenario,
)
from robustcheck.spec import Spec, Subject


def subjects_two_group(values_a, values_b):
    out = [Subject("A%d" % i, "active", {"v": v}, i)
           for i, v in enumerate(values_a)]
    out += [Subject("B%d" % i, "sham", {"v": v}, 100 + i)
            for i, v in enumerate(values_b)]
    return out


TWO_GROUP = Spec("two-group", value="v", group="g")
PAIRED = Spec("paired", pre="pre", post="post")
CORR = Spec("corr", x="x", y="y")
LEVELS = ("active", "sham")


# ------------------------------------------------------------------ 격자


def test_two_group_grid_is_twelve():
    assert len(grid(TWO_GROUP)) == 12


def test_paired_grid_is_thirtysix():
    assert len(grid(PAIRED)) == 36


def test_corr_grid_is_twelve():
    assert len(grid(CORR)) == 12


def test_grid_has_no_duplicate_combinations():
    keys = [a.key for a in grid(PAIRED)]
    assert len(keys) == len(set(keys))


def test_grid_contains_baseline_exactly_once():
    for spec in (TWO_GROUP, PAIRED, CORR):
        assert sum(1 for a in grid(spec) if a.is_baseline) == 1


def test_grid_description_states_axis_counts():
    text = grid_description(PAIRED)
    assert "3" in text and "36" in text
    two = grid_description(TWO_GROUP)
    assert "paired 전용" in two
    assert two.endswith("12")


def test_baseline_axes_are_the_untouched_combination():
    assert BASELINE_AXES == (OUTLIER_LEVELS[0], MISSING_LEVELS[0],
                             TEST_LEVELS[0], LOG_LEVELS[0])


def test_axes_order_is_lexicographic_over_levels():
    axes = grid(PAIRED)
    assert [a.order for a in axes] == sorted(a.order for a in axes)


def test_axes_label_hides_missing_axis_for_non_paired():
    axes = Axes("없음", "완결자만", "모수", "미적용")
    assert "결측" not in axes.label(include_missing=False)
    assert "결측" in axes.label(include_missing=True)


# ------------------------------------------------------------ 효과크기 계열


def test_effect_family_by_design():
    assert effect_family("two-group") == D_FAMILY
    assert effect_family("paired") == D_FAMILY
    assert effect_family("corr") == R_FAMILY


@pytest.mark.parametrize("value,expected", [
    (0.0, "미미"), (0.19, "미미"), (0.2, "小"), (0.49, "小"),
    (0.5, "中"), (0.79, "中"), (0.8, "大"), (3.0, "大"), (-0.9, "大"),
])
def test_effect_grade_d_family(value, expected):
    assert effect_grade(value, D_FAMILY) == expected


@pytest.mark.parametrize("value,expected", [
    (0.05, "미미"), (0.1, "小"), (0.3, "中"), (0.5, "大"), (-0.6, "大"),
])
def test_effect_grade_r_family(value, expected):
    assert effect_grade(value, R_FAMILY) == expected


def test_effect_grade_of_nan():
    assert effect_grade(float("nan"), D_FAMILY) == "판정불가"


def test_min_delta_and_sign_floor_are_narrow_not_wide():
    assert family_min_delta(D_FAMILY) == 0.10
    assert family_min_delta(R_FAMILY) == 0.05
    assert sign_flip_floor(D_FAMILY) == 0.2
    assert sign_flip_floor(R_FAMILY) == 0.1


def test_hedges_g_is_smaller_than_cohen_d():
    a = [10.0, 11.0, 12.0, 13.0]
    b = [7.0, 8.0, 9.0, 10.0]
    g = hedges_g_two_group(a, b)
    # 보정 없이 계산한 Cohen d
    import statistics
    sp = math.sqrt((statistics.variance(a) + statistics.variance(b)) / 2.0)
    d = (statistics.mean(a) - statistics.mean(b)) / sp
    assert 0 < g < d


def test_hedges_g_sign_follows_first_group():
    assert hedges_g_two_group([5.0, 6.0, 7.0], [1.0, 2.0, 3.0]) > 0
    assert hedges_g_two_group([1.0, 2.0, 3.0], [5.0, 6.0, 7.0]) < 0


def test_hedges_g_paired_uses_difference_scores():
    g = hedges_g_paired([10.0, 11.0, 12.0, 13.0], [8.0, 9.0, 9.0, 12.0])
    assert g < 0


def test_rank_biserial_bounds():
    assert rank_biserial_two_group([1.0, 2.0], [3.0, 4.0]) == -1.0
    assert rank_biserial_two_group([3.0, 4.0], [1.0, 2.0]) == 1.0


def test_matched_rank_biserial_bounds():
    assert matched_rank_biserial([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) == 1.0
    assert matched_rank_biserial([4.0, 5.0, 6.0], [1.0, 2.0, 3.0]) == -1.0


def test_matched_rank_biserial_all_zero_raises():
    with pytest.raises(ValueError):
        matched_rank_biserial([1.0, 2.0], [1.0, 2.0])


def test_hedges_g_zero_variance_raises():
    with pytest.raises(ValueError):
        hedges_g_two_group([3.0, 3.0, 3.0], [3.0, 3.0, 3.0])


# ------------------------------------------------------------ 시나리오 실행


def test_run_scenario_computes_baseline():
    subjects = subjects_two_group([10.0, 11.0, 12.0, 13.0, 14.0],
                                  [5.0, 6.0, 7.0, 8.0, 9.0])
    result = run_scenario(subjects, TWO_GROUP, LEVELS,
                          Axes(*BASELINE_AXES))
    assert result.computed
    assert result.test.name == "Welch t"
    assert result.n == 10


def test_equal_var_switches_to_student_t():
    subjects = subjects_two_group([10.0, 11.0, 12.0, 13.0, 14.0],
                                  [5.0, 6.0, 7.0, 8.0, 9.0])
    result = run_scenario(subjects, TWO_GROUP, LEVELS, Axes(*BASELINE_AXES),
                          equal_var=True)
    assert result.test.name == "Student t"


def test_nonparametric_axis_uses_mann_whitney():
    subjects = subjects_two_group([10.0, 11.0, 12.0, 13.0, 14.0],
                                  [5.0, 6.0, 7.0, 8.0, 9.0])
    result = run_scenario(subjects, TWO_GROUP, LEVELS,
                          Axes("없음", "완결자만", "비모수", "미적용"))
    assert result.test.name == "Mann-Whitney U"
    assert result.native_effect_name == "rank-biserial"


def test_comparable_effect_does_not_change_with_test_axis_two_group():
    """검정을 바꿔도 비교용 효과크기는 그대로여야 한다(단위가 섞이면 거짓말)."""
    subjects = subjects_two_group([10.0, 11.0, 12.0, 13.0, 14.0],
                                  [5.0, 6.0, 7.0, 8.0, 9.0])
    parametric = run_scenario(subjects, TWO_GROUP, LEVELS, Axes(*BASELINE_AXES))
    nonparametric = run_scenario(subjects, TWO_GROUP, LEVELS,
                                 Axes("없음", "완결자만", "비모수", "미적용"))
    assert parametric.effect == pytest.approx(nonparametric.effect)


def test_corr_uses_its_own_coefficient_as_comparable_effect():
    subjects = [Subject("S%d" % i, None, {"x": float(i), "y": float(i) ** 2}, i)
                for i in range(1, 9)]
    parametric = run_scenario(subjects, CORR, (), Axes(*BASELINE_AXES))
    nonparametric = run_scenario(subjects, CORR, (),
                                 Axes("없음", "완결자만", "비모수", "미적용"))
    assert nonparametric.effect == pytest.approx(1.0)
    assert parametric.effect < 1.0


def test_corr_log_does_not_move_spearman():
    subjects = [Subject("S%d" % i, None, {"x": float(i), "y": float(i) ** 2 + i}, i)
                for i in range(1, 10)]
    plain = run_scenario(subjects, CORR, (),
                         Axes("없음", "완결자만", "비모수", "미적용"))
    logged = run_scenario(subjects, CORR, (),
                          Axes("없음", "완결자만", "비모수", "적용"))
    assert plain.effect == pytest.approx(logged.effect)
    assert plain.p == pytest.approx(logged.p)


def test_skipped_scenario_records_reason():
    subjects = subjects_two_group([1.0, -2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0])
    result = run_scenario(subjects, TWO_GROUP, LEVELS,
                          Axes("없음", "완결자만", "모수", "적용"))
    assert not result.computed
    assert result.skip_reason == "로그변환 불가"
    assert math.isnan(result.p)


def test_zero_variance_scenario_is_skipped_not_crashed():
    subjects = subjects_two_group([5.0] * 5, [5.0] * 5)
    result = run_scenario(subjects, TWO_GROUP, LEVELS, Axes(*BASELINE_AXES))
    assert not result.computed
    assert "검정 불가" in result.skip_reason


def test_singular_covariate_is_skipped():
    spec = Spec("two-group", value="v", group="g", covariate="c")
    subjects = [Subject("A%d" % i, "active", {"v": float(i), "c": 5.0}, i)
                for i in range(4)]
    subjects += [Subject("B%d" % i, "sham", {"v": float(i) + 1, "c": 5.0}, 10 + i)
                 for i in range(4)]
    result = run_scenario(subjects, spec, LEVELS, Axes(*BASELINE_AXES))
    assert not result.computed
    assert result.skip_reason == "공변량 모형 특이"


def test_covariate_parametric_uses_ancova():
    spec = Spec("two-group", value="v", group="g", covariate="c")
    subjects = [Subject("A%d" % i, "active", {"v": 10.0 + i, "c": 20.0 + i}, i)
                for i in range(5)]
    subjects += [Subject("B%d" % i, "sham", {"v": 6.0 + i, "c": 19.0 + 2 * i}, 10 + i)
                 for i in range(5)]
    result = run_scenario(subjects, spec, LEVELS, Axes(*BASELINE_AXES))
    assert result.test.name.startswith("ANCOVA")
    assert result.native_effect_name == "보정 Hedges g"


def test_covariate_nonparametric_uses_quade_not_silent_drop():
    spec = Spec("two-group", value="v", group="g", covariate="c")
    subjects = [Subject("A%d" % i, "active", {"v": 10.0 + i, "c": 20.0 + i}, i)
                for i in range(5)]
    subjects += [Subject("B%d" % i, "sham", {"v": 6.0 + i, "c": 19.0 + 2 * i}, 10 + i)
                 for i in range(5)]
    result = run_scenario(subjects, spec, LEVELS,
                          Axes("없음", "완결자만", "비모수", "미적용"))
    assert result.test.name == "Quade 순위 ANCOVA"


def test_run_scenario_is_deterministic():
    subjects = subjects_two_group([10.0, 11.5, 12.0, 13.0, 14.2],
                                  [5.0, 6.3, 7.0, 8.0, 9.1])
    first = run_scenario(subjects, TWO_GROUP, LEVELS, Axes(*BASELINE_AXES))
    second = run_scenario(subjects, TWO_GROUP, LEVELS, Axes(*BASELINE_AXES))
    assert (first.p, first.effect, first.n) == (second.p, second.effect, second.n)


def test_paired_grid_runs_all_missing_levels(tmp_path):
    rows = make_rows(14)
    rows[0][3] = ""
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, design="paired", pre="isi_baseline",
                            post="isi_week4")
    used = {j.axes.missing for j in analysis.judged if j.result.computed}
    assert used == set(MISSING_LEVELS)
