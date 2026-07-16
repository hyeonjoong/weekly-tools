"""Standardized mean differences (SMD) for two-group balance assessment.

The SMD is the standard covariate-balance metric reported alongside a Table 1
(a common rule of thumb flags |SMD| > 0.1 as a meaningful imbalance). All
routines are pure standard library.

- Continuous:  d = (m1 - m2) / sqrt((s1^2 + s2^2) / 2)
- Binary:      d = (p1 - p2) / sqrt((p1(1-p1) + p2(1-p2)) / 2)
- Categorical (k levels): the multivariate SMD of Yang & Dalton (2012),
  d = sqrt( (P1 - P2)^T  S^{-1}  (P1 - P2) ), using the first k-1 level
  proportions and S = (S1 + S2) / 2, the averaged multinomial covariance.
  For k = 2 this reduces exactly to the binary formula above.

Reference:
    Yang D., Dalton J.E. (2012). "A unified approach to measuring the effect
    size between two groups using SAS." SAS Global Forum, Paper 335-2012.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence

__all__ = ["continuous_smd", "categorical_smd", "MAX_SMD_LEVELS"]

# The multivariate SMD inverts a (k-1)x(k-1) matrix, which is O(k^3) in the
# number of category levels. Beyond a few dozen levels the value is both
# uninterpretable as a balance metric and slow enough to hang (k=800 ~ 80 s),
# so we decline to compute it. Reachable only via --categorical / --max-levels
# on a high-cardinality column; the chi-square test on the same table is fine.
MAX_SMD_LEVELS = 50


def continuous_smd(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Absolute SMD for a continuous variable across two groups.

    Returns None if either group has < 2 observations. If the pooled spread is
    zero (both groups constant) the SMD is 0 when the means coincide, else inf.
    """
    if len(a) < 2 or len(b) < 2:
        return None
    m1 = sum(a) / len(a)
    m2 = sum(b) / len(b)
    v1 = sum((x - m1) ** 2 for x in a) / (len(a) - 1)
    v2 = sum((x - m2) ** 2 for x in b) / (len(b) - 1)
    denom = math.sqrt((v1 + v2) / 2.0)
    if denom == 0.0:
        return 0.0 if m1 == m2 else float("inf")
    return abs(m1 - m2) / denom


def _invert(matrix: List[List[float]]) -> Optional[List[List[float]]]:
    """Invert a small square matrix via Gauss-Jordan; None if singular."""
    n = len(matrix)
    # Augment with the identity.
    aug = [list(row) + [1.0 if i == j else 0.0 for j in range(n)]
           for i, row in enumerate(matrix)]
    for col in range(n):
        # Partial pivot for numerical stability.
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            return None
        aug[col], aug[pivot] = aug[pivot], aug[col]
        piv = aug[col][col]
        aug[col] = [v / piv for v in aug[col]]
        for r in range(n):
            if r != col and aug[r][col] != 0.0:
                factor = aug[r][col]
                aug[r] = [v - factor * aug[col][k] for k, v in enumerate(aug[r])]
    return [row[n:] for row in aug]


def categorical_smd(counts1: Sequence[int], counts2: Sequence[int]
                    ) -> Optional[float]:
    """Multivariate (Yang & Dalton) SMD between two groups' level counts.

    ``counts1``/``counts2`` are per-level counts in a common level order.
    Returns None if either group is empty or the variable has < 2 levels, or
    if the averaged covariance is singular (perfectly separated / degenerate).
    """
    if len(counts1) != len(counts2):
        raise ValueError("count vectors must share the same levels")
    k = len(counts1)
    n1 = sum(counts1)
    n2 = sum(counts2)
    if k < 2 or n1 == 0 or n2 == 0:
        return None
    if k > MAX_SMD_LEVELS:
        # Too many levels for a meaningful (or fast) multivariate SMD.
        return None
    p1 = [c / n1 for c in counts1]
    p2 = [c / n2 for c in counts2]

    if k == 2:  # closed-form binary case (also avoids a 1x1 inverse)
        denom = math.sqrt((p1[0] * (1 - p1[0]) + p2[0] * (1 - p2[0])) / 2.0)
        if denom == 0.0:
            return 0.0 if p1[0] == p2[0] else float("inf")
        return abs(p1[0] - p2[0]) / denom

    # Drop the last (redundant) level; work in k-1 dimensions.
    d = k - 1
    diff = [p1[i] - p2[i] for i in range(d)]

    def cov(p: Sequence[float]) -> List[List[float]]:
        return [[(p[i] * (1 - p[i]) if i == j else -p[i] * p[j])
                 for j in range(d)] for i in range(d)]

    s1 = cov(p1)
    s2 = cov(p2)
    s = [[(s1[i][j] + s2[i][j]) / 2.0 for j in range(d)] for i in range(d)]
    inv = _invert(s)
    if inv is None:
        return None
    quad = 0.0
    for i in range(d):
        for j in range(d):
            quad += diff[i] * inv[i][j] * diff[j]
    if quad < 0:  # tiny negative from round-off
        quad = 0.0
    return math.sqrt(quad)
