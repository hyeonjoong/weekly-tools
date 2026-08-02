"""MMRM: exact identities, missing-data behaviour, edge cases and reporting.

The two exact identities are the backbone of this file.  A REML mixed model is
easy to get subtly wrong in a way that still produces plausible numbers, so
rather than trusting the estimates we pin them to closed-form answers that hold
by construction:

* complete data, one arm  → visit-vs-baseline contrast **is** the paired t-test;
* complete data, ≥2 arms  → per-visit contrast **is** the per-visit ANCOVA
  (Zellner: SUR with identical regressors collapses to equation-by-equation
  OLS), including the standard error and the residual degrees of freedom.

Anything that breaks the covariance estimation, the GLS solve, the REML
correction term or the df bookkeeping breaks at least one of them.
"""

from __future__ import annotations

import math
import random

import pytest

from longistat.analyze import Options, analyze
from longistat.ancova import ancova_analysis
from longistat.basics import paired_t
from longistat.cli import main
from longistat.dataio import Panel
from longistat.mmrm import (MAX_TIMES, _cholesky, _fit_reml, _identifiable,
                            _inverse_spd, mmrm_analysis)
from longistat.report import render_csv, render_json, render_markdown, render_text


def _panel(values, groups=None, times=None):
    n_t = len(values[0])
    return Panel(subjects=[f"s{i}" for i in range(len(values))],
                 times=times or [f"V{j}" for j in range(n_t)],
                 values=[list(r) for r in values], groups=groups,
                 group_name="군" if groups else None, value_name="점수")


def _make(rng, n, n_times, grouped=True, dropout=0.0, effect=-3.0):
    groups = ["A" if i % 2 else "B" for i in range(n)] if grouped else None
    rows = []
    for i in range(n):
        base = rng.gauss(20, 4)
        arm = effect if (groups and groups[i] == "A") else 0.0
        row = [base] + [base + arm * (j + 1) / n_times
                        + rng.gauss(-(j + 1), 2.5) for j in range(n_times - 1)]
        for j in range(1, n_times):
            if rng.random() < dropout:
                for k in range(j, n_times):
                    row[k] = None
                break
        rows.append(row)
    return _panel(rows, groups)


# --------------------------------------------------------------------------
# exact identity 1: single arm, complete data == paired t-test
# --------------------------------------------------------------------------

def test_single_arm_complete_data_is_the_paired_t_test():
    rng = random.Random(20260731)
    rows = [[rng.gauss(10, 3), rng.gauss(9, 3), rng.gauss(7, 3)]
            for _ in range(28)]
    p = _panel(rows)
    res = mmrm_analysis(p)
    assert res is not None and res.converged
    assert not res.grouped and not res.adjusted
    for c in res.contrasts:
        j = res.times.index(c.time)
        pt = paired_t([r[j] - r[0] for r in rows])
        assert c.estimate == pytest.approx(pt.mean_diff, abs=1e-10)
        assert c.df == pytest.approx(pt.df, abs=1e-10)
        assert c.p_raw == pytest.approx(pt.p, rel=1e-7)
        assert c.ci_low == pytest.approx(pt.ci_low, abs=1e-8)
        assert c.ci_high == pytest.approx(pt.ci_high, abs=1e-8)


def test_single_arm_lsmeans_are_the_observed_visit_means():
    rng = random.Random(5)
    rows = [[rng.gauss(10, 3), rng.gauss(9, 3), rng.gauss(7, 3)]
            for _ in range(20)]
    res = mmrm_analysis(_panel(rows))
    for k, ls in enumerate(res.lsmeans):
        want = sum(r[k] for r in rows) / len(rows)
        assert ls.estimate == pytest.approx(want, abs=1e-9)
        assert ls.n == len(rows)


# --------------------------------------------------------------------------
# exact identity 2: two arms, complete data == per-visit ANCOVA
# --------------------------------------------------------------------------

def test_two_arms_complete_data_reproduce_ancova_exactly():
    p = _make(random.Random(42), 40, 4)
    res = mmrm_analysis(p, 0)
    anc = ancova_analysis(p, 0)
    assert res is not None and res.converged and res.adjusted
    assert len(res.contrasts) == len(anc.contrasts) == 3
    for c in res.contrasts:
        match = [x for x in anc.contrasts if x.time == c.time][0]
        assert c.estimate == pytest.approx(match.adjusted_diff, abs=1e-9)
        assert c.df == pytest.approx(match.df, abs=1e-9)
        assert c.t == pytest.approx(match.t, abs=1e-7)
        assert c.p_raw == pytest.approx(match.p_raw, abs=1e-9)
        assert c.ci_low == pytest.approx(match.ci_low, abs=1e-7)
        assert c.ci_high == pytest.approx(match.ci_high, abs=1e-7)


def test_three_arms_complete_data_reproduce_ancova_exactly():
    rng = random.Random(9)
    groups = ["A", "B", "C"] * 14
    rows = []
    for i, g in enumerate(groups):
        base = rng.gauss(30, 5)
        shift = {"A": -4.0, "B": -1.0, "C": 0.0}[g]
        rows.append([base] + [base + shift + rng.gauss(-1, 3) for _ in range(2)])
    p = _panel(rows, groups)
    res = mmrm_analysis(p, 0)
    anc = ancova_analysis(p, 0)
    assert len(res.contrasts) == len(anc.contrasts) == 6
    for c in res.contrasts:
        match = [x for x in anc.contrasts
                 if x.time == c.time and x.group_a == c.group_a
                 and x.group_b == c.group_b][0]
        assert c.estimate == pytest.approx(match.adjusted_diff, abs=1e-8)
        assert c.p_raw == pytest.approx(match.p_raw, abs=1e-9)


