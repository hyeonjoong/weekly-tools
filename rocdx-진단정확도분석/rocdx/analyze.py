"""Putting it together: clean the data, fit the ROC, pick operating points.

This module owns two things the maths modules deliberately do not: the
*direction* of the marker (which end means disease) and the *honesty* of a
cut-off that was chosen on the same data it is reported on.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .delong import AucComparison, AucEstimate, compare_paired, estimate_auc
from .loader import (
    LoadError,
    Table,
    infer_decimal_comma,
    parse_label_column,
    parse_number,
    resolve_column,
)
from .roc import (
    Metrics,
    Point,
    closest_topleft_point,
    metrics_at,
    point_at_min_sens,
    point_at_min_spec,
    point_for_cutoff,
    roc_points,
    youden_point,
)

__all__ = [
    "ColumnData",
    "Dataset",
    "SelectedPoint",
    "BootstrapSummary",
    "Analysis",
    "load_dataset",
    "orient",
    "analyze",
    "percentile_ci",
]


@dataclass
class ColumnData:
    """One marker column after parsing, with a tally of what was unusable."""

    name: str
    values: List[Optional[float]]
    n_missing: int = 0
    n_unparsed: int = 0
    n_censored: int = 0
    n_percent: int = 0
    n_plain: int = 0
    decimal_comma: Optional[bool] = None
    unparsed_examples: List[str] = field(default_factory=list)


@dataclass
class Dataset:
    """Rows that survived cleaning, plus the audit trail of what was dropped."""

    score_name: str
    truth_name: str
    scores: List[float]
    positive: List[bool]
    positive_label: str
    negative_label: str
    n_rows_in: int
    n_dropped: int
    drop_reasons: Dict[str, int]
    encoding: str = ""
    delimiter: str = ""
    notes: List[str] = field(default_factory=list)
    extra: Dict[str, List[float]] = field(default_factory=dict)  # aligned comparator markers

    @property
    def n_pos(self) -> int:
        return sum(1 for p in self.positive if p)

    @property
    def n_neg(self) -> int:
        return len(self.positive) - self.n_pos


def _parse_numeric_column(table: Table, name: str) -> ColumnData:
    cells = table.column(name)
    policy = infer_decimal_comma(cells, table.delimiter)
    col = ColumnData(name=name, values=[], decimal_comma=policy)
    for raw in cells:
        val, note = parse_number(raw, decimal_comma=policy)
        col.values.append(val)
        if note == "missing":
            col.n_missing += 1
        elif note == "unparsed":
            col.n_unparsed += 1
            if len(col.unparsed_examples) < 3 and raw.strip():
                # Truncated on purpose: this string is printed in the report,
                # and a clinical cell can contain a name or an MRN.
                text = raw.strip()
                col.unparsed_examples.append(
                    text if len(text) <= 12 else text[:12] + "…")
        elif note == "censored":
            col.n_censored += 1
        elif note == "percent":
            col.n_percent += 1
        else:
            col.n_plain += 1
    return col


def load_dataset(table: Table, score_col: str, truth_col: str,
                 positive_label: Optional[str] = None,
                 negative_label: Optional[str] = None,
                 compare_cols: Sequence[str] = ()) -> Dataset:
    """Parse the score/outcome (and comparator) columns and drop unusable rows.

    A row is kept only when every requested column has a usable value, so the
    marker and its comparator are always compared on exactly the same subjects.
    """
    score_name = resolve_column(table, score_col)
    truth_name = resolve_column(table, truth_col)
    comp_names = [resolve_column(table, c) for c in compare_cols]

    score = _parse_numeric_column(table, score_name)
    comps = [_parse_numeric_column(table, c) for c in comp_names]
    labels = parse_label_column(table.column(truth_name), positive_label, negative_label)

    scores: List[float] = []
    positive: List[bool] = []
    extra: Dict[str, List[float]] = {c.name: [] for c in comps}
    reasons: Dict[str, int] = {}

    def bump(key: str) -> None:
        reasons[key] = reasons.get(key, 0) + 1

    for i in range(len(table.rows)):
        s = score.values[i]
        y = labels.values[i]
        if s is None:
            bump("검사값 없음/숫자 아님 (score missing or non-numeric)")
            continue
        if y is None:
            bump("결과(진단) 값 없음/판독 불가 (outcome missing or unrecognised)")
            continue
        cvals = [c.values[i] for c in comps]
        if any(v is None for v in cvals):
            bump("비교 검사값 없음 (comparator missing) — 짝지은 비교를 위해 제외")
            continue
        scores.append(s)
        positive.append(y)
        for c, v in zip(comps, cvals):
            extra[c.name].append(float(v))  # type: ignore[arg-type]

    notes = list(table.notes)
    for c in [score] + comps:
        if c.decimal_comma is True:
            notes.append(
                f"'{c.name}' 열의 쉼표를 소수점으로 해석했습니다 (예: 1,06 → 1.06). "
                f"천단위 구분자였다면 --sep 로 구분자를 바꾸거나 원본을 수정하세요 "
                f"(comma read as a decimal separator for this column)"
            )
        if not c.n_percent:
            continue
        notes.append(
            f"'{c.name}' 열의 퍼센트 표기 {c.n_percent}건은 100으로 나누어 비율(0~1)로 "
            f"변환했습니다 (percent cells divided by 100) — 절단점도 0~1 척도로 출력됩니다"
        )
        if c.n_plain:
            notes.append(
                f"경고: '{c.name}' 열에 퍼센트 표기({c.n_percent}건)와 일반 숫자"
                f"({c.n_plain}건)가 섞여 있습니다. 두 표기의 단위가 100배 다르므로 "
                f"결과를 신뢰할 수 없습니다 — 원본 열을 한 가지 표기로 통일하세요 "
                f"(mixed percent and plain numbers in one column)"
            )
    if labels.n_folded_negative:
        shown = ", ".join(repr(v) for v in labels.folded_negative[:6])
        notes.append(
            f"--positive-label '{labels.positive_label}' 이(가) 아닌 값 "
            f"{labels.n_folded_negative}건({shown})은 모두 비질환군으로 처리했습니다. "
            f"판정보류·불확정 결과가 섞여 있으면 특이도·PPV가 부풀려집니다 — 제외하려면 "
            f"--negative-label 도 함께 지정하세요"
        )
    if score.n_censored:
        notes.append(
            f"'{score_name}' 열의 검출한계 표기(<, >, ≤, ≥) {score.n_censored}건은 "
            f"한계값 자체로 처리했습니다 (censored values replaced by the limit itself; "
            f"this creates ties at the limit)"
        )
    for c in comps:
        if c.n_censored:
            notes.append(f"'{c.name}' 열의 검출한계 표기 {c.n_censored}건도 같은 방식으로 처리")
    if score.n_unparsed:
        ex = ", ".join(repr(e) for e in score.unparsed_examples)
        notes.append(f"'{score_name}' 열에서 숫자로 읽을 수 없는 값 {score.n_unparsed}건 (예: {ex})")
    if labels.n_unparsed:
        notes.append(f"'{truth_name}' 열에서 양성/음성 어느 쪽도 아닌 값 {labels.n_unparsed}건")

    return Dataset(
        score_name=score_name, truth_name=truth_name, scores=scores, positive=positive,
        positive_label=labels.positive_label, negative_label=labels.negative_label,
        n_rows_in=len(table.rows), n_dropped=len(table.rows) - len(scores),
        drop_reasons=reasons, encoding=table.encoding, delimiter=table.delimiter,
        notes=notes, extra=extra,
    )


def orient(scores: Sequence[float], positive: Sequence[bool],
           direction: str = "auto") -> Tuple[List[float], bool, str]:
    """Return scores oriented so that *higher means more likely diseased*.

    ``direction`` is ``higher`` (keep), ``lower`` (negate) or ``auto``. ``auto``
    negates when the empirical AUC of the raw scores is below 0.5. Returns
    ``(oriented, flipped, how)`` where ``how`` records whether the choice was
    made by the user or by the data.
    """
    if direction not in ("auto", "higher", "lower"):
        raise ValueError("direction must be 'auto', 'higher' or 'lower'")
    if direction == "higher":
        return list(scores), False, "user"
    if direction == "lower":
        return [-s for s in scores], True, "user"
    from .roc import auc_from_scores

    auc = auc_from_scores(scores, positive)
    if math.isnan(auc) or auc >= 0.5:
        return list(scores), False, "auto"
    return [-s for s in scores], True, "auto"


# --- operating-point selection ------------------------------------------------

Selector = Callable[[Sequence[Point]], Optional[Point]]


def _selector(rule: str, value: Optional[float]) -> Selector:
    if rule == "youden":
        return lambda pts: youden_point(pts)
    if rule == "topleft":
        return lambda pts: closest_topleft_point(pts)
    if rule == "min_spec":
        return lambda pts: point_at_min_spec(pts, float(value))  # type: ignore[arg-type]
    if rule == "min_sens":
        return lambda pts: point_at_min_sens(pts, float(value))  # type: ignore[arg-type]
    raise ValueError(f"unknown selection rule: {rule}")


@dataclass
class BootstrapSummary:
    """Resampling-based uncertainty and optimism for a data-chosen cut-off."""

    n_boot: int
    n_effective: int
    seed: int
    n_cutoff_draws: int
    cutoff_ci: Optional[Tuple[float, float]]
    sens_ci: Optional[Tuple[float, float]]
    spec_ci: Optional[Tuple[float, float]]
    youden_ci: Optional[Tuple[float, float]]
    sens_corrected: Optional[float]
    spec_corrected: Optional[float]
    youden_corrected: Optional[float]
    optimism_youden: Optional[float]


@dataclass
class SelectedPoint:
    """An operating point together with how it was chosen and how it performs."""

    key: str
    label: str
    rule: str
    metrics: Metrics
    bootstrap: Optional[BootstrapSummary] = None
    data_chosen: bool = True
    feasible: bool = True
    note: str = ""


def percentile_ci(values: Sequence[float], alpha: float) -> Optional[Tuple[float, float]]:
    """Percentile interval of a bootstrap distribution (None if too few draws)."""
    vals = sorted(v for v in values if math.isfinite(v))
    b = len(vals)
    # Efron's order statistics: floor((B+1)a/2) and ceil((B+1)(1-a/2)). With too
    # few draws those land on the extremes, which would be an interval narrower
    # than advertised, so refuse instead of mislabelling it.
    if b < 2.0 / alpha - 1.0:
        return None
    lo_i = max(0, int(math.floor((b + 1) * alpha / 2.0)) - 1)
    hi_i = min(b - 1, int(math.ceil((b + 1) * (1.0 - alpha / 2.0))) - 1)
    return (vals[lo_i], vals[hi_i])


def bootstrap_selected_point(scores: Sequence[float], positive: Sequence[bool],
                             selector: Selector, n_boot: int, seed: int,
                             alpha: float) -> Optional[BootstrapSummary]:
    """Stratified bootstrap of the *whole* procedure: reselect, then re-evaluate.

    Each resample repeats the cut-off search and the resulting cut-off is then
    applied to the *original* data. The percentile intervals are built from that
    out-of-sample evaluation, not from the resample's own (optimistic) numbers —
    scoring a cut-off on the data that chose it would centre the interval on
    ``apparent + optimism`` and cover the truth far below the nominal rate. The
    same pairing gives Harrell's optimism estimate.
    """
    pos_idx = [i for i, p in enumerate(positive) if p]
    neg_idx = [i for i, p in enumerate(positive) if not p]
    if n_boot <= 0 or len(pos_idx) < 2 or len(neg_idx) < 2:
        return None
    rng = random.Random(seed)
    cuts: List[float] = []
    senss: List[float] = []
    specs: List[float] = []
    js: List[float] = []
    opt_j: List[float] = []
    opt_sens: List[float] = []
    opt_spec: List[float] = []

    for _ in range(n_boot):
        idx = [rng.choice(pos_idx) for _ in pos_idx] + [rng.choice(neg_idx) for _ in neg_idx]
        s = [scores[i] for i in idx]
        p = [positive[i] for i in idx]
        pt = selector(roc_points(s, p))
        if pt is None:
            continue
        orig = point_for_cutoff(scores, positive, pt.threshold)
        if math.isnan(pt.sens) or math.isnan(pt.spec) or math.isnan(orig.sens):
            continue
        if math.isfinite(pt.threshold):
            cuts.append(pt.threshold)
        senss.append(orig.sens)
        specs.append(orig.spec)
        js.append(orig.youden)
        opt_j.append(pt.youden - orig.youden)
        opt_sens.append(pt.sens - orig.sens)
        opt_spec.append(pt.spec - orig.spec)

    if not senss:
        return None

    apparent = selector(roc_points(scores, positive))
    mean = lambda xs: math.fsum(xs) / len(xs)  # noqa: E731
    corr_sens = corr_spec = corr_j = None
    # An optimism estimate from a handful of resamples is noise; below 50 usable
    # draws it is not reported at all rather than printed like a 2000-rep run.
    if apparent is not None and len(opt_j) >= 50:
        corr_sens = apparent.sens - mean(opt_sens)
        corr_spec = apparent.spec - mean(opt_spec)
        corr_j = apparent.youden - mean(opt_j)
    return BootstrapSummary(
        n_boot=n_boot, n_effective=len(senss), seed=seed,
        n_cutoff_draws=len(cuts),
        cutoff_ci=percentile_ci(cuts, alpha) if cuts else None,
        sens_ci=percentile_ci(senss, alpha),
        spec_ci=percentile_ci(specs, alpha),
        youden_ci=percentile_ci(js, alpha),
        sens_corrected=corr_sens, spec_corrected=corr_spec, youden_corrected=corr_j,
        optimism_youden=mean(opt_j) if (corr_j is not None) else None,
    )


@dataclass
class Analysis:
    """Everything the report needs."""

    dataset: Dataset
    oriented: List[float]
    flipped: bool
    direction_source: str
    alpha: float
    auc: AucEstimate
    points: List[Point]
    selected: List[SelectedPoint]
    prevalence_user: Optional[float]
    warnings: List[str] = field(default_factory=list)
    comparisons: List[AucComparison] = field(default_factory=list)
    # comparator column name -> was its direction flipped?
    comparison_flipped: Dict[str, bool] = field(default_factory=dict)

    def cutoff_in_original_units(self, threshold: float) -> Tuple[float, str]:
        """Map an oriented threshold back to the user's scale and its operator."""
        if self.flipped:
            return (-threshold, "<=")
        return (threshold, ">=")


