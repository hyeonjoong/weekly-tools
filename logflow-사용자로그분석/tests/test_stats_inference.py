"""추론 통계(Newcombe · Fisher · Mann-Whitney · Kaplan-Meier · log-rank · Holm) 검증.

가능한 곳은 **독립 구현(브루트포스)이나 손계산 값**과 비교한다 —
같은 코드를 다시 쓰는 자기충족 테스트가 되지 않도록.
"""

import math

import pytest

from logflow.stats import (
    fisher_exact_two_sided,
    holm_adjust,
    kaplan_meier,
    logrank_test,
    mann_whitney_u,
    newcombe_diff_interval,
    wilson_interval,
)


# ---------------------------------------------------------------- Fisher exact

def _fisher_bruteforce(a, b, c, d):
    """math.comb 로 초기하 확률을 직접 더한 독립 구현 (lgamma 판을 검증용)."""
    row1, row2 = a + b, c + d
    col1 = a + c
    n = row1 + row2
    if row1 == 0 or row2 == 0 or col1 == 0 or (b + d) == 0:
        return 1.0

    def pmf(k):
        return math.comb(row1, k) * math.comb(row2, col1 - k) / math.comb(n, col1)

    p_obs = pmf(a)
    lo, hi = max(0, col1 - row2), min(row1, col1)
    return min(1.0, sum(pmf(k) for k in range(lo, hi + 1) if pmf(k) <= p_obs * (1 + 1e-7)))


def test_fisher_matches_known_reference_value():
    # R: fisher.test(matrix(c(3,1,1,3), nrow=2))$p.value == 0.4857142857142857
    assert abs(fisher_exact_two_sided(3, 1, 1, 3) - 0.4857142857142857) < 1e-12


def test_fisher_matches_bruteforce_over_many_tables():
    for a in range(0, 7):
        for b in range(0, 7):
            for c in range(0, 7):
                for d in range(0, 7):
                    got = fisher_exact_two_sided(a, b, c, d)
                    want = _fisher_bruteforce(a, b, c, d)
                    assert abs(got - want) < 1e-9, (a, b, c, d, got, want)


def test_fisher_perfect_separation_is_small_and_symmetric():
    p = fisher_exact_two_sided(10, 0, 0, 10)
    assert p < 1e-4
    assert abs(p - fisher_exact_two_sided(0, 10, 10, 0)) < 1e-15


def test_fisher_rejects_negative_cells():
    with pytest.raises(ValueError):
        fisher_exact_two_sided(-1, 2, 3, 4)


# ---------------------------------------------------------------- Newcombe 차이

def test_newcombe_matches_documented_formula():
    s1, n1, s2, n2 = 3, 4, 1, 4
    r = newcombe_diff_interval(s1, n1, s2, n2)
    l1, u1 = wilson_interval(s1, n1)
    l2, u2 = wilson_interval(s2, n2)
    p1, p2 = s1 / n1, s2 / n2
    lo = (p1 - p2) - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = (p1 - p2) + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    assert abs(r.diff - 0.5) < 1e-12
    assert abs(r.ci[0] - lo) < 1e-12
    assert abs(r.ci[1] - hi) < 1e-12


def test_newcombe_is_antisymmetric_under_group_swap():
    """군을 바꾸면 차이와 구간이 부호만 뒤집혀야 한다 (항이 뒤바뀐 버그를 잡는다)."""
    for (s1, n1, s2, n2) in [(3, 4, 1, 4), (0, 10, 7, 9), (12, 12, 5, 12), (1, 3, 1, 30)]:
        a = newcombe_diff_interval(s1, n1, s2, n2)
        b = newcombe_diff_interval(s2, n2, s1, n1)
        assert abs(a.diff + b.diff) < 1e-12
        assert abs(a.ci[0] + b.ci[1]) < 1e-12
        assert abs(a.ci[1] + b.ci[0]) < 1e-12


