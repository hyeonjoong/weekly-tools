"""작은 통계 유틸 (표준 라이브러리만).

- 비율(proportion)의 신뢰구간: Wilson score interval — 리텐션/퍼널 전환율처럼
  0/1 비율을 논문·리포트에 실을 때 표본이 작아도 안정적인 95% 구간을 준다.
- 분위수(quantile): 세션 길이처럼 치우친(skewed) 분포를 평균 하나로 요약하면
  오해를 부르므로 중앙값·사분위수를 함께 보고하기 위한 헬퍼.

외부 의존성 없음. 모두 순수 함수.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

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
