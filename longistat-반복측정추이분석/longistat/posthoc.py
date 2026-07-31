"""Follow-up comparisons that turn an omnibus ANOVA into a readable trial result.

Three families, each multiplicity-adjusted within itself:

``pairwise_times``    every pair of visits, within subject (paired t / Wilcoxon)
``between_at_time``   the groups compared at each visit (independent t / M-W)
``change_analysis``   change from baseline per group, and the between-group
                      difference in change — the estimand most trial protocols
                      actually name as the primary endpoint

Pairwise *paired* comparisons are deliberately used instead of a pooled-error
term: they do not assume sphericity, which is exactly the assumption most likely
to fail in a 3+ visit clinical study.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .basics import (adjust, ci_mean, mann_whitney, mean, paired_t, sd,
                     student_t, welch_t, wilcoxon_signed_rank)
from .dataio import Panel
from .describe import ALL_LABEL

__all__ = [
    "PairComparison", "GroupComparison", "ChangeRow", "ChangeContrast",
    "ChangeAnalysis", "pairwise_times", "between_at_time", "change_analysis",
    "DENSE_PAIRWISE_MAX",
]

# Above this many visits, "every pair" stops being a report: 200 visits is
# 19 900 rows per scope, computed twice.  Baseline-vs-each plus adjacent pairs
# answers the questions people actually ask; --all-pairs restores the rest.
DENSE_PAIRWISE_MAX = 12


def _group_index(panel: Panel) -> List[Tuple[str, List[int]]]:
    out: List[Tuple[str, List[int]]] = [(ALL_LABEL, list(range(panel.n_subjects)))]
    if panel.groups is not None:
        for lab in panel.group_labels():
            out.append((lab, [i for i, g in enumerate(panel.groups) if g == lab]))
    return out


@dataclass
class PairComparison:
    group: str
    time_a: str
    time_b: str
    n: int
    mean_diff: float                 # b − a
    ci_low: float
    ci_high: float
    statistic: float
    p_raw: float
    p_adj: float
    effect: float                    # dz (parametric) or rank-biserial r
    effect_label: str
    method: str
    effect_ci: Tuple[float, float] = (float("nan"), float("nan"))


def _pairs(n_times: int, baseline: int, all_pairs: bool
           ) -> List[Tuple[int, int]]:
    """Which visit pairs to compare."""
    if all_pairs or n_times <= DENSE_PAIRWISE_MAX:
        return [(a, b) for a in range(n_times) for b in range(a + 1, n_times)]
    wanted = {(min(baseline, j), max(baseline, j))
              for j in range(n_times) if j != baseline}
    wanted |= {(j, j + 1) for j in range(n_times - 1)}
    return sorted(wanted)


def pairwise_times(panel: Panel, alpha: float = 0.05,
                   correction: str = "holm",
                   nonparametric: bool = False,
                   by_group: bool = True, baseline: int = 0,
                   all_pairs: bool = False) -> List[PairComparison]:
    """Within-subject visit-to-visit comparisons, Holm/BH adjusted.

    Each comparison uses the subjects with **both** visits observed, so a
    subject who dropped out after week 4 still contributes to baseline→week 4.
    """
    scopes = _group_index(panel) if (by_group and panel.groups is not None) \
        else [(ALL_LABEL, list(range(panel.n_subjects)))]
    wanted = _pairs(panel.n_times, baseline, all_pairs)
    rows: List[PairComparison] = []
    for label, idx in scopes:
        raw: List[PairComparison] = []
        for a, b in wanted:
            diffs = [float(panel.values[i][b]) - float(panel.values[i][a])
                     for i in idx
                     if panel.values[i][a] is not None
                     and panel.values[i][b] is not None]
            if len(diffs) < 2:
                continue
            if nonparametric:
                res = wilcoxon_signed_rank(diffs, alpha)
                lo, hi = ci_mean(diffs, alpha)
                raw.append(PairComparison(
                    group=label, time_a=panel.times[a], time_b=panel.times[b],
                    n=len(diffs), mean_diff=mean(diffs), ci_low=lo, ci_high=hi,
                    statistic=res["w"], p_raw=res["p"], p_adj=float("nan"),
                    effect=res["r"], effect_label="rank-biserial r",
                    method=res["method"]))
            else:
                res = paired_t(diffs, alpha)
                raw.append(PairComparison(
                    group=label, time_a=panel.times[a], time_b=panel.times[b],
                    n=res.n, mean_diff=res.mean_diff, ci_low=res.ci_low,
                    ci_high=res.ci_high, statistic=res.t, p_raw=res.p,
                    p_adj=float("nan"), effect=res.dz_hedges,
                    effect_label="Hedges 보정 dz", method="대응 t-검정",
                    effect_ci=res.dz_ci))
        for row, padj in zip(raw, adjust([r.p_raw for r in raw], correction)):
            row.p_adj = padj
        rows.extend(raw)
    return rows


@dataclass
class GroupComparison:
    time: str
    group_a: str
    group_b: str
    n_a: int
    n_b: int
    mean_a: float
    mean_b: float
    diff: float                      # a − b
    ci_low: float
    ci_high: float
    statistic: float
    p_raw: float
    p_adj: float
    effect: float
    effect_label: str
    method: str
    effect_ci: Tuple[float, float] = (float("nan"), float("nan"))
    reference_only: bool = False     # baseline balance — described, not tested


def between_at_time(panel: Panel, alpha: float = 0.05,
                    correction: str = "holm",
                    nonparametric: bool = False,
                    welch: bool = True, baseline: int = 0
                    ) -> List[GroupComparison]:
    """Compare groups at each visit ("simple effects"), adjusted across visits.

    The **baseline** row is computed but marked ``reference_only``: CONSORT
    item 15 says testing baseline balance in a randomised trial is
    inappropriate, and including it also inflated the Holm multiplier on the
    comparisons that do matter (x3 instead of x2 in a 3-visit trial).
    """
    if panel.groups is None:
        return []
    labels = panel.group_labels()
    if len(labels) < 2:
        return []
    rows: List[GroupComparison] = []
    for a in range(len(labels)):
        for b in range(a + 1, len(labels)):
            ia = [i for i, g in enumerate(panel.groups) if g == labels[a]]
            ib = [i for i, g in enumerate(panel.groups) if g == labels[b]]
            for j, tname in enumerate(panel.times):
                va = [float(panel.values[i][j]) for i in ia
                      if panel.values[i][j] is not None]
                vb = [float(panel.values[i][j]) for i in ib
                      if panel.values[i][j] is not None]
                if len(va) < 2 or len(vb) < 2:
                    continue
                is_baseline = j == baseline
                if nonparametric:
                    res = mann_whitney(va, vb)
                    rows.append(GroupComparison(
                        time=tname, group_a=labels[a], group_b=labels[b],
                        n_a=len(va), n_b=len(vb), mean_a=mean(va),
                        mean_b=mean(vb), diff=mean(va) - mean(vb),
                        ci_low=float("nan"), ci_high=float("nan"),
                        statistic=res["u"], p_raw=res["p"], p_adj=float("nan"),
                        effect=res["r"], effect_label="rank-biserial r",
                        method=res["method"], reference_only=is_baseline))
                else:
                    res = (welch_t(va, vb, alpha) if welch
                           else student_t(va, vb, alpha))
                    rows.append(GroupComparison(
                        time=tname, group_a=labels[a], group_b=labels[b],
                        n_a=res.n1, n_b=res.n2, mean_a=res.mean1,
                        mean_b=res.mean2, diff=res.diff, ci_low=res.ci_low,
                        ci_high=res.ci_high, statistic=res.t, p_raw=res.p,
                        p_adj=float("nan"), effect=res.g,
                        effect_label="Hedges g", method=res.name,
                        effect_ci=res.g_ci, reference_only=is_baseline))
    tested = [r for r in rows if not r.reference_only]
    for row, padj in zip(tested, adjust([r.p_raw for r in tested], correction)):
        row.p_adj = padj
    for row in rows:
        if row.reference_only:
            row.p_adj = float("nan")
    return rows


@dataclass
class ChangeRow:
    group: str
    time: str
    n: int
    mean_change: float
    sd_change: float
    ci_low: float
    ci_high: float
    p_raw: float
    p_adj: float
    effect: float
    method: str
    effect_ci: Tuple[float, float] = (float("nan"), float("nan"))


@dataclass
class ChangeContrast:
    time: str
    group_a: str
    group_b: str
    n_a: int
    n_b: int
    change_a: float
    change_b: float
    diff: float                      # a − b, the "treatment effect"
    ci_low: float
    ci_high: float
    p_raw: float
    p_adj: float
    effect: float
    effect_label: str
    method: str
    effect_ci: Tuple[float, float] = (float("nan"), float("nan"))
    primary: bool = False            # pre-specified primary endpoint?


@dataclass
class ChangeAnalysis:
    baseline: str
    within: List[ChangeRow] = field(default_factory=list)
    between: List[ChangeContrast] = field(default_factory=list)
    primary_time: Optional[str] = None


def change_analysis(panel: Panel, baseline: int = 0, alpha: float = 0.05,
                    correction: str = "holm",
                    nonparametric: bool = False,
                    welch: bool = True,
                    primary_time: Optional[str] = None) -> ChangeAnalysis:
    """Change-from-baseline within each group, and the between-group contrast.

    The between-group contrast on change scores is the classic trial estimand
    ("difference in change from baseline"); its confidence interval is the
    number that belongs in the abstract, not the interaction p-value alone.

    ``primary_time`` names a pre-specified primary visit.  That contrast is
    reported **unadjusted** — a SAP that designates week 8 as primary does not
    pay a multiplicity penalty for an exploratory week-4 look — and the
    remaining visits are adjusted among themselves.
    """
    if not 0 <= baseline < panel.n_times:
        raise ValueError("기준 시점 색인이 범위를 벗어났습니다.")
    if primary_time is not None and primary_time not in panel.times:
        raise ValueError(
            f"--primary-time '{primary_time}' 을(를) 시점 목록 {panel.times} "
            "에서 찾을 수 없습니다.")
    if primary_time is not None and primary_time == panel.times[baseline]:
        raise ValueError("--primary-time 은 기준시점이 아닌 시점이어야 합니다.")
    out = ChangeAnalysis(baseline=panel.times[baseline])
    scopes = _group_index(panel)
    if panel.groups is None:
        scopes = [(ALL_LABEL, list(range(panel.n_subjects)))]

    changes: Dict[Tuple[str, int], List[float]] = {}
    for label, idx in scopes:
        rows: List[ChangeRow] = []
        for j in range(panel.n_times):
            if j == baseline:
                continue
            diffs = [float(panel.values[i][j]) - float(panel.values[i][baseline])
                     for i in idx
                     if panel.values[i][j] is not None
                     and panel.values[i][baseline] is not None]
            changes[(label, j)] = diffs
            if len(diffs) < 2:
                continue
            if nonparametric:
                res = wilcoxon_signed_rank(diffs, alpha)
                lo, hi = ci_mean(diffs, alpha)
                rows.append(ChangeRow(
                    group=label, time=panel.times[j], n=len(diffs),
                    mean_change=mean(diffs), sd_change=sd(diffs), ci_low=lo,
                    ci_high=hi, p_raw=res["p"], p_adj=float("nan"),
                    effect=res["r"], method=res["method"]))
            else:
                res = paired_t(diffs, alpha)
                rows.append(ChangeRow(
                    group=label, time=panel.times[j], n=res.n,
                    mean_change=res.mean_diff, sd_change=res.sd_diff,
                    ci_low=res.ci_low, ci_high=res.ci_high, p_raw=res.p,
                    p_adj=float("nan"), effect=res.dz_hedges,
                    method="대응 t-검정", effect_ci=res.dz_ci))
        for row, padj in zip(rows, adjust([r.p_raw for r in rows], correction)):
            row.p_adj = padj
        out.within.extend(rows)

    if panel.groups is not None and len(panel.group_labels()) >= 2:
        labels = panel.group_labels()
        contrasts: List[ChangeContrast] = []
        for a in range(len(labels)):
            for b in range(a + 1, len(labels)):
                for j in range(panel.n_times):
                    if j == baseline:
                        continue
                    ca = changes.get((labels[a], j), [])
                    cb = changes.get((labels[b], j), [])
                    if len(ca) < 2 or len(cb) < 2:
                        continue
                    is_primary = panel.times[j] == primary_time
                    if nonparametric:
                        res = mann_whitney(ca, cb)
                        contrasts.append(ChangeContrast(
                            time=panel.times[j], group_a=labels[a],
                            group_b=labels[b], n_a=len(ca), n_b=len(cb),
                            change_a=mean(ca), change_b=mean(cb),
                            diff=mean(ca) - mean(cb), ci_low=float("nan"),
                            ci_high=float("nan"), p_raw=res["p"],
                            p_adj=float("nan"), effect=res["r"],
                            effect_label="rank-biserial r", method=res["method"],
                            primary=is_primary))
                    else:
                        res = (welch_t(ca, cb, alpha) if welch
                               else student_t(ca, cb, alpha))
                        contrasts.append(ChangeContrast(
                            time=panel.times[j], group_a=labels[a],
                            group_b=labels[b], n_a=res.n1, n_b=res.n2,
                            change_a=res.mean1, change_b=res.mean2,
                            diff=res.diff, ci_low=res.ci_low,
                            ci_high=res.ci_high, p_raw=res.p,
                            p_adj=float("nan"), effect=res.g,
                            effect_label="Hedges g", method=res.name,
                            effect_ci=res.g_ci, primary=is_primary))
        secondary = [c for c in contrasts if not c.primary]
        for row, padj in zip(secondary,
                             adjust([r.p_raw for r in secondary], correction)):
            row.p_adj = padj
        for row in contrasts:
            if row.primary:
                row.p_adj = row.p_raw       # pre-specified: no penalty
        out.between = contrasts
        out.primary_time = primary_time
    return out
