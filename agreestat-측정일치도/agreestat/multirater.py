"""Agreement among **three or more** raters / methods.

The two-method machinery in :mod:`agreestat.agreement` and
:mod:`agreestat.categorical` covers "new device vs reference". Clinical work
just as often has *k* readers grading the same images, or three assays run on
the same aliquots. This module covers that case:

* **Continuous** — the full ICC family (Shrout & Fleiss 1979; McGraw & Wong
  1996): ICC(1,1)/ICC(1,k), ICC(2,1)/ICC(2,k), ICC(3,1)/ICC(3,k) with exact
  F-based confidence intervals, a systematic rater-effect F test, the standard
  error of measurement (SEM), minimal detectable change (MDC95), the
  within-subject SD and repeatability coefficient, and a pairwise
  Bland-Altman/CCC table.
* **Categorical** — Fleiss' kappa (overall and per category), Gwet's AC1 for
  multiple raters, Krippendorff's alpha (nominal / ordinal), overall percent
  agreement, and the pairwise Cohen's kappa matrix (Light's kappa = its mean).
  Confidence intervals come from a subject-level (nonparametric) bootstrap, so
  they stay honest when the chance-corrected coefficients are skewed.

Pure standard library.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .agreement import (
    ICCResult,
    MeanSquares,
    ccc as _ccc,
    icc as _icc_two_way,
    interpret_icc,
    mean as _mean,
    two_way_ms,
    variance as _variance,
)
from .categorical import (
    _delta2,
    confusion_matrix,
    interpret_kappa,
    kappa as _kappa,
)
from .special import f_ppf, f_sf, norm_ppf, norm_sf

__all__ = [
    "RaterDescriptive", "ICCFamily", "PairwiseContinuous", "MultiContinuous",
    "FleissEntry", "MultiCategorical",
    "icc_family", "pairwise_continuous", "multi_continuous",
    "fleiss_kappa", "gwet_ac1_multi", "krippendorff_alpha_multi",
    "multi_categorical",
]


# ==========================================================================
# Continuous: the ICC family
# ==========================================================================
@dataclass
class RaterDescriptive:
    """Per-rater summary plus its average deviation from the subject mean."""

    name: str
    mean: float
    sd: float
    bias: float          # mean_i(x_ij - subject_mean_i): systematic rater offset


@dataclass
class ICCFamily:
    """Every ICC form for an n-subjects x k-raters table, with CIs."""

    n: int
    k: int
    ms: MeanSquares
    msw: float                      # one-way within-subject mean square
    single: List[ICCResult]         # ICC(1,1), ICC(2,1), ICC(3,1)
    average: List[ICCResult]        # ICC(1,k), ICC(2,k), ICC(3,k)
    rater_f: float                  # MSC / MSE: systematic differences between raters
    rater_df1: float
    rater_df2: float
    rater_p: float
    sem: float                      # sqrt(MSW) — SEM, absolute agreement
    sem_consistency: float          # sqrt(MSE) — SEM after removing rater offsets
    mdc95: float                    # 1.96*sqrt(2)*SEM — minimal detectable change
    sw: float                       # sqrt(MSW) — within-subject SD (Bland-Altman)
    rc: float                       # 2.77*sw — repeatability coefficient


def _spearman_brown(r: float, k: int) -> float:
    """Single-measure reliability -> k-measure reliability.

    The transform is only monotone (and only meaningful) while
    ``1 + (k-1)r > 0``; below that pole it flips sign and can return a value
    above 1 or a reversed interval, so we return NaN instead. This matters for
    ICC(2,1), whose Satterthwaite bounds have no ``-1/(k-1)`` floor.
    """
    if not math.isfinite(r):
        return float("nan")
    denom = 1.0 + (k - 1) * r
    if denom <= 0.0:
        return float("nan")
    return k * r / denom


def _one_way_msw(rows: Sequence[Sequence[float]]) -> float:
    """Within-subject mean square of the one-way random-effects model."""
    n = len(rows)
    k = len(rows[0])
    ssw = 0.0
    for row in rows:
        m = sum(row) / k
        ssw += sum((v - m) ** 2 for v in row)
    return ssw / (n * (k - 1))


def _f_ci(f: float, df1: float, df2: float, k: int, alpha: float
          ) -> Tuple[float, float]:
    """Shrout-Fleiss F-based CI for a single-measure ICC."""
    if not math.isfinite(f) or f <= 0.0:
        return float("nan"), float("nan")
    fl = f / f_ppf(1.0 - alpha / 2.0, df1, df2)
    fu = f * f_ppf(1.0 - alpha / 2.0, df2, df1)
    if not (math.isfinite(fl) and math.isfinite(fu)):
        return float("nan"), float("nan")
    lo = (fl - 1.0) / (fl + (k - 1))
    hi = (fu - 1.0) / (fu + (k - 1))
    return lo, hi


def icc_family(rows: Sequence[Sequence[float]], alpha: float = 0.05
               ) -> ICCFamily:
    """Compute ICC(1,1)/(1,k), ICC(2,1)/(2,k), ICC(3,1)/(3,k) with CIs.

    ``rows`` is an n-subjects x k-raters table with no missing cells.
    """
    ms = two_way_ms(rows)
    n, k = ms.n, ms.k
    msw = _one_way_msw(rows)
    msr, msc, mse = ms.msr, ms.msc, ms.mse

    # ---- ICC(1,1) / ICC(1,k): one-way random ----
    f1 = msr / msw if msw > 0 else float("inf")
    df1_1, df1_2 = n - 1, n * (k - 1)
    denom1 = msr + (k - 1) * msw
    v11 = (msr - msw) / denom1 if denom1 != 0 else float("nan")
    lo11, hi11 = _f_ci(f1, df1_1, df1_2, k, alpha)
    p1 = f_sf(f1, df1_1, df1_2) if math.isfinite(f1) else 0.0
    icc11 = ICCResult("ICC(1,1)", "one-way random, absolute agreement, "
                      "single measures", v11, lo11, hi11, f1,
                      float(df1_1), float(df1_2), p1, interpret_icc(v11))

    # ---- ICC(2,1) and ICC(3,1) from the shared two-way implementation ----
    icc21, icc31, _ = _icc_two_way(rows, alpha=alpha)

    # ---- Average-measures forms (Spearman-Brown of the single-measure CI) ----
    def avg(src: ICCResult, model: str, desc: str) -> ICCResult:
        v = _spearman_brown(src.value, k)
        lo = _spearman_brown(src.ci_lower, k)
        hi = _spearman_brown(src.ci_upper, k)
        # Refuse to print an interval that is reversed or does not bracket the
        # point estimate (same guard the two-way ICC already applies).
        if not (math.isfinite(lo) and math.isfinite(hi) and lo <= hi
                and math.isfinite(v) and lo - 1e-9 <= v <= hi + 1e-9):
            lo = hi = float("nan")
        return ICCResult(model, desc, v, lo, hi,
                         src.f, src.df1, src.df2, src.pvalue, interpret_icc(v))

    icc1k = avg(icc11, f"ICC(1,{k})",
                "one-way random, absolute agreement, average measures")
    icc2k = avg(icc21, f"ICC(2,{k})",
                "two-way random, absolute agreement, average measures")
    icc3k = avg(icc31, f"ICC(3,{k})",
                "two-way mixed, consistency, average measures")

    # ---- Systematic rater effect: MSC / MSE ----
    if mse > 0:
        rf = msc / mse
        rp = f_sf(rf, k - 1, (n - 1) * (k - 1))
    elif msc > 0:
        rf, rp = float("inf"), 0.0
    else:
        rf, rp = float("nan"), float("nan")

    # The standard error of measurement that belongs next to ICC(2,1) (absolute
    # agreement) is sqrt(MSW), NOT sqrt(MSE): MSW = MSE + (MSC - MSE)/n, so only
    # MSW carries the systematic between-rater offsets. Using sqrt(MSE) would
    # report MDC95 = 0 for raters that disagree by a constant 10 units.
    sw = math.sqrt(msw) if msw >= 0 else float("nan")
    sem_c = math.sqrt(mse) if mse >= 0 else float("nan")
    return ICCFamily(
        n=n, k=k, ms=ms, msw=msw,
        single=[icc11, icc21, icc31],
        average=[icc1k, icc2k, icc3k],
        rater_f=rf, rater_df1=float(k - 1), rater_df2=float((n - 1) * (k - 1)),
        rater_p=rp,
        sem=sw, sem_consistency=sem_c,
        mdc95=1.959963984540054 * math.sqrt(2.0) * sw,
        sw=sw, rc=2.77 * sw,
    )


@dataclass
class PairwiseContinuous:
    """One rater-pair row of the pairwise agreement table."""

    name_a: str
    name_b: str
    mean_diff: float
    sd_diff: float
    loa_lower: float
    loa_upper: float
    ccc: float


def pairwise_continuous(names: Sequence[str], rows: Sequence[Sequence[float]],
                        alpha: float = 0.05) -> List[PairwiseContinuous]:
    """Bland-Altman bias/LoA and Lin's CCC for every rater pair."""
    k = len(names)
    z = norm_ppf(1.0 - alpha / 2.0)
    out: List[PairwiseContinuous] = []
    for i in range(k):
        for j in range(i + 1, k):
            a = [r[i] for r in rows]
            b = [r[j] for r in rows]
            d = [x - y for x, y in zip(a, b)]
            md = _mean(d)
            sd = math.sqrt(_variance(d)) if len(d) > 1 else float("nan")
            try:
                cc = _ccc(a, b, alpha=alpha).value
            except (ValueError, ZeroDivisionError):
                cc = float("nan")
            out.append(PairwiseContinuous(
                names[i], names[j], md, sd, md - z * sd, md + z * sd, cc))
    return out


