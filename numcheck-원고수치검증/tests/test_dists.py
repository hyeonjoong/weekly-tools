"""검정통계량 → p. scipy 로 뽑아 박아 둔 기준값과 상대오차 ≤1e-9 로 일치해야 한다.

기준값은 scipy 1.17 에서 산출했고, 전체 격자 재검증은
``dev/verify_against_scipy.py`` 로 언제든 다시 돌릴 수 있다. 테스트 자체는
**scipy 를 import 하지 않는다** — 완전 오프라인·의존성 0 이어야 하므로.
"""

from __future__ import annotations

import math

import pytest

from numcheck.dists import (
    chi2_sf,
    f_sf,
    p_from_statistic,
    r_two_tailed,
    t_sf,
    t_two_tailed,
    z_sf,
    z_two_tailed,
)
from numcheck.dists import MAX_DF
from numcheck.mathx import NumericError

TOL = 1e-9

T_CASES = [
    (3.05, 44, 0.0038672801175369054),
    (2.31, 45, 0.0255309405453513),
    (1.85, 30, 0.07418590321936766),
    (1.75, 40, 0.08778676546147231),
    (0.0, 10, 1.0),
    (12.0, 5, 7.08949251716152e-05),
    (2.0, 1, 0.2951672353008665),
]

F_CASES = [
    (4.12, 2, 88, 0.019480071384321996),
    (9.30, 1, 44, 0.003871627280567289),
    (1.0, 3, 3, 0.5000000000000001),
    (25.0, 5, 10, 2.3777132644427366e-05),
    (0.5, 10, 100, 0.886350391153074),
]

CHI2_CASES = [
    (6.44, 1, 0.011157864637719724),
    (5.62, 1, 0.017756648872218405),
    (3.84, 1, 0.050043521248705085),
    (10.0, 4, 0.04042768199451279),
    (0.5, 2, 0.7788007830714049),
    (60.0, 3, 5.878230727906919e-13),
]

Z_CASES = [
    (2.05, 0.04036443081140879),
    (1.96, 0.04999579029644087),
    (0.0, 1.0),
    (4.5, 6.795346249460109e-06),
    (9.0, 2.2571768119076647e-19),
]

R_CASES = [
    (0.18, 44, 0.23129539182549236),
    (0.41, 38, 0.008602056794219593),
    (0.85, 10, 0.0004623019196319586),
    (0.0, 20, 1.0),
]


@pytest.mark.parametrize("t,df,expected", T_CASES)
def test_t_two_tailed(t, df, expected):
    assert t_two_tailed(t, df) == pytest.approx(expected, rel=TOL)
    assert t_two_tailed(-t, df) == pytest.approx(expected, rel=TOL)


@pytest.mark.parametrize("f,df1,df2,expected", F_CASES)
def test_f_upper_tail(f, df1, df2, expected):
    assert f_sf(f, df1, df2) == pytest.approx(expected, rel=TOL)


@pytest.mark.parametrize("x,df,expected", CHI2_CASES)
def test_chi2_upper_tail(x, df, expected):
    assert chi2_sf(x, df) == pytest.approx(expected, rel=TOL)


@pytest.mark.parametrize("z,expected", Z_CASES)
def test_z_two_tailed(z, expected):
    assert z_two_tailed(z) == pytest.approx(expected, rel=TOL)


@pytest.mark.parametrize("r,df,expected", R_CASES)
def test_r_two_tailed(r, df, expected):
    assert r_two_tailed(r, df) == pytest.approx(expected, rel=TOL)


def test_t_and_f_agree_when_df1_is_one():
    """F(1, k) = t(k)². 두 경로가 같은 값을 내야 한다."""
    for t, df in ((2.31, 45), (3.05, 44), (0.7, 12)):
        assert f_sf(t * t, 1, df) == pytest.approx(t_two_tailed(t, df), rel=1e-12)


def test_z_is_limit_of_t():
    """자유도가 매우 크면 t 는 z 에 수렴한다(수렴 속도는 O(1/df) 이므로 느슨하게)."""
    assert t_two_tailed(1.96, 50_000) == pytest.approx(z_two_tailed(1.96), rel=1e-3)
    assert abs(t_two_tailed(1.96, 200) - z_two_tailed(1.96)) > \
        abs(t_two_tailed(1.96, 20_000) - z_two_tailed(1.96))


