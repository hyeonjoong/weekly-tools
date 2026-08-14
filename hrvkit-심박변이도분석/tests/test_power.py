"""표본수·검정력 계획(hrvkit.power) 테스트.

검증 전략
---------
1) **닫힌 형태 항등식**: ncp=0 이면 기존 student_t_cdf 와 일치하고,
   t=0 에서는 Φ(−δ), 그리고 반사 항등식 F(t;δ)=1−F(−t;−δ) 가 성립해야 합니다.
   적분기 자체의 정확도를 근사 없이 확인합니다.
2) **R power.t.test 표본수와 대조**: 널리 검증된 구현이 내는 **필요 표본수**와
   일치하는지 확인합니다(_R_REQUIRED_N — R 4.x `stats::power.t.test` 출력).
   각 표본수에서의 검정력 값은 R 출력을 인용하지 않고 이 구현의 값을 회귀
   기준으로 고정합니다(_POWER_PINS) — 확인하지 않은 값을 출처처럼 적지 않기
   위함입니다.
3) **몬테카를로**: 검정력을 실제로 표본을 뽑아 t 검정을 돌려 재현합니다 —
   공식이 아니라 정의로부터 확인하는 완전히 독립적인 검사입니다.
"""

import math
import random
import statistics
import subprocess
import sys

import pytest

from hrvkit.power import (MAX_N, NONPARAM_ARE, detectable_delta, hedges_j,
                          inflate_for_dropout, min_exact_n, noncentral_t_cdf,
                          plan_paired, plan_parallel, power_grid, required_n,
                          t_test_power)
from hrvkit.report import (power_plan_groups, power_plan_paired,
                           power_plan_to_csv, render_plan, render_power_plan)
from hrvkit.stats import student_t_cdf, student_t_ppf


# --------------------------------------------------------------------------- #
# 1) 비중심 t CDF
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("df", [1, 2, 3, 5, 12, 30, 200, 5000])
@pytest.mark.parametrize("t", [-4.0, -2.5, -0.3, 0.0, 1.0, 3.3, 7.0])
def test_noncentral_t_reduces_to_central_t(df, t):
    """ncp=0 이면 중심 t 분포 CDF 와 같아야 합니다."""
    assert noncentral_t_cdf(t, df, 0.0) == pytest.approx(
        student_t_cdf(t, df), abs=1e-7)


def test_noncentral_t_monotone_in_t_and_ncp():
    prev = -1.0
    for t in [-5.0, -2.0, 0.0, 2.0, 5.0, 9.0]:
        v = noncentral_t_cdf(t, 20, 2.0)
        assert v >= prev - 1e-12
        prev = v
    # ncp 가 커지면 분포가 오른쪽으로 밀려 같은 t 에서의 CDF 는 줄어듭니다.
    vals = [noncentral_t_cdf(2.0, 20, ncp) for ncp in (0.0, 1.0, 2.0, 4.0)]
    assert all(a >= b for a, b in zip(vals, vals[1:]))


def test_noncentral_t_bounds_and_bad_df():
    assert 0.0 <= noncentral_t_cdf(0.0, 10, 3.0) <= 1.0
    assert noncentral_t_cdf(float("inf"), 10, 3.0) == 1.0
    assert noncentral_t_cdf(float("-inf"), 10, 3.0) == 0.0
    with pytest.raises(ValueError):
        noncentral_t_cdf(0.0, 0, 1.0)


@pytest.mark.parametrize("df", [1, 4, 20, 300])
@pytest.mark.parametrize("ncp", [0.5, 2.0, 4.0])
def test_noncentral_t_at_zero_equals_normal_tail(df, ncp):
    """t=0 에서는 닫힌 형태가 있습니다: P(T' ≤ 0) = P(Z + δ ≤ 0) = Φ(−δ).

    T' = (Z+δ)/√(V/df) 이고 √(V/df) > 0 이므로 부호는 분자만으로 정해집니다 —
    자유도와 무관하게 성립하는 항등식이라 적분기의 강한 검증입니다.
    """
    assert noncentral_t_cdf(0.0, df, ncp) == pytest.approx(
        0.5 * math.erfc(ncp / math.sqrt(2.0)), abs=1e-9)


@pytest.mark.parametrize("df", [2, 7, 40])
@pytest.mark.parametrize("t,ncp", [(1.3, 2.0), (-0.7, 1.0), (3.0, 0.5)])
def test_noncentral_t_reflection_identity(df, t, ncp):
    """반사 항등식: F(t; df, δ) = 1 − F(−t; df, −δ).

    T'(df, −δ) = −T'(df, δ) 이므로 분포 자체에서 따라 나오는 성질입니다.
    """
    assert noncentral_t_cdf(t, df, ncp) == pytest.approx(
        1.0 - noncentral_t_cdf(-t, df, -ncp), abs=1e-9)


# --------------------------------------------------------------------------- #
# 2) R power.t.test 기준값
# --------------------------------------------------------------------------- #
# R 4.x `stats::power.t.test` 가 내는 필요 표본수 (ceiling 적용).
# (d, design, alpha, target_power, 필요 n)
_R_REQUIRED_N = [
    (0.5, "paired", 0.05, 0.80, 34),
    (0.2, "paired", 0.05, 0.80, 199),
    (1.0, "paired", 0.05, 0.90, 13),
    (0.5, "parallel", 0.05, 0.80, 64),
    (0.8, "parallel", 0.05, 0.80, 26),
    (0.3, "parallel", 0.05, 0.80, 176),
]


