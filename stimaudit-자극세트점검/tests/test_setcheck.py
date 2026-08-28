"""음성 대조 — 일부러 어긋뜨린 세트를 **각각 정확히 그 항목으로** 잡는지.

그리고 그 반대: 깨끗한 세트에서 0건이 나오는지(오탐 억제). 매번 우는 체커는
첫 실행 이후로 아무도 열지 않으므로, 이쪽이 더 중요할 수도 있습니다.
"""
from __future__ import annotations

import math
import os

import pytest

from stimaudit import analyze, claims, design, findings as F, setcheck, wavread
from tests.conftest import FS, fade, noise, sine, sine_rms

FS48 = 48000


def _set(tmp_path, spec):
    """`{"이름.wav": [채널...]}` → {이름: FileMetrics}."""
    out = {}
    for name, chans in spec.items():
        p = os.path.join(str(tmp_path), name)
        wavread.write_wav(p, chans, FS48, 24)
        out[name] = analyze.analyze_file(wavread.probe(p))
    return out


def _design(conditions, claims_=None, contrast=None):
    d = design.Design()
    d.conditions = dict(conditions)
    d.claims = dict(claims_ or {})
    d.contrast = contrast
    return d


def _kinds(fs, severity=None):
    return [f.kind for f in fs if severity is None or f.severity == severity]


def _tone(level_dbfs, seconds=1.2, freq=400.0):
    return fade(sine_rms(freq, seconds, level_dbfs, FS48), FS48, ms=100.0)


# ---------------------------------------------------------------- 오탐 억제

def test_matched_set_yields_nothing(tmp_path):
    """음량이 0.2 LU 안에 맞은 깨끗한 세트에 경고를 뱉으면 안 됩니다."""
    m = _set(tmp_path, {"a.wav": [_tone(-23.0, freq=400.0)],
                        "b.wav": [_tone(-23.0, freq=420.0)]})
    r = setcheck.run(m, _design({"x": ["a.wav"], "y": ["b.wav"]}), [], 1.0, 2.0)
    assert r.findings == []


def test_small_level_difference_is_not_reported(tmp_path):
    m = _set(tmp_path, {"a.wav": [_tone(-23.0)], "b.wav": [_tone(-23.4, freq=420.0)]})
    r = setcheck.run(m, _design({"x": ["a.wav"], "y": ["b.wav"]}), [], 1.0, 2.0)
    assert F.KIND_LEVEL_MISMATCH not in _kinds(r.findings)


# ------------------------------------------------------------- 음성 대조 ①~⑧

def test_three_lu_difference_is_critical(tmp_path):
    m = _set(tmp_path, {"a.wav": [_tone(-20.0)], "b.wav": [_tone(-23.0, freq=420.0)]})
    r = setcheck.run(m, _design({"active": ["a.wav"], "control": ["b.wav"]}), [], 1.0, 2.0)
    crit = [f for f in r.findings if f.severity == F.CRITICAL]
    assert [f.kind for f in crit] == [F.KIND_LEVEL_MISMATCH]
    assert "3.0 LU" in crit[0].detail
    assert "구분하지 못합니다" in crit[0].consequence
    # 사운드 담당자에게 그대로 보낼 한 줄까지 냅니다(파일은 만들지 않습니다).
    assert "control 를 +3.0 dB 하면 active 와 맞습니다" in crit[0].action
    assert "파일을 만들지 않습니다" in crit[0].action


def test_one_and_a_half_lu_difference_is_only_a_warning(tmp_path):
    m = _set(tmp_path, {"a.wav": [_tone(-21.5)], "b.wav": [_tone(-23.0, freq=420.0)]})
    r = setcheck.run(m, _design({"active": ["a.wav"], "control": ["b.wav"]}), [], 1.0, 2.0)
    assert F.KIND_LEVEL_MISMATCH in _kinds(r.findings, F.WARNING)
    assert F.count(r.findings, F.CRITICAL) == 0


def test_clipping_is_critical(tmp_path):
    x = _tone(-12.0)
    for i in range(20000, 20040):
        x[i] = 1.0
    m = _set(tmp_path, {"a.wav": [x], "b.wav": [_tone(-12.0, freq=420.0)]})
    r = setcheck.run(m, _design({"x": ["a.wav", "b.wav"]}), [], 1.0, 2.0)
    crit = [f for f in r.findings if f.severity == F.CRITICAL]
    assert [f.kind for f in crit] == [F.KIND_CLIPPING]
    assert crit[0].subject == "a.wav"


def test_dc_offset_is_a_warning(tmp_path):
    m = _set(tmp_path, {"a.wav": [[v + 0.05 for v in _tone(-23.0)]],
                        "b.wav": [_tone(-23.0, freq=420.0)]})
    r = setcheck.run(m, _design({"x": ["a.wav", "b.wav"]}), [], 1.0, 2.0)
    dc = [f for f in r.findings if f.kind == F.KIND_DC_OFFSET]
    assert len(dc) == 1 and dc[0].subject == "a.wav" and dc[0].severity == F.WARNING
    assert "+0.05" in dc[0].measured


