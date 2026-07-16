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


def test_json_output_written(tmp_path):
    import json as _json
    path = _write(tmp_path, {
        "datasets": [
            {"modality": "eeg", "n": 90, "variables": ["alpha_power"]},
            {"modality": "respiration", "n": 90, "variables": ["resp_rate"]},
        ]
    })
    jout = tmp_path / "ideas.json"
    assert main([path, "--json", str(jout)]) == 0
    payload = _json.loads(jout.read_text(encoding="utf-8"))
    assert "ideas" in payload and payload["ideas"]


def test_dropout_flag_accepted_and_validated(tmp_path, capsys):
    path = _write(tmp_path, {"datasets": [
        {"modality": "eeg", "n": 90, "variables": ["alpha_power"]},
        {"modality": "respiration", "n": 90, "variables": ["resp_rate"]},
    ]})
    assert main([path, "--dropout", "0.2", "--top", "1"]) == 0
    assert "권장 모집 N" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main([path, "--dropout", "1.0"])
    assert exc.value.code == 2
    assert "--dropout" in capsys.readouterr().err


def test_unsupported_alpha_exits_2_via_evaluate(tmp_path, capsys):
    # evaluate() raises ValueError for an unsupported alpha -> CLI exit 2 with a
    # "분석 오류" message and NOTHING on stdout (covers cli.py's ValueError path).
    path = _write(tmp_path, {"datasets": [
        {"modality": "eeg", "n": 90, "variables": ["alpha_power"]},
        {"modality": "respiration", "n": 90, "variables": ["resp_rate"]},
    ]})
    assert main([path, "--alpha", "0.123"]) == 2
    captured = capsys.readouterr()
    assert "분석 오류" in captured.err
    assert captured.out == ""


def test_unsupported_power_exits_2(tmp_path, capsys):
    path = _write(tmp_path, {"datasets": [
        {"modality": "eeg", "n": 90, "variables": ["alpha_power"]},
        {"modality": "respiration", "n": 90, "variables": ["resp_rate"]},
    ]})
    assert main([path, "--power", "0.85"]) == 2
    assert "분석 오류" in capsys.readouterr().err


def test_effect_scale_flag(tmp_path, capsys):
    path = _write(tmp_path, {"datasets": [
        {"modality": "eeg", "n": 90, "variables": []},
        {"modality": "respiration", "n": 90, "variables": []},
    ]})
    # scale 0.5 -> smaller assumed effect -> larger required N -> underpowered.
    assert main([path, "--effect-scale", "0.5", "--top", "3"]) == 0
    out = capsys.readouterr().out
    assert "표본수 민감도" in out
    with pytest.raises(SystemExit) as exc:
        main([path, "--effect-scale", "0"])
    assert exc.value.code == 2
    assert "--effect-scale" in capsys.readouterr().err


def test_csv_manifest_via_cli(tmp_path, capsys):
    p = tmp_path / "inv.csv"
    p.write_text("modality,n,variables\neeg,90,alpha_power\n"
                 "respiration,90,resp_rate\n", encoding="utf-8")
    assert main([str(p), "--top", "1"]) == 0
    assert "논문 아이디어 매트릭스" in capsys.readouterr().out


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
