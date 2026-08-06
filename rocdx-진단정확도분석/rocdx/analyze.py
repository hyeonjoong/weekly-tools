"""Putting it together: clean the data, fit the ROC, pick operating points.

This module owns two things the maths modules deliberately do not: the
*direction* of the marker (which end means disease) and the *honesty* of a
cut-off that was chosen on the same data it is reported on.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .delong import (
    AucComparison,
    AucEstimate,
    NonInferiority,
    compare_paired,
    estimate_auc,
    noninferiority,
)
from .loader import (
    LoadError,
    Table,
    infer_decimal_comma,
    parse_label_column,
    parse_number,
    resolve_column,
)
from .pauc import PartialAuc, partial_auc, spec_range_to_fpr
from .stats_core import holm_adjust, var_ddof1
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
    "CurveBootstrap",
    "Analysis",
    "load_dataset",
    "orient",
    "analyze",
    "percentile_ci",
    "bootstrap_curve",
    "stratified_resamples",
    "finalize_comparisons",
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
    path: str = ""                  # input file, for the machine-readable record
    cluster_name: str = ""          # column that identifies the independent unit
    clusters: List[str] = field(default_factory=list)  # one per kept row

    @property
    def n_pos(self) -> int:
        return sum(1 for p in self.positive if p)

    @property
    def n_neg(self) -> int:
        return len(self.positive) - self.n_pos

    @property
    def n_clusters(self) -> int:
        return len(set(self.clusters)) if self.clusters else 0

    @property
    def max_cluster_size(self) -> int:
        if not self.clusters:
            return 0
        counts: Dict[str, int] = {}
        for c in self.clusters:
            counts[c] = counts.get(c, 0) + 1
        return max(counts.values())


def _cell_shape(text: str, limit: int = 16) -> str:
    """Describe an unusable cell by its *shape*, never by its contents.

    The report has to say something about a cell it could not read, or the user
    cannot find it. Echoing the text — even a 12-character prefix — leaks: a
    three-syllable Korean name fits whole, and the first 12 characters of a
    resident registration number are a full date of birth plus five ID digits.
    Worse, ``--json`` writes these notes into a *file* that gets emailed around.

    So each character is replaced by its class (한글 → 가, letter → A, digit → 9)
    and only structural punctuation survives. "홍길동 010-1234-5678" becomes
    "가가가 999-9999-9999", which is exactly as useful for finding the column and
    carries no identity at all.
    """
    out: List[str] = []
    for ch in text[:limit]:
        if "가" <= ch <= "힣" or "ᄀ" <= ch <= "ᇿ":
            out.append("가")
        elif ch.isdigit():
            out.append("9")
        elif ch.isalpha():
            out.append("A")
        elif ch in " -.,:;/_<>=+%()[]#'\"":
            out.append(ch)
        else:
            out.append("?")
    shape = "".join(out)
    if len(text) > limit:
        shape += "…"
    return f"{shape} ({len(text)}자)"


def _canon_cluster_id(raw: str) -> str:
    """Normalise a cluster ID the same way the outcome column is normalised.

    A patient ID column exported from pandas with one missing value comes back as
    ``1.0``/``2.0`` in some rows and ``1``/``2`` in others. Matching those
    byte-exactly split one patient into two "independent" units, which is the one
    thing the cluster bootstrap exists to prevent. Trailing/leading space is
    stripped; case is deliberately NOT folded, because "P01" and "p01" can be two
    real patients in a hand-typed column.
    """
    t = (raw or "").strip()
    if not t:
        return ""
    try:
        f = float(t)
    except (ValueError, OverflowError):
        return t
    return str(int(f)) if f == int(f) else t


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
                # Shape only, never content: this string reaches the report *and*
                # the --json file, and a clinical cell can hold a name or an MRN.
                col.unparsed_examples.append(_cell_shape(raw.strip()))
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
                 compare_cols: Sequence[str] = (),
                 cluster_col: Optional[str] = None) -> Dataset:
    """Parse the score/outcome (and comparator) columns and drop unusable rows.

    A row is kept only when every requested column has a usable value, so the
    marker and its comparator are always compared on exactly the same subjects.
    ``cluster_col`` names the column identifying the independent unit (a patient
    ID when the same patient contributes several rows, a site ID for multicentre
    data); it is carried along for the cluster bootstrap and is never used as a
    marker. Rows whose cluster ID is blank get their own singleton cluster, which
    is the conservative reading — merging them would invent dependence.
    """
    score_name = resolve_column(table, score_col)
    truth_name = resolve_column(table, truth_col)
    # Two spellings of one column ("--compare b1 --compare B1", or a name plus
    # "#4") resolve to the same header. Keeping both would append two values per
    # row to a single comparator list and crash downstream with an IndexError, so
    # duplicates are dropped here and reported.
    comp_names: List[str] = []
    dup_comps: List[str] = []
    for c in compare_cols:
        resolved = resolve_column(table, c)
        if resolved in comp_names or resolved == resolve_column(table, score_col):
            dup_comps.append(resolved)
            continue
        comp_names.append(resolved)
    cluster_name = resolve_column(table, cluster_col) if cluster_col else ""

    score = _parse_numeric_column(table, score_name)
    comps = [_parse_numeric_column(table, c) for c in comp_names]
    labels = parse_label_column(table.column(truth_name), positive_label, negative_label)
    cluster_cells = table.column(cluster_name) if cluster_name else []

    scores: List[float] = []
    positive: List[bool] = []
    clusters: List[str] = []
    extra: Dict[str, List[float]] = {c.name: [] for c in comps}
    reasons: Dict[str, int] = {}
    n_blank_cluster = 0
    n_canon_cluster = 0

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
        if cluster_name:
            raw_cid = (cluster_cells[i] or "").strip() if i < len(cluster_cells) else ""
            cid = _canon_cluster_id(raw_cid)
            if not cid:
                n_blank_cluster += 1
                cid = f"\x00row{i}"  # unnameable on purpose: cannot collide with real IDs
            elif cid != raw_cid:
                n_canon_cluster += 1
            clusters.append(cid)
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
    if labels.n_folded_negative and len(labels.folded_negative) > 1:
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
    if dup_comps:
        shown = ", ".join(dict.fromkeys(dup_comps))
        notes.append(
            f"--compare 에 같은 열이 여러 번(또는 검사값 열과 같이) 지정되어 "
            f"중복을 제외했습니다: {shown} (duplicate comparator columns dropped)"
        )
    if n_canon_cluster:
        notes.append(
            f"'{cluster_name}' 값 {n_canon_cluster}건은 숫자 표기를 통일해 같은 단위로 "
            f"묶었습니다 (예: '1.0' → '1'; numeric cluster IDs canonicalised)"
        )
    if n_blank_cluster:
        notes.append(
            f"'{cluster_name}' 열이 빈 행 {n_blank_cluster}건은 각각 독립된 군집으로 "
            f"처리했습니다 (blank cluster IDs treated as singleton clusters — they are "
            f"not merged into one group)"
        )

    return Dataset(
        score_name=score_name, truth_name=truth_name, scores=scores, positive=positive,
        positive_label=labels.positive_label, negative_label=labels.negative_label,
        n_rows_in=len(table.rows), n_dropped=len(table.rows) - len(scores),
        drop_reasons=reasons, encoding=table.encoding, delimiter=table.delimiter,
        notes=notes, extra=extra, path=table.path,
        cluster_name=cluster_name, clusters=clusters,
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


def stratified_resamples(positive: Sequence[bool], n_boot: int,
                         seed: int) -> Optional[List[List[int]]]:
    """The resample index lists a stratified bootstrap would draw.

    Built once and reused for every operating point. Each selected point used to
    re-derive these from the same seed, so a run with ``--min-spec --min-sens``
    drew four byte-identical sets of resamples and did four times the work for
    identical numbers. The draws (and therefore every reported interval) are
    unchanged — only the repetition is gone.
    """
    pos_idx = [i for i, p in enumerate(positive) if p]
    neg_idx = [i for i, p in enumerate(positive) if not p]
    if n_boot <= 0 or len(pos_idx) < 2 or len(neg_idx) < 2:
        return None
    rng = random.Random(seed)
    return [[rng.choice(pos_idx) for _ in pos_idx]
            + [rng.choice(neg_idx) for _ in neg_idx]
            for _ in range(n_boot)]


def bootstrap_selected_point(scores: Sequence[float], positive: Sequence[bool],
                             selector: Selector, n_boot: int, seed: int,
                             alpha: float,
                             resamples: Optional[Sequence[Sequence[int]]] = None
                             ) -> Optional[BootstrapSummary]:
    """Stratified bootstrap of the *whole* procedure: reselect, then re-evaluate.

    Each resample repeats the cut-off search and the resulting cut-off is then
    applied to the *original* data. The percentile intervals are built from that
    out-of-sample evaluation, not from the resample's own (optimistic) numbers —
    scoring a cut-off on the data that chose it would centre the interval on
    ``apparent + optimism`` and cover the truth far below the nominal rate. The
    same pairing gives Harrell's optimism estimate.
    """
    if resamples is None:
        resamples = stratified_resamples(positive, n_boot, seed)
    if resamples is None:
        return None
    cuts: List[float] = []
    senss: List[float] = []
    specs: List[float] = []
    js: List[float] = []
    opt_j: List[float] = []
    opt_sens: List[float] = []
    opt_spec: List[float] = []

    for idx in resamples:
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
class CurveBootstrap:
    """Resampling CI for whole-curve summaries (AUC, pAUC).

    ``kind`` is ``"stratified"`` (cases and controls resampled separately — the
    case-control design the DeLong interval also assumes) or ``"cluster"`` (whole
    clusters resampled, which is the only one of the two that is honest when a
    patient contributes more than one row).
    """

    kind: str
    n_boot: int
    n_effective: int
    seed: int
    auc_ci: Optional[Tuple[float, float]]
    auc_se: Optional[float]
    pauc_ci: Optional[Tuple[float, float]] = None
    pauc_area_ci: Optional[Tuple[float, float]] = None
    n_clusters: int = 0
    max_cluster_size: int = 0
    # every resample gave the identical AUC, so no interval is defined
    degenerate: bool = False


def bootstrap_curve(scores: Sequence[float], positive: Sequence[bool],
                    n_boot: int, seed: int, alpha: float,
                    fpr_range: Optional[Tuple[float, float]] = None,
                    clusters: Optional[Sequence[str]] = None) -> Optional[CurveBootstrap]:
    """Percentile intervals for the AUC (and pAUC) by resampling.

    Without ``clusters`` this is the usual stratified case-control bootstrap.
    With ``clusters`` whole clusters are drawn with replacement, so a patient who
    contributes three rows enters or leaves the resample as a unit — the
    correlation between their rows then shows up in the interval width instead of
    being silently assumed away. Resamples that end up with only one group, or
    with a single member in a group, are skipped and counted.
    """
    from .roc import auc_from_scores

    if n_boot <= 0:
        return None
    rng = random.Random(seed)
    pos_idx = [i for i, p in enumerate(positive) if p]
    neg_idx = [i for i, p in enumerate(positive) if not p]
    if len(pos_idx) < 2 or len(neg_idx) < 2:
        return None

    groups: List[List[int]] = []
    if clusters is not None:
        by_id: Dict[str, List[int]] = {}
        for i, c in enumerate(clusters):
            by_id.setdefault(c, []).append(i)
        groups = list(by_id.values())
        if len(groups) < 2:
            return None

    aucs: List[float] = []
    paucs: List[float] = []
    pauc_areas: List[float] = []
    for _ in range(n_boot):
        if clusters is not None:
            idx: List[int] = []
            for _k in range(len(groups)):
                idx.extend(groups[rng.randrange(len(groups))])
        else:
            idx = ([rng.choice(pos_idx) for _ in pos_idx]
                   + [rng.choice(neg_idx) for _ in neg_idx])
        s = [scores[i] for i in idx]
        p = [positive[i] for i in idx]
        n_p = sum(1 for q in p if q)
        if n_p < 2 or len(p) - n_p < 2:
            continue
        a = auc_from_scores(s, p)
        if not math.isfinite(a):
            continue
        aucs.append(a)
        if fpr_range is not None:
            pa = partial_auc(s, p, fpr_range[0], fpr_range[1])
            if math.isfinite(pa.standardized):
                paucs.append(pa.standardized)
                pauc_areas.append(pa.area)

    if not aucs:
        return None
    degenerate = len(set(round(a, 12) for a in aucs)) < 2
    # Same minimum-draw rule as percentile_ci: reporting an SE from a handful of
    # resamples beside a withheld interval invites the reader to use the SE.
    enough = len(aucs) >= 2.0 / alpha - 1.0
    se = math.sqrt(var_ddof1(aucs)) if (len(aucs) > 1 and enough) else None
    n_clusters = len(groups) if clusters is not None else 0
    max_size = max((len(g) for g in groups), default=0) if clusters is not None else 0
    ci = None if degenerate else percentile_ci(aucs, alpha)
    return CurveBootstrap(
        kind="cluster" if clusters is not None else "stratified",
        n_boot=n_boot, n_effective=len(aucs), seed=seed,
        auc_ci=ci, auc_se=se, degenerate=degenerate,
        pauc_ci=percentile_ci(paucs, alpha) if paucs else None,
        pauc_area_ci=percentile_ci(pauc_areas, alpha) if pauc_areas else None,
        n_clusters=n_clusters, max_cluster_size=max_size,
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
    pauc: Optional[PartialAuc] = None
    curve_boot: Optional[CurveBootstrap] = None
    # comparator label -> Holm-adjusted p (only when >1 comparison was made)
    comparison_p_adjusted: Dict[str, Optional[float]] = field(default_factory=dict)
    # how many comparisons Holm actually corrected for (untestable ones excluded)
    holm_family_size: int = 0
    # comparator label -> non-inferiority verdict (only with --ni-margin)
    noninferiority: Dict[str, NonInferiority] = field(default_factory=dict)

    def cutoff_in_original_units(self, threshold: float) -> Tuple[float, str]:
        """Map an oriented threshold back to the user's scale and its operator."""
        if self.flipped:
            return (-threshold, "<=")
        return (threshold, ">=")


