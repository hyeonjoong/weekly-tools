"""분석 결과(Analysis)를 사람이 읽는 텍스트 리포트로 렌더링한다."""

from __future__ import annotations

import unicodedata
from typing import List, Optional, Sequence

from .analyze import Analysis, analyze
from .dataio import Event
from .metrics import ActiveUsers, EventStat, FunnelStep, Retention, UserStat

_WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


# 폭 0으로 취급할 제로폭 문자(제로폭 공백/조이너/BOM 등).
_ZERO_WIDTH = {"​", "‌", "‍", "﻿", "⁠"}


def _dw(s: str) -> int:
    """터미널 표시 폭 (한글 등 전각 문자는 2칸, 결합/제로폭 문자는 0칸)."""
    w = 0
    for c in s:
        if unicodedata.combining(c) or c in _ZERO_WIDTH:
            continue  # 결합 악센트·제로폭 문자는 폭 0
        w += 2 if unicodedata.east_asian_width(c) in "WF" else 1
    return w


def _rj(s: str, width: int) -> str:
    """표시 폭 기준 오른쪽 정렬 (전각 문자 고려)."""
    return " " * max(0, width - _dw(s)) + s


def _lj(s: str, width: int) -> str:
    """표시 폭 기준 왼쪽 정렬 (전각 문자 고려)."""
    return s + " " * max(0, width - _dw(s))


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _fmt_dt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def _fmt_ci(pair, confidence: float) -> str:
    if pair is None:
        return ""
    lo, hi = pair
    return f", {int(round(confidence * 100))}%CI {lo * 100:.1f}–{hi * 100:.1f}%"


def _fmt_secs(seconds: Optional[float]) -> str:
    """초 단위를 사람이 읽기 좋은 문자열로 (분/시간 자동)."""
    if seconds is None:
        return "n/a"
    if seconds < 90:
        return f"{seconds:.0f}초"
    minutes = seconds / 60.0
    if minutes < 90:
        return f"{minutes:.1f}분"
    return f"{minutes / 60.0:.1f}시간"


def build_report(
    events: List[Event],
    *,
    gap_seconds: float = 1800.0,
    retention_days: Sequence[int] = (1, 7),
    funnel_steps: Optional[Sequence[str]] = None,
    top: int = 10,
    confidence: float = 0.95,
) -> str:
    a = analyze(
        events,
        gap_seconds=gap_seconds,
        retention_days=retention_days,
        funnel_steps=funnel_steps,
        confidence=confidence,
    )
    return render_text(a, top=top)


def render_text(a: Analysis, *, top: int = 10) -> str:
    lines: List[str] = []
    add = lines.append

    add("=" * 64)
    add("  logflow — 사용자 이벤트 로그 분석 리포트")
    add("=" * 64)
    _overview(add, a)
    _events_section(add, a.event_stats, top)
    _active_section(add, a.active, a.stickiness)
    _retention_section(add, a.retention, a.confidence, a.retention_mode)
    if a.funnel:
        _funnel_section(add, a.funnel, a.confidence)
    _activity_section(add, a)
    _top_users_section(add, a.user_stats, top)
    add("")
    return "\n".join(lines)


def _overview(add, a: Analysis):
    events = a.events
    days = sorted({e.ts.date() for e in events})
    span = (days[-1] - days[0]).days + 1
    dur = a.session_summary.get("duration_seconds")
    eps = a.session_summary.get("events_per_session")
    add("")
    add("[ 개요 ]")
    add(f"  총 이벤트       : {len(events):,}")
    add(f"  고유 사용자     : {len(a.user_stats):,}")
    add(f"  고유 이벤트종류 : {len(a.event_stats):,}")
    add(f"  기간            : {days[0]} ~ {days[-1]}  (달력 {span}일, 활성 {len(days)}일)")
    spu = len(a.sessions) / len(a.user_stats) if a.user_stats else 0.0
    add(f"  세션 수         : {len(a.sessions):,}  (비활동 기준 {int(a.gap_seconds // 60)}분)")
    add(f"  사용자당 세션   : 평균 {spu:.1f}")
    if eps:
        add(f"  세션당 이벤트   : 평균 {eps['mean']:.1f} · 중앙값 {eps['median']:.0f}")
    if dur:
        add(f"  세션 길이       : 평균 {dur['mean'] / 60:.1f}분 · 중앙값 {dur['median'] / 60:.1f}분 "
            f"(단일이벤트 세션 제외 n={dur['n']})")
    else:
        add("  세션 길이       : n/a (모든 세션이 단일 이벤트)")


def _events_section(add, evs: List[EventStat], top: int):
    add("")
    add(f"[ 이벤트별 집계 ] (상위 {min(top, len(evs))})")
    add("  " + _lj("이벤트", 24) + _rj("발생수", 10) + _rj("고유사용자", 12))
    for s in evs[:top]:
        add("  " + _lj(s.name, 24) + _rj(f"{s.count:,}", 10) + _rj(f"{s.unique_users:,}", 12))


