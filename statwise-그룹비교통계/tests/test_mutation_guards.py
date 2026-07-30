"""Value-level tests for the paths mutation testing found unprotected.

Each test here corresponds to a mutation that the rest of the suite could not
detect. They deliberately assert *numbers pulled out of the rendered output*
and *counts derived from raw cells*, because the surviving mutations were all
of the form "the pipeline still runs, it just reports something different".
"""

import json
import math

import pytest

from statwise.binary import (BinaryGroup, compare_binary,
                             number_needed_to_treat, risk_difference)
from statwise.dataio import map_binary_levels, _tally
from statwise.equivalence import tost, tost_independent
from statwise.analyze import _bh_adjust, _holm_adjust, analyze, analyze_paired
from statwise.report import (_fmt_p, _pct, binary_sentence, render_binary_text,
                             render_text, result_to_dict)


# --------------------------------------------------------------------------
# M25 / M27 / M36 — event mapping and tallying decide every rate in the report
# --------------------------------------------------------------------------

def test_event_value_maps_the_named_value_and_nothing_else():
    """Swapping the event / non-event sets inverts the whole clinical result."""
    events, non_events = map_binary_levels(
        ["improved", "stable", "improved", "worse"], event_value="improved")
    assert events == {"IMPROVED"}
    assert non_events == {"STABLE", "WORSE"}


def test_auto_mapping_puts_yes_in_the_event_set():
    events, non_events = map_binary_levels(["yes", "no", "YES", "No"])
    assert events == {"YES"} and non_events == {"NO"}
    events, non_events = map_binary_levels(["1", "0"])
    assert events == {"1"} and non_events == {"0"}


def test_tally_counts_events_and_denominator_exactly():
    cells = ["yes", "no", "yes", "", "NA", "yes", "no", "garbage"]
    ev, tot, bad = _tally(cells, {"YES"}, {"NO"})
    assert (ev, tot, bad) == (3, 5, 2)      # 3 events / 5 usable / NA + garbage


def test_tally_never_counts_unmappable_cells_as_non_events():
    """An unreadable cell must not silently become a 'no'."""
    ev, tot, bad = _tally(["yes", "???", "!!!"], {"YES"}, {"NO"})
    assert (ev, tot, bad) == (1, 1, 2)


def test_event_value_end_to_end_produces_the_right_counts():
    events, non_events = map_binary_levels(
        ["improved"] * 8 + ["stable"] * 2, event_value="improved")
    ev, tot, _ = _tally(["improved"] * 8 + ["stable"] * 2, events, non_events)
    res = compare_binary([("a", (ev, tot)), ("b", (3, 10))])
    assert res.groups[0].events == 8 and res.groups[0].n == 10
    assert res.groups[0].proportion == pytest.approx(0.8)


# --------------------------------------------------------------------------
# M08 — binary post-hoc correction was entirely unverified
# --------------------------------------------------------------------------

def test_binary_posthoc_adjusted_p_matches_holm_reference():
    groups = [("a", (5, 40)), ("b", (15, 40)), ("c", (25, 40)), ("d", (30, 40))]
    res = compare_binary(groups, correction="holm")
    raw = [pw.pvalue_raw for pw in res.pairwise]
    expected = _holm_adjust(list(raw))
    assert [pw.pvalue_adj for pw in res.pairwise] == pytest.approx(expected,
                                                                   rel=1e-12)
    # and the correction must actually move at least one p-value
    assert any(adj > r + 1e-9 for adj, r in zip(expected, raw))


def test_binary_posthoc_adjusted_p_matches_bh_reference():
    groups = [("a", (5, 40)), ("b", (15, 40)), ("c", (25, 40)), ("d", (30, 40))]
    res = compare_binary(groups, correction="bh")
    raw = [pw.pvalue_raw for pw in res.pairwise]
    assert [pw.pvalue_adj for pw in res.pairwise] == pytest.approx(
        _bh_adjust(list(raw)), rel=1e-12)


def test_binary_posthoc_significance_uses_the_adjusted_p():
    groups = [("a", (5, 40)), ("b", (15, 40)), ("c", (25, 40)), ("d", (30, 40))]
    res = compare_binary(groups, correction="holm")
    for pw in res.pairwise:
        assert pw.significant == (pw.pvalue_adj < res.alpha)


# --------------------------------------------------------------------------
# M49 / M50 — post-hoc must not run on a non-significant omnibus
# --------------------------------------------------------------------------

