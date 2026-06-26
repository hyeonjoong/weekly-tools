"""순수 파이썬 통계 함수 모음 (표준 라이브러리만 사용).

설문 분석에 필요한 최소한의 통계량을 직접 구현한다. 외부 의존성을 두지 않아
어디서든 바로 실행되고, 손계산으로 검증한 값과 1:1로 맞춘다.

규약
- 분산/표준편차는 표본 기준(ddof=1)을 사용한다. 관측치가 2개 미만이면 None.
- 모든 함수는 결측이 이미 제거된(=None이 없는) float 리스트를 받는다.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence


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
