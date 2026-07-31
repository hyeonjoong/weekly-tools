"""Regression tests for the round-1 hardening findings (2026-07-31).

Each test names the defect it locks down so a future refactor cannot silently
reintroduce it.
"""

import json
import math

import pytest

from agreestat.analyze import analyze
from agreestat.cli import main
from agreestat.multirater import (
    _spearman_brown,
    fleiss_kappa,
    gwet_ac1_multi,
    icc_family,
    krippendorff_alpha_multi,
    multi_categorical,
    multi_continuous,
)
from agreestat.multireport import render_multi_text, render_multicat_text
from agreestat.report import render_json, render_text

CATS = ["mild", "moderate", "severe"]


# --------------------------------------------------------------------------
# S1 — Fleiss H0 standard error must not depend on the number of raters
# --------------------------------------------------------------------------
def test_fleiss_h0_se_has_no_rater_count_term():
    """The old (wrong) formula inflated SE 3x on skewed marginals."""
    # 200 subjects, 4 raters, marginals ~ (0.9, 0.1)
    counts = [[4, 0]] * 130 + [[3, 1]] * 50 + [[2, 2]] * 15 + [[1, 3]] * 5
    _k, _pb, pe, se, m, p_j = fleiss_kappa(counts)
    n = len(counts)
    s = sum(p * (1 - p) for p in p_j)
    var = ((2.0 / (n * m * (m - 1)))
           * (s ** 2 - sum(p * (1 - p) * ((1 - p) - p) for p in p_j)) / s ** 2)
    assert se == pytest.approx(math.sqrt(var), rel=1e-12)
    # the discarded formula would have produced a much larger SE
    p3 = sum(p ** 3 for p in p_j)
    old = (math.sqrt(2 * (pe - (2 * m - 3) * pe ** 2 + 2 * (m - 2) * p3))
           / ((1 - pe) * math.sqrt(n * m * (m - 1))))
    assert old > 2.0 * se


def test_fleiss_se_agrees_with_old_formula_only_for_uniform_marginals():
    """Sanity: the two formulas coincide exactly when marginals are uniform."""
    counts = [[2, 2]] * 40
    _k, _pb, pe, se, m, p_j = fleiss_kappa(counts)
    n = len(counts)
    p3 = sum(p ** 3 for p in p_j)
    old = (math.sqrt(2 * (pe - (2 * m - 3) * pe ** 2 + 2 * (m - 2) * p3))
           / ((1 - pe) * math.sqrt(n * m * (m - 1))))
    assert se == pytest.approx(old, rel=1e-12)


# --------------------------------------------------------------------------
# S2 — SEM/MDC95 must include systematic rater offsets
# --------------------------------------------------------------------------
def test_mdc95_is_not_zero_when_raters_differ_by_a_constant():
    rows = [[float(i) + 0.3, float(i) - 0.3, float(i) + 10.0] for i in range(12)]
    fam = icc_family(rows)
    assert fam.sem_consistency == pytest.approx(0.0, abs=1e-9)
    assert fam.sem == pytest.approx(math.sqrt(fam.msw))
    assert fam.mdc95 > 15.0
    txt = render_multi_text(multi_continuous(["a", "b", "c"], rows))
    assert "절대일치 기준" in txt and "일관성 기준" in txt


# --------------------------------------------------------------------------
# S3 — Spearman-Brown pole must not emit a reversed / out-of-range CI
# --------------------------------------------------------------------------
def test_spearman_brown_returns_nan_past_the_pole():
    assert math.isnan(_spearman_brown(-0.6, 3))    # 1 + 2*(-0.6) < 0
    assert math.isnan(_spearman_brown(-0.5, 3))    # exactly at the pole
    assert _spearman_brown(0.5, 3) == pytest.approx(0.75)


def test_icc2k_ci_never_reversed_or_above_one():
    rows = [[-0.22, -1.61, 2.16], [-0.58, 0.17, -1.70],
            [-1.02, -0.02, -2.97], [0.60, -0.21, -2.37]]
    fam = icc_family(rows)
    for r in list(fam.single) + list(fam.average):
        if math.isfinite(r.ci_lower) and math.isfinite(r.ci_upper):
            assert r.ci_lower <= r.ci_upper
            assert r.ci_upper <= 1.0 + 1e-12
            assert r.ci_lower - 1e-9 <= r.value <= r.ci_upper + 1e-9
    txt = render_multi_text(multi_continuous(["a", "b", "c"], rows))
    assert "8.134" not in txt


