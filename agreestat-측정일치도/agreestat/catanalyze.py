"""Orchestration for categorical / ordinal rater agreement.

:func:`analyze_categorical` runs the whole kappa-family pipeline on two raters'
paired classifications and collects the interpretive warnings a clinical reader
needs: the kappa paradox, marginal heterogeneity, sparse cells, an arbitrary
category order on an ordinal scale, and CI-lower-bound grading.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import categorical as C
from .categorical import (
    CategoryAgreement,
    ClusterResult,
    ConfusionMatrix,
    KappaResult,
    MarginalTest,
    ParadoxDiagnostics,
    interpret_kappa,
)

__all__ = ["CategoricalResult", "analyze_categorical"]


@dataclass
class CategoricalResult:
    name_a: str
    name_b: str
    n: int
    dropped: int
    alpha: float
    cm: ConfusionMatrix
    ordinal: bool
    weights: str                       # "unweighted" | "linear" | "quadratic"
    kappa: KappaResult
    kappa_weighted: Optional[KappaResult]
    ac1: KappaResult
    ac2: Optional[KappaResult]
    scott_pi: float
    krippendorff: float
    krippendorff_metric: str
    paradox: ParadoxDiagnostics
    per_category: List[CategoryAgreement]
    marginal: MarginalTest
    headline: str = "Cohen's kappa"    # which statistic to report as primary
    min_kappa: Optional[float] = None
    meets_threshold: Optional[bool] = None
    warnings: List[str] = field(default_factory=list)
    # cluster-robust inference when a subject column with repeats is supplied
    cluster: Optional[ClusterResult] = None
    cluster_ac1: Optional[ClusterResult] = None

    @property
    def primary(self) -> KappaResult:
        """The KappaResult the report headlines."""
        if self.headline == "weighted" and self.kappa_weighted is not None:
            return self.kappa_weighted
        return self.kappa

    @property
    def decision_ci(self) -> Tuple[float, float]:
        """The CI a verdict must be judged on: cluster-robust when available.

        With repeated units per subject the naive CI can be several times too
        narrow, so judging --min-kappa on it would manufacture a false pass.
        """
        if self.cluster is not None and self.cluster.available:
            return self.cluster.ci_lower, self.cluster.ci_upper
        return self.primary.ci_lower, self.primary.ci_upper


def _num(x: float, d: int = 3) -> str:
    return "NaN" if x != x else f"{x:.{d}f}"


def _labels(cats: Sequence[str], limit: int = 8, width: int = 20) -> str:
    """Render a label list for a warning without letting one huge label —
    or a hundred of them — blow the line up."""
    shown = [(c if len(c) <= width else c[:width - 1] + "…") for c in cats[:limit]]
    extra = f" 외 {len(cats) - limit}개" if len(cats) > limit else ""
    return f"{shown}{extra}"


def _grade(x: float) -> str:
    return interpret_kappa(x).split(" / ")[0]


def analyze_categorical(a: Sequence[str], b: Sequence[str],
                        name_a: str = "A", name_b: str = "B",
                        alpha: float = 0.05,
                        categories: Optional[Sequence[str]] = None,
                        ordinal: bool = False,
                        weights: Optional[str] = None,
                        dropped: int = 0,
                        min_kappa: Optional[float] = None,
                        extra_warnings: Optional[Sequence[str]] = None,
                        subjects: Optional[Sequence[str]] = None,
                        bootstrap: int = 2000,
                        seed: int = 20260716) -> CategoricalResult:
    """Run the categorical agreement pipeline on two raters' classifications.

    ``ordinal`` enables weighted kappa (default weighting: quadratic, which is
    the ordinal convention and coincides asymptotically with ICC(2,1) on the
    category scores). ``weights`` overrides the scheme. ``min_kappa`` is a
    pre-specified acceptance threshold, judged — deliberately — against the
    **CI lower bound**, not the point estimate.

    ``subjects`` (optional) marks which rows belong to the same subject. When a
    subject contributes several rated units (sleep epochs, multiple lesions),
    the rows are not independent and the asymptotic CI is far too narrow; a
    cluster bootstrap over subjects is then computed and becomes the CI every
    verdict is judged on.
    """
    a = [str(v) for v in a]
    b = [str(v) for v in b]
    if len(a) != len(b):
        raise ValueError("rater A and rater B must have the same length")
    n = len(a)
    if n < 2:
        raise ValueError("need at least 2 paired ratings")

    warnings: List[str] = list(extra_warnings or [])

    cats, cat_notes = C.order_categories(a + b, categories)
    warnings.extend(cat_notes)
    cm = C.confusion_matrix(a, b, cats, name_a, name_b)

    if cm.k < 2:
        raise ValueError(
            f"두 평가자가 사용한 범주가 1개뿐입니다('{cats[0]}') — "
            "일치도(kappa)를 계산할 수 없습니다.")

    # Weighted kappa only makes sense on an ordered scale.
    if weights is None:
        scheme = "quadratic" if ordinal else "unweighted"
    else:
        scheme = weights
        if scheme != "unweighted" and not ordinal:
            ordinal = True  # asking for weights implies an ordered scale
            warnings.append(
                "--weights 를 지정해 순서형(ordinal) 척도로 처리했습니다. "
                "범주 순서가 맞는지 --categories 로 확인하세요.")

    k_un = C.kappa(cm, "unweighted", alpha)
    k_w = C.kappa(cm, scheme, alpha) if scheme != "unweighted" else None
    ac1 = C.gwet_ac(cm, "unweighted", alpha)
    ac2 = C.gwet_ac(cm, scheme, alpha) if scheme != "unweighted" else None
    pi = C.scott_pi(cm)
    metric = "ordinal" if ordinal else "nominal"
    try:
        kalpha = C.krippendorff_alpha(cm, metric)
    except (ValueError, ZeroDivisionError):
        kalpha = float("nan")
    par = C.paradox_diagnostics(cm, k_un.value, k_un.max_kappa)
    per_cat = C.per_category_agreement(cm, alpha)
    marg = C.mcnemar(cm) if cm.k == 2 else C.stuart_maxwell(cm)

    headline = "weighted" if k_w is not None else "unweighted"
    primary = k_w if k_w is not None else k_un

    # ---- cluster-robust inference ---------------------------------------
    cluster = cluster_ac1 = None
    if subjects is not None:
        if len(subjects) != n:
            raise ValueError("subjects must be the same length as the ratings")
        cluster = C.cluster_bootstrap(
            a, b, subjects, cm.categories, "kappa", scheme, alpha,
            replicates=bootstrap, seed=seed,
            naive_se=primary.se, naive_ci=(primary.ci_lower, primary.ci_upper))
        if cluster.available:
            cluster_ac1 = C.cluster_bootstrap(
                a, b, subjects, cm.categories, "ac", scheme, alpha,
                replicates=bootstrap, seed=seed,
                naive_se=(ac2 or ac1).se,
                naive_ci=((ac2 or ac1).ci_lower, (ac2 or ac1).ci_upper))

    # ---- warnings -------------------------------------------------------
    if cluster is not None and cluster.available:
        msg = (
            f"피험자당 여러 행이 있는 군집 자료입니다(피험자 {cluster.n_subjects}명, "
            f"총 {cluster.n_pairs}행). 각 행을 독립으로 보는 일반 kappa 신뢰구간은 "
            "너무 좁으므로, 피험자를 재표집한 군집 부트스트랩 CI "
            f"[{_num(cluster.ci_lower)}, {_num(cluster.ci_upper)}]를 "
            f"보고하세요 (naive CI [{_num(primary.ci_lower)}, "
            f"{_num(primary.ci_upper)}]).")
        if cluster.design_effect == cluster.design_effect:
            msg += (f" 설계효과(design effect) ≈ {_num(cluster.design_effect, 1)}"
                    f", 유효 표본수 ≈ {_num(cluster.n_effective, 0)}"
                    f" (실제 {cluster.n_pairs}행).")
        warnings.append(msg)
        if cluster.note:
            warnings.append(cluster.note)
    elif subjects is not None and cluster is not None and not cluster.available:
        if cluster.n_replicated_subjects == 0 and cluster.n_subjects:
            pass  # one row per subject: rows already independent, nothing to say
        else:
            warnings.append(f"군집 보정 불가: {cluster.note}")
    if par.paradox:
        warnings.append(par.note)
    if primary.value == primary.value and primary.ci_lower == primary.ci_lower:
        if _grade(primary.ci_lower) != _grade(primary.value):
            warnings.append(
                f"{primary.statistic} 점추정 등급({_grade(primary.value)})은 "
                f"신뢰구간 하한({_num(primary.ci_lower)}) 기준 등급"
                f"({_grade(primary.ci_lower)})보다 높습니다. 보수적으로 "
                "신뢰구간 하한으로 판단하는 것을 권장합니다.")
    if k_un.max_kappa == k_un.max_kappa and k_un.max_kappa < 0.85:
        warnings.append(
            f"주변분포 불균형 때문에 이 표에서 이론상 가능한 최대 kappa는 "
            f"{_num(k_un.max_kappa)}입니다 (완전일치가 불가능한 구조). "
            "kappa가 낮은 원인이 '불일치'가 아니라 '두 평가자의 범주 사용 빈도 "
            "차이'일 수 있습니다.")
    if marg.available and marg.pvalue == marg.pvalue and marg.pvalue < alpha:
        warnings.append(
            f"{marg.name} 검정에서 두 평가자의 주변분포가 다릅니다 "
            f"(p={_num(marg.pvalue)}) — 한쪽이 특정 범주를 체계적으로 더 많이 "
            "사용합니다(계통 편향). kappa 하나로는 이 편향이 드러나지 않습니다.")

    # sparse-cell / small-n guidance
    expected_min = min(
        (cm.row_totals[i] * cm.col_totals[j]) / cm.n
        for i in range(cm.k) for j in range(cm.k))
    if n < 30:
        warnings.append(
            f"표본이 작습니다(n={n}). kappa의 정규근사 신뢰구간이 부정확할 수 "
            "있으니 CI를 넓게 해석하세요.")
    elif expected_min < 1.0 and cm.k > 2:
        warnings.append(
            "표에 기대빈도가 1 미만인 칸이 있습니다(희소 범주). 해당 범주의 "
            "범주별 일치도와 가중 kappa가 불안정할 수 있으니 희소 범주를 "
            "합치는 것을 고려하세요.")
    unused = [c for i, c in enumerate(cm.categories)
              if cm.row_totals[i] == 0 and cm.col_totals[i] == 0]
    if unused:
        warnings.append(f"자료에 한 번도 나타나지 않은 범주: {_labels(unused)}.")
    only_one = [c for i, c in enumerate(cm.categories)
                if (cm.row_totals[i] == 0) != (cm.col_totals[i] == 0)]
    if only_one:
        warnings.append(
            f"한쪽 평가자만 사용한 범주: {_labels(only_one)} — 구조적으로 그 범주의 "
            "일치도는 0이 되고 최대 kappa가 1보다 작아집니다.")

    # ---- acceptance threshold -------------------------------------------
    # Judge on the cluster-robust CI when there is one: with repeated units per
    # subject the naive lower bound can clear the bar while the honest one does
    # not, which would turn this "conservative" feature into a false pass.
    meets = None
    clustered = cluster is not None and cluster.available
    ci_lo, _ci_hi = (cluster.ci_lower, cluster.ci_upper) if clustered else (
        primary.ci_lower, primary.ci_upper)
    if min_kappa is not None:
        if ci_lo == ci_lo:
            meets = ci_lo >= min_kappa
            src = "군집 부트스트랩 CI 하한" if clustered else "신뢰구간 하한"
            warnings.append(
                f"사전 설정 기준 {primary.statistic} ≥ {_num(min_kappa, 2)}: "
                f"{src} {_num(ci_lo)} → "
                + ("기준 충족 ✔" if meets else "기준 미충족 ✗")
                + f" (점추정 {_num(primary.value)}). 점추정이 아니라 CI 하한으로 "
                  "판정합니다 — 표본이 작으면 점추정만으로는 과대주장이 됩니다.")
            if clustered and primary.ci_lower == primary.ci_lower:
                naive_meets = primary.ci_lower >= min_kappa
                if naive_meets and not meets:
                    warnings.append(
                        "⚠ 각 행을 독립으로 가정한 naive CI 하한"
                        f"({_num(primary.ci_lower)})만 보면 기준을 '충족'하는 것처럼 "
                        "보이지만, 군집(피험자 내 반복)을 보정하면 기준에 미치지 "
                        "못합니다. 군집 보정 결과로 보고하세요.")
        else:
            warnings.append(
                f"사전 설정 기준 {_num(min_kappa, 2)}과 비교할 신뢰구간을 "
                "계산할 수 없어 판정을 보류합니다.")

    return CategoricalResult(
        name_a=name_a, name_b=name_b, n=n, dropped=dropped, alpha=alpha,
        cm=cm, ordinal=ordinal, weights=scheme,
        kappa=k_un, kappa_weighted=k_w, ac1=ac1, ac2=ac2,
        scott_pi=pi, krippendorff=kalpha, krippendorff_metric=metric,
        paradox=par, per_category=per_cat, marginal=marg,
        headline=headline, min_kappa=min_kappa, meets_threshold=meets,
        warnings=warnings, cluster=cluster, cluster_ac1=cluster_ac1)