def analyze(dataset: Dataset, direction: str = "auto", alpha: float = 0.05,
            prevalence: Optional[float] = None, min_spec: Optional[float] = None,
            min_sens: Optional[float] = None, cutoffs: Sequence[float] = (),
            n_boot: int = 0, seed: int = 20260806,
            ci_method: str = "logit") -> Analysis:
    """Run the full analysis for one marker."""
    if not 0.0 < alpha < 1.0:
        raise LoadError("alpha 는 0과 1 사이여야 합니다 / alpha must be in (0, 1)")
    if prevalence is not None and not 0.0 < prevalence < 1.0:
        raise LoadError("--prevalence 는 0과 1 사이여야 합니다 / prevalence must be in (0, 1)")
    n_pos, n_neg = dataset.n_pos, dataset.n_neg
    if n_pos == 0 or n_neg == 0:
        detail = ""
        if dataset.n_dropped:
            reasons = "; ".join(f"{k}: {v}" for k, v in
                                sorted(dataset.drop_reasons.items(), key=lambda kv: -kv[1]))
            detail = (f" 입력 {dataset.n_rows_in}행 중 {dataset.n_dropped}행이 제외되었습니다 "
                      f"({reasons}).")
        raise LoadError(
            f"질환군 {n_pos}명, 비질환군 {n_neg}명 — ROC 분석에는 양쪽 모두 필요합니다 / "
            f"ROC analysis needs at least one case in each group.{detail}"
        )

    oriented, flipped, how = orient(dataset.scores, dataset.positive, direction)
    pos_scores = [s for s, p in zip(oriented, dataset.positive) if p]
    neg_scores = [s for s, p in zip(oriented, dataset.positive) if not p]
    auc = estimate_auc(pos_scores, neg_scores, alpha, ci_method)
    points = roc_points(oriented, dataset.positive)

    warnings: List[str] = []
    if how == "auto" and flipped:
        warnings.append(
            "검사값이 낮을수록 질환에 가까운 것으로 판단해 방향을 자동으로 뒤집었습니다. "
            "자동 방향 선택은 참 AUC가 0.5에 가까울 때 AUC를 위쪽으로 부풀립니다 — "
            "방향은 데이터가 아니라 임상 지식으로 정하고 --direction 으로 명시하세요 "
            "(auto-flipped; data-driven direction inflates the AUC when the marker is "
            "near-useless — state the direction with --direction instead)"
        )
    if min(n_pos, n_neg) < 10:
        warnings.append(
            f"한쪽 군의 표본이 매우 작습니다 (질환군 {n_pos}, 비질환군 {n_neg}). "
            f"신뢰구간이 넓고 정규근사 기반 p값은 신뢰하기 어렵습니다 "
            f"(very small group — normal-approximation intervals are unreliable)"
        )
    n_unique = len(set(oriented))
    if n_unique < 5:
        warnings.append(
            f"검사값의 서로 다른 값이 {n_unique}가지뿐입니다. ROC 곡선이 계단 몇 개로만 "
            f"이루어져 최적 절단점 선택이 매우 불안정합니다 "
            f"(only {n_unique} distinct score values)"
        )
    tie_frac = 1.0 - n_unique / len(oriented)
    if tie_frac > 0.5 and n_unique >= 5:
        warnings.append(
            f"검사값의 {tie_frac * 100:.0f}%가 동점(tie)입니다. 동점은 ROC 곡선의 대각선 "
            f"구간을 만들고 AUC를 낮춥니다 (heavy ties)"
        )
    if prevalence is not None:
        warnings.append(
            f"PPV/NPV는 지정한 유병률 {prevalence:.4g} 기준으로 베이즈 정리로 다시 계산했습니다. "
            f"이 값들은 표본 유병률({n_pos / (n_pos + n_neg):.4g})이 아닌 가정에 의존하며 "
            f"신뢰구간은 제공하지 않습니다 (PPV/NPV recomputed at the assumed prevalence; "
            f"no interval is given because it would not reflect this sample alone)"
        )

    selected: List[SelectedPoint] = []

    def add(key: str, label: str, rule: str, pt: Optional[Point], data_chosen: bool,
            selector: Optional[Selector] = None, note: str = "") -> None:
        if pt is None:
            selected.append(SelectedPoint(
                key=key, label=label, rule=rule,
                metrics=metrics_at(Point(float("nan"), 0, 0, 0, 0), alpha, prevalence),
                feasible=False, data_chosen=data_chosen, note=note))
            return
        boot = None
        if data_chosen and n_boot > 0 and selector is not None:
            boot = bootstrap_selected_point(oriented, dataset.positive, selector,
                                            n_boot, seed, alpha)
        selected.append(SelectedPoint(
            key=key, label=label, rule=rule,
            metrics=metrics_at(pt, alpha, prevalence),
            bootstrap=boot, data_chosen=data_chosen, note=note))

    sel_y = _selector("youden", None)
    add("youden", "Youden J 최대 (sensitivity + specificity - 1)", "youden",
        sel_y(points), True, sel_y)
    sel_tl = _selector("topleft", None)
    add("topleft", "좌상단 (0,1)에 가장 가까운 점", "topleft", sel_tl(points), True, sel_tl)

    if min_spec is not None:
        sel = _selector("min_spec", min_spec)
        pt = sel(points)
        add("min_spec", f"특이도 ≥ {min_spec:.3g} 중 민감도 최대", "min_spec", pt, True, sel,
            note=("" if pt else f"특이도 {min_spec:.3g} 이상인 절단점이 없습니다"))
    if min_sens is not None:
        sel = _selector("min_sens", min_sens)
        pt = sel(points)
        add("min_sens", f"민감도 ≥ {min_sens:.3g} 중 특이도 최대", "min_sens", pt, True, sel,
            note=("" if pt else f"민감도 {min_sens:.3g} 이상인 절단점이 없습니다"))

    for c in cutoffs:
        oriented_cut = -c if flipped else c
        pt = point_for_cutoff(oriented, dataset.positive, oriented_cut)
        op = "<=" if flipped else ">="
        add(f"cutoff:{c}", f"지정 절단점 ({dataset.score_name} {op} {c:g})", "cutoff",
            pt, False)

    return Analysis(
        dataset=dataset, oriented=oriented, flipped=flipped, direction_source=how,
        alpha=alpha, auc=auc, points=points, selected=selected,
        prevalence_user=prevalence, warnings=warnings,
    )


