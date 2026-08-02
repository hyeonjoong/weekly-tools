"""Egger 비대칭 검정과 leave-one-out 민감도."""

import math

import pytest

from metapool.diagnostics import egger_test, leave_one_out
from metapool.effects import Study
from metapool.meta import random_effects

# z = y/se = [2, 3, 7], x = 1/se = [1, 2, 4]
# 손계산: mean x = 7/3, mean z = 4, Sxx = 42/9, Sxz = 8
#         기울기 = 8/(42/9) = 12/7, 절편 = 4 - (12/7)(7/3) = 0 (정확히 0)
#         잔차제곱합 = 2/7, df = 1 → SE(절편) = sqrt(3/7)
EGGER_DATA = [
    Study("A", 2.00, 1.0 ** 2),
    Study("B", 1.50, 0.5 ** 2),
    Study("C", 1.75, 0.25 ** 2),
]


def test_egger_intercept_and_slope_hand_computed():
    r = egger_test(EGGER_DATA)
    assert r is not None
    assert r.intercept == pytest.approx(0.0, abs=1e-13)
    assert r.slope == pytest.approx(12.0 / 7.0, rel=1e-13)
    assert r.se == pytest.approx(math.sqrt(3.0 / 7.0), rel=1e-12)
    assert r.df == 1
    assert r.t == pytest.approx(0.0, abs=1e-12)
    assert r.p == pytest.approx(1.0, abs=1e-12)


def test_egger_detects_small_study_effect():
    # 작은(부정확한) 연구일수록 효과가 크다 → 절편이 0에서 멀어진다
    skewed = [
        Study("big", 0.20, 0.01),
        Study("mid", 0.50, 0.04),
        Study("small", 1.20, 0.09),
        Study("tiny", 1.60, 0.16),
    ]
    r = egger_test(skewed)
    assert r is not None
    assert r.intercept > 1.0
    assert r.p < 0.10


def test_egger_is_symmetric_when_effects_are_identical():
    same = [Study("A", 0.4, 0.01), Study("B", 0.4, 0.04), Study("C", 0.4, 0.09)]
    r = egger_test(same)
    # y_i/se_i = 0.4 * (1/se_i) → 절편 0, 완전적합이라 t 통계량 정의 불가 → None
    assert r is None


def test_egger_needs_three_studies():
    assert egger_test(EGGER_DATA[:2]) is None
    assert egger_test([]) is None


def test_egger_returns_none_when_all_precisions_equal():
    equal = [Study("A", 0.1, 0.01), Study("B", 0.5, 0.01), Study("C", 0.9, 0.01)]
    assert egger_test(equal) is None


def test_leave_one_out_matches_direct_pooling():
    data = [
        Study("A", 0.1, 0.01),
        Study("B", 0.9, 0.01),
        Study("C", 0.5, 0.04),
        Study("D", 0.3, 0.01),
    ]
    results = leave_one_out(data)
    assert [r.omitted for r in results] == ["A", "B", "C", "D"]
    direct = random_effects([s for s in data if s.label != "B"])
    assert results[1].estimate == pytest.approx(direct.estimate, rel=1e-14)
    assert results[1].ci_low == pytest.approx(direct.ci_low, rel=1e-14)
    assert results[1].tau2 == pytest.approx(direct.tau2, rel=1e-14)


def test_leave_one_out_skipped_below_three_studies():
    assert leave_one_out([Study("A", 0.1, 0.01), Study("B", 0.2, 0.01)]) == []


def test_leave_one_out_flags_influential_study():
    # D 하나가 전체 방향을 끌고 가는 자료
    data = [
        Study("A", 0.02, 0.0025),
        Study("B", 0.03, 0.0025),
        Study("C", 0.01, 0.0025),
        Study("D", 0.90, 0.0025),
    ]
    results = leave_one_out(data)
    without_d = [r for r in results if r.omitted == "D"][0]
    assert without_d.estimate < 0.05  # D를 빼면 효과가 거의 사라진다
    assert max(r.estimate for r in results) > 0.2
