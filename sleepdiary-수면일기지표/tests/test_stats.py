"""통계 함수 — scipy/numpy 참조값과 대조 (참조값은 하드코딩, 실행에 scipy 불필요).

참조값 출처 (scipy 1.17.1 / numpy 2.4.3):
    stats.t.sf, stats.t.ppf, stats.ttest_rel,
    stats.wilcoxon(method='exact') / (method='approx', correction=False),
    numpy.quantile(..., method='linear')
"""

import math

import pytest

from sleepdiary.stats import (
    circular_diff,
    circular_mean,
    circular_sd,
    mean,
    mean_ci,
    median,
    normal_sf,
    paired_ttest,
    quantile,
    sd,
    student_t_sf,
    summarize,
    t_ppf,
    wilcoxon_signed_rank,
)

# 두 검정 모두에 쓰는 차이값 벡터: 동점 없음, 0 없음, n=11
DIFFS_CLEAN = [12.0, -3.0, 7.5, 22.0, -1.5, 9.0, 4.25, 18.0, -6.0, 3.5, 11.0]
# 동점(|2|이 두 번, |5|가 두 번, |3|이 두 번)과 0을 포함
DIFFS_TIED = [1.0, -2.0, 2.0, 3.0, -3.0, 4.0, 5.0, -5.0, 6.0, 0.0, -2.0]


# ---------------------------------------------------------------- 기술통계

def test_mean_sd_median_match_hand_computation():
    values = [3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0]
    assert mean(values) == pytest.approx(31.0 / 8)
    assert median(values) == pytest.approx(3.5)
    assert sd(values) == pytest.approx(2.748376143938713, rel=1e-12)


def test_sd_needs_two_values():
    assert sd([]) is None
    assert sd([5.0]) is None
    assert sd([2.0, 2.0]) == 0.0


def test_quantile_matches_numpy_linear_method():
    values = [3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0]
    assert quantile(values, 0.25) == pytest.approx(1.75)
    assert quantile(values, 0.75) == pytest.approx(5.25)
    assert quantile(values, 0.0) == 1.0
    assert quantile(values, 1.0) == 9.0


def test_quantile_rejects_bad_input():
    with pytest.raises(ValueError):
        quantile([], 0.5)
    with pytest.raises(ValueError):
        quantile([1.0], 1.5)


def test_summarize_of_empty_is_all_none_not_a_crash():
    out = summarize([])
    assert out["n"] == 0 and out["mean"] is None and out["q3"] is None


# ---------------------------------------------------------------- 원형통계

def test_circular_mean_wraps_around_midnight():
    """23:50과 00:10의 평균은 12:00이 아니라 00:00이어야 한다."""
    assert circular_mean([23 * 60 + 50, 10]) == pytest.approx(0.0, abs=1e-6)
    # 산술평균이라면 720분(정오)이 나온다 — 그 함정을 피했는지 명시적으로 확인
    assert abs(circular_mean([23 * 60 + 50, 10]) - 720.0) > 700


def test_circular_mean_matches_arithmetic_when_no_wrap():
    values = [3 * 60, 4 * 60, 5 * 60]
    assert circular_mean(values) == pytest.approx(4 * 60, abs=1e-6)


def test_circular_mean_undefined_for_antipodal_values():
    with pytest.raises(ValueError):
        circular_mean([6 * 60, 18 * 60])


def test_circular_sd_grows_with_spread_and_is_zero_when_identical():
    assert circular_sd([120.0, 120.0, 120.0]) == pytest.approx(0.0, abs=1e-6)
    tight = circular_sd([180.0, 190.0, 200.0, 185.0])
    loose = circular_sd([120.0, 300.0, 60.0, 400.0])
    assert tight < loose
    assert circular_sd([100.0]) is None


def test_circular_sd_is_a_population_sd_and_matches_linear_sd_for_tight_clusters():
    """뭉쳐 있는 값들에서는 원형 SD가 모집단 SD(ddof=0)와 거의 같아야 한다.

    표본 SD(ddof=1)와는 sqrt(n/(n-1))배 차이가 나는데, 이는 sqrt(-2 ln R)
    정의가 n으로 나누는 형태이기 때문이다. 이 차이는 의도된 것이고
    문서에도 적혀 있다.
    """
    values = [180.0, 190.0, 200.0, 185.0, 195.0]
    population_sd = 7.0710678118654755          # numpy.std(values, ddof=0)
    assert circular_sd(values) == pytest.approx(population_sd, rel=0.01)
    assert circular_sd(values) < sd(values)     # ddof=1 보다 항상 조금 작다


