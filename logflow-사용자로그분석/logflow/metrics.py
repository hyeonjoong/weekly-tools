"""핵심 지표 계산: 집계 · 활성 사용자(DAU/WAU/MAU) · 리텐션 · 퍼널.

모든 함수는 입력 이벤트를 변형하지 않는다(순수 함수). 날짜 버킷은 타임스탬프의
달력 날짜(date)를 사용한다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .dataio import Event
from .stats import median, wilson_interval


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
    # 동점은 활성일수 → 첫 등장 시각 → ID 순으로 가른다. ID 를 먼저 보면 `--anonymize`
    # 로 ID 를 바꿨을 때 상위 사용자 표에 다른 사람이 올라와, 원본 리포트와 공유용
    # 리포트를 나란히 놓고 볼 수 없다.
    stats.sort(key=lambda s: (-s.event_count, -s.active_days, s.first_seen, s.user))
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
    retained: int     # 재활성 사용자 수 (exact: 정확히 day-N / rolling: day-N 이후)
    rate: Optional[float]
    ci: Optional[Tuple[float, float]] = None  # rate 의 Wilson 신뢰구간


def retention(
    events: Sequence[Event],
    days: Sequence[int] = (1, 7),
    confidence: float = 0.95,
    mode: str = "exact",
) -> List[Retention]:
    """day-N 리텐션 (코호트 = 첫 활성일).

    사용자의 첫 활성일을 C라 하고 데이터 최종일을 max_day 라 할 때:
    - mode="exact"   : '정확히' C+N 일에 다시 활성이면 retained (클래식 day-N).
    - mode="rolling" : C+N 일 '이후(포함)' 언제든 한 번이라도 활성이면 retained.
      표본이 작을 때 exact 의 요철(특정일만 세는 데서 오는)을 완화해 더 안정적이다.

    두 모드 모두 C+N 이 데이터 최종일을 넘는 코호트는 관찰 기회가 없어 eligible 에서
    제외한다. 각 비율에는 Wilson score 신뢰구간(기본 95%)을 붙인다.
    """
    if mode not in ("exact", "rolling"):
        raise ValueError(f"mode 는 'exact' 또는 'rolling' 이어야 합니다 (받은 값: {mode!r})")
    bad = [n for n in days if n < 1]
    if bad:
        raise ValueError(f"리텐션 day-N 은 1 이상이어야 합니다 (받은 값: {bad})")

    by_user_days: Dict[str, Set[date]] = defaultdict(set)
    for e in events:
        by_user_days[e.user].add(e.ts.date())
    if not by_user_days:
        return [Retention(n, 0, 0, None, None) for n in days]

    first_day = {u: min(ds) for u, ds in by_user_days.items()}
    last_day = {u: max(ds) for u, ds in by_user_days.items()}
    max_day = max(last_day.values())

    out: List[Retention] = []
    for n in days:
        eligible = retained = 0
        for u, c in first_day.items():
            # 정수 비교로 관찰 기회를 판정 — day-N 이 매우 커도 date 연산 오버플로를 피한다.
            horizon = (max_day - c).days
            if n > horizon:
                continue
            eligible += 1
            if mode == "exact":
                if (c + timedelta(days=n)) in by_user_days[u]:
                    retained += 1
            else:  # rolling: C+N 이후(포함) 활성일이 하나라도 있는가
                if (last_day[u] - c).days >= n:
                    retained += 1
        rate = (retained / eligible) if eligible else None
        ci = wilson_interval(retained, eligible, confidence) if eligible else None
        out.append(Retention(n=n, eligible=eligible, retained=retained, rate=rate, ci=ci))
    return out


# ---------------------------------------------------------------- 퍼널

@dataclass
class FunnelStep:
    name: str
    reached: int                 # 이 단계까지 도달한 사용자 수
    step_conversion: Optional[float]   # 직전 단계 대비 전환율
    overall_conversion: Optional[float]  # 1단계 대비 전환율
    step_ci: Optional[Tuple[float, float]] = None   # step_conversion 의 Wilson 구간
    median_seconds_from_prev: Optional[float] = None  # 직전 단계→이 단계 소요시간 중앙값


def funnel(
    events: Sequence[Event],
    steps: Sequence[str],
    confidence: float = 0.95,
) -> List[FunnelStep]:
    """순서가 있는 퍼널 전환 분석.

    각 사용자의 이벤트를 시간순으로 정렬한 뒤, 단계별로 '서로 다른 이벤트'를 시간순으로
    소비하며 진행한다: step_i 는 step_{i-1} 에 매칭된 이벤트보다 *뒤(인덱스)* 의 최초
    동일-이름 이벤트여야 한다. 따라서 한 이벤트가 두 단계를 동시에 만족하거나, 동일
    타임스탬프 이벤트가 순서를 거슬러 매칭되는 일이 없다. 단계 이름이 중복돼도 안전하다.

    각 단계의 '직전 대비 전환율'에는 Wilson 신뢰구간을, 그리고 직전 단계에서 이 단계까지
    걸린 시간의 중앙값(median_seconds_from_prev)을 함께 계산한다 — 어디서 얼마나
    오래 걸려 이탈하는지 파악하는 데 쓴다.
    """
    if not steps:
        raise ValueError("퍼널 단계가 비어 있습니다")

    by_user: Dict[str, List[Event]] = defaultdict(list)
    for e in events:
        by_user[e.user].append(e)

    reached = [0] * len(steps)
    # 각 전이(step i-1 -> i)에 대한 사용자별 소요 시간(초) 모음
    transition_secs: List[List[float]] = [[] for _ in steps]
    for evs in by_user.values():
        evs = sorted(evs, key=lambda e: e.ts)
        idx = -1  # 직전 단계에 매칭된 이벤트의 인덱스
        prev_ts = None
        depth = 0
        for si, step in enumerate(steps):
            nxt = _next_event_index(evs, step, idx)
            if nxt is None:
                break
            if si >= 1 and prev_ts is not None:
                transition_secs[si].append((evs[nxt].ts - prev_ts).total_seconds())
            idx = nxt
            prev_ts = evs[nxt].ts
            depth += 1
        for i in range(depth):
            reached[i] += 1

    out: List[FunnelStep] = []
    base = reached[0] if reached else 0
    for i, step in enumerate(steps):
        if i == 0:
            step_conv = 1.0 if base else None
            step_ci = None
        else:
            prev = reached[i - 1]
            step_conv = (reached[i] / prev) if prev else None
            step_ci = wilson_interval(reached[i], prev, confidence) if prev else None
        overall = (reached[i] / base) if base else None
        med = median(transition_secs[i]) if transition_secs[i] else None
        out.append(
            FunnelStep(
                name=step,
                reached=reached[i],
                step_conversion=step_conv,
                overall_conversion=overall,
                step_ci=step_ci,
                median_seconds_from_prev=med,
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


# ---------------------------------------------------------------- 활동 시간대

def activity_by_hour(events: Sequence[Event]) -> List[int]:
    """시(0..23)별 이벤트 수 (길이 24 리스트). 하루 중 언제 쓰는지 파악용."""
    hours = [0] * 24
    for e in events:
        hours[e.ts.hour] += 1
    return hours


def activity_by_weekday(events: Sequence[Event]) -> List[int]:
    """요일(월=0 .. 일=6)별 이벤트 수 (길이 7 리스트)."""
    days = [0] * 7
    for e in events:
        days[e.ts.weekday()] += 1
    return days


def peak_hour(events: Sequence[Event]) -> Optional[int]:
    """이벤트가 가장 많은 시(동률이면 이른 시각). 이벤트 없으면 None."""
    hours = activity_by_hour(events)
    if not events:
        return None
    return max(range(24), key=lambda h: (hours[h], -h))
