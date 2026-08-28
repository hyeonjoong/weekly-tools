"""FFT · 2차 섹션 · 쌍선형 변환의 기본 성질."""
from __future__ import annotations

import cmath
import math

import pytest

from stimaudit import dsp


def _naive_dft_mag2(x):
    n = len(x)
    out = []
    for k in range(n // 2 + 1):
        acc = 0j
        for i, v in enumerate(x):
            acc += v * cmath.exp(-2j * math.pi * k * i / n)
        out.append(abs(acc) ** 2)
    return out


def test_next_pow2():
    assert dsp.next_pow2(1) == 1
    assert dsp.next_pow2(2) == 2
    assert dsp.next_pow2(3) == 4
    assert dsp.next_pow2(1023) == 1024
    assert dsp.next_pow2(1024) == 1024


def test_rfft_matches_naive_dft():
    x = [math.sin(i * 0.3) + 0.4 * math.cos(i * 1.1) for i in range(64)]
    fast = dsp.rfft_mag2(x)
    slow = _naive_dft_mag2(x)
    assert len(fast) == 33
    for a, b in zip(fast, slow):
        assert a == pytest.approx(b, rel=1e-9, abs=1e-9)


def test_rfft_impulse_is_flat():
    x = [0.0] * 32
    x[0] = 1.0
    mag2 = dsp.rfft_mag2(x)
    assert all(v == pytest.approx(1.0) for v in mag2)


def test_rfft_dc_signal():
    mag2 = dsp.rfft_mag2([1.0] * 16)
    assert mag2[0] == pytest.approx(256.0)
    assert all(v == pytest.approx(0.0, abs=1e-18) for v in mag2[1:])


def test_rfft_rejects_non_power_of_two():
    with pytest.raises(ValueError):
        dsp.rfft_mag2([0.0] * 30)


def test_rfft_empty():
    assert dsp.rfft_mag2([]) == []


def test_parseval():
    """에너지 보존 — Σ|x|² · N = Σ|X|² (단측 스펙트럼 보정 포함)."""
    x = [math.sin(i * 0.21) for i in range(128)]
    mag2 = dsp.rfft_mag2(x)
    total = mag2[0] + mag2[-1] + 2.0 * sum(mag2[1:-1])
    assert total == pytest.approx(len(x) * sum(v * v for v in x), rel=1e-9)


def test_hann_window():
    w = dsp.hann(8)
    assert len(w) == 8
    assert w[0] == pytest.approx(0.0)
    assert w[4] == pytest.approx(1.0)
    assert dsp.hann(1) == [1.0]
    assert dsp.hann(0) == []


@pytest.mark.parametrize("freq", [100.0, 440.0, 1000.0, 5000.0])
def test_parabolic_peak_recovers_frequency(freq):
    fs, n = 48000.0, 4096
    x = [math.sin(2 * math.pi * freq * i / fs) for i in range(n)]
    w = dsp.hann(n)
    mag2 = dsp.rfft_mag2([a * b for a, b in zip(x, w)])
    k = max(range(len(mag2)), key=lambda i: mag2[i])
    est = dsp.parabolic_peak(mag2, k) * fs / n
    assert est == pytest.approx(freq, abs=2.0)


def test_parabolic_peak_at_boundary_returns_index():
    assert dsp.parabolic_peak([5.0, 1.0, 1.0], 0) == 0.0
    assert dsp.parabolic_peak([1.0, 1.0, 5.0], 2) == 2.0


def test_parabolic_peak_flat_returns_index():
    assert dsp.parabolic_peak([1.0, 1.0, 1.0], 1) == 1.0


def test_biquad_passthrough():
    coeffs = (1.0, 0.0, 0.0, 0.0, 0.0)
    st = [0.0, 0.0]
    x = [1.0, 2.0, 3.0]
    assert dsp.biquad_block(x, coeffs, st) == x


def test_biquad_block_split_is_identical():
    """블록 경계에서 값이 튀면 스트리밍 결과가 틀립니다 — 상태 보존을 고정."""
    from stimaudit.filters import k_weighting_sos
    sos = k_weighting_sos(48000.0)
    x = [math.sin(i * 0.05) for i in range(5000)]
    st1 = dsp.new_states(sos)
    whole = dsp.sos_block(x, sos, st1)
    st2 = dsp.new_states(sos)
    parts = []
    for i in range(0, len(x), 137):
        parts.extend(dsp.sos_block(x[i:i + 137], sos, st2))
    assert len(parts) == len(whole)
    for a, b in zip(whole, parts):
        assert a == pytest.approx(b, abs=1e-12)


def test_new_states_shape():
    st = dsp.new_states([(1, 0, 0, 0, 0), (1, 0, 0, 0, 0)])
    assert st == [[0.0, 0.0], [0.0, 0.0]]
    st[0][0] = 1.0
    assert st[1][0] == 0.0        # 섹션끼리 상태를 공유하면 안 됩니다


def test_sos_freq_response_of_unity():
    assert dsp.sos_freq_response_db([(1.0, 0.0, 0.0, 0.0, 0.0)], 1000.0, 48000.0) == \
        pytest.approx(0.0)


def test_bilinear_biquad_lowpass_dc_gain():
    """1차 저역통과 1/(s+w) 는 DC 에서 1/w 배 — 쌍선형 변환 뒤에도 같아야 합니다."""
    w = 2 * math.pi * 1000.0
    sec = dsp.bilinear_biquad([0.0, 0.0, w], [0.0, 1.0, w], 48000.0)
    assert dsp.sos_freq_response_db([sec], 0.001, 48000.0) == pytest.approx(0.0, abs=1e-6)


def test_bilinear_biquad_rejects_zero_denominator():
    with pytest.raises(ValueError):
        dsp.bilinear_biquad([1.0, 0.0, 0.0], [0.0, 0.0, 0.0], 48000.0)


def test_bilinear_biquad_cutoff_is_minus_3db():
    w = 2 * math.pi * 1000.0
    sec = dsp.bilinear_biquad([0.0, 0.0, w], [0.0, 1.0, w], 192000.0)
    assert dsp.sos_freq_response_db([sec], 1000.0, 192000.0) == pytest.approx(-3.0103, abs=0.02)
