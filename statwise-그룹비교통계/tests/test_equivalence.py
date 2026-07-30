"""TOST / non-inferiority tests, validated against statsmodels + hand math.

Reference p-values were produced with
``statsmodels.stats.weightstats.ttost_ind`` / ``ttost_paired`` (0.14.6) and
``scipy.stats.t`` (1.17.1) and are hard-coded here so the suite keeps running
with zero third-party dependencies.
"""

import math

import pytest

from statwise import EquivalenceSpec, analyze, analyze_paired
from statwise.equivalence import (noninferiority, noninferiority_independent,
                                  noninferiority_paired, parse_margin, tost,
                                  tost_independent, tost_paired)
from statwise.report import render_json, render_text, result_to_dict

A = [5.1, 4.9, 5.3, 5.0, 5.2, 4.8, 5.4, 5.0]
B = [5.0, 5.2, 4.7, 5.1, 4.9, 5.3, 5.1, 4.8]
PA = [10.0, 11.5, 9.8, 10.2, 11.0, 10.6, 9.9, 10.4, 10.8, 11.2]
PB = [10.2, 11.0, 10.1, 10.0, 11.3, 10.4, 10.2, 10.1, 11.0, 11.1]


# --------------------------------------------------------------------------
# core math vs external reference
# --------------------------------------------------------------------------

def test_tost_independent_welch_matches_statsmodels():
    r = tost_independent(A, B, -0.5, 0.5, alpha=0.05, model="welch")
    assert r.diff == pytest.approx(0.075, abs=1e-12)
    assert r.se == pytest.approx(0.1015504800579495, rel=1e-12)
    assert r.df == pytest.approx(14.000000000000002, rel=1e-9)
    assert r.t_low == pytest.approx(5.662208585049316, rel=1e-9)
    assert r.p_low == pytest.approx(2.9318945680267672e-05, rel=1e-7)
    assert r.t_high == pytest.approx(-4.185110693297302, rel=1e-9)
    assert r.p_high == pytest.approx(0.0004583535000607423, rel=1e-7)
    # statsmodels ttost_ind(..., usevar='unequal')[0]
    assert r.pvalue == pytest.approx(0.000458353500060742, rel=1e-7)
    assert r.concluded is True


def test_tost_independent_student_matches_statsmodels():
    r = tost_independent(A, B, -0.5, 0.5, alpha=0.05, model="student")
    assert r.df == pytest.approx(14.0)
    # statsmodels ttost_ind(..., usevar='pooled')[0]
    assert r.pvalue == pytest.approx(0.00045835350006074205, rel=1e-7)


def test_tost_paired_matches_statsmodels():
    r = tost_paired(PA, PB, -0.5, 0.5, alpha=0.05)
    assert r.model == "paired"
    assert r.df == pytest.approx(9.0)
    assert r.se == pytest.approx(0.09309493362512616, rel=1e-12)
    # statsmodels ttost_paired(...)[0]
    assert r.pvalue == pytest.approx(0.000224926879679193, rel=1e-7)
    assert r.concluded is True


def test_noninferiority_matches_one_sided_t():
    r = noninferiority_independent(A, B, 0.5, "higher_is_better", alpha=0.05,
                                   model="welch")
    assert r.t_low == pytest.approx(5.662208585049316, rel=1e-9)
    assert r.pvalue == pytest.approx(2.9318945680267672e-05, rel=1e-7)
    assert r.ci_low == pytest.approx(-0.10386188981887137, rel=1e-7)
    assert r.ci_high is None
    assert r.concluded is True


def test_noninferiority_lower_is_better_is_mirror_image():
    """Flipping the sign of the data must flip the direction, not the verdict."""
    hi = noninferiority_independent(A, B, 0.5, "higher_is_better")
    lo = noninferiority_independent([-x for x in A], [-x for x in B], 0.5,
                                    "lower_is_better")
    assert lo.pvalue == pytest.approx(hi.pvalue, rel=1e-12)
    assert lo.concluded == hi.concluded
    assert lo.ci_high == pytest.approx(-hi.ci_low, rel=1e-12)


