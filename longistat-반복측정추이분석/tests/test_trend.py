"""Polynomial trend contrasts and per-subject slopes."""

from __future__ import annotations

import math

import pytest

from longistat.analyze import Options, analyze
from longistat.anova import rm_anova
from longistat.dataio import Panel
from longistat.report import render_csv, render_json, render_markdown, render_text
from longistat.trend import (orthogonal_polynomials, resolve_time_values,
                             trend_analysis, trend_shape)


def panel(values, groups=None, times=("T0", "T1", "T2")):
    return Panel(subjects=[f"s{i}" for i in range(len(values))],
                 times=list(times), values=[list(r) for r in values],
                 groups=None if groups is None else list(groups),
                 value_name="score")


# --------------------------------------------------------------------------
# orthogonal polynomials
# --------------------------------------------------------------------------

def test_equal_spacing_reproduces_textbook_weights():
    """k=3 equally spaced: linear ∝ (-1, 0, 1), quadratic ∝ (1, -2, 1)."""
    poly = orthogonal_polynomials([1.0, 2.0, 3.0], 2)
    lin = [row[0] for row in poly]
    quad = [row[1] for row in poly]
    ratio = lin[2] / (1 / math.sqrt(2))
    assert lin == pytest.approx([-1 / math.sqrt(2), 0.0, 1 / math.sqrt(2)])
    assert abs(ratio) == pytest.approx(1.0)
    expect_q = [1, -2, 1]
    scale = quad[0] / expect_q[0]
    assert quad == pytest.approx([w * scale for w in expect_q])
    assert math.fsum(w * w for w in quad) == pytest.approx(1.0)


def test_columns_are_orthonormal_and_orthogonal_to_one():
    poly = orthogonal_polynomials([0.0, 4.0, 12.0, 24.0], 3)
    cols = [[row[c] for row in poly] for c in range(3)]
    for c, col in enumerate(cols):
        assert math.fsum(col) == pytest.approx(0.0, abs=1e-12)
        assert math.fsum(v * v for v in col) == pytest.approx(1.0)
        for other in cols[c + 1:]:
            assert math.fsum(a * b for a, b in zip(col, other)) == \
                pytest.approx(0.0, abs=1e-12)


def test_unequal_spacing_changes_the_linear_contrast():
    even = orthogonal_polynomials([0.0, 4.0, 8.0], 1)
    uneven = orthogonal_polynomials([0.0, 4.0, 24.0], 1)
    assert [r[0] for r in even] != pytest.approx([r[0] for r in uneven])


def test_order_capped_by_number_of_timepoints():
    poly = orthogonal_polynomials([1.0, 2.0, 3.0], 3)
    assert len(poly[0]) == 2                       # k-1, not 3


def test_constant_positions_rejected():
    with pytest.raises(ValueError):
        orthogonal_polynomials([2.0, 2.0, 2.0], 2)


def test_huge_offsets_stay_precise():
    """Epoch-scale visit stamps must not destroy the cubic column."""
    base = 1.7e9
    poly = orthogonal_polynomials([base, base + 86400, base + 172800,
                                   base + 259200], 3)
    for c in range(3):
        col = [row[c] for row in poly]
        assert math.fsum(v * v for v in col) == pytest.approx(1.0)
        assert math.fsum(col) == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------
# sums of squares partition the omnibus time effect
# --------------------------------------------------------------------------

DATA = [
    [10.0, 8.0, 5.0], [12.0, 9.0, 7.0], [11.0, 10.0, 6.0], [9.0, 7.0, 4.0],
    [14.0, 12.0, 11.0], [13.0, 13.0, 12.0], [15.0, 14.0, 10.0],
    [12.0, 11.0, 11.0],
]
ARMS = ["A", "A", "A", "A", "B", "B", "B", "B"]


