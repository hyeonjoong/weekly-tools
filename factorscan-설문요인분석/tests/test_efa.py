"""EFA 수치 엔진을 손으로 계산한 기댓값/독립 오라클에 대해 검증."""
import numpy as np
import pytest

from factorscan import efa


def equicorr(p, rho):
    r = np.full((p, p), rho, dtype=float)
    np.fill_diagonal(r, 1.0)
    return r


# ---------- 고유값 (등상관 행렬은 닫힌형 해가 있음) ----------
def test_eigen_equicorrelation_closed_form():
    # p변수, 비대각 rho → 고유값: 1+(p-1)rho (1회), 1-rho (p-1회)
    r = equicorr(4, 0.5)
    eig = efa.eigen_summary(r)
    assert eig.values[0] == pytest.approx(2.5, abs=1e-9)       # 1+3*0.5
    assert eig.values[1:] == pytest.approx([0.5, 0.5, 0.5], abs=1e-9)
    assert eig.values.sum() == pytest.approx(4.0)              # 대각합 보존
    assert eig.kaiser_k == 1
    assert eig.prop_variance[0] == pytest.approx(2.5 / 4)


# ---------- Bartlett (등상관 행렬 행렬식 닫힌형) ----------
def test_bartlett_equicorrelation_hand_computed():
    p, rho, n = 4, 0.5, 100
    r = equicorr(p, rho)
    # det = (1-rho)^(p-1) * (1+(p-1)rho) = 0.5^3 * 2.5 = 0.3125
    det = (1 - rho) ** (p - 1) * (1 + (p - 1) * rho)
    expected_chi = -((n - 1) - (2 * p + 5) / 6.0) * np.log(det)
    b = efa.bartlett_sphericity(r, n)
    assert b.df == 6                       # 4*3/2
    assert b.chi_square == pytest.approx(expected_chi, rel=1e-9)
    assert 0.0 < b.p_value < 1e-10


def test_bartlett_identity_is_nonsignificant():
    r = np.eye(5)
    b = efa.bartlett_sphericity(r, 50)
    assert b.chi_square == pytest.approx(0.0, abs=1e-9)
    assert b.p_value == pytest.approx(1.0)


# ---------- KMO (회귀 잔차 편상관을 독립 오라클로 사용) ----------
def _partial_corr_via_regression(x):
    n, p = x.shape
    P = np.zeros((p, p))
    for i in range(p):
        for j in range(p):
            if i == j:
                continue
            others = [c for c in range(p) if c not in (i, j)]
            Z = np.column_stack([np.ones(n)] + [x[:, c] for c in others])
            bi = np.linalg.lstsq(Z, x[:, i], rcond=None)[0]
            bj = np.linalg.lstsq(Z, x[:, j], rcond=None)[0]
            ri = x[:, i] - Z @ bi
            rj = x[:, j] - Z @ bj
            P[i, j] = np.corrcoef(ri, rj)[0, 1]
    return P


def test_kmo_matches_independent_partial_correlation_oracle():
    rng = np.random.default_rng(3)
    f = rng.standard_normal((200, 1))
    x = f @ np.array([[0.8, 0.7, 0.6, 0.5]]) + 0.5 * rng.standard_normal((200, 4))
    r = np.corrcoef(x, rowvar=False)

    got = efa.kmo(r)

    r_off = r.copy()
    np.fill_diagonal(r_off, 0.0)
    P = _partial_corr_via_regression(x)
    r2, p2 = r_off ** 2, P ** 2
    expected_overall = r2.sum() / (r2.sum() + p2.sum())
    assert got.overall == pytest.approx(expected_overall, rel=1e-6)
    assert 0.0 < got.overall < 1.0
    assert got.per_item.shape == (4,)


# ---------- 주성분 적재 / 공통성 ----------
def test_component_loadings_equicorrelation():
    r = equicorr(4, 0.5)
    L = efa.component_loadings(r, 1)
    # 1요인 적재 = 고유벡터(±0.5) * sqrt(2.5) → |적재| = 0.5*sqrt(2.5)
    assert np.allclose(np.abs(L[:, 0]), 0.5 * np.sqrt(2.5))
    comm = efa.communalities(L)
    assert comm == pytest.approx([0.625] * 4, abs=1e-9)   # (0.5^2 * 2.5)


