"""새로 넣은 두 설계 — 반복사건 계수(count)와 순서형(ordinal) — 의 검증.

핵심 원칙: **공식을 손으로 다시 세워서** 대조한다. 코드가 코드를 검증하면
같은 실수를 두 번 하고 통과한다.
"""

from __future__ import annotations

import json
import math
import random

import pytest

from powerplan.cli import main
from powerplan.designs import CountRateRatio, OrdinalProportionalOdds, po_shift
from powerplan.solve import Adjustments, make_plan, smallest_unit
from powerplan.special import norm_cdf, norm_ppf
from powerplan.validate import PowerPlanError


# ==========================================================================
# 1) count — 음이항 발생률비
# ==========================================================================
def _count_power_by_hand(rate1, rr, k, t, n1, n2, alpha=0.05, sides=2):
    """Zhu & Lakkis(2014)의 분산식을 그대로 손으로 세운 검정력."""
    rate2 = rate1 * rr
    var = (1.0 / (rate1 * t) + k) / n1 + (1.0 / (rate2 * t) + k) / n2
    se = math.sqrt(var)
    zc = norm_ppf(1.0 - alpha / sides)
    delta = abs(math.log(rr))
    upper = norm_cdf((delta - zc * se) / se)
    lower = norm_cdf((-delta - zc * se) / se) if sides == 2 else 0.0
    return upper + lower


@pytest.mark.parametrize("rate1,rr,k,t,n", [
    (1.2, 0.75, 0.7, 1.0, 400),
    (0.5, 0.6, 0.0, 2.0, 120),
    (3.0, 1.4, 1.5, 0.5, 250),
])
def test_count_power_matches_hand_formula(rate1, rr, k, t, n):
    d = CountRateRatio(rate1, rr, k, t)
    assert d.power(n) == pytest.approx(_count_power_by_hand(rate1, rr, k, t, n, n),
                                       abs=1e-12)


def test_count_closed_form_sample_size():
    """n = (z_{1-α/2} + z_{1-β})²·Σ분산 / (log RR)² 를 직접 풀어 대조."""
    rate1, rr, k, t = 1.2, 0.75, 0.7, 1.0
    z = norm_ppf(0.975) + norm_ppf(0.90)
    unit_var = (1.0 / (rate1 * t) + k) + (1.0 / (rate1 * rr * t) + k)
    n_exact = z * z * unit_var / (math.log(rr) ** 2)
    assert n_exact == pytest.approx(424.6, abs=0.5)
    d = CountRateRatio(rate1, rr, k, t)
    assert smallest_unit(d, 0.90) == math.ceil(n_exact)


def test_count_poisson_is_dispersion_zero():
    """k = 0이면 포아송 — 분산이 1/(λt)로만 결정된다."""
    d = CountRateRatio(2.0, 0.5, 0.0, 1.0)
    n = 40
    var = (1.0 / 2.0 + 1.0 / 1.0) / n
    assert d.power(n) == pytest.approx(
        norm_cdf((abs(math.log(0.5)) - norm_ppf(0.975) * math.sqrt(var)) / math.sqrt(var))
        + norm_cdf((-abs(math.log(0.5)) - norm_ppf(0.975) * math.sqrt(var)) / math.sqrt(var)),
        abs=1e-12)


def test_count_dispersion_increases_sample_size_monotonically():
    ns = [smallest_unit(CountRateRatio(1.2, 0.75, k, 1.0), 0.8)
          for k in (0.0, 0.3, 0.7, 1.5)]
    assert ns == sorted(ns)
    assert ns[0] < ns[-1]


def test_count_exposure_and_rate_enter_only_as_product():
    """분산은 1/(λ·t)에만 의존하므로 λ를 2배·t를 절반으로 하면 같은 검정력."""
    a = CountRateRatio(1.0, 0.7, 0.4, 2.0)
    b = CountRateRatio(2.0, 0.7, 0.4, 1.0)
    for n in (30, 100, 500):
        assert a.power(n) == pytest.approx(b.power(n), abs=1e-12)


def test_count_simulation_agrees_with_asymptotic_power():
    """음이항 자료를 실제로 만들어 Wald 검정을 돌려 검정력을 대조.

    모멘트법으로 log 발생률비와 그 표준오차를 추정한다(1군 t 고정이므로
    log λ̂_i = log(Σy_i / (n_i t))이고, Var(log λ̂_i) ≈ (1/(λt) + k)/n_i).
    """
    rate1, rr, k, t, n = 1.2, 0.6, 0.6, 1.0, 120
    d = CountRateRatio(rate1, rr, k, t)
    target = d.power(n)
    rng = random.Random(20260731)
    mu1, mu2 = rate1 * t, rate1 * rr * t

    def nb_sample(mu):
        # NB(평균 mu, 분산 mu + k·mu²) = 감마 혼합 포아송, 형상 1/k
        if k == 0.0:  # pragma: no cover - 이 테스트는 k>0
            lam = mu
        else:
            shape = 1.0 / k
            lam = rng.gammavariate(shape, mu / shape)
        # 포아송 난수 (Knuth) — lam이 작아 충분히 빠르다
        limit, prod, count = math.exp(-lam), 1.0, 0
        while True:
            prod *= rng.random()
            if prod <= limit:
                return count
            count += 1

    reps, hits = 4000, 0
    zc = norm_ppf(0.975)
    for _ in range(reps):
        s1 = sum(nb_sample(mu1) for _ in range(n))
        s2 = sum(nb_sample(mu2) for _ in range(n))
        if s1 == 0 or s2 == 0:
            continue
        lam1, lam2 = s1 / (n * t), s2 / (n * t)
        var = (1.0 / (lam1 * t) + k) / n + (1.0 / (lam2 * t) + k) / n
        if abs(math.log(lam2 / lam1)) > zc * math.sqrt(var):
            hits += 1
    se = math.sqrt(target * (1 - target) / reps)
    assert abs(hits / reps - target) < 4 * se


