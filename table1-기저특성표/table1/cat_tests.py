"""Categorical association tests — pure standard library.

Pearson's chi-square test of independence on an r x c contingency table, and
Fisher's exact test (two-sided) for the 2 x 2 case. p-values come from the
chi-square distribution / exact hypergeometric enumeration implemented here and
match ``scipy.stats.chi2_contingency(correction=False)`` and
``scipy.stats.fisher_exact(alternative='two-sided')`` to ~1e-10.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence

from .special import chi2_sf

__all__ = [
    "ChiSquareResult",
    "FisherResult",
    "chi_square",
    "fisher_exact_2x2",
    "expected_counts",
    "min_expected",
]


@dataclass
class ChiSquareResult:
    statistic: float
    df: int
    pvalue: float
    min_expected: float


@dataclass
class FisherResult:
    oddsratio: float
    pvalue: float


def expected_counts(table: Sequence[Sequence[float]]) -> List[List[float]]:
    """Return the expected-count matrix under independence."""
    rows = len(table)
    cols = len(table[0]) if rows else 0
    row_tot = [sum(r) for r in table]
    col_tot = [sum(table[i][j] for i in range(rows)) for j in range(cols)]
    total = sum(row_tot)
    if total == 0:
        raise ValueError("contingency table is all zeros")
    return [[row_tot[i] * col_tot[j] / total for j in range(cols)]
            for i in range(rows)]


def min_expected(table: Sequence[Sequence[float]]) -> float:
    """Smallest expected count — used to decide whether chi-square is valid."""
    exp = expected_counts(table)
    return min(min(r) for r in exp)


def chi_square(table: Sequence[Sequence[float]]) -> ChiSquareResult:
    """Pearson chi-square test of independence (no continuity correction).

    Rows/columns that are entirely zero are dropped first (a level or group
    with no observations carries no degrees of freedom). Raises ValueError if
    fewer than 2 rows and 2 columns remain.
    """
    # Drop all-zero rows and columns so the df matches the observed structure.
    tbl = [list(map(float, r)) for r in table]
    tbl = [r for r in tbl if sum(r) > 0]
    if not tbl:
        raise ValueError("contingency table has no observations")
    ncol = len(tbl[0])
    keep_cols = [j for j in range(ncol) if sum(r[j] for r in tbl) > 0]
    tbl = [[r[j] for j in keep_cols] for r in tbl]
    rows = len(tbl)
    cols = len(tbl[0])
    if rows < 2 or cols < 2:
        raise ValueError(
            "chi-square needs at least 2 non-empty rows and 2 non-empty columns")
    exp = expected_counts(tbl)
    chisq = 0.0
    for i in range(rows):
        for j in range(cols):
            e = exp[i][j]
            if e > 0:
                chisq += (tbl[i][j] - e) ** 2 / e
    df = (rows - 1) * (cols - 1)
    mn = min(min(r) for r in exp)
    return ChiSquareResult(chisq, df, chi2_sf(chisq, df), mn)


def _log_binom(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def _hypergeom_logpmf(a: int, row1: int, row2: int, col1: int) -> float:
    """log P(X=a) for the central hypergeometric with fixed margins."""
    n = row1 + row2
    return (_log_binom(row1, a) + _log_binom(row2, col1 - a)
            - _log_binom(n, col1))


def fisher_exact_2x2(table: Sequence[Sequence[int]]) -> FisherResult:
    """Two-sided Fisher's exact test for a 2 x 2 table [[a, b], [c, d]].

    Sums the probabilities of all tables (with the same margins) whose
    probability is <= that of the observed table, matching
    ``scipy.stats.fisher_exact(alternative='two-sided')``.
    """
    (a, b), (c, d) = table
    a, b, c, d = int(a), int(b), int(c), int(d)
    row1 = a + b
    row2 = c + d
    col1 = a + c
    col2 = b + d
    n = row1 + row2
    if row1 == 0 or row2 == 0 or col1 == 0 or col2 == 0:
        # A zero margin means the two variables are trivially independent.
        return FisherResult(float("nan"), 1.0)

    # Odds ratio (with the conventional sample estimate; inf/0/nan at edges).
    if b == 0 or c == 0:
        oddsratio = float("inf") if (a and d) else float("nan")
    elif a == 0 or d == 0:
        oddsratio = 0.0
    else:
        oddsratio = (a * d) / (b * c)

    lo = max(0, col1 - row2)
    hi = min(col1, row1)
    log_p_obs = _hypergeom_logpmf(a, row1, row2, col1)
    p_obs = math.exp(log_p_obs)
    tol = 1.0 + 1e-7
    total = 0.0
    for x in range(lo, hi + 1):
        px = math.exp(_hypergeom_logpmf(x, row1, row2, col1))
        if px <= p_obs * tol:
            total += px
    return FisherResult(oddsratio, min(1.0, total))