@dataclass
class MultiContinuous:
    """Complete continuous multi-rater result."""

    names: List[str]
    n: int
    k: int
    alpha: float
    dropped: int
    descriptives: List[RaterDescriptive]
    icc: ICCFamily
    pairwise: List[PairwiseContinuous]
    reported: str = "ICC(2,1)"
    warnings: List[str] = field(default_factory=list)


def multi_continuous(names: Sequence[str], rows: Sequence[Sequence[float]],
                     alpha: float = 0.05, dropped: int = 0,
                     extra_warnings: Optional[Sequence[str]] = None
                     ) -> MultiContinuous:
    """Run every continuous multi-rater statistic on a complete n x k table."""
    names = list(names)
    k = len(names)
    if k < 3:
        raise ValueError("multi-rater analysis needs at least 3 raters")
    rows = [[float(v) for v in r] for r in rows]
    n = len(rows)
    if n < 2:
        raise ValueError("need at least 2 subjects for multi-rater ICC")
    if any(len(r) != k for r in rows):
        raise ValueError("every subject must have a value for every rater")
    if any(not math.isfinite(v) for r in rows for v in r):
        raise ValueError("non-finite value in the rater table")

    warnings: List[str] = list(extra_warnings or [])
    if n < 10:
        warnings.append(f"피험자 수가 적습니다(n={n}). ICC 신뢰구간이 매우 넓어집니다 "
                        "— Koo & Li(2016)는 ICC 연구에 최소 30명을 권합니다.")
    elif n < 30:
        warnings.append(f"피험자 수 n={n} 입니다. 신뢰성 연구 권장 표본(≥30)보다 "
                        "적어 ICC 신뢰구간이 넓을 수 있습니다.")

    subject_means = [sum(r) / k for r in rows]
    descs: List[RaterDescriptive] = []
    for j, nm in enumerate(names):
        col = [r[j] for r in rows]
        sd = math.sqrt(_variance(col)) if n > 1 else float("nan")
        bias = _mean([col[i] - subject_means[i] for i in range(n)])
        descs.append(RaterDescriptive(nm, _mean(col), sd, bias))

    if all(v == rows[0][0] for r in rows for v in r):
        warnings.append("모든 값이 동일합니다(총분산 0). ICC는 정의되지 않습니다.")
    if n < 4:
        warnings.append(
            f"대상이 {n}명뿐이라 ICC는 사실상 추정 불가능합니다 — 자유도가 거의 "
            "없어 점추정·신뢰구간·SEM 모두 해석하지 마세요.")

    fam = icc_family(rows, alpha=alpha)
    if fam.msw == 0.0:
        warnings.append(
            "모든 평가자가 대상마다 완전히 같은 값을 냈습니다(개체내 분산 0). "
            "SEM·MDC95가 0으로 나오지만 이는 '오차가 없다'는 뜻이 아니라 "
            "이 자료로는 측정오차를 추정할 수 없다는 뜻입니다.")
    if math.isfinite(fam.rater_p) and fam.rater_p < 0.05:
        worst = max(descs, key=lambda d: abs(d.bias))
        warnings.append(
            f"평가자 간 계통적 차이가 유의합니다 (F={fam.rater_f:.2f}, "
            f"p={fam.rater_p:.4f}) — 예: '{worst.name}'의 평균 편차 "
            f"{worst.bias:+.3f}. 절대일치도 ICC(2,1)가 일관성 ICC(3,1)보다 "
            "낮아지며, 보정(calibration)이 필요할 수 있습니다.")
    pw = pairwise_continuous(names, rows, alpha=alpha)
    return MultiContinuous(names, n, k, alpha, dropped, descs, fam, pw,
                           warnings=warnings)


