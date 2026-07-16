"""Categorical / ordinal rater-agreement statistics — pure standard library.

The continuous side of :mod:`agreestat` (Bland–Altman, ICC, CCC) answers "do two
methods give the same *number*?". This module answers the other half of the
clinical method-comparison question: **"do two raters/devices give the same
*class*?"** — device sleep stage vs PSG, algorithm rhythm call vs cardiologist,
ordinal severity grade by two readers.

Implemented from first principles:

* **Confusion (agreement) matrix** with marginals and observed agreement ``po``.
* **Cohen's kappa** with the Fleiss–Cohen–Everitt (1969) asymptotic CI and a
  z-test of H0: κ=0 (which uses a *different*, H0-specific variance).
* **Weighted kappa** (linear / quadratic disagreement weights) for ordinal
  scales, same variance family. Quadratic-weighted κ ≍ ICC(2,1) on the scores.
* **Gwet's AC1 / AC2** (Gwet 2008) with the linearization variance — the
  prevalence-robust alternative that does not collapse when one category
  dominates.
* **Scott's pi** and **Krippendorff's alpha** (nominal / ordinal / interval
  difference functions) via the coincidence matrix.
* **Kappa-paradox diagnostics**: Byrt's prevalence index (PI) and bias index
  (BI), and PABAK (Byrt, Bishop & Carlin 1993).
* **Per-category specific agreement** (Cicchetti & Feinstein 1990) — positive /
  negative percent agreement for a 2×2 device validation — plus one-vs-rest κ.
* **Marginal homogeneity**: exact McNemar (2×2) and Stuart–Maxwell (k×k), i.e.
  "does one rater systematically use a category more than the other?".

All quantiles/p-values come from :mod:`agreestat.special`; no numpy/scipy.

References
----------
Cohen J. (1960, 1968); Fleiss, Cohen & Everitt (1969) Psychol Bull 72:323-327;
Landis & Koch (1977) Biometrics 33:159-174; Cicchetti & Feinstein (1990) J Clin
Epidemiol 43:551-558; Byrt, Bishop & Carlin (1993) J Clin Epidemiol 46:423-429;
Gwet K.L. (2008) Br J Math Stat Psychol 61:29-48; Krippendorff K. (2004);
Stuart A. (1955) Biometrika 42:412-416; Maxwell A.E. (1970) Br J Psychiatry.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .special import chi2_sf, norm_ppf, norm_sf

__all__ = [
    "ConfusionMatrix",
    "KappaResult",
    "CategoryAgreement",
    "MarginalTest",
    "ParadoxDiagnostics",
    "ClusterResult",
    "confusion_matrix",
    "weight_matrix",
    "kappa",
    "gwet_ac",
    "scott_pi",
    "krippendorff_alpha",
    "paradox_diagnostics",
    "per_category_agreement",
    "mcnemar",
    "stuart_maxwell",
    "interpret_kappa",
    "order_categories",
    "cluster_bootstrap",
    "subject_kappas",
]


# --------------------------------------------------------------------------
# Categories / confusion matrix
# --------------------------------------------------------------------------
def _as_number(label: str) -> Optional[float]:
    """Numeric value of a category label, or None if it is not a plain number."""
    try:
        v = float(label.strip())
    except (ValueError, AttributeError):
        return None
    return v if math.isfinite(v) else None


def order_categories(labels: Sequence[str],
                     explicit: Optional[Sequence[str]] = None
                     ) -> Tuple[List[str], List[str]]:
    """Return (ordered_categories, notes).

    Order rule: an explicit list wins (and must cover every observed label);
    otherwise numeric-looking labels sort numerically ("2" < "10"), and any other
    label set sorts lexicographically — which is *arbitrary* for an ordinal
    scale, so a note is emitted telling the user to pass ``--categories``.
    """
    observed = sorted(set(labels))
    notes: List[str] = []
    if explicit is not None:
        exp = list(dict.fromkeys(explicit))  # de-duplicate, keep order
        missing = [c for c in observed if c not in exp]
        if missing:
            raise ValueError(
                f"--categories 에 없는 값이 자료에 있습니다: {missing}. "
                f"지정한 순서: {exp}")
        unused = [c for c in exp if c not in observed]
        if unused:
            notes.append(
                f"--categories 로 지정했지만 자료에 없는 범주: {unused} "
                "(빈 행/열로 표에 포함됩니다).")
        return exp, notes

    nums = [_as_number(c) for c in observed]
    if all(v is not None for v in nums):
        ordered = [c for _, c in sorted(zip(nums, observed), key=lambda t: t[0])]
        return ordered, notes
    notes.append(
        "범주 순서를 알파벳순으로 가정했습니다. 순서형(ordinal) 자료라면 "
        "--categories 로 실제 순서를 지정하세요 (가중 kappa 결과가 달라집니다).")
    return observed, notes


@dataclass
class ConfusionMatrix:
    categories: List[str]
    counts: List[List[int]]       # counts[i][j] = A said i, B said j
    n: int
    row_totals: List[int]         # rater A marginals
    col_totals: List[int]         # rater B marginals
    name_a: str = "A"
    name_b: str = "B"

    @property
    def k(self) -> int:
        return len(self.categories)

    @property
    def p(self) -> List[List[float]]:
        """Cell proportions."""
        return [[c / self.n for c in row] for row in self.counts]

    @property
    def po(self) -> float:
        """Observed (raw) proportion of agreement."""
        return sum(self.counts[i][i] for i in range(self.k)) / self.n


def confusion_matrix(a: Sequence[str], b: Sequence[str],
                     categories: Optional[Sequence[str]] = None,
                     name_a: str = "A", name_b: str = "B") -> ConfusionMatrix:
    """Cross-tabulate paired categorical ratings."""
    if len(a) != len(b):
        raise ValueError("rater A and rater B must have the same length")
    n = len(a)
    if n < 1:
        raise ValueError("need at least 1 paired rating")
    if categories is None:
        cats, _ = order_categories(list(a) + list(b))
    else:
        cats = list(dict.fromkeys(categories))
        unknown = sorted((set(a) | set(b)) - set(cats))
        if unknown:
            raise ValueError(f"categories list is missing observed labels: {unknown}")
    idx = {c: i for i, c in enumerate(cats)}
    k = len(cats)
    counts = [[0] * k for _ in range(k)]
    for x, y in zip(a, b):
        counts[idx[x]][idx[y]] += 1
    row = [sum(r) for r in counts]
    col = [sum(counts[i][j] for i in range(k)) for j in range(k)]
    return ConfusionMatrix(cats, counts, n, row, col, name_a, name_b)


# --------------------------------------------------------------------------
# Weights
# --------------------------------------------------------------------------
def weight_matrix(categories: Sequence[str], scheme: str = "unweighted"
                  ) -> Tuple[List[List[float]], str]:
    """Agreement-weight matrix ``w[i][j]`` in [0,1] (1 = full agreement).

    ``scheme``: ``unweighted`` (identity), ``linear`` (1-|i-j|/(k-1)) or
    ``quadratic`` (1-((i-j)/(k-1))^2). For numeric category labels the labels'
    own values are the scores, so an unequally spaced scale (0, 1, 2, 4) is
    weighted by its real distances rather than by rank position.

    Returns (w, note).
    """
    k = len(categories)
    if scheme == "unweighted":
        return [[1.0 if i == j else 0.0 for j in range(k)] for i in range(k)], ""
    if scheme not in ("linear", "quadratic"):
        raise ValueError("weights must be 'unweighted', 'linear' or 'quadratic'")
    if k < 2:
        raise ValueError("weighted kappa needs at least 2 categories")

    nums = [_as_number(c) for c in categories]
    note = ""
    if all(v is not None for v in nums) and len(set(nums)) == k:
        scores = [float(v) for v in nums]  # type: ignore[arg-type]
        if any(scores[i] >= scores[i + 1] for i in range(k - 1)):
            scores = list(range(k))
            note = ("범주 순서가 숫자 크기순이 아니어서 가중치는 순위(0,1,2,…) "
                    "간격으로 계산했습니다.")
        else:
            note = "가중치는 범주 라벨의 실제 숫자값 간격으로 계산했습니다."
    else:
        scores = [float(i) for i in range(k)]
        note = "가중치는 범주 순서(0,1,2,…) 간격으로 계산했습니다."

    span = scores[-1] - scores[0]
    if span <= 0:
        raise ValueError("weighted kappa needs distinct ordered category scores")
    w: List[List[float]] = []
    for i in range(k):
        row = []
        for j in range(k):
            d = abs(scores[i] - scores[j]) / span
            row.append(1.0 - d if scheme == "linear" else 1.0 - d * d)
        w.append(row)
    return w, note


# --------------------------------------------------------------------------
# Cohen's kappa (weighted and unweighted share one variance formula)
# --------------------------------------------------------------------------
@dataclass
class KappaResult:
    statistic: str            # "Cohen's kappa", "weighted kappa (linear)", ...
    value: float
    se: float
    ci_lower: float
    ci_upper: float
    po: float                 # (weighted) observed agreement
    pe: float                 # (weighted) chance agreement
    n: int
    interpretation: str
    alpha: float = 0.05
    z: float = float("nan")   # H0: kappa = 0 (unweighted only)
    pvalue: float = float("nan")
    note: str = ""
    max_kappa: float = float("nan")  # max attainable given the margins


def interpret_kappa(value: float) -> str:
    """Landis & Koch (1977) benchmark grades."""
    if value != value:
        return "판정 불가 / undefined"
    if value < 0.0:
        return "poor / 일치 없음(우연 이하)"
    if value < 0.21:
        return "slight / 미미함"
    if value < 0.41:
        return "fair / 약함"
    if value < 0.61:
        return "moderate / 보통"
    if value < 0.81:
        return "substantial / 상당함"
    return "almost perfect / 거의 완벽"


def _weighted_agreements(cm: ConfusionMatrix, w: List[List[float]]
                         ) -> Tuple[float, float, List[float], List[float]]:
    """Return (po_w, pe_w, p_row, p_col) for weight matrix *w*."""
    k, n = cm.k, cm.n
    p = cm.p
    pr = [t / n for t in cm.row_totals]
    pc = [t / n for t in cm.col_totals]
    po_w = sum(w[i][j] * p[i][j] for i in range(k) for j in range(k))
    pe_w = sum(w[i][j] * pr[i] * pc[j] for i in range(k) for j in range(k))
    return po_w, pe_w, pr, pc


def _kappa_variance(cm: ConfusionMatrix, w: List[List[float]], kw: float,
                    po_w: float, pe_w: float, pr: List[float],
                    pc: List[float]) -> float:
    """Fleiss, Cohen & Everitt (1969) asymptotic variance of (weighted) kappa.

    Reduces exactly to the classic unweighted kappa variance when ``w`` is the
    identity matrix. Used for the CI (not for testing H0: kappa = 0).
    """
    k, n = cm.k, cm.n
    p = cm.p
    if pe_w >= 1.0:
        return float("nan")
    # Weighted marginal means: wbar_i. = sum_j p_.j w_ij ; wbar_.j = sum_i p_i. w_ij
    wr = [sum(pc[j] * w[i][j] for j in range(k)) for i in range(k)]
    wc = [sum(pr[i] * w[i][j] for i in range(k)) for j in range(k)]
    acc = 0.0
    for i in range(k):
        for j in range(k):
            term = w[i][j] - (wr[i] + wc[j]) * (1.0 - kw)
            acc += p[i][j] * term * term
    acc -= (kw - pe_w * (1.0 - kw)) ** 2
    var = acc / (n * (1.0 - pe_w) ** 2)
    return var if var > 0.0 else 0.0


def _kappa_variance_h0(cm: ConfusionMatrix, pe: float) -> float:
    """Variance of unweighted kappa under H0: kappa = 0 (Fleiss et al. 1969)."""
    k, n = cm.k, cm.n
    pr = [t / n for t in cm.row_totals]
    pc = [t / n for t in cm.col_totals]
    if pe >= 1.0:
        return float("nan")
    s = sum(pr[i] * pc[i] * (pr[i] + pc[i]) for i in range(k))
    var = (pe + pe * pe - s) / (n * (1.0 - pe) ** 2)
    return var if var > 0.0 else 0.0


def _max_kappa(cm: ConfusionMatrix, pe: float) -> float:
    """Maximum kappa attainable with the observed marginals (Cohen 1960).

    The maximum-agreement table with the same margins has diagonal
    min(r_i, c_i); the off-diagonal remainder is spread to keep the margins,
    which for the *unweighted* case only affects agreement through the diagonal.
    Unweighted only — the caller must pass the UNWEIGHTED pe, since po_max here
    counts only exact agreements (mixing it with a weighted pe is unsound).
    """
    if pe >= 1.0:
        return float("nan")
    k, n = cm.k, cm.n
    po_max = sum(min(cm.row_totals[i], cm.col_totals[i]) for i in range(k)) / n
    return (po_max - pe) / (1.0 - pe)


def kappa(cm: ConfusionMatrix, weights: str = "unweighted",
          alpha: float = 0.05) -> KappaResult:
    """Cohen's (weighted) kappa with an asymptotic CI."""
    w, wnote = weight_matrix(cm.categories, weights)
    po_w, pe_w, pr, pc = _weighted_agreements(cm, w)
    label = {"unweighted": "Cohen's kappa",
             "linear": "weighted kappa (linear)",
             "quadratic": "weighted kappa (quadratic)"}[weights]

    if pe_w >= 1.0 - 1e-15:
        # Both raters used a single category (or perfectly confounded margins):
        # chance agreement is 1, kappa is 0/0.
        return KappaResult(
            label, float("nan"), float("nan"), float("nan"), float("nan"),
            po_w, pe_w, cm.n, interpret_kappa(float("nan")), alpha,
            note=("우연 일치확률 pe=1 이라 kappa가 정의되지 않습니다 "
                  "(두 평가자가 한 범주만 사용). 원자료의 범주 분포를 확인하세요."))

    kw = (po_w - pe_w) / (1.0 - pe_w)
    var = _kappa_variance(cm, w, kw, po_w, pe_w, pr, pc)
    se = math.sqrt(var) if var == var else float("nan")
    zc = norm_ppf(1.0 - alpha / 2.0)
    if se == se:
        lo, hi = kw - zc * se, kw + zc * se
        # kappa is bounded above by 1; the asymptotic interval can overshoot.
        hi = min(hi, 1.0)
        lo = max(lo, -1.0)
    else:
        lo = hi = float("nan")

    z = p = float("nan")
    if weights == "unweighted":
        var0 = _kappa_variance_h0(cm, pe_w)
        if var0 == var0 and var0 > 0.0:
            z = kw / math.sqrt(var0)
            p = 2.0 * norm_sf(abs(z))

    note = wnote
    if cm.n < 30:
        note = (note + " " if note else "") + (
            f"표본이 작습니다(n={cm.n}) — 정규근사 CI가 부정확할 수 있습니다.")
    return KappaResult(label, kw, se, lo, hi, po_w, pe_w, cm.n,
                       interpret_kappa(kw), alpha, z, p, note.strip(),
                       _max_kappa(cm, pe_w) if weights == "unweighted"
                       else float("nan"))


