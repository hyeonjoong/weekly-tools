"""48시간 더블플롯 텍스트 액토그램.

관례(액티그래피 문헌의 double-plot): 한 줄 = 하루 D의 0–24시 + 이어서
D+1일의 0–24시(같은 내용이 다음 줄 왼쪽 절반에 다시 나온다). 자정을 넘는
수면·지연된 리듬이 줄 안에서 끊기지 않고 보인다.

문자 규칙(30분 슬롯 1문자, 한 줄 96문자):
    Z  수면(슬롯의 50% 이상이 수면구간)
    #  높은 활동(전체 양수 슬롯의 상위 1/3)
    +  중간 활동
    -  낮은 활동(양수)
    .  활동 0(착용 중, 움직임 없음)
    (공백) 데이터 없음
활동원은 걸음(합). 걸음이 없으면 심박(평균, 사분위 대신 같은 3분위)을
쓰고 헤더에 표기한다. 최대 42일 — 넘으면 마지막 42일만 그리고 자백.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Sequence, Tuple

MAX_DAYS = 42
SLOT_MIN = 30
SLOTS_PER_DAY = 24 * 60 // SLOT_MIN          # 48
SLOTS_PER_LINE = SLOTS_PER_DAY * 2           # 96


def _slot_key(stamp: dt.datetime) -> Tuple[dt.date, int]:
    return stamp.date(), (stamp.hour * 60 + stamp.minute) // SLOT_MIN


def _sleep_fraction(slot_start: dt.datetime,
                    intervals: Sequence[Tuple[dt.datetime, dt.datetime]]) -> float:
    slot_end = slot_start + dt.timedelta(minutes=SLOT_MIN)
    covered = 0.0
    for s, e in intervals:
        lo, hi = max(s, slot_start), min(e, slot_end)
        if hi > lo:
            covered += (hi - lo).total_seconds()
    return covered / (SLOT_MIN * 60.0)


def render_actogram(activity: Optional[Sequence[Tuple[dt.datetime, float]]],
                    sleep_intervals: Optional[Sequence[Tuple[dt.datetime, dt.datetime]]],
                    activity_kind: str = "걸음") -> Optional[str]:
    """activity: (datetime, 값) — 걸음이면 슬롯 합, 심박이면 슬롯 평균.
    둘 다 없으면 None."""
    if not activity and not sleep_intervals:
        return None

    # 표시할 날짜 범위
    dates: List[dt.date] = []
    stamps: List[dt.datetime] = []
    if activity:
        stamps.extend([t for t, _ in activity])
    if sleep_intervals:
        stamps.extend([s for s, _ in sleep_intervals])
        stamps.extend([e for _, e in sleep_intervals])
    d0, d1 = min(stamps).date(), max(stamps).date()
    all_days = (d1 - d0).days + 1
    truncated = 0
    if all_days > MAX_DAYS:
        truncated = all_days - MAX_DAYS
        d0 = d1 - dt.timedelta(days=MAX_DAYS - 1)
        all_days = MAX_DAYS
    dates = [d0 + dt.timedelta(days=i) for i in range(all_days)]

    # 슬롯 값 집계
    slot_vals: Dict[Tuple[dt.date, int], List[float]] = {}
    if activity:
        for t, v in activity:
            slot_vals.setdefault(_slot_key(t), []).append(v)
    agg_sum = activity_kind == "걸음"
    slots: Dict[Tuple[dt.date, int], float] = {
        k: (sum(vs) if agg_sum else sum(vs) / len(vs))
        for k, vs in slot_vals.items()}

    positives = sorted(v for v in slots.values() if v > 0)
    def _tertile(v: float) -> str:
        if not positives:
            return "-"
        i1 = positives[len(positives) // 3]
        i2 = positives[2 * len(positives) // 3]
        return "#" if v >= i2 else ("+" if v >= i1 else "-")

    def _char(day: dt.date, slot: int) -> str:
        slot_start = dt.datetime.combine(day, dt.time()) + dt.timedelta(
            minutes=slot * SLOT_MIN)
        if sleep_intervals and _sleep_fraction(slot_start, sleep_intervals) >= 0.5:
            return "Z"
        key = (day, slot)
        if key not in slots:
            return " "
        v = slots[key]
        return "." if v <= 0 else _tertile(v)

    # 헤더/눈금
    lines: List[str] = []
    lines.append(f"48시간 더블플롯 액토그램 — 활동원: {activity_kind}"
                 + (f" (전체 {all_days + truncated}일 중 마지막 {MAX_DAYS}일만 표시)"
                    if truncated else ""))
    lines.append("범례: Z 수면  # 높은 활동  + 중간  - 낮음  . 활동 0  (공백) 데이터 없음")
    tick = ""
    label = ""
    for h in range(0, 49, 6):
        col = h * (60 // SLOT_MIN)             # 시간당 2슬롯
        pad = col - len(tick)
        tick += " " * pad + "|"
        label += " " * (col - len(label)) + f"{h % 24:02d}"
    lines.append(" " * 6 + label)
    lines.append(" " * 6 + tick)

    for day in dates:
        nxt = day + dt.timedelta(days=1)
        row = "".join(_char(day, s) for s in range(SLOTS_PER_DAY))
        row += "".join(_char(nxt, s) for s in range(SLOTS_PER_DAY)) \
            if nxt <= d1 else " " * SLOTS_PER_DAY
        lines.append(f"{day.month:02d}-{day.day:02d} {row}")
    return "\n".join(lines)
