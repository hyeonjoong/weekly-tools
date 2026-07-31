"""무익성(futility) 경계 — β 소비함수로 정한 비구속적 중단 경계.

검증 전략은 α 소비함수 쪽과 같다: 정의로부터 다시 계산해 맞춰 본다.

1. **정의 검증** — 시점 k까지 무익성으로 멈출 누적확률(대립가설에서)이 정확히
   β*(t_k)여야 한다. 이것이 β 소비함수의 정의 그 자체다.
2. **몬테카를로** — 독립증분 브라운 운동을 직접 굴려 검정력·1종오류율·기대
   정보량·무익성 중단확률을 모두 대조한다.
3. **비구속성** — 무익성 경계를 무시하고 계속 갔을 때 전체 α가 그대로여야 한다
   (이것이 규제기관이 비구속적을 요구하는 이유다).
4. **단조성** — 무익성을 넣으면 검정력이 깎이므로 표본수는 반드시 늘어난다.
"""

from __future__ import annotations

import math
import random
import subprocess
import sys

import pytest

from powerplan import sequential as S
from powerplan.designs import TwoSampleT
from powerplan.solve import Adjustments, make_plan
from powerplan.validate import PowerPlanError

KINDS = ("obf", "pocock", "linear")


# --------------------------------------------------------------------------
# 1. β 소비함수의 정의
# --------------------------------------------------------------------------
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("sides", (1, 2))
@pytest.mark.parametrize("timing", [None, (0.4, 0.7, 1.0), (0.25, 0.6, 1.0)])
def test_cumulative_futility_equals_beta_spending(kind, sides, timing):
    """대립가설에서 무익성으로 멈출 누적확률 = β*(t_k) — 정의 그대로."""
    seq = S.sequential_plan(2, 0.05, sides, 0.85, "obf", timing, kind)
    beta = 1.0 - 0.85
    for i, t in enumerate(seq["timing"][:-1]):
        expected = S._spent(t, beta, kind)
        got = seq["cumulative_futility_h1"][i]
        assert got == pytest.approx(expected, abs=2e-6), (kind, sides, i)
    # 마지막 시점의 무익성 경계는 효능 경계와 같다 (넘지 못하면 곧 실패)
    assert seq["futility_bounds"][-1] == seq["bounds"][-1]
    # 총 2종오류 = β
    assert seq["achieved_power"] == pytest.approx(0.85, abs=1e-4)


@pytest.mark.parametrize("kind", KINDS)
def test_beta_spending_monotone_and_bounded(kind):
    seq = S.sequential_plan(3, 0.05, 2, 0.9, "obf", None, kind)
    cum = seq["cumulative_futility_h1"]
    assert all(b >= a - 1e-12 for a, b in zip(cum, cum[1:]))
    assert cum[-2] < 0.1 + 1e-9          # β = 0.1을 넘겨 쓸 수 없다
    fut, eff = seq["futility_bounds"], seq["bounds"]
    assert all(f <= e + 1e-12 for f, e in zip(fut, eff))   # 무익성 ≤ 효능
    assert all(b >= a - 1e-9 for a, b in zip(fut, fut[1:]))  # 무익성 경계는 상승


# --------------------------------------------------------------------------
# 2. 몬테카를로 (독립증분 브라운 운동)
# --------------------------------------------------------------------------
def _simulate(seq, drift, reps, seed):
    """누적통계량 S_k = Z_k√t_k 를 직접 굴린다 → (기각률, 중간무익률, 기대정보량)."""
    rng = random.Random(seed)
    t, eff, fut = seq["timing"], seq["bounds"], seq["futility_bounds"]
    steps = [t[0]] + [t[i] - t[i - 1] for i in range(1, len(t))]
    sds = [math.sqrt(s) for s in steps]
    roots = [math.sqrt(x) for x in t]
    last = len(t) - 1
    rejects = interim_futile = 0
    info = 0.0
    for _ in range(reps):
        s = 0.0
        for k in range(len(t)):
            s += rng.gauss(drift * steps[k], sds[k])
            z = s / roots[k]
            if z >= eff[k]:
                rejects += 1
                info += t[k]
                break
            if fut is not None and z <= fut[k]:
                if k < last:
                    interim_futile += 1
                info += t[k]
                break
            if k == last:
                info += t[k]
    return rejects / reps, interim_futile / reps, info / reps


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("sides", (1, 2))
def test_monte_carlo_matches_power_alpha_and_expected_information(kind, sides):
    seq = S.sequential_plan(2, 0.05, sides, 0.85, "obf", (0.4, 0.7, 1.0), kind)
    reps = 200_000
    tol = 4.0 / math.sqrt(reps)          # ≈ 4 표준오차

    power, futile_h1, info_h1 = _simulate(seq, seq["drift"], reps, seed=20260731)
    assert power == pytest.approx(0.85, abs=tol)
    assert futile_h1 == pytest.approx(seq["cumulative_futility_h1"][-2], abs=tol)
    assert info_h1 == pytest.approx(seq["expected_fraction_h1"], abs=0.01)

    alpha_up, futile_h0, info_h0 = _simulate(seq, 0.0, reps, seed=1234567)
    assert alpha_up == pytest.approx(seq["alpha_if_honored"], abs=tol)
    assert futile_h0 == pytest.approx(seq["cumulative_futility_h0"][-2], abs=tol)
    assert info_h0 == pytest.approx(seq["expected_fraction_h0"], abs=0.01)


