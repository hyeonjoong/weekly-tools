"""새 기능의 CLI·리포트 통합 — 실제로 사람이 치는 명령이 끝까지 도는지 본다.

여기서 잡으려는 것은 "수식은 맞는데 출력이 깨지거나 옵션 조합이 죽는" 부류다.
"""

from __future__ import annotations

import json
import math

import pytest

from powerplan.cli import main
from powerplan.designs import LogRankSurvival, TwoSampleT
from powerplan.report import render_json, render_markdown, render_text
from powerplan.solve import Adjustments, continuous_unit, make_plan, smallest_unit
from powerplan.validate import PowerPlanError


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


# --------------------------------------------------------------------------
# 새 하위 명령 스모크 (세 가지 출력 형식 모두)
# --------------------------------------------------------------------------
NEW_COMMANDS = [
    ("survival", "--hr", "0.7", "--median1", "12", "--accrual", "18",
     "--followup", "12", "--power", "0.8"),
    ("survival", "--hr", "0.6", "--event-rate", "0.4", "--power", "0.9", "--ratio", "2"),
    ("repeated", "--d", "0.4", "--post", "3", "--rho", "0.6", "--power", "0.8"),
    ("repeated", "--mean1", "8", "--mean2", "6", "--sd", "5", "--post", "2",
     "--baseline-n", "2", "--rho", "0.5", "--analysis", "change", "--power", "0.8"),
    ("mcnemar", "--p01", "0.05", "--p10", "0.15", "--power", "0.8"),
    ("kappa", "--kappa", "0.7", "--width", "0.2"),
    ("kappa", "--kappa", "0.6", "--width", "0.15", "--prevalence", "0.2", "--alpha", "0.1"),
    ("noninf", "--margin", "0.1", "--p1", "0.7", "--p2", "0.7", "--power", "0.8"),
    ("noninf", "--margin", "0.05", "--p1", "0.02", "--p2", "0.03", "--power", "0.8",
     "--lower-is-better"),
    ("equiv", "--margin", "0.1", "--p1", "0.6", "--p2", "0.62", "--power", "0.8"),
    ("ttest2", "--d", "0.5", "--power", "0.9", "--interim", "2"),
    ("ttest2", "--d", "0.5", "--power", "0.8", "--interim", "1", "--spending", "pocock",
     "--timing", "0.5"),
    ("prop2", "--p1", "0.3", "--p2", "0.5", "--power", "0.8", "--interim", "1"),
    ("survival", "--hr", "0.7", "--event-rate", "0.5", "--power", "0.9", "--interim", "2",
     "--spending", "linear"),
]


@pytest.mark.parametrize("argv", NEW_COMMANDS)
@pytest.mark.parametrize("fmt", ["text", "md", "json"])
def test_new_commands_run(capsys, argv, fmt):
    code, out, err = run(capsys, *argv, "--format", fmt)
    assert code == 0, err
    assert out.strip()
    if fmt == "json":
        payload = json.loads(out)
        assert payload["sentences"]["kr"]
        assert payload["sentences"]["en"]


@pytest.mark.parametrize("argv", NEW_COMMANDS)
def test_new_commands_have_protocol_sentences(capsys, argv):
    code, out, _ = run(capsys, *argv)
    assert code == 0
    assert "[KR]" in out and "[EN]" in out
    # 한국어 문장에 조사 오류로 잘 생기는 패턴이 없어야 한다
    assert "을 을" not in out and "를 를" not in out
    assert "None" not in out
    assert "nan" not in out.lower()


@pytest.mark.parametrize("argv", NEW_COMMANDS)
def test_new_commands_json_is_finite(capsys, argv):
    code, out, _ = run(capsys, *argv, "--format", "json")
    assert code == 0
    payload = json.loads(out)          # allow_nan=False라 NaN이면 여기서 죽는다

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, float):
            assert math.isfinite(node)

    walk(payload)


# --------------------------------------------------------------------------
# 중간분석(군차별설계) 통합
# --------------------------------------------------------------------------
def test_interim_increases_sample_size_and_shows_boundaries(capsys):
    _, fixed, _ = run(capsys, "ttest2", "--d", "0.5", "--power", "0.9")
    _, seq, _ = run(capsys, "ttest2", "--d", "0.5", "--power", "0.9", "--interim", "2")
    assert "중간분석 경계" in seq
    assert "중간분석 경계" not in fixed
    assert "군당 86명" in fixed
    assert "군당 87명" in seq            # 팽창계수 1.02배
    assert "팽창계수" in seq
    assert "기대 표본수" in seq