# ==========================================================================
# Categorical: Fleiss' kappa, Gwet AC1, Krippendorff alpha (m raters)
# ==========================================================================
def _count_table(ratings: Sequence[Sequence[str]], categories: Sequence[str]
                 ) -> List[List[int]]:
    """n_ij = how many raters put subject i in category j (missing = '')."""
    index = {c: j for j, c in enumerate(categories)}
    q = len(categories)
    out: List[List[int]] = []
    for row in ratings:
        counts = [0] * q
        for lab in row:
            if lab == "":
                continue
            j = index.get(lab)
            if j is None:
                raise ValueError(f"라벨 '{lab}' 이(가) 범주 목록에 없습니다")
            counts[j] += 1
        out.append(counts)
    return out


@dataclass
class FleissEntry:
    """Per-category Fleiss kappa (category j vs. the rest)."""

    category: str
    proportion: float
    kappa: float
    se: float
    z: float
    pvalue: float


def fleiss_kappa(counts: Sequence[Sequence[int]]
                 ) -> Tuple[float, float, float, float, float, List[float]]:
    """Fleiss' kappa from an n x q table of per-subject category counts.

    Every subject must be rated by the same number ``m >= 2`` of raters.
    Returns ``(kappa, p_bar, pe_bar, se_h0, mean_m, p_j)``.
    """
    n = len(counts)
    if n < 1:
        raise ValueError("need at least 1 subject")
    ms = {sum(c) for c in counts}
    if len(ms) != 1:
        raise ValueError("Fleiss' kappa needs the same number of raters for "
                         "every subject")
    m = ms.pop()
    if m < 2:
        raise ValueError("Fleiss' kappa needs at least 2 raters per subject")

    q = len(counts[0])
    total = float(n * m)
    p_j = [sum(c[j] for c in counts) / total for j in range(q)]
    p_i = [(sum(v * v for v in c) - m) / float(m * (m - 1)) for c in counts]
    p_bar = sum(p_i) / n
    pe = sum(v * v for v in p_j)
    # P_e == 1 means every rating fell in one category, which forces P_bar == 1
    # too: the raters agreed perfectly. kappa = (1-Pe)/(1-Pe) -> 1 by continuity,
    # so report 1 rather than 0/0 (dropping it would truncate bootstrap CIs at
    # exactly the perfect-agreement end).
    kap = (p_bar - pe) / (1.0 - pe) if pe < 1.0 else 1.0

    # Fleiss (1971) standard error under H0: kappa = 0, in the form used by
    # R irr::kappam.fleiss:  var = 2/(N m(m-1)) * [S^2 - sum p_j q_j (q_j-p_j)]/S^2
    # with S = sum p_j q_j = 1 - Pe.  That bracket expands to Pe + Pe^2 - 2*sum p_j^3
    # (no dependence on m).
    p3 = sum(v ** 3 for v in p_j)
    inner = pe + pe * pe - 2.0 * p3
    if pe < 1.0 and inner > 0 and n * m * (m - 1) > 0:
        se = math.sqrt(2.0 * inner) / ((1.0 - pe) * math.sqrt(n * m * (m - 1)))
    else:
        se = float("nan")
    return kap, p_bar, pe, se, float(m), p_j


