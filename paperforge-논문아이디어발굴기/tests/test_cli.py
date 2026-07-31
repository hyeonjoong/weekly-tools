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


@pytest.mark.parametrize("flag,value", [
    ("--alpha", "1.5"), ("--alpha", "0"), ("--alpha", "1"), ("--alpha", "inf"),
    ("--alpha", "nan"), ("--alpha", "abc"),
    ("--power", "0"), ("--power", "1"), ("--power", "7"), ("--power", "inf"),
    ("--power", "nan"), ("--power", "-0.2"),
])
def test_out_of_range_alpha_power_are_usage_errors(tmp_path, capsys, flag, value):
    """Rejected by argparse, before anything is computed or printed.

    These were bare `type=float`: `--power 7` reached the report header and
    `--power inf` wrote a literal `Infinity` into --json, which no JSON parser
    accepts.
    """
    path = _write(tmp_path, {"datasets": [
        {"modality": "eeg", "n": 90, "variables": ["alpha_power"]},
        {"modality": "respiration", "n": 90, "variables": ["resp_rate"]},
    ]})
    with pytest.raises(SystemExit) as exc:
        main([path, flag, value])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert flag in captured.err
    assert captured.out == ""


def test_degenerate_effect_size_exits_2_via_evaluate(tmp_path, capsys):
    # evaluate() raises ValueError when the scaled effect underflows -> CLI
    # exit 2 with "분석 오류" and NOTHING on stdout (cli.py's ValueError path).
    path = _write(tmp_path, {"datasets": [
        {"modality": "eeg", "n": 90, "variables": ["alpha_power"]},
        {"modality": "respiration", "n": 90, "variables": ["resp_rate"]},
    ]})
    assert main([path, "--effect-scale", "1e-300"]) == 2
    captured = capsys.readouterr()
    assert "분석 오류" in captured.err
    assert captured.out == ""


def test_json_output_is_always_parseable(tmp_path):
    path = _write(tmp_path, {"datasets": [
        {"modality": "eeg", "n": 90, "variables": ["alpha_power"]},
        {"modality": "respiration", "n": 90, "variables": ["resp_rate"]},
    ]})
    out = tmp_path / "o.json"
    assert main([path, "--json", str(out), "--alpha", "0.001",
                 "--power", "0.999"]) == 0
    text = out.read_text(encoding="utf-8")
    assert "Infinity" not in text and "NaN" not in text
    json.loads(text)  # strict parse: raises on Infinity/NaN


def test_non_tabulated_alpha_power_now_run(tmp_path, capsys):
    # These used to be rejected outright; arbitrary values are the point of the
    # inverse-normal quantile function.
    path = _write(tmp_path, {"datasets": [
        {"modality": "eeg", "n": 90, "variables": ["alpha_power"]},
        {"modality": "respiration", "n": 90, "variables": ["resp_rate"]},
    ]})
    assert main([path, "--alpha", "0.025", "--power", "0.85", "--top", "1"]) == 0
    assert "논문 아이디어 매트릭스" in capsys.readouterr().out


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


# --- new-capability flags ----------------------------------------------------

_TWO_MODALITY = {"datasets": [
    {"modality": "eeg", "n": 90, "variables": ["alpha_power"]},
    {"modality": "respiration", "n": 90, "variables": ["resp_rate"]},
]}


def test_attained_power_column_is_rendered(tmp_path, capsys):
    path = _write(tmp_path, _TWO_MODALITY)
    assert main([path, "--top", "1"]) == 0
    out = capsys.readouterr().out
    assert "현재 검정력" in out
    assert "현재 표본의 검정력" in out


def test_n_tests_flag_reports_corrected_alpha(tmp_path, capsys):
    path = _write(tmp_path, _TWO_MODALITY)
    assert main([path, "--n-tests", "5", "--top", "1"]) == 0
    out = capsys.readouterr().out
    assert "다중비교 보정" in out and "Bonferroni" in out
    with pytest.raises(SystemExit) as exc:
        main([path, "--n-tests", "0"])
    assert exc.value.code == 2
    assert "--n-tests" in capsys.readouterr().err


def test_repeats_and_icc_flags(tmp_path, capsys):
    path = _write(tmp_path, _TWO_MODALITY)
    assert main([path, "--repeats", "3", "--icc", "0.3", "--top", "1"]) == 0
    out = capsys.readouterr().out
    assert "설계효과" in out
    assert "반복측정 환산" in out
    for bad in (["--icc", "1.5"], ["--icc", "-0.1"], ["--repeats", "0"]):
        with pytest.raises(SystemExit) as exc:
            main([path] + bad)
        assert exc.value.code == 2


def test_one_sided_flag(tmp_path, capsys):
    path = _write(tmp_path, _TWO_MODALITY)
    assert main([path, "--one-sided", "--top", "1"]) == 0
    assert "단측검정" in capsys.readouterr().out


def test_linked_n_surfaces_in_report(tmp_path, capsys):
    data = dict(_TWO_MODALITY)
    data["linked_n"] = {"eeg+respiration": 40}
    path = _write(tmp_path, data)
    assert main([path]) == 0  # full report: the capped idea is ranked last
    out = capsys.readouterr().out
    assert "선언된 연결 표본수" in out
    # EEG-respiration coupling needs 85 but only 40 subjects have both.
    assert "표본 부족" in out


def test_list_templates_without_manifest(capsys):
    assert main(["--list-templates"]) == 0
    out = capsys.readouterr().out
    assert "eeg_resp_coupling" in out
    assert "사용 가능한 아이디어 템플릿" in out


def test_missing_manifest_argument_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    assert "매니페스트" in capsys.readouterr().err


def test_custom_template_pack_adds_ideas(tmp_path, capsys):
    pack = tmp_path / "pack.json"
    pack.write_text(json.dumps({"templates": [{
        "id": "my_idea", "title": "내 아이디어", "required": ["뇌파"],
        "optional": [], "hypothesis": "h", "predictors": ["p"],
        "outcomes": ["o"], "analysis": "a", "design": "d",
        "effect": {"type": "correlation", "r": 0.4},
        "journal": "j", "novelty": "n",
    }]}, ensure_ascii=False), encoding="utf-8")
    path = _write(tmp_path, _TWO_MODALITY)
    assert main([path, "--templates", str(pack)]) == 0
    assert "내 아이디어" in capsys.readouterr().out


def test_no_builtin_requires_a_pack(tmp_path, capsys):
    path = _write(tmp_path, _TWO_MODALITY)
    assert main([path, "--no-builtin"]) == 2
    assert "템플릿 오류" in capsys.readouterr().err


def test_bad_template_pack_exits_2_before_reading_manifest(tmp_path, capsys):
    pack = tmp_path / "bad.json"
    pack.write_text('{"templates": [{"id": "x"}]}', encoding="utf-8")
    assert main([str(tmp_path / "does-not-exist.json"), "--templates",
                 str(pack)]) == 2
    err = capsys.readouterr().err
    assert "템플릿 오류" in err  # not "파일을 찾을 수 없습니다"


def test_missing_template_pack_exits_2(tmp_path, capsys):
    path = _write(tmp_path, _TWO_MODALITY)
    assert main([path, "--templates", str(tmp_path / "nope.json")]) == 2
    assert "템플릿 팩을 찾을 수 없습니다" in capsys.readouterr().err


def test_directory_as_manifest_exits_2(tmp_path, capsys):
    assert main([str(tmp_path)]) == 2
    assert "디렉터리" in capsys.readouterr().err