# --------------------------------------------------------------------------
# 3. 비구속성 (non-binding) — 규제기관이 요구하는 성질
# --------------------------------------------------------------------------
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("sides", (1, 2))
def test_efficacy_bounds_are_unchanged_by_futility(kind, sides):
    """효능 경계는 무익성과 **무관하게** 정해진다 — 그래서 비구속적이다."""
    base = S.sequential_plan(2, 0.05, sides, 0.85, "obf", (0.4, 0.7, 1.0))
    with_f = S.sequential_plan(2, 0.05, sides, 0.85, "obf", (0.4, 0.7, 1.0), kind)
    assert with_f["bounds"] == base["bounds"]
    assert with_f["nominal_p"] == base["nominal_p"]
    assert with_f["cumulative_alpha"] == base["cumulative_alpha"]
    # 무익성 경계를 무시하고 계속 가도 전체 α는 그대로
    assert with_f["achieved_alpha"] == pytest.approx(0.05, abs=2e-4)
    # 지키면 더 보수적 (효능 방향만 세므로 양측에서는 대략 절반)
    assert with_f["alpha_if_honored"] < with_f["achieved_alpha"] + 1e-12


@pytest.mark.parametrize("kind", KINDS)
def test_futility_increases_sample_size_and_cuts_expected_size(kind):
    base = S.sequential_plan(1, 0.05, 2, 0.8, "obf")
    with_f = S.sequential_plan(1, 0.05, 2, 0.8, "obf", None, kind)
    assert with_f["inflation"] > base["inflation"]        # 검정력 손실만큼 커진다
    # 대신 효과가 없을 때 기대 정보량은 확 줄어든다 — 무익성 경계를 넣는 이유
    assert with_f["expected_fraction_h0"] < base["expected_fraction_h0"] - 0.05


def test_pocock_stops_earlier_than_obf_under_the_null():
    """Pocock 형 β 소비는 초기부터 적극적으로 멈춘다 — OBF보다 첫 경계가 높다."""
    obf = S.sequential_plan(2, 0.05, 2, 0.85, "obf", (0.4, 0.7, 1.0), "obf")
    poc = S.sequential_plan(2, 0.05, 2, 0.85, "obf", (0.4, 0.7, 1.0), "pocock")
    assert poc["futility_bounds"][0] > obf["futility_bounds"][0]
    assert poc["cumulative_futility_h0"][0] > obf["cumulative_futility_h0"][0]
    assert poc["inflation"] > obf["inflation"]   # 더 자주 멈추니 더 많이 물어야 한다


# --------------------------------------------------------------------------
# 4. 경계 사례
# --------------------------------------------------------------------------
def test_two_sided_futility_never_below_the_harm_boundary():
    """양측설계에서 −a_k 아래는 이미 '해' 효능 경계다 — 무익성은 그 위에 있어야."""
    seq = S.sequential_plan(3, 0.05, 2, 0.99, "pocock", None, "obf")
    for f, e in zip(seq["futility_bounds"], seq["bounds"]):
        assert f >= -e - 1e-9


@pytest.mark.parametrize("power", (0.5, 0.7, 0.8, 0.9, 0.95, 0.99))
def test_achieved_power_hits_the_target_across_the_range(power):
    seq = S.sequential_plan(2, 0.05, 2, power, "obf", None, "pocock")
    assert seq["achieved_power"] == pytest.approx(power, abs=5e-4)


