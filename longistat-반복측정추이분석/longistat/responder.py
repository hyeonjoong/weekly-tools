"""Responder / MCID analysis and the Jacobson–Truax reliable change index.

A group mean that moves by 3 points is not the same claim as "48 % of patients
improved by at least the minimal clinically important difference".  Reviewers of
clinical trials ask for the second one, so this module turns each subject's
change from baseline into a binary responder status and compares the rates:

* responder rate per group with a Wilson 95 % CI;
* risk difference (Newcombe hybrid-score CI), risk ratio, odds ratio, NNT;
* Fisher's exact test (default for the small 2 × 2 tables trials produce) or
  Pearson χ²;
* the reliable change index (RCI), which asks whether an individual's change is
  larger than the measurement error of the instrument itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .basics import adjust, sd, wilson_interval
from .dataio import Panel
from .describe import ALL_LABEL
from .special import chi2_sf, norm_ppf

__all__ = [
    "ResponderRate", "RateContrast", "ResponderResult", "RCIRow", "RCIResult",
    "responder_analysis", "rci_analysis", "fisher_exact_2x2", "chi2_2x2",
    "improvement",
]


def improvement(baseline: float, follow: float, lower_is_better: bool) -> float:
    """Signed *benefit*: positive means the patient got better."""
    return (baseline - follow) if lower_is_better else (follow - baseline)


# --------------------------------------------------------------------------
# 2 x 2 tests
# --------------------------------------------------------------------------

def _log_binom(n: int, k: int) -> float:
    return (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1))


def fisher_exact_2x2(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p (sum of tables no more likely than observed)."""
    if min(a, b, c, d) < 0:
        raise ValueError("빈도는 0 이상이어야 합니다.")
    r1, r2 = a + b, c + d
    c1 = a + c
    n = r1 + r2
    if r1 == 0 or r2 == 0 or c1 == 0 or c1 == n:
        return 1.0
    log_den = _log_binom(n, c1)

    def log_prob(x: int) -> float:
        return _log_binom(r1, x) + _log_binom(r2, c1 - x) - log_den

    lo = max(0, c1 - r2)
    hi = min(r1, c1)
    observed = log_prob(a)
    tol = 1e-9
    total = 0.0
    for x in range(lo, hi + 1):
        lp = log_prob(x)
        if lp <= observed + tol:
            total += math.exp(lp)
    return min(1.0, total)


def chi2_2x2(a: int, b: int, c: int, d: int, yates: bool = False
             ) -> Tuple[float, float]:
    """Pearson χ² for a 2 × 2 table; returns ``(chi2, p)``."""
    n = a + b + c + d
    r1, r2, c1, c2 = a + b, c + d, a + c, b + d
    if min(r1, r2, c1, c2) == 0:
        return float("nan"), 1.0
    num = abs(a * d - b * c)
    if yates:
        num = max(0.0, num - n / 2.0)
    chi2 = n * num * num / (r1 * r2 * c1 * c2)
    return chi2, chi2_sf(chi2, 1)


def _newcombe_rd(r1: int, n1: int, r2: int, n2: int, alpha: float
                 ) -> Tuple[float, float, float]:
    """Risk difference with Newcombe's hybrid-score interval (method 10)."""
    p1, p2 = r1 / n1, r2 / n2
    l1, u1 = wilson_interval(r1, n1, alpha)
    l2, u2 = wilson_interval(r2, n2, alpha)
    diff = p1 - p2
    lo = diff - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = diff + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return diff, max(-1.0, lo), min(1.0, hi)


def _ratio_ci(r1: int, n1: int, r2: int, n2: int, alpha: float, odds: bool
              ) -> Tuple[float, float, float]:
    """Risk ratio or odds ratio, with a log-scale interval.

    The Haldane 0.5 continuity correction is used **only for the standard
    error**.  Applying it to the point estimate too is asymmetric whenever
    n1 ≠ n2: two arms with identical 100 % response (5/5 vs 10/10) came out as
    ``OR = 0.52`` printed right next to ``RD = +0.0 %``.  When the raw estimate
    is genuinely undefined (a zero denominator) it is reported as NaN rather
    than as a corrected number pretending to be an estimate.
    """
    a, b, c, d = float(r1), float(n1 - r1), float(r2), float(n2 - r2)
    z = norm_ppf(1.0 - alpha / 2.0)
    if odds:
        est = (a * d) / (b * c) if b > 0 and c > 0 else float("nan")
    else:
        est = (a / n1) / (c / n2) if c > 0 else float("nan")
    ca, cb, cc, cd = (a, b, c, d)
    if min(a, b, c, d) == 0:
        ca, cb, cc, cd = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    if odds:
        se = math.sqrt(1 / ca + 1 / cb + 1 / cc + 1 / cd)
        centre = (ca * cd) / (cb * cc)
    else:
        na, nb = ca + cb, cc + cd
        se = math.sqrt(1 / ca - 1 / na + 1 / cc - 1 / nb)
        centre = (ca / na) / (cc / nb)
    log_c = math.log(centre)
    return est, math.exp(log_c - z * se), math.exp(log_c + z * se)


