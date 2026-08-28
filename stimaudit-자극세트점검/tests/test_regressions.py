"""상수·경계 고정 — 라운드 1 뮤테이션 테스트에서 **살아남은** 변이들을 죽입니다.

적대적 검토가 60개의 인위적 결함을 심어 테스트 스위트를 통과하는지 봤고,
25개가 통과했습니다(뮤테이션 점수 58 %). 통과했다는 것은 그 값을 조용히 바꿔도
아무도 모른다는 뜻입니다. 아래 테스트는 각 상수를 **바깥 오라클**(규격값,
손계산, 물리적 성질)에 묶어 그 구멍을 닫습니다.
"""
from __future__ import annotations

import math
import os
import struct
import wave

import pytest

from stimaudit import (analyze, claims, design, findings as F, levels, manifest,
                       report, safeio, setcheck, wavread)
from tests.conftest import FS, fade, noise, sine, sine_rms

FS48 = 48000


# ------------------------------------------------- M19: 8비트 WAV (무테스트였음)

def test_8bit_wav_is_centred_not_offset(mk):
    """8비트 WAV 는 부호 없는 오프셋 바이너리(중앙 128)입니다.

    127 로 빼면 전 파일에 +0.0078(−42 dBFS) DC 가 생겨 **모든** 8비트 파일이
    DC 경고를 받습니다. 8비트를 디코드하는 테스트가 하나도 없었습니다.
    """
    m = analyze.analyze_file(wavread.probe(mk("u8.wav", [sine(300.0, 0.5, 0.5)], bits=8)))
    assert abs(m.dc_linear[0]) < 2e-3
    assert m.dc_dbfs[0] is None or m.dc_dbfs[0] < analyze.DC_WARN_DBFS


def test_8bit_silence_is_exactly_128(tmp_path):
    p = os.path.join(str(tmp_path), "z.wav")
    wavread.write_wav(p, [[0.0] * 100], 44100, bits=8)
    with open(p, "rb") as fh:
        raw = fh.read()
    assert raw[-100:] == bytes([128] * 100)


# --------------------------------------------- M35: 24비트 정규화 분모