def test_count_variance_null_is_not_conservative_and_says_so():
    """1/λ는 볼록이라 합동(null) 분산이 더 작다 — 표본수도 더 작아진다.

    문서가 반대로 주장하면 사용자가 'null이 안전하다'고 잘못 고를 수 있으므로
    숫자와 문구를 함께 검사한다.
    """
    alt_d = CountRateRatio(1.2, 0.75, 0.7, 1.0, variance="alt")
    null_d = CountRateRatio(1.2, 0.75, 0.7, 1.0, variance="null")
    assert smallest_unit(null_d, 0.8) <= smallest_unit(alt_d, 0.8)
    assert null_d.power(300) >= alt_d.power(300)
    note = " ".join(null_d.notes())
    assert "작게" in note and "보수적인 쪽이 아닙니다" in note


def test_count_variance_null_uses_pooled_rate():
    d = CountRateRatio(1.2, 0.75, 0.7, 1.0, variance="null")
    n = 300
    pooled = (1.2 + 0.9) / 2.0
    unit = 1.0 / pooled + 0.7
    var_null = 2.0 * unit / n
    var_alt = ((1.0 / 1.2 + 0.7) + (1.0 / 0.9 + 0.7)) / n
    delta = abs(math.log(0.75))
    zc = norm_ppf(0.975)
    expect = (norm_cdf((delta - zc * math.sqrt(var_null)) / math.sqrt(var_alt))
              + norm_cdf((-delta - zc * math.sqrt(var_null)) / math.sqrt(var_alt)))
    assert d.power(n) == pytest.approx(expect, abs=1e-12)


def test_count_ratio_allocation():
    d = CountRateRatio(1.2, 0.75, 0.7, 1.0, ratio=2.0)
    alloc = d.allocation(100)
    assert alloc == {"n1": 100, "n2": 200, "total": 300}
    assert d.power_of_allocation(alloc) == pytest.approx(
        _count_power_by_hand(1.2, 0.75, 0.7, 1.0, 100, 200), abs=1e-12)


def test_count_scaled_moves_log_rate_ratio():
    d = CountRateRatio(1.2, 0.75, 0.7, 1.0)
    assert d.scaled(2.0).rate_ratio == pytest.approx(0.75 ** 2)
    assert d.scaled(0.5).rate_ratio == pytest.approx(math.sqrt(0.75))
    with pytest.raises(PowerPlanError):
        d.scaled(0.0)


def test_count_symmetric_in_direction():
    """RR과 1/RR은 같은 검정력이어야 한다 (군을 바꾼 것뿐)."""
    a = CountRateRatio(1.0, 0.5, 0.0, 1.0)
    b = CountRateRatio(0.5, 2.0, 0.0, 1.0)
    for n in (20, 200):
        assert a.power(n) == pytest.approx(b.power(n), abs=1e-12)


@pytest.mark.parametrize("kwargs,fragment", [
    (dict(rate1=0.0, rate_ratio=0.7), "--rate1"),
    (dict(rate1=1.0, rate_ratio=0.0), "--rr"),
    (dict(rate1=1.0, rate_ratio=1.0), "1이면"),
    (dict(rate1=1.0, rate_ratio=0.7, dispersion=-0.1), "--dispersion"),
    (dict(rate1=1.0, rate_ratio=0.7, exposure=0.0), "--exposure"),
    (dict(rate1=1.0, rate_ratio=0.7, variance="wat"), "--variance"),
    (dict(rate1=float("nan"), rate_ratio=0.7), "--rate1"),
    (dict(rate1=float("inf"), rate_ratio=0.7), "--rate1"),
])
def test_count_rejects_bad_input(kwargs, fragment):
    with pytest.raises(PowerPlanError) as err:
        CountRateRatio(**kwargs)
    assert fragment in str(err.value)


def test_count_poisson_warning_is_present_and_honest():
    notes = " ".join(CountRateRatio(1.2, 0.75, 0.0, 1.0).notes())
    assert "포아송" in notes and "과소평가" in notes
    notes_nb = " ".join(CountRateRatio(1.2, 0.75, 0.7, 1.0).notes())
    assert "과소평가" not in notes_nb


def test_count_low_event_warning():
    notes = " ".join(CountRateRatio(0.1, 0.5, 0.5, 1.0).notes())
    assert "기대 사건 수가" in notes


def test_count_plan_lines_expected_events():
    d = CountRateRatio(1.2, 0.75, 0.7, 2.0)
    lines = dict(d.plan_lines({"n1": 100, "n2": 100, "total": 200}))
    # 대조 100 × 1.2 × 2 = 240건, 중재 100 × 0.9 × 2 = 180건
    assert "420.0건" in lines["기대 사건 수"]
    assert "대조 240.0" in lines["기대 사건 수"]


def test_count_information_caveat_mentions_person_time():
    info = CountRateRatio(1.2, 0.75, 0.7, 1.0).information(
        {"n1": 10, "n2": 10, "total": 20})
    assert "person-" in info["caveat"]


# ==========================================================================
# 2) ordinal — 비례오즈
# ==========================================================================
def test_po_shift_matches_cumulative_logit_definition():
    probs = (0.1, 0.2, 0.4, 0.2, 0.1)
    or_ = 1.8
    shifted = po_shift(probs, or_)
    assert math.fsum(shifted) == pytest.approx(1.0, abs=1e-12)
    cum1 = cum2 = 0.0
    for a, b in zip(probs[:-1], shifted[:-1]):
        cum1 += a
        cum2 += b
        odds1 = cum1 / (1 - cum1)
        odds2 = cum2 / (1 - cum2)
        assert odds2 / odds1 == pytest.approx(or_, rel=1e-12)


