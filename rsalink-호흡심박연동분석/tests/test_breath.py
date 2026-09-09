import math
import random

import pytest

from rsalink.parse import RespWaveform, RespEvents, TimeInfo
from rsalink.breath import detect_breaths, rate_summary


def make_wave(freq_hz, dur_s=120.0, fs=10.0, noise=0.05, seed=1, drift=0.0):
    rnd = random.Random(seed)
    t = [i / fs for i in range(int(dur_s * fs))]
    v = [math.sin(2 * math.pi * freq_hz * x) + noise * rnd.gauss(0, 1) + drift * x for x in t]
    return RespWaveform(t=t, v=v, fs_est=fs, irregular_frac=0.0, n_gaps=0, gap_total_s=0.0,
                        dup_ts=0, time=TimeInfo("seconds", None))


@pytest.mark.parametrize("f, expect_bpm", [(0.1, 6.0), (0.25, 15.0)])
def test_rate_recovery_sine_with_noise(f, expect_bpm):
    br = detect_breaths(make_wave(f, noise=0.05, drift=0.002))
    rates = br.rates()
    # 120 s: 0.1 Hz → 12 주기 → 11 호흡 완전, 0.25 Hz → 30 주기 → 29
    assert len(rates) >= int(120 * f) - 2
    s = rate_summary(rates)
    assert abs(s["mean"] - expect_bpm) < 0.1
    assert br.artifact_frac < 0.1


def test_onset_is_trough_peak_between():
    br = detect_breaths(make_wave(0.1, noise=0.0))
    b = br.valid_breaths[0]
    # sin 골은 t = 7.5, 17.5, ...; 피크는 12.5
    assert abs((b.t_onset - 7.5) % 10.0) < 0.15 or abs((b.t_onset - 7.5) % 10.0 - 10) < 0.15
    assert b.t_onset < b.t_peak < b.t_end
    assert abs(b.period_s - 10.0) < 0.15
    assert abs((b.t_peak - b.t_onset) - 5.0) < 0.15


def test_noise_bumps_not_counted():
    # 큰 잡음(0.3) 이라도 min 간격 + MAD 돌출로 호흡 수는 크게 안 늘어야 한다
    br = detect_breaths(make_wave(0.1, noise=0.3, seed=3))
    s = rate_summary(br.rates())
    assert abs(s["mean"] - 6.0) < 0.5


def test_events_input():
    ev = RespEvents(t=[0.0, 5.0, 10.0, 15.5, 16.0, 40.0], dup_ts=0, time=TimeInfo("seconds", None))
    br = detect_breaths(ev)
    assert br.source == "events" and br.peak_assumed
    flags = [b.flag for b in br.breaths]
    assert flags == ["", "", "", "short", "long"]
    assert abs(br.breaths[0].t_peak - 2.0) < 1e-9
