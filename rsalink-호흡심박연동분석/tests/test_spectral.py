import cmath
import math
import random

import pytest

from rsalink.spectral import (fft, welch, band_power, resample_linear, analyze_segment,
                              suggest_offset, choose_nperseg, LF_BAND, HF_BAND)


def dft(x):
    n = len(x)
    return [sum(x[k] * cmath.exp(-2j * math.pi * j * k / n) for k in range(n)) for j in range(n)]


def test_fft_matches_dft():
    rnd = random.Random(0)
    x = [rnd.gauss(0, 1) for _ in range(64)]
    a, b = fft(x), dft(x)
    assert max(abs(p - q) for p, q in zip(a, b)) < 1e-9
    with pytest.raises(ValueError):
        fft([1, 2, 3])


def test_resample_linear():
    g, v = resample_linear([0.0, 1.0, 2.0], [0.0, 10.0, 20.0], 0.0, 2.0)
    assert g == [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75]
    assert v == pytest.approx([0.0, 2.5, 5.0, 7.5, 10.0, 12.5, 15.0, 17.5])


def test_welch_sine_power_parseval():
    # 진폭 A 사인의 파워는 A²/2 — 대역 적분이 이를 (Hann 누설 포함) 5% 안에서 복원
    fs, A, f = 4.0, 40.0, 0.1
    x = [1000 + A * math.sin(2 * math.pi * f * k / fs) for k in range(2400)]
    sp = welch(x, fs, 256)
    assert sp.n_segments == 17 and abs(sp.df - 1 / 64) < 1e-12
    p = band_power(sp, 0.05, 0.15)
    assert abs(p - A * A / 2) / (A * A / 2) < 0.05


def _rr_resp(f_resp, A=40.0, dur=600.0, noise=0.0, seed=0, resp_noise=0.0, coupled=True):
    rnd = random.Random(seed)
    # RR 박동열
    rr, tb, t = [], [], 0.0
    while t < dur:
        # RR 값은 그 RR 이 끝나는 박동 시각 기준(툴 규약) — 고정점 3회
        x = 1000.0
        for _ in range(3):
            x = 1000 + (A * math.sin(2 * math.pi * f_resp * (t + x / 1000.0) + math.pi) if coupled else 0.0)
        x += noise * rnd.gauss(0, 1)
        t += x / 1000.0
        rr.append(x)
        tb.append(t)
    rt = [k / 10.0 for k in range(int(dur * 10))]
    rv = [math.sin(2 * math.pi * f_resp * s) + resp_noise * rnd.gauss(0, 1) for s in rt]
    return tb, rr, rt, rv


def test_band_shift_6bpm_vs_15bpm():
    # 6/분(0.1 Hz): RSA 파워가 고정 HF 밖 → 호흡중심/HF고정 비율이 임계(5) 초과
    tb, rr, rt, rv = _rr_resp(0.1, noise=3.0)
    b6 = analyze_segment(tb, rr, 0.0, 600.0, 0.1, rt, rv)
    assert b6.resp_over_hf > 5.0
    assert b6.lf > b6.hf_fixed * 5
    assert abs(b6.rr_peak_freq - 0.1) < 0.02
    # 15/분(0.25 Hz): 호흡중심 대역이 HF 안 → 두 값이 2배 안
    tb, rr, rt, rv = _rr_resp(0.25, noise=3.0)
    b15 = analyze_segment(tb, rr, 0.0, 600.0, 0.25, rt, rv)
    assert 0.5 <= b15.resp_over_hf <= 1.0
    assert abs(b15.rr_peak_freq - 0.25) < 0.02
    assert b15.resp_band == (0.21, 0.29)


def test_coherence_coupled_vs_independent():
    tb, rr, rt, rv = _rr_resp(0.1, noise=3.0, resp_noise=0.1)
    c = analyze_segment(tb, rr, 0.0, 600.0, 0.1, rt, rv)
    assert c.coherence_at_resp >= 0.95
    assert c.n_segments == 17 and abs(c.coherence_bias - 1 / 17) < 1e-12
    tb, rr, rt, rv = _rr_resp(0.1, noise=20.0, resp_noise=0.1, coupled=False, seed=5)
    i = analyze_segment(tb, rr, 0.0, 600.0, 0.1, rt, rv)
    assert i.coherence_at_resp <= 0.2


def test_no_resp_waveform_no_coherence():
    tb, rr, rt, rv = _rr_resp(0.1)
    b = analyze_segment(tb, rr, 0.0, 600.0, 0.1)
    assert b.coherence_at_resp is None and b.coherence_bias is None


def test_suggest_offset_recovers_shift():
    tb, rr, rt, rv = _rr_resp(0.1, noise=2.0)
    # 호흡 시계를 1.5 s 앞당겨(값을 그대로 두고 시각만 −1.5) 넣으면 제안값 ≈ +1.5
    rt2 = [s - 1.5 for s in rt]
    lag, r = suggest_offset(tb, rr, rt2, rv, 0.0, 300.0)
    assert abs(lag - 1.5) <= 0.25   # 4 Hz 격자 → 0.25 s 분해능
    assert abs(r) > 0.8


def test_choose_nperseg():
    assert choose_nperseg(2400) == 256
    assert choose_nperseg(480) == 128    # 2 분
    assert choose_nperseg(100) == 64