@pytest.mark.parametrize("d,design,alpha,target,n_exp", _R_REQUIRED_N)
def test_required_n_matches_r_power_t_test(d, design, alpha, target, n_exp):
    assert required_n(d, target_power=target, design=design,
                      alpha=alpha) == n_exp


# 위 표본수에서의 검정력 — 이 구현이 낸 값을 회귀 기준으로 고정합니다
# (정확성 자체는 중심 t 대조·반사 항등식·몬테카를로가 독립적으로 보증).
_POWER_PINS = [
    (0.5, "paired", 34, 0.807778),
    (0.2, "paired", 199, 0.801691),
    (1.0, "paired", 13, 0.910708),
    (0.5, "parallel", 64, 0.801460),
    (0.8, "parallel", 26, 0.807487),
    (0.3, "parallel", 176, 0.801379),
]


@pytest.mark.parametrize("d,design,n,pow_exp", _POWER_PINS)
def test_power_regression_pins(d, design, n, pow_exp):
    assert t_test_power(d, n, design=design, alpha=0.05) == pytest.approx(
        pow_exp, abs=1e-5)
    # 그 표본수는 목표를 넘고 하나 적으면 못 넘어야 합니다.
    assert t_test_power(d, n - 1, design=design, alpha=0.05) < pow_exp


def test_required_n_is_the_minimum():
    """반환된 n 은 목표를 만족하는 **최소값** 이어야 합니다."""
    for d, design in ((0.45, "paired"), (0.6, "parallel"), (0.25, "paired")):
        n = required_n(d, target_power=0.8, design=design)
        assert t_test_power(d, n, design=design) >= 0.8
        assert t_test_power(d, n - 1, design=design) < 0.8


def test_power_is_monotone_in_n_and_d():
    prev = 0.0
    for n in range(3, 60, 4):
        p = t_test_power(0.5, n, design="paired")
        assert p >= prev - 1e-12
        prev = p
    ps = [t_test_power(d, 20, design="paired") for d in (0.1, 0.3, 0.6, 1.2)]
    assert all(a <= b for a, b in zip(ps, ps[1:]))


def test_power_sign_symmetric_and_alpha_floor():
    assert t_test_power(-0.6, 25) == pytest.approx(t_test_power(0.6, 25))
    # d=0 이면 검정력 = 유의수준 (제1종 오류율).
    assert t_test_power(1e-12, 40, alpha=0.05) == pytest.approx(0.05, abs=1e-6)


# --------------------------------------------------------------------------- #
# 3) 몬테카를로 — 공식이 아니라 정의로부터 검정력 재현
# --------------------------------------------------------------------------- #
def _mc_power_paired(d, n, alpha, trials, seed):
    rng = random.Random(seed)
    tcrit = student_t_ppf(1.0 - alpha / 2.0, n - 1)
    hits = 0
    for _ in range(trials):
        xs = [rng.gauss(d, 1.0) for _ in range(n)]
        m = statistics.fmean(xs)
        s = statistics.stdev(xs)
        if s > 0 and abs(m / (s / math.sqrt(n))) > tcrit:
            hits += 1
    return hits / trials


@pytest.mark.parametrize("d,n", [(0.5, 20), (0.8, 12)])
def test_power_matches_monte_carlo(d, n):
    """t 검정을 실제로 20,000번 돌려 검정력을 재현합니다 (SE ≈ 0.003)."""
    mc = _mc_power_paired(d, n, 0.05, trials=20000, seed=20260814)
    exact = t_test_power(d, n, design="paired", alpha=0.05)
    assert mc == pytest.approx(exact, abs=0.015)


# --------------------------------------------------------------------------- #
# 4) required_n / detectable_delta 의 경계
# --------------------------------------------------------------------------- #
def test_required_n_returns_none_for_zero_or_nan_effect():
    assert required_n(0.0) is None
    assert required_n(float("nan")) is None
    # 효과가 너무 작으면 상한 안에서 목표에 도달 못 함.
    assert required_n(1e-5, target_power=0.8, max_n=2000) is None


def test_required_n_respects_max_n_but_can_reach_it():
    n = required_n(0.02, target_power=0.8, design="paired", max_n=MAX_N)
    assert n is not None and 2 < n <= MAX_N
    assert t_test_power(0.02, n, design="paired") >= 0.8


def test_required_n_bad_target_power():
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            required_n(0.5, target_power=bad)


def test_t_test_power_argument_validation():
    with pytest.raises(ValueError):
        t_test_power(0.5, 1)
    with pytest.raises(ValueError):
        t_test_power(0.5, 20, alpha=0.0)
    with pytest.raises(ValueError):
        t_test_power(0.5, 20, design="crossover")
    assert math.isnan(t_test_power(float("nan"), 20))


def test_detectable_delta_roundtrip():
    """MDD 를 다시 검정력에 넣으면 목표 검정력이 나와야 합니다."""
    for n, design in ((20, "paired"), (24, "parallel"), (60, "paired")):
        d = detectable_delta(n, target_power=0.8, design=design, sd=1.0)
        assert t_test_power(d, n, design=design) == pytest.approx(0.8, abs=1e-4)


def test_detectable_delta_scales_with_sd():
    a = detectable_delta(30, target_power=0.9, sd=1.0)
    b = detectable_delta(30, target_power=0.9, sd=17.5)
    assert b == pytest.approx(a * 17.5, rel=1e-6)


