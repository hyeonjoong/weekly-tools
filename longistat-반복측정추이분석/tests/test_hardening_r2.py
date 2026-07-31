"""Regression tests for round 2 — the trend / sensitivity hardening panel.

One test per defect the reviewers found in `longistat/trend.py` and
`longistat/sensitivity.py`.  Named after the entry in HARDENING.md.
"""

from __future__ import annotations

import json
import math

import pytest

from longistat.analyze import Options, _sensitivity_kinds, analyze
from longistat.anova import rm_anova
from longistat.cli import main
from longistat.dataio import Panel
from longistat.report import (apa_sentences, render_csv, render_json,
                              render_text)
from longistat.sensitivity import impute_panel, sensitivity_analysis
from longistat.special import f_sf
from longistat.trend import (orthogonal_polynomials, resolve_time_values,
                             trend_analysis, trend_shape)


def P(values, groups=None, times=None, value_name="v"):
    times = times or [f"t{j}" for j in range(len(values[0]))]
    return Panel(subjects=[f"s{i}" for i in range(len(values))],
                 times=list(times),
                 values=[[None if v is None else float(v) for v in r]
                         for r in values],
                 groups=None if groups is None else list(groups),
                 value_name=value_name)


# ==========================================================================
# A. statistical correctness
# ==========================================================================

def test_a1_perfectly_linear_data_does_not_fabricate_a_cubic_trend():
    """`scale` used to be the contrast's own SS, so the noise test compared a
    1e-30 residue against 1e-42 and passed: F = 3718, p < .001 on a panel with
    no cubic component whatsoever."""
    rows = [[20 - j + i % 3 for j in range(4)] for i in range(12)]
    tr = trend_analysis(P(rows))
    cubic = [e for e in tr.effects if e.order == 3][0]
    assert cubic.ss < 1e-20
    assert math.isnan(cubic.f) and math.isnan(cubic.p_raw)
    assert not any(math.isfinite(e.p_raw) and e.p_raw < 0.05
                   for e in tr.effects if e.order >= 2)


def test_a1b_flat_two_arm_data_reports_no_interaction_trend():
    tr = trend_analysis(P([[5.0] * 4] * 6 + [[9.0] * 4] * 6,
                          groups=["A"] * 6 + ["B"] * 6))
    assert all(math.isnan(e.f) for e in tr.effects)


def test_a2_partial_eta2_is_nan_when_the_error_term_is_rounding_noise():
    """`ηp² = 1.000` used to print beside `F = —` on constant data."""
    tr = trend_analysis(P([[5.0] * 4] * 6 + [[9.0] * 4] * 6,
                          groups=["A"] * 6 + ["B"] * 6))
    assert all(math.isnan(e.partial_eta2) for e in tr.effects)
    assert "ηp²" not in "".join(
        s for s in apa_sentences(analyze(P([[5.0] * 4] * 6 + [[9.0] * 4] * 6,
                                           groups=["A"] * 6 + ["B"] * 6)))
        if "추세" in s)


def test_a3_ambiguous_visit_labels_are_not_trusted():
    """'방문1(0주)' holds an ordinal and a week; taking the first invented a
    significant quadratic 'plateau' out of a perfectly linear 0/4/24 schedule."""
    notes = []
    vals, source = resolve_time_values(
        ["방문1(0주)", "방문2(4주)", "방문3(24주)"], None, notes)
    assert vals == [1.0, 2.0, 3.0] and source == "등간격 가정"
    assert notes and "숫자가 둘 이상" in notes[0]


def test_a3b_single_number_labels_still_parse():
    notes = []
    vals, source = resolve_time_values(["0주", "4주", "24주"], None, notes)
    assert vals == [0.0, 4.0, 24.0] and source == "시점 이름에서 읽음"
    assert not notes


def test_a4_high_order_components_are_pooled_not_dropped():
    rows = [[10.0, 9.0, 7.0, 6.0, 6.5], [12.0, 10.0, 9.0, 7.0, 7.5],
            [11.0, 11.0, 8.0, 8.0, 7.0], [9.0, 7.0, 7.0, 5.0, 6.0],
            [13.0, 12.0, 10.0, 9.0, 9.5], [14.0, 12.0, 12.0, 10.0, 9.0]]
    p = P(rows)
    an = rm_anova(p.matrix(), p.times)
    tr = trend_analysis(p)
    assert math.fsum(e.ss for e in tr.effects) == \
        pytest.approx(an.effect("시점(시간)").ss)
    assert "4차 이상(잔여)" in render_text(analyze(p))


