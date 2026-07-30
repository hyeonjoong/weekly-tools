"""입력 검증 — 잘못된 설계 파라미터는 계산 전에 한국어로 분명히 거절한다.

표본수 계산에서 조용히 넘어간 잘못된 입력은 곧 잘못된 프로토콜이 된다.
그래서 여기서는 "관대하게 받아주기"보다 **정확한 메시지로 거절**하는 쪽을 택했다.
"""

from __future__ import annotations

import math

__all__ = ["PowerPlanError", "as_float", "positive", "probability", "in_unit_open", "as_int"]


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


def in_unit_open(name: str, value) -> float:
    """(-1, 1) 열린구간 (상관계수 등)."""
    out = as_float(name, value)
    if not (-1.0 < out < 1.0):
        raise PowerPlanError(f"{name}: -1과 1 사이여야 합니다 (받은 값: {out:g})")
    return out
