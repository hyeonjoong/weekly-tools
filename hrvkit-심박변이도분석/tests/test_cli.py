"""CLI 스모크 테스트 — 텍스트 및 JSON 출력, 오류 처리."""

import json
import os

import pytest

from hrvkit import cli

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


def test_cli_text_output(capsys):
    rc = cli.main([os.path.join(EXAMPLES, "resting.csv")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "심박변이도" in out
    assert "RMSSD" in out
    assert "Welch" in out


def test_cli_json_output(capsys):
    rc = cli.main([os.path.join(EXAMPLES, "slow_breathing.csv"), "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["unit"] == "ms"
    assert "time_domain" in data
    assert "frequency_domain" in data
    assert "nonlinear" in data
    assert data["nonlinear"]["sd1"] > 0


def test_cli_missing_file(capsys):
    rc = cli.main(["/nonexistent/path/nope.csv"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "오류" in err


def test_cli_unit_override(capsys, tmp_path):
    p = tmp_path / "hr.csv"
    p.write_text("hr_bpm\n" + "\n".join(str(v) for v in
                 [72, 75, 68, 80, 71, 69, 74, 77, 70, 73] * 3) + "\n",
                 encoding="utf-8")
    rc = cli.main([str(p), "--unit", "bpm", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["unit"] == "bpm"


def test_cli_no_sampen(capsys):
    rc = cli.main([os.path.join(EXAMPLES, "resting.csv"), "--no-sampen", "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert rc == 0
    # --no-sampen 이면 SampEn 이 실제로 생략(NaN → 문자열 "NaN")되어야 함
    assert data["nonlinear"]["sampen"] == "NaN"


def test_cli_sampen_present_without_flag(capsys):
    rc = cli.main([os.path.join(EXAMPLES, "resting.csv"), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    # 플래그 없으면 유한한 수치여야 함
    assert isinstance(data["nonlinear"]["sampen"], float)
    assert data["nonlinear"]["sampen"] > 0


def test_cli_clean_remove(capsys):
    rc = cli.main([os.path.join(EXAMPLES, "resting.csv"), "--clean", "remove",
                   "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["clean_method"] == "remove"