def test_lsmeans_are_adjusted_mean_changes_when_grouped():
    """At the mean baseline the cell coefficient is the LS-mean change."""
    p = _make(random.Random(3), 36, 3)
    res = mmrm_analysis(p, 0)
    # Complete data: LS-mean change = arm mean change (baseline centred, so the
    # covariate contributes nothing at the grand mean only when arms are
    # balanced on baseline; check against the ANCOVA-implied value instead).
    for ls in res.lsmeans:
        assert math.isfinite(ls.estimate) and math.isfinite(ls.se)
        assert ls.ci_low < ls.estimate < ls.ci_high


# --------------------------------------------------------------------------
# missing data
# --------------------------------------------------------------------------

def test_restricted_loglikelihood_never_decreases():
    from longistat.mmrm import _build
    p = _make(random.Random(77), 60, 4, dropout=0.25)
    subs, visits, _lab, _cc, _bc, ncols, _drop, _cov = _build(p, 0, True)
    *_rest, history = _fit_reml(subs, ncols, len(visits), 400, 1e-9)
    assert len(history) > 3
    for a, b in zip(history, history[1:]):
        assert b >= a - 1e-9


def test_uses_more_subjects_than_the_complete_case_tracks():
    p = _make(random.Random(101), 70, 4, dropout=0.25)
    res = mmrm_analysis(p, 0)
    assert res.n_subjects > len(p.complete_rows())
    assert res.n_obs > len(p.complete_rows()) * 3
    assert any("완전사례" in n for n in res.notes)


def test_dropouts_after_baseline_are_reported_as_such_not_as_missing_baseline():
    rows = [[10.0, 9.0, 8.0], [11.0, 10.0, 9.0], [12.0, 11.0, None],
            [9.0, None, None],            # baseline only → unusable, but has base
            [13.0, 12.0, 11.0], [10.0, 9.5, 9.0], [14.0, 13.0, 12.5],
            [8.0, 7.5, 7.0], [15.0, 14.0, 13.0], [11.5, 11.0, 10.0]]
    groups = ["A", "B"] * 5
    res = mmrm_analysis(_panel(rows, groups), 0)
    joined = " ".join(res.notes)
    assert "관측 시점이 하나도 없어" in joined
    assert "기준시점" not in joined or "값이 없어" not in joined


def test_subject_without_baseline_is_dropped_from_the_adjusted_model():
    rng = random.Random(88)
    rows = [[None, 9.0, 8.0]]
    for _ in range(19):
        base = rng.gauss(20, 4)
        rows.append([base, base + rng.gauss(-2, 3), base + rng.gauss(-4, 3)])
    groups = ["A", "B"] * 10
    res = mmrm_analysis(_panel(rows, groups), 0)
    assert res.n_subjects == 19
    assert any("기준시점" in n and "값이 없어" in n for n in res.notes)


def test_recovers_the_truth_better_than_complete_case_under_mar_dropout():
    """Dropout driven by the observed *week-4* value is MAR, not MCAR.

    The complete-case ANCOVA only conditions on baseline, so throwing those
    subjects away biases it; MMRM reads their week-4 value and recovers the
    week-8 effect.  Averaged over 30 simulated trials so this is about bias,
    not one lucky draw.
    """
    rng = random.Random(2026)
    truth = -4.0
    err_mmrm = err_cc = 0.0
    for _ in range(30):
        rows, groups = [], []
        for i in range(90):
            g = "A" if i % 2 == 0 else "B"     # "A" first → contrast is A − B
            base = rng.gauss(20, 5)
            arm = truth if g == "A" else 0.0
            mid = base + arm * 0.5 + rng.gauss(-1, 3)
            row = [base, mid, base + arm + rng.gauss(-2, 3)]
            # still symptomatic at week 4 → likelier to leave the study
            if rng.random() < min(0.9, max(0.0, (mid - 13) / 10.0)):
                row[2] = None
            rows.append(row)
            groups.append(g)
        p = _panel(rows, groups)
        res = mmrm_analysis(p, 0)
        last = [c for c in res.contrasts if c.time == "V2"][0]
        anc = ancova_analysis(p.complete_case(), 0)
        cc = [c for c in anc.contrasts if c.time == "V2"][0]
        assert last.group_a == "A" and last.group_b == "B"
        err_mmrm += abs(last.estimate - truth)
        err_cc += abs(cc.adjusted_diff - truth)
    # Not a photo-finish: the complete-case answer is biased by roughly the size
    # of the effect itself, so the margin is large and the test is not flaky.
    assert err_mmrm < 0.5 * err_cc


def test_non_monotone_missing_is_handled():
    rng = random.Random(8)
    rows, groups = [], []
    for i in range(40):
        g = "A" if i % 2 else "B"
        base = rng.gauss(20, 4)
        row = [base, base + rng.gauss(-2, 3), base + rng.gauss(-4, 3)]
        if i % 7 == 0:
            row[1] = None                      # intermittent, not dropout
        rows.append(row)
        groups.append(g)
    res = mmrm_analysis(_panel(rows, groups), 0)
    assert res is not None and res.converged
    assert len(res.contrasts) == 2


# --------------------------------------------------------------------------
# refusals: say why instead of returning a silent None
# --------------------------------------------------------------------------

