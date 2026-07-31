"""Report rendering: labels must describe the numbers actually printed.

The report is what gets pasted into a manuscript, so a wrong *label* on a
right number is as damaging as a wrong number.
"""

import math
from random import Random

import pytest

from helpers import write_csv
from medpath.dataio import build_design, load_table
from medpath.mediation import analyze
from medpath.report import display_width, fmt, fmt_p, render


def _sim(n=120, seed=7):
    rng = Random(seed)
    rows = []
    for i in range(n):
        x = float(i % 2)
        cov = round(rng.gauss(50, 10), 2)
        m = round(10 + 2.0 * x + 0.05 * cov + rng.gauss(0, 1.5), 4)
        y = round(5 + 0.8 * m + 1.0 * x + 0.02 * cov + rng.gauss(0, 2.0), 4)
        rows.append([x, m, y, cov])
    return rows


def _result(tmp_path, conf=0.95, n_boot=200, **kw):
    t = load_table(write_csv(tmp_path / "r.csv", ["x", "m", "y", "cov"], _sim()))
    d = build_design(t, "x", ["m"], "y", ["cov"])
    return analyze(d, conf=conf, n_boot=n_boot, **kw)


# --------------------------------------------------------------------------
# Confidence-level labelling
# --------------------------------------------------------------------------
@pytest.mark.parametrize("conf,label", [(0.95, "95% CI"), (0.90, "90% CI"),
                                        (0.99, "99% CI")])
def test_regression_tables_label_the_requested_confidence_level(tmp_path, conf, label):
    """Regression coefficient CIs are built with --conf, so they must say so.

    Regression: the header was hard-coded to "95% CI" and silently mislabelled
    every run at any other confidence level.
    """
    text = render(_result(tmp_path, conf=conf), "r.csv")
    assert label in text
    for other in ("95% CI", "90% CI", "99% CI"):
        if other != label:
            assert other not in text, "report mixes %s into a %s run" % (other, label)


def test_regression_ci_columns_match_the_coefficient_intervals(tmp_path):
    """The number under the "90% CI" header is really the 90% interval."""
    res = _result(tmp_path, conf=0.90)
    text = render(res, "r.csv")
    c = res.total_regression.coef("x")
    assert "%.3f" % c.ci_lo in text and "%.3f" % c.ci_hi in text
    # ...and it is genuinely narrower than the 95% one on the same data.
    res95 = _result(tmp_path, conf=0.95)
    c95 = res95.total_regression.coef("x")
    assert (c.ci_hi - c.ci_lo) < (c95.ci_hi - c95.ci_lo)


def test_markdown_mode_also_labels_the_confidence_level(tmp_path):
    text = render(_result(tmp_path, conf=0.90), "r.csv", mode="md")
    assert "90% CI" in text
    assert "95% CI" not in text


# --------------------------------------------------------------------------
# Formatting primitives
# --------------------------------------------------------------------------
def test_fmt_handles_nan_inf_and_none():
    assert fmt(None) == "—"
    assert fmt(float("nan")) == "—"
    assert fmt(float("inf")) == "∞"
    assert fmt(float("-inf")) == "-∞"
    assert fmt(1.23456, 3) == "1.235"


def test_fmt_p_is_apa_style():
    assert fmt_p(0.0004) == "< .001"
    assert fmt_p(0.0432) == "= .043"
    assert fmt_p(float("nan")) == "—"


def test_display_width_counts_cjk_as_two_columns():
    assert display_width("abc") == 3
    assert display_width("매개") == 4
    assert display_width("a매개") == 5


# --------------------------------------------------------------------------
# Report content honesty
# --------------------------------------------------------------------------
def test_report_states_the_analysed_n_not_the_file_n(tmp_path):
    rows = _sim(n=60)
    rows[0][1] = ""          # blow a hole in M -> one row dropped listwise
    t = load_table(write_csv(tmp_path / "m.csv", ["x", "m", "y", "cov"], rows))
    d = build_design(t, "x", ["m"], "y", ["cov"])
    res = analyze(d, n_boot=100)
    text = render(res, "m.csv")
    assert d.n_total == 60 and d.n_used == 59
    assert "파일 60행 → 분석 59행" in text


def test_brief_mode_omits_the_regression_tables(tmp_path):
    res = _result(tmp_path)
    full = render(res, "r.csv", brief=False)
    brief = render(res, "r.csv", brief=True)
    assert "경로 계수 (회귀 결과)" in full
    assert "경로 계수 (회귀 결과)" not in brief
    assert len(brief) < len(full)


# --------------------------------------------------------------------------
# "not tested" must never be rendered as "not significant"
# --------------------------------------------------------------------------
def test_untested_effect_is_not_called_not_significant(tmp_path):
    """--bootstrap 0 computes no interval, so nothing was tested.

    Regression: the APA sentences read "간접효과는 유의하지 않았다 / the indirect
    effect was not significant" for an effect that had never been tested — a
    paste-ready false negative.
    """
    res = _result(tmp_path, n_boot=0)
    text = render(res, "r.csv")
    assert "유의하지 않았다" not in text
    assert "not significant" not in text
    assert "검정하지 않음" in text
    assert "not tested" in text


def test_untested_effect_is_flagged_as_such_in_the_effect_table(tmp_path):
    text = render(_result(tmp_path, n_boot=0), "r.csv")
    line = [ln for ln in text.splitlines() if "간접효과" in ln and "→" in ln][0]
    assert "검정 안 함" in line
    assert "0 포함" not in line


def test_untested_effect_does_not_claim_a_ci_method(tmp_path):
    text = render(_result(tmp_path, n_boot=0), "r.csv")
    assert "신뢰구간 없음" in text
    assert "0을 포함(효과 근거 부족)" not in text


def test_bootstrap_zero_emits_an_explicit_warning(tmp_path):
    res = _result(tmp_path, n_boot=0)
    assert any("--bootstrap 0" in w for w in res.warnings)
    assert "'유의하지 않다'는 뜻이 아닙니다" in " ".join(res.warnings)


def test_tested_flag_separates_null_from_untested(tmp_path):
    untested = _result(tmp_path, n_boot=0).indirect_effects[0]
    tested = _result(tmp_path, n_boot=300).indirect_effects[0]
    assert untested.tested is False and untested.significant is False
    assert tested.tested is True


def test_json_marks_untested_effects_distinctly(tmp_path):
    d = _result(tmp_path, n_boot=0).to_dict()
    eff = [e for e in d["effects"] if e["kind"] == "indirect"][0]
    assert eff["tested"] is False
    assert eff["excludes_zero"] is None      # not False — it was never tested


def test_tested_effects_keep_the_normal_wording(tmp_path):
    text = render(_result(tmp_path, n_boot=400), "r.csv")
    assert ("유의하였다" in text) or ("유의하지 않았다" in text)
    assert "검정하지 않음" not in text


def test_bootstrap_effects_are_judged_by_the_interval_not_a_p_value(tmp_path):
    """Indirect effects must never be reported with a p-value column."""
    res = _result(tmp_path)
    text = render(res, "r.csv")
    eff = res.indirect_effects[0]
    line = [ln for ln in text.splitlines() if eff.label in ln][0]
    assert ("0 미포함" in line) or ("0 포함" in line)
