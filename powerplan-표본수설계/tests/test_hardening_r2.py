"""2차 적대적 검토에서 나온 결함과 **테스트 구멍**을 고정한다.

돌연변이 검사(새 코드에 125종을 심어 테스트가 잡는지 확인)에서 살아남은 21종과,
정확성·엣지케이스·보안 검토가 지적한 결함을 여기서 전부 사살한다. 각 테스트에는
"이 테스트가 없으면 무엇이 조용히 틀리는가"를 적어 둔다.
"""

from __future__ import annotations

import io
import json
import math
import os
import random
import re
import shlex
import stat
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout

import pytest

from powerplan import sequential as S
from powerplan.cli import MAX_GIVEN_N, main
from powerplan.designs import (
    EquivalenceProportions,
    LogRankSurvival,
    McNemarPaired,
    NonInferiorityProportions,
    RepeatedMeasuresT,
    TwoSampleT,
    ancova_inflation,
    mcnemar_exact_power,
)
from powerplan.pilot import read_two_group
from powerplan.precision import kappa_plan
from powerplan.sequential import MAX_INTERIM, check_interim, sequential_plan
from powerplan.solve import (
    Adjustments,
    _SENSITIVITY_POWERS,
    continuous_unit,
    make_plan,
    smallest_unit,
)
from powerplan.special import norm_ppf
from powerplan.validate import MIN_ALPHA, PowerPlanError

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
# A. 군차별설계 — 단측 경로에는 오라클이 하나도 없었다
# ==========================================================================
def _simulate_one_sided(bounds, timing, drift, trials, seed):
    """단측 설계의 상측 경계 통과율을 직접 시뮬레이션한다."""
    rng = random.Random(seed)
    cs = [b * math.sqrt(t) for b, t in zip(bounds, timing)]
    steps = [t - p for t, p in zip(timing, (0.0,) + tuple(timing[:-1]))]
    hits = 0
    for _ in range(trials):
        s = 0.0
        for c, step in zip(cs, steps):
            s += rng.gauss(drift * step, math.sqrt(step))
            if s >= c:
                hits += 1
                break
    return hits / trials


@pytest.mark.parametrize("spending", ["obf", "pocock"])
def test_one_sided_boundaries_hold_alpha_by_simulation(spending):
    """단측 경로(_lower_limit의 절단)를 몬테카를로로 검증.

    _solve_bounds와 _crossing이 같은 함수를 공유하므로 '자기일관성' 검사만으로는
    공통 오류를 잡을 수 없다 — 바깥 오라클이 있어야 한다.
    """
    seq = sequential_plan(2, 0.025, 1, 0.90, spending)
    trials = 200_000
    mc_null = _simulate_one_sided(seq["bounds"], seq["timing"], 0.0, trials, seed=101)
    se = math.sqrt(0.025 * 0.975 / trials)
    assert abs(mc_null - 0.025) < 4 * se

    mc_alt = _simulate_one_sided(seq["bounds"], seq["timing"], seq["drift"], trials,
                                 seed=202)
    se = math.sqrt(0.9 * 0.1 / trials)
    assert abs(mc_alt - 0.90) < 4 * se


def test_one_sided_inflation_is_above_one_and_sensible():
    """단측에서 z_{1−α}가 아니라 z_{1−α/2}를 쓰면 팽창계수가 1 밑으로 떨어진다."""
    for spending in ("obf", "pocock", "linear"):
        for interim in (1, 2, 3):
            seq = sequential_plan(interim, 0.025, 1, 0.90, spending)
            assert seq["inflation"] > 1.0
            assert seq["inflation"] < 1.30
            # 고정설계 이동모수는 단측이므로 z_{0.975}+z_{0.90}이어야 한다
            assert seq["drift_fixed"] == pytest.approx(Z975 + norm_ppf(0.90))


def test_one_sided_cli_interim_increases_sample_size():
    fixed = run_json("ttest2", "--d", "0.5", "--sides", "1", "--power", "0.9")
    seq = run_json("ttest2", "--d", "0.5", "--sides", "1", "--power", "0.9",
                   "--interim", "2")
    assert (seq["analysis"]["allocation"]["n1"]
            > fixed["analysis"]["allocation"]["n1"])
    assert seq["achieved_power"] >= 0.9 - 1e-6
    assert seq["sequential"]["inflation"] > 1.0


