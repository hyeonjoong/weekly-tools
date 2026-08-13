"""표준 라이브러리만으로 구현한 특수함수 — 정규화 불완전 감마/베타.

이 모듈은 numcheck 가 **원고에 적힌 p 값을 다시 계산**하기 위한 최소한의 수치
기반이다. scipy 를 런타임 의존성으로 두지 않기 위해 직접 구현했고,
``dev/verify_against_scipy.py`` 로 scipy 와 상대오차 ≤1e-9 를 확인한다
(그 스크립트는 개발용이며 배포 의존성이 아니다).

알고리즘은 Numerical Recipes 의 고전적인 형태다.

* ``gammainc_lower_reg(a, x)`` = P(a, x) — 급수(x < a+1) / 연분수(x ≥ a+1)
* ``gammainc_upper_reg(a, x)`` = Q(a, x) = 1 − P(a, x)
* ``betainc_reg(a, b, x)``     = I_x(a, b) — Lentz 연분수 + 대칭 변환

꼬리 확률의 **상대**정확도가 중요하다(p = 1e-12 를 0 으로 만들면 그 자리에서
"보고값과 다르다"는 헛된 지적이 나온다). 그래서 1 − (큰 값) 형태의 뺄셈이
일어나지 않도록 항상 작은 쪽을 직접 계산하는 분기를 탄다.
"""

from __future__ import annotations

import math

__all__ = [
    "gammainc_lower_reg",
    "gammainc_upper_reg",
    "betainc_reg",
]

_EPS = 3.0e-16
_FPMIN = 1.0e-300
# 급수/연분수 반복 상한. 큰 a 에서 _gser 는 a 의 1% 남짓한 항이 필요하다
# (a = 5e5 → 약 5,400항). 예전 값 500 은 조용히 잘려 χ²(df ≳ 13,500) 에서
# 상대오차가 1e-9 를 넘겼다 — **수렴하지 않으면 틀린 값 대신 예외를 낸다.**
_ITMAX = 200_000


class NumericError(ValueError):
    """정의역 밖의 인자이거나, 반복이 수렴하지 못했을 때.

    수렴 실패를 조용히 넘기면 '재계산값'이라는 이름의 틀린 숫자가 리포트에
    실린다. 그래서 여기서는 반드시 예외를 던지고, 호출부(pvalues)가 그 claim 을
    '계산 범위를 벗어남'으로 **건너뛴다**.
    """


# ── 불완전 감마 ──────────────────────────────────────────────────────────────


def _gser(a: float, x: float) -> float:
    """급수 전개로 P(a, x). x < a + 1 에서 빠르게 수렴한다."""
    ap = a
    total = 1.0 / a
    delta = total
    for _ in range(_ITMAX):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * _EPS:
            break
    else:
        raise NumericError(f"불완전 감마 급수가 수렴하지 않았습니다 (a={a!r}, x={x!r}).")
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a: float, x: float) -> float:
    """연분수(수정 Lentz)로 Q(a, x). x ≥ a + 1 에서 빠르게 수렴한다."""
    b = x + 1.0 - a
    c = 1.0 / _FPMIN
    d = 1.0 / b if abs(b) >= _FPMIN else 1.0 / _FPMIN
    h = d
    for i in range(1, _ITMAX + 1):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = b + an / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        step = d * c
        h *= step
        if abs(step - 1.0) < _EPS:
            break
    else:
        raise NumericError(f"불완전 감마 연분수가 수렴하지 않았습니다 (a={a!r}, x={x!r}).")
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def gammainc_lower_reg(a: float, x: float) -> float:
    """정규화 하부 불완전 감마 P(a, x) = γ(a, x) / Γ(a)."""
    if a <= 0.0:
        raise NumericError("불완전 감마의 a 는 양수여야 합니다.")
    if x < 0.0:
        raise NumericError("불완전 감마의 x 는 음수일 수 없습니다.")
    if x == 0.0:
        return 0.0
    if x < a + 1.0:
        return _gser(a, x)
    return 1.0 - _gcf(a, x)


def gammainc_upper_reg(a: float, x: float) -> float:
    """정규화 상부 불완전 감마 Q(a, x) = Γ(a, x) / Γ(a).

    χ² 상측 꼬리가 여기로 들어온다. 꼬리에서 1 − P 를 쓰면 유효숫자가 통째로
    날아가므로, x ≥ a + 1 이면 연분수를 **직접** 쓴다.
    """
    if a <= 0.0:
        raise NumericError("불완전 감마의 a 는 양수여야 합니다.")
    if x < 0.0:
        raise NumericError("불완전 감마의 x 는 음수일 수 없습니다.")
    if x == 0.0:
        return 1.0
    if x < a + 1.0:
        return 1.0 - _gser(a, x)
    return _gcf(a, x)


# ── 불완전 베타 ──────────────────────────────────────────────────────────────


def _betacf(a: float, b: float, x: float) -> float:
    """I_x(a, b) 의 연분수 부분(수정 Lentz)."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, _ITMAX + 1):
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
        step = d * c
        h *= step
        if abs(step - 1.0) < _EPS:
            break
    else:
        raise NumericError(f"불완전 베타 연분수가 수렴하지 않았습니다 (a={a!r}, b={b!r}, x={x!r}).")
    return h


def betainc_reg(a: float, b: float, x: float) -> float:
    """정규화 불완전 베타 I_x(a, b)."""
    if a <= 0.0 or b <= 0.0:
        raise NumericError("불완전 베타의 a, b 는 양수여야 합니다.")
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    # log 로 앞부분을 계산해 언더/오버플로를 피한다.
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b