def test_detectable_delta_matches_r():
    # R: power.t.test(n=24, power=0.8, type="two.sample")$delta = 0.8262
    assert detectable_delta(24, target_power=0.8, design="parallel") == \
        pytest.approx(0.8262, abs=2e-3)


def test_detectable_delta_validation():
    with pytest.raises(ValueError):
        detectable_delta(1)
    with pytest.raises(ValueError):
        detectable_delta(20, target_power=1.0)


def test_inflate_for_dropout():
    assert inflate_for_dropout(20, 0.0) == 20
    assert inflate_for_dropout(20, 0.2) == 25          # 20/0.8
    assert inflate_for_dropout(19, 0.15) == 23         # ceil(22.35)
    assert inflate_for_dropout(None, 0.2) is None
    with pytest.raises(ValueError):
        inflate_for_dropout(20, 1.0)
    with pytest.raises(ValueError):
        inflate_for_dropout(20, -0.1)


def test_nonparam_penalty_is_the_documented_are():
    """순위검정 보정은 ARE=3/π 를 그대로 반영해야 합니다."""
    assert NONPARAM_ARE == pytest.approx(3.0 / math.pi)
    g = power_grid(delta=0.5, sd=1.0, design="paired")
    row = g["rows"][0]
    assert row["n_nonparam"] == math.ceil(row["n_t"] / NONPARAM_ARE)
    assert row["n_nonparam"] >= row["n_t"]


# --------------------------------------------------------------------------- #
# 5) 파일럿 요약 → 계획
# --------------------------------------------------------------------------- #
def _paired_summary_like(n, mean_diff, sd_diff):
    return {"n": n, "mean_diff": mean_diff, "sd_diff": sd_diff}


def test_plan_paired_observed_and_conservative():
    p = plan_paired(_paired_summary_like(12, 8.0, 10.0), target_power=0.8)
    # 관측 dz 는 그대로 쓰지 않고 Hedges J(df=n−1) 로 편의 보정합니다.
    assert p["dz_uncorrected"] == pytest.approx(0.8)
    assert p["hedges_j"] == pytest.approx(hedges_j(11))
    assert p["observed"]["d"] == pytest.approx(0.8 * hedges_j(11))
    assert p["observed"]["n_t"] == required_n(p["observed"]["d"],
                                              target_power=0.8)
    # 보수적 d 는 관측 d 보다 항상 작아야 합니다(0 쪽 경계이므로).
    assert 0 < p["conservative"]["d"] < p["observed"]["d"]
    assert p["conservative"]["n_t"] > p["observed"]["n_t"]
    # 신뢰구간이 실제로 mean_diff 를 감쌉니다.
    assert p["ci_low"] < 8.0 < p["ci_high"]


def test_plan_paired_ci_spanning_zero_has_no_conservative_n():
    p = plan_paired(_paired_summary_like(8, 1.0, 10.0))
    assert p["observed"] is not None
    assert p["conservative"] is None
    assert "신뢰구간" in p["note"]


def test_plan_paired_negative_effect_uses_magnitude():
    pos = plan_paired(_paired_summary_like(12, 8.0, 10.0))
    neg = plan_paired(_paired_summary_like(12, -8.0, 10.0))
    assert pos["observed"]["n_t"] == neg["observed"]["n_t"]
    assert pos["conservative"]["n_t"] == neg["conservative"]["n_t"]
    assert neg["observed"]["d"] < 0


@pytest.mark.parametrize("summary", [
    {"n": 0},
    {"n": 2, "mean_diff": 5.0, "sd_diff": 3.0},          # n<3 → SD 자유도 1
    {"n": 10, "mean_diff": float("nan"), "sd_diff": 3.0},
    {"n": 10, "mean_diff": 5.0, "sd_diff": 0.0},
    {"n": 10, "mean_diff": 5.0, "sd_diff": float("nan")},
])
def test_plan_paired_degenerate_inputs(summary):
    p = plan_paired(summary)
    assert p["observed"] is None and p["conservative"] is None
    assert p["note"]


def test_plan_paired_dropout_inflates():
    p = plan_paired(_paired_summary_like(12, 8.0, 10.0), dropout=0.2)
    o = p["observed"]
    assert o["n_enrol"] == math.ceil(o["n_nonparam"] / 0.8)


def _unpaired_summary_like(na, nb, mean_diff, sp):
    d = mean_diff / sp
    j = 1.0 - 3.0 / (4.0 * (na + nb) - 9.0)
    return {"n_a": na, "n_b": nb, "mean_diff": mean_diff, "sd_pooled": sp,
            "cohens_d": d, "hedges_g": d * j}


def test_plan_parallel_uses_hedges_g():
    s = _unpaired_summary_like(10, 10, 6.0, 10.0)
    p = plan_parallel(s, target_power=0.8)
    assert p["observed"]["d"] == pytest.approx(s["hedges_g"])
    # g < d 이므로 필요 N 은 d 로 계산했을 때보다 크거나 같습니다(보수적).
    assert p["observed"]["n_t"] >= required_n(s["cohens_d"],
                                              target_power=0.8,
                                              design="parallel")
    assert p["design"] == "parallel"


def test_plan_parallel_ci_spanning_zero():
    p = plan_parallel(_unpaired_summary_like(6, 6, 1.0, 10.0))
    assert p["conservative"] is None and p["note"]