def test_po_shift_identity_at_or_one():
    probs = (0.25, 0.25, 0.3, 0.2)
    assert po_shift(probs, 1.0) == pytest.approx(probs, abs=1e-12)


def test_ordinal_matches_whitehead_closed_form():
    """N = 3(z_{1-α/2}+z_{1-β})² / (q1·q2·(logOR)²·(1−Σp̄³)) 를 손으로 풀어 대조."""
    probs = (0.2, 0.2, 0.2, 0.2, 0.2)
    or_ = 2.0
    d = OrdinalProportionalOdds(probs, or_)
    pbar = d.mean_probs()
    tie = 1.0 - sum(p ** 3 for p in pbar)
    z = norm_ppf(0.975) + norm_ppf(0.90)
    n_total = 3.0 * z * z / (0.25 * math.log(or_) ** 2 * tie)
    n1 = n_total / 2.0
    assert smallest_unit(d, 0.90) == math.ceil(n1 - 1e-9)


def test_ordinal_converges_to_exact_logistic_at_binary_boundary():
    """범주를 사실상 2개로 만들고 OR → 1로 보내면 정확한 로지스틱 표본수와 일치.

    Whitehead 공식은 **국소 대립가설**(log OR이 작을 때) 근사이므로, OR이 1에서
    멀어지면 정확한 MLE 분산과 몇 % 차이가 나는 것이 정상이다. 이 테스트는 그
    수렴 자체를 확인한다 — 수렴하지 않으면 정보량 상수(3, q1·q2, 1−Σp̄³) 중
    어느 하나가 틀린 것이다.
    """
    z = norm_ppf(0.975) + norm_ppf(0.80)
    eps = 1e-7
    ratios = []
    for or_ in (2.0, 1.4, 1.1, 1.02):
        d = OrdinalProportionalOdds((0.5 - eps, eps, 0.5), or_)
        pbar = d.mean_probs()
        tie = 1.0 - math.fsum(p ** 3 for p in pbar)
        n_whitehead = 3.0 * z * z / (0.25 * math.log(or_) ** 2 * tie)
        # 정확한 이분형 로지스틱: Var(logOR) = 1/(n1 p1 q1) + 1/(n2 p2 q2)
        p1 = 0.5
        odds2 = (p1 / (1 - p1)) * or_
        p2 = odds2 / (1 + odds2)
        n_exact = (2.0 * z * z * (1.0 / (p1 * (1 - p1)) + 1.0 / (p2 * (1 - p2)))
                   / math.log(or_) ** 2)
        ratios.append(n_whitehead / n_exact)
    # OR이 1에 가까워질수록 1로 수렴
    assert [abs(r - 1.0) for r in ratios] == sorted(
        [abs(r - 1.0) for r in ratios], reverse=True)
    assert abs(ratios[-1] - 1.0) < 0.002
    assert abs(ratios[0] - 1.0) < 0.05      # OR=2에서도 5% 안


def test_ordinal_tie_factor_shrinks_when_mass_concentrates():
    spread = OrdinalProportionalOdds((0.2, 0.2, 0.2, 0.2, 0.2), 1.8)
    peaked = OrdinalProportionalOdds((0.02, 0.03, 0.9, 0.03, 0.02), 1.8)
    assert peaked.tie_factor < spread.tie_factor
    assert smallest_unit(peaked, 0.8) > smallest_unit(spread, 0.8)


def test_ordinal_win_probability_matches_direct_enumeration():
    d = OrdinalProportionalOdds((0.1, 0.2, 0.4, 0.2, 0.1), 1.8)
    p1, p2 = d.probs, d.probs2
    greater = sum(p2[j] * sum(p1[:j]) for j in range(len(p2)))
    ties = sum(a * b for a, b in zip(p1, p2))
    assert d.win_probability() == pytest.approx(greater + 0.5 * ties, abs=1e-14)
    # OR > 1이면 중재군이 낮은 범주로 몰리므로 0.5보다 작다
    assert d.win_probability() < 0.5
    assert OrdinalProportionalOdds((0.1, 0.2, 0.4, 0.2, 0.1), 0.5).win_probability() > 0.5


def test_ordinal_or_and_inverse_or_need_same_sample_size():
    a = OrdinalProportionalOdds((0.1, 0.2, 0.4, 0.2, 0.1), 2.0)
    b = OrdinalProportionalOdds(a.probs2, 0.5)
    assert smallest_unit(a, 0.8) == smallest_unit(b, 0.8)


def test_ordinal_is_more_efficient_than_dichotomising():
    """순서형 그대로 쓰면 이분화보다 표본수가 적어야 한다 (README의 주장)."""
    from powerplan.designs import TwoProportions
    probs = (0.1, 0.2, 0.4, 0.2, 0.1)
    or_ = 1.8
    ordinal = OrdinalProportionalOdds(probs, or_)
    probs2 = ordinal.probs2
    n_ord = smallest_unit(ordinal, 0.8)
    # 가장 좋은 절단점으로 이분화해도 순서형보다 많이 필요한지 본다
    best = None
    for cut in range(1, len(probs)):
        p1 = sum(probs[:cut])
        p2 = sum(probs2[:cut])
        n_bin = smallest_unit(TwoProportions(p1, p2), 0.8)
        best = n_bin if best is None else min(best, n_bin)
    assert n_ord < best