# --------------------------------------------------------------------------
# responder analysis
# --------------------------------------------------------------------------

@dataclass
class ResponderRate:
    group: str
    time: str
    n: int
    responders: int
    rate: float
    ci_low: float
    ci_high: float


@dataclass
class RateContrast:
    time: str
    group_a: str
    group_b: str
    rate_a: float
    rate_b: float
    risk_difference: float
    rd_ci: Tuple[float, float]
    risk_ratio: float
    rr_ci: Tuple[float, float]
    odds_ratio: float
    or_ci: Tuple[float, float]
    nnt: float
    nnt_note: str
    p_raw: float
    p_adj: float
    method: str


@dataclass
class ResponderResult:
    baseline: str
    threshold: float
    kind: str                       # "절대" or "%"
    lower_is_better: bool
    nri: bool = False               # dropouts counted as non-responders?
    rates: List[ResponderRate] = field(default_factory=list)
    contrasts: List[RateContrast] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def responder_analysis(panel: Panel, baseline: int, threshold: float,
                       lower_is_better: bool = True, percent: bool = False,
                       alpha: float = 0.05, correction: str = "holm",
                       test: str = "fisher", nri: bool = False
                       ) -> ResponderResult:
    """Responder rates at every post-baseline visit, and their contrasts.

    ``threshold`` is the MCID: an absolute improvement (default) or, with
    ``percent=True``, a percent improvement relative to the subject's own
    baseline.  ``lower_is_better`` is required for scales like ISI or PHQ-9
    where improvement means the score goes *down*.

    ``nri`` switches the denominator from observed completers to everyone with
    a baseline measurement, counting dropouts as non-responders
    (non-responder imputation).  That is the conservative ITT-style rate a
    confirmatory report is usually asked for; the default observed-case rate
    flatters the treatment when dropout is informative.
    """
    if not 0 <= baseline < panel.n_times:
        raise ValueError("기준 시점 색인이 범위를 벗어났습니다.")
    if threshold <= 0:
        raise ValueError("MCID 임계값은 0보다 커야 합니다.")
    res = ResponderResult(baseline=panel.times[baseline], threshold=threshold,
                          kind="%" if percent else "절대",
                          lower_is_better=lower_is_better, nri=nri)
    scopes: List[Tuple[str, List[int]]] = [
        (ALL_LABEL, list(range(panel.n_subjects)))]
    if panel.groups is not None:
        for lab in panel.group_labels():
            scopes.append((lab, [i for i, g in enumerate(panel.groups)
                                 if g == lab]))

    counts: Dict[Tuple[str, int], Tuple[int, int]] = {}
    zero_baseline = 0
    for label, idx in scopes:
        # 전체 covers every subject exactly once; per-group scopes would count
        # the same excluded observation again.
        count_exclusions = label == ALL_LABEL
        for j in range(panel.n_times):
            if j == baseline:
                continue
            n = 0
            hit = 0
            for i in idx:
                base = panel.values[i][baseline]
                post = panel.values[i][j]
                if base is None:
                    continue
                if post is None:
                    # Non-responder imputation keeps the randomised denominator.
                    if nri:
                        n += 1
                    continue
                gain = improvement(float(base), float(post), lower_is_better)
                if percent:
                    if base == 0:
                        zero_baseline += count_exclusions
                        continue
                    gain = gain / abs(float(base)) * 100.0
                n += 1
                if gain >= threshold:
                    hit += 1
            if n == 0:
                continue
            counts[(label, j)] = (hit, n)
            lo, hi = wilson_interval(hit, n, alpha)
            res.rates.append(ResponderRate(
                group=label, time=panel.times[j], n=n, responders=hit,
                rate=hit / n, ci_low=lo, ci_high=hi))
    if zero_baseline:
        res.notes.append(
            f"기준값이 0이라 %개선을 계산할 수 없는 관측 {zero_baseline}건을 "
            "제외했습니다.")

    if panel.groups is not None and len(panel.group_labels()) >= 2:
        labels = panel.group_labels()
        contrasts: List[RateContrast] = []
        for a in range(len(labels)):
            for b in range(a + 1, len(labels)):
                for j in range(panel.n_times):
                    if j == baseline:
                        continue
                    ga = counts.get((labels[a], j))
                    gb = counts.get((labels[b], j))
                    if not ga or not gb:
                        continue
                    r1, n1 = ga
                    r2, n2 = gb
                    rd, rd_lo, rd_hi = _newcombe_rd(r1, n1, r2, n2, alpha)
                    rr, rr_lo, rr_hi = _ratio_ci(r1, n1, r2, n2, alpha, False)
                    orr, or_lo, or_hi = _ratio_ci(r1, n1, r2, n2, alpha, True)
                    if test == "chi2":
                        _, p = chi2_2x2(r1, n1 - r1, r2, n2 - r2)
                        method = "Pearson χ²"
                    else:
                        p = fisher_exact_2x2(r1, n1 - r1, r2, n2 - r2)
                        method = "Fisher 정확검정"
                    if rd == 0:
                        nnt, note = float("inf"), "반응률 차이가 0"
                    else:
                        nnt = 1.0 / abs(rd)
                        note = "NNT(이득)" if rd > 0 else "NNH(불리)"
                        if rd_lo <= 0 <= rd_hi:
                            note += " — 95% CI가 0을 포함하므로 NNT 구간은 무한대까지"
                    contrasts.append(RateContrast(
                        time=panel.times[j], group_a=labels[a], group_b=labels[b],
                        rate_a=r1 / n1, rate_b=r2 / n2, risk_difference=rd,
                        rd_ci=(rd_lo, rd_hi), risk_ratio=rr, rr_ci=(rr_lo, rr_hi),
                        odds_ratio=orr, or_ci=(or_lo, or_hi), nnt=nnt,
                        nnt_note=note, p_raw=p, p_adj=float("nan"),
                        method=method))
        for row, padj in zip(contrasts,
                             adjust([r.p_raw for r in contrasts], correction)):
            row.p_adj = padj
        res.contrasts = contrasts
    return res


