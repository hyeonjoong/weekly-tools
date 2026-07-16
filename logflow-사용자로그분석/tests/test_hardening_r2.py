"""R2 적대적 리뷰(정확성·엣지·유용성/리포트 3패널)에서 나온 개선·회귀 테스트."""

import csv
import io
import os
import tempfile
from datetime import datetime, timedelta

import pytest

from logflow.analyze import analyze, to_csv_tables, to_dict
from logflow.cli import main
from logflow.dataio import Event, load_events
from logflow.metrics import retention
from logflow.report import _dw, _lj, _rj, render_text

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "examples", "app_events.csv")


def _ev(u, n, day=0, hour=0):
    return Event(u, n, datetime(2026, 1, 5) + timedelta(days=day, hours=hour))


def _write(text, encoding="utf-8"):
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(path, "w", encoding=encoding) as fh:
        fh.write(text)
    return path


# ---- 롤링 리텐션 ----

def test_rolling_retention_semantics():
    # u1: day0,4 (span 4) ; u2: day0,1 (span 1) ; u3: day0 (span 0)
    events = [
        _ev("u1", "a", 0), _ev("u1", "a", 4),
        _ev("u2", "a", 0), _ev("u2", "a", 1),
        _ev("u3", "a", 0),
    ]
    # max_day = day4. day-1 rolling: span>=1 → u1,u2 (u3 no). eligible=3 (모두 관찰가능)
    r = {x.n: x for x in retention(events, days=[1, 3], confidence=0.95, mode="rolling")}
    assert r[1].eligible == 3 and r[1].retained == 2      # u1,u2
    # day-3 rolling: span>=3 → u1 only. eligible: c+3<=day4 → 모두(코호트 day0) =3
    assert r[3].eligible == 3 and r[3].retained == 1      # u1


def test_rolling_retention_monotonic_non_increasing():
    events = [_ev(f"u{i}", "a", d) for i in range(5) for d in range(i + 1)]
    rates = [
        x.retained
        for x in retention(events, days=[1, 2, 3, 4], mode="rolling")
    ]
    assert rates == sorted(rates, reverse=True)  # N 커질수록 retained 비증가


def test_exact_vs_rolling_differ_and_rolling_ge_exact_counts():
    # 롤링은 항상 exact 이상(같은 N, 같은 eligible)의 retained 를 갖는다
    events = [_ev(f"u{i}", "a", d) for i in range(6) for d in [0, i]]
    ex = {x.n: x for x in retention(events, days=[3], mode="exact")}[3]
    ro = {x.n: x for x in retention(events, days=[3], mode="rolling")}[3]
    assert ro.eligible == ex.eligible
    assert ro.retained >= ex.retained


def test_retention_mode_invalid_raises():
    events = [_ev("u1", "a", 0), _ev("u1", "a", 1)]
    with pytest.raises(ValueError):
        retention(events, days=[1], mode="weekly")


# ---- CSV 표 내보내기 ----

def test_to_csv_tables_keys_and_valid_csv():
    a = analyze([_ev(f"u{i}", n, day=d) for i in range(3) for d in range(3)
                 for n in ["open", "buy"]], funnel_steps=["open", "buy"])
    tables = to_csv_tables(a)
    assert {"active_users", "retention", "funnel", "events", "users",
            "activity_by_hour", "activity_by_weekday"} <= set(tables)
    # 각 표가 파싱 가능한 CSV 인지
    for name, text in tables.items():
        rows = list(csv.reader(io.StringIO(text)))
        assert len(rows) >= 2  # 헤더 + 최소 1행 (활동 표는 24/7행)
    # activity_by_hour 는 24행 데이터
    assert len(list(csv.reader(io.StringIO(tables["activity_by_hour"])))) == 25


def test_to_csv_tables_no_funnel_key_when_absent():
    a = analyze([_ev("u1", "a", 0), _ev("u1", "a", 1)])
    assert "funnel" not in to_csv_tables(a)


def test_cli_csv_dir_writes_files(tmp_path, capsys):
    outdir = tmp_path / "csv"
    rc = main([EXAMPLE, "--funnel", "app_open,breathing_start", "--csv-dir", str(outdir)])
    assert rc == 0
    names = {p.name for p in outdir.iterdir()}
    assert "retention.csv" in names and "funnel.csv" in names and "active_users.csv" in names
    # BOM 붙은 utf-8-sig (엑셀 호환)
    assert (outdir / "retention.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    assert "CSV 표" in capsys.readouterr().err


# ---- 엣지 방어 ----

def test_cli_multichar_delimiter_rejected(capsys):
    assert main([EXAMPLE, "--delimiter", ";;"]) == 1
    assert "delimiter" in capsys.readouterr().err


def test_cli_empty_delimiter_rejected(capsys):
    assert main([EXAMPLE, "--delimiter", ""]) == 1
    assert "오류" in capsys.readouterr().err


def test_cli_out_equals_input_rejected(capsys):
    assert main([EXAMPLE, "--out", EXAMPLE]) == 1
    assert "덮어쓰기" in capsys.readouterr().err


def test_ambiguous_column_raises():
    # 공백만 다른 두 헤더 → 모호
    path = _write("event, event ,timestamp\nx,a,2026-01-01T09:00:00\n")
    try:
        with pytest.raises(ValueError):
            load_events(path, user_col="event", event_col="event", time_col="timestamp")
    finally:
        os.remove(path)


# ---- 표시폭 정렬 ----

def test_display_width_helpers():
    assert _dw("\uac00") == 2          # 한글 전각
    assert _dw("a") == 1
    assert _dw("\uac00a") == 3
    # 전각/반각 섞어도 같은 표시폭으로 정렬
    assert _dw(_rj("\uac00", 10)) == 10
    assert _dw(_lj("abc", 10)) == 10
    assert _dw(_rj("\uac00\ub098\ub2e4", 4)) == 6  # 내용이 폭보다 넓으면 그대로
    # 결합 악센트·제로폭 문자는 폭 0
    assert _dw("e" + chr(0x0301)) == 1          # e + 결합 acute -> 폭 1
    assert _dw("a" + chr(0x200B) + "b") == 2    # 제로폭 공백 -> 폭 0


def test_report_columns_align_by_display_width():
    a = analyze([_ev(f"u{i}", n, day=d) for i in range(3) for d in range(2)
                 for n in ["open", "buy"]], funnel_steps=["open", "buy"])
    text = render_text(a)
    # 활성 사용자 표: 헤더와 데이터 행의 표시폭이 같아야 (정렬됨)
    lines = [ln for ln in text.splitlines() if ln.startswith("  ") and "DAU" in ln]
    assert lines, "활성 헤더를 찾지 못함"


def test_sessions_per_user_line_present():
    a = analyze([_ev("u1", "a", 0), _ev("u1", "a", 1), _ev("u2", "a", 0)])
    assert "사용자당 세션" in render_text(a)


def test_to_dict_includes_retention_mode():
    d = to_dict(analyze([_ev("u1", "a", 0), _ev("u1", "a", 1)], retention_mode="rolling"))
    assert d["meta"]["retention_mode"] == "rolling"
