"""Tipping-point (delta-adjusted) sensitivity analysis for departures from MAR.

LOCF and BOCF (:mod:`longistat.sensitivity`) answer "what if dropouts froze?".
They do **not** answer the question a regulator actually asks under ICH E9(R1):

    *How much worse would the missing patients have had to do before the
    conclusion changes?*

That number — the **tipping point** — is what this module computes.  The recipe
is the standard delta-adjustment / "shift parameter" one:

1. Fill every post-baseline gap under a **MAR-consistent** rule: the subject's
   own baseline plus the mean change observed *in their own arm at that visit*.
   With no shift this reproduces, in expectation, the observed-case estimate,
   so δ = 0 is the honest starting point rather than an arbitrary one.
2. Add a shift **δ** to the imputed values of one arm only, in the direction
   that erodes that arm's apparent advantage (i.e. as though its dropouts did
   worse than the completers who stayed).  Observed values are never touched.
3. Increase δ until the contrast stops being significant.  The smallest such δ
   is reported, together with what it means in units the reader has: multiples
   of the MCID when one was supplied, and multiples of the observed SD of
   change otherwise.

Reading the answer
------------------
* **δ\\* small relative to the MCID** (say under 0.5 × MCID) — a clinically
  trivial amount of unmeasured worsening among dropouts overturns the result.
  The finding is fragile and the paper should say so.
* **δ\\* large, or none found in the searched range** — even implausible
  assumptions about the dropouts leave the conclusion standing.  That is a
  strong robustness sentence, and it is worth one line in the discussion.

Honest limits (also printed in the report):

* The imputation is **deterministic**, not multiple imputation, so the
  intervals here understate uncertainty exactly the way LOCF/BOCF do.  The
  tipping point is a *stress test*, never a primary analysis; a confirmatory
  submission wants MI-based delta adjustment (SAS ``PROC MI``/``MIANALYZE``,
  R ``mice`` + ``mitml``, or the ``rbmi`` package).
* The search assumes the p-value rises monotonically as δ pushes the estimate
  toward zero.  That holds for the mean shift used here; the implementation
  still brackets by a coarse scan first rather than trusting monotonicity over
  the whole range.
* A subject with a missing **baseline** is never imputed (the same rule as
  LOCF/BOCF): there is no change score to shift.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .basics import mean, sd
from .dataio import Panel
from .describe import ALL_LABEL
from .posthoc import change_analysis

__all__ = ["TippingRow", "TippingResult", "mar_impute", "tipping_analysis"]

# Coarse brackets first, then bisection inside the bracket that flips.  60 × 25
# evaluations of a change-score t-test is milliseconds even for a big panel,
# and it does not rely on p being monotone across the *whole* search range.
_SCAN_STEPS = 60
_BISECT_STEPS = 25
# How far to look, as a multiple of the pooled SD of observed change.  Four SDs
# of extra worsening in the dropouts alone is already beyond anything a
# reviewer would call plausible.
_DEFAULT_MAX_SD = 4.0


def mar_impute(panel: Panel, baseline: int
               ) -> Tuple[Panel, List[List[bool]]]:
    """Fill post-baseline gaps with ``baseline_i + mean change in arm at visit``.

    Returns the filled panel and a mask marking the cells that were invented,
    so a later shift can be applied to *those cells only*.

    Falls back to the study-wide mean change when a subject's own arm has no
    observed change at that visit; leaves the cell missing when nobody in the
    study has one, and when the subject's baseline is itself missing.
    """
    if not 0 <= baseline < panel.n_times:
        raise ValueError("기준 시점 색인이 범위를 벗어났습니다.")

    labels = panel.groups
    # mean observed change per (arm, visit) and study-wide per visit
    per_arm: Dict[Tuple[str, int], float] = {}
    overall: Dict[int, float] = {}
    for j in range(panel.n_times):
        if j == baseline:
            continue
        pool: List[float] = []
        by_arm: Dict[str, List[float]] = {}
        for i in range(panel.n_subjects):
            v, b = panel.values[i][j], panel.values[i][baseline]
            if v is None or b is None:
                continue
            d = float(v) - float(b)
            pool.append(d)
            by_arm.setdefault(ALL_LABEL if labels is None else labels[i],
                              []).append(d)
        if pool:
            overall[j] = mean(pool)
        for lab, vals in by_arm.items():
            per_arm[(lab, j)] = mean(vals)

    values: List[List[Optional[float]]] = []
    mask: List[List[bool]] = []
    for i in range(panel.n_subjects):
        row = list(panel.values[i])
        flags = [False] * panel.n_times
        base = row[baseline]
        if base is not None:
            arm = ALL_LABEL if labels is None else labels[i]
            for j in range(panel.n_times):
                if j == baseline or j < baseline or row[j] is not None:
                    continue
                shift = per_arm.get((arm, j), overall.get(j))
                if shift is None:
                    continue
                row[j] = float(base) + shift
                flags[j] = True
        values.append(row)
        mask.append(flags)
    filled = Panel(
        subjects=list(panel.subjects), times=list(panel.times), values=values,
        groups=None if panel.groups is None else list(panel.groups),
        group_name=panel.group_name, value_name=panel.value_name,
        time_name=panel.time_name, id_name=panel.id_name,
        notes=list(panel.notes),
        covariates=panel.subset_covariates(range(panel.n_subjects)))
    return filled, mask


@dataclass
class TippingRow:
    time: str
    contrast: str                 # "A − B", or the group name when ungrouped
    arm: str                      # arm whose imputed values were shifted
    n_imputed: int                # cells shifted in that arm at that visit
    p0: float                     # p at δ = 0 (MAR imputation, no shift)
    estimate0: float              # contrast estimate at δ = 0
    delta: float                  # δ* — smallest shift that removes significance
    estimate_at_delta: float
    p_at_delta: float
    searched_to: float            # how far the search went
    status: str                   # tipped | robust | already_ns | no_imputed
    per_mcid: Optional[float] = None    # δ* expressed in MCIDs
    per_sd: Optional[float] = None      # δ* expressed in SDs of observed change


@dataclass
class TippingResult:
    baseline: str
    grouped: bool
    sd_change: float              # pooled SD of observed change (the δ yardstick)
    mcid: Optional[float]
    rows: List[TippingRow] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def fragile(self, ratio: float = 0.5) -> List[str]:
        """Rows whose tipping point is small enough to worry about."""
        out: List[str] = []
        for r in self.rows:
            if r.status != "tipped":
                continue
            scale, unit = ((r.per_mcid, "MCID") if r.per_mcid is not None
                           else (r.per_sd, "SD"))
            if scale is not None and math.isfinite(scale) and scale < ratio:
                out.append(
                    f"{r.time} {r.contrast}: 탈락자가 {unit}의 {scale:.2f}배"
                    f"({r.delta:.3g})만 더 나빴어도 유의성이 사라집니다.")
        return out


def _shifted(panel: Panel, mask: List[List[bool]], j: int, arm: Optional[str],
             delta: float) -> Panel:
    """Copy of *panel* with ``delta`` added to imputed cells at visit *j*."""
    if delta == 0.0:
        return panel
    labels = panel.groups
    values: List[List[Optional[float]]] = []
    for i in range(panel.n_subjects):
        row = list(panel.values[i])
        if mask[i][j] and (arm is None or labels is None or labels[i] == arm):
            row[j] = float(row[j]) + delta
        values.append(row)
    return Panel(
        subjects=list(panel.subjects), times=list(panel.times), values=values,
        groups=None if labels is None else list(labels),
        group_name=panel.group_name, value_name=panel.value_name,
        time_name=panel.time_name, id_name=panel.id_name,
        notes=list(panel.notes),
        covariates=panel.subset_covariates(range(panel.n_subjects)))


def _pooled_sd_change(panel: Panel, baseline: int) -> float:
    """SD of observed change scores, centred within arm — the δ yardstick."""
    labels = panel.groups
    resid: List[float] = []
    for j in range(panel.n_times):
        if j == baseline:
            continue
        by_arm: Dict[str, List[float]] = {}
        for i in range(panel.n_subjects):
            v, b = panel.values[i][j], panel.values[i][baseline]
            if v is None or b is None:
                continue
            by_arm.setdefault(ALL_LABEL if labels is None else labels[i],
                              []).append(float(v) - float(b))
        for vals in by_arm.values():
            if len(vals) >= 2:
                m = mean(vals)
                resid.extend(v - m for v in vals)
    if len(resid) >= 2:
        s = sd(resid)
        if math.isfinite(s) and s > 0:
            return s
    return float("nan")


class _Probe:
    """Evaluate one contrast's (estimate, p) at a given δ."""

    def __init__(self, filled: Panel, mask: List[List[bool]], baseline: int,
                 j: int, arm: Optional[str], sign: float, alpha: float,
                 welch: bool, key: Tuple[str, ...], grouped: bool) -> None:
        self.filled, self.mask, self.baseline = filled, mask, baseline
        self.j, self.arm, self.sign = j, arm, sign
        self.alpha, self.welch, self.key, self.grouped = alpha, welch, key, grouped

    def __call__(self, delta: float) -> Tuple[float, float]:
        panel = _shifted(self.filled, self.mask, self.j, self.arm,
                         self.sign * delta)
        # No multiplicity correction: this table is read row-by-row against the
        # unadjusted primary contrast, exactly like the LOCF/BOCF table.
        ch = change_analysis(panel, self.baseline, self.alpha, "none", False,
                             self.welch, None)
        if self.grouped:
            for c in ch.between:
                if (c.time, c.group_a, c.group_b) == self.key:
                    return c.diff, c.p_raw
        else:
            for r in ch.within:
                if (r.time, r.group) == self.key:
                    return r.mean_change, r.p_raw
        return float("nan"), float("nan")


