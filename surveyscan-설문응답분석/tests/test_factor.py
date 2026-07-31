"""factor.py (단일요인 PAF · McDonald ω) 산술 검증.

핵심: ω 값을 factor.py 자신이 낸 결과로 되짚어 확인하면 아무것도 검증하지 못한다.
여기서는 **독립적인 오라클**을 쓴다.

- 문항 3개일 때 단일요인 모형은 just-identified 라서 표준화 부하량의 닫힌 해가 있다:
      λ_i = sqrt(r_ij · r_ik / r_jk)
  이 값으로 상관행렬이 정확히 재현되므로, PAF 결과와 1:1로 비교할 수 있다.
- ω_total = (Σλ)² / ((Σλ)² + Σψ),  λ_i = 표준화λ_i · SD_i,  ψ_i = Var_i − λ_i²
  를 테스트 안에서 닫힌 해 λ 로부터 직접 계산해 omega_total() 과 맞춘다.
  (부하량의 제곱 누락·ψ 이중계산·공분산 단위 미변환 같은 실수를 모두 잡는다.)
"""
import math

import pytest

from surveyscan import factor, stats


def _closed_form_loadings3(R):
    """k=3 단일요인 모형의 닫힌 해 표준화 부하량."""
    r01, r02, r12 = R[0][1], R[0][2], R[1][2]
    return [
        math.sqrt(r01 * r02 / r12),
        math.sqrt(r01 * r12 / r02),
        math.sqrt(r02 * r12 / r01),
    ]


def test_paf_matches_closed_form_for_three_items():
    R = [
        [1.0, 0.0796, 0.1699],
        [0.0796, 1.0, 0.2814],
        [0.1699, 0.2814, 1.0],
    ]
    fit = factor.single_factor_loadings(R)
    assert fit is not None, "k=3 just-identified 모형은 수렴해야 한다"
    for got, want in zip(fit["loadings"], _closed_form_loadings3(R)):
        assert got == pytest.approx(want, abs=1e-7)


def test_paf_reproduces_offdiagonal_correlations():
    """단일요인 모형이 맞으면 λ_i·λ_j 가 비대각 상관을 재현해야 한다."""
    R = [
        [1.0, 0.42, 0.36],
        [0.42, 1.0, 0.48],
        [0.36, 0.48, 1.0],
    ]
    fit = factor.single_factor_loadings(R)
    assert fit is not None
    lam = fit["loadings"]
    for i in range(3):
        for j in range(i + 1, 3):
            assert lam[i] * lam[j] == pytest.approx(R[i][j], abs=1e-7)


def test_paf_converges_for_slow_low_correlation_case():
    """상한이 너무 낮으면 수렴 가능한 행렬인데도 None 이 나온다(과거 결함)."""
    R = [
        [1.0, 0.0796, 0.1699],
        [0.0796, 1.0, 0.2814],
        [0.1699, 0.2814, 1.0],
    ]
    assert factor.single_factor_loadings(R) is not None


def test_dominant_eig_matches_hand_computed():
    """대칭행렬 [[2,1],[1,2]] 의 최대 고유값은 3, 고유벡터는 (1,1)/√2."""
    lam, v = factor._dominant_eig([[2.0, 1.0], [1.0, 2.0]])
    assert lam == pytest.approx(3.0, abs=1e-9)
    assert abs(v[0]) == pytest.approx(abs(v[1]), abs=1e-9)
    assert abs(v[0]) == pytest.approx(1 / math.sqrt(2), abs=1e-9)


def test_dominant_eig_finds_algebraically_largest_not_largest_magnitude():
    """축소상관행렬은 음의 고유값을 가질 수 있다 — |·|가 큰 음수로 수렴하면 안 된다.

    diag(-5, 1) 의 고유값은 -5 와 1. 대수적으로 가장 큰 값은 1.
    """
    lam, _ = factor._dominant_eig([[-5.0, 0.0], [0.0, 1.0]])
    assert lam == pytest.approx(1.0, abs=1e-9)


