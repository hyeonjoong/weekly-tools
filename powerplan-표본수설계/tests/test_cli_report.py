"""CLI와 출력(텍스트/Markdown/JSON) 검증 — 종료코드·문장·파일저장까지."""

import json
import math
import os

import pytest

from powerplan.cli import main
from powerplan.designs import EquivalenceT, OneWayAnova, TwoProportions, TwoSampleT
from powerplan.precision import icc_plan, loa_plan
from powerplan.report import protocol_sentences, render_json, render_markdown, render_text
from powerplan.solve import Adjustments, make_plan

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(os.path.dirname(HERE), "examples")
SERENE = os.path.join(EXAMPLES, "serene_pilot.csv")
WOWFIT = os.path.join(EXAMPLES, "wowfit_pilot.csv")


def run(argv, capsys):
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --------------------------------------------------------------------------
# 출력 렌더링
# --------------------------------------------------------------------------
def test_text_report_contains_the_numbers_that_matter():
    plan = make_plan(TwoSampleT(0.5), target_power=0.80,
                     adjustments=Adjustments(dropout=0.15))
    text = render_text(plan)
    assert "군당 64명" in text
    assert "군당 76명" in text          # 탈락 보정된 모집 수
    assert "80.1%" in text              # 실제 검정력
    assert "프로토콜용 문장" in text
    assert "Cohen" in text              # 근거 문헌
    assert "비중심 t" in text            # 계산 방법 명시


def test_markdown_is_a_valid_table():
    plan = make_plan(OneWayAnova(0.25, 3), target_power=0.80, sensitivity=True)
    md = render_markdown(plan)
    lines = md.splitlines()
    assert lines[0].startswith("| 항목 |")
    assert lines[1].startswith("|---")
    # 표의 각 행은 파이프로 시작/끝
    table_rows = [ln for ln in lines if ln.startswith("|")]
    assert all(ln.rstrip().endswith("|") for ln in table_rows)
    assert "**군당 53명 × 3군 = 총 159명**" in md
    assert "민감도" in md


def test_json_is_parseable_and_complete():
    plan = make_plan(TwoSampleT(0.5, ratio=2.0), target_power=0.9,
                     adjustments=Adjustments(dropout=0.1, cluster_size=8, cluster_icc=0.02),
                     sensitivity=True)
    payload = json.loads(render_json(plan))
    assert payload["design"]["key"] == "ttest2"
    assert payload["analysis"]["allocation"]["n2"] == 2 * payload["analysis"]["allocation"]["n1"]
    assert payload["enrollment"]["clusters"]["n1"] >= 1
    assert payload["sentences"]["kr"] and payload["sentences"]["en"]
    assert payload["adjustments"]["design_effect"] == pytest.approx(1.14)


def test_json_has_no_nan_or_infinity():
    """allow_nan=False이므로 NaN이 새면 예외가 난다 — 모든 설계에서 안전한지 확인."""
    for plan in (make_plan(TwoSampleT(0.5), target_power=0.8),
                 make_plan(TwoProportions(0.2, 0.4), unit=50),
                 make_plan(EquivalenceT(5, 8), target_power=0.8, sensitivity=True),
                 icc_plan(0.8, 0.2), loa_plan(2.0, 0.5)):
        text = render_json(plan)
        assert "NaN" not in text and "Infinity" not in text
        json.loads(text)


def test_protocol_sentences_are_bilingual_and_specific():
    plan = make_plan(TwoSampleT(0.5), target_power=0.80)
    sentences = protocol_sentences(plan)
    assert "64" in sentences["kr"] and "128" in sentences["kr"]
    assert "64" in sentences["en"] and "two-sample t-test" in sentences["en"]
    # 한글 문장에 영문 효과크기 이름이 섞여도 영문 문장은 순수 영문 라벨을 쓴다
    paired = make_plan(make_paired(), target_power=0.80)
    assert "변화량" not in protocol_sentences(paired)["en"]


def make_paired():
    from powerplan.designs import PairedT
    return PairedT(0.5)


def test_precision_reports_render():
    for plan in (icc_plan(0.8, 0.15, 3), loa_plan(2.0, 0.5)):
        text = render_text(plan)
        assert "필요한 대상자 수" in text
        assert "프로토콜용 문장" in text
        md = render_markdown(plan)
        assert "**필요 대상자**" in md


def test_compute_power_report_shows_gap():
    plan = make_plan(TwoSampleT(0.5), target_power=0.8, unit=30)
    text = render_text(plan)
    assert "검정력" in text and "미달" in text
    assert "목표 달성에 필요" in text


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def test_cli_no_arguments_prints_help(capsys):
    code, out, _ = run([], capsys)
    assert code == 0
    assert "powerplan" in out and "설계" in out


