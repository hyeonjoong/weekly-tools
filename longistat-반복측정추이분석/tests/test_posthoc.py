"""Post-hoc, change-from-baseline and descriptive/missingness behaviour."""

from __future__ import annotations

import math

import pytest

from longistat.basics import holm
from longistat.dataio import Panel
from longistat.describe import ALL_LABEL, describe, profile_missing
from longistat.nonparam import friedman, friedman_by_group
from longistat.posthoc import between_at_time, change_analysis, pairwise_times


def _panel(values, groups=None, times=("기저", "4주", "8주")):
    return Panel(subjects=[f"S{i}" for i in range(len(values))],
                 times=list(times), values=[list(v) for v in values],
                 groups=list(groups) if groups else None,
                 group_name="군" if groups else None, value_name="점수")


def test_describe_is_available_case_per_cell():
    p = _panel([[10, 8, None], [12, None, 6], [14, 10, 8]])
    cells = {(c.group, c.time): c for c in describe(p)}
    assert cells[(ALL_LABEL, "기저")].n == 3
    assert cells[(ALL_LABEL, "4주")].n == 2
    assert cells[(ALL_LABEL, "4주")].n_missing == 1
    assert math.isclose(cells[(ALL_LABEL, "8주")].mean, 7.0)


def test_describe_adds_group_rows_only_when_grouped():
    p = _panel([[1, 2, 3], [4, 5, 6]])
    assert {c.group for c in describe(p)} == {ALL_LABEL}
    pg = _panel([[1, 2, 3], [4, 5, 6]], ["A", "B"])
    assert {c.group for c in describe(pg)} == {ALL_LABEL, "A", "B"}


def test_missing_profile_detects_monotone_dropout():
    p = _panel([[1, 2, 3], [1, 2, None], [1, None, None]])
    rep = profile_missing(p)
    assert rep.monotone is True
    assert rep.n_complete == 1
    assert rep.per_time_observed == {"기저": 3, "4주": 2, "8주": 1}
    assert any("완전자료" in w for w in rep.warnings)


def test_missing_profile_flags_intermittent_gaps():
    p = _panel([[1, None, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3]])
    rep = profile_missing(p)
    assert rep.monotone is False
    assert any("단조" in w for w in rep.warnings)


def test_pairwise_uses_every_subject_with_both_visits():
    p = _panel([[10, 8, 6], [12, 9, None], [14, 10, 8], [16, 12, 10]])
    rows = {(r.time_a, r.time_b): r for r in pairwise_times(p)}
    assert rows[("기저", "4주")].n == 4
    assert rows[("기저", "8주")].n == 3
    assert rows[("4주", "8주")].n == 3
    assert math.isclose(rows[("기저", "4주")].mean_diff, -3.25)


def test_pairwise_holm_adjusts_within_each_group_scope():
    values = [[10, 8, 6], [12, 9, 7], [14, 10, 8], [16, 12, 11],
              [11, 11, 11], [13, 13, 12], [15, 14, 15], [17, 17, 16]]
    groups = ["A"] * 4 + ["B"] * 4
    rows = pairwise_times(_panel(values, groups))
    for scope in ("A", "B", ALL_LABEL):
        subset = [r for r in rows if r.group == scope]
        assert len(subset) == 3
        assert [r.p_adj for r in subset] == pytest.approx(
            holm([r.p_raw for r in subset]), rel=1e-12)
        assert all(r.p_adj > r.p_raw for r in subset)


def test_change_analysis_between_group_contrast_is_the_difference_in_change():
    values = [[20, 12], [20, 10], [20, 14], [20, 18], [20, 17], [20, 19]]
    groups = ["능동"] * 3 + ["가짜"] * 3
    ca = change_analysis(_panel(values, groups, times=("기저", "8주")), 0)
    within = {r.group: r for r in ca.within}
    assert math.isclose(within["능동"].mean_change, -8.0)
    assert math.isclose(within["가짜"].mean_change, -2.0)
    con = ca.between[0]
    assert math.isclose(con.diff, -6.0)
    assert con.ci_low < con.diff < con.ci_high


def test_change_analysis_respects_a_non_first_baseline():
    p = _panel([[1, 10, 12], [2, 20, 23], [3, 30, 34]])
    ca = change_analysis(p, baseline=1)
    assert ca.baseline == "4주"
    times = {r.time for r in ca.within}
    assert times == {"기저", "8주"}


def test_change_analysis_rejects_a_bad_baseline_index():
    with pytest.raises(ValueError):
        change_analysis(_panel([[1, 2, 3], [4, 5, 6]]), baseline=9)


def test_between_at_time_is_empty_without_groups():
    assert between_at_time(_panel([[1, 2, 3], [4, 5, 6]])) == []


def test_between_at_time_reports_each_visit():
    values = [[10, 8, 6], [12, 9, 7], [20, 19, 18], [22, 21, 20]]
    rows = between_at_time(_panel(values, ["A", "A", "B", "B"]))
    assert {r.time for r in rows} == {"기저", "4주", "8주"}
    first = [r for r in rows if r.time == "기저"][0]
    assert math.isclose(first.diff, 11.0 - 21.0)
    assert first.method.startswith("Welch")


def test_nonparametric_pairwise_reports_rank_effect_sizes():
    p = _panel([[10, 8, 6], [12, 9, 7], [14, 10, 8], [16, 12, 10]])
    rows = pairwise_times(p, nonparametric=True)
    assert all(r.effect_label == "rank-biserial r" for r in rows)
    assert all("Wilcoxon" in r.method for r in rows)


def test_friedman_hand_computed():
    """Rank sums 7/9/8 with n=4, k=3 → χ² = 12·2/48 = 0.5, W = 0.0625."""
    res = friedman([[1, 2, 3], [2, 3, 1], [3, 1, 2], [1, 3, 2]])
    assert res.rank_sums == [7.0, 9.0, 8.0]
    assert math.isclose(res.chi2, 0.5, rel_tol=1e-12)
    assert math.isclose(res.kendall_w, 0.0625, rel_tol=1e-12)
    assert res.df == 2 and not res.ties


def test_friedman_needs_three_timepoints():
    with pytest.raises(ValueError, match="3개 이상"):
        friedman([[1, 2], [2, 3], [3, 4]])
    with pytest.raises(ValueError):
        friedman([[1, 2, 3]])


def test_friedman_by_group_covers_overall_and_each_arm():
    values = [[1, 2, 3], [2, 3, 4], [3, 4, 5], [4, 5, 6]]
    out = friedman_by_group(_panel(values, ["A", "A", "B", "B"]))
    assert [r.group for r in out] == [ALL_LABEL, "A", "B"]
    assert friedman_by_group(_panel([[1, 2], [3, 4]], times=("a", "b"))) == []
