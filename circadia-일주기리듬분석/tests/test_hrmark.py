"""심박 마커 — 강하율·nadir·기상 전 상승 손계산 대조.

손계산:
[고정 창 강하율 25%] 주간(09–21시) 심박 80, 야간(00–06시) 60:
  (80 − 60)/80 × 100 = 25.0 (정확히).
[수면구간 강하율 26.67%] 수면 중 55, 수면 밖 75:
  (75 − 55)/75 × 100 = 26.666... = 80/3.
[기상 전 상승] 수면(00:00–07:00) 중 시간당 평균 60,58,56,54,54,56,62 —
  마지막 60분 평균 62, 수면 중 최저 시간당 평균 54 → Δ = 8 > 2 → 상승.
"""

import datetime as dt

import pytest

from circadia.hrmark import hr_markers
from circadia.sleepreg import group_nights

D0 = dt.datetime(2026, 8, 3)


def test_fixed_window_dip_exactly_25pct():
    samples = []
    for d in range(2):
        base = D0 + dt.timedelta(days=d)
        for h in range(9, 21):
            samples.append((base + dt.timedelta(hours=h), 80.0))
        for h in range(0, 6):
            samples.append((base + dt.timedelta(hours=h), 60.0))
    samples.sort()
    m = hr_markers(samples, None, None)
    assert m.method.startswith("고정 시계창")
    assert m.day_mean == 80.0 and m.night_mean == 60.0
    assert m.dip_pct == 25.0
    # 수면구간이 없으면 기상 전 상승은 판단하지 않는다(추측 금지)
    assert m.prewake_rise is None
    assert any("기상 전 상승" in n for n in m.notes)


def test_sleep_based_dip_and_night_day_split():
    sleep = [(D0, D0 + dt.timedelta(hours=6))]
    samples = ([(D0 + dt.timedelta(hours=h), 55.0) for h in range(6)]
               + [(D0 + dt.timedelta(hours=h), 75.0) for h in range(6, 24)])
    m = hr_markers(samples, sleep, None)
    assert m.method.startswith("수면구간 기준")
    assert m.night_mean == 55.0 and m.day_mean == 75.0
    assert m.dip_pct == pytest.approx(80.0 / 3.0, rel=1e-12)


def test_nadir_planted_at_hour_4_reported_as_0430():
    """시간당 프로파일 최저를 4시 빈에 심으면 nadir 중앙 4.5h."""
    prof = [70.0] * 24
    prof[4] = 50.0
    samples = [(D0 + dt.timedelta(hours=h), prof[h]) for h in range(24)]
    m = hr_markers(samples, None, None)
    assert m.nadir_hour_mid == 4.5
    assert m.nadir_value == 50.0


def test_prewake_rise_detected_from_planted_profile():
    hourly = [60.0, 58.0, 56.0, 54.0, 54.0, 56.0, 62.0]   # 00~07시
    sleep = [(D0, D0 + dt.timedelta(hours=7))]
    samples = []
    for h, v in enumerate(hourly):
        for m30 in (0, 30):
            samples.append((D0 + dt.timedelta(hours=h, minutes=m30), v))
    # 수면 밖 표본도 조금
    samples += [(D0 + dt.timedelta(hours=12), 80.0)]
    nights = group_nights(sleep)
    m = hr_markers(samples, sleep, nights)
    assert m.n_nights_used == 1
    assert m.prewake_delta_bpm == pytest.approx(8.0, abs=1e-9)
    assert m.prewake_rise is True


def test_prewake_flat_profile_not_flagged():
    sleep = [(D0, D0 + dt.timedelta(hours=7))]
    samples = [(D0 + dt.timedelta(minutes=10 * i), 56.0) for i in range(42)]
    nights = group_nights(sleep)
    m = hr_markers(samples, sleep, nights)
    assert m.prewake_delta_bpm == pytest.approx(0.0, abs=1e-9)
    assert m.prewake_rise is False


def test_missing_night_samples_confessed():
    samples = [(D0 + dt.timedelta(hours=h), 80.0) for h in range(9, 21)]
    m = hr_markers(samples, None, None)
    assert m.dip_pct is None
    assert any("계산하지 못함" in n for n in m.notes)
