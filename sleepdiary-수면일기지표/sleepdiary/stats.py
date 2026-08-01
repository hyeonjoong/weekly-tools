"""통계 유틸 — 표준 라이브러리만으로 구현.

수면일기 지표 보고에 필요한 최소한만 담았다:
기술통계, 대응표본 t검정(+95% CI, Cohen's dz), Wilcoxon 부호순위검정,
그리고 취침 중앙시각처럼 자정을 넘나드는 값에 필요한 원형(circular) 통계.

t분포/정규분포 꼬리확률은 정규화 불완전베타함수와 erf로 직접 계산하며,
`tests/test_stats.py`에서 scipy 참조값과 대조한다 (실행 시엔 scipy 불필요).
"""

from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence

__all__ = [
    "mean", "sd", "median", "quantile", "circular_mean", "circular_sd",
    "student_t_sf", "t_ppf", "normal_sf", "paired_ttest", "wilcoxon_signed_rank",
    "PairedResult", "WilcoxonResult",
]


# --------------------------------------------------------------------------
# 기술통계
# --------------------------------------------------------------------------

def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("빈 표본의 평균은 정의되지 않습니다")
    return math.fsum(values) / len(values)


def sd(values: Sequence[float]) -> Optional[float]:
    """표본표준편차 (n-1). n<2면 None."""
    n = len(values)
    if n < 2:
        return None
    mu = mean(values)
    var = math.fsum((v - mu) ** 2 for v in values) / (n - 1)
    return math.sqrt(max(var, 0.0))


def median(values: Sequence[float]) -> float:
    return quantile(values, 0.5)


def quantile(values: Sequence[float], q: float) -> float:
    """선형보간 분위수 (numpy 기본 'linear' 방식과 동일)."""
    if not values:
        raise ValueError("빈 표본의 분위수는 정의되지 않습니다")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q는 0..1 범위여야 합니다")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = q * (len(ordered) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(ordered[lo])
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo))


# --------------------------------------------------------------------------
# 원형(circular) 통계 — 하루 1440분을 원둘레로 본다
# --------------------------------------------------------------------------

_PERIOD = 1440.0


def _resultant(minutes: Sequence[float]) -> tuple[float, float]:
    """평균 방향(라디안)과 결과벡터 길이 R을 돌려준다."""
    if not minutes:
        raise ValueError("빈 표본")
    angles = [2 * math.pi * (m % _PERIOD) / _PERIOD for m in minutes]
    c = math.fsum(math.cos(a) for a in angles) / len(angles)
    s = math.fsum(math.sin(a) for a in angles) / len(angles)
    return math.atan2(s, c), math.hypot(c, s)


def circular_mean(minutes: Sequence[float]) -> float:
    """자정 기준 분들의 원형 평균 (0 ≤ x < 1440).

    23:50과 00:10의 평균은 12:00이 아니라 00:00이어야 하므로 필요하다.
    """
    ang, r = _resultant(minutes)
    if r < 1e-12:
        # 완전히 상쇄된 경우(예: 06:00과 18:00) 평균 방향이 정의되지 않는다.
        raise ValueError("원형 평균이 정의되지 않습니다 (값들이 정반대로 분산)")
    return (ang * _PERIOD / (2 * math.pi)) % _PERIOD


def circular_sd(minutes: Sequence[float]) -> Optional[float]:
    """원형 표준편차(분). sqrt(-2 ln R) 정의. n<2면 None.

    취침 중앙시각의 규칙성(불규칙할수록 큰 값) 지표로 쓴다.
    """
    if len(minutes) < 2:
        return None
    _, r = _resultant(minutes)
    r = min(max(r, 1e-12), 1.0)
    return math.sqrt(-2.0 * math.log(r)) * _PERIOD / (2 * math.pi)


def circular_diff(a: float, b: float) -> float:
    """a - b 를 [-720, 720) 범위의 최단 차이로."""
    return (a - b + _PERIOD / 2) % _PERIOD - _PERIOD / 2


# --------------------------------------------------------------------------
# 분포 함수
# --------------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    """연분수 전개 (Numerical Recipes 6.4의 Lentz 알고리즘)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h


def betainc_reg(a: float, b: float, x: float) -> float:
    """정규화 불완전베타함수 I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta + b * math.log1p(-x) + a * math.log(x)) * _betacf(b, a, 1.0 - x) / b


def student_t_sf(t: float, df: float) -> float:
    """P(T > t), 자유도 df의 Student t분포."""
    if df <= 0:
        raise ValueError("자유도는 양수여야 합니다")
    if math.isnan(t):
        return float("nan")
    x = df / (df + t * t)
    half = 0.5 * betainc_reg(0.5 * df, 0.5, x)
    return half if t > 0 else 1.0 - half


def t_ppf(p: float, df: float) -> float:
    """t분포의 분위수 (이분법). 0<p<1."""
    if not 0.0 < p < 1.0:
        raise ValueError("p는 0과 1 사이여야 합니다")
    lo, hi = -1e4, 1e4
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if 1.0 - student_t_sf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def normal_sf(z: float) -> float:
    """P(Z > z), 표준정규."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


# --------------------------------------------------------------------------
# 대응표본 검정
# --------------------------------------------------------------------------

class PairedResult:
    """대응표본 t검정 결과."""

    __slots__ = ("n", "mean_diff", "sd_diff", "se", "t", "df", "p", "ci_low", "ci_high", "dz")

    def __init__(self, n, mean_diff, sd_diff, se, t, df, p, ci_low, ci_high, dz):
        self.n = n
        self.mean_diff = mean_diff
        self.sd_diff = sd_diff
        self.se = se
        self.t = t
        self.df = df
        self.p = p
        self.ci_low = ci_low
        self.ci_high = ci_high
        self.dz = dz

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}