# --------------------------------------------------------------------------
# Gwet's AC1 / AC2
# --------------------------------------------------------------------------
def gwet_ac(cm: ConfusionMatrix, weights: str = "unweighted",
            alpha: float = 0.05) -> KappaResult:
    """Gwet's AC1 (unweighted) / AC2 (weighted) with the linearization variance.

    Gwet (2008) replaces Cohen's chance-agreement term — which is inflated
    exactly when one category dominates, producing the "kappa paradox" (high raw
    agreement, near-zero kappa) — with
    ``pe = (1/(K-1)) * sum_k pi_k (1 - pi_k)``, where ``pi_k`` is the mean of the
    two raters' marginal probabilities for category k. The variance is Gwet's
    linearized (subject-level) estimator.
    """
    k, n = cm.k, cm.n
    label = "Gwet's AC1" if weights == "unweighted" else f"Gwet's AC2 ({weights})"
    if k < 2:
        # Guard BEFORE weight_matrix(), which raises on k<2 for weighted schemes.
        return KappaResult(label, float("nan"), float("nan"), float("nan"),
                           float("nan"), 1.0, float("nan"), n,
                           interpret_kappa(float("nan")), alpha,
                           note="범주가 1개뿐이라 AC1이 정의되지 않습니다.")
    w, wnote = weight_matrix(cm.categories, weights)
    pr = [t / n for t in cm.row_totals]
    pc = [t / n for t in cm.col_totals]
    pi = [(pr[i] + pc[i]) / 2.0 for i in range(k)]
    tw = sum(w[i][j] for i in range(k) for j in range(k))  # total weight
    # AC2 chance term: (tw / (K(K-1))) * sum_k pi_k (1 - pi_k)   [Gwet 2008]
    pe = (tw / (k * (k - 1))) * sum(v * (1.0 - v) for v in pi)
    po_w, _pe_cohen, _pr, _pc = _weighted_agreements(cm, w)
    if pe >= 1.0 - 1e-15:
        return KappaResult(label, float("nan"), float("nan"), float("nan"),
                           float("nan"), po_w, pe, n,
                           interpret_kappa(float("nan")), alpha,
                           note="우연 일치확률 pe=1 이라 AC가 정의되지 않습니다.")
    gamma = (po_w - pe) / (1.0 - pe)

    # ---- linearization variance (Gwet 2008, §4) ----
    # Each subject contributes pa_i (its weighted agreement) and pe_i (its
    # chance term); the influence function is
    #   g_i = (pa_i - pe)/(1-pe) - 2(1-gamma)(pe_i - pe)/(1-pe)
    if n < 2:
        return KappaResult(label, gamma, float("nan"), float("nan"),
                           float("nan"), po_w, pe, n, interpret_kappa(gamma),
                           alpha, note="n<2 이라 분산을 추정할 수 없습니다.")
    infl: List[float] = []
    for i in range(k):
        for j in range(k):
            cnt = cm.counts[i][j]
            if cnt == 0:
                continue
            pa_i = w[i][j]
            # subject-level category membership probabilities (2 raters)
            pi_i = [((1.0 if i == m else 0.0) + (1.0 if j == m else 0.0)) / 2.0
                    for m in range(k)]
            pe_i = (tw / (k * (k - 1))) * sum(
                pi_i[m] * (1.0 - pi[m]) for m in range(k))
            g = ((pa_i - pe) / (1.0 - pe)
                 - 2.0 * (1.0 - gamma) * (pe_i - pe) / (1.0 - pe))
            infl.extend([g] * cnt)
    gbar = sum(infl) / n
    var = sum((g - gbar) ** 2 for g in infl) / (n * (n - 1))
    se = math.sqrt(var) if var > 0 else 0.0
    zc = norm_ppf(1.0 - alpha / 2.0)
    lo, hi = max(-1.0, gamma - zc * se), min(1.0, gamma + zc * se)
    note = wnote
    if n < 30:
        note = (note + " " if note else "") + (
            f"표본이 작습니다(n={n}) — 정규근사 CI가 부정확할 수 있습니다.")
    return KappaResult(label, gamma, se, lo, hi, po_w, pe, n,
                       interpret_kappa(gamma), alpha, note=note.strip())


