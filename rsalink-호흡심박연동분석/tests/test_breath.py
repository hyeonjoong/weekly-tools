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


def make_pause_wave(bpm=6.0, dur_s=300.0, fs=10.0, noise=0.10, seed=1, t_insp=1.5, tau=1.2):
    """정지형 파형: 빠른 선형 흡기(1.5 s) → 지수 호기(τ 1.2 s) → 호기 말 정지(≈0). 진폭 1.
    σ 0.10 이면 SNR ≈ 8.6 dB (신호 RMS 0.27) — 패널 조건(17.5 dB) 보다 나쁘다."""
    rnd = random.Random(seed)
    T = 60.0 / bpm
    t = [i / fs for i in range(int(dur_s * fs))]
    v = []
    for x in t:
        ph = x % T
        c = ph / t_insp if ph < t_insp else math.exp(-(ph - t_insp) / tau)
        v.append(c + noise * rnd.gauss(0, 1))
    return RespWaveform(t=t, v=v, fs_est=fs, irregular_frac=0.0, n_gaps=0, gap_total_s=0.0,
                        dup_ts=0, time=TimeInfo("seconds", None))


def test_pause_waveform_not_oversplit():
    # 라운드 1 A2: 정지 파형에서 신호 MAD 가 붕괴해 잡음 골을 호흡으로 세던 결함.
    br = detect_breaths(make_pause_wave(noise=0.10))
    s = rate_summary(br.rates())
    assert 6.0 <= s["mean"] <= 6.3, s
    assert br.artifact_frac <= 0.10
    assert br.prom_thr >= br.prom_floor > 0
    # 라운드 0 설정(k 1.0) 은 같은 파형에서 두 배 넘게 셌다 — 기본 k 가 실제로 일하는지 확인
    old = rate_summary(detect_breaths(make_pause_wave(noise=0.10), k_mad=1.0).rates())
    assert old["mean"] > 10.0


# ---------------------------------------------------------------- 라운드 2 #3·#4


@pytest.mark.parametrize("amp2", [0.14, 0.12])
def test_small_breath_condition_survives_after_large(amp2):
    # 0–5분 진폭 1.0 → 5–15분 진폭 amp2(≈7:1·8:1), 12/분. 전역 0.15·(p95−p5) 바닥(≈0.20)은 0.12 조건의
    # 골(돌출 ≈0.23)을 거의 다 기각했다(유효 16/120, HARDENING 라운드 2 #3). 60 s 창별 바닥이면 둘 다 검출.
    rnd = random.Random(7)
    fs = 10.0
    t = [i / fs for i in range(int(900 * fs))]
    v = [(1.0 if x < 300 else amp2) * math.sin(2 * math.pi * 0.2 * x) + 0.01 * rnd.gauss(0, 1) for x in t]
    br = detect_breaths(RespWaveform(t=t, v=v, fs_est=fs, irregular_frac=0.0, n_gaps=0, gap_total_s=0.0,
                                     dup_ts=0, time=TimeInfo("seconds", None)))
    for lo, hi, n_exp in ((0, 300, 60), (300, 900, 120)):
        valid = [b for b in br.breaths if lo <= b.t_onset < hi and b.valid]
        s = rate_summary([b.rate_bpm for b in valid])
        assert len(valid) >= 0.9 * n_exp, (amp2, lo, len(valid))
        assert abs(s["mean"] - 12.0) < 0.3, (amp2, lo, s)
    assert br.prom_floor_lo < 0.06 < 0.25 < br.prom_floor_hi     # 창별 바닥이 조건마다 다르다


def test_rolling_floor_equals_global_when_short():
    from rsalink.breath import rolling_range_floor, _percentile
    d = [math.sin(2 * math.pi * 0.1 * i / 10.0) for i in range(500)]     # 50 s < 60 s 창
    fl = rolling_range_floor(d, 10.0)
    g = 0.15 * (_percentile(d[::2], 0.95) - _percentile(d[::2], 0.05))   # ~4 Hz 로 솎은 전역 값
    assert len(fl) == 500 and max(fl) == pytest.approx(g, rel=0.05) and min(fl) == pytest.approx(g, rel=0.05)


