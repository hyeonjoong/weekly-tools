"""순수 파이썬 통계 함수 모음 (표준 라이브러리만 사용).

설문 분석에 필요한 최소한의 통계량을 직접 구현한다. 외부 의존성을 두지 않아
어디서든 바로 실행되고, 손계산으로 검증한 값과 1:1로 맞춘다.

규약
- 분산/표준편차는 표본 기준(ddof=1)을 사용한다. 관측치가 2개 미만이면 None.
- 모든 함수는 결측이 이미 제거된(=None이 없는) float 리스트를 받는다.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from . import special


def mean(xs: Sequence[float]) -> Optional[float]:
    """산술평균. 빈 리스트면 None."""
    if not xs:
        return None
    return sum(xs) / len(xs)


def variance(xs: Sequence[float]) -> Optional[float]:
    """표본분산(ddof=1). 관측치가 2개 미만이면 None."""
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    return sum((x - m) ** 2 for x in xs) / (n - 1)


def stdev(xs: Sequence[float]) -> Optional[float]:
    """표본표준편차. 관측치가 2개 미만이면 None."""
    v = variance(xs)
    return math.sqrt(v) if v is not None else None


def median(xs: Sequence[float]) -> Optional[float]:
    """중앙값. 빈 리스트면 None."""
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def quantile(xs: Sequence[float], q: float) -> Optional[float]:
    """분위수(선형보간, R type-7 / numpy 기본과 동일). q∈[0,1]. 빈 리스트면 None."""
    if not xs:
        return None
    if not (0.0 <= q <= 1.0):
        raise ValueError("quantile: q는 0과 1 사이여야 합니다.")
    s = sorted(xs)
    n = len(s)
    if n == 1:
        return float(s[0])
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return float(s[lo] + (s[hi] - s[lo]) * frac)


def skewness(xs: Sequence[float]) -> Optional[float]:
    """표본 왜도(bias 보정 G1, SPSS/Excel과 동일). 관측치 3개 미만이거나 분산 0이면 None."""
    n = len(xs)
    if n < 3:
        return None
    m = sum(xs) / n
    m2 = sum((x - m) ** 2 for x in xs) / n
    if m2 == 0:
        return None
    m3 = sum((x - m) ** 3 for x in xs) / n
    g1 = m3 / (m2 ** 1.5)
    # 편향보정: G1 = g1 * sqrt(n(n-1))/(n-2)
    return g1 * math.sqrt(n * (n - 1)) / (n - 2)


def kurtosis(xs: Sequence[float]) -> Optional[float]:
    """표본 초과첨도(bias 보정 G2, SPSS/Excel과 동일). 관측치 4개 미만이거나 분산 0이면 None."""
    n = len(xs)
    if n < 4:
        return None
    m = sum(xs) / n
    m2 = sum((x - m) ** 2 for x in xs) / n
    if m2 == 0:
        return None
    m4 = sum((x - m) ** 4 for x in xs) / n
    g2 = m4 / (m2 ** 2) - 3.0
    # 편향보정: G2 = ((n+1)*g2 + 6) * (n-1)/((n-2)(n-3))
    return ((n + 1) * g2 + 6) * (n - 1) / ((n - 2) * (n - 3))


def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """피어슨 상관계수. 길이가 다르거나 한쪽 분산이 0이면 None."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx, my = mean(xs), mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def cronbach_alpha(item_columns: Sequence[Sequence[float]]) -> Optional[float]:
    """Cronbach's alpha (내적 일치도 신뢰도).

    item_columns[i] = 문항 i 의 응답들(완전응답자만, 응답자 순서 동일).
    공식: alpha = (k/(k-1)) * (1 - sum(item_var) / total_var)
      k        = 문항 수
      item_var = 각 문항의 표본분산
      total_var= 응답자별 총점(문항 합)의 표본분산

    문항이 2개 미만, 응답자가 2명 미만, 또는 총점 분산이 0이면 None.
    """
    k = len(item_columns)
    if k < 2:
        return None
    n = len(item_columns[0])
    if n < 2 or any(len(col) != n for col in item_columns):
        return None
    item_var_sum = 0.0
    for col in item_columns:
        v = variance(col)
        if v is None:
            return None
        item_var_sum += v
    totals = [sum(item_columns[i][r] for i in range(k)) for r in range(n)]
    total_var = variance(totals)
    if total_var is None or total_var == 0:
        return None
    return (k / (k - 1)) * (1 - item_var_sum / total_var)


def cronbach_alpha_ci(
    alpha: Optional[float], n_subjects: int, k_items: int, conf: float = 0.95
) -> Optional[Tuple[float, float]]:
    """Cronbach α의 신뢰구간 (Feldt 1965, F 분포 기반).

    CI = [1 - (1-α)·F_{1-a/2}(df1,df2),  1 - (1-α)·F_{a/2}(df1,df2)]
      df1 = n-1,  df2 = (n-1)(k-1),  a = 1-conf

    α가 None이거나 응답자<2·문항<2면 None. 상한은 1.0으로 클램프한다
    (F 분위수 반올림으로 1을 미세하게 넘는 것을 방지).
    """
    if alpha is None or n_subjects < 2 or k_items < 2:
        return None
    if not (0.0 < conf < 1.0):
        raise ValueError("conf는 0과 1 사이여야 합니다.")
    a = 1.0 - conf
    df1 = n_subjects - 1
    df2 = (n_subjects - 1) * (k_items - 1)
    lower = 1.0 - (1.0 - alpha) * special.f_ppf(1.0 - a / 2.0, df1, df2)
    upper = 1.0 - (1.0 - alpha) * special.f_ppf(a / 2.0, df1, df2)
    return (lower, min(upper, 1.0))


def sem_from_alpha(sd_total: Optional[float], alpha: Optional[float]) -> Optional[float]:
    """측정의 표준오차 SEM = SD_총점 · sqrt(1-α).

    고전검사이론에서 SEM = σ_E = SD·√(1-ρ) 이고 신뢰도 ρ∈[0,1] 이므로 SEM ≤ SD 이다.
    따라서 α 가 [0,1] 밖이면 SEM 은 정의되지 않는다 → None.

    - α>1: 이 추정량에서는 사실상 발생하지 않지만 방어적으로 막는다.
    - α<0: 역문항 재코딩 누락 등으로 평균 문항간 상관이 음수일 때 실제로 발생한다.
      이때 sqrt(1-α)>1 이 되어 SEM 이 SD 보다 커지는 불가능한 값이 나오고,
      그 값으로 계산한 MDC₉₅ 는 척도 범위의 절반을 넘기도 한다. 틀린 숫자를
      보고하느니 산출불가로 남긴다(리포트는 '-' 로 표기).
    """
    if sd_total is None or alpha is None:
        return None
    if alpha < 0.0 or alpha > 1.0:
        return None
    return sd_total * math.sqrt(1.0 - alpha)


def t_ci_mean(
    xs: Sequence[float], conf: float = 0.95
) -> Optional[Tuple[float, float]]:
    """평균의 t 기반 신뢰구간. 관측치 2개 미만이면 None."""
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    sd = stdev(xs)
    if sd is None:
        return None
    se = sd / math.sqrt(n)
    tcrit = special.t_ppf(1.0 - (1.0 - conf) / 2.0, n - 1)
    return (m - tcrit * se, m + tcrit * se)