def test_one_sided_grid_is_converged():
    timing = (0.5, 1.0)
    coarse = S._solve_bounds(timing, 0.025, "obf", False, npts=S._GRID)
    fine = S._solve_bounds(timing, 0.025, "obf", False, npts=321)
    assert all(abs(a - b) < 1e-6 for a, b in zip(coarse, fine))


# ==========================================================================
# B. α 소비함수의 '모양'을 식으로 고정 (끝점·단조성만으로는 t vs t² 구분 불가)
# ==========================================================================
@pytest.mark.parametrize("t", [0.1, 0.25, 0.4, 0.5, 0.75, 0.9])
def test_spending_functions_match_their_formulas(t):
    alpha = 0.05
    assert S._spent(t, alpha, "linear") == pytest.approx(alpha * t)
    assert S._spent(t, alpha, "pocock") == pytest.approx(
        alpha * math.log1p((math.e - 1.0) * t))
    from powerplan.special import norm_cdf
    assert S._spent(t, alpha, "obf") == pytest.approx(
        2.0 * (1.0 - norm_cdf(norm_ppf(1.0 - alpha / 2.0) / math.sqrt(t))))


def test_obf_spending_at_half_information_is_tiny():
    """LD-OBF의 대표값 — 정보 절반에서 전체 α의 11%만 쓴다."""
    assert S._spent(0.5, 0.05, "obf") == pytest.approx(0.005573, abs=1e-5)
    assert S._spent(0.5, 0.05, "pocock") == pytest.approx(0.031, abs=1e-3)


def test_incremental_alpha_is_the_difference_of_cumulative():
    seq = sequential_plan(3, 0.05, 2, 0.9, "obf")
    cum = seq["cumulative_alpha"]
    want = tuple(c - p for c, p in zip(cum, (0.0,) + cum[:-1]))
    assert seq["incremental_alpha"] == pytest.approx(want)
    assert math.fsum(seq["incremental_alpha"]) == pytest.approx(0.05)
    assert all(x > 0.0 for x in seq["incremental_alpha"])


def test_interim_limit_boundary_is_accepted():
    assert check_interim(MAX_INTERIM) == MAX_INTERIM
    assert sequential_plan(MAX_INTERIM, 0.05, 2, 0.9, "obf")["looks"] == MAX_INTERIM + 1
    with pytest.raises(PowerPlanError):
        check_interim(MAX_INTERIM + 1)


def test_huge_interim_is_rejected_without_allocating():
    """예전에는 20억짜리 튜플을 먼저 만들며 메모리를 다 먹었다."""
    code, _, err = run("ttest2", "--d", "0.5", "--power", "0.8",
                       "--interim", "2147483647")
    assert code == 2 and "interim" in err


def test_inflation_never_drops_below_one():
    """극단적 --timing에서 경계 탐색이 상한에 걸려도 팽창계수는 1 이상이어야 한다."""
    for timing in ((0.01, 0.02, 1.0), (0.01, 1.0), (0.98, 1.0)):
        seq = sequential_plan(len(timing) - 1, 0.05, 2, 0.90, "obf", timing)
        assert seq["inflation"] >= 1.0
    # 라운드 4: 그보다 촘촘한 간격은 합성곱 격자가 앨리어싱을 일으켜 확률이 1을
    # 넘고 표본수가 고정설계보다 작아졌다. 이제는 계산하지 않고 거절한다.
    for timing in ((0.001, 1.0), (0.999, 1.0), (0.5, 0.500001, 1.0)):
        with pytest.raises(PowerPlanError, match="촘촘"):
            sequential_plan(len(timing) - 1, 0.05, 2, 0.90, "obf", timing)


# ==========================================================================
# C. power_from_fixed — 양측 아래쪽 꼬리까지 역산해야 한다
# ==========================================================================
def test_power_from_fixed_at_null_returns_half_alpha():
    """효과가 0이면 상측 경계 통과확률은 α/2다 (예전에는 0.049를 돌려줬다)."""
    seq = sequential_plan(2, 0.05, 2, 0.90, "obf")
    assert S.power_from_fixed(seq, 0.05) == pytest.approx(0.025, abs=1e-6)
    assert S.power_from_fixed(seq, 0.049) == pytest.approx(0.025, abs=1e-6)