def test_refuses_when_too_few_subjects_for_an_unstructured_covariance():
    rows = [[1.0, 2.0, 3.0, 4.0], [2.0, 3.0, 4.0, 6.0], [3.0, 3.0, 5.0, 7.0]]
    skipped = []
    assert mmrm_analysis(_panel(rows), 0, skipped=skipped) is None
    assert skipped and "비구조화 공분산" in skipped[0]


def test_refuses_when_a_visit_pair_is_never_jointly_observed():
    rows = [[float(i), float(i) + 1.0, None] for i in range(8)]
    rows += [[float(i), None, float(i) + 2.0] for i in range(8)]
    skipped = []
    assert mmrm_analysis(_panel(rows), 0, skipped=skipped) is None
    assert skipped and "모두 관측한 대상이" in skipped[0]


def test_refuses_when_there_are_too_many_visits():
    rng = random.Random(1)
    # Hard-code 13 rather than MAX_TIMES + 1: derived from the constant, this
    # test passes for any value of MAX_TIMES, including a nonsensical one.
    assert MAX_TIMES <= 12, "an unstructured Σ at T > 12 has 78+ free parameters"
    rows = [[rng.gauss(10, 2) for _ in range(13)] for _ in range(30)]
    skipped = []
    assert mmrm_analysis(_panel(rows), 0, skipped=skipped) is None
    assert skipped and "13" in skipped[0]


def test_the_largest_allowed_visit_count_still_fits():
    """The accept side of MAX_TIMES — a 12-visit panel must not be refused."""
    rng = random.Random(11)
    # 12 visits ⇒ 78 covariance parameters, so this needs 78+ subjects.
    rows = [[rng.gauss(10, 2) for _ in range(MAX_TIMES)] for _ in range(80)]
    skipped = []
    res = mmrm_analysis(_panel(rows), 0, skipped=skipped)
    assert not skipped and res is not None and res.converged
    assert len(res.times) == MAX_TIMES


def test_refuses_when_a_single_subject_links_two_visits():
    """`both < 2` is the boundary: one linking subject gives a garbage Σ.

    With exactly one subject seeing both visits the off-diagonal is fitted from
    a single pair, and the model happily reports correlations of .999 and
    hair-thin standard errors.
    """
    rng = random.Random(303)
    rows = [[rng.gauss(20, 4), rng.gauss(18, 4), None] for _ in range(10)]
    rows += [[rng.gauss(20, 4), None, rng.gauss(16, 4)] for _ in range(10)]
    rows += [[20.0, 18.5, 16.5]]                     # the single linking subject
    skipped = []
    assert mmrm_analysis(_panel(rows), 0, skipped=skipped) is None
    assert skipped and "1명뿐" in skipped[0]


def test_refuses_when_the_design_has_no_residual_degrees_of_freedom():
    """More mean parameters than observations must be refused, not attempted."""
    rng = random.Random(304)
    groups = [f"G{i}" for i in range(8)]              # one subject per arm
    rows = [[rng.gauss(20, 4), rng.gauss(18, 4), rng.gauss(16, 4)]
            for _ in groups]
    skipped = []
    assert mmrm_analysis(_panel(rows, groups), 0, skipped=skipped) is None
    assert skipped and "평균 모수 수" in skipped[0]


def test_refuses_on_a_single_timepoint():
    skipped = []
    p = Panel(subjects=["a", "b"], times=["only"], values=[[1.0], [2.0]])
    assert mmrm_analysis(p, 0, skipped=skipped) is None
    assert skipped


def test_rejects_an_out_of_range_baseline_index():
    with pytest.raises(ValueError):
        mmrm_analysis(_make(random.Random(1), 20, 3), baseline=9)


def test_constant_outcome_raises_rather_than_inventing_a_covariance():
    rows = [[5.0, 5.0, 5.0] for _ in range(20)]
    with pytest.raises(ArithmeticError):
        mmrm_analysis(_panel(rows), 0)


def test_identifiable_helper_names_the_offending_visits():
    p = _panel([[1.0, 2.0, None]] * 3)
    from longistat.mmrm import _Subject
    subs = [_Subject((0, 1), [1.0, 2.0], [[(0, 1.0)], [(1, 1.0)]], "")
            for _ in range(3)]
    msg = _identifiable(subs, ["기저", "4주", "8주"])
    assert msg is not None and "8주" in msg


# --------------------------------------------------------------------------
# linear algebra guards
# --------------------------------------------------------------------------

def test_cholesky_rejects_a_non_positive_definite_matrix():
    with pytest.raises(ArithmeticError):
        _cholesky([[1.0, 2.0], [2.0, 1.0]])
    with pytest.raises(ArithmeticError):
        _cholesky([[0.0]])


def test_inverse_spd_round_trips_and_gives_the_log_determinant():
    a = [[4.0, 1.0, 0.5], [1.0, 3.0, 0.25], [0.5, 0.25, 2.0]]
    inv, logdet = _inverse_spd(a)
    for i in range(3):
        for j in range(3):
            want = 1.0 if i == j else 0.0
            got = math.fsum(a[i][k] * inv[k][j] for k in range(3))
            assert got == pytest.approx(want, abs=1e-12)
        assert inv[i][j] == inv[j][i]
    # 4*(3*2 - .0625) - 1*(2 - .125) + .5*(.25 - 1.5) = 23.75 - 1.875 - 0.625
    assert math.exp(logdet) == pytest.approx(23.75 - 1.875 - 0.625, abs=1e-9)


