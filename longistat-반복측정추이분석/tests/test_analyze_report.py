"""End-to-end analysis orchestration plus report rendering."""

from __future__ import annotations

import json
import math

import pytest

from longistat.analyze import Options, analyze
from longistat.dataio import Panel
from longistat.report import (apa_sentences, fmt, fmt_df, fmt_es, fmt_p,
                              fmt_p_cell, render_csv, render_json, render_text,
                              table)


def _trial_panel(n_per=10):
    """Deterministic two-arm, three-visit panel with a clear interaction."""
    values, groups = [], []
    for i in range(n_per):
        base = 18 + (i % 5)
        values.append([base, base - 5 - (i % 3), base - 8 - (i % 4)])
        groups.append("능동")
    for i in range(n_per):
        base = 18 + (i % 5)
        values.append([base, base - 1 - (i % 2), base - 2 + (i % 3)])
        groups.append("가짜")
    return Panel(subjects=[f"S{i}" for i in range(2 * n_per)],
                 times=["기저", "4주", "8주"], values=values, groups=groups,
                 group_name="군", value_name="ISI")


def test_full_pipeline_produces_every_section():
    a = analyze(_trial_panel(), Options(mcid=5, direction="lower",
                                        reliability=0.9, recovery_cutoff=8))
    assert a.anova is not None and a.anova_error is None
    assert {e.name for e in a.anova.effects} == {"그룹(집단)", "시점(시간)",
                                                 "그룹 × 시점"}
    assert a.friedman and a.pairwise_param and a.pairwise_rank
    assert a.between and a.change_param.between
    assert a.responder is not None and a.rci is not None
    assert a.recommended in ("parametric", "nonparametric")
    assert a.recommendation_reason


def test_interaction_is_detected_in_a_designed_panel():
    a = analyze(_trial_panel())
    inter = a.anova.effect("그룹 × 시점")
    assert inter.p < 0.001
    assert inter.partial_eta2 > 0.3


def test_mcid_without_direction_is_rejected():
    with pytest.raises(ValueError, match="--direction"):
        analyze(_trial_panel(), Options(mcid=5))
    with pytest.raises(ValueError, match="--direction"):
        analyze(_trial_panel(), Options(reliability=0.9))


def test_named_baseline_is_honoured_and_validated():
    a = analyze(_trial_panel(), Options(baseline="4주"))
    assert a.baseline_index == 1
    with pytest.raises(ValueError, match="baseline"):
        analyze(_trial_panel(), Options(baseline="없는시점"))


def test_single_group_panel_skips_group_effects():
    p = Panel(subjects=[f"S{i}" for i in range(8)],
              times=["t1", "t2", "t3"],
              values=[[10 + i, 8 + i, 6 + i] for i in range(8)])
    a = analyze(p)
    assert [e.name for e in a.anova.effects] == ["시점(시간)"]
    assert a.grouped is False
    assert a.between == []


def test_anova_falls_back_gracefully_when_no_one_is_complete():
    p = Panel(subjects=["a", "b", "c"], times=["t1", "t2"],
              values=[[1.0, None], [None, 2.0], [3.0, None]])
    a = analyze(p)
    assert a.anova is None
    assert a.anova_error and "2명 미만" in a.anova_error
    assert "ANOVA 미수행" in render_text(a)


def test_sphericity_correction_selection():
    a = analyze(_trial_panel(), Options(sphericity="gg"))
    assert a.correction_used == "gg"
    a_none = analyze(_trial_panel(), Options(sphericity="none"))
    assert a_none.correction_used == "none"
    eff = a.anova.effect("시점(시간)")
    if a.anova.sphericity.epsilon_ok:
        assert eff.p_gg is not None and eff.p_reported("gg") == eff.p_gg


def test_forcing_a_method_overrides_the_recommendation():
    a = analyze(_trial_panel(), Options(method="nonparametric"))
    assert a.recommended == "nonparametric"
    assert "nonparametric" in a.recommendation_reason
    text = render_text(a)
    assert "Friedman 검정 (시점 효과" in text


