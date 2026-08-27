"""심박 일주기 마커 — 야간 강하율·최저점(nadir) 시각·기상 전 상승.

주의: 여기 쓰는 '야간 강하(dipping)' 관례는 24시간 활동혈압(ABPM) 문헌에서
온 것(주야간 평균 10–20% 강하를 정상 범주로 봄)을 심박에 준용한 참고
지표입니다 — 심박 고유의 확립된 진단 기준이 아니며, 리포트에도 그렇게
표기합니다.

문서화된 규칙(결정론 — 테스트로 고정):
- 수면구간이 있으면: 야간 = 수면구간 내 심박 표본, 주간 = 수면구간 밖 표본.
- 수면구간이 없으면: 고정 시계창 — 야간 00:00–06:00, 주간 09:00–21:00
  (경계 미포함 아님: 시(hour) 기준 [0,6), [9,21)).
- 강하율(%) = (주간 평균 − 야간 평균) / 주간 평균 × 100.
- nadir = 평균 24h 시간당 프로파일이 최저인 빈의 중앙 시각(h+0.5).
  동률이면 이른 시각(결정론).
- 기상 전 상승: 밤마다 '주 수면 마지막 60분 평균 심박 − 그 수면 중 시간당
  평균의 최저값'을 Δ로 정의, 밤별 Δ의 중앙값이 +2 bpm 초과면 '상승 관찰'.
  수면구간이 없으면 판단하지 않는다(고정 창으로 기상시각을 추측하지 않음).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .sleepreg import Night

RISE_THRESHOLD_BPM = 2.0


@dataclass
class HRMarkers:
    day_mean: Optional[float]
    night_mean: Optional[float]
    dip_pct: Optional[float]
    method: str                  # "수면구간 기준" | "고정 시계창(00–06/09–21)"
    nadir_hour_mid: Optional[float]    # 예: 4.5 → 04:30
    nadir_value: Optional[float]
    prewake_delta_bpm: Optional[float]  # 밤별 Δ의 중앙값
    prewake_rise: Optional[bool]
    n_nights_used: int = 0
    notes: List[str] = field(default_factory=list)


def _median(xs: List[float]) -> float:
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _in_any(stamp: dt.datetime,
            intervals: Sequence[Tuple[dt.datetime, dt.datetime]]) -> bool:
    return any(s <= stamp < e for s, e in intervals)


def hr_markers(hr_samples: Sequence[Tuple[dt.datetime, float]],
               sleep_intervals: Optional[Sequence[Tuple[dt.datetime, dt.datetime]]],
               nights: Optional[List[Night]] = None) -> HRMarkers:
    notes: List[str] = []

    # --- 주/야 평균과 강하율 ---------------------------------------------
    if sleep_intervals:
        method = "수면구간 기준(야간=수면 중, 주간=수면 밖)"
        night_vals = [v for t, v in hr_samples if _in_any(t, sleep_intervals)]
        day_vals = [v for t, v in hr_samples if not _in_any(t, sleep_intervals)]
    else:
        method = "고정 시계창(야간 00–06시, 주간 09–21시)"
        night_vals = [v for t, v in hr_samples if 0 <= t.hour < 6]
        day_vals = [v for t, v in hr_samples if 9 <= t.hour < 21]
    day_mean = sum(day_vals) / len(day_vals) if day_vals else None
    night_mean = sum(night_vals) / len(night_vals) if night_vals else None
    dip = None
    if day_mean and night_mean is not None and day_mean > 0:
        dip = (day_mean - night_mean) / day_mean * 100.0
    if day_mean is None or night_mean is None:
        notes.append("주간 또는 야간 심박 표본이 없어 강하율을 계산하지 못함")

    # --- nadir: 평균 24h 프로파일 최저 빈 --------------------------------
    buckets: Dict[int, List[float]] = {}
    for t, v in hr_samples:
        buckets.setdefault(t.hour, []).append(v)
    nadir_mid: Optional[float] = None
    nadir_val: Optional[float] = None
    if buckets:
        prof = {h: sum(vs) / len(vs) for h, vs in buckets.items()}
        h_min = min(sorted(prof), key=lambda h: (prof[h], h))
        nadir_mid, nadir_val = h_min + 0.5, prof[h_min]
        if len(prof) < 24:
            notes.append(f"24개 시간 빈 중 {len(prof)}개만 데이터 있음 — "
                         "nadir는 관측된 빈 안에서의 최저")

    # --- 기상 전 상승 -----------------------------------------------------
    prewake_delta: Optional[float] = None
    prewake_rise: Optional[bool] = None
    n_used = 0
    if nights:
        deltas: List[float] = []
        for n in nights:
            in_sleep = [(t, v) for t, v in hr_samples
                        if _in_any(t, n.main)]
            if len(in_sleep) < 12:
                continue
            last_hour = [v for t, v in in_sleep
                         if (n.wake - t).total_seconds() <= 3600]
            if not last_hour:
                continue
            hourly: Dict[int, List[float]] = {}
            for t, v in in_sleep:
                key = int((t - n.onset).total_seconds() // 3600)
                hourly.setdefault(key, []).append(v)
            min_hourly = min(sum(vs) / len(vs) for vs in hourly.values())
            deltas.append(sum(last_hour) / len(last_hour) - min_hourly)
        if deltas:
            n_used = len(deltas)
            prewake_delta = _median(deltas)
            prewake_rise = prewake_delta > RISE_THRESHOLD_BPM
        else:
            notes.append("수면 중 심박 표본이 부족해 기상 전 상승을 판단하지 못함")
    elif sleep_intervals is None:
        notes.append("수면구간이 없어 기상 전 상승은 판단하지 않음(기상시각 추측 금지)")

    return HRMarkers(day_mean, night_mean, dip, method, nadir_mid, nadir_val,
                     prewake_delta, prewake_rise, n_used, notes)
