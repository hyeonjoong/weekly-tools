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
from .adherence import Adherence, adherence as compute_adherence
from .dataio import Event
from .groups import GroupComparison, compare_groups
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
    groups: Optional[GroupComparison] = None
    adherence: Optional[Adherence] = None


def analyze(
    events: List[Event],
    *,
    gap_seconds: float = 1800.0,
    retention_days: Sequence[int] = (1, 7),
    funnel_steps: Optional[Sequence[str]] = None,
    confidence: float = 0.95,
    retention_mode: str = "exact",
    group_col: Optional[str] = None,
    churn_days: int = 7,
    reference_group: Optional[str] = None,
    adherence_min_days: Optional[int] = None,
    adherence_period: int = 7,
    adherence_target: float = 0.8,
    adherence_weeks: Optional[int] = None,
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
        groups=(
            compare_groups(
                events,
                group_col=group_col,
                gap_seconds=gap_seconds,
                retention_days=retention_days,
                funnel_steps=funnel_steps,
                confidence=confidence,
                retention_mode=retention_mode,
                churn_days=churn_days,
                reference=reference_group,
                adherence_min_days=adherence_min_days,
                adherence_period=adherence_period,
                adherence_target=adherence_target,
                adherence_weeks=adherence_weeks,
            )
            if group_col
            else None
        ),
        adherence=(
            compute_adherence(
                events,
                min_days=adherence_min_days,
                period_days=adherence_period,
                target=adherence_target,
                confidence=confidence,
                max_weeks=adherence_weeks,
            )
            if adherence_min_days is not None
            else None
        ),
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
        "adherence": _adherence_to_dict(a.adherence),
        "groups": _groups_to_dict(a.groups),
    }


def _adherence_to_dict(ad: Optional[Adherence]) -> Optional[dict]:
    """준수도 결과를 JSON 직렬화 가능한 dict 로."""
    if ad is None:
        return None
    return {
        "min_days": ad.min_days,
        "period_days": ad.period_days,
        "target": ad.target,
        "window_weeks": ad.window_weeks,
        "observed_weeks": ad.observed_weeks,
        "required_weeks": ad.required_weeks,
        "eligible_weeks_range": (
            list(ad.eligible_weeks_range) if ad.eligible_weeks_range else None
        ),
        "n_users": ad.n_users,
        "n_adherent_users": ad.n_adherent_users,
        "adherent_rate": (ad.n_adherent_users / ad.n_users) if ad.n_users else None,
        "adherent_ci": _ci(ad.adherent_ci),
        "median_user_rate": ad.median_user_rate,
        "median_streak_weeks": ad.median_streak,
        "n_no_full_week": ad.n_no_full_week,
        "n_incomplete": ad.n_incomplete,
        "notes": ad.notes,
        "weeks": [
            {
                "week": w.week,
                "eligible": w.eligible,
                "adherent": w.adherent,
                "rate": w.rate,
                "ci": _ci(w.ci),
                "median_active_days": w.median_active_days,
            }
            for w in ad.weeks
        ],
        "users": [
            {
                "user": u.user,
                "eligible_weeks": u.eligible_weeks,
                "adherent_weeks": u.adherent_weeks,
                "rate": u.rate,
                "longest_streak_weeks": u.longest_streak,
                "active_days_in_window": u.active_days_in_window,
            }
            for u in ad.users
        ],
    }


