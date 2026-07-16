"""Paired / repeated-measures two-condition tests — pure standard library.

Clinical work is full of *within-subject* comparisons: pre vs post treatment,
baseline vs follow-up, left vs right, cross-over arms.  These must be analysed
as **paired** data (one value per subject per condition), not as two
independent groups.  This module provides the two standard choices:

* ``paired_t``            — paired-samples t-test on the differences.
* ``wilcoxon_signed_rank`` — Wilcoxon signed-rank test (exact for small,
                             tie-free samples; otherwise the tie-corrected
                             normal approximation with continuity correction,
                             matching ``scipy.stats.wilcoxon``).

Both operate on two equal-length, row-matched sequences ``a`` and ``b`` and
test whether the median/mean of ``a - b`` differs from zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from . import exact
from .special import norm_sf, t_sf_two_sided
from .tests_stat import _rankdata, _tie_term, mean, variance

__all__ = [
    "PairedTResult",
    "SignedRankResult",
    "paired_differences",
    "paired_t",
    "wilcoxon_signed_rank",
]


@dataclass
class PairedTResult:
    statistic: float
    df: float
    pvalue: float
    mean_diff: float
    sd_diff: float
    n: int


@dataclass
class SignedRankResult:
    statistic: float   # W = min(W+, W-)
    w_plus: float
    w_minus: float
    zscore: float
    pvalue: float
    method: str        # "exact" or "asymptotic"
    n_nonzero: int
    n_zero: int


def _check_paired(a: Sequence[float], b: Sequence[float]) -> None:
    if len(a) != len(b):
        raise ValueError(
            f"paired data must have equal length (got {len(a)} and {len(b)})")
    if len(a) < 2:
        raise ValueError("paired test needs at least 2 matched pairs")


def paired_differences(a: Sequence[float], b: Sequence[float]) -> List[float]:
    """Row-wise differences ``a[i] - b[i]``."""
    _check_paired(a, b)
    return [float(x) - float(y) for x, y in zip(a, b)]


def paired_t(a: Sequence[float], b: Sequence[float]) -> PairedTResult:
    """Paired-samples t-test (a one-sample t on the differences vs 0)."""
    d = paired_differences(a, b)
    n = len(d)
    md = mean(d)
    vd = variance(d)  # ddof=1
    sd = math.sqrt(vd)
    if sd == 0.0:
        # No within-pair variability: difference is a constant.
        if md == 0.0:
            t, p = float("nan"), float("nan")
        else:
            t, p = math.inf * (1 if md > 0 else -1), 0.0
        return PairedTResult(t, float(n - 1), p, md, sd, n)
    se = sd / math.sqrt(n)
    t = md / se
    df = n - 1
    return PairedTResult(t, float(df), t_sf_two_sided(t, df), md, sd, n)


def wilcoxon_signed_rank(a: Sequence[float], b: Sequence[float],
                         method: str = "auto",
                         correction: bool = True) -> SignedRankResult:
    """Wilcoxon signed-rank test on paired data.

    ``method='auto'`` uses the exact permutation distribution when there are no
    ties among the non-zero |differences| and the number of non-zero
    differences is small (<= ``exact.SIGNED_RANK_EXACT_MAX_N``); otherwise it
    uses the tie-corrected normal approximation.  Zero differences are dropped
    ("wilcox" handling), matching ``scipy.stats.wilcoxon`` defaults.
    """
    d = paired_differences(a, b)
    nz = [v for v in d if v != 0.0]
    n_zero = len(d) - len(nz)
    n = len(nz)
    if n == 0:
        # All pairs identical -> no evidence of a difference.
        return SignedRankResult(0.0, 0.0, 0.0, 0.0, 1.0, "exact", 0, n_zero)

    abs_vals = [abs(v) for v in nz]
    ranks = _rankdata(abs_vals)
    w_plus = sum(r for r, v in zip(ranks, nz) if v > 0)
    w_minus = sum(r for r, v in zip(ranks, nz) if v < 0)
    w = min(w_plus, w_minus)

    has_ties = _tie_term(abs_vals) > 0
    use_exact = (method == "exact" or
                 (method == "auto" and not has_ties
                  and n <= exact.SIGNED_RANK_EXACT_MAX_N))
    if method == "exact" and has_ties:
        # Exact distribution assumes distinct ranks; fall back rather than lie.
        use_exact = False

    if use_exact:
        p = exact.signed_rank_exact_p(w, n)
        # z reported for reference (not used for the p-value)
        mu = n * (n + 1) / 4.0
        sigma = math.sqrt(n * (n + 1) * (2 * n + 1) / 24.0)
        z = (w - mu) / sigma if sigma > 0 else 0.0
        return SignedRankResult(w, w_plus, w_minus, z, p, "exact", n, n_zero)

    # Asymptotic normal approximation with tie correction (matches scipy).
    mu = n * (n + 1) / 4.0
    tie = _tie_term(abs_vals)  # sum(t^3 - t)
    sigma2 = n * (n + 1) * (2 * n + 1) / 24.0 - tie / 48.0
    sigma = math.sqrt(sigma2) if sigma2 > 0 else 0.0
    if sigma == 0.0:
        return SignedRankResult(w, w_plus, w_minus, 0.0, 1.0, "asymptotic",
                                n, n_zero)
    d_cc = 0.5 if correction else 0.0
    # continuity correction shrinks |W - mu| toward mu; sign(W - mu) so the
    # exactly-balanced case (W+ == W-, w == mu) gets no correction -> z = 0,
    # p = 1 (matches scipy, which uses sign(T - mn) and sign(0) = 0).
    if w > mu:
        z = (w - mu - d_cc) / sigma
    elif w < mu:
        z = (w - mu + d_cc) / sigma
    else:
        z = 0.0
    p = min(1.0, 2.0 * norm_sf(abs(z)))
    return SignedRankResult(w, w_plus, w_minus, z, p, "asymptotic", n, n_zero)
