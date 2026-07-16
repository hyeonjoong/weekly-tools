"""Orchestration: pick the right test from assumption checks and assemble a report.

The decision rules mirror standard clinical-stats practice:

Two independent groups
    - Shapiro-Wilk normality on each group (when 3 <= n <= 5000).
    - Both normal  -> Levene's test:  equal variance -> Student's t
                                       unequal        -> Welch's t
    - Any non-normal -> Mann-Whitney U (exact for small tie-free samples), plus a
      Hodges-Lehmann median-difference estimate with a distribution-free CI.

Two paired conditions (``analyze_paired``)
    - Normality of the within-pair differences -> paired t-test, else Wilcoxon
      signed-rank (exact when possible) with a Hodges-Lehmann CI.

Three or more groups
    - All normal + equal variance   -> one-way ANOVA (pairwise Student's t).
    - All normal + unequal variance -> Welch's ANOVA (pairwise Welch's t).
    - Otherwise                     -> Kruskal-Wallis (pairwise Mann-Whitney).
    - If the omnibus test is significant, pairwise post-hoc comparisons with
      Holm-Bonferroni (default) or Benjamini-Hochberg (FDR) correction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import effects, location as location_mod, paired as paired_mod, tests_stat
from .normality import shapiro_wilk
from .special import t_ppf

__all__ = ["Group", "PairwiseResult", "AnalysisResult", "analyze",
           "analyze_paired"]


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
    # paired / repeated-measures extras
    paired: bool = False
    diff_normality: Optional[NormalityCheck] = None
    n_pairs: Optional[int] = None
    n_zero_diff: Optional[int] = None
    correction: str = "holm"
    # distribution-free location difference (Hodges-Lehmann) for rank tests
    location: Optional[location_mod.LocationEstimate] = None


def _finite(vals: Sequence[float], label: str) -> List[float]:
    """Coerce to float and reject non-finite values (NaN/inf) with a clear error."""
    out: List[float] = []
    for v in vals:
        f = float(v)
        if not math.isfinite(f):
            raise ValueError(
                f"group '{label}' contains a non-finite value ({v!r}); "
                f"remove NaN/inf before analysis")
        out.append(f)
    return out


def _normality_check(g: Group, alpha_norm: float) -> NormalityCheck:
    if g.n < 3:
        return NormalityCheck(g.label, None, None, None,
                              "n<3: normality unknown (defaulting to non-parametric, conservative)")
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
    method_note = ("exact permutation p-value" if res.method == "exact"
                   else "normal approximation")
    reason = ("not all groups are normal (" + ", ".join(non_normal) +
              ") -> Mann-Whitney U test [" + method_note + "]")
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


def _bh_adjust(pvals: List[float]) -> List[float]:
    """Benjamini-Hochberg (FDR) step-up adjustment; returns adjusted p-values."""
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvals[i])  # ascending
    adj = [0.0] * m
    running = 1.0
    # step up from the largest p-value, enforcing monotonicity
    for rank in range(m - 1, -1, -1):
        idx = order[rank]
        val = pvals[idx] * m / (rank + 1)
        running = min(running, val)
        adj[idx] = min(1.0, running)
    return adj


def _correct(pvals: List[float], method: str) -> List[float]:
    if method == "bh":
        return _bh_adjust(pvals)
    if method == "holm":
        return _holm_adjust(pvals)
    raise ValueError(f"unknown correction method '{method}' (use 'holm' or 'bh')")


def _pairwise(groups: List[Group], kind: str, alpha: float,
              correction: str = "holm") -> List[PairwiseResult]:
    """Pairwise post-hoc comparisons with multiple-testing correction.

    ``kind`` selects the per-pair test, kept consistent with the omnibus:
        'student'     -> pairwise Student's t   (after equal-variance ANOVA)
        'welch'       -> pairwise Welch's t     (after Welch's ANOVA)
        'mannwhitney' -> pairwise Mann-Whitney U (after Kruskal-Wallis)
    """
    pairs = [(i, j) for i in range(len(groups)) for j in range(i + 1, len(groups))]
    raw = []
    meta = []
    for i, j in pairs:
        a, b = groups[i], groups[j]
        if kind == "student":
            r = tests_stat.students_t(a.values, b.values)
            es = effects.cohens_d(a.values, b.values, hedges=True)
            meta.append(("Student's t", r.statistic, es.name, es.value, a.label, b.label))
            raw.append(r.pvalue)
        elif kind == "welch":
            r = tests_stat.welch_t(a.values, b.values)
            es = effects.cohens_d(a.values, b.values, hedges=True)
            meta.append(("Welch's t", r.statistic, es.name, es.value, a.label, b.label))
            raw.append(r.pvalue)
        else:
            r = tests_stat.mann_whitney_u(a.values, b.values)
            es = effects.rank_biserial(a.values, b.values)
            meta.append(("Mann-Whitney U", r.statistic, es.name, es.value, a.label, b.label))
            raw.append(r.pvalue)
    adj = _correct(raw, correction)
    out = []
    for k, (test, stat, ename, eval_, la, lb) in enumerate(meta):
        out.append(PairwiseResult(la, lb, test, stat, raw[k], adj[k],
                                  ename, eval_, adj[k] < alpha))
    return out


def analyze(named_groups: Sequence[Tuple[str, Sequence[float]]],
            alpha: float = 0.05, alpha_norm: float = 0.05,
            posthoc: bool = True, correction: str = "holm") -> AnalysisResult:
    """Run the full auto-selected group comparison and return an AnalysisResult."""
    if correction not in ("holm", "bh"):
        raise ValueError("correction must be 'holm' or 'bh'")
    groups = [Group(str(label), _finite(vals, label)) for label, vals in named_groups]
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
        loc = None
        if name == "Mann-Whitney U test":
            loc = location_mod.hodges_lehmann_independent(
                groups[0].values, groups[1].values, conf=1.0 - alpha)
        return AnalysisResult(
            groups=groups, alpha=alpha, alpha_norm=alpha_norm, normality=norm,
            levene=lev, test_name=name, statistic=stat, df=df, df2=None, pvalue=p,
            significant=p < alpha, effects=es, mean_diff=diff, mean_diff_ci=ci,
            warnings=warnings, reason=reason, correction=correction, location=loc)

    # 3+ groups
    both_normal = all(nc.normal for nc in norm)
    equal_var = lev.pvalue > alpha_norm
    # If a group has zero variance the Levene p-value is NaN; treat variance as
    # not demonstrably equal so we don't select equal-variance ANOVA on it.
    if lev.pvalue != lev.pvalue:  # NaN
        equal_var = False
    df2: Optional[float]
    if both_normal and equal_var:
        res = tests_stat.one_way_anova([g.values for g in groups])
        es = [effects.eta_squared([g.values for g in groups], res.ss_between, res.ss_total)]
        name, stat, df, df2, p = ("One-way ANOVA", res.statistic,
                                  res.df_between, res.df_within, res.pvalue)
        reason = ("all groups ~normal and equal variance (Levene p={:.3f}) "
                  "-> one-way ANOVA").format(lev.pvalue)
        pair_kind = "student"
    elif both_normal and not equal_var:
        # Normal but heteroscedastic -> Welch's ANOVA (more appropriate than
        # forcing a rank test on data that is actually normal).
        try:
            res = tests_stat.welch_anova([g.values for g in groups])
            ow = tests_stat.one_way_anova([g.values for g in groups])
            es = [effects.eta_squared([g.values for g in groups],
                                      ow.ss_between, ow.ss_total)]
            name, stat, df, df2, p = ("Welch's ANOVA", res.statistic,
                                      res.df_between, res.df_within, res.pvalue)
            lp = "{:.3f}".format(lev.pvalue) if lev.pvalue == lev.pvalue else "NaN"
            reason = ("all groups ~normal but unequal variance (Levene p=" + lp +
                      ") -> Welch's ANOVA")
            warnings.append(
                "효과크기 η²는 등분산 가정의 고전적 제곱합(pooled SS)에서 계산되어 "
                "Welch's ANOVA와 정확히 일관되지는 않습니다(이분산에서 근사치로 해석하세요).")
            pair_kind = "welch"
        except ValueError:
            # zero-variance group makes Welch's ANOVA undefined -> Kruskal-Wallis
            res = tests_stat.kruskal_wallis([g.values for g in groups])
            n_total = sum(g.n for g in groups)
            es = [effects.eta_squared_h(res.statistic, n_total, len(groups))]
            name, stat, df, df2, p = ("Kruskal-Wallis H test", res.statistic,
                                      res.df, None, res.pvalue)
            reason = ("unequal variance with a zero-variance group "
                      "-> Kruskal-Wallis H test")
            pair_kind = "mannwhitney"
    else:
        res = tests_stat.kruskal_wallis([g.values for g in groups])
        n_total = sum(g.n for g in groups)
        es = [effects.eta_squared_h(res.statistic, n_total, len(groups))]
        name, stat, df, df2, p = ("Kruskal-Wallis H test", res.statistic,
                                  res.df, None, res.pvalue)
        reason = "not all groups normal -> Kruskal-Wallis H test"
        pair_kind = "mannwhitney"

    pairwise: List[PairwiseResult] = []
    if posthoc and p == p and p < alpha:
        pairwise = _pairwise(groups, pair_kind, alpha, correction)

    return AnalysisResult(
        groups=groups, alpha=alpha, alpha_norm=alpha_norm, normality=norm,
        levene=lev, test_name=name, statistic=stat, df=df, df2=df2, pvalue=p,
        significant=(p == p and p < alpha), effects=es, pairwise=pairwise,
        warnings=warnings, reason=reason, correction=correction)


def analyze_paired(cond_a: Tuple[str, Sequence[float]],
                   cond_b: Tuple[str, Sequence[float]],
                   alpha: float = 0.05, alpha_norm: float = 0.05
                   ) -> AnalysisResult:
    """Paired / repeated-measures analysis of two matched conditions.

    Normality is checked on the *differences* (a - b), then:
        differences ~normal      -> paired-samples t-test (effect: Cohen's dz)
        differences non-normal   -> Wilcoxon signed-rank (effect: matched
                                    rank-biserial r; exact for small tie-free
                                    samples, otherwise the normal approximation)
    """
    la, va = cond_a[0], _finite(cond_a[1], cond_a[0])
    lb, vb = cond_b[0], _finite(cond_b[1], cond_b[0])
    if len(va) != len(vb):
        raise ValueError(
            f"paired analysis needs equal-length conditions (got {len(va)} "
            f"and {len(vb)})")
    if len(va) < 2:
        raise ValueError("paired analysis needs at least 2 matched pairs")

    ga, gb = Group(str(la), va), Group(str(lb), vb)
    diffs = [x - y for x, y in zip(va, vb)]
    warnings: List[str] = []

    # Normality of the differences drives the choice.
    dnc: NormalityCheck
    n = len(diffs)
    if all(d == diffs[0] for d in diffs):
        dnc = NormalityCheck("differences", None, None, None,
                             "all differences identical (zero variance)")
    elif n < 3:
        dnc = NormalityCheck("differences", None, None, None,
                             "n<3: normality unknown (defaulting to non-parametric, conservative)")
    elif n > 5000:
        dnc = NormalityCheck("differences", None, None, True,
                             "n>5000: normality test skipped (assumed normal)")
    else:
        try:
            w, pw = shapiro_wilk(diffs)
            dnc = NormalityCheck("differences", w, pw, pw > alpha_norm)
        except ValueError as exc:
            dnc = NormalityCheck("differences", None, None, None, str(exc))
    if dnc.note:
        warnings.append(f"[differences] {dnc.note}")

    if dnc.normal:
        res = paired_mod.paired_t(va, vb)
        es = [effects.cohens_dz(va, vb)]
        tcrit = t_ppf(1 - alpha / 2, res.df)
        se = res.sd_diff / math.sqrt(res.n)
        ci = (res.mean_diff - tcrit * se, res.mean_diff + tcrit * se)
        reason = ("differences ~normal (Shapiro p={:.3f}) "
                  "-> paired-samples t-test").format(dnc.pvalue)
        return AnalysisResult(
            groups=[ga, gb], alpha=alpha, alpha_norm=alpha_norm, normality=[],
            levene=None, test_name="Paired t-test", statistic=res.statistic,
            df=res.df, df2=None, pvalue=res.pvalue,
            significant=(res.pvalue == res.pvalue and res.pvalue < alpha),
            effects=es, mean_diff=res.mean_diff, mean_diff_ci=ci,
            warnings=warnings, reason=reason, paired=True, diff_normality=dnc,
            n_pairs=res.n)

    res = paired_mod.wilcoxon_signed_rank(va, vb)
    es = [effects.matched_rank_biserial(res.w_plus, res.w_minus)]
    why = ("differences non-normal" if dnc.normal is False
           else "normality of differences undetermined")
    method_note = ("exact" if res.method == "exact" else "normal approximation")
    reason = f"{why} -> Wilcoxon signed-rank test [{method_note}]"
    if res.n_zero:
        warnings.append(
            f"{res.n_zero} pair(s) with zero difference dropped (Wilcoxon)")
    loc = location_mod.hodges_lehmann_paired(diffs, conf=1.0 - alpha)
    return AnalysisResult(
        groups=[ga, gb], alpha=alpha, alpha_norm=alpha_norm, normality=[],
        levene=None, test_name="Wilcoxon signed-rank test",
        statistic=res.statistic, df=None, df2=None, pvalue=res.pvalue,
        significant=(res.pvalue < alpha), effects=es, warnings=warnings,
        reason=reason, paired=True, diff_normality=dnc,
        n_pairs=res.n_nonzero + res.n_zero, n_zero_diff=res.n_zero, location=loc)