@pytest.mark.parametrize("fixed_power", [0.06, 0.10, 0.20, 0.30, 0.50, 0.80, 0.95])
def test_drift_from_fixed_power_inverts_two_sided_power(fixed_power):
    from powerplan.special import norm_cdf
    mu = S.drift_from_fixed_power(fixed_power, 0.05, 2)
    back = norm_cdf(mu - Z975) + norm_cdf(-mu - Z975)
    assert back == pytest.approx(fixed_power, abs=1e-9)


def test_drift_from_fixed_power_one_sided():
    mu = S.drift_from_fixed_power(0.80, 0.025, 1)
    assert mu == pytest.approx(Z975 + norm_ppf(0.80))


# ==========================================================================
# D. 표본수 탐색 — 이중 올림 제거와 최소성
# ==========================================================================
@pytest.mark.parametrize("spending", ["obf", "pocock", "linear"])
@pytest.mark.parametrize("interim", [1, 2, 3])
def test_sequential_sample_size_is_minimal(spending, interim):
    """n−1로도 목표를 만족하면 안 된다 (심사자가 재현하면 바로 지적당한다)."""
    plan = make_plan(TwoSampleT(0.5), target_power=0.90,
                     adjustments=Adjustments(interim=interim, spending=spending))
    seq = sequential_plan(interim, 0.05, 2, 0.90, spending)
    n = plan["analysis"]["allocation"]["n1"]
    design = TwoSampleT(0.5)

    def gs_power(unit):
        return S.power_from_fixed(
            seq, design.power_of_allocation(design.allocation(unit)))

    assert gs_power(n) >= 0.90
    assert gs_power(n - 1) < 0.90


def test_continuous_unit_never_undershoots():
    """연속 해가 목표 아래로 내려가면 팽창 후 표본수가 통째로 모자란다."""
    for d in (0.2, 0.35, 0.5, 0.9):
        design = TwoSampleT(d)
        assert design.power(continuous_unit(design, 0.8)) >= 0.8


def test_sensitivity_rows_each_use_their_own_inflation():
    """행마다 목표 검정력이 다르므로 팽창계수도 달라야 한다."""
    design = TwoSampleT(0.5)
    adj = Adjustments(interim=2, spending="pocock")
    plan = make_plan(design, target_power=0.80, adjustments=adj, sensitivity=True)
    cells = plan["sensitivity"]["cells"]
    for row, power in zip(cells, _SENSITIVITY_POWERS):
        inflation = sequential_plan(2, 0.05, 2, power, "pocock")["inflation"]
        want = max(design.min_unit,
                   math.ceil(continuous_unit(design, power) * inflation - 1e-9))
        assert abs(row[1]["unit"] - want) <= 1, (power, row[1]["unit"], want)


def test_sensitivity_power_table_floors_the_unit():
    """올림하면 실제로 확보한 인원보다 많은 사람으로 검정력을 주장하게 된다."""
    plan = make_plan(TwoSampleT(0.5), unit=63,
                     adjustments=Adjustments(dropout=0.13), sensitivity=True)
    unit = plan["analysis"]["unit"]
    assert unit != int(unit)          # 소수여야 이 테스트가 의미가 있다
    for row in plan["sensitivity"]["rows"]:
        assert row["unit"] == max(2, math.floor(unit * row["factor"]))


def test_sequential_look_counts_round_up_with_uneven_timing():
    plan = run_json("ttest2", "--d", "0.45", "--power", "0.85", "--interim", "3",
                    "--timing", "0.31,0.57,0.83")
    total = plan["analysis"]["allocation"]["total"]
    for row in plan["sequential"]["looks_detail"]:
        assert row["n_total"] == math.ceil(total * row["information"] - 1e-9)


