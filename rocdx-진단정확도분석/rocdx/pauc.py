"""Partial AUC over a clinically relevant region of the ROC curve.

A screening test is only ever used where specificity is high enough to be
affordable, and a rule-out test only where sensitivity is high enough to be
safe. The full AUC averages discrimination over regions nobody would operate in,
so two markers with the same AUC can be very different where it matters. The
partial AUC (pAUC) integrates the empirical curve over one range only.

Two numbers are reported: the raw area (whose scale depends on the width of the
range, so it is not comparable across ranges) and McClish's standardised pAUC,
which rescales the raw area so that 0.5 is chance and 1.0 is perfect *within
that range*. That makes it comparable across different ranges and across
markers, but NOT to a full AUC: 0.83 over specificity 0.9-1.0 and a full AUC of
0.83 are different quantities. The scale is bounded above by 1 but not below —
a marker that is worse than chance inside a narrow window can standardise to a
large negative number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .roc import Point, curve_xy, roc_points

__all__ = ["PartialAuc", "partial_auc_from_points", "partial_auc", "spec_range_to_fpr"]


@dataclass(frozen=True)
class PartialAuc:
    """Partial area under the empirical ROC curve over one FPR range."""

    fpr_low: float
    fpr_high: float
    area: float             # raw integral of TPR dFPR over the range
    chance_area: float      # what the chance diagonal contributes over the range
    max_area: float         # what a perfect curve contributes (= range width)
    standardized: float     # McClish: 0.5 * (1 + (area - chance) / (max - chance))
    ci: Optional[Tuple[float, float]] = None          # bootstrap CI of `standardized`
    area_ci: Optional[Tuple[float, float]] = None     # bootstrap CI of `area`
    ci_source: str = ""     # "" | "bootstrap" | "cluster-bootstrap"
    n_boot: int = 0
    n_effective: int = 0
    # distinct false-positive rates the data actually reaches inside the region;
    # 0-2 means the value is essentially interpolation, not measurement
    n_observed_fprs: int = 0

    @property
    def spec_low(self) -> float:
        """Lowest specificity in the integrated range."""
        return 1.0 - self.fpr_high

    @property
    def spec_high(self) -> float:
        return 1.0 - self.fpr_low


def spec_range_to_fpr(min_spec: float, max_spec: float = 1.0) -> Tuple[float, float]:
    """Translate a specificity range into the (fpr_low, fpr_high) the maths uses."""
    if not (0.0 <= min_spec <= 1.0 and 0.0 <= max_spec <= 1.0):
        raise ValueError("specificity bounds must lie in [0, 1]")
    if min_spec >= max_spec:
        raise ValueError("min_spec must be strictly below max_spec")
    return (1.0 - max_spec, 1.0 - min_spec)


def _interp(x0: float, y0: float, x1: float, y1: float, x: float) -> float:
    if x1 == x0:
        return y1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def partial_auc_from_points(points: Sequence[Point], fpr_low: float,
                            fpr_high: float) -> PartialAuc:
    """Integrate the empirical ROC curve between two false-positive rates.

    The curve is the same step function ``roc_points`` produces, integrated with
    the trapezoid rule — so a tied score contributes its diagonal segment, and
    the full range [0, 1] reproduces the Mann-Whitney AUC exactly. Where the
    range boundary falls inside a segment the curve is interpolated linearly,
    which is the standard convention (Wilcoxon/trapezoidal pAUC) and keeps
    pAUC(0, t) + pAUC(t, 1) = AUC for every t.
    """
    if not (0.0 <= fpr_low < fpr_high <= 1.0):
        raise ValueError("need 0 <= fpr_low < fpr_high <= 1")
    xy = [(x, y) for x, y in curve_xy(points)
          if not (math.isnan(x) or math.isnan(y))]
    width = fpr_high - fpr_low
    chance = (fpr_high * fpr_high - fpr_low * fpr_low) / 2.0
    if len(xy) < 2:
        return PartialAuc(fpr_low, fpr_high, float("nan"), chance, width, float("nan"))
    xy.sort()
    area = 0.0
    for (x0, y0), (x1, y1) in zip(xy, xy[1:]):
        if x1 <= fpr_low or x0 >= fpr_high or x1 == x0:
            continue
        t0, t1 = max(x0, fpr_low), min(x1, fpr_high)
        area += (t1 - t0) * (_interp(x0, y0, x1, y1, t0)
                             + _interp(x0, y0, x1, y1, t1)) / 2.0
    denom = width - chance
    std = 0.5 * (1.0 + (area - chance) / denom) if denom > 0 else float("nan")
    return PartialAuc(fpr_low=fpr_low, fpr_high=fpr_high, area=area,
                      chance_area=chance, max_area=width, standardized=std)


def partial_auc(scores: Sequence[float], positive: Sequence[bool],
                fpr_low: float = 0.0, fpr_high: float = 1.0) -> PartialAuc:
    """Partial AUC straight from oriented scores and labels."""
    n_pos = sum(1 for p in positive if p)
    if n_pos == 0 or n_pos == len(positive):
        width = fpr_high - fpr_low
        chance = (fpr_high * fpr_high - fpr_low * fpr_low) / 2.0
        return PartialAuc(fpr_low, fpr_high, float("nan"), chance, width, float("nan"))
    return partial_auc_from_points(roc_points(scores, positive), fpr_low, fpr_high)