def test_ordinal_simulation_agrees_with_asymptotic_power():
    """실제 순서형 자료를 만들어 Wilcoxon 순위합(정규근사)으로 검정력을 대조.

    비례오즈 점수검정은 OR이 1 근처일 때 순위합 검정과 국소적으로 같으므로,
    적당한 효과크기에서 두 검정력이 비슷해야 한다.
    """
    probs = (0.2, 0.2, 0.2, 0.2, 0.2)
    or_ = 1.6
    n = 120
    d = OrdinalProportionalOdds(probs, or_)
    target = d.power(n)
    probs2 = d.probs2
    cum1 = [sum(probs[:i + 1]) for i in range(len(probs))]
    cum2 = [sum(probs2[:i + 1]) for i in range(len(probs2))]
    rng = random.Random(4242)

    def draw(cum):
        u = rng.random()
        for i, c in enumerate(cum):
            if u <= c:
                return i
        return len(cum) - 1  # pragma: no cover - 부동소수 경계

    reps, hits = 3000, 0
    zc = norm_ppf(0.975)
    for _ in range(reps):
        x = [draw(cum1) for _ in range(n)]
        y = [draw(cum2) for _ in range(n)]
        pooled = sorted(x + y)
        # 동점 평균순위
        ranks = {}
        i = 0
        while i < len(pooled):
            j = i
            while j + 1 < len(pooled) and pooled[j + 1] == pooled[i]:
                j += 1
            ranks[pooled[i]] = (i + j) / 2.0 + 1.0
            i = j + 1
        w = sum(ranks[v] for v in x)
        n1 = n2 = n
        mu = n1 * (n1 + n2 + 1) / 2.0
        counts = {}
        for v in pooled:
            counts[v] = counts.get(v, 0) + 1
        N = n1 + n2
        tie_corr = sum(c ** 3 - c for c in counts.values())
        var = n1 * n2 / 12.0 * ((N + 1) - tie_corr / (N * (N - 1.0)))
        if abs(w - mu) / math.sqrt(var) > zc:
            hits += 1
    se = math.sqrt(target * (1 - target) / reps)
    assert abs(hits / reps - target) < 4.5 * se


@pytest.mark.parametrize("probs,or_,fragment", [
    ((0.5, 0.5), 2.0, "3개 이상"),
    (tuple([1.0 / 40] * 40), 2.0, "너무 많습니다"),
    ((0.3, 0.0, 0.7), 2.0, "0보다 커야"),
    ((0.3, -0.1, 0.8), 2.0, "0보다 커야"),
    ((0.3, 0.3, 0.3), 2.0, "합이 1이어야"),
    ((10.0, 20.0, 70.0), 2.0, "퍼센트가 아니라"),
    ((0.2, 0.3, 0.5), 1.0, "1이면"),
    ((0.2, 0.3, 0.5), 0.0, "--or"),
    ((0.2, 0.3, 0.5), float("nan"), "--or"),
])
def test_ordinal_rejects_bad_input(probs, or_, fragment):
    with pytest.raises(PowerPlanError) as err:
        OrdinalProportionalOdds(probs, or_)
    assert fragment in str(err.value)


def test_ordinal_rare_category_warning():
    notes = " ".join(OrdinalProportionalOdds((0.005, 0.495, 0.5), 1.8).notes())
    assert "드문 범주" in notes


def test_ordinal_unequal_allocation_uses_weighted_mean_probs():
    d = OrdinalProportionalOdds((0.2, 0.3, 0.5), 2.0, ratio=3.0)
    pbar = d.mean_probs()
    expected = tuple(0.25 * a + 0.75 * b for a, b in zip(d.probs, d.probs2))
    assert pbar == pytest.approx(expected, abs=1e-14)
    # 1:1이 같은 총 N에서 항상 더 효율적
    equal = OrdinalProportionalOdds((0.2, 0.3, 0.5), 2.0)
    assert equal.power(200) > d.power(100)          # 둘 다 총 400명


# ==========================================================================
# 3) CLI 통합 — 조정(탈락·군집·중간분석)과 출력 형식
# ==========================================================================
def test_count_cli_runs_and_reports(capsys):
    assert main(["count", "--rate1", "1.2", "--rr", "0.75",
                 "--dispersion", "0.7", "--power", "0.9"]) == 0
    out = capsys.readouterr().out
    assert "군당 425명" in out
    assert "기대 사건 수" in out
    assert "Zhu H, Lakkis H" in out


def test_count_cli_rate2_equals_rr(capsys):
    main(["count", "--rate1", "1.2", "--rate2", "0.9", "--dispersion", "0.7",
          "--power", "0.9", "--format", "json"])
    a = json.loads(capsys.readouterr().out)
    main(["count", "--rate1", "1.2", "--rr", "0.75", "--dispersion", "0.7",
          "--power", "0.9", "--format", "json"])
    b = json.loads(capsys.readouterr().out)
    assert a["analysis"]["allocation"] == b["analysis"]["allocation"]
    assert a["design"]["effect"]["value"] == pytest.approx(
        b["design"]["effect"]["value"])


def test_count_cli_rejects_both_rr_and_rate2(capsys):
    assert main(["count", "--rate1", "1.2", "--rr", "0.8", "--rate2", "0.9",
                 "--power", "0.8"]) == 2
    assert "함께 쓸 수 없습니다" in capsys.readouterr().err


def test_count_cli_requires_one_of_rr_or_rate2(capsys):
    assert main(["count", "--rate1", "1.2", "--power", "0.8"]) == 2
    assert "--rr" in capsys.readouterr().err


def test_count_cli_with_dropout_and_interim(capsys):
    assert main(["count", "--rate1", "1.2", "--rr", "0.75", "--dispersion", "0.7",
                 "--power", "0.9", "--dropout", "0.15", "--interim", "2"]) == 0
    out = capsys.readouterr().out
    assert "중간분석" in out
    assert "모집" in out


