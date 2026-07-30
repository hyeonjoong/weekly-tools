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
from typing import Dict, List, Optional, Sequence, Tuple

from . import (effects, equivalence as equiv_mod, location as location_mod,
               paired as paired_mod, tests_stat)
from .dataio import sanitize_label as _safe_label
from .normality import shapiro_wilk
from .special import t_ppf

__all__ = ["Group", "PairwiseResult", "AnalysisResult", "analyze",
           "analyze_paired"]


@dataclass
class Group:
    label: str
    values: List[float]
    #: cells that were present in the source file but unusable (blank / NA /
    #: non-numeric).  Reported for CONSORT-style accounting; 0 when unknown.
    n_missing: int = 0

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

    @property
    def minimum(self) -> float:
        return min(self.values) if self.values else float("nan")

    @property
    def maximum(self) -> float:
        return max(self.values) if self.values else float("nan")

    def quartiles(self) -> Tuple[float, float]:
        """(Q1, Q3) using linear interpolation (numpy default 'linear')."""
        s = sorted(self.values)
        return _quantile(s, 0.25), _quantile(s, 0.75)

    def mean_ci(self, conf: float = 0.95) -> Tuple[float, float]:
        """Two-sided t confidence interval for this group's own mean.

        NaN when n < 2 or the group is constant-free of variation is fine (a
        zero-width interval is the correct answer there).
        """
        if self.n < 2:
            return (float("nan"), float("nan"))
        sd = self.sd
        if sd != sd:
            return (float("nan"), float("nan"))
        se = sd / math.sqrt(self.n)
        tcrit = t_ppf(1.0 - (1.0 - conf) / 2.0, self.n - 1)
        return (self.mean - tcrit * se, self.mean + tcrit * se)


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
    #: difference on the original scale (mean difference for the t-based
    #: post-hocs, Hodges-Lehmann location shift after Kruskal-Wallis) with its
    #: interval -- a post-hoc table of p-values alone is not reportable.
    diff: Optional[float] = None
    diff_ci: Optional[Tuple[float, float]] = None
    diff_label: str = ""
    n_a: int = 0
    n_b: int = 0


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
    # equivalence (TOST) / non-inferiority test, when a margin was supplied
    equivalence: Optional[equiv_mod.EquivalenceResult] = None
    # multi-endpoint bookkeeping (set by the endpoint runner, not by analyze())
    endpoint: Optional[str] = None
    pvalue_adj: Optional[float] = None


@dataclass
class EquivalenceSpec:
    """What kind of similarity claim the user asked us to test, if any.

    Exactly one of ``margin`` (TOST equivalence) or ``ni_margin``
    (non-inferiority) may be set; both ``None`` means no equivalence analysis.
    """

    margin: Optional[Tuple[float, float]] = None
    ni_margin: Optional[float] = None
    ni_direction: str = "higher_is_better"

    def __post_init__(self) -> None:
        if self.margin is not None and self.ni_margin is not None:
            raise ValueError(
                "등가(TOST) 마진과 비열등성 마진은 동시에 지정할 수 없습니다 "
                "(둘 중 하나만 선택하세요).")

    @property
    def active(self) -> bool:
        return self.margin is not None or self.ni_margin is not None


_RANK_TEST_EQUIV_NOTE = (
    "등가/비열등성 검정은 평균차에 대한 t-모형(정규 근사)으로 계산했습니다. "
    "선택된 주검정은 순위검정({test})이므로, 등가 결론은 평균차가 의미 있는 "
    "요약일 때에만 유효합니다(왜곡이 심하면 해석에 주의).")


def _equiv_model_for(test_name: str) -> Tuple[str, bool]:
    """(t-model to use for the margin test, whether the main test was a rank test)."""
    if test_name.startswith("Student"):
        return "student", False
    if test_name.startswith("Welch"):
        return "welch", False
    if test_name.startswith("Paired"):
        return "paired", False
    if test_name.startswith("Wilcoxon"):
        return "paired", True
    return "welch", True  # Mann-Whitney


