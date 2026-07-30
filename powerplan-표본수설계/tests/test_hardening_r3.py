"""3차 검토에서 나온 결함과 **테스트 구멍**을 고정한다.

2차 돌연변이 검사(135종)에서 35종이 살아남았고, 그 구멍은 대체로 축 하나가
통째로 비어 있어서 생겼다:

- ``powerplan/korean.py``에 테스트가 하나도 없었다 (조사가 전부 뒤집혀도 통과)
- ``_shell_quote``에 테스트가 하나도 없었다 (붙여넣기용 명령이 셸 주입 벡터가 됨)
- ``ratio ≠ 1``에서의 생존분석, ``sides = 2``인 prop1, ``sides = 1``인 McNemar
- 한쪽 방향 부등호만 확인해서 "너무 좋은" 값도 통과하던 단언

여기에 더해 3차 검토가 찾은 실제 결함(재현 정보의 이스케이프 누출, prop1의 O(n²),
diag 역방향 모드의 잘못된 안내, -o 파이프 정지)을 회귀 테스트로 못 박는다.
"""

from __future__ import annotations

import ast
import io
import json
import math
import os
import pathlib
import re
import shlex
import time
from contextlib import redirect_stderr, redirect_stdout

import pytest

from powerplan.cli import _provenance, _shell_quote, _shorten_path_token, main
from powerplan.designs import (
    LogRankSurvival,
    OneSampleProportion,
    RepeatedMeasuresT,
    TwoSampleT,
    binomial_sf,
    mcnemar_exact_power,
)
from powerplan.korean import has_final_consonant, josa
from powerplan.pilot import strip_unsafe
from powerplan.precision import diagnostic_plan, icc_plan, proportion_half_width
from powerplan.sequential import power_from_fixed, sequential_plan
from powerplan.solve import smallest_unit
from powerplan.special import norm_ppf
from powerplan.validate import PowerPlanError

ROOT = pathlib.Path(__file__).resolve().parent.parent
Z975 = norm_ppf(0.975)


def run(*argv):
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        code = main(list(argv))
    return code, buf.getvalue(), err.getvalue()


def run_json(*argv):
    code, out, err = run(*argv, "--format", "json")
    assert code == 0, err
    return json.loads(out)


# ==========================================================================
# A. 한국어 조사 — 모듈 전체가 무검증이었다
# ==========================================================================
@pytest.mark.parametrize("digit, expected", [
    ("0", True), ("1", True), ("2", False), ("3", True), ("4", False),
    ("5", False), ("6", True), ("7", True), ("8", True), ("9", False),
])
def test_digit_final_consonants(digit, expected):
    """영 일 이 삼 사 오 육 칠 팔 구 — 종성 유무가 조사를 가른다."""
    assert has_final_consonant(digit) is expected
    assert has_final_consonant("0." + digit) is expected


@pytest.mark.parametrize("word, expected", [
    ("중재군", True), ("바나나", False), ("사람", True), ("효과", False),
    ("힣", True), ("가", False), ("각", True), ("표본수", False), ("검정력", True),
])
def test_hangul_final_consonants(word, expected):
    assert has_final_consonant(word) is expected


def test_percent_is_read_without_a_final_consonant():
    assert josa("80.0%", "을", "를") == "80.0%를"
    assert josa("5%", "으로", "로") == "5%로"


def test_trailing_punctuation_is_skipped():
    """조사는 뒤에 그대로 붙되, 종성 판정은 문장부호를 건너뛰고 그 앞 글자를 본다."""
    assert josa("128명.", "이", "가") == "128명.이"      # 명 → ㅇ 종성
    assert josa("(0.5)", "을", "를") == "(0.5)를"       # 오 → 종성 없음
    assert josa("16.", "이", "가") == "16.이"            # 육 → ㄱ 종성
    assert josa("0.2)", "으로", "로") == "0.2)로"


def test_english_finals_follow_korean_pronunciation():
    assert josa("mL", "을", "를") == "mL을"           # 엠엘 → ㄹ 종성
    assert josa("ISI total", "을", "를") == "ISI total을"  # 토탈 → ㄹ 종성
    assert josa("HR", "이", "가") == "HR이"           # 에이치아르 → ㄹ 종성
    # t·p·k·s는 트·프·크·스로 읽혀 종성이 없다
    assert josa("ITT", "을", "를") == "ITT를"
    assert josa("SAP", "이", "가") == "SAP가"