@pytest.mark.parametrize("summary", [
    {"n_a": 1, "n_b": 8, "mean_diff": 5.0, "sd_pooled": 3.0,
     "cohens_d": 1.67, "hedges_g": 1.5},
    {"n_a": 8, "n_b": 8, "mean_diff": 5.0, "sd_pooled": 0.0,
     "cohens_d": float("nan"), "hedges_g": float("nan")},
    {"n_a": 0, "n_b": 0},
])
def test_plan_parallel_degenerate_inputs(summary):
    p = plan_parallel(summary)
    assert p["observed"] is None and p["note"]


# --------------------------------------------------------------------------- #
# 6) power_grid
# --------------------------------------------------------------------------- #
def test_power_grid_delta_mode():
    g = power_grid(delta=8.0, sd=15.0, design="paired", dropout=0.15)
    assert [r["target_power"] for r in g["rows"]] == [0.80, 0.85, 0.90, 0.95]
    ns = [r["n_t"] for r in g["rows"]]
    assert all(a <= b for a, b in zip(ns, ns[1:]))     # 검정력↑ → N↑
    assert all(r["n_enrol"] >= r["n_nonparam"] for r in g["rows"])
    assert "power_at_n" not in g


def test_power_grid_n_mode_and_both():
    g = power_grid(n=24, sd=15.0, design="parallel")
    mdds = [r["mdd"] for r in g["rows"]]
    assert all(a <= b for a, b in zip(mdds, mdds[1:]))
    both = power_grid(delta=12.4, n=24, sd=15.0, design="parallel")
    assert both["power_at_n"] == pytest.approx(
        t_test_power(12.4 / 15.0, 24, design="parallel"), abs=1e-9)


def test_power_grid_validation():
    with pytest.raises(ValueError):
        power_grid(sd=10.0)                      # delta 도 n 도 없음
    with pytest.raises(ValueError):
        power_grid(delta=5.0, sd=0.0)
    with pytest.raises(ValueError):
        power_grid(delta=0.0, sd=10.0)
    with pytest.raises(ValueError):
        power_grid(delta=5.0, sd=float("nan"))


# --------------------------------------------------------------------------- #
# 7) 리포트 레이어
# --------------------------------------------------------------------------- #
def _pair_results():
    from hrvkit.analyze import analyze_rr
    rng = random.Random(4242)
    pairs = []
    for i in range(8):
        base = [850 + 25 * math.sin(k * 0.5) + rng.gauss(0, 8)
                for k in range(320)]
        interv = [850 + 45 * math.sin(k * 0.5) + rng.gauss(0, 8)
                  for k in range(320)]
        pairs.append((analyze_rr(base, source=f"b{i}", do_sampen=False),
                      analyze_rr(interv, source=f"v{i}", do_sampen=False)))
    return pairs


def test_power_plan_paired_report_shape():
    pairs = _pair_results()
    plan = power_plan_paired(pairs, target_power=0.9, dropout=0.1)
    assert plan["_meta"] == {"design": "paired", "n_pilot": 8,
                             "target_power": 0.9, "alpha": 0.05,
                             "dropout": 0.1}
    assert plan["rmssd"]["observed"]["n_t"] >= 2
    txt = render_power_plan(plan)
    assert "표본수 설계" in txt and "RMSSD (ms)" in txt
    # 정직성: 사후 검정력을 절대 보고하지 않습니다.
    assert "observed power" in txt and "계산하지 않습니다" in txt
    csv_txt = power_plan_to_csv(plan)
    assert csv_txt.splitlines()[0].startswith("metric,design,n_pilot")
    assert len(csv_txt.strip().splitlines()) == 12       # 헤더 + 11개 지표


def test_power_plan_groups_report_shape():
    pairs = _pair_results()
    a = [b for b, _ in pairs]
    b = [v for _, v in pairs]
    plan = power_plan_groups(a, b, target_power=0.8)
    assert plan["_meta"]["design"] == "parallel"
    assert plan["_meta"]["n_pilot_a"] == 8
    txt = render_power_plan(plan)
    assert "군당" in txt


def test_render_power_plan_when_nothing_computable():
    from hrvkit.analyze import analyze_rr
    rng = random.Random(11)
    one = [(analyze_rr([800 + rng.gauss(0, 5) for _ in range(60)],
                       do_sampen=False),
            analyze_rr([800 + rng.gauss(0, 5) for _ in range(60)],
                       do_sampen=False))]
    txt = render_power_plan(power_plan_paired(one))
    assert "표본수를 낼 수 없습니다" in txt


def test_render_plan_text_blocks():
    txt = render_plan(power_grid(delta=8.0, sd=15.0, design="paired"))
    assert "필요 표본수" in txt and "개인 내 차이" in txt
    txt2 = render_plan(power_grid(n=24, sd=15.0, design="parallel"))
    assert "탐지 가능한 최소 차이" in txt2 and "군당" in txt2
    txt3 = render_plan(power_grid(delta=8.0, n=24, sd=15.0))
    assert "검정력 =" in txt3


# --------------------------------------------------------------------------- #
# 8) CLI
# --------------------------------------------------------------------------- #
def _run(args):
    return subprocess.run([sys.executable, "-m", "hrvkit.cli"] + args,
                          capture_output=True, text=True)