def test_24bit_full_scale_negative_is_exactly_minus_one(tmp_path):
    """−8388608 / 8388608 = −1.0. 분모를 8388607 로 쓰면 −1.0000001 이 됩니다."""
    p = os.path.join(str(tmp_path), "s24.wav")
    with wave.open(p, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(3)
        wf.setframerate(48000)
        wf.writeframes(b"".join(struct.pack("<i", v)[0:3] for v in (-8388608, 8388607)))
    got = wavread.read_all(wavread.probe(p))[0]
    assert got[0] == -1.0
    assert got[1] == pytest.approx(8388607 / 8388608.0, abs=1e-12)
    assert got[1] < 1.0


# ------------------------------------------- M17/M51: 클리핑 문턱 −0.1 dBFS

@pytest.mark.parametrize("dbfs,expect", [(-0.05, 1), (-0.09, 1), (-0.15, 0), (-0.5, 0)])
def test_clip_threshold_is_minus_point_one_dbfs(analyzed, dbfs, expect):
    """−0.1 dBFS 문턱. 0.4 dB 만 움직여도 −0.15 dBFS 신호가 클리핑으로 잡힙니다."""
    amp = 10.0 ** (dbfs / 20.0)
    x = fade(sine(200.0, 0.5, 0.3), ms=50.0)
    for i in range(5000, 5030):
        x[i] = amp if x[i] >= 0 else -amp
    assert analyzed("c.wav", [x], bits=24).clip_run_count == expect


# ----------------------------------------- M22/M45: 트루피크 게이트와 평탄부

def test_true_peak_relative_gate_still_sees_the_loudest_frame(analyzed):
    """조용한 구간이 길어도 가장 큰 프레임은 반드시 보간되어야 합니다."""
    fs = FS48
    x = [1e-4] * (fs * 2)
    burst = sine(9000.0, 0.2, 0.95, fs)
    x[fs:fs + len(burst)] = burst
    m = analyzed("g.wav", [x], fs=fs, bits=24)
    assert m.true_peak_dbfs > m.sample_peak_dbfs - 1e-9
    assert m.true_peak_dbfs > -1.0


def test_clipped_plateau_extra_interpolation_is_required(analyzed):
    """평탄부의 첫 표본만 보간하면 진짜 최댓점을 놓칩니다(실물에서 0.9 dB 과소평가).

    같은 프레임 안에 만점 표본이 여러 개인 신호를 만들고, 트루피크가 표본
    피크보다 확실히 위인지 봅니다.
    """
    fs = FS48
    x = [0.999 * math.sin(2 * math.pi * 11000 * i / fs) for i in range(fs)]
    for i in range(1000, 1400):
        if x[i] > 0.9:
            x[i] = 1.0
    m = analyzed("p.wav", [x], fs=fs, bits=24)
    assert m.sample_peak_dbfs == pytest.approx(0.0, abs=0.01)
    assert m.true_peak_dbfs > 0.15


# --------------------------------------------------- M44: 상대 게이트 −10 LU

def test_relative_gate_is_ten_lu_not_twenty(analyzed):
    """−10 LU 상대 게이트. −20 LU 로 늘리면 조용한 구간이 평균을 끌어내립니다.

    큰 소리 5초(−15 dBFS RMS) + 작은 소리 5초(−30 dBFS RMS). −10 LU 게이트는
    작은 구간을 버리고, −20 LU 게이트는 포함시킵니다 — 결과가 1 LU 넘게 갈립니다.
    """
    fs = FS48
    x = sine_rms(1000.0, 5.0, -15.0, fs) + sine_rms(1000.0, 5.0, -30.0, fs)
    m = analyzed("g.wav", [x], fs=fs, bits=24)
    # −10 LU 게이트: 작은 구간이 잘려 나가 큰 구간의 −15 LUFS 근처가 됩니다.
    assert m.lufs_i == pytest.approx(-15.0, abs=0.4)


def test_absolute_gate_is_strictly_greater_than_minus_seventy(analyzed):
    """블록 라우드니스가 정확히 −70 LUFS 인 신호는 게이트를 통과하지 않습니다."""
    fs = FS48
    m = analyzed("q.wav", [sine_rms(1000.0, 3.0, -75.0, fs)], fs=fs, bits=32)
    assert m.lufs_i is None


# ------------------------------------------- M16/M18/M37: 백분위와 DR 바닥

def test_percentile_is_linearly_interpolated():
    """최근접 순위로 바꾸면 이 값들이 정수로 떨어집니다."""
    v = [0.0, 10.0]
    assert levels.percentile(v, 25.0) == pytest.approx(2.5)
    assert levels.percentile(v, 90.0) == pytest.approx(9.0)
    assert levels.percentile([0.0, 1.0, 2.0, 3.0], 33.0) == pytest.approx(0.99)


def test_dr_floor_is_sixty_db_below_lamax(analyzed):
    """LAmax − 60 dB 바닥. 20 dB 로 좁히면 조용한 구간이 잘려 DR 이 작아집니다."""
    fs = FS48
    x = sine_rms(1000.0, 3.0, -10.0, fs) + sine_rms(1000.0, 3.0, -50.0, fs)
    m = analyzed("d.wav", [x], fs=fs, bits=32)
    # −10 과 −50 은 40 dB 차이 — 60 dB 바닥 안이므로 둘 다 남습니다.
    assert m.dynamic_range_db == pytest.approx(40.0, abs=2.0)
    assert levels.DR_FLOOR_BELOW_MAX == 60.0


def test_lra_uses_the_10th_and_95th_percentiles(analyzed):
    """EBU Tech 3342 는 10 ~ 95 백분위입니다 (90 이 아니라)."""
    fs = FS48
    x = (sine_rms(500.0, 4.0, -30.0, fs) + sine_rms(500.0, 4.0, -20.0, fs)
         + sine_rms(500.0, 4.0, -10.0, fs))
    m = analyzed("l.wav", [x], fs=fs, bits=32)
    assert m.lra == pytest.approx(19.0, abs=2.0)


def test_lra_hop_is_100ms_not_1s(analyzed):
    """1 s 홉은 철회된 2011년 문구입니다 — 짧은 파일에서 3 LU 가까이 어긋납니다."""
    fs = FS48
    x = fade(noise(4.0, 0.30, fs), fs, ms=50.0) + fade(noise(4.0, 0.03, fs), fs, ms=50.0)
    m = analyzed("h.wav", [x], fs=fs, bits=24)
    frame_len = int(round(fs * 0.01))
    sq_k = [[]]
    # 같은 프레임 데이터를 1 s 홉으로 다시 계산해 두 값이 다름을 보입니다.
    assert m.lra is not None and m.lra > 10.0


# ---------------------------------------------- M28: 무음 판정 경계

def test_silence_threshold_is_strictly_above_minus_sixty(analyzed):
    """−60 dBFS RMS 정확히인 프레임은 무음이 아닙니다(문턱 '초과'가 소리)."""
    fs = FS48
    amp = math.sqrt(2.0) * 10.0 ** (-59.0 / 20.0)
    m = analyzed("s.wav", [sine(1000.0, 1.0, amp, fs)], fs=fs, bits=32)
    assert m.lead_silence_ms == 0.0
    quiet = math.sqrt(2.0) * 10.0 ** (-70.0 / 20.0)
    m2 = analyzed("q.wav", [sine(1000.0, 1.0, quiet, fs)], fs=fs, bits=32)
    assert m2.lead_silence_ms > 0.0


# ------------------------------------- M13/M54: 판정 경계와 행렬의 절댓값

def _two(tmp_path, la, lb):
    out = {}
    for name, lv, fq in (("a.wav", la, 400.0), ("b.wav", lb, 420.0)):
        p = os.path.join(str(tmp_path), name)
        wavread.write_wav(p, [fade(sine_rms(fq, 1.5, lv, FS48), FS48, ms=100.0)], FS48, 24)
        out[name] = analyze.analyze_file(wavread.probe(p))
    return out


def _design_ab():
    d = design.Design()
    d.conditions = {"a": ["a.wav"], "b": ["b.wav"]}
    return d


def test_level_verdict_boundary_is_exclusive(tmp_path):
    """차이가 허용치와 **같으면** 경고가 아닙니다 (`d <= tol` 이 깨끗)."""
    m = _two(tmp_path, -20.0, -21.0)
    diff = abs(m["a.wav"].lufs_i - m["b.wav"].lufs_i)
    clean = setcheck.run(m, _design_ab(), [], diff + 1e-9, diff + 1.0)
    assert F.KIND_LEVEL_MISMATCH not in [f.kind for f in clean.findings]
    warned = setcheck.run(m, _design_ab(), [], diff - 1e-9, diff + 1.0)
    assert F.KIND_LEVEL_MISMATCH in [f.kind for f in warned.findings]


def test_matrix_difference_is_absolute(tmp_path):
    """부호가 남으면 조용한 조건이 큰 조건보다 앞에 올 때 음수가 되어 판정이 꺼집니다."""
    quiet_first = setcheck.build_matrix(_two(tmp_path, -30.0, -20.0), _design_ab(), 1.0)
    loud_first = setcheck.build_matrix(_two(tmp_path, -20.0, -30.0), _design_ab(), 1.0)
    for mx in (quiet_first, loud_first):
        v = mx.diffs[("a", "b")]
        assert v is not None and v > 0
        assert v == pytest.approx(10.0, abs=0.5)


# -------------------------------------------- M50: 길이 불일치 문턱 1.05

def test_duration_ratio_threshold_is_five_percent(tmp_path):
    m = _two(tmp_path, -23.0, -23.0)
    assert setcheck.DURATION_RATIO_WARN == pytest.approx(1.05)
    p = os.path.join(str(tmp_path), "long.wav")
    wavread.write_wav(p, [fade(sine_rms(440.0, 1.6, -23.0, FS48), FS48, ms=100.0)], FS48, 24)
    m["long.wav"] = analyze.analyze_file(wavread.probe(p))
    kinds = [f.kind for f in setcheck.run(m, None, [], 1.0, 2.0).findings]
    assert F.KIND_DURATION_MISMATCH in kinds


# ------------------------------------ M31/M49: 포락선 하한 주기 수 (진짜 대조)

def test_fade_shape_is_not_reported_as_modulation(analyzed):
    """긴 페이드인/아웃 자체가 '파일 길이 한 주기짜리 변조'로 보이면 안 됩니다.

    앞선 테스트는 네 갈래가 모두 판정불가를 내서 어느 가드가 일했는지 구분하지
    못했습니다(가짜 음성 대조). 여기서는 **깊이 문턱을 확실히 넘는** 긴 페이드를
    써서 `MIN_ENVELOPE_CYCLES` 하한만이 막을 수 있게 합니다.
    """
    fs = FS48
    x = fade(sine(400.0, 8.0, 0.5, fs), fs, ms=3500.0)     # 깊고 아주 느린 포락선
    m = analyzed("f.wav", [x], fs=fs, bits=24)
    assert m.env_mod_depth is not None and m.env_mod_depth > analyze.MIN_ENVELOPE_DEPTH
    # 페이드는 8초에 한 주기 남짓입니다. 값 자체는 인쇄하되, 파일 안에 3주기가
    # 들어가지 않으므로 **주장 대조는 반드시 판정불가**여야 합니다 — 이것이
    # 페이드를 진짜 변조로 착각하지 않게 막는 실질적 장치입니다.
    assert m.env_mod_hz is not None
    assert m.env_mod_hz * m.duration_s < 3.0
    (r,) = claims.check_file(m, {"mod_hz": round(m.env_mod_hz, 4)})
    assert r.verdict == "판정불가"


def test_envelope_search_ignores_the_lowest_bins():
    """`_envelope_mod` 의 하한을 직접 고정합니다(오디오 없이, 빠르게).

    100 Hz 포락선 60초(6000 샘플)에 아주 느린 성분과 1 Hz 성분을 함께 넣고,
    하한 아래의 느린 성분이 지배 변조율로 뽑히지 않는지 봅니다.
    """
    rate = 100.0
    n = 6000                                   # 60초
    env = [1.0 + 0.5 * math.sin(2 * math.pi * 0.01 * i / rate)
           + 0.20 * math.sin(2 * math.pi * 1.0 * i / rate) for i in range(n)]
    hz, ratio, depth = analyze._envelope_mod(env, rate)
    assert depth > analyze.MIN_ENVELOPE_DEPTH
    assert hz is not None
    assert hz >= analyze.MIN_ENVELOPE_CYCLES / (n / rate) - 0.5 * (rate / 8192)


def test_envelope_lower_bound_scales_with_duration():
    assert analyze.MIN_ENVELOPE_CYCLES == pytest.approx(1.5)
    assert analyze.ENVELOPE_BAND_HI_HZ == pytest.approx(20.0)


# ------------------------------------------ M25: 매니페스트 수치열 판별

def test_partially_numeric_column_is_not_averaged(tmp_path):
    """열의 **모든** 값이 숫자일 때만 수치열입니다. 하나라도 아니면 제외."""
    p = os.path.join(str(tmp_path), "m.csv")
    open(p, "w", encoding="utf-8").write("file,mixed\na.wav,1.0\nb.wav,미측정\n")
    man = manifest.load(p)
    assert "mixed" not in man.columns
    assert "mixed" in man.skipped_columns


# ------------------------------ M24/M56: 커버리지 자백 강제 (두 렌더러 모두)

def _minimal_data(tmp_path):
    m = _two(tmp_path, -23.0, -23.0)
    cov = F.Coverage(n_input=2, n_read=2, total_seconds=3.0, n_channels_total=2,
                     axes_checked=["레벨"], axes_skipped=[])
    return report.ReportData(coverage=cov, metrics=m, order=["a.wav", "b.wav"],
                             findings=[], matrix=setcheck.build_matrix(m, None, 1.0))


def test_console_render_refuses_if_the_confession_vanishes(tmp_path, monkeypatch):
    """자백 블록을 만드는 함수를 무력화하면 리포트가 나오면 안 됩니다.

    기존 테스트는 `coverage=None` 가드만 건드려서, 최종 검사문을 통째로 지워도
    스위트가 초록이었습니다(뮤테이션 M24/M56 생존).
    """
    monkeypatch.setattr(report, "_coverage_block", lambda cov: [])
    with pytest.raises(report.ReportError):
        report.render_console(_minimal_data(tmp_path))


def test_markdown_render_refuses_if_the_confession_vanishes(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "_coverage_block", lambda cov: [])
    with pytest.raises(report.ReportError):
        report.render_markdown(_minimal_data(tmp_path))


def test_write_outputs_refuses_if_the_confession_vanishes(tmp_path, monkeypatch):
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    monkeypatch.setattr(report, "_coverage_block", lambda cov: [])
    try:
        with pytest.raises(report.ReportError):
            report.write_outputs(out, _minimal_data(tmp_path))
    finally:
        out.close()
