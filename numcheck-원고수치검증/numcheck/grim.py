"""GRIM / GRIMMER — "그 N 에서 그 평균이 산술적으로 존재할 수 있는가".

ISI 는 7문항 0–4점 정수 합이다. N = 23 명의 평균은 반드시 (정수)/23 꼴이어야
한다. `14.37` 이라고 적혀 있으면 14.37 × 23 = 330.51 — 정수가 아니다. 어떤
반올림 관례로도 이 평균은 나올 수 없다. 오탈자든 이전 버전의 잔재든, 사람은
절대 못 잡고 기계는 한 줄로 잡는다.

GRIMMER 는 같은 논리를 표준편차로 확장한다. 개인 점수가 정수이면 제곱합도
정수여야 하고, 게다가 x² ≡ x (mod 2) 이므로 **제곱합의 홀짝은 합계의 홀짝과
같아야 한다.** 이 두 제약을 통과하는 제곱합이 하나도 없으면 그 (평균, SD, N)
조합은 존재할 수 없다.

두 함수 모두 **판별력이 없을 때(구간 안에 후보가 널려 있을 때)는 조용히
'일치'로 돌려준다.** 판별력 없는 검사를 통과라고 부르지 않도록, 호출부가
:func:`grim_has_power` 로 먼저 물어볼 수 있게 해 두었다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .rounding import Reported

__all__ = ["GrimResult", "grim_has_power", "grim_check", "grimmer_check"]

_FUZZ = 1e-9
# 정수 후보를 실제로 나열할 때의 상한. 이보다 많으면 판별력이 없다는 뜻이다.
_MAX_CANDIDATES = 64


@dataclass
class GrimResult:
    consistent: bool
    has_power: bool
    candidates: List[float]        # 구간에 들어오는 '가능한 평균' (최대 몇 개만)
    nearest: Optional[float]       # 보고된 평균에 가장 가까운 가능한 평균
    numerator: Optional[float]     # nearest 를 만드는 합계(=m·unit)
    reason: str = ""


def grim_has_power(mean: Reported, n: int, unit: float = 1.0, k: float = 1.0) -> bool:
    """이 (자릿수, N) 조합에서 GRIM 이 무언가를 가려낼 수 있는가.

    가능한 평균들의 간격은 ``unit / n``. 반올림 허용 구간의 폭은 ``2·k·ulp``.
    간격이 구간보다 좁으면 어떤 평균이든 항상 후보가 있어 검사가 무의미하다.
    """
    if n <= 0:
        return False
    return (unit / n) > (2.0 * k * mean.ulp + _FUZZ)


def grim_check(
    mean: Reported,
    n: int,
    unit: float = 1.0,
    lo: float = float("-inf"),
    hi: float = float("inf"),
    k: float = 1.0,
) -> GrimResult:
    """평균이 N 명에서 도달 가능한 값인지.

    Parameters
    ----------
    mean : 원고에 적힌 평균(반올림 구간을 안다)
    n : 표본 수
    unit : 개인 점수의 최소 증분 (정수합 척도면 1)
    lo, hi : 척도의 최소/최대 (범위 밖 후보를 배제)
    k : 반올림 허용 배수 (1 이면 반올림/버림/올림 모두 허용)
    """
    if n <= 0:
        return GrimResult(True, False, [], None, None, "N 이 0 이하")
    if unit <= 0:
        return GrimResult(True, False, [], None, None, "점수 증분 미상")
    ilo, ihi = mean.interval(k)
    # 척도 범위와 교집합
    ilo = max(ilo, lo - _FUZZ)
    ihi = min(ihi, hi + _FUZZ)
    if ilo > ihi:
        return GrimResult(
            False, True, [], None, None,
            f"보고된 평균이 척도 범위({lo:g}–{hi:g}) 밖",
        )
    # 가능한 평균 = m·unit/n  (m 은 정수)
    m_lo = math.ceil(ilo * n / unit - _FUZZ)
    m_hi = math.floor(ihi * n / unit + _FUZZ)
    power = grim_has_power(mean, n, unit, k)
    if m_hi < m_lo:
        # 후보가 하나도 없다 = GRIM 위반
        m_near = round(mean.value * n / unit)
        nearest = m_near * unit / n
        return GrimResult(False, power, [], nearest, m_near * unit, "")
    count = m_hi - m_lo + 1
    cands = [
        (m_lo + i) * unit / n for i in range(min(count, _MAX_CANDIDATES))
    ]
    m_near = min(range(m_lo, m_lo + min(count, _MAX_CANDIDATES)),
                 key=lambda m: abs(m * unit / n - mean.value))
    return GrimResult(True, power, cands, m_near * unit / n, m_near * unit, "")


def _integers_with_parity(lo: float, hi: float, parity: int) -> bool:
    """[lo, hi] 안에 ``parity`` 홀짝을 가진 정수가 있는가."""
    a = math.ceil(lo - _FUZZ)
    b = math.floor(hi + _FUZZ)
    if b < a:
        return False
    # a 이상에서 parity 를 맞춘 첫 정수
    first = a if (a % 2 + 2) % 2 == parity else a + 1
    return first <= b


def grimmer_check(
    mean: Reported,
    sd: Reported,
    n: int,
    lo: float = float("-inf"),
    hi: float = float("inf"),
    k: float = 1.0,
) -> Tuple[bool, str]:
    """(평균, SD, N) 조합이 정수 점수로 만들어질 수 있는가.

    정수 합 척도(``unit = 1``)에만 쓴다. 표본 SD(n−1)와 모집단 SD(n) 중
    **어느 쪽 관례로든** 성립하면 통과시킨다(관례를 추측해서 지적하지 않는다).

    Returns
    -------
    (consistent, reason) — ``consistent`` 가 False 일 때만 ``reason`` 이 채워진다.
    """
    if n < 2:
        return True, ""
    if sd.value < 0:
        return False, "SD 가 음수"
    grim = grim_check(mean, n, 1.0, lo, hi, k)
    if not grim.consistent:
        return True, ""  # GRIM 이 이미 잡았다. 중복 지적하지 않는다.
    # 다른 모든 곳과 같은 `interval()` 을 쓴다. 여기만 원시 k·ulp 를 쓰면
    # 상대 fuzz 가 빠져 경계에서 판정이 1 ulp 엇갈린다.
    sd_lo, sd_hi = sd.interval(k)
    sd_lo = max(0.0, sd_lo)
    var_lo, var_hi = sd_lo * sd_lo, sd_hi * sd_hi

    # GRIM 후보 합계들(보통 1개). 너무 많으면 판별력이 없다.
    ilo, ihi = mean.interval(k)
    ilo, ihi = max(ilo, lo - _FUZZ), min(ihi, hi + _FUZZ)
    m_lo = math.ceil(ilo * n - _FUZZ)
    m_hi = math.floor(ihi * n + _FUZZ)
    if m_hi < m_lo or (m_hi - m_lo) > _MAX_CANDIDATES:
        return True, ""

    for total in range(m_lo, m_hi + 1):
        base = total * total / n  # = n·mean²
        for denom in (n - 1, n):  # 표본 SD / 모집단 SD 두 관례 모두 허용
            ss_lo = var_lo * denom
            ss_hi = var_hi * denom
            # 제곱합 T = SS + n·mean²  이고 T 는 정수, T ≡ total (mod 2)
            if _integers_with_parity(ss_lo + base, ss_hi + base, (total % 2 + 2) % 2):
                return True, ""
    return False, (
        f"정수 점수 합계 {m_lo}–{m_hi} 중 어느 것으로도 이 SD 를 만들 수 없음"
        " (제곱합이 정수·홀짝 조건을 만족하지 못함)"
    )
