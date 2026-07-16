"""Multiple-comparison p-value adjustment — pure standard library.

A Table 1 reports one p-value per variable, so testing many variables inflates
the family-wise / false-discovery error. When a comparative (non-randomized)
table wants adjusted p-values, these routines add an adjusted-p column.

Supported methods (matching ``statsmodels.stats.multitest.multipletests``):
    - ``bonferroni``  family-wise, p * m
    - ``holm``        family-wise, step-down Holm-Bonferroni
    - ``bh`` / ``fdr``  Benjamini-Hochberg FDR (step-up)
    - ``by``          Benjamini-Yekutieli FDR (BH under arbitrary dependence)

``None`` p-values (a variable whose test could not be computed) pass through
unchanged and are excluded from the family size ``m``.

Note: in a *randomized* trial CONSORT discourages baseline p-values altogether
(use SMD); multiplicity adjustment is meant for comparative/observational
tables. The caller is responsible for that editorial choice.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence

__all__ = ["METHODS", "adjust_pvalues", "normalize_method"]

# Canonical method name -> set of accepted aliases (all lower-case).
_ALIASES = {
    "bonferroni": {"bonferroni", "bonf"},
    "holm": {"holm", "holm-bonferroni"},
    "bh": {"bh", "fdr", "fdr_bh", "benjamini-hochberg"},
    "by": {"by", "fdr_by", "benjamini-yekutieli"},
}
METHODS = ["none", "bonferroni", "holm", "bh", "by"]


def normalize_method(name: Optional[str]) -> str:
    """Map a user-supplied method name (or alias) to a canonical key.

    Returns "none" for None/"none"/"" and raises ValueError on an unknown name.
    """
    if name is None:
        return "none"
    key = name.strip().lower()
    if key in ("", "none"):
        return "none"
    for canon, aliases in _ALIASES.items():
        if key in aliases:
            return canon
    raise ValueError(
        f"알 수 없는 다중비교 보정 방법: {name!r} "
        f"(사용 가능: {', '.join(METHODS)})")


def adjust_pvalues(pvals: Sequence[Optional[float]], method: str
                   ) -> List[Optional[float]]:
    """Return adjusted p-values aligned 1:1 with ``pvals``.

    ``None`` entries (untestable variables) stay ``None`` and do not count
    toward the family size ``m``. ``method`` must already be canonical
    (see :func:`normalize_method`); "none" returns the inputs unchanged.
    """
    method = normalize_method(method)
    out: List[Optional[float]] = list(pvals)
    if method == "none":
        return out

    # Indices of the real (testable) p-values, in input order.
    idx = [i for i, p in enumerate(pvals)
           if p is not None and not (isinstance(p, float) and math.isnan(p))]
    m = len(idx)
    if m == 0:
        return out
    ps = [float(pvals[i]) for i in idx]

    if method == "bonferroni":
        adj = [min(1.0, p * m) for p in ps]
        for i, a in zip(idx, adj):
            out[i] = a
        return out

    # Order statistics for the step methods.
    order = sorted(range(m), key=lambda k: ps[k])  # ascending p
    ordered = [ps[k] for k in order]
    adj_ordered = [0.0] * m

    if method == "holm":
        # Step-down: a_(i) = max_{j<=i} (m - j) * p_(j), then clamp/monotone.
        running = 0.0
        for i in range(m):
            val = (m - i) * ordered[i]
            running = max(running, val)          # enforce monotone non-decreasing
            adj_ordered[i] = min(1.0, running)
    else:  # bh or by (step-up)
        c = 1.0
        if method == "by":
            c = sum(1.0 / (k + 1) for k in range(m))  # harmonic number H_m
        # Walk from the largest p down, keeping the running minimum.
        running = 1.0
        for i in range(m - 1, -1, -1):
            rank = i + 1
            val = ordered[i] * m * c / rank
            running = min(running, val)           # enforce monotone non-decreasing
            adj_ordered[i] = min(1.0, running)

    # Scatter back to original positions.
    for pos, k in enumerate(order):
        out[idx[k]] = adj_ordered[pos]
    return out
