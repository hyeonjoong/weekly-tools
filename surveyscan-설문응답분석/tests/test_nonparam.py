"""순위 기반(비모수) 검정 테스트.

참조값은 scipy(mannwhitneyu/kruskal/wilcoxon, 점근·정확법)로 미리 계산해 하드코딩했다.
런타임은 표준 라이브러리만 쓰므로 여기서도 scipy를 import하지 않는다.
"""
import math

import pytest

from surveyscan import nonparam, special


# ── 순위 매기기 ──────────────────────────────────────────────────────────────
def test_rank_average_handles_ties():
    ranks, tie_sum = nonparam.rank_average([10, 20, 20, 30])
    assert ranks == [1.0, 2.5, 2.5, 4.0]
    assert tie_sum == 2 ** 3 - 2  # 동점 2개짜리 한 그룹


def test_rank_average_no_ties():
    ranks, tie_sum = nonparam.rank_average([5, 1, 3])
    assert ranks == [3.0, 1.0, 2.0]
    assert tie_sum == 0.0


# ── Mann-Whitney U ──────────────────────────────────────────────────────────
def test_mannwhitney_matches_scipy():
    xs = [2, 3, 3, 4, 5, 5, 5, 6]
    ys = [1, 2, 2, 3, 3, 4, 4, 5, 6]
    r = nonparam.mannwhitney_u(xs, ys)
    # scipy.stats.mannwhitneyu(alternative='two-sided', method='asymptotic')
    assert r["U"] == pytest.approx(47.0)
    assert r["p"] == pytest.approx(0.30356901390613644, rel=1e-12)
    # rank-biserial: 2U/(n1 n2) - 1
    assert r["rank_biserial"] == pytest.approx(2 * 47.0 / (8 * 9) - 1)


def test_mannwhitney_u1_plus_u2_is_n1n2():
    r = nonparam.mannwhitney_u([1, 2, 3], [4, 5, 6, 7])
    assert r["U"] + r["U2"] == 3 * 4
    assert r["rank_biserial"] == pytest.approx(-1.0)  # 완전 분리


def test_mannwhitney_all_identical_is_none():
    # 모든 값이 같으면 순위에 정보가 없어 z 가 정의되지 않는다 → 틀린 p 대신 None.
    assert nonparam.mannwhitney_u([3, 3, 3], [3, 3, 3]) is None


def test_mannwhitney_empty_group_is_none():
    assert nonparam.mannwhitney_u([], [1, 2]) is None


def test_mannwhitney_tiny_samples():
    r = nonparam.mannwhitney_u([1.0], [2.0])
    assert r is not None and 0.0 < r["p"] <= 1.0


# ── Kruskal-Wallis ──────────────────────────────────────────────────────────
def test_kruskal_matches_scipy():
    g1, g2, g3 = [3, 4, 4, 5, 6], [1, 2, 2, 3, 3], [5, 5, 6, 7, 8]
    r = nonparam.kruskal_wallis([g1, g2, g3])
    assert r["H"] == pytest.approx(10.654280510018223, rel=1e-12)
    assert r["p"] == pytest.approx(0.004857942643842003, rel=1e-10)
    assert r["df"] == 2.0
    assert r["epsilon_sq"] == pytest.approx(10.654280510018223 / 14)


def test_kruskal_all_tied_is_none():
    assert nonparam.kruskal_wallis([[2, 2], [2, 2], [2, 2]]) is None


def test_kruskal_needs_two_groups():
    assert nonparam.kruskal_wallis([[1, 2, 3]]) is None


# ── Wilcoxon 부호순위 ───────────────────────────────────────────────────────
def test_wilcoxon_exact_matches_scipy():
    d = [1.5, -2.0, 3.25, 4.0, -0.5, 5.0, 7.0]
    r = nonparam.wilcoxon_signed_rank(d)
    assert r["exact"] is True
    assert r["W"] == pytest.approx(4.0)  # scipy.stats.wilcoxon(method='exact')
    assert r["p"] == pytest.approx(0.109375, rel=1e-12)


