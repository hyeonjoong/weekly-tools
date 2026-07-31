"""Orchestration: turn a :class:`Panel` into a complete longitudinal analysis.

Design choice worth knowing about: the parametric **and** the rank-based track
are always computed.  Auto-switching on a normality test is fragile — it
over-rejects in large samples and has no power in small ones — so instead both
answers are produced, one is marked 권장 (recommended) with its reason, and the
report shows the other as a cross-check.  If the two disagree, that disagreement
is itself the finding, and hiding it would be dishonest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .ancova import AncovaResult, ancova_analysis
from .anova import RMAnovaResult, rm_anova
from .basics import adjust, mean
from .dataio import Panel
from .describe import ALL_LABEL, Cell, MissingReport, describe, profile_missing
from .nonparam import FriedmanResult, friedman_by_group
from .normality import MAX_RELIABLE_N, shapiro_wilk
from .posthoc import (ChangeAnalysis, GroupComparison, PairComparison,
                      between_at_time, change_analysis, pairwise_times)
from .responder import RCIResult, ResponderResult, rci_analysis, responder_analysis

__all__ = ["Analysis", "NormalityRow", "Options", "analyze"]


@dataclass
class NormalityRow:
    what: str
    label: str
    n: int
    w: float
    p_raw: float
    p_adj: float


@dataclass
class Options:
    alpha: float = 0.05
    alpha_norm: float = 0.05
    correction: str = "holm"
    sphericity: str = "auto"           # auto | gg | hf | none
    method: str = "auto"               # auto | parametric | nonparametric
    baseline: Optional[str] = None
    welch: bool = True
    mcid: Optional[float] = None
    mcid_percent: bool = False
    direction: Optional[str] = None    # "lower" | "higher" (improvement side)
    responder_test: str = "fisher"
    reliability: Optional[float] = None
    rci_sd: Optional[float] = None
    rci_cutoff: float = 1.96
    recovery_cutoff: Optional[float] = None
    primary_time: Optional[str] = None     # pre-specified primary visit
    all_pairs: bool = False                # keep every visit pair at large k
    responder_denominator: str = "observed"   # observed | randomized (NRI)
    labels_en: Dict[str, str] = field(default_factory=dict)  # 한글 라벨 → English


@dataclass
class Analysis:
    panel: Panel
    options: Options
    baseline_index: int
    descriptives: List[Cell]
    missing: MissingReport
    normality: List[NormalityRow]
    anova: Optional[RMAnovaResult]
    anova_error: Optional[str]
    friedman: List[FriedmanResult]
    pairwise_param: List[PairComparison]
    pairwise_rank: List[PairComparison]
    between: List[GroupComparison]
    change_param: ChangeAnalysis
    change_rank: ChangeAnalysis
    ancova: Optional[AncovaResult]
    responder: Optional[ResponderResult]
    rci: Optional[RCIResult]
    recommended: str                       # "parametric" | "nonparametric"
    recommendation_reason: str
    correction_used: str                   # sphericity correction actually applied
    warnings: List[str] = field(default_factory=list)

    @property
    def grouped(self) -> bool:
        return self.panel.groups is not None and len(self.panel.group_labels()) > 1


def _baseline_index(panel: Panel, name: Optional[str]) -> int:
    if name is None:
        return 0
    for j, t in enumerate(panel.times):
        if t == name:
            return j
    raise ValueError(
        f"--baseline '{name}' 을(를) 시점 목록 {panel.times} 에서 찾을 수 없습니다.")


def _normality(panel: Panel, baseline: int, alpha: float
               ) -> Tuple[List[NormalityRow], bool]:
    """Shapiro–Wilk on within-group residuals and on change scores.

    Returns the rows plus whether normality is rejected anywhere after a Holm
    adjustment across all the tests performed.
    """
    rows: List[NormalityRow] = []
    labels = panel.group_labels() if panel.groups is not None else [""]

    for j, tname in enumerate(panel.times):
        resid: List[float] = []
        for lab in labels:
            idx = [i for i in range(panel.n_subjects)
                   if (panel.groups is None or panel.groups[i] == lab)
                   and panel.values[i][j] is not None]
            vals = [float(panel.values[i][j]) for i in idx]
            if len(vals) >= 2:
                m = mean(vals)
                resid.extend(v - m for v in vals)
        if len(resid) >= 3:
            try:
                w, p = shapiro_wilk(resid)
            except ValueError:
                continue
            rows.append(NormalityRow("시점 잔차", tname, len(resid), w, p, float("nan")))

    for j, tname in enumerate(panel.times):
        if j == baseline:
            continue
        # Centre the change scores *within arm*.  Pooling them across arms
        # makes a real treatment effect look like bimodality, and Shapiro then
        # rejects normality because the arms differ, not because either is skewed.
        diffs: List[float] = []
        for lab in labels:
            per_group = [
                float(panel.values[i][j]) - float(panel.values[i][baseline])
                for i in range(panel.n_subjects)
                if (panel.groups is None or panel.groups[i] == lab)
                and panel.values[i][j] is not None
                and panel.values[i][baseline] is not None]
            if len(per_group) >= 2:
                m = mean(per_group)
                diffs.extend(v - m for v in per_group)
        if len(diffs) >= 3:
            try:
                w, p = shapiro_wilk(diffs)
            except ValueError:
                continue
            rows.append(NormalityRow(
                "변화량 잔차", f"{tname} − {panel.times[baseline]}", len(diffs),
                w, p, float("nan")))

    for row, padj in zip(rows, adjust([r.p_raw for r in rows], "holm")):
        row.p_adj = padj
    rejected = any(r.p_adj < alpha for r in rows)
    return rows, rejected


def _smallest_cell(panel: Panel) -> int:
    labels = panel.group_labels() if panel.groups is not None else [""]
    smallest = panel.n_subjects
    for lab in labels:
        for j in range(panel.n_times):
            n = sum(1 for i in range(panel.n_subjects)
                    if (panel.groups is None or panel.groups[i] == lab)
                    and panel.values[i][j] is not None)
            smallest = min(smallest, n)
    return smallest


def analyze(panel: Panel, options: Optional[Options] = None) -> Analysis:
    """Run the full longitudinal analysis pipeline on *panel*."""
    opt = options or Options()
    if panel.n_times < 2:
        raise ValueError("시점이 2개 이상이어야 반복측정 분석을 할 수 있습니다.")
    baseline = _baseline_index(panel, opt.baseline)
    warnings: List[str] = list(panel.notes)

    desc = describe(panel, opt.alpha)
    miss = profile_missing(panel)
    warnings.extend(miss.warnings)

    norm_rows, norm_rejected = _normality(panel, baseline, opt.alpha_norm)
    smallest = _smallest_cell(panel)

    # ---- omnibus ---------------------------------------------------------
    cc = panel.complete_case()
    anova: Optional[RMAnovaResult] = None
    anova_error: Optional[str] = None
    if panel.groups is not None:
        lost = [g for g in panel.group_labels()
                if g not in (cc.groups or [])]
        if lost:
            warnings.append(
                f"완전자료가 한 명도 없는 그룹이 있어({', '.join(lost)}) "
                "혼합 ANOVA의 그룹·상호작용 효과를 추정할 수 없습니다 — "
                "아래 ANOVA 표는 남은 그룹만의 반복측정 분석입니다.")
    if cc.n_subjects >= 2:
        try:
            anova = rm_anova(cc.matrix(), cc.times, cc.groups)
        except (ValueError, ArithmeticError) as exc:
            anova_error = str(exc)
    else:
        anova_error = ("모든 시점이 관측된 대상이 2명 미만이라 반복측정 ANOVA를 "
                       "수행할 수 없습니다.")
    fried = friedman_by_group(panel)

    # ---- follow-ups ------------------------------------------------------
    pair_param = pairwise_times(panel, opt.alpha, opt.correction, False,
                                baseline=baseline, all_pairs=opt.all_pairs)
    pair_rank = pairwise_times(panel, opt.alpha, opt.correction, True,
                               baseline=baseline, all_pairs=opt.all_pairs)
    change_param = change_analysis(panel, baseline, opt.alpha, opt.correction,
                                   False, opt.welch, opt.primary_time)
    change_rank = change_analysis(panel, baseline, opt.alpha, opt.correction,
                                  True, opt.welch, opt.primary_time)
    ancova = ancova_analysis(panel, baseline, opt.alpha, opt.correction,
                             opt.primary_time)

    # ---- responder / RCI -------------------------------------------------
    responder: Optional[ResponderResult] = None
    rci: Optional[RCIResult] = None
    if opt.mcid is not None or opt.reliability is not None:
        if opt.direction not in ("lower", "higher"):
            raise ValueError(
                "--mcid 또는 --reliability 를 쓸 때는 --direction lower|higher "
                "로 '어느 쪽이 좋아지는 것인지' 반드시 지정해야 합니다 "
                "(예: ISI 불면중증도는 낮을수록 좋으므로 lower).")
    lower_better = opt.direction == "lower"
    if opt.mcid is not None:
        responder = responder_analysis(
            panel, baseline, opt.mcid, lower_better, opt.mcid_percent,
            opt.alpha, opt.correction, opt.responder_test,
            nri=opt.responder_denominator == "randomized")
    if opt.reliability is not None:
        rci = rci_analysis(panel, baseline, opt.reliability, lower_better,
                           opt.rci_sd, opt.rci_cutoff, opt.recovery_cutoff)

    # ---- which track to recommend ---------------------------------------
    _ = smallest
    if opt.method == "parametric":
        recommended, reason = "parametric", "사용자가 --method parametric 을 지정했습니다."
    elif opt.method == "nonparametric":
        recommended, reason = ("nonparametric",
                               "사용자가 --method nonparametric 을 지정했습니다.")
    elif norm_rejected and smallest < 30:
        recommended = "nonparametric"
        reason = (f"Shapiro–Wilk(Holm 보정)에서 정규성이 기각되었고 가장 작은 "
                  f"셀의 n={smallest}(<30)이라 중심극한정리에 기대기 어렵습니다.")
    elif norm_rejected:
        recommended = "parametric"
        reason = (f"정규성은 기각되었지만 가장 작은 셀의 n={smallest}(≥30)이라 "
                  "t/F 검정이 충분히 강건합니다. 순위검정 결과도 함께 확인하세요.")
    else:
        recommended = "parametric"
        reason = "Shapiro–Wilk(Holm 보정)에서 정규성 위배 근거가 없습니다."

    # The per-visit group comparison follows the recommended track, so the
    # report never labels a Welch t as a rank test (or the reverse).
    between = between_at_time(panel, opt.alpha, opt.correction,
                              nonparametric=recommended == "nonparametric",
                              welch=opt.welch, baseline=baseline)

    # ---- sphericity correction actually applied --------------------------
    if anova is None:
        correction_used = "none"
    elif opt.sphericity == "auto":
        correction_used = anova.sphericity.recommended(opt.alpha_norm)
    else:
        correction_used = opt.sphericity
        if correction_used in ("gg", "hf") and not anova.sphericity.epsilon_ok:
            correction_used = "none"

    if any(r.n > MAX_RELIABLE_N for r in norm_rows):
        warnings.append(
            f"Shapiro–Wilk 근사는 n ≤ {MAX_RELIABLE_N} 에서 검증된 것입니다 — "
            "표본이 그보다 크면 사소한 이탈도 기각되니 p값보다 그림·왜도를 "
            "함께 보세요.")
    if opt.primary_time is None and panel.groups is not None \
            and len([t for t in panel.times]) > 2:
        warnings.append(
            "주요 시점(--primary-time)을 지정하지 않아 모든 방문의 군간 비교에 "
            "다중비교 보정을 적용했습니다. 계획서에 주요 시점이 있다면 "
            "--primary-time 으로 지정하세요 (그 시점은 보정 없이 보고).")
    if smallest < 3:
        warnings.append(
            f"가장 작은 (그룹 × 시점) 셀의 관측 수가 {smallest}명입니다 — "
            "추정치가 매우 불안정합니다.")
    if anova is not None and correction_used == "none" \
            and anova.sphericity.epsilon_ok \
            and (anova.sphericity.violated(opt.alpha_norm)
                 or not anova.sphericity.mauchly_ok):
        warnings.append(
            "구형성이 기각되었는데 보정을 끄셨습니다 (--sphericity none). "
            "시점 관련 p값이 실제보다 작게(유의하게) 나옵니다.")

    return Analysis(
        panel=panel, options=opt, baseline_index=baseline, descriptives=desc,
        missing=miss, normality=norm_rows, anova=anova, anova_error=anova_error,
        friedman=fried, pairwise_param=pair_param, pairwise_rank=pair_rank,
        between=between, change_param=change_param, change_rank=change_rank,
        ancova=ancova, responder=responder, rci=rci, recommended=recommended,
        recommendation_reason=reason, correction_used=correction_used,
        warnings=warnings)
