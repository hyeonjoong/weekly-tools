"""CLI end-to-end 테스트 (오프라인, 번들 예시 사용)."""
import json
import os

from surveyscan.cli import run

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
CSV = os.path.join(ROOT, "examples", "sleep_survey.csv")
CFG = os.path.join(ROOT, "examples", "sleep_config.json")


def test_cli_text_output(capsys):
    rc = run([CSV, "--config", CFG, "--id-col", "ID"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "설문 응답 분석 리포트" in out
    assert "Cronbach" in out
    assert "불면증상(ISI)" in out


def test_cli_json_output(capsys):
    rc = run([CSV, "--config", CFG, "--id-col", "ID", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["n_respondents"] == 40
    assert len(data["subscales"]) == 2
    # scores 리스트는 JSON에서 제거되어야 함
    assert "scores" not in data["subscales"][0]


def test_cli_missing_file(capsys):
    rc = run(["/no/such/file.csv"])
    assert rc == 2


def test_cli_auto_config(capsys):
    # config 없이 실행: ID 제외하면 숫자 컬럼 전체가 '전체' 척도
    rc = run([CSV, "--id-col", "ID"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "전체" in out