@pytest.mark.parametrize("interim", (1, 2, 5, 10))
def test_many_looks_stay_consistent(interim):
    seq = S.sequential_plan(interim, 0.05, 1, 0.9, "obf", None, "linear")
    assert seq["achieved_power"] == pytest.approx(0.9, abs=1e-3)
    assert seq["achieved_alpha"] == pytest.approx(0.05, abs=1e-3)
    assert len(seq["futility_bounds"]) == interim + 1


def test_power_from_fixed_accounts_for_futility():
    """--n 경로: 같은 n에서 무익성이 있으면 검정력이 더 낮아야 한다."""
    base = S.sequential_plan(1, 0.05, 2, 0.8, "obf")
    with_f = S.sequential_plan(1, 0.05, 2, 0.8, "obf", None, "pocock")
    for fixed in (0.3, 0.5, 0.8, 0.95):
        p_base = S.power_from_fixed(base, fixed)
        p_fut = S.power_from_fixed(with_f, fixed)
        assert p_fut < p_base
        assert 0.0 <= p_fut <= 1.0
    # 효과가 0인 자리에서도 확률이지 음수가 아니다
    assert 0.0 <= S.power_from_fixed(with_f, 0.05) <= 0.05


def test_power_from_fixed_recovers_the_design_power():
    design = TwoSampleT(0.5)
    seq = S.sequential_plan(1, 0.05, 2, 0.8, "obf", None, "obf")
    # 설계 표본수에서의 고정설계 검정력 → 군차별 검정력이 목표 부근이어야 한다
    plan = make_plan(design, target_power=0.8,
                     adjustments=Adjustments(interim=1, futility="obf"))
    assert plan["achieved_power"] >= 0.8
    assert plan["sequential"]["futility"] == "obf"
    assert seq["inflation"] > 1.0


def test_invalid_futility_kind_is_rejected():
    with pytest.raises(PowerPlanError, match="--futility"):
        S.sequential_plan(1, 0.05, 2, 0.8, "obf", None, "bogus")
    with pytest.raises(PowerPlanError, match="--futility"):
        S.check_futility("obf ")
    with pytest.raises(PowerPlanError, match="--futility"):
        S.check_futility(3)
    assert S.check_futility(None) is None


def test_futility_requires_interim():
    with pytest.raises(PowerPlanError, match="--interim"):
        Adjustments(futility="obf")


def test_no_futility_keeps_the_old_output_shape():
    """무익성을 안 쓰면 예전 계획과 숫자가 한 자리도 달라지지 않아야 한다."""
    seq = S.sequential_plan(2, 0.05, 2, 0.9, "obf", (0.3, 0.6, 1.0))
    assert seq["futility"] is None
    assert seq["futility_bounds"] is None
    assert "alpha_if_honored" not in seq
    assert seq["achieved_alpha"] == pytest.approx(0.05, abs=1e-4)
    assert seq["achieved_power"] == pytest.approx(0.9, abs=1e-4)


# --------------------------------------------------------------------------
# 5. CLI · 보고서
# --------------------------------------------------------------------------
def _run(*argv):
    return subprocess.run([sys.executable, "-m", "powerplan.cli", *argv],
                          capture_output=True, text=True)


def test_cli_reports_futility_columns_and_protocol_sentence():
    out = _run("ttest2", "--d", "0.5", "--power", "0.8",
               "--interim", "1", "--futility", "obf")
    assert out.returncode == 0, out.stderr
    assert "무익성 Z" in out.stdout
    assert "비구속적" in out.stdout
    assert "non-binding" in out.stdout.lower()
    # 프로토콜 문장(EN)에도 들어가야 한다
    assert "futility bound" in out.stdout.lower()


def test_cli_json_carries_futility_fields():
    import json
    out = _run("prop2", "--p1", "0.3", "--p2", "0.5", "--power", "0.9",
               "--interim", "2", "--futility", "pocock", "--format", "json")
    assert out.returncode == 0, out.stderr
    seq = json.loads(out.stdout)["sequential"]
    assert seq["futility"] == "pocock"
    assert len(seq["futility_bounds"]) == 3
    assert seq["looks_detail"][0]["futility_z"] == pytest.approx(
        seq["futility_bounds"][0])
    assert seq["alpha_if_honored"] < seq["achieved_alpha"]


