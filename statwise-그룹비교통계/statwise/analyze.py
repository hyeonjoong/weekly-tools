"""Orchestration: pick the right test from assumption checks and assemble a report.

The decision rules mirror standard clinical-stats practice:

Two groups
    - Shapiro-Wilk normality on each group (when 3 <= n <= 5000).
    - Both normal  -> Levene's test:  equal variance -> Student's t
                                       unequal        -> Welch's t
    - Any non-normal -> Mann-Whitney U.

Three or more groups
    - All normal and equal variance -> one-way ANOVA.
    - Otherwise                     -> Kruskal-Wallis.
    - If the omnibus test is significant, pairwise post-hoc comparisons with
      Holm-Bonferroni correction of the p-values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import effects, tests_stat
from .normality import shapiro_wilk
from .special import t_ppf

__all__ = ["Group", "PairwiseResult", "AnalysisResult", "analyze"]


@dataclass
class Group:
    label: str
    values: List[float]

    @property
    def n(self) -> int:
        return len(self.values)

    @property
    def mean(self) -> float:
        return tests_stat.mean(self.values)

    @property
    def sd(self) -> float:
        return math.sqrt(tests_stat.variance(self.values)) if self.n > 1 else float("nan")

    @property
    def median(self) -> float:
        s = sorted(self.values)
        m = len(s)
        mid = m // 2
        return s[mid] if m % 2 else (s[mid - 1] + s[mid]) / 2.0

    def quartiles(self) -> Tuple[float, float]:
        """(Q1, Q3) using linear interpolation (numpy default 'linear')."""
        s = sorted(self.values)
        return _quantile(s, 0.25), _quantile(s, 0.75)


def _quantile(sorted_vals: Sequence[float], q: float) -> float:
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


@dataclass
class NormalityCheck:
    label: str
    w: Optional[float]
    pvalue: Optional[float]
    normal: Optional[bool]
    note: str = ""


@dataclass
class PairwiseResult:
    a: str
    b: str
    test: str
    statistic: float
    pvalue_raw: float
    pvalue_adj: float
    effect_name: str
    effect_value: float
    significant: bool


@dataclass
class AnalysisResult:
    groups: List[Group]
    alpha: float
    alpha_norm: float
    normality: List[NormalityCheck]
    levene: Optional[tests_stat.LeveneResult]
    test_name: str
    statistic: float
    df: Optional[float]
    df2: Optional[float]
    pvalue: float
    significant: bool
    effects: List[effects.EffectSize] = field(default_factory=list)
    mean_diff: Optional[float] = None
    mean_diff_ci: Optional[Tuple[float, float]] = None
    pairwise: List[PairwiseResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    reason: str = ""


def _normality_check(g: Group, alpha_norm: float) -> NormalityCheck:
    if g.n < 3:
        return NormalityCheck(g.label, None, None, None,
                              "n<3: cannot test normality (assumed non-normal)")
    if g.n > 5000:
        return NormalityCheck(g.label, None, None, True,
                              "n>5000: normality test skipped (assumed normal)")
    try:
        w, p = shapiro_wilk(g.values)
    except ValueError as exc:
        return NormalityCheck(g.label, None, None, None, str(exc))
    return NormalityCheck(g.label, w, p, p > alpha_norm)


def _auto_two_group(a: Group, b: Group, alpha: float, alpha_norm: float,
                    norm: List[NormalityCheck], lev: tests_stat.LeveneResult
                    ) -> Tuple[str, float, Optional[float], float, List, str,
                               Optional[float], Optional[Tuple[float, float]]]:
    both_normal = all(nc.normal for nc in norm)
    warns_reason = ""
    if both_normal:
        equal_var = lev.pvalue > alpha_norm
        if equal_var:
            res = tests_stat.students_t(a.values, b.values)
            reason = ("both groups ~normal (Shapiro p>{:.2f}) and equal variance "
                      "(Levene p={:.3f}) -> Student's t-test").format(alpha_norm, lev.pvalue)
        else:
            res = tests_stat.welch_t(a.values, b.values)
            reason = ("both groups ~normal but unequal variance (Levene p={:.3f}) "
                      "-> Welch's t-test").format(lev.pvalue)
        es = [effects.cohens_d(a.values, b.values, hedges=True)]
        # mean difference CI
        n1, n2 = a.n, b.n
        v1, v2 = tests_stat.variance(a.values), tests_stat.variance(b.values)
        diff = a.mean - b.mean
        if res.kind == "student":
            sp2 = ((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2)
            se = math.sqrt(sp2 * (1 / n1 + 1 / n2))
        else:
            se = math.sqrt(v1 / n1 + v2 / n2)
        tcrit = t_ppf(1 - alpha / 2, res.df)
        ci = (diff - tcrit * se, diff + tcrit * se)
        label = "Student's t-test" if res.kind == "student" else "Welch's t-test"
        return (label, res.statistic, res.df, res.pvalue, es, reason, diff, ci)
    # non-parametric
    res = tests_stat.mann_whitney_u(a.values, b.values)
    non_normal = [nc.label for nc in norm if nc.normal is not True]
    reason = ("not all groups are normal (" + ", ".join(non_normal) +
              ") -> Mann-Whitney U test")
    es = [effects.rank_biserial(a.values, b.values),
          effects.cliffs_delta(a.values, b.values)]
    return ("Mann-Whitney U test", res.statistic, None, res.pvalue, es, reason, None, None)


def _holm_adjust(pvals: List[float]) -> List[float]:
    """Holm-Bonferroni step-down adjustment; returns adjusted p-values."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        val = (m - rank) * pvals[idx]
        running = max(running, val)
        adj[idx] = min(1.0, running)
    return adj


