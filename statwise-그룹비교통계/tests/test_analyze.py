"""End-to-end orchestration and decision-logic tests."""

import pytest

from statwise.analyze import Group, _bh_adjust, _holm_adjust, _quantile
from statwise.analyze import analyze as run
from statwise.analyze import analyze_paired


def test_group_descriptives():
    g = Group("x", [1.0, 2.0, 3.0, 4.0])
    assert g.n == 4
    assert g.mean == 2.5
    assert g.median == 2.5
    q1, q3 = g.quartiles()
    assert q1 == pytest.approx(1.75)
    assert q3 == pytest.approx(3.25)


def test_quantile_linear():
    s = [1, 2, 3, 4, 5]
    assert _quantile(s, 0.0) == 1
    assert _quantile(s, 0.5) == 3
    assert _quantile(s, 1.0) == 5
    assert _quantile(s, 0.25) == pytest.approx(2.0)


def test_holm_adjust_monotone():
    raw = [0.01, 0.04, 0.03]
    adj = _holm_adjust(raw)
    # smallest*3, then step-down enforcing monotonicity
    assert adj[0] == pytest.approx(0.03)   # 0.01*3
    assert adj[2] == pytest.approx(0.06)   # 0.03*2
    assert adj[1] == pytest.approx(0.06)   # 0.04*1=0.04 but >= previous 0.06
    assert all(0 <= x <= 1 for x in adj)


def test_two_group_selects_student_when_normal_equalvar():
    # symmetric, equal-variance normalish groups
    a = [10.5, 12.1, 11.3, 13.0, 9.8, 11.7, 10.9, 12.4, 11.1, 10.2]
    b = [13.1, 14.5, 12.8, 15.0, 11.9, 13.6, 14.1, 12.7, 13.9, 14.3]
    res = run([("a", a), ("b", b)])
    assert res.test_name == "Student's t-test"
    assert res.significant
    assert res.mean_diff is not None
    assert res.mean_diff_ci[0] < res.mean_diff < res.mean_diff_ci[1]


def test_two_group_selects_mannwhitney_when_skewed():
    a = [1, 1, 1, 2, 2, 2, 3, 3, 100]      # heavy right skew
    b = [2, 3, 3, 4, 4, 5, 6, 7, 200]
    res = run([("a", a), ("b", b)])
    assert res.test_name == "Mann-Whitney U test"
    assert any(e.name == "rank-biserial r" for e in res.effects)


def test_three_group_anova_and_posthoc():
    g1 = [3, 5, 4, 6, 2, 5, 4, 3, 7, 5, 4, 6]
    g2 = [6, 8, 7, 9, 5, 8, 7, 6, 10, 8, 7, 9]
    g3 = [9, 11, 10, 12, 8, 11, 10, 9, 13, 11, 10, 12]
    res = run([("low", g1), ("mid", g2), ("high", g3)])
    assert res.test_name == "One-way ANOVA"
    assert res.significant
    assert len(res.pairwise) == 3
    assert all(pw.pvalue_adj >= pw.pvalue_raw for pw in res.pairwise)
    # ANOVA post-hoc uses pairwise Student's t (consistent with equal-variance)
    assert all(pw.test == "Student's t" for pw in res.pairwise)


def test_report_uses_alpha_norm_for_levene_verdict():
    # alpha != alpha_norm must not make the report's Levene verdict contradict
    # the test that was actually selected.
    from statwise.report import render_text
    a = [10.5, 12.1, 11.3, 13.0, 9.8, 11.7, 10.9, 12.4, 11.1, 10.2]
    b = [13.1, 14.5, 12.8, 15.0, 11.9, 13.6, 14.1, 12.7, 13.9, 14.3]
    res = run([("a", a), ("b", b)], alpha=0.01, alpha_norm=0.10)
    assert res.alpha_norm == 0.10
    txt = render_text(res)
    # verdict line must agree with the stored decision boundary (alpha_norm)
    equal_var = res.levene.pvalue > res.alpha_norm
    assert ("등분산 가정 충족" in txt) == equal_var


def test_posthoc_disabled():
    g1 = [3, 5, 4, 6, 2, 5, 4, 3, 7, 5, 4, 6]
    g2 = [6, 8, 7, 9, 5, 8, 7, 6, 10, 8, 7, 9]
    g3 = [9, 11, 10, 12, 8, 11, 10, 9, 13, 11, 10, 12]
    res = run([("a", g1), ("b", g2), ("c", g3)], posthoc=False)
    assert res.pairwise == []


def test_small_group_normality_skipped():
    res = run([("a", [1.0, 5.0]), ("b", [2.0, 9.0])])
    # n<3 -> normality can't be tested; falls to Mann-Whitney
    assert res.test_name == "Mann-Whitney U test"
    assert any("normality unknown" in w for w in res.warnings)


def test_bh_adjust_reference():
    # statsmodels multipletests fdr_bh reference
    adj = _bh_adjust([0.01, 0.04, 0.03, 0.20])
    assert adj[0] == pytest.approx(0.04)
    assert adj[1] == pytest.approx(0.05333333333333334)
    assert adj[2] == pytest.approx(0.05333333333333334)
    assert adj[3] == pytest.approx(0.20)
    assert all(0 <= x <= 1 for x in adj)


