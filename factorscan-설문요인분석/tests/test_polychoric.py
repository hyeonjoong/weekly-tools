"""폴리코릭 상관 검증 — 닫힌형 오라클·잠재변수 복원·독립(scipy) 대조.

scipy가 있으면 이변량 정규 CDF/최적화와 대조하고, 없으면 닫힌형 항등식으로 검증한다.
"""
import math

import numpy as np
import pytest

from factorscan import polychoric as pc
from factorscan.analyze import analyze
from factorscan.cli import run
from factorscan.dataio import Dataset, listwise


def _prep(names, mat):
    return listwise(Dataset(names=names, data=np.asarray(mat, dtype=float)))


# ---------- 정규 CDF/probit ----------
def test_norm_cdf_known():
    assert pc.norm_cdf(0.0) == pytest.approx(0.5)
    assert pc.norm_cdf(1.959963985) == pytest.approx(0.975, abs=1e-6)
    assert pc.norm_cdf(-1.959963985) == pytest.approx(0.025, abs=1e-6)


def test_norm_ppf_inverts_cdf():
    for p in [1e-5, 0.01, 0.1, 0.3, 0.5, 0.7, 0.9, 0.99, 1 - 1e-5]:
        assert pc.norm_cdf(pc.norm_ppf(p)) == pytest.approx(p, abs=1e-7)
    assert pc.norm_ppf(0.0) == -math.inf
    assert pc.norm_ppf(1.0) == math.inf


# ---------- 이변량 정규 CDF: 닫힌형 오라클 ----------
def test_bvn_cdf_closed_form_at_origin():
    # Φ₂(0,0;ρ) = 1/4 + arcsin(ρ)/(2π)  (고전적 정확식)
    for rho in [-0.9, -0.5, 0.0, 0.3, 0.7, 0.95]:
        expected = 0.25 + math.asin(rho) / (2 * math.pi)
        assert pc.bvn_cdf(0.0, 0.0, rho) == pytest.approx(expected, abs=1e-12)


def test_bvn_cdf_independence_and_bounds():
    # ρ=0 → 독립: Φ₂ = Φ(a)Φ(b)
    for a in [-1.0, 0.5, 2.0]:
        for b in [-0.5, 1.0]:
            assert pc.bvn_cdf(a, b, 0.0) == pytest.approx(pc.norm_cdf(a) * pc.norm_cdf(b), abs=1e-12)
    # 무한 경계
    assert pc.bvn_cdf(math.inf, math.inf, 0.5) == pytest.approx(1.0)
    assert pc.bvn_cdf(-math.inf, 1.0, 0.5) == 0.0
    assert pc.bvn_cdf(math.inf, 0.7, 0.5) == pytest.approx(pc.norm_cdf(0.7))
    # perfect ρ→1 → Φ₂(a,a;1)=Φ(a) (극단 |ρ|≈1은 Genz 정밀도가 다소 낮아 완화 허용)
    assert pc.bvn_cdf(0.4, 0.4, 0.999999) == pytest.approx(pc.norm_cdf(0.4), abs=1e-3)


def test_bvn_cdf_matches_scipy():
    mvn = pytest.importorskip("scipy.stats").multivariate_normal
    maxerr = 0.0
    for r in [-0.8, -0.3, 0.2, 0.6, 0.9]:
        for a in [-1.5, 0.0, 1.0]:
            for b in [-0.7, 0.4, 1.8]:
                got = pc.bvn_cdf(a, b, r)
                exp = mvn.cdf([a, b], mean=[0, 0], cov=[[1, r], [r, 1]])
                maxerr = max(maxerr, abs(got - exp))
    assert maxerr < 1e-10


# ---------- 폴리코릭 상관: 잠재변수 복원 ----------
def _latent_ordinal(n, true_r, cuts, seed):
    rng = np.random.default_rng(seed)
    L = np.linalg.cholesky([[1, true_r], [true_r, 1]])
    z = rng.standard_normal((n, 2)) @ L.T
    xi = np.digitize(z[:, 0], cuts)
    xj = np.digitize(z[:, 1], cuts)
    return xi, xj


def test_polychoric_recovers_latent_rho_and_deattenuates():
    for true_r in [0.3, 0.6, 0.8]:
        xi, xj = _latent_ordinal(5000, true_r, [-1.0, -0.3, 0.4, 1.1], seed=1)
        rho = pc.polychoric_corr(xi, xj)
        pear = np.corrcoef(xi, xj)[0, 1]
        assert abs(rho - true_r) < 0.05           # 참값 근방 복원
        assert rho > pear - 1e-9                    # 피어슨보다 크(하향편의 보정)


def test_polychoric_matches_scipy_two_step():
    stats = pytest.importorskip("scipy.stats")
    opt = pytest.importorskip("scipy.optimize")
    mvn, norm = stats.multivariate_normal, stats.norm
    xi, xj = _latent_ordinal(3000, 0.55, [-0.8, 0.1, 0.9], seed=7)
    got = pc.polychoric_corr(xi, xj)
    ci, cj = np.unique(xi), np.unique(xj)
    mi = {v: k for k, v in enumerate(ci)}
    mj = {v: k for k, v in enumerate(cj)}
    T = np.zeros((ci.size, cj.size))
    for a, b in zip(xi, xj):
        T[mi[a], mj[b]] += 1

    def th(counts):
        cum = np.cumsum(counts) / counts.sum()
        return [-math.inf] + [norm.ppf(c) for c in cum[:-1]] + [math.inf]

    ta, tb = th(T.sum(1)), th(T.sum(0))

    def Phi2(a, b, r):
        if a == -math.inf or b == -math.inf:
            return 0.0
        if a == math.inf:
            return 1.0 if b == math.inf else float(norm.cdf(b))
        if b == math.inf:
            return float(norm.cdf(a))
        return float(mvn.cdf([a, b], mean=[0, 0], cov=[[1, r], [r, 1]]))

    def nll(r):
        s = 0.0
        for u in range(ci.size):
            for v in range(cj.size):
                c = T[u, v]
                if c == 0:
                    continue
                p = (Phi2(ta[u + 1], tb[v + 1], r) - Phi2(ta[u], tb[v + 1], r)
                     - Phi2(ta[u + 1], tb[v], r) + Phi2(ta[u], tb[v], r))
                s += c * math.log(max(p, 1e-12))
        return -s

    oracle = opt.minimize_scalar(nll, bounds=(-0.999, 0.999), method="bounded").x
    assert got == pytest.approx(oracle, abs=1e-3)


