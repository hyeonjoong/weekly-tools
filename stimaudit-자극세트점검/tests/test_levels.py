"""LUFS · LRA · LAeq · 트루피크 — 값이 맞는지, 그리고 못 잴 때 침묵하는지.

기준선(빌드 중 `ffmpeg -af ebur128` 과 실제 대조한 값):
  −20 dBFS RMS 1 kHz 사인 → mine −19.99 LUFS / ffmpeg −20.00  (Δ 0.007 LU)
  실물 12개 파일 전부 ≤ 0.1 LU 일치. 자세한 표는 HARDENING.md 에 있습니다.
"""
from __future__ import annotations

import math

import pytest

from stimaudit import levels
from tests.conftest import FS, fade, noise, sine, sine_rms


def test_1khz_sine_at_minus20_is_minus20_lufs(analyzed):
    """BS.1770 의 교정점 — 여기가 틀리면 나머지 전부가 거짓 정밀도입니다."""
    m = analyzed("s.wav", [sine_rms(1000.0, 5.0, -20.0, 48000)], fs=48000, bits=24)
    assert m.lufs_i == pytest.approx(-20.0, abs=0.1)


@pytest.mark.parametrize("target", [-14.0, -20.0, -23.0, -31.0])
def test_lufs_tracks_level_linearly(analyzed, target):
    m = analyzed("s.wav", [sine_rms(1000.0, 4.0, target, 48000)], fs=48000, bits=24)
    assert m.lufs_i == pytest.approx(target, abs=0.15)


def test_lufs_is_sample_rate_independent(analyzed):
    a = analyzed("a.wav", [sine_rms(1000.0, 4.0, -20.0, 44100)], fs=44100, bits=24)
    b = analyzed("b.wav", [sine_rms(1000.0, 4.0, -20.0, 48000)], fs=48000, bits=24)
    assert a.lufs_i == pytest.approx(b.lufs_i, abs=0.05)


def test_stereo_duplicate_is_3db_louder(analyzed):
    """같은 신호를 두 채널에 넣으면 채널 가중 합산으로 +3 LU 입니다."""
    x = sine_rms(1000.0, 4.0, -20.0, 48000)
    mono = analyzed("m.wav", [x], fs=48000, bits=24)
    stereo = analyzed("s.wav", [x, list(x)], fs=48000, bits=24)
    assert stereo.lufs_i - mono.lufs_i == pytest.approx(3.01, abs=0.05)


def test_gain_change_shifts_lufs_by_same_amount(analyzed):
    x = fade(noise(3.0, 0.2, 48000), 48000)
    a = analyzed("a.wav", [x], fs=48000, bits=24)
    b = analyzed("b.wav", [[v * 0.5 for v in x]], fs=48000, bits=24)
    assert a.lufs_i - b.lufs_i == pytest.approx(6.02, abs=0.05)


def test_silence_yields_no_lufs(analyzed):
    """무음에 숫자를 지어내면 안 됩니다 — None 이어야 합니다."""
    m = analyzed("q.wav", [[0.0] * 48000], fs=48000)
    assert m.lufs_i is None


def test_channel_weights_are_unity_for_mono_and_stereo():
    assert levels.channel_weights(1) == [1.0]
    assert levels.channel_weights(2) == [1.0, 1.0]
    assert levels.channel_weights(6) == [1.0] * 6      # 배치 미상 → 자백하고 1.0


def test_percentile_interpolates():
    v = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert levels.percentile(v, 0.0) == 0.0
    assert levels.percentile(v, 100.0) == 4.0
    assert levels.percentile(v, 50.0) == 2.0
    assert levels.percentile(v, 25.0) == 1.0
    assert levels.percentile([7.0], 50.0) == 7.0


def test_percentile_rejects_empty():
    with pytest.raises(ValueError):
        levels.percentile([], 50.0)


def test_lra_is_near_zero_for_steady_signal(analyzed):
    m = analyzed("s.wav", [sine_rms(500.0, 8.0, -20.0, 48000)], fs=48000, bits=24)
    assert m.lra == pytest.approx(0.0, abs=0.2)


def test_lra_grows_with_dynamics(analyzed):
    fs = 48000
    quiet = sine_rms(500.0, 5.0, -35.0, fs)
    loud = sine_rms(500.0, 5.0, -15.0, fs)
    m = analyzed("d.wav", [quiet + loud], fs=fs, bits=24)
    assert m.lra is not None and m.lra > 10.0


def test_dynamic_range_excludes_digital_silence(analyzed):
    """앞뒤에 무음이 붙어도 DR 이 수천 dB 로 튀면 안 됩니다(실제로 2290 dB 가 나왔습니다)."""
    fs = 48000
    x = [0.0] * fs + sine_rms(500.0, 3.0, -20.0, fs) + [0.0] * fs
    m = analyzed("g.wav", [x], fs=fs, bits=24)
    assert m.dynamic_range_db is not None
    assert 0.0 <= m.dynamic_range_db < 10.0


