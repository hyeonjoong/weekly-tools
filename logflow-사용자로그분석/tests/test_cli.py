import os

from logflow.cli import main

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "examples", "app_events.csv")


def test_cli_runs_on_example(capsys):
    rc = main([EXAMPLE, "--funnel", "app_open,breathing_start,breathing_complete,sleep_report"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "logflow" in out
    assert "고유 사용자" in out
    assert "리텐션" in out
    assert "퍼널 전환" in out
    # 검증된 핵심 수치 (손계산과 일치)
    assert "고유 사용자     : 6" in out
    assert "day-1   :   50.0%" in out
    assert "day-7   :   40.0%" in out
    # 손계산 검증값 고정 (회귀 방지)
    assert "세션 수         : 18" in out
    assert "점착도(평균DAU/평균MAU): 31.3%" in out
    assert "2026-01-07         2       6       6" in out   # DAU/WAU/MAU 한 행


def test_cli_negative_top_returns_1(capsys):
    rc = main([EXAMPLE, "--top", "-1"])
    assert rc == 1
    assert "오류" in capsys.readouterr().err


def test_cli_missing_file_returns_2(capsys):
    rc = main(["/no/such/file.csv"])
    assert rc == 2
    assert "찾을 수 없" in capsys.readouterr().err


def test_cli_bad_column_returns_1(tmp_path, capsys):
    p = tmp_path / "x.csv"
    p.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    rc = main([str(p)])
    assert rc == 1
    assert "오류" in capsys.readouterr().err


def test_cli_no_funnel_ok(capsys):
    rc = main([EXAMPLE])
    assert rc == 0
    out = capsys.readouterr().out
    assert "퍼널 전환" not in out   # funnel 미지정 시 섹션 없음