def _omega_from_closed_form(columns):
    """테스트 전용 독립 계산: 닫힌 해 λ 로부터 ω_total 을 직접 구한다."""
    k = len(columns)
    assert k == 3
    R = factor.correlation_matrix(columns)
    std_lam = _closed_form_loadings3(R)
    sds = [stats.stdev(c) for c in columns]
    lam_cov = [l * sd for l, sd in zip(std_lam, sds)]
    sum_lam = sum(lam_cov)
    psi_sum = sum(sd * sd - lc * lc for lc, sd in zip(lam_cov, sds))
    return (sum_lam ** 2) / (sum_lam ** 2 + psi_sum)


def test_omega_total_matches_independent_computation():
    """ω 공식을 테스트 안에서 독립적으로 계산해 대조한다.

    (Σλ)² 의 제곱 누락, ψ 이중계산, 표준화→공분산 단위 미변환을 모두 잡는다.
    """
    # Heywood 가 아닌(=닫힌 해가 그대로 유효한) 3문항 자료
    columns = [
        [2.0, 5.0, 1.0, 3.0, 2.0, 4.0, 4.0, 2.0, 3.0, 4.0, 4.0, 2.0],
        [3.0, 1.0, 1.0, 1.0, 1.0, 4.0, 3.0, 1.0, 2.0, 5.0, 4.0, 4.0],
        [4.0, 2.0, 1.0, 3.0, 1.0, 3.0, 4.0, 2.0, 2.0, 3.0, 2.0, 3.0],
    ]
    res = factor.omega_total(columns)
    assert res is not None
    assert res["heywood"] is False, "이 픽스처는 정상해여야 닫힌 해와 비교할 수 있다"
    assert res["omega"] == pytest.approx(_omega_from_closed_form(columns), abs=1e-7)
    assert res["n_items"] == 3 and res["n_complete"] == 12
    # 위 닫힌 해 오라클로 손계산한 값(0.69569531)을 고정해 변화를 감지한다.
    assert res["omega"] == pytest.approx(0.69569531, abs=1e-7)


def test_omega_is_a_proportion_and_near_alpha_for_parallel_items():
    """문항이 사실상 평행하면 ω 와 α 가 가까워야 한다(둘 다 신뢰도 추정치)."""
    columns = [
        [1.0, 2.0, 3.0, 4.0, 5.0, 2.0, 4.0, 3.0],
        [1.0, 2.0, 3.0, 4.0, 5.0, 3.0, 4.0, 2.0],
        [2.0, 2.0, 3.0, 4.0, 5.0, 2.0, 5.0, 3.0],
    ]
    om = factor.omega_total(columns)
    alpha = stats.cronbach_alpha(columns)
    assert 0.0 <= om["omega"] <= 1.0
    assert abs(om["omega"] - alpha) < 0.15


def test_omega_none_for_too_few_items_or_respondents():
    two = [[1.0, 2.0, 3.0], [2.0, 1.0, 3.0]]
    assert factor.omega_total(two) is None  # 문항 2개
    short = [[1.0, 2.0], [2.0, 1.0], [1.0, 3.0]]
    assert factor.omega_total(short) is None  # 응답자 2명


def test_omega_none_for_constant_item():
    cols = [
        [1.0, 2.0, 3.0, 4.0, 5.0],
        [2.0, 2.0, 3.0, 4.0, 5.0],
        [3.0, 3.0, 3.0, 3.0, 3.0],  # 분산 0
    ]
    assert factor.omega_total(cols) is None


def test_correlation_matrix_is_symmetric_with_unit_diagonal():
    cols = [
        [1.0, 2.0, 3.0, 4.0],
        [2.0, 1.0, 4.0, 3.0],
        [1.0, 3.0, 2.0, 4.0],
    ]
    R = factor.correlation_matrix(cols)
    for i in range(3):
        assert R[i][i] == 1.0
        for j in range(3):
            assert R[i][j] == pytest.approx(R[j][i])
            assert R[i][j] == pytest.approx(stats.pearson(cols[i], cols[j]))


def test_inverse_matches_hand_computed():
    inv = factor._inverse([[4.0, 7.0], [2.0, 6.0]])
    # 1/det * [[6,-7],[-2,4]], det=10
    assert inv[0][0] == pytest.approx(0.6)
    assert inv[0][1] == pytest.approx(-0.7)
    assert inv[1][0] == pytest.approx(-0.2)
    assert inv[1][1] == pytest.approx(0.4)


def test_inverse_none_for_singular():
    assert factor._inverse([[1.0, 2.0], [2.0, 4.0]]) is None