def test_laeq_attenuates_low_frequency(analyzed):
    """A-가중은 100 Hz 를 19 dB 깎습니다 — 같은 RMS 라도 LAeq 는 낮아야 합니다."""
    fs = 48000
    hi = analyzed("hi.wav", [sine_rms(1000.0, 4.0, -20.0, fs)], fs=fs, bits=24)
    lo = analyzed("lo.wav", [sine_rms(100.0, 4.0, -20.0, fs)], fs=fs, bits=24)
    assert hi.laeq_dbfs == pytest.approx(-20.0, abs=0.2)
    assert lo.laeq_dbfs == pytest.approx(-20.0 - 19.1, abs=0.5)


def test_lamax_exceeds_laeq_on_dynamic_material(analyzed):
    """`lamax >= laeq` 는 정의상 참이라 아무것도 증명하지 않습니다."""
    fs = 48000
    m = analyzed("m.wav", [fade(noise(4.0, 0.3, fs), fs)], fs=fs, bits=24)
    assert m.lamax_dbfs > m.laeq_dbfs
    steady = analyzed("s.wav", [sine_rms(1000.0, 4.0, -20.0, fs)], fs=fs, bits=24)
    assert steady.lamax_dbfs - steady.laeq_dbfs < 0.2


def test_lamax_catches_transient_that_laeq_misses(analyzed):
    """Czempik 2020 의 논거 — 평균은 조용해도 순간 최대치는 클 수 있습니다."""
    fs = 48000
    x = [0.0001] * (fs * 8)
    burst = sine_rms(1000.0, 0.1, -6.0, fs)      # LAmax 창(100 ms)을 꽉 채우는 길이
    x[fs:fs + len(burst)] = burst
    m = analyzed("b.wav", [x], fs=fs, bits=24)
    # 8초 중 0.1초만 −6 dBFS → LAeq ≈ −25 dBFS, LAmax ≈ −6 dBFS
    assert m.lamax_dbfs == pytest.approx(-6.0, abs=0.3)
    assert m.lamax_dbfs - m.laeq_dbfs > 15.0


def test_interpolated_peak_of_a_lone_impulse_is_the_sample_itself():
    """단일 임펄스의 재구성 최댓값은 표본값 그 자체입니다 (창의 DC 이득 = 1).

    `>= 0.8` 만 보면 보간 루프를 통째로 지워도 통과합니다.
    """
    win = [0.0] * (2 * levels._TAP_HALF)
    win[levels._TAP_HALF] = 0.8
    assert levels.interpolated_peak(win) == pytest.approx(0.8, abs=1e-9)


def test_interpolator_taps_have_unit_dc_gain():
    """계수 합이 1 이 아니면 위상마다 이득이 달라져 트루피크를 과소평가합니다."""
    for taps in levels._PHASES:
        assert sum(taps) == pytest.approx(1.0, abs=1e-12)


def test_interpolated_peak_finds_intersample_overshoot():
    """표본 사이에 최고점이 놓인 사인 — 트루피크가 표본 피크보다 커야 합니다."""
    fs, f = 48000.0, 12000.0
    half = levels._TAP_HALF
    win = [math.sin(2 * math.pi * f * (i + 0.5) / fs) for i in range(2 * half)]
    sample_peak = max(abs(v) for v in win)
    assert levels.interpolated_peak(win) > sample_peak


def test_interpolated_peak_handles_short_window():
    assert levels.interpolated_peak([0.3, -0.7]) == pytest.approx(0.7)


def test_true_peak_at_least_sample_peak_on_real_signal(analyzed):
    m = analyzed("t.wav", [sine(7000.0, 1.0, 0.9, 48000)], fs=48000, bits=24)
    assert m.true_peak_dbfs >= m.sample_peak_dbfs - 1e-9


def test_dbfs_of_none_and_zero():
    assert levels.dbfs(None) is None
    assert levels.dbfs(0.0) is None
    assert levels.dbfs(1.0) == pytest.approx(0.0)
    assert levels.dbfs(0.5) == pytest.approx(-6.0206, abs=1e-3)


def test_gating_drops_silent_blocks(analyzed):
    """절대 게이트(−70 LUFS) — 뒤에 붙은 10초 무음이 통합 라우드니스를 끌어내리지 않습니다.

    완전히 같아지지는 않습니다: 소리와 무음의 경계에 걸친 400 ms 블록 3개는
    일부만 조용하므로 게이트를 통과하면서 평균을 조금 낮춥니다. 이것은 BS.1770
    의 정의된 동작이고, `ffmpeg -af ebur128` 도 같은 값(−20.0 → −20.2)을 냅니다.
    """
    fs = 48000
    tone = sine_rms(1000.0, 3.0, -20.0, fs)
    m_short = analyzed("s.wav", [tone], fs=fs, bits=24)
    m_long = analyzed("l.wav", [tone + [0.0] * (fs * 10)], fs=fs, bits=24)
    assert m_short.lufs_i == pytest.approx(-20.0, abs=0.1)
    assert m_long.lufs_i == pytest.approx(-20.2, abs=0.1)   # ffmpeg 대조값
