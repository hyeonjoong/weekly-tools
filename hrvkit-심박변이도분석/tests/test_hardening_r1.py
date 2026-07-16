"""하드닝 라운드 1 회귀 테스트 — 적대적 리뷰에서 발견된 결함 방어.

다룸: 비생리적(0/음수) 박동 크래시, JSON 표준 준수(NaN/Infinity), 헤더 전용
파일, 구분자 과반 규칙, 느린/공명 호흡 레짐 감지, remove-소수박동, 경로 PII.
"""

import csv
import io
import json
import math
import os

import pytest

from hrvkit import analyze_rr, cli
from hrvkit.analyze import flat_metrics
from hrvkit.dataio import load_series, _sniff_delimiter
from hrvkit.report import render_comparison
from hrvkit.timedomain import time_domain

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")
REST = os.path.join(EXAMPLES, "resting.csv")


# --- HIGH-1: 비생리적 값이 지표 계산을 크래시시키지 않음 -------------------- #
def test_time_domain_rejects_nonpositive():
    with pytest.raises(ValueError):
        time_domain([800.0, 0.0, 810.0])
    with pytest.raises(ValueError):
        time_domain([800.0, -5.0, 810.0])


def test_analyze_all_zeros_clean_error_not_crash():
    with pytest.raises(ValueError):
        analyze_rr([0.0] * 10)


def test_analyze_drops_single_zero_and_warns():
    res = analyze_rr([800, 810, 0, 805, 795, 800, 812, 790],
                     clean_method="none")
    assert any("비생리" in w for w in res.warnings)
    assert res.time["mean_nn"] > 0  # 크래시 없이 정상 계산


def test_cli_zeros_exits_cleanly(capsys, tmp_path):
    p = tmp_path / "z.csv"
    p.write_text("rr_ms\n0\n0\n0\n0\n", encoding="utf-8")
    rc = cli.main([str(p)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "오류" in err  # 트레이스백이 아니라 깔끔한 메시지


# --- HIGH-2: 예상 못한 예외도 파일명과 함께 exit 2 --------------------------- #
def test_cli_batch_bad_file_named(capsys, tmp_path):
    z = tmp_path / "zeros.csv"
    z.write_text("rr_ms\n0\n0\n0\n", encoding="utf-8")
    rc = cli.main([REST, str(z)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "zeros.csv" in err


# --- MEDIUM-3: JSON 표준 준수(비유한 → 문자열) ------------------------------- #
def test_json_output_is_strict_valid_on_degenerate(capsys, tmp_path):
    p = tmp_path / "flat.csv"
    p.write_text("rr_ms\n" + "\n".join(["800"] * 60) + "\n", encoding="utf-8")
    rc = cli.main([str(p), "--format", "json"])
    out = capsys.readouterr().out
    assert rc == 0
    # 엄격 파서 거부 토큰이 없어야 함
    assert "Infinity" not in out and "NaN," not in out.replace('"NaN"', "")
    data = json.loads(out)  # 표준 파서로도 성공
    assert data["frequency_domain"]["lf_hf_ratio"] in ("inf", "-inf")


# --- LOW-4: 헤더 전용 파일 → 깔끔한 오류 ------------------------------------ #
def test_header_only_clean_error(tmp_path):
    p = tmp_path / "h.csv"
    p.write_text("rr_ms\n", encoding="utf-8")
    with pytest.raises(ValueError) as e:
        load_series(str(p))
    assert "데이터 행" in str(e.value)


# --- LOW-5: 구분자 과반 규칙 ------------------------------------------------ #
def test_sniff_ignores_single_stray_semicolon():
    lines = ["rr_ms", "80;5", "798", "805", "790"]
    assert _sniff_delimiter(lines) == ","


def test_stray_semicolon_row_dropped_not_split(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("rr_ms\n80;5\n798\n805\n790\n810\n", encoding="utf-8")
    rr, meta = load_series(str(p))
    assert meta["delimiter"] == ","
    assert rr == [798.0, 805.0, 790.0, 810.0]  # 오염된 80;5 는 제외


# --- 느린/공명 호흡 레짐 감지 ---------------------------------------------- #
def _paced(breath_hz, n=400, mean=900.0, amp=60.0):
    nn, t = [], 0.0
    for _ in range(n):
        v = mean + amp * math.sin(2 * math.pi * breath_hz * t)
        nn.append(v)
        t += v / 1000.0
    return nn


def test_slow_breathing_regime_detected_and_resp_from_lf():
    res = analyze_rr(_paced(0.1), source="paced6.csv")  # 6회/분, LF
    assert res.freq["slow_breathing_regime"] is True
    assert res.freq["resp_source"] == "LF"
    assert res.freq["resp_rate_brpm"] == pytest.approx(6.0, abs=1.5)
    assert any("레짐" in w for w in res.warnings)


def test_hf_breathing_not_flagged():
    res = analyze_rr(_paced(0.25), source="spont.csv")  # 15회/분, HF
    assert res.freq["slow_breathing_regime"] is False
    assert res.freq["resp_source"] == "HF"


def test_examples_not_flagged_as_slow_regime():
    for name in ("resting.csv", "slow_breathing.csv"):
        rr, _ = load_series(os.path.join(EXAMPLES, name))
        assert analyze_rr(rr).freq["slow_breathing_regime"] is False


def test_comparison_excludes_hf_rows_under_slow_regime():
    base = analyze_rr(_paced(0.25), source="base.csv")     # HF
    interv = analyze_rr(_paced(0.1), source="interv.csv")  # 느린 호흡(LF)
    out = render_comparison(base, interv)
    assert "레짐" in out
    # HF 기반 행은 '레짐?' 로 표시되어 방향 집계에서 빠짐
    assert "레짐?" in out


# --- remove 로 소수 박동만 남는 경우 --------------------------------------- #
def test_analyze_remove_too_few_beats_raises():
    with pytest.raises(ValueError):
        analyze_rr([250.0, 3000.0, 240.0, 2500.0], clean_method="remove")


# --- 경로 PII: CSV source 는 basename ------------------------------------- #
def test_flat_metrics_source_is_basename():
    res = analyze_rr([800 + 10 * math.sin(i) for i in range(60)],
                     source="/private/patients/subjX/rec.csv")
    assert flat_metrics(res)["source"] == "rec.csv"


def test_ragged_rows_flagged(tmp_path):
    p = tmp_path / "r.csv"
    # 한 행만 열 개수가 다름(time_s 열 누락) → ragged
    p.write_text("time_s,rr_ms\n0.0,800\n0.8,810\n1.6\n2.4,805\n",
                 encoding="utf-8")
    rr, meta = load_series(str(p))
    assert meta["ragged"] is True
    assert rr == [800.0, 810.0, 805.0]  # 1열짜리 행은 값 열 없어 제외