def test_one_millisecond_onset_click_is_a_warning(tmp_path):
    click = sine_rms(400.0, 1.2, -23.0, FS48)          # 페이드 없음 = 즉시 시작
    m = _set(tmp_path, {"a.wav": [click], "b.wav": [_tone(-23.0, freq=420.0)]})
    r = setcheck.run(m, _design({"x": ["a.wav", "b.wav"]}), [], 1.0, 2.0)
    click_findings = [f for f in r.findings if f.kind == F.KIND_EDGE_CLICK]
    assert click_findings and click_findings[0].subject == "a.wav"
    assert all(f.severity == F.WARNING for f in click_findings)


def test_two_db_lr_imbalance_is_a_warning(tmp_path):
    left = _tone(-23.0)
    right = [v * 10 ** (-2.0 / 20.0) for v in left]
    m = _set(tmp_path, {"a.wav": [left, right], "b.wav": [_tone(-23.0, freq=420.0)]})
    r = setcheck.run(m, _design({"x": ["a.wav", "b.wav"]}), [], 1.0, 2.0)
    lr = [f for f in r.findings if f.kind == F.KIND_LR_IMBALANCE]
    assert len(lr) == 1 and lr[0].subject == "a.wav"
    assert "2.0 dB" in lr[0].detail


def test_balanced_stereo_gives_no_imbalance_warning(tmp_path):
    x = _tone(-23.0)
    m = _set(tmp_path, {"a.wav": [x, list(x)], "b.wav": [_tone(-23.0, freq=420.0), _tone(-23.0, freq=420.0)]})
    r = setcheck.run(m, _design({"x": ["a.wav", "b.wav"]}), [], 1.0, 2.0)
    assert F.KIND_LR_IMBALANCE not in _kinds(r.findings)


def test_wrong_carrier_claim_is_critical(tmp_path):
    m = _set(tmp_path, {"a.wav": [_tone(-23.0, seconds=1.5, freq=300.0)],
                        "b.wav": [_tone(-23.0, seconds=1.5, freq=310.0)]})
    d = _design({"x": ["a.wav", "b.wav"]}, {"a.wav": {"carrier_hz": 440.0}})
    cr = claims.check_all(m, d.claims)
    r = setcheck.run(m, d, cr, 1.0, 2.0)
    crit = [f for f in r.findings if f.severity == F.CRITICAL]
    assert [f.kind for f in crit] == [F.KIND_CLAIM_MISMATCH]
    assert "논문에 적을 값이 틀립니다" in crit[0].consequence


def test_dead_file_is_critical(tmp_path):
    m = _set(tmp_path, {"a.wav": [[0.0] * (FS48 * 2)], "b.wav": [_tone(-23.0)]})
    r = setcheck.run(m, _design({"x": ["a.wav", "b.wav"]}), [], 1.0, 2.0)
    crit = [f for f in r.findings if f.severity == F.CRITICAL]
    assert [f.kind for f in crit] == [F.KIND_DEAD]


def test_dead_file_does_not_also_report_clipping(tmp_path):
    """죽은 파일은 한 가지로만 보고합니다 — 같은 파일에 판정을 쌓지 않습니다."""
    m = _set(tmp_path, {"a.wav": [[0.0] * (FS48 * 2)], "b.wav": [_tone(-23.0)]})
    r = setcheck.run(m, _design({"x": ["a.wav", "b.wav"]}), [], 1.0, 2.0)
    assert len([f for f in r.findings if f.subject == "a.wav"]) == 1


# ------------------------------------------------------------------ 세트 일관성

def test_format_mismatch_warning(tmp_path):
    a = os.path.join(str(tmp_path), "a.wav")
    b = os.path.join(str(tmp_path), "b.wav")
    wavread.write_wav(a, [_tone(-23.0)], FS48, 24)
    wavread.write_wav(b, [fade(sine_rms(420.0, 1.2, -23.0, 44100), 44100, ms=100.0)], 44100, 16)
    m = {"a.wav": analyze.analyze_file(wavread.probe(a)),
         "b.wav": analyze.analyze_file(wavread.probe(b))}
    r = setcheck.run(m, None, [], 1.0, 2.0)
    assert F.KIND_FORMAT_MISMATCH in _kinds(r.findings, F.WARNING)


def test_duration_mismatch_warning(tmp_path):
    m = _set(tmp_path, {"a.wav": [_tone(-23.0, seconds=1.0)],
                        "b.wav": [_tone(-23.0, seconds=3.0, freq=420.0)]})
    r = setcheck.run(m, None, [], 1.0, 2.0)
    dur = [f for f in r.findings if f.kind == F.KIND_DURATION_MISMATCH]
    assert dur and "3.0배" in dur[0].detail