def test_cli_markdown_table_has_futility_columns():
    out = _run("ttest2", "--d", "0.6", "--power", "0.85", "--interim", "1",
               "--futility", "linear", "--format", "md")
    assert out.returncode == 0, out.stderr
    assert "무익성 Z" in out.stdout
    header = [ln for ln in out.stdout.splitlines() if ln.startswith("| 시점")][0]
    sep = out.stdout.splitlines()[out.stdout.splitlines().index(header) + 1]
    assert header.count("|") == sep.count("|")   # 마크다운 표가 깨지지 않는다


def test_cli_rejects_futility_without_interim():
    out = _run("ttest2", "--d", "0.5", "--power", "0.8", "--futility", "obf")
    assert out.returncode != 0
    assert "--interim" in out.stdout + out.stderr


def test_cli_futility_needs_a_known_kind():
    out = _run("ttest2", "--d", "0.5", "--power", "0.8",
               "--interim", "1", "--futility", "haybittle")
    assert out.returncode != 0
    assert "obf" in (out.stdout + out.stderr)


def test_futility_sample_size_is_larger_than_efficacy_only():
    design = TwoSampleT(0.4)
    eff_only = make_plan(design, target_power=0.9,
                         adjustments=Adjustments(interim=2))
    with_fut = make_plan(design, target_power=0.9,
                         adjustments=Adjustments(interim=2, futility="obf"))
    assert (with_fut["analysis"]["allocation"]["total"]
            > eff_only["analysis"]["allocation"]["total"])
    assert with_fut["achieved_power"] >= 0.9


# --------------------------------------------------------------------------
# 6. 조건부 검정력 (DSMB가 실제로 읽는 숫자)
# --------------------------------------------------------------------------
def test_conditional_power_matches_direct_simulation():
    """CP = P(최종 Z ≥ a_K | 중간 Z = z)를 브라운 운동으로 직접 확인."""
    rng = random.Random(31337)
    a_final, t, z, drift = 2.0152, 1.0 / 3.0, 0.5, 3.3
    got = S.conditional_power(a_final, t, z, drift)
    reps, hits = 200_000, 0
    s_now = z * math.sqrt(t)
    rest = 1.0 - t
    for _ in range(reps):
        s_end = s_now + rng.gauss(drift * rest, math.sqrt(rest))
        if s_end >= a_final:
            hits += 1
    assert got == pytest.approx(hits / reps, abs=4.0 / math.sqrt(reps))


def test_conditional_power_edges_and_monotonicity():
    assert S.conditional_power(2.0, 1.0, 2.5, 3.0) == 1.0     # 최종시점, 이미 넘음
    assert S.conditional_power(2.0, 1.0, 1.5, 3.0) == 0.0     # 최종시점, 못 넘음
    # z가 클수록, drift가 클수록 CP는 커진다
    base = S.conditional_power(2.0, 0.5, 1.0, 2.0)
    assert S.conditional_power(2.0, 0.5, 1.5, 2.0) > base
    assert S.conditional_power(2.0, 0.5, 1.0, 3.0) > base
    assert 0.0 <= S.conditional_power(2.0, 0.5, -5.0, 0.0) <= 1.0


def test_plan_reports_conditional_power_at_the_futility_boundary():
    seq = S.sequential_plan(2, 0.05, 2, 0.9, "obf", None, "obf")
    assert len(seq["cp_at_futility_alt"]) == 2       # 중간분석 시점에만
    assert len(seq["cp_at_futility_trend"]) == 2
    for alt, trend in zip(seq["cp_at_futility_alt"], seq["cp_at_futility_trend"]):
        assert 0.0 <= trend < alt <= 1.0             # 추세 기준이 언제나 더 비관적
    # 손으로 다시 계산
    t, b, aK, mu = seq["timing"][0], seq["futility_bounds"][0], seq["bounds"][-1], seq["drift"]
    expected = 1.0 - S.norm_cdf((aK - b * math.sqrt(t) - mu * (1 - t)) / math.sqrt(1 - t))
    assert seq["cp_at_futility_alt"][0] == pytest.approx(expected, abs=1e-12)


def test_cli_shows_conditional_power():
    out = _run("ttest2", "--d", "0.5", "--power", "0.9",
               "--interim", "2", "--futility", "obf")
    assert out.returncode == 0, out.stderr
    assert "조건부 검정력" in out.stdout


