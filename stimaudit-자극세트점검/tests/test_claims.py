"""주장 대조 — 맞으면 일치, 틀리면 불일치, 못 재면 침묵."""
from __future__ import annotations

import math

import pytest

from stimaudit import claims
from tests.conftest import fade, noise, sine


def _tone(analyzed, name, freq, seconds=1.5, fs=48000):
    return analyzed(name, [fade(sine(freq, seconds, 0.35, fs), fs, ms=50.0)],
                    fs=fs, bits=24)


def test_carrier_claim_matches(analyzed):
    m = _tone(analyzed, "t.wav", 440.0)
    (r,) = claims.check_file(m, {"carrier_hz": 440.0})
    assert r.verdict == "일치"
    assert r.measured == pytest.approx(440.0, abs=2.0)


def test_carrier_claim_mismatch(analyzed):
    m = _tone(analyzed, "t.wav", 300.0)
    (r,) = claims.check_file(m, {"carrier_hz": 440.0})
    assert r.verdict == "불일치"
    assert r.is_mismatch


def test_carrier_tolerance_is_two_percent(analyzed):
    m = _tone(analyzed, "t.wav", 1000.0)
    assert claims.check_file(m, {"carrier_hz": 1015.0})[0].verdict == "일치"
    assert claims.check_file(m, {"carrier_hz": 1100.0})[0].verdict == "불일치"


def test_carrier_on_noise_is_undecidable(analyzed):
    """잡음에는 반송음이 없습니다 — 최대 빈을 반송음이라 부르면 거짓말입니다."""
    m = analyzed("n.wav", [fade(noise(2.0, 0.3, 48000), 48000)], fs=48000, bits=24)
    (r,) = claims.check_file(m, {"carrier_hz": 440.0})
    assert r.verdict == "판정불가"
    assert "두드러짐" in r.note


def test_beat_claim_matches(analyzed):
    fs = 48000
    m = analyzed("b.wav", [fade(sine(360.0, 1.5, 0.3, fs), fs, ms=50.0),
                           fade(sine(400.0, 1.5, 0.3, fs), fs, ms=50.0)], fs=fs, bits=24)
    (r,) = claims.check_file(m, {"beat_hz": 40.0})
    assert r.verdict == "일치"
    assert r.measured == pytest.approx(40.0, abs=1.0)
    assert "L 360" in r.note


def test_beat_claim_mismatch(analyzed):
    fs = 48000
    m = analyzed("b.wav", [fade(sine(300.0, 1.5, 0.3, fs), fs, ms=50.0),
                           fade(sine(320.0, 1.5, 0.3, fs), fs, ms=50.0)], fs=fs, bits=24)
    (r,) = claims.check_file(m, {"beat_hz": 40.0})
    assert r.verdict == "불일치"
    assert r.measured == pytest.approx(20.0, abs=1.0)


def test_beat_claim_on_mono_is_undecidable(analyzed):
    m = _tone(analyzed, "m.wav", 360.0)
    (r,) = claims.check_file(m, {"beat_hz": 40.0})
    assert r.verdict == "판정불가"
    assert "좌우" in r.note


def test_mod_claim_matches(analyzed):
    fs = 48000
    n = int(fs * 8)
    x = [0.4 * math.sin(2 * math.pi * 400 * i / fs)
         * (0.5 + 0.5 * math.sin(2 * math.pi * 0.8 * i / fs)) for i in range(n)]
    m = analyzed("am.wav", [x], fs=fs, bits=24)
    (r,) = claims.check_file(m, {"mod_hz": 0.8})
    assert r.verdict == "일치"


def test_mod_claim_mismatch(analyzed):
    fs = 48000
    n = int(fs * 8)
    x = [0.4 * math.sin(2 * math.pi * 400 * i / fs)
         * (0.5 + 0.5 * math.sin(2 * math.pi * 2.0 * i / fs)) for i in range(n)]
    m = analyzed("am.wav", [x], fs=fs, bits=24)
    (r,) = claims.check_file(m, {"mod_hz": 0.8})
    assert r.verdict == "불일치"


def test_mod_claim_needs_three_cycles(analyzed):
    """20초 파일에 0.1 Hz 주장 = 주기 2번. 값을 지어내지 않고 판정불가로 둡니다."""
    fs = 48000
    m = analyzed("s.wav", [fade(sine(400.0, 2.0, 0.3, fs), fs)], fs=fs, bits=24)
    (r,) = claims.check_file(m, {"mod_hz": 0.5})
    assert r.verdict == "판정불가"
    assert "주기" in r.note


def test_mod_claim_on_steady_tone_is_undecidable(analyzed):
    fs = 48000
    m = analyzed("s.wav", [fade(sine(400.0, 8.0, 0.3, fs), fs, ms=20.0)], fs=fs, bits=24)
    (r,) = claims.check_file(m, {"mod_hz": 1.0})
    assert r.verdict == "판정불가"