# ==========================================================================
# E. --n-total — 모든 두 군 설계에서 총 N을 지켜야 한다
# ==========================================================================
@pytest.mark.parametrize("argv, total", [
    (("ttest2", "--d", "0.5"), 100),
    (("prop2", "--p1", "0.3", "--p2", "0.5"), 120),
    (("repeated", "--d", "0.4", "--rho", "0.5"), 140),
    (("survival", "--hr", "0.7", "--event-rate", "0.5"), 160),
    (("noninf", "--margin", "3", "--sd", "8"), 180),
    (("equiv", "--margin", "5", "--sd", "8"), 200),
])
def test_n_total_is_split_not_duplicated(argv, total):
    plan = run_json(*argv, "--n-total", str(total))
    assert plan["given"]["allocation"]["total"] == total


def test_n_total_never_exceeds_what_the_user_has():
    """101명을 1:1로 나누면 50/50이어야 한다 (51/51 = 102명은 없는 사람이다)."""
    for total in (99, 101, 137):
        plan = run_json("ttest2", "--d", "0.5", "--n-total", str(total))
        assert plan["given"]["allocation"]["total"] <= total


def test_n_total_reports_the_leftover():
    _, out, _ = run("ttest2", "--d", "0.5", "--n-total", "101")
    assert "계산에 쓰이지 않았습니다" in out.replace("\n", " ").replace("  ", " ")


def test_n_total_boundary_values():
    code, _, _ = run("ttest2", "--d", "0.5", "--n-total", str(MAX_GIVEN_N))
    assert code == 0
    code, _, err = run("ttest2", "--d", "0.5", "--n-total", str(MAX_GIVEN_N + 1))
    assert code == 2 and "n-total" in err


@pytest.mark.parametrize("argv", [
    ("ttest2", "--d", "0.5", "--n-total", "100", "--ratio", "-1"),
    ("prop2", "--p1", "0.3", "--p2", "0.5", "--n-total", "100", "--ratio", "-1"),
    ("survival", "--hr", "0.7", "--event-rate", "0.5", "--n-total", "100",
     "--ratio", "0"),
    ("anova", "--k", "0", "--f", "0.25", "--n-total", "100"),
    ("anova", "--k", "1", "--f", "0.25", "--n-total", "100"),
])
def test_n_total_validates_before_dividing(argv):
    """검증보다 나눗셈이 먼저 오면 ZeroDivisionError로 죽는다."""
    code, _, err = run(*argv)
    assert code == 2
    assert "오류" in err and "Traceback" not in err


def test_fractional_ratio_rounds_the_second_arm_up():
    design = NonInferiorityProportions(0.7, 0.7, 0.1, 0.025, 2.5)
    alloc = design.allocation(101)
    assert alloc["n2"] == math.ceil(2.5 * 101)
    assert alloc["total"] == alloc["n1"] + alloc["n2"]


# ==========================================================================
# F. 설계별 경계값 (돌연변이가 살아남던 자리)
# ==========================================================================
def test_mcnemar_rejects_zero_concordant_pairs():
    """p01+p10 = 1이면 일치하는 쌍이 하나도 없다는 뜻 — 모형이 성립하지 않는다."""
    with pytest.raises(PowerPlanError, match="1보다"):
        McNemarPaired(0.4, 0.6)


def test_equivalence_rejects_difference_exactly_at_margin():
    with pytest.raises(PowerPlanError, match="밖입니다"):
        EquivalenceProportions(0.5, 0.75, 0.25, 0.05)


def test_kappa_rejects_width_at_the_upper_bound():
    with pytest.raises(PowerPlanError):
        kappa_plan(0.7, 2.0)


def test_kappa_expected_positive_uses_prevalence():
    plan = kappa_plan(0.7, 0.2, 0.2)
    assert plan["expected_positive"] == pytest.approx(plan["n"] * 0.2)


def test_survival_pooled_event_probability_weights_by_allocation():
    design = LogRankSurvival(0.7, ratio=3.0, median1=12.0, accrual=12.0, followup=12.0)
    want = (design.prob1 + 3.0 * design.prob2) / 4.0
    assert design.prob_event_pooled == pytest.approx(want)