# --------------------------------------------------------------------------
# the defining identities (recomputed from first principles)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("low,high", [(-0.5, 0.5), (-0.05, 0.05),
                                      (-1.0, 0.2), (0.0, 1.5), (-2.0, -0.01)])
def test_tost_verdict_equals_ci_containment(low, high):
    """p_TOST < alpha  <=>  the (1-2a) CI lies wholly inside the margin."""
    r = tost_independent(A, B, low, high, alpha=0.05)
    inside = r.ci_low > low and r.ci_high < high
    assert r.concluded == inside
    assert r.conf == pytest.approx(0.90)


@pytest.mark.parametrize("margin", [0.05, 0.2, 0.5, 1.0, 3.0])
def test_noninferiority_verdict_equals_bound_vs_margin(margin):
    r = noninferiority_independent(A, B, margin, "higher_is_better", alpha=0.05)
    assert r.concluded == (r.ci_low > -margin)
    assert r.conf == pytest.approx(0.95)
    # p < alpha and the CI rule must agree
    assert (r.pvalue < 0.05) == r.concluded


def test_tost_pvalue_is_max_of_the_two_one_sided_tests():
    r = tost(0.3, 0.2, 20.0, -1.0, 1.0, alpha=0.05)
    assert r.pvalue == pytest.approx(max(r.p_low, r.p_high))
    assert r.p_low < 0.5 and r.p_high < 0.5


def test_wider_margin_never_makes_equivalence_harder():
    """Monotonicity: enlarging the margin can only lower p_TOST."""
    prev = None
    for width in (0.05, 0.1, 0.2, 0.4, 0.8, 1.6):
        p = tost_independent(A, B, -width, width, alpha=0.05).pvalue
        if prev is not None:
            assert p <= prev + 1e-12
        prev = p


def test_tost_is_symmetric_under_group_swap():
    r = tost_independent(A, B, -0.4, 0.6)
    s = tost_independent(B, A, -0.6, 0.4)
    assert s.pvalue == pytest.approx(r.pvalue, rel=1e-12)
    assert s.concluded == r.concluded


# --------------------------------------------------------------------------
# validation / error handling
# --------------------------------------------------------------------------

@pytest.mark.parametrize("low,high", [(1.0, 1.0), (2.0, 1.0),
                                      (float("nan"), 1.0), (0.0, float("inf"))])
def test_tost_rejects_bad_margins(low, high):
    with pytest.raises(ValueError):
        tost(0.1, 0.2, 10.0, low, high)


def test_tost_rejects_zero_standard_error():
    with pytest.raises(ValueError, match="standard error"):
        tost(0.0, 0.0, 10.0, -1.0, 1.0)


def test_constant_groups_do_not_crash_the_analysis():
    """SE=0 must degrade to a warning, never an exception."""
    res = analyze([("a", [1.0, 1.0, 1.0, 1.0]), ("b", [1.0, 1.0, 1.0, 1.0])],
                  equivalence=EquivalenceSpec(margin=(-1.0, 1.0)))
    assert res.equivalence is None
    assert any("등가" in w for w in res.warnings)


@pytest.mark.parametrize("bad_alpha", [0.0, 0.5, 1.0, -0.1])
def test_alpha_out_of_range_rejected(bad_alpha):
    with pytest.raises(ValueError):
        tost(0.1, 0.2, 10.0, -1.0, 1.0, alpha=bad_alpha)
    with pytest.raises(ValueError):
        noninferiority(0.1, 0.2, 10.0, 1.0, alpha=bad_alpha)


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_noninferiority_rejects_bad_margin(bad):
    with pytest.raises(ValueError):
        noninferiority(0.1, 0.2, 10.0, bad)


def test_noninferiority_rejects_bad_direction():
    with pytest.raises(ValueError, match="direction"):
        noninferiority(0.1, 0.2, 10.0, 1.0, direction="sideways")


