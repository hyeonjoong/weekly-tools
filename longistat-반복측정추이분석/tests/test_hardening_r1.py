"""Regression tests for every material defect found in hardening round 1.

Each test names the failure it prevents.  See HARDENING.md for the full log.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os

import pytest

from longistat.analyze import Options, analyze
from longistat.anova import Sphericity, rm_anova
from longistat.basics import (adjust, benjamini_hochberg, holm, paired_t,
                              student_t, hedges_g)
from longistat.cli import main
from longistat.dataio import DataError, Panel, clean_label, load_long, load_wide
from longistat.describe import ALL_LABEL, profile_missing
from longistat.posthoc import (DENSE_PAIRWISE_MAX, between_at_time,
                               change_analysis, pairwise_times)
from longistat.report import (_safe, _stars, fmt_p, render_csv, render_json,
                              render_markdown, render_text)
from longistat.responder import (_ratio_ci, chi2_2x2, rci_analysis,
                                 responder_analysis)


def _panel(values, groups=None, times=("기저", "4주", "8주")):
    return Panel(subjects=[f"S{i}" for i in range(len(values))],
                 times=list(times), values=[list(v) for v in values],
                 groups=list(groups) if groups else None,
                 group_name="군" if groups else None, value_name="ISI")


def _trial_panel(n_per=10):
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


def _lcg(rows, cols, seed=12345):
    x = seed
    out = []
    for _ in range(rows):
        row = []
        for _ in range(cols):
            x = (1103515245 * x + 12345) % (2 ** 31)
            row.append(round(10.0 + 8.0 * x / 2 ** 31, 4))
        out.append(row)
    return out


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------

def test_mauchly_uses_the_two_term_expansion_like_r():
    """The first-order form alone is anti-conservative for d>=3 at small n.

    Values verified against R's stats::mauchly.test formula
    (p = p1 + w2*(p2 - p1)) recomputed independently with SciPy.
    """
    matrix = _lcg(9, 5, seed=4242)
    s = rm_anova(matrix, [f"t{j}" for j in range(5)]).sphericity
    assert s.mauchly_ok
    d, nu = 4, 8
    f_corr = 1.0 - (2 * d * d + d + 2) / (6 * d * nu)
    chi2 = -nu * f_corr * math.log(s.w)
    assert s.chi2 == pytest.approx(chi2, rel=1e-12)
    st = pytest.importorskip("scipy.stats")
    df_m = d * (d + 1) / 2 - 1
    p1 = float(st.chi2.sf(chi2, df_m))
    p2 = float(st.chi2.sf(chi2, df_m + 4))
    w2 = ((d + 2) * (d - 1) * (d - 2) * (2 * d ** 3 + 6 * d * d + 3 * d + 2)
          / (288 * (nu * d * f_corr) ** 2))
    assert s.p == pytest.approx(p1 + w2 * (p2 - p1), rel=1e-10, abs=0.0)
    assert s.p > p1                      # the correction always raises p


def test_mauchly_is_flagged_unreliable_when_nu_is_small_relative_to_d():
    s = rm_anova(_lcg(12, 6, seed=9), [f"t{j}" for j in range(6)]).sphericity
    assert s.unreliable is True
    res = rm_anova(_lcg(12, 6, seed=9), [f"t{j}" for j in range(6)])
    assert any("과신하지 마세요" in n for n in res.notes)


def test_student_t_pools_with_n_minus_one_weights():
    """With n1 == n2 and equal variances both formulas agree — so use neither."""
    a = [1.0, 2.0, 3.0, 4.0, 10.0]
    b = [6.0, 7.0, 11.0]
    res = student_t(a, b)
    assert res.name.startswith("Student") and res.df == 6.0
    st = pytest.importorskip("scipy.stats")
    ref = st.ttest_ind(a, b, equal_var=True)
    assert res.t == pytest.approx(float(ref.statistic), rel=1e-12, abs=0.0)
    assert res.p == pytest.approx(float(ref.pvalue), rel=1e-12, abs=0.0)


def test_effect_size_intervals_are_computed_and_carried_to_the_tables():
    res = paired_t([2.0, 3.0, 1.0, 4.0, 5.0])
    se = math.sqrt(1.0 / 5 + res.dz ** 2 / (2.0 * 5))
    z = 1.959963984540054
    assert res.dz_ci[0] == pytest.approx(res.dz - z * se, rel=1e-12)
    assert res.dz_ci[1] == pytest.approx(res.dz + z * se, rel=1e-12)
    rows = pairwise_times(_panel([[10, 8, 6], [12, 9, 7], [14, 11, 8],
                                  [16, 12, 11]]))
    assert all(math.isfinite(r.effect_ci[0]) for r in rows)
    text = render_text(analyze(_trial_panel()))
    assert "효과크기 [95% CI]" in text


def test_yates_corrected_chi_square_has_its_exact_value():
    """chi2 = 40*180^2/20^4 = 8.1 (scipy chi2_contingency correction=True)."""
    yates, p = chi2_2x2(15, 5, 5, 15, yates=True)
    assert yates == pytest.approx(8.1, rel=1e-12)
    assert p == pytest.approx(0.004426525857919809, rel=1e-9, abs=0.0)


def test_haldane_correction_never_moves_the_point_estimate():
    """5/5 vs 10/10 are identical; the corrected OR used to print 0.52."""
    rr, rr_lo, rr_hi = _ratio_ci(5, 5, 10, 10, 0.05, odds=False)
    assert rr == pytest.approx(1.0, rel=1e-12)
    assert rr_lo < 1.0 < rr_hi
    orr, or_lo, or_hi = _ratio_ci(5, 5, 10, 10, 0.05, odds=True)
    assert math.isnan(orr)               # 0/0 — genuinely undefined
    assert or_lo < or_hi
    # With no zero cell the estimate is the plain table value.
    rr2, _, _ = _ratio_ci(20, 50, 10, 50, 0.05, odds=False)
    assert rr2 == pytest.approx(2.0, rel=1e-12)


def test_zero_variance_never_earns_significance_stars():
    """Three identical differences used to print '<.001 ***' and dz = inf."""
    rows = pairwise_times(_panel([[10, 5, 5], [10, 5, 5], [10, 5, 5]]))
    base = [r for r in rows if r.time_a == "기저" and r.time_b == "4주"][0]
    assert math.isnan(base.p_raw) and math.isnan(base.p_adj)
    assert _stars(base.p_adj, 0.05) == ""
    text = render_text(analyze(_panel([[10, 5, 5]] * 4)))
    assert "***" not in text.split("[10]")[0].replace(
        "* p < α, ** p < .01, *** p < .001", "")


def test_uncomputable_effects_do_not_become_a_claim_of_no_effect():
    a = analyze(_panel([[5.0, 5.0, 5.0]] * 6))
    sentences = "\n".join(render_text(a).splitlines())
    assert "검정할 수 없었습니다" in sentences
    assert "유의하지 않았다" not in sentences


def test_perfectly_additive_data_does_not_produce_an_astronomical_f():
    a = analyze(_panel([[20.0 + i, 14.0 + i, 10.0 + i] for i in range(10)]))
    eff = a.anova.effect("시점(시간)")
    assert math.isnan(eff.f)
    assert "e+" not in render_text(a)


def test_multiplicity_adjustment_ignores_uncomputable_comparisons():
    assert math.isnan(holm([0.01, float("nan")])[1])
    assert holm([0.01, float("nan")])[0] == pytest.approx(0.01)
    assert math.isnan(benjamini_hochberg([float("nan"), 0.02])[0])
    assert math.isnan(adjust([float("nan")], "none")[0])


# --------------------------------------------------------------------------
# data loading
# --------------------------------------------------------------------------

def test_clean_label_normalises_and_strips_control_characters():
    assert clean_label("실험") == "실험"
    assert clean_label("\x1b[2J능동") == "[2J능동"
    assert clean_label("﻿기저 ") == "기저"


def test_ambiguous_case_insensitive_column_match_is_an_error(tmp_path):
    p = tmp_path / "amb.csv"
    p.write_text("ID,id,t,v\nA,B,t1,1\nA,B,t2,2\n", encoding="utf-8")
    with pytest.raises(DataError, match="대소문자"):
        load_long(str(p), "Id", "t", "v")


def test_error_messages_do_not_echo_identifiers_or_whole_cells(tmp_path):
    p = tmp_path / "phi.csv"
    p.write_text(
        "id,t,v\n환자-김철수-19800101,기저,12 (김철수 010-1234-5678 재검)\n",
        encoding="utf-8")
    with pytest.raises(DataError) as exc:
        load_long(str(p), "id", "t", "v")
    text = str(exc.value)
    assert "010-1234-5678" not in text
    assert "김철수" not in text
    assert "2행" in text                 # still says exactly where

    dup = tmp_path / "dup.csv"
    dup.write_text("id,t,v\n환자-김철수,기저,1\n환자-김철수,기저,2\n"
                   "B,기저,3\nB,4주,4\n", encoding="utf-8")
    with pytest.raises(DataError) as exc2:
        load_long(str(dup), "id", "t", "v")
    assert "김철수" not in str(exc2.value)


def test_error_line_numbers_are_physical_file_lines(tmp_path):
    p = tmp_path / "blanks.csv"
    p.write_text("id,t,v\n\n\nS1,a,1\n\nS1,b,oops\n", encoding="utf-8")
    with pytest.raises(DataError) as exc:
        load_long(str(p), "id", "t", "v")
    assert "6행" in str(exc.value)


def test_wide_format_rejects_a_subject_in_two_arms(tmp_path):
    p = tmp_path / "w.csv"
    p.write_text("id,base,wk4,arm\nS1,20,10,A\nS1,10,4,B\nS2,19,12,B\n",
                 encoding="utf-8")
    with pytest.raises(DataError, match="엇갈"):
        load_wide(str(p), ["base", "wk4"], id_col="id", group_col="arm",
                  duplicates="mean")


def test_absurd_timepoint_count_is_refused_with_the_likely_cause(tmp_path):
    p = tmp_path / "dates.csv"
    rows = "".join(f"S{i},2024-{1 + d // 28:02d}-{1 + d % 28:02d},{i + d}\n"
                   for i in range(3) for d in range(70))
    p.write_text("id,visit_date,v\n" + rows, encoding="utf-8")
    with pytest.raises(DataError, match="방문일"):
        load_long(str(p), "id", "visit_date", "v")


def test_oversized_input_is_refused_before_it_is_read(tmp_path, monkeypatch):
    import longistat.dataio as dio
    p = tmp_path / "big.csv"
    p.write_text("id,t,v\nS1,a,1\nS1,b,2\n", encoding="utf-8")
    monkeypatch.setattr(dio, "MAX_INPUT_BYTES", 4)
    with pytest.raises(DataError, match="너무 큽니다"):
        dio.load_long(str(p), "id", "t", "v")


# --------------------------------------------------------------------------
# missingness / trial reporting
# --------------------------------------------------------------------------

def test_exactly_twenty_percent_dropout_still_warns():
    """`0 < frac < 0.8` left the planned-for 20 % dropout case silent."""
    values = [[1.0, 2.0, 3.0]] * 8 + [[1.0, 2.0, None]] * 2
    rep = profile_missing(_panel(values))
    assert rep.complete_fraction == pytest.approx(0.8)
    assert any("80%" in w for w in rep.warnings)


def test_differential_dropout_between_arms_is_called_out():
    values = [[1.0, 2.0, 3.0]] * 9 + [[1.0, 2.0, None]] * 1 \
        + [[1.0, 2.0, 3.0]] * 3 + [[1.0, 2.0, None]] * 7
    groups = ["A"] * 10 + ["B"] * 10
    rep = profile_missing(_panel(values, groups))
    assert any("차등 탈락" in w for w in rep.warnings)


def test_availability_table_is_printed_per_arm_and_per_visit():
    values = [[1.0, 2.0, 3.0]] * 6 + [[1.0, 2.0, None]] * 4
    a = analyze(_panel(values, ["A"] * 5 + ["B"] * 5))
    text = render_text(a)
    assert "CONSORT" in text
    assert "결측 패턴별 인원" in text
    assert a.missing.per_time_by_group["A"]["8주"] == 5


def test_completer_caveat_sits_next_to_the_anova():
    text = render_text(analyze(_trial_panel()))
    head = text.split("[5]")[0]
    assert "완전사례" in head and "ITT 주분석이 아닙니다" in head


def test_a_group_wiped_out_by_dropout_is_reported_not_silently_dropped():
    values = [[1.0, 2.0, 3.0]] * 6 + [[1.0, 2.0, None]] * 6
    a = analyze(_panel(values, ["A"] * 6 + ["B"] * 6))
    assert any("완전자료가 한 명도 없는 그룹" in w for w in a.warnings)


# --------------------------------------------------------------------------
# multiplicity / trial design
# --------------------------------------------------------------------------

def test_baseline_is_excluded_from_the_between_group_multiplicity_family():
    rows = between_at_time(_trial_panel(), baseline=0)
    base = [r for r in rows if r.reference_only]
    tested = [r for r in rows if not r.reference_only]
    assert len(base) == 1 and math.isnan(base[0].p_adj)
    assert [r.p_adj for r in tested] == pytest.approx(
        holm([r.p_raw for r in tested]), rel=1e-12)


def test_primary_time_contrast_is_reported_unadjusted():
    ca = change_analysis(_trial_panel(), 0, primary_time="8주")
    primary = [c for c in ca.between if c.time == "8주"][0]
    other = [c for c in ca.between if c.time == "4주"][0]
    assert primary.primary and primary.p_adj == primary.p_raw
    assert other.p_adj == pytest.approx(other.p_raw)   # sole remaining member
    with pytest.raises(ValueError, match="primary-time"):
        change_analysis(_trial_panel(), 0, primary_time="없음")
    with pytest.raises(ValueError, match="기준시점"):
        change_analysis(_trial_panel(), 0, primary_time="기저")


def test_pairwise_is_capped_at_many_timepoints_unless_asked():
    k = 30
    values = [[float((i * 7 + j * 5) % 23) for j in range(k)] for i in range(6)]
    panel = _panel(values, times=tuple(f"t{j}" for j in range(k)))
    capped = pairwise_times(panel, baseline=0)
    assert len(capped) == (k - 1) + (k - 2)          # baseline-vs-each + adjacent
    assert len(pairwise_times(panel, baseline=0, all_pairs=True)) == k * (k - 1) // 2
    assert DENSE_PAIRWISE_MAX == 12


def test_correction_bh_really_dispatches_to_benjamini_hochberg():
    a = analyze(_trial_panel(), Options(correction="bh"))
    rows = [r for r in a.pairwise_param if r.group == "능동"]
    raw = [r.p_raw for r in rows]
    assert [r.p_adj for r in rows] == pytest.approx(
        benjamini_hochberg(raw), rel=1e-12, abs=0.0)


def test_equal_var_and_responder_test_options_are_wired_through():
    a = analyze(_trial_panel(), Options(welch=False))
    assert all(r.method.startswith("Student") for r in a.between)
    chi = analyze(_trial_panel(), Options(mcid=5, direction="lower",
                                          responder_test="chi2"))
    assert chi.responder.contrasts[0].method == "Pearson χ²"


def test_sphericity_hf_option_reports_hf_p_with_hf_degrees_of_freedom():
    a = analyze(_trial_panel(), Options(sphericity="hf"))
    assert a.correction_used == "hf"
    eff = a.anova.effect("시점(시간)")
    assert eff.p_reported("hf") == eff.p_hf
    assert eff.df_reported("hf") == (eff.df1_hf, eff.df2_hf)


# --------------------------------------------------------------------------
# responder / RCI
# --------------------------------------------------------------------------

def test_zero_baseline_exclusions_are_counted_once_not_per_group():
    panel = _panel([[0, 5, 5], [0, 5, 5], [20, 10, 8], [20, 12, 9]],
                   ["A", "B", "A", "B"])
    res = responder_analysis(panel, 0, 30.0, lower_is_better=True, percent=True)
    note = [n for n in res.notes if "기준값이 0" in n][0]
    # 2 subjects x 2 post-baseline visits = 4 observations, counted once each.
    # Counting them again inside every group scope used to report 8.
    assert "4건" in note


def test_rci_note_only_fires_when_the_sd_was_estimated():
    panel = _panel([[20, 10, 8], [18, 12, 9]])
    supplied = rci_analysis(panel, 0, 0.9, sd_baseline=5.0)
    assert supplied.sd_supplied and not supplied.notes
    observed = rci_analysis(panel, 0, 0.9)
    assert not observed.sd_supplied and observed.notes


def test_rci_recovery_label_follows_the_direction():
    panel = _panel([[50, 60, 70], [55, 65, 75]])
    higher = rci_analysis(panel, 0, 0.9, lower_is_better=False,
                          sd_baseline=10.0, recovery_cutoff=65.0)
    assert higher.recovery_label == "회복(≥65.00)"
    lower = rci_analysis(panel, 0, 0.9, lower_is_better=True,
                         sd_baseline=10.0, recovery_cutoff=7.0)
    assert lower.recovery_label == "회복(≤7.00)"
    a = analyze(_panel([[50, 60, 70], [55, 65, 75], [52, 62, 72]]),
                Options(reliability=0.9, direction="higher", rci_sd=10.0,
                        recovery_cutoff=65.0))
    assert "회복(≥65.00)" in render_text(a)


def test_non_responder_imputation_keeps_the_randomised_denominator():
    values = [[20.0, 10.0, 8.0]] * 5 + [[20.0, 10.0, None]] * 5
    panel = _panel(values)
    observed = responder_analysis(panel, 0, 5.0, lower_is_better=True)
    nri = responder_analysis(panel, 0, 5.0, lower_is_better=True, nri=True)
    at8_obs = [r for r in observed.rates if r.time == "8주"][0]
    at8_nri = [r for r in nri.rates if r.time == "8주"][0]
    assert (at8_obs.n, at8_obs.responders) == (5, 5)
    assert (at8_nri.n, at8_nri.responders) == (10, 5)
    assert at8_nri.rate == pytest.approx(0.5)


# --------------------------------------------------------------------------
# ANCOVA
# --------------------------------------------------------------------------

def test_ancova_matches_an_ordinary_least_squares_fit():
    smf = pytest.importorskip("statsmodels.formula.api")
    pd = pytest.importorskip("pandas")
    a = analyze(_trial_panel())
    assert a.ancova is not None
    con = [c for c in a.ancova.contrasts if c.time == "8주"][0]
    rows = [(g, v[0], v[2]) for g, v in zip(a.panel.groups, a.panel.values)]
    df = pd.DataFrame({"post": [r[2] for r in rows],
                       "base": [r[1] for r in rows],
                       "arm": [r[0] for r in rows]})
    m = smf.ols("post ~ C(arm, Treatment(reference='가짜')) + base",
                data=df).fit()
    key = [n for n in m.params.index if "능동" in n][0]
    assert con.adjusted_diff == pytest.approx(float(m.params[key]), rel=1e-9)
    assert con.t == pytest.approx(float(m.tvalues[key]), rel=1e-9)
    assert con.p_raw == pytest.approx(float(m.pvalues[key]), rel=1e-9, abs=0.0)
    assert con.slope == pytest.approx(float(m.params["base"]), rel=1e-9)


def test_ancova_is_absent_without_groups_and_reported_when_present():
    assert analyze(_panel([[1.0, 2.0, 3.0], [2.0, 4.0, 5.0]])).ancova is None
    text = render_text(analyze(_trial_panel()))
    assert "ANCOVA" in text and "조정평균차" in text


def test_ancova_reports_a_singular_design_instead_of_crashing():
    values = [[5.0, 1.0], [5.0, 2.0], [5.0, 3.0], [5.0, 4.0]]
    a = analyze(_panel(values, ["A", "A", "B", "B"], times=("base", "post")))
    assert a.ancova is None or a.ancova.notes or a.ancova.contrasts


# --------------------------------------------------------------------------
# output safety
# --------------------------------------------------------------------------

def test_csv_escapes_every_formula_trigger_but_keeps_negative_numbers():
    for prefix in ("=", "+", "@"):
        p = _trial_panel()
        p.groups = [f"{prefix}cmd|'/C calc'!A0" if g == "능동" else g
                    for g in p.groups]
        cells = [c for r in csv.reader(io.StringIO(render_csv(analyze(p))))
                 for c in r]
        assert any(c.startswith("'" + prefix) for c in cells), prefix
    assert _safe("-4.25") == "-4.25"
    assert _safe("-A군") == "'-A군"
    assert _safe("=1+1") == "'=1+1"


def test_csv_exports_both_tracks_so_the_export_matches_the_screen():
    a = analyze(_trial_panel(), Options(method="nonparametric"))
    rows = list(csv.reader(io.StringIO(render_csv(a))))
    head = rows[0]
    tracks = {r[head.index("track")] for r in rows[1:] if r[0] == "change"}
    assert tracks == {"parametric", "nonparametric"}
    rank_ps = [float(r[head.index("p")]) for r in rows[1:]
               if r[0] == "change" and r[head.index("track")] == "nonparametric"]
    assert rank_ps
    assert set(rank_ps) != set(
        r.p_raw for r in a.change_param.within)      # genuinely different


def test_csv_exports_the_rci_and_ancova_sections():
    a = analyze(_trial_panel(), Options(reliability=0.9, direction="lower",
                                        recovery_cutoff=8))
    sections = {r[0] for r in csv.reader(io.StringIO(render_csv(a)))}
    assert {"rci", "ancova"} <= sections


def test_json_is_safe_to_inline_in_html():
    p = _trial_panel()
    p.groups = ["</script><img src=x onerror=alert(1)>" if g == "능동" else g
                for g in p.groups]
    payload = render_json(analyze(p))
    assert "</script>" not in payload
    assert "\\u003c" in payload
    assert json.loads(payload)["groups"][0].startswith("</script>")


def test_markdown_output_is_a_real_table():
    md = render_markdown(analyze(_trial_panel(), Options(mcid=5,
                                                        direction="lower")))
    assert md.startswith("# ")
    assert "| 효과 |" in md and "|---|" in md
    assert md.count("\n|") > 10


# --------------------------------------------------------------------------
# CLI safety
# --------------------------------------------------------------------------

LONG_CSV = "id,visit,isi,arm\n" + "".join(
    f"S{i},{t},{20 - j * 3 + (i % 3)},{'A' if i < 4 else 'B'}\n"
    for i in range(8) for j, t in enumerate(("기저", "4주", "8주")))


@pytest.fixture()
def csv_path(tmp_path):
    p = tmp_path / "trial.csv"
    p.write_text(LONG_CSV, encoding="utf-8")
    return str(p)


def _args(path, *extra):
    return [path, "--id", "id", "--time", "visit", "--value", "isi",
            "--time-order", "기저,4주,8주"] + list(extra)


def test_output_cannot_overwrite_the_input_file(csv_path, capsys):
    before = open(csv_path, encoding="utf-8").read()
    code = main(_args(csv_path, "--format", "csv", "-o", csv_path,
                      "--overwrite"))
    assert code == 1
    assert "입력 CSV 와 같습니다" in capsys.readouterr().err
    assert open(csv_path, encoding="utf-8").read() == before


def test_output_to_a_directory_fails_with_a_message_not_a_traceback(
        csv_path, tmp_path, capsys):
    d = tmp_path / "adir"
    d.mkdir()
    assert main(_args(csv_path, "-o", str(d), "--overwrite")) == 1
    assert "폴더입니다" in capsys.readouterr().err


def test_dangling_symlink_does_not_bypass_the_overwrite_guard(
        csv_path, tmp_path, capsys):
    target = tmp_path / "target.txt"
    link = tmp_path / "dangle.out"
    os.symlink(str(target), str(link))
    assert main(_args(csv_path, "-o", str(link))) == 1
    assert "이미 있습니다" in capsys.readouterr().err
    assert not target.exists()


def test_json_output_file_has_no_bom(csv_path, tmp_path):
    dest = tmp_path / "r.json"
    assert main(_args(csv_path, "--format", "json", "-o", str(dest))) == 0
    assert dest.read_bytes()[:3] != b"\xef\xbb\xbf"
    json.loads(dest.read_text(encoding="utf-8"))
    dest_csv = tmp_path / "r.csv"
    assert main(_args(csv_path, "--format", "csv", "-o", str(dest_csv))) == 0
    assert dest_csv.read_bytes()[:3] == b"\xef\xbb\xbf"   # Excel needs it


def test_nan_and_inf_options_are_rejected(csv_path, capsys):
    for extra in (["--mcid", "nan", "--direction", "lower"],
                  ["--mcid", "inf", "--direction", "lower"],
                  ["--rci-cutoff", "nan", "--reliability", "0.9",
                   "--direction", "lower"],
                  ["--alpha", "nan"]):
        assert main(_args(csv_path, *extra)) == 1, extra
        assert "nan/inf" in capsys.readouterr().err


def test_flags_that_would_be_silently_ignored_are_rejected(csv_path, capsys):
    assert main(_args(csv_path, "--direction", "lower")) == 1
    assert "--direction" in capsys.readouterr().err
    assert main(_args(csv_path, "--recovery-cutoff", "7")) == 1
    assert "--recovery-cutoff" in capsys.readouterr().err


def test_bad_delimiter_is_a_message_not_a_typeerror(csv_path, capsys):
    assert main(_args(csv_path, "--delimiter", "::")) == 1
    assert "한 글자" in capsys.readouterr().err


def test_markdown_and_primary_time_flags_work_end_to_end(csv_path, capsys):
    code = main(_args(csv_path, "--group", "arm", "--primary-time", "8주",
                      "--format", "md", "--labels-en", "기저=Baseline,A=Active"))
    assert code == 0
    out = capsys.readouterr().out
    assert out.startswith("# ") and "|---|" in out
    assert "Baseline" in out


def test_extreme_alpha_is_rejected_with_a_korean_message(csv_path, capsys):
    assert main(_args(csv_path, "--alpha", "1e-300")) == 1
    assert "1e-6" in capsys.readouterr().err