def add_comparison(analysis: Analysis, comparator: str, direction: str = "auto",
                   alpha: float = 0.05) -> AucComparison:
    """Paired DeLong comparison of the main marker against another column."""
    ds = analysis.dataset
    if comparator not in ds.extra:
        raise LoadError(f"비교 열 '{comparator}' 이(가) 데이터에 없습니다 / comparator not loaded")
    other = ds.extra[comparator]
    other_oriented, other_flipped, other_how = orient(other, ds.positive, direction)
    if other_how == "auto" and other_flipped:
        analysis.warnings.append(
            f"비교 검사 '{comparator}' 의 방향도 데이터를 보고 자동으로 뒤집었습니다. "
            f"자동 방향 선택은 그 검사의 AUC를 위쪽으로 부풀리므로 AUC 차이 검정도 "
            f"영향을 받습니다 — --direction 으로 명시하세요 (comparator auto-flipped)"
        )
    pos_a = [s for s, p in zip(analysis.oriented, ds.positive) if p]
    neg_a = [s for s, p in zip(analysis.oriented, ds.positive) if not p]
    pos_b = [s for s, p in zip(other_oriented, ds.positive) if p]
    neg_b = [s for s, p in zip(other_oriented, ds.positive) if not p]
    cmp_ = compare_paired(pos_a, neg_a, pos_b, neg_b, ds.score_name, comparator, alpha)
    analysis.comparison_flipped[comparator] = other_flipped
    analysis.comparisons.append(cmp_)
    return cmp_
