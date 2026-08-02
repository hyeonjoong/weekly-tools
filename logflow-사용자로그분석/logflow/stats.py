"""작은 통계 유틸 (표준 라이브러리만).

- 비율(proportion)의 신뢰구간: Wilson score interval — 리텐션/퍼널 전환율처럼
  0/1 비율을 논문·리포트에 실을 때 표본이 작아도 안정적인 95% 구간을 준다.
- 두 비율의 차(risk difference)와 그 신뢰구간: Newcombe hybrid-score 구간 —
  군(arm) 간 리텐션/완주율 차이를 보고할 때 Wald 보다 작은 표본에서 정확하다.
- 정확검정: Fisher exact (양측, 2×2) — 임상 유저테스트처럼 셀 빈도가 작을 때
  카이제곱 근사 대신 써야 하는 검정.
- 순위검정: Mann-Whitney U (동점 보정 + 연속성 보정 정규근사) — 이벤트 수처럼
  치우친 분포의 군 간 비교에 t-검정보다 적절.
- 생존분석: Kaplan-Meier 추정(Greenwood 분산)과 log-rank 검정 — 사용자 이탈까지의
  시간을 우측 절단(censoring)을 지키며 다루기 위함.
- 대응표본(paired) 이분형 검정: Cochran's Q(k개 시점)와 McNemar 정확검정(2개 시점) —
  같은 참여자를 주차마다 반복 관찰한 준수 여부처럼 **관측이 독립이 아닌** 자료에
  카이제곱·Fisher 를 쓰면 안 되기 때문에 필요하다.
- 발생률(rate): Poisson 정확(Garwood) 신뢰구간과 두 발생률 비(rate ratio)의 조건부
  이항 정확검정 — 반복 이벤트를 "1인-주당 몇 건" 으로 볼 때 필요한 도구.
- 분위수(quantile): 세션 길이처럼 치우친(skewed) 분포를 평균 하나로 요약하면
  오해를 부르므로 중앙값·사분위수를 함께 보고하기 위한 헬퍼.

외부 의존성 없음. 모두 순수 함수.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

# Fisher exact 에서 "관측 확률 이하" 를 판정할 때의 상대 허용오차.
# 부동소수 반올림 때문에 이론상 같은 확률의 표가 탈락하는 것을 막는다 (scipy 와 동일한 관례).
_FISHER_EPS = 1.0 + 1e-7

# 자주 쓰는 신뢰수준의 z 값 (양측). 95% 기본.
Z_BY_CONFIDENCE = {
    0.80: 1.2815515594600549,
    0.90: 1.6448536269514722,
    0.95: 1.959963984540054,
    0.99: 2.5758293035489004,
}


def z_for_confidence(confidence: float = 0.95) -> float:
    """신뢰수준(예: 0.95)에 대응하는 양측 z 값.

    표에 없는 값은 정규분포 역함수(Acklam 근사)로 계산한다.
    """
    if not (0.0 < confidence < 1.0):
        raise ValueError(f"confidence 는 0과 1 사이여야 합니다 (받은 값: {confidence})")
    if confidence in Z_BY_CONFIDENCE:
        return Z_BY_CONFIDENCE[confidence]
    # 양측: 상위 (1-conf)/2 분위의 z
    return _inv_norm_cdf(1.0 - (1.0 - confidence) / 2.0)


def wilson_interval(
    successes: int, total: int, confidence: float = 0.95
) -> Optional[Tuple[float, float]]:
    """이항 비율의 Wilson score 신뢰구간 (lo, hi), [0,1] 로 클램프.

    total 이 0 이면 None. successes 는 0..total 범위여야 한다.
    정규근사(Wald)와 달리 표본이 작거나 비율이 0/1 근처여도 구간이 붕괴하지 않는다.
    """
    if total < 0:
        raise ValueError("total 은 음수일 수 없습니다")
    if not (0 <= successes <= total):
        raise ValueError(f"successes({successes}) 는 0..total({total}) 범위여야 합니다")
    if total == 0:
        return None
    z = z_for_confidence(confidence)
    n = float(total)
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    lo = max(0.0, center - half)
    hi = min(1.0, center + half)
    return (lo, hi)


def quantile(values: Sequence[float], q: float) -> Optional[float]:
    """선형보간 분위수 (numpy 기본 'linear'/type-7 과 동일).

    values 는 비어있지 않아야 하며, 내부에서 정렬한다. q 는 [0,1].
    비어 있으면 None.
    """
    if not (0.0 <= q <= 1.0):
        raise ValueError(f"q 는 0..1 범위여야 합니다 (받은 값: {q})")
    xs = sorted(values)
    n = len(xs)
    if n == 0:
        return None
    if n == 1:
        return float(xs[0])
    pos = q * (n - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(xs[lo])
    frac = pos - lo
    return float(xs[lo]) * (1.0 - frac) + float(xs[hi]) * frac


def median(values: Sequence[float]) -> Optional[float]:
    return quantile(values, 0.5)


def describe(values: Sequence[float]) -> Optional[dict]:
    """치우친 분포를 정직하게 요약: n·평균·중앙값·사분위·p90·최소·최대."""
    xs = sorted(values)
    if not xs:
        return None
    return {
        "n": len(xs),
        "mean": sum(xs) / len(xs),
        "min": float(xs[0]),
        "p25": quantile(xs, 0.25),
        "median": quantile(xs, 0.50),
        "p75": quantile(xs, 0.75),
        "p90": quantile(xs, 0.90),
        "max": float(xs[-1]),
    }


# ---------------------------------------------------------------- 분포 함수

def norm_sf(z: float) -> float:
    """표준정규 상측꼬리 P(Z > z). erfc 기반이라 꼬리에서도 상대오차가 작다."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def chi2_sf_1df(x: float) -> float:
    """자유도 1 카이제곱의 상측꼬리 P(X > x).

    자유도 1 에서 X = Z^2 이므로 P(X > x) = 2·P(Z > sqrt(x)) = erfc(sqrt(x/2)).
    """
    if x <= 0.0:
        return 1.0
    if math.isinf(x):
        return 0.0
    return math.erfc(math.sqrt(x / 2.0))