def test_cli_plan_text_json_csv():
    r = _run(["--plan", "--delta", "8", "--sd", "15"])
    assert r.returncode == 0 and "필요 표본수" in r.stdout

    import json
    r = _run(["--plan", "--delta", "8", "--sd", "15", "--json"])
    assert r.returncode == 0
    d = json.loads(r.stdout)
    assert d["mode"] == "plan" and len(d["rows"]) == 4

    r = _run(["--plan", "--delta", "8", "--sd", "15", "--format", "csv"])
    assert r.returncode == 0
    head = r.stdout.splitlines()[0]
    assert head.startswith("design,alpha,sd,delta,n,dropout,target_power")


def test_cli_plan_target_power_is_used():
    r = _run(["--plan", "--plan-n", "30", "--sd", "10", "--json"])
    import json
    rows = json.loads(r.stdout)["rows"]
    assert [x["target_power"] for x in rows] == [0.8, 0.85, 0.9, 0.95]


@pytest.mark.parametrize("args", [
    ["--plan", "--delta", "5"],                       # --sd 없음
    ["--plan", "--sd", "10"],                         # delta 도 n 도 없음
    ["--plan", "--sd", "10", "--delta", "0"],
    ["--plan", "--sd", "0", "--delta", "5"],
    ["--plan", "--sd", "10", "--plan-n", "1"],
    ["--plan", "--sd", "10", "--delta", "5", "examples/resting.csv"],
    ["--power", "examples/resting.csv"],              # --power 단독 불가
    ["--plan", "--sd", "10", "--delta", "5", "--target-power", "1.0"],
    ["--plan", "--sd", "10", "--delta", "5", "--dropout", "1.0"],
    ["--plan", "--sd", "10", "--delta", "5", "--dropout", "-0.1"],
])
def test_cli_plan_input_errors_exit_2(args):
    r = _run(args)
    assert r.returncode == 2
    assert "입력 오류" in r.stderr
    assert r.stdout == ""


def test_cli_plan_conflicts_with_other_modes(tmp_path):
    man = tmp_path / "m.csv"
    man.write_text("baseline,intervention\na.csv,b.csv\n", encoding="utf-8")
    r = _run(["--plan", "--sd", "10", "--delta", "5", "--paired", str(man)])
    assert r.returncode == 2 and "함께 쓸 수 없습니다" in r.stderr


def test_cli_power_with_paired(tmp_path):
    import json
    files = []
    rng = random.Random(99)
    rows = []
    for i in range(6):
        b = tmp_path / f"b{i}.csv"
        v = tmp_path / f"v{i}.csv"
        b.write_text("rr_ms\n" + "\n".join(
            f"{850 + 25 * math.sin(k * 0.5) + rng.gauss(0, 8):.1f}"
            for k in range(300)) + "\n", encoding="utf-8")
        v.write_text("rr_ms\n" + "\n".join(
            f"{850 + 45 * math.sin(k * 0.5) + rng.gauss(0, 8):.1f}"
            for k in range(300)) + "\n", encoding="utf-8")
        files += [b, v]
        rows.append(f"{b},{v},S{i}")
    man = tmp_path / "man.csv"
    man.write_text("baseline,intervention,label\n" + "\n".join(rows) + "\n",
                   encoding="utf-8")

    r = _run(["--paired", str(man), "--power", "--dropout", "0.2"])
    assert r.returncode == 0
    assert "[C] 표본수 설계" in r.stdout and "모집" in r.stdout

    r = _run(["--paired", str(man), "--power", "--json"])
    assert r.returncode == 0
    d = json.loads(r.stdout)
    assert "power_plan" in d
    assert d["power_plan"]["_meta"]["design"] == "paired"

    # --power 없으면 계획 블록이 나오면 안 됩니다.
    r = _run(["--paired", str(man), "--json"])
    assert "power_plan" not in json.loads(r.stdout)

    r = _run(["--paired", str(man), "--power", "--format", "csv"])
    assert r.returncode == 0
    # 계획 표와 통계 표가 빈 줄로 구분되어 둘 다 들어 있어야 합니다.
    assert "n_recommended_observed" in r.stdout and "wilcoxon_p" in r.stdout


def test_cli_power_with_groups(tmp_path):
    import json
    rng = random.Random(1234)
    rows = []
    for i in range(8):
        f = tmp_path / f"g{i}.csv"
        amp = 25 if i < 4 else 45
        f.write_text("rr_ms\n" + "\n".join(
            f"{850 + amp * math.sin(k * 0.5) + rng.gauss(0, 8):.1f}"
            for k in range(300)) + "\n", encoding="utf-8")
        rows.append(f"{f},{'placebo' if i < 4 else 'drug'},S{i}")
    man = tmp_path / "gman.csv"
    man.write_text("file,group,subject\n" + "\n".join(rows) + "\n",
                   encoding="utf-8")
    r = _run(["--groups", str(man), "--power", "--json"])
    assert r.returncode == 0
    d = json.loads(r.stdout)
    assert d["power_plan"]["_meta"]["design"] == "parallel"
    assert d["power_plan"]["_meta"]["n_pilot_a"] == 4


