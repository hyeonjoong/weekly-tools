# -*- coding: utf-8 -*-
"""정확 이항 구간 — 참조값이 아니라 **이항 항등식 자체**로 검증한다."""

import math

import pytest

from jamoscore import binom

GRID_N = (10, 18, 25, 50)


def tail_ge(k, n, p):
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def tail_le(k, n, p):
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(0, k + 1))


@pytest.mark.parametrize("n", GRID_N)
def test_clopper_pearson_satisfies_binomial_tail_identity(n):
    """CP 하한 L 은 P(X >= k | L) = alpha/2 를 만족해야 한다 (오차 <= 1e-10)."""
    worst = 0.0
    for k in range(n + 1):
        lo, hi = binom.clopper_pearson(k, n, 0.05)
        if k > 0:
            worst = max(worst, abs(tail_ge(k, n, lo) - 0.025))
        if k < n:
            worst = max(worst, abs(tail_le(k, n, hi) - 0.025))
    assert worst <= 1e-10, worst


@pytest.mark.parametrize("n", GRID_N)
def test_clopper_pearson_closed_form_at_zero(n):
    """k=0 의 상한은 1 - (alpha/2)^(1/n) 이라는 닫힌 형태와 같아야 한다."""
    lo, hi = binom.clopper_pearson(0, n, 0.05)
    assert lo == 0.0
    assert abs(hi - (1 - 0.025 ** (1.0 / n))) <= 1e-12


@pytest.mark.parametrize("n", GRID_N)
def test_clopper_pearson_closed_form_at_n(n):
    lo, hi = binom.clopper_pearson(n, n, 0.05)
    assert hi == 1.0
    assert abs(lo - 0.025 ** (1.0 / n)) <= 1e-12


@pytest.mark.parametrize("n", GRID_N)
@pytest.mark.parametrize("p", [0.1, 0.3, 0.5, 0.7, 0.9])
def test_interval_contains_point_estimate(n, p):
    k = int(round(p * n))
    lo, hi = binom.clopper_pearson(k, n, 0.05)
    assert lo <= k / n <= hi


@pytest.mark.parametrize("n", GRID_N)
def test_interval_is_monotone_in_k(n):
    prev_lo = prev_hi = -1.0
    for k in range(n + 1):
        lo, hi = binom.clopper_pearson(k, n, 0.05)
        assert lo >= prev_lo - 1e-12
        assert hi >= prev_hi - 1e-12
        prev_lo, prev_hi = lo, hi


def test_narrower_alpha_gives_wider_interval():
    wide = binom.clopper_pearson(12, 18, 0.01)
    narrow = binom.clopper_pearson(12, 18, 0.10)
    assert wide[0] < narrow[0]
    assert wide[1] > narrow[1]


@pytest.mark.parametrize("x,a,b", [(0.3, 2, 5), (0.5, 1, 1), (0.9, 10, 3),
                                   (0.01, 0.5, 0.5)])
def test_betainc_inverse_roundtrip(x, a, b):
    p = binom.betainc_reg(x, a, b)
    assert abs(binom.betainv_reg(p, a, b) - x) <= 1e-9


def test_betainc_boundaries():
    assert binom.betainc_reg(0.0, 2, 3) == 0.0
    assert binom.betainc_reg(1.0, 2, 3) == 1.0
    assert binom.betainv_reg(0.0, 2, 3) == 0.0
    assert binom.betainv_reg(1.0, 2, 3) == 1.0


def test_betainc_symmetry():
    """I_x(a,b) = 1 - I_(1-x)(b,a)."""
    for x, a, b in [(0.25, 3, 7), (0.6, 4, 4), (0.8, 9, 2)]:
        assert abs(binom.betainc_reg(x, a, b)
                   - (1 - binom.betainc_reg(1 - x, b, a))) <= 1e-12


@pytest.mark.parametrize("bad", [(-1, 10), (11, 10), (5, 0), (5, -1)])
def test_clopper_pearson_rejects_bad_inputs(bad):
    with pytest.raises(ValueError):
        binom.clopper_pearson(bad[0], bad[1])