def chi2_sf(x: float, df: int) -> float:
    """자유도 df(≥1) 카이제곱의 상측꼬리 P(X > x).

    df=1 은 erfc 로 정확히, df=2 는 닫힌형 exp(-x/2) 로, 그 외는 정규화 상측
    불완전감마 Q(df/2, x/2) 로 계산한다 (급수 ↔ 연분수 전환).
    """
    if df < 1:
        raise ValueError(f"자유도는 1 이상이어야 합니다 (받은 값: {df})")
    if x <= 0.0:
        return 1.0
    if math.isnan(x):
        raise ValueError("검정통계량이 NaN 입니다")
    if math.isinf(x):
        return 0.0
    if df == 1:
        return chi2_sf_1df(x)
    if df == 2:
        return math.exp(-x / 2.0)
    return _gamma_q(df / 2.0, x / 2.0)


def _gamma_q(a: float, x: float) -> float:
    """정규화 상측 불완전감마 Q(a, x) = 1 - P(a, x). a > 0, x >= 0.

    x < a+1 이면 하측 급수가, 그 이상이면 연분수(Lentz)가 빠르게 수렴한다 —
    각자 수렴이 나쁜 영역을 서로 피한다.
    """
    if x < a + 1.0:
        return 1.0 - _gamma_p_series(a, x)
    return _gamma_q_cf(a, x)


def _gamma_p_series(a: float, x: float) -> float:
    """하측 P(a, x) 의 급수전개 Σ x^n / (a(a+1)···(a+n)) · x^a e^-x / Γ(a)."""
    if x <= 0.0:
        return 0.0
    term = 1.0 / a
    total = term
    n = a
    for _ in range(1000):
        n += 1.0
        term *= x / n
        total += term
        if abs(term) < abs(total) * 1e-16:
            break
    return min(1.0, total * math.exp(-x + a * math.log(x) - math.lgamma(a)))


def _gamma_q_cf(a: float, x: float) -> float:
    """상측 Q(a, x) 의 연분수 전개 (수정 Lentz 알고리즘)."""
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b if b != 0.0 else 1.0 / tiny
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-16:
            break
    return max(0.0, min(1.0, math.exp(-x + a * math.log(x) - math.lgamma(a)) * h))


# ---------------------------------------------------------------- 두 비율의 차

@dataclass
class DiffResult:
    """두 비율의 차(risk difference) p1 - p2 와 그 신뢰구간."""

    p1: float
    p2: float
    diff: float
    ci: Tuple[float, float]
    n1: int
    n2: int


def newcombe_diff_interval(
    s1: int, n1: int, s2: int, n2: int, confidence: float = 0.95
) -> Optional[DiffResult]:
    """두 독립 비율의 차 (p1 - p2) 에 대한 Newcombe hybrid-score 신뢰구간.

    (Newcombe 1998, method 10.) 각 군의 Wilson 구간 (l_i, u_i) 를 구한 뒤

        lo = (p1 - p2) - sqrt((p1 - l1)^2 + (u2 - p2)^2)
        hi = (p1 - p2) + sqrt((u1 - p1)^2 + (p2 - l2)^2)

    로 합성한다. 단순 Wald 구간과 달리 표본이 작거나 비율이 0%/100% 근처여도
    [-1, 1] 을 벗어나지 않고 실제 포함확률이 명목 수준에 가깝다 — 군당 수십 명
    규모의 임상 유저테스트에서 특히 중요하다.

    n1 또는 n2 가 0 이면 (비교 대상이 없으면) None.
    """
    if n1 < 0 or n2 < 0:
        raise ValueError("n 은 음수일 수 없습니다")
    if not (0 <= s1 <= n1) or not (0 <= s2 <= n2):
        raise ValueError("successes 는 0..n 범위여야 합니다")
    if n1 == 0 or n2 == 0:
        return None
    w1 = wilson_interval(s1, n1, confidence)
    w2 = wilson_interval(s2, n2, confidence)
    assert w1 is not None and w2 is not None  # n>0 이므로 항상 존재
    l1, u1 = w1
    l2, u2 = w2
    p1 = s1 / n1
    p2 = s2 / n2
    diff = p1 - p2
    lo = diff - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = diff + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return DiffResult(
        p1=p1, p2=p2, diff=diff,
        ci=(max(-1.0, lo), min(1.0, hi)), n1=n1, n2=n2,
    )


# ---------------------------------------------------------------- Fisher exact