def analyze(dataset: Dataset, direction: str = "auto", alpha: float = 0.05,
            prevalence: Optional[float] = None, min_spec: Optional[float] = None,
            min_sens: Optional[float] = None, cutoffs: Sequence[float] = (),
            n_boot: int = 0, seed: int = 20260806,
            ci_method: str = "logit",
            pauc_min_spec: Optional[float] = None,
            pauc_max_spec: float = 1.0,
            cluster: bool = False) -> Analysis:
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
    # One set of draws for every operating point (identical results, 3-5x less work).
    point_resamples = (stratified_resamples(dataset.positive, n_boot, seed)
                       if n_boot > 0 else None)

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
                                            n_boot, seed, alpha,
                                            resamples=point_resamples)
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

    # --- partial AUC over the clinically usable part of the curve -------------
    pauc = None
    fpr_range: Optional[Tuple[float, float]] = None
    if pauc_min_spec is not None:
        if not 0.0 <= pauc_min_spec < 1.0:
            raise LoadError("--pauc-min-spec 는 0 이상 1 미만이어야 합니다 / "
                            "must satisfy 0 <= min_spec < 1")
        if not 0.0 < pauc_max_spec <= 1.0:
            raise LoadError("--pauc-max-spec 는 0 초과 1 이하여야 합니다 / "
                            "must satisfy 0 < max_spec <= 1")
        if pauc_min_spec >= pauc_max_spec:
            raise LoadError("--pauc-min-spec 는 --pauc-max-spec 보다 작아야 합니다 / "
                            "min_spec must be strictly below max_spec")
        fpr_range = spec_range_to_fpr(pauc_min_spec, pauc_max_spec)
        pauc = partial_auc(oriented, dataset.positive, fpr_range[0], fpr_range[1])
        # How well the region is *resolved*: distinct false-positive rates the
        # data actually reaches inside it. Many operating points can share one
        # FPR (every threshold above the highest control has FPR 0), and those
        # add no information about the shape of the curve in the region.
        band_fprs = {round(1.0 - p.spec, 12) for p in points
                     if not math.isnan(p.spec)
                     and fpr_range[0] - 1e-12 <= 1.0 - p.spec <= fpr_range[1] + 1e-12}
        n_in_band = len(band_fprs)
        pauc = replace(pauc, n_observed_fprs=n_in_band)
        std = pauc.standardized
        if math.isfinite(std) and std < 0.5:
            warnings.append(
                f"부분 AUC 구간(특이도 {pauc_min_spec:.3g}–{pauc_max_spec:.3g})에서 "
                f"표준화 pAUC가 {std:.3f}로 우연(0.5)보다 낮습니다 — 이 구간에서는 검사가 "
                f"우연보다 못합니다. McClish 표준화는 0.5=우연, 1.0=완벽이지만 아래로는 "
                f"경계가 없어 좁은 고위양성률 구간에서는 음수까지 내려갈 수 있습니다 "
                f"(standardised pAUC below chance; the scale is bounded above by 1 "
                f"but not below)"
            )
        if n_in_band < 3:
            warnings.append(
                f"부분 AUC 구간(특이도 {pauc_min_spec:.3g}–{pauc_max_spec:.3g}) 안에서 "
                f"실제로 관측된 위양성률이 {n_in_band}가지뿐입니다(비질환군 {n_neg}명). "
                f"구간 대부분이 보간(interpolation)으로 채워지므로 부분 AUC가 매우 "
                f"불안정합니다 (the pAUC region is resolved by only {n_in_band} observed "
                f"false-positive rate(s))"
            )

    # --- resampling interval for the whole curve ------------------------------
    curve_boot = None
    use_cluster = bool(cluster and dataset.clusters)
    if n_boot > 0 and (fpr_range is not None or use_cluster):
        curve_boot = bootstrap_curve(
            oriented, dataset.positive, n_boot, seed, alpha,
            fpr_range=fpr_range,
            clusters=list(dataset.clusters) if use_cluster else None,
        )
    if curve_boot is not None and curve_boot.degenerate:
        warnings.append(
            f"군집/부트스트랩 재표본 {curve_boot.n_effective}회가 모두 동일한 AUC를 "
            f"주었습니다(군집 {curve_boot.n_clusters or '—'}개). 폭이 0인 구간은 "
            f"신뢰구간이 아니므로 표시하지 않습니다 — 군집 수가 너무 적습니다 "
            f"(degenerate resampling distribution; no interval reported)"
        )
    elif curve_boot is not None and curve_boot.kind == "cluster" \
            and curve_boot.n_clusters < 20:
        warnings.append(
            f"군집이 {curve_boot.n_clusters}개뿐입니다. 군집 부트스트랩 구간은 군집 수가 "
            f"적으면(경험적으로 20개 미만) 매우 불안정하며 실제 포함률이 명목값보다 "
            f"낮습니다 (few clusters — the cluster bootstrap interval is unreliable)"
        )
    if pauc is not None and curve_boot is not None:
        pauc = replace(pauc, ci=curve_boot.pauc_ci, area_ci=curve_boot.pauc_area_ci,
                       ci_source=("cluster-bootstrap" if curve_boot.kind == "cluster"
                                  else "bootstrap"),
                       n_boot=curve_boot.n_boot, n_effective=curve_boot.n_effective)
    if pauc is not None and (curve_boot is None or curve_boot.pauc_ci is None):
        if n_boot <= 0:
            warnings.append(
                "부분 AUC에는 해석적 신뢰구간을 제공하지 않습니다 — --bootstrap 2000 을 함께 "
                "지정하면 부트스트랩 백분위 구간을 계산합니다 (no analytic CI for the pAUC; "
                "add --bootstrap for a percentile interval)"
            )
        else:
            need = int(math.ceil(2.0 / alpha - 1.0))
            warnings.append(
                f"--bootstrap {n_boot} 을 지정했지만 부분 AUC 구간을 만들지 못했습니다 — "
                f"백분위 구간에는 사용 가능한 재표본이 최소 {need}회 필요하고(--alpha "
                f"{alpha:g} 기준), 군집이 2개 미만이거나 한쪽 군이 너무 작으면 재표본이 "
                f"모두 버려집니다 (requested resamples produced no usable pAUC interval)"
            )

    if dataset.clusters:
        n_cl, n_rows = dataset.n_clusters, len(dataset.scores)
        if n_cl < n_rows:
            # Which way the independence assumption errs depends on the design,
            # so the warning must not assert a direction. Repeated measures on one
            # patient (rows sharing an outcome) make DeLong too NARROW; a unit that
            # contributes one case *and* one control (both eyes, matched pairs)
            # makes it too WIDE. Simulation puts the paired design at 100%
            # coverage and the repeated-measures design at 83%.
            mixed = _clusters_mix_outcomes(dataset)
            direction_txt = (
                "한 단위 안에 질환군과 비질환군이 섞여 있어(짝지은 설계) DeLong 구간이 "
                "오히려 넓어질 수 있습니다"
                if mixed else
                "한 단위의 행들이 같은 결과를 공유하므로 DeLong 구간은 실제보다 좁습니다"
            )
            if use_cluster:
                warnings.append(
                    f"'{dataset.cluster_name}' 기준 군집 {n_cl}개에 {n_rows}행이 "
                    f"들어 있습니다(최대 {dataset.max_cluster_size}행/군집). DeLong "
                    f"신뢰구간은 행끼리 독립이라고 가정합니다 — {direction_txt}. 어느 "
                    f"쪽이든 군집 부트스트랩 구간을 보고하세요 (rows are not independent; "
                    f"the DeLong interval is biased, in a direction that depends on "
                    f"the design)"
                )
            else:
                warnings.append(
                    f"'{dataset.cluster_name}' 값이 중복됩니다 — {n_rows}행이 군집 "
                    f"{n_cl}개에서 나왔습니다(최대 {dataset.max_cluster_size}행/군집). "
                    f"{direction_txt}. --cluster --bootstrap 2000 을 지정하면 군집 보정 "
                    f"신뢰구간을 계산합니다 (duplicate IDs: rows are not independent)"
                )
        elif use_cluster:
            warnings.append(
                f"'{dataset.cluster_name}' 값이 모두 서로 달라 보정할 군집 구조가 "
                f"없습니다. 이때 군집 부트스트랩은 행 단위 재표본이 되어 질환군/비질환군을 "
                f"층화하지 않으므로, 기본 층화 부트스트랩과 완전히 같지는 않습니다(균형 "
                f"자료에서는 차이가 미미하지만 희귀질환 설계에서는 벌어질 수 있습니다) "
                f"(no clustering to correct for; this resample is unstratified)"
            )

    return Analysis(
        dataset=dataset, oriented=oriented, flipped=flipped, direction_source=how,
        alpha=alpha, auc=auc, points=points, selected=selected,
        prevalence_user=prevalence, warnings=warnings,
        pauc=pauc, curve_boot=curve_boot,
    )


