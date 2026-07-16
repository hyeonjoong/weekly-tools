"""주파수영역 — FFT 정확도, Welch PSD, 대역 파워, 절대 스케일(Parseval) 검증.

numpy/scipy 가 있으면 교차검증하고, 없으면 순수 표준 라이브러리 검사만으로도
전부 통과합니다.
"""

import cmath
import math
import random

import pytest

from hrvkit.frequency import (
    fft, frequency_domain, rfft_pow2, welch_psd, _next_pow2, _prev_pow2,
)

try:
    import numpy as _np
    HAVE_NUMPY = True
except Exception:  # pragma: no cover
    HAVE_NUMPY = False

try:
    import scipy.signal as _sig
    HAVE_SCIPY = True
except Exception:  # pragma: no cover
    HAVE_SCIPY = False


def _naive_dft(a):
    """O(n²) 기준 DFT — FFT 검증용."""
    n = len(a)
    out = []
    for k in range(n):
        s = 0j
        for t in range(n):
            s += a[t] * cmath.exp(-2j * math.pi * k * t / n)
        out.append(s)
    return out


def test_pow2_helpers():
    assert _next_pow2(1) == 1
    assert _next_pow2(5) == 8
    assert _next_pow2(1024) == 1024
    assert _prev_pow2(1) == 1
    assert _prev_pow2(1000) == 512
    assert _prev_pow2(256) == 256


def test_fft_matches_naive_dft():
    rng = random.Random(123)
    a = [complex(rng.gauss(0, 1), rng.gauss(0, 1)) for _ in range(64)]
    fast = fft(a)
    slow = _naive_dft(a)
    max_err = max(abs(f - s) for f, s in zip(fast, slow))
    assert max_err < 1e-9


def test_fft_real_signal_matches_naive():
    rng = random.Random(7)
    a = [rng.gauss(0, 1) for _ in range(128)]
    fast = fft(a)
    slow = _naive_dft(a)
    assert max(abs(f - s) for f, s in zip(fast, slow)) < 1e-9


def test_fft_requires_power_of_two():
    with pytest.raises(ValueError):
        fft([1, 2, 3])  # 길이 3


def test_rfft_pow2_zero_pads():
    x = [1.0, 2.0, 3.0, 4.0, 5.0]  # 길이 5 → 8로 zero-pad
    spec = rfft_pow2(x, 8)
    assert len(spec) == 8 // 2 + 1
    # DC = 합
    assert spec[0].real == pytest.approx(sum(x))
    assert spec[0].imag == pytest.approx(0.0)


@pytest.mark.skipif(not HAVE_NUMPY, reason="numpy 없음")
def test_rfft_matches_numpy():
    rng = random.Random(11)
    x = [rng.gauss(0, 1) for _ in range(50)]
    mine = rfft_pow2(x, 64)
    ref = _np.fft.rfft(_np.asarray(x + [0.0] * 14))
    assert _np.allclose([complex(z) for z in mine], ref, atol=1e-9)


@pytest.mark.skipif(not HAVE_SCIPY, reason="scipy 없음")
def test_welch_matches_scipy():
    rng = random.Random(2024)
    fs = 4.0
    x = [math.sin(2 * math.pi * 0.15 * (k / fs)) + 0.3 * rng.gauss(0, 1)
         for k in range(600)]
    freqs, psd, meta = welch_psd(x, fs, nperseg=128)
    f_ref, p_ref = _sig.welch(_np.asarray(x), fs=fs, window="hann",
                              nperseg=128, noverlap=64, detrend="constant",
                              scaling="density", return_onesided=True)
    assert meta["nperseg"] == 128
    assert _np.allclose(freqs, f_ref, atol=1e-9)
    assert _np.allclose(psd, p_ref, rtol=1e-6, atol=1e-9)


def test_parseval_absolute_scale():
    """단측 PSD 적분 ≈ 신호 분산(A²/2). 순수 표준 라이브러리 검사."""
    fs = 4.0
    N = 1024
    A = 5.0
    f0 = 0.20
    x = [10.0 + A * math.sin(2 * math.pi * f0 * (k / fs)) for k in range(N)]
    freqs, psd, meta = welch_psd(x, fs, nperseg=256)
    df = freqs[1] - freqs[0]
    integrated = sum(p * df for p in psd)
    # 창/구간화에 의한 누출로 정확히 A²/2=12.5는 아니지만 근접해야 함.
    assert integrated == pytest.approx(A * A / 2.0, rel=0.10)
    # 피크가 f0 부근이어야 함
    peak_f = max(zip(psd, freqs))[1]
    assert abs(peak_f - f0) < 0.02


def test_band_separation():
    """HF(0.25 Hz)에만 에너지를 넣으면 HF power ≫ LF power."""
    fs = 4.0
    # NN 시계열을 직접 만들기보다 frequency_domain 경로를 쓰기 위해
    # 평균 800ms + 0.25Hz 진동으로 tachogram 생성
    nn = []
    t = 0.0
    for _ in range(600):
        v = 800.0 + 40.0 * math.sin(2 * math.pi * 0.25 * t)
        nn.append(v)
        t += v / 1000.0
    r = frequency_domain(nn, fs=fs)
    assert r["hf_power"] > 5 * r["lf_power"]
    assert r["peak_hf"] == pytest.approx(0.25, abs=0.02)
    assert r["lf_hf_ratio"] < 0.5


def test_frequency_domain_non_power_of_two_length():
    # 박동 수가 2의 거듭제곱이 아니어도 동작해야 함
    rng = random.Random(3)
    nn = [800 + 30 * math.sin(2 * math.pi * 0.2 * i * 0.8) + rng.gauss(0, 5)
          for i in range(237)]
    r = frequency_domain(nn, fs=4.0)
    assert r["total_power"] > 0
    assert r["welch_nperseg"] & (r["welch_nperseg"] - 1) == 0  # 2의 거듭제곱


def test_frequency_domain_needs_min_beats():
    with pytest.raises(ValueError):
        frequency_domain([800, 810, 790])  # 4개 미만


def test_respiration_estimate_from_hf_peak():
    """HF(0.25 Hz) 호흡 → 호흡수 추정 ≈ 15회/분, ln_hf 는 유한."""
    fs = 4.0
    nn, t = [], 0.0
    for _ in range(600):
        v = 800.0 + 40.0 * math.sin(2 * math.pi * 0.25 * t)
        nn.append(v)
        t += v / 1000.0
    r = frequency_domain(nn, fs=fs)
    assert r["resp_rate_hz"] == pytest.approx(0.25, abs=0.02)
    assert r["resp_rate_brpm"] == pytest.approx(15.0, abs=1.5)
    assert math.isfinite(r["ln_hf"])
    assert r["ln_hf"] == pytest.approx(math.log(r["hf_power"]), rel=1e-9)
