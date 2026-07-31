"""Missing-data sensitivity analysis — LOCF and BOCF re-runs of the main estimate.

Everything else in longistat that involves two timepoints is an **observed-case**
analysis: a subject who missed week 8 contributes nothing to week 8.  That is the
honest default, but it is also an assumption (missing at random, roughly), and a
reviewer or a regulator will ask what happens if it is wrong.  The conventional
minimum answer — required by ICH E9 and its addendum in the sense that *some*
sensitivity analysis is expected, and still the one most often tabulated — is to
re-run the primary contrast under deterministic single imputation:

* **LOCF** (last observation carried forward): a missing visit inherits the
  subject's most recent earlier observation.  Assumes the patient froze at the
  moment they left.
* **BOCF** (baseline observation carried forward): a missing visit reverts to the
  subject's own baseline.  Assumes any benefit vanished on dropout — the
  conservative reading for an improving outcome.

Neither is a good *primary* analysis; both understate uncertainty because the
imputed values are treated as though they were measured, and both can bias in
either direction.  They earn their place as a **stability check**: if the
observed-case, LOCF and BOCF answers all point the same way, dropout is not
driving the conclusion, and that sentence is worth a paragraph in the discussion.
If they disagree, the report says so instead of leaving the reader to guess, and
that disagreement is the signal to reach for a proper MMRM in R or SAS.

Baseline itself is never imputed — a subject with no baseline has no change score
to impute *toward*, and inventing one would silently manufacture the primary
endpoint.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .dataio import Panel
from .describe import ALL_LABEL
from .posthoc import change_analysis

__all__ = ["SensitivityRow", "SensitivityResult", "impute_panel",
           "sensitivity_analysis", "KIND_LABEL"]

KIND_LABEL = {"observed": "관측값(기본)", "locf": "LOCF", "bocf": "BOCF"}
KIND_EN = {"observed": "observed cases", "locf": "LOCF", "bocf": "BOCF"}
VALID_KINDS = ("locf", "bocf")


def impute_panel(panel: Panel, baseline: int, kind: str) -> Panel:
    """A copy of *panel* with post-baseline gaps filled by *kind*.

    ``locf`` walks each subject's row in visit order and carries the most recent
    observed value forward; ``bocf`` fills every gap from the subject's own
    baseline.  A subject whose baseline is missing keeps their gaps under both.

    Neither method touches a visit that comes *before* the baseline (possible
    when ``--baseline`` names a later visit than the first): LOCF has nothing to
    carry there, and letting BOCF fill it alone would mean the two methods
    imputed different cell sets, so the "대체 셀" counts would differ for a
    reason that has nothing to do with dropout.
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"알 수 없는 대체 방법입니다: {kind}")
    if not 0 <= baseline < panel.n_times:
        raise ValueError("기준 시점 색인이 범위를 벗어났습니다.")

    values: List[List[Optional[float]]] = []
    for row in panel.values:
        new = list(row)
        base = new[baseline]
        carried = base
        if base is None:
            # No baseline, no change score to impute toward: leave the row alone
            # rather than filling cells that no analysis can use anyway.
            values.append(new)
            continue
        for j in range(baseline + 1, panel.n_times):
            if new[j] is not None:
                if kind == "locf":
                    carried = new[j]
                continue
            new[j] = base if kind == "bocf" else carried
        values.append(new)
    return Panel(
        subjects=list(panel.subjects), times=list(panel.times), values=values,
        groups=None if panel.groups is None else list(panel.groups),
        group_name=panel.group_name, value_name=panel.value_name,
        time_name=panel.time_name, id_name=panel.id_name, notes=list(panel.notes))


@dataclass
class SensitivityRow:
    kind: str                        # observed | locf | bocf
    time: str
    contrast: str                    # "A − B" or the group name when ungrouped
    n: int
    estimate: float
    ci_low: float
    ci_high: float
    p: float
    imputed: int                     # cells filled at this visit by this method


@dataclass
class SensitivityResult:
    kinds: List[str]
    baseline: str
    grouped: bool
    rows: List[SensitivityRow] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def flips(self, alpha: float) -> List[str]:
        """Where an imputation changes the conclusion — or cannot be compared.

        Only the *conclusion* is compared, not the point estimate: a shift of
        0.3 points is noise, a shift from "significant benefit" to "no
        difference" is the finding.  A sign reversal counts only when at least
        one of the two results is significant, otherwise two emphatically null
        estimates straddling zero would raise a false alarm.

        Rows that have **no observed-case counterpart** (every subject was
        imputed at that visit) are reported as *incomparable* rather than
        skipped: silently dropping them let :func:`report.apa_sentences` write
        "the sensitivity analyses agreed" about a visit that has no
        observed-case answer to agree with.
        """
        out: List[str] = []
        base = {(r.time, r.contrast): r for r in self.rows
                if r.kind == "observed"}
        seen_incomparable = set()
        for r in self.rows:
            if r.kind == "observed":
                continue
            ref = base.get((r.time, r.contrast))
            if ref is None or not math.isfinite(ref.p) or not math.isfinite(r.p):
                key = (r.time, r.contrast)
                if key not in seen_incomparable:
                    seen_incomparable.add(key)
                    out.append(
                        f"{r.time} {r.contrast}: 비교할 관측값 결과가 없어 "
                        "(또는 검정할 수 없어) 민감도 판정을 할 수 없습니다.")
                continue
            if (ref.p < alpha) != (r.p < alpha):
                out.append(
                    f"{r.time} {r.contrast}: 관측값 p {ref.p:.3f} vs "
                    f"{KIND_LABEL[r.kind]} p {r.p:.3f} — 유의성 판정이 뒤집힙니다.")
            elif (ref.p < alpha or r.p < alpha) \
                    and math.isfinite(ref.estimate) \
                    and math.isfinite(r.estimate) \
                    and ref.estimate * r.estimate < 0:
                out.append(
                    f"{r.time} {r.contrast}: 관측값과 {KIND_LABEL[r.kind]}에서 "
                    "추정치의 부호가 반대입니다 (한쪽은 유의).")
        return out