def test_contrast_ss_sums_to_time_ss_one_group():
    p = panel(DATA)
    an = rm_anova(p.matrix(), p.times)
    tr = trend_analysis(p)
    time_ss = math.fsum(e.ss for e in tr.effects if e.scope == "시점")
    assert time_ss == pytest.approx(an.effect("시점(시간)").ss)
    # Each contrast keeps its *own* error term (df = N − g), which is exactly
    # why no sphericity correction applies to it; the omnibus pools them
    # (df = (N − g)·d), and the pieces add back up.
    assert all(e.df2 == len(DATA) - 1 for e in tr.effects)
    ss_err = math.fsum(e.ss * (1.0 / e.partial_eta2 - 1.0) for e in tr.effects)
    assert ss_err == pytest.approx(an.ss_error_within)
    assert an.effect("시점(시간)").df2 == pytest.approx(
        math.fsum(e.df2 for e in tr.effects))


def test_contrast_ss_sums_to_time_and_interaction_ss_two_groups():
    p = panel(DATA, ARMS)
    an = rm_anova(p.matrix(), p.times, p.groups)
    tr = trend_analysis(p)
    assert math.fsum(e.ss for e in tr.effects if e.scope == "시점") == \
        pytest.approx(an.effect("시점(시간)").ss)
    assert math.fsum(e.ss for e in tr.effects if e.scope == "그룹 × 시점") == \
        pytest.approx(an.effect("그룹 × 시점").ss)


def test_unbalanced_groups_still_partition_the_omnibus():
    values = [[10.0, 8.0, 5.0], [12.0, 9.0, 7.0], [11.0, 10.0, 6.0],
              [9.0, 7.0, 4.0], [13.5, 11.0, 9.0],
              [14.0, 12.0, 11.0], [13.0, 13.0, 12.0], [15.0, 14.0, 10.0]]
    arms = ["A"] * 5 + ["B"] * 3
    p = panel(values, arms)
    an = rm_anova(p.matrix(), p.times, p.groups)
    tr = trend_analysis(p)
    assert math.fsum(e.ss for e in tr.effects if e.scope == "시점") == \
        pytest.approx(an.effect("시점(시간)").ss)
    assert math.fsum(e.ss for e in tr.effects if e.scope == "그룹 × 시점") == \
        pytest.approx(an.effect("그룹 × 시점").ss)


FIVE = [[10.0, 9.0, 7.0, 6.0, 6.5], [12.0, 10.0, 9.0, 7.0, 7.5],
        [11.0, 11.0, 8.0, 8.0, 7.0], [9.0, 7.0, 7.0, 5.0, 6.0],
        [13.0, 12.0, 10.0, 9.0, 9.5], [14.0, 12.0, 12.0, 10.0, 9.0]]
FIVE_TIMES = ("T0", "T1", "T2", "T3", "T4")


def test_five_visits_pool_the_unnamed_orders_into_a_residual_row():
    """Only 3 orders get names, but the quartic must not silently vanish —
    56 % of the time effect can live there."""
    p = panel(FIVE, times=FIVE_TIMES)
    an = rm_anova(p.matrix(), p.times)
    tr = trend_analysis(p)
    named = [e for e in tr.effects if not e.residual]
    resid = [e for e in tr.effects if e.residual]
    assert {e.order for e in named} == {1, 2, 3}
    assert len(resid) == 1 and resid[0].df1 == 1.0     # 4 df total − 3 named
    # The partition is still exact once the residual is counted.
    assert math.fsum(e.ss for e in tr.effects) == \
        pytest.approx(an.effect("시점(시간)").ss)
    assert math.fsum(e.ss for e in named) < an.effect("시점(시간)").ss
    assert any("잔여" in n for n in tr.notes)


def test_residual_row_is_excluded_from_the_holm_family_and_the_verdict():
    tr = trend_analysis(panel(FIVE, times=FIVE_TIMES))
    resid = [e for e in tr.effects if e.residual][0]
    assert resid.p_adj == resid.p_raw or math.isnan(resid.p_raw)
    # trend_shape only ever speaks about linear/quadratic
    assert "잔여" not in trend_shape(tr.effects, 0.05)