@pytest.mark.parametrize("argv,needle", [
    (["ttest2", "--d", "0.5", "--power", "0.8"], "군당 64명"),
    (["ttest2", "--mean1", "8", "--mean2", "5", "--sd", "6", "--power", "0.8"], "필요한 분석 표본수"),
    (["paired", "--dz", "0.5", "--power", "0.8"], "34명"),
    (["paired", "--diff", "3", "--sd-diff", "6", "--power", "0.8"], "34명"),
    (["onesample", "--mean", "8", "--ref", "6", "--sd", "5", "--power", "0.9"], "필요한 분석 표본수"),
    (["prop2", "--p1", "0.3", "--p2", "0.5", "--power", "0.8"], "군당 93명"),
    (["anova", "--k", "3", "--f", "0.25", "--power", "0.8"], "군당 53명"),
    (["anova", "--k", "3", "--means", "8,6,5", "--sd", "6", "--power", "0.8"], "Cohen's f"),
    (["corr", "--r", "0.3", "--power", "0.8"], "85명"),
    (["corr", "--r", "0.3", "--power", "0.8", "--bias-correct"], "84명"),
    (["noninf", "--margin", "3", "--sd", "8", "--power", "0.8"], "군당 113명"),
    (["equiv", "--margin", "5", "--sd", "8", "--power", "0.8"], "군당 45명"),
    (["icc", "--icc", "0.8", "--width", "0.2"], "51명"),
    (["loa", "--sd-diff", "2", "--half-width", "0.5"], "183명"),
    (["ttest2", "--d", "0.5", "--n", "30"], "47.8%"),
    (["ttest2", "--d", "0.5", "--power", "0.8", "--sensitivity"], "민감도"),
])
def test_cli_designs_succeed(argv, needle, capsys):
    code, out, err = run(argv, capsys)
    assert code == 0, err
    assert needle in out


def test_cli_pilot_two_group(capsys):
    code, out, err = run(["pilot", SERENE, "--value", "isi_week8", "--group", "arm",
                          "--power", "0.8"], capsys)
    assert code == 0, err
    assert "사전연구에서 관측된 효과크기" in out
    assert "device" in out and "sham" in out
    assert "Cohen's d = -0.3086" in out
    assert "신뢰구간이 0을 포함" in out       # 정직한 경고


def test_cli_pilot_paired(capsys):
    code, out, err = run(["pilot", WOWFIT, "--pre", "훈련전_단어인지도",
                          "--post", "훈련후_단어인지도", "--power", "0.8"], capsys)
    assert code == 0, err
    assert "계획 기준: **신뢰구간 하한**" in out
    assert "35명" in out          # 하한 0.4945로 계획한 표본수
    assert "사전-사후 상관" in out


def test_cli_pilot_group_selection(capsys):
    code, out, err = run(["pilot", WOWFIT, "--value", "훈련후_단어인지도", "--group", "군",
                          "--groups", "중재,대조", "--power", "0.8"], capsys)
    assert code == 0, err
    assert "중재" in out and "대조" in out


def test_cli_pilot_argument_errors(capsys):
    code, _, err = run(["pilot", SERENE], capsys)
    assert code == 2 and "--value" in err
    code, _, err = run(["pilot", SERENE, "--value", "isi_week8", "--group", "arm",
                        "--pre", "isi_baseline"], capsys)
    assert code == 2 and "함께 쓸 수 없" in err
    code, _, err = run(["pilot", SERENE, "--pre", "isi_baseline"], capsys)
    assert code == 2 and "--post" in err
    code, _, err = run(["pilot", SERENE, "--value", "isi_week8", "--group", "arm",
                        "--groups", "device"], capsys)
    assert code == 2 and "군A,군B" in err


def test_cli_reports_missing_target(capsys):
    code, _, err = run(["ttest2", "--d", "0.5"], capsys)
    assert code == 2
    assert "--power" in err and "--n" in err


def test_cli_reports_bad_effect_specification(capsys):
    code, _, err = run(["ttest2", "--mean1", "8", "--power", "0.8"], capsys)
    assert code == 2 and "--mean1/--mean2/--sd" in err
    code, _, err = run(["anova", "--k", "3", "--means", "8,6", "--sd", "6", "--power", "0.8"],
                       capsys)
    assert code == 2 and "개수" in err
    code, _, err = run(["paired", "--diff", "3", "--power", "0.8"], capsys)
    assert code == 2 and "--sd-diff" in err


