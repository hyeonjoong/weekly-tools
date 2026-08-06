"""Paired binary endpoints — McNemar's test, paired RD, conditional OR, kappa.

The binary side of statwise assumed two *independent* arms, so a design that is
extremely common in practice had no home at all:

    - responder / non-responder in the **same** patients before and after,
    - two diagnostic tests (or two readers) applied to the **same** samples,
    - a crossover trial where each subject receives both treatments.

For those the 2x2 table is not "arm x outcome" but "condition A x condition B"
over matched pairs, and the concordant cells carry no information about the
change.  Running a chi-square or Fisher test on such data treats the same
patient as two independent patients: the sample size is doubled, the variance
is wrong, and the p-value is anti-conservative.  This module implements what
the design actually calls for.

Layout of the 2x2 table (rows = condition A, columns = condition B)::

                       B: event    B: non-event
    A: event             n11 (both)     n12 (a_only)
    A: non-event         n21 (b_only)   n22 (neither)

Only the *discordant* pairs n12 and n21 carry information about a difference in
the marginal rates, which is why every method here is a function of them.

Tests (stated in the output, never guessed silently):

    discordant pairs < 25  -> exact binomial (sign) test on the discordants
    otherwise              -> McNemar chi-square (b-c)^2 / (b+c), df = 1
    forced                 -> 'exact', 'mcnemar', 'mcnemar-cc' (Edwards'
                              continuity-corrected chi-square)

Estimates:

    paired risk difference   p(A) - p(B) = (n12 - n21) / n, with **Tango's
                             (1998) score interval** -- the method that keeps
                             its coverage at rates near 0 or 1 and never leaves
                             [-1, 1], where the naive Wald interval on paired
                             proportions fails outright.
    conditional odds ratio   n12 / n21, with an exact (Clopper-Pearson) interval
                             obtained from the discordant proportion.  This is
                             the odds ratio McNemar's test is actually about;
                             it is *not* the marginal OR of the two rates.
    Cohen's kappa            chance-corrected agreement with the Fleiss (1969)
                             large-sample interval -- the quantity a reader
                             wants when the two "conditions" are two tests or
                             two raters rather than two time points.

NNT/NNH is reported the same way the independent-groups path reports it (and
withheld when the RD interval spans zero, where it is not an interval at all).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .binary import Estimate, number_needed_to_treat
from .dataio import sanitize_label as _safe_label
from .special import betainc, chi2_sf, norm_ppf

__all__ = [
    "PairedBinaryResult",
    "PairedTable",
    "mcnemar_exact_p",
    "mcnemar_chi_square",
    "paired_risk_difference",
    "conditional_odds_ratio",
    "cohens_kappa",
    "clopper_pearson",
    "compare_paired_binary",
]

#: Below this many discordant pairs the chi-square approximation to the
#: binomial is not trustworthy and the exact test is used instead. 25 is the
#: usual textbook cut-off (Agresti); it is stated in the report either way.
EXACT_MAX_DISCORDANT = 25


# --------------------------------------------------------------------------
# containers
# --------------------------------------------------------------------------

@dataclass
class PairedTable:
    """The 2x2 table of matched pairs for conditions ``label_a`` / ``label_b``."""

    label_a: str
    label_b: str
    both: int          # n11: event under both
    a_only: int        # n12: event under A only
    b_only: int        # n21: event under B only
    neither: int       # n22: event under neither

    def __post_init__(self) -> None:
        for name, v in (("both", self.both), ("a_only", self.a_only),
                        ("b_only", self.b_only), ("neither", self.neither)):
            if v < 0:
                raise ValueError(f"cell '{name}' cannot be negative (got {v})")

    @property
    def n(self) -> int:
        return self.both + self.a_only + self.b_only + self.neither

    @property
    def n_discordant(self) -> int:
        return self.a_only + self.b_only

    @property
    def events_a(self) -> int:
        return self.both + self.a_only

    @property
    def events_b(self) -> int:
        return self.both + self.b_only

    @property
    def prop_a(self) -> float:
        return self.events_a / self.n if self.n else float("nan")

    @property
    def prop_b(self) -> float:
        return self.events_b / self.n if self.n else float("nan")


@dataclass
class PairedBinaryResult:
    table: PairedTable
    alpha: float
    test_name: str
    statistic: Optional[float]
    df: Optional[float]
    pvalue: float
    significant: bool
    reason: str
    method: str = "exact"          # 'exact' | 'asymptotic'
    estimates: List[Estimate] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    #: unusable / unpaired source cells per condition, for CONSORT accounting
    missing: Dict[str, int] = field(default_factory=dict)
    endpoint: Optional[str] = None
    pvalue_adj: Optional[float] = None

    @property
    def n_pairs(self) -> int:
        return self.table.n


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------

def _binom_cdf_half(k: int, m: int) -> float:
    """P(X <= k) for X ~ Binomial(m, 0.5), exact for small m."""
    if m <= 0:
        return 1.0
    if k < 0:
        return 0.0
    if k >= m:
        return 1.0
    if m <= 1000:
        total = sum(math.comb(m, i) for i in range(k + 1))
        return total / (2.0 ** m)
    # Beyond a thousand discordant pairs the exact sum overflows the patience
    # of anyone waiting for it; the regularized incomplete beta gives the same
    # number (P(X<=k) = I_{0.5}(m-k, k+1)) in constant time.
    return betainc(m - k, k + 1, 0.5)


def mcnemar_exact_p(b: int, c: int) -> float:
    """Two-sided exact (binomial sign) p-value for the discordant pairs.

    Under the null the ``b + c`` discordant pairs split 50/50, so the p-value is
    twice the lower binomial tail (the distribution is symmetric at p = 0.5).
    With no discordant pairs at all there is no evidence of a change and the
    p-value is 1.
    """
    if b < 0 or c < 0:
        raise ValueError("discordant counts cannot be negative")
    m = b + c
    if m == 0:
        return 1.0
    return min(1.0, 2.0 * _binom_cdf_half(min(b, c), m))


def mcnemar_chi_square(b: int, c: int, continuity: bool = False
                       ) -> Tuple[float, float, float]:
    """McNemar chi-square ``(statistic, df, pvalue)`` on the discordant pairs.

    ``continuity=True`` applies Edwards' correction ``(|b-c| - 1)^2 / (b+c)``,
    clamped at zero.  R's ``mcnemar.test(correct=TRUE)`` and statsmodels do not
    clamp and report ``1/(b+c)`` when ``b == c``; the clamp is the more
    conservative reading (no evidence at all -> p = 1) and cannot change a
    verdict, since the unclamped statistic is < 1 there.
    """
    if b < 0 or c < 0:
        raise ValueError("discordant counts cannot be negative")
    m = b + c
    if m == 0:
        return (float("nan"), 1.0, 1.0)
    diff = abs(b - c)
    if continuity:
        diff = max(0.0, diff - 1.0)
    chi2 = diff * diff / m
    return (chi2, 1.0, chi2_sf(chi2, 1.0))


# --------------------------------------------------------------------------
# interval methods
# --------------------------------------------------------------------------

def _z(conf: float) -> float:
    if not 0.0 < conf < 1.0:
        raise ValueError("conf must be in (0, 1)")
    return norm_ppf(1.0 - (1.0 - conf) / 2.0)


def _tango_score(b: int, c: int, n: int, delta: float) -> float:
    """Tango's score statistic for H0: p(A) - p(B) = ``delta``.

    The constrained ML estimate of p21 solves ``A q^2 + B q + C = 0`` with
    ``A = 2n``, ``B = -b - c + (2n - b + c) delta``, ``C = -c delta (1 - delta)``.
    At ``delta = 0`` this reduces to ``q = (b + c) / 2n`` and the statistic to
    the ordinary McNemar z, which is the check that keeps the two consistent.
    """
    aa = 2.0 * n
    bb = -float(b) - float(c) + (2.0 * n - b + c) * delta
    cc = -float(c) * delta * (1.0 - delta)
    disc = bb * bb - 4.0 * aa * cc
    q = (math.sqrt(disc) - bb) / (2.0 * aa) if disc > 0.0 else -bb / (2.0 * aa)
    var = n * (2.0 * q + delta * (1.0 - delta))
    num = float(b) - float(c) - n * delta
    if var <= 0.0:
        if num == 0.0:
            return 0.0
        return math.inf if num > 0.0 else -math.inf
    return num / math.sqrt(var)


def _tango_root(b: int, c: int, n: int, lo: float, hi: float,
                target: float) -> float:
    """Bisect for the delta where the score statistic equals ``target``.

    The statistic decreases in delta, so ``lo`` is the end where it is above
    the target.  Returns the bracket end when the sign never changes (which
    only happens at the boundary of [-1, 1]).
    """
    f_lo = _tango_score(b, c, n, lo) - target
    f_hi = _tango_score(b, c, n, hi) - target
    if f_lo != f_lo or f_hi != f_hi:
        return float("nan")
    if (f_lo > 0.0) == (f_hi > 0.0):
        return lo if abs(f_lo) < abs(f_hi) else hi
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if mid == lo or mid == hi:
            break
        f_mid = _tango_score(b, c, n, mid) - target
        if f_mid == 0.0:
            return mid
        if (f_mid > 0.0) == (f_lo > 0.0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def paired_risk_difference(table: PairedTable, conf: float = 0.95) -> Estimate:
    """``p(A) - p(B)`` over matched pairs, with Tango's score interval.

    The estimate is ``(n12 - n21) / n``: the concordant pairs cancel, which is
    exactly why a paired design is more efficient than two independent arms.
    """
    n = table.n
    if n == 0:
        return Estimate("Risk difference (paired)", float("nan"), None, None,
                        conf, "Tango score", "짝지어진 관측치가 없습니다.")
    b, c = table.a_only, table.b_only
    point = (b - c) / n
    z = _z(conf)
    lo = _tango_root(b, c, n, -1.0, point, z)
    hi = _tango_root(b, c, n, point, 1.0, -z)
    if lo != lo or hi != hi:
        return Estimate("Risk difference (paired)", point, None, None, conf,
                        "Tango score", "신뢰구간을 계산할 수 없습니다.")
    return Estimate("Risk difference (paired)", point,
                    max(-1.0, min(lo, point)), min(1.0, max(hi, point)),
                    conf, "Tango 점수(score) 구간")


def clopper_pearson(k: int, m: int, conf: float = 0.95) -> Tuple[float, float]:
    """Exact (Clopper-Pearson) interval for a binomial proportion ``k`` of ``m``.

    Obtained by inverting the binomial tails through the regularized incomplete
    beta, which is monotone in p, so a bisection is both safe and exact enough
    for reporting.
    """
    if m <= 0:
        return (float("nan"), float("nan"))
    if k < 0 or k > m:
        raise ValueError("k must be within 0..m")
    tail = (1.0 - conf) / 2.0
    if not 0.0 < tail < 0.5:
        raise ValueError("conf must be in (0, 1)")

    def _solve(a: float, bb: float, target: float) -> float:
        lo, hi = 0.0, 1.0
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            if mid == lo or mid == hi:
                break
            if betainc(a, bb, mid) < target:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    low = 0.0 if k == 0 else _solve(k, m - k + 1, tail)
    high = 1.0 if k == m else _solve(k + 1, m - k, 1.0 - tail)
    return (low, high)


def conditional_odds_ratio(table: PairedTable, conf: float = 0.95) -> Estimate:
    """Conditional odds ratio ``n12 / n21`` with an exact interval.

    This is the parameter McNemar's test is a test of.  It is a ratio of the
    *discordant* counts, so it answers "among the pairs that changed, how many
    more went one way than the other" — a different question from the marginal
    odds ratio of the two rates, and it must not be quoted as one.
    """
    b, c = table.a_only, table.b_only
    m = b + c
    name = "Conditional odds ratio (paired)"
    if m == 0:
        return Estimate(name, float("nan"), None, None, conf,
                        "exact (Clopper-Pearson)",
                        "불일치(discordant) 쌍이 없어 조건부 오즈비가 정의되지 "
                        "않습니다 — 신뢰구간도 보고하지 않습니다.")
    point = (b / c) if c > 0 else (float("inf") if b > 0 else float("nan"))
    plo, phi = clopper_pearson(b, m, conf)
    lo = plo / (1.0 - plo) if plo < 1.0 else float("inf")
    hi = phi / (1.0 - phi) if phi < 1.0 else float("inf")
    note = ""
    if b == 0 or c == 0:
        note = ("불일치 칸 하나가 0이라 점추정값이 0 또는 ∞ 입니다 — 신뢰구간의 "
                "유한한 쪽 경계만 정보를 줍니다.")
    return Estimate(name, point, lo, hi, conf,
                    "정확(Clopper-Pearson) 구간 변환", note)


def cohens_kappa(table: PairedTable, conf: float = 0.95) -> Estimate:
    """Chance-corrected agreement between the two conditions, Fleiss interval.

    Reported because the paired 2x2 has a second, equally common reading: two
    diagnostic tests or two readers scoring the same samples.  There McNemar
    answers "is one test positive more often" while kappa answers "do they
    agree on the same samples" — two arms of the same table can differ wildly
    on one and not the other, so reporting only one of them hides half the
    result.
    """
    n = table.n
    name = "Cohen's kappa (일치도)"
    if n == 0:
        return Estimate(name, float("nan"), None, None, conf, "Fleiss")
    p = [[table.both / n, table.a_only / n],
         [table.b_only / n, table.neither / n]]
    row = [p[0][0] + p[0][1], p[1][0] + p[1][1]]
    col = [p[0][0] + p[1][0], p[0][1] + p[1][1]]
    po = p[0][0] + p[1][1]
    pe = row[0] * col[0] + row[1] * col[1]
    if abs(1.0 - pe) < 1e-12:
        return Estimate(name, float("nan"), None, None, conf, "Fleiss",
                        "두 조건 모두 한쪽 범주만 관측되어 우연 일치 확률이 1입니다 "
                        "— kappa가 정의되지 않습니다.")
    kappa = (po - pe) / (1.0 - pe)
    # A constant condition (a whole row or column empty) pins kappa to a
    # structural value with zero sampling variance. The variance expression
    # below then evaluates to ~1e-18 instead of exactly 0, so the guard on
    # `var <= 0` misses it and a zero-width interval prints as "[-0.000,
    # 0.000]" -- certainty manufactured out of rounding dust. Detect the
    # degenerate margin directly instead of hoping the arithmetic cancels.
    degenerate = any(r == 0.0 for r in row) or any(c == 0.0 for c in col)
    if degenerate:
        return Estimate(
            name, kappa, None, None, conf, "Fleiss",
            "두 조건 중 하나에서 한 범주만 관측되어 kappa가 자료와 무관하게 "
            "고정된 값입니다(일치도 정보 없음) — 신뢰구간을 보고하지 않습니다.")
    # Fleiss (1969) large-sample variance of the estimated kappa.
    term_a = sum(p[i][i] * (1.0 - (row[i] + col[i]) * (1.0 - kappa)) ** 2
                 for i in range(2))
    term_b = (1.0 - kappa) ** 2 * sum(
        p[i][j] * (col[i] + row[j]) ** 2
        for i in range(2) for j in range(2) if i != j)
    term_c = (kappa - pe * (1.0 - kappa)) ** 2
    var = (term_a + term_b - term_c) / (n * (1.0 - pe) ** 2)
    # Landis-Koch reads a *positive* kappa; below zero the agreement is worse
    # than chance and "poor" understates it, so it gets its own label.
    mag = ("below chance" if kappa < 0.0 else
           "poor" if kappa < 0.20 else "fair" if kappa < 0.40 else
           "moderate" if kappa < 0.60 else "substantial" if kappa < 0.80
           else "almost perfect")
    if var <= 0.0:
        return Estimate(name, kappa, None, None, conf, "Fleiss",
                        "표본 분산이 0이라 신뢰구간을 계산할 수 없습니다.", mag)
    z = _z(conf)
    half = z * math.sqrt(var)
    return Estimate(name, kappa, max(-1.0, kappa - half),
                    min(1.0, kappa + half), conf,
                    "Fleiss 대표본 근사", "", mag)


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

#: Attached to every kappa estimate. Two conditions are two *time points* as
#: often as they are two raters, and in a pre/post design a low kappa is the
#: treatment working -- reading it as "my outcome measure is unreliable" is a
#: mistake a clinician makes on their own successful trial.
_KAPPA_READING = (
    "kappa는 '두 조건이 같은 대상에서 일치하는가'를 재는 값입니다 — 두 검사/"
    "판독자 비교에서만 신뢰도로 읽으세요. 치료 전/후 비교라면 일치도가 낮은 "
    "것이 곧 치료 효과이므로 이 값을 신뢰도로 해석하면 안 됩니다.")


def _warn_ci_p_disagreement(rd, pvalue: float, alpha: float, method: str,
                            warnings: List[str]) -> None:
    """Flag the case where the McNemar test and the paired RD interval disagree.

    The Tango interval inverts the *score* (asymptotic) statistic, while the
    p-value falls back to the exact binomial test on small discordant counts.
    The two are not the same inference, so on ~3% of small tables the report
    would say "not significant" beside an interval that excludes zero -- and
    the paste-ready sentence would state both in one breath. Neither number is
    wrong; the disagreement itself is what the reader has to know about.
    """
    if rd.ci_low is None or rd.ci_high is None or pvalue != pvalue:
        return
    excludes_zero = not (rd.ci_low <= 0.0 <= rd.ci_high)
    if (pvalue < alpha) == excludes_zero:
        return
    why = ("p값은 정확 이항검정, 신뢰구간은 Tango 점수(근사) 구간으로 서로 다른 "
           "방식이라" if method == "exact"
           else "p값과 구간이 서로 다른 근사를 쓰기 때문에")
    warnings.append(
        "McNemar p값과 대응 위험차 신뢰구간의 판정이 서로 다릅니다 "
        "(p={:.4f}, {:.0f}% CI [{:.1f}%, {:.1f}%]). {} 생기는 경계선 결과이므로 "
        "어느 한쪽만 골라 단정적으로 해석하지 마세요.".format(
            pvalue, rd.conf * 100, rd.ci_low * 100, rd.ci_high * 100, why))


def _select(table: PairedTable, force: str
            ) -> Tuple[str, Optional[float], Optional[float], float, str, str]:
    """(name, statistic, df, pvalue, reason, method)."""
    b, c = table.a_only, table.b_only
    m = table.n_discordant
    if force == "auto":
        force = "exact" if m < EXACT_MAX_DISCORDANT else "mcnemar"
        auto = True
    else:
        auto = False
    if force == "exact":
        p = mcnemar_exact_p(b, c)
        why = ("불일치 쌍 {}개 < {} → 정확 이항검정(exact)".format(
            m, EXACT_MAX_DISCORDANT) if auto
            else "사용자가 정확 이항검정을 지정")
        return ("McNemar exact test (binomial)", None, None, p, why, "exact")
    cc = force == "mcnemar-cc"
    chi2, df, p = mcnemar_chi_square(b, c, continuity=cc)
    if auto:
        why = ("불일치 쌍 {}개 ≥ {} → McNemar 카이제곱 검정".format(
            m, EXACT_MAX_DISCORDANT))
    elif cc:
        why = "사용자가 연속성 보정(Edwards) McNemar 검정을 지정"
    else:
        why = "사용자가 McNemar 카이제곱 검정을 지정"
    label = ("McNemar's test (continuity-corrected)" if cc
             else "McNemar's chi-square test")
    return (label, chi2, df, p, why, "asymptotic")


def compare_paired_binary(cond_a: Tuple[str, Sequence[int]],
                          cond_b: Tuple[str, Sequence[int]],
                          alpha: float = 0.05, test: str = "auto",
                          event_is: str = "unspecified",
                          missing: Optional[Dict[str, int]] = None
                          ) -> PairedBinaryResult:
    """Compare a binary endpoint measured twice on the same subjects.

    ``cond_a`` / ``cond_b`` are ``(label, indicators)`` with the indicators
    **row-matched**: element *i* of each is the same subject.  Each indicator is
    1 (event) or 0 (no event).  The reported difference is
    ``p(A) - p(B)``, so pass the baseline / reference condition as ``cond_b``.
    """
    if test not in ("auto", "exact", "mcnemar", "mcnemar-cc"):
        raise ValueError(
            "test must be 'auto', 'exact', 'mcnemar' or 'mcnemar-cc'")
    if not 0.0 < alpha < 0.5:
        raise ValueError("alpha must be in (0, 0.5)")
    # Labels come straight out of a CSV cell and land in a fixed-width table, a
    # paste-ready sentence and a terminal. Without this a control character
    # shears the 2x2 grid apart and an ANSI escape reaches the terminal raw --
    # the continuous paired path has sanitized for exactly this reason.
    la, va = _safe_label(cond_a[0]), list(cond_a[1])
    lb, vb = _safe_label(cond_b[0]), list(cond_b[1])
    if not la or not lb:
        raise ValueError(
            "조건 이름이 비어 있습니다 — 열 이름이나 그룹 값이 빈 칸인지 "
            "확인하세요 (이름 없는 조건은 리포트에서 구분할 수 없습니다).")
    if len(va) != len(vb):
        raise ValueError(
            f"대응 이진 분석에는 길이가 같은 두 조건이 필요합니다 "
            f"(받은 값: {len(va)}개 / {len(vb)}개).")
    if not va:
        raise ValueError("짝을 이루는 관측치가 없습니다.")
    if la == lb:
        raise ValueError(
            f"두 조건의 이름이 같습니다 ('{la}') — 서로 다른 두 조건을 지정하세요.")
    cells = [0, 0, 0, 0]
    for x, y in zip(va, vb):
        if x not in (0, 1) or y not in (0, 1):
            raise ValueError(
                "이진 지표는 0 또는 1이어야 합니다 "
                f"(받은 값: {x!r}, {y!r}).")
        cells[(0 if x else 2) + (0 if y else 1)] += 1
    table = PairedTable(la, lb, cells[0], cells[1], cells[2], cells[3])

    name, stat, df, p, reason, method = _select(table, test)
    warnings: List[str] = []
    if table.n_discordant == 0:
        warnings.append(
            "불일치(discordant) 쌍이 하나도 없습니다 — 두 조건의 결과가 모든 "
            "대상에서 동일하므로 변화에 대한 정보가 전혀 없고, p=1.000은 "
            "'차이 없음의 증거'가 아니라 '판단 불가'로 읽어야 합니다.")
    elif table.n_discordant < 10:
        warnings.append(
            f"불일치 쌍이 {table.n_discordant}개뿐입니다 — 검정력이 사실상 "
            f"이 개수만으로 정해지므로(일치 쌍은 정보를 주지 않습니다) "
            f"결과를 단정적으로 해석하지 마세요.")
    if method == "asymptotic" and table.n_discordant < EXACT_MAX_DISCORDANT:
        warnings.append(
            f"불일치 쌍이 {table.n_discordant}개인데 카이제곱 근사를 "
            f"지정했습니다 — 이 표본에서는 정확검정(--binary-test exact)이 "
            f"권장됩니다.")

    conf = 1.0 - alpha
    rd = paired_risk_difference(table, conf)
    kappa = cohens_kappa(table, conf)
    kappa.note = (kappa.note + " " + _KAPPA_READING).strip()
    estimates = [rd, conditional_odds_ratio(table, conf),
                 number_needed_to_treat(rd, event_is), kappa]
    for est in estimates:
        if est.note and "정의되지 않습니다" in est.note:
            warnings.append(f"{est.name}: {est.note}")
    if table.n < 30 and kappa.ci_low is not None:
        warnings.append(
            f"Cohen's kappa 신뢰구간은 대표본 근사(Fleiss)라 짝 {table.n}개에서는 "
            f"실제보다 좁을 수 있습니다 — 폭을 정확한 값으로 인용하지 마세요.")
    _warn_ci_p_disagreement(rd, p, alpha, method, warnings)

    return PairedBinaryResult(
        table=table, alpha=alpha, test_name=name, statistic=stat, df=df,
        pvalue=p, significant=(p == p and p < alpha), reason=reason,
        method=method, estimates=estimates, warnings=warnings,
        missing=dict(missing or {}))
