"""DeLong variance, intervals and AUC comparison.

The fast mid-rank implementation is checked against the O(n^2) definition it
replaces, and against a Monte-Carlo sampling distribution, so the test would
catch both an algebra slip and a wrong estimator.
"""

import math
import random
import statistics

import pytest

from rocdx.delong import (
    auc_ci,
    compare_paired,
    compare_unpaired,
    delong_variance,
    estimate_auc,
    mann_whitney_p,
    placements,
)


def brute_placements(pos, neg):
    """V10 / V01 straight from the definition."""
    psi = lambda x, y: 1.0 if x > y else (0.5 if x == y else 0.0)  # noqa: E731
    v10 = [sum(psi(x, y) for y in neg) / len(neg) for x in pos]
    v01 = [sum(psi(x, y) for x in pos) / len(pos) for y in neg]
    return v10, v01


def brute_variance(pos, neg):
    v10, v01 = brute_placements(pos, neg)
    return (statistics.variance(v10) / len(pos) + statistics.variance(v01) / len(neg))


def test_placements_match_the_definition_including_ties():
    rng = random.Random(2)
    for _ in range(20):
        pos = [rng.choice([0, 1, 1, 2, 3, 3]) for _ in range(rng.randint(4, 25))]
        neg = [rng.choice([0, 1, 2, 2, 3]) for _ in range(rng.randint(4, 25))]
        pl = placements(pos, neg)
        v10, v01 = brute_placements(pos, neg)
        assert pl.v10 == pytest.approx(v10, abs=1e-12)
        assert pl.v01 == pytest.approx(v01, abs=1e-12)
        # Both placement means equal the AUC.
        assert statistics.fmean(pl.v10) == pytest.approx(pl.auc, abs=1e-12)
        assert statistics.fmean(pl.v01) == pytest.approx(pl.auc, abs=1e-12)


def test_delong_variance_matches_the_definition():
    rng = random.Random(4)
    for _ in range(20):
        pos = [rng.gauss(1, 1) for _ in range(rng.randint(5, 40))]
        neg = [rng.gauss(0, 1) for _ in range(rng.randint(5, 40))]
        assert delong_variance(placements(pos, neg)) == pytest.approx(
            brute_variance(pos, neg), rel=1e-12)


def test_delong_se_tracks_the_monte_carlo_sampling_sd():
    """The estimated SE should be close to the true sampling SD of the AUC."""
    rng = random.Random(101)
    aucs = []
    for _ in range(400):
        pos = [rng.gauss(1.0, 1.0) for _ in range(40)]
        neg = [rng.gauss(0.0, 1.0) for _ in range(60)]
        aucs.append(placements(pos, neg).auc)
    empirical_sd = statistics.stdev(aucs)
    rng2 = random.Random(202)
    ses = []
    for _ in range(50):
        pos = [rng2.gauss(1.0, 1.0) for _ in range(40)]
        neg = [rng2.gauss(0.0, 1.0) for _ in range(60)]
        ses.append(estimate_auc(pos, neg).se)
    assert statistics.fmean(ses) == pytest.approx(empirical_sd, rel=0.15)


def test_perfect_separation_has_zero_variance_and_no_interval():
    est = estimate_auc([5, 6, 7, 8], [1, 2, 3, 4])
    assert est.auc == pytest.approx(1.0)
    assert est.se == 0.0
    assert est.ci is None


def test_auc_ci_logit_stays_inside_the_unit_interval():
    lo, hi = auc_ci(0.97, 0.02, 0.05, "logit")
    assert 0.0 < lo < 0.97 < hi < 1.0
    # Wald would have crossed 1 here.
    wlo, whi = auc_ci(0.97, 0.02, 0.05, "wald")
    assert whi == 1.0
    assert hi < 1.0


def test_auc_ci_wald_is_symmetric_when_far_from_the_boundary():
    lo, hi = auc_ci(0.70, 0.05, 0.05, "wald")
    assert (lo + hi) / 2 == pytest.approx(0.70)
    assert hi - lo == pytest.approx(2 * 1.959963984540054 * 0.05, rel=1e-9)


def test_auc_ci_rejects_unknown_method():
    with pytest.raises(ValueError):
        auc_ci(0.7, 0.05, 0.05, "bootstrap")