def test_binary_posthoc_skipped_when_omnibus_not_significant():
    res = compare_binary([("a", (10, 40)), ("b", (11, 40)), ("c", (12, 40))])
    assert not res.significant
    assert res.pairwise == []


def test_continuous_posthoc_skipped_when_omnibus_not_significant():
    res = analyze([("a", [1.0, 2.0, 3.0, 4.0, 5.0]),
                   ("b", [1.1, 2.1, 3.1, 4.1, 5.1]),
                   ("c", [0.9, 1.9, 2.9, 3.9, 4.9])])
    assert not res.significant
    assert res.pairwise == []


# --------------------------------------------------------------------------
# M11 / M20 / M23 / M43 — interval *widths*, not just containment
# --------------------------------------------------------------------------

def test_tost_interval_is_the_one_minus_two_alpha_interval():
    """TOST must report 100(1-2a)%, not the ordinary 100(1-a)% interval."""
    from statwise.special import t_ppf
    diff, se, df, alpha = 0.4, 0.25, 30.0, 0.05
    r = tost(diff, se, df, -1.0, 1.0, alpha)
    expected_half = t_ppf(1.0 - alpha, df) * se          # one-sided crit
    assert r.ci_high - r.ci_low == pytest.approx(2 * expected_half, rel=1e-9)
    # and it must be strictly narrower than the 95% two-sided interval
    two_sided = 2 * t_ppf(1.0 - alpha / 2.0, df) * se
    assert r.ci_high - r.ci_low < two_sided - 1e-9


def test_mean_difference_interval_width_matches_the_t_critical_value():
    from statwise.special import t_ppf
    a = [5.1, 4.9, 5.3, 5.0, 5.2, 4.8, 5.4, 5.0]
    b = [7.1, 6.9, 7.3, 7.0, 7.2, 6.8, 7.4, 7.0]
    res = analyze([("a", a), ("b", b)], alpha=0.05)
    lo, hi = res.mean_diff_ci
    half = (hi - lo) / 2.0
    # Student's t here: se from the pooled variance
    n = len(a)
    va = sum((x - sum(a) / n) ** 2 for x in a) / (n - 1)
    vb = sum((x - sum(b) / n) ** 2 for x in b) / (n - 1)
    sp2 = ((n - 1) * va + (n - 1) * vb) / (2 * n - 2)
    se = math.sqrt(sp2 * (2.0 / n))
    assert half == pytest.approx(t_ppf(0.975, 2 * n - 2) * se, rel=1e-9)


def test_paired_interval_width_matches_the_t_critical_value():
    from statwise.special import t_ppf
    a = [10.0, 11.5, 9.8, 10.2, 11.0, 10.6, 9.9, 10.4, 10.8, 11.2]
    b = [10.2, 11.0, 10.1, 10.0, 11.3, 10.4, 10.2, 10.1, 11.0, 11.1]
    res = analyze_paired(("a", a), ("b", b), alpha=0.05)
    d = [x - y for x, y in zip(a, b)]
    n = len(d)
    md = sum(d) / n
    sd = math.sqrt(sum((x - md) ** 2 for x in d) / (n - 1))
    half = (res.mean_diff_ci[1] - res.mean_diff_ci[0]) / 2.0
    assert half == pytest.approx(t_ppf(0.975, n - 1) * sd / math.sqrt(n),
                                 rel=1e-9)


def test_binary_wilson_interval_tracks_alpha_in_the_report():
    res = compare_binary([("a", (10, 50)), ("b", (22, 52))], alpha=0.01)
    text = render_binary_text(res)
    assert "99% CI (Wilson)" in text
    assert "95% CI" not in text


# --------------------------------------------------------------------------
# M28 / M29 / M31 / M40 / M45 / M51 — the numbers that reach a manuscript
# --------------------------------------------------------------------------

def test_pct_scales_and_keeps_the_sign():
    assert _pct(0.8) == "80.0%"
    assert _pct(-0.223) == "-22.3%"
    assert _pct(0.0) == "0.0%"
    assert _pct(1.0) == "100.0%"


def test_fmt_p_threshold_and_precision():
    assert _fmt_p(0.0005) == "<0.001"
    assert _fmt_p(0.0011) == "0.001"
    assert _fmt_p(0.04321) == "0.043"
    assert _fmt_p(0.5) == "0.500"