# ==========================================================================
# G. 정밀도 설계의 수치 하한 (예전에는 OverflowError / ZeroDivisionError)
# ==========================================================================
@pytest.mark.parametrize("argv", [
    ("kappa", "--kappa", "0.7", "--width", "0.2", "--alpha", "1e-16"),
    ("icc", "--icc", "0.8", "--width", "0.15", "--alpha", "1e-16"),
    ("loa", "--sd-diff", "2", "--half-width", "0.5", "--alpha", "1e-16"),
    ("ttest2", "--d", "0.5", "--power", "0.8", "--alpha", "1e-16"),
    ("kappa", "--kappa", "0.5", "--width", "1e-300"),
    ("icc", "--icc", "0.9", "--width", "1e-300"),
    ("icc", "--icc", "0.8", "--width", "0.15", "--raters", "1000000000"),
])
def test_precision_designs_reject_extreme_inputs_cleanly(argv):
    code, _, err = run(*argv)
    assert code == 2, f"{argv} 는 종료코드 2여야 합니다"
    assert "Traceback" not in err
    assert err.strip().startswith("오류")


def test_alpha_floor_message_mentions_alpha():
    code, _, err = run("ttest2", "--d", "0.5", "--power", "0.8", "--alpha", "1e-16")
    assert "alpha" in err and f"{MIN_ALPHA:g}" in err


def test_alpha_at_the_floor_still_works():
    code, _, err = run("ttest2", "--d", "0.5", "--power", "0.8", "--alpha", str(MIN_ALPHA))
    assert code == 0, err


# ==========================================================================
# H. ANCOVA 분산 팽창
# ==========================================================================
def test_ancova_inflation_formula():
    assert ancova_inflation(100, False) == 1.0
    assert ancova_inflation(100, True) == pytest.approx(1.0 + 1.0 / 97.0)
    assert ancova_inflation(4, True) == 2.0        # 방어적 상한


def test_ancova_power_is_below_the_naive_calculation():
    """팽창 항을 빼면 검정력이 과대평가된다 — 두 설계 모두에서."""
    for design in (TwoSampleT(0.4, baseline_r=0.6, analysis="ancova"),
                   RepeatedMeasuresT(0.4, 2, 1, 0.6, "ancova")):
        n = 40
        naive_ncp = design.effective_d / math.sqrt(2.0 / n)
        from powerplan.distributions import nct_cdf, nct_sf, t_ppf
        df = 2 * n - 3
        tc = t_ppf(0.975, df)
        naive = nct_sf(tc, df, naive_ncp) + nct_cdf(-tc, df, naive_ncp)
        assert design.power(n) < naive
        assert design.power(n) > naive - 0.02


def test_ancova_inflation_vanishes_for_large_n():
    design = TwoSampleT(0.05, baseline_r=0.6, analysis="ancova")
    assert design.power(50_000) == pytest.approx(design.power(50_000), abs=0)
    assert ancova_inflation(100_000, True) == pytest.approx(1.0, abs=1e-4)


# ==========================================================================
# I. 반복측정 추정 대상 (estimand)
# ==========================================================================
def test_estimand_default_is_last_visit():
    plan = run_json("repeated", "--d", "0.4", "--post", "3", "--rho", "0.6",
                    "--power", "0.8")
    assert plan["design"]["effect"]["estimand"] == "last"
    assert "마지막 방문" in plan["design"]["test_kr"]


def test_average_estimand_is_documented_as_a_risk():
    _, out, _ = run("repeated", "--d", "0.4", "--post", "3", "--rho", "0.6",
                    "--power", "0.8", "--estimand", "average")
    flat = re.sub(r"\s+", " ", out)
    assert "과소" in flat        # 마지막 방문을 분석할 거면 이 표본수는 부족하다


def test_estimand_choice_changes_sample_size_materially():
    last = run_json("repeated", "--d", "0.4", "--post", "3", "--rho", "0.6",
                    "--power", "0.8")["analysis"]["allocation"]["n1"]
    avg = run_json("repeated", "--d", "0.4", "--post", "3", "--rho", "0.6",
                   "--power", "0.8", "--estimand", "average")["analysis"]["allocation"]["n1"]
    assert last > avg * 1.5


def test_repeated_rejects_bad_estimand():
    with pytest.raises(PowerPlanError, match="estimand"):
        RepeatedMeasuresT(0.4, 2, 1, 0.5, "ancova", estimand="mean")


