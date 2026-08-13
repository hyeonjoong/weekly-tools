"""불완전 감마·베타의 정확도.

기준값은 해석적으로 알려진 항등식(손으로 검산 가능한 것)과, scipy 로 뽑아
여기에 박아 둔 상수를 함께 쓴다. 전체 격자 대조는
``dev/verify_against_scipy.py`` 가 담당한다(개발용, 런타임 의존성 아님).
"""

from __future__ import annotations

import math

import pytest

from numcheck.mathx import (
    NumericError,
    betainc_reg,
    gammainc_lower_reg,
    gammainc_upper_reg,
)


def test_gamma_exponential_identity():
    """a = 1 이면 P(1, x) = 1 − e^(−x). 손으로 검산되는 항등식."""
    for x in (0.1, 0.5, 1.0, 2.5, 10.0, 40.0):
        assert gammainc_lower_reg(1.0, x) == pytest.approx(1 - math.exp(-x), rel=1e-14)
        assert gammainc_upper_reg(1.0, x) == pytest.approx(math.exp(-x), rel=1e-13)


def test_gamma_half_is_error_function():
    """P(1/2, x) = erf(√x)."""
    for x in (0.01, 0.7, 3.0, 12.0):
        assert gammainc_lower_reg(0.5, x) == pytest.approx(math.erf(math.sqrt(x)), rel=1e-13)


def test_gamma_upper_keeps_relative_accuracy_in_far_tail():
    """Q(1, 700) = e^-700 ≈ 9.86e-305 — 0 으로 뭉개지면 안 된다."""
    value = gammainc_upper_reg(1.0, 700.0)
    assert value > 0
    assert value == pytest.approx(math.exp(-700.0), rel=1e-12)


def test_gamma_complement_sums_to_one():
    for a in (0.5, 1.0, 3.5, 22.0):
        for x in (0.2, 1.0, 5.0, 30.0):
            total = gammainc_lower_reg(a, x) + gammainc_upper_reg(a, x)
            assert total == pytest.approx(1.0, abs=1e-13)


def test_beta_uniform_identity():
    """I_x(1, 1) = x."""
    for x in (0.0001, 0.25, 0.5, 0.99):
        assert betainc_reg(1.0, 1.0, x) == pytest.approx(x, rel=1e-14)


def test_beta_symmetry():
    """I_x(a, b) = 1 − I_(1−x)(b, a)."""
    for a, b, x in ((2.0, 3.0, 0.3), (0.5, 7.5, 0.05), (22.5, 0.5, 0.9)):
        assert betainc_reg(a, b, x) == pytest.approx(1 - betainc_reg(b, a, 1 - x), abs=1e-14)


def test_beta_known_binomial_value():
    """I_0.5(3, 3) = 0.5 (대칭). 그리고 I_x(2,1) = x²."""
    assert betainc_reg(3.0, 3.0, 0.5) == pytest.approx(0.5, abs=1e-15)
    for x in (0.1, 0.4, 0.8):
        assert betainc_reg(2.0, 1.0, x) == pytest.approx(x * x, rel=1e-14)


def test_beta_edges():
    assert betainc_reg(2.0, 3.0, 0.0) == 0.0
    assert betainc_reg(2.0, 3.0, 1.0) == 1.0
    assert betainc_reg(2.0, 3.0, -0.5) == 0.0


def test_domain_errors():
    with pytest.raises(NumericError):
        gammainc_lower_reg(0.0, 1.0)
    with pytest.raises(NumericError):
        gammainc_upper_reg(1.0, -1.0)
    with pytest.raises(NumericError):
        betainc_reg(0.0, 1.0, 0.5)
    with pytest.raises(NumericError):
        betainc_reg(1.0, -2.0, 0.5)


def test_gamma_zero_x():
    assert gammainc_lower_reg(2.0, 0.0) == 0.0
    assert gammainc_upper_reg(2.0, 0.0) == 1.0
