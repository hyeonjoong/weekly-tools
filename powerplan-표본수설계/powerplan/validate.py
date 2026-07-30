"""입력 검증 — 잘못된 설계 파라미터는 계산 전에 한국어로 분명히 거절한다.

표본수 계산에서 조용히 넘어간 잘못된 입력은 곧 잘못된 프로토콜이 된다.
그래서 여기서는 "관대하게 받아주기"보다 **정확한 메시지로 거절**하는 쪽을 택했다.
"""

from __future__ import annotations

import math

__all__ = ["PowerPlanError", "as_float", "positive", "probability", "in_unit_open",
           "as_int", "alpha_value", "MIN_ALPHA"]

#: 유의수준/신뢰수준의 하한. 이보다 작으면 1 − α/2가 배정도에서 정확히 1.0으로
#: 반올림되어 z 분위수가 무한대가 되고, 그 아래로는 어떤 구현도 신뢰할 수 없다.
MIN_ALPHA = 1e-9


class PowerPlanError(ValueError):
    """사용자 입력이 잘못됐을 때 (CLI에서 종료코드 2로 처리)."""


def as_float(name: str, value) -> float:
    """유한한 실수로 변환. NaN/inf/문자열은 거절."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise PowerPlanError(f"{name}: 숫자여야 합니다 (받은 값: {value!r})") from None
    if not math.isfinite(out):
        raise PowerPlanError(f"{name}: 유한한 숫자여야 합니다 (받은 값: {value!r})")
    return out


def as_int(name: str, value, minimum: int | None = None) -> int:
    """정수로 변환. 소수점이 붙은 값은 거절(표본수는 정수여야 한다)."""
    f = as_float(name, value)
    if f != int(f):
        raise PowerPlanError(f"{name}: 정수여야 합니다 (받은 값: {value!r})")
    out = int(f)
    if minimum is not None and out < minimum:
        raise PowerPlanError(f"{name}: {minimum} 이상이어야 합니다 (받은 값: {out})")
    return out


def positive(name: str, value) -> float:
    """0보다 큰 실수."""
    out = as_float(name, value)
    if out <= 0.0:
        raise PowerPlanError(f"{name}: 0보다 커야 합니다 (받은 값: {out:g})")
    return out


def probability(name: str, value, lo: float = 0.0, hi: float = 1.0) -> float:
    """(lo, hi) 열린구간 안의 확률."""
    out = as_float(name, value)
    if not (lo < out < hi):
        raise PowerPlanError(f"{name}: {lo}보다 크고 {hi}보다 작아야 합니다 (받은 값: {out:g})")
    return out


def alpha_value(name: str, value) -> float:
    """유의수준 — (MIN_ALPHA, 0.5) 사이여야 한다.

    상한 0.5는 의미 없는 검정을 막고, 하한은 수치적으로 표현 가능한 범위를 지킨다.
    """
    out = as_float(name, value)
    if out < MIN_ALPHA:
        raise PowerPlanError(
            f"{name}: {MIN_ALPHA:g}보다 작은 유의수준은 수치적으로 계산할 수 없습니다 "
            f"(받은 값: {out:g}). 보통 0.05를 씁니다"
        )
    if out >= 0.5:
        raise PowerPlanError(
            f"{name}: 0.5 이상은 의미가 없습니다 (받은 값: {out:g}). 보통 0.05를 씁니다"
        )
    return out


def in_unit_open(name: str, value) -> float:
    """(-1, 1) 열린구간 (상관계수 등)."""
    out = as_float(name, value)
    if not (-1.0 < out < 1.0):
        raise PowerPlanError(f"{name}: -1과 1 사이여야 합니다 (받은 값: {out:g})")
    return out