def fleiss_per_category(counts: Sequence[Sequence[int]],
                        categories: Sequence[str]) -> List[FleissEntry]:
    """Category-specific Fleiss kappa (Fleiss, Levin & Paik 2003, §18.2)."""
    n = len(counts)
    m = sum(counts[0])
    total = float(n * m)
    q = len(categories)
    se = (math.sqrt(2.0 / (n * m * (m - 1)))
          if n * m * (m - 1) > 0 else float("nan"))
    out: List[FleissEntry] = []
    for j in range(q):
        pj = sum(c[j] for c in counts) / total
        denom = n * m * (m - 1) * pj * (1.0 - pj)
        if denom > 0:
            num = sum(c[j] * (m - c[j]) for c in counts)
            kj = 1.0 - num / denom
        else:
            kj = float("nan")
        z = kj / se if (math.isfinite(kj) and math.isfinite(se) and se > 0) \
            else float("nan")
        p = 2.0 * norm_sf(abs(z)) if math.isfinite(z) else float("nan")
        out.append(FleissEntry(categories[j], pj, kj, se, z, p))
    return out


def gwet_ac1_multi(counts: Sequence[Sequence[int]]
                   ) -> Tuple[float, float, float]:
    """Gwet's AC1 for multiple raters. Returns ``(ac1, p_a, p_e)``.

    Unlike kappa, the chance-agreement term does not collapse when one category
    dominates, so AC1 avoids the kappa "prevalence paradox".
    """
    n = len(counts)
    q = len(counts[0]) if n else 0
    if q < 2:
        return float("nan"), float("nan"), float("nan")
    # Gwet: p_a averages over subjects with >= 2 ratings, but pi_k averages the
    # rating distribution over *every* subject that was rated at all.
    pa_terms: List[float] = []
    pi = [0.0] * q
    rated = 0
    for c in counts:
        m = sum(c)
        if m < 1:
            continue
        rated += 1
        for j in range(q):
            pi[j] += c[j] / float(m)
        if m >= 2:
            pa_terms.append(sum(v * (v - 1) for v in c) / float(m * (m - 1)))
    if not pa_terms or rated == 0:
        return float("nan"), float("nan"), float("nan")
    pa = sum(pa_terms) / len(pa_terms)
    pi = [v / rated for v in pi]
    pe = sum(v * (1.0 - v) for v in pi) / (q - 1)
    ac1 = (pa - pe) / (1.0 - pe) if pe < 1.0 else float("nan")
    return ac1, pa, pe