def test_count_cli_cluster_and_sensitivity(capsys):
    assert main(["count", "--rate1", "1.2", "--rr", "0.75", "--dispersion", "0.7",
                 "--power", "0.8", "--cluster-size", "20", "--cluster-icc", "0.02",
                 "--sensitivity"]) == 0
    out = capsys.readouterr().out
    assert "설계효과" in out


def test_ordinal_cli_runs_and_reports(capsys):
    assert main(["ordinal", "--probs", "0.1,0.2,0.4,0.2,0.1", "--or", "1.8",
                 "--power", "0.9"]) == 0
    out = capsys.readouterr().out
    assert "군당 198명" in out
    assert "Whitehead" in out
    assert "Mann–Whitney 확률" in out


def test_ordinal_cli_probs2_reports_cut_odds_ratios(capsys):
    assert main(["ordinal", "--probs", "0.1,0.2,0.4,0.2,0.1",
                 "--probs2", "0.16,0.27,0.37,0.14,0.06", "--power", "0.8"]) == 0
    out = capsys.readouterr().out
    assert "절단점별 오즈비" in out
    assert "기하평균" in out


def test_ordinal_cli_probs2_flags_proportional_odds_violation(capsys):
    # 절단점별 오즈비가 크게 흩어지는 분포
    assert main(["ordinal", "--probs", "0.25,0.25,0.25,0.25",
                 "--probs2", "0.5,0.05,0.05,0.4", "--power", "0.8"]) == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "비례오즈 가정이 의심스럽습니다" in out
    assert "0.5~3" in out


def test_ordinal_cli_probs_space_separated(capsys):
    assert main(["ordinal", "--probs", "0.2 0.3 0.5", "--or", "2.0",
                 "--power", "0.8"]) == 0
    assert "순서형" in capsys.readouterr().out


@pytest.mark.parametrize("bad,fragment", [
    ("0.1,x,0.9", "숫자여야"),
    (",", "쉼표로 적으세요"),
])
def test_ordinal_cli_rejects_bad_probs(capsys, bad, fragment):
    assert main(["ordinal", "--probs", bad, "--or", "1.8", "--power", "0.8"]) == 2
    assert fragment in capsys.readouterr().err


def test_ordinal_cli_probs2_length_mismatch(capsys):
    assert main(["ordinal", "--probs", "0.2,0.3,0.5",
                 "--probs2", "0.25,0.25,0.25,0.25", "--power", "0.8"]) == 2
    assert "범주 개수" in capsys.readouterr().err


def test_ordinal_cli_requires_or_or_probs2(capsys):
    assert main(["ordinal", "--probs", "0.2,0.3,0.5", "--power", "0.8"]) == 2
    assert "--or" in capsys.readouterr().err


def test_ordinal_cli_rejects_both_or_and_probs2(capsys):
    assert main(["ordinal", "--probs", "0.2,0.3,0.5", "--or", "1.8",
                 "--probs2", "0.3,0.3,0.4", "--power", "0.8"]) == 2
    assert "함께 쓸 수 없습니다" in capsys.readouterr().err


def test_ordinal_cli_n_total_and_power_direction(capsys):
    assert main(["ordinal", "--probs", "0.1,0.2,0.4,0.2,0.1", "--or", "1.8",
                 "--n-total", "400"]) == 0
    out = capsys.readouterr().out
    assert "검정력" in out


def test_new_designs_json_round_trip():
    for argv in (["count", "--rate1", "1.2", "--rr", "0.75", "--power", "0.8",
                  "--format", "json"],
                 ["ordinal", "--probs", "0.2,0.3,0.5", "--or", "1.8",
                  "--power", "0.8", "--format", "json"]):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert main(argv) == 0
        plan = json.loads(buf.getvalue())
        assert plan["design"]["key"] in ("count", "ordinal")
        assert plan["sentences"]["kr"]
        assert plan["sentences"]["en"]
        assert "[" not in plan["sentences"]["kr"]      # 조사 실패 표시가 없어야


def test_new_designs_markdown_renders():
    import contextlib
    import io
    for argv in (["count", "--rate1", "1.2", "--rr", "0.75", "--power", "0.8",
                  "--format", "md"],
                 ["ordinal", "--probs", "0.2,0.3,0.5", "--or", "1.8",
                  "--power", "0.8", "--format", "md"]):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert main(argv) == 0
        text = buf.getvalue()
        assert "|" in text and "powerplan" in text


def test_new_designs_make_plan_with_comparisons():
    for design in (CountRateRatio(1.2, 0.75, 0.7, 1.0),
                   OrdinalProportionalOdds((0.2, 0.3, 0.5), 1.8)):
        adj = Adjustments(comparisons=3)
        alpha, info = adj.adjusted_alpha(0.05)
        plan = make_plan(type(design)(*[getattr(design, f.name) if f.name != "alpha"
                                        else alpha
                                        for f in design.__dataclass_fields__.values()]),
                         target_power=0.8, adjustments=adj, alpha_adjustment=info)
        assert plan["design"]["alpha"] == pytest.approx(0.05 / 3)


def test_korean_sentences_have_no_broken_josa(capsys):
    """조사 처리 실패는 '[값]으로' 같은 흔적을 남긴다 — 새 설계에도 없어야."""
    for argv in (["count", "--rate1", "1.2", "--rr", "0.75", "--dispersion", "0.7",
                  "--power", "0.9"],
                 ["count", "--rate1", "7", "--rr", "1.4", "--power", "0.8"],
                 ["ordinal", "--probs", "0.1,0.2,0.4,0.2,0.1", "--or", "1.8",
                  "--power", "0.9"],
                 ["ordinal", "--probs", "0.2,0.3,0.5", "--or", "0.5",
                  "--power", "0.8", "--sides", "1"]):
        assert main(argv) == 0
        out = capsys.readouterr().out
        assert "]으로" not in out
        assert "]이(가)" not in out


