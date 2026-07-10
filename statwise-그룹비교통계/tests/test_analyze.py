"""End-to-end orchestration and decision-logic tests."""

import pytest

from statwise.analyze import Group, _holm_adjust, _quantile
from statwise.analyze import analyze as run


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
    assert any("cannot test normality" in w for w in res.warnings)


def test_needs_two_groups():
    with pytest.raises(ValueError):
        run([("only", [1.0, 2.0, 3.0])])


def test_group_too_small_rejected():
    with pytest.raises(ValueError):
        run([("a", [1.0]), ("b", [2.0, 3.0])])
