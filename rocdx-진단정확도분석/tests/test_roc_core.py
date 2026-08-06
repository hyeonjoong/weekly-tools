"""Core ROC maths: curve construction, AUC (incl. ties), metrics and intervals.

Reference values are either hand-computable 2x2 tables or brute-force
recomputations of the definition, so a bug in the fast path cannot hide behind
the same bug in the test.
"""

import math
import random

import pytest

from rocdx.roc import (
    Point,
    auc_from_scores,
    closest_topleft_point,
    curve_xy,
    metrics_at,
    point_at_min_sens,
    point_at_min_spec,
    point_for_cutoff,
    roc_points,
    youden_point,
)
from rocdx.stats_core import midranks, norm_ppf, wilson_ci


def brute_auc(scores, positive):
    """AUC straight from the definition: P(X>Y) + 0.5 P(X=Y)."""
    pos = [s for s, p in zip(scores, positive) if p]
    neg = [s for s, p in zip(scores, positive) if not p]
    total = 0.0
    for x in pos:
        for y in neg:
            total += 1.0 if x > y else (0.5 if x == y else 0.0)
    return total / (len(pos) * len(neg))


# --- AUC ---------------------------------------------------------------------

def test_auc_perfect_separation():
    scores = [1, 2, 3, 4, 5, 6]
    positive = [False, False, False, True, True, True]
    assert auc_from_scores(scores, positive) == pytest.approx(1.0)


def test_auc_perfectly_reversed_is_zero():
    scores = [6, 5, 4, 3, 2, 1]
    positive = [False, False, False, True, True, True]
    assert auc_from_scores(scores, positive) == pytest.approx(0.0)


def test_auc_all_tied_is_one_half():
    scores = [7] * 8
    positive = [True] * 3 + [False] * 5
    assert auc_from_scores(scores, positive) == pytest.approx(0.5)


def test_auc_matches_brute_force_with_heavy_ties():
    rng = random.Random(11)
    for _ in range(30):
        n = rng.randint(6, 60)
        # Deliberately few distinct values so ties dominate.
        scores = [rng.choice([0, 1, 1, 2, 3, 3, 3, 4]) for _ in range(n)]
        positive = [rng.random() < 0.4 for _ in range(n)]
        if not any(positive) or all(positive):
            continue
        assert auc_from_scores(scores, positive) == pytest.approx(
            brute_auc(scores, positive), abs=1e-12)


def test_auc_is_nan_when_a_group_is_empty():
    assert math.isnan(auc_from_scores([1, 2, 3], [True, True, True]))
    assert math.isnan(auc_from_scores([1, 2, 3], [False, False, False]))


def test_auc_equals_trapezoid_area_under_the_empirical_curve():
    rng = random.Random(5)
    scores = [round(rng.gauss(0, 1), 1) for _ in range(80)]
    positive = [rng.random() < 0.35 for _ in range(80)]
    xy = sorted(curve_xy(roc_points(scores, positive)))
    area = 0.0
    for (x0, y0), (x1, y1) in zip(xy, xy[1:]):
        area += (x1 - x0) * (y0 + y1) / 2.0
    assert area == pytest.approx(auc_from_scores(scores, positive), abs=1e-12)


# --- curve -------------------------------------------------------------------

def test_roc_points_endpoints_and_monotonicity():
    rng = random.Random(3)
    scores = [rng.random() for _ in range(50)]
    positive = [rng.random() < 0.5 for _ in range(50)]
    pts = roc_points(scores, positive)
    assert pts[0].tp == 0 and pts[0].fp == 0          # nobody called positive
    assert pts[-1].fn == 0 and pts[-1].tn == 0        # everybody called positive
    assert pts[0].sens == 0.0 and pts[0].spec == 1.0
    assert pts[-1].sens == 1.0 and pts[-1].spec == 0.0
    xs = [1 - p.spec for p in pts]
    ys = [p.sens for p in pts]
    assert xs == sorted(xs) and ys == sorted(ys)      # non-decreasing to the right
    for p in pts:
        assert p.tp + p.fp + p.fn + p.tn == 50


def test_tied_scores_share_a_single_operating_point():
    scores = [1, 1, 1, 2, 2, 3]
    positive = [True, False, True, False, True, False]
    pts = roc_points(scores, positive)
    # +inf plus one point per distinct value.
    assert len(pts) == 4
    assert [p.threshold for p in pts] == [float("inf"), 3, 2, 1]


def test_point_for_cutoff_matches_a_curve_point():
    rng = random.Random(7)
    scores = [round(rng.gauss(0, 1), 2) for _ in range(40)]
    positive = [rng.random() < 0.5 for _ in range(40)]
    pts = roc_points(scores, positive)
    for p in pts[1:]:
        direct = point_for_cutoff(scores, positive, p.threshold)
        assert (direct.tp, direct.fp, direct.fn, direct.tn) == (p.tp, p.fp, p.fn, p.tn)