def _log_hypergeom_pmf(k: int, row1: int, row2: int, col1: int) -> float:
    """초기하 분포 pmf 의 로그값 — lgamma 로 계산해 큰 n 에서도 넘치지 않는다."""
    n = row1 + row2

    def lc(a: int, b: int) -> float:  # log C(a, b)
        return (
            math.lgamma(a + 1) - math.lgamma(b + 1) - math.lgamma(a - b + 1)
        )

    return lc(row1, k) + lc(row2, col1 - k) - lc(n, col1)


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """2×2 분할표 [[a, b], [c, d]] 의 Fisher 정확검정 양측 p 값.

    행 합·열 합을 고정한 초기하 분포에서, 관측된 표보다 확률이 같거나 낮은 모든
    표의 확률을 더한다 (conditional/'sum of small p' 정의 — R 의 `fisher.test`,
    scipy 의 `fisher_exact` 와 같은 관례).

    셀 빈도가 작을 때(임상 유저테스트에서 흔함) 카이제곱 근사는 1종 오류를 부풀리므로
    이 검정을 쓴다. 어떤 행이나 열의 합이 0 이면 비교할 것이 없으므로 1.0 을 반환한다.
    """
    for v in (a, b, c, d):
        if v < 0:
            raise ValueError("분할표 셀은 음수일 수 없습니다")
    row1, row2 = a + b, c + d
    col1, col2 = a + c, b + d
    if row1 == 0 or row2 == 0 or col1 == 0 or col2 == 0:
        return 1.0

    log_p_obs = _log_hypergeom_pmf(a, row1, row2, col1)
    p_obs = math.exp(log_p_obs)
    lo = max(0, col1 - row2)
    hi = min(row1, col1)
    total = 0.0
    for k in range(lo, hi + 1):
        p_k = math.exp(_log_hypergeom_pmf(k, row1, row2, col1))
        if p_k <= p_obs * _FISHER_EPS:
            total += p_k
    return min(1.0, total)


# ---------------------------------------------------------------- Mann-Whitney U

@dataclass
class MannWhitneyResult:
    u: float               # 1군 기준 U 통계량
    z: float               # 연속성 보정 정규근사 z (부호 = 방향; p 는 |z| 로 계산)
    p: float               # 양측 p 값
    n1: int
    n2: int
    median1: Optional[float]
    median2: Optional[float]
    rank_biserial: float   # 효과크기 (-1..1): 2U/(n1·n2) - 1