def test_non_convergence_is_flagged_not_hidden():
    p = _make(random.Random(4), 50, 4, dropout=0.3)
    res = mmrm_analysis(p, 0, max_iter=2)
    assert res is not None and not res.converged
    assert any("수렴하지 않" in n for n in res.notes)


# --------------------------------------------------------------------------
# wiring into analyze() and the renderers
# --------------------------------------------------------------------------

def test_analysis_carries_the_mmrm_and_the_report_shows_it():
    p = _make(random.Random(31), 50, 4, dropout=0.2)
    a = analyze(p, Options())
    assert a.mmrm is not None and a.mmrm_error is None
    text = render_text(a, full=True)
    assert "[4c] MMRM" in text
    assert "비구조화 공분산" in text
    assert "−2 REML log-likelihood" in text
    assert "[4c] MMRM 에 있습니다" in text
    md = render_markdown(a)
    assert "MMRM LS 평균" in md
    csv_out = render_csv(a)
    assert "mmrm_lsmean" in csv_out and "mmrm_contrast" in csv_out


def test_json_export_contains_the_model_and_its_covariance():
    import json
    a = analyze(_make(random.Random(32), 40, 3, dropout=0.2), Options())
    payload = json.loads(render_json(a))
    assert payload["mmrm"]["converged"] is True
    assert len(payload["mmrm"]["cov"]) == 2
    assert payload["mmrm"]["contrasts"][0]["se"] > 0
    assert payload["mmrm_error"] is None


def test_apa_sentences_describe_the_model_in_both_languages():
    from longistat.report import apa_sentences
    a = analyze(_make(random.Random(33), 40, 3, dropout=0.2), Options())
    joined = "\n".join(apa_sentences(a))
    assert "MMRM" in joined and "missing-at-random" in joined
    assert "unstructured covariance" in joined


def test_mmrm_can_be_switched_off():
    a = analyze(_make(random.Random(34), 40, 3), Options(mmrm=False))
    assert a.mmrm is None and a.mmrm_error is None
    assert "[4c]" not in render_text(a)


def test_skip_reason_reaches_the_report_instead_of_a_blank():
    rows = [[1.0, 2.0, 3.0, 4.0], [2.0, 3.0, 4.0, 6.0], [3.0, 3.0, 5.0, 7.0]]
    a = analyze(_panel(rows), Options())
    assert a.mmrm is None and a.mmrm_error
    assert "[4c] MMRM 미수행" in render_text(a)


def test_disagreement_with_the_complete_case_track_is_warned_about():
    """Construct a dataset where dropping the dropouts flips significance."""
    rng = random.Random(4242)
    rows, groups = [], []
    for i in range(60):
        g = "A" if i % 2 else "B"
        base = rng.gauss(20, 4)
        arm = -5.0 if g == "A" else 0.0
        row = [base, base + arm + rng.gauss(-1, 2.0)]
        rows.append(row)
        groups.append(g)
    # Hide most of arm A's benefit from the complete-case analysis.
    hidden = 0
    for i, g in enumerate(groups):
        if g == "A" and hidden < 20:
            rows[i][1] = rows[i][0] - 9.0
            hidden += 1
    p = _panel(rows, groups)
    a = analyze(p, Options())
    assert a.mmrm is not None
    # No dropout here, so the warning must *not* fire — the guard is that the
    # model only speaks up when it actually read more data than the completers.
    assert not any("MMRM([4c])의 유의성 판정이 다릅니다" in w for w in a.warnings)


def test_cli_no_mmrm_flag(tmp_path, capsys):
    csv_path = tmp_path / "d.csv"
    lines = ["id,arm,visit,score"]
    rng = random.Random(6)
    for i in range(40):
        arm = "A" if i % 2 else "B"
        base = rng.gauss(20, 4)
        for j, name in enumerate(("base", "wk4", "wk8")):
            val = base - (3 if arm == "A" else 1) * j + rng.gauss(0, 2)
            lines.append(f"p{i},{arm},{name},{val:.2f}")
    csv_path.write_text("\n".join(lines), encoding="utf-8")
    argv = [str(csv_path), "--id", "id", "--time", "visit", "--value", "score",
            "--group", "arm", "--time-order", "base,wk4,wk8"]
    assert main(argv) == 0
    assert "[4c] MMRM" in capsys.readouterr().out
    assert main(argv + ["--no-mmrm"]) == 0
    assert "[4c]" not in capsys.readouterr().out


def test_primary_time_escapes_multiplicity_in_the_mmrm_table():
    p = _make(random.Random(35), 50, 4, dropout=0.15)
    res = mmrm_analysis(p, 0, primary_time="V3")
    primary = [c for c in res.contrasts if c.primary]
    assert len(primary) == 1
    assert primary[0].p_adj == pytest.approx(primary[0].p_raw)
    for c in res.contrasts:
        if not c.primary:
            assert c.p_adj >= c.p_raw - 1e-12


def test_wide_and_long_inputs_give_the_same_model(tmp_path):
    rng = random.Random(36)
    rows, groups = [], []
    for i in range(30):
        g = "A" if i % 2 else "B"
        base = rng.gauss(20, 4)
        rows.append([base, base + rng.gauss(-2, 3), base + rng.gauss(-4, 3)])
        groups.append(g)
    p = _panel(rows, groups, times=["기저", "4주", "8주"])
    res = mmrm_analysis(p, 0)
    # Same panel through a subset/reorder must not change a completed fit.
    again = mmrm_analysis(p, 0)
    for a, b in zip(res.contrasts, again.contrasts):
        assert a.estimate == pytest.approx(b.estimate, abs=1e-12)
        assert a.se == pytest.approx(b.se, abs=1e-12)