def _groups_to_dict(g: Optional[GroupComparison]) -> Optional[dict]:
    """군 비교 결과를 JSON 직렬화 가능한 dict 로."""
    if g is None:
        return None
    return {
        "group_col": g.group_col,
        "groups": g.groups,
        "reference": g.reference,
        "compare_a": g.compare_a,
        "compare_b": g.compare_b,
        "n_tests": g.n_tests,
        "ungrouped_users": g.ungrouped_users,
        "conflicting_users": g.conflicting_users,
        "notes": g.notes,
        "arms": [
            {
                "group": s.group,
                "n_users": s.n_users,
                "n_events": s.n_events,
                "n_sessions": s.n_sessions,
                "median_events_per_user": s.median_events_per_user,
                "median_sessions_per_user": s.median_sessions_per_user,
                "median_minutes_per_user": s.median_minutes_per_user,
                "median_active_days": s.median_active_days,
                "retention": {
                    str(n): {"retained": r, "eligible": e}
                    for n, (r, e) in sorted(s.retention.items())
                },
                "funnel_completion": (
                    {"completed": s.funnel_completion[0], "entered": s.funnel_completion[1]}
                    if s.funnel_completion is not None
                    else None
                ),
                "adherence": (
                    {
                        "adherent_users": s.adherence[0],
                        "n_users": s.adherence[1],
                        "median_user_rate": s.median_adherence_rate,
                    }
                    if s.adherence is not None
                    else None
                ),
            }
            for s in g.arms
        ],
        "proportion_tests": [
            {
                "label": t.label,
                "group_a": t.group_a,
                "group_b": t.group_b,
                "a": {"successes": t.successes_a, "n": t.n_a, "rate": t.diff.p1},
                "b": {"successes": t.successes_b, "n": t.n_b, "rate": t.diff.p2},
                "diff": t.diff.diff,
                "diff_ci": [t.diff.ci[0], t.diff.ci[1]],
                "p_fisher": t.p_value,
                "p_holm": t.p_adjusted,
            }
            for t in g.proportions
        ],
        "distribution_tests": [
            {
                "label": t.label,
                "unit": t.unit,
                "group_a": t.group_a,
                "group_b": t.group_b,
                "n_a": t.result.n1,
                "n_b": t.result.n2,
                "median_a": t.result.median1,
                "median_b": t.result.median2,
                "u": t.result.u,
                "z": t.result.z,
                "rank_biserial": t.result.rank_biserial,
                "p_mann_whitney": t.result.p,
                "p_holm": t.p_adjusted,
            }
            for t in g.distributions
        ],
        "survival": (
            {
                "churn_days": g.survival.churn_days,
                "n_churned": g.survival.n_churned,
                "curves": {
                    name: {
                        "n": c.n,
                        "n_events": c.n_events,
                        "median_survival_days": c.median_survival,
                        "points": [
                            {
                                "day": p.time,
                                "n_risk": p.n_risk,
                                "n_event": p.n_event,
                                "survival": p.survival,
                                "ci": _ci(p.ci),
                            }
                            for p in c.points
                        ],
                    }
                    for name, c in g.survival.curves.items()
                },
                "logrank": (
                    {
                        "chi2": g.survival.logrank.chi2,
                        "p": g.survival.logrank.p,
                        "p_holm": g.survival.p_adjusted,
                        "observed1": g.survival.logrank.observed1,
                        "expected1": g.survival.logrank.expected1,
                        "observed2": g.survival.logrank.observed2,
                        "expected2": g.survival.logrank.expected2,
                    }
                    if g.survival.logrank is not None
                    else None
                ),
            }
            if g.survival is not None
            else None
        ),
    }


def _round(x: Optional[float], nd: int = 4) -> Optional[float]:
    return None if x is None else round(x, nd)


# 엑셀/스프레드시트가 수식으로 해석하는 선두 문자. 사용자 ID·이벤트 이름·군 라벨은
# 입력에서 그대로 오므로, `=cmd|...` 같은 값이 표에 실려 열람자의 엑셀에서 실행되지
# 않도록 앞에 작은따옴표를 붙인다 (CSV injection 방어; 값 자체는 보존).
_CSV_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r", "\n")