def test_a5_verdict_line_uses_the_same_p_the_table_prints():
    """raw .022 / Holm .066 printed an unstarred row and then declared the
    linear component significant on the next line."""
    class _E:
        residual = False

        def __init__(self, order, p_raw, p_adj):
            self.order, self.scope = order, "시점"
            self.p_raw, self.p_adj = p_raw, p_adj

    assert "유의하지 않" in trend_shape(
        [_E(1, 0.022, 0.066), _E(2, 0.5, 0.5)], 0.05)
    assert "선형 성분만 유의" in trend_shape(
        [_E(1, 0.022, 0.022), _E(2, 0.5, 0.5)], 0.05)


def test_a6_repeated_gram_schmidt_survives_pathological_spacing():
    poly = orthogonal_polynomials([0.0, 0.001, 0.002, 1000.0], 3)
    cols = [[r[c] for r in poly] for c in range(3)]
    for c, col in enumerate(cols):
        assert math.fsum(col) == pytest.approx(0.0, abs=1e-12)
        for other in cols[c + 1:]:
            assert math.fsum(a * b for a, b in zip(col, other)) == \
                pytest.approx(0.0, abs=1e-12)


def test_a7_type_iii_weighting_shows_up_on_unbalanced_arms():
    """Balanced arms make Type III and Type I identical, so `weight = 1/n` and
    `mu = grand_w` both survived the original suite."""
    values = [[10.0, 8.0, 5.0], [12.0, 9.0, 7.0], [11.0, 10.0, 6.0],
              [9.0, 7.0, 4.0], [13.0, 11.0, 8.0],
              [14.0, 12.0, 11.0], [13.0, 13.0, 12.0]]
    p = P(values, ["A"] * 5 + ["B"] * 2)
    an = rm_anova(p.matrix(), p.times, p.groups)
    tr = trend_analysis(p)
    assert math.fsum(e.ss for e in tr.effects if e.scope == "시점") == \
        pytest.approx(an.effect("시점(시간)").ss)
    assert math.fsum(e.ss for e in tr.effects if e.scope == "그룹 × 시점") == \
        pytest.approx(an.effect("그룹 × 시점").ss)


def test_a8_interaction_partial_eta2_recomputed_independently():
    """The one-group identity is degenerate; only the interaction row
    distinguishes ss/(ss+ss_err) from ss/total."""
    values = [[10.0, 8.0, 5.0], [12.0, 9.0, 7.0], [11.0, 10.0, 6.0],
              [9.0, 7.0, 4.0], [13.0, 11.0, 8.0],
              [14.0, 12.0, 11.0], [13.0, 13.0, 12.0]]
    tr = trend_analysis(P(values, ["A"] * 5 + ["B"] * 2))
    inter = [e for e in tr.effects
             if e.scope == "그룹 × 시점" and e.order == 1][0]
    ms_err = (inter.ss / inter.df1) / inter.f       # recover MS_error from F
    ss_err = ms_err * inter.df2
    assert inter.partial_eta2 == pytest.approx(inter.ss / (inter.ss + ss_err))
    assert inter.p_raw == pytest.approx(
        f_sf(inter.f, inter.df1, inter.df2), rel=1e-9)


def test_a9_three_arms_give_the_interaction_two_numerator_df():
    values = ([[10.0, 8.0, 5.0], [12.0, 9.0, 7.0], [11.0, 10.0, 6.0]]
              + [[14.0, 12.0, 11.0], [13.0, 13.0, 12.0], [15.0, 14.0, 13.0]]
              + [[9.0, 9.0, 8.0], [11.0, 10.5, 10.0], [10.0, 10.0, 9.5]])
    tr = trend_analysis(P(values, ["A"] * 3 + ["B"] * 3 + ["C"] * 3))
    inter = [e for e in tr.effects if e.scope == "그룹 × 시점"]
    assert inter and all(e.df1 == 2.0 for e in inter)
    assert all(e.df1 == 1.0 for e in tr.effects if e.scope == "시점")


