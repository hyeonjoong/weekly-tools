"""Per-timepoint (and per-group) descriptive statistics and dropout profiling."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .basics import ci_mean, iqr, mean, median, sd, sem
from .dataio import Panel

__all__ = ["Cell", "describe", "MissingReport", "profile_missing", "ALL_LABEL"]

ALL_LABEL = "전체"


@dataclass
class Cell:
    """Descriptives of one (group, timepoint) cell, computed on observed values."""

    group: str
    time: str
    n: int
    n_missing: int
    mean: float
    sd: float
    sem: float
    ci_low: float
    ci_high: float
    median: float
    q1: float
    q3: float
    minimum: float
    maximum: float


def describe(panel: Panel, alpha: float = 0.05) -> List[Cell]:
    """Available-case descriptives for every (group, timepoint) cell.

    The ``전체`` row is always present; per-group rows are added when the panel
    carries a group column.  Available-case (not complete-case) on purpose: the
    descriptive table should show what was actually measured at each visit,
    while the inferential tests state their own complete-case N.
    """
    cells: List[Cell] = []
    groups: List[Tuple[str, List[int]]] = [
        (ALL_LABEL, list(range(panel.n_subjects)))]
    if panel.groups is not None:
        for lab in panel.group_labels():
            groups.append((lab, [i for i, g in enumerate(panel.groups)
                                 if g == lab]))
    for label, idx in groups:
        for j, tname in enumerate(panel.times):
            obs = [panel.values[i][j] for i in idx
                   if panel.values[i][j] is not None]
            obs = [float(v) for v in obs]
            lo, hi = ci_mean(obs, alpha) if len(obs) >= 2 else (
                float("nan"), float("nan"))
            q1, q3 = iqr(obs) if obs else (float("nan"), float("nan"))
            cells.append(Cell(
                group=label, time=tname, n=len(obs), n_missing=len(idx) - len(obs),
                mean=mean(obs), sd=sd(obs), sem=sem(obs), ci_low=lo, ci_high=hi,
                median=median(obs), q1=q1, q3=q3,
                minimum=min(obs) if obs else float("nan"),
                maximum=max(obs) if obs else float("nan")))
    return cells


@dataclass
class MissingReport:
    n_subjects: int
    n_complete: int
    per_time_observed: Dict[str, int]
    per_time_by_group: Dict[str, Dict[str, int]]       # group -> time -> n
    per_group_complete: Dict[str, Tuple[int, int]]     # group -> (complete, total)
    patterns: List[Tuple[str, int]]                    # "1101" -> count
    monotone: bool
    warnings: List[str]

    @property
    def complete_fraction(self) -> float:
        return self.n_complete / self.n_subjects if self.n_subjects else float("nan")


def profile_missing(panel: Panel,
                    mmrm_available: bool = True) -> MissingReport:
    """Describe how much data is missing, and whether it looks like dropout.

    A *monotone* pattern (once missing, always missing) is the signature of
    study dropout; a non-monotone one means intermittent missed visits, which
    changes what an analyst should do about it.  Retention is reported **per
    arm**: differential dropout is the first thing a trial statistician looks
    for, and it is the pattern under which a completer analysis is most biased.

    *mmrm_available* only steers the advice: with ``--no-mmrm`` the report has
    no ``[4c]`` section, so pointing the reader at one would be a dead link.
    """
    n = panel.n_subjects
    per_time = {t: len(panel.column(j)) for j, t in enumerate(panel.times)}
    patterns: Dict[str, int] = {}
    monotone = True
    for row in panel.values:
        key = "".join("1" if v is not None else "0" for v in row)
        patterns[key] = patterns.get(key, 0) + 1
        seen_missing = False
        for ch in key:
            if ch == "0":
                seen_missing = True
            elif seen_missing:
                monotone = False
                break
    complete_idx = set(panel.complete_rows())
    per_group: Dict[str, Tuple[int, int]] = {}
    per_time_group: Dict[str, Dict[str, int]] = {}
    labels = panel.group_labels() if panel.groups is not None else []
    for lab in labels:
        idx = [i for i, g in enumerate(panel.groups or []) if g == lab]
        per_group[lab] = (sum(1 for i in idx if i in complete_idx), len(idx))
        per_time_group[lab] = {
            t: sum(1 for i in idx if panel.values[i][j] is not None)
            for j, t in enumerate(panel.times)}
    per_group[ALL_LABEL] = (len(complete_idx), n)
    per_time_group[ALL_LABEL] = dict(per_time)

    warnings: List[str] = []
    frac = len(complete_idx) / n if n else 0.0
    if len(complete_idx) < n:
        warnings.append(
            f"결측 때문에 반복측정 ANOVA는 완전자료 {len(complete_idx)}명"
            f"(전체 {n}명 중 {frac:.0%})만 사용합니다.")
    # <= 0.8, not < 0.8: a trial planned around 20 % dropout lands exactly on
    # the boundary, and the strict test left it with no substantive warning.
    if 0 < frac <= 0.8:
        warnings.append(
            "완전자료 비율이 80% 이하입니다. 완전사례(completer) 분석은 탈락이 "
            "무작위가 아닐 때 편향됩니다 — 확증적 주분석으로 쓰지 말고 "
            + ("[4c] MMRM(부분 관측 대상을 그대로 사용) 결과를 함께 보세요."
               if mmrm_available else
               "혼합효과모형이나 다중대체를 고려하세요 "
               "(--no-mmrm 을 빼면 MMRM 구획이 나옵니다)."))
    if len(labels) >= 2:
        rates = {lab: (c / t if t else float("nan"))
                 for lab, (c, t) in per_group.items() if lab != ALL_LABEL}
        finite = [v for v in rates.values() if v == v]
        if finite and (max(finite) - min(finite)) >= 0.10:
            detail = ", ".join(f"{lab} {v:.0%}" for lab, v in rates.items())
            warnings.append(
                f"군별 완전자료 비율이 10%포인트 이상 차이납니다 ({detail}). "
                "차등 탈락(differential dropout)은 완전사례 분석의 군간 비교를 "
                "직접적으로 편향시킵니다 — 탈락 사유를 함께 보고하세요.")
    if len(complete_idx) < n and not monotone:
        warnings.append(
            "결측이 단조(dropout) 패턴이 아닙니다 — 중간 방문 누락이 섞여 "
            "있습니다.")
    return MissingReport(
        n_subjects=n, n_complete=len(complete_idx), per_time_observed=per_time,
        per_time_by_group=per_time_group, per_group_complete=per_group,
        patterns=sorted(patterns.items(), key=lambda kv: (-kv[1], kv[0])),
        monotone=monotone, warnings=warnings)