# --------------------------------------------------------------------------
# S4 — unanimous data is perfect agreement, not a dropped resample
# --------------------------------------------------------------------------
def test_unanimous_table_gives_coefficients_of_one():
    counts = [[3, 0, 0]] * 20
    assert fleiss_kappa(counts)[0] == pytest.approx(1.0)
    assert gwet_ac1_multi(counts)[0] == pytest.approx(1.0)
    assert krippendorff_alpha_multi(counts, CATS) == pytest.approx(1.0)


def test_bootstrap_ci_not_truncated_at_the_perfect_agreement_end():
    """Unanimous resamples are kappa=1, not dropped — the CI must reach 1.0.

    With 19 of 20 subjects unanimous, ~36% of resamples are fully unanimous;
    the old code discarded every one of them, truncating the CI from above.
    """
    rows = [["mild", "mild", "mild"]] * 19 + [["mild", "moderate", "severe"]]
    res = multi_categorical(["A", "B", "C"], rows, CATS, bootstrap=2000)
    assert res.fleiss_ci[1] == pytest.approx(1.0)


# --------------------------------------------------------------------------
# S5 — an undefined CI is "undecidable", not "criterion not met"
# --------------------------------------------------------------------------
def test_threshold_is_undecidable_when_the_ci_is_undefined():
    rows = [["mild"] * 3] * 20        # perfect unanimity -> degenerate CI
    res = multi_categorical(["A", "B", "C"], rows, CATS, bootstrap=300,
                            min_kappa=0.6)
    if not math.isfinite(res.fleiss_ci[0]):
        assert res.meets_threshold is None
        assert "판정 불가" in render_multicat_text(res)
    else:                              # unanimity now yields kappa = 1
        assert res.meets_threshold is True


def test_threshold_undecidable_renders_as_such():
    rows = [["mild", "moderate", "severe"]] * 3
    res = multi_categorical(["A", "B", "C"], rows, CATS, bootstrap=300,
                            min_kappa=0.6)
    txt = render_multicat_text(res)
    assert ("판정 불가" in txt) or ("미충족" in txt)
    assert "NaN <" not in txt


# --------------------------------------------------------------------------
# S6 — alpha's CI must resample the units its point estimate uses
# --------------------------------------------------------------------------
def test_alpha_ci_covers_the_partially_rated_units_too():
    rows = ([["mild", "mild", "moderate"], ["severe", "severe", "severe"],
             ["mild", "moderate", "mild"], ["moderate", "moderate", "moderate"],
             ["severe", "moderate", "severe"], ["mild", "mild", "mild"],
             ["moderate", "severe", "moderate"]]
            + [["severe", "severe", ""], ["mild", "", "mild"],
               ["moderate", "", "severe"], ["", "mild", "mild"],
               ["severe", "moderate", ""]])
    res = multi_categorical(["A", "B", "C"], rows, CATS, bootstrap=1500)
    assert res.n == 7 and res.n_alpha == 12
    assert math.isfinite(res.kalpha_ci[0])
    assert res.kalpha_ci[0] <= res.kalpha <= res.kalpha_ci[1]


# --------------------------------------------------------------------------
# S7 — Gwet's pi_k averages over every rated subject
# --------------------------------------------------------------------------
def test_gwet_pi_uses_all_rated_subjects():
    counts = [[3, 0], [2, 1], [1, 2], [0, 3], [1, 0], [1, 0]]
    ac1, pa, pe = gwet_ac1_multi(counts)
    # pi = (0.667, 0.333) over all six rated subjects, not (0.5, 0.5)
    assert pe == pytest.approx(2 * (2 / 3) * (1 / 3), rel=1e-12)
    assert ac1 == pytest.approx((pa - pe) / (1 - pe), rel=1e-12)