def test_mann_whitney_p_matches_a_known_result():
    # Complete separation of 5 vs 5: U = 0, mu = 12.5, sigma = sqrt(5*5*11/12).
    # z = (12.5 - 0.5) / 4.78714 = 2.50673 → two-sided p = 0.012186.
    # (scipy.stats.mannwhitneyu(..., method="asymptotic") gives the same value;
    #  the *exact* permutation p is 2/252 = 0.0079, i.e. the normal approximation
    #  is conservative here — which is why the report labels its own method.)
    p = mann_whitney_p([6, 7, 8, 9, 10], [1, 2, 3, 4, 5])
    assert p == pytest.approx(0.012186, abs=1e-6)


def test_mann_whitney_p_is_one_when_groups_are_identical():
    p = mann_whitney_p([1, 2, 3, 4], [1, 2, 3, 4])
    assert p == pytest.approx(1.0)


def test_mann_whitney_p_undefined_when_every_score_is_the_same():
    assert mann_whitney_p([3, 3, 3], [3, 3, 3]) is None


def test_paired_comparison_of_identical_markers_is_a_null_result():
    rng = random.Random(6)
    pos = [rng.gauss(1, 1) for _ in range(30)]
    neg = [rng.gauss(0, 1) for _ in range(30)]
    cmp_ = compare_paired(pos, neg, pos, neg)
    assert cmp_.diff == pytest.approx(0.0)
    assert cmp_.se_diff == pytest.approx(0.0, abs=1e-12)
    assert cmp_.p_value == 1.0


def test_paired_comparison_detects_a_real_difference():
    rng = random.Random(8)
    # marker A separates the groups, marker B is pure noise on the same subjects.
    pos_a = [rng.gauss(2.0, 1.0) for _ in range(60)]
    neg_a = [rng.gauss(0.0, 1.0) for _ in range(60)]
    pos_b = [rng.gauss(0.0, 1.0) for _ in range(60)]
    neg_b = [rng.gauss(0.0, 1.0) for _ in range(60)]
    cmp_ = compare_paired(pos_a, neg_a, pos_b, neg_b, "A", "B")
    assert cmp_.auc_a > 0.85 > cmp_.auc_b
    assert cmp_.p_value < 0.001
    assert cmp_.ci is not None and cmp_.ci[0] > 0
    assert cmp_.paired is True


def test_correlated_markers_get_a_smaller_se_than_the_unpaired_test():
    """Pairing is what buys power — the paired SE must be the smaller one."""
    rng = random.Random(12)
    pos_a, neg_a, pos_b, neg_b = [], [], [], []
    for _ in range(60):
        u = rng.gauss(1.2, 1.0)
        pos_a.append(u)
        pos_b.append(u + rng.gauss(0.1, 0.25))  # highly correlated with A
    for _ in range(60):
        u = rng.gauss(0.0, 1.0)
        neg_a.append(u)
        neg_b.append(u + rng.gauss(0.0, 0.25))
    paired = compare_paired(pos_a, neg_a, pos_b, neg_b)
    unpaired = compare_unpaired(pos_a, neg_a, pos_b, neg_b)
    assert paired.diff == pytest.approx(unpaired.diff)
    assert paired.se_diff < unpaired.se_diff
    assert unpaired.paired is False


def test_paired_comparison_requires_matching_sample_sizes():
    with pytest.raises(ValueError):
        compare_paired([1, 2, 3], [0, 1], [1, 2], [0, 1])


def test_placements_requires_both_groups():
    with pytest.raises(ValueError):
        placements([], [1, 2, 3])
    with pytest.raises(ValueError):
        placements([1, 2, 3], [])


def test_delong_ci_coverage_is_close_to_nominal():
    """Monte-Carlo check that the 95% interval covers the true AUC ~95% of the time."""
    true_auc = 0.5 * math.erfc(-1.0 / 2.0)  # P(X>Y) for N(1,1) vs N(0,1)
    rng = random.Random(77)
    covered = 0
    trials = 300
    for _ in range(trials):
        pos = [rng.gauss(1.0, 1.0) for _ in range(50)]
        neg = [rng.gauss(0.0, 1.0) for _ in range(50)]
        est = estimate_auc(pos, neg)
        if est.ci and est.ci[0] <= true_auc <= est.ci[1]:
            covered += 1
    assert 0.88 <= covered / trials <= 1.0