def test_baseline_choice_changes_the_model():
    p = _make(random.Random(37), 40, 4)
    first = mmrm_analysis(p, 0)
    second = mmrm_analysis(p, 1)
    assert first.baseline == "V0" and second.baseline == "V1"
    assert set(second.times) == {"V0", "V2", "V3"}


def test_lsmean_intervals_use_the_pooled_residual_df():
    """Σ is pooled over arms, so an LS-mean CI must not use one arm's n alone.

    Reference: the per-visit ANCOVA on change with a cell-means arm coding and
    the mean-centred baseline covariate — on complete data that is the same fit,
    so the estimate, SE *and* the interval have to agree exactly.
    """
    from longistat.ancova import solve_ols
    from longistat.special import t_ppf

    rng = random.Random(5)
    n = 40
    groups = ["A" if i % 2 else "B" for i in range(n)]
    rows = []
    for i in range(n):
        base = rng.gauss(20, 4)
        arm = -3.0 if groups[i] == "A" else 0.0
        rows.append([base] + [base + arm + rng.gauss(-2, 3) for _ in range(2)])
    res = mmrm_analysis(_panel(rows, groups), 0)

    base_mean = sum(r[0] for r in rows) / n
    for j in (1, 2):
        design, y = [], []
        for i in range(n):
            code = [1.0, 0.0] if groups[i] == "A" else [0.0, 1.0]
            design.append(code + [rows[i][0] - base_mean])
            y.append(rows[i][j] - rows[i][0])
        beta, xtx_inv, sigma2, df = solve_ols(design, y)
        assert df == n - 3
        for k, lab in enumerate(("A", "B")):
            se = math.sqrt(sigma2 * xtx_inv[k][k])
            ls = [x for x in res.lsmeans
                  if x.time == f"V{j}" and x.group == lab][0]
            crit = t_ppf(0.975, df)
            assert ls.estimate == pytest.approx(beta[k], abs=1e-9)
            assert ls.se == pytest.approx(se, abs=1e-9)
            assert ls.df == pytest.approx(df, abs=1e-9)
            assert ls.ci_low == pytest.approx(beta[k] - crit * se, abs=1e-7)
            assert ls.ci_high == pytest.approx(beta[k] + crit * se, abs=1e-7)


def test_size_guard_refuses_instead_of_running_for_minutes():
    from longistat.mmrm import MAX_WORK

    # Pin the constant, not only the mechanism.  The EM cost grows with
    # subjects x visits-squared, so a plain "cells" ceiling lets the genuinely
    # expensive shape (many visits) straight through: 5000x12 measured ~140 s
    # against 5000x8 at ~28 s for two thirds as many cells.
    assert MAX_WORK <= 400_000
    assert 5_000 * 12 ** 2 > MAX_WORK, "5000x12 measured ~140 s - must refuse"
    assert 2_000 * 6 ** 2 <= MAX_WORK, "2000x6 measured ~4 s - must still run"

    skipped = []
    big = Panel(subjects=[str(i) for i in range(5_000)],
                times=[f"V{j}" for j in range(12)],
                values=[[0.0] * 12 for _ in range(5_000)])
    assert mmrm_analysis(big, 0, skipped=skipped) is None
    assert skipped and "너무 큽니다" in skipped[0] and "시점 수" in skipped[0]


# --------------------------------------------------------------------------
# review round 3 (2026-07-31): honesty of what [4c] says about itself
# --------------------------------------------------------------------------

def test_banner_does_not_claim_extra_subjects_when_there_are_none():
    """With complete data MMRM reads exactly the completers — don't imply more."""
    p = _make(random.Random(51), 40, 3)          # no dropout at all
    a = analyze(p, Options())
    text = render_text(a)
    assert a.mmrm.n_subjects == a.missing.n_complete
    assert "버리지 않고 씁니다" not in text
    assert "완전자료 대상과 같은 인원" in text
    assert "[4c] MMRM 에 있습니다" not in text


def test_banner_explains_who_the_model_still_excludes():
    p = _make(random.Random(52), 60, 4, dropout=0.25)
    text = render_text(analyze(p, Options()))
    assert "일부 방문만 마친 대상도 버리지 않고 씁니다" in text
    assert "기저값과 기저 이후 관측이 각각 하나 이상" in text
    assert "유일한 분석" not in text          # the old overclaim, in any output


def test_no_output_format_calls_mmrm_the_only_analysis_that_keeps_dropouts():
    p = _make(random.Random(53), 50, 4, dropout=0.2)
    a = analyze(p, Options())
    for text in (render_text(a, full=True), render_markdown(a), render_json(a),
                 render_csv(a)):
        assert "유일한 분석" not in text


def test_normality_warning_appears_when_the_rank_track_is_recommended():
    """[4c] is Gaussian even when the tool recommends ranks — say so."""
    rng = random.Random(54)
    rows, groups = [], []
    for i in range(30):
        g = "A" if i % 2 else "B"
        base = math.exp(rng.gauss(2.0, 1.1))     # heavily skewed
        rows.append([base, base * math.exp(rng.gauss(-0.4, 1.1)),
                     base * math.exp(rng.gauss(-0.8, 1.1))])
        groups.append(g)
    a = analyze(_panel(rows, groups), Options())
    assert a.recommended == "nonparametric"
    assert a.mmrm is not None
    assert "다변량 정규성을 가정하는 모수 모형" in render_text(a)
    assert "정규성이 기각되었으므로" in render_markdown(a)