def test_polychoric_matrix_symmetric_unit_diagonal():
    rng = np.random.default_rng(3)
    x = rng.integers(1, 6, (300, 5))
    r = pc.polychoric_matrix(x.astype(float))
    assert np.allclose(np.diag(r), 1.0)
    assert np.allclose(r, r.T)
    assert np.all(np.abs(r) <= 1.0 + 1e-9)


def test_polychoric_corr_constant_is_nan():
    xi = np.array([2, 2, 2, 2, 2])
    xj = np.array([1, 2, 3, 4, 5])
    assert math.isnan(pc.polychoric_corr(xi, xj))


def test_polychoric_strong_monotonic_near_one():
    xi = np.array([1, 1, 2, 2, 3, 3, 4, 4, 5, 5] * 20)
    xj = xi.copy()
    assert pc.polychoric_corr(xi, xj) > 0.95


# ---------- analyze/CLI 통합 ----------
def _ordinal_two_factor(n=400, seed=5):
    rng = np.random.default_rng(seed)
    f = rng.standard_normal((n, 2))
    load = np.array([[0.8, 0], [0.75, 0], [0.7, 0], [0, 0.8], [0, 0.75], [0, 0.7]])
    z = f @ load.T + 0.5 * rng.standard_normal((n, 6))
    x = np.clip(np.digitize(z, [-1.2, -0.4, 0.4, 1.2]) + 1, 1, 5).astype(float)
    return _prep([f"Q{i+1}" for i in range(6)], x)


def test_analyze_polychoric_end_to_end():
    prep = _ordinal_two_factor()
    res = analyze(prep, parallel_iter=0, correlation="polychoric")
    assert res["correlation"] == "polychoric"
    r = np.array(res["correlation_matrix"])
    assert np.allclose(np.diag(r), 1.0)
    # 두 요인으로 갈림
    L = np.array(res["loadings"])
    g1 = {int(np.argmax(np.abs(L[i]))) for i in range(3)}
    g2 = {int(np.argmax(np.abs(L[i]))) for i in range(3, 6)}
    assert len(g1) == 1 and len(g2) == 1 and g1 != g2


def test_analyze_invalid_correlation():
    prep = _ordinal_two_factor()
    with pytest.raises(ValueError, match="correlation"):
        analyze(prep, parallel_iter=0, correlation="spearman")


def test_polychoric_noninteger_warns():
    rng = np.random.default_rng(0)
    x = rng.integers(1, 6, (100, 4)).astype(float)
    x[0, 0] = 2.5   # 비정수 값
    prep = _prep([f"Q{i}" for i in range(4)], x)
    res = analyze(prep, parallel_iter=0, correlation="polychoric")
    assert any("정수" in w for w in res["warnings"])


def test_polychoric_many_categories_warns():
    rng = np.random.default_rng(0)
    x = rng.integers(1, 40, (200, 4)).astype(float)   # 범주 매우 많음
    prep = _prep([f"Q{i}" for i in range(4)], x)
    res = analyze(prep, parallel_iter=0, correlation="polychoric")
    assert any("범주 수가 많" in w for w in res["warnings"])


def test_polychoric_non_pd_warns_and_survives():
    # 완전상관 문항쌍 → 폴리코릭 행렬 비양정부호. KMO/Bartlett 생략+경고, 죽지 않음.
    rng = np.random.default_rng(0)
    base = rng.integers(1, 6, (80, 2))
    x = np.column_stack([base, base[:, 0], base[:, 1]]).astype(float)  # Q3=Q1, Q4=Q2
    prep = _prep([f"Q{i}" for i in range(4)], x)
    res = analyze(prep, parallel_iter=0, correlation="polychoric")
    assert res["correlation"] == "polychoric"
    # 비양정부호면 KMO/Bartlett None + 경고(폴리코릭 또는 특이 경로)
    if res["kmo"] is None:
        assert any("정부호" in w or "특이" in w for w in res["warnings"])
    assert len(res["eigenvalues"]) == 4   # 고유값은 여전히 계산


def test_cli_polychoric_json_and_report(capsys):
    import os
    ex = os.path.join(os.path.dirname(__file__), "..", "examples", "sleep_scale.csv")
    cfg = os.path.join(os.path.dirname(__file__), "..", "examples", "sleep_config.json")
    rc = run([os.path.abspath(ex), "--config", os.path.abspath(cfg),
              "--parallel-iter", "0", "--correlation", "polychoric"])
    assert rc == 0
    assert "폴리코릭" in capsys.readouterr().out


def test_polychoric_higher_loadings_than_pearson():
    # 폴리코릭은 하향편의를 보정 → 같은 순서형 자료에서 첫 고유값이 피어슨보다 크거나 같다.
    prep = _ordinal_two_factor()
    pe = analyze(prep, parallel_iter=0, correlation="pearson")
    po = analyze(prep, parallel_iter=0, correlation="polychoric")
    assert po["eigenvalues"][0] >= pe["eigenvalues"][0] - 1e-9