def _ranks_with_ties(xs: Sequence[float]) -> Tuple[List[float], float]:
    """오름차순 평균순위(midrank)와 동점 보정항 sum(t^3 - t) 를 반환."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    tie_term = 0.0
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        # i..j 는 동점 그룹 → 평균순위(1-기반)를 공유
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        t = j - i + 1
        if t > 1:
            tie_term += t ** 3 - t
        i = j + 1
    return ranks, tie_term


def mann_whitney_u(
    x: Sequence[float], y: Sequence[float]
) -> Optional[MannWhitneyResult]:
    """Mann-Whitney U 검정 (양측, 동점 보정 + 연속성 보정 정규근사).

    사용자당 이벤트 수·세션 길이처럼 심하게 치우친(skewed) 분포를 군 간 비교할 때
    t-검정보다 적절하다. 정규성을 가정하지 않고 순위만 쓴다.

    반환값의 `rank_biserial` 은 효과크기로, "1군에서 뽑은 값이 2군에서 뽑은 값보다
    클 확률 - 작을 확률" 이다 (동점은 절반씩). 0 = 차이 없음.

    주의 — 정규근사이므로 표본이 아주 작으면(각 군 ~8 미만) p 값이 대략적이다.
    이 경우 리포트는 p 값을 참고용으로만 표시한다. 한쪽 군이 비면 None.
    """
    n1, n2 = len(x), len(y)
    if n1 == 0 or n2 == 0:
        return None
    combined = list(x) + list(y)
    ranks, tie_term = _ranks_with_ties(combined)
    r1 = sum(ranks[:n1])
    u1 = r1 - n1 * (n1 + 1) / 2.0
    mu = n1 * n2 / 2.0
    n = n1 + n2
    if n > 1:
        var = (n1 * n2 / 12.0) * ((n + 1) - tie_term / (n * (n - 1.0)))
    else:
        var = 0.0
    if var <= 0.0:
        # 모든 값이 동점 → 순위로 구분할 정보가 없음
        z, p = 0.0, 1.0
    else:
        magnitude = max(0.0, (abs(u1 - mu) - 0.5) / math.sqrt(var))
        p = min(1.0, 2.0 * norm_sf(magnitude))
        # 부호는 방향(1군이 큰가 작은가)을 담는다 — 크기만 있으면 방향을 복원할 수 없다.
        z = magnitude if u1 >= mu else -magnitude
    return MannWhitneyResult(
        u=u1, z=z, p=p, n1=n1, n2=n2,
        median1=median(x), median2=median(y),
        rank_biserial=2.0 * u1 / (n1 * n2) - 1.0,
    )


# ------------------------------------------------- 3군 이상 총괄(omnibus) 검정

@dataclass
class KruskalResult:
    """Kruskal-Wallis H 검정 — 3군 이상 분포의 총괄 비교."""

    h: float                 # 동점 보정된 H 통계량
    df: int                  # k - 1
    p: float                 # 카이제곱 근사 양측 p
    n: List[int]             # 군별 표본 수
    medians: List[Optional[float]]
    epsilon_squared: float   # 효과크기 ε² = H/(N-1), 0~1


def kruskal_wallis(samples: Sequence[Sequence[float]]) -> Optional[KruskalResult]:
    """Kruskal-Wallis H 검정 (동점 보정, 카이제곱 근사).

    Mann-Whitney U 를 3군 이상으로 확장한 순위 기반 총괄검정이다. "적어도 한 군이
    다른가" 만 답하며, 어느 군이 다른지는 말해주지 않는다 — 그건 사후 쌍 비교의 몫.

    두 군만 넣으면 H 는 (연속성 보정 없는) Mann-Whitney 정규근사 z 의 제곱과 같다.

    비어 있지 않은 군이 2개 미만이거나 모든 값이 동점이면 순위로 가를 정보가 없어 None.
    표본이 작으면(군당 ~5 미만) 카이제곱 근사가 대략적이다.
    """
    groups = [list(s) for s in samples if len(s) > 0]
    k = len(groups)
    if k < 2:
        return None
    combined: List[float] = []
    for g in groups:
        combined.extend(g)
    n_total = len(combined)
    if n_total < 3:
        return None
    ranks, tie_term = _ranks_with_ties(combined)
    tie_correction = 1.0 - tie_term / (n_total ** 3 - n_total)
    if tie_correction <= 0.0:
        return None  # 전부 동점 → H 가 정의되지 않음
    total = 0.0
    pos = 0
    for g in groups:
        r_sum = sum(ranks[pos:pos + len(g)])
        total += r_sum * r_sum / len(g)
        pos += len(g)
    h = (12.0 / (n_total * (n_total + 1.0))) * total - 3.0 * (n_total + 1.0)
    h = max(0.0, h / tie_correction)
    df = k - 1
    return KruskalResult(
        h=h,
        df=df,
        p=chi2_sf(h, df),
        n=[len(g) for g in groups],
        medians=[median(g) for g in groups],
        epsilon_squared=min(1.0, h / (n_total - 1.0)),
    )


@dataclass
class HomogeneityResult:
    """k×2 비율 동질성 카이제곱 검정 — 3군 이상 비율의 총괄 비교."""

    chi2: float
    df: int
    p: float
    successes: List[int]
    totals: List[int]
    min_expected: float      # 최소 기대빈도 (5 미만이면 근사가 불안정)


def chi2_homogeneity(
    successes: Sequence[int], totals: Sequence[int]
) -> Optional[HomogeneityResult]:
    """k개 군의 성공비율이 모두 같은지에 대한 카이제곱 동질성 검정 (df = k-1).

    연속성 보정은 하지 않는다 (k×2 에서는 관례가 아니며 2×2 에서도 과보정 논란이 있다).
    기대빈도가 5 미만인 칸이 있으면 근사가 불안정하므로 `min_expected` 를 함께 돌려주니
    호출자가 경고를 붙일 수 있다 — 이때는 총괄 p 보다 쌍별 Fisher 정확검정을 보라.

    분모가 0 인 군을 빼고 2군 미만이거나, 성공이 전부/전무이면(변이 없음) None.
    """
    if len(successes) != len(totals):
        raise ValueError("successes 와 totals 의 길이가 다릅니다")
    rows = []
    for s, n in zip(successes, totals):
        if n < 0 or s < 0 or s > n:
            raise ValueError(f"성공 수는 0..n 범위여야 합니다 (받은 값: {s}/{n})")
        if n > 0:
            rows.append((s, n))
    if len(rows) < 2:
        return None
    n_total = sum(n for _, n in rows)
    s_total = sum(s for s, _ in rows)
    f_total = n_total - s_total
    if s_total == 0 or f_total == 0:
        return None  # 한 열이 통째로 0 → 기대빈도 0, 비교할 변이가 없음
    p_hat = s_total / n_total
    chi2 = 0.0
    min_expected = float("inf")
    for s, n in rows:
        e_s = n * p_hat
        e_f = n * (1.0 - p_hat)
        min_expected = min(min_expected, e_s, e_f)
        chi2 += (s - e_s) ** 2 / e_s + ((n - s) - e_f) ** 2 / e_f
    df = len(rows) - 1
    return HomogeneityResult(
        chi2=chi2, df=df, p=chi2_sf(chi2, df),
        successes=[s for s, _ in rows], totals=[n for _, n in rows],
        min_expected=min_expected,
    )


# ---------------------------------------------------------------- 생존분석

@dataclass
class KMPoint:
    time: float
    n_risk: int
    n_event: int
    survival: float
    ci: Optional[Tuple[float, float]]


@dataclass
class KMCurve:
    points: List[KMPoint]
    n: int
    n_events: int
    median_survival: Optional[float]   # S(t) 가 처음 0.5 이하가 되는 t (없으면 None)


def kaplan_meier(
    times: Sequence[float],
    events: Sequence[bool],
    confidence: float = 0.95,
) -> Optional[KMCurve]:
    """Kaplan-Meier 생존함수 추정 (Greenwood 분산 + log-log 변환 신뢰구간).

    - times:  각 대상의 관찰 시간 (이탈까지 또는 절단까지)
    - events: True = 사건(이탈) 관찰, False = 우측 절단(censored)

    같은 시각에 사건과 절단이 겹치면 관례대로 **사건을 먼저** 처리한다(절단된 대상은
    그 시각의 위험집합에 포함).

    신뢰구간은 log-log 변환(θ = ln(-ln S))으로 만들어 [0,1] 을 벗어나지 않으며
    꼬리에서 단순 Greenwood 구간보다 포함확률이 낫다. S(t) 가 0 또는 1 인 구간에서는
    변환이 정의되지 않아 구간을 None 으로 둔다.

    입력이 비었으면 None. times 와 events 의 길이는 같아야 한다.
    """
    if len(times) != len(events):
        raise ValueError("times 와 events 의 길이가 다릅니다")
    if not times:
        return None
    if any(t < 0 or not math.isfinite(t) for t in times):
        raise ValueError("생존 시간은 0 이상의 유한한 값이어야 합니다")
    z = z_for_confidence(confidence)

    # 시각 오름차순 한 번만 정렬하고 위험집합을 누적으로 줄여 나간다 (O(n log n)).
    # 같은 시각의 절단은 그 시각의 위험집합에 포함되므로 n_risk 를 먼저 읽고 나서 뺀다.
    pairs = sorted(zip(times, events), key=lambda p: p[0])
    n_total = len(pairs)

    points: List[KMPoint] = []
    surv = 1.0
    var_sum = 0.0  # Greenwood 누적합 Σ d/(n(n-d))
    n_events_total = 0
    median_surv: Optional[float] = None
    n_risk_remaining = n_total
    i = 0
    while i < n_total:
        t = pairs[i][0]
        j = i
        d = 0
        while j < n_total and pairs[j][0] == t:
            d += 1 if pairs[j][1] else 0
            j += 1
        n_risk = n_risk_remaining
        n_risk_remaining -= (j - i)
        i = j
        if d == 0:
            continue  # 절단만 있는 시각은 생존 계단을 만들지 않는다
        n_events_total += d
        surv *= 1.0 - d / n_risk
        if n_risk > d:
            var_sum += d / (n_risk * (n_risk - d))
        else:
            var_sum = math.inf  # 위험집합이 모두 사건 → 분산 발산
        points.append(
            KMPoint(
                time=float(t), n_risk=n_risk, n_event=d, survival=surv,
                ci=_km_ci(surv, var_sum, z),
            )
        )
        if median_surv is None and surv <= 0.5:
            median_surv = float(t)
    return KMCurve(
        points=points, n=n_total, n_events=n_events_total,
        median_survival=median_surv,
    )


def _km_ci(surv: float, var_sum: float, z: float) -> Optional[Tuple[float, float]]:
    """KM 생존확률의 log-log 변환 신뢰구간. S∈(0,1) 이고 분산이 유한할 때만."""
    if not (0.0 < surv < 1.0) or not math.isfinite(var_sum) or var_sum <= 0.0:
        return None
    log_s = math.log(surv)
    theta = math.log(-log_s)
    se_theta = math.sqrt(var_sum) / abs(log_s)
    lo = math.exp(-math.exp(theta + z * se_theta))
    hi = math.exp(-math.exp(theta - z * se_theta))
    return (max(0.0, lo), min(1.0, hi))


@dataclass
class LogRankResult:
    chi2: float
    p: float
    observed1: int
    expected1: float
    observed2: int
    expected2: float


def logrank_test(
    times1: Sequence[float], events1: Sequence[bool],
    times2: Sequence[float], events2: Sequence[bool],
) -> Optional[LogRankResult]:
    """두 군의 생존곡선 동일성에 대한 log-rank 검정 (자유도 1).

    각 사건 시각에서 초기하 분포의 기대 사건수 E1 = d·n1/n 과 분산
    V = d·n1·n2·(n-d) / (n^2·(n-1)) 을 누적해 chi2 = (O1-E1)^2 / ΣV 로 계산한다.
    동점(같은 시각의 사건)도 이 초기하 분산이 정확히 처리한다.

    비례위험(proportional hazards)을 가정하며, 곡선이 교차하면 검정력이 떨어진다 —
    p 값만 보지 말고 KM 곡선을 함께 볼 것.

    보고되는 observed/expected 는 **비교 가능한 시각**(두 군을 합쳐 위험집합이 2명 이상인
    시각)으로 한정해 누적한 값이다. 한 군만 남은 시점의 사건은 분산이 정의되지 않아
    통계량에 기여할 수 없으므로 O 와 E 양쪽에서 똑같이 제외한다 — 그래서
    (O1-E1) + (O2-E2) = 0 이 성립하고, 한쪽만 제외해 다른 군에 떠넘기는 일이 없다.
    따라서 observed1 + observed2 가 전체 사건 수보다 작을 수 있다(정상).

    어느 한 군이 비었거나 사건이 전혀 없으면 None.
    """
    if len(times1) != len(events1) or len(times2) != len(events2):
        raise ValueError("times 와 events 의 길이가 다릅니다")
    if not times1 or not times2:
        return None
    o1_total = sum(1 for e in events1 if e)
    o2_total = sum(1 for e in events2 if e)
    if o1_total + o2_total == 0:
        return None

    # 시각별 (관측 수, 사건 수)를 미리 집계해 위험집합을 누적으로 줄인다 (O(n log n)).
    from collections import Counter

    at_risk1 = Counter(times1)
    at_risk2 = Counter(times2)
    ev_at1 = Counter(t for t, ev in zip(times1, events1) if ev)
    ev_at2 = Counter(t for t, ev in zip(times2, events2) if ev)
    all_times = sorted(set(at_risk1) | set(at_risk2))
    n1_remaining = len(times1)
    n2_remaining = len(times2)
    o1 = 0
    o2 = 0
    e1_sum = 0.0
    e2_sum = 0.0
    v_sum = 0.0
    for t in all_times:
        n1 = n1_remaining
        n2 = n2_remaining
        n1_remaining -= at_risk1.get(t, 0)
        n2_remaining -= at_risk2.get(t, 0)
        d1 = ev_at1.get(t, 0)
        d2 = ev_at2.get(t, 0)
        d = d1 + d2
        n = n1 + n2
        if d == 0 or n < 2:
            continue
        o1 += d1
        o2 += d2
        e1_sum += d * n1 / n
        e2_sum += d * n2 / n
        v_sum += d * n1 * n2 * (n - d) / (n * n * (n - 1.0))
    if v_sum <= 0.0:
        return None
    chi2 = (o1 - e1_sum) ** 2 / v_sum
    return LogRankResult(
        chi2=chi2, p=chi2_sf_1df(chi2),
        observed1=o1, expected1=e1_sum,
        observed2=o2, expected2=e2_sum,
    )


# ------------------------------------------------ 대응표본(paired) 이분형 검정

@dataclass
class CochranQResult:
    """Cochran's Q — 같은 참여자를 k개 시점에서 반복 관찰한 이분형 결과의 동질성 검정."""

    q: float
    df: int
    p: float
    n_subjects: int
    k: int
    successes_by_time: List[int]     # 시점별 성공(=1) 수