def krippendorff_alpha_multi(counts: Sequence[Sequence[int]],
                             categories: Sequence[str],
                             metric: str = "nominal") -> float:
    """Krippendorff's alpha for any number of raters, unbalanced allowed.

    Units with fewer than 2 ratings contribute nothing (Krippendorff's own
    rule), so this tolerates missing ratings without listwise deletion.
    """
    q = len(categories)
    if q < 2:
        return float("nan")
    o = [[0.0] * q for _ in range(q)]
    for c in counts:
        m = sum(c)
        if m < 2:
            continue
        for a in range(q):
            if c[a] == 0:
                continue
            for b in range(q):
                val = c[a] * c[b] - (c[a] if a == b else 0)
                if val:
                    o[a][b] += val / float(m - 1)
    return _alpha_from_coincidence(o, categories, metric)


def _alpha_from_coincidence(o: Sequence[Sequence[float]],
                            categories: Sequence[str], metric: str) -> float:
    """Krippendorff's alpha given an already-built coincidence matrix."""
    q = len(categories)
    nc = [sum(o[i]) for i in range(q)]
    total = sum(nc)
    if total < 2:
        return float("nan")
    d2 = _delta2(categories, metric, nc)
    do = sum(o[i][j] * d2[i][j] for i in range(q) for j in range(q)) / total
    de = sum(nc[i] * nc[j] * d2[i][j]
             for i in range(q) for j in range(q) if i != j) / (total * (total - 1))
    if de <= 0.0:
        # Every rating in one category: observed disagreement is 0 and so is the
        # expected disagreement. The continuous limit is perfect reliability.
        return 1.0 if do == 0.0 else float("nan")
    return 1.0 - do / de


# Building one pair's kappa costs O(q^2) (confusion + weight matrices) and there
# are k(k-1)/2 pairs, so k=100 raters x q=200 categories is ~200 M cell writes —
# minutes of CPU from a 30 KB file. Above this budget we skip the pairwise table
# and say so instead of grinding.
_PAIRWISE_WORK_CAP = 20_000_000


def pairwise_kappa(names: Sequence[str], ratings: Sequence[Sequence[str]],
                   categories: Sequence[str], weights: str = "unweighted"
                   ) -> List[Tuple[str, str, float, int]]:
    """Cohen's kappa for every rater pair, using their complete cases.

    Returns an empty list when the k x k x q x q work would be excessive; the
    caller turns that into an explicit warning.
    """
    k = len(names)
    q = max(1, len(categories))
    if k * (k - 1) // 2 * q * q > _PAIRWISE_WORK_CAP:
        return []
    out: List[Tuple[str, str, float, int]] = []
    for i in range(k):
        for j in range(i + 1, k):
            a = [r[i] for r in ratings]
            b = [r[j] for r in ratings]
            pairs = [(x, y) for x, y in zip(a, b) if x != "" and y != ""]
            if len(pairs) < 2:
                out.append((names[i], names[j], float("nan"), len(pairs)))
                continue
            cm = confusion_matrix([p[0] for p in pairs], [p[1] for p in pairs],
                                  categories)
            try:
                kv = _kappa(cm, weights=weights).value
            except (ValueError, ZeroDivisionError):
                kv = float("nan")
            out.append((names[i], names[j], kv, len(pairs)))
    return out


@dataclass
class MultiCategorical:
    """Complete categorical multi-rater result."""

    names: List[str]
    categories: List[str]
    n: int                     # subjects used for Fleiss (complete cases)
    m: int                     # raters per subject
    n_alpha: int               # units contributing to Krippendorff's alpha
    dropped: int
    alpha_level: float
    weights: str
    ordinal: bool
    percent_agreement: float
    fleiss: float
    fleiss_ci: Tuple[float, float]
    fleiss_se_h0: float
    fleiss_z: float
    fleiss_p: float
    pe: float
    per_category: List[FleissEntry]
    ac1: float
    ac1_ci: Tuple[float, float]
    kalpha: float
    kalpha_ci: Tuple[float, float]
    kalpha_metric: str
    pairwise: List[Tuple[str, str, float, int]]
    light_kappa: float
    category_counts: List[int]
    bootstrap: int
    seed: int
    min_kappa: Optional[float] = None
    meets_threshold: Optional[bool] = None
    interpretation: str = ""
    warnings: List[str] = field(default_factory=list)


# Operation budget before we scale the resample count down. Because subjects are
# grouped into distinct rating *patterns* first, a resample costs O(n) index
# draws plus O(P*q^2) accumulation, where P (<= n) is the number of distinct
# patterns — so this only bites on genuinely huge inputs. A percentile CI needs a
# few hundred resamples to mean anything, so the count never drops below
# _MIN_BOOT.
_BOOT_WORK_CAP = 12_000_000
_MIN_BOOT = 200


