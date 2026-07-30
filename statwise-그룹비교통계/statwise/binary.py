"""Binary (yes/no) endpoint comparison — response rates, RD / RR / OR, NNT.

Half of the endpoints in a clinical protocol are not continuous at all: they are
*responder* / *adverse event* / *cured* indicators.  For those, the mean and SD
that the continuous side of statwise reports are meaningless; what a trial
report needs is

    - the event rate per arm with a confidence interval,
    - a between-arm comparison test that is valid at the observed cell counts,
    - and an effect measure on the scale the protocol was written in —
      risk difference (absolute), risk ratio or odds ratio (relative),
      plus the number needed to treat.

Test selection mirrors standard practice and is stated in the output:

    2x2, any expected count < 5   -> Fisher's exact test (exact hypergeometric)
    otherwise                     -> Pearson chi-square test of independence

Fisher is a 2x2-only method here, so with three or more arms the chi-square is
used whatever the expected counts are, and the reason line says so.

For a 2x2 table the Yates continuity-corrected chi-square is reported alongside
so that either convention can be quoted.  With three or more arms the omnibus is
the k-group chi-square (df = k-1) with Cramér's V, and, when it is significant,
pairwise 2x2 comparisons with Holm or Benjamini-Hochberg correction.

Interval methods (all chosen because they behave at the boundary, where the
textbook Wald interval fails badly and can even leave [0, 1]):

    proportion       Wilson score interval
    risk difference  Newcombe's hybrid-score interval (his "method 10")
    risk ratio       Katz log interval
    odds ratio       Woolf logit interval

Zero cells make the log-scale RR/OR intervals undefined; when that happens a
Haldane-Anscombe 0.5 continuity correction is applied to the *estimate used for
the interval only*, and the output says so rather than silently printing a
number that came from a different table than the one the reader sees.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .special import chi2_sf, norm_ppf

__all__ = [
    "BinaryGroup",
    "BinaryPairwise",
    "BinaryResult",
    "Estimate",
    "wilson_interval",
    "fisher_exact_2x2",
    "chi_square_contingency",
    "risk_difference",
    "risk_ratio",
    "odds_ratio",
    "number_needed_to_treat",
    "compare_binary",
]


# --------------------------------------------------------------------------
# containers
# --------------------------------------------------------------------------

@dataclass
class BinaryGroup:
    """One arm of a binary endpoint: ``events`` successes out of ``n``."""

    label: str
    events: int
    n: int
    #: cells present in the source file but unusable (blank / NA / unmappable)
    n_missing: int = 0

    def __post_init__(self) -> None:
        if self.n < 0 or self.events < 0:
            raise ValueError(f"group '{self.label}': counts cannot be negative")
        if self.events > self.n:
            raise ValueError(
                f"group '{self.label}': events ({self.events}) cannot exceed "
                f"n ({self.n})")

    @property
    def non_events(self) -> int:
        return self.n - self.events

    @property
    def proportion(self) -> float:
        return self.events / self.n if self.n else float("nan")

    def ci(self, conf: float = 0.95) -> Tuple[float, float]:
        return wilson_interval(self.events, self.n, conf)


@dataclass
class Estimate:
    """A point estimate with a confidence interval and how it was computed."""

    name: str
    value: float
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    conf: float = 0.95
    method: str = ""
    note: str = ""
    magnitude: str = ""


@dataclass
class BinaryPairwise:
    a: str
    b: str
    test: str
    pvalue_raw: float
    pvalue_adj: float
    risk_diff: float
    significant: bool
    #: Newcombe interval for the risk difference. A pairwise table of bare
    #: p-values is not reportable; the reader needs the size of the difference.
    rd_ci_low: Optional[float] = None
    rd_ci_high: Optional[float] = None
    n_a: int = 0
    n_b: int = 0


@dataclass
class BinaryResult:
    groups: List[BinaryGroup]
    alpha: float
    test_name: str
    statistic: Optional[float]
    df: Optional[float]
    pvalue: float
    significant: bool
    reason: str
    expected_min: float
    #: Yates continuity-corrected chi-square p-value (2x2 tables only)
    pvalue_yates: Optional[float] = None
    estimates: List[Estimate] = field(default_factory=list)
    pairwise: List[BinaryPairwise] = field(default_factory=list)
    correction: str = "holm"
    warnings: List[str] = field(default_factory=list)
    endpoint: Optional[str] = None
    pvalue_adj: Optional[float] = None

    @property
    def total_n(self) -> int:
        return sum(g.n for g in self.groups)


# --------------------------------------------------------------------------
# intervals for a single proportion
# --------------------------------------------------------------------------

def _z(conf: float) -> float:
    if not 0.0 < conf < 1.0:
        raise ValueError("conf must be in (0, 1)")
    return norm_ppf(1.0 - (1.0 - conf) / 2.0)


def wilson_interval(events: int, n: int, conf: float = 0.95
                    ) -> Tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Unlike the Wald interval it never leaves [0, 1] and stays sensible at
    ``events == 0`` or ``events == n``, which is exactly where clinical response
    rates like to sit.
    """
    if n <= 0:
        return (float("nan"), float("nan"))
    if events < 0 or events > n:
        raise ValueError("events must be within 0..n")
    z = _z(conf)
    p = events / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    lo, hi = max(0.0, centre - half), min(1.0, centre + half)
    # Snap the exact boundaries: with no events the true Wilson lower limit is
    # 0 and with all events the upper limit is 1, but the algebra above leaves
    # ~1e-17 of rounding dust that would print as a nonsense "0.000000%" bound.
    if events == 0:
        lo = 0.0
    if events == n:
        hi = 1.0
    return (lo, hi)