def cochran_q(rows: Sequence[Sequence[int]]) -> Optional[CochranQResult]:
    """k개 시점의 대응표본 이분형 자료에 대한 Cochran's Q 검정.

    rows[i][j] = 참여자 i 의 시점 j 결과(0/1). 모든 행의 길이가 같아야 한다
    (결측 없는 **균형 패널**만 받는다 — Q 는 결측을 다루지 못한다).

    귀무가설: k개 시점의 성공 확률이 모두 같다. Q ~ chi2(k-1) 근사.

        Q = (k-1) · [k·ΣG_j² − (ΣG_j)²] / [k·ΣL_i − ΣL_i²]

    여기서 G_j 는 시점별 성공 수, L_i 는 참여자별 성공 수다.

    분모가 0 이면(모든 참여자가 전 시점 성공이거나 전 시점 실패) 시점 간 차이를
    말해 줄 정보가 없으므로 None 을 돌려준다 — 이때 Q=0/p=1 로 보고하면
    "차이가 없다" 는 증거처럼 읽히지만 실제로는 검정이 불가능한 상태다.

    McNemar 와의 관계: k=2 이면 Q 는 연속성 보정 없는 McNemar 카이제곱과 같다.
    표본이 작으면(불일치 쌍이 적으면) Q 의 카이제곱 근사가 나쁘므로
    k=2 는 `mcnemar_exact` 를 쓰라.
    """
    n = len(rows)
    if n == 0:
        return None
    k = len(rows[0])
    if k < 2:
        raise ValueError(f"시점은 2개 이상이어야 합니다 (받은 값: {k})")
    col = [0] * k
    row_tot: List[int] = []
    for r in rows:
        if len(r) != k:
            raise ValueError("모든 참여자의 시점 수가 같아야 합니다 (균형 패널만 가능)")
        s = 0
        for j, v in enumerate(r):
            if v not in (0, 1, True, False):
                raise ValueError(f"결과값은 0 또는 1 이어야 합니다 (받은 값: {v!r})")
            if v:
                col[j] += 1
                s += 1
        row_tot.append(s)
    denom = k * sum(row_tot) - sum(v * v for v in row_tot)
    if denom <= 0:
        return None
    total = sum(col)
    numer = (k - 1) * (k * sum(v * v for v in col) - total * total)
    q = numer / denom
    return CochranQResult(
        q=q, df=k - 1, p=chi2_sf(q, k - 1), n_subjects=n, k=k,
        successes_by_time=col,
    )