def test_point_for_cutoff_boundary_is_inclusive():
    scores = [1.0, 2.0, 3.0]
    positive = [False, True, True]
    pt = point_for_cutoff(scores, positive, 2.0)
    assert (pt.tp, pt.fp, pt.fn, pt.tn) == (2, 0, 0, 1)


# --- point selection ---------------------------------------------------------

def test_youden_point_is_the_argmax_of_j():
    rng = random.Random(9)
    scores = [round(rng.gauss(0, 1), 1) for _ in range(60)]
    positive = [rng.random() < 0.4 for _ in range(60)]
    pts = roc_points(scores, positive)
    best = youden_point(pts)
    assert best.youden == pytest.approx(max(p.youden for p in pts))


def test_selection_is_deterministic_under_input_reordering():
    rng = random.Random(21)
    scores = [round(rng.gauss(0, 1), 1) for _ in range(70)]
    positive = [rng.random() < 0.45 for _ in range(70)]
    a = youden_point(roc_points(scores, positive))
    idx = list(range(70))
    rng.shuffle(idx)
    b = youden_point(roc_points([scores[i] for i in idx], [positive[i] for i in idx]))
    assert (a.threshold, a.tp, a.fp) == (b.threshold, b.tp, b.fp)


def test_min_spec_returns_the_most_sensitive_feasible_point():
    rng = random.Random(13)
    scores = [round(rng.gauss(0, 1), 1) for _ in range(80)]
    positive = [rng.random() < 0.4 for _ in range(80)]
    pts = roc_points(scores, positive)
    pt = point_at_min_spec(pts, 0.90)
    assert pt is not None and pt.spec >= 0.90 - 1e-12
    feasible = [p for p in pts if p.spec >= 0.90 - 1e-12]
    assert pt.sens == pytest.approx(max(p.sens for p in feasible))


def test_min_sens_returns_the_most_specific_feasible_point():
    rng = random.Random(17)
    scores = [round(rng.gauss(0, 1), 1) for _ in range(80)]
    positive = [rng.random() < 0.4 for _ in range(80)]
    pts = roc_points(scores, positive)
    pt = point_at_min_sens(pts, 0.95)
    assert pt is not None and pt.sens >= 0.95 - 1e-12
    feasible = [p for p in pts if p.sens >= 0.95 - 1e-12]
    assert pt.spec == pytest.approx(max(p.spec for p in feasible))


def test_min_spec_of_one_is_always_feasible_via_the_trivial_point():
    pts = roc_points([1, 2, 3, 4], [False, False, True, True])
    pt = point_at_min_spec(pts, 1.0)
    assert pt is not None and pt.spec == 1.0


def test_impossible_sensitivity_floor_returns_none():
    pts = roc_points([1, 2, 3, 4], [False, False, True, True])
    assert point_at_min_sens(pts, 1.01) is None


def test_topleft_point_minimises_distance_to_the_corner():
    rng = random.Random(29)
    scores = [round(rng.gauss(0, 1), 1) for _ in range(60)]
    positive = [rng.random() < 0.5 for _ in range(60)]
    pts = roc_points(scores, positive)
    pt = closest_topleft_point(pts)
    d = lambda p: math.hypot(1 - p.spec, 1 - p.sens)  # noqa: E731
    assert d(pt) == pytest.approx(min(d(p) for p in pts))


# --- metrics -----------------------------------------------------------------

def test_metrics_on_a_hand_computed_table():
    # TP 40, FP 10, FN 10, TN 140  → sens .80, spec .933...
    m = metrics_at(Point(1.0, tp=40, fp=10, fn=10, tn=140))
    assert m.sens == pytest.approx(0.8)
    assert m.spec == pytest.approx(140 / 150)
    assert m.ppv == pytest.approx(40 / 50)
    assert m.npv == pytest.approx(140 / 150)
    assert m.accuracy == pytest.approx(180 / 200)
    assert m.balanced_accuracy == pytest.approx((0.8 + 140 / 150) / 2)
    assert m.plr == pytest.approx(0.8 / (10 / 150))
    assert m.nlr == pytest.approx(0.2 / (140 / 150))
    assert m.dor == pytest.approx((40 * 140) / (10 * 10))
    assert m.prevalence_source == "sample"
    assert m.prevalence == pytest.approx(50 / 200)


def test_likelihood_ratio_ci_matches_simel_formula():
    m = metrics_at(Point(1.0, tp=40, fp=10, fn=10, tn=140))
    sens, spec, n_pos, n_neg = 0.8, 140 / 150, 50, 150
    se = math.sqrt((1 - sens) / (sens * n_pos) + spec / ((1 - spec) * n_neg))
    z = norm_ppf(0.975)
    lo = math.exp(math.log(m.plr) - z * se)
    hi = math.exp(math.log(m.plr) + z * se)
    assert m.plr_ci == pytest.approx((lo, hi))
    # The interval must contain the point estimate.
    assert lo < m.plr < hi


