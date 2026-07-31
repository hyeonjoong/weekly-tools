"""독립 2군(Mann–Whitney) 통계 — 1차 원리(완전열거)로 검증.

핵심 주장:
  - 정확 영분포는 결합 표본의 모든 배열을 직접 세어 만든 것과 일치한다.
  - 정확 p값은 손으로 셀 수 있는 작은 경우와 일치한다.
  - HL 신뢰구간은 정확 검정과 **쌍대**다(구간 밖 ⇔ 기각).
  - 동점이 있으면 정규 근사로 자동 전환하고, 동점 보정 분산을 쓴다.
"""

import itertools
import math
import random

import pytest

from hrvkit.stats import (EXACT_MAX_N_2SAMPLE, hodges_lehmann_2sample,
                          mann_whitney_ci, mann_whitney_u,
                          mannwhitney_null_counts, pairwise_differences,
                          unpaired_summary)


# --------------------------------------------------------------------------- #
# 정확 영분포
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("m,n", [(0, 0), (0, 3), (3, 0), (1, 1), (2, 3),
                                 (3, 4), (4, 4), (5, 2)])
def test_null_counts_total_is_binomial(m, n):
    counts = mannwhitney_null_counts(m, n)
    assert len(counts) == m * n + 1
    assert sum(counts) == math.comb(m + n, m)


@pytest.mark.parametrize("m,n", [(2, 3), (3, 3), (3, 4), (4, 2)])
def test_null_counts_match_brute_force_enumeration(m, n):
    """모든 C(m+n, m) 배열을 직접 세어 DP 결과와 비교."""
    brute = [0] * (m * n + 1)
    for pos in itertools.combinations(range(m + n), m):
        a = set(pos)
        u = 0
        for j in range(m + n):
            if j not in a:                      # j 는 군2 원소
                u += sum(1 for i in a if i < j)  # 그보다 작은 군1 원소 수
        brute[u] += 1
    assert brute == mannwhitney_null_counts(m, n)


def test_null_distribution_is_symmetric():
    counts = mannwhitney_null_counts(4, 5)
    assert counts == counts[::-1]


def test_null_counts_rejects_negative():
    with pytest.raises(ValueError):
        mannwhitney_null_counts(-1, 3)


# --------------------------------------------------------------------------- #
# 검정통계량과 p값
# --------------------------------------------------------------------------- #
def test_u_statistic_hand_computable():
    """군2가 전부 크면 U = m·n (최댓값)."""
    r = mann_whitney_u([1, 2, 3], [4, 5, 6])
    assert r["u_stat"] == pytest.approx(9.0)
    assert r["cles"] == pytest.approx(1.0)
    assert r["rank_biserial"] == pytest.approx(1.0)
    # 완전분리에서 정확 양측 p = 2/C(6,3) = 2/20 = 0.1
    assert r["p_value"] == pytest.approx(0.1)
    assert r["method"] == "exact"


def test_u_statistic_direction_reversed():
    r = mann_whitney_u([4, 5, 6], [1, 2, 3])
    assert r["u_stat"] == pytest.approx(0.0)
    assert r["rank_biserial"] == pytest.approx(-1.0)
    assert r["p_value"] == pytest.approx(0.1)


def test_exact_p_for_4_vs_4_full_separation():
    r = mann_whitney_u([1, 2, 3, 4], [5, 6, 7, 8])
    assert r["p_value"] == pytest.approx(2.0 / math.comb(8, 4))


def test_u_statistic_by_direct_pair_counting():
    """U = #{b>a} + 0.5·#{동점} 정의를 쌍 열거로 직접 확인(동점 포함)."""
    a = [1.0, 4.0, 4.0, 7.0]
    b = [2.0, 4.0, 9.0]
    expected = sum(1.0 for y in b for x in a if y > x) + \
        0.5 * sum(1.0 for y in b for x in a if y == x)
    assert mann_whitney_u(a, b)["u_stat"] == pytest.approx(expected)


def test_ties_force_normal_approximation():
    r = mann_whitney_u([1, 2, 3], [3, 4, 5])
    assert r["method"] == "approx"
    assert 0.0 <= r["p_value"] <= 1.0