# ==========================================================================
# 4) 검토 라운드 5에서 잡힌 결함의 회귀 테스트
# ==========================================================================
def test_ordinal_rejects_partial_cumulative_reaching_one():
    """(0.2,0.3,0.5,1e-7)처럼 합 허용오차 안이어도 부분 누적이 1에 닿으면 거절.

    예전에는 po_shift가 cum/(1-cum)에서 ZeroDivisionError로 트레이스백을 냈다.
    """
    for probs in ((0.2, 0.3, 0.5, 1e-7), (0.5, 0.5, 1e-7), (0.25,) * 4 + (5e-7,),
                  (0.1, 0.2, 0.3, 0.4, 1e-7)):
        with pytest.raises(PowerPlanError) as err:
            OrdinalProportionalOdds(probs, 1.8)
        assert "누적확률" in str(err.value)


def test_ordinal_cli_partial_cumulative_is_clean_error(capsys):
    assert main(["ordinal", "--probs", "0.1,0.2,0.3,0.4,0.0000001", "--or", "1.8",
                 "--power", "0.9"]) == 2
    assert "누적확률" in capsys.readouterr().err


def test_po_shift_guards_degenerate_cumulative_directly():
    with pytest.raises(PowerPlanError):
        po_shift((0.5, 0.5, 1e-9), 1.8)


def test_po_shift_survives_extreme_odds_ratios_without_nan():
    """큰 OR에서 오즈를 직접 곱하면 inf/(1+inf) = nan이 됐다 (로짓 척도로 계산)."""
    for or_ in (1e-300, 1e-6, 1e6, 1e300):
        shifted = po_shift((0.2, 0.3, 0.5), or_)
        assert all(math.isfinite(p) and p >= 0.0 for p in shifted), (or_, shifted)
        assert math.fsum(shifted) == pytest.approx(1.0, abs=1e-9)
        # 누적확률은 단조여야 한다
        cum = 0.0
        for p in shifted:
            cum += p
            assert cum <= 1.0 + 1e-12


def test_ordinal_extreme_or_gives_finite_output_not_nan(capsys):
    assert main(["ordinal", "--probs", "0.5,0.4999999999,0.0000000001",
                 "--or", "1e300", "--power", "0.8"]) == 0
    out = capsys.readouterr().out
    assert "nan" not in out.lower()


def test_ordinal_power_is_zero_not_one_when_tie_factor_is_nan():
    """`tie <= 0.0`은 NaN을 통과시켜 '검정력 100%, n = 2'를 만들었다.

    NaN 비교는 항상 False라 방어 분기를 그냥 지나가고, 뒤의 min(1.0, NaN)이
    1.0을 돌려주기 때문이다. mean_probs를 오염시켜 그 경로만 직접 확인한다.
    """
    class _Nan(OrdinalProportionalOdds):
        def mean_probs(self, ratio=None):
            return (float("nan"), 0.3, 0.5)

    broken = _Nan((0.2, 0.3, 0.5), 1.8)
    assert math.isnan(broken.tie_factor)
    assert broken._power(100.0, 100.0) == 0.0
    assert broken.power(100.0) == 0.0


def test_ordinal_sensitivity_does_not_overflow(capsys):
    assert main(["ordinal", "--probs", "0.33,0.33,0.34", "--or", "1e300",
                 "--sensitivity", "--power", "0.5"]) == 0
    assert "Traceback" not in capsys.readouterr().out


def test_ordinal_scaled_raises_powerplan_error_on_overflow():
    d = OrdinalProportionalOdds((0.33, 0.33, 0.34), 1e300)
    with pytest.raises(PowerPlanError):
        d.scaled(1.5)


def test_count_scaled_raises_powerplan_error_on_overflow():
    d = CountRateRatio(1.2, 1e300, 0.0, 1.0)
    with pytest.raises(PowerPlanError):
        d.scaled(1.5)


@pytest.mark.parametrize("kwargs", [
    dict(rate1=1e-300, rate_ratio=1e-300),                    # rate2 언더플로
    dict(rate1=1e-200, rate_ratio=0.5, exposure=1e-200),      # rate1·t 언더플로
    dict(rate1=1e300, rate_ratio=0.75, exposure=1e300),       # rate1·t 오버플로
])
def test_count_rejects_products_out_of_range(kwargs):
    with pytest.raises(PowerPlanError) as err:
        CountRateRatio(**kwargs)
    assert "범위를 벗어났습니다" in str(err.value)


def test_ordinal_sum_error_shows_the_actual_deviation():
    """'합이 1이어야 하는데 받은 합은 1' 같은 자기모순 메시지를 막는다."""
    with pytest.raises(PowerPlanError) as err:
        OrdinalProportionalOdds((0.333334, 0.333334, 0.333333), 1.8)
    message = str(err.value)
    assert "1.000001" in message and "+1e-06" in message


@pytest.mark.parametrize("probs2,fragment", [
    ("0.1,0.1,0.1", "합이 1이어야"),            # 합이 0.3인데 예전에는 통과했다
    ("0.5,-0.1,0.6", "0보다 커야"),
    ("20,30,50", "퍼센트가 아니라"),
    ("0.2,0.3,0.5", "유도한 오즈비가 1"),
])
def test_ordinal_probs2_gets_the_same_validation_as_probs(capsys, probs2, fragment):
    assert main(["ordinal", "--probs", "0.2,0.3,0.5", "--probs2", probs2,
                 "--power", "0.8"]) == 2
    assert fragment in capsys.readouterr().err


def test_ordinal_probs2_last_category_is_no_longer_ignorable(capsys):
    """예전에는 마지막 원소를 안 써서 0.1,0.2,0.6과 0.1,0.2,0.9가 같은 결과였다."""
    assert main(["ordinal", "--probs", "0.1,0.2,0.7", "--probs2", "0.1,0.2,0.6",
                 "--power", "0.8"]) == 2
    assert "합이 1이어야" in capsys.readouterr().err


