"""IS/IV/RA/L5/M10 — 극단 패턴 손계산 대조.

손계산 (Van Someren 1999 공식, tests가 유일한 진실):

[IS=1, IV=4 동시 검증] 매일 동일한 24h 패턴 [10,30,10,30,...] × 7일:
  - 전체 평균 x̄ = 20, 편차 ±10, Σ(x−x̄)² = 168·100 = 16800.
  - 시간대별 평균 프로파일 = 그 패턴 그대로 → Σ_h(x̄_h−x̄)² = 24·100 = 2400.
  - IS = (168·2400)/(24·16800) = 403200/403200 = 1.0 (정확히).
  - 연속 차분 167개, 전부 ±20 → Σdiff² = 167·400 = 66800.
  - IV = (168·66800)/(167·16800) = 168·400/16800 = 4.0 (정확히 — 교대
    패턴의 이론 최대 근방).

[L5/M10] 프로파일: h∈1..5 → 5, h∈10..19 → 100, 나머지 → 20.
  - L5 = 5.0 (시작 01시; 이웃 창 [0..4]·[2..6]은 (20+4·5)/5=8 > 5).
  - L5 중앙 = 1 + 2.5 = 3.5 → "03:30".
  - M10 = 100.0 (시작 10시), 중앙 = 15.0 → "15:00".
  - RA = (100−5)/(100+5) = 95/105 = 0.904761904...

[wrap] h∈{22,23,0,1,2} → 2, 나머지 → 50: L5 창은 22시 시작(자정 넘김),
  중앙 = (22+2.5) mod 24 = 0.5 → "00:30".
"""

import datetime as dt

import pytest

from circadia.cosinor import hours_to_clock
from circadia.nonparam import (Coverage, coverage, hourly_bin, is_iv, l5m10,
                               mean_profile)

D0 = dt.datetime(2026, 8, 3)


def samples_from_daily_pattern(pattern24, days=7, skip=None):
    """하루 24개 값 → (datetime, 값) 시간당 1표본. skip=(day,hour) 집합."""
    out = []
    for d in range(days):
        for h in range(24):
            if skip and (d, h) in skip:
                continue
            out.append((D0 + dt.timedelta(days=d, hours=h), float(pattern24[h])))
    return out


# ---------------------------------------------------------------- IS / IV

def test_perfectly_regular_alternating_gives_IS_exactly_1_and_IV_exactly_4():
    pattern = [10, 30] * 12
    binned = hourly_bin(samples_from_daily_pattern(pattern), "mean")
    res = is_iv(binned)
    assert res.is_ == 1.0          # 정확히 — 도킹스트링 손계산
    assert res.iv == 4.0           # 정확히
    assert res.n_days == 7 and not res.insufficient


def test_white_noise_IS_near_zero():
    """잡음 IS 기대값 ≈ 1/일수 ≈ 0.14 (7일). seed=3 → 0.107."""
    import random
    rng = random.Random(3)
    samples = [(D0 + dt.timedelta(days=d, hours=h), rng.gauss(50, 10))
               for d in range(7) for h in range(24)]
    res = is_iv(hourly_bin(samples, "mean"))
    assert 0.0 < res.is_ < 0.3


def test_min_days_gate_refuses_short_data():
    pattern = [10, 30] * 12
    binned = hourly_bin(samples_from_daily_pattern(pattern, days=4), "mean")
    res = is_iv(binned)
    assert res.insufficient
    assert res.is_ is None and res.iv is None
    assert res.note == "데이터 부족(4일<5일)"


def test_incomplete_day_is_dropped_not_interpolated():
    pattern = [10, 30] * 12
    samples = samples_from_daily_pattern(pattern, days=7, skip={(3, 13)})
    binned = hourly_bin(samples, "mean")
    assert len(binned.valid_days) == 6
    assert binned.dropped_days == [(dt.date(2026, 8, 6), 23)]
    res = is_iv(binned)
    assert res.n_days == 6
    assert "비연속" in res.note      # 3일 + 3일 run — IV 차분은 run 안에서만


def test_constant_series_IS_IV_undefined_not_fake():
    binned = hourly_bin(samples_from_daily_pattern([50] * 24), "mean")
    res = is_iv(binned)
    assert res.is_ is None and res.iv is None
    assert "상수" in res.note


def test_steps_agg_is_sum_hr_agg_is_mean():
    samples = [(D0, 10.0), (D0 + dt.timedelta(minutes=30), 30.0)]
    assert hourly_bin(samples, "sum").days[D0.date()][0] == 40.0
    assert hourly_bin(samples, "mean").days[D0.date()][0] == 20.0


# ---------------------------------------------------------------- L5 / M10 / RA

def profile_pattern():
    p = [20.0] * 24
    for h in range(1, 6):
        p[h] = 5.0
    for h in range(10, 20):
        p[h] = 100.0
    return p


def test_l5_m10_exact_values_and_clock_midpoints():
    binned = hourly_bin(samples_from_daily_pattern(profile_pattern(), days=3), "mean")
    r = l5m10(binned)
    assert r.l5 == 5.0 and r.l5_onset_hour == 1
    assert r.l5_mid_hours == 3.5
    assert hours_to_clock(r.l5_mid_hours) == "03:30"
    assert r.m10 == 100.0 and r.m10_onset_hour == 10
    assert r.m10_mid_hours == 15.0
    assert hours_to_clock(r.m10_mid_hours) == "15:00"
    assert r.ra == pytest.approx(95.0 / 105.0, rel=1e-12)


def test_l5_window_wraps_midnight():
    p = [50.0] * 24
    for h in (22, 23, 0, 1, 2):
        p[h] = 2.0
    binned = hourly_bin(samples_from_daily_pattern(p, days=3), "mean")
    r = l5m10(binned)
    assert r.l5 == 2.0 and r.l5_onset_hour == 22
    assert r.l5_mid_hours == 0.5
    assert hours_to_clock(r.l5_mid_hours) == "00:30"
    assert r.m10 == 50.0 and r.m10_onset_hour == 3   # 동률 → 이른 시작


def test_l5m10_none_when_no_valid_day():
    samples = [(D0 + dt.timedelta(hours=h), 1.0) for h in range(12)]  # 반나절뿐
    assert l5m10(hourly_bin(samples, "mean")) is None


def test_mean_profile_averages_across_days():
    day1 = samples_from_daily_pattern([10] * 24, days=1)
    day2 = [(t + dt.timedelta(days=1), 30.0) for t, _ in day1]
    prof = mean_profile(hourly_bin(day1 + day2, "mean"))
    assert prof == [20.0] * 24


# ---------------------------------------------------------------- 커버리지

def test_coverage_wear_rate_and_gap_list():
    """0~9시·14~23시 시간당 1표본 → 24빈 중 20빈 = 83.33%, 갭 1건(4h)."""
    stamps = [D0 + dt.timedelta(hours=h) for h in list(range(10)) + list(range(14, 24))]
    cov = coverage([(t, 1.0) for t in stamps])
    assert cov.n_hour_bins == 24 and cov.n_covered == 20
    assert cov.wear_rate == pytest.approx(20.0 / 24.0, rel=1e-12)
    assert len(cov.gaps) == 1
    a, b = cov.gaps[0]
    assert a.hour == 9 and b.hour == 14
