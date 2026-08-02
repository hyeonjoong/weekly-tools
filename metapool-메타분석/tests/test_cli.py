"""CLI 종단 시험 — 종료코드, 출력 형식, 번들 예제, 오류 메시지."""

import json
import os

import pytest

from metapool.cli import main

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


def ex(name):
    return os.path.join(EXAMPLES, name)


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------
# 번들 예제 (실행.command 가 쓰는 파일들)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["breathing_isi_smd.csv", "adherence_or.csv", "published_effects.csv"]
)
def test_bundled_examples_run_cleanly(name, capsys):
    assert main([ex(name)]) == 0
    out = capsys.readouterr().out
    assert "메타분석 결과" in out
    assert "통합 효과" in out
    assert "논문에 붙일 문장" in out


def test_smd_example_numbers_are_stable(capsys):
    """번들 예제의 핵심 수치 회귀 시험 (손계산 검증된 값)."""
    assert main([ex("breathing_isi_smd.csv"), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["measure"] == "smd"
    assert data["k"] == 8
    assert data["total_n"] == 691
    assert data["random_effects"]["estimate"] == pytest.approx(-0.5197, abs=5e-4)
    assert data["fixed_effect"]["estimate"] == pytest.approx(-0.5370, abs=5e-4)
    assert data["heterogeneity"]["I2_percent"] == pytest.approx(35.4, abs=0.2)
    assert data["subgroup_test"]["q_between"] == pytest.approx(8.97, abs=0.02)


def test_or_example_back_transforms_to_odds_ratio(capsys):
    assert main([ex("adherence_or.csv"), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["measure"] == "or"
    assert data["scale"] == "log"
    # 로그척도 추정치와 지수변환 값이 서로 일치해야 한다
    import math

    assert data["random_effects"]["estimate_exp"] == pytest.approx(
        math.exp(data["random_effects"]["estimate"]), rel=1e-12
    )
    assert 1.3 < data["random_effects"]["estimate_exp"] < 2.0


# --------------------------------------------------------------------------
# 출력 형식
# --------------------------------------------------------------------------


def test_json_output_is_valid_and_complete(capsys):
    assert main([ex("published_effects.csv"), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    for key in (
        "measure", "k", "studies", "fixed_effect", "random_effects",
        "heterogeneity", "prediction_interval", "leave_one_out", "egger_test",
    ):
        assert key in data
    assert len(data["studies"]) == data["k"]
    total = sum(s["weight_random_pct"] for s in data["studies"])
    assert total == pytest.approx(100.0, abs=1e-9)


def test_markdown_output_has_tables(capsys):
    assert main([ex("published_effects.csv"), "--md"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# 메타분석 결과")
    assert "| 연구 |" in out and "|---|" in out


def test_out_file_infers_format_from_extension(tmp_path, capsys):
    target = str(tmp_path / "result.json")
    assert main([ex("published_effects.csv"), "-o", target]) == 0
    with open(target, encoding="utf-8") as fh:
        assert json.load(fh)["k"] == 6
    md_target = str(tmp_path / "result.md")
    assert main([ex("published_effects.csv"), "-o", md_target]) == 0
    with open(md_target, encoding="utf-8") as fh:
        assert fh.read().startswith("# 메타분석 결과")


def test_out_file_reports_path_on_stderr(tmp_path, capsys):
    target = str(tmp_path / "r.txt")
    assert main([ex("published_effects.csv"), "-o", target]) == 0
    assert target in capsys.readouterr().err


def test_json_and_md_together_is_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        main([ex("published_effects.csv"), "--json", "--md"])
    assert exc.value.code == 2


# --------------------------------------------------------------------------
# 옵션
# --------------------------------------------------------------------------


def test_measure_can_be_forced(capsys):
    assert main([ex("adherence_or.csv"), "--measure", "rr", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["measure"] == "rr"
    assert main([ex("adherence_or.csv"), "--measure", "rd", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["measure"] == "rd" and data["scale"] == "raw"


def test_forcing_incompatible_measure_fails_cleanly(capsys):
    assert main([ex("published_effects.csv"), "--measure", "smd"]) == 1
    assert "필요한 열이 없습니다" in capsys.readouterr().err


def test_paule_mandel_option(capsys):
    assert main([ex("breathing_isi_smd.csv"), "--tau2", "PM", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["random_effects"]["tau2_method"] == "PM"


def test_no_hksj_switches_to_z(capsys):
    assert main([ex("breathing_isi_smd.csv"), "--no-hksj", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["random_effects"]["ci_method"] == "z"
    assert data["random_effects"]["statistic_type"] == "z"


def test_fixed_model_changes_reported_sentence(capsys):
    assert main([ex("published_effects.csv"), "--model", "fixed"]) == 0
    assert "고정효과 모형으로" in capsys.readouterr().out


def test_conf_level_changes_interval_width(capsys):
    assert main([ex("published_effects.csv"), "--json"]) == 0
    wide95 = json.loads(capsys.readouterr().out)["random_effects"]
    assert main([ex("published_effects.csv"), "--conf", "0.99", "--json"]) == 0
    wide99 = json.loads(capsys.readouterr().out)["random_effects"]
    assert (wide99["ci_high"] - wide99["ci_low"]) > (wide95["ci_high"] - wide95["ci_low"])


def test_invalid_conf_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        main([ex("published_effects.csv"), "--conf", "1.5"])
    assert exc.value.code == 2


def test_negative_cc_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        main([ex("adherence_or.csv"), "--cc", "-1"])
    assert exc.value.code == 2


def test_sections_can_be_switched_off(capsys):
    assert main([ex("published_effects.csv"), "--no-forest", "--no-sensitivity", "--no-bias"]) == 0
    out = capsys.readouterr().out
    assert "숲그림" not in out
    assert "leave-one-out" not in out
    assert "Egger" not in out


def test_sort_by_effect(capsys):
    assert main([ex("published_effects.csv"), "--sort", "effect", "--json"]) == 0
    effects = [s["effect"] for s in json.loads(capsys.readouterr().out)["studies"]]
    assert effects == sorted(effects)


def test_map_option_on_custom_headers(tmp_path, capsys):
    path = write(tmp_path, "custom.csv", "논문,크기,오차\nA,0.5,0.1\nB,0.3,0.2\nC,0.7,0.15\n")
    assert main([path, "--map", "크기=effect", "--map", "오차=se", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["k"] == 3


def test_bad_map_syntax_fails(tmp_path, capsys):
    path = write(tmp_path, "c.csv", "study,effect,se\nA,0.5,0.1\n")
    assert main([path, "--map", "effectse"]) == 1
    assert "--map" in capsys.readouterr().err


def test_log_input_converts_ratios_and_back_transforms(tmp_path, capsys):
    import math

    path = write(
        tmp_path, "ratio.csv",
        "study,effect,ci_low,ci_high\nA,2.0,1.2,3.3\nB,1.5,1.0,2.25\nC,2.5,1.4,4.5\n",
    )
    assert main([path, "--measure", "generic", "--log-input", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["scale"] == "log"                      # 로그 척도임을 밝혀야 한다
    assert data["studies"][0]["effect"] == pytest.approx(math.log(2.0), rel=1e-12)
    assert data["studies"][0]["se"] == pytest.approx(
        (math.log(3.3) - math.log(1.2)) / (2 * 1.959963984540054), rel=1e-12
    )
    # 되돌린 값이 함께 보고돼야 한다 (사용자는 비(ratio)로 읽는다)
    assert data["random_effects"]["estimate_exp"] == pytest.approx(
        math.exp(data["random_effects"]["estimate"]), rel=1e-12
    )
    assert 1.0 < data["random_effects"]["estimate_exp"] < 3.0


def test_log_input_rejects_nonpositive(tmp_path, capsys):
    path = write(tmp_path, "neg.csv", "study,effect,ci_low,ci_high\nA,-2.0,-3,-1\n")
    assert main([path, "--measure", "generic", "--log-input"]) == 1


# --------------------------------------------------------------------------
# 오류 · 경계 상황
# --------------------------------------------------------------------------


def test_missing_file_exit_code(capsys):
    assert main(["/definitely/not/here.csv"]) == 1
    assert "찾을 수 없습니다" in capsys.readouterr().err


def test_all_rows_invalid_exit_code(tmp_path, capsys):
    path = write(tmp_path, "bad.csv", "study,effect,se\nA,abc,0.1\nB,xyz,0.2\n")
    assert main([path]) == 1
    err = capsys.readouterr().err
    assert "유효한 연구가 한 편도 없습니다" in err


def test_single_study_still_produces_report(tmp_path, capsys):
    path = write(tmp_path, "one.csv", "study,effect,se\nA,0.5,0.1\n")
    assert main([path]) == 0
    out = capsys.readouterr().out
    assert "연구 수 (k) : 1" in out
    assert "── 출판편향" not in out and "── 민감도" not in out
    assert "95% 예측구간" not in out          # k<3 이면 예측구간을 계산하지 않는다
    assert "유효한 연구가 1편뿐" in out


def test_two_studies_skip_egger_and_loo(tmp_path, capsys):
    path = write(tmp_path, "two.csv", "study,effect,se\nA,0.5,0.1\nB,0.3,0.2\n")
    assert main([path]) == 0
    out = capsys.readouterr().out
    assert "── 출판편향" not in out           # 섹션 자체가 없어야 한다
    assert "── 민감도" not in out
    assert "Egger 비대칭 검정을 생략" in out   # 대신 왜 생략했는지 경고로 알려준다


def test_partially_bad_rows_are_dropped_with_warning(tmp_path, capsys):
    path = write(
        tmp_path, "mix.csv",
        "study,effect,se\nA,0.5,0.1\nB,,0.2\nC,0.7,0.15\nD,0.2,0\n",
    )
    assert main([path]) == 0
    out = capsys.readouterr().out
    assert "연구 수 (k) : 2" in out
    assert "경고" in out and "제외" in out


def test_zero_cells_trigger_continuity_warning(tmp_path, capsys):
    path = write(
        tmp_path, "zero.csv",
        "study,events1,n1,events2,n2\nA,0,20,6,20\nB,3,25,8,25\nC,5,30,9,30\n",
    )
    assert main([path]) == 0
    out = capsys.readouterr().out
    assert "연속성 보정" in out


def test_version_flag():
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0


def test_help_lists_input_formats(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "events1" in out and "--map" in out