# --------------------------------------------------------------------------
# 7. 돌연변이 검사에서 살아남은 것들을 사살한다 (라운드 4 검토)
# --------------------------------------------------------------------------
def test_futility_bounds_are_numerically_converged():
    """S1 — `_TAIL_SIGMAS`·격자수는 결과에 영향이 없어야 한다(수치 절단일 뿐).

    검토에서 `_TAIL_SIGMAS`를 10 → 3으로 바꿔도 1027개 테스트가 다 통과했다.
    실제로는 무익성 경계가 2.17이나 움직이는 설정이 있다 — 그걸 못 잡았다.
    """
    args = (3, 0.05, 2, 0.99, "pocock", None, "obf")
    ref = S.sequential_plan(*args)
    original = S._TAIL_SIGMAS
    try:
        for sigmas in (6.0, 10.0, 15.0):
            S._TAIL_SIGMAS = sigmas
            S.sequential_plan.cache_clear()
            got = S.sequential_plan(*args)
            for a, b in zip(ref["futility_bounds"], got["futility_bounds"]):
                assert abs(a - b) < 1e-6, (sigmas, a, b)
            for a, b in zip(ref["cumulative_futility_h0"], got["cumulative_futility_h0"]):
                assert abs(a - b) < 1e-8, (sigmas, a, b)
            assert abs(ref["inflation"] - got["inflation"]) < 1e-6
    finally:
        S._TAIL_SIGMAS = original
        S.sequential_plan.cache_clear()


def test_futility_bounds_are_grid_converged():
    """S1 — Simpson 격자를 촘촘히 해도 경계가 같아야 한다."""
    timing = (0.3, 0.6, 1.0)
    eff = S._solve_bounds(timing, 0.05, "obf", True)
    coarse = S._futility_bounds(eff, timing, 3.5, True, "obf", 0.1, npts=S._GRID)
    fine = S._futility_bounds(eff, timing, 3.5, True, "obf", 0.1, npts=321)
    assert all(abs(a - b) < 1e-5 for a, b in zip(coarse, fine)), (coarse, fine)


def test_per_look_futility_stops_are_the_increments_of_the_cumulative():
    """S2 — `futility_stop_h0`/`h1`을 서로 바꿔치기해도 아무 테스트가 안 깨졌다."""
    seq = S.sequential_plan(2, 0.05, 2, 0.85, "obf", (0.4, 0.7, 1.0), "pocock")
    for key, cum_key in (("futility_stop_h0", "cumulative_futility_h0"),
                         ("futility_stop_h1", "cumulative_futility_h1")):
        cum = seq[cum_key]
        prev = 0.0
        for i, per in enumerate(seq[key]):
            assert per == pytest.approx(cum[i] - prev, abs=1e-12), (key, i)
            prev = cum[i]
    # 귀무가설에서 훨씬 자주 무익성으로 멈춘다 — 두 열이 뒤바뀌면 여기서 죽는다
    assert seq["cumulative_futility_h0"][-2] > 5 * seq["cumulative_futility_h1"][-2]
    # 경계표에도 같은 값이 실려야 한다
    detail = make_plan(TwoSampleT(0.5), target_power=0.85,
                       adjustments=Adjustments(interim=2, timing=(0.4, 0.7, 1.0),
                                               futility="pocock"))["sequential"]
    for i, row in enumerate(detail["looks_detail"]):
        assert row["futility_stop_h0"] == pytest.approx(seq["futility_stop_h0"][i])
        assert row["futility_stop_h1"] == pytest.approx(seq["futility_stop_h1"][i])


def test_prose_uses_the_last_interim_not_the_final_look():
    """S3 — 산문 속 `[-2]`를 `[-1]`로 바꿔도 안 깨졌다. 이제 숫자를 직접 읽는다."""
    seq = S.sequential_plan(2, 0.05, 2, 0.85, "obf", (0.4, 0.7, 1.0), "obf")
    note = [n for n in S.sequential_notes(seq) if "무익성(futility) 중단 경계" in n][0]
    assert f"{seq['cumulative_futility_h0'][-2]:.1%}" in note
    assert f"{seq['cumulative_futility_h1'][-2]:.1%}" in note
    # 최종 시점 값(무익성 경계 = 효능 경계라 훨씬 크다)이 새어 나오면 안 된다
    assert seq["cumulative_futility_h0"][-1] > seq["cumulative_futility_h0"][-2] + 0.05
    assert f"{seq['cumulative_futility_h0'][-1]:.1%}" not in note