def test_josa_picks_the_right_particle():
    assert josa("0.208", "을", "를") == "0.208을"
    assert josa("0.5", "으로", "로") == "0.5로"
    assert josa("0.6", "을", "를") == "0.6을"
    assert josa("0.3", "으로", "로") == "0.3으로"


def test_real_output_uses_correct_particles():
    """실제 리포트에 흔한 오류 조합이 나오지 않아야 한다."""
    _, out, _ = run("ttest2", "--d", "0.5", "--power", "0.8")
    flat = re.sub(r"\s+", " ", out)
    assert "명가 " not in flat and "명이 필요" in flat
    for bad in ("을 을", "를 를", "이 이 ", "가 가 "):
        assert bad not in flat


# ==========================================================================
# B. 재현 정보 — 붙여넣기용 명령이 안전해야 한다
# ==========================================================================
def test_shell_quote_round_trips_through_shlex():
    for token in ["plain", "it's", "a b", "$(rm -rf /)", "x'; rm -rf ~; echo '",
                  "개월", "--d", "0.5", "`whoami`", 'say "hi"', "a|b", "a\\b"]:
        assert shlex.split(_shell_quote(token)) == [token]


def test_shell_quote_leaves_ordinary_option_tokens_alone():
    for token in ("--d", "0.5", "--n-total", "ttest2", "a=b", "path/to.csv"):
        assert _shell_quote(token) == token


def test_provenance_command_reparses_to_the_original_arguments():
    argv = ["survival", "--hr", "0.7", "--event-rate", "0.5", "--power", "0.8",
            "--time-unit", "주 (weeks)"]
    prov = _provenance(argv)
    assert shlex.split(prov["command"])[1:] == argv


@pytest.mark.parametrize("hostile", [
    "\x1b[31mRED", "\x07bell", "‮RLO", "​ZWSP", "=cmd|'/C calc'!A1",
    "line1\nline2", "\x1b]0;title\x07",
])
def test_provenance_strips_terminal_and_bidi_injection(hostile):
    """이 줄은 사용자가 문서에 그대로 붙여 넣는다 — 조작 문자가 남으면 안 된다."""
    prov = _provenance(["survival", "--hr", "0.7", "--event-rate", "0.5",
                        "--power", "0.8", "--time-unit", hostile])
    command = prov["command"]
    for char in "\x1b\x07\n\r‮​  ":
        assert char not in command


def test_time_unit_is_sanitised_in_the_report_body():
    _, out, _ = run("survival", "--hr", "0.7", "--median1", "12", "--accrual", "12",
                    "--followup", "12", "--power", "0.8",
                    "--time-unit", "\x1b[31m개월‮")
    assert "\x1b" not in out and "‮" not in out


def test_markdown_provenance_survives_backticks():
    _, out, _ = run("survival", "--hr", "0.7", "--event-rate", "0.5", "--power", "0.8",
                    "--time-unit", "mo`​`x", "--format", "md")
    # 펜스가 깨지지 않아야 한다 (열고 닫는 개수가 짝수)
    fences = [line for line in out.splitlines() if line.startswith("```")]
    assert len(fences) % 2 == 0 and len(fences) >= 2


@pytest.mark.parametrize("form", ["-o {p}", "--output {p}", "--output={p}", "-o{p}"])
def test_provenance_shortens_paths_in_every_option_form(tmp_path, form):
    target = tmp_path / "SNUH_PT0042_불면증.txt"
    argv = ["ttest2", "--d", "0.5", "--power", "0.8"] + shlex.split(
        form.format(p=str(target))) + ["--force"]
    prov = _provenance(argv)
    assert str(tmp_path) not in prov["command"]
    assert "SNUH_PT0042_불면증.txt" in prov["command"]
    assert prov["paths_shortened"] is True


def test_shorten_path_token_handles_option_forms():
    assert _shorten_path_token("--output=/a/b/c.txt") == ("--output=c.txt", True)
    assert _shorten_path_token("-o/a/b/c.txt") == ("-oc.txt", True)
    assert _shorten_path_token("/a/b/c.txt") == ("c.txt", True)
    assert _shorten_path_token("--force") == ("--force", False)


