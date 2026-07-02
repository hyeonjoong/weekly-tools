"""자립형(scipy 불필요) 통계 함수.

Bartlett 구형성 검정의 p값에 필요한 카이제곱 상측꼬리확률만 순수 파이썬으로
구현한다(Numerical Recipes의 정규화 불완전감마 함수). scipy에 의존하지 않기 위함.
"""
from __future__ import annotations

import math

_MAXIT = 1000
_EPS = 1e-15
_FPMIN = 1e-300


def _gser(a: float, x: float) -> float:
    """정규화 하측 불완전감마 P(a, x)를 급수 전개로 계산 (x < a+1 에서 수렴 빠름)."""
    if x <= 0.0:
        return 0.0
    gln = math.lgamma(a)
    ap = a
    total = 1.0 / a
    delta = total
    for _ in range(_MAXIT):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * _EPS:
            break
    return total * math.exp(-x + a * math.log(x) - gln)


def _gcf(a: float, x: float) -> float:
    """정규화 상측 불완전감마 Q(a, x)를 연분수로 계산 (x >= a+1 에서 수렴 빠름)."""
    gln = math.lgamma(a)
    b = x + 1.0 - a
    c = 1.0 / _FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, _MAXIT):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = b + an / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def gammq(a: float, x: float) -> float:
    """정규화 상측 불완전감마 Q(a, x) = 1 - P(a, x)."""
    if x < 0.0 or a <= 0.0:
        raise ValueError("gammq: a>0, x>=0 이어야 합니다")
    if x == 0.0:
        return 1.0
    if x < a + 1.0:
        return 1.0 - _gser(a, x)
    return _gcf(a, x)


def chi2_sf(x: float, df: float) -> float:
    """자유도 df인 카이제곱 분포의 상측꼬리확률 P(X > x)."""
    if df <= 0:
        raise ValueError("chi2_sf: 자유도는 양수여야 합니다")
    if x <= 0.0:
        return 1.0
    return gammq(df / 2.0, x / 2.0)
