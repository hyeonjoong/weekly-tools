"""코사이너 — 기지 파라미터 합성파로 손계산 대조.

acrophase 부호·시각 변환은 cosinor의 고전적 함정이므로(기획서 실패지점 4)
기지 위상 3개(03:00·15:00·21:30)를 심어 시계 문자열까지 검증한다.

F(2,d2) 생존함수 폐형식 손계산:
    P(F(2,10) > 4.1) = (10 / (10 + 2·4.1))^(10/2) = (10/18.2)^5
    10/18.2 = 0.549450549...; ^2 = 0.301896...; ^4 = 0.091141...;
    ×0.549450 = 0.05007754848...  (독립 산술 — scipy 불필요)
"""

import math
import random

import pytest

from circadia.cosinor import CosinorFit, f2_sf, fit_cosinor, hours_to_clock


def make_wave(mesor, amp, peak_h, days=3, step_min=10, noise=None, seed=0):
    """t: 하루 중 시각(시간). 며칠치를 이어 붙인다."""
    rng = random.Random(seed)
    ts, ys = [], []
    n = days * 24 * 60 // step_min
    for i in range(n):
        t_abs = i * step_min / 60.0
        t = t_abs % 24.0
        y = mesor + amp * math.cos(2 * math.pi * (t - peak_h) / 24.0)
        if noise:
            y += rng.gauss(0, noise)
        ts.append(t)
        ys.append(y)
    return ts, ys


# ---------------------------------------------------------------- 파라미터 복원

def test_recovers_known_parameters_to_1e6():
    """MESOR 70, 진폭 10, 정점 15.4h(=15:24) — 오차 ≤ 1e-6 (완료 기준)."""
    ts, ys = make_wave(70.0, 10.0, 15.4)
    fit = fit_cosinor(ts, ys)
    assert abs(fit.mesor - 70.0) <= 1e-6
    assert abs(fit.amplitude - 10.0) <= 1e-6
    assert abs(fit.acrophase_hours - 15.4) <= 1e-6
    assert fit.acrophase_clock == "15:24"


@pytest.mark.parametrize("peak_h,clock", [
    (3.0, "03:00"),      # 기획서 지정 위상 1
    (15.0, "15:00"),     # 위상 2
    (21.5, "21:30"),     # 위상 3
])
def test_planted_acrophases_recovered_as_clock_strings(peak_h, clock):
    ts, ys = make_wave(60.0, 8.0, peak_h)
    fit = fit_cosinor(ts, ys)
    assert abs(fit.acrophase_hours - peak_h) <= 1e-6
    assert fit.acrophase_clock == clock


def test_r2_near_one_for_pure_sine_and_bathyphase():
    ts, ys = make_wave(70.0, 10.0, 16.0)
    fit = fit_cosinor(ts, ys)
    assert fit.r2 > 0.999999
    assert abs(fit.bathyphase_hours - 4.0) <= 1e-6   # 정점+12h


def test_noisy_sine_still_close():
    ts, ys = make_wave(66.0, 9.0, 16.0, days=7, noise=1.5, seed=42)
    fit = fit_cosinor(ts, ys)
    assert abs(fit.mesor - 66.0) < 0.2
    assert abs(fit.amplitude - 9.0) < 0.3
    assert abs(fit.acrophase_hours - 16.0) < 0.2
    assert fit.p_value < 1e-6


# ---------------------------------------------------------------- F 검정

def test_f2_sf_matches_hand_computation():
    # (10/18.2)^5 — 도킹스트링의 독립 산술
    assert f2_sf(4.1, 10) == pytest.approx(0.05007754848108387, rel=1e-12)
    assert f2_sf(3.0, 20) == pytest.approx((20.0 / 26.0) ** 10, rel=1e-12)
    assert f2_sf(0.0, 10) == 1.0
    assert f2_sf(-1.0, 10) == 1.0   # 방어


def test_zero_amplitude_test_nonsignificant_on_white_noise():
    """무리듬 백색잡음 → F 검정 비유의 (완료 기준). seed=1은 p≈0.58."""
    rng = random.Random(1)
    ts = [i * 0.5 for i in range(200)]
    ys = [rng.gauss(0, 1) for _ in ts]
    fit = fit_cosinor(ts, ys)
    assert fit.p_value > 0.05
    assert fit.amplitude < 0.5


def test_constant_series_reports_no_rhythm_not_crash():
    fit = fit_cosinor([i * 1.0 for i in range(48)], [70.0] * 48)
    assert fit.amplitude == 0.0
    assert fit.r2 is None and fit.p_value is None


def test_too_few_samples_returns_none():
    assert fit_cosinor([0, 1, 2], [1, 2, 3]) is None


# ---------------------------------------------------------------- 시각 표기

def test_hours_to_clock_rounding_and_wrap():
    assert hours_to_clock(15.4) == "15:24"
    assert hours_to_clock(0.0) == "00:00"
    assert hours_to_clock(23.9999) == "00:00"   # 반올림이 24:00 → 00:00
    assert hours_to_clock(24.0) == "00:00"
    assert hours_to_clock(3.5) == "03:30"