def test_dor_ci_uses_haldane_correction_for_an_empty_cell():
    m = metrics_at(Point(1.0, tp=10, fp=0, fn=2, tn=8))
    assert m.dor == pytest.approx((10.5 * 8.5) / (0.5 * 2.5))
    assert m.dor_ci is not None and m.dor_ci[0] < m.dor < m.dor_ci[1]


def test_prevalence_override_uses_bayes_and_drops_the_intervals():
    # sens .90 / spec .90 at 1% prevalence → PPV = .9*.01 / (.9*.01 + .1*.99)
    m = metrics_at(Point(1.0, tp=90, fp=10, fn=10, tn=90), prevalence=0.01)
    expected_ppv = 0.9 * 0.01 / (0.9 * 0.01 + 0.1 * 0.99)
    assert m.ppv == pytest.approx(expected_ppv)
    assert m.ppv < 0.09  # the classic screening surprise
    assert m.ppv_ci is None and m.npv_ci is None and m.accuracy_ci is None
    assert m.prevalence_source == "user"
    assert m.accuracy == pytest.approx(0.9 * 0.01 + 0.9 * 0.99)
    # PPV/NPV at the sample prevalence must reproduce the plain counts.
    m0 = metrics_at(Point(1.0, tp=90, fp=10, fn=10, tn=90))
    assert m0.ppv == pytest.approx(0.9)


def test_perfect_specificity_gives_infinite_lr_with_a_corrected_interval():
    """LR+ is infinite with zero false positives, but the interval is not lost.

    Simel's 0.5 correction is applied to the table so the reader still sees how
    uncertain that "infinite" LR really is — a 2x2 with 8/0/2/10 is not proof of
    a perfect rule-in test.
    """
    m = metrics_at(Point(1.0, tp=8, fp=0, fn=2, tn=10))
    assert math.isinf(m.plr)
    assert m.lr_ci_corrected is True
    assert m.plr_ci is not None
    lo, hi = m.plr_ci
    # 0.5-corrected table: sens = 8.5/11, spec = 10.5/11 → LR+ = 17.0
    assert lo == pytest.approx(1.112, abs=0.01)
    assert hi == pytest.approx(259.87, rel=0.01)
    assert lo < 5.0  # the data are compatible with a mediocre test


def test_zero_false_negatives_get_a_corrected_lr_negative_interval():
    m = metrics_at(Point(1.0, tp=10, fp=4, fn=0, tn=6))
    assert m.nlr == 0.0
    assert m.lr_ci_corrected is True and m.nlr_ci is not None
    assert m.nlr_ci[1] > 0.0   # an upper bound the reader can judge


def test_ordinary_table_uses_the_uncorrected_lr_interval():
    m = metrics_at(Point(1.0, tp=40, fp=10, fn=10, tn=140))
    assert m.lr_ci_corrected is False


def test_alpha_widens_the_intervals():
    narrow = metrics_at(Point(1.0, tp=40, fp=10, fn=10, tn=140), alpha=0.10)
    wide = metrics_at(Point(1.0, tp=40, fp=10, fn=10, tn=140), alpha=0.01)
    assert wide.sens_ci[0] < narrow.sens_ci[0]
    assert wide.sens_ci[1] > narrow.sens_ci[1]


# --- stats primitives --------------------------------------------------------

def test_wilson_ci_known_values():
    # Published Wilson interval for 0/10 and 10/10 at 95%.
    lo, hi = wilson_ci(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=5e-4)
    lo, hi = wilson_ci(10, 10)
    assert hi == 1.0 and lo == pytest.approx(0.7225, abs=5e-4)
    lo, hi = wilson_ci(5, 10)
    assert (lo, hi) == pytest.approx((0.2366, 0.7634), abs=5e-4)


def test_wilson_ci_never_leaves_the_unit_interval():
    for n in (1, 3, 7, 50):
        for k in range(n + 1):
            lo, hi = wilson_ci(k, n)
            assert 0.0 <= lo <= hi <= 1.0
            assert lo <= k / n <= hi


def test_norm_ppf_known_quantiles():
    assert norm_ppf(0.975) == pytest.approx(1.959963984540054, abs=1e-10)
    assert norm_ppf(0.995) == pytest.approx(2.575829303548901, abs=1e-10)
    assert norm_ppf(0.5) == pytest.approx(0.0, abs=1e-12)
    assert norm_ppf(1e-8) == pytest.approx(-5.612001244174, abs=1e-8)
    with pytest.raises(ValueError):
        norm_ppf(0.0)
    with pytest.raises(ValueError):
        norm_ppf(1.0)


def test_midranks_average_ties():
    assert midranks([10, 20, 20, 30]) == [1.0, 2.5, 2.5, 4.0]
    assert midranks([5, 5, 5]) == [2.0, 2.0, 2.0]
