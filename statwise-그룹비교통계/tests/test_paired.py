"""Paired / repeated-measures tests validated against scipy + hand math."""

import math

import pytest

from statwise import paired
from statwise.analyze import analyze_paired


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_paired_t_handmath():
    a = [10.0, 12.0, 14.0, 16.0, 18.0]
    b = [8.0, 11.0, 12.0, 15.0, 16.0]
    r = paired.paired_t(a, b)
    # diffs = [2,1,2,1,2]; mean 1.6, sd = 0.5477225575
    assert approx(r.mean_diff, 1.6)
    assert approx(r.sd_diff, 0.5477225575051661)
    assert r.df == 4
    # scipy ttest_rel
    assert approx(r.statistic, 6.531972647421809, 1e-6)
    assert approx(r.pvalue, 0.0028378459267344473, 1e-9)


def test_paired_t_scipy_ref():
    pre = [120, 135, 128, 140, 132, 125, 138, 145, 130, 133]
    post = [115, 128, 124, 130, 127, 120, 131, 138, 126, 129]
    r = paired.paired_t(pre, post)
    assert approx(r.statistic, 9.492478225265666, 1e-6)
    assert approx(r.pvalue, 5.510598017964472e-06, 1e-10)


def test_paired_t_length_mismatch():
    with pytest.raises(ValueError):
        paired.paired_t([1.0, 2.0], [1.0])


def test_paired_t_zero_variance_diff():
    # constant non-zero difference -> infinite t
    r = paired.paired_t([3.0, 4.0, 5.0], [1.0, 2.0, 3.0])
    assert math.isinf(r.statistic)
    assert r.pvalue == 0.0


def test_wilcoxon_exact_all_positive():
    # all differences positive -> W=0; exact p (n=10) = 0.001953125
    pre = [120, 135, 128, 140, 132, 125, 138, 145, 130, 133]
    post = [115, 128, 124, 130, 127, 120, 131, 138, 126, 129]
    r = paired.wilcoxon_signed_rank(pre, post)
    assert r.method == "asymptotic"  # ties in |diffs| -> normal approx
    assert r.statistic == 0.0


def test_wilcoxon_exact_no_ties_matches_scipy():
    x = [5.0, 3.1, 8.0, 2.0, 9.3, 1.0, 7.4, 6.2]
    y = [4.0, 3.5, 6.1, 2.9, 7.5, 2.2, 5.0, 6.9]
    r = paired.wilcoxon_signed_rank(x, y)
    assert r.method == "exact"
    assert approx(r.statistic, 11.0, 1e-9)
    assert approx(r.pvalue, 0.3828125, 1e-9)


def test_wilcoxon_asymptotic_with_ties_matches_scipy():
    a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    b = [1.0, 3.0, 2.0, 6.0, 5.0, 9.0, 10.0, 7.0]
    r = paired.wilcoxon_signed_rank(a, b)
    assert r.method == "asymptotic"
    assert approx(r.pvalue, 0.20210204418937272, 1e-9)


def test_wilcoxon_drops_zero_differences():
    a = [1.0, 2.0, 3.0, 5.0, 6.0]
    b = [1.0, 1.0, 4.0, 3.0, 9.0]  # first pair identical -> dropped
    r = paired.wilcoxon_signed_rank(a, b)
    assert r.n_zero == 1
    assert r.n_nonzero == 4


def test_wilcoxon_all_zero():
    r = paired.wilcoxon_signed_rank([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert r.pvalue == 1.0
    assert r.n_nonzero == 0


def test_analyze_paired_selects_t_when_normal():
    a = [10.2, 12.5, 14.1, 16.8, 18.3, 11.4, 13.9, 15.2, 9.7, 17.1]
    b = [8.1, 11.3, 12.6, 15.0, 16.2, 9.9, 12.4, 13.1, 8.0, 15.5]
    res = analyze_paired(("a", a), ("b", b))
    assert res.paired is True
    assert res.test_name == "Paired t-test"
    assert res.effects[0].name == "Cohen's dz"
    assert res.mean_diff_ci[0] < res.mean_diff < res.mean_diff_ci[1]


def test_analyze_paired_selects_wilcoxon_when_skewed():
    a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 100.0]
    b = [1.1, 2.2, 2.9, 4.3, 4.8, 6.4, 6.6, 3.0]  # one huge diff -> non-normal
    res = analyze_paired(("a", a), ("b", b))
    assert res.test_name == "Wilcoxon signed-rank test"
    assert res.effects[0].name == "matched rank-biserial r"


def test_analyze_paired_length_mismatch():
    with pytest.raises(ValueError):
        analyze_paired(("a", [1.0, 2.0, 3.0]), ("b", [1.0, 2.0]))


def test_analyze_paired_needs_two_pairs():
    with pytest.raises(ValueError):
        analyze_paired(("a", [1.0]), ("b", [2.0]))


def test_wilcoxon_balanced_ranks_no_spurious_correction():
    # W+ == W- (perfectly balanced) with ties in |diff| -> asymptotic path.
    # Continuity correction must NOT fire: z = 0, p = 1.0 (matches scipy).
    a = [3, 4, -1, 3, -2, -3, -3, 2, -2, 5, -2, -3, -3]
    b = [0.0] * 13
    r = paired.wilcoxon_signed_rank(a, b)
    assert r.method == "asymptotic"
    assert approx(r.w_plus, r.w_minus)
    assert r.zscore == 0.0
    assert r.pvalue == pytest.approx(1.0)


def test_analyze_paired_rejects_non_finite():
    with pytest.raises(ValueError):
        analyze_paired(("a", [1.0, 2.0, float("inf")]), ("b", [1.0, 2.0, 3.0]))


def test_wilcoxon_auto_uses_asymptotic_over_size_cap():
    from statwise import exact
    n = exact.SIGNED_RANK_EXACT_MAX_N + 3
    a = [float(i) for i in range(n)]
    b = [float(i) - 0.3 for i in range(n)]  # all diffs +0.3, distinct |diff|? no, all equal
    # make distinct non-zero diffs with no ties in |diff|
    b = [a[i] - (i + 1) * 0.1 for i in range(n)]
    r = paired.wilcoxon_signed_rank(a, b)
    assert r.method == "asymptotic"