def _run_equivalence(a_vals: Sequence[float], b_vals: Sequence[float],
                     test_name: str, alpha: float, spec: EquivalenceSpec,
                     warnings: List[str]
                     ) -> Optional[equiv_mod.EquivalenceResult]:
    """Run the requested TOST / non-inferiority test alongside the main test.

    The t-model is kept consistent with the selected superiority test so both
    inferences describe the same difference; for a rank-based main test we fall
    back to the Welch/paired t-model and say so.
    """
    if not spec.active:
        return None
    model, is_rank = _equiv_model_for(test_name)
    if is_rank:
        warnings.append(_RANK_TEST_EQUIV_NOTE.format(test=test_name))
    try:
        if model == "paired":
            if spec.margin is not None:
                return equiv_mod.tost_paired(a_vals, b_vals, spec.margin[0],
                                             spec.margin[1], alpha)
            return equiv_mod.noninferiority_paired(
                a_vals, b_vals, spec.ni_margin, spec.ni_direction, alpha)
        if spec.margin is not None:
            return equiv_mod.tost_independent(a_vals, b_vals, spec.margin[0],
                                              spec.margin[1], alpha, model=model)
        return equiv_mod.noninferiority_independent(
            a_vals, b_vals, spec.ni_margin, spec.ni_direction, alpha, model=model)
    except ValueError as exc:
        warnings.append(f"등가/비열등성 검정을 수행할 수 없습니다: {exc}")
        return None


def _warn_ci_p_disagreement(loc, pvalue: float, alpha: float,
                            warnings: List[str]) -> None:
    """Flag the case where the rank test and its own interval disagree.

    The Hodges-Lehmann interval inverts the *exact* rank distribution, while the
    p-value falls back to the tie-corrected normal approximation when ties are
    present. On tied data the two can land on opposite sides of alpha, so the
    report would say "significant" beside an interval containing 0. Neither is
    wrong; the disagreement itself is the thing the reader has to know about.
    """
    if loc is None or loc.ci_low is None or loc.ci_high is None:
        return
    if pvalue != pvalue:
        return
    excludes_zero = not (loc.ci_low <= 0.0 <= loc.ci_high)
    if (pvalue < alpha) != excludes_zero:
        warnings.append(
            "순위검정 p값과 Hodges-Lehmann 신뢰구간의 판정이 서로 다릅니다 "
            "(p={:.4f}, {:.0f}% CI [{:.4g}, {:.4g}]). 동점(tie)이 있어 p값은 "
            "정규근사로, 신뢰구간은 정확 순위분포로 계산되기 때문입니다 — "
            "경계선 결과이므로 단정적으로 해석하지 마세요.".format(
                pvalue, loc.conf * 100, loc.ci_low, loc.ci_high))


def _check_computable(groups: List["Group"]) -> None:
    """Refuse magnitudes where the summary statistics stop being representable.

    Above |x| ~ 1e154 the sum of squares overflows, so the SD is ``inf`` and
    every downstream quantity silently collapses: t = diff/inf = -0.0, p = 1.000,
    Hedges' g = -0.000 ("negligible"), CI = [-inf, inf]. All of those are finite,
    so a NaN check does not see them, and the report confidently states the
    opposite of the truth. A refusal naming the fix is the only honest answer.
    """
    for g in groups:
        if g.n < 2:
            continue
        stats_ = (g.mean, g.sd)
        if any(v == v and not math.isfinite(v) for v in stats_):
            raise ValueError(
                f"그룹 '{g.label}'의 요약통계가 배정밀도 범위를 넘어갑니다 "
                f"(값의 크기가 약 1e154 이상). 단위를 바꾸거나(예: 원 → 백만원, "
                f"ng → mg) 자료 오류가 없는지 확인한 뒤 다시 실행하세요 — "
                f"이 상태로 계산하면 t=0, p=1.000 같은 무의미한 값이 나옵니다.")