def test_circular_diff_takes_the_short_way_around():
    # 23:30 → 00:10 은 +40분이지 -1400분이 아니다
    assert circular_diff(10.0, 23 * 60 + 30) == pytest.approx(40.0)
    assert circular_diff(23 * 60 + 30, 10.0) == pytest.approx(-40.0)
    assert circular_diff(120.0, 60.0) == pytest.approx(60.0)


# ---------------------------------------------------------------- 분포함수

@pytest.mark.parametrize("t,df,expected", [
    (2.3, 11, 0.021015963334351892),
    (0.5, 3.0, 0.3257239824240755),
    (-1.2, 7, 0.8654140315863967),
    (0.0, 5, 0.5),
])
def test_student_t_sf_matches_scipy(t, df, expected):
    assert student_t_sf(t, df) == pytest.approx(expected, rel=1e-10)


def test_t_ppf_matches_scipy():
    assert t_ppf(0.975, 11) == pytest.approx(2.2009851600916384, rel=1e-8)
    assert t_ppf(0.995, 4) == pytest.approx(4.604094871349992, rel=1e-8)


def test_t_ppf_and_sf_are_inverses():
    for df in (2, 7, 30, 120):
        for p in (0.6, 0.9, 0.975, 0.999):
            assert 1.0 - student_t_sf(t_ppf(p, df), df) == pytest.approx(p, abs=1e-9)


def test_normal_sf_matches_known_values():
    assert normal_sf(0.0) == pytest.approx(0.5)
    assert normal_sf(1.959963984540054) == pytest.approx(0.025, rel=1e-9)
    assert normal_sf(-1.0) == pytest.approx(0.8413447460685429, rel=1e-12)


def test_t_converges_to_normal_at_large_df():
    assert student_t_sf(1.96, 100000) == pytest.approx(normal_sf(1.96), abs=1e-5)


# ------------------------------------------------------------ 대응표본 t검정

def test_paired_ttest_matches_scipy_ttest_rel():
    result = paired_ttest(DIFFS_CLEAN)
    assert result.n == 11
    assert result.df == 10
    assert result.t == pytest.approx(2.6679921205844117, rel=1e-10)
    assert result.p == pytest.approx(0.023573657222864752, rel=1e-9)


def test_paired_ttest_ci_contains_mean_and_matches_manual_formula():
    result = paired_ttest(DIFFS_CLEAN)
    crit = t_ppf(0.975, 10)
    assert result.ci_low == pytest.approx(result.mean_diff - crit * result.se)
    assert result.ci_high == pytest.approx(result.mean_diff + crit * result.se)
    assert result.ci_low < result.mean_diff < result.ci_high


def test_paired_ttest_dz_is_mean_over_sd_of_differences():
    result = paired_ttest(DIFFS_CLEAN)
    assert result.dz == pytest.approx(result.mean_diff / result.sd_diff)


def test_paired_ttest_handles_zero_variance_without_dividing_by_zero():
    """분산이 0이면 폭 0짜리 CI를 지어내지 않고 없다고 말해야 한다."""
    result = paired_ttest([5.0, 5.0, 5.0, 5.0])
    assert result.t is None and result.p is None and result.dz is None
    assert result.mean_diff == 5.0
    assert result.ci_low is None and result.ci_high is None


def test_paired_ttest_needs_two_pairs():
    with pytest.raises(ValueError):
        paired_ttest([1.0])


def test_wider_confidence_level_gives_wider_interval():
    narrow = paired_ttest(DIFFS_CLEAN, conf=0.90)
    wide = paired_ttest(DIFFS_CLEAN, conf=0.99)
    assert wide.ci_high - wide.ci_low > narrow.ci_high - narrow.ci_low


# ---------------------------------------------------------------- Wilcoxon

def test_wilcoxon_exact_matches_scipy():
    result = wilcoxon_signed_rank(DIFFS_CLEAN)
    assert result.method == "exact"
    assert result.n_used == 11 and result.n_zero == 0
    assert result.statistic == pytest.approx(8.0)
    assert result.p == pytest.approx(0.0244140625, rel=1e-12)


def test_wilcoxon_normal_approximation_with_ties_matches_scipy():
    result = wilcoxon_signed_rank(DIFFS_TIED)
    assert result.method == "normal"
    assert result.n_zero == 1          # 0인 차이는 제외 (scipy zero_method='wilcox')
    assert result.n_used == 10
    assert result.statistic == pytest.approx(20.0)
    assert result.z == pytest.approx(-0.7674667651449762, rel=1e-9)
    assert result.p == pytest.approx(0.44280404593651523, rel=1e-9)


def test_wilcoxon_all_zero_differences_is_reported_not_crashed():
    result = wilcoxon_signed_rank([0.0, 0.0, 0.0])
    assert result.n_used == 0 and result.p is None and result.method == "none"