def test_exact_method_rejects_ties():
    with pytest.raises(ValueError):
        mann_whitney_u([1, 2, 3], [3, 4, 5], method="exact")


def test_identical_groups_give_p_one():
    r = mann_whitney_u([5, 5, 5], [5, 5, 5])
    assert r["p_value"] == pytest.approx(1.0)


def test_empty_group_returns_nan_not_crash():
    r = mann_whitney_u([], [1, 2, 3])
    assert r["n_a"] == 0
    assert r["u_stat"] != r["u_stat"]        # NaN


def test_unknown_method_rejected():
    with pytest.raises(ValueError):
        mann_whitney_u([1, 2], [3, 4], method="bogus")


def test_large_samples_use_approximation():
    n = EXACT_MAX_N_2SAMPLE + 1
    a = [float(i) for i in range(n)]
    b = [float(i) + 0.5 for i in range(n)]
    assert mann_whitney_u(a, b)["method"] == "approx"


def test_p_value_matches_exhaustive_permutation_p():
    """정확 p를 **재배치 검정**(모든 군배정 열거)으로 독립 검증."""
    a = [1.0, 3.0, 6.0]
    b = [2.0, 8.0, 9.0, 11.0]
    obs = mann_whitney_u(a, b)
    pooled = a + b
    m = len(a)
    us = []
    for pos in itertools.combinations(range(len(pooled)), m):
        aa = [pooled[i] for i in pos]
        bb = [pooled[i] for i in range(len(pooled)) if i not in set(pos)]
        us.append(mann_whitney_u(aa, bb)["u_stat"])
    u = obs["u_stat"]
    lower = sum(1 for v in us if v <= u) / len(us)
    upper = sum(1 for v in us if v >= u) / len(us)
    assert obs["p_value"] == pytest.approx(min(1.0, 2 * min(lower, upper)))


# --------------------------------------------------------------------------- #
# Hodges–Lehmann 이동량과 신뢰구간
# --------------------------------------------------------------------------- #
def test_hl_shift_is_median_of_pairwise_differences():
    a = [1.0, 2.0, 4.0]
    b = [10.0, 20.0]
    diffs = sorted(y - x for y in b for x in a)
    assert len(diffs) == 6
    assert hodges_lehmann_2sample(a, b) == pytest.approx(
        (diffs[2] + diffs[3]) / 2.0)


def test_hl_shift_recovers_constant_shift():
    a = [810.0, 795.0, 823.0, 801.0, 788.0]
    b = [v + 42.0 for v in a]
    assert hodges_lehmann_2sample(a, b) == pytest.approx(42.0)


def test_pairwise_differences_count_and_order():
    d = pairwise_differences([1, 2], [5, 9, 11])
    assert len(d) == 6
    assert d == sorted(d)


def test_hl_shift_empty_is_nan():
    v = hodges_lehmann_2sample([], [1.0])
    assert v != v


def test_ci_contains_hl_point_estimate():
    a = [10.0, 12.0, 14.0, 9.0, 11.0, 13.0]
    b = [20.0, 25.0, 22.0, 27.0, 21.0, 24.0]
    ci = mann_whitney_ci(a, b, alpha=0.05)
    assert ci["ci_low"] <= ci["hl_shift"] <= ci["ci_high"]


def test_ci_duality_with_exact_test():
    """CI가 Δ를 배제 ⇔ Δ만큼 이동한 자료에서 정확검정이 α에서 기각.

    쌍대성이 깨지면 "p>0.05 인데 CI가 그 값을 배제" 같은 자기모순이 납니다.
    """
    rng = random.Random(4242)
    checked = 0
    for _ in range(60):
        m = rng.randint(3, 7)
        n = rng.randint(3, 7)
        a = [v / 7.0 for v in rng.sample(range(1000), m)]
        b = [v / 7.0 for v in rng.sample(range(1000, 2000), n)]
        for alpha in (0.05, 0.1, 0.2):
            ci = mann_whitney_ci(a, b, alpha=alpha)
            if ci["ci_method"] == "insufficient-n":
                continue
            probes = [ci["ci_low"] - 1.0, ci["ci_low"] + 1e-9,
                      ci["ci_high"] - 1e-9, ci["ci_high"] + 1.0]
            for delta in probes:
                shifted = [y - delta for y in b]
                rejects = mann_whitney_u(a, shifted)["p_value"] < alpha
                inside = ci["ci_low"] <= delta <= ci["ci_high"]
                assert rejects != inside
                checked += 1
    assert checked > 100


