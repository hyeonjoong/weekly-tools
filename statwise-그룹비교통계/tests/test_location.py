"""Hodges-Lehmann location estimate + distribution-free CI.

The point estimate is validated as the median of pairwise differences / Walsh
averages.  The CI is validated by test inversion: a distribution-free CI must
exclude 0 exactly when the corresponding exact rank test rejects at the same
alpha.
"""

import random
import statistics

import pytest

from statwise import exact, location
from statwise.tests_stat import _rankdata


def _u1(a, b):
    combined = list(a) + list(b)
    r = _rankdata(combined)
    return sum(r[:len(a)]) - len(a) * (len(a) + 1) / 2.0


def test_hl_independent_point_estimate_is_median_pairwise():
    a = [10, 12, 14, 16, 18]
    b = [8, 11, 12, 15, 16]
    le = location.hodges_lehmann_independent(a, b)
    pd = [x - y for x in a for y in b]
    assert le.estimate == pytest.approx(statistics.median(pd))


def test_hl_paired_point_estimate_is_median_walsh():
    d = [2.0, 1.0, 2.0, 1.0, 2.0, 3.0]
    le = location.hodges_lehmann_paired(d)
    walsh = [(d[i] + d[j]) / 2 for i in range(len(d)) for j in range(i, len(d))]
    assert le.estimate == pytest.approx(statistics.median(walsh))


def test_hl_independent_ci_brackets_estimate():
    a = [21, 24, 27, 30, 33, 36]
    b = [10, 13, 16, 19, 22, 25]
    le = location.hodges_lehmann_independent(a, b)
    assert le.ci_low <= le.estimate <= le.ci_high


def test_hl_independent_ci_excludes_zero_iff_test_rejects():
    # test-inversion consistency over many small exact cases
    random.seed(11)
    tested = 0
    for _ in range(80):
        n1 = random.randint(4, 8)
        n2 = random.randint(4, 8)
        vals = random.sample(range(1, 500), n1 + n2)
        a, b = vals[:n1], vals[n1:]
        le = location.hodges_lehmann_independent(a, b, conf=0.95)
        if le.ci_low is None:
            continue
        tested += 1
        p = exact.mannwhitney_exact_p(_u1(a, b), n1, n2)
        excludes_zero = le.ci_low > 0 or le.ci_high < 0
        assert excludes_zero == (p < 0.05), (a, b, p, le.ci_low, le.ci_high)
    assert tested > 30


def test_hl_paired_ci_excludes_zero_iff_test_rejects():
    random.seed(13)
    tested = 0
    for _ in range(150):
        n = random.randint(6, 14)
        d = [v + 0.5 for v in random.sample(range(-300, 300), n)]
        if len(set(abs(v) for v in d)) < n:   # need tie-free for exact
            continue
        le = location.hodges_lehmann_paired(d, conf=0.95)
        if le.ci_low is None:
            continue
        ranks = _rankdata([abs(v) for v in d])
        wp = sum(r for r, v in zip(ranks, d) if v > 0)
        wm = sum(r for r, v in zip(ranks, d) if v < 0)
        w = min(wp, wm)
        p = exact.signed_rank_exact_p(w, n)
        tested += 1
        excludes_zero = le.ci_low > 0 or le.ci_high < 0
        assert excludes_zero == (p < 0.05), (d, p, le.ci_low, le.ci_high)
    assert tested > 50


def test_hl_independent_complete_separation():
    # n=6 per group so a 95% CI exists (min two-sided exact p < 0.05)
    le = location.hodges_lehmann_independent([1, 2, 3, 4, 5, 6],
                                             [10, 11, 12, 13, 14, 15])
    assert le.estimate < 0
    assert le.ci_low is not None and le.ci_high < 0  # CI entirely below 0


def test_hl_integrated_in_mann_whitney_result():
    from statwise.analyze import analyze
    a = [1, 2, 3, 4, 5, 6, 7, 100]
    b = [2, 4, 6, 8, 10, 12, 14, 300]
    res = analyze([("a", a), ("b", b)])
    assert res.test_name == "Mann-Whitney U test"
    assert res.location is not None
    assert res.location.name.startswith("Hodges-Lehmann")


def test_hl_integrated_in_wilcoxon_result():
    from statwise.analyze import analyze_paired
    a = [1, 2, 3, 4, 5, 6, 7, 100]
    b = [1.1, 2.2, 2.9, 4.3, 4.8, 6.4, 6.6, 3.0]
    res = analyze_paired(("a", a), ("b", b))
    assert res.test_name == "Wilcoxon signed-rank test"
    assert res.location is not None