def test_a10_contrast_p_is_uncorrected_where_the_omnibus_is_gg_corrected():
    """The real content of 'sphericity does not apply here': the contrast p is
    the raw F tail, while the omnibus time p is ε-shrunk."""
    values = [[10.0, 8.0, 1.0], [12.0, 9.0, 9.0], [11.0, 10.0, 2.0],
              [9.0, 7.0, 8.0], [13.0, 11.0, 3.0], [14.0, 12.0, 12.0],
              [13.0, 13.0, 4.0], [15.0, 14.0, 13.0]]
    p = P(values)
    tr = trend_analysis(p)
    lin = [e for e in tr.effects if e.order == 1][0]
    assert lin.p_raw == pytest.approx(f_sf(lin.f, lin.df1, lin.df2))
    an = rm_anova(p.matrix(), p.times)
    eff = an.effect("시점(시간)")
    if an.sphericity.epsilon_ok and eff.p_gg is not None:
        assert eff.p_gg != pytest.approx(eff.p)     # omnibus really is shrunk


def test_a11_welch_flag_reaches_the_slope_contrast():
    values = [[10.0, 8.0, 6.0], [11.0, 9.0, 7.0], [12.0, 10.0, 8.0],
              [10.0, 10.0, 10.0], [11.0, 11.5, 12.0], [12.0, 12.0, 12.1],
              [9.0, 9.5, 11.0]]
    arms = ["A"] * 3 + ["B"] * 4
    w = trend_analysis(P(values, arms), welch=True).slope_contrasts[0]
    s = trend_analysis(P(values, arms), welch=False).slope_contrasts[0]
    assert "Welch" in w.method and "Student" in s.method
    assert w.p != pytest.approx(s.p)


def test_a12_cubic_row_and_holm_family_at_four_visits():
    rows = [[10.0, 9.0, 7.0, 6.0], [12.0, 10.0, 9.0, 7.5],
            [11.0, 11.0, 8.0, 8.0], [9.0, 7.0, 7.0, 5.5],
            [13.0, 12.0, 10.0, 9.0], [14.0, 12.5, 12.0, 10.0]]
    tr = trend_analysis(P(rows, ["A"] * 3 + ["B"] * 3))
    times = [e for e in tr.effects if e.scope == "시점"]
    assert [e.order for e in times] == [1, 2, 3]
    assert "삼차(cubic)" in {e.order_name for e in times}
    # Holm runs over a family of 3, so every adjusted p is >= its raw p and
    # the smallest raw p pays the full x3 penalty (capped at 1).
    finite = [e for e in times if math.isfinite(e.p_raw)]
    assert len(finite) == 3
    assert all(e.p_adj >= e.p_raw - 1e-12 for e in finite)
    best = min(finite, key=lambda e: e.p_raw)
    assert best.p_adj == pytest.approx(min(1.0, 3 * best.p_raw))
    assert not any(e.residual for e in tr.effects)


# ==========================================================================
# B. edge cases
# ==========================================================================

def test_b1_astronomical_time_values_do_not_overflow():
    tr = trend_analysis(P([[1.0, 2.0, 3.0, 4.0], [2.0, 3.0, 4.0, 6.0]]),
                        time_values=[1e200, 2e200, 3e200, 4e200])
    assert tr is not None
    assert math.isfinite(tr.slopes[0].mean_slope)


def test_b1b_overflow_is_a_warning_not_a_traceback(tmp_path, capsys):
    csv = tmp_path / "t.csv"
    csv.write_text("id,visit,val\n" + "".join(
        f"s{i},t{j},{10 + i - j}\n" for i in range(4) for j in range(3)),
        encoding="utf-8")
    assert main([str(csv), "--id", "id", "--time", "visit", "--value", "val",
                 "--time-values", "1e300,2e300,3e300"]) == 0
    assert "Traceback" not in capsys.readouterr().err