def test_wilcoxon_is_symmetric_under_sign_flip():
    a = wilcoxon_signed_rank(DIFFS_CLEAN)
    b = wilcoxon_signed_rank([-d for d in DIFFS_CLEAN])
    assert a.statistic == pytest.approx(b.statistic)
    assert a.p == pytest.approx(b.p)


def test_wilcoxon_exact_p_is_a_valid_probability():
    for n in range(1, 12):
        diffs = [float(i + 1) for i in range(n)]
        result = wilcoxon_signed_rank(diffs)
        assert 0.0 < result.p <= 1.0


def test_wilcoxon_switches_to_normal_above_25_untied_values():
    diffs = [float(i + 1) for i in range(30)]
    assert wilcoxon_signed_rank(diffs).method == "normal"


# ---------------------------------------------------------------- 평균 CI

def test_mean_ci_matches_manual_t_interval():
    values = [7.0, 8.5, 6.25, 9.0, 7.75]
    low, high = mean_ci(values)
    crit = t_ppf(0.975, 4)
    se = sd(values) / math.sqrt(5)
    assert low == pytest.approx(mean(values) - crit * se)
    assert high == pytest.approx(mean(values) + crit * se)


def test_mean_ci_degenerate_cases():
    assert mean_ci([]) == (None, None)
    assert mean_ci([3.0]) == (None, None)
    assert mean_ci([3.0, 3.0]) == (None, None)      # 분산 0 → 구간을 지어내지 않는다


# ------------------------------------------- 심사 라운드 1에서 나온 회귀 테스트

def test_t_ppf_expands_its_bracket_instead_of_returning_the_boundary():
    """고정 ±1e4 구간을 쓰면 극단 신뢰수준에서 조용히 10000을 돌려주었다."""
    assert t_ppf(0.99999, 1) == pytest.approx(31830.988618379067, rel=1e-6)
    assert t_ppf(0.9999999999, 1) == pytest.approx(3183098861.837907, rel=1e-4)
    assert t_ppf(0.999999, 2) == pytest.approx(707.1063505497382, rel=1e-6)


def test_extreme_confidence_level_widens_the_interval_without_clipping():
    low, high = mean_ci([10.0, 20.0], conf=0.99999)
    assert high - low > 1e4      # 예전에는 ±1e4 경계에 붙어 잘렸다


def test_circular_mean_never_returns_exactly_one_full_day():
    """(-1e-14) % 1440 이 1440.0 으로 올림되어 계약을 깨뜨렸다."""
    for values in ([1439.0, 1.0], [1439.5, 0.5], [1439.9, 0.1]):
        result = circular_mean(values)
        assert 0.0 <= result < 1440.0


def test_circular_sd_of_identical_values_is_positive_zero_not_negative_zero():
    result = circular_sd([0.0, 0.0, 0.0])
    assert result == 0.0
    assert math.copysign(1.0, result) > 0


def test_circular_sd_returns_none_instead_of_an_impossible_28_hour_spread():
    """시계 전체에 흩어지면 sqrt(-2 ln R)은 하루보다 큰 수를 낸다 — 지어내지 않는다."""
    scattered = [0.0, 360.0, 720.0, 1080.0]      # 정확히 균등 → R ≈ 0
    assert circular_sd(scattered) is None
    # 균등분포의 이론적 상한 근처(1440/sqrt(12) ≈ 416분)까지는 값을 돌려준다
    wide = circular_sd([0.0, 300.0, 600.0])
    assert wide is not None and wide < 1440.0


def test_wilcoxon_reports_an_effect_size_in_the_exact_branch_too():
    """동점이 우연히 생겼는지에 따라 효과크기가 나왔다 안 나왔다 하면 안 된다."""
    exact = wilcoxon_signed_rank(DIFFS_CLEAN)
    assert exact.method == "exact"
    assert exact.z is not None and exact.r is not None
    assert exact.r == pytest.approx(abs(exact.z) / math.sqrt(exact.n_used))

    # 값 하나만 살짝 옮겨 동점을 만들면 분기가 바뀌지만 r 크기는 비슷해야 한다
    tied = list(DIFFS_CLEAN)
    tied[1] = -tied[0]
    tied_result = wilcoxon_signed_rank(tied)
    assert tied_result.method == "normal"
    assert tied_result.r == pytest.approx(exact.r, abs=0.25)


def test_wilcoxon_exact_p_still_matches_scipy_after_the_effect_size_change():
    result = wilcoxon_signed_rank(DIFFS_CLEAN)
    assert result.p == pytest.approx(0.0244140625, rel=1e-12)
