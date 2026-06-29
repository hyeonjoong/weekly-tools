"""분석 결과를 사람이 읽는 텍스트 리포트로 렌더링한다."""

from __future__ import annotations

from typing import List, Optional, Sequence

from .dataio import Event
from .metrics import (
    ActiveUsers,
    EventStat,
    FunnelStep,
    Retention,
    UserStat,
    active_users,
    event_breakdown,
    funnel,
    retention,
    stickiness,
    user_breakdown,
)
from .sessionize import Session, sessionize


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _fmt_dt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def build_report(
    events: List[Event],
    *,
    gap_seconds: float = 1800.0,
    retention_days: Sequence[int] = (1, 7),
    funnel_steps: Optional[Sequence[str]] = None,
    top: int = 10,
) -> str:
    sessions = sessionize(events, gap_seconds=gap_seconds)
    evs = event_breakdown(events)
    users = user_breakdown(events)
    active = active_users(events)
    ret = retention(events, retention_days)

    lines: List[str] = []
    add = lines.append

    add("=" * 64)
    add("  logflow — 사용자 이벤트 로그 분석 리포트")
    add("=" * 64)
    _overview(add, events, users, evs, sessions, gap_seconds)
    _events_section(add, evs, top)
    _active_section(add, active)
    _retention_section(add, ret)
    if funnel_steps:
        _funnel_section(add, funnel(events, funnel_steps))
    _top_users_section(add, users, top)
    add("")
    return "\n".join(lines)


def _overview(add, events, users, evs, sessions, gap_seconds):
    days = sorted({e.ts.date() for e in events})
    span = (days[-1] - days[0]).days + 1
    durations = [s.duration_seconds for s in sessions if s.event_count > 1]
    eps = [s.event_count for s in sessions]
    add("")
    add("[ 개요 ]")
    add(f"  총 이벤트       : {len(events):,}")
    add(f"  고유 사용자     : {len(users):,}")
    add(f"  고유 이벤트종류 : {len(evs):,}")
    add(f"  기간            : {days[0]} ~ {days[-1]}  (달력 {span}일, 활성 {len(days)}일)")
    add(f"  세션 수         : {len(sessions):,}  (비활동 기준 {int(gap_seconds // 60)}분)")
    add(f"  세션당 이벤트   : 평균 {_mean(eps):.1f}")
    if durations:
        add(f"  세션 길이       : 평균 {_mean(durations) / 60:.1f}분 "
            f"(단일이벤트 세션 제외 n={len(durations)})")
    else:
        add("  세션 길이       : n/a (모든 세션이 단일 이벤트)")


def _events_section(add, evs: List[EventStat], top: int):
    add("")
    add(f"[ 이벤트별 집계 ] (상위 {min(top, len(evs))})")
    add(f"  {'이벤트':<24}{'발생수':>10}{'고유사용자':>12}")
    for s in evs[:top]:
        add(f"  {s.name:<24}{s.count:>10,}{s.unique_users:>12,}")


def _active_section(add, active: List[ActiveUsers]):
    add("")
    add("[ 활성 사용자 (DAU / WAU / MAU) ]")
    add(f"  {'날짜':<12}{'DAU':>8}{'WAU':>8}{'MAU':>8}")
    for a in active:
        add(f"  {str(a.day):<12}{a.dau:>8}{a.wau:>8}{a.mau:>8}")
    st = stickiness(active)
    add(f"  점착도(평균DAU/평균MAU): {_pct(st)}")


def _retention_section(add, ret: List[Retention]):
    add("")
    add("[ 리텐션 ] (코호트=첫 활성일, 정확히 day-N 재방문)")
    for r in ret:
        add(f"  day-{r.n:<3} : {_pct(r.rate):>7}  "
            f"(retained {r.retained}/{r.eligible})")


def _funnel_section(add, steps: List[FunnelStep]):
    add("")
    add("[ 퍼널 전환 ] (시간순 진행)")
    add(f"  {'단계':<22}{'도달':>8}{'직전대비':>10}{'1단계대비':>12}")
    for s in steps:
        add(f"  {s.name:<22}{s.reached:>8}"
            f"{_pct(s.step_conversion):>10}{_pct(s.overall_conversion):>12}")


def _top_users_section(add, users: List[UserStat], top: int):
    add("")
    add(f"[ 상위 사용자 ] (이벤트 수 기준 상위 {min(top, len(users))})")
    add(f"  {'사용자':<16}{'이벤트':>8}{'활성일':>8}  {'첫활동':<18}{'마지막활동':<18}")
    for u in users[:top]:
        add(f"  {u.user:<16}{u.event_count:>8}{u.active_days:>8}  "
            f"{_fmt_dt(u.first_seen):<18}{_fmt_dt(u.last_seen):<18}")