# --------------------------------------------------------------------------
# margin parsing
# --------------------------------------------------------------------------

def test_parse_margin_symmetric_and_asymmetric():
    assert parse_margin("1.5") == (-1.5, 1.5)
    assert parse_margin("-1.5") == (-1.5, 1.5)
    assert parse_margin(" -1.0 , 2.0 ") == (-1.0, 2.0)


@pytest.mark.parametrize("bad", ["", "abc", "0", "1,2,3", "2.0,1.0", "nan",
                                 "inf", "1;2"])
def test_parse_margin_rejects_junk(bad):
    with pytest.raises(ValueError):
        parse_margin(bad)


# --------------------------------------------------------------------------
# integration through analyze() / analyze_paired() / reporting
# --------------------------------------------------------------------------

def test_analyze_runs_tost_when_spec_given():
    res = analyze([("a", A), ("b", B)],
                  equivalence=EquivalenceSpec(margin=(-0.5, 0.5)))
    assert res.equivalence is not None
    assert res.equivalence.kind == "tost"
    assert res.equivalence.concluded is True
    # the t-model must match the selected superiority test
    assert res.equivalence.model in ("student", "welch")
    assert res.test_name.lower().startswith(res.equivalence.model[:5])


def test_analyze_without_spec_leaves_equivalence_none():
    res = analyze([("a", A), ("b", B)])
    assert res.equivalence is None
    assert "equivalence" not in result_to_dict(res)


def test_paired_equivalence_uses_paired_model():
    res = analyze_paired(("post", PA), ("pre", PB),
                         equivalence=EquivalenceSpec(margin=(-0.5, 0.5)))
    assert res.equivalence.model == "paired"
    assert res.equivalence.df == pytest.approx(9.0)


def test_rank_test_equivalence_is_flagged_as_approximate():
    """Mann-Whitney + TOST is allowed but must warn that the model differs."""
    skewed_a = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 40.0]
    skewed_b = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.7, 45.0]
    res = analyze([("a", skewed_a), ("b", skewed_b)],
                  equivalence=EquivalenceSpec(margin=(-30.0, 30.0)))
    assert res.test_name == "Mann-Whitney U test"
    assert res.equivalence is not None
    assert any("순위검정" in w for w in res.warnings)


def test_equivalence_skipped_with_more_than_two_groups():
    res = analyze([("a", A), ("b", B), ("c", [5.0, 5.1, 5.2, 4.9, 5.0, 5.3,
                                              5.1, 4.95])],
                  equivalence=EquivalenceSpec(margin=(-0.5, 0.5)))
    assert res.equivalence is None
    assert any("두 그룹" in w for w in res.warnings)


def test_equivalence_spec_rejects_both_margins():
    with pytest.raises(ValueError):
        EquivalenceSpec(margin=(-1.0, 1.0), ni_margin=1.0)


def test_report_renders_equivalence_sections():
    res = analyze([("a", A), ("b", B)],
                  equivalence=EquivalenceSpec(margin=(-0.5, 0.5)))
    text = render_text(res)
    assert "[3b] 등가성 검정" in text
    assert "p(TOST)" in text
    assert "90% CI" in text
    assert "equivalence was established" in text  # publication sentence

    ni = analyze([("a", A), ("b", B)],
                 equivalence=EquivalenceSpec(ni_margin=0.5,
                                             ni_direction="higher_is_better"))
    ni_text = render_text(ni)
    assert "[3b] 비열등성 검정" in ni_text
    assert "단측" in ni_text
    assert "non-inferiority was established" in ni_text


def test_json_contains_equivalence_block():
    res = analyze([("a", A), ("b", B)],
                  equivalence=EquivalenceSpec(margin=(-0.5, 0.5)))
    import json
    d = json.loads(render_json(res))
    eq = d["equivalence"]
    assert eq["kind"] == "tost"
    assert eq["concluded"] is True
    assert eq["conf"] == pytest.approx(0.90)
    assert math.isfinite(eq["pvalue"])
    assert eq["direction"] is None