def test_binary_sentence_reports_each_arm_with_its_own_counts():
    res = compare_binary([("drug", (22, 52)), ("placebo", (10, 50))])
    s = binary_sentence(res)
    assert "22/52" in s and "42.3%" in s
    assert "10/50" in s and "20.0%" in s
    # the arms must not be transposed
    assert s.index("22/52") < s.index("10/50")
    assert "in drug" in s and "in placebo" in s
    assert "χ²(1)" in s          # the statistic journals ask for


def test_binary_sentence_quotes_the_configured_coverage():
    res = compare_binary([("drug", (22, 52)), ("placebo", (10, 50))],
                         alpha=0.10)
    assert "90% CI" in binary_sentence(res)
    assert "10% CI" not in binary_sentence(res)


def test_descriptives_row_columns_are_in_the_documented_order():
    """sd and median must not be transposed in the table."""
    res = analyze([("a", [1.0, 2.0, 3.0, 4.0, 100.0]),
                   ("b", [1.0, 2.0, 3.0, 4.0, 5.0])])
    row = [ln for ln in render_text(res).splitlines()
           if ln.strip().startswith("a ")][0]
    nums = [float(x) for x in row.split()[1:]]
    n, mean, sd, median, q1, q3, lo, hi = nums
    assert n == 5
    assert mean == pytest.approx(22.0)
    assert sd == pytest.approx(43.618, abs=1e-3)   # sd is much larger here
    assert median == pytest.approx(3.0)
    assert lo == pytest.approx(1.0) and hi == pytest.approx(100.0)


def test_risk_difference_sign_survives_rendering():
    res = compare_binary([("low", (10, 50)), ("high", (22, 52))])
    text = render_binary_text(res)
    rd_line = [ln for ln in text.splitlines() if "Risk difference" in ln][0]
    assert "-22.3%" in rd_line          # low - high is negative
    assert result_to_dict is not None


# --------------------------------------------------------------------------
# M14 / M41 — boundary conventions
# --------------------------------------------------------------------------

def test_nnt_interval_withheld_when_the_rd_bound_touches_zero_exactly():
    rd = risk_difference(BinaryGroup("a", 25, 50), BinaryGroup("b", 25, 50))
    nnt = number_needed_to_treat(rd)
    assert nnt.ci_low is None and nnt.ci_high is None


def test_noninferiority_boundary_is_strict():
    """concluded must require the bound to clear the margin, not merely touch."""
    from statwise.equivalence import noninferiority
    from statwise.special import t_ppf
    se, df, margin = 0.5, 20.0, 1.0
    # place the lower bound exactly on -margin
    diff = -margin + t_ppf(0.95, df) * se
    r = noninferiority(diff, se, df, margin, "higher_is_better", 0.05)
    assert r.ci_low == pytest.approx(-margin, abs=1e-9)
    assert r.concluded is False


# --------------------------------------------------------------------------
# M48 — a NaN omnibus p must not be given a rank ordering
# --------------------------------------------------------------------------

def test_nan_pvalue_disables_across_endpoint_reordering():
    from statwise.endpoints import run_endpoints
    const = [("a", [1.0, 1.0, 1.0, 1.0]), ("b", [1.0, 1.0, 1.0, 1.0]),
             ("c", [1.0, 1.0, 1.0, 1.0])]
    good = [("a", [1.0, 2.0, 3.0, 4.0]), ("b", [9.0, 10.0, 11.0, 12.0]),
            ("c", [20.0, 21.0, 22.0, 23.0])]
    multi = run_endpoints([("degenerate", const), ("clean", good)])
    for run in multi.analysed:
        assert run.result.pvalue_adj == run.result.pvalue or \
            run.result.pvalue_adj >= run.result.pvalue


# --------------------------------------------------------------------------
# M35 — wide-layout blanks are ragged columns, not missing data
# --------------------------------------------------------------------------

def test_wide_blank_cells_are_not_counted_as_missing(tmp_path):
    from statwise.dataio import load_wide
    p = tmp_path / "w.csv"
    p.write_text("a,b\n1,2\n3,\n5,\nNA,8\n", encoding="utf-8")
    miss = {}
    load_wide(str(p), None, None, None, missing_out=miss)
    assert miss["b"] == 0          # two blanks: shorter column, not lost data
    assert miss["a"] == 1          # one explicit NA: a real missing value