# --------------------------------------------------------------------------
# reliable change index
# --------------------------------------------------------------------------

@dataclass
class RCIRow:
    group: str
    time: str
    n: int
    improved: int
    unchanged: int
    deteriorated: int
    recovered: Optional[int] = None


@dataclass
class RCIResult:
    baseline: str
    reliability: float
    sd_baseline: float
    sd_supplied: bool
    lower_is_better: bool
    s_diff: float
    cutoff: float
    recovery_cutoff: Optional[float]
    rows: List[RCIRow] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def recovery_label(self) -> str:
        """How the recovery criterion should be *printed*.

        The comparison direction follows ``--direction``; a hard-coded ``≤``
        published an inverted criterion for higher-is-better scales.
        """
        if self.recovery_cutoff is None:
            return ""
        op = "≤" if self.lower_is_better else "≥"
        return f"회복({op}{self.recovery_cutoff:.2f})"


def rci_analysis(panel: Panel, baseline: int, reliability: float,
                 lower_is_better: bool = True,
                 sd_baseline: Optional[float] = None,
                 cutoff: float = 1.96,
                 recovery_cutoff: Optional[float] = None) -> RCIResult:
    """Jacobson–Truax reliable change index.

    ``S_diff = √2 · SD_baseline · √(1 − r_xx)`` is the standard error of the
    *difference*; a change larger than ``cutoff · S_diff`` (1.96 → 95 %) is
    larger than the instrument's own measurement error and so is called
    *reliable*.  ``sd_baseline`` defaults to the observed baseline SD of the
    whole sample; supply the published normative SD when you have it.
    """
    if not 0.0 < reliability < 1.0:
        raise ValueError("신뢰도(--reliability)는 0과 1 사이여야 합니다.")
    base_vals = [float(v) for v in panel.column(baseline)]
    supplied = sd_baseline is not None
    if sd_baseline is None:
        sd_baseline = sd(base_vals)
    if not math.isfinite(sd_baseline) or sd_baseline <= 0:
        raise ValueError("기준시점 표준편차를 계산할 수 없습니다 "
                         "(--rci-sd 로 직접 지정하세요).")
    se_meas = sd_baseline * math.sqrt(1.0 - reliability)
    s_diff = math.sqrt(2.0) * se_meas
    res = RCIResult(baseline=panel.times[baseline], reliability=reliability,
                    sd_baseline=sd_baseline, sd_supplied=supplied,
                    lower_is_better=lower_is_better, s_diff=s_diff,
                    cutoff=cutoff, recovery_cutoff=recovery_cutoff)
    if not supplied and len(base_vals) < 10:
        res.notes.append(
            "기준시점 표본이 작아 관측 SD가 불안정합니다 — 가능하면 "
            "--rci-sd 로 규준 SD를 지정하세요.")

    scopes: List[Tuple[str, List[int]]] = [
        (ALL_LABEL, list(range(panel.n_subjects)))]
    if panel.groups is not None:
        for lab in panel.group_labels():
            scopes.append((lab, [i for i, g in enumerate(panel.groups)
                                 if g == lab]))
    for label, idx in scopes:
        for j in range(panel.n_times):
            if j == baseline:
                continue
            up = down = same = 0
            recovered = 0
            n = 0
            for i in idx:
                base = panel.values[i][baseline]
                post = panel.values[i][j]
                if base is None or post is None:
                    continue
                n += 1
                gain = improvement(float(base), float(post), lower_is_better)
                rci = gain / s_diff
                if rci >= cutoff:
                    up += 1
                    if recovery_cutoff is not None:
                        crossed = (float(post) <= recovery_cutoff
                                   if lower_is_better
                                   else float(post) >= recovery_cutoff)
                        if crossed:
                            recovered += 1
                elif rci <= -cutoff:
                    down += 1
                else:
                    same += 1
            if n:
                res.rows.append(RCIRow(
                    group=label, time=panel.times[j], n=n, improved=up,
                    unchanged=same, deteriorated=down,
                    recovered=recovered if recovery_cutoff is not None else None))
    return res