def _pairwise(groups: List[Group], parametric: bool, alpha: float,
              alpha_norm: float) -> List[PairwiseResult]:
    pairs = [(i, j) for i in range(len(groups)) for j in range(i + 1, len(groups))]
    raw = []
    meta = []
    for i, j in pairs:
        a, b = groups[i], groups[j]
        if parametric:
            # ANOVA assumed equal variance, so pairwise Student's t keeps the
            # post-hoc internally consistent with the omnibus (pairwise t-tests
            # with Holm correction; not Tukey HSD).
            r = tests_stat.students_t(a.values, b.values)
            es = effects.cohens_d(a.values, b.values, hedges=True)
            meta.append(("Student's t", r.statistic, es.name, es.value, a.label, b.label))
            raw.append(r.pvalue)
        else:
            r = tests_stat.mann_whitney_u(a.values, b.values)
            es = effects.rank_biserial(a.values, b.values)
            meta.append(("Mann-Whitney U", r.statistic, es.name, es.value, a.label, b.label))
            raw.append(r.pvalue)
    adj = _holm_adjust(raw)
    out = []
    for k, (test, stat, ename, eval_, la, lb) in enumerate(meta):
        out.append(PairwiseResult(la, lb, test, stat, raw[k], adj[k],
                                  ename, eval_, adj[k] < alpha))
    return out


def analyze(named_groups: Sequence[Tuple[str, Sequence[float]]],
            alpha: float = 0.05, alpha_norm: float = 0.05,
            posthoc: bool = True) -> AnalysisResult:
    """Run the full auto-selected group comparison and return an AnalysisResult."""
    groups = [Group(str(label), [float(v) for v in vals]) for label, vals in named_groups]
    if len(groups) < 2:
        raise ValueError("need at least 2 groups to compare")
    for g in groups:
        if g.n < 2:
            raise ValueError(f"group '{g.label}' has fewer than 2 observations")

    warnings: List[str] = []
    norm = [_normality_check(g, alpha_norm) for g in groups]
    for nc in norm:
        if nc.note:
            warnings.append(f"[{nc.label}] {nc.note}")

    lev = tests_stat.levene([g.values for g in groups])

    if len(groups) == 2:
        (name, stat, df, p, es, reason, diff, ci) = _auto_two_group(
            groups[0], groups[1], alpha, alpha_norm, norm, lev)
        return AnalysisResult(
            groups=groups, alpha=alpha, alpha_norm=alpha_norm, normality=norm,
            levene=lev, test_name=name, statistic=stat, df=df, df2=None, pvalue=p,
            significant=p < alpha, effects=es, mean_diff=diff, mean_diff_ci=ci,
            warnings=warnings, reason=reason)

    # 3+ groups
    both_normal = all(nc.normal for nc in norm)
    equal_var = lev.pvalue > alpha_norm
    if both_normal and equal_var:
        res = tests_stat.one_way_anova([g.values for g in groups])
        es = [effects.eta_squared([g.values for g in groups], res.ss_between, res.ss_total)]
        name, stat, df, df2, p = ("One-way ANOVA", res.statistic,
                                  res.df_between, res.df_within, res.pvalue)
        reason = ("all groups ~normal and equal variance (Levene p={:.3f}) "
                  "-> one-way ANOVA").format(lev.pvalue)
        parametric = True
    else:
        res = tests_stat.kruskal_wallis([g.values for g in groups])
        n_total = sum(g.n for g in groups)
        es = [effects.eta_squared_h(res.statistic, n_total, len(groups))]
        name, stat, df, df2, p = ("Kruskal-Wallis H test", res.statistic,
                                  res.df, None, res.pvalue)
        why = "unequal variance" if both_normal else "not all groups normal"
        reason = f"{why} -> Kruskal-Wallis H test"
        parametric = False

    pairwise: List[PairwiseResult] = []
    if posthoc and p < alpha:
        pairwise = _pairwise(groups, parametric, alpha, alpha_norm)

    return AnalysisResult(
        groups=groups, alpha=alpha, alpha_norm=alpha_norm, normality=norm,
        levene=lev, test_name=name, statistic=stat, df=df, df2=df2, pvalue=p,
        significant=p < alpha, effects=es, pairwise=pairwise,
        warnings=warnings, reason=reason)
