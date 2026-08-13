"""GRIM / GRIMMER 의 산술. 손으로 검산한 값과 대조한다."""

from __future__ import annotations

import pytest

from numcheck.grim import grim_check, grim_has_power, grimmer_check
from numcheck.rounding import parse_number


def R(text):
    return parse_number(text)


def test_grim_violation_from_proposal():
    """ISI 평균 14.37, N = 23 → 14.37 × 23 = 330.51 (정수 아님) → 위반.

    이웃 후보를 손으로 확인: 330/23 = 14.3478, 331/23 = 14.3913.
    허용 구간 [14.36, 14.38] 안에 둘 다 없다.
    """
    result = grim_check(R("14.37"), 23, 1.0, 0, 28)
    assert result.consistent is False
    assert result.has_power is True
    assert result.nearest == pytest.approx(331 / 23, rel=1e-9)


def test_grim_consistent_value():
    """11.13 × 23 = 255.99 → 256/23 = 11.1304 이 구간 [11.12, 11.14] 안에 있다."""
    result = grim_check(R("11.13"), 23, 1.0, 0, 21)
    assert result.consistent is True
    assert result.numerator == pytest.approx(256.0)


def test_grim_has_no_power_when_n_is_large():
    """N = 500 이면 가능한 평균 간격이 0.002 라 두 자리 평균은 늘 가능하다."""
    assert grim_has_power(R("14.37"), 23) is True
    assert grim_has_power(R("14.37"), 500) is False
    # 판별력이 없으면 어떤 값이든 통과한다
    assert grim_check(R("14.37"), 500, 1.0, 0, 28).consistent is True


def test_grim_one_decimal_has_little_power():
    """소수 한 자리 + N = 23 → 간격 0.043 < 허용폭 0.2 → 판별력 없음."""
    assert grim_has_power(R("18.4"), 23) is False


def test_grim_out_of_scale_range_is_flagged():
    result = grim_check(R("31.42"), 23, 1.0, 0, 28)
    assert result.consistent is False
    assert "범위" in result.reason


def test_grim_percent_of_count_scale():
    """50문항 정답률(%): 개인 점수 증분은 2%p. N = 7 에서 가능한 평균은 2k/7."""
    scale_unit = 100 / 50
    ok = grim_check(R("62.86"), 7, scale_unit, 0, 100)   # 220/7 = 62.857…
    bad = grim_check(R("62.40"), 7, scale_unit, 0, 100)
    assert ok.consistent is True
    assert bad.consistent is False


def test_grim_handles_degenerate_inputs():
    assert grim_check(R("10.0"), 0).consistent is True
    assert grim_check(R("10.0"), 5, 0.0).consistent is True


def test_grimmer_accepts_a_constructible_combination():
    """실제 정수 자료 [1, 2, 3, 4, 5]: 평균 3.00, 표본 SD = 1.58."""
    ok, reason = grimmer_check(R("3.00"), R("1.58"), 5, 0, 10)
    assert ok is True
    assert reason == ""


def test_grimmer_rejects_impossible_sd():
    """평균 3.00 (합계 15, 홀수) 에서 제곱합의 홀짝이 맞지 않는 SD."""
    ok, _ = grimmer_check(R("3.00"), R("1.60"), 5, 0, 10)
    assert ok is False


def test_grimmer_defers_when_grim_already_failed():
    """GRIM 이 이미 잡은 건 GRIMMER 가 중복으로 지적하지 않는다."""
    ok, _ = grimmer_check(R("14.37"), R("3.21"), 23, 0, 28)
    assert ok is True


def test_grimmer_needs_at_least_two_observations():
    assert grimmer_check(R("3.00"), R("1.00"), 1)[0] is True


def test_grimmer_rejects_negative_sd():
    assert grimmer_check(R("3.00"), R("-1.00"), 5)[0] is False


def test_grimmer_population_sd_convention_is_allowed():
    """모집단 SD(n) 로 계산한 값도 통과해야 한다 — 관례를 추측해서 지적하지 않는다."""
    # [1,2,3,4,5]: 모집단 SD = sqrt(2) = 1.41, 표본 SD = 1.58
    assert grimmer_check(R("3.00"), R("1.41"), 5, 0, 10)[0] is True