# --------------------------------------------------------------------------- #
# 9) 정확검정 하한 (리뷰 라운드 1 회귀)
#
# 결함: 비중심 t 만 보고 N=3~5 를 권고했는데, hrvkit 이 실제로 쓰는 정확
# 순위검정은 그 표본수에서 **효과가 아무리 커도** α=0.05 를 못 넘습니다
# (부호순위 최소 양측 p = 2^(1−n): n=5 → 0.0625). 즉 "검정력 80%" 라는 답이
# 거짓이고 실제 검정력은 0 입니다. min_exact_n 으로 바닥칩니다.
# --------------------------------------------------------------------------- #
def test_min_exact_n_known_values():
    assert min_exact_n(0.05, "paired") == 6        # 2^-5 = 0.03125 <= 0.05
    assert min_exact_n(0.05, "parallel") == 4      # 2/C(8,4) = 0.0286
    assert min_exact_n(0.01, "paired") == 8
    assert min_exact_n(0.01, "parallel") == 5
    assert min_exact_n(0.10, "paired") == 5
    assert min_exact_n(0.10, "parallel") == 3


def test_min_exact_n_closed_form_matches_exact_null_distribution():
    """닫힌 형태가 hrvkit 의 정확 영분포 구현과 실제로 같은지 대조합니다.

    검정 구현이 바뀌면 이 테스트가 먼저 깨져야 합니다.
    """
    from hrvkit.stats import _exact_mw_two_sided_p, _exact_two_sided_p
    for n in range(2, 13):
        assert 2.0 ** (1 - n) == pytest.approx(
            _exact_two_sided_p(n * (n + 1) / 2.0, n), abs=1e-12)
    for n in range(2, 9):
        assert 2.0 / math.comb(2 * n, n) == pytest.approx(
            _exact_mw_two_sided_p(float(n * n), n, n), abs=1e-12)


def test_min_exact_n_is_the_minimum_rejectable_size():
    """하한 바로 아래 표본수는 정말로 α 에서 기각이 불가능해야 합니다."""
    for alpha in (0.05, 0.01, 0.1):
        for design in ("paired", "parallel"):
            n = min_exact_n(alpha, design)
            below = (2.0 ** (1 - (n - 1)) if design == "paired"
                     else 2.0 / math.comb(2 * (n - 1), n - 1))
            assert below > alpha


def test_min_exact_n_validation_and_unreachable():
    with pytest.raises(ValueError):
        min_exact_n(0.0)
    with pytest.raises(ValueError):
        min_exact_n(0.05, "crossover")
    assert min_exact_n(1e-40, "paired", max_n=10) is None


def test_pack_floors_tiny_n_at_exact_test_minimum():
    """거대한 효과크기에서도 권고 N 이 정확검정 하한 아래로 내려가면 안 됩니다."""
    g = power_grid(delta=60.0, sd=15.0, design="paired")   # d = 4.0
    for row in g["rows"]:
        assert row["n_t"] < 6                      # t 기준으로는 3~4명
        assert row["n_recommended"] == 6           # 하지만 권고는 6명
        assert row["floored"] is True
        assert row["n_exact_floor"] == 6
    gp = power_grid(delta=60.0, sd=15.0, design="parallel")
    for row in gp["rows"]:
        assert row["n_recommended"] >= 4


def test_pack_does_not_floor_when_n_already_large():
    g = power_grid(delta=8.0, sd=15.0, design="paired")
    for row in g["rows"]:
        assert row["floored"] is False
        assert row["n_recommended"] == row["n_nonparam"]


def test_floor_tracks_alpha():
    """α 를 낮추면 하한도 같이 올라가야 합니다."""
    strict = power_grid(delta=60.0, sd=15.0, design="paired", alpha=0.001)
    assert strict["rows"][0]["n_exact_floor"] == min_exact_n(0.001, "paired")
    assert strict["rows"][0]["n_recommended"] >= 11


def test_enrol_is_computed_from_recommended_not_raw_n():
    g = power_grid(delta=60.0, sd=15.0, design="paired", dropout=0.2)
    row = g["rows"][0]
    assert row["n_enrol"] == math.ceil(row["n_recommended"] / 0.8)


def test_plan_paired_floors_too():
    p = plan_paired({"n": 10, "mean_diff": 40.0, "sd_diff": 10.0})
    assert p["observed"]["n_recommended"] == 6
    assert p["observed"]["floored"] is True


def test_render_marks_floored_rows():
    txt = render_plan(power_grid(delta=60.0, sd=15.0, design="paired"))
    assert "\u2021" in txt and "정확검정 하한" in txt
    clean = render_plan(power_grid(delta=8.0, sd=15.0, design="paired"))
    assert "\u2021" not in clean


# --------------------------------------------------------------------------- #
# 10) MDD 의 순위검정 환산 + 정직성 문구 (리뷰 라운드 1 회귀)
# --------------------------------------------------------------------------- #
def test_mdd_nonparam_is_are_scaled():
    """검정력은 n·d² 로 정해지므로 같은 n 에서 MDD 는 1/√ARE 배가 됩니다."""
    g = power_grid(n=24, sd=15.0, design="parallel")
    for row in g["rows"]:
        assert row["mdd_nonparam"] == pytest.approx(
            row["mdd"] / math.sqrt(NONPARAM_ARE), rel=1e-12)
        assert row["mdd_nonparam"] > row["mdd"]


def test_mdd_only_plan_does_not_print_n_footnote():
    """MDD 표만 있을 때 존재하지 않는 N 열을 설명하면 안 됩니다."""
    txt = render_plan(power_grid(n=24, sd=15.0, design="parallel"))
    assert "N(t 검정) 은" not in txt
    assert "MDD(순위검정)" in txt