def test_absurd_df_is_refused_rather_than_guessed():
    """보장할 수 없는 정확도의 값을 내놓느니 거절한다."""
    with pytest.raises(NumericError, match="지원 범위"):
        t_two_tailed(1.96, MAX_DF * 10)
    with pytest.raises(NumericError):
        chi2_sf(5.0, MAX_DF * 10)


def test_large_df_stays_within_the_repository_tolerance():
    """예전 _ITMAX = 500 은 χ²(df ≳ 13,500) 에서 조용히 잘려 48% 틀린 값을 냈다.

    아래 기준값은 scipy 1.17 산출값이다.
    """
    assert chi2_sf(13500, 13500) == pytest.approx(0.49838140840072365, rel=TOL)
    assert chi2_sf(50000, 50000) == pytest.approx(0.49915895563911135, rel=TOL)
    assert t_two_tailed(1.96, 50000) == pytest.approx(0.050001336111927667, rel=TOL)


def test_supported_df_range_is_generous_but_honest():
    """원고에 나올 수 있는 자유도(정규식 상한 999,999)보다 훨씬 크면 거절한다.

    상한을 낮춘 이유는 정확도다 — df ≈ 7×10^5 에서 상대오차가 6e-9 로 저장소
    기준(≤1e-9)을 넘는다. 보장할 수 없는 값을 내놓느니 그 claim 을 건너뛴다.
    """
    assert 10_000 <= MAX_DF <= 100_000
    t_two_tailed(1.96, MAX_DF)              # 상한 자체는 계산된다
    with pytest.raises(NumericError):
        t_two_tailed(1.96, MAX_DF + 1)


def test_one_sided_tails_are_half_of_two_sided():
    assert t_sf(2.31, 45) == pytest.approx(t_two_tailed(2.31, 45) / 2, rel=1e-13)
    assert z_sf(2.05) == pytest.approx(z_two_tailed(2.05) / 2, rel=1e-13)
    assert t_sf(-2.31, 45) == pytest.approx(1 - t_sf(2.31, 45), rel=1e-13)


def test_noninteger_df_supported():
    """Welch·Greenhouse–Geisser 의 소수 자유도."""
    value = t_two_tailed(2.31, 43.7)
    assert 0 < value < 1
    assert t_two_tailed(2.31, 43) > value > t_two_tailed(2.31, 45)


def test_r_at_unity_is_zero_p():
    assert r_two_tailed(1.0, 10) == 0.0
    assert r_two_tailed(-1.0, 10) == 0.0


def test_p_from_statistic_dispatch():
    assert p_from_statistic("t", 2.31, (45,)) == pytest.approx(t_two_tailed(2.31, 45))
    assert p_from_statistic("t", 2.31, (45,), "one") == pytest.approx(
        t_two_tailed(2.31, 45) / 2)
    assert p_from_statistic("F", 4.12, (2, 88)) == pytest.approx(f_sf(4.12, 2, 88))
    assert p_from_statistic("chi2", 6.44, (1,)) == pytest.approx(chi2_sf(6.44, 1))
    assert p_from_statistic("z", 2.05, ()) == pytest.approx(z_two_tailed(2.05))
    with pytest.raises(NumericError):
        p_from_statistic("wilcoxon", 1.0, (1,))


def test_invalid_degrees_of_freedom_raise():
    for fn, args in (
        (t_two_tailed, (2.0, 0)),
        (t_two_tailed, (2.0, -3)),
        (chi2_sf, (2.0, 0)),
        (f_sf, (2.0, 0, 5)),
        (f_sf, (2.0, 5, 0)),
        (r_two_tailed, (0.4, 0)),
    ):
        with pytest.raises(NumericError):
            fn(*args)


def test_non_finite_inputs_raise():
    with pytest.raises(NumericError):
        t_two_tailed(float("nan"), 10)
    with pytest.raises(NumericError):
        z_two_tailed(float("inf"))
    with pytest.raises(NumericError):
        chi2_sf(float("nan"), 3)
    with pytest.raises(NumericError):
        f_sf(math.inf, 2, 3)


def test_nonpositive_statistics_give_p_one():
    assert f_sf(-1.0, 2, 3) == 1.0
    assert chi2_sf(-1.0, 3) == 1.0
