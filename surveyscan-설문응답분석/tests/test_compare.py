"""집단 비교(Welch t / Welch ANOVA · Hedges g · Holm) 테스트.

참조값은 scipy(scipy.stats.ttest_ind(equal_var=False))와 statsmodels
(stats.oneway.anova_oneway(use_var='unequal'), stats.multitest.multipletests(method='holm'))
로 미리 계산해 하드코딩했다.
런타임은 표준 라이브러리만 쓰므로 여기서도 scipy를 import하지 않는다.
"""
import math

import pytest

from surveyscan import compare

XS = [12.0, 14, 15, 9, 20, 22, 13, 11, 17, 18, 7, 25]
YS = [10.0, 11, 13, 8, 9, 14, 12, 15, 7, 6]


def test_welch_ttest_matches_scipy():
    r = compare.welch_ttest(XS, YS)
    assert r["t"] == pytest.approx(2.6098507150250914, rel=1e-12)
    assert r["df"] == pytest.approx(17.83638229488102, rel=1e-12)
    assert r["p"] == pytest.approx(0.01781744047092385, rel=1e-9)
    lo, hi = r["diff_ci"]
    assert lo == pytest.approx(0.9237483809930902, rel=1e-10)
    assert hi == pytest.approx(8.57625161900691, rel=1e-10)
    assert r["mean_diff"] == pytest.approx(sum(XS) / len(XS) - sum(YS) / len(YS))


def test_welch_equals_student_when_equal_n_and_equal_variance():
    # 표본크기와 표본분산이 같으면 Welch = Student, df = 2n-2 (해석적 성질).
    xs = [1.0, 2, 3, 4, 5]
    ys = [11.0, 12, 13, 14, 15]  # 같은 분산, 평균만 +10
    r = compare.welch_ttest(xs, ys)
    assert r["df"] == pytest.approx(8.0, abs=1e-9)
    sd = math.sqrt(2.5)
    expected_t = (3.0 - 13.0) / (sd * math.sqrt(2.0 / 5.0))
    assert r["t"] == pytest.approx(expected_t, rel=1e-12)


def test_welch_ttest_none_cases():
    assert compare.welch_ttest([1.0], [1.0, 2.0]) is None      # N<2
    assert compare.welch_ttest([2.0, 2.0], [2.0, 2.0]) is None  # 양쪽 분산 0
    # 한쪽만 분산 0이면 계산 가능해야 한다(SE>0).
    r = compare.welch_ttest([2.0, 2.0, 2.0], [1.0, 2.0, 3.0])
    assert r is not None and math.isfinite(r["t"]) and 0.0 <= r["p"] <= 1.0


def test_welch_ttest_sign_and_ci_symmetry():
    a = compare.welch_ttest(XS, YS)
    b = compare.welch_ttest(YS, XS)
    assert a["t"] == pytest.approx(-b["t"], rel=1e-12)
    assert a["p"] == pytest.approx(b["p"], rel=1e-12)
    assert a["diff_ci"][0] == pytest.approx(-b["diff_ci"][1], rel=1e-12)


def test_hedges_g_matches_reference_and_shrinks_d():
    g = compare.hedges_g(XS, YS)
    assert g["g"] == pytest.approx(1.0233984630636122, rel=1e-12)
    lo, hi = g["ci"]
    assert lo < g["g"] < hi
    # 편향보정 J<1 이므로 |g| < |d|
    n1, n2 = len(XS), len(YS)
    j = 1.0 - 3.0 / (4.0 * (n1 + n2) - 9.0)
    assert abs(g["g"]) < abs(g["g"] / j)


def test_hedges_g_none_when_no_variance():
    assert compare.hedges_g([3.0, 3.0], [3.0, 3.0]) is None
    assert compare.hedges_g([1.0], [1.0, 2.0]) is None