@pytest.mark.parametrize("alpha", [0.0, 1.0, -0.1, 1.5])
def test_clopper_pearson_rejects_bad_alpha(alpha):
    with pytest.raises(ValueError):
        binom.clopper_pearson(5, 10, alpha)


def test_betainc_rejects_out_of_range():
    with pytest.raises(ValueError):
        binom.betainc_reg(1.5, 2, 3)
    with pytest.raises(ValueError):
        binom.betainc_reg(0.5, 0, 3)


def test_score_interval_is_within_bounds():
    for n in GRID_N:
        for k in range(n + 1):
            lo, hi = binom.score_interval(k, n)
            assert 0 <= lo <= k <= hi <= n


def test_score_interval_18_items_known_shape():
    """n=18 · 사전 12/18(66.7%) 에서 오차 범위는 7~16 정답."""
    assert binom.score_interval(12, 18, 0.05) == (7, 16)


def test_critical_difference_for_18_items():
    """n=18, 사전 66.7% 에서 2문항(11.1%p) 변화는 어느 방향으로도 임계차에 못 미친다."""
    down, up = binom.critical_difference_pp(12, 18, 0.05)
    assert down is not None and up is not None
    assert min(down, up) > 11.1
    assert 25.0 < down < 40.0 and 25.0 < up < 40.0


def test_critical_difference_is_none_in_impossible_direction():
    """천장(18/18)에서 '더 오를 수 있다'고 말하면 거짓말이다."""
    down, up = binom.critical_difference_pp(18, 18, 0.05)
    assert up is None
    assert down is not None and down > 20.0
    down0, up0 = binom.critical_difference_pp(0, 18, 0.05)
    assert down0 is None and up0 is not None


def test_critical_difference_never_contradicts_verdict():
    """임계차보다 큰 변화인데 '미달'로 판정하는 조합이 없어야 한다."""
    for n in (10, 14, 18, 25):
        for k in range(n + 1):
            down, up = binom.critical_difference_pp(k, n)
            lo, hi = binom.score_interval(k, n)
            if down is not None:
                assert binom.verdict(k, lo - 1, n) == "초과"
                assert abs(down - (k - lo + 1) * 100.0 / n) < 1e-9
            if up is not None:
                assert binom.verdict(k, hi + 1, n) == "초과"


def test_format_critical_difference_marks_impossible_direction():
    assert binom.format_critical_difference(18, 18) == "↓27.8 ↑—"
    assert "↓—" in binom.format_critical_difference(0, 18)


def test_verdict_inside_and_outside():
    assert binom.verdict(12, 14, 18) == "미달"
    assert binom.verdict(12, 17, 18) == "초과"
    assert binom.verdict(12, 6, 18) == "초과"


@pytest.mark.parametrize("pre,post,n", [(None, 12, 18), (12, None, 18),
                                        (12, 12, 0), (12, 12, None),
                                        (-1, 12, 18), (12, 99, 18)])
def test_verdict_undetermined(pre, post, n):
    assert binom.verdict(pre, post, n) == "판정불가"


def test_verdict_vocabulary_is_closed():
    """판정 어휘는 세 가지뿐이어야 한다 — '유의하게 호전' 같은 말이 못 새어 나오게."""
    seen = set()
    for n in (10, 18):
        for pre in range(n + 1):
            for post in range(n + 1):
                seen.add(binom.verdict(pre, post, n))
    assert seen <= {"초과", "미달", "판정불가"}


def test_critical_difference_reports_both_directions_separately():
    k, n = 1, 18
    lo, hi = binom.score_interval(k, n)
    down, up = binom.critical_difference_pp(k, n)
    assert down == pytest.approx((k - lo + 1) * 100.0 / n) if lo > 0 else down is None
    assert up == pytest.approx((hi - k + 1) * 100.0 / n)


def test_log_beta_matches_lgamma_definition():
    assert binom.log_beta(3, 5) == pytest.approx(
        math.lgamma(3) + math.lgamma(5) - math.lgamma(8))