def _n_imputed(panel: Panel, imputed: Panel, j: int,
               arms: Optional[Sequence[str]] = None) -> int:
    """Cells filled at visit *j*, counted **within the row's own arms**.

    Counting study-wide put the same total next to every contrast: in a
    three-arm trial with one imputed cell in A and one in C, the ``B − C`` row
    claimed 2 imputed out of its n = 4, when only 1 of its subjects was.
    """
    labels = panel.groups
    return sum(1 for i in range(panel.n_subjects)
               if panel.values[i][j] is None
               and imputed.values[i][j] is not None
               and (arms is None or labels is None or labels[i] in arms))


def _collect(kind: str, panel: Panel, source: Panel, baseline: int,
             alpha: float, welch: bool, grouped: bool) -> List[SensitivityRow]:
    """Rows for one imputation variant, from the same change-score machinery."""
    # No multiplicity correction here on purpose: this table exists to be
    # compared row-for-row with the primary analysis, and Holm-adjusting a
    # different number of rows would make the columns incomparable.
    ch = change_analysis(panel, baseline, alpha, "none", False, welch, None)
    rows: List[SensitivityRow] = []
    index = {t: j for j, t in enumerate(panel.times)}
    if grouped:
        for c in ch.between:
            rows.append(SensitivityRow(
                kind=kind, time=c.time, contrast=f"{c.group_a} − {c.group_b}",
                n=c.n_a + c.n_b, estimate=c.diff, ci_low=c.ci_low,
                ci_high=c.ci_high, p=c.p_raw,
                imputed=_n_imputed(source, panel, index[c.time],
                                   (c.group_a, c.group_b))))
    else:
        for r in ch.within:
            if r.group != ALL_LABEL:
                continue
            rows.append(SensitivityRow(
                kind=kind, time=r.time, contrast=r.group, n=r.n,
                estimate=r.mean_change, ci_low=r.ci_low, ci_high=r.ci_high,
                p=r.p_raw, imputed=_n_imputed(source, panel, index[r.time])))
    return rows


def sensitivity_analysis(panel: Panel, baseline: int = 0,
                         kinds: Sequence[str] = VALID_KINDS,
                         alpha: float = 0.05, welch: bool = True
                         ) -> Optional[SensitivityResult]:
    """Re-run the primary change-from-baseline estimate under each imputation.

    Returns ``None`` when there is nothing to impute (no post-baseline gap) or
    when no requested method fills a single cell — an empty table that repeats
    the primary analysis three times is worse than no table.
    """
    kinds = [k for k in kinds if k in VALID_KINDS]
    if not kinds:
        return None
    if not 0 <= baseline < panel.n_times:
        raise ValueError("기준 시점 색인이 범위를 벗어났습니다.")
    gaps = sum(1 for i in range(panel.n_subjects)
               for j in range(panel.n_times)
               if j != baseline and panel.values[i][j] is None)
    if gaps == 0:
        return None

    grouped = panel.groups is not None and len(panel.group_labels()) > 1
    res = SensitivityResult(kinds=list(kinds),
                            baseline=panel.times[baseline], grouped=grouped)
    res.rows.extend(_collect("observed", panel, panel, baseline, alpha, welch,
                             grouped))
    filled_any = False
    for kind in kinds:
        imputed = impute_panel(panel, baseline, kind)
        rows = _collect(kind, imputed, panel, baseline, alpha, welch, grouped)
        if any(r.imputed for r in rows):
            filled_any = True
        res.rows.extend(rows)
    if not res.rows or not filled_any:
        return None

    mostly = sorted({r.time for r in res.rows
                     if r.kind != "observed" and r.n and r.imputed * 2 > r.n},
                    key=lambda t: panel.times.index(t))
    if mostly:
        res.notes.append(
            f"{', '.join(mostly)} 은(는) 절반 이상이 대체값입니다 — 그 행은 "
            "측정 결과라기보다 가정의 결과에 가깝습니다.")
    no_base = sum(1 for row in panel.values if row[baseline] is None)
    if no_base:
        res.notes.append(
            f"기준시점이 결측인 {no_base}명은 어떤 방법으로도 대체하지 "
            "않았습니다 (기저값 자체를 만들어내지 않습니다).")
    res.notes.append(
        "이 표의 p는 세 분석을 나란히 비교하려고 다중비교 보정 없이 계산한 "
        "값입니다 — 위의 보정 p와 다를 수 있습니다.")
    res.notes.append(
        "단일 대체는 불확실성을 과소평가합니다 — 주분석이 아니라 결론이 "
        "탈락에 흔들리는지 보는 안정성 점검으로만 쓰세요.")
    return res