def test_welch_anova_balanced_equal_var_matches_statsmodels():
    """균형·등분산이어도 Welch F 는 고전 ANOVA F 와 다르다(보정계수 B 로 나눈 값).

    참조: statsmodels.stats.oneway.anova_oneway(use_var='unequal', welch_correction=True)
    → F=8.0, df=(2, 8.0), p=0.012345679012345668
    (같은 자료의 고전 ANOVA F 는 8.6667 — 여기서 B=13/12 만큼 차이가 난다.)
    """
    a = [1.0, 2, 3, 4, 5]
    b = [2.0, 3, 4, 5, 6]
    c = [5.0, 6, 7, 8, 9]
    r = compare.welch_anova([a, b, c])
    assert r["F"] == pytest.approx(8.0, rel=1e-10)
    assert r["df1"] == 2.0
    assert r["df2"] == pytest.approx(8.0, rel=1e-9)
    assert r["p"] == pytest.approx(0.012345679012345668, rel=1e-9)
    # 고전 ANOVA F(=8.6667, scipy.f_oneway) 와의 관계: F_welch = F_classic / B
    b_corr = 13.0 / 12.0
    assert r["F"] == pytest.approx(8.666666666666666 / b_corr, rel=1e-10)


def test_welch_anova_unequal_variance_reference():
    # 참조: statsmodels anova_oneway(use_var='unequal') → F=12.919918204453598,
    # df=(2, 17.946819929070795), p=0.0003342806732696843
    zs = [5.0, 6, 7, 8, 9, 10, 4, 3]
    r = compare.welch_anova([XS, YS, zs])
    assert r["F"] == pytest.approx(12.9199182044536, rel=1e-10)
    assert r["df2"] == pytest.approx(17.946819929070795, rel=1e-10)
    assert r["p"] == pytest.approx(0.00033428067326968236, rel=1e-8)


def test_welch_anova_none_cases():
    assert compare.welch_anova([[1.0, 2], [3.0, 4]]) is None          # 집단 2개
    assert compare.welch_anova([[1.0, 2], [3.0, 4], [5.0]]) is None    # N<2 집단
    assert compare.welch_anova([[1.0, 2], [3.0, 4], [5.0, 5.0]]) is None  # 분산 0