def test_interim_boundary_table_is_consistent(capsys):
    _, out, _ = run(capsys, "ttest2", "--d", "0.5", "--power", "0.9", "--interim", "2",
                    "--format", "json")
    seq = json.loads(out)["sequential"]
    rows = seq["looks_detail"]
    assert len(rows) == 3
    assert [r["look"] for r in rows] == [1, 2, 3]
    assert rows[-1]["is_final"] is True
    # 경계는 OBF에서 단조감소, 누적 α는 증가, 누적 N도 증가
    assert all(a["bound_z"] > b["bound_z"] for a, b in zip(rows, rows[1:]))
    assert all(a["cumulative_alpha"] < b["cumulative_alpha"] for a, b in zip(rows, rows[1:]))
    assert all(a["n_total"] < b["n_total"] for a, b in zip(rows, rows[1:]))
    assert abs(rows[-1]["cumulative_alpha"] - 0.05) < 1e-9
    assert rows[-1]["n_total"] == json.loads(out)["analysis"]["allocation"]["total"]


def test_interim_achieved_power_meets_target(capsys):
    for interim in (1, 2, 3):
        for spending in ("obf", "pocock", "linear"):
            _, out, _ = run(capsys, "ttest2", "--d", "0.5", "--power", "0.8",
                            "--interim", str(interim), "--spending", spending,
                            "--format", "json")
            plan = json.loads(out)
            assert plan["achieved_power"] >= 0.8 - 1e-3
            assert plan["achieved_power"] < 0.83