# --------------------------------------------------------------------------
# Scott's pi / Krippendorff's alpha
# --------------------------------------------------------------------------
def scott_pi(cm: ConfusionMatrix) -> float:
    """Scott's pi — like kappa but with a single pooled marginal distribution."""
    k, n = cm.k, cm.n
    pi = [((cm.row_totals[i] + cm.col_totals[i]) / (2.0 * n)) for i in range(k)]
    pe = sum(v * v for v in pi)
    if pe >= 1.0 - 1e-15:
        return float("nan")
    return (cm.po - pe) / (1.0 - pe)


def _delta2(cats: Sequence[str], metric: str, coincidence_marg: List[float]
            ) -> List[List[float]]:
    """Krippendorff difference function δ² for the given metric."""
    k = len(cats)
    if metric == "nominal":
        return [[0.0 if i == j else 1.0 for j in range(k)] for i in range(k)]
    if metric == "interval":
        nums = [_as_number(c) for c in cats]
        if any(v is None for v in nums):
            raise ValueError("interval metric needs numeric category labels")
        return [[(nums[i] - nums[j]) ** 2 for j in range(k)]  # type: ignore
                for i in range(k)]
    if metric != "ordinal":
        raise ValueError("metric must be 'nominal', 'ordinal' or 'interval'")
    # Ordinal: δ²_ck = ( sum_{g=c..k} n_g - (n_c + n_k)/2 )^2
    out = [[0.0] * k for _ in range(k)]
    for i in range(k):
        for j in range(k):
            lo, hi = (i, j) if i <= j else (j, i)
            s = sum(coincidence_marg[g] for g in range(lo, hi + 1))
            s -= (coincidence_marg[lo] + coincidence_marg[hi]) / 2.0
            out[i][j] = s * s
    return out