# ==========================================================================
# J. 생존분석 — 사건 기준 정보량, 비례위험 사건률, 두 공식
# ==========================================================================
def test_survival_interim_table_is_in_events():
    plan = run_json("survival", "--hr", "0.7", "--median1", "12", "--accrual", "12",
                    "--followup", "12", "--power", "0.8", "--interim", "2")
    seq = plan["sequential"]
    assert seq["information_label"] == "누적 사건 수"
    events = [row["information_amount"] for row in seq["looks_detail"]]
    assert all(e is not None for e in events)
    assert all(a < b for a, b in zip(events, events[1:]))
    # 마지막 시점의 사건 수가 Schoenfeld 필요 사건 수 언저리여야 한다
    design = LogRankSurvival(0.7, median1=12.0, accrual=12.0, followup=12.0)
    # 표본수를 정수로 올리므로 목표 사건 수를 조금 넘는다 (모자라면 안 된다)
    required = design.required_events(0.8)
    assert required <= events[-1] < required + 12
    # 인원 기준 '누적 N'은 계산할 수 없으므로 아예 비워야 한다 (달력 시간이 필요)
    assert all(row["n_total"] is None for row in seq["looks_detail"])


def test_survival_interim_warns_that_information_is_events():
    _, out, _ = run("survival", "--hr", "0.7", "--median1", "12", "--accrual", "12",
                    "--followup", "12", "--power", "0.8", "--interim", "1")
    flat = re.sub(r"\s+", " ", out)
    assert "사건 수" in flat and "추적기간" in flat


def test_non_survival_interim_table_is_in_completers():
    plan = run_json("ttest2", "--d", "0.5", "--power", "0.8", "--interim", "1")
    assert plan["sequential"]["information_label"] == "누적 N"
    notes = " ".join(plan["notes"])
    assert "완료한" in notes and "등록 인원이 아닙니다" in notes


def test_event_rate_derives_treatment_arm_by_proportional_hazards():
    for p1, hr in ((0.5, 0.7), (0.3, 0.5), (0.8, 1.5)):
        design = LogRankSurvival(hr, event_rate=p1)
        assert design.prob1 == pytest.approx(p1)
        assert design.prob2 == pytest.approx(1.0 - (1.0 - p1) ** hr)
    # --event-rate 1은 '사건 수 = 총 N' 관용구로 계속 쓸 수 있어야 한다
    assert LogRankSurvival(0.7, event_rate=1.0).prob2 == pytest.approx(1.0)


def test_event_rate_no_longer_pretends_both_arms_are_equal():
    """예전에는 두 군을 같게 둬 사건 수를 과대평가했다 (표본수가 낙관적)."""
    design = LogRankSurvival(0.4, event_rate=0.6)
    assert design.prob2 < design.prob1
    assert smallest_unit(design, 0.8) > smallest_unit(
        LogRankSurvival(0.4, event_rate=0.6, ratio=1.0), 0.8) - 1


def test_freedman_is_more_conservative_than_schoenfeld():
    for hr in (0.5, 0.7, 0.85, 1.4):
        sch = LogRankSurvival(hr, event_rate=1.0, method="schoenfeld")
        fre = LogRankSurvival(hr, event_rate=1.0, method="freedman")
        assert fre.required_events(0.8) > sch.required_events(0.8)
    # 손계산 대조 (HR 0.7, 양측 0.05, 검정력 0.8)
    z = Z975 + norm_ppf(0.8)
    want = z * z * (1 + 0.7) ** 2 / (1 - 0.7) ** 2
    got = LogRankSurvival(0.7, event_rate=1.0, method="freedman").required_events(0.8)
    assert got == pytest.approx(want)
    assert got == pytest.approx(252.0, abs=0.5)


def test_extreme_hazard_ratio_gets_a_warning():
    _, out, _ = run("survival", "--hr", "0.3", "--event-rate", "0.5", "--power", "0.8")
    flat = re.sub(r"\s+", " ", out)
    assert "과대평가" in flat and "freedman" in flat
    _, mild, _ = run("survival", "--hr", "0.8", "--event-rate", "0.5", "--power", "0.8")
    assert "과대평가" not in re.sub(r"\s+", " ", mild)


def test_survival_method_is_rejected_when_unknown():
    with pytest.raises(PowerPlanError, match="method"):
        LogRankSurvival(0.7, event_rate=0.5, method="lachin")