def test_bh_is_not_more_conservative_than_holm():
    raw = [0.001, 0.5, 0.02]
    holm = _holm_adjust(raw)
    bh = _bh_adjust(raw)
    assert all(b <= h + 1e-12 for b, h in zip(bh, holm))


def test_three_group_welch_anova_when_hetero_normal():
    # normal-ish groups, one with much larger variance -> Welch's ANOVA
    g1 = [11.691, 9.534, 10.033, 10.408, 9.211, 10.002, 9.999, 8.245,
          11.018, 10.6, 9.375, 9.828, 10.505, 9.739, 9.757]
    g2 = [10.547, 12.555, 12.124, 12.274, 10.473, 13.651, 12.154, 11.613,
          14.029, 11.955, 10.549, 11.595, 9.712, 13.049, 11.584]
    g3 = [10.287, 19.362, 5.745, 16.677, 3.678, 10.689, 7.979, 21.31,
          22.831, 12.353, 18.204, 13.1, 16.84, 10.236, 5.458]
    res = run([("A", g1), ("B", g2), ("C", g3)])
    assert res.test_name == "Welch's ANOVA"
    assert res.df2 != int(res.df2)  # fractional Welch-Satterthwaite df
    # post-hoc uses pairwise Welch's t (consistent with the omnibus)
    assert all(pw.test == "Welch's t" for pw in res.pairwise)
    # the pooled-SS eta-squared caveat must be surfaced honestly
    assert any("η²" in w or "eta" in w.lower() for w in res.warnings)


def test_paired_direction_reflects_condition_order():
    # diff = A - B; swapping the tuple order flips the sign of mean_diff/effect
    a = [10.2, 12.5, 14.1, 16.8, 18.3, 11.4, 13.9, 15.2, 9.7, 17.1]
    b = [8.1, 11.3, 12.6, 15.0, 16.2, 9.9, 12.4, 13.1, 8.0, 15.5]
    fwd = analyze_paired(("post", a), ("pre", b))
    rev = analyze_paired(("pre", b), ("post", a))
    assert fwd.mean_diff == pytest.approx(-rev.mean_diff)
    assert fwd.effects[0].value == pytest.approx(-rev.effects[0].value)


def test_correction_bh_option_used():
    g1 = [3, 5, 4, 6, 2, 5, 4, 3, 7, 5, 4, 6]
    g2 = [6, 8, 7, 9, 5, 8, 7, 6, 10, 8, 7, 9]
    g3 = [9, 11, 10, 12, 8, 11, 10, 9, 13, 11, 10, 12]
    res = run([("low", g1), ("mid", g2), ("high", g3)], correction="bh")
    assert res.correction == "bh"
    holm = run([("low", g1), ("mid", g2), ("high", g3)], correction="holm")
    # BH adjusted p-values are <= Holm adjusted (less conservative)
    for pb, ph in zip(res.pairwise, holm.pairwise):
        assert pb.pvalue_adj <= ph.pvalue_adj + 1e-12


def test_invalid_correction_rejected():
    with pytest.raises(ValueError):
        run([("a", [1.0, 2.0, 3.0]), ("b", [4.0, 5.0, 6.0])], correction="xyz")


def test_zero_variance_group_does_not_pick_equal_var_anova():
    # a constant group makes Levene undefined; must not select equal-var ANOVA
    g1 = [5.0, 5.0, 5.0, 5.0]
    g2 = [1.0, 2.0, 3.0, 4.0]
    g3 = [10.0, 11.0, 12.0, 13.0]
    res = run([("a", g1), ("b", g2), ("c", g3)])
    assert res.test_name in ("Welch's ANOVA", "Kruskal-Wallis H test")


def test_analyze_rejects_non_finite_values():
    with pytest.raises(ValueError):
        run([("a", [1.0, 2.0, float("nan")]), ("b", [3.0, 4.0, 5.0])])
    with pytest.raises(ValueError):
        run([("a", [1.0, 2.0, float("inf")]), ("b", [3.0, 4.0, 5.0])])


def test_two_constant_groups_small_n_no_crash():
    # zero-variance groups, small n -> Shapiro can't run -> Mann-Whitney, no crash
    res = run([("a", [5.0, 5.0, 5.0]), ("b", [9.0, 9.0, 9.0])])
    assert res.test_name == "Mann-Whitney U test"


def test_two_constant_groups_large_n_clean_error():
    # >5000 constant rows each: normality assumed, Levene NaN -> Welch t on zero
    # variance. Must raise a clean ValueError, not ZeroDivisionError.
    a = [5.0] * 5001
    b = [9.0] * 5001
    with pytest.raises(ValueError):
        run([("a", a), ("b", b)])


def test_needs_two_groups():
    with pytest.raises(ValueError):
        run([("only", [1.0, 2.0, 3.0])])


def test_group_too_small_rejected():
    with pytest.raises(ValueError):
        run([("a", [1.0]), ("b", [2.0, 3.0])])