def test_refine_onset_literal():
    from rsalink.breath import _refine_onset
    # 정지(잡음) 뒤 상승: 골 i0=2(−0.03), 봉우리 ip=8. tol 0.02 면 봉우리에서 내려오다 −0.01(5) 뒤 0.02 가
    # 누적 최소 + tol 을 넘어 정지 → 5(상승 발치). tol 0.1 이면 되오름이 없어 골 2 그대로.
    d = [-0.02, 0.01, -0.03, 0.0, 0.02, -0.01, 0.2, 0.6, 1.0]
    assert _refine_onset(d, 2, 8, 0.02) == 5
    assert _refine_onset(d, 2, 8, 0.1) == 2
    # 단조 하강(매끈한 파형)이면 항상 골
    assert _refine_onset([0.0, 0.2, 0.5, 1.0], 0, 3, 0.01) == 0


@pytest.mark.parametrize("seed", list(range(20)))
def test_pause_waveform_sharper_multi_seed(seed):
    # 라운드 2 #4: 더 뾰족한 정지형(흡기 1.0 s, τ 0.9 s, σ 0.10, 10 Hz) 20 seed. 라운드 1 은 흡기 시작이
    # 평탄 정지 구간 안의 잡음 최저점에 놓여(주기 4–14 s 교대) seed 15 가 6.85/분(rate 평균의 Jensen 편향).
    # 상승 발치 보정 뒤 6.0–6.3, 주기 CV ≤ 30%(전 13–26%), 유효 ≥ 90%.
    br = detect_breaths(make_pause_wave(noise=0.10, seed=seed, t_insp=1.0, tau=0.9))
    s = rate_summary(br.rates())
    assert 5.8 <= s["mean"] <= 6.6, (seed, s)
    assert s["cv"] <= 0.30, (seed, s)
    assert br.artifact_frac <= 0.10


def test_onset_refinement_leaves_sine_onsets_at_trough():
    # 잡음 σ 0.3 사인에서도 보정이 골을 옮기지 않는다(되오름 허용치 3·σ_smooth 가 잡음 봉우리를 넘긴다)
    br = detect_breaths(make_wave(0.1, noise=0.3, seed=3))
    d, fs = br.detrended, 10.0
    for b in br.valid_breaths:
        i0, ip = int(round(b.t_onset * fs)), int(round(b.t_peak * fs))
        assert d[i0] == min(d[i0:ip + 1])       # 흡기 시작 = [onset, peak] 구간의 최솟값 자리


def test_threshold_floor_applies_when_noise_free():
    # 잡음 0 → 잡음 MAD ≈ 0(평활 잔차에 남는 곡률뿐, 측정 0.005) → 바닥 0.15·(p95−p5) 가 임계
    br = detect_breaths(make_wave(0.1, noise=0.0))
    assert br.mad < 0.01
    assert br.prom_thr == pytest.approx(br.prom_floor)
    assert 0.25 < br.prom_floor < 0.35      # 사인 진폭 1: p95−p5 ≈ 1.9
    assert len(br.valid_breaths) >= 10


def test_rate_stability_flag_and_gate():
    from rsalink.breath import rate_stability
    assert rate_stability([12.0] * 20) == (False, [])
    flag, reasons = rate_stability([12.0, 6.0] * 10)        # CV ≈ 34% → 플래그 없음
    assert not flag and not reasons
    flag, reasons = rate_stability([12.0, 4.0] * 10)        # CV ≈ 52% → 플래그, 게이트 없음
    assert flag and not reasons
    flag, reasons = rate_stability([30.0, 6.0] * 10)        # CV ≈ 70% → 게이트
    assert flag and any("CV" in r for r in reasons)
    flag, reasons = rate_stability([10.0] * 20 + [40.0] * 6)   # 중앙값 10, 평균 16.9 → 차 69% → 게이트
    assert any("중앙값" in r for r in reasons)


def test_events_input():
    ev = RespEvents(t=[0.0, 5.0, 10.0, 15.5, 16.0, 40.0], dup_ts=0, time=TimeInfo("seconds", None))
    br = detect_breaths(ev)
    assert br.source == "events" and br.peak_assumed
    flags = [b.flag for b in br.breaths]
    assert flags == ["", "", "", "short", "long"]
    assert abs(br.breaths[0].t_peak - 2.0) < 1e-9