def test_redact_also_hides_labels_in_the_provenance_line(tmp_path):
    csv = tmp_path / "phi.csv"
    csv.write_text("v,arm,site\n1,환자김철수,서울대병원\n2,환자김철수,서울대병원\n"
                   "3,환자김철수,서울대병원\n4,대조박영희,서울대병원\n"
                   "5,대조박영희,서울대병원\n6,대조박영희,서울대병원\n", encoding="utf-8")
    plan = run_json("pilot", str(csv), "--value", "v", "--group", "arm",
                    "--groups", "환자김철수,대조박영희", "--filter", "site=서울대병원",
                    "--power", "0.8", "--redact")
    payload = json.dumps(plan, ensure_ascii=False)
    assert "김철수" not in payload and "박영희" not in payload
    assert "서울대병원" not in payload
    assert plan["provenance"]["redacted"] is True


def test_without_redact_the_command_is_reproducible(tmp_path):
    csv = tmp_path / "ok.csv"
    csv.write_text("v,arm\n1,a\n2,a\n3,a\n4,b\n5,b\n6,b\n", encoding="utf-8")
    plan = run_json("pilot", str(csv), "--value", "v", "--group", "arm",
                    "--groups", "a,b", "--power", "0.8")
    assert "--groups a,b" in plan["provenance"]["command"]


def test_strip_unsafe_keeps_leading_dashes():
    assert strip_unsafe("--d") == "--d"
    assert strip_unsafe("\x1b[31m--d‮") == "[31m--d"


# ==========================================================================
# C. 축이 통째로 비어 있던 매개변수들
# ==========================================================================
def test_prop1_two_sided_halves_alpha():
    design = OneSampleProportion(0.6, 0.45, 0.025, 2)
    assert design.one_sided_alpha == pytest.approx(0.0125)
    assert design.critical_value(95) == 55
    one = OneSampleProportion(0.6, 0.45, 0.025, 1)
    assert smallest_unit(design, 0.8) > smallest_unit(one, 0.8)


def test_prop1_two_sided_warns_about_the_convention():
    _, out, _ = run("prop1", "--p1", "0.6", "--p0", "0.45", "--power", "0.8", "--sides", "2")
    assert "양측" in out and "관례상 단측" in out


def test_prop1_reports_the_actual_alpha_not_the_power():
    _, out, _ = run("prop1", "--p1", "0.60", "--p0", "0.45", "--power", "0.8")
    match = re.search(r"실제 유의수준\s*:\s*([0-9.]+)", out)
    assert match, out
    assert float(match.group(1)) <= 0.025 + 1e-12


def test_prop1_clopper_pearson_bound_is_reported_and_correct():
    design = OneSampleProportion(0.60, 0.45, 0.025, 1)
    lo, hi = design._clopper_pearson(53, 95)
    # 정의: P(X ≥ k | lo) = α, P(X ≤ k | hi) = α
    assert binomial_sf(53, 95, lo) == pytest.approx(0.025, abs=1e-9)
    assert 1.0 - binomial_sf(54, 95, hi) == pytest.approx(0.025, abs=1e-9)
    assert lo > 0.45          # 성능목표치를 넘는다
    _, out, _ = run("prop1", "--p1", "0.60", "--p0", "0.45", "--power", "0.8")
    assert "Clopper–Pearson" in out


def test_prop1_power_at_the_all_responders_boundary():
    """crit == n인 자리(전원이 반응해야 성공)에서도 검정력이 0이 아니다."""
    design = OneSampleProportion(0.99, 0.5, 0.025, 1)
    assert design.critical_value(8) == 8
    assert design.power(8) == pytest.approx(0.99 ** 8)


def test_prop1_lower_is_better_power_is_the_exact_lower_tail():
    design = OneSampleProportion(0.05, 0.15, 0.025, 1)
    n = 84
    crit = design.critical_value(n)
    assert design.power(n) == pytest.approx(1.0 - binomial_sf(crit + 1, n, 0.05))


def test_prop1_power_floors_a_fractional_unit():
    design = OneSampleProportion(0.60, 0.45, 0.025, 1)
    assert design.power(94.9) == design.power(94)


@pytest.mark.parametrize("ratio", [0.5, 2.0, 3.0])
def test_freedman_formula_is_pinned_for_unequal_allocation(ratio):
    """1:1이 아니면 Freedman 공식이 성립하지 않는다 — 막았는지 확인."""
    with pytest.raises(PowerPlanError, match="1:1"):
        LogRankSurvival(0.7, event_rate=1.0, ratio=ratio, method="freedman")