def krippendorff_alpha(cm: ConfusionMatrix, metric: str = "nominal") -> float:
    """Krippendorff's alpha for two raters with complete data.

    Built from the coincidence matrix (each unit of 2 ratings contributes both
    ordered pairs), so ``alpha`` here is the reliability-of-the-data measure —
    slightly more conservative than Scott's pi (it corrects for finite n).
    """
    k, n = cm.k, cm.n
    # Coincidence matrix for m_u = 2 raters: o_ck = n_ck + n_kc (diag: 2*n_cc)
    o = [[float(cm.counts[i][j] + cm.counts[j][i]) for j in range(k)]
         for i in range(k)]
    nc = [sum(o[i]) for i in range(k)]
    total = sum(nc)  # = 2n
    if total < 2:
        return float("nan")
    d2 = _delta2(cm.categories, metric, nc)
    do = sum(o[i][j] * d2[i][j] for i in range(k) for j in range(k)) / total
    de = sum(nc[i] * nc[j] * d2[i][j]
             for i in range(k) for j in range(k) if i != j) / (total * (total - 1))
    if de <= 0.0:
        return float("nan")
    return 1.0 - do / de


# --------------------------------------------------------------------------
# Kappa-paradox diagnostics (Byrt 1993)
# --------------------------------------------------------------------------
@dataclass
class ParadoxDiagnostics:
    po: float
    prevalence_index: float = float("nan")   # 2x2 only
    bias_index: float = float("nan")         # 2x2 only
    pabak: float = float("nan")
    max_kappa: float = float("nan")
    paradox: bool = False
    note: str = ""


