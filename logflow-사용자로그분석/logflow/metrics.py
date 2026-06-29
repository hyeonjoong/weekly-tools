"""핵심 지표 계산: 집계 · 활성 사용자(DAU/WAU/MAU) · 리텐션 · 퍼널.

모든 함수는 입력 이벤트를 변형하지 않는다(순수 함수). 날짜 버킷은 타임스탬프의
달력 날짜(date)를 사용한다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Set

from .dataio import Event


# ---------------------------------------------------------------- 집계

@dataclass
class EventStat:
    name: str
    count: int
    unique_users: int


@dataclass
class UserStat:
    user: str
    event_count: int
    active_days: int
    first_seen: "object"  # datetime
    last_seen: "object"   # datetime


def event_breakdown(events: Sequence[Event]) -> List[EventStat]:
    """이벤트 이름별 총 발생 수와 고유 사용자 수. 발생 수 내림차순 정렬."""
    counts: Dict[str, int] = defaultdict(int)
    users: Dict[str, Set[str]] = defaultdict(set)
    for e in events:
        counts[e.name] += 1
        users[e.name].add(e.user)
    stats = [EventStat(n, counts[n], len(users[n])) for n in counts]
    stats.sort(key=lambda s: (-s.count, s.name))
    return stats


def user_breakdown(events: Sequence[Event]) -> List[UserStat]:
    """사용자별 이벤트 수 · 활성 일수 · 첫/마지막 등장 시각. 이벤트 수 내림차순."""
    counts: Dict[str, int] = defaultdict(int)
    days: Dict[str, Set[date]] = defaultdict(set)
    first: Dict[str, object] = {}
    last: Dict[str, object] = {}
    for e in events:
        counts[e.user] += 1
        days[e.user].add(e.ts.date())
        if e.user not in first or e.ts < first[e.user]:
            first[e.user] = e.ts
        if e.user not in last or e.ts > last[e.user]:
            last[e.user] = e.ts
    stats = [
        UserStat(u, counts[u], len(days[u]), first[u], last[u]) for u in counts
    ]
    stats.sort(key=lambda s: (-s.event_count, s.user))
    return stats


# ---------------------------------------------------------------- 활성 사용자

def _users_by_day(events: Sequence[Event]) -> Dict[date, Set[str]]:
    by_day: Dict[date, Set[str]] = defaultdict(set)
    for e in events:
        by_day[e.ts.date()].add(e.user)
    return by_day


def _date_range(events: Sequence[Event]) -> List[date]:
    days = {e.ts.date() for e in events}
    lo, hi = min(days), max(days)
    out, d = [], lo
    while d <= hi:
        out.append(d)
        d += timedelta(days=1)
    return out


@dataclass
class ActiveUsers:
    day: date
    dau: int   # 그 날 활성 사용자
    wau: int   # 직전 7일(당일 포함) 활성 사용자
    mau: int   # 직전 28일(당일 포함) 활성 사용자


def active_users(events: Sequence[Event]) -> List[ActiveUsers]:
    """날짜별 DAU(당일), WAU(7일 롤링), MAU(28일 롤링)을 계산한다.

    WAU/MAU는 '당일을 포함한 직전 N일' 윈도우의 고유 사용자 수다.
    """
    by_day = _users_by_day(events)
    out: List[ActiveUsers] = []
    for d in _date_range(events):
        dau = len(by_day.get(d, ()))
        wau = _rolling_unique(by_day, d, 7)
        mau = _rolling_unique(by_day, d, 28)
        out.append(ActiveUsers(day=d, dau=dau, wau=wau, mau=mau))
    return out


def _rolling_unique(by_day: Dict[date, Set[str]], end: date, window: int) -> int:
    seen: Set[str] = set()
    for k in range(window):
        seen |= by_day.get(end - timedelta(days=k), set())
    return len(seen)


def stickiness(active: Sequence[ActiveUsers]) -> Optional[float]:
    """평균 DAU / 평균 MAU (사용자 점착도). 데이터가 없으면 None."""
    if not active:
        return None
    mean_dau = sum(a.dau for a in active) / len(active)
    mean_mau = sum(a.mau for a in active) / len(active)
    if mean_mau == 0:
        return None
    return mean_dau / mean_mau


# ---------------------------------------------------------------- 리텐션

@dataclass
class Retention:
    n: int            # day-N
    eligible: int     # 기회가 있었던 코호트 사용자 수
    retained: int     # day-N 에 다시 활성이었던 수
    rate: Optional[float]


def retention(events: Sequence[Event], days: Sequence[int] = (1, 7)) -> List[Retention]:
    """클래식 day-N 리텐션 (코호트 = 첫 활성일).

    사용자의 첫 활성일을 C라 할 때, '정확히' C+N 일에 다시 활성이면 retained.
    C+N 이 데이터 최종일을 넘으면 그 사용자는 해당 N에서 eligible 에서 제외한다
    (관찰 기회가 없었으므로).
    """
    bad = [n for n in days if n < 1]
    if bad:
        raise ValueError(f"리텐션 day-N 은 1 이상이어야 합니다 (받은 값: {bad})")

    by_user_days: Dict[str, Set[date]] = defaultdict(set)
    for e in events:
        by_user_days[e.user].add(e.ts.date())
    if not by_user_days:
        return [Retention(n, 0, 0, None) for n in days]

    first_day = {u: min(ds) for u, ds in by_user_days.items()}
    max_day = max(max(ds) for ds in by_user_days.values())

    out: List[Retention] = []
    for n in days:
        eligible = retained = 0
        for u, c in first_day.items():
            if c + timedelta(days=n) > max_day:
                continue
            eligible += 1
            if (c + timedelta(days=n)) in by_user_days[u]:
                retained += 1
        rate = (retained / eligible) if eligible else None
        out.append(Retention(n=n, eligible=eligible, retained=retained, rate=rate))
    return out


# ---------------------------------------------------------------- 퍼널

@dataclass
class FunnelStep:
    name: str
    reached: int                 # 이 단계까지 도달한 사용자 수
    step_conversion: Optional[float]   # 직전 단계 대비 전환율
    overall_conversion: Optional[float]  # 1단계 대비 전환율


def funnel(events: Sequence[Event], steps: Sequence[str]) -> List[FunnelStep]:
    """순서가 있는 퍼널 전환 분석.

    각 사용자의 이벤트를 시간순으로 정렬한 뒤, 단계별로 '서로 다른 이벤트'를 시간순으로
    소비하며 진행한다: step_i 는 step_{i-1} 에 매칭된 이벤트보다 *뒤(인덱스)* 의 최초
    동일-이름 이벤트여야 한다. 따라서 한 이벤트가 두 단계를 동시에 만족하거나, 동일
    타임스탬프 이벤트가 순서를 거슬러 매칭되는 일이 없다. 단계 이름이 중복돼도 안전하다.
    """
    if not steps:
        raise ValueError("퍼널 단계가 비어 있습니다")

    by_user: Dict[str, List[Event]] = defaultdict(list)
    for e in events:
        by_user[e.user].append(e)

    reached = [0] * len(steps)
    for evs in by_user.values():
        evs = sorted(evs, key=lambda e: e.ts)
        idx = -1  # 직전 단계에 매칭된 이벤트의 인덱스
        depth = 0
        for step in steps:
            idx = _next_event_index(evs, step, idx)
            if idx is None:
                break
            depth += 1
        for i in range(depth):
            reached[i] += 1

    out: List[FunnelStep] = []
    base = reached[0] if reached else 0
    for i, step in enumerate(steps):
        if i == 0:
            step_conv = 1.0 if base else None
        else:
            prev = reached[i - 1]
            step_conv = (reached[i] / prev) if prev else None
        overall = (reached[i] / base) if base else None
        out.append(
            FunnelStep(
                name=step,
                reached=reached[i],
                step_conversion=step_conv,
                overall_conversion=overall,
            )
        )
    return out


def _next_event_index(evs: List[Event], name: str, after_idx: int) -> Optional[int]:
    """after_idx 보다 큰 인덱스에서 name 과 일치하는 첫 이벤트의 인덱스 (없으면 None).

    evs 는 시간 오름차순이므로 인덱스 진행 = 시간 비역행을 보장한다.
    """
    for j in range(after_idx + 1, len(evs)):
        if evs[j].name == name:
            return j
    return None