def test_b2_arm_named_like_the_pooled_row_is_not_swallowed():
    values = ([[20.0 - 1.3 * j for j in range(4)] for _ in range(6)]
              + [[20.5 - 1.0 * j for j in range(4)] for _ in range(6)])
    tr = trend_analysis(P(values, ["전체"] * 6 + ["B"] * 6))
    labels = [s.group for s in tr.slopes]
    assert len(labels) == len(set(labels))          # no duplicate row
    assert ("전체", 6) in [(s.group, s.n) for s in tr.slopes]
    assert [(c.group_a, c.group_b) for c in tr.slope_contrasts] == [("전체", "B")]


def test_b3_time_values_count_is_checked_on_two_visit_designs(tmp_path, capsys):
    csv = tmp_path / "two.csv"
    csv.write_text("id,visit,val\n" + "".join(
        f"s{i},t{j},{10 + i - j}\n" for i in range(4) for j in range(2)),
        encoding="utf-8")
    assert main([str(csv), "--id", "id", "--time", "visit", "--value", "val",
                 "--time-values", "1,2,3,4,5"]) == 1
    assert "시점 개수" in capsys.readouterr().err


def test_b3b_two_visits_say_the_trend_section_was_skipped():
    a = analyze(P([[10.0, 8.0], [12.0, 9.0], [11.0, 7.0]]),
                Options(time_values=[0.0, 8.0]))
    assert a.trend is None
    assert any("추세 구획" in w for w in a.warnings)


def test_b4_auto_cannot_be_combined_with_a_method():
    with pytest.raises(ValueError, match="단독으로만"):
        _sensitivity_kinds("auto,locf")


def test_b5_zero_variance_slopes_do_not_produce_stars():
    tr = trend_analysis(P([[10.0 - j for j in range(3)] for _ in range(5)]))
    row = tr.slopes[0]
    assert row.sd == 0.0 and math.isnan(row.p)
    text = render_text(analyze(P([[10.0 - j for j in range(3)]
                                 for _ in range(5)])))
    slope_line = [ln for ln in text.splitlines()
                  if ln.strip().startswith("전체") and "-1.000" in ln]
    assert slope_line and "*" not in slope_line[0]


# ==========================================================================
# C. sensitivity analysis
# ==========================================================================

def test_c1_bocf_never_carries_a_value_backwards():
    p = P([[1.0, None, 3.0, None]], times=["run", "pre", "base", "post"])
    assert impute_panel(p, 2, "bocf").values[0] == [1.0, None, 3.0, 3.0]
    assert impute_panel(p, 2, "locf").values[0] == [1.0, None, 3.0, 3.0]


def test_c2_locf_fills_an_interior_gap_from_the_earlier_visit():
    p = P([[10.0, None, 5.0]])
    assert impute_panel(p, 0, "locf").values[0] == [10.0, 10.0, 5.0]


def test_c3_imputed_counts_are_per_contrast_not_study_wide():
    values = ([[10.0, 6.0], [10.0, None], [10.0, 6.5]]          # A: 1 imputed
              + [[10.0, 9.0], [10.0, 9.5], [10.0, 9.2]]         # B: 0
              + [[10.0, 7.0], [10.0, None], [10.0, 7.5]])       # C: 1
    res = sensitivity_analysis(
        P(values, ["A"] * 3 + ["B"] * 3 + ["C"] * 3, times=["기저", "8주"]), 0)
    got = {r.contrast: r.imputed for r in res.rows if r.kind == "locf"}
    assert got == {"A − B": 1, "A − C": 2, "B − C": 1}


def test_c4_no_observed_counterpart_is_reported_as_incomparable():
    """Every subject missing at a visit used to be silently counted as
    'the sensitivity analyses agreed'."""
    values = ([[10.0, 5.0, None], [10.0, 5.5, None], [10.0, 6.0, None]]
              + [[10.0, 9.0, None], [10.0, 9.5, None], [10.0, 9.2, None]])
    a = analyze(P(values, ["A"] * 3 + ["B"] * 3,
                  times=["기저", "4주", "8주"]))
    assert a.sensitivity is not None
    issues = a.sensitivity.flips(0.05)
    assert any("비교" in i and "8주" in i for i in issues)
    sentences = apa_sentences(a)
    assert not any("동일했다" in s for s in sentences)
    assert any("비교할 수 없" in s for s in sentences)


