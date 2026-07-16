"""R1 리뷰가 지목한 미커버 분기들에 대한 표적 테스트."""

import os
import tempfile

from logflow.cli import main
from logflow.dataio import _detect_delimiter, load_events
from logflow.report import _bar_row, _fmt_secs, _hour_sparkline

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "examples", "app_events.csv")


def _write(text, encoding="utf-8"):
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(path, "w", encoding=encoding) as fh:
        fh.write(text)
    return path


# ---- 구분자 자동감지: 파이프 + Sniffer 실패 폴백 ----

def test_pipe_delimiter_autodetected():
    path = _write("user_id|event|timestamp\nu1|a|2026-01-01T09:00:00\nu2|b|2026-01-01T10:00:00\n")
    try:
        assert [e.user for e in load_events(path)] == ["u1", "u2"]
    finally:
        os.remove(path)


def test_detect_delimiter_fallback_on_sniffer_error():
    # 구분자 없는 단일 토큰 샘플 → Sniffer 예외 → 폴백은 콤마
    assert _detect_delimiter("abc") == ","
    # 폴백 카운팅: 세미콜론이 더 많으면 세미콜론
    assert _detect_delimiter("a;b;c") in (";", ",")  # sniffer 또는 폴백 모두 허용
    assert _detect_delimiter("") == ","


# ---- utf-8-sig BOM 제거 (기본 인코딩) ----

def test_utf8_sig_bom_stripped_from_header():
    path = _write("﻿user_id,event,timestamp\nu1,a,2026-01-01T09:00:00\n", encoding="utf-8")
    try:
        # 기본 encoding=utf-8-sig 가 BOM 을 제거해 헤더가 정상 매칭돼야 한다
        evs = load_events(path)
        assert [e.user for e in evs] == ["u1"]
    finally:
        os.remove(path)


# ---- report 헬퍼 분기 ----

def test_fmt_secs_units():
    assert _fmt_secs(None) == "n/a"
    assert _fmt_secs(30).endswith("초")
    assert _fmt_secs(600).endswith("분")        # 10분
    assert _fmt_secs(3600 * 2).endswith("시간")  # 2시간 (>90분 경로)


def test_bar_row_all_zero_branch():
    row = _bar_row([0, 0, 0], ["월", "화", "수"])
    assert "월" in row and "0" in row  # mx==0 분기가 예외 없이 렌더


def test_hour_sparkline_all_zero_and_nonzero():
    assert "▁" in _hour_sparkline([0] * 24)          # mx==0 분기
    line = _hour_sparkline([0] * 23 + [10])
    assert "23시" in line and "█" in line


# ---- CLI 비유한/극단 입력 방어 ----

def test_cli_gap_min_nan_rejected(capsys):
    assert main([EXAMPLE, "--gap-min", "nan"]) == 1
    assert "gap-min" in capsys.readouterr().err


def test_cli_gap_min_inf_rejected(capsys):
    assert main([EXAMPLE, "--gap-min", "inf"]) == 1
    assert "오류" in capsys.readouterr().err


def test_cli_tz_offset_nonfinite_rejected(capsys):
    assert main([EXAMPLE, "--tz-offset", "nan"]) == 1
    assert "tz-offset" in capsys.readouterr().err


def test_cli_tz_offset_huge_no_traceback(capsys):
    # 유한하지만 거대한 오프셋 → OverflowError 를 깔끔한 오류로 처리 (트레이스백 X)
    rc = main([EXAMPLE, "--tz-offset", "1e18"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "오류" in err and "Traceback" not in err


def test_cli_retention_huge_n_no_traceback(capsys):
    rc = main([EXAMPLE, "--retention", "3000000"])
    assert rc == 0
    assert "Traceback" not in capsys.readouterr().err


# ---- --out 내용 정확성 (약한 assert 보강) ----

def test_cli_out_file_content_matches_stdout(tmp_path, capsys):
    # stdout 렌더와 파일 저장 내용이 (개행 제외) 동일해야 한다
    main([EXAMPLE])
    stdout_report = capsys.readouterr().out.rstrip("\n")
    out = tmp_path / "r.txt"
    assert main([EXAMPLE, "--out", str(out)]) == 0
    saved = out.read_text(encoding="utf-8").rstrip("\n")
    assert saved == stdout_report