def _pattern_groups(counts: Sequence[Sequence[int]], categories: Sequence[str],
                    metric: str, m: int
                    ) -> Tuple[List[int], List[Tuple[int, ...]], List[float],
                               List[List[List[float]]]]:
    """Collapse subjects to distinct count patterns for a cheap bootstrap.

    Every statistic here is a function of *sums over subjects*, so a resample
    only needs the multiplicity of each distinct pattern. Returns
    ``(pattern_index_per_subject, patterns, agree_term, coincidence)`` where
    ``agree_term`` is the subject's observed-agreement contribution
    ``sum_j n_j(n_j-1)/(m(m-1))`` (shared by Fleiss' P_i and Gwet's p_a) and
    ``coincidence`` is its Krippendorff coincidence block.
    """
    q = len(categories)
    index: Dict[Tuple[int, ...], int] = {}
    per_subject: List[int] = []
    patterns: List[Tuple[int, ...]] = []
    agree: List[float] = []
    coin: List[List[List[float]]] = []
    denom = float(m * (m - 1))
    for c in counts:
        key = tuple(c)
        p = index.get(key)
        if p is None:
            p = len(patterns)
            index[key] = p
            patterns.append(key)
            agree.append(sum(v * (v - 1) for v in c) / denom)
            block = [[0.0] * q for _ in range(q)]
            for a in range(q):
                if c[a] == 0:
                    continue
                for b in range(q):
                    val = c[a] * c[b] - (c[a] if a == b else 0)
                    if val:
                        block[a][b] = val / float(m - 1)
            coin.append(block)
        per_subject.append(p)
    return per_subject, patterns, agree, coin


