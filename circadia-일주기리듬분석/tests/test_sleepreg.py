"""SRI·중간수면·사회적 시차 — 경계와 손계산 대조.

손계산:
[SRI=100] 이틀 연속 23:00–07:00 수면. 관측 구간 = [D1 23:00, D3 07:00]
  (32h). 비교쌍은 t ∈ [D1 23:00, D2 07:00) 의 480분 — 양쪽 다 수면 →
  일치율 1 → SRI = 200·1 − 100 = 100 (정확히).
[SRI=−100] A = D1 00:00–08:00 수면, B = D2 08:00–D3 00:00 수면.
  관측 구간 48h, 비교쌍 1440분: t<08:00 → (수면, 각성), t≥08:00 →
  (각성, 수면) — 전부 불일치 → SRI = −100 (정확히).
[사회적 시차 1.5h] 월~목·일요일 밤 23:00→07:00 (중간수면 03:00 =
  정오 기준 15.0h), 금·토 밤 00:30→08:30 (중간수면 04:30 = 16.5h).
  MSW = 15.0, MSF = 16.5 → SJL = 1.5 (정확히).
2026-08-03 = 월요일 (datetime.weekday 로 재확인).
"""

import datetime as dt

import pytest

from circadia.sleepreg import (Night, analyze_sleep, group_nights,
                               hours_to_clock_from_noon, sri)


def T(day, h, m=0):
    return dt.datetime(2026, 8, day, h, m)


# ---------------------------------------------------------------- SRI 경계

def test_sri_identical_two_nights_is_exactly_100():
    ivs = [(T(3, 23), T(4, 7)), (T(4, 23), T(5, 7))]
    r = sri(ivs, min_nights=2)
    assert r.sri == 100.0
    assert r.n_pairs == 480          # 도킹스트링 손계산


def test_sri_complete_reversal_is_exactly_minus_100():
    ivs = [(T(3, 0), T(3, 8)), (T(4, 8), T(5, 0))]
    r = sri(ivs, min_nights=2)
    assert r.sri == -100.0
    assert r.n_pairs == 1440


def test_sri_gate_below_min_nights():
    ivs = [(T(d, 23), T(d + 1, 7)) for d in range(3, 7)]   # 4밤
    r = sri(ivs)                                            # 기본 min 5
    assert r.insufficient and r.sri is None
    assert r.note == "데이터 부족(4일<5일)"


def test_sri_span_must_exceed_24h():
    r = sri([(T(3, 23), T(4, 7))], min_nights=1)
    assert r.insufficient and "24시간" in r.note


# ---------------------------------------------------------------- 밤 배정 규칙

def test_midnight_crossing_sleep_is_one_night_midsleep_0300():
    nights = group_nights([(T(3, 23), T(4, 7))])
    assert len(nights) == 1
    n = nights[0]
    assert n.date == dt.date(2026, 8, 3)      # 저녁 쪽 날짜
    assert n.midsleep == T(4, 3)              # 23:00~07:00 의 중점 03:00
    assert n.tst_hours == 8.0


def test_fragmented_night_same_cluster_within_2h_gap():
    """23:00–03:00 + 03:30–07:00 (갭 30분) → 한 덩어리, TST=7.5h."""
    nights = group_nights([(T(3, 23), T(4, 3)), (T(4, 3, 30), T(4, 7))])
    n = nights[0]
    assert len(nights) == 1 and not n.naps
    assert n.onset == T(3, 23) and n.wake == T(4, 7)
    assert n.tst_hours == 7.5
    assert n.midsleep == T(4, 3)              # (23:00+07:00)/2


def test_afternoon_nap_is_separated_from_main_sleep():
    """낮잠 14:00–15:10 은 중점(14:35)이 정오 이후 → 같은 날짜 밤에 배정되되
    주 수면(01:00–09:10, 다음날)과 갭 > 2h → 낮잠으로 분류."""
    nights = group_nights([(T(12, 14), T(12, 15, 10)),
                           (T(13, 1), T(13, 9, 10))])
    assert len(nights) == 1
    n = nights[0]
    assert n.date == dt.date(2026, 8, 12)
    assert n.onset == T(13, 1)                # 낮잠은 입면 통계에서 제외
    assert len(n.naps) == 1 and n.naps[0] == (T(12, 14), T(12, 15, 10))


def test_weekend_night_labeling_fri_sat():
    """2026-08-07(금)·08(토) 밤만 주말밤."""
    ivs = [(T(d, 23), T(d + 1, 7)) for d in range(3, 10)]
    nights = group_nights(ivs)
    labels = {n.date.day: n.is_weekend for n in nights}
    assert labels == {3: False, 4: False, 5: False, 6: False,
                      7: True, 8: True, 9: False}
    assert dt.date(2026, 8, 3).weekday() == 0    # 월요일 전제 재확인


# ---------------------------------------------------------------- 사회적 시차

def test_social_jetlag_hand_computed_1p5h():
    ivs = []
    for d in range(3, 10):
        if dt.date(2026, 8, d).weekday() in (4, 5):      # 금·토 밤
            ivs.append((T(d + 1, 0, 30), T(d + 1, 8, 30)))
        else:
            ivs.append((T(d, 23), T(d + 1, 7)))
    reg = analyze_sleep(sorted(ivs), min_nights=5)
    assert reg.n_work == 5 and reg.n_free == 2
    assert reg.msw_h == pytest.approx(15.0, abs=1e-12)
    assert reg.msf_h == pytest.approx(16.5, abs=1e-12)
    assert reg.sjl_hours == pytest.approx(1.5, abs=1e-12)
    assert hours_to_clock_from_noon(reg.msw_h) == "03:00"
    assert hours_to_clock_from_noon(reg.msf_h) == "04:30"


def test_social_jetlag_refused_without_both_night_types():
    ivs = [(T(d, 23), T(d + 1, 7)) for d in range(3, 7)]   # 월~목 밤만
    reg = analyze_sleep(ivs, min_nights=2)
    assert reg.sjl_hours is None
    assert "주말 0밤" in reg.sjl_note


# ---------------------------------------------------------------- 요약 통계

def test_midsleep_sd_zero_for_identical_schedule():
    ivs = [(T(d, 23), T(d + 1, 7)) for d in range(3, 8)]
    reg = analyze_sleep(ivs, min_nights=5)
    assert reg.midsleep_sd_h == 0.0
    assert reg.onset_sd_h == 0.0 and reg.wake_sd_h == 0.0
    assert reg.tst_mean_h == 8.0 and reg.tst_sd_h == 0.0
    assert hours_to_clock_from_noon(reg.midsleep_mean_h) == "03:00"


def test_hours_to_clock_from_noon_wraps():
    assert hours_to_clock_from_noon(11.0) == "23:00"
    assert hours_to_clock_from_noon(12.0) == "00:00"
    assert hours_to_clock_from_noon(15.5) == "03:30"
    assert hours_to_clock_from_noon(0.0) == "12:00"