def test_c5_sign_reversal_needs_at_least_one_significant_side():
    """Two emphatically null estimates straddling zero are not a finding."""
    values = ([[10.0, 10.005], [10.0, 9.99], [10.0, 10.02], [10.0, 9.98],
               [10.0, None], [10.0, None]]
              + [[10.0, 10.0], [10.0, 10.01], [10.0, 9.995], [10.0, 10.005],
                 [10.0, None], [10.0, None]])
    res = sensitivity_analysis(
        P(values, ["A"] * 6 + ["B"] * 6, times=["기저", "8주"]), 0)
    if res is not None:
        assert not any("부호가 반대" in f for f in res.flips(0.05))


def test_c6_mostly_imputed_visits_are_flagged():
    values = [[10.0, 6.0, 4.0]] + [[10.0, None, None] for _ in range(5)]
    res = sensitivity_analysis(P(values, times=["기저", "4주", "8주"]), 0)
    assert any("절반 이상이 대체값" in n for n in res.notes)


def test_c7_single_named_arm_still_gets_a_sensitivity_table():
    values = [[10.0, 6.0, 4.0], [10.0, None, None], [10.0, 8.0, 6.0],
              [12.0, 9.0, 7.0]]
    res = sensitivity_analysis(P(values, ["A"] * 4), 0)
    assert res is not None and not res.grouped
    assert {r.contrast for r in res.rows} == {"전체"}


def test_c8_out_of_range_baseline_is_rejected_by_the_analysis_too():
    with pytest.raises(ValueError):
        sensitivity_analysis(P([[1.0, 2.0, None]]), 9)


def test_c9_nonparametric_track_labels_the_parametric_sensitivity_columns():
    values = [[10.0, 6.0, 4.0], [10.0, None, 5.0], [10.0, 8.0, 6.0],
              [12.0, 9.0, 7.0], [11.0, 8.0, 6.0]]
    text = render_text(analyze(P(values), Options(method="nonparametric")))
    assert "모두 모수 t-검정 기준" in text


# ==========================================================================
# D. security / export safety on the new rows
# ==========================================================================

_HOSTILE_A = '=HYPERLINK("http://x")'
_HOSTILE_B = "</script>"


def _hostile_analysis():
    values = [[10.0, 8.0, 5.0], [12.0, 9.0, 7.0], [11.0, 10.0, None],
              [14.0, 12.0, 11.0], [13.0, 13.0, 12.0], [15.0, 14.0, None]]
    return analyze(P(values, [_HOSTILE_A] * 3 + [_HOSTILE_B] * 3))


def test_d1_formula_injection_neutralised_in_the_new_csv_sections():
    csv = render_csv(_hostile_analysis())
    rows = [ln for ln in csv.splitlines()
            if ln.startswith(("sensitivity", "slope_between", "subject_slope"))]
    assert rows
    for line in rows:
        for cell in line.split(","):
            stripped = cell.strip('"')
            assert not stripped.startswith("=HYPERLINK")


def test_d2_json_escapes_html_in_the_new_sections():
    payload = render_json(_hostile_analysis())
    assert "</script>" not in payload
    assert "\\u003c/script\\u003e" in payload
    json.loads(payload.replace("\\u003c", "<").replace("\\u003e", ">")
               .replace("\\u0026", "&"))


def test_d3_no_subject_ids_leak_into_the_new_sections():
    values = [[10.0, 8.0, 5.0], [12.0, 9.0, None], [11.0, 10.0, 6.0],
              [14.0, 12.0, 11.0]]
    p = Panel(subjects=["환자-홍길동-01", "환자-홍길동-02",
                        "환자-홍길동-03", "환자-홍길동-04"],
              times=["기저", "4주", "8주"],
              values=[[None if v is None else float(v) for v in r]
                      for r in values],
              groups=["A", "A", "B", "B"], value_name="v")
    a = analyze(p)
    for text in (render_text(a), render_csv(a), render_json(a)):
        assert "홍길동" not in text


def test_d4_trend_failure_note_carries_no_exception_text():
    tr = trend_analysis(P([[1.0, 2.0, 3.0]] * 4), time_values=None)
    assert tr is not None
    for note in tr.notes:
        assert "Traceback" not in note and "fsum" not in note