def _finite(vals: Sequence[float], label: str) -> List[float]:
    """Coerce to float and reject non-finite values (NaN/inf) with a clear error."""
    out: List[float] = []
    for v in vals:
        f = float(v)
        if not math.isfinite(f):
            raise ValueError(
                f"group '{label}' contains a non-finite value (NaN or inf); "
                f"remove it before analysis")
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


#: Pre-specifying the test is not just a convenience. Choosing it from a
#: normality/variance pretest is itself a data-dependent decision: at small
#: unbalanced n Levene has little power, so Student's t gets selected on
#: heteroscedastic data exactly where it is anti-conservative (a simulated
#: type-I error of ~0.11 at n=(6,18) with SD=(3,1), against 0.05 for Welch).
#: ICH E9 wants the analysis fixed in the SAP, so the caller must be able to
#: say "Welch, always".
_PRESPECIFIED_NOTE = (
    "검정을 사전 지정({test})했으므로 정규성·등분산 검정 결과로 검정을 바꾸지 "
    "않았습니다. 가정 점검 결과는 참고용으로만 표시합니다.")

_AUTO_SELECTION_NOTE_OMNIBUS = (
    "검정을 자료에서 골랐습니다(사전 지정이 아님). 정규성·등분산 사전검정 결과에 "
    "따라 ANOVA / Welch-ANOVA / Kruskal-Wallis 중 하나를 선택했으므로, 사전 "
    "계획서에 검정을 못 박아야 한다면 이 결과를 그대로 쓰지 마세요.")

_AUTO_SELECTION_NOTE_PAIRED = (
    "검정을 자료에서 골랐습니다(사전 지정이 아님). 차이값의 정규성 검정 결과에 "
    "따라 대응 t-검정과 Wilcoxon 부호순위검정 중 하나를 선택했습니다.")

_AUTO_SELECTION_NOTE = (
    "검정을 자료에서 골랐습니다(사전 지정이 아님). 정규성·등분산 사전검정에 "
    "조건부로 검정을 고르면 소표본·불균형 설계에서 1종 오류가 커질 수 있습니다 "
    "— 사전 지정이 필요하면 --test welch 처럼 직접 지정하세요.")


def _forced_two_group(a: Group, b: Group, alpha: float, test: str
                      ) -> Tuple[str, float, Optional[float], float, List, str,
                                 Optional[float], Optional[Tuple[float, float]]]:
    """Run the test the caller pre-specified, with no pretest branching."""
    if test == "mannwhitney":
        res = tests_stat.mann_whitney_u(a.values, b.values)
        if res.method != "exact" and min(a.n, b.n) < 8:
            _SMALL_N_APPROX.append(min(a.n, b.n))
        method_note = ("exact permutation p-value" if res.method == "exact"
                       else "normal approximation")
        es = [effects.rank_biserial(a.values, b.values),
              effects.cliffs_delta(a.values, b.values)]
        reason = ("pre-specified Mann-Whitney U test [" + method_note + "]")
        return ("Mann-Whitney U test", res.statistic, None, res.pvalue, es,
                reason, None, None)
    if test == "student":
        res = tests_stat.students_t(a.values, b.values)
        label = "Student's t-test"
    else:
        res = tests_stat.welch_t(a.values, b.values)
        label = "Welch's t-test"
    es = [effects.cohens_d(a.values, b.values, hedges=True, ci=1.0 - alpha)]
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
    return (label, res.statistic, res.df, res.pvalue, es,
            f"pre-specified {label}", diff, ci)


#: Collected by _auto_two_group / _forced_two_group and drained by analyze();
#: a module-level list keeps the tuple-returning helper signatures unchanged.
_SMALL_N_APPROX: List[int] = []


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
        es = [effects.cohens_d(a.values, b.values, hedges=True,
                               ci=1.0 - alpha)]
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
    if res.method != "exact" and min(a.n, b.n) < 8:
        _SMALL_N_APPROX.append(min(a.n, b.n))
    non_normal = [nc.label for nc in norm if nc.normal is False]
    undetermined = [nc.label for nc in norm if nc.normal is None]
    method_note = ("exact permutation p-value" if res.method == "exact"
                   else "normal approximation")
    parts = []
    if non_normal:
        parts.append("normality rejected for " + ", ".join(non_normal))
    if undetermined:
        # n<3 or a degenerate group: normality is *unknown*, not violated
        parts.append("normality undetermined for " + ", ".join(undetermined))
    reason = ("; ".join(parts) + " -> Mann-Whitney U test [" +
              method_note + "]")
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