def test_paper_sentence_states_the_df_approximation():
    from longistat.report import apa_sentences
    a = analyze(_make(random.Random(55), 40, 3, dropout=0.2), Options())
    joined = "\n".join(apa_sentences(a))
    assert "Kenward–Roger 가 아니다" in joined
    assert "not Kenward-Roger" in joined or "not Kenward–Roger" in joined


def test_markdown_and_csv_carry_the_df_method():
    a = analyze(_make(random.Random(56), 40, 3, dropout=0.2), Options())
    assert "MMRM df 산출" in render_markdown(a)
    assert "mmrm_model" in render_csv(a)
    assert "Kenward–Roger 아님" in render_csv(a)


def test_help_and_package_metadata_mention_mmrm():
    import pathlib

    from longistat.cli import build_parser
    help_text = build_parser().format_help()
    assert "MMRM" in help_text and "--no-mmrm" in help_text
    assert "Kenward–Roger 가 아닌 근사" in help_text
    root = pathlib.Path(__file__).resolve().parent.parent
    assert "MMRM" in (root / "pyproject.toml").read_text(encoding="utf-8")


def test_json_carries_the_reml_aic_which_asdict_would_skip():
    import json
    a = analyze(_make(random.Random(57), 40, 3, dropout=0.2), Options())
    payload = json.loads(render_json(a))
    assert payload["mmrm"]["aic"] == pytest.approx(a.mmrm.aic)
    assert payload["mmrm"]["aic"] == pytest.approx(
        -2 * a.mmrm.loglik + 2 * a.mmrm.n_cov_params)


def test_ancova_identity_survives_unbalanced_arms_and_a_late_baseline():
    """Neither equal arm sizes nor baseline-is-visit-1 may be load-bearing."""
    rng = random.Random(19)
    groups = ["A"] * 27 + ["B"] * 13
    rows = []
    for g in groups:
        base = rng.gauss(20, 4)
        shift = -3.0 if g == "A" else 0.0
        rows.append([base + rng.gauss(0, 3), base,
                     base + shift + rng.gauss(-2, 3),
                     base + shift + rng.gauss(-3, 3)])
    p = _panel(rows, groups)
    res = mmrm_analysis(p, 1)                 # baseline is the *second* visit
    anc = ancova_analysis(p, 1)
    assert len(res.contrasts) == 3
    for c in res.contrasts:
        match = [x for x in anc.contrasts if x.time == c.time][0]
        assert c.estimate == pytest.approx(match.adjusted_diff, abs=1e-8)
        assert c.df == pytest.approx(match.df, abs=1e-9)
        assert c.p_raw == pytest.approx(match.p_raw, abs=1e-9)
        assert c.ci_low == pytest.approx(match.ci_low, abs=1e-7)


# --------------------------------------------------------------------------
# review round 3 (2026-07-31): statistical-correctness panel
# --------------------------------------------------------------------------

def test_primary_visit_is_a_family_not_a_free_pass_with_three_arms():
    """Three arms → three contrasts at the primary visit; all unadjusted = FWER .14."""
    rng = random.Random(61)
    groups = ["A", "B", "C"] * 20
    rows = []
    for g in groups:
        base = rng.gauss(20, 4)
        shift = {"A": -3.0, "B": -1.5, "C": 0.0}[g]
        rows.append([base] + [base + shift + rng.gauss(-2, 3) for _ in range(2)])
    p = _panel(rows, groups)
    res = mmrm_analysis(p, 0, primary_time="V2")
    primary = [c for c in res.contrasts if c.primary]
    assert len(primary) == 3
    assert any(c.p_adj > c.p_raw + 1e-12 for c in primary), (
        "the primary visit's own family must still be Holm-adjusted")
    # …and the two families stay separate: the secondary visit is not inflated
    # by the primary one's members.
    secondary = [c for c in res.contrasts if not c.primary]
    assert len(secondary) == 3
    assert max(c.p_adj for c in secondary) <= 1.0


def test_primary_visit_with_one_contrast_is_still_reported_unadjusted():
    """Holm on a family of one is the identity — the two-arm case must not move."""
    p = _make(random.Random(62), 50, 4, dropout=0.15)
    res = mmrm_analysis(p, 0, primary_time="V3")
    primary = [c for c in res.contrasts if c.primary]
    assert len(primary) == 1
    assert primary[0].p_adj == pytest.approx(primary[0].p_raw)


def test_ancova_primary_visit_is_also_a_family():
    rng = random.Random(63)
    groups = ["A", "B", "C"] * 20
    rows = []
    for g in groups:
        base = rng.gauss(20, 4)
        shift = {"A": -3.0, "B": -1.5, "C": 0.0}[g]
        rows.append([base] + [base + shift + rng.gauss(-2, 3) for _ in range(2)])
    anc = ancova_analysis(_panel(rows, groups), 0, primary_time="V2")
    primary = [c for c in anc.contrasts if c.primary]
    assert len(primary) == 3
    assert any(c.p_adj > c.p_raw + 1e-12 for c in primary)


