"""CLI 의 군 비교(--group-col 등) 와 입력 형식(--format) 옵션 검증."""

import gzip
import json

import pytest

from logflow.cli import main

CSV = (
    "user_id,event,timestamp,arm\n"
    "a1,open,2026-01-01T09:00:00,중재군\n"
    "a2,open,2026-01-01T09:10:00,중재군\n"
    "a1,open,2026-01-02T09:00:00,중재군\n"
    "a2,open,2026-01-02T09:10:00,중재군\n"
    "b1,open,2026-01-01T09:20:00,대조군\n"
    "b2,open,2026-01-01T09:30:00,대조군\n"
    "b1,open,2026-01-02T09:20:00,대조군\n"
    "a1,open,2026-01-05T09:00:00,중재군\n"
)


def write(tmp_path, text=CSV, name="log.csv"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_group_section_rendered(tmp_path, capsys):
    assert main([write(tmp_path), "--group-col", "arm", "--ref-group", "대조군"]) == 0
    out = capsys.readouterr().out
    assert "[ 군 비교 ]" in out
    assert "기준군: 대조군" in out
    assert "중재군" in out
    assert "p(Holm)" in out
    assert "Holm–Bonferroni" in out
    assert "p(Holm) 으로 하세요" in out


def test_no_group_section_without_flag(tmp_path, capsys):
    assert main([write(tmp_path)]) == 0
    assert "[ 군 비교 ]" not in capsys.readouterr().out


def test_group_json_output(tmp_path, capsys):
    assert main([write(tmp_path), "--group-col", "arm", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["groups"]["groups"] == ["대조군", "중재군"]
    assert payload["groups"]["arms"][0]["n_users"] == 2


def test_json_groups_null_without_flag(tmp_path, capsys):
    assert main([write(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["groups"] is None


def test_unknown_group_column_errors(tmp_path, capsys):
    assert main([write(tmp_path), "--group-col", "없는열"]) == 1
    assert "필수 열이 없습니다" in capsys.readouterr().err


def test_unknown_ref_group_errors(tmp_path, capsys):
    assert main([write(tmp_path), "--group-col", "arm", "--ref-group", "없는군"]) == 1
    assert "기준군" in capsys.readouterr().err


def test_ref_group_requires_group_col(tmp_path, capsys):
    assert main([write(tmp_path), "--ref-group", "대조군"]) == 1
    assert "--group-col 과 함께" in capsys.readouterr().err


def test_empty_group_col_rejected(tmp_path, capsys):
    assert main([write(tmp_path), "--group-col", "  "]) == 1
    assert "빈 값" in capsys.readouterr().err


def test_churn_days_must_be_positive(tmp_path, capsys):
    assert main([write(tmp_path), "--group-col", "arm", "--churn-days", "0"]) == 1
    assert "--churn-days" in capsys.readouterr().err


def test_churn_days_changes_survival_output(tmp_path, capsys):
    main([write(tmp_path), "--group-col", "arm", "--churn-days", "2", "--json"])
    a = json.loads(capsys.readouterr().out)["groups"]["survival"]
    main([write(tmp_path), "--group-col", "arm", "--churn-days", "30", "--json"])
    b = json.loads(capsys.readouterr().out)["groups"]["survival"]
    assert sum(a["n_churned"].values()) > sum(b["n_churned"].values())


def test_csv_dir_writes_group_tables(tmp_path, capsys):
    out = tmp_path / "표"
    assert main([write(tmp_path), "--group-col", "arm", "--csv-dir", str(out)]) == 0
    names = {p.name for p in out.iterdir()}
    assert {"group_summary.csv", "group_tests.csv"} <= names
    text = (out / "group_summary.csv").read_text(encoding="utf-8-sig")
    assert "중재군" in text


def test_format_jsonl_via_cli(tmp_path, capsys):
    rows = [
        {"user_id": "u1", "event": "open", "timestamp": "2026-01-01T09:00:00"},
        {"user_id": "u2", "event": "open", "timestamp": "2026-01-02T09:00:00"},
    ]
    p = tmp_path / "log.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert main([str(p), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["overview"]["total_events"] == 2


def test_gzip_input_via_cli(tmp_path, capsys):
    p = tmp_path / "log.csv.gz"
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        fh.write(CSV)
    assert main([str(p), "--group-col", "arm", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["overview"]["total_events"] == 8


def test_bad_format_value_rejected_by_argparse(tmp_path):
    with pytest.raises(SystemExit):
        main([write(tmp_path), "--format", "parquet"])


def test_conflicting_group_labels_are_reported(tmp_path, capsys):
    text = CSV + "a1,open,2026-01-06T09:00:00,대조군\n"
    assert main([write(tmp_path, text), "--group-col", "arm"]) == 0
    out = capsys.readouterr().out
    assert "서로 다른 군 라벨" in out


def test_ungrouped_users_reported(tmp_path, capsys):
    text = CSV + "z9,open,2026-01-01T09:00:00,\nz9,open,2026-01-02T09:00:00,\n"
    assert main([write(tmp_path, text), "--group-col", "arm"]) == 0
    out = capsys.readouterr().out
    assert "군 라벨이 없는 사용자 1명" in out


def test_three_arms_note_in_report(tmp_path, capsys):
    text = CSV + "c1,open,2026-01-01T09:00:00,제3군\nc1,open,2026-01-02T09:00:00,제3군\n"
    assert main([write(tmp_path, text), "--group-col", "arm"]) == 0
    out = capsys.readouterr().out
    assert "검정은 생략" in out
    assert "p(Holm)" not in out


def test_group_report_lines_are_not_absurdly_wide(tmp_path, capsys):
    """군 비교 표가 터미널을 넘지 않도록 (표시폭 기준)."""
    from logflow.report import _dw

    main([write(tmp_path), "--group-col", "arm", "--funnel", "open"])
    out = capsys.readouterr().out
    section = out.split("[ 군 비교 ]")[1].split("[ 상위 사용자 ]")[0]
    lines = [l for l in section.splitlines() if l.strip()]
    assert max((_dw(l) for l in lines), default=0) <= 110