def test_newcombe_interval_contains_diff_and_stays_in_range():
    for (s1, n1, s2, n2) in [(0, 5, 5, 5), (5, 5, 0, 5), (1, 2, 1, 2), (50, 100, 40, 100)]:
        r = newcombe_diff_interval(s1, n1, s2, n2)
        assert -1.0 <= r.ci[0] <= r.diff <= r.ci[1] <= 1.0


def test_newcombe_none_when_a_group_is_empty():
    assert newcombe_diff_interval(0, 0, 1, 5) is None


# ---------------------------------------------------------------- Mann-Whitney

def test_mann_whitney_complete_separation():
    r = mann_whitney_u([1, 2, 3, 4], [5, 6, 7, 8])
    assert r.u == 0.0                      # R1=10, U1 = 10 - 4*5/2 = 0
    assert abs(r.rank_biserial + 1.0) < 1e-12   # 완전히 작음 → -1
    assert r.p < 0.05


def test_mann_whitney_u_matches_pairwise_count_definition():
    """U 는 '쌍 비교에서 x 가 큰 횟수 + 동점의 절반' 과 같아야 한다 (독립 정의)."""
    x = [3.0, 1.0, 4.0, 1.0, 5.0]
    y = [2.0, 7.0, 1.0, 8.0]
    wins = sum((a > b) + 0.5 * (a == b) for a in x for b in y)
    r = mann_whitney_u(x, y)
    assert abs(r.u - wins) < 1e-12
    assert abs(r.rank_biserial - (2 * wins / (len(x) * len(y)) - 1)) < 1e-12


def test_mann_whitney_all_ties_gives_p_one():
    r = mann_whitney_u([2, 2, 2], [2, 2, 2])
    assert r.p == 1.0
    assert r.rank_biserial == 0.0


def test_mann_whitney_none_when_group_empty():
    assert mann_whitney_u([], [1, 2]) is None


# ---------------------------------------------------------------- Kaplan-Meier

def test_km_no_censoring_matches_hand_computation():
    km = kaplan_meier([1.0, 2.0, 3.0], [True, True, True])
    surv = [p.survival for p in km.points]
    assert abs(surv[0] - 2 / 3) < 1e-12
    assert abs(surv[1] - 1 / 3) < 1e-12
    assert abs(surv[2] - 0.0) < 1e-12
    assert km.median_survival == 2.0   # S(2)=1/3 <= 0.5 이 처음 되는 시점
    assert km.n == 3 and km.n_events == 3


def test_km_censoring_keeps_censored_at_risk_at_same_time():
    # t=1 사건(위험 4), t=2 절단, t=3 사건(위험 2) → S = 0.75, 0.375
    km = kaplan_meier([1.0, 2.0, 3.0, 4.0], [True, False, True, False])
    assert [p.n_risk for p in km.points] == [4, 2]
    assert abs(km.points[0].survival - 0.75) < 1e-12
    assert abs(km.points[1].survival - 0.375) < 1e-12
    assert km.median_survival == 3.0


def test_km_survival_is_non_increasing_and_ci_brackets_estimate():
    times = [1, 1, 2, 3, 3, 5, 8, 8, 13, 21]
    events = [True, False, True, True, False, True, False, True, True, False]
    km = kaplan_meier(times, events)
    prev = 1.0
    for p in km.points:
        assert p.survival <= prev + 1e-15
        prev = p.survival
        if p.ci is not None:
            assert 0.0 <= p.ci[0] <= p.survival <= p.ci[1] <= 1.0


def test_km_rejects_mismatched_lengths_and_negative_times():
    with pytest.raises(ValueError):
        kaplan_meier([1.0], [True, False])
    with pytest.raises(ValueError):
        kaplan_meier([-1.0], [True])
    assert kaplan_meier([], []) is None


# ---------------------------------------------------------------- log-rank

def test_logrank_matches_hand_computation():
    # g1: 사건 t=1,2 / g2: 사건 t=3,4  (손계산: O1=2, E1=5/6, V=0.25+2/9)
    r = logrank_test([1.0, 2.0], [True, True], [3.0, 4.0], [True, True])
    e1 = 0.5 + 1 / 3
    v = 0.25 + 4 / 18
    assert abs(r.expected1 - e1) < 1e-12
    assert r.observed1 == 2
    assert abs(r.chi2 - (2 - e1) ** 2 / v) < 1e-12
    assert 0.05 < r.p < 0.15