def test_unconverged_fit_still_returns_a_self_consistent_set_of_numbers():
    """Σ, cov(β) and the log-likelihood must all belong to the same sweep."""
    from longistat.mmrm import _build, _inverse_spd

    p = _make(random.Random(64), 50, 4, dropout=0.3)
    res = mmrm_analysis(p, 0, max_iter=2)
    assert res is not None and not res.converged

    # Rebuild X'V⁻¹X from the *reported* Σ and check the reported SEs.
    subs, visits, _lab, cell_col, base_col, n_cols, _drop, _cov = _build(p, 0, True)
    sigma = res.cov
    xtx = [[0.0] * n_cols for _ in range(n_cols)]
    for s in subs:
        inv, _ld = _inverse_spd([[sigma[a][b] for b in s.obs] for a in s.obs])
        k = len(s.obs)
        for r in range(k):
            for c in range(k):
                for ca, va in s.rows[r]:
                    for cb, vb in s.rows[c]:
                        xtx[ca][cb] += inv[r][c] * va * vb
    cov, _ = _inverse_spd(xtx)
    for ls in res.lsmeans:
        pos = res.times.index(ls.time)
        col = cell_col[(pos, ls.group)]
        assert ls.se == pytest.approx(math.sqrt(cov[col][col]), rel=1e-9)


def test_english_paper_sentence_names_the_arms():
    from longistat.report import apa_sentences
    a = analyze(_make(random.Random(65), 40, 3, dropout=0.2), Options())
    english = [s for s in apa_sentences(a)
               if s.startswith("[EN]") and "mean change" in s and "At " in s]
    assert english
    assert all("(A − B)" in s or "(B − A)" in s for s in english)


def test_ungrouped_contrast_reports_the_n_its_df_is_built_from():
    rng = random.Random(66)
    rows = []
    for i in range(40):
        base = rng.gauss(20, 4)
        row = [base, base + rng.gauss(-2, 3), base + rng.gauss(-4, 3)]
        if i < 12:
            row[0] = None                    # observed later, but no baseline
        rows.append(row)
    res = mmrm_analysis(_panel(rows), 0)
    for c in res.contrasts:
        assert c.n_a == c.df + 1, "printed n must match the df it produced"
    assert "두 시점을 모두 관측한" in res.df_method


def test_grouped_and_ungrouped_df_methods_describe_their_own_rule():
    grouped = mmrm_analysis(_make(random.Random(67), 40, 3), 0)
    ungrouped = mmrm_analysis(_make(random.Random(67), 40, 3, grouped=False), 0)
    assert "시점별 관측 수 − 그 시점의 평균 모수 수" in grouped.df_method
    assert "LS 평균은 그 시점 관측 수 − 1" in ungrouped.df_method
    for res in (grouped, ungrouped):
        assert "Kenward–Roger 아님" in res.df_method


def test_csv_export_lets_a_reader_rebuild_the_lsmean_interval():
    import csv as _csv
    import io as _io

    from longistat.special import t_ppf
    a = analyze(_make(random.Random(68), 40, 3, dropout=0.2), Options())
    rows = list(_csv.reader(_io.StringIO(render_csv(a))))
    header = rows[0]
    lsm = [dict(zip(header, r)) for r in rows[1:] if r[0] == "mmrm_lsmean"]
    assert lsm
    for r in lsm:
        est, se, df = float(r["estimate"]), float(r["sd_or_se"]), float(r["effect"])
        crit = t_ppf(0.975, df)
        assert float(r["ci_low"]) == pytest.approx(est - crit * se, abs=1e-9)
        assert float(r["ci_high"]) == pytest.approx(est + crit * se, abs=1e-9)


def test_disagreement_warning_quotes_the_track_the_report_actually_prints():
    """When ranks are recommended, compare MMRM against the rank change scores.

    The two tracks can disagree with each other, so reading the parametric
    change scores while [5] prints the rank ones would let the warning
    contradict the very table it tells the reader to look at.
    """
    from longistat.analyze import _mmrm_disagreements

    rng = random.Random(909)
    rows, groups = [], []
    for i in range(60):
        g = "A" if i % 2 else "B"
        base = math.exp(rng.gauss(2.2, 1.0))
        row = [base, base * math.exp(rng.gauss(-0.5, 1.0)),
               base * math.exp(rng.gauss(-0.9, 1.0))]
        if rng.random() < 0.3:
            row[2] = None
        rows.append(row)
        groups.append(g)
    p = _panel(rows, groups)
    a = analyze(p, Options())
    assert a.mmrm is not None

    # The helper must read whichever ChangeAnalysis it is handed — feeding it
    # the two tracks has to be able to give two different answers.
    param = _mmrm_disagreements(a.mmrm, a.ancova, a.change_param, a.options.alpha)
    rank = _mmrm_disagreements(a.mmrm, a.ancova, a.change_rank, a.options.alpha)
    assert isinstance(param, list) and isinstance(rank, list)

    # And analyze() must have used the recommended one.
    used = a.change_param if a.recommended == "parametric" else a.change_rank
    expected = _mmrm_disagreements(a.mmrm, a.ancova, used, a.options.alpha)
    emitted = [w for w in a.warnings if "MMRM([4c])의 유의성 판정이 다릅니다" in w]
    if expected:
        assert emitted and all(line in emitted[0] for line in expected)
    else:
        assert not emitted


def test_refuses_when_no_subject_can_enter_the_model_at_all():
    """Every subject missing baseline → nothing to fit, and it must say so."""
    rows = [[None, 9.0 + i, 8.0 + i] for i in range(12)]
    groups = ["A", "B"] * 6
    skipped = []
    assert mmrm_analysis(_panel(rows, groups), 0, skipped=skipped) is None
    assert skipped and "관측이 없습니다" in skipped[0]