def _active_section(add, active: List[ActiveUsers], st: Optional[float]):
    add("")
    add("[ 활성 사용자 (DAU / WAU / MAU) ]  (WAU=7일 롤링, MAU=28일 롤링)")
    add("  " + _lj("날짜", 12) + _rj("DAU", 8) + _rj("WAU", 8) + _rj("MAU", 8))
    for a in active:
        add("  " + _lj(str(a.day), 12) + _rj(str(a.dau), 8)
            + _rj(str(a.wau), 8) + _rj(str(a.mau), 8))
    add(f"  점착도(평균DAU/평균MAU): {_pct(st)}")


def _retention_section(add, ret: List[Retention], confidence: float, mode: str = "exact"):
    add("")
    desc = "정확히 day-N 재방문" if mode == "exact" else "day-N 이후(포함) 재방문 [rolling]"
    add(f"[ 리텐션 ] (코호트=첫 활성일, {desc})")
    for r in ret:
        add(f"  day-{r.n:<3} : {_pct(r.rate):>7}  "
            f"(retained {r.retained}/{r.eligible}{_fmt_ci(r.ci, confidence)})")


def _funnel_section(add, steps: List[FunnelStep], confidence: float):
    add("")
    add("[ 퍼널 전환 ] (시간순 진행)")
    add("  " + _lj("단계", 22) + _rj("도달", 8) + _rj("직전대비", 10)
        + _rj("1단계대비", 12) + _rj("소요(중앙)", 14))
    for s in steps:
        t = "-" if s.median_seconds_from_prev is None else _fmt_secs(s.median_seconds_from_prev)
        add("  " + _lj(s.name, 22) + _rj(str(s.reached), 8)
            + _rj(_pct(s.step_conversion), 10) + _rj(_pct(s.overall_conversion), 12)
            + _rj(t, 14))
    add("  * 소요(중앙): 직전 단계 도달자 중 이 단계 도달자의 소요시간 중앙값(조건부).")
    # 신뢰구간은 폭이 넓어 표 아래에 별도로 (2단계부터)
    ci_lines = [
        f"    {s.name}: 직전대비 {_pct(s.step_conversion)}{_fmt_ci(s.step_ci, confidence)}"
        for s in steps[1:]
        if s.step_ci is not None
    ]
    if ci_lines:
        add(f"  ── 전환율 {int(round(confidence * 100))}% 신뢰구간 ──")
        for line in ci_lines:
            add(line)


def _activity_section(add, a: Analysis):
    add("")
    add("[ 활동 시간대 ]")
    total_h = sum(a.hour_hist)
    if a.peak_hour is not None:
        share = a.hour_hist[a.peak_hour] / total_h if total_h else 0.0
        add(f"  피크 시간대     : {a.peak_hour:02d}시 "
            f"({a.hour_hist[a.peak_hour]:,}건, 전체의 {share * 100:.0f}%)")
    add("  요일별 이벤트   : " + _bar_row(a.weekday_hist, _WEEKDAY_KO))
    add("  시간대 분포     : " + _hour_sparkline(a.hour_hist))


def _bar_row(counts: List[int], labels: List[str]) -> str:
    mx = max(counts) if counts else 0
    parts = []
    blocks = "▁▂▃▄▅▆▇█"
    for c, lab in zip(counts, labels):
        if c == 0:
            b = "·"   # 0 은 낮은 막대(▁)와 구분되도록 점으로
        elif mx == 0:
            b = "▁"
        else:
            idx = int(round(c / mx * (len(blocks) - 1)))
            b = blocks[idx]
        parts.append(f"{lab}{b}{c}")
    return "  ".join(parts)


def _hour_sparkline(counts: List[int]) -> str:
    mx = max(counts) if counts else 0
    blocks = "▁▂▃▄▅▆▇█"
    if mx == 0:
        return "0…23  " + "▁" * 24
    out = []
    for c in counts:
        out.append(blocks[int(round(c / mx * (len(blocks) - 1)))])
    return "0시 " + "".join(out) + " 23시"


def _top_users_section(add, users: List[UserStat], top: int):
    add("")
    add(f"[ 상위 사용자 ] (이벤트 수 기준 상위 {min(top, len(users))})")
    add("  " + _lj("사용자", 16) + _rj("이벤트", 8) + _rj("활성일", 8)
        + "  " + _lj("첫활동", 18) + "마지막활동")
    for u in users[:top]:
        add("  " + _lj(u.user, 16) + _rj(str(u.event_count), 8) + _rj(str(u.active_days), 8)
            + "  " + _lj(_fmt_dt(u.first_seen), 18) + _fmt_dt(u.last_seen))