def test_freedman_matches_the_published_closed_form():
    z = Z975 + norm_ppf(0.8)
    got = LogRankSurvival(0.7, event_rate=1.0, method="freedman").required_events(0.8)
    assert got == pytest.approx(z * z * (1 + 0.7) ** 2 / (1 - 0.7) ** 2)


@pytest.mark.parametrize("ratio", [1.0, 2.0, 3.0])
def test_survival_information_weights_each_arm_correctly(ratio):
    design = LogRankSurvival(0.5, event_rate=0.6, ratio=ratio)
    alloc = design.allocation(50)
    info = design.information(alloc)
    want = alloc["n1"] * design.prob1 + alloc["n2"] * design.prob2
    assert info["total"] == pytest.approx(want)
    assert info["unit"] == "건"


def test_non_survival_information_is_the_analysis_total():
    plan = run_json("ttest2", "--d", "0.5", "--power", "0.8", "--ratio", "2",
                    "--interim", "1")
    seq = plan["sequential"]
    assert seq["information_label"] == "누적 N"
    assert seq["information_total"] == plan["analysis"]["allocation"]["total"]


def test_survival_interim_reports_events_in_the_right_unit():
    _, out, _ = run("survival", "--hr", "0.5", "--event-rate", "0.6", "--power", "0.8",
                    "--interim", "2")
    flat = re.sub(r"\s+", " ", out)
    assert "기대 누적 사건 수" in flat
    assert re.search(r"효과가 있으면 \d+건", flat)


@pytest.mark.parametrize("sides", [1, 2])
def test_mcnemar_exact_power_uses_the_right_direction(sides):
    """p10 > p01이면 검정력이 높고, 뒤집으면 (단측에서는) 거의 0이어야 한다."""
    forward = mcnemar_exact_power(155, 0.05, 0.15, 0.05, sides)
    backward = mcnemar_exact_power(155, 0.15, 0.05, 0.05, sides)
    assert forward > 0.7
    if sides == 1:
        assert backward < 1e-3
    else:
        assert backward == pytest.approx(forward, abs=1e-9)   # 양측은 대칭


def test_mcnemar_exact_power_boundary_of_the_cap():
    from powerplan.designs import MAX_EXACT_MCNEMAR_N
    assert mcnemar_exact_power(MAX_EXACT_MCNEMAR_N, 0.05, 0.15) is not None
    assert mcnemar_exact_power(MAX_EXACT_MCNEMAR_N + 1, 0.05, 0.15) is None


def test_repeated_scaled_keeps_the_estimand():
    design = RepeatedMeasuresT(0.5, 4, 1, 0.6, "ancova", estimand="average")
    weaker = design.scaled(0.8)
    assert weaker.estimand == "average"
    assert weaker.design_factor == pytest.approx(design.design_factor)


def test_repeated_rejects_an_unknown_estimand_with_a_clear_error():
    with pytest.raises(PowerPlanError, match="estimand"):
        RepeatedMeasuresT(0.5, 2, 1, 0.5, "ancova", estimand="x")


# ==========================================================================
# D. 한쪽 방향만 보던 단언 — 정확한 값으로 못 박는다
# ==========================================================================
@pytest.mark.parametrize("n, p", [(200, 0.05), (500, 0.9), (1000, 0.5)])
def test_binomial_sf_is_accurate_in_the_far_tail(n, p):
    """절대오차만 보면 1e-87짜리 값이 통째로 틀려도 통과한다 — 상대오차로 본다."""
    for k in (int(n * p) + int(3 * math.sqrt(n * p * (1 - p))), n - 1, n):
        want = math.fsum(math.comb(n, i) * p ** i * (1 - p) ** (n - i)
                         for i in range(k, n + 1))
        if want > 0:
            assert binomial_sf(k, n, p) == pytest.approx(want, rel=1e-9)


