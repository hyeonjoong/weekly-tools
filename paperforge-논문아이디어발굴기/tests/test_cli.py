"""CLI-level tests (argument validation, exit codes) — fully offline."""
import json

import pytest

from paperforge.cli import main


def _write(tmp_path, data):
    p = tmp_path / "m.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def test_top_zero_is_rejected(tmp_path, capsys):
    path = _write(tmp_path, {"datasets": [{"modality": "eeg", "n": 30}]})
    with pytest.raises(SystemExit) as exc:
        main([path, "--top", "0"])
    assert exc.value.code == 2  # argparse usage error
    # Disambiguate from manifest/FileNotFound (also exit 2) via the message.
    assert "--top" in capsys.readouterr().err


def test_top_negative_is_rejected(tmp_path, capsys):
    path = _write(tmp_path, {"datasets": [{"modality": "eeg", "n": 30}]})
    with pytest.raises(SystemExit) as exc:
        main([path, "--top", "-3"])
    assert exc.value.code == 2
    assert "--top" in capsys.readouterr().err


def test_missing_file_exit_code(tmp_path):
    assert main([str(tmp_path / "nope.json")]) == 2


def test_happy_path_exit_zero(tmp_path, capsys):
    path = _write(tmp_path, {
        "datasets": [
            {"modality": "eeg", "n": 90, "variables": ["alpha_power"]},
            {"modality": "respiration", "n": 90, "variables": ["resp_rate"]},
        ]
    })
    assert main([path, "--top", "2"]) == 0
    out = capsys.readouterr().out
    assert "논문 아이디어 매트릭스" in out


def test_invalid_manifest_exit_code(tmp_path):
    path = _write(tmp_path, {"datasets": []})
    assert main([path]) == 2


def test_out_and_csv_files_written(tmp_path, capsys):
    path = _write(tmp_path, {
        "datasets": [
            {"modality": "eeg", "n": 90, "variables": ["alpha_power"]},
            {"modality": "respiration", "n": 90, "variables": ["resp_rate"]},
        ]
    })
    out = tmp_path / "ideas.md"
    csv = tmp_path / "ideas.csv"
    assert main([path, "--out", str(out), "--csv", str(csv)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# 논문 아이디어 매트릭스")
    assert csv.read_text(encoding="utf-8").startswith("rank,idea_id")


def test_unwritable_out_path_clean_error(tmp_path, capsys):
    path = _write(tmp_path, {"datasets": [
        {"modality": "eeg", "n": 90, "variables": ["alpha_power"]},
        {"modality": "respiration", "n": 90, "variables": ["resp_rate"]},
    ]})
    bad = tmp_path / "no_such_dir" / "ideas.md"
    assert main([path, "--out", str(bad)]) == 2
    captured = capsys.readouterr()
    assert "출력 파일 쓰기 오류" in captured.err
    # The report must NOT be printed to stdout before the write fails.
    assert captured.out == ""