def _clusters_mix_outcomes(dataset: Dataset) -> bool:
    """Does any cluster hold both a case and a control?

    That single fact decides which way the independence assumption errs, so it is
    worth computing rather than guessing (see the warning that uses it).
    """
    seen: Dict[str, bool] = {}
    for cid, pos in zip(dataset.clusters, dataset.positive):
        if cid in seen and seen[cid] != pos:
            return True
        seen.setdefault(cid, pos)
    return False


def finalize_comparisons(analysis: Analysis, ni_margin: Optional[float] = None) -> None:
    """Apply multiplicity correction and the non-inferiority verdict, once.

    Called after every ``add_comparison``: Holm needs the whole family of
    p-values at once, and a family of one needs no correction at all (reporting
    an "adjusted" p identical to the raw one only invites the reader to think a
    correction happened).
    """
    cmps = analysis.comparisons
    analysis.comparison_p_adjusted = {}
    analysis.holm_family_size = 0
    if len(cmps) > 1:
        adj = holm_adjust([c.p_value for c in cmps])
        analysis.comparison_p_adjusted = {c.label_b: a for c, a in zip(cmps, adj)}
        analysis.holm_family_size = sum(
            1 for c in cmps
            if c.p_value is not None and not math.isnan(c.p_value))
    analysis.noninferiority = {}
    if ni_margin is not None:
        for c in cmps:
            analysis.noninferiority[c.label_b] = noninferiority(
                c, ni_margin, analysis.alpha)


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
