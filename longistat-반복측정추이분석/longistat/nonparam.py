"""Friedman test and Kendall's W — the rank-based fallback for the time effect.

Used when the change scores are clearly non-normal or the outcome is ordinal
(most patient-reported scales are).  Ranking happens *within* each subject, so
the test is free of between-subject scale differences entirely.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .basics import ranks, tie_correction
from .dataio import Panel
from .describe import ALL_LABEL
from .special import chi2_sf

__all__ = ["FriedmanResult", "friedman", "friedman_by_group"]


@dataclass
class FriedmanResult:
    group: str
    n: int
    k: int
    rank_sums: List[float]
    mean_ranks: List[float]
    chi2: float
    df: int
    p: float
    kendall_w: float
    ties: bool


def friedman(matrix: Sequence[Sequence[float]],
             group: str = ALL_LABEL) -> FriedmanResult:
    """Friedman test with the standard correction for within-subject ties."""
    n = len(matrix)
    if n < 2:
        raise ValueError("Friedman 검정에는 완전자료 대상이 2명 이상 필요합니다.")
    k = len(matrix[0])
    if k < 3:
        raise ValueError("Friedman 검정은 시점이 3개 이상일 때 사용합니다 "
                         "(2개면 Wilcoxon 부호순위 검정을 쓰세요).")
    rank_sums = [0.0] * k
    tie_total = 0.0
    for row in matrix:
        if len(row) != k:
            raise ValueError("모든 대상의 시점 개수가 같아야 합니다.")
        rk = ranks(list(row))
        tie_total += tie_correction(list(row))
        for j in range(k):
            rank_sums[j] += rk[j]
    expected = n * (k + 1) / 2.0
    numer = 12.0 * math.fsum((r - expected) ** 2 for r in rank_sums)
    denom = n * k * (k + 1) - tie_total / (k - 1)
    if denom <= 0:
        chi2 = 0.0
    else:
        chi2 = numer / denom
    df = k - 1
    p = chi2_sf(chi2, df) if chi2 > 0 else 1.0
    w = chi2 / (n * (k - 1)) if n * (k - 1) > 0 else float("nan")
    return FriedmanResult(
        group=group, n=n, k=k, rank_sums=rank_sums,
        mean_ranks=[r / n for r in rank_sums], chi2=chi2, df=df, p=p,
        kendall_w=min(1.0, w), ties=tie_total > 0)


def friedman_by_group(panel: Panel) -> List[FriedmanResult]:
    """Friedman on the complete cases overall and, when present, per group."""
    cc = panel.complete_case()
    if cc.n_subjects < 2 or cc.n_times < 3:
        return []
    out = [friedman(cc.matrix(), ALL_LABEL)]
    if cc.groups is not None:
        for lab in cc.group_labels():
            idx = [i for i, g in enumerate(cc.groups) if g == lab]
            if len(idx) < 2:
                continue
            out.append(friedman([cc.values[i] for i in idx], lab))  # type: ignore[arg-type]
    return out