def test_count_rate2_equal_to_rate1_names_the_option_the_user_typed(capsys):
    assert main(["count", "--rate1", "1.2", "--rate2", "1.2", "--power", "0.8"]) == 2
    err = capsys.readouterr().err
    assert "--rate2" in err and "--rr" not in err


def test_count_poisson_wording_when_dispersion_is_zero(capsys):
    """k = 0인데 '음이항 회귀, 과산포 k = 0'이라고 쓰면 자기모순이다."""
    assert main(["count", "--rate1", "1.2", "--rr", "0.75", "--power", "0.9"]) == 0
    out = capsys.readouterr().out
    assert "포아송 회귀 발생률비 검정" in out
    assert "음이항 회귀 발생률비 검정" not in out
    assert main(["count", "--rate1", "1.2", "--rr", "0.75", "--dispersion", "0.7",
                 "--power", "0.9"]) == 0
    assert "음이항 회귀 발생률비 검정" in capsys.readouterr().out


def test_count_english_sentence_carries_the_time_unit():
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert main(["count", "--rate1", "1.2", "--rr", "0.75", "--dispersion", "0.7",
                     "--exposure", "2", "--time-unit", "year", "--power", "0.9",
                     "--format", "json"]) == 0
    en = json.loads(buf.getvalue())["sentences"]["en"]
    assert "events per year" in en
    assert "mean exposure 2 year per participant" in en
    assert "per unit time" not in en


def test_ordinal_sentences_state_the_direction():
    import contextlib
    import io
    for or_, kr_word, en_word in ((1.8, "낮은", "lower"), (0.55, "높은", "higher")):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert main(["ordinal", "--probs", "0.1,0.2,0.4,0.2,0.1", "--or", str(or_),
                         "--power", "0.8", "--format", "json"]) == 0
        sent = json.loads(buf.getvalue())["sentences"]
        assert f"중재군이 {kr_word} 범주 쪽으로 이동" in sent["kr"]
        assert f"toward {en_word} categories" in sent["en"]


def test_count_dispersion_reciprocal_warning_is_shown():
    notes = " ".join(CountRateRatio(1.2, 0.75, 0.7, 1.0).notes())
    assert "역수" in notes and "glm.nb" in notes
    assert "1.43" in notes          # θ = 0.7 → k = 1.43


def test_count_variance_null_direction_note_matches_reality():
    """1:1에서는 null이 더 작고, 배분비가 다르면 뒤집힌다 — 문구가 숫자를 따라가야 한다."""
    equal_alt = smallest_unit(CountRateRatio(1.2, 0.75, 0.7, 1.0, variance="alt"), 0.9)
    equal_null = smallest_unit(CountRateRatio(1.2, 0.75, 0.7, 1.0, variance="null"), 0.9)
    assert equal_null < equal_alt
    assert "작아집니다" in " ".join(
        CountRateRatio(1.2, 0.75, 0.7, 1.0, variance="null").notes())

    skew_alt = CountRateRatio(1.2, 0.75, 0.7, 1.0, ratio=2.0, variance="alt")
    skew_null = CountRateRatio(1.2, 0.75, 0.7, 1.0, ratio=2.0, variance="null")
    assert (skew_null.allocation(smallest_unit(skew_null, 0.9))["total"]
            > skew_alt.allocation(smallest_unit(skew_alt, 0.9))["total"])
    assert "커집니다" in " ".join(skew_null.notes())


def test_count_cluster_caveat_present():
    assert "설계효과" in " ".join(CountRateRatio(1.2, 0.75, 0.7, 1.0).notes())
    assert "설계효과" in " ".join(
        OrdinalProportionalOdds((0.2, 0.3, 0.5), 1.8).notes())


def test_count_huge_event_counts_do_not_print_300_digit_integers():
    d = CountRateRatio(1e100, 0.75, 0.0, 1.0)
    lines = dict(d.plan_lines({"n1": 1000, "n2": 1000, "total": 2000}))
    assert "e+" in lines["기대 사건 수"]
    assert len(lines["기대 사건 수"]) < 120


# ==========================================================================
# 5) 돌연변이 검사에서 살아남은 8종을 사살하는 테스트
# ==========================================================================
def _ordinal_power_by_hand(probs, or_, n1, n2, alpha=0.05, sides=2):
    """Whitehead의 정보량 I = N·q1·q2·(1−Σp̄³)/3 를 독립적으로 다시 세운 검정력."""
    probs2 = po_shift(probs, or_)
    n = n1 + n2
    q1, q2 = n1 / n, n2 / n
    pbar = [q1 * a + q2 * b for a, b in zip(probs, probs2)]
    tie = 1.0 - sum(p ** 3 for p in pbar)
    info = n * q1 * q2 * tie / 3.0
    se = math.sqrt(1.0 / info)
    zc = norm_ppf(1.0 - alpha / sides)
    delta = abs(math.log(or_))
    upper = norm_cdf(delta / se - zc)
    lower = norm_cdf(-delta / se - zc) if sides == 2 else 0.0
    return upper + lower


@pytest.mark.parametrize("probs,or_,n1,n2,sides", [
    ((0.1, 0.2, 0.4, 0.2, 0.1), 1.8, 60, 60, 2),
    ((0.2, 0.2, 0.2, 0.2, 0.2), 1.05, 20, 20, 2),    # 아래 꼬리가 실제로 기여하는 영역
    ((0.1, 0.2, 0.4, 0.2, 0.1), 1.8, 40, 90, 2),     # 배분이 self.ratio와 다른 경우
    ((0.1, 0.2, 0.4, 0.2, 0.1), 1.8, 60, 60, 1),     # 단측
])
def test_ordinal_power_matches_hand_formula(probs, or_, n1, n2, sides):
    d = OrdinalProportionalOdds(probs, or_, sides=sides)
    got = d.power_of_allocation({"n1": n1, "n2": n2, "total": n1 + n2})
    assert got == pytest.approx(
        _ordinal_power_by_hand(probs, or_, n1, n2, sides=sides), abs=1e-12)