def paradox_diagnostics(cm: ConfusionMatrix, kappa_value: float,
                        max_kappa: float = float("nan")) -> ParadoxDiagnostics:
    """Byrt et al. (1993) prevalence/bias indices + PABAK.

    PABAK = (K*po - 1)/(K - 1) is the prevalence- and bias-adjusted kappa (for a
    2×2 table this is the familiar 2*po - 1). The prevalence and bias indices are
    defined for 2×2 tables only.
    """
    k, n = cm.k, cm.n
    po = cm.po
    pabak = (k * po - 1.0) / (k - 1.0) if k > 1 else float("nan")
    pi_idx = bi_idx = float("nan")
    if k == 2:
        a, b = cm.counts[0][0], cm.counts[0][1]
        c, d = cm.counts[1][0], cm.counts[1][1]
        pi_idx = abs(a - d) / n
        bi_idx = abs(b - c) / n
    paradox = (po >= 0.80 and kappa_value == kappa_value and kappa_value < 0.50)
    note = ""
    if paradox:
        note = ("관찰 일치도는 높은데(po≥0.80) kappa는 낮습니다 — 전형적인 "
                "'kappa 역설'입니다(한 범주가 대부분을 차지하면 우연 일치확률이 "
                "부풀려져 kappa가 눌립니다; Feinstein & Cicchetti 1990). "
                "Gwet's AC1과 PABAK을 함께 보고하세요.")
    return ParadoxDiagnostics(po, pi_idx, bi_idx, pabak, max_kappa, paradox, note)


