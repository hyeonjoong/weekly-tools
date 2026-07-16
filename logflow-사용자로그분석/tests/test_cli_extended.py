import json
import os

from logflow.cli import main

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "examples", "app_events.csv")


def test_cli_json_output_is_valid(capsys):
    rc = main([EXAMPLE, "--funnel", "app_open,breathing_start", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    d = json.loads(out)
    assert d["meta"]["tool"] == "logflow"
    assert d["overview"]["unique_users"] == 6
    # retention entries carry CIs
    r1 = {r["n"]: r for r in d["retention"]}[1]
    assert r1["retained"] == 3 and r1["eligible"] == 6
    assert r1["ci"][0] < r1["rate"] < r1["ci"][1]
    # funnel timing present
    assert d["funnel"][1]["median_seconds_from_prev"] is not None
    assert d["activity"]["peak_hour"] == 22


def test_cli_json_no_funnel_null(capsys):
    rc = main([EXAMPLE, "--json"])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["funnel"] is None


def test_cli_out_file_writes(tmp_path, capsys):
    out = tmp_path / "report.txt"
    rc = main([EXAMPLE, "--out", str(out)])
    assert rc == 0
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "logflow" in text
    # stdout should show the saved-path note, not the whole report
    assert "저장" in capsys.readouterr().err


def test_cli_json_to_file_roundtrips(tmp_path):
    out = tmp_path / "r.json"
    rc = main([EXAMPLE, "--json", "--out", str(out)])
    assert rc == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["overview"]["total_events"] == 41


def test_cli_confidence_validation(capsys):
    assert main([EXAMPLE, "--confidence", "1.5"]) == 1
    assert "confidence" in capsys.readouterr().err


def test_cli_confidence_changes_ci_width(capsys):
    main([EXAMPLE, "--json", "--confidence", "0.80"])
    d80 = json.loads(capsys.readouterr().out)
    main([EXAMPLE, "--json", "--confidence", "0.99"])
    d99 = json.loads(capsys.readouterr().out)
    r80 = {r["n"]: r for r in d80["retention"]}[1]
    r99 = {r["n"]: r for r in d99["retention"]}[1]
    assert (r99["ci"][1] - r99["ci"][0]) > (r80["ci"][1] - r80["ci"][0])


def test_cli_dedup_flag(tmp_path, capsys):
    p = tmp_path / "dup.csv"
    p.write_text(
        "user_id,event,timestamp\n"
        "u1,a,2026-01-01T09:00:00\n"
        "u1,a,2026-01-01T09:00:00\n"
        "u2,a,2026-01-02T09:00:00\n",
        encoding="utf-8",
    )
    rc = main([str(p), "--dedup", "--json"])
    assert rc == 0
    cap = capsys.readouterr()
    d = json.loads(cap.out)
    assert d["overview"]["total_events"] == 2
    assert "중복" in cap.err


def test_cli_date_filter_flags(tmp_path, capsys):
    p = tmp_path / "f.csv"
    p.write_text(
        "user_id,event,timestamp\n"
        "u1,a,2026-01-01T09:00:00\n"
        "u1,a,2026-01-05T09:00:00\n"
        "u1,a,2026-01-10T09:00:00\n",
        encoding="utf-8",
    )
    rc = main([str(p), "--from", "2026-01-03", "--to", "2026-01-08", "--json"])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["overview"]["total_events"] == 1


def test_cli_bad_date_returns_1(capsys):
    assert main([EXAMPLE, "--from", "not-a-date"]) == 1
    assert "오류" in capsys.readouterr().err


def test_cli_text_report_shows_ci_and_activity(capsys):
    rc = main([EXAMPLE, "--funnel", "app_open,breathing_start,breathing_complete"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "95%CI" in out
    assert "활동 시간대" in out
    assert "피크 시간대" in out
    assert "중앙값" in out
