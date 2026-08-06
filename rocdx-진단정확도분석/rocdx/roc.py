"""Empirical ROC curve, operating-point selection and accuracy metrics.

A "positive call" is always ``score >= threshold``. Markers where a *low* value
indicates disease are handled by negating the scores upstream (see
``cli.orient``), so this module never has to reason about direction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .stats_core import midranks, norm_ppf, wilson_ci

__all__ = [
    "Point",
    "Metrics",
    "roc_points",
    "auc_from_scores",
    "youden_point",
    "closest_topleft_point",
    "point_at_min_spec",
    "point_at_min_sens",
    "point_for_cutoff",
    "metrics_at",
    "curve_xy",
]

_INF = float("inf")


@dataclass(frozen=True)
class Point:
    """One operating point of the empirical ROC curve."""

    threshold: float  # call positive when score >= threshold
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def sens(self) -> float:
        n = self.tp + self.fn
        return self.tp / n if n else float("nan")

    @property
    def spec(self) -> float:
        n = self.tn + self.fp
        return self.tn / n if n else float("nan")

    @property
    def youden(self) -> float:
        return self.sens + self.spec - 1.0


@dataclass(frozen=True)
class Metrics:
    """Accuracy metrics at one operating point, with 95% intervals."""

    point: Point
    alpha: float
    sens: float
    sens_ci: Tuple[float, float]
    spec: float
    spec_ci: Tuple[float, float]
    ppv: float
    ppv_ci: Optional[Tuple[float, float]]
    npv: float
    npv_ci: Optional[Tuple[float, float]]
    accuracy: float
    accuracy_ci: Optional[Tuple[float, float]]
    balanced_accuracy: float
    plr: float
    plr_ci: Optional[Tuple[float, float]]
    nlr: float
    nlr_ci: Optional[Tuple[float, float]]
    dor: float
    dor_ci: Optional[Tuple[float, float]]
    prevalence: float
    prevalence_source: str  # "sample" or "user"
    lr_ci_corrected: bool = False  # LR interval came from a 0.5-corrected table


def roc_points(scores: Sequence[float], positive: Sequence[bool]) -> List[Point]:
    """All operating points of the empirical ROC curve, high threshold first.

    The first point (threshold ``+inf``) calls nobody positive; the last calls
    everybody positive. Tied scores share a single point, which is what keeps
    the curve honest about non-separable data.
    """
    n_pos = sum(1 for p in positive if p)
    n_neg = len(positive) - n_pos
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)

    pts = [Point(_INF, 0, 0, n_pos, n_neg)]
    tp = fp = 0
    i = 0
    while i < len(order):
        s = scores[order[i]]
        j = i
        while j < len(order) and scores[order[j]] == s:
            if positive[order[j]]:
                tp += 1
            else:
                fp += 1
            j += 1
        pts.append(Point(s, tp, fp, n_pos - tp, n_neg - fp))
        i = j
    return pts


def curve_xy(points: Sequence[Point]) -> List[Tuple[float, float]]:
    """(1 - specificity, sensitivity) pairs, i.e. the plottable ROC curve."""
    return [(1.0 - p.spec, p.sens) for p in points]


def auc_from_scores(scores: Sequence[float], positive: Sequence[bool]) -> float:
    """Area under the empirical ROC curve.

    Computed from mid-ranks (the Mann-Whitney form), which equals the
    trapezoidal area under the empirical curve *including* ties, where a tie
    contributes a diagonal segment worth half its rectangle.
    """
    n = len(scores)
    ranks = midranks(scores)
    pos_ranks = [ranks[i] for i in range(n) if positive[i]]
    m = len(pos_ranks)
    n_neg = n - m
    if m == 0 or n_neg == 0:
        return float("nan")
    return (math.fsum(pos_ranks) - m * (m + 1) / 2.0) / (m * n_neg)


def youden_point(points: Sequence[Point]) -> Point:
    """Operating point maximising Youden's J = sensitivity + specificity - 1.

    Ties are broken toward the higher sensitivity and then the higher
    threshold, so the choice is deterministic rather than input-order dependent.
    """
    return max(points, key=lambda p: (round(p.youden, 12), round(p.sens, 12), p.threshold))


def closest_topleft_point(points: Sequence[Point]) -> Point:
    """Operating point closest to the perfect corner (0, 1) in ROC space."""
    def dist(p: Point) -> float:
        return math.hypot(1.0 - p.spec, 1.0 - p.sens)

    return min(points, key=lambda p: (round(dist(p), 12), -round(p.sens, 12), -p.threshold))


def point_at_min_spec(points: Sequence[Point], min_spec: float) -> Optional[Point]:
    """Most sensitive point whose specificity is at least ``min_spec``."""
    cand = [p for p in points if p.spec >= min_spec - 1e-12]
    if not cand:
        return None
    return max(cand, key=lambda p: (round(p.sens, 12), round(p.spec, 12), p.threshold))


def point_at_min_sens(points: Sequence[Point], min_sens: float) -> Optional[Point]:
    """Most specific point whose sensitivity is at least ``min_sens``."""
    cand = [p for p in points if p.sens >= min_sens - 1e-12]
    if not cand:
        return None
    return max(cand, key=lambda p: (round(p.spec, 12), round(p.sens, 12), -p.threshold))


def point_for_cutoff(scores: Sequence[float], positive: Sequence[bool], cutoff: float) -> Point:
    """Operating point for a user-supplied cutoff (positive when score >= cutoff)."""
    tp = fp = fn = tn = 0
    for s, is_pos in zip(scores, positive):
        called = s >= cutoff
        if is_pos and called:
            tp += 1
        elif is_pos:
            fn += 1
        elif called:
            fp += 1
        else:
            tn += 1
    return Point(cutoff, tp, fp, fn, tn)


def _log_ratio_ci(ratio: float, se_log: float, alpha: float) -> Optional[Tuple[float, float]]:
    if not math.isfinite(ratio) or ratio <= 0 or not math.isfinite(se_log):
        return None
    z = norm_ppf(1.0 - alpha / 2.0)
    lo = math.exp(math.log(ratio) - z * se_log)
    hi = math.exp(math.log(ratio) + z * se_log)
    return (lo, hi)


def metrics_at(point: Point, alpha: float = 0.05,
               prevalence: Optional[float] = None) -> Metrics:
    """Accuracy metrics with confidence intervals at one operating point.

    Sensitivity/specificity/accuracy use Wilson score intervals. Likelihood
    ratios and the diagnostic odds ratio use the standard log-scale intervals
    (Simel 1991), with a Haldane 0.5 correction applied to the odds ratio when a
    cell is empty. When ``prevalence`` is given, PPV/NPV are recomputed by
    Bayes' rule for that population and reported without an interval, because
    the interval would no longer reflect this sample's uncertainty alone.
    """
    tp, fp, fn, tn = point.tp, point.fp, point.fn, point.tn
    n_pos, n_neg, n = tp + fn, tn + fp, tp + fp + fn + tn

    sens = tp / n_pos if n_pos else float("nan")
    spec = tn / n_neg if n_neg else float("nan")
    sens_ci = wilson_ci(tp, n_pos, alpha)
    spec_ci = wilson_ci(tn, n_neg, alpha)

    if prevalence is None:
        prev = n_pos / n if n else float("nan")
        prev_src = "sample"
        ppv = tp / (tp + fp) if (tp + fp) else float("nan")
        npv = tn / (tn + fn) if (tn + fn) else float("nan")
        ppv_ci = wilson_ci(tp, tp + fp, alpha) if (tp + fp) else None
        npv_ci = wilson_ci(tn, tn + fn, alpha) if (tn + fn) else None
        accuracy = (tp + tn) / n if n else float("nan")
        accuracy_ci = wilson_ci(tp + tn, n, alpha) if n else None
    else:
        prev = prevalence
        prev_src = "user"
        num_p = sens * prev
        den_p = num_p + (1.0 - spec) * (1.0 - prev)
        ppv = num_p / den_p if den_p > 0 else float("nan")
        num_n = spec * (1.0 - prev)
        den_n = num_n + (1.0 - sens) * prev
        npv = num_n / den_n if den_n > 0 else float("nan")
        ppv_ci = npv_ci = None
        accuracy = sens * prev + spec * (1.0 - prev)
        accuracy_ci = None

    balanced = (sens + spec) / 2.0

    # Likelihood ratios: Simel's log-scale intervals. A perfect cell (sens or
    # spec exactly 0 or 1) makes the uncorrected SE undefined, which is exactly
    # when a naive reader would take "LR- = 0.00" at face value; Simel (1991)
    # prescribes the same 0.5 correction used for the odds ratio, so the
    # interval is computed from the corrected table and flagged as such.
    # 0/0 at the two trivial corners is undefined, not infinite: with nobody
    # called positive LR+ is 0/0, and with everybody called positive LR- is 0/0.
    # Printing "inf" there would read as overwhelming evidence.
    if spec < 1.0:
        plr = sens / (1.0 - spec)
    else:
        plr = float("inf") if sens > 0.0 else float("nan")
    if spec > 0.0:
        nlr = (1.0 - sens) / spec
    else:
        nlr = float("inf") if sens < 1.0 else float("nan")
    plr_ci = nlr_ci = None
    lr_ci_corrected = False
    if n_pos and n_neg:
        if 0 < sens < 1 and 0 < spec < 1:
            se_plr = math.sqrt((1 - sens) / (sens * n_pos) + spec / ((1 - spec) * n_neg))
            se_nlr = math.sqrt(sens / ((1 - sens) * n_pos) + (1 - spec) / (spec * n_neg))
            plr_ci = _log_ratio_ci(plr, se_plr, alpha)
            nlr_ci = _log_ratio_ci(nlr, se_nlr, alpha)
        else:
            ca, cb, cc, cd = tp + 0.5, fp + 0.5, fn + 0.5, tn + 0.5
            np_c, nn_c = ca + cc, cb + cd
            sens_c, spec_c = ca / np_c, cd / nn_c
            plr_c = sens_c / (1.0 - spec_c)
            nlr_c = (1.0 - sens_c) / spec_c
            se_plr = math.sqrt((1 - sens_c) / (sens_c * np_c) + spec_c / ((1 - spec_c) * nn_c))
            se_nlr = math.sqrt(sens_c / ((1 - sens_c) * np_c) + (1 - spec_c) / (spec_c * nn_c))
            plr_ci = _log_ratio_ci(plr_c, se_plr, alpha)
            nlr_ci = _log_ratio_ci(nlr_c, se_nlr, alpha)
            lr_ci_corrected = True

    # Diagnostic odds ratio with Haldane correction for empty cells.
    a, b, c, d = tp, fp, fn, tn
    if min(a, b, c, d) == 0:
        a, b, c, d = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    dor = (a * d) / (b * c) if b * c else float("inf")
    dor_ci = None
    if b * c:
        se_dor = math.sqrt(1 / a + 1 / b + 1 / c + 1 / d)
        dor_ci = _log_ratio_ci(dor, se_dor, alpha)

    return Metrics(
        point=point, alpha=alpha,
        sens=sens, sens_ci=sens_ci, spec=spec, spec_ci=spec_ci,
        ppv=ppv, ppv_ci=ppv_ci, npv=npv, npv_ci=npv_ci,
        accuracy=accuracy, accuracy_ci=accuracy_ci, balanced_accuracy=balanced,
        plr=plr, plr_ci=plr_ci, nlr=nlr, nlr_ci=nlr_ci, dor=dor, dor_ci=dor_ci,
        prevalence=prev, prevalence_source=prev_src,
        lr_ci_corrected=lr_ci_corrected,
    )
