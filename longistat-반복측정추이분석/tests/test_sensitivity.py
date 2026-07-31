"""LOCF/BOCF missing-data sensitivity analysis."""

from __future__ import annotations

import json
import math

import pytest

from longistat.analyze import Options, _sensitivity_kinds, analyze
from longistat.dataio import Panel
from longistat.report import render_csv, render_json, render_markdown, render_text
from longistat.sensitivity import impute_panel, sensitivity_analysis


def panel(values, groups=None, times=("기저", "4주", "8주")):
    return Panel(subjects=[f"s{i}" for i in range(len(values))],
                 times=list(times), values=[list(r) for r in values],
                 groups=None if groups is None else list(groups),
                 value_name="ISI")


# --------------------------------------------------------------------------
# imputation mechanics
# --------------------------------------------------------------------------

def test_locf_carries_the_most_recent_observation():
    p = panel([[10.0, 7.0, None]])
    assert impute_panel(p, 0, "locf").values[0] == [10.0, 7.0, 7.0]


def test_locf_falls_back_to_baseline_when_nothing_between():
    p = panel([[10.0, None, None]])
    assert impute_panel(p, 0, "locf").values[0] == [10.0, 10.0, 10.0]


def test_bocf_always_uses_the_subjects_own_baseline():
    p = panel([[10.0, 7.0, None]])
    assert impute_panel(p, 0, "bocf").values[0] == [10.0, 7.0, 10.0]


def test_bocf_does_not_overwrite_observed_values():
    p = panel([[10.0, 7.0, 4.0]])
    assert impute_panel(p, 0, "bocf").values[0] == [10.0, 7.0, 4.0]


def test_missing_baseline_is_never_invented():
    p = panel([[None, 7.0, None]])
    for kind in ("locf", "bocf"):
        assert impute_panel(p, 0, kind).values[0] == [None, 7.0, None]


def test_baseline_column_itself_is_never_filled():
    p = panel([[None, 7.0, 5.0]])
    assert impute_panel(p, 0, "locf").values[0][0] is None


@pytest.mark.parametrize("kind", ["locf", "bocf"])
def test_visits_before_the_baseline_are_never_filled(kind):
    """Both methods must impute the same cells, or 대체 셀 counts mislead."""
    p = panel([[None, 10.0, None]], times=("선별", "기저", "8주"))
    assert impute_panel(p, 1, kind).values[0] == [None, 10.0, 10.0]


def test_gap_before_the_first_observation_stays_missing():
    p = panel([[None, None, 5.0]], times=("선별", "기저", "8주"))
    for kind in ("locf", "bocf"):
        assert impute_panel(p, 1, kind).values[0] == [None, None, 5.0]


def test_imputation_does_not_mutate_the_source_panel():
    p = panel([[10.0, None, None]])
    impute_panel(p, 0, "locf")
    assert p.values[0] == [10.0, None, None]


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        impute_panel(panel([[1.0, 2.0, 3.0]]), 0, "mmrm")


def test_out_of_range_baseline_rejected():
    with pytest.raises(ValueError):
        impute_panel(panel([[1.0, 2.0, 3.0]]), 9, "locf")


# --------------------------------------------------------------------------
# the analysis
# --------------------------------------------------------------------------

COMPLETE = [[20.0, 15.0, 10.0], [22.0, 16.0, 11.0], [19.0, 14.0, 9.0],
            [21.0, 17.0, 12.0], [20.0, 19.0, 19.0], [22.0, 21.0, 20.0],
            [19.0, 18.0, 18.0], [21.0, 20.0, 21.0]]
ARMS = ["A"] * 4 + ["B"] * 4


def test_no_missing_means_no_sensitivity_table():
    assert sensitivity_analysis(panel(COMPLETE, ARMS), 0) is None


def test_dropout_produces_three_rows_per_visit():
    values = [list(r) for r in COMPLETE]
    values[0][2] = None
    res = sensitivity_analysis(panel(values, ARMS), 0)
    assert res is not None
    at8 = [r for r in res.rows if r.time == "8주"]
    assert {r.kind for r in at8} == {"observed", "locf", "bocf"}
    assert [r.imputed for r in at8 if r.kind == "locf"] == [1]
    assert [r.imputed for r in at8 if r.kind == "observed"] == [0]


def test_locf_recovers_the_dropped_subject_in_n():
    values = [list(r) for r in COMPLETE]
    values[0][2] = None
    res = sensitivity_analysis(panel(values, ARMS), 0)
    obs = [r for r in res.rows if r.time == "8주" and r.kind == "observed"][0]
    locf = [r for r in res.rows if r.time == "8주" and r.kind == "locf"][0]
    assert locf.n == obs.n + 1


def test_bocf_estimate_is_hand_checkable():
    """One dropout, BOCF ⇒ that subject's change is exactly zero."""
    values = [[10.0, 6.0], [10.0, None], [10.0, 8.0]]
    res = sensitivity_analysis(panel(values, times=("기저", "8주")), 0)
    bocf = [r for r in res.rows if r.kind == "bocf"][0]
    assert bocf.estimate == pytest.approx((-4.0 + 0.0 + -2.0) / 3)
    obs = [r for r in res.rows if r.kind == "observed"][0]
    assert obs.estimate == pytest.approx(-3.0)