# --------------------------------------------------------------------------
# S8 — non-estimable tables are flagged
# --------------------------------------------------------------------------
def test_tiny_and_degenerate_tables_are_flagged():
    res = multi_continuous(["a", "b", "c"], [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    assert any("추정 불가능" in w for w in res.warnings)
    res2 = multi_continuous(["a", "b", "c"],
                            [[float(i)] * 3 for i in range(10)])
    assert any("측정오차를 추정할 수 없다" in w for w in res2.warnings)


# --------------------------------------------------------------------------
# Docs-honesty findings
# --------------------------------------------------------------------------
def test_continuous_report_grades_from_the_ci_lower_bound():
    """Point estimate 'excellent' but CI lower 'moderate' -> must be flagged."""
    rows = [[1.69, 3.61, 1.82], [4.87, 4.96, 4.11], [5.62, 6.12, 6.61],
            [2.37, 3.76, 2.40], [4.14, 1.85, 4.04], [5.90, 7.03, 6.30],
            [5.25, 5.34, 5.14], [1.70, 1.81, -0.18], [5.30, 5.22, 5.70],
            [9.35, 8.76, 9.93]]
    fam = multi_continuous(["a", "b", "c"], rows).icc
    assert fam.single[1].ci_lower < 0.9 <= fam.single[1].value
    txt = render_multi_text(multi_continuous(["a", "b", "c"], rows))
    assert "Koo & Li(2016) 권장" in txt
    assert "CI 하한 기준 신뢰도 등급" in txt


def test_categorical_sentence_states_metric_weighting_and_order():
    rows = [["mild", "mild", "moderate"], ["severe", "severe", "severe"],
            ["mild", "moderate", "mild"], ["moderate", "moderate", "moderate"],
            ["severe", "moderate", "severe"], ["mild", "mild", "mild"]] * 4
    res = multi_categorical(["A", "B", "C"], rows, CATS, ordinal=True,
                            weights="quadratic", bootstrap=500)
    txt = render_multicat_text(res)
    assert "Fleiss' kappa(비가중)" in txt
    assert "Krippendorff's alpha(ordinal)" in txt
    assert "범주 순서 mild<moderate<severe" in txt
    assert "해석 기준(Landis & Koch 1977)" in txt


def test_negative_ci_bounds_are_readable_in_prose():
    rows = [["mild", "moderate", "severe"], ["severe", "mild", "moderate"],
            ["moderate", "severe", "mild"]] * 5
    txt = render_multicat_text(
        multi_categorical(["A", "B", "C"], rows, CATS, bootstrap=400))
    assert "–-" not in txt          # never an en dash followed by a minus sign


def test_pairwise_loa_header_states_the_confidence_level():
    rows = [[float(i), float(i) + 1, float(i) - 1] for i in range(10)]
    txt = render_multi_text(multi_continuous(["a", "b", "c"], rows, alpha=0.01))
    assert "LoA(99%)" in txt


def test_small_categorical_sample_warns_below_twenty():
    rows = [["mild", "mild", "moderate"], ["severe", "severe", "severe"],
            ["mild", "moderate", "mild"]] * 5      # n = 15
    res = multi_categorical(["A", "B", "C"], rows, CATS, bootstrap=300)
    assert any("20건 미만" in w for w in res.warnings)


# --------------------------------------------------------------------------
# Acceptance verdict must not hide an LoA CI that crosses the limit
# --------------------------------------------------------------------------
def _accept_case():
    a = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0]
    b = [10.4, 10.7, 12.5, 12.6, 14.4, 15.3, 15.6, 17.4, 17.6, 19.4]
    return a, b


def test_interchangeable_verdict_flags_a_loa_ci_that_exceeds_the_limit():
    a, b = _accept_case()
    res = analyze(a, b, accept=(-1.0, 1.0))
    if res.interchangeable:
        assert res.loa_ci_exceeds_accept is True
        txt = render_text(res)
        assert "LoA 신뢰구간은 한계를 넘음" in txt
        assert "확정할 수 없" in txt
        data = json.loads(render_json(res))
        assert data["acceptance"]["loa_ci_exceeds_limit"] is True


def test_clean_interchangeable_case_has_no_caveat():
    a = [float(i) for i in range(40)]
    b = [float(i) + 0.01 for i in range(40)]
    res = analyze(a, b, accept=(-5.0, 5.0))
    assert res.interchangeable is True
    assert res.loa_ci_exceeds_accept is False
    assert "LoA 신뢰구간은 한계를 넘음" not in render_text(res)


def test_wrong_mode_error_points_at_categorical(tmp_path, capsys):
    p = tmp_path / "c.csv"
    p.write_text("id,a,b,c\n1,mild,mild,severe\n2,severe,severe,severe\n",
                 encoding="utf-8")
    assert main([str(p), "--raters", "a,b,c"]) == 2
    assert "--categorical" in capsys.readouterr().err