# ---------- Varimax 불변량 ----------
def test_varimax_preserves_communalities_and_total_variance():
    rng = np.random.default_rng(11)
    f = rng.standard_normal((300, 2))
    load_true = np.array([[0.8, 0.1], [0.7, 0.0], [0.1, 0.8], [0.0, 0.75]])
    x = f @ load_true.T + 0.4 * rng.standard_normal((300, 4))
    r = np.corrcoef(x, rowvar=False)
    raw = efa.component_loadings(r, 2)
    rot = efa.varimax(raw)

    # 직교회전 → 행별 공통성 보존, 총 적재제곱합 보존
    assert efa.communalities(rot) == pytest.approx(efa.communalities(raw), abs=1e-8)
    assert (rot ** 2).sum() == pytest.approx((raw ** 2).sum(), abs=1e-8)

    # 단순구조 증가: 제곱적재의 열별 분산 합이 커진다
    def varimax_crit(m):
        return np.var(m ** 2, axis=0).sum()
    assert varimax_crit(rot) >= varimax_crit(raw) - 1e-9


def test_varimax_recovers_simple_structure():
    rng = np.random.default_rng(1)
    f = rng.standard_normal((400, 2))
    load_true = np.array([[0.85, 0.0], [0.80, 0.0], [0.0, 0.82], [0.0, 0.78]])
    x = f @ load_true.T + 0.35 * rng.standard_normal((400, 4))
    r = np.corrcoef(x, rowvar=False)
    rot = efa.apply_sign_convention(efa.varimax(efa.component_loadings(r, 2)))
    # 문항 0,1은 같은 요인, 문항 2,3은 다른 요인에 주적재
    assert np.argmax(np.abs(rot[0])) == np.argmax(np.abs(rot[1]))
    assert np.argmax(np.abs(rot[2])) == np.argmax(np.abs(rot[3]))
    assert np.argmax(np.abs(rot[0])) != np.argmax(np.abs(rot[2]))


def test_sign_convention_makes_lead_positive():
    L = np.array([[-0.9, 0.1], [-0.8, 0.2], [0.1, -0.7]])
    out = efa.apply_sign_convention(L)
    for j in range(out.shape[1]):
        lead = out[np.argmax(np.abs(out[:, j])), j]
        assert lead > 0


# ---------- 수정된 문항-총점 상관 ----------
def test_item_total_perfect_linear():
    x = np.array([[1., 2, 3], [2, 3, 4], [3, 4, 5], [4, 5, 6]])
    it = efa.corrected_item_total(x)
    assert it == pytest.approx([1.0, 1.0, 1.0], abs=1e-12)


def test_item_total_matches_manual_corr():
    x = np.array([[1., 5, 2], [2, 4, 2], [3, 3, 5], [4, 2, 1], [5, 1, 4]])
    it = efa.corrected_item_total(x)
    # 문항0 vs (문항1+문항2) 를 직접 계산
    rest0 = x[:, 1] + x[:, 2]
    assert it[0] == pytest.approx(np.corrcoef(x[:, 0], rest0)[0, 1])


# ---------- 평행분석 ----------
def test_parallel_analysis_deterministic_and_shaped():
    a = efa.parallel_analysis(100, 5, iters=20, seed=42)
    b = efa.parallel_analysis(100, 5, iters=20, seed=42)
    assert a.shape == (5,)
    assert np.allclose(a, b)                 # 동일 시드 → 동일 결과
    assert a[0] > a[-1]                        # 내림차순
    # 95th percentile 기준선: 첫 요인은 1보다 크고, 마지막은 1보다 작다
    assert a[0] > 1.0 > a[-1]


def test_kmo_no_nan_when_item_uncorrelated():
    # 문항3이 다른 문항과 무상관 → 분모 0. NaN 대신 0.0(적합성 없음)이어야 한다.
    r = np.array([[1.0, 0.7, 0.0],
                  [0.7, 1.0, 0.0],
                  [0.0, 0.0, 1.0]])
    with np.errstate(all="raise"):   # 0/0 경고/오류가 나면 실패
        got = efa.kmo(r)
    assert np.all(np.isfinite(got.per_item))
    assert got.per_item[2] == 0.0
    assert np.isfinite(got.overall)


def test_positive_definite_detection():
    assert efa.is_positive_definite(equicorr(4, 0.3))
    singular = np.array([[1.0, 1.0], [1.0, 1.0]])   # 완전상관 → 특이
    assert not efa.is_positive_definite(singular)