def _safe_cell(value):
    """CSV 셀 하나를 스프레드시트 수식으로 해석되지 않게 다듬는다 (문자열만)."""
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_LEAD):
        return "'" + value
    return value


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
        w.writerows([_safe_cell(c) for c in row] for row in rows)
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
    if a.adherence is not None:
        ad = a.adherence
        tables["adherence_weekly"] = _csv(
            ["week", "eligible", "adherent", "rate", "ci_low", "ci_high",
             "median_active_days", "min_days", "period_days"],
            [[w.week, w.eligible, w.adherent, _round(w.rate),
              _round(w.ci[0]) if w.ci else None, _round(w.ci[1]) if w.ci else None,
              _round(w.median_active_days, 2), ad.min_days, ad.period_days]
             for w in ad.weeks],
        )
        tables["adherence_users"] = _csv(
            ["user", "eligible_weeks", "adherent_weeks", "adherence_rate",
             "longest_streak_weeks", "active_days_in_window"],
            [[u.user, u.eligible_weeks, u.adherent_weeks, _round(u.rate),
              u.longest_streak, u.active_days_in_window] for u in ad.users],
        )
    if a.groups is not None:
        tables.update(_group_tables(a.groups, _csv))
    return tables


def _group_tables(g: GroupComparison, _csv) -> Dict[str, str]:
    """군 비교 표 — 기술통계 · 검정 결과 · KM 곡선점."""
    out: Dict[str, str] = {}
    out["group_summary"] = _csv(
        ["group", "n_users", "n_events", "n_sessions", "median_events_per_user",
         "median_sessions_per_user", "median_minutes_per_user", "median_active_days",
         "adherent_users", "adherence_denominator", "median_adherence_pct"],
        [[s.group, s.n_users, s.n_events, s.n_sessions,
          _round(s.median_events_per_user, 2), _round(s.median_sessions_per_user, 2),
          _round(s.median_minutes_per_user, 2), _round(s.median_active_days, 2),
          s.adherence[0] if s.adherence else None,
          s.adherence[1] if s.adherence else None,
          # group_tests.csv 의 '사용자당 준수 주 비율(%)' 과 같은 척도(0~100)로 맞춘다 —
          # 두 표에 1.0 과 100.0 이 나란히 놓이면 원고에 옮겨 적을 때 틀리기 쉽다.
          None if s.median_adherence_rate is None else _round(s.median_adherence_rate * 100, 2)]
         for s in g.arms],
    )
    rows = []
    for t in g.proportions:
        rows.append([
            "proportion", t.label, t.group_a, t.group_b,
            f"{t.successes_a}/{t.n_a}", f"{t.successes_b}/{t.n_b}",
            _round(t.diff.p1), _round(t.diff.p2), _round(t.diff.diff),
            _round(t.diff.ci[0]), _round(t.diff.ci[1]),
            "Fisher exact", _round(t.p_value, 6), _round(t.p_adjusted, 6), "",
        ])
    for t in g.distributions:
        rows.append([
            "distribution", f"{t.label}({t.unit})", t.group_a, t.group_b,
            t.result.n1, t.result.n2,
            _round(t.result.median1, 3), _round(t.result.median2, 3), "", "", "",
            "Mann-Whitney U", _round(t.result.p, 6), _round(t.p_adjusted, 6),
            _round(t.result.rank_biserial, 4),
        ])
    if g.survival is not None and g.survival.logrank is not None:
        lr = g.survival.logrank
        rows.append([
            "survival", f"이탈까지의 시간(churn≥{g.survival.churn_days}일)",
            g.compare_a or "", g.compare_b or "",
            lr.observed1, lr.observed2, "", "", "", "", "",
            "log-rank", _round(lr.p, 6), _round(g.survival.p_adjusted, 6),
            _round(lr.chi2, 4),
        ])
    out["group_tests"] = _csv(
        ["kind", "metric", "group_a", "group_b", "a", "b", "rate_a", "rate_b",
         "diff", "diff_ci_low", "diff_ci_high", "test", "p_value", "p_holm",
         "effect_size"],
        rows,
    )
    if g.survival is not None:
        km_rows = []
        for name, curve in g.survival.curves.items():
            for p in curve.points:
                km_rows.append([
                    name, p.time, p.n_risk, p.n_event, _round(p.survival),
                    _round(p.ci[0]) if p.ci else None,
                    _round(p.ci[1]) if p.ci else None,
                ])
        out["group_survival_km"] = _csv(
            ["group", "day", "n_risk", "n_event", "survival", "ci_low", "ci_high"],
            km_rows,
        )
    return out