def test_cli_validation_errors_exit_2(capsys):
    bad_cases = [
        ["ttest2", "--d", "0", "--power", "0.8"],
        ["ttest2", "--d", "0.5", "--power", "1.2"],
        ["ttest2", "--d", "0.5", "--power", "0.8", "--alpha", "0"],
        ["ttest2", "--d", "0.5", "--power", "0.8", "--dropout", "1.0"],
        ["ttest2", "--d", "0.5", "--power", "0.8", "--cluster-size", "10"],
        ["ttest2", "--d", "0.5", "--power", "0.8", "--comparisons", "0"],
        ["prop2", "--p1", "0.5", "--p2", "0.5", "--power", "0.8"],
        ["anova", "--k", "1", "--f", "0.3", "--power", "0.8"],
        ["corr", "--r", "1.5", "--power", "0.8"],
        ["icc", "--icc", "1.2", "--width", "0.2"],
        ["loa", "--sd-diff", "-1", "--half-width", "0.5"],
        ["noninf", "--margin", "3", "--sd", "8", "--diff", "-4", "--power", "0.8"],
        ["equiv", "--margin", "3", "--sd", "8", "--diff", "5", "--power", "0.8"],
    ]
    for argv in bad_cases:
        code, out, err = run(argv, capsys)
        assert code == 2, (argv, out)
        assert err.startswith("오류:"), (argv, err)


def test_cli_unreachable_power_is_explained(capsys):
    code, _, err = run(["ttest2", "--d", "0.0001", "--power", "0.99"], capsys)
    assert code == 2
    assert "효과크기" in err or "검정력" in err


def test_cli_json_and_md_formats(capsys):
    code, out, _ = run(["ttest2", "--d", "0.5", "--power", "0.8", "--format", "json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["analysis"]["allocation"]["n1"] == 64
    code, out, _ = run(["ttest2", "--d", "0.5", "--power", "0.8", "--format", "md"], capsys)
    assert code == 0 and out.startswith("| 항목 |")


def test_cli_writes_output_file(tmp_path, capsys):
    target = tmp_path / "plan.md"
    code, out, err = run(["ttest2", "--d", "0.5", "--power", "0.8",
                          "--format", "md", "-o", str(target)], capsys)
    assert code == 0, err
    assert "저장했습니다" in out
    saved = target.read_text(encoding="utf-8")
    assert "군당 64명" in saved


def test_cli_output_file_error_is_reported(capsys):
    code, _, err = run(["ttest2", "--d", "0.5", "--power", "0.8",
                        "-o", "/no/such/dir/plan.txt"], capsys)
    assert code == 2 and "저장할 수 없습니다" in err


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "powerplan" in out


def test_cli_help_lists_every_design(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    for key in ("ttest2", "paired", "onesample", "prop2", "anova", "corr",
                "noninf", "equiv", "icc", "loa", "pilot"):
        assert key in out


def test_cli_pilot_skip_invalid(tmp_path, capsys):
    path = tmp_path / "messy.csv"
    path.write_text("v,g\n1,a\n삼,a\n3,a\n5,b\n7,b\n9,b\n", encoding="utf-8")
    code, _, err = run(["pilot", str(path), "--value", "v", "--group", "g",
                        "--power", "0.8"], capsys)
    assert code == 2 and "숫자로 읽을 수 없는" in err
    code, out, err = run(["pilot", str(path), "--value", "v", "--group", "g",
                          "--power", "0.8", "--skip-invalid"], capsys)
    assert code == 0, err
    assert "결측으로 처리" in out


def test_cli_cluster_design_reports_clusters(capsys):
    code, out, err = run(["ttest2", "--d", "0.4", "--power", "0.8",
                          "--cluster-size", "10", "--cluster-icc", "0.05"], capsys)
    assert code == 0, err
    assert "군집 수" in out
    assert "설계효과" in out


def test_alpha_adjustment_appears_in_output(capsys):
    code, out, err = run(["ttest2", "--d", "0.5", "--power", "0.8",
                          "--comparisons", "3"], capsys)
    assert code == 0, err
    assert "Bonferroni" in out
    code, out, _ = run(["ttest2", "--d", "0.5", "--power", "0.8",
                        "--comparisons", "3", "--alpha-method", "sidak"], capsys)
    assert "Šidák" in out


def test_report_wrapping_keeps_all_words():
    """줄바꿈이 단어를 잃지 않는다."""
    from powerplan.report import _wrap
    text = ("이 문장은 충분히 길어서 여러 줄로 나뉘어야 하며 그 과정에서 어떤 단어도 "
            "사라지지 않아야 한다 including some english words too")
    wrapped = _wrap(text, 30, "    ")
    assert wrapped.split() == text.split()


def test_display_width_counts_hangul_as_two():
    from powerplan.report import _display_width, _pad
    assert _display_width("abc") == 3
    assert _display_width("한글") == 4
    assert _display_width("한a") == 3
    assert len(_pad("한글", 10)) == 10 - 2  # 표시 폭 10 = 문자 8개