def test_ordinal_two_sided_power_floor_is_alpha_not_half():
    """효과가 없을 때 양측 검정력은 **α**로 수렴해야 한다 (α/2가 아니라).

    아래 꼬리를 빠뜨리면 여기서 정확히 절반(0.025)이 나온다. 이 툴의 --sides 1은
    '한쪽 꼬리에 α를 다 쓰는' 단측이므로 단측의 귀무값도 α다.
    """
    near_null = OrdinalProportionalOdds((0.2,) * 5, 1.000001)
    assert near_null.power(10) == pytest.approx(0.05, abs=1e-3)
    assert near_null.power(10000) == pytest.approx(0.05, abs=2e-3)
    one_sided = OrdinalProportionalOdds((0.2,) * 5, 1.000001, sides=1)
    assert one_sided.power(10) == pytest.approx(0.05, abs=1e-3)
    # 반대 방향(OR < 1)도 양측에서는 같은 검정력이어야 아래 꼬리가 살아 있는 것이다
    down = OrdinalProportionalOdds((0.2,) * 5, 1.0 / 1.8)
    up = OrdinalProportionalOdds((0.2,) * 5, 1.8)
    assert down.power(150) == pytest.approx(up.power(150), abs=1e-9)


def test_count_one_sided_uses_the_full_alpha():
    d1 = CountRateRatio(1.2, 0.75, 0.7, 1.0, sides=1)
    d2 = CountRateRatio(1.2, 0.75, 0.7, 1.0, sides=2)
    assert d1.power(300) == pytest.approx(
        _count_power_by_hand(1.2, 0.75, 0.7, 1.0, 300, 300, alpha=0.05, sides=1),
        abs=1e-12)
    assert d1.power(300) > d2.power(300)
    assert smallest_unit(d1, 0.9) < smallest_unit(d2, 0.9)


def test_ordinal_reported_tie_factor_is_the_one_used_in_power():
    """화면에 찍는 보정계수와 검정력이 쓰는 보정계수가 갈라지지 않게."""
    d = OrdinalProportionalOdds((0.1, 0.2, 0.4, 0.2, 0.1), 1.8)
    pbar = d.mean_probs()
    assert d.tie_factor == pytest.approx(1.0 - sum(p ** 3 for p in pbar), abs=1e-15)
    assert "0.9220" in d.effect()["label"]
    assert "0.9220" in dict(d.plan_lines({"n1": 10, "n2": 10, "total": 20}))[
        "동점 보정계수 1 − Σp̄³"]
    # 표시값에서 검정력을 되짚어 만들어 본다
    n = 200.0
    info = n * 0.25 * d.tie_factor / 3.0
    se = math.sqrt(1.0 / info)
    zc = norm_ppf(0.975)
    delta = abs(math.log(1.8))
    assert d.power(100.0) == pytest.approx(
        norm_cdf(delta / se - zc) + norm_cdf(-delta / se - zc), abs=1e-12)


def test_ordinal_probs2_uses_geometric_mean_of_cut_odds_ratios(capsys):
    """산술평균으로 바꾸면 비례오즈가 깨진 예에서 표본수가 5배 작아진다."""
    from powerplan.cli import _or_from_two_distributions
    probs = (0.25, 0.25, 0.25, 0.25)
    probs2 = (0.5, 0.05, 0.05, 0.4)
    or_, cuts = _or_from_two_distributions(probs, probs2)
    expected = [3.0, (0.55 / 0.45) / 1.0, 0.5]
    assert cuts == pytest.approx(expected, rel=1e-12)
    geo = math.exp(sum(math.log(c) for c in expected) / 3.0)
    assert or_ == pytest.approx(geo, rel=1e-12)
    assert geo < sum(expected) / 3.0          # 산술평균과 확실히 다르다
    main(["ordinal", "--probs", "0.25,0.25,0.25,0.25",
          "--probs2", "0.5,0.05,0.05,0.4", "--power", "0.8", "--format", "json"])
    plan = json.loads(capsys.readouterr().out)
    assert plan["design"]["effect"]["value"] == pytest.approx(geo, rel=1e-9)
    assert plan["analysis"]["allocation"]["n1"] == smallest_unit(
        OrdinalProportionalOdds(probs, geo), 0.8)


@pytest.mark.parametrize("design_at", [
    lambda ratio: CountRateRatio(1.2, 0.75, 0.7, 1.0, ratio=ratio),
    lambda ratio: OrdinalProportionalOdds((0.1, 0.2, 0.4, 0.2, 0.1), 1.8, ratio=ratio),
])
def test_ratio_flows_through_power_not_just_allocation(design_at):
    """--ratio가 allocation에만 반영되고 power()를 안 거치면 표본수가 크게 틀린다."""
    skewed = design_at(2.0)
    equal = design_at(1.0)
    unit = smallest_unit(skewed, 0.9)
    alloc = skewed.allocation(unit)
    assert alloc["n2"] == 2 * alloc["n1"]
    assert skewed.power(unit) == pytest.approx(
        skewed.power_of_allocation(alloc), abs=1e-12)
    equal_unit = smallest_unit(equal, 0.9)
    assert alloc["n1"] < equal_unit                  # 1군은 줄고
    assert alloc["total"] > 2 * equal_unit           # 총 N은 늘어난다