@dataclass
class McNemarResult:
    """McNemar 정확검정 — 같은 참여자의 두 시점 이분형 결과 비교."""

    b: int                # 시점1 성공 → 시점2 실패 (불일치)
    c: int                # 시점1 실패 → 시점2 성공 (불일치)
    n_discordant: int
    p: float              # 양측 정확검정 (이항 n=b+c, p=0.5)


def mcnemar_exact(b: int, c: int) -> McNemarResult:
    """불일치 쌍 (b, c) 에 대한 McNemar 양측 **정확**검정.

    일치 쌍(둘 다 성공/둘 다 실패)은 검정에 정보를 주지 않으므로 들어가지 않는다.
    귀무가설 하에서 불일치 쌍은 Bin(b+c, 0.5) 를 따르므로

        p = min(1, 2 · P(X ≤ min(b, c)))

    카이제곱 근사 대신 정확검정을 쓰는 이유: 임상 코호트는 불일치 쌍이 한 자릿수인
    경우가 흔하고(참여자 24명 규모), 그 영역에서 근사는 p 를 과소평가한다.

    b = c = 0 이면(불일치 쌍 없음) p = 1.0 — 두 시점이 완전히 같았다는 뜻이다.
    """
    if b < 0 or c < 0:
        raise ValueError(f"불일치 쌍 수는 0 이상이어야 합니다 (받은 값: b={b}, c={c})")
    n = b + c
    if n == 0:
        return McNemarResult(b=b, c=c, n_discordant=0, p=1.0)
    lo = min(b, c)
    # 이항 하측 꼬리를 정수 조합으로 정확히 — 부동소수 누적 없이 분수로 더한다.
    # 정수 조합의 합을 정수 2^n 으로 나눈다 — 몫이 1 이하라 큰 n 에서도 오버플로 없이
    # 올바르게 반올림된다(부동소수 2.0**n 은 n≥1024 에서 inf 가 된다).
    tail = sum(math.comb(n, i) for i in range(lo + 1))
    return McNemarResult(b=b, c=c, n_discordant=n, p=min(1.0, (2 * tail) / (2 ** n)))


# ---------------------------------------------------------------- 발생률(Poisson)

# 이 사건 수를 넘으면 정확 구간 대신 로그-정규 근사를 쓴다. 불완전감마 급수의 반복
# 상한(1000)이 a ~ 1e6 부근에서 부족해지기 시작하고, 그쯤이면 두 방법의 차이가
# 상대적으로 1e-4 미만이라 실용적 손실이 없다.
MAX_EXACT_POISSON_K = 200_000

# 조건부 이항 정확검정을 그대로 계산할 최대 시행 수 (O(n) 항 합산). 이보다 크면
# 연속성 보정 정규근사로 대체한다 — 그 영역에서는 두 값이 사실상 같다.
MAX_EXACT_BINOM_N = 200_000