def test_holm_matches_statsmodels_and_is_monotone():
    assert compare.holm_adjust([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    # None 은 보정 대상에서 빠지고 None 으로 남는다(개수 m 에도 포함되지 않음).
    out = compare.holm_adjust([0.01, None, 0.04])
    assert out[1] is None
    assert out[0] == pytest.approx(0.02)
    # 단일 검정이면 보정 없음
    assert compare.holm_adjust([0.023]) == pytest.approx([0.023])
    # 전부 None
    assert compare.holm_adjust([None, None]) == [None, None]
    # 1.0 을 넘지 않는다
    assert all(p <= 1.0 for p in compare.holm_adjust([0.6, 0.7, 0.8]))


def test_group_scores_skips_blank_labels_and_missing_scores():
    scores = [1.0, None, 3.0, 4.0]
    groups = ["A", "A", " ", "B"]
    buckets, n_no_label = compare.group_scores(scores, groups)
    assert buckets == {"A": [1.0], "B": [4.0]}
    assert n_no_label == 1  # 점수는 있는데 집단 라벨이 없는 응답자


def _subscale(name, scores):
    return {"name": name, "scores": scores, "score_method": "sum"}


def test_compare_subscales_two_groups_uses_welch_t():
    scores = XS + YS
    groups = ["치료"] * len(XS) + ["대조"] * len(YS)
    out = compare.compare_subscales([_subscale("S", scores)], groups, "군")
    assert out["usable"] is True
    row = out["subscales"][0]
    assert row["test"]["test"] == "welch_t"
    assert row["effect"] is not None
    assert row["p"] == pytest.approx(0.01781744047092385, rel=1e-9)
    # 검정이 1개면 Holm 보정은 원래 p 그대로
    assert row["p_holm"] == pytest.approx(row["p"])
    labels = [g["label"] for g in row["groups"]]
    assert labels == sorted(labels)  # 결정적 정렬
    # 평균차·g 의 부호 기준(어느 집단에서 어느 집단을 뺐는지)을 명시적으로 남긴다.
    assert row["diff_labels"] == ["대조", "치료"]
    assert row["test"]["mean_diff"] == pytest.approx(
        sum(YS) / len(YS) - sum(XS) / len(XS)
    )


def test_compare_subscales_three_groups_uses_anova():
    zs = [5.0, 6, 7, 8, 9, 10, 4, 3]
    scores = XS + YS + zs
    groups = ["A"] * len(XS) + ["B"] * len(YS) + ["C"] * len(zs)
    out = compare.compare_subscales([_subscale("S", scores)], groups, "군")
    row = out["subscales"][0]
    assert row["test"]["test"] == "welch_anova"
    assert row["effect"] is None  # 3집단 이상은 쌍별 효과크기를 내지 않는다
    assert row["n_groups_tested"] == 3


def test_compare_subscales_single_group_unusable():
    out = compare.compare_subscales(
        [_subscale("S", [1.0, 2.0, 3.0])], ["A", "A", "A"], "군"
    )
    assert out["usable"] is False
    assert "2개 이상" in out["reason"]
    assert out["subscales"] == []


def test_compare_subscales_too_many_groups_unusable():
    n = compare.MAX_GROUPS + 1
    scores = [float(i) for i in range(n)]
    groups = [f"G{i}" for i in range(n)]
    out = compare.compare_subscales([_subscale("S", scores)], groups, "ID")
    assert out["usable"] is False
    assert "너무 많습니다" in out["reason"]


def test_compare_subscales_holm_across_subscales():
    scores_a = XS + YS
    scores_b = list(reversed(XS)) + YS
    groups = ["치료"] * len(XS) + ["대조"] * len(YS)
    out = compare.compare_subscales(
        [_subscale("A", scores_a), _subscale("B", scores_b)], groups, "군"
    )
    ps = [r["p"] for r in out["subscales"]]
    adj = [r["p_holm"] for r in out["subscales"]]
    assert out["n_tests"] == 2
    for p, a in zip(ps, adj):
        assert a >= p - 1e-12  # 보정 p 는 원 p 이상
        assert a <= 1.0


def test_compare_subscales_group_with_one_score_is_described_but_not_tested():
    scores = XS + YS + [3.0]
    groups = ["A"] * len(XS) + ["B"] * len(YS) + ["C"]
    out = compare.compare_subscales([_subscale("S", scores)], groups, "군")
    row = out["subscales"][0]
    c = [g for g in row["groups"] if g["label"] == "C"][0]
    assert c["n"] == 1 and c["sd"] is None
    # N<2 집단은 검정에서 빠지므로 남은 2집단 → Welch t
    assert row["n_groups_tested"] == 2
    assert row["test"]["test"] == "welch_t"
    # 빠진 집단을 조용히 숨기지 않는다(‘전체 비교’로 오해 방지)
    assert row["excluded_groups"] == ["C"]


def test_compare_subscales_all_constant_scores_reports_reason():
    scores = [5.0] * 6
    groups = ["A", "A", "A", "B", "B", "B"]
    out = compare.compare_subscales([_subscale("S", scores)], groups, "군")
    row = out["subscales"][0]
    assert row["test"] is None
    assert row["p"] is None
    assert "분산이 0" in row["reason"]


def test_compare_subscales_counts_unlabeled_respondents():
    scores = [1.0, 2.0, 3.0, 4.0, None]
    groups = ["A", "A", "B", "", ""]
    out = compare.compare_subscales([_subscale("S", scores)], groups, "군")
    assert out["n_no_label"] == 2  # 점수 유무와 무관하게 라벨 없는 응답자 수
