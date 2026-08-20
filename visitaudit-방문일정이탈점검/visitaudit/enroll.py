"""등록 진행 — 월별 등록 수와 단순 선형 외삽.

통계 모형이 아니다. '이 속도면 언제쯤'의 단순 나눗셈이며, 리포트에 그렇게
명시한다. 등록일이 없으면 계산하지 않고 자백한다.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .tables import Subject


@dataclass
class Enrollment:
    monthly: List[Tuple[str, int]] = field(default_factory=list)  # ("2026-03", 5)
    rate_months: List[str] = field(default_factory=list)          # 평균에 쓴 달
    rate: Optional[float] = None                                  # 명/월
    n_enrolled: int = 0        # as-of 기준 실제 등록 인원
    n_total_rows: int = 0      # 무작위배정 행 전체(미래 등록일 포함)
    n_missing_dates: int = 0
    n_future_dates: int = 0        # 등록일 > as-of — 월별 집계 제외, 별도 자백
    target_n: Optional[int] = None
    remaining: Optional[int] = None
    projected_month: Optional[str] = None
    skipped: Optional[str] = None


def _ym(d: dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _add_months(year: int, month: int, k: int) -> Tuple[int, int]:
    total = year * 12 + (month - 1) + k
    return total // 12, total % 12 + 1


def build_enrollment(subjects: Optional[List[Subject]], target_n: Optional[int],
                     as_of: dt.date) -> Enrollment:
    """월별 등록 수와 목표 도달 예상 시점(단순 선형 외삽, 신뢰구간 아님).

    as-of 가 속한 부분 월은 평균에서 빼고, 최근 **최대 3개** 완결월만 쓴다.
    완결월이 하나도 없으면 속도를 내지 않는다.
    """
    e = Enrollment(target_n=target_n)
    if subjects is None:
        e.skipped = "피험자.csv 없음"
        return e
    randomized = []
    seen = set()
    for s in subjects:
        if s.randomized and s.sid not in seen:
            seen.add(s.sid)
            randomized.append(s)
    if not randomized:
        e.skipped = "무작위배정된 피험자가 없음"
        return e
    # 등록일 > as-of 는 이 기준시점의 집계가 아니다 — 월별 합 + 미래 + 미기재 = 전체
    dated = [s.enroll for s in randomized if s.enroll is not None and s.enroll <= as_of]
    e.n_future_dates = sum(1 for s in randomized
                           if s.enroll is not None and s.enroll > as_of)
    e.n_total_rows = len(randomized)
    # 목표까지 남은 인원은 **이 기준시점에 실제로 등록된 수**로 센다. 전체 행 수로
    # 세면 과거 기준일로 돌렸을 때 "아직 등록 안 된 사람"까지 등록된 것으로 쳐서
    # 같은 페이지의 CONSORT·등록곡선과 어긋난다.
    e.n_enrolled = len(randomized) - e.n_future_dates
    e.n_missing_dates = len(randomized) - len(dated) - e.n_future_dates
    if not dated:
        extra = f" (as-of 이후 등록일 {e.n_future_dates}건)" if e.n_future_dates else ""
        e.skipped = f"as-of 이전 등록일이 있는 피험자가 없음{extra}"
        return e

    first = min(dated)
    # 첫 등록 달부터 as-of 달까지 빠짐없이 나열 (등록 0 인 달도 0 으로 보인다)
    months: List[str] = []
    y, m = first.year, first.month
    while (y, m) <= (as_of.year, as_of.month):
        months.append(f"{y:04d}-{m:02d}")
        y, m = _add_months(y, m, 1)
    counts = {ym: 0 for ym in months}
    for d in dated:
        counts[_ym(d)] = counts.get(_ym(d), 0) + 1
    e.monthly = [(ym, counts[ym]) for ym in months]

    # 최근 3개 '완결된' 달(= as-of 달 제외)의 평균. 완결된 달이 없으면 계산 불가.
    asof_ym = _ym(as_of)
    complete = [(ym, n) for ym, n in e.monthly if ym != asof_ym]
    recent = complete[-3:]
    if recent:
        e.rate_months = [ym for ym, _ in recent]
        e.rate = sum(n for _, n in recent) / len(recent)

    if target_n is not None:
        e.remaining = max(0, target_n - e.n_enrolled)
        if e.remaining == 0:
            e.projected_month = asof_ym
        elif e.rate and e.rate > 0:
            months_needed = math.ceil(e.remaining / e.rate)
            yy, mm = _add_months(as_of.year, as_of.month, months_needed)
            e.projected_month = f"{yy:04d}-{mm:02d}"
    return e