def chi2_ppf(p: float, df: int) -> float:
    """자유도 df 카이제곱의 분위수 — P(X ≤ x) = p 를 만족하는 x.

    `chi2_sf` 가 x 에 대해 단조감소한다는 사실만 이용한 괄호잡기 + 이분법이라
    별도의 근사식 없이 `chi2_sf` 와 항상 일관된 값을 준다(역함수 관계가 깨지지 않는다).
    """
    if not (0.0 <= p < 1.0):
        raise ValueError(f"p 는 0 이상 1 미만이어야 합니다 (받은 값: {p})")
    if df < 1:
        raise ValueError(f"자유도는 1 이상이어야 합니다 (받은 값: {df})")
    if p == 0.0:
        return 0.0
    target = 1.0 - p  # 찾는 x 에서의 상측꼬리 확률
    lo = 0.0
    hi = max(1.0, float(df))
    # sf(hi) 가 target 아래로 내려갈 때까지 상한을 넓힌다.
    for _ in range(200):
        if chi2_sf(hi, df) <= target:
            break
        lo = hi
        hi *= 2.0
    else:  # pragma: no cover - df<1e300 이면 도달 불가
        return hi
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if mid <= lo or mid >= hi:  # 부동소수 해상도 한계 — 더 좁힐 수 없다
            break
        if chi2_sf(mid, df) > target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def poisson_exact_interval(
    k: int, confidence: float = 0.95
) -> Tuple[float, float]:
    """관측 사건 수 k 에 대한 Poisson 평균의 정확(Garwood) 신뢰구간.

    카이제곱 분위수와의 관계를 그대로 쓴다:

        lower = χ²(α/2; 2k) / 2      (k = 0 이면 0)
        upper = χ²(1−α/2; 2k+2) / 2

    Wald 구간(k ± z√k)과 달리 k 가 한 자릿수여도 음수로 내려가지 않고 실제
    포함확률이 명목 수준 이상이다 — 임상 로그에서 흔한 "8주간 이상반응 3건" 같은
    작은 수에 필요한 성질이다.

    k 가 매우 클 때(`MAX_EXACT_POISSON_K` 초과)는 로그-정규 근사로 대체한다.
    """
    if k < 0:
        raise ValueError(f"사건 수는 0 이상이어야 합니다 (받은 값: {k})")
    if not (math.isfinite(confidence) and 0.0 < confidence < 1.0):
        raise ValueError(f"신뢰수준은 0 과 1 사이여야 합니다 (받은 값: {confidence})")
    alpha = 1.0 - confidence
    if k > MAX_EXACT_POISSON_K:
        z = z_for_confidence(confidence)
        half = z / math.sqrt(k)
        return (k * math.exp(-half), k * math.exp(half))
    lower = 0.0 if k == 0 else chi2_ppf(alpha / 2.0, 2 * k) / 2.0
    upper = chi2_ppf(1.0 - alpha / 2.0, 2 * k + 2) / 2.0
    return (lower, upper)


def binom_test_two_sided(k: int, n: int, p0: float) -> float:
    """이항 정확검정(양측) — H0: 성공확률 = p0.

    양측 p 값은 "관측된 것만큼 또는 그보다 확률이 낮은" 모든 결과의 확률 합
    (small-p method, Fisher exact 와 같은 관례)이다.

    두 발생률의 비를 검정할 때 이 함수가 곧 **조건부 Poisson 정확검정**이 된다:
    전체 사건 수 N 을 고정하면 한쪽 군의 사건 수는 Binomial(N, π) 를 따르고,
    귀무가설(rate ratio = 1)에서 π₀ = T_a / (T_a + T_b) (T = 관찰 인-시간)이다.

    n 이 매우 클 때(`MAX_EXACT_BINOM_N` 초과)는 연속성 보정 정규근사로 대체한다.
    """
    if n < 0 or not (0 <= k <= n):
        raise ValueError(f"k 는 0..n 범위여야 합니다 (받은 값: k={k}, n={n})")
    if not (math.isfinite(p0) and 0.0 <= p0 <= 1.0):
        raise ValueError(f"p0 는 0 과 1 사이여야 합니다 (받은 값: {p0})")
    if n == 0:
        return 1.0
    if p0 == 0.0:
        return 1.0 if k == 0 else 0.0
    if p0 == 1.0:
        return 1.0 if k == n else 0.0
    if n > MAX_EXACT_BINOM_N:
        mean = n * p0
        sd = math.sqrt(n * p0 * (1.0 - p0))
        if sd == 0.0:  # pragma: no cover - p0 이 0/1 인 경우는 위에서 걸러짐
            return 1.0 if k == mean else 0.0
        z = (abs(k - mean) - 0.5) / sd
        return min(1.0, 2.0 * norm_sf(max(0.0, z)))
    # log pmf 를 점화식으로 훑는다: logpmf(i+1) = logpmf(i) + log((n-i)/(i+1)) + logit(p0).
    logit = math.log(p0) - math.log1p(-p0)
    logpmf = n * math.log1p(-p0)
    logs: List[float] = [logpmf]
    for i in range(n):
        logpmf += math.log((n - i) / (i + 1)) + logit
        logs.append(logpmf)
    threshold = logs[k] + 1e-7  # Fisher exact 와 같은 상대 허용오차
    return min(1.0, sum(math.exp(lp) for lp in logs if lp <= threshold))


@dataclass
class RateRatioResult:
    """두 발생률의 비(rate ratio)와 그 구간·p 값."""

    events_a: int
    time_a: float          # 인-시간 (a 군)
    events_b: int
    time_b: float
    rate_a: Optional[float]
    rate_b: Optional[float]
    ratio: Optional[float]           # rate_a / rate_b
    ci: Optional[Tuple[float, float]]  # log 비의 Wald 구간 (과산포 보정 포함)
    p_exact: float                   # 조건부 이항 정확검정 (과산포 보정 없음)
    p_value: float                   # 과산포 보정 Wald 검정 (dispersion=1 이면 Poisson Wald)
    dispersion: float                # 적용한 과산포 계수 φ (1 미만은 1 로 둔다)