def test_ci_insufficient_n_is_infinite_not_fake():
    """n=2 vs 2 에서 정확검정 최소 p = 2/C(4,2) = 0.333 → 95% 유한구간 없음."""
    ci = mann_whitney_ci([1.0, 2.0], [10.0, 20.0], alpha=0.05)
    assert ci["ci_method"] == "insufficient-n"
    assert ci["ci_low"] == float("-inf")
    assert ci["ci_high"] == float("inf")


def test_ci_alpha_must_be_in_unit_interval():
    with pytest.raises(ValueError):
        mann_whitney_ci([1, 2, 3], [4, 5, 6], alpha=0.0)
    with pytest.raises(ValueError):
        mann_whitney_ci([1, 2, 3], [4, 5, 6], alpha=1.0)


def test_wider_alpha_gives_narrower_ci():
    a = [10.0, 12.0, 14.0, 9.0, 11.0, 13.0, 15.0]
    b = [20.0, 25.0, 22.0, 27.0, 21.0, 24.0, 26.0]
    wide = mann_whitney_ci(a, b, alpha=0.01)
    narrow = mann_whitney_ci(a, b, alpha=0.20)
    assert (wide["ci_high"] - wide["ci_low"]) >= \
           (narrow["ci_high"] - narrow["ci_low"])


def test_ci_empty_group_is_nan():
    ci = mann_whitney_ci([], [1.0, 2.0])
    assert ci["ci_low"] != ci["ci_low"]


# --------------------------------------------------------------------------- #
# unpaired_summary
# --------------------------------------------------------------------------- #
def test_unpaired_summary_effect_sizes_hand_checked():
    a = [10.0, 12.0, 14.0]      # mean 12, sd 2
    b = [20.0, 22.0, 24.0]      # mean 22, sd 2
    s = unpaired_summary(a, b)
    assert s["mean_a"] == pytest.approx(12.0)
    assert s["mean_b"] == pytest.approx(22.0)
    assert s["mean_diff"] == pytest.approx(10.0)
    assert s["sd_pooled"] == pytest.approx(2.0)
    assert s["cohens_d"] == pytest.approx(5.0)
    j = 1.0 - 3.0 / (4.0 * 6 - 9.0)
    assert s["hedges_g"] == pytest.approx(5.0 * j)
    assert s["hl_shift"] == pytest.approx(10.0)


def test_unpaired_summary_drops_nan_values():
    nan = float("nan")
    s = unpaired_summary([1.0, nan, 3.0], [5.0, 7.0, nan])
    assert s["n_a"] == 2
    assert s["n_b"] == 2


def test_unpaired_summary_all_nan_returns_counts_only():
    nan = float("nan")
    s = unpaired_summary([nan, nan], [nan])
    assert s == {"n_a": 0, "n_b": 0}


def test_unpaired_summary_single_value_groups_do_not_crash():
    s = unpaired_summary([5.0], [9.0])
    assert s["n_a"] == 1 and s["n_b"] == 1
    assert s["hl_shift"] == pytest.approx(4.0)
    assert s["sd_a"] == 0.0


def test_unpaired_summary_zero_variance_gives_nan_d():
    s = unpaired_summary([5.0, 5.0], [5.0, 5.0])
    assert s["cohens_d"] != s["cohens_d"]        # NaN, not inf
    assert s["mw_p"] == pytest.approx(1.0)


def test_cles_is_probability_b_exceeds_a():
    a = [1.0, 2.0, 3.0, 4.0]
    b = [2.5, 5.0]
    s = unpaired_summary(a, b)
    # b=2.5 는 a 중 2개보다 큼, b=5 는 4개 모두보다 큼 → U=6, mn=8
    assert s["cles"] == pytest.approx(6.0 / 8.0)
