"""비교/일괄/CSV 출력 및 다중 파일 CLI 모드 검증."""

import csv
import io
import json
import math
import os

import pytest

from hrvkit import analyze_rr, cli
from hrvkit.analyze import FLAT_COLUMNS, flat_metrics
from hrvkit.report import metrics_to_csv, render_batch_table, render_comparison

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")
REST = os.path.join(EXAMPLES, "resting.csv")
SLOW = os.path.join(EXAMPLES, "slow_breathing.csv")


def _mk(seed, mean=800, amp=20):
    return [mean + amp * math.sin(i / 3.0) for i in range(120)]


def test_flat_metrics_has_all_columns():
    res = analyze_rr(_mk(1), source="a.csv")
    flat = flat_metrics(res)
    for key, _ in FLAT_COLUMNS:
        assert key in flat


def test_metrics_to_csv_roundtrip():
    r1 = analyze_rr(_mk(1), source="a.csv")
    r2 = analyze_rr(_mk(2, mean=900), source="b.csv")
    text = metrics_to_csv([r1, r2])
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == [k for k, _ in FLAT_COLUMNS]
    assert len(rows) == 3  # 헤더 + 2행
    assert rows[1][0] == "a.csv"
    # rmssd 열이 수치로 파싱됨
    idx = [k for k, _ in FLAT_COLUMNS].index("rmssd")
    assert float(rows[1][idx]) > 0


def test_metrics_to_csv_handles_nan_inf():
    res = analyze_rr([800.0] * 60, source="flat.csv")  # 분산 0 → inf/nan
    text = metrics_to_csv([res])
    rows = list(csv.reader(io.StringIO(text)))
    assert len(rows) == 2
    cols = rows[0]
    row = rows[1]
    # 분산 0: LF/HF = inf, ln_hf = NaN 이 문자열로 표기되어야 함(빈칸/토큰 아님)
    lfhf = row[cols.index("lf_hf_ratio")]
    assert lfhf in ("inf", "-inf")
    assert row[cols.index("ln_hf")] == "NaN"


def test_render_comparison_direction_and_text():
    rest, _ = cli.load_series(REST)
    slow, _ = cli.load_series(SLOW)
    b = analyze_rr(rest, source=REST)
    v = analyze_rr(slow, source=SLOW)
    out = render_comparison(b, v)
    assert "짝지은 비교" in out
    assert "RMSSD" in out
    assert "부교감" in out
    # 느린 호흡이 부교감 방향이므로 대부분 ↑부교감
    assert out.count("↑부교감") >= 5


def test_render_batch_table_lists_files():
    r1 = analyze_rr(_mk(1), source="/x/aaa.csv")
    r2 = analyze_rr(_mk(2), source="/y/bbb.csv")
    out = render_batch_table([r1, r2])
    assert "aaa.csv" in out and "bbb.csv" in out
    assert "RMSSD" in out


def test_cli_compare_text(capsys):
    rc = cli.main([REST, SLOW, "--compare"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "짝지은 비교" in out


def test_cli_compare_wrong_count(capsys):
    rc = cli.main([REST, "--compare"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "2개" in err


def test_cli_batch_text(capsys):
    rc = cli.main([REST, SLOW])
    out = capsys.readouterr().out
    assert rc == 0
    assert "일괄 요약" in out


def test_cli_batch_csv(capsys):
    rc = cli.main([REST, SLOW, "--format", "csv"])
    out = capsys.readouterr().out
    assert rc == 0
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0][0] == "source"
    assert len(rows) == 3


def test_cli_single_csv(capsys):
    rc = cli.main([REST, "--format", "csv"])
    out = capsys.readouterr().out
    assert rc == 0
    rows = list(csv.reader(io.StringIO(out)))
    assert len(rows) == 2


def test_cli_compare_json(capsys):
    rc = cli.main([REST, SLOW, "--compare", "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["mode"] == "compare"
    assert "baseline" in data and "intervention" in data


def test_cli_batch_json(capsys):
    rc = cli.main([REST, SLOW, "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "batch"
    assert len(data["files"]) == 2


def test_cli_batch_error_names_file(capsys, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("", encoding="utf-8")   # 빈 파일
    rc = cli.main([REST, str(bad)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "bad.csv" in err


def test_cli_timestamps_flag(capsys, tmp_path):
    p = tmp_path / "ts.csv"
    p.write_text("t\n0.0\n0.8\n1.62\n2.4\n3.25\n", encoding="utf-8")
    rc = cli.main([str(p), "--timestamps", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["input_meta"]["beat_times"] is True