def test_duration_claim(analyzed):
    m = _tone(analyzed, "t.wav", 440.0, seconds=1.5)
    assert claims.check_file(m, {"duration_s": 1.5})[0].verdict == "일치"
    assert claims.check_file(m, {"duration_s": 3.0})[0].verdict == "불일치"


def test_duration_tolerance(analyzed):
    m = _tone(analyzed, "t.wav", 440.0, seconds=2.0)
    assert claims.check_file(m, {"duration_s": 2.02})[0].verdict == "일치"
    assert claims.check_file(m, {"duration_s": 2.2})[0].verdict == "불일치"


def test_multiple_claims_sorted(analyzed):
    m = _tone(analyzed, "t.wav", 440.0)
    rs = claims.check_file(m, {"duration_s": 1.5, "carrier_hz": 440.0})
    assert [r.key for r in rs] == ["carrier_hz", "duration_s"]


def test_unknown_claim_key_raises(analyzed):
    m = _tone(analyzed, "t.wav", 440.0)
    with pytest.raises(ValueError):
        claims._tolerance("roughness_asper", 0.3, m)


def test_check_all_skips_unknown_files(analyzed):
    m = _tone(analyzed, "t.wav", 440.0)
    out = claims.check_all({"t.wav": m}, {"t.wav": {"carrier_hz": 440.0},
                                          "ghost.wav": {"carrier_hz": 1.0}})
    assert len(out) == 1
    assert out[0].file == "t.wav"


def test_claim_text_formatting(analyzed):
    m = _tone(analyzed, "t.wav", 440.0)
    (r,) = claims.check_file(m, {"carrier_hz": 440.0})
    assert r.claimed_text() == "440 Hz"
    assert "Hz" in r.measured_text()


def test_undecidable_measured_text_is_dash(analyzed):
    m = _tone(analyzed, "m.wav", 360.0)
    (r,) = claims.check_file(m, {"beat_hz": 40.0})
    assert r.measured_text() == "—"


def test_mod_claim_above_envelope_band_is_undecidable(analyzed):
    """40 Hz AM 주장에 "실측 19.999 Hz" 라는 치명이 붙던 결함(라운드 1)."""
    fs = 48000
    n = int(fs * 6)
    x = [0.35 * math.sin(2 * math.pi * 500 * i / fs)
         * (0.5 + 0.5 * math.sin(2 * math.pi * 40 * i / fs)) for i in range(n)]
    m = analyzed("am40.wav", [x], fs=fs, bits=24)
    (r,) = claims.check_file(m, {"mod_hz": 40.0})
    assert r.verdict == "판정불가"
    assert "대역" in r.note and "DEBUSSY" in r.note


def test_carrier_measurement_does_not_depend_on_the_claim(analyzed):
    """주장에 가까운 채널을 고르면 같은 파일에 대한 모순된 두 주장이 둘 다 통과합니다."""
    fs = 48000
    m = analyzed("split.wav", [fade(sine(440.0, 1.5, 0.3, fs), fs, ms=50.0),
                               fade(sine(1000.0, 1.5, 0.3, fs), fs, ms=50.0)],
                 fs=fs, bits=24)
    a = claims.check_file(m, {"carrier_hz": 440.0})[0]
    b = claims.check_file(m, {"carrier_hz": 1000.0})[0]
    assert a.verdict == "판정불가" and b.verdict == "판정불가"
    assert a.measured is None and b.measured is None
    assert "너무 멀어" in a.note


def test_binaural_pair_carrier_is_the_channel_mean(analyzed):
    """L 360 / R 400 의 반송주파수는 관례상 평균 380 Hz 입니다."""
    fs = 48000
    m = analyzed("bi.wav", [fade(sine(360.0, 1.5, 0.3, fs), fs, ms=50.0),
                            fade(sine(400.0, 1.5, 0.3, fs), fs, ms=50.0)],
                 fs=fs, bits=24)
    r = claims.check_file(m, {"carrier_hz": 380.0})[0]
    assert r.verdict == "일치"
    assert r.measured == pytest.approx(380.0, abs=2.0)
    assert "채널 평균" in r.note
    # 한쪽 채널 값을 주장하면 통과하지 않아야 합니다.
    assert claims.check_file(m, {"carrier_hz": 360.0})[0].verdict == "불일치"


def test_no_psychoacoustic_claims_supported():
    """경계 강제 — asper/acum 주장은 애초에 스키마에 없습니다."""
    from stimaudit.design import SUPPORTED_CLAIMS
    joined = " ".join(SUPPORTED_CLAIMS)
    assert "asper" not in joined and "acum" not in joined and "roughness" not in joined