@pytest.mark.parametrize("argv", [
    ("anova", "--k", "3", "--f", "0.25", "--power", "0.8"),
    ("equiv", "--margin", "5", "--sd", "8", "--power", "0.8"),
    ("prop1", "--p1", "0.6", "--p0", "0.45", "--power", "0.8"),
    ("crossover", "--diff", "3", "--sd-within", "6", "--power", "0.8"),
])
def test_interim_option_absent_where_it_does_not_apply(capsys, argv):
    """받아 놓고 나중에 거절하면 --help와 실제 동작이 어긋난다 — 아예 없어야 한다."""
    with pytest.raises(SystemExit) as exc:
        main([*argv, "--interim", "1"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--interim" in err
    # 도움말에도 나오면 안 된다
    with pytest.raises(SystemExit):
        main([argv[0], "--help"])
    assert "--interim" not in capsys.readouterr().out


@pytest.mark.parametrize("argv", [
    ("paired", "--dz", "0.5", "--power", "0.8"),
    ("onesample", "--d", "0.5", "--power", "0.8"),
    ("mcnemar", "--p01", "0.05", "--p10", "0.15", "--power", "0.8"),
    ("prop1", "--p1", "0.6", "--p0", "0.45", "--power", "0.8"),
    ("crossover", "--diff", "3", "--sd-within", "6", "--power", "0.8"),
])
def test_cluster_options_absent_where_they_do_not_apply(capsys, argv):
    with pytest.raises(SystemExit) as exc:
        main([*argv, "--cluster-size", "10", "--cluster-icc", "0.05"])
    assert exc.value.code == 2
    assert "--cluster-size" in capsys.readouterr().err


def test_timing_requires_interim(capsys):
    code, _, err = run(capsys, "ttest2", "--d", "0.5", "--power", "0.8", "--timing", "0.5")
    assert code == 2 and "--interim" in err


@pytest.mark.parametrize("timing", ["0.5,0.4", "abc", "1.5", "0", "", "0.5,0.6,0.7"])
def test_bad_timing_is_rejected(capsys, timing):
    code, _, err = run(capsys, "ttest2", "--d", "0.5", "--power", "0.8",
                       "--interim", "1", "--timing", timing)
    assert code == 2
    assert "timing" in err


def test_interim_with_dropout_and_cluster(capsys):
    code, out, err = run(capsys, "ttest2", "--d", "0.4", "--power", "0.8", "--interim", "1",
                         "--dropout", "0.2", "--cluster-size", "8", "--cluster-icc", "0.05")
    assert code == 0, err
    assert "중간분석 경계" in out
    assert "모집 표본수" in out
    assert "군집 수" in out


def test_interim_with_sensitivity_table_uses_inflated_numbers(capsys):
    _, plain, _ = run(capsys, "ttest2", "--d", "0.5", "--power", "0.8", "--sensitivity",
                      "--format", "json")
    _, seq, _ = run(capsys, "ttest2", "--d", "0.5", "--power", "0.8", "--interim", "2",
                    "--sensitivity", "--format", "json")
    a = json.loads(plain)["sensitivity"]["cells"]
    b = json.loads(seq)["sensitivity"]["cells"]
    assert all(x["unit"] <= y["unit"] for row_a, row_b in zip(a, b)
               for x, y in zip(row_a, row_b))
    assert any(x["unit"] < y["unit"] for row_a, row_b in zip(a, b)
               for x, y in zip(row_a, row_b))
    # 표의 80% 행이 헤드라인 표본수와 같아야 한다 (어긋나면 심사에서 지적당한다)
    headline = json.loads(seq)["analysis"]["allocation"]["n1"]
    assert b[1][1]["unit"] == headline


def test_interim_power_direction(capsys):
    """--n을 주면 군차별설계 검정력이 고정설계보다 낮게 나와야 한다."""
    _, fixed, _ = run(capsys, "ttest2", "--d", "0.5", "--n", "64", "--format", "json")
    _, seq, _ = run(capsys, "ttest2", "--d", "0.5", "--n", "64", "--interim", "2",
                    "--format", "json")
    a, b = json.loads(fixed), json.loads(seq)
    assert b["achieved_power"] < a["achieved_power"]
    assert b["fixed_power_at_n"] == pytest.approx(a["achieved_power"])


def test_interim_sentence_mentions_boundaries(capsys):
    _, out, _ = run(capsys, "ttest2", "--d", "0.5", "--power", "0.9", "--interim", "1",
                    "--format", "json")
    sentences = json.loads(out)["sentences"]
    assert "중간분석" in sentences["kr"]
    assert "소비함수" in sentences["kr"]
    # 1회면 단수, 2회 이상이면 복수여야 한다 (IRB에 붙는 문장이다)
    assert "An interim analysis is planned" in sentences["en"]
    assert "alpha spending" in sentences["en"]
    _, out, _ = run(capsys, "ttest2", "--d", "0.5", "--power", "0.9", "--interim", "2",
                    "--format", "json")
    assert "Interim analyses are planned" in json.loads(out)["sentences"]["en"]


# --------------------------------------------------------------------------
# --n-total
# --------------------------------------------------------------------------
def test_n_total_splits_by_ratio(capsys):
    _, a, _ = run(capsys, "ttest2", "--d", "0.5", "--n-total", "100", "--format", "json")
    _, b, _ = run(capsys, "ttest2", "--d", "0.5", "--n", "50", "--format", "json")
    assert json.loads(a)["achieved_power"] == json.loads(b)["achieved_power"]
    assert json.loads(a)["given"]["allocation"]["total"] == 100


def test_n_total_with_ratio_two(capsys):
    _, out, _ = run(capsys, "ttest2", "--d", "0.5", "--n-total", "120", "--ratio", "2",
                    "--format", "json")
    alloc = json.loads(out)["given"]["allocation"]
    assert alloc["n1"] == 40 and alloc["n2"] == 80


def test_n_total_for_anova_divides_by_k(capsys):
    _, out, _ = run(capsys, "anova", "--k", "3", "--f", "0.25", "--n-total", "150",
                    "--format", "json")
    alloc = json.loads(out)["given"]["allocation"]
    assert alloc["n_per_group"] == 50 and alloc["total"] == 150


def test_n_total_for_single_group(capsys):
    _, out, _ = run(capsys, "paired", "--dz", "0.5", "--n-total", "40", "--format", "json")
    assert json.loads(out)["given"]["allocation"]["n"] == 40


def test_n_total_conflicts_with_n(capsys):
    code, _, err = run(capsys, "ttest2", "--d", "0.5", "--n", "10", "--n-total", "40")
    assert code == 2 and "함께 쓸 수 없습니다" in err


@pytest.mark.parametrize("total", ["0", "-5"])
def test_n_total_rejects_bad_values(capsys, total):
    code, _, err = run(capsys, "ttest2", "--d", "0.5", "--n-total", total)
    assert code == 2
    assert "n-total" in err


def test_n_total_rejects_non_integer(capsys):
    """argparse 단계에서 걸러진다 (종료코드 2, 트레이스백 없음)."""
    with pytest.raises(SystemExit) as exc:
        main(["ttest2", "--d", "0.5", "--n-total", "1.5"])
    assert exc.value.code == 2


def test_n_total_note_is_recorded(capsys):
    _, out, _ = run(capsys, "ttest2", "--d", "0.5", "--n-total", "100")
    assert "--n-total 100 → 1군 n 50" in out
    assert "총 100명" in out


# --------------------------------------------------------------------------
# 이분형 비열등성/동등성 CLI 분기
# --------------------------------------------------------------------------
def test_noninf_requires_sd_or_proportions(capsys):
    code, _, err = run(capsys, "noninf", "--margin", "3", "--power", "0.8")
    assert code == 2 and "--sd" in err and "--p1" in err


def test_noninf_rejects_mixing_sd_and_proportions(capsys):
    code, _, err = run(capsys, "noninf", "--margin", "0.1", "--sd", "5", "--p1", "0.5",
                       "--p2", "0.5", "--power", "0.8")
    assert code == 2 and "함께 쓸 수 없습니다" in err


def test_noninf_rejects_half_specified_proportions(capsys):
    code, _, err = run(capsys, "noninf", "--margin", "0.1", "--p1", "0.5", "--power", "0.8")
    assert code == 2 and "--p2" in err


def test_noninf_binary_rejects_diff(capsys):
    code, _, err = run(capsys, "noninf", "--margin", "0.1", "--p1", "0.7", "--p2", "0.7",
                       "--diff", "0.02", "--power", "0.8")
    assert code == 2 and "--diff" in err


def test_continuous_noninf_still_works(capsys):
    code, out, _ = run(capsys, "noninf", "--margin", "3", "--sd", "8", "--power", "0.8",
                       "--lower-is-better")
    assert code == 0 and "비열등성" in out


# --------------------------------------------------------------------------
# 리포트 계층
# --------------------------------------------------------------------------
def test_design_lines_appear_in_all_formats():
    design = LogRankSurvival(0.7, median1=12.0, accrual=12.0, followup=12.0)
    plan = make_plan(design, target_power=0.8)
    assert plan["design_lines"]
    assert "기대 사건 수" in render_text(plan)
    assert "기대 사건 수" in render_markdown(plan)
    assert "기대 사건 수" in render_json(plan)


def test_markdown_sequential_table_is_wellformed():
    plan = make_plan(TwoSampleT(0.5), target_power=0.9,
                     adjustments=Adjustments(interim=2))
    md = render_markdown(plan)
    assert "| 시점 | 정보비율" in md
    table = [line for line in md.splitlines() if line.startswith("| 최종")]
    assert len(table) == 1
    assert table[0].count("|") == 8


def test_continuous_unit_brackets_smallest_unit():
    for d in (0.2, 0.5, 0.9):
        design = TwoSampleT(d)
        cont = continuous_unit(design, 0.8)
        integer = smallest_unit(design, 0.8)
        assert integer - 1 <= cont <= integer
        assert design.power(cont) == pytest.approx(0.8, abs=1e-6)


def test_continuous_unit_rejects_impossible_target():
    with pytest.raises(PowerPlanError):
        continuous_unit(TwoSampleT(1e-6), 0.99, cap=1000)


def test_adjustments_rejects_bad_sequential_settings():
    with pytest.raises(PowerPlanError, match="spending"):
        Adjustments(interim=1, spending="nope")
    with pytest.raises(PowerPlanError, match="interim"):
        Adjustments(interim=0)
    with pytest.raises(PowerPlanError, match="timing"):
        Adjustments(timing=(0.5, 1.0))


def test_help_lists_new_designs(capsys):
    code, out, _ = run(capsys)
    assert code == 0
    for key in ("survival", "repeated", "mcnemar", "kappa"):
        assert key in out


@pytest.mark.parametrize("design", ["survival", "repeated", "mcnemar", "kappa"])
def test_subcommand_help_runs(capsys, design):
    with pytest.raises(SystemExit) as exc:
        main([design, "--help"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip()