def test_plan_footnotes_state_known_limitations():
    """과대주장 방지 — 문구가 사라지면 테스트가 깨지도록 고정합니다."""
    txt = render_plan(power_grid(delta=8.0, sd=15.0, design="paired"))
    assert "수치적분(Simpson)" in txt          # '정확히' 라는 과대주장 금지
    assert "0.864" in txt                       # ARE 최악값 공개
    assert "보정 없는 α" in txt                 # 다중비교 함정
    assert "비열등성" in txt                    # NI/동등성 미지원 명시


def test_power_plan_footnotes_state_known_limitations():
    plan = power_plan_paired(_pair_results())
    txt = render_power_plan(plan)
    assert "97.5% 단측 신뢰한계" in txt          # 방법을 이름으로 밝힘
    assert "Browne 1995" in txt
    assert "보정 없는 α" in txt
    assert "MCID" in txt
    assert "ln 변환" in txt


def test_plan_csv_includes_power_at_n():
    """--plan 에 delta 와 n 을 둘 다 주면 CSV 에도 검정력이 나와야 합니다."""
    r = _run(["--plan", "--delta", "8", "--sd", "15", "--plan-n", "30",
              "--format", "csv"])
    assert r.returncode == 0
    import csv as _csv
    rows = list(_csv.DictReader(r.stdout.splitlines()))
    assert rows[0]["power_at_n"]
    assert float(rows[0]["power_at_n"]) == pytest.approx(
        t_test_power(8.0 / 15.0, 30, design="paired"), abs=1e-9)
    assert rows[0]["mdd_nonparam"]


# --------------------------------------------------------------------------- #
# 11) 리뷰 라운드 1 회귀 — 정확성·강건성
# --------------------------------------------------------------------------- #
def test_noncentral_t_accurate_for_large_t():
    """격자 간격이 df 로만 정해지면 |t| 가 클 때 크게 틀어집니다.

    Φ(t·u − ncp) 의 전이폭은 ~1/|t| 이므로 격자를 |t| 에 맞춰 촘촘히 해야
    합니다. 회귀 전 값: F(1000; df=1) = 0.9993052 (참값 0.9996817).
    """
    for t in (100.0, 500.0, 1000.0, 5000.0):
        for df in (1, 2, 5, 30):
            assert noncentral_t_cdf(t, df, 0.0) == pytest.approx(
                student_t_cdf(t, df), abs=1e-8)
            assert noncentral_t_cdf(-t, df, 0.0) == pytest.approx(
                student_t_cdf(-t, df), abs=1e-8)


def test_power_accurate_at_tiny_alpha_and_df():
    """작은 α + df=1 은 |t_crit| 가 커지는 최악 조합입니다.

    회귀 전 값: 0.0148439 (참값 0.0141789), 0.0013896 (참값 0.0035449).
    """
    assert t_test_power(8.0, 2, alpha=0.001) == pytest.approx(0.0141789, abs=1e-5)
    assert t_test_power(20.0, 2, alpha=1e-4) == pytest.approx(0.0035449, abs=1e-5)


def test_hedges_j_known_values_and_limit():
    assert hedges_j(4) == pytest.approx(0.7978845608, abs=1e-9)
    assert hedges_j(9) == pytest.approx(0.9138748918, abs=1e-9)
    assert hedges_j(19) == pytest.approx(0.9599103529, abs=1e-9)
    assert hedges_j(100000) == pytest.approx(1.0, abs=1e-5)   # df→∞ 이면 보정 없음
    assert 0.0 < hedges_j(4) < hedges_j(9) < hedges_j(19) < 1.0
    assert math.isnan(hedges_j(1))


def test_plan_paired_bias_correction_increases_n():
    """보정 없이는 짝지은 시험 N 이 **작게** 나옵니다 — 위험한 방향입니다."""
    small = plan_paired(_paired_summary_like(5, 8.0, 10.0))
    assert small["observed"]["d"] < small["dz_uncorrected"]
    assert small["observed"]["n_t"] == 22          # 보정 전에는 15
    big = plan_paired(_paired_summary_like(40, 8.0, 10.0))
    # 파일럿이 커지면 보정은 사라집니다.
    assert big["hedges_j"] > small["hedges_j"]
    assert big["observed"]["d"] == pytest.approx(0.8, abs=0.02)


def test_plan_parallel_j_computed_from_df_not_ratio():
    """J 를 g/d 로 되짚으면 cohens_d 가 없을 때 조용히 보정이 사라집니다."""
    s = _unpaired_summary_like(20, 20, 12.0, 10.0)
    ref = plan_parallel(s)
    broken = dict(s)
    broken["cohens_d"] = None            # 예전 구현이면 j=1.0 으로 떨어짐
    got = plan_parallel(broken)
    assert got["conservative"]["n_t"] == ref["conservative"]["n_t"]


def test_conservative_delta_is_the_confidence_limit_not_rescaled():
    """conservative.delta 는 원 단위 신뢰한계여야 합니다(J 로 줄인 값이 아님)."""
    p = plan_paired(_paired_summary_like(12, 8.0, 10.0))
    assert p["conservative"]["delta"] == pytest.approx(p["ci_low"])
    q = plan_parallel(_unpaired_summary_like(20, 20, 12.0, 10.0))
    assert q["conservative"]["delta"] == pytest.approx(q["ci_low"])