def test_beta_bookkeeping_uses_the_actual_spend_not_the_nominal():
    """S4 — 누적 기준은 계획량 β*(t)가 아니라 **실제로 쓴 양**이어야 한다.

    양측설계에서 무익성 경계는 −a_k 아래로 못 내려간다(그 아래는 이미 해 방향
    효능 경계다). 검정력이 아주 높으면 첫 시점의 β 계획량이 그 하한보다 작아
    경계가 −a_1에 걸리고, 그러면 계획보다 **더** 쓰게 된다 — 하한 아래 질량은
    어차피 멈추기 때문이다. 그 초과분은 다음 시점 목표에서 빠져야 한다.
    """
    beta = 1.0 - 0.99
    seq = S.sequential_plan(3, 0.05, 2, 0.99, "pocock", None, "obf")
    clamped = [i for i, (f, e) in enumerate(zip(seq["futility_bounds"][:-1],
                                                seq["bounds"][:-1]))
               if abs(f + e) < 1e-6]
    assert clamped, "이 설정에서는 하한에 걸리는 시점이 있어야 한다"
    # 첫 시점은 계획량을 초과해서 쓴다 (하한 때문에 어쩔 수 없다)
    assert seq["cumulative_futility_h1"][0] > S._spent(seq["timing"][0], beta, "obf")
    # 그 이후 시점은 **실제 누적**을 기준으로 목표를 잡는다 — 이것이 이월 규칙이다.
    # 계획량 기준(β*(t_k) − β*(t_{k−1}))이었다면 누적이 β*(t_k)를 넘었을 것이다.
    for i, t in enumerate(seq["timing"][1:-1], start=1):
        assert seq["cumulative_futility_h1"][i] == pytest.approx(
            S._spent(t, beta, "obf"), abs=1e-9), i
    # 어떤 경우에도 interim 전체에서 β를 넘겨 쓰지 않는다
    assert seq["cumulative_futility_h1"][-2] < beta


def test_derived_futility_fields_are_what_they_claim():
    """S5 — cumulative_beta·futility_nominal_p·binding에 아무 단언이 없었다."""
    seq = S.sequential_plan(2, 0.05, 1, 0.9, "obf", (0.3, 0.6, 1.0), "linear")
    beta = 1.0 - 0.9
    for i, t in enumerate(seq["timing"][:-1]):
        assert seq["cumulative_beta"][i] == pytest.approx(S._spent(t, beta, "linear"))
    assert seq["cumulative_beta"][-1] == pytest.approx(beta)
    for p, b in zip(seq["futility_nominal_p"], seq["futility_bounds"]):
        assert p == pytest.approx(1.0 - S.norm_cdf(b), abs=1e-15)
    assert seq["binding"] is False
    assert seq["beta_spent_interim"] == pytest.approx(
        seq["cumulative_futility_h1"][-2], abs=1e-12)


def test_power_loss_is_not_the_beta_spent():
    """검토 4번 — β 소비량을 '검정력 손실'이라 부르면 4배쯤 과장된다."""
    for fut in KINDS:
        seq = S.sequential_plan(1, 0.05, 2, 0.9, "pocock", None, fut)
        assert seq["power_loss"] < seq["beta_spent_interim"], fut
        assert seq["power_same_n"] == pytest.approx(0.9 - seq["power_loss"], abs=1e-12)
        # 같은 표본수에 규칙만 얹은 것이므로 목표보다 낮되 크게 낮지는 않다
        assert 0.0 < seq["power_loss"] < 0.1
    # OBF β 소비에서는 β 소비 2.0% 대비 실제 손실이 1%p 미만이어야 한다
    obf = S.sequential_plan(1, 0.05, 2, 0.9, "pocock", None, "obf")
    assert obf["beta_spent_interim"] == pytest.approx(0.02, abs=1e-4)
    assert obf["power_loss"] < 0.01