def test_same_duration_no_warning(tmp_path):
    m = _set(tmp_path, {"a.wav": [_tone(-23.0)], "b.wav": [_tone(-23.0, freq=420.0)]})
    r = setcheck.run(m, None, [], 1.0, 2.0)
    assert F.KIND_DURATION_MISMATCH not in _kinds(r.findings)


def test_true_peak_warning(tmp_path):
    m = _set(tmp_path, {"a.wav": [sine(9000.0, 1.2, 0.99, FS48)],
                        "b.wav": [_tone(-23.0)]})
    r = setcheck.run(m, None, [], 1.0, 2.0)
    assert F.KIND_TRUE_PEAK in _kinds(r.findings, F.WARNING)


def test_within_condition_spread_warning(tmp_path):
    m = _set(tmp_path, {"a.wav": [_tone(-20.0)], "b.wav": [_tone(-26.0, freq=420.0)],
                        "c.wav": [_tone(-23.0, freq=440.0)]})
    r = setcheck.run(m, _design({"active": ["a.wav", "b.wav"], "control": ["c.wav"]}),
                     [], 1.0, 2.0)
    spread = [f for f in r.findings if f.kind == F.KIND_LEVEL_SPREAD]
    assert spread and spread[0].subject == "active" and "6.0 LU" in spread[0].detail


# ------------------------------------------------------------------ 행렬

def test_matrix_without_design_is_file_level(tmp_path):
    m = _set(tmp_path, {"a.wav": [_tone(-20.0)], "b.wav": [_tone(-23.0, freq=420.0)]})
    r = setcheck.run(m, None, [], 1.0, 2.0)
    assert r.matrix.is_condition_level is False
    assert r.matrix.labels == ["a.wav", "b.wav"]
    assert r.matrix.diffs[("a.wav", "b.wav")] == pytest.approx(3.0, abs=0.1)


def test_no_level_verdict_without_design(tmp_path):
    """조건을 모르면 무엇이 대조군인지 알 수 없으므로 판정하지 않습니다."""
    m = _set(tmp_path, {"a.wav": [_tone(-14.0)], "b.wav": [_tone(-30.0, freq=420.0)]})
    r = setcheck.run(m, None, [], 1.0, 2.0)
    assert F.KIND_LEVEL_MISMATCH not in _kinds(r.findings)


def test_matrix_condition_value_is_mean_of_members(tmp_path):
    m = _set(tmp_path, {"a.wav": [_tone(-20.0)], "b.wav": [_tone(-24.0, freq=420.0)],
                        "c.wav": [_tone(-23.0, freq=440.0)]})
    r = setcheck.run(m, _design({"active": ["a.wav", "b.wav"], "control": ["c.wav"]}),
                     [], 1.0, 2.0)
    expected = (m["a.wav"].lufs_i + m["b.wav"].lufs_i) / 2.0
    assert r.matrix.values["active"] == pytest.approx(expected, abs=1e-9)
    assert r.matrix.values["control"] == pytest.approx(m["c.wav"].lufs_i, abs=1e-9)


def test_single_condition_makes_no_level_finding(tmp_path):
    m = _set(tmp_path, {"a.wav": [_tone(-14.0)], "b.wav": [_tone(-30.0, freq=420.0)]})
    r = setcheck.run(m, _design({"only": ["a.wav", "b.wav"]}), [], 1.0, 2.0)
    assert F.KIND_LEVEL_MISMATCH not in _kinds(r.findings)


def test_condition_map_recorded(tmp_path):
    m = _set(tmp_path, {"a.wav": [_tone(-23.0)], "b.wav": [_tone(-23.0, freq=420.0)]})
    r = setcheck.run(m, _design({"x": ["a.wav"], "y": ["b.wav"]}), [], 1.0, 2.0)
    assert r.condition_of == {"a.wav": "x", "b.wav": "y"}


def test_findings_sorted_critical_first(tmp_path):
    x = _tone(-12.0)
    for i in range(20000, 20040):
        x[i] = 1.0
    m = _set(tmp_path, {"a.wav": [[v + 0.05 for v in x]], "b.wav": [_tone(-23.0, freq=420.0)]})
    r = setcheck.run(m, _design({"p": ["a.wav"], "q": ["b.wav"]}), [], 1.0, 2.0)
    severities = [f.severity for f in r.findings]
    assert severities == sorted(severities, key=lambda s: {F.CRITICAL: 0, F.WARNING: 1}.get(s, 2))


def test_custom_thresholds_respected(tmp_path):
    m = _set(tmp_path, {"a.wav": [_tone(-20.0)], "b.wav": [_tone(-23.0, freq=420.0)]})
    d = _design({"x": ["a.wav"], "y": ["b.wav"]})
    assert F.count(setcheck.run(m, d, [], 1.0, 2.0).findings, F.CRITICAL) == 1
    assert F.count(setcheck.run(m, d, [], 4.0, 5.0).findings, F.CRITICAL) == 0
