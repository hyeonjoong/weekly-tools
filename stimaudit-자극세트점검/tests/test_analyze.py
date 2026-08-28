"""파일 위생 지표 — 클리핑·DC·무음·상승시간·죽은 파일·스펙트럼·포락선."""
from __future__ import annotations

import math

import pytest

from stimaudit import analyze
from tests.conftest import FS, fade, noise, sine, sine_rms


def _with_clipping(x, start, length, fs=FS):
    y = list(x)
    for i in range(start, start + length):
        y[i] = 1.0 if y[i] >= 0 else -1.0
    return y


def test_clean_signal_has_no_clipping(analyzed):
    m = analyzed("c.wav", [fade(sine(300.0, 0.5, 0.5))])
    assert m.clip_run_count == 0
    assert m.clip_sample_count == 0
    assert m.clip_runs == []


def test_clipping_run_detected_with_position(analyzed):
    x = _with_clipping(fade(sine(300.0, 0.5, 0.5)), 10000, 20)
    m = analyzed("c.wav", [x])
    assert m.clip_run_count == 1
    assert m.clip_sample_count == 20
    assert m.clip_runs[0].start_s == pytest.approx(10000 / FS, abs=0.001)
    assert m.clip_runs[0].length_samples == 20
    assert m.clip_runs[0].channel == 0


def test_two_short_clipped_samples_are_not_a_run(analyzed):
    """연속 3샘플 미만은 구간으로 세지 않습니다 — 규칙을 좁게 잡은 부분입니다."""
    x = _with_clipping(fade(sine(300.0, 0.5, 0.5)), 10000, 2)
    assert analyzed("c.wav", [x]).clip_run_count == 0


def test_exactly_three_clipped_samples_is_a_run(analyzed):
    x = _with_clipping(fade(sine(300.0, 0.5, 0.5)), 10000, 3)
    assert analyzed("c.wav", [x]).clip_run_count == 1


def test_clipping_run_spanning_block_boundary(analyzed):
    """블록(64 k 프레임) 경계를 넘는 클리핑도 한 구간으로 세야 합니다."""
    x = _with_clipping(fade(sine(300.0, 2.0, 0.5)), 65536 - 5, 10)
    m = analyzed("c.wav", [x])
    assert m.clip_run_count == 1
    assert m.clip_sample_count == 10


def test_clipping_reported_per_channel(analyzed):
    left = _with_clipping(fade(sine(300.0, 0.5, 0.5)), 5000, 5)
    right = fade(sine(310.0, 0.5, 0.5))
    m = analyzed("s.wav", [left, right])
    assert m.clip_run_count == 1
    assert m.clip_runs[0].channel == 0


def test_dc_offset_measured(analyzed):
    m = analyzed("dc.wav", [[v + 0.05 for v in sine(300.0, 0.5, 0.3)]], bits=24)
    assert m.dc_linear[0] == pytest.approx(0.05, abs=0.002)
    assert m.dc_dbfs[0] == pytest.approx(-26.0, abs=0.5)


def test_no_dc_on_symmetric_signal(analyzed):
    m = analyzed("s.wav", [sine(441.0, 1.0, 0.3)], bits=24)
    assert abs(m.dc_linear[0]) < 1e-4


def test_leading_and_trailing_silence(analyzed):
    fs = FS
    x = [0.0] * int(fs * 0.3) + fade(sine(300.0, 0.5, 0.4)) + [0.0] * int(fs * 0.2)
    m = analyzed("s.wav", [x], bits=24)
    assert m.lead_silence_ms == pytest.approx(300.0, abs=20.0)
    assert m.tail_silence_ms == pytest.approx(200.0, abs=20.0)


def test_no_silence_when_sound_starts_immediately(analyzed):
    m = analyzed("s.wav", [sine(300.0, 0.5, 0.4)], bits=24)
    assert m.lead_silence_ms == 0.0
    assert m.tail_silence_ms == 0.0


def test_fast_onset_gives_short_rise_time(analyzed):
    """1 ms 안에 튀어 오르는 시작 = 클릭."""
    m = analyzed("f.wav", [sine(300.0, 0.5, 0.5)], bits=24)
    assert m.onset_rise_ms is not None
    assert m.onset_rise_ms < 2.0


def test_gradual_onset_gives_long_rise_time(analyzed):
    m = analyzed("g.wav", [fade(sine(300.0, 1.0, 0.5), ms=300.0)], bits=24)
    assert m.onset_rise_ms == pytest.approx(150.0, abs=40.0)


def test_offset_fall_time_measured(analyzed):
    m = analyzed("g.wav", [fade(sine(300.0, 1.0, 0.5), ms=300.0)], bits=24)
    assert m.offset_fall_ms == pytest.approx(150.0, abs=40.0)


def test_all_silent_file_is_dead(analyzed):
    m = analyzed("z.wav", [[0.0] * FS])
    assert m.dead_reason is not None
    assert "무음" in m.dead_reason


def test_all_dc_file_is_dead(analyzed):
    m = analyzed("d.wav", [[0.3] * FS], bits=24)
    assert m.dead_reason is not None
    assert "DC" in m.dead_reason


def test_normal_file_is_not_dead(analyzed):
    assert analyzed("n.wav", [fade(sine(300.0, 0.5, 0.4))]).dead_reason is None


def test_lr_rms_difference(analyzed):
    left = sine(300.0, 0.5, 0.4)
    right = [v * 10 ** (-2.0 / 20.0) for v in left]
    m = analyzed("s.wav", [left, right], bits=24)
    assert m.lr_rms_diff_db == pytest.approx(2.0, abs=0.05)