def test_no_futility_output_is_bit_identical_to_the_pre_futility_design():
    """무익성을 안 쓰면 3라운드까지의 숫자가 한 자리도 안 바뀌어야 한다 (골든값)."""
    seq = S.sequential_plan(1, 0.05, 2, 0.9, "pocock")
    assert seq["bounds"][0] == pytest.approx(2.1570, abs=5e-5)
    assert seq["bounds"][1] == pytest.approx(2.2010, abs=5e-5)
    assert seq["inflation"] == pytest.approx(1.1110, abs=5e-5)
    assert seq["stop_prob_h1"][0] == pytest.approx(0.602, abs=5e-4)


def test_rendered_text_table_numbers_match_the_plan():
    """S2·S3 — 렌더된 표의 숫자를 직접 읽어 계획값과 대조한다."""
    from powerplan.report import render_text
    plan = make_plan(TwoSampleT(0.5), target_power=0.9,
                     adjustments=Adjustments(interim=1, spending="pocock",
                                             futility="obf"))
    text = render_text(plan)
    seq = plan["sequential"]
    row = seq["looks_detail"][0]
    line = [ln for ln in text.splitlines() if ln.strip().startswith("중간 1")][0]
    assert f"{row['futility_z']:.4f}" in line
    assert f"{row['futility_stop_h0']:.1%}" in line
    assert f"{row['bound_z']:.4f}" in line
    assert f"{seq['power_same_n']:.1%}" in text
    assert f"{seq['cumulative_futility_h0'][-2]:.1%}" in text


# --------------------------------------------------------------------------
# 8. 라운드 4 검토에서 나온 결함 (엣지케이스 검토자)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("timing", [
    (0.5, 0.500001, 1.0),      # 확률 478%, 표본수 44명(고정설계 128명)이 나오던 값
    (0.001, 1.0),
    (0.999, 1.0),
    (1e-12, 1.0),
])
def test_degenerate_timing_is_rejected_not_computed(timing):
    """간격이 촘촘하면 합성곱이 앨리어싱을 일으킨다 — 계산하지 말고 거절해야 한다."""
    with pytest.raises(PowerPlanError, match="촘촘"):
        S.sequential_plan(len(timing) - 1, 0.05, 2, 0.8, "obf", timing, "obf")
    with pytest.raises(PowerPlanError, match="촘촘"):
        S.check_timing(len(timing) - 1, timing)


def test_probabilities_never_exceed_one_across_allowed_timings():
    """거절선 안쪽에서는 어떤 조합에서도 확률이 1을 넘지 않는다."""
    for timing in [None, (0.25, 0.5, 0.75, 1.0), (0.01, 0.5, 1.0), (0.9, 0.95, 1.0)]:
        for fut in KINDS:
            for sides in (1, 2):
                seq = S.sequential_plan(3 if timing is None else len(timing) - 1,
                                        0.05, sides, 0.85, "obf", timing, fut)
                for key in ("stop_prob_h1", "stop_prob_h0",
                            "futility_stop_h0", "futility_stop_h1"):
                    assert all(-1e-9 <= p <= 1.0 + 1e-9 for p in seq[key]), (key, seq[key])
                    assert math.fsum(seq[key]) <= 1.0 + 1e-6
                assert 0.0 <= seq["achieved_power"] <= 1.0
                assert seq["power_same_n"] <= 1.0


def test_degenerate_timing_never_shrinks_below_the_fixed_design():
    """허용 구간 안에서 군차별설계 표본수는 절대 고정설계보다 작을 수 없다."""
    for timing in [(0.01, 1.0), (0.5, 1.0), (0.98, 1.0)]:
        for fut in (None, "obf", "pocock"):
            plan = make_plan(TwoSampleT(0.5), target_power=0.8,
                             adjustments=Adjustments(interim=1, timing=timing,
                                                     futility=fut))
            assert (plan["analysis"]["allocation"]["total"]
                    >= plan["fixed_design"]["allocation"]["total"]), (timing, fut)


def test_futility_with_given_n_requires_an_explicit_target_power():
    """무익성 경계는 β 소비함수로 정하므로 목표 검정력 없이는 정의되지 않는다.

    예전에는 조용히 80%를 가정해, --power를 바꾸면 **같은 설계의 달성 검정력이**
    85.8% ~ 93.4%로 흔들렸다.
    """
    with pytest.raises(PowerPlanError, match="--power"):
        make_plan(TwoSampleT(0.5), unit=100,
                  adjustments=Adjustments(interim=2, futility="obf"))
    # 목표 검정력을 주면 정상 동작하고, 무익성이 없으면 예전처럼 --power가 없어도 된다
    ok = make_plan(TwoSampleT(0.5), unit=100, target_power=0.9,
                   adjustments=Adjustments(interim=2, futility="obf"))
    assert ok["sequential"]["futility"] == "obf"
    plain = make_plan(TwoSampleT(0.5), unit=100,
                      adjustments=Adjustments(interim=2))
    assert plain["sequential"]["futility"] is None