def _bootstrap_alpha(all_counts: List[List[int]], categories: Sequence[str],
                     metric: str, b: int, seed: int, alpha: float
                     ) -> Tuple[float, float]:
    """Percentile bootstrap CI for Krippendorff's alpha.

    Resamples the *same* units the point estimate uses — every unit with >= 2
    ratings — so the interval and the estimate are the same quantity. (Fleiss'
    kappa and AC1 use complete cases, hence a separate resampling universe.)
    """
    units = [c for c in all_counts if sum(c) >= 2]
    n = len(units)
    nan2 = (float("nan"), float("nan"))
    if n < 2 or b < 1:
        return nan2
    q = len(categories)
    blocks: List[List[List[float]]] = []
    index: Dict[Tuple[int, ...], int] = {}
    pat_of: List[int] = []
    for c in units:
        key = tuple(c)
        p = index.get(key)
        if p is None:
            p = len(blocks)
            index[key] = p
            mu = sum(c)
            blk = [[0.0] * q for _ in range(q)]
            for a in range(q):
                if c[a] == 0:
                    continue
                for bcat in range(q):
                    val = c[a] * c[bcat] - (c[a] if a == bcat else 0)
                    if val:
                        blk[a][bcat] = val / float(mu - 1)
            blocks.append(blk)
        pat_of.append(p)

    eff = min(b, max(_MIN_BOOT,
                     _BOOT_WORK_CAP // max(1, n + len(blocks) * q * q)))
    rng = random.Random(seed ^ 0x5A17)
    rr = rng.randrange
    vals: List[float] = []
    for _ in range(eff):
        mult = [0] * len(blocks)
        for _i in range(n):
            mult[pat_of[rr(n)]] += 1
        o = [[0.0] * q for _ in range(q)]
        for p, w in enumerate(mult):
            if not w:
                continue
            blk = blocks[p]
            for a in range(q):
                row, orow = blk[a], o[a]
                for bcat in range(q):
                    if row[bcat]:
                        orow[bcat] += w * row[bcat]
        v = _alpha_from_coincidence(o, categories, metric)
        if math.isfinite(v):
            vals.append(v)
    if len(vals) < 20 or len(vals) < 0.5 * eff:
        return nan2
    vals.sort()
    return _quantile(vals, alpha / 2.0), _quantile(vals, 1.0 - alpha / 2.0)


def _bootstrap_cis(counts: List[List[int]], categories: Sequence[str],
                   metric: str, b: int, seed: int, alpha: float, m: int
                   ) -> Tuple[Tuple[float, float], Tuple[float, float],
                              Tuple[float, float], int]:
    """Subject-level percentile bootstrap CIs for (Fleiss, AC1, alpha).

    Returns ``(fleiss_ci, ac1_ci, alpha_ci, resamples)``; the alpha interval
    here is over the complete cases only and is used when every unit is
    complete (otherwise :func:`_bootstrap_alpha` supplies it).
    """
    n = len(counts)
    q = len(categories)
    nan2 = (float("nan"), float("nan"))
    if n < 2 or b < 1 or m < 2:
        return nan2, nan2, nan2, 0

    pat_of, patterns, agree, coin = _pattern_groups(counts, categories, metric, m)
    npat = len(patterns)
    per_resample = n + npat * max(1, q * q)
    eff = min(b, max(_MIN_BOOT, _BOOT_WORK_CAP // max(1, per_resample)))

    rng = random.Random(seed)
    total_ratings = float(n * m)
    fk: List[float] = []
    ac: List[float] = []
    ka: List[float] = []
    rr = rng.randrange
    for _ in range(eff):
        mult = [0] * npat
        for _i in range(n):
            mult[pat_of[rr(n)]] += 1

        s = [0] * q
        pa_sum = 0.0
        o = [[0.0] * q for _ in range(q)]
        for p, w in enumerate(mult):
            if not w:
                continue
            cnt = patterns[p]
            for j in range(q):
                s[j] += w * cnt[j]
            pa_sum += w * agree[p]
            blk = coin[p]
            for a in range(q):
                row = blk[a]
                orow = o[a]
                for bcat in range(q):
                    if row[bcat]:
                        orow[bcat] += w * row[bcat]

        p_j = [v / total_ratings for v in s]
        pa = pa_sum / n
        pe_f = sum(v * v for v in p_j)
        # Same continuity convention as fleiss_kappa(): a resample where every
        # rating fell in one category is perfect agreement (kappa = 1), not a
        # NaN to be discarded — discarding truncates the CI from above.
        fk.append(1.0 if pe_f >= 1.0 else (pa - pe_f) / (1.0 - pe_f))
        if q > 1:
            pe_g = sum(v * (1.0 - v) for v in p_j) / (q - 1)
            ac.append(1.0 if pe_g >= 1.0 else (pa - pe_g) / (1.0 - pe_g))
        av = _alpha_from_coincidence(o, categories, metric)
        if math.isfinite(av):
            ka.append(av)

    def pct(vals: List[float]) -> Tuple[float, float]:
        # Too many degenerate resamples (a constant table leaves the coefficient
        # undefined) would make a percentile CI meaningless, so require most of
        # them to have produced a finite value.
        if len(vals) < 20 or len(vals) < 0.5 * eff:
            return nan2
        vals = sorted(vals)
        return (_quantile(vals, alpha / 2.0), _quantile(vals, 1.0 - alpha / 2.0))

    return pct(fk), pct(ac), pct(ka), eff


def _quantile(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = p * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def multi_categorical(names: Sequence[str], ratings: Sequence[Sequence[str]],
                      categories: Sequence[str], alpha: float = 0.05,
                      ordinal: bool = False, weights: str = "unweighted",
                      dropped: int = 0, bootstrap: int = 2000,
                      seed: int = 20260716,
                      min_kappa: Optional[float] = None,
                      extra_warnings: Optional[Sequence[str]] = None
                      ) -> MultiCategorical:
    """Run every categorical multi-rater statistic.

    ``ratings`` is an n-subjects x m-raters table of labels; ``""`` marks a
    missing rating. Fleiss' kappa, AC1 and the percent agreement use complete
    cases; Krippendorff's alpha uses every unit with >= 2 ratings.
    """
    names = list(names)
    categories = list(categories)
    m = len(names)
    if m < 3:
        raise ValueError("multi-rater analysis needs at least 3 raters")
    if any(len(r) != m for r in ratings):
        raise ValueError("every subject row must have one entry per rater")
    if len(categories) < 2:
        raise ValueError(
            "관측된 범주가 1종뿐입니다 — 우연일치 보정 계수(Fleiss' kappa·"
            "Gwet AC1·Krippendorff alpha)는 서로 다른 범주가 2종 이상이어야 "
            "정의됩니다. 모든 평가자가 같은 범주만 매긴 자료입니다.")
    warnings: List[str] = list(extra_warnings or [])

    all_counts = _count_table(ratings, categories)
    complete = [c for c in all_counts if sum(c) == m]
    incomplete = len(all_counts) - len(complete)
    if incomplete:
        warnings.append(
            f"평가자 {m}명 중 일부만 평가한 대상 {incomplete}건은 Fleiss' kappa·"
            "AC1·전체일치율 계산에서 제외했습니다(완전자료 분석). "
            "Krippendorff's alpha 는 2명 이상 평가된 모든 대상을 사용합니다.")
    if len(complete) < 2:
        raise ValueError(
            f"모든 평가자가 평가한 대상이 {len(complete)}건뿐입니다 — "
            "Fleiss' kappa 계산에 최소 2건이 필요합니다.")

    n = len(complete)
    if n < 20:
        warnings.append(
            f"완전자료 대상이 적습니다(n={n}). 대상 수가 20건 미만이면 "
            "부트스트랩 백분위 신뢰구간이 불안정합니다 — CI 폭을 반드시 함께 "
            "보고하세요.")

    kap, p_bar, pe, se0, _mm, p_j = fleiss_kappa(complete)
    z = kap / se0 if (math.isfinite(kap) and math.isfinite(se0) and se0 > 0) \
        else float("nan")
    pval = 2.0 * norm_sf(abs(z)) if math.isfinite(z) else float("nan")
    per_cat = fleiss_per_category(complete, categories)
    ac1, _pa, _pe1 = gwet_ac1_multi(complete)

    metric = "ordinal" if ordinal else "nominal"
    n_alpha = sum(1 for c in all_counts if sum(c) >= 2)
    kalpha = krippendorff_alpha_multi(all_counts, categories, metric)

    fk_ci, ac_ci, ka_ci, eff_b = _bootstrap_cis(
        complete, categories, metric, bootstrap, seed, alpha, m)
    if incomplete:
        # The alpha point estimate uses every unit with >= 2 ratings, so its CI
        # must resample those same units — not just the complete cases.
        ka_ci = _bootstrap_alpha(all_counts, categories, metric, bootstrap,
                                 seed, alpha)
    if eff_b and eff_b < bootstrap:
        warnings.append(
            f"부트스트랩 반복을 {bootstrap}회에서 {eff_b}회로 줄였습니다 "
            "(자료 크기 대비 계산량 상한 — --bootstrap 으로 더 늘릴 수는 "
            f"없습니다). {eff_b}회는 백분위 CI에 충분하지만, 소수점 셋째 자리까지 "
            "안정적인 CI가 필요하면 대상 수나 범주 수를 줄여 다시 실행하세요.")

    pw = pairwise_kappa(names, ratings, categories, weights=weights)
    if not pw and m >= 2:
        warnings.append(
            f"평가자 {m}명 × 범주 {len(categories)}종은 쌍별 kappa 표 "
            f"({m * (m - 1) // 2}쌍)를 만들기에 계산량이 지나치게 큽니다 — "
            "쌍별 표를 생략했습니다. 평가자나 범주를 줄여 다시 실행하세요.")
    finite_pw = [v for _, _, v, _ in pw if math.isfinite(v)]
    light = sum(finite_pw) / len(finite_pw) if finite_pw else float("nan")

    cat_counts = [sum(c[j] for c in complete) for j in range(len(categories))]
    total_ratings = sum(cat_counts)
    if total_ratings:
        top = max(cat_counts) / total_ratings
        if top > 0.85 and math.isfinite(kap) and math.isfinite(ac1) \
                and ac1 - kap > 0.15:
            warnings.append(
                f"한 범주가 전체 평가의 {top * 100:.1f}%를 차지합니다(불균형). "
                f"Fleiss' kappa({kap:.3f})가 AC1({ac1:.3f})보다 크게 낮은 것은 "
                "kappa 유병률 역설의 전형적 신호입니다 — AC1을 함께 보고하세요.")

    def _bracket(ci: Tuple[float, float], point: float) -> Tuple[float, float]:
        """Drop a bootstrap interval that fails to contain its point estimate."""
        lo, hi = ci
        if not (math.isfinite(lo) and math.isfinite(hi) and math.isfinite(point)):
            return ci
        if lo - 1e-9 <= point <= hi + 1e-9:
            return ci
        return float("nan"), float("nan")

    fk_ci = _bracket(fk_ci, kap)
    ac_ci = _bracket(ac_ci, ac1)
    ka_ci = _bracket(ka_ci, kalpha)

    meets: Optional[bool] = None
    if min_kappa is not None and math.isfinite(fk_ci[0]):
        meets = bool(fk_ci[0] >= min_kappa)
    # meets stays None when the bootstrap CI is undefined: "판정 불가", not
    # "미충족" — a NaN comparison must not read as a failed criterion.

    return MultiCategorical(
        names=names, categories=categories, n=n, m=m, n_alpha=n_alpha,
        dropped=dropped, alpha_level=alpha, weights=weights, ordinal=ordinal,
        percent_agreement=p_bar, fleiss=kap, fleiss_ci=fk_ci,
        fleiss_se_h0=se0, fleiss_z=z, fleiss_p=pval, pe=pe,
        per_category=per_cat, ac1=ac1, ac1_ci=ac_ci,
        kalpha=kalpha, kalpha_ci=ka_ci, kalpha_metric=metric,
        pairwise=pw, light_kappa=light, category_counts=cat_counts,
        bootstrap=eff_b, seed=seed, min_kappa=min_kappa,
        meets_threshold=meets, interpretation=interpret_kappa(kap),
        warnings=warnings)
