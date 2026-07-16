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


def test_quantile_known():
    xs = [2, 4, 4, 4, 5, 5, 7, 9]  # numpy 기본(선형보간)과 동일
    assert stats.quantile(xs, 0.25) == pytest.approx(4.0)
    assert stats.quantile(xs, 0.75) == pytest.approx(5.5)
    assert stats.quantile(xs, 0.0) == 2.0
    assert stats.quantile(xs, 1.0) == 9.0
    assert stats.quantile([7], 0.5) == 7.0
    assert stats.quantile([], 0.5) is None
    with pytest.raises(ValueError):
        stats.quantile(xs, 1.5)


def test_skewness_kurtosis_known():
    xs = [2, 4, 4, 4, 5, 5, 7, 9]  # scipy bias=False 참조값
    assert stats.skewness(xs) == pytest.approx(0.8184875533567996, abs=1e-9)
    assert stats.kurtosis(xs) == pytest.approx(0.9406249999999998, abs=1e-9)


def test_skewness_kurtosis_guards():
    assert stats.skewness([1, 2]) is None  # n<3
    assert stats.kurtosis([1, 2, 3]) is None  # n<4
    # 분산 0
    assert stats.skewness([5, 5, 5, 5]) is None
    assert stats.kurtosis([5, 5, 5, 5]) is None


def test_symmetric_distribution_zero_skew():
    xs = [1, 2, 3, 4, 5]  # 평균 3 중심으로 대칭 -> 왜도 0
    assert stats.skewness(xs) == pytest.approx(0.0, abs=1e-12)


def test_cronbach_alpha_ci_feldt():
    # 문헌 예시: alpha=0.90, n=100, k=10, 95% -> [0.868, 0.927]
    ci = stats.cronbach_alpha_ci(0.90, 100, 10, 0.95)
    assert ci[0] == pytest.approx(0.868, abs=1e-3)
    assert ci[1] == pytest.approx(0.927, abs=1e-3)
    # 하한 < alpha < 상한
    assert ci[0] < 0.90 < ci[1]


def test_cronbach_alpha_ci_guards():
    assert stats.cronbach_alpha_ci(None, 50, 5) is None
    assert stats.cronbach_alpha_ci(0.8, 1, 5) is None  # 응답자<2
    assert stats.cronbach_alpha_ci(0.8, 50, 1) is None  # 문항<2
    # 상한은 1.0으로 클램프
    ci = stats.cronbach_alpha_ci(0.999, 200, 20, 0.95)
    assert ci[1] <= 1.0


def test_sem_from_alpha():
    # SEM = SD * sqrt(1-alpha)
    assert stats.sem_from_alpha(10.0, 0.75) == pytest.approx(5.0)
    assert stats.sem_from_alpha(10.0, 1.0) == pytest.approx(0.0)
    assert stats.sem_from_alpha(None, 0.8) is None
    assert stats.sem_from_alpha(5.0, None) is None
    # alpha>1 이면 정의불가
    assert stats.sem_from_alpha(5.0, 1.2) is None


def test_t_ci_mean():
    xs = [2, 4, 4, 4, 5, 5, 7, 9]  # mean 5.0
    lo, hi = stats.t_ci_mean(xs, 0.95)
    assert lo < 5.0 < hi
    # 손계산: mean=5, sd=2.138(ddof1), n=8, t.975(7)=2.3646 -> ±1.787
    assert lo == pytest.approx(3.213, abs=1e-2)
    assert hi == pytest.approx(6.787, abs=1e-2)
    assert stats.t_ci_mean([3], 0.95) is None  # n<2
    # 상수 벡터는 폭 0의 퇴화 CI (sd=0)
    assert stats.t_ci_mean([5, 5, 5], 0.95) == (5.0, 5.0)