def test_warning_when_sphericity_violated_but_correction_disabled():
    values = [[1.0, 2.0, 30.0], [2.0, 3.0, 4.0], [3.0, 40.0, 5.0],
              [4.0, 5.0, 6.0], [5.0, 6.0, 70.0], [6.0, 7.0, 8.0],
              [7.0, 80.0, 9.0], [8.0, 9.0, 10.0]]
    p = Panel(subjects=[f"S{i}" for i in range(8)], times=["a", "b", "c"],
              values=values)
    a = analyze(p, Options(sphericity="none"))
    if a.anova.sphericity.violated():
        assert any("구형성이 기각" in w for w in a.warnings)


def test_text_report_contains_the_key_headings():
    a = analyze(_trial_panel(), Options(mcid=5, direction="lower",
                                        reliability=0.9))
    text = render_text(a)
    for heading in ("[1] 결측", "[2] 기술통계", "[3] 가정 점검", "[4] 주 분석",
                    "[5] 기준시점", "[6] 시점 간 사후비교", "[7] 시점별 군간 비교",
                    "[8] 반응자 분석", "[9] 신뢰변화지수", "[10] 논문용 문장"):
        assert heading in text
    assert "[6]" not in render_text(a, brief=True)
    assert "Friedman 검정 (시점 효과" in render_text(a, full=True)


def test_json_output_is_valid_and_finite():
    a = analyze(_trial_panel(), Options(mcid=5, direction="lower"))
    payload = json.loads(render_json(a))
    assert payload["n_subjects"] == 20
    assert payload["times"] == ["기저", "4주", "8주"]
    assert payload["anova"]["effects"][0]["name"]
    assert payload["responder"]["rates"]

    def no_nan(obj):
        if isinstance(obj, float):
            assert math.isfinite(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                no_nan(v)
        elif isinstance(obj, list):
            for v in obj:
                no_nan(v)

    no_nan(payload)


def test_csv_output_is_parseable_and_escapes_formulas():
    p = _trial_panel()
    p.groups = ["=cmd()" if g == "능동" else g for g in p.groups]
    a = analyze(p, Options(mcid=5, direction="lower"))
    import csv
    import io
    rows = list(csv.reader(io.StringIO(render_csv(a))))
    assert rows[0][0] == "section"
    assert any(r[0] == "anova" for r in rows)
    dangerous = [c for r in rows for c in r if c.startswith("=")]
    assert not dangerous
    assert any(c.startswith("'=") for r in rows for c in r)


def test_apa_sentences_are_bilingual_and_quote_numbers():
    a = analyze(_trial_panel(), Options(mcid=5, direction="lower",
                                        method="parametric"))
    lines = apa_sentences(a)
    assert any(s.startswith("[KO]") for s in lines)
    assert any(s.startswith("[EN]") for s in lines)
    assert any("ηp²" in s for s in lines)


def test_formatting_helpers():
    assert fmt(1.239) == "1.24"
    assert fmt(float("nan")) == "—"
    assert fmt(float("inf")) == "∞"
    assert fmt_p(0.0004) == "< .001"
    assert fmt_p(0.0234) == "= .023"
    assert fmt_p(0.9999) == "> .999"
    assert fmt_p_cell(0.0234) == ".023"
    assert fmt_p_cell(0.0004) == "<.001"
    assert fmt_es(0.5432) == ".543"
    assert fmt_es(-0.5432) == "-.543"
    assert fmt_es(1.5) == "1.500"
    assert fmt_df(3.0) == "3"
    assert fmt_df(2.3149) == "2.31"


def test_table_pads_korean_columns_to_equal_display_width():
    import unicodedata

    def display_width(text):
        return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1
                   for ch in text)

    lines = table(["가나", "b"], [["다", "22"], ["라마바", "3"]])
    assert len(lines) == 4
    # Equal *display* width on every line; padding by len() would not achieve
    # this because "라마바" is 3 characters but 6 columns wide.
    assert len({display_width(ln) for ln in lines}) == 1
    assert len({len(ln) for ln in lines}) > 1


def test_two_timepoint_panel_reports_without_friedman():
    p = Panel(subjects=[f"S{i}" for i in range(8)], times=["pre", "post"],
              values=[[10 + i, 6 + i] for i in range(8)])
    a = analyze(p)
    assert a.friedman == []
    text = render_text(a)
    assert "Friedman" not in text
    assert "시점이 2개면" in text


def test_analyze_requires_at_least_two_timepoints():
    p = Panel(subjects=["a"], times=["only"], values=[[1.0]])
    with pytest.raises(ValueError, match="2개 이상"):
        analyze(p)