def test_six_visits_residual_carries_two_df():
    rows = [r + [r[-1] - 0.5] for r in FIVE]
    p = panel(rows, times=FIVE_TIMES + ("T5",))
    an = rm_anova(p.matrix(), p.times)
    tr = trend_analysis(p)
    resid = [e for e in tr.effects if e.residual][0]
    assert resid.df1 == 2.0
    assert math.fsum(e.ss for e in tr.effects) == \
        pytest.approx(an.effect("시점(시간)").ss)


def test_four_visits_still_partition_exactly():
    rows = [[10.0, 9.0, 7.0, 6.0], [12.0, 10.0, 9.0, 7.0],
            [11.0, 11.0, 8.0, 8.0], [9.0, 7.0, 7.0, 5.0],
            [13.0, 12.0, 10.0, 9.0]]
    p = panel(rows, times=("T0", "T1", "T2", "T3"))
    an = rm_anova(p.matrix(), p.times)
    tr = trend_analysis(p)
    assert math.fsum(e.ss for e in tr.effects) == \
        pytest.approx(an.effect("시점(시간)").ss)
    assert not any("SS보다 작습니다" in n for n in tr.notes)


def test_linear_contrast_matches_a_one_sample_t_by_hand():
    """One group: F for the linear contrast is t² of the contrast scores."""
    p = panel(DATA)
    tr = trend_analysis(p)
    lin = [e for e in tr.effects if e.order == 1][0]
    w = [-1 / math.sqrt(2), 0.0, 1 / math.sqrt(2)]
    scores = [sum(wi * v for wi, v in zip(w, row)) for row in DATA]
    n = len(scores)
    m = math.fsum(scores) / n
    var = math.fsum((s - m) ** 2 for s in scores) / (n - 1)
    t = m / math.sqrt(var / n)
    assert lin.f == pytest.approx(t * t)
    assert lin.df1 == 1 and lin.df2 == n - 1


def test_single_df_contrasts_ignore_sphericity():
    """Every contrast line carries exactly 1 numerator df, by construction."""
    tr = trend_analysis(panel(DATA, ARMS))
    assert all(e.df1 == 1.0 for e in tr.effects if e.scope == "시점")


# --------------------------------------------------------------------------
# per-subject slopes
# --------------------------------------------------------------------------

def test_slope_matches_hand_computed_ols():
    p = panel([[10.0, 8.0, 6.0], [10.0, 9.0, 8.0]], times=("0", "1", "2"))
    tr = trend_analysis(p, time_values=[0.0, 1.0, 2.0])
    row = [s for s in tr.slopes][0]
    assert row.mean_slope == pytest.approx((-2.0 + -1.0) / 2)


def test_slope_uses_subjects_with_a_missing_middle_visit():
    values = [[10.0, 8.0, 6.0], [12.0, None, 8.0], [9.0, 7.0, None]]
    tr = trend_analysis(panel(values), time_values=[0.0, 1.0, 2.0])
    row = tr.slopes[0]
    assert row.n == 3                     # completers alone would be 1
    assert row.min_points == 2
    assert any("관측된 시점만" in n for n in tr.notes)


def test_subject_with_one_observation_is_skipped_not_crashed():
    values = [[10.0, 8.0, 6.0], [12.0, None, None], [9.0, 7.0, 5.0]]
    tr = trend_analysis(panel(values), time_values=[0.0, 1.0, 2.0])
    assert tr.slopes[0].n == 2


def test_slope_units_scale_with_time_values():
    values = [[10.0, 8.0, 6.0], [12.0, 10.0, 8.0]]
    weeks = trend_analysis(panel(values), time_values=[0.0, 4.0, 8.0])
    steps = trend_analysis(panel(values), time_values=[0.0, 1.0, 2.0])
    assert weeks.slopes[0].mean_slope == pytest.approx(
        steps.slopes[0].mean_slope / 4.0)


def test_group_slope_contrast_direction():
    values = [[10.0, 8.0, 6.0], [11.0, 9.0, 7.0], [12.0, 10.0, 8.0],
              [10.0, 10.0, 10.0], [11.0, 11.2, 11.0], [12.0, 12.0, 12.1]]
    tr = trend_analysis(panel(values, ["A"] * 3 + ["B"] * 3),
                        time_values=[0.0, 1.0, 2.0])
    con = tr.slope_contrasts[0]
    assert con.slope_a == pytest.approx(-2.0)
    assert con.diff < 0 and con.p < 0.05