#: Above this many arms the post-hoc table is both computationally silly and
#: statistically meaningless (1000 groups = 499,500 Holm-corrected comparisons
#: and a 79 MB report). Refuse rather than appear to hang.
MAX_POSTHOC_GROUPS = 60


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
        n1, n2 = a.n, b.n
        v1, v2 = tests_stat.variance(a.values), tests_stat.variance(b.values)
        if kind in ("student", "welch"):
            if kind == "student":
                r = tests_stat.students_t(a.values, b.values)
                name = "Student's t"
                sp2 = ((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2)
                se = math.sqrt(sp2 * (1.0 / n1 + 1.0 / n2))
            else:
                r = tests_stat.welch_t(a.values, b.values)
                name = "Welch's t"
                se = math.sqrt(v1 / n1 + v2 / n2)
            es = effects.cohens_d(a.values, b.values, hedges=True,
                                  ci=1.0 - alpha)
            diff = a.mean - b.mean
            tcrit = t_ppf(1 - alpha / 2, r.df)
            dci = (diff - tcrit * se, diff + tcrit * se)
            dlabel = "mean difference"
        else:
            r = tests_stat.mann_whitney_u(a.values, b.values)
            name = "Mann-Whitney U"
            es = effects.rank_biserial(a.values, b.values)
            loc = location_mod.hodges_lehmann_independent(
                a.values, b.values, conf=1.0 - alpha)
            diff = loc.estimate
            dci = ((loc.ci_low, loc.ci_high)
                   if loc.ci_low is not None and loc.ci_high is not None
                   else None)
            dlabel = "Hodges-Lehmann shift"
        meta.append((name, r.statistic, es.name, es.value, a.label, b.label,
                     diff, dci, dlabel, n1, n2))
        raw.append(r.pvalue)
    adj = _correct(raw, correction)
    out = []
    for k, (test, stat, ename, eval_, la, lb, diff, dci, dlabel,
            n1, n2) in enumerate(meta):
        out.append(PairwiseResult(la, lb, test, stat, raw[k], adj[k],
                                  ename, eval_, adj[k] < alpha,
                                  diff, dci, dlabel, n1, n2))
    return out


def _posthoc_ci_conflicts(pairwise: List[PairwiseResult]) -> List[str]:
    """Comparisons whose per-comparison interval disagrees with the adjusted p."""
    out = []
    for pw in pairwise:
        if pw.diff_ci is None:
            continue
        lo, hi = pw.diff_ci
        if lo != lo or hi != hi:
            continue
        excludes_zero = not (lo <= 0.0 <= hi)
        if excludes_zero != pw.significant:
            out.append(f"{pw.a} vs {pw.b}")
    return out


def analyze(named_groups: Sequence[Tuple[str, Sequence[float]]],
            alpha: float = 0.05, alpha_norm: float = 0.05,
            posthoc: bool = True, correction: str = "holm",
            equivalence: Optional[EquivalenceSpec] = None,
            missing: Optional[Dict[str, int]] = None,
            test: str = "auto") -> AnalysisResult:
    """Run the full auto-selected group comparison and return an AnalysisResult.

    ``equivalence`` optionally adds a TOST / non-inferiority test on the mean
    difference (two groups only).  ``missing`` maps group label -> count of
    unusable source cells, for CONSORT-style reporting.
    """
    if correction not in ("holm", "bh"):
        raise ValueError("correction must be 'holm' or 'bh'")
    if test not in ("auto", "student", "welch", "mannwhitney"):
        raise ValueError(
            "test must be 'auto', 'student', 'welch' or 'mannwhitney'")
    miss = missing or {}
    groups = [Group(_safe_label(label), _finite(vals, label),
                    int(miss.get(str(label), 0)))
              for label, vals in named_groups]
    if len(groups) < 2:
        raise ValueError("need at least 2 groups to compare")
    for g in groups:
        if g.n < 2:
            raise ValueError(f"group '{g.label}' has fewer than 2 observations")

    _check_computable(groups)
    warnings: List[str] = []
    _SMALL_N_APPROX.clear()
    spec = equivalence or EquivalenceSpec()
    if spec.active and len(groups) != 2:
        warnings.append(
            "등가(TOST)/비열등성 검정은 두 그룹 비교에서만 정의됩니다 "
            f"(지금 {len(groups)}그룹) — 건너뜁니다.")
        spec = EquivalenceSpec()
    norm = [_normality_check(g, alpha_norm) for g in groups]
    for nc in norm:
        if nc.note:
            warnings.append(f"[{nc.label}] {nc.note}")

    lev = tests_stat.levene([g.values for g in groups])

    if len(groups) == 2:
        if test == "auto":
            (name, stat, df, p, es, reason, diff, ci) = _auto_two_group(
                groups[0], groups[1], alpha, alpha_norm, norm, lev)
            warnings.append(_AUTO_SELECTION_NOTE)
        else:
            (name, stat, df, p, es, reason, diff, ci) = _forced_two_group(
                groups[0], groups[1], alpha, test)
            warnings.append(_PRESPECIFIED_NOTE.format(test=name))
        if _SMALL_N_APPROX:
            warnings.append(
                f"표본이 작은데(최소 그룹 n={min(_SMALL_N_APPROX)}) 동점 때문에 "
                f"정확검정을 쓸 수 없어 정규근사 p값을 보고했습니다. 소표본에서 "
                f"정규근사는 정확검정보다 p값을 작게 만들 수 있으니 경계선 결과는 "
                f"신뢰하지 마세요.")
        loc = None
        if name == "Mann-Whitney U test":
            loc = location_mod.hodges_lehmann_independent(
                groups[0].values, groups[1].values, conf=1.0 - alpha)
            _warn_ci_p_disagreement(loc, p, alpha, warnings)
        if p != p or not math.isfinite(stat):
            warnings.append(
                "검정통계량 또는 p값을 계산할 수 없습니다(NaN). 값의 크기가 "
                "극단적이거나 분산이 0인 그룹이 있어 이 결과는 해석할 수 "
                "없습니다 — '유의하지 않음'을 결론으로 쓰지 마세요.")
        eq = _run_equivalence(groups[0].values, groups[1].values, name, alpha,
                              spec, warnings)
        return AnalysisResult(
            groups=groups, alpha=alpha, alpha_norm=alpha_norm, normality=norm,
            levene=lev, test_name=name, statistic=stat, df=df, df2=None, pvalue=p,
            significant=p < alpha, effects=es, mean_diff=diff, mean_diff_ci=ci,
            warnings=warnings, reason=reason, correction=correction, location=loc,
            equivalence=eq)

    # 3+ groups
    warnings.append(_AUTO_SELECTION_NOTE_OMNIBUS)
    if test != "auto":
        warnings.append(
            f"--test {test} 는 독립 2그룹 비교에만 적용됩니다 "
            f"(지금 {len(groups)}그룹) — 자동 선택을 사용했습니다.")
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

    if (p != p or (stat is not None and not math.isfinite(stat))):
        warnings.append(
            "검정통계량 또는 p값을 계산할 수 없습니다(NaN). 값의 크기가 극단적이거나 "
            "분산이 0인 그룹이 있어 이 결과는 해석할 수 없습니다 — '유의하지 않음'을 "
            "결론으로 쓰지 마세요.")
    pairwise: List[PairwiseResult] = []
    if posthoc and p == p and p < alpha:
        if len(groups) > MAX_POSTHOC_GROUPS:
            n_pairs = len(groups) * (len(groups) - 1) // 2
            warnings.append(
                f"그룹이 {len(groups)}개라 사후검정 쌍이 {n_pairs:,}개가 되어 "
                f"생략했습니다 (상한 {MAX_POSTHOC_GROUPS}그룹). 이 정도 수의 "
                f"쌍별 비교는 다중비교 보정 후 사실상 아무것도 검출하지 못하며, "
                f"보고할 표도 아닙니다 — 관심 있는 그룹만 골라 다시 실행하세요.")
        else:
            pairwise = _pairwise(groups, pair_kind, alpha, correction)
            conflicts = _posthoc_ci_conflicts(pairwise)
            if conflicts:
                warnings.append(
                    "사후검정의 신뢰구간은 **비교 1건 기준(비보정)** 이고 별표는 "
                    "다중비교 보정 후 판정이라, 다음 비교에서 둘의 결론이 "
                    "다릅니다: " + ", ".join(conflicts) +
                    ". 보정된 결론(별표/p(adj))을 따르고, 구간은 효과 크기의 "
                    "규모를 읽는 용도로만 쓰세요 — 동시신뢰구간이 아닙니다.")

    return AnalysisResult(
        groups=groups, alpha=alpha, alpha_norm=alpha_norm, normality=norm,
        levene=lev, test_name=name, statistic=stat, df=df, df2=df2, pvalue=p,
        significant=(p == p and p < alpha), effects=es, pairwise=pairwise,
        warnings=warnings, reason=reason, correction=correction)


def analyze_paired(cond_a: Tuple[str, Sequence[float]],
                   cond_b: Tuple[str, Sequence[float]],
                   alpha: float = 0.05, alpha_norm: float = 0.05,
                   equivalence: Optional[EquivalenceSpec] = None,
                   missing: Optional[Dict[str, int]] = None
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

    miss = missing or {}
    ga = Group(_safe_label(la), va, int(miss.get(str(la), 0)))
    gb = Group(_safe_label(lb), vb, int(miss.get(str(lb), 0)))
    _check_computable([ga, gb])
    diffs = [x - y for x, y in zip(va, vb)]
    warnings: List[str] = []
    spec = equivalence or EquivalenceSpec()

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
    warnings.append(_AUTO_SELECTION_NOTE_PAIRED)

    if dnc.normal:
        res = paired_mod.paired_t(va, vb)
        es = [effects.cohens_dz(va, vb, ci=1.0 - alpha)]
        warnings.append(effects.DZ_MAGNITUDE_CAVEAT)
        tcrit = t_ppf(1 - alpha / 2, res.df)
        se = res.sd_diff / math.sqrt(res.n)
        ci = (res.mean_diff - tcrit * se, res.mean_diff + tcrit * se)
        reason = ("differences ~normal (Shapiro p={:.3f}) "
                  "-> paired-samples t-test").format(dnc.pvalue)
        eq = _run_equivalence(va, vb, "Paired t-test", alpha, spec, warnings)
        return AnalysisResult(
            groups=[ga, gb], alpha=alpha, alpha_norm=alpha_norm, normality=[],
            levene=None, test_name="Paired t-test", statistic=res.statistic,
            df=res.df, df2=None, pvalue=res.pvalue,
            significant=(res.pvalue == res.pvalue and res.pvalue < alpha),
            effects=es, mean_diff=res.mean_diff, mean_diff_ci=ci,
            warnings=warnings, reason=reason, paired=True, diff_normality=dnc,
            n_pairs=res.n, equivalence=eq)

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
    _warn_ci_p_disagreement(loc, res.pvalue, alpha, warnings)
    eq = _run_equivalence(va, vb, "Wilcoxon signed-rank test", alpha, spec,
                          warnings)
    return AnalysisResult(
        groups=[ga, gb], alpha=alpha, alpha_norm=alpha_norm, normality=[],
        levene=None, test_name="Wilcoxon signed-rank test",
        statistic=res.statistic, df=None, df2=None, pvalue=res.pvalue,
        significant=(res.pvalue < alpha), effects=es, warnings=warnings,
        reason=reason, paired=True, diff_normality=dnc,
        n_pairs=res.n_nonzero + res.n_zero, n_zero_diff=res.n_zero, location=loc,
        equivalence=eq)