def test_logrank_observed_and_expected_balance_across_arms():
    """O-E 는 두 군에서 정확히 상쇄돼야 한다.

    한 군만 위험집합에 남은 시각의 사건은 분산이 정의되지 않아 통계량에 기여할 수
    없으므로 O 와 E 양쪽에서 똑같이 제외된다. 한쪽만 제외하고 나머지를 상대 군에
    떠넘기면 이 항등식이 깨진다(그러면 군별 O/E 가 뒤바뀌어 보고된다).
    """
    t1, e1 = [2.0, 4.0, 6.0, 9.0], [True, True, False, True]
    t2, e2 = [1.0, 3.0, 5.0, 12.0], [True, False, True, True]
    r = logrank_test(t1, e1, t2, e2)
    assert abs((r.observed1 - r.expected1) + (r.observed2 - r.expected2)) < 1e-9
    assert r.observed1 + r.observed2 == pytest.approx(r.expected1 + r.expected2)
    assert r.observed1 + r.observed2 <= sum(e1) + sum(e2)


def test_logrank_per_arm_counts_are_not_swapped():
    """비교 가능한 시각이 하나뿐인 경우에도 군별 O/E 가 서로 뒤바뀌지 않아야 한다.

    손계산: t=1 에서 n1=2, n2=1, d1=1, d2=1 → E1=4/3, E2=2/3.
    t=5 는 2군의 위험집합이 비어 제외된다.
    """
    r = logrank_test([1.0, 5.0], [True, True], [1.0], [True])
    assert (r.observed1, r.observed2) == (1, 1)
    assert r.expected1 == pytest.approx(4 / 3)
    assert r.expected2 == pytest.approx(2 / 3)
    assert r.chi2 == pytest.approx(0.5)


def test_logrank_identical_groups_is_not_significant():
    t = [1.0, 2.0, 3.0, 4.0, 5.0]
    ev = [True] * 5
    r = logrank_test(t, ev, list(t), list(ev))
    assert r.chi2 < 1e-9
    assert r.p > 0.99


def test_logrank_none_without_events():
    assert logrank_test([1.0], [False], [2.0], [False]) is None
    assert logrank_test([], [], [1.0], [True]) is None


# ---------------------------------------------------------------- Holm

def test_holm_hand_computed():
    # 정렬 [0.01,0.02,0.03] → 3*0.01=0.03, 2*0.02=0.04, 1*0.03=0.03→누적최대 0.04
    assert holm_adjust([0.01, 0.02, 0.03]) == pytest.approx([0.03, 0.04, 0.04])


def test_holm_preserves_input_order():
    # 정렬 [0.001, 0.4, 0.6] → 3*0.001=0.003, 2*0.4=0.8, 1*0.6=0.6→누적최대 0.8
    assert holm_adjust([0.6, 0.001, 0.4]) == pytest.approx([0.8, 0.003, 0.8])


def test_holm_clamps_to_one():
    assert holm_adjust([0.6, 0.7]) == pytest.approx([1.0, 1.0])


def test_holm_is_monotone_and_never_below_raw():
    ps = [0.001, 0.004, 0.02, 0.04, 0.3]
    adj = holm_adjust(ps)
    assert all(a >= p - 1e-15 for a, p in zip(adj, ps))
    assert adj == sorted(adj)          # 입력이 정렬돼 있으면 출력도 비감소
    assert all(a <= 1.0 for a in adj)


def test_holm_single_and_empty():
    assert holm_adjust([0.02]) == [0.02]
    assert holm_adjust([]) == []


def test_holm_rejects_out_of_range():
    with pytest.raises(ValueError):
        holm_adjust([0.5, 1.5])
    with pytest.raises(ValueError):
        holm_adjust([float("nan")])