def test_wilcoxon_approx_with_ties_matches_scipy():
    d = [1.0, -2.0, 3.0, 4.0, -1.0, 5.0, 2.0, 6.0]  # |d| 에 동점 있음 → 정규근사
    r = nonparam.wilcoxon_signed_rank(d)
    assert r["exact"] is False
    assert r["W"] == pytest.approx(5.0)
    assert r["p"] == pytest.approx(0.07931816374405429, rel=1e-10)


def test_wilcoxon_drops_zero_differences():
    r = nonparam.wilcoxon_signed_rank([0.0, 1.0, 2.0, 3.0])
    assert r["n"] == 3 and r["n_zero"] == 1
    # 0 이 있으면 정확검정 조건이 깨지므로 정규근사를 쓴다.
    assert r["exact"] is False


def test_wilcoxon_all_zero_is_none():
    assert nonparam.wilcoxon_signed_rank([0.0, 0.0]) is None
    assert nonparam.wilcoxon_signed_rank([]) is None


def test_wilcoxon_rank_biserial_sign_follows_direction():
    up = nonparam.wilcoxon_signed_rank([1.0, 2.0, 3.0, 4.0])
    down = nonparam.wilcoxon_signed_rank([-1.0, -2.0, -3.0, -4.0])
    assert up["rank_biserial"] == pytest.approx(1.0)
    assert down["rank_biserial"] == pytest.approx(-1.0)
    assert up["p"] == pytest.approx(down["p"])


def test_wilcoxon_exact_p_is_symmetric_distribution():
    # W⁺ 의 정확분포는 대칭이므로 전부 양수/전부 음수의 p 가 같아야 한다.
    assert nonparam._wilcoxon_exact_p(0, 5) == pytest.approx(2 / 32)
    assert nonparam._wilcoxon_exact_p(15, 5) == pytest.approx(2 / 32)


# ── χ² 상측 꼬리 ────────────────────────────────────────────────────────────
def test_chi2_sf_matches_scipy():
    assert special.chi2_sf(7.5, 3) == pytest.approx(0.057558451972636406, rel=1e-12)
    # 큰 통계량에서도 0.0 으로 뭉개지지 않아야 한다(1-CDF 였다면 0.0).
    assert special.chi2_sf(100, 2) == pytest.approx(1.9287498479639183e-22, rel=1e-10)


def test_chi2_sf_edges():
    assert special.chi2_sf(0.0, 4) == 1.0
    assert special.chi2_sf(-1.0, 4) == 1.0
    with pytest.raises(ValueError):
        special.chi2_sf(1.0, 0)


def test_norm_sf_does_not_underflow_to_zero():
    assert special.norm_sf(3.5) == pytest.approx(0.00023262907903552502, rel=1e-12)
    assert 0.0 < special.norm_sf(30.0) < 1e-190
    assert special.norm_sf(0.0) == pytest.approx(0.5)


def test_rank_effect_label_bands():
    assert nonparam.rank_effect_label(0.6) == "큼"
    assert nonparam.rank_effect_label(-0.35) == "중간"
    assert nonparam.rank_effect_label(0.15) == "작음"
    assert nonparam.rank_effect_label(0.01) == "매우 작음"
    assert nonparam.rank_effect_label(None) == "-"


def test_pvalues_never_exceed_one():
    # 연속성 보정이 과도하게 적용되면 p>1 이 나올 수 있다(작은 표본에서 흔한 버그).
    for n in range(2, 8):
        r = nonparam.mannwhitney_u([1.0] * n + [2.0], [1.0] * n + [2.0])
        if r:
            assert 0.0 <= r["p"] <= 1.0
        w = nonparam.wilcoxon_signed_rank([1.0] * n + [-1.0] * n)
        if w:
            assert 0.0 <= w["p"] <= 1.0
            assert math.isfinite(w["p"])