# --------------------------------------------------------------------------
# omnibus tests
# --------------------------------------------------------------------------

def _log_choose(n: int, k: int) -> float:
    return (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1))


def fisher_exact_2x2(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p-value for the table [[a, b], [c, d]].

    Rows are the two groups, columns are event / non-event.  The two-sided
    p-value sums the hypergeometric probability of every table with the same
    margins whose probability is not greater than the observed one — the same
    convention scipy and R use.
    """
    for name, v in (("a", a), ("b", b), ("c", c), ("d", d)):
        if v < 0:
            raise ValueError(f"cell {name} must be non-negative (got {v})")
    n = a + b + c + d
    if n == 0:
        return float("nan")
    row1, col1 = a + b, a + c
    if row1 == 0 or col1 == 0 or row1 == n or col1 == n:
        return 1.0  # a degenerate margin leaves only one possible table
    log_denom = _log_choose(n, col1)

    def log_p(x: int) -> float:
        return (_log_choose(row1, x) + _log_choose(n - row1, col1 - x)
                - log_denom)

    lo = max(0, col1 - (n - row1))
    hi = min(row1, col1)
    obs = log_p(a)
    # relative tolerance mirrors scipy: tables within rounding of the observed
    # probability count as "as extreme as"
    total = 0.0
    for x in range(lo, hi + 1):
        lp = log_p(x)
        if lp <= obs + 1e-9:
            total += math.exp(lp)
    return min(1.0, total)


def _expected(table: Sequence[Sequence[int]]) -> List[List[float]]:
    n = float(sum(sum(row) for row in table))
    rows = [float(sum(row)) for row in table]
    cols = [float(sum(row[j] for row in table)) for j in range(len(table[0]))]
    return [[rows[i] * cols[j] / n for j in range(len(cols))]
            for i in range(len(rows))]


def chi_square_contingency(table: Sequence[Sequence[int]],
                           correction: bool = False
                           ) -> Tuple[float, float, float, float]:
    """Pearson chi-square test of independence.

    Returns ``(chi2, df, pvalue, min_expected)``.  ``correction`` applies Yates'
    continuity correction, which is only defined for a 2x2 table.
    """
    if not table or not table[0]:
        raise ValueError("contingency table is empty")
    ncol = len(table[0])
    if any(len(row) != ncol for row in table):
        raise ValueError("contingency table rows must all have the same length")
    n = sum(sum(row) for row in table)
    if n == 0:
        raise ValueError("contingency table is all zeros")
    exp = _expected(table)
    min_exp = min(min(r) for r in exp)
    if min_exp <= 0.0:
        # an empty row or column: that variable has only one observed level, so
        # the tables are trivially independent along it
        return (0.0, 0.0, 1.0, min_exp)
    yates = correction and len(table) == 2 and ncol == 2
    chi2 = 0.0
    for i, row in enumerate(table):
        for j, obs in enumerate(row):
            diff = abs(obs - exp[i][j])
            if yates:
                diff = max(0.0, diff - 0.5)
            chi2 += diff * diff / exp[i][j]
    df = float((len(table) - 1) * (ncol - 1))
    if df <= 0:
        return (chi2, 0.0, 1.0, min_exp)
    return (chi2, df, chi2_sf(chi2, df), min_exp)


# --------------------------------------------------------------------------
# two-group effect measures
# --------------------------------------------------------------------------

def risk_difference(a: BinaryGroup, b: BinaryGroup, conf: float = 0.95
                    ) -> Estimate:
    """Risk difference ``p(a) - p(b)`` with Newcombe's hybrid-score interval.

    Newcombe (1998) "method 10": combine each arm's Wilson interval rather than
    a pooled normal approximation.  It keeps sensible coverage at rates near
    0 or 1, where the Wald interval can run outside [-1, 1].
    """
    p1, p2 = a.proportion, b.proportion
    l1, u1 = wilson_interval(a.events, a.n, conf)
    l2, u2 = wilson_interval(b.events, b.n, conf)
    d = p1 - p2
    lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return Estimate("Risk difference (RD)", d, max(-1.0, lo), min(1.0, hi),
                    conf, "Newcombe hybrid score")


def _haldane(a: BinaryGroup, b: BinaryGroup) -> Tuple[float, float, float, float]:
    return (a.events + 0.5, a.non_events + 0.5,
            b.events + 0.5, b.non_events + 0.5)


def risk_ratio(a: BinaryGroup, b: BinaryGroup, conf: float = 0.95) -> Estimate:
    """Risk ratio ``p(a) / p(b)`` with the Katz log interval."""
    p1, p2 = a.proportion, b.proportion
    point = p1 / p2 if p2 > 0 else float("inf") if p1 > 0 else float("nan")
    if point != point:
        # no events in either arm: the ratio is undefined, and an interval
        # spanning three orders of magnitude around it would read as evidence
        return Estimate("Risk ratio (RR)", point, None, None, conf, "Katz log",
                        "두 군 모두 사건이 전혀(또는 전부) 발생하지 않아 위험비가 "
                        "정의되지 않습니다 — 신뢰구간도 보고하지 않습니다.")
    # Any empty cell breaks the log-scale variance -- including an empty
    # *non-event* cell (a 100%-response arm), which contributes exactly zero
    # variance and would otherwise produce an anticonservative interval with no
    # warning next to an odds ratio that does warn.
    zero_cell = min(a.events, a.non_events, b.events, b.non_events) == 0
    note = ""
    if zero_cell:
        e1, _, e2, _ = _haldane(a, b)
        n1, n2 = a.n + 1.0, b.n + 1.0
        note = ("0인 칸이 있어 신뢰구간은 Haldane-Anscombe 0.5 보정으로 "
                "계산했습니다(점추정값은 원자료 그대로).")
    else:
        e1, e2 = float(a.events), float(b.events)
        n1, n2 = float(a.n), float(b.n)
    if e1 <= 0 or e2 <= 0 or n1 <= 0 or n2 <= 0:
        return Estimate("Risk ratio (RR)", point, None, None, conf,
                        "Katz log", note or "계산 불가")
    log_rr = math.log(e1 / n1) - math.log(e2 / n2)
    var = 1.0 / e1 - 1.0 / n1 + 1.0 / e2 - 1.0 / n2
    if var <= 0.0:      # defensive: Haldane keeps e < n strictly, so var > 0
        return Estimate("Risk ratio (RR)", point, None, None, conf, "Katz log",
                        "로그 스케일 분산이 0이라 신뢰구간을 계산할 수 없습니다.")
    se = math.sqrt(var)
    z = _z(conf)
    lo, hi = math.exp(log_rr - z * se), math.exp(log_rr + z * se)
    if zero_cell and not (lo <= point <= hi):
        # The point comes from the raw table and the interval from the
        # Haldane-corrected one, so with a zero cell the estimate can sit
        # outside its own interval. Widening would throw away the informative
        # bound, so instead name the corrected estimate the interval is built
        # around -- a reader must never have to guess why the pair disagrees.
        note = (note + " 이 신뢰구간은 보정된 추정값 RR*={:.3f} 를 중심으로 "
                       "계산된 것이라 원자료 점추정값({:.3f})을 포함하지 "
                       "않습니다.".format(math.exp(log_rr), point))
    return Estimate("Risk ratio (RR)", point, lo, hi, conf, "Katz log", note)


def odds_ratio(a: BinaryGroup, b: BinaryGroup, conf: float = 0.95) -> Estimate:
    """Odds ratio with the Woolf logit interval."""
    ad = a.events * b.non_events
    bc = a.non_events * b.events
    point = ad / bc if bc > 0 else float("inf") if ad > 0 else float("nan")
    cells = (a.events, a.non_events, b.events, b.non_events)
    if point != point:
        # a whole column is empty: the odds ratio is undefined, so an interval
        # around it would be an interval around nothing
        return Estimate("Odds ratio (OR)", point, None, None, conf,
                        "Woolf logit",
                        "두 군 모두 사건이 전혀(또는 전부) 발생하지 않아 오즈비가 "
                        "정의되지 않습니다 — 신뢰구간도 보고하지 않습니다.")
    note = ""
    if min(cells) == 0:
        aa, bb, cc, dd = _haldane(a, b)
        note = ("0인 칸이 있어 신뢰구간은 Haldane-Anscombe 0.5 보정으로 "
                "계산했습니다(점추정값은 원자료 그대로).")
    else:
        aa, bb, cc, dd = (float(c) for c in cells)
    log_or = math.log(aa) + math.log(dd) - math.log(bb) - math.log(cc)
    se = math.sqrt(1.0 / aa + 1.0 / bb + 1.0 / cc + 1.0 / dd)
    z = _z(conf)
    return Estimate("Odds ratio (OR)", point, math.exp(log_or - z * se),
                    math.exp(log_or + z * se), conf, "Woolf logit", note)


def number_needed_to_treat(rd: Estimate, event_is: str = "unspecified"
                           ) -> Estimate:
    """NNT / NNH = 1 / |risk difference|, interval inverted from the RD CI.

    The *number* is symmetric in the sign of the risk difference; the *name* is
    not.  On an adverse-event endpoint a positive risk difference means one
    extra harm per 1/RD patients treated -- that is NNH, and printing it as
    "NNT = 2.5" states the opposite of the truth.  Because only the caller knows
    whether the counted event is good or bad, ``event_is`` ("benefit", "harm" or
    "unspecified") selects the label, and when it is unspecified the estimate is
    named neutrally and says which arm had the higher rate instead of guessing.

    When the RD interval contains zero the interval is *not* an interval at all
    -- it runs from a finite NNT out through infinity and back as a number
    needed to harm (Altman 1998).  Reporting a tidy pair of numbers there would
    be wrong, so the interval is withheld and the reason is stated.
    """
    if event_is not in ("benefit", "harm", "unspecified"):
        raise ValueError(
            "event_is must be 'benefit', 'harm' or 'unspecified'")
    d = rd.value
    # d = p(first arm) - p(reference). A *beneficial* event more often in the
    # first arm (d > 0) is a treatment benefit -> NNT; a *harmful* event more
    # often in the first arm is a harm -> NNH.
    if d != d or d == 0.0:
        # No direction: naming it NNT or NNH would assert one.
        return Estimate("NNT/NNH (1/|위험차|)", float("inf"), None, None,
                        rd.conf, "1/RD",
                        "관측된 위험차가 정확히 0이라 필요 환자 수는 무한대입니다 "
                        "— 효과가 없다는 뜻이 아니라 이 표본에서 차이가 "
                        "관측되지 않았다는 뜻입니다.")
    if event_is == "benefit":
        name = ("Number needed to treat (NNT)" if d > 0
                else "Number needed to harm (NNH)")
        extra = ""
    elif event_is == "harm":
        name = ("Number needed to harm (NNH)" if d > 0
                else "Number needed to treat (NNT)")
        extra = ""
    else:
        name = "NNT/NNH (1/|위험차|)"
        extra = ("사건이 이로운 것인지 해로운 것인지 알 수 없어 NNT/NNH를 "
                 "구분하지 않았습니다 — --event-is benefit|harm 을 지정하면 "
                 "올바른 이름으로 표시합니다.")
    nnt = abs(1.0 / d)
    lo, hi = rd.ci_low, rd.ci_high
    if lo is None or hi is None:
        return Estimate(name, nnt, None, None, rd.conf, "1/RD", extra)
    if lo <= 0.0 <= hi:
        note = ("위험차 신뢰구간이 0을 포함하므로 구간은 유한 구간이 아닙니다 "
                "(NNT_benefit ~ ∞ ~ NNT_harm). 구간을 보고하지 않습니다.")
        return Estimate(name, nnt, None, None, rd.conf, "1/RD",
                        (extra + " " + note).strip())
    # both bounds on the same side of 0 -> invert and order
    b1, b2 = abs(1.0 / hi), abs(1.0 / lo)
    return Estimate(name, nnt, min(b1, b2), max(b1, b2), rd.conf,
                    "1/RD (inverted from the RD CI)", extra)


def cramers_v(chi2: float, n: int, n_rows: int, n_cols: int) -> Estimate:
    """Cramér's V effect size for a contingency table."""
    k = min(n_rows, n_cols) - 1
    if n <= 0 or k <= 0 or chi2 != chi2:
        return Estimate("Cramér's V", float("nan"))
    v = math.sqrt(chi2 / (n * k))
    mag = "negligible" if v < 0.1 else "small" if v < 0.3 else \
        "medium" if v < 0.5 else "large"
    return Estimate("Cramér's V", min(1.0, v), magnitude=mag)


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

def _select_test(table: List[List[int]], force: str
                 ) -> Tuple[str, Optional[float], Optional[float], float, str,
                            float, Optional[float]]:
    """(name, statistic, df, pvalue, reason, min_expected, p_yates)."""
    two_by_two = len(table) == 2
    chi2, df, p_chi, min_exp = chi_square_contingency(table, correction=False)
    p_yates = None
    if two_by_two:
        _, _, p_yates, _ = chi_square_contingency(table, correction=True)

    if force == "fisher" or (force == "auto" and two_by_two and min_exp < 5.0):
        if not two_by_two:
            raise ValueError(
                "Fisher 정확검정은 2x2 표(그룹 2개)에서만 지원합니다. "
                "그룹이 3개 이상이면 --binary-test chisq 를 사용하세요.")
        (a, b), (c, d) = table
        p = fisher_exact_2x2(a, b, c, d)
        why = ("기대빈도 최솟값 {:.2f} < 5 → Fisher 정확검정".format(min_exp)
               if force == "auto" else "사용자가 Fisher 정확검정을 지정")
        return ("Fisher's exact test", None, None, p, why, min_exp, p_yates)

    if force == "chisq-yates":
        if not two_by_two:
            raise ValueError("Yates 연속성 보정은 2x2 표에서만 정의됩니다.")
        return ("Chi-square test (Yates-corrected)", chi2, df, p_yates,
                "사용자가 Yates 연속성 보정 카이제곱을 지정", min_exp, p_yates)

    if force == "auto" and not two_by_two and min_exp < 5.0:
        why = ("기대빈도 최솟값 {:.2f} < 5 → 카이제곱 근사가 부정확할 수 있음 "
               "(그룹 3개 이상이라 Fisher 정확검정 대신 카이제곱 사용)".format(min_exp))
    elif force == "auto":
        why = ("모든 기대빈도 ≥ 5 (최솟값 {:.2f}) → Pearson 카이제곱 "
               "독립성 검정".format(min_exp))
    else:
        why = "사용자가 Pearson 카이제곱 검정을 지정"
    return ("Chi-square test of independence", chi2, df, p_chi, why, min_exp,
            p_yates)


def _adjust(pvals: List[float], method: str) -> List[float]:
    from .analyze import _correct  # single source of truth for Holm / BH
    return _correct(pvals, method)


def compare_binary(named_groups: Sequence[Tuple[str, Tuple[int, int]]],
                   alpha: float = 0.05, correction: str = "holm",
                   posthoc: bool = True, test: str = "auto",
                   missing: Optional[Dict[str, int]] = None,
                   event_is: str = "unspecified") -> BinaryResult:
    """Compare event rates across arms.

    ``named_groups`` is ``[(label, (events, n)), ...]``.  ``test`` is one of
    ``auto`` (default), ``chisq``, ``chisq-yates`` or ``fisher``.
    """
    if test not in ("auto", "chisq", "chisq-yates", "fisher"):
        raise ValueError("test must be 'auto', 'chisq', 'chisq-yates' or "
                         "'fisher'")
    if correction not in ("holm", "bh"):
        raise ValueError("correction must be 'holm' or 'bh'")
    if not 0.0 < alpha < 0.5:
        raise ValueError("alpha must be in (0, 0.5)")
    miss = missing or {}
    groups = [BinaryGroup(str(label), int(ev), int(n),
                          int(miss.get(str(label), 0)))
              for label, (ev, n) in named_groups]
    if len(groups) < 2:
        raise ValueError("need at least 2 groups to compare")
    empty = [g.label for g in groups if g.n == 0]
    if empty:
        raise ValueError("관측치가 없는 그룹: " + ", ".join(empty))
    # Counts beyond 2^53 stop being exactly representable as floats, and the
    # chi-square then comes back NaN while every effect measure still prints.
    huge = [g.label for g in groups if g.n > 2 ** 53]
    if huge:
        raise ValueError(
            "표본 수가 비현실적으로 큽니다 (" + ", ".join(huge) +
            "). 사건 수/표본 수 열을 잘못 지정했는지 확인하세요.")

    warnings: List[str] = []
    table = [[g.events, g.non_events] for g in groups]
    name, stat, df, p, reason, min_exp, p_yates = _select_test(table, test)
    if p != p or (stat is not None and not math.isfinite(stat)):
        warnings.append(
            "검정통계량 또는 p값을 계산할 수 없습니다(NaN/무한대). 표본 수가 "
            "극단적이거나 표가 퇴화되어 이 결과는 해석할 수 없습니다 — "
            "'유의하지 않음'을 결론으로 쓰지 마세요.")

    if min_exp < 1.0:
        warnings.append(
            "기대빈도가 1 미만인 칸이 있습니다 (최솟값 {:.2f}) — 검정 결과를 "
            "매우 조심해서 해석하세요.".format(min_exp))
    if all(g.events == 0 for g in groups) or all(
            g.non_events == 0 for g in groups):
        warnings.append(
            "모든 그룹에서 사건이 전부 발생했거나 전혀 발생하지 않았습니다 — "
            "군간 비교가 사실상 정보를 주지 못합니다.")

    conf = 1.0 - alpha
    estimates: List[Estimate] = []
    pairwise: List[BinaryPairwise] = []
    if len(groups) == 2:
        a, b = groups
        rd = risk_difference(a, b, conf)
        estimates = [rd, risk_ratio(a, b, conf), odds_ratio(a, b, conf),
                     number_needed_to_treat(rd, event_is)]
        for est in estimates:
            if est.note and "Haldane" in est.note:
                warnings.append(f"{est.name}: {est.note}")
        # With a common outcome the odds ratio is much further from 1 than the
        # risk ratio, and readers routinely quote it as if it were one.
        if max(a.proportion, b.proportion) > 0.10:
            warnings.append(
                "결과가 흔한 사건(발생률 10% 초과)이라 오즈비(OR)는 위험비(RR)보다 "
                "1에서 훨씬 멀어집니다 — OR를 '몇 배 위험'으로 읽지 마세요. "
                "임상 보고에는 위험차(RD)와 위험비(RR)를 우선 쓰는 것이 안전합니다.")
    else:
        if stat is not None:
            estimates = [cramers_v(stat, sum(g.n for g in groups),
                                   len(groups), 2)]
        if posthoc and p == p and p < alpha:
            pairwise = _pairwise_binary(groups, alpha, correction, test)

    return BinaryResult(
        groups=groups, alpha=alpha, test_name=name, statistic=stat, df=df,
        pvalue=p, significant=(p == p and p < alpha), reason=reason,
        expected_min=min_exp, pvalue_yates=p_yates, estimates=estimates,
        pairwise=pairwise, correction=correction, warnings=warnings)


def _pairwise_binary(groups: List[BinaryGroup], alpha: float, correction: str,
                     test: str) -> List[BinaryPairwise]:
    pairs = [(i, j) for i in range(len(groups))
             for j in range(i + 1, len(groups))]
    raw: List[float] = []
    meta = []
    conf = 1.0 - alpha
    for i, j in pairs:
        a, b = groups[i], groups[j]
        sub = [[a.events, a.non_events], [b.events, b.non_events]]
        name, _, _, p, _, _, _ = _select_test(sub, test)
        rd = risk_difference(a, b, conf)
        raw.append(p)
        meta.append((a.label, b.label, name, rd.value, rd.ci_low, rd.ci_high,
                     a.n, b.n))
    adj = _adjust(raw, correction)
    return [BinaryPairwise(la, lb, nm, raw[k], adj[k], rd, adj[k] < alpha,
                           lo, hi, na, nb)
            for k, (la, lb, nm, rd, lo, hi, na, nb) in enumerate(meta)]