# --------------------------------------------------------------------------
# Per-category agreement
# --------------------------------------------------------------------------
@dataclass
class CategoryAgreement:
    category: str
    n_a: int                 # times rater A used it
    n_b: int                 # times rater B used it
    n_both: int              # both raters
    specific_agreement: float        # 2*n_both / (n_a + n_b)
    sa_ci: Tuple[float, float] = (float("nan"), float("nan"))
    kappa_ovr: float = float("nan")  # one-vs-rest Cohen's kappa
    kappa_ovr_ci: Tuple[float, float] = (float("nan"), float("nan"))


def _wilson_ci(successes: float, trials: float, alpha: float
               ) -> Tuple[float, float]:
    """Wilson score interval — well-behaved at 0/1 unlike the Wald interval."""
    if trials <= 0:
        return float("nan"), float("nan")
    z = norm_ppf(1.0 - alpha / 2.0)
    p = successes / trials
    den = 1.0 + z * z / trials
    centre = (p + z * z / (2.0 * trials)) / den
    half = (z * math.sqrt(p * (1.0 - p) / trials
                          + z * z / (4.0 * trials * trials))) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def per_category_agreement(cm: ConfusionMatrix, alpha: float = 0.05
                           ) -> List[CategoryAgreement]:
    """Specific agreement + one-vs-rest kappa for every category.

    Specific agreement (Cicchetti & Feinstein 1990) is ``2*n_ii / (r_i + c_i)``:
    the conditional probability that, given one rater used category i, the other
    did too. For a 2×2 device-validation table these are exactly the FDA's
    positive and negative percent agreement. Its CI is a Wilson interval on
    ``n_ii`` successes out of ``(r_i + c_i)/2`` effective trials — an
    approximation (the two raters' uses of the category are not independent
    trials), so it is reported as indicative.
    """
    out: List[CategoryAgreement] = []
    k, n = cm.k, cm.n
    for i, cat in enumerate(cm.categories):
        r, c = cm.row_totals[i], cm.col_totals[i]
        both = cm.counts[i][i]
        sa = 2.0 * both / (r + c) if (r + c) > 0 else float("nan")
        sa_ci = (_wilson_ci(both, (r + c) / 2.0, alpha) if (r + c) > 0
                 else (float("nan"), float("nan")))
        # one-vs-rest 2x2 collapse
        a11 = both
        a12 = r - both
        a21 = c - both
        a22 = n - a11 - a12 - a21
        sub = ConfusionMatrix([cat, "rest"], [[a11, a12], [a21, a22]], n,
                              [a11 + a12, a21 + a22],
                              [a11 + a21, a12 + a22], cm.name_a, cm.name_b)
        try:
            kr = kappa(sub, "unweighted", alpha)
            kv, kci = kr.value, (kr.ci_lower, kr.ci_upper)
        except (ValueError, ZeroDivisionError):
            kv, kci = float("nan"), (float("nan"), float("nan"))
        out.append(CategoryAgreement(cat, r, c, both, sa, sa_ci, kv, kci))
    return out


# --------------------------------------------------------------------------
# Marginal homogeneity
# --------------------------------------------------------------------------
@dataclass
class MarginalTest:
    available: bool
    name: str = ""
    statistic: float = float("nan")
    df: int = 0
    pvalue: float = float("nan")
    note: str = ""
    b: int = 0   # 2x2 discordants (A=1,B=2)
    c: int = 0   # 2x2 discordants (A=2,B=1)


def _binom_two_sided_p(b: int, c: int) -> float:
    """Exact two-sided binomial p for McNemar (H0: p = 0.5 among discordants)."""
    m = b + c
    if m == 0:
        return 1.0
    x = min(b, c)
    tail = sum(math.comb(m, i) for i in range(0, x + 1)) / (2.0 ** m)
    return min(1.0, 2.0 * tail)


def mcnemar(cm: ConfusionMatrix, exact_max: int = 1000) -> MarginalTest:
    """McNemar's test of marginal homogeneity for a 2×2 table.

    Exact (binomial) when the discordant count is manageable, else the
    continuity-corrected chi-square.
    """
    if cm.k != 2:
        return MarginalTest(False, note="McNemar는 2x2 표에서만 정의됩니다")
    b = cm.counts[0][1]
    c = cm.counts[1][0]
    if b + c == 0:
        return MarginalTest(True, "McNemar (exact)", float("nan"), 1, 1.0,
                            "불일치 셀이 없어 주변 동질성 위배 증거가 없습니다.",
                            b, c)
    if b + c <= exact_max:
        return MarginalTest(True, "McNemar (exact binomial)", float("nan"), 0,
                            _binom_two_sided_p(b, c), "", b, c)
    stat = (abs(b - c) - 1.0) ** 2 / (b + c)
    return MarginalTest(True, "McNemar (chi-square, continuity-corrected)",
                        stat, 1, chi2_sf(stat, 1), "", b, c)


