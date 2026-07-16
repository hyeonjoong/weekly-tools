"""표준 라이브러리만으로 구현한 특수함수 · 분포 분위수.

신뢰구간(CI)을 계산하려면 t 분포와 F 분포의 역누적분포(분위수)가 필요하다.
외부 의존성(scipy) 없이 동작해야 하므로 정규화 불완전 베타함수(regularized
incomplete beta)를 직접 구현하고, 그 위에 t·F 분포의 CDF/PPF를 올린다.

정확도: scipy.special.betainc / scipy.stats.t / scipy.stats.f 와 대조해
상대오차 ~1e-12 수준에서 일치함을 테스트로 확인한다(테스트에서만 scipy 사용).

규약
- 모든 함수는 결측이 없는 유한한 float를 받는다.
- 분위수(PPF)는 0<p<1 에 대해서만 정의. 경계는 ±inf 를 반환하지 않고 호출부에서
  걸러야 한다(여기서는 p 를 (eps, 1-eps)로 클램프해 유한값을 보장).
"""
from __future__ import annotations

import math

# 연속분수 반복의 상한/정밀도.
_MAXIT = 500
_EPS = 3.0e-16
_FPMIN = 1.0e-300


def _betacf(a: float, b: float, x: float) -> float:
    """정규화 불완전 베타함수의 연속분수(Lentz 알고리즘). Numerical Recipes 참고."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, _MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """정규화 불완전 베타함수 I_x(a, b). x∈[0,1], a>0, b>0. 반환 ∈[0,1]."""
    if a <= 0 or b <= 0:
        raise ValueError("betainc: a, b는 양수여야 합니다.")
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    # 로그감마로 접두인자 계산(오버플로 방지).
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    # 수렴이 빠른 쪽을 고른다(대칭식 사용).
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def betaincinv(a: float, b: float, p: float) -> float:
    """I_x(a, b) = p 를 만족하는 x (역 정규화 불완전 베타). p∈[0,1]."""
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if betainc(a, b, mid) < p:
            lo = mid
        else:
            hi = mid
        # 절대·상대 기준을 함께 사용해 x가 0에 매우 가까운 극단 꼬리에서도
        # 상대정밀도를 확보한다(일반적인 설문 CI 범위에는 영향 없음).
        if hi - lo <= 1e-15 + 4e-16 * abs(hi):
            break
    return 0.5 * (lo + hi)


def t_cdf(x: float, df: float) -> float:
    """스튜던트 t 분포의 CDF P(T ≤ x). df>0."""
    if df <= 0:
        raise ValueError("t_cdf: df는 양수여야 합니다.")
    if x == 0.0:
        return 0.5
    xb = df / (df + x * x)
    tail = 0.5 * betainc(df / 2.0, 0.5, xb)  # P(T ≤ -|x|)
    return tail if x < 0 else 1.0 - tail


def t_ppf(p: float, df: float) -> float:
    """스튜던트 t 분포의 분위수(역 CDF). 0<p<1, df>0. 대칭성으로 안정적으로 계산."""
    if df <= 0:
        raise ValueError("t_ppf: df는 양수여야 합니다.")
    p = min(max(p, 1e-12), 1.0 - 1e-12)
    if p == 0.5:
        return 0.0
    # |T| 의 꼬리확률 q 에서 x = df/(df+t^2) 를 역베타로 구한다.
    lower = p < 0.5
    q = 2.0 * (p if lower else 1.0 - p)  # 양측 꼬리확률
    xb = betaincinv(df / 2.0, 0.5, q)
    t = math.sqrt(df * (1.0 - xb) / xb) if xb > 0 else float("inf")
    return -t if lower else t


def f_cdf(x: float, dfn: float, dfd: float) -> float:
    """F 분포의 CDF P(F ≤ x). dfn,dfd>0, x≥0."""
    if dfn <= 0 or dfd <= 0:
        raise ValueError("f_cdf: 자유도는 양수여야 합니다.")
    if x <= 0.0:
        return 0.0
    xb = dfn * x / (dfn * x + dfd)
    return betainc(dfn / 2.0, dfd / 2.0, xb)


def f_ppf(p: float, dfn: float, dfd: float) -> float:
    """F 분포의 분위수(역 CDF). 0<p<1, dfn,dfd>0."""
    if dfn <= 0 or dfd <= 0:
        raise ValueError("f_ppf: 자유도는 양수여야 합니다.")
    p = min(max(p, 1e-12), 1.0 - 1e-12)
    # xb = betaincinv(dfn/2, dfd/2, p) = dfn*x/(dfn*x+dfd) 를 x에 대해 역산.
    xb = betaincinv(dfn / 2.0, dfd / 2.0, p)
    if xb >= 1.0:
        return float("inf")
    return (xb * dfd) / (dfn * (1.0 - xb))