def test_power_grid_honours_target_power():
    """--target-power 가 표에 실제로 들어가야 합니다(예전엔 조용히 무시)."""
    g = power_grid(delta=8.0, sd=20.0, design="paired", target_power=0.99)
    powers = [r["target_power"] for r in g["rows"]]
    assert 0.99 in powers
    assert sum(1 for r in g["rows"] if r["requested"]) == 1
    # 이미 격자에 있는 값이면 행이 늘지 않습니다.
    g2 = power_grid(delta=8.0, sd=20.0, target_power=0.90)
    assert len(g2["rows"]) == 4
    with pytest.raises(ValueError):
        power_grid(delta=8.0, sd=20.0, target_power=1.0)


def test_detectable_delta_boundaries():
    # 목표가 α 이하이면 d=0 에서 이미 달성 → MDE 는 0.
    assert detectable_delta(10, target_power=0.04, alpha=0.05) == 0.0
    # 도달 불가 케이스는 배가 후에 검사하므로 큰 d 도 제대로 찾습니다.
    d = detectable_delta(2, target_power=0.5, design="paired")
    assert math.isfinite(d) and d > 0


def test_required_n_max_n_below_two():
    assert required_n(0.5, max_n=1) is None


def test_pct_never_rounds_to_a_false_zero_or_hundred():
    """'탈락률 100%' 같은 거짓 문장이 IRB 문서로 나가면 안 됩니다."""
    from hrvkit.report import _pct
    assert _pct(0.999) == "99.9"
    assert _pct(0.999999) == "99.9999"
    assert _pct(0.00001) == "0.001"
    assert _pct(1.0) == "100"
    assert _pct(0.0) == "0"
    assert _pct(0.8) == "80"


def test_zero_variance_pilot_is_not_called_too_small():
    p = plan_paired({"n": 10, "mean_diff": 0.0, "sd_diff": 0.0})
    assert "분산이 0" in p["note"] and "작" not in p["note"].split("—")[0]
    q = plan_parallel({"n_a": 8, "n_b": 8, "mean_diff": 0.0, "sd_pooled": 0.0,
                       "cohens_d": float("nan"), "hedges_g": float("nan")})
    assert "분산이 0" in q["note"]


@pytest.mark.parametrize("args,frag", [
    (["--paired", "MAN", "--power", "--alpha", "1e-20"], "너무 작습니다"),
    (["--plan", "--plan-n", "100000000000000000000", "--sd", "1"], "이하여야"),
    (["--plan", "--delta", "nan", "--sd", "1"], "유한한 수"),
    (["--plan", "--delta", "inf", "--sd", "1"], "유한한 수"),
    (["--plan", "--sd", "nan", "--delta", "1"], "유한한 수"),
    (["--paired", "MAN", "--power", "--delta", "8"], "--plan 에서만"),
    (["--paired", "MAN", "--power", "--sd", "8"], "--plan 에서만"),
    (["examples/resting.csv", "--plan-n", "40"], "--plan 에서만"),
])
def test_cli_rejects_pathological_inputs(tmp_path, args, frag):
    man = tmp_path / "m.csv"
    man.write_text("baseline,intervention\na.csv,b.csv\n", encoding="utf-8")
    args = [str(man) if a == "MAN" else a for a in args]
    r = _run(args)
    assert r.returncode == 2, r.stdout
    assert frag in r.stderr
    assert r.stdout == ""


def test_cli_plan_target_power_appears_in_table():
    r = _run(["--plan", "--delta", "8", "--sd", "20", "--target-power", "0.99"])
    assert r.returncode == 0
    assert "99%" in r.stdout and "--target-power" in r.stdout


def test_power_csv_puts_main_table_first_and_delimits_plan(tmp_path):
    """두 표는 열 구성이 달라 합칠 수 없으므로 순서와 구분선을 고정합니다."""
    rng = random.Random(5)
    rows = []
    for i in range(6):
        b = tmp_path / f"b{i}.csv"
        v = tmp_path / f"v{i}.csv"
        b.write_text("rr_ms\n" + "\n".join(
            f"{850 + 25 * math.sin(k * 0.5) + rng.gauss(0, 8):.1f}"
            for k in range(300)) + "\n", encoding="utf-8")
        v.write_text("rr_ms\n" + "\n".join(
            f"{850 + 45 * math.sin(k * 0.5) + rng.gauss(0, 8):.1f}"
            for k in range(300)) + "\n", encoding="utf-8")
        rows.append(f"{b},{v},S{i}")
    man = tmp_path / "man.csv"
    man.write_text("baseline,intervention,label\n" + "\n".join(rows) + "\n",
                   encoding="utf-8")
    r = _run(["--paired", str(man), "--power", "--format", "csv"])
    assert r.returncode == 0
    lines = r.stdout.splitlines()
    # 주 통계표가 먼저 → head -1 이 주 헤더.
    assert lines[0].startswith("metric,psd_method,")
    delim = [i for i, ln in enumerate(lines) if ln.startswith("# ----")]
    assert len(delim) == 1
    assert lines[delim[0] + 1].startswith("metric,design,n_pilot")
    # 각 표는 그 자체로 파싱 가능해야 합니다.
    import csv as _csv
    top = list(_csv.DictReader(lines[:delim[0] - 1]))
    bot = list(_csv.DictReader(lines[delim[0] + 1:]))
    assert len(top) == 11 and len(bot) == 11