# --------------------------------------------------------------------------
# visit spacing resolution
# --------------------------------------------------------------------------

def test_time_values_read_from_labels():
    notes = []
    vals, source = resolve_time_values(["0", "4", "12"], None, notes)
    assert vals == [0.0, 4.0, 12.0] and "이름" in source and not notes


def test_labels_with_units_are_parsed():
    notes = []
    vals, source = resolve_time_values(["wk0", "wk4", "wk12"], None, notes)
    assert vals == [0.0, 4.0, 12.0] and not notes


def test_unparseable_labels_fall_back_to_equal_spacing_with_a_note():
    notes = []
    vals, source = resolve_time_values(["기저", "4주", "8주"], None, notes)
    assert vals == [1.0, 2.0, 3.0]
    assert "등간격" in source
    assert notes and "--time-values" in notes[0]


def test_non_increasing_labels_fall_back():
    notes = []
    vals, _ = resolve_time_values(["V3", "V1", "V2"], None, notes)
    assert vals == [1.0, 2.0, 3.0] and notes


def test_explicit_values_must_increase():
    with pytest.raises(ValueError, match="증가"):
        resolve_time_values(["a", "b", "c"], [0.0, 8.0, 4.0], [])


def test_explicit_values_must_match_count():
    with pytest.raises(ValueError, match="개수"):
        resolve_time_values(["a", "b", "c"], [0.0, 4.0], [])


def test_explicit_values_reject_nan():
    with pytest.raises(ValueError, match="nan"):
        resolve_time_values(["a", "b", "c"], [0.0, float("nan"), 4.0], [])


# --------------------------------------------------------------------------
# edges
# --------------------------------------------------------------------------

def test_two_timepoints_produce_no_trend_section():
    p = panel([[1.0, 2.0], [3.0, 5.0]], times=("pre", "post"))
    assert trend_analysis(p) is None


def test_all_missing_after_baseline_returns_none():
    p = panel([[1.0, None, None], [2.0, None, None]])
    assert trend_analysis(p) is None


def test_constant_data_does_not_blow_up():
    p = panel([[5.0] * 3 for _ in range(4)])
    tr = trend_analysis(p)
    assert tr is not None
    assert all(math.isnan(e.f) for e in tr.effects)


def test_single_completer_still_reports_slopes():
    values = [[10.0, 8.0, 6.0], [12.0, None, 8.0], [9.0, None, 5.0]]
    tr = trend_analysis(panel(values), time_values=[0.0, 1.0, 2.0])
    assert tr.slopes and any("부족" in n for n in tr.notes)


def test_trend_shape_wording():
    tr = trend_analysis(panel(DATA))
    assert trend_shape(tr.effects, 0.05)
    assert trend_shape([], 0.05) == ""


# --------------------------------------------------------------------------
# integration through analyze()/report
# --------------------------------------------------------------------------

def test_trend_flows_into_every_output_format():
    a = analyze(panel(DATA, ARMS),
                Options(time_values=[0.0, 4.0, 8.0], time_unit="주"))
    assert a.trend is not None
    text = render_text(a)
    assert "[4b] 시점 추세" in text and "score/주" in text
    assert "선형(linear)" in render_markdown(a)
    assert "trend_contrast" in render_csv(a)
    assert "subject_slope" in render_csv(a)
    import json
    payload = json.loads(render_json(a))
    assert payload["trend"]["time_source"] == "지정한 값"
    assert payload["trend"]["time_values"] == [0.0, 4.0, 8.0]
    assert any("linear" in s for s in payload["apa"])


def test_no_trend_option_suppresses_the_section():
    a = analyze(panel(DATA, ARMS), Options(trend=False))
    assert a.trend is None
    assert "[4b]" not in render_text(a)


def test_equal_spacing_assumption_is_surfaced_as_a_warning():
    a = analyze(panel(DATA, ARMS, times=("기저", "4주", "8주")))
    assert any("등간격" in w for w in a.warnings)