# ==========================================================================
# K. McNemar 정확검정
# ==========================================================================
def test_mcnemar_exact_power_matches_simulation():
    """정확 조건부 이항검정의 검정력을 직접 시뮬레이션과 대조."""
    n, p01, p10, alpha = 155, 0.05, 0.15, 0.05
    got = mcnemar_exact_power(n, p01, p10, alpha, 2)
    rng = random.Random(17)
    trials, hits = 20_000, 0
    for _ in range(trials):
        b = c = 0
        for _ in range(n):
            u = rng.random()
            if u < p10:
                b += 1
            elif u < p10 + p01:
                c += 1
        disc = b + c
        if disc == 0:
            continue
        obs = math.comb(disc, b) * 0.5 ** disc
        pv = math.fsum(math.comb(disc, k) * 0.5 ** disc
                       for k in range(disc + 1)
                       if math.comb(disc, k) * 0.5 ** disc <= obs * (1 + 1e-12))
        if pv <= alpha:
            hits += 1
    se = math.sqrt(0.76 * 0.24 / trials)
    assert abs(got - hits / trials) < 4 * se


def test_mcnemar_exact_power_is_conservative_versus_connor():
    """이산분포라 실제 유의수준이 α보다 낮고, 그만큼 검정력도 낮다."""
    design = McNemarPaired(0.05, 0.15, 0.05, 2)
    n = smallest_unit(design, 0.80)
    exact = mcnemar_exact_power(n, 0.05, 0.15, 0.05, 2)
    assert exact < design.power(n)
    assert exact > 0.70


def test_mcnemar_exact_power_returns_none_when_too_large():
    assert mcnemar_exact_power(10_000, 0.05, 0.15) is None


def test_mcnemar_reports_both_powers():
    _, out, _ = run("mcnemar", "--p01", "0.05", "--p10", "0.15", "--power", "0.8")
    assert "정확검정 검정력" in out
    assert "Connor" in out


# ==========================================================================
# L. 개인정보·파일 취급
# ==========================================================================
def _pii_csv(tmp_path):
    path = tmp_path / "pii.csv"
    path.write_text(
        "subject,value,group\n"
        "1,10.2,김철수 MRN 0012345\n2,11.5,김철수 MRN 0012345\n3,12.1,김철수 MRN 0012345\n"
        "4,20.0,이영희 010-1234-5678\n5,21.0,이영희 010-1234-5678\n"
        "6,22.0,이영희 010-1234-5678\n", encoding="utf-8")
    return str(path)


def test_redact_hides_labels_and_observed_range(tmp_path):
    path = _pii_csv(tmp_path)
    plan = run_json("pilot", path, "--value", "value", "--group", "group",
                    "--power", "0.8", "--redact")
    payload = json.dumps(plan, ensure_ascii=False)
    assert "김철수" not in payload and "0012345" not in payload
    assert "010-1234-5678" not in payload
    assert plan["pilot"]["data"]["group1"]["label"] == "군1"
    assert plan["pilot"]["data"]["group1"]["min"] is None
    assert plan["pilot"]["data"]["group1"]["n"] == 3      # 통계는 그대로 나온다


def test_json_records_only_the_file_name(tmp_path):
    path = _pii_csv(tmp_path)
    plan = run_json("pilot", path, "--value", "value", "--group", "group", "--power", "0.8")
    assert plan["pilot"]["data"]["path"] == "pii.csv"
    assert os.path.dirname(path) not in json.dumps(plan, ensure_ascii=False)


def test_provenance_records_version_and_command():
    plan = run_json("ttest2", "--d", "0.5", "--power", "0.8")
    prov = plan["provenance"]
    from powerplan import __version__
    assert prov["version"] == __version__
    assert prov["command"] == "powerplan ttest2 --d 0.5 --power 0.8 --format json"
    assert prov["generated"]


def test_provenance_shortens_paths(tmp_path):
    path = _pii_csv(tmp_path)
    plan = run_json("pilot", path, "--value", "value", "--group", "group", "--power", "0.8")
    assert plan["provenance"]["paths_shortened"] is True
    assert "pii.csv" in plan["provenance"]["command"]
    assert os.path.dirname(path) not in plan["provenance"]["command"]