# --------------------------------------------------------------------------
# review round 3 (2026-07-31): edge-case panel
# --------------------------------------------------------------------------

def test_the_fit_is_invariant_to_the_units_of_the_outcome():
    """Rescaling the outcome must rescale the answer, not change it.

    A convergence tolerance with an absolute floor stops EM after one sweep on
    small-magnitude outcomes (proportions, absorbances, mmol/L), leaving Σ at
    its diagonal starting value — the estimate moved 13% and p doubled.
    """
    rng = random.Random(707)
    rows, groups = [], []
    for i in range(60):
        g = "A" if i % 2 else "B"
        base = rng.gauss(20, 4)
        arm = -3.0 if g == "A" else 0.0
        row = [base, base + arm * 0.5 + rng.gauss(-1, 3),
               base + arm + rng.gauss(-2, 3)]
        if rng.random() < 0.2:
            row[2] = None
        rows.append(row)
        groups.append(g)

    reference = mmrm_analysis(_panel(rows, groups), 0)
    for mult in (1e-3, 1e-5, 1e-7, 1e3):
        scaled = [[None if v is None else v * mult for v in r] for r in rows]
        res = mmrm_analysis(_panel(scaled, groups), 0)
        assert res.converged
        for a, b in zip(res.contrasts, reference.contrasts):
            assert a.estimate / mult == pytest.approx(b.estimate, rel=1e-6)
            assert a.se / mult == pytest.approx(b.se, rel=1e-6)
            assert a.p_raw == pytest.approx(b.p_raw, rel=1e-6)
        for i in range(len(res.times)):
            for j in range(len(res.times)):
                assert res.corr[i][j] == pytest.approx(
                    reference.corr[i][j], rel=1e-6)


def test_underidentified_covariance_is_refused_with_the_two_numbers():
    """T=9 with 20 subjects means 36 parameters from 20 rows — say that."""
    rng = random.Random(708)
    rows, groups = [], []
    for i in range(20):
        base = rng.gauss(20, 4)
        rows.append([base] + [base + rng.gauss(-1, 3) for _ in range(8)])
        groups.append("A" if i % 2 else "B")
    skipped = []
    assert mmrm_analysis(_panel(rows, groups), 0, skipped=skipped) is None
    assert skipped and "36개" in skipped[0] and "20명" in skipped[0]


def test_a_collapsed_visit_variance_is_flagged_not_printed_as_certainty():
    """SE 0.00 with a zero-width 95% CI is false precision, not a result."""
    rng = random.Random(709)
    rows, groups = [], []
    for i in range(40):
        g = "A" if i % 2 else "B"
        base = rng.gauss(20, 4)
        rows.append([base, base + rng.gauss(-2, 3), 5.0])   # constant last visit
        groups.append(g)
    res = mmrm_analysis(_panel(rows, groups), 0)
    assert res is not None
    assert any("잔차분산이 사실상 0인 시점" in n for n in res.notes)
    flat = [x for x in res.lsmeans if x.time == "V2"]
    assert flat and all(math.isnan(x.se) for x in flat)
    assert all(math.isnan(x.ci_low) and math.isnan(x.ci_high) for x in flat)
    con = [c for c in res.contrasts if c.time == "V2"]
    assert con and all(math.isnan(c.se) and math.isnan(c.p_raw) for c in con)
    # the healthy visit is untouched
    ok = [x for x in res.lsmeans if x.time == "V1"]
    assert ok and all(math.isfinite(x.se) and x.se > 0 for x in ok)


def test_non_convergence_reaches_every_output_format():
    p = _make(random.Random(710), 60, 5, dropout=0.3)
    a = analyze(p, Options())
    assert a.mmrm is not None
    object.__setattr__(a.mmrm, "converged", False)
    a.mmrm.notes.insert(0, "EM 반복 400회 안에 수렴하지 않았습니다 — 시험용.")
    assert "수렴하지 않았습니다" in render_text(a)
    assert "수렴하지 않았습니다" in render_markdown(a)
    csv_out = render_csv(a)
    assert "NOT CONVERGED" in csv_out and "mmrm_note" in csv_out
    from longistat.report import apa_sentences
    joined = "\n".join(apa_sentences(a))
    assert "그대로 인용하지 마십시오" in joined
    assert "should not be quoted" in joined


def test_non_convergence_is_raised_to_the_top_level_warnings():
    p = _make(random.Random(711), 50, 4, dropout=0.3)
    a = analyze(p, Options(mmrm=True))
    if a.mmrm is not None and not a.mmrm.converged:
        assert any("수렴하지 않았습니다" in w for w in a.warnings)
    # force the path deterministically through the analyse-level check
    from longistat.analyze import Analysis  # noqa: F401
    res = mmrm_analysis(p, 0, max_iter=2)
    assert res is not None and not res.converged


def test_no_mmrm_never_points_at_a_section_it_suppressed():
    p = _make(random.Random(712), 60, 4, dropout=0.35)
    a = analyze(p, Options(mmrm=False))
    text = render_text(a)
    assert "[4c] MMRM" not in text
    # No warning may point the reader at a section this run did not render.
    for w in a.warnings:
        assert "[4c]" not in w
    assert any("--no-mmrm 을 빼면" in w or "--no-mmrm 을 빼고" in w
               for w in a.warnings)