@pytest.mark.parametrize("prevalence", [0.05, 0.2, 0.5, 0.8])
def test_diag_reported_precision_is_exactly_what_the_counts_give(prevalence):
    plan = diagnostic_plan(0.9, 0.85, prevalence, 0.05, method="wald")
    assert plan["n_disease"] == pytest.approx(math.floor(plan["n"] * prevalence))
    assert plan["achieved_half_width"] == pytest.approx(
        proportion_half_width(plan["n_disease"], 0.9, 0.05))
    # 제약이 되는 쪽은 목표를 만족하되 지나치게 좋지도 않아야 한다
    # (한쪽 부등호만 보면 "너무 좋은" 값도 통과한다)
    binding = ("achieved_half_width" if plan["binding"] == "민감도"
               else "achieved_half_width_spec")
    assert 0.5 * 0.05 < plan[binding] <= 0.05 + 1e-9
    assert plan["achieved_half_width"] <= 0.05 + 1e-9
    assert plan["achieved_half_width_spec"] <= 0.05 + 1e-9


def test_diag_reverse_mode_note_states_the_true_requirement():
    plan = diagnostic_plan(0.9, 0.85, 0.2, 0.05, given_n=300)
    notes = " ".join(plan["notes"])
    assert f"{math.ceil(plan['n_exact'] - 1e-9):,}명을 등록해야" in notes
    assert "300명으로는" in notes


def test_proportion_half_width_rejects_tiny_n():
    for n in (0, 0.5, -1):
        with pytest.raises(PowerPlanError):
            proportion_half_width(n, 0.9)


def test_precision_given_n_respects_the_upper_bound():
    with pytest.raises(PowerPlanError):
        icc_plan(0.8, 0.15, given_n=10 ** 7)


def test_power_from_fixed_handles_exactly_one():
    seq = sequential_plan(1, 0.025, 1, 0.8, "obf")
    assert power_from_fixed(seq, 1.0) == pytest.approx(1.0)
    assert power_from_fixed(seq, 0.9999999999) > 0.99


def test_event_rate_derivation_is_asymmetric_between_arms():
    """예전 테스트는 x > x − 1 꼴이라 절대 실패할 수 없었다."""
    design = LogRankSurvival(0.4, event_rate=0.6)
    assert design.prob2 == pytest.approx(1.0 - 0.4 ** 0.4)
    assert design.prob2 < design.prob1
    naive = LogRankSurvival(0.4, event_rate=0.6)
    same_rate_events = 100 * 0.6 * 2          # 예전처럼 두 군을 같게 뒀을 때
    real_events = naive.events_for(100)
    assert real_events < same_rate_events * 0.95


# ==========================================================================
# E. 3차 검토가 찾은 실제 결함의 회귀 테스트
# ==========================================================================
def test_prop1_is_fast_even_for_small_margins():
    """예전에는 O(n²)이라 --p1 0.47 --p0 0.45가 65초 걸렸다."""
    start = time.monotonic()
    code, _, err = run("prop1", "--p1", "0.47", "--p0", "0.45", "--power", "0.8")
    elapsed = time.monotonic() - start
    assert code == 0, err
    assert elapsed < 10.0, f"{elapsed:.1f}초"


def test_prop1_rejects_absurd_n_instead_of_grinding():
    code, _, err = run("prop1", "--p1", "0.6", "--p0", "0.45", "--n", "10000000")
    assert code == 2
    assert "prop1" in err and "정규근사" in err


def test_prop1_large_but_allowed_n_is_quick():
    start = time.monotonic()
    code, _, err = run("prop1", "--p1", "0.9", "--p0", "0.85", "--n", "50000")
    assert code == 0, err
    assert time.monotonic() - start < 10.0


def test_output_to_a_named_pipe_is_refused_not_hung(tmp_path):
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    code, _, err = run("ttest2", "--d", "0.5", "--power", "0.8", "-o", str(fifo),
                       "--force")
    assert code == 2 and "일반 파일이 아닙니다" in err


def test_output_to_a_symlink_says_symlink(tmp_path):
    target = tmp_path / "real.txt"
    link = tmp_path / "link.txt"
    os.symlink(target, link)
    code, _, err = run("ttest2", "--d", "0.5", "--power", "0.8", "-o", str(link),
                       "--force")
    assert code == 2 and "심볼릭 링크" in err
    assert "Too many levels" not in err


def test_no_duplicate_top_level_definitions():
    """편집 중 블록이 두 번 들어가면 뒤의 정의가 조용히 이깁니다 — CI에서 막는다."""
    for path in sorted((ROOT / "powerplan").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = [node.name for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
        duplicated = {name for name in names if names.count(name) > 1}
        assert not duplicated, f"{path.name}에 중복 정의: {sorted(duplicated)}"