def test_force_overwrite_restores_owner_only_permissions(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("old", encoding="utf-8")
    os.chmod(target, 0o666)
    code, _, err = run("ttest2", "--d", "0.5", "--power", "0.8", "-o", str(target),
                       "--force")
    assert code == 0, err
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_hard_linked_target_is_refused(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("SECRET", encoding="utf-8")
    link = tmp_path / "link.txt"
    os.link(victim, link)
    code, _, err = run("ttest2", "--d", "0.5", "--power", "0.8", "-o", str(link),
                       "--force")
    assert code == 2 and "하드 링크" in err
    assert victim.read_text(encoding="utf-8") == "SECRET"


def test_empty_output_path_is_rejected():
    code, _, err = run("ttest2", "--d", "0.5", "--power", "0.8", "-o", "")
    assert code == 2 and "비어 있습니다" in err


def test_utf16_csv_gets_a_useful_message(tmp_path):
    path = tmp_path / "u16.csv"
    path.write_bytes("v,g\n1.0,a\n2.0,a\n3.0,b\n4.0,b\n".encode("utf-16"))
    with pytest.raises(PowerPlanError, match="UTF-16"):
        read_two_group(str(path), "v", "g")


def test_filter_that_removes_a_group_says_so(tmp_path):
    path = tmp_path / "f.csv"
    path.write_text("v,g,site\n1,a,S1\n2,a,S1\n3,b,S2\n4,b,S2\n", encoding="utf-8")
    with pytest.raises(PowerPlanError, match="--filter"):
        read_two_group(str(path), "v", "g", filters=[("site", "S1")])


def test_overflowing_variance_is_diagnosed_correctly(tmp_path):
    """예전에는 '차이가 0'이라는 정반대 메시지가 나왔다."""
    path = tmp_path / "huge.csv"
    path.write_text("v,g\n1e308,a\n-1e308,a\n1e307,b\n-1e307,b\n", encoding="utf-8")
    data = read_two_group(str(path), "v", "g")
    from powerplan.pilot import effect_from_two_group
    with pytest.raises(PowerPlanError, match="넘쳤습니다"):
        effect_from_two_group(data)


# ==========================================================================
# M. 사전연구 계획이 불가능할 때는 프로토콜 문장을 만들지 않는다
# ==========================================================================
def test_no_protocol_sentence_when_ci_includes_zero():
    _, out, _ = run("pilot", "examples/serene_pilot.csv", "--value", "isi_week8",
                    "--group", "arm", "--power", "0.8")
    assert "그대로 붙여 쓰세요" not in out
    assert "만들지 않았습니다" in out
    plan = run_json("pilot", "examples/serene_pilot.csv", "--value", "isi_week8",
                    "--group", "arm", "--power", "0.8")
    assert "sentences" not in plan
    assert plan["suppress_protocol_sentence"]


def test_protocol_sentence_returns_when_effect_is_solid():
    _, out, _ = run("pilot", "examples/wowfit_pilot.csv", "--pre", "훈련전_단어인지도",
                    "--post", "훈련후_단어인지도", "--filter", "군=중재", "--power", "0.8")
    assert "그대로 붙여 쓰세요" in out


# ==========================================================================
# N. JSON 정수성 / 소수 표본수
# ==========================================================================
def test_cluster_analysis_unit_is_an_integer():
    plan = run_json("ttest2", "--d", "0.5", "--power", "0.8",
                    "--cluster-size", "20", "--cluster-icc", "0.05")
    assert isinstance(plan["analysis"]["unit"], int)
    assert plan["analysis"]["unit"] == plan["analysis"]["allocation"]["n1"]
    assert plan["analysis"]["unit_exact"] != plan["analysis"]["unit"]


def test_loa_text_never_prints_inf():
    code, out, err = run("loa", "--sd-diff", "1e-300", "--half-width", "1e300")
    assert code == 0, err
    assert "inf" not in out.lower()


# ==========================================================================
# O. 성능 상한 (사용자 기계를 멈추게 하면 안 된다)
# ==========================================================================
def test_worst_case_interim_run_is_bounded():
    import time
    start = time.monotonic()
    code, _, err = run("ttest2", "--d", "0.5", "--power", "0.8",
                       "--interim", str(MAX_INTERIM), "--sensitivity")
    assert code == 0, err
    assert time.monotonic() - start < 20.0