def test_alpha_comparison_is_like_for_like():
    """0.05 → 0.022가 아니라 0.025 → 0.022가 옳은 비교다."""
    seq = S.sequential_plan(1, 0.05, 2, 0.9, "obf", None, "obf")
    assert seq["alpha_upper_nominal"] == pytest.approx(0.025, abs=5e-4)
    assert seq["alpha_if_honored"] < seq["alpha_upper_nominal"]
    note = [n for n in S.sequential_notes(seq) if "비구속" in n][0]
    assert f"{seq['alpha_upper_nominal']:.4g}" in note
    # 단측 설계에서는 두 값의 기준이 α 자체다
    one = S.sequential_plan(1, 0.05, 1, 0.9, "obf", None, "obf")
    assert one["alpha_upper_nominal"] == pytest.approx(0.05, abs=5e-4)


def test_futility_clamped_to_the_harm_boundary_is_flagged():
    """무익성 경계가 해(harm) 경계와 같으면 추가 규칙이 없다고 알려야 한다."""
    seq = S.sequential_plan(1, 0.1, 2, 0.8, "pocock", (0.1, 1.0), "obf")
    assert seq["futility_bounds"][0] == pytest.approx(-seq["bounds"][0], abs=1e-6)
    assert seq["futility_at_harm_bound"][0] is True
    assert seq["futility_at_harm_bound"][-1] is False
    out = _run("ttest2", "--d", "0.5", "--power", "0.8", "--alpha", "0.1",
               "--spending", "pocock", "--interim", "1", "--timing", "0.1",
               "--futility", "obf")
    assert out.returncode == 0, out.stderr
    assert "해(harm) 방향 효능 경계와 일치" in out.stdout
    # 걸리지 않는 보통 설계에서는 그 문구가 나오면 안 된다
    normal = _run("ttest2", "--d", "0.5", "--power", "0.9", "--interim", "1",
                  "--futility", "obf")
    assert "해(harm) 방향 효능 경계와 일치" not in normal.stdout


def test_euro_ro_josa_after_rieul_final_digits():
    """일·칠·팔은 ㄹ 종성이라 '로'를 쓴다 — '0.01961으로'는 틀린 표기였다."""
    from powerplan.korean import has_rieul_final, josa
    for value in ("0.01961", "0.028", "0.02477", "0.0001", "0.7", "0.8", "0.21"):
        assert josa(value, "으로", "로") == value + "로", value
        assert has_rieul_final(value)
        # 을/를·이/가는 그대로 종성 규칙을 따른다 (일**을**, 팔**을**)
        assert josa(value, "을", "를") == value + "을", value
    for value in ("0.03", "0.06", "0.10", "0.13"):     # 삼·육·영 → ㅁ/ㄱ/ㅇ
        assert josa(value, "으로", "로") == value + "으로", value
        assert not has_rieul_final(value)
    for value in ("0.5", "0.2", "0.4", "0.9"):         # 종성 없음
        assert josa(value, "으로", "로") == value + "로", value
    assert josa("서울", "으로", "로") == "서울로"       # 한글 ㄹ 종성
    assert josa("부산", "으로", "로") == "부산으로"
    assert josa("Excel", "으로", "로") == "Excel로"     # 영문 l → '엘'


def test_cli_never_prints_euro_after_rieul():
    """실제 출력에 '1으로/7으로/8으로'가 새어 나오지 않는지."""
    import re
    for argv in (["ttest2", "--d", "0.5", "--power", "0.9", "--interim", "1",
                  "--futility", "obf"],
                 ["ttest2", "--d", "0.5", "--power", "0.8", "--alpha", "0.0001"],
                 ["survival", "--hr", "0.6", "--power", "0.8", "--median1", "12",
                  "--accrual", "24", "--followup", "12", "--interim", "1",
                  "--futility", "pocock"]):
        out = _run(*argv)
        assert out.returncode == 0, out.stderr
        bad = re.findall(r"[178]으로", out.stdout)
        assert not bad, (argv, bad)
