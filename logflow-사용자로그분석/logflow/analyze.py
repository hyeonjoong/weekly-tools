"""이벤트 리스트 → 모든 지표를 한 번에 계산한 구조화 결과(Analysis).

텍스트 리포트와 JSON 출력이 이 하나의 결과에서 렌더링되도록 하여 두 출력이
항상 일치하도록 한다(단일 진실 원천).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Sequence

from . import __version__
from .dataio import Event
from .metrics import (
    ActiveUsers,
    EventStat,
    FunnelStep,
    Retention,
    UserStat,
    activity_by_hour,
    activity_by_weekday,
    active_users,
    event_breakdown,
    funnel,
    peak_hour,
    retention,
    stickiness,
    user_breakdown,
)
from .sessionize import Session, session_summary, sessionize

_WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


@dataclass
class Analysis:
    events: List[Event]
    sessions: List[Session]
    event_stats: List[EventStat]
    user_stats: List[UserStat]
    active: List[ActiveUsers]
    stickiness: Optional[float]
    retention: List[Retention]
    funnel: Optional[List[FunnelStep]]
    session_summary: dict
    hour_hist: List[int]
    weekday_hist: List[int]
    peak_hour: Optional[int]
    gap_seconds: float
    confidence: float
    retention_days: List[int]
    funnel_steps: Optional[List[str]]
    retention_mode: str = "exact"


def analyze(
    events: List[Event],
    *,
    gap_seconds: float = 1800.0,
    retention_days: Sequence[int] = (1, 7),
    funnel_steps: Optional[Sequence[str]] = None,
    confidence: float = 0.95,
    retention_mode: str = "exact",
) -> Analysis:
    if not events:
        raise ValueError("분석할 이벤트가 없습니다 (빈 입력)")
    # 각 파생값을 한 번씩만 계산해 공유한다.
    sessions = sessionize(events, gap_seconds=gap_seconds)
    active = active_users(events)
    return Analysis(
        events=events,
        sessions=sessions,
        event_stats=event_breakdown(events),
        user_stats=user_breakdown(events),
        active=active,
        stickiness=stickiness(active),
        retention=retention(events, retention_days, confidence=confidence, mode=retention_mode),
        funnel=(funnel(events, funnel_steps, confidence=confidence) if funnel_steps else None),
        session_summary=session_summary(sessions),
        hour_hist=activity_by_hour(events),
        weekday_hist=activity_by_weekday(events),
        peak_hour=peak_hour(events),
        gap_seconds=gap_seconds,
        confidence=confidence,
        retention_days=list(retention_days),
        funnel_steps=list(funnel_steps) if funnel_steps else None,
        retention_mode=retention_mode,
    )


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def _ci(pair) -> Optional[List[float]]:
    return [pair[0], pair[1]] if pair is not None else None


def to_dict(a: Analysis) -> dict:
    """JSON 직렬화 가능한 순수 dict 로 변환 (날짜/시각은 ISO 문자열)."""
    days = sorted({e.ts.date() for e in a.events})
    span = (days[-1] - days[0]).days + 1 if days else 0
    dur = a.session_summary.get("duration_seconds")
    eps = a.session_summary.get("events_per_session")

    return {
        "meta": {
            "tool": "logflow",
            "version": __version__,
            "gap_minutes": a.gap_seconds / 60.0,
            "confidence": a.confidence,
            "retention_days": a.retention_days,
            "retention_mode": a.retention_mode,
            "funnel_steps": a.funnel_steps,
        },
        "overview": {
            "total_events": len(a.events),
            "unique_users": len(a.user_stats),
            "unique_events": len(a.event_stats),
            "first_day": _iso(days[0]) if days else None,
            "last_day": _iso(days[-1]) if days else None,
            "span_days": span,
            "active_days": len(days),
            "n_sessions": len(a.sessions),
            "session_duration_seconds": dur,
            "events_per_session": eps,
        },
        "events": [
            {"name": s.name, "count": s.count, "unique_users": s.unique_users}
            for s in a.event_stats
        ],
        "users": [
            {
                "user": u.user,
                "event_count": u.event_count,
                "active_days": u.active_days,
                "first_seen": _iso(u.first_seen),
                "last_seen": _iso(u.last_seen),
            }
            for u in a.user_stats
        ],
        "active_users": [
            {"day": _iso(x.day), "dau": x.dau, "wau": x.wau, "mau": x.mau}
            for x in a.active
        ],
        "stickiness": a.stickiness,
        "retention": [
            {
                "n": r.n,
                "eligible": r.eligible,
                "retained": r.retained,
                "rate": r.rate,
                "ci": _ci(r.ci),
            }
            for r in a.retention
        ],
        "funnel": (
            [
                {
                    "name": s.name,
                    "reached": s.reached,
                    "step_conversion": s.step_conversion,
                    "overall_conversion": s.overall_conversion,
                    "step_ci": _ci(s.step_ci),
                    "median_seconds_from_prev": s.median_seconds_from_prev,
                }
                for s in a.funnel
            ]
            if a.funnel is not None
            else None
        ),
        "activity": {
            "by_hour": a.hour_hist,
            "by_weekday": a.weekday_hist,
            "weekday_labels": _WEEKDAY_KO,
            "peak_hour": a.peak_hour,
        },
    }


def _round(x: Optional[float], nd: int = 4) -> Optional[float]:
    return None if x is None else round(x, nd)


def to_csv_tables(a: Analysis) -> Dict[str, str]:
    """분석 결과를 원고·엑셀에 붙이기 좋은 여러 CSV 표(문자열)로 반환.

    반환 키: active_users, retention, funnel(퍼널 지정 시), events, users,
    activity_by_hour, activity_by_weekday. 각 값은 완결된 CSV 텍스트.
    비율/구간은 소수(0~1)로, 신뢰구간은 lo/hi 두 열로 편다.
    """
    tables: Dict[str, str] = {}

    def _csv(header, rows) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        return buf.getvalue()

    tables["active_users"] = _csv(
        ["day", "dau", "wau", "mau"],
        [[x.day.isoformat(), x.dau, x.wau, x.mau] for x in a.active],
    )
    tables["retention"] = _csv(
        ["day_n", "mode", "eligible", "retained", "rate", "ci_low", "ci_high"],
        [
            [r.n, a.retention_mode, r.eligible, r.retained, _round(r.rate),
             _round(r.ci[0]) if r.ci else None, _round(r.ci[1]) if r.ci else None]
            for r in a.retention
        ],
    )
    if a.funnel is not None:
        tables["funnel"] = _csv(
            ["step", "name", "reached", "step_conversion", "overall_conversion",
             "step_ci_low", "step_ci_high", "median_seconds_from_prev"],
            [
                [i + 1, s.name, s.reached, _round(s.step_conversion),
                 _round(s.overall_conversion),
                 _round(s.step_ci[0]) if s.step_ci else None,
                 _round(s.step_ci[1]) if s.step_ci else None,
                 s.median_seconds_from_prev]
                for i, s in enumerate(a.funnel)
            ],
        )
    tables["events"] = _csv(
        ["event", "count", "unique_users"],
        [[s.name, s.count, s.unique_users] for s in a.event_stats],
    )
    tables["users"] = _csv(
        ["user", "event_count", "active_days", "first_seen", "last_seen"],
        [[u.user, u.event_count, u.active_days, u.first_seen.isoformat(),
          u.last_seen.isoformat()] for u in a.user_stats],
    )
    tables["activity_by_hour"] = _csv(
        ["hour", "events"], [[h, a.hour_hist[h]] for h in range(24)],
    )
    tables["activity_by_weekday"] = _csv(
        ["weekday", "label", "events"],
        [[i, _WEEKDAY_KO[i], a.weekday_hist[i]] for i in range(7)],
    )
    return tables