def _solve_sym(mat: List[List[float]], vec: List[float]) -> Optional[List[float]]:
    """Solve mat*x = vec by Gaussian elimination with partial pivoting."""
    n = len(vec)
    m = [row[:] + [vec[i]] for i, row in enumerate(mat)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        pv = m[col][col]
        for r in range(n):
            if r == col:
                continue
            f = m[r][col] / pv
            if f == 0.0:
                continue
            for cc in range(col, n + 1):
                m[r][cc] -= f * m[col][cc]
    return [m[i][n] / m[i][i] for i in range(n)]


def stuart_maxwell(cm: ConfusionMatrix) -> MarginalTest:
    """Stuart–Maxwell test of marginal homogeneity for a k×k table.

    H0: rater A and rater B use every category with the same overall frequency.
    A significant result means one rater systematically over-uses some category —
    a *bias* that a single kappa cannot show. Reduces to (uncorrected) McNemar
    when k = 2.
    """
    k = cm.k
    if k < 2:
        return MarginalTest(False, note="범주가 2개 미만입니다")
    # Drop categories that contribute no information and would make V singular:
    # those neither rater used, and those with no discordant pairs at all
    # (row+col == 2*n_ii implies row == col == n_ii, i.e. a perfectly and
    # exclusively agreed category, whose marginal difference is exactly 0).
    keep = [i for i in range(k)
            if cm.row_totals[i] + cm.col_totals[i] - 2 * cm.counts[i][i] > 0]
    if len(keep) < 2:
        if all(cm.row_totals[i] == cm.col_totals[i] for i in range(k)):
            return MarginalTest(True, "Stuart–Maxwell", 0.0, 0, 1.0,
                                "모든 범주에서 두 평가자의 주변분포가 같습니다.")
        return MarginalTest(False, note="불일치가 있는 범주가 2개 미만입니다")
    d_all = [cm.row_totals[i] - cm.col_totals[i] for i in keep]
    if all(v == 0 for v in d_all):
        return MarginalTest(True, "Stuart–Maxwell", 0.0, len(keep) - 1, 1.0,
                            "두 평가자의 주변분포가 완전히 같습니다.")
    m = len(keep)
    d = d_all[:-1]
    V = [[0.0] * (m - 1) for _ in range(m - 1)]
    for a in range(m - 1):
        i = keep[a]
        V[a][a] = float(cm.row_totals[i] + cm.col_totals[i] - 2 * cm.counts[i][i])
        for b_ in range(m - 1):
            if a == b_:
                continue
            j = keep[b_]
            V[a][b_] = -float(cm.counts[i][j] + cm.counts[j][i])
    x = _solve_sym(V, [float(v) for v in d])
    if x is None:
        return MarginalTest(False, "Stuart–Maxwell",
                            note=("분산행렬이 특이(singular)라 계산할 수 없습니다 "
                                  "— 희소한 범주를 합치거나 자료를 늘리세요."))
    stat = sum(d[a] * x[a] for a in range(m - 1))
    if stat < 0.0:  # numerical noise on a near-singular V
        return MarginalTest(False, "Stuart–Maxwell",
                            note="분산행렬이 수치적으로 불안정합니다.")
    df = m - 1
    return MarginalTest(True, "Stuart–Maxwell", stat, df, chi2_sf(stat, df))


# --------------------------------------------------------------------------
# Cluster-robust inference (repeated ratings per subject)
# --------------------------------------------------------------------------
@dataclass
class ClusterResult:
    """Cluster (subject-level) bootstrap CIs for the agreement coefficients.

    When each subject contributes many rated units — sleep epochs scored by a
    device and by PSG, several lesions per patient — the rows are **not
    independent**, and the asymptotic kappa SE (which assumes n independent
    units) is far too small: with subject-level heterogeneity the design effect
    can run into the tens, so a 95% CI computed from n=18,000 epochs can be ~9x
    too narrow. Resampling *subjects* (not rows) with replacement restores
    honest coverage.

    ``design_effect`` is (cluster SE / naive SE)^2, i.e. how many times more
    variance the clustering costs; ``n_effective`` is n_pairs / design_effect.
    """
    available: bool
    note: str = ""
    n_subjects: int = 0
    n_pairs: int = 0
    n_replicated_subjects: int = 0
    replicates: int = 0
    seed: int = 0
    statistic: str = ""
    value: float = float("nan")
    se: float = float("nan")
    ci_lower: float = float("nan")
    ci_upper: float = float("nan")
    naive_se: float = float("nan")
    naive_ci: Tuple[float, float] = (float("nan"), float("nan"))
    design_effect: float = float("nan")
    n_effective: float = float("nan")
    n_failed: int = 0            # resamples that could not be evaluated
    # per-subject spread (only over subjects with a computable coefficient)
    subject_median: float = float("nan")
    subject_q1: float = float("nan")
    subject_q3: float = float("nan")
    subject_min: float = float("nan")
    subject_max: float = float("nan")
    n_subject_estimates: int = 0


def _quantile(sorted_vals: List[float], q: float) -> float:
    """Linear-interpolation quantile of an already-sorted list."""
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _coef(a: Sequence[str], b: Sequence[str], cats: Sequence[str],
          statistic: str, weights: str, alpha: float) -> float:
    """Evaluate one agreement coefficient on a resample."""
    cm = confusion_matrix(a, b, cats)
    if statistic == "ac":
        return gwet_ac(cm, weights, alpha).value
    return kappa(cm, weights, alpha).value


def subject_kappas(a: Sequence[str], b: Sequence[str],
                   subjects: Sequence[str], cats: Sequence[str],
                   statistic: str = "kappa", weights: str = "unweighted",
                   alpha: float = 0.05) -> List[float]:
    """The coefficient computed *within* each subject (NaNs dropped).

    Reader/device papers routinely report "kappa ranged 0.51-0.88 across
    participants"; this is that distribution. Subjects whose own table is
    degenerate (one category used) yield NaN and are excluded.
    """
    by: Dict[str, List[int]] = {}
    for i, s in enumerate(subjects):
        by.setdefault(s, []).append(i)
    out: List[float] = []
    for idx in by.values():
        if len(idx) < 2:
            continue
        try:
            v = _coef([a[i] for i in idx], [b[i] for i in idx], cats,
                      statistic, weights, alpha)
        except (ValueError, ZeroDivisionError, OverflowError):
            continue
        if v == v:
            out.append(v)
    return out


def cluster_bootstrap(a: Sequence[str], b: Sequence[str],
                      subjects: Optional[Sequence[str]],
                      cats: Sequence[str], statistic: str = "kappa",
                      weights: str = "unweighted", alpha: float = 0.05,
                      replicates: int = 2000, seed: int = 20260716,
                      naive_se: float = float("nan"),
                      naive_ci: Tuple[float, float] = (float("nan"), float("nan"))
                      ) -> ClusterResult:
    """Percentile cluster bootstrap: resample SUBJECTS with replacement.

    Deterministic given ``seed`` (a published CI must be reproducible).
    """
    import random as _random

    if subjects is None:
        return ClusterResult(False, "no subject-id column supplied")
    if len(subjects) != len(a):
        raise ValueError("subjects must be the same length as the ratings")

    by: Dict[str, List[int]] = {}
    for i, s in enumerate(subjects):
        by.setdefault(s, []).append(i)
    keys = list(by)
    n_subj = len(keys)
    n_pairs = len(a)
    n_rep_subj = sum(1 for v in by.values() if len(v) >= 2)

    if n_rep_subj == 0:
        return ClusterResult(
            False, "각 피험자가 1행씩이라 군집 보정이 필요 없습니다 "
                   "(행이 곧 독립 관측치).", n_subj, n_pairs, 0)
    if n_subj < 2:
        return ClusterResult(
            False, "피험자가 1명뿐이라 군집 부트스트랩을 할 수 없습니다.",
            n_subj, n_pairs, n_rep_subj)
    if replicates < 2:
        raise ValueError("replicates must be >= 2")

    point = _coef(a, b, cats, statistic, weights, alpha)
    rng = _random.Random(seed)
    boots: List[float] = []
    failed = 0
    for _ in range(replicates):
        idx: List[int] = []
        for _ in range(n_subj):
            idx.extend(by[keys[rng.randrange(n_subj)]])
        try:
            v = _coef([a[i] for i in idx], [b[i] for i in idx], cats,
                      statistic, weights, alpha)
        except (ValueError, ZeroDivisionError, OverflowError):
            failed += 1
            continue
        if v == v:
            boots.append(v)
        else:
            failed += 1

    if len(boots) < 2:
        return ClusterResult(
            False, "부트스트랩 재표본 대부분에서 계수를 계산할 수 없었습니다 "
                   "(범주가 매우 희소하거나 피험자 수가 너무 적음).",
            n_subj, n_pairs, n_rep_subj, replicates, seed)

    mb = sum(boots) / len(boots)
    se = math.sqrt(sum((v - mb) ** 2 for v in boots) / (len(boots) - 1))
    boots.sort()
    lo = _quantile(boots, alpha / 2.0)
    hi = _quantile(boots, 1.0 - alpha / 2.0)

    deff = eff = float("nan")
    if naive_se == naive_se and naive_se > 0 and se == se:
        deff = (se / naive_se) ** 2
        if deff > 0:
            eff = n_pairs / deff

    subj = sorted(subject_kappas(a, b, subjects, cats, statistic, weights, alpha))
    res = ClusterResult(
        True, "", n_subj, n_pairs, n_rep_subj, replicates, seed,
        statistic, point, se, lo, hi, naive_se, naive_ci, deff, eff, failed)
    if subj:
        res.subject_median = _quantile(subj, 0.5)
        res.subject_q1 = _quantile(subj, 0.25)
        res.subject_q3 = _quantile(subj, 0.75)
        res.subject_min = subj[0]
        res.subject_max = subj[-1]
        res.n_subject_estimates = len(subj)
    if n_rep_subj < 10:
        res.note = (f"반복이 있는 피험자가 {n_rep_subj}명뿐입니다 — 군집 "
                    "부트스트랩 CI가 불안정할 수 있습니다.")
    return res
