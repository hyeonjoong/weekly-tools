"""손계산으로 검증한 통계 함수 테스트."""
import math

import pytest

from surveyscan import stats


def test_mean_variance_stdev():
    xs = [1, 2, 3, 4]  # mean 2.5, 표본분산 = 5/3
    assert stats.mean(xs) == 2.5
    assert stats.variance(xs) == pytest.approx(5 / 3)
    assert stats.stdev(xs) == pytest.approx(math.sqrt(5 / 3))


def test_variance_needs_two():
    assert stats.variance([3]) is None
    assert stats.variance([]) is None


def test_median_odd_even():
    assert stats.median([3, 1, 2]) == 2
    assert stats.median([4, 1, 3, 2]) == 2.5
    assert stats.median([]) is None


def test_pearson_perfect_and_zero_var():
    assert stats.pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert stats.pearson([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    # 한쪽 분산 0 -> None
    assert stats.pearson([1, 1, 1], [1, 2, 3]) is None


def test_cronbach_alpha_perfect():
    # 완전 평행한 문항 -> alpha = 1.0
    # 응답자 4명, 문항 3개
    cols = [
        [1, 2, 3, 4],
        [2, 3, 4, 5],
        [3, 4, 5, 6],
    ]
    assert stats.cronbach_alpha(cols) == pytest.approx(1.0)


def test_cronbach_alpha_hand_computed():
    # 손계산: 응답자 4명, 문항 3개
    # 문항분산합 = 4.83333..., 총점분산 = 9.66667..., 비율 = 0.5
    # alpha = (3/2)*(1-0.5) = 0.75
    cols = [
        [4, 3, 5, 2],  # 문항A
        [5, 2, 5, 3],  # 문항B
        [3, 4, 4, 2],  # 문항C
    ]
    assert stats.cronbach_alpha(cols) == pytest.approx(0.75)


def test_cronbach_alpha_guards():
    assert stats.cronbach_alpha([[1, 2, 3]]) is None  # 문항 1개
    assert stats.cronbach_alpha([[1], [2]]) is None  # 응답자 1명
    # 총점 분산 0 (모든 응답자 동일 총점)
    assert stats.cronbach_alpha([[1, 2], [2, 1]]) is None