def test_mono_has_no_lr_difference(analyzed):
    assert analyzed("m.wav", [sine(300.0, 0.5, 0.4)]).lr_rms_diff_db is None


@pytest.mark.parametrize("freq", [120.0, 440.0, 1000.0])
def test_spectral_peak_finds_carrier(analyzed, freq):
    m = analyzed("t.wav", [fade(sine(freq, 1.5, 0.4, 48000), 48000, ms=50.0)], fs=48000, bits=24)
    assert m.spectral_peak_hz[0] == pytest.approx(freq, abs=2.0)
    assert m.spectral_peak_prominence_db[0] > 20.0


def test_spectral_peak_of_noise_has_low_prominence(analyzed):
    """잡음에는 '반송음'이 없습니다 — 두드러짐이 낮아야 주장 대조가 거절합니다."""
    m = analyzed("n.wav", [fade(noise(2.0, 0.3, 48000), 48000)], fs=48000, bits=24)
    assert m.spectral_peak_prominence_db[0] < 20.0


def test_dc_offset_is_not_mistaken_for_a_carrier(analyzed):
    m = analyzed("d.wav", [[v + 0.3 for v in sine(500.0, 1.5, 0.3, 48000)]], fs=48000, bits=24)
    assert m.spectral_peak_hz[0] == pytest.approx(500.0, abs=3.0)


def test_binaural_channels_have_separate_peaks(analyzed):
    m = analyzed("b.wav", [fade(sine(360.0, 1.5, 0.3, 48000), 48000, ms=50.0),
                           fade(sine(400.0, 1.5, 0.3, 48000), 48000, ms=50.0)],
                 fs=48000, bits=24)
    assert m.spectral_peak_hz[0] == pytest.approx(360.0, abs=2.0)
    assert m.spectral_peak_hz[1] == pytest.approx(400.0, abs=2.0)


@pytest.mark.parametrize("rate", [0.8, 1.2, 4.0])
def test_envelope_modulation_rate(analyzed, rate):
    fs = 48000
    n = int(fs * 8)
    carrier = [0.4 * math.sin(2 * math.pi * 400 * i / fs) for i in range(n)]
    x = [v * (0.5 + 0.5 * math.sin(2 * math.pi * rate * i / fs)) for i, v in enumerate(carrier)]
    m = analyzed("am.wav", [x], fs=fs, bits=24)
    assert m.env_mod_hz == pytest.approx(rate, rel=0.06)
    assert m.env_mod_ratio > 0.1


def test_steady_signal_reports_no_modulation(analyzed):
    """변조가 없는 톤은 변조율을 내놓지 않습니다 (깊이 문턱 아래)."""
    m = analyzed("s.wav", [sine(400.0, 4.0, 0.4, 48000)], fs=48000, bits=24)
    assert m.env_mod_depth is not None and m.env_mod_depth < analyze.MIN_ENVELOPE_DEPTH
    assert m.env_mod_hz is None


def test_spectral_centroid_rises_with_frequency(analyzed):
    lo = analyzed("lo.wav", [fade(sine(200.0, 1.5, 0.4, 48000), 48000, ms=50.0)], fs=48000, bits=24)
    hi = analyzed("hi.wav", [fade(sine(4000.0, 1.5, 0.4, 48000), 48000, ms=50.0)], fs=48000, bits=24)
    assert hi.spectral_centroid_hz > lo.spectral_centroid_hz


def test_metrics_are_block_size_independent(mk):
    """스트리밍 블록 크기를 바꿔도 결과가 같아야 합니다."""
    from stimaudit import wavread
    path = mk("b.wav", [fade(sine(300.0, 1.0, 0.5)), fade(sine(305.0, 1.0, 0.4))], bits=24)
    info = wavread.probe(path)
    a = analyze.analyze_file(info, block_frames=65536)
    b = analyze.analyze_file(info, block_frames=1000)
    assert a.lufs_i == pytest.approx(b.lufs_i, abs=1e-9)
    assert a.laeq_dbfs == pytest.approx(b.laeq_dbfs, abs=1e-9)
    assert a.true_peak_dbfs == pytest.approx(b.true_peak_dbfs, abs=1e-9)
    assert a.lead_silence_ms == b.lead_silence_ms
    assert a.clip_run_count == b.clip_run_count


def test_very_short_file_does_not_crash(analyzed):
    m = analyzed("s.wav", [[0.1, -0.1, 0.2, -0.2] * 10])
    assert m.duration_s > 0
    assert m.lufs_i is None            # 400 ms 블록이 하나도 안 나옵니다
    assert m.sample_peak_dbfs is not None


def test_single_sample_file(analyzed):
    m = analyzed("one.wav", [[0.5]])
    assert m.n_frames_10ms == 0
    assert m.lufs_i is None
    assert m.sample_peak_dbfs == pytest.approx(-6.02, abs=0.01)


def test_true_peak_on_clipped_file_exceeds_zero(analyzed):
    """클리핑된 파일은 표본 피크 0 dBFS 지만 트루피크는 그 위입니다."""
    x = _with_clipping(sine(7000.0, 0.5, 0.99, 48000), 1000, 200, 48000)
    m = analyzed("c.wav", [x], fs=48000, bits=24)
    assert m.sample_peak_dbfs == pytest.approx(0.0, abs=0.01)
    assert 0.0 < m.true_peak_dbfs < 6.0


def test_analysis_time_recorded(analyzed):
    m = analyzed("t.wav", [sine(300.0, 0.3, 0.3)])
    assert 0.0 < m.analysis_seconds < 60.0


def test_name_is_basename_only(analyzed):
    m = analyzed("이름.wav", [sine(300.0, 0.2, 0.3)])
    assert m.name == "이름.wav"
    assert "/" not in m.name