def paired_ttest(diffs: Sequence[float], conf: float = 0.95) -> PairedResult:
    """차이값 벡터에 대한 대응표본 t검정(양측) + 평균차 신뢰구간 + Cohen's dz.

    dz = 평균차 / 차이의 표준편차 (대응표본 효과크기).
    차이의 분산이 0이면 t/p/dz는 None으로 둔다 (0으로 나누지 않는다).
    """
    n = len(diffs)
    if n < 2:
        raise ValueError("대응표본 t검정에는 최소 2쌍이 필요합니다")
    md = mean(diffs)
    s = sd(diffs)
    df = n - 1
    if s is None or s == 0.0:
        return PairedResult(n, md, s, 0.0, None, df, None, md, md, None)
    se = s / math.sqrt(n)
    t = md / se
    p = 2.0 * student_t_sf(abs(t), df)
    crit = t_ppf(0.5 + conf / 2.0, df)
    return PairedResult(n, md, s, se, t, df, min(p, 1.0),
                        md - crit * se, md + crit * se, md / s)


class WilcoxonResult:
    """Wilcoxon 부호순위검정 결과."""

    __slots__ = ("n_used", "n_zero", "statistic", "p", "method", "z", "r")

    def __init__(self, n_used, n_zero, statistic, p, method, z, r):
        self.n_used = n_used
        self.n_zero = n_zero
        self.statistic = statistic
        self.p = p
        self.method = method
        self.z = z
        self.r = r

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}


def _rank_average(values: Sequence[float]) -> list[float]:
    """동점은 평균순위로 (1부터 시작)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _signed_rank_exact_p(w: float, n: int) -> float:
    """동점·0이 없는 경우의 정확 양측 p값. w는 정수 통계량."""
    total = n * (n + 1) // 2
    counts = [0] * (total + 1)
    counts[0] = 1
    for k in range(1, n + 1):
        for s in range(total, k - 1, -1):
            counts[s] += counts[s - k]
    space = float(2 ** n)
    w_int = int(round(w))
    left = sum(counts[: w_int + 1]) / space
    right = sum(counts[w_int:]) / space
    return min(1.0, 2.0 * min(left, right))


def wilcoxon_signed_rank(diffs: Sequence[float]) -> WilcoxonResult:
    """양측 Wilcoxon 부호순위검정.

    - 0인 차이는 제외한다 (scipy 기본 zero_method='wilcox'와 동일).
    - 0/동점이 없고 남은 n ≤ 25면 정확분포, 아니면 동점보정 정규근사.
    - 통계량은 min(R+, R-), 효과크기 r = |z| / sqrt(n_used) (정규근사일 때만).
    """
    nonzero = [d for d in diffs if d != 0.0]
    n_zero = len(diffs) - len(nonzero)
    n = len(nonzero)
    if n == 0:
        return WilcoxonResult(0, n_zero, None, None, "none", None, None)

    abs_vals = [abs(d) for d in nonzero]
    ranks = _rank_average(abs_vals)
    r_plus = math.fsum(r for r, d in zip(ranks, nonzero) if d > 0)
    r_minus = math.fsum(r for r, d in zip(ranks, nonzero) if d < 0)
    stat = min(r_plus, r_minus)

    has_ties = len(set(abs_vals)) != n
    if not has_ties and n <= 25:
        return WilcoxonResult(n, n_zero, stat, _signed_rank_exact_p(stat, n), "exact", None, None)

    mn = n * (n + 1) / 4.0
    se_sq = n * (n + 1) * (2 * n + 1) / 24.0
    # 동점 보정: 같은 |d| 묶음 크기 t마다 (t^3 - t)/48 을 뺀다.
    tie_groups: dict[float, int] = {}
    for v in abs_vals:
        tie_groups[v] = tie_groups.get(v, 0) + 1
    se_sq -= sum((c ** 3 - c) for c in tie_groups.values()) / 48.0
    if se_sq <= 0:
        return WilcoxonResult(n, n_zero, stat, 1.0, "normal", 0.0, 0.0)
    z = (stat - mn) / math.sqrt(se_sq)
    p = min(1.0, 2.0 * normal_sf(abs(z)))
    return WilcoxonResult(n, n_zero, stat, p, "normal", z, abs(z) / math.sqrt(n))


def summarize(values: Sequence[float]) -> dict:
    """평균/SD/중앙값/IQR/최소·최대/개수 묶음."""
    if not values:
        return {"n": 0, "mean": None, "sd": None, "median": None,
                "q1": None, "q3": None, "min": None, "max": None}
    return {
        "n": len(values),
        "mean": mean(values),
        "sd": sd(values),
        "median": median(values),
        "q1": quantile(values, 0.25),
        "q3": quantile(values, 0.75),
        "min": min(values),
        "max": max(values),
    }


def mean_ci(values: Sequence[float], conf: float = 0.95) -> tuple[Optional[float], Optional[float]]:
    """평균의 t 기반 신뢰구간. n<2면 (None, None)."""
    n = len(values)
    if n < 2:
        return (None, None)
    s = sd(values)
    if s is None or s == 0.0:
        m = mean(values)
        return (m, m)
    se = s / math.sqrt(n)
    crit = t_ppf(0.5 + conf / 2.0, n - 1)
    m = mean(values)
    return (m - crit * se, m + crit * se)