def rate_ratio_test(
    events_a: int,
    time_a: float,
    events_b: int,
    time_b: float,
    *,
    confidence: float = 0.95,
    dispersion: float = 1.0,
) -> Optional[RateRatioResult]:
    """두 군의 발생률 비 λ_a/λ_b 를 검정한다.

    - p_exact : 조건부 이항 정확검정. Poisson 가정(사건들이 서로 독립)이 맞을 때 정확하다.
    - p_value : log 비에 대한 Wald 검정, SE = √(φ · (1/e_a + 1/e_b)).
      φ 는 과산포(overdispersion) 계수다. 같은 사람이 사건을 여러 번 만드는 반복이벤트
      자료에서는 φ > 1 이 보통이고, 그때 Poisson 구간은 **실제보다 좁다**. φ 를 넘기면
      quasi-Poisson 방식으로 구간과 p 값을 함께 넓힌다. φ < 1 은 1 로 둔다(구간을
      Poisson 보다 좁히지 않는다).

    어느 한쪽 사건 수가 0 이면 비와 구간은 정의되지 않는다(None) — 정확검정 p 값은
    여전히 계산된다.
    """
    if events_a < 0 or events_b < 0:
        raise ValueError("사건 수는 0 이상이어야 합니다")
    if not (time_a > 0.0 and time_b > 0.0) or not (
        math.isfinite(time_a) and math.isfinite(time_b)
    ):
        return None
    if not (math.isfinite(dispersion) and dispersion > 0.0):
        raise ValueError(f"과산포 계수는 양의 유한한 수여야 합니다 (받은 값: {dispersion})")
    phi = max(1.0, dispersion)
    total = events_a + events_b
    rate_a = events_a / time_a
    rate_b = events_b / time_b
    p0 = time_a / (time_a + time_b)
    p_exact = binom_test_two_sided(events_a, total, p0)
    if events_a == 0 or events_b == 0:
        return RateRatioResult(
            events_a=events_a, time_a=time_a, events_b=events_b, time_b=time_b,
            rate_a=rate_a, rate_b=rate_b, ratio=None, ci=None,
            p_exact=p_exact, p_value=p_exact, dispersion=phi,
        )
    ratio = rate_a / rate_b
    se = math.sqrt(phi * (1.0 / events_a + 1.0 / events_b))
    z = z_for_confidence(confidence)
    log_r = math.log(ratio)
    ci = (math.exp(log_r - z * se), math.exp(log_r + z * se))
    p_wald = min(1.0, 2.0 * norm_sf(abs(log_r) / se))
    return RateRatioResult(
        events_a=events_a, time_a=time_a, events_b=events_b, time_b=time_b,
        rate_a=rate_a, rate_b=rate_b, ratio=ratio, ci=ci,
        p_exact=p_exact, p_value=p_wald, dispersion=phi,
    )


def poisson_dispersion(
    counts: Sequence[int], exposures: Sequence[float], rates: Sequence[float]
) -> Optional[Tuple[float, float, int, float]]:
    """Pearson 과산포 계수 φ = X²/df 와 그 적합도 검정.

    X² = Σ (kᵢ − λᵢ·tᵢ)² / (λᵢ·tᵢ) — 사람 i 의 기대 사건 수 대비 편차의 제곱합.
    Poisson 이 맞으면 X² ~ χ²(df) 이고 φ ≈ 1 이다. 같은 사람이 사건을 몰아서 만드는
    군집(clustering)이 있으면 φ 가 1 보다 크게 나오고, 그만큼 Poisson 신뢰구간을
    믿으면 안 된다는 신호가 된다.

    df 는 (관측 수 − 추정한 비율의 수)로, `rates` 에 들어 있는 서로 다른 값의 개수를
    추정 모수로 센다. 반환: (φ, X², df, p) 또는 계산 불가 시 None.
    """
    if not (len(counts) == len(exposures) == len(rates)):
        raise ValueError("counts·exposures·rates 의 길이가 서로 같아야 합니다")
    chi2 = 0.0
    n = 0
    for k, t, lam in zip(counts, exposures, rates):
        expected = lam * t
        if not (expected > 0.0):
            continue
        n += 1
        chi2 += (k - expected) ** 2 / expected
    n_params = len(set(r for r in rates if r > 0.0))
    df = n - n_params
    if df < 1:
        return None
    phi = chi2 / df
    return (phi, chi2, df, chi2_sf(chi2, df))


# ---------------------------------------------------------------- 다중비교 보정

def holm_adjust(pvalues: Sequence[float]) -> List[float]:
    """Holm–Bonferroni 단계적 하향(step-down) 보정 p 값.

    m 개의 p 값을 오름차순으로 정렬해 k 번째(0-기반)에 (m-k) 를 곱하고, 앞선 값보다
    작아지지 않도록 누적 최대값을 취한 뒤 1 로 클램프한다. 원래 순서로 돌려준다.

    Bonferroni 보다 검정력이 높으면서도 family-wise 오류율을 같은 수준으로 통제한다.
    logflow 는 군 비교에서 여러 검정(리텐션 day-N·퍼널 완주·참여도·log-rank)을 한꺼번에
    하므로, 보정하지 않은 p 값만 보고하면 우연한 '유의' 를 그대로 싣게 된다.
    """
    ps = list(pvalues)
    for p in ps:
        if not (0.0 <= p <= 1.0) or math.isnan(p):
            raise ValueError(f"p 값은 0..1 범위여야 합니다 (받은 값: {p})")
    m = len(ps)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: ps[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, (m - rank) * ps[idx])
        adjusted[idx] = min(1.0, running)
    return adjusted


def _inv_norm_cdf(p: float) -> float:
    """표준정규 분위함수(역 CDF)의 Acklam 유리근사. z-공간 최대오차 ~3e-9 수준."""
    if not (0.0 < p < 1.0):
        raise ValueError("p 는 (0,1) 범위여야 합니다")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow = 0.02425
    phigh = 1.0 - plow
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
               (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