def test_ungrouped_panel_reports_the_within_group_change():
    values = [[10.0, 6.0, 4.0], [10.0, None, None], [10.0, 8.0, 6.0],
              [12.0, 9.0, 7.0]]
    res = sensitivity_analysis(panel(values), 0)
    assert res is not None and not res.grouped
    assert {r.contrast for r in res.rows} == {"전체"}


# Three completers who improved a lot, twenty who walked out.  The observed
# analysis is overwhelmingly significant on n = 3; carrying baseline forward
# dilutes it to nothing.  This is the whole point of the section.
DILUTED = ([[10.0, 5.0], [10.0, 4.8], [10.0, 5.2]]
           + [[10.0, None] for _ in range(20)])


def test_flips_detects_a_significance_reversal():
    res = sensitivity_analysis(panel(DILUTED, times=("기저", "8주")), 0)
    obs = [r for r in res.rows if r.kind == "observed"][0]
    bocf = [r for r in res.rows if r.kind == "bocf"][0]
    assert obs.p < 0.05 <= bocf.p
    flips = res.flips(0.05)
    assert flips and "유의성" in flips[0]


def test_flips_silent_when_everything_agrees():
    values = [list(r) for r in COMPLETE]
    values[4][2] = None          # a flat-arm subject; imputing changes little
    res = sensitivity_analysis(panel(values, ARMS), 0)
    assert res.flips(0.05) == []


def test_subjects_missing_baseline_are_reported_not_hidden():
    values = [[None, 5.0, 4.0], [10.0, 6.0, None], [10.0, 7.0, 5.0],
              [12.0, 8.0, 6.0]]
    res = sensitivity_analysis(panel(values), 0)
    assert any("기준시점이 결측인 1명" in n for n in res.notes)


def test_only_the_requested_method_is_run():
    values = [list(r) for r in COMPLETE]
    values[0][2] = None
    res = sensitivity_analysis(panel(values, ARMS), 0, kinds=["bocf"])
    assert {r.kind for r in res.rows} == {"observed", "bocf"}


def test_empty_kind_list_returns_none():
    values = [list(r) for r in COMPLETE]
    values[0][2] = None
    assert sensitivity_analysis(panel(values, ARMS), 0, kinds=[]) is None


def test_gap_that_cannot_be_filled_yields_no_table():
    """Only subjects with no baseline are missing data ⇒ nothing to impute."""
    values = [[None, None, None], [10.0, 7.0, 5.0], [12.0, 8.0, 6.0]]
    assert sensitivity_analysis(panel(values), 0) is None


# --------------------------------------------------------------------------
# option parsing and wiring
# --------------------------------------------------------------------------

@pytest.mark.parametrize("spec,expect", [
    ("auto", ["locf", "bocf"]),
    ("none", []),
    ("", []),
    ("locf", ["locf"]),
    ("BOCF", ["bocf"]),
    ("locf,bocf", ["locf", "bocf"]),
    ("locf,locf", ["locf"]),
])
def test_sensitivity_kind_parsing(spec, expect):
    assert _sensitivity_kinds(spec) == expect


def test_unknown_sensitivity_kind_is_rejected():
    with pytest.raises(ValueError, match="sensitivity"):
        _sensitivity_kinds("mmrm")


def test_sensitivity_flows_into_every_output_format():
    values = [list(r) for r in COMPLETE]
    values[0][2] = None
    values[5][1] = None
    a = analyze(panel(values, ARMS))
    assert a.sensitivity is not None
    text = render_text(a)
    assert "결측 대체 민감도" in text and "LOCF" in text and "BOCF" in text
    assert "LOCF" in render_markdown(a)
    assert "sensitivity" in render_csv(a)
    payload = json.loads(render_json(a))
    assert payload["sensitivity"]["kinds"] == ["locf", "bocf"]
    assert any("LOCF" in s for s in payload["apa"])


def test_sensitivity_off_leaves_the_report_unchanged():
    values = [list(r) for r in COMPLETE]
    values[0][2] = None
    a = analyze(panel(values, ARMS), Options(sensitivity="none"))
    assert a.sensitivity is None
    assert "결측 대체 민감도" not in render_text(a)


def test_conflicting_conclusions_become_a_top_level_warning():
    a = analyze(panel(DILUTED, times=("기저", "8주")))
    assert any("결측 대체 방법에 따라" in w for w in a.warnings)
    assert any("MMRM" in w for w in a.warnings)


def test_conflicting_conclusions_change_the_apa_sentence():
    a = analyze(panel(DILUTED, times=("기저", "8주")))
    from longistat.report import apa_sentences
    assert any("결론이 달라졌다" in s for s in apa_sentences(a))


def test_estimates_are_finite_where_reported():
    values = [list(r) for r in COMPLETE]
    values[0][2] = None
    res = sensitivity_analysis(panel(values, ARMS), 0)
    for r in res.rows:
        assert math.isfinite(r.estimate)