def _search(probe: _Probe, alpha: float, max_delta: float
            ) -> Tuple[Optional[float], float, float]:
    """Smallest δ in ``(0, max_delta]`` with ``p >= alpha``.

    A coarse scan brackets the first crossing; bisection then locates it.  The
    scan comes first on purpose: past the crossing the estimate changes sign and
    p falls again, so a plain bisection anchored at ``max_delta`` could miss the
    bracket entirely.
    """
    step = max_delta / _SCAN_STEPS
    lo = 0.0
    for k in range(1, _SCAN_STEPS + 1):
        hi = step * k
        est, p = probe(hi)
        if math.isnan(p):
            return None, float("nan"), float("nan")
        if p >= alpha:
            for _ in range(_BISECT_STEPS):
                mid = (lo + hi) / 2.0
                _, pm = probe(mid)
                if math.isnan(pm):
                    break
                if pm >= alpha:
                    hi = mid
                else:
                    lo = mid
            est, p = probe(hi)
            return hi, est, p
        lo = hi
    return None, est, p


def tipping_analysis(panel: Panel, baseline: int = 0, alpha: float = 0.05,
                     welch: bool = True, mcid: Optional[float] = None,
                     max_delta: Optional[float] = None
                     ) -> Optional[TippingResult]:
    """How much worse would the dropouts have to be to overturn each contrast?

    Returns ``None`` when there is nothing to shift — no post-baseline gap, or
    no gap that the MAR rule can fill (e.g. every subject missing baseline).
    """
    if not 0 <= baseline < panel.n_times:
        raise ValueError("기준 시점 색인이 범위를 벗어났습니다.")
    if max_delta is not None and (not math.isfinite(max_delta)
                                  or max_delta <= 0):
        raise ValueError("--tipping-max 는 0보다 큰 숫자여야 합니다.")
    filled, mask = mar_impute(panel, baseline)
    if not any(any(row) for row in mask):
        return None

    grouped = panel.groups is not None and len(panel.group_labels()) > 1
    sd_change = _pooled_sd_change(panel, baseline)
    scale = max_delta
    if scale is None:
        scale = (_DEFAULT_MAX_SD * sd_change if math.isfinite(sd_change)
                 else float("nan"))
        if not math.isfinite(scale) or scale <= 0:
            # Degenerate spread (every change identical): fall back to the span
            # of the observed values so the search still has a sane yardstick.
            obs = [float(v) for row in panel.values for v in row if v is not None]
            span = (max(obs) - min(obs)) if len(obs) >= 2 else 0.0
            scale = span if span > 0 else 1.0

    res = TippingResult(baseline=panel.times[baseline], grouped=grouped,
                        sd_change=sd_change, mcid=mcid)
    index = {t: j for j, t in enumerate(panel.times)}
    base = change_analysis(filled, baseline, alpha, "none", False, welch, None)

    targets: List[Tuple[str, str, Optional[str], float, float, float,
                        Tuple[str, ...]]] = []
    if grouped:
        for c in base.between:
            j = index[c.time]
            # Shift whichever arm carries more invented values — that is where
            # the MAR assumption is doing the most work.  Ties go to arm A so
            # the choice is deterministic across runs.
            counts = {}
            for lab in (c.group_a, c.group_b):
                counts[lab] = sum(
                    1 for i in range(panel.n_subjects)
                    if mask[i][j] and panel.groups is not None
                    and panel.groups[i] == lab)
            arm = c.group_a if counts[c.group_a] >= counts[c.group_b] \
                else c.group_b
            n_imp = counts[arm]
            # Move the contrast toward zero: raising arm A raises (A − B).
            toward_zero = -1.0 if c.diff > 0 else 1.0
            sign = toward_zero if arm == c.group_a else -toward_zero
            targets.append((c.time, f"{c.group_a} − {c.group_b}", arm, n_imp,
                            c.diff, sign,
                            (c.time, c.group_a, c.group_b)))
    else:
        for r in base.within:
            if r.group != ALL_LABEL:
                continue
            j = index[r.time]
            n_imp = sum(1 for i in range(panel.n_subjects) if mask[i][j])
            sign = -1.0 if r.mean_change > 0 else 1.0
            targets.append((r.time, r.group, None, n_imp, r.mean_change, sign,
                            (r.time, r.group)))

    for time, contrast, arm, n_imp, est0, sign, key in targets:
        j = index[time]
        probe = _Probe(filled, mask, baseline, j, arm, sign, alpha, welch,
                       key, grouped)
        _, p0 = probe(0.0)
        row = TippingRow(time=time, contrast=contrast,
                         arm=arm or ALL_LABEL, n_imputed=n_imp, p0=p0,
                         estimate0=est0, delta=float("nan"),
                         estimate_at_delta=float("nan"),
                         p_at_delta=float("nan"), searched_to=scale,
                         status="tipped")
        if n_imp == 0:
            row.status = "no_imputed"
        elif math.isnan(p0):
            row.status = "no_imputed"
        elif p0 >= alpha:
            row.status = "already_ns"
            row.delta = 0.0
            row.estimate_at_delta, row.p_at_delta = est0, p0
        else:
            delta, est, p = _search(probe, alpha, scale)
            if delta is None:
                row.status = "robust"
            else:
                row.delta = delta
                row.estimate_at_delta, row.p_at_delta = est, p
                if mcid:
                    row.per_mcid = delta / abs(mcid)
                if math.isfinite(sd_change) and sd_change > 0:
                    row.per_sd = delta / sd_change
        res.rows.append(row)

    if not any(r.status in ("tipped", "robust", "already_ns") for r in res.rows):
        return None

    res.notes.append(
        "δ 는 '대체된 값에만' 더해지는 벌점입니다 — 관측된 값은 건드리지 "
        "않습니다. δ*가 클수록 결론이 탈락 가정에 견고합니다.")
    if math.isfinite(sd_change):
        res.notes.append(
            f"기준 척도: 관측 변화량의 (군내 중심화) SD = {sd_change:.3g}"
            + (f", MCID = {mcid:.3g}" if mcid else ""))
    res.notes.append(
        "δ=0 대체는 '군·시점별 평균 변화'를 넣는 결정적(단일) 대체라 "
        "불확실성을 과소평가합니다 — 확증적 분석에는 다중대체 기반 "
        "delta-adjustment(R rbmi·mice, SAS PROC MI)를 쓰세요.")
    if any(r.status == "robust" for r in res.rows):
        res.notes.append(
            f"'견고'는 δ 를 {scale:.3g} 까지 올려도 유의성이 유지됐다는 "
            "뜻입니다 (--tipping-max 로 범위를 넓힐 수 있습니다).")
    return res
