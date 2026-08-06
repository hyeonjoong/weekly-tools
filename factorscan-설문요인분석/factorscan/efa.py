"""탐색적 요인분석(EFA)과 요인분석 적합성 진단의 수치 엔진.

- Bartlett 구형성 검정 / KMO(전체 + 문항별 MSA)
- 상관행렬 고유값, 설명분산, Kaiser 기준, 평행분석(Horn)
- 주성분 추출 적재량 + Varimax(Kaiser 정규화) 회전, 공통성
- 수정된 문항-총점 상관(corrected item-total correlation)

추출 방식은 주성분(principal component)으로, SPSS의 기본 추출 방식과 동일하다.
모든 입력은 결측 제거가 끝난 (관측자 x 문항) 실수 행렬을 가정한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np

from .stats import chi2_sf, ncx2_nc_for_quantile, ncx2_sf


def correlation_matrix(x: np.ndarray) -> np.ndarray:
    """열(문항) 간 피어슨 상관행렬. x: (n, p)."""
    return np.corrcoef(x, rowvar=False)


def pairwise_correlation(x: np.ndarray) -> tuple:
    """쌍별 완전관측(pairwise-complete) 피어슨 상관행렬과 쌍별 유효 표본 수.

    listwise 삭제는 '한 문항만 빠져도' 응답자를 통째로 버린다. 20문항 설문에서 문항별
    결측이 5%만 무작위로 흩어져 있어도 완전응답자는 0.95²⁰ ≈ 36%만 남는다 — 즉 300명
    연구가 100명대 연구로 줄어든다. 쌍별 삭제는 각 상관 r_ij 를 **i·j 를 모두 답한
    응답자**로 계산해 정보를 최대한 살린다(Marsh 1998; Enders 2010의 available-case).

    대가도 분명하다: 셀마다 표본이 달라 결과 행렬이 양의 정부호가 아닐 수 있고, 그러면
    KMO·Bartlett·ML이 성립하지 않는다. 그래서 쌍별 표본 수를 함께 돌려주고
    (분석 쪽에서 최솟값을 보수적 유효 N으로 쓴다) 필요하면 smooth_correlation 으로
    보정한 뒤 그 사실을 보고한다.

    반환: (r, counts) — r은 (p,p) 상관행렬(계산 불가 셀은 NaN),
          counts는 (p,p) 정수 행렬(대각은 그 문항의 관측 수).
    """
    x = np.asarray(x, dtype=float)
    n, p = x.shape
    obs = np.isfinite(x)
    r = np.eye(p, dtype=float)
    counts = np.zeros((p, p), dtype=np.int64)
    for i in range(p):
        counts[i, i] = int(obs[:, i].sum())
    for i in range(p):
        for j in range(i + 1, p):
            m = obs[:, i] & obs[:, j]
            cnt = int(m.sum())
            counts[i, j] = counts[j, i] = cnt
            val = np.nan
            if cnt >= 3:
                a = x[m, i]
                b = x[m, j]
                a = a - a.mean()
                b = b - b.mean()
                denom = math.sqrt(float(a @ a) * float(b @ b))
                if math.isfinite(denom) and denom > 0:
                    val = float(np.clip(float(a @ b) / denom, -1.0, 1.0))
            r[i, j] = r[j, i] = val
    return r, counts


def smooth_correlation(r: np.ndarray, eps: float = 1e-6,
                       max_iter: int = 100) -> tuple:
    """양의 정부호가 아닌 상관행렬을 고유값 절단 + 대각 재정규화로 보정한다.

    쌍별 삭제·폴리코릭 추정은 셀마다 다른 정보를 쓰기 때문에 음의 고유값을 가진
    '상관행렬 비슷한 것'을 만들어 낼 수 있다. 그대로 두면 행렬식이 0 이하가 되어
    KMO·Bartlett·ML이 전부 불가능해진다. 여기서는 고전적인 eigenvalue clipping(bending):
    음/미세 고유값을 eps로 올리고 다시 단위 대각으로 재정규화하기를 반복한다.

    반환: (보정행렬, 진단 dict). 진단에는 max_delta(원본 대비 최대 절대변화),
    min_eig_before(보정 전 최소 고유값), n_clipped(하한에 걸린 고유값 개수)가 들어간다.

    **max_delta만으로 심각도를 읽으면 안 된다**: 문항이 완전히 중복돼 고유값이 정확히 0인
    경우(가장 나쁜 입력) 보정은 그 값을 eps로 올릴 뿐이라 상관값 변화는 1e-6 수준으로
    작게 나온다. 그런데 그때 ln|R|은 사실상 ln(eps)가 지배하므로 Bartlett χ²·KMO는
    **자료가 아니라 하한값이 만들어 낸 숫자**가 된다. 그래서 호출 쪽은 '보정을 했는가'를
    기준으로 검정을 생략하고, min_eig_before/n_clipped를 함께 보고해야 한다.
    """
    r0 = np.asarray(r, dtype=float)
    out = np.array(r0, dtype=float, copy=True)
    out = (out + out.T) / 2.0
    w0 = np.linalg.eigvalsh(out) if out.size else np.array([1.0])
    min_eig_before = float(w0.min())
    n_clipped = int(np.sum(w0 <= eps))
    for _ in range(max_iter):
        w, v = np.linalg.eigh(out)
        if float(w.min()) > eps:
            break
        w = np.clip(w, eps, None)
        out = (v * w) @ v.T
        d = np.sqrt(np.clip(np.diag(out), 1e-12, None))
        out = out / np.outer(d, d)
        out = (out + out.T) / 2.0
        np.fill_diagonal(out, 1.0)
    delta = float(np.max(np.abs(out - r0))) if out.size else 0.0
    return out, {"max_delta": delta, "min_eig_before": min_eig_before,
                 "n_clipped": n_clipped, "eps": float(eps)}


def _safe_inv(m: np.ndarray) -> np.ndarray:
    """역행렬. 특이행렬이면 유사역행렬(pinv)로 대체해 예외 없이 진행한다."""
    try:
        return np.linalg.inv(m)
    except np.linalg.LinAlgError:
        return np.linalg.pinv(m)


def is_positive_definite(r: np.ndarray) -> bool:
    """상관행렬이 양의 정부호(역행렬/행렬식 사용 가능)인지."""
    try:
        w = np.linalg.eigvalsh(r)
    except np.linalg.LinAlgError:
        return False
    return bool(np.all(w > 1e-10))


@dataclass
class Bartlett:
    chi_square: float
    df: int
    p_value: float


def bartlett_sphericity(r: np.ndarray, n: int) -> Bartlett:
    """Bartlett 구형성 검정. H0: 상관행렬 = 단위행렬(요인분석 부적합).

    chi^2 = -[(n-1) - (2p+5)/6] * ln|R|,  df = p(p-1)/2.
    """
    p = r.shape[0]
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        sign, logdet = np.linalg.slogdet(r)
    if sign <= 0:
        raise ValueError("상관행렬의 행렬식이 0 이하입니다(특이/비양정부호) — Bartlett 검정 불가")
    mult = (n - 1) - (2 * p + 5) / 6.0
    if mult <= 0:
        # 표본이 문항 수에 비해 너무 작으면 Bartlett 보정계수가 음수가 되고, χ²가
        # **음수**로 나온다. chi2_sf가 그걸 p=1.0으로 잘라 내면 "상관행렬이 단위행렬과
        # 다르지 않다(=요인분석 근거 없음)"로 조용히 읽힌다 — 검정이 성립하지 않는 것과
        # 귀무가설을 기각하지 못한 것은 완전히 다른 결론이므로 여기서 막는다.
        raise ValueError(
            f"표본이 너무 작아 Bartlett 보정계수가 0 이하입니다"
            f"(n={n}, p={p} → (n−1)−(2p+5)/6 = {mult:.2f}). 검정이 성립하지 않습니다.")
    chi = -mult * logdet
    df = p * (p - 1) // 2
    return Bartlett(chi_square=float(chi), df=int(df), p_value=float(chi2_sf(chi, df)))


@dataclass
class KMO:
    overall: float
    per_item: np.ndarray  # (p,)  문항별 MSA


def kmo(r: np.ndarray) -> KMO:
    """Kaiser-Meyer-Olkin 표본적합성 측도(전체 + 문항별 MSA).

    편상관 p_ij = -R^{-1}_ij / sqrt(R^{-1}_ii * R^{-1}_jj) 를 사용해
    KMO = Σr² / (Σr² + Σp²)  (대각 제외).
    """
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        r_inv = np.linalg.inv(r)
    d = np.sqrt(np.diag(r_inv))
    partial = -r_inv / np.outer(d, d)
    np.fill_diagonal(partial, 0.0)

    r_off = r.copy()
    np.fill_diagonal(r_off, 0.0)

    r2 = r_off ** 2
    p2 = partial ** 2

    r2_sum = r2.sum()
    p2_sum = p2.sum()
    denom = r2_sum + p2_sum
    # 모든 상관·편상관이 0인 퇴화 상황(분모 0)은 적합성 없음(0.0)으로 처리.
    overall = r2_sum / denom if denom > 0 else 0.0

    r2_row = r2.sum(axis=1)
    p2_row = p2.sum(axis=1)
    row_denom = r2_row + p2_row
    # 특정 문항이 다른 모든 문항과 무상관이면 분모 0 → MSA 정의 불가.
    # 0.0(적합성 없음)으로 두어 NaN 전파와 잘못된 플래그 비교를 막는다.
    per_item = np.divide(r2_row, row_denom,
                         out=np.zeros_like(r2_row), where=row_denom > 0)
    return KMO(overall=float(overall), per_item=per_item)


@dataclass
class Eigen:
    values: np.ndarray          # 내림차순 고유값 (p,)
    prop_variance: np.ndarray   # 각 요인 설명분산 비율
    cum_variance: np.ndarray    # 누적 설명분산 비율
    kaiser_k: int               # 고유값 > 1 개수


def eigen_summary(r: np.ndarray) -> Eigen:
    """상관행렬 고유값 분해 요약(고유값·설명분산·Kaiser 기준)."""
    w = np.linalg.eigvalsh(r)[::-1]
    w = np.clip(w, 0.0, None)  # 수치오차로 인한 미세 음수 제거
    # 상관행렬의 대각합(trace)은 변수 수 p와 정확히 같다. 클리핑된 고유값 합 대신
    # p로 나누면 미세 음수 제거로 인한 총합 왜곡 없이 설명분산 비율이 정확해진다.
    total = float(w.size)
    prop = w / total if total > 0 else np.zeros_like(w)
    return Eigen(
        values=w,
        prop_variance=prop,
        cum_variance=np.cumsum(prop),
        kaiser_k=int(np.sum(w > 1.0)),
    )


def retained_by_parallel(observed: np.ndarray, random_ref: np.ndarray) -> int:
    """평행분석 유지 요인 수: 관측 고유값이 무작위 기준선을 넘는 '선행' 요인 개수.

    Horn 평행분석의 관례는 첫 교차(관측 <= 무작위) 지점에서 멈추는 것이다.
    단순 개수 합산(np.sum(observed > random))은 뒤쪽에서 관측이 다시 커지는
    비단조 상황에서 요인을 과대추정할 수 있으므로 선행 연속 개수만 센다.
    """
    above = observed > random_ref
    k = 0
    for flag in above:
        if not flag:
            break
        k += 1
    return int(k)


def parallel_analysis(n: int, p: int, iters: int, seed: int,
                      percentile: float = 95.0) -> np.ndarray:
    """Horn의 평행분석: 무작위 정규데이터(n x p)의 고유값 분포 percentile 값.

    관측 고유값이 이 값보다 큰 요인만 유지하는 기준선을 제공한다.
    """
    rng = np.random.default_rng(seed)
    eigs = np.empty((iters, p))
    for i in range(iters):
        x = rng.standard_normal((n, p))
        rr = np.corrcoef(x, rowvar=False)
        eigs[i] = np.linalg.eigvalsh(rr)[::-1]
    return np.percentile(eigs, percentile, axis=0)


def component_loadings(r: np.ndarray, k: int) -> np.ndarray:
    """주성분 추출: 상위 k개 성분의 적재량 (p, k).  loading = 고유벡터 * sqrt(고유값)."""
    vals, vecs = np.linalg.eigh(r)
    order = np.argsort(vals)[::-1][:k]
    vals_k = np.clip(vals[order], 0.0, None)
    vecs_k = vecs[:, order]
    return vecs_k * np.sqrt(vals_k)


def squared_multiple_correlations(r: np.ndarray) -> np.ndarray:
    """각 변수의 다중상관제곱(SMC) = 1 − 1/R⁻¹_ii. 공통요인 추출(PAF)의 초기 공통성.

    SMC는 나머지 변수들로 그 변수를 회귀했을 때의 R²이며, 공통성의 하한 추정으로 널리 쓰인다.
    상관행렬이 특이하면 유사역행렬(pinv)로 대체하고, 결과를 [0,1]로 절단한다.
    """
    try:
        r_inv = np.linalg.inv(r)
    except np.linalg.LinAlgError:
        r_inv = np.linalg.pinv(r)
    diag = np.diag(r_inv).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        smc = 1.0 - 1.0 / diag
    smc = np.where(np.isfinite(smc), smc, 0.0)
    return np.clip(smc, 0.0, 1.0)


@dataclass
class PAFResult:
    loadings: np.ndarray        # (p, k) 비회전 공통요인 적재
    communalities: np.ndarray   # (p,) 최종 공통성(0..1로 절단)
    n_iter: int                 # 실제 반복 횟수
    converged: bool             # 공통성 수렴 여부
    heywood: bool               # Heywood 케이스(공통성>1 발생) 여부


def paf_loadings(r: np.ndarray, k: int, max_iter: int = 2000,
                 tol: float = 1e-6) -> PAFResult:
    """주축분해(반복 Principal Axis Factoring)로 공통요인 적재를 추출한다.

    주성분(PCA)이 관측분산 전체를 쓰는 것과 달리, PAF는 대각을 공통성 추정치로 바꾼
    '축소상관행렬'을 분해해 **공통분산만** 모형화한다(SPSS/R의 principal-axis와 동일 계열).
    절차: 대각 ← SMC → 상위 k 고유분해 → 공통성 갱신 → 수렴까지 반복.

    반환 PAFResult. Heywood 케이스(공통성>1)는 1로 절단하고 flag로 알린다.

    max_iter 기본값은 2000이다. 100으로 두면 작은 표본·낮은 공통성에서 재표본의 71~93%가
    '비수렴'으로 걸러졌는데, 그 해들을 끝까지 돌려 보면 절반이 최종해와 1e-3 이내였고
    필요한 반복은 중앙값 194회였다 — 즉 진짜 발산이 아니라 상한이 낮았던 것이다.
    부트스트랩이 대부분의 재표본을 버리면 구간이 최대 11% 좁아져(과신) 오히려 해롭다.
    """
    p = r.shape[0]
    h2 = squared_multiple_correlations(r)
    load = np.zeros((p, k))
    converged = False
    heywood = False
    n_iter = 0
    for it in range(1, max_iter + 1):
        n_iter = it
        reduced = r.copy()
        np.fill_diagonal(reduced, h2)
        vals, vecs = np.linalg.eigh(reduced)
        order = np.argsort(vals)[::-1][:k]
        vals_k = np.clip(vals[order], 0.0, None)
        load = vecs[:, order] * np.sqrt(vals_k)
        h2_new = (load ** 2).sum(axis=1)
        # Heywood: 공통성이 1을 초과하거나(반복 중) 경계(≈1)에 붙으면 플래그.
        # 특이자료에서 SMC 초기값이 이미 1.0으로 고정돼 반복 중 '초과'가 안 나는 경우도 잡는다.
        if np.any(h2_new >= 1.0 - 1e-9):
            heywood = True
        h2_new = np.clip(h2_new, 0.0, 1.0)
        delta = float(np.max(np.abs(h2_new - h2)))
        h2 = h2_new
        if delta < tol:
            converged = True
            break
    return PAFResult(loadings=load, communalities=h2, n_iter=n_iter,
                     converged=converged, heywood=heywood)


@dataclass
class MLResult:
    loadings: np.ndarray        # (p, k) 비회전 최대우도 적재
    uniquenesses: np.ndarray    # (p,) 고유분산 Ψ
    communalities: np.ndarray   # (p,) = 1 − Ψ
    criterion: float            # 최소화된 불일치함수 F_min (Lawley-Maxwell)
    n_iter: int
    converged: bool
    heywood: bool               # Ψ가 하한에 붙은 문항 존재(부적절해)


# ML 고유분산의 하한. 0이면 목적함수가 발산하므로 R의 factanal과 같은 관례값을 쓴다.
_ML_PSI_MIN = 0.005


def _ml_objective(psi: np.ndarray, r: np.ndarray, k: int) -> float:
    """Lawley-Maxwell 프로파일 불일치함수 F(Ψ).

    Ψ가 주어지면 Λ는 해석적으로 결정되므로, 우도는 Ψ만의 함수로 축약된다:
    F(Ψ) = Σ_{j>k} (λ_j − ln λ_j − 1),  λ_j = Ψ^{-1/2} R Ψ^{-1/2} 의 고유값(내림차순).
    """
    sc = 1.0 / np.sqrt(psi)
    sstar = r * np.outer(sc, sc)
    e = np.linalg.eigvalsh(sstar)[::-1][k:]
    if np.any(e <= 1e-12):      # 비양정부호/특이 → 이 Ψ는 실행 불가 영역
        return 1e10
    return float(np.sum(e - np.log(e) - 1.0))


def _ml_loadings_given_psi(psi: np.ndarray, r: np.ndarray, k: int) -> np.ndarray:
    """주어진 Ψ에서 우도를 최대화하는 적재 Λ = Ψ^{1/2} · V_k · sqrt(max(λ_k − 1, 0))."""
    sc = 1.0 / np.sqrt(psi)
    sstar = r * np.outer(sc, sc)
    vals, vecs = np.linalg.eigh(sstar)
    order = np.argsort(vals)[::-1][:k]
    load = vecs[:, order] * np.sqrt(np.clip(vals[order] - 1.0, 0.0, None))
    return load * np.sqrt(psi)[:, None]


def _ml_gradient(psi: np.ndarray, r: np.ndarray, k: int) -> np.ndarray:
    """∂F/∂ψ_i = diag(ΛΛᵀ + Ψ − R)_i / ψ_i²  (프로파일 목적함수의 해석적 기울기)."""
    load = _ml_loadings_given_psi(psi, r, k)
    g = load @ load.T + np.diag(psi) - r
    return np.diag(g) / psi ** 2


def ml_factor_analysis(r: np.ndarray, k: int, max_iter: int = 500,
                       tol: float = 1e-9) -> MLResult:
    """최대우도(ML) 공통요인 추출 — 다변량 정규 가정 하의 요인모형 적합.

    PCA/PAF와 달리 ML은 **확률모형**을 적합하므로 χ² 적합도 검정과 RMSEA/TLI/CFI 같은
    공식 적합도지수를 낼 수 있다(EFA 게재 표에서 리뷰어가 요구하는 지표). R의 `factanal`,
    SPSS의 '최대우도' 추출과 같은 계열이며, 프로파일 우도 F(Ψ)를 고유분산 Ψ에 대해
    상자제약(0.005 ≤ ψ ≤ 1) 최소화한 뒤 Λ를 해석적으로 복원한다.

    최적화는 사영 Barzilai-Borwein 기울기법 + 백트래킹 선탐색(scipy 불필요).
    초기값은 factanal과 동일한 ψ_i = (1 − k/(2p)) / R⁻¹_ii.
    Ψ가 하한에 붙으면 Heywood 케이스로 플래그한다.
    """
    p = r.shape[0]
    if k < 1 or k >= p:
        raise ValueError(f"ML 추출의 요인 수는 1..{p - 1} 범위여야 합니다.")

    inv_diag = np.diag(_safe_inv(r)).astype(float)
    inv_diag = np.where(inv_diag > 1e-12, inv_diag, 1.0)
    psi = np.clip((1.0 - 0.5 * k / p) / inv_diag, _ML_PSI_MIN, 1.0)

    def f(v):
        return _ml_objective(v, r, k)

    def g(v):
        return _ml_gradient(v, r, k)

    fx = f(psi)
    gx = g(psi)
    step = 1.0 / max(1e-12, float(np.max(np.abs(gx))))
    converged = False
    n_iter = 0
    for it in range(1, max_iter + 1):
        n_iter = it
        # 사영 백트래킹: 실행가능 상자 안으로 사영한 뒤 충분감소(Armijo)를 만족할 때까지 축소.
        ok = False
        for _ in range(60):
            xn = np.clip(psi - step * gx, _ML_PSI_MIN, 1.0)
            fn = f(xn)
            d = psi - xn
            if fn <= fx - 1e-4 * float(gx @ d):
                ok = True
                break
            step *= 0.5
        if not ok:
            converged = True     # 더 이상 감소 방향을 찾지 못함 = 정류점
            break
        gn = g(xn)
        s = xn - psi
        y = gn - gx
        psi, fx, gx = xn, fn, gn
        if float(np.max(np.abs(s))) < tol:
            converged = True
            break
        sy = float(s @ y)
        # BB 스텝(곡률 정보). 음/영 곡률이면 보수적으로 확장만 한다.
        step = float(s @ s) / sy if sy > 1e-14 else min(step * 2.0, 1e8)

    load = _ml_loadings_given_psi(psi, r, k)
    heywood = bool(np.any(psi <= _ML_PSI_MIN + 1e-12))
    return MLResult(loadings=load, uniquenesses=psi, communalities=1.0 - psi,
                    criterion=float(fx), n_iter=n_iter, converged=converged,
                    heywood=heywood)


def ml_max_factors(p: int) -> int:
    """자유도가 양수로 남는 ML 요인 수의 상한(모형 식별 가능 최대 k)."""
    k = 0
    while ml_df(p, k + 1) > 0:
        k += 1
    return k


def ml_df(p: int, k: int) -> int:
    """ML 요인모형의 자유도: [(p−k)² − (p+k)] / 2."""
    return int(((p - k) ** 2 - (p + k)) // 2)


def fit_indices(criterion: float, n: int, p: int, k: int,
                null_chi_square: Optional[float] = None) -> dict:
    """ML 요인해의 공식 적합도지수(χ², RMSEA+90% CI, TLI, CFI, AIC/BIC).

    - χ² = [ (n−1) − (2p+5)/6 − 2k/3 ] · F_min  (Bartlett 보정, factanal과 동일)
    - RMSEA = sqrt( max(χ²−df, 0) / (df·(n−1)) ),  90% CI는 비중심 카이제곱 역산(Steiger).
    - p_close: 근접적합 검정 H0: RMSEA ≤ .05 의 p값.
    - TLI/CFI는 독립모형(모든 상관=0)을 기준선으로 삼는다. 독립모형의 χ²는 Bartlett
      통계량과 정확히 같다(k=0에서 F = −ln|R| 이므로) — null_chi_square로 넘긴다.
    - AIC/BIC는 χ²에 대한 상대값(χ²−2df, χ²−df·ln n)으로, k 비교에만 쓴다(작을수록 좋음).

    df ≤ 0(과다모수화로 모형 식별 불가)이면 None 필드로 반환한다.
    """
    df = ml_df(p, k)
    out: dict = {"df": df, "criterion": float(criterion)}
    if df <= 0:
        out.update({"chi_square": None, "p_value": None, "rmsea": None,
                    "rmsea_lo": None, "rmsea_hi": None, "p_close": None,
                    "tli": None, "cfi": None, "aic": None, "bic": None,
                    "identified": False})
        return out

    mult = (n - 1) - (2.0 * p + 5.0) / 6.0 - (2.0 * k) / 3.0
    if mult <= 0:
        # 보정계수가 0 이하가 되면 χ² = max(mult,0)·F = 0 이 되어 **적합도가 완벽한 것처럼**
        # 보고된다(RMSEA=0, CI=[0,0], PCLOSE=1, CFI=1). 실제로는 표본이 문항 수에 비해
        # 너무 작아 검정 자체가 성립하지 않는 것이며, 같은 실행에서 TLI가 음수로 나와
        # 표가 자기모순에 빠진다. df≤0과 똑같이 '식별 불가'로 처리한다.
        out.update({"chi_square": None, "p_value": None, "rmsea": None,
                    "rmsea_lo": None, "rmsea_hi": None, "p_close": None,
                    "tli": None, "cfi": None, "aic": None, "bic": None,
                    "identified": False,
                    "unidentified_reason": (
                        f"표본이 너무 작아 χ² 보정계수가 0 이하입니다"
                        f"(n={n}, p={p}, k={k} → {mult:.2f})")})
        return out
    chi = float(max(mult, 0.0) * criterion)
    out["identified"] = True
    out["chi_square"] = chi
    out["p_value"] = float(chi2_sf(chi, df)) if chi > 0 else 1.0

    denom = df * (n - 1.0)
    out["rmsea"] = float(math.sqrt(max(chi - df, 0.0) / denom)) if denom > 0 else None
    # 90% CI: CDF가 비중심모수에 대해 단조감소하므로 상측확률 .95→하한, .05→상한.
    try:
        lo_nc = ncx2_nc_for_quantile(chi, df, 0.95)
        hi_nc = ncx2_nc_for_quantile(chi, df, 0.05)
        out["rmsea_lo"] = float(math.sqrt(lo_nc / denom))
        out["rmsea_hi"] = float(math.sqrt(hi_nc / denom))
        # 근접적합 검정(PCLOSE): H0: RMSEA ≤ .05 → λ0 = .05²·df·(n−1)
        # 상측꼬리를 직접 합산(ncx2_sf)한다 — 1−CDF로는 아주 작은 p값이 상쇄로 뭉개진다.
        nc0 = 0.05 ** 2 * denom
        out["p_close"] = float(ncx2_sf(chi, df, nc0))
    except (ValueError, OverflowError):
        out["rmsea_lo"] = out["rmsea_hi"] = out["p_close"] = None

    if null_chi_square is not None and null_chi_square > 0:
        df0 = p * (p - 1) / 2.0
        d_m = max(chi - df, 0.0)
        d_0 = max(null_chi_square - df0, 0.0)
        # CFI 분모는 두 비중심성의 최댓값(Bentler) — 모형이 기준선보다 나쁠 때도 [0,1] 유계.
        out["cfi"] = float(1.0 - d_m / max(d_0, d_m)) if max(d_0, d_m) > 0 else 1.0
        ratio0 = null_chi_square / df0 if df0 > 0 else None
        if ratio0 is not None and abs(ratio0 - 1.0) > 1e-12:
            tli = (ratio0 - chi / df) / (ratio0 - 1.0)
            out["tli"] = float(tli)
        else:
            out["tli"] = None
    else:
        out["cfi"] = None
        out["tli"] = None

    out["aic"] = float(chi - 2.0 * df)
    out["bic"] = float(chi - df * math.log(n)) if n > 1 else None
    return out


def velicer_map(r: np.ndarray, max_components: Optional[int] = None) -> dict:
    """Velicer의 MAP(최소평균편상관) 검정: 유지 요인 수를 편상관으로 결정.

    m개 성분을 편출(partial out)한 뒤 남는 편상관행렬의 '평균 제곱 비대각'을 f_m으로 두고,
    f_m을 최소화하는 m을 유지 성분 수로 본다. 성분을 더 뽑을수록 체계분산이 걷히다가
    잡음까지 걷히기 시작하면 f_m이 다시 증가하므로 최소점이 '체계적 성분'의 경계가 된다.
    Kaiser(과대추정)·평행분석과 독립적인 제3의 근거를 제공한다(Velicer 1976, power=2).

    반환: {"k": 유지수, "values": [f_0, f_1, ...], "min_index": argmin}.
    편상관이 정의 불가한 지점(대각≤0)은 NaN으로 두고 최소값 탐색에서 제외한다.
    """
    p = r.shape[0]
    if max_components is None:
        max_components = p - 1
    max_components = int(min(max_components, p - 1))
    vals, vecs = np.linalg.eigh(r)
    order = np.argsort(vals)[::-1]
    loadings = vecs[:, order] * np.sqrt(np.clip(vals[order], 0.0, None))
    iu = np.triu_indices(p, k=1)

    values: List[float] = []
    for m in range(0, max_components + 1):
        if m == 0:
            partial = r
        else:
            a = loadings[:, :m]
            cstar = r - a @ a.T
            diag = np.diag(cstar)
            if np.any(diag <= 1e-12):   # 편분산이 0/음수 → 편상관 정의 불가
                values.append(float("nan"))
                continue
            d = np.sqrt(diag)
            partial = cstar / np.outer(d, d)
        off = partial[iu]
        values.append(float(np.mean(off ** 2)))

    finite = [(i, v) for i, v in enumerate(values) if np.isfinite(v)]
    min_index = min(finite, key=lambda t: t[1])[0] if finite else 0
    return {"k": int(min_index), "values": values, "min_index": int(min_index)}


def varimax(loadings: np.ndarray, gamma: float = 1.0,
            max_iter: int = 500, tol: float = 1e-6) -> np.ndarray:
    """Kaiser 정규화 Varimax 직교회전. 단순구조를 최대화한다.

    회전은 직교변환이므로 각 문항의 공통성과 총 설명분산은 보존된다.
    """
    p, k = loadings.shape
    if k < 2:
        return loadings.copy()

    h = np.sqrt((loadings ** 2).sum(axis=1))
    h[h == 0] = 1.0
    norm = loadings / h[:, None]

    rot = np.eye(k)
    d = 0.0
    for _ in range(max_iter):
        lam = norm @ rot
        col_sumsq = (lam ** 2).sum(axis=0)
        grad = norm.T @ (lam ** 3 - (gamma / p) * (lam @ np.diag(col_sumsq)))
        u, s, vt = np.linalg.svd(grad)
        rot = u @ vt
        d_new = float(s.sum())
        if d != 0.0 and d_new < d * (1 + tol):
            break
        d = d_new

    return (norm @ rot) * h[:, None]


def promax(loadings: np.ndarray, power: int = 4) -> tuple:
    """Promax 사교(oblique) 회전. Varimax 해를 목표행렬로 기울여 요인 간 상관을 허용.

    반환: (pattern, phi) — pattern은 (p, k) 패턴적재(회귀계수 성격), phi는 (k, k)
    요인 상관행렬. 임상·심리 하위척도는 보통 서로 상관이 있어, 직교(Varimax)보다
    사교회전이 단순구조를 더 정확히 드러내고 요인 간 상관을 함께 보고할 수 있다.
    Hendrickson-White 절차(Varimax → |Λ|^power 목표 → 최소제곱 변환).
    """
    p, k = loadings.shape
    if k < 2:
        return loadings.copy(), np.eye(max(k, 1))
    v = varimax(loadings)                       # Varimax 선행
    # 목표행렬·최소제곱은 공통성 정규화(Kaiser)된 적재에서 수행하고 마지막에 되돌린다
    # (SPSS/R/factor_analyzer promax와 일치시키기 위함).
    h = np.sqrt((v ** 2).sum(axis=1))
    h[h == 0] = 1.0
    vn = v / h[:, None]
    target = vn * np.abs(vn) ** (power - 1)      # sign(vn)·|vn|^power
    coef = np.linalg.lstsq(vn, target, rcond=None)[0]
    # coef가 특이(예: PAF에서 요인 수 과다로 0 적재 열 발생)하면 inv가 죽으므로 pinv로 대체.
    inv = _safe_inv(coef.T @ coef)
    coef = coef @ np.diag(np.sqrt(np.clip(np.diag(inv), 0.0, None)))
    pattern = (vn @ coef) * h[:, None]           # 정규화 복원
    cinv = _safe_inv(coef)
    phi = cinv @ cinv.T
    return pattern, phi


def sign_convention_signs(loadings: np.ndarray) -> np.ndarray:
    """각 요인에서 절댓값 최대 적재가 양수가 되게 하는 부호벡터(+1/-1) (k,)."""
    signs = np.ones(loadings.shape[1])
    for j in range(loadings.shape[1]):
        col = loadings[:, j]
        if col[np.argmax(np.abs(col))] < 0:
            signs[j] = -1.0
    return signs


def apply_sign_convention(loadings: np.ndarray) -> np.ndarray:
    """요인 부호를 관례에 맞게 정렬: 각 요인에서 절댓값이 가장 큰 적재가 양수가 되도록.

    (요인 부호는 수학적으로 임의이므로 해석 편의를 위해 정렬한다.)
    """
    return loadings * sign_convention_signs(loadings)


def communalities(loadings: np.ndarray) -> np.ndarray:
    """추출된 요인들이 설명하는 각 문항의 분산 비율(공통성)."""
    return (loadings ** 2).sum(axis=1)


def corrected_item_total(x: np.ndarray) -> np.ndarray:
    """수정된 문항-총점 상관: 각 문항과 (자신을 제외한 나머지 문항 합)의 상관.

    x: (n, p) 결측제거된 문항 응답. 낮으면(<.30) 척도와 겉도는 문항.
    """
    n, p = x.shape
    total = x.sum(axis=1)
    out = np.full(p, np.nan)
    for i in range(p):
        rest = total - x[:, i]
        if np.std(rest) == 0 or np.std(x[:, i]) == 0:
            continue
        out[i] = np.corrcoef(x[:, i], rest)[0, 1]
    return out


def corrected_item_total_by_group(x: np.ndarray, groups: Sequence[int]) -> np.ndarray:
    """요인(하위척도)별 수정된 문항-총점 상관.

    각 문항을 자신이 속한 요인(groups[i])의 '나머지 문항 합'과만 상관낸다.
    다차원 척도에서는 전체 합 대신 하위척도 내 총점을 쓰는 것이 관례이며,
    좋은 문항이 다른 차원 때문에 낮게 나오는 왜곡을 막는다.
    요인 내 문항이 1개뿐이면(비교할 나머지 없음) NaN.
    """
    n, p = x.shape
    groups = np.asarray(list(groups))
    out = np.full(p, np.nan)
    for i in range(p):
        mates = np.where((groups == groups[i]) & (np.arange(p) != i))[0]
        if mates.size == 0:
            continue
        rest = x[:, mates].sum(axis=1)
        if np.std(rest) == 0 or np.std(x[:, i]) == 0:
            continue
        out[i] = np.corrcoef(x[:, i], rest)[0, 1]
    return out


def cronbach_alpha(x: np.ndarray) -> Optional[float]:
    """Cronbach's α(내적일관성 신뢰도) — 표본 원점수 기반.

    α = (k/(k−1))·(1 − Σσ²_i / σ²_total),  σ²는 표본분산(ddof=1).
    문항이 2개 미만이거나 총점 분산이 0이면 정의 불가(None).
    적재 기반 ω(PCA 낙관적)와 달리 실제 응답분산에서 직접 계산해 함께 보고하기 좋다.
    """
    n, k = x.shape
    if k < 2 or n < 2:
        return None
    item_var = x.var(axis=0, ddof=1)
    total_var = float(x.sum(axis=1).var(ddof=1))
    if total_var <= 0:
        return None
    return float(k / (k - 1.0) * (1.0 - item_var.sum() / total_var))


def alpha_ci_feldt(alpha: float, n: int, n_items: int,
                   conf: float = 0.95) -> Optional[tuple]:
    """Cronbach α의 Feldt 신뢰구간 (하한, 상한). scipy 불필요(자체 F 분위수 사용).

    척도 논문은 "α = .87" 만 적는 관행이 오래 남아 있었지만, 최근 심리측정 보고 지침
    (APA·COSMIN)은 **신뢰구간을 함께 요구**한다. n=50에서 α=.80의 95% CI는 대략
    [.70, .87]로 꽤 넓어서, 점추정만 보면 ".80 달성"이라 단정하게 된다.

    Feldt(1965)의 결과: 본질적 타우동등(essentially tau-equivalent) 가정 하에
        (1 − α) / (1 − α̂)  ~  F(df1 = n−1,  df2 = (n−1)(k−1))
    이므로 α ∈ [1 − (1−α̂)·F_{1−γ/2},  1 − (1−α̂)·F_{γ/2}] 이 된다(γ = 1 − conf).

    주의: 이 구간은 위 가정과 정규성에 의존한다(부트스트랩 구간과 함께 보면 좋다).
    α̂ ≥ 1 이거나 n < 2 · 문항 < 2 이면 정의 불가(None).
    """
    from .stats import f_ppf
    if alpha is None or not np.isfinite(alpha) or alpha >= 1.0:
        return None
    if n < 2 or n_items < 2:
        return None
    if not (0.0 < conf < 1.0):
        raise ValueError("conf는 0과 1 사이여야 합니다.")
    df1 = float(n - 1)
    df2 = float((n - 1) * (n_items - 1))
    g = (1.0 - conf) / 2.0
    try:
        f_hi = f_ppf(1.0 - g, df1, df2)
        f_lo = f_ppf(g, df1, df2)
    except (ValueError, OverflowError):
        return None
    one_minus = 1.0 - alpha
    return (float(1.0 - one_minus * f_hi), float(1.0 - one_minus * f_lo))


def alpha_by_group(x: np.ndarray, groups: Sequence[int], k: int) -> Dict[int, Optional[float]]:
    """요인(하위척도)별 Cronbach's α. 각 요인에 argmax 배정된 문항들로 계산.

    요인 내 문항이 2개 미만이면 None. ω와 나란히 표시해 신뢰도를 이중 확인한다.
    """
    groups = np.asarray(list(groups))
    out: Dict[int, Optional[float]] = {}
    for f in range(k):
        idx = np.where(groups == f)[0]
        out[f] = cronbach_alpha(x[:, idx]) if idx.size >= 2 else None
    return out


def subscale_scores(x: np.ndarray, groups: Sequence[int], k: int,
                    method: str = "sum") -> np.ndarray:
    """요인(하위척도)별 응답자 점수 (n, k). 각 요인에 배정된 문항의 합 또는 평균.

    groups[i]는 문항 i의 소속 요인(0-based, |적재|최대 배정). 역문항이 이미 재점수화된
    행렬 x를 받으므로 방향이 올바르다. 어떤 문항도 없는 요인 열은 NaN.
    method: "sum"(합산점수, 기본) 또는 "mean"(평균점수).
    """
    if method not in ("sum", "mean"):
        raise ValueError("method는 'sum' 또는 'mean'이어야 합니다.")
    groups = np.asarray(list(groups))
    out = np.full((x.shape[0], k), np.nan)
    for f in range(k):
        idx = np.where(groups == f)[0]
        if idx.size == 0:
            continue
        sub = x[:, idx]
        out[:, f] = sub.sum(axis=1) if method == "sum" else sub.mean(axis=1)
    return out


def prorated_subscale_scores(raw: np.ndarray, groups: Sequence[int], k: int,
                             method: str = "sum",
                             max_missing_prop: float = 0.2) -> tuple:
    """결측을 비례배분(prorate)해 **모든 응답자**의 하위척도 점수를 낸다. (scores, n_imputed)

    임상시험에서 하위척도 점수가 평가변수(endpoint)일 때, listwise 삭제는 한 문항만 빠진
    환자의 점수를 통째로 없앤다 — 실제 자료에서 4명 중 1명이 사라지기도 한다. 그래서 거의
    모든 PRO 채점 매뉴얼(SF-36·EORTC QLQ-C30 등)은 **비례배분 규칙**을 둔다:
    하위척도 문항의 일정 비율 이하만 결측이면, 응답한 문항의 평균으로 총점을 환산한다.

        prorated_sum = mean(응답한 문항) × (그 하위척도의 전체 문항 수)

    요인분석 자체는 완전응답자로 하고(구조 추정은 결측 대체에 민감하다), **채점만** 이
    규칙으로 넓히는 것이 관례다. 허용 비율을 넘는 응답자는 NaN으로 남긴다(억지로 만들지 않는다).

    raw: (n_total, p) 역문항 재점수화가 끝난 **결측 제거 전** 행렬(결측은 NaN).
    반환: (scores (n_total, k), n_imputed (n_total,) 각 응답자에게 대체된 문항 응답 수)
    """
    if method not in ("sum", "mean"):
        raise ValueError("비례배분 점수의 method는 'sum' 또는 'mean'이어야 합니다.")
    if not (0.0 <= max_missing_prop < 1.0):
        raise ValueError("max_missing_prop 은 0 이상 1 미만이어야 합니다.")
    raw = np.asarray(raw, dtype=float)
    groups = np.asarray(list(groups), dtype=int)
    n = raw.shape[0]
    out = np.full((n, k), np.nan)
    n_imputed = np.zeros(n, dtype=int)
    for f in range(k):
        idx = np.where(groups == f)[0]
        if idx.size == 0:
            continue
        sub = raw[:, idx]
        ok = np.isfinite(sub)
        n_ok = ok.sum(axis=1)
        miss_prop = 1.0 - n_ok / idx.size
        usable = (n_ok > 0) & (miss_prop <= max_missing_prop + 1e-12)
        if not np.any(usable):
            continue
        means = np.divide(np.nansum(np.where(ok, sub, 0.0), axis=1), np.maximum(n_ok, 1),
                          out=np.full(n, np.nan), where=n_ok > 0)
        out[usable, f] = (means[usable] * idx.size if method == "sum" else means[usable])
        n_imputed += np.where(usable, idx.size - n_ok, 0)
    return out, n_imputed


def item_descriptives(x: np.ndarray, scale_min: Optional[float] = None,
                      scale_max: Optional[float] = None) -> List[Dict]:
    """문항별 기술통계: 평균·SD·왜도·첨도·최솟값/최댓값·바닥/천장 비율.

    척도 타당화 논문의 'Table 1'이자, 이 도구의 다른 선택을 결정해 주는 근거다:
    - **바닥/천장 효과**: 응답이 척도 양 끝에 몰리면(관례상 >15%) 그 문항은 변별력이
      거의 없다. COSMIN이 별도 측정속성으로 다루며, 요인분석은 이를 알려주지 않는다
      (분산이 줄어든 문항도 적재는 높게 나올 수 있다).
    - **왜도/첨도**: |왜도|>2 또는 |첨도|>7 은 Curran, West & Finch(1996)가 **'중간 정도'**
      비정규로 설정한 조건이다(심한 조건은 3/21). 이 수준에서 ML의 χ² 통계량이 팽창하기
      시작하므로 폴리코릭 전환을 검토할 객관적 근거가 된다(원 논문이 ML을 '부적절'하다고
      한 것은 아니다).

    왜도·첨도는 적률 기반(g1, g2 = 초과첨도)이며 표본분산은 ddof=1을 쓴다.
    scale_min/max가 주어지면 그 값을, 없으면 관측 최솟값/최댓값을 바닥/천장 기준으로 쓴다.
    """
    n_all, p = x.shape
    out: List[Dict] = []
    for i in range(p):
        # 결측이 섞인 행렬(--missing pairwise)에서도 문항별 '응답한 사람'만으로 계산한다.
        # listwise 입력에는 결측이 없으므로 동작이 달라지지 않는다.
        col = x[:, i]
        col = col[np.isfinite(col)]
        n = int(col.size)
        if n == 0:
            out.append({
                "mean": float("nan"), "sd": 0.0, "skew": 0.0, "kurtosis": 0.0,
                "min": float("nan"), "max": float("nan"),
                "floor_prop": 0.0, "ceiling_prop": 0.0, "n_categories": 0,
                "extreme_threshold": floor_ceiling_threshold(0), "n_obs": 0,
            })
            continue
        mu = float(col.mean())
        sd = float(col.std(ddof=1)) if n > 1 else 0.0
        m2 = float(((col - mu) ** 2).mean())
        if m2 > 1e-12:
            skew = float(((col - mu) ** 3).mean() / m2 ** 1.5)
            kurt = float(((col - mu) ** 4).mean() / m2 ** 2 - 3.0)
        else:
            skew = kurt = 0.0     # 상수 문항은 모양이 정의되지 않음
        lo = scale_min if scale_min is not None else float(col.min())
        hi = scale_max if scale_max is not None else float(col.max())
        floor = float(np.mean(np.isclose(col, lo))) if n else 0.0
        ceil = float(np.mean(np.isclose(col, hi))) if n else 0.0
        out.append({
            "mean": mu, "sd": sd, "skew": skew, "kurtosis": kurt,
            "min": float(col.min()), "max": float(col.max()),
            "floor_prop": floor, "ceiling_prop": ceil,
            "n_categories": int(np.unique(col).size),
            "extreme_threshold": floor_ceiling_threshold(int(np.unique(col).size)),
            "n_obs": n,
        })
    return out


# 응답 범주가 이보다 많으면 '리커트 범주'가 아니라 연속형으로 보고 범주표를 만들지 않는다.
CATEGORY_TABLE_MAX = 15
# 선택률이 이 미만인 범주는 '거의 쓰이지 않음'으로 본다(범주 축소 검토 신호).
RARE_CATEGORY_PROP = 0.05


def category_frequencies(x: np.ndarray, names: Sequence[str],
                         scale_min: Optional[float] = None,
                         scale_max: Optional[float] = None) -> Optional[Dict]:
    """문항별 응답 범주 분포와 '죽은/희귀 범주' 진단.

    규제기관(FDA PRO guidance)과 척도 논문이 직접 요구하는 표다. 요인분석은 이 정보를
    전혀 주지 않는다 — 어떤 범주를 **아무도 고르지 않아도** 적재량은 멀쩡하게 나온다.
    실제로 5점 척도에서 2번을 한 명도 고르지 않으면 그 문항은 사실상 4점 척도이며,
    범주를 합치거나(collapse) 문구를 고쳐야 한다는 근거가 된다.

    scale_min/max를 주면 **관측되지 않은 범주까지** 열로 세워 0%로 드러낸다(이게 핵심 —
    관측값만 보면 없는 범주는 표에서 통째로 사라져 눈에 띄지 않는다).

    정수 코드 순서형이 아니거나 범주가 CATEGORY_TABLE_MAX개를 넘으면 None(표 생략).
    """
    n, p = x.shape
    if n == 0 or p == 0:
        return None
    # 결측(NaN)이 섞여 있어도 '응답한 값'만으로 범주표를 만든다(--missing pairwise).
    finite = np.isfinite(x)
    vals = x[finite]
    if vals.size == 0:
        return None
    if np.any(np.abs(vals - np.rint(vals)) > 1e-8):
        return None                     # 연속형 → 범주표가 의미 없음
    # int64로 캐스팅하기 전에 범위를 확인한다. 1e19 같은 값은 조용히 포화(saturate)해
    # 9223372036854775807 이라는 존재하지 않는 '범주'를 100%로 만들어 낸다.
    if float(np.max(np.abs(vals))) > 2 ** 53:
        return None
    xi = np.rint(np.where(finite, x, 0.0)).astype(np.int64)
    observed = np.unique(np.rint(vals).astype(np.int64))
    if observed.size > CATEGORY_TABLE_MAX:
        return None

    if scale_min is not None and scale_max is not None:
        lo, hi = int(round(scale_min)), int(round(scale_max))
        if hi < lo or (hi - lo + 1) > CATEGORY_TABLE_MAX:
            cats = [int(v) for v in observed]
            declared = False
        else:
            cats = list(range(lo, hi + 1))
            declared = True
    else:
        cats = [int(v) for v in observed]
        declared = False

    rows: List[Dict] = []
    n_max = 0
    for i in range(p):
        col = xi[finite[:, i], i]
        n_i = int(col.size)
        n_max = max(n_max, n_i)
        counts = [int(np.sum(col == c)) for c in cats]
        props = [(c / n_i if n_i else 0.0) for c in counts]
        # 선언된 척도 범위를 벗어난 값이 있으면 그 사실을 숨기지 않는다.
        outside = int(n_i - sum(counts))
        rows.append({
            "item": names[i],
            "counts": counts,
            "props": props,
            "n": n_i,
            "unused": [cats[j] for j, c in enumerate(counts) if c == 0],
            "rare": [cats[j] for j, pr in enumerate(props)
                     if 0 < pr < RARE_CATEGORY_PROP],
            "outside_range": outside,
        })
    return {"categories": cats, "declared_range": declared, "n": int(n_max), "items": rows}


# COSMIN 관례의 바닥/천장 기준(총점·다범주 기준). 문항 단위에는 그대로 쓰면 안 된다.
FLOOR_CEILING_BASE = 0.15


def floor_ceiling_threshold(n_categories: int) -> float:
    """문항의 바닥/천장 효과 판정 기준을 응답 범주 수에 맞춰 정한다.

    '끝 범주 응답 >15%'라는 관례는 **총점**(범주가 많은 값)에서 나온 기준이다. 5점
    리커트 문항은 균등하게 답해도 각 끝 범주가 20%라, 15%를 그대로 문항에 적용하면
    정상 문항까지 전부 '바닥/천장 효과'로 잡힌다(거짓 경보).

    그래서 균등응답 기대치(1/C)의 1.5배와 15% 중 **큰 값**을 쓴다:
    5점 → 30%, 7점 → 21.4%, 범주가 많거나 연속형 → 15%로 수렴.
    """
    if n_categories < 2:
        return 1.0          # 상수 문항은 여기서 잡지 않는다(분산 0으로 따로 걸림)
    return max(FLOOR_CEILING_BASE, 1.5 / n_categories)


def alpha_if_deleted(x: np.ndarray, groups: Sequence[int], k: int) -> np.ndarray:
    """문항 i를 뺐을 때 그 문항이 속한 요인의 Cronbach α (p,).

    척도 개발에서 가장 실행에 옮기기 쉬운 숫자다: "이 문항을 지우면 신뢰도가 오르나?"
    현재 α보다 **높아지면** 그 문항은 하위척도를 갉아먹고 있다는 뜻이다.
    요인 내 문항이 3개 미만이면(빼고 나면 2개 미만이라 α 정의 불가) NaN.
    """
    p = x.shape[1]
    groups = np.asarray(list(groups))
    out = np.full(p, np.nan)
    for i in range(p):
        mates = np.where((groups == groups[i]) & (np.arange(p) != i))[0]
        if mates.size < 2:
            continue
        a = cronbach_alpha(x[:, mates])
        if a is not None:
            out[i] = a
    return out


def regression_factor_scores(x: np.ndarray, loadings: np.ndarray, r: np.ndarray,
                             phi: Optional[np.ndarray] = None) -> np.ndarray:
    """Thurstone 회귀법 요인점수 (n, k) — 표준화 응답을 요인에 회귀해 추정한다.

    W = R⁻¹ · S,  F = Z · W.  Z는 열별 표준화(z) 응답, S는 구조행렬(요인-문항 상관;
    직교회전이면 S=Λ, 사교회전이면 S=ΛΦ). SPSS의 '회귀(regression)' 요인점수와 같은 계열.

    합산점수(sum/mean)와 달리 **모든 문항의 적재를 가중치로** 반영하므로, 교차적재나
    적재 크기 차이가 있는 척도에서 요인을 더 충실히 대표한다. 다만 요인해에 의존하고
    표본 특이적이라, 척도 개발 단계에서는 합산점수를 함께 보는 것이 관례다.

    R이 특이하면 유사역행렬(pinv)로 대체한다. 반환 점수는 표준화 스케일(대략 평균0)이다.
    """
    s = loadings if phi is None else loadings @ phi
    w = _safe_inv(r) @ s
    mu = x.mean(axis=0)
    sd = x.std(axis=0, ddof=1)
    # 분산 0인 문항은 z 정의 불가 → 해당 문항 기여를 0으로 두어 NaN 전파를 막는다.
    sd = np.where(sd > 1e-12, sd, np.inf)
    z = (x - mu) / sd
    return z @ w


def reproduced_correlation(loadings: np.ndarray,
                           phi: Optional[np.ndarray] = None) -> np.ndarray:
    """추출된 요인해가 재현하는 상관행렬. 직교: R̂ = ΛΛᵀ, 사교: R̂ = P Φ Pᵀ."""
    if phi is None:
        return loadings @ loadings.T
    return loadings @ phi @ loadings.T


def residual_stats(r: np.ndarray, loadings: np.ndarray,
                   phi: Optional[np.ndarray] = None,
                   threshold: float = 0.05) -> dict:
    """재현 상관행렬의 잔차 적합도 지표.

    RMSR = sqrt(비대각 잔차 제곱 평균), 그리고 |잔차|>threshold 인 비대각
    성분의 비율(비중복 잔차 비율)을 반환한다. 값이 작을수록 적합이 좋다.
    사교회전이면 요인 상관행렬 phi를 넘겨 R̂ = P Φ Pᵀ 로 재현한다.
    """
    resid = r - reproduced_correlation(loadings, phi)
    p = r.shape[0]
    iu = np.triu_indices(p, k=1)
    off = resid[iu]
    if off.size == 0:
        return {"rmsr": 0.0, "n_resid": 0, "n_large": 0, "prop_large": 0.0}
    rmsr = float(np.sqrt(np.mean(off ** 2)))
    n_large = int(np.sum(np.abs(off) > threshold))
    return {
        "rmsr": rmsr,
        "n_resid": int(off.size),
        "n_large": n_large,
        "prop_large": float(n_large / off.size),
        "threshold": threshold,
    }


def extract_with_flags(r: np.ndarray, k: int, extraction: str = "pca") -> tuple:
    """비회전 적재와 수렴 진단을 함께 반환: (loadings, converged, heywood).

    PAF/ML은 **수렴에 실패해도 예외를 던지지 않고** 마지막 반복값을 그대로 돌려준다.
    적재만 받아 쓰면 그 실패가 조용히 흘러가므로, 재표본을 걸러야 하는 부트스트랩과
    집단별 재적합에서는 이 진단을 반드시 함께 봐야 한다.
    PCA는 닫힌 형태라 수렴 개념이 없어 (True, False)를 돌려준다.
    """
    if extraction == "paf":
        res = paf_loadings(r, k)
        return res.loadings, bool(res.converged), bool(res.heywood)
    if extraction == "ml":
        res = ml_factor_analysis(r, k)
        return res.loadings, bool(res.converged), bool(res.heywood)
    if extraction == "pca":
        return component_loadings(r, k), True, False
    raise ValueError("extraction은 'pca', 'paf', 'ml' 중 하나여야 합니다.")


def extract_loadings(r: np.ndarray, k: int, extraction: str = "pca") -> np.ndarray:
    """추출 방식 이름으로 비회전 적재를 뽑는 공용 진입점(pca/paf/ml).

    analyze와 부트스트랩·집단별 재적합이 **같은 코드 경로**를 타게 해, 재표본 해가
    본해와 다른 방식으로 계산되는 조용한 불일치를 막는다.
    """
    return extract_with_flags(r, k, extraction)[0]


def rotate_loadings(loadings: np.ndarray, rotation: str = "varimax") -> tuple:
    """회전 방식 이름으로 (회전적재, Φ) 를 반환. 요인이 1개면 회전하지 않는다."""
    if rotation not in ("varimax", "promax", "none"):
        raise ValueError("rotation은 'varimax', 'promax', 'none' 중 하나여야 합니다.")
    if loadings.shape[1] < 2 or rotation == "none":
        return loadings.copy(), None
    if rotation == "promax":
        return promax(loadings)
    return varimax(loadings), None


def procrustes_align(loadings: np.ndarray, reference: np.ndarray) -> tuple:
    """직교 Procrustes 정렬: ‖Λ·T − Λ_ref‖를 최소화하는 직교 T를 찾아 (Λ·T, T) 반환.

    부트스트랩·집단별 재표본의 요인해는 **요인 순서와 부호가 임의**다(요인 1과 2가 뒤바뀌거나
    부호가 뒤집혀도 수학적으로 같은 해다). 정렬 없이 적재를 그대로 모으면, 실제로는 안정적인
    적재가 '부호가 널뛴다'는 이유로 0을 포함하는 넓은 구간을 갖게 돼 **거짓 불안정**이 보고된다.
    직교행렬은 부호 반전과 열 치환을 모두 포함하므로 이 정렬 하나로 둘 다 해결된다.

    T = U·Vᵀ,  U·S·Vᵀ = SVD(Λᵀ·Λ_ref)  (Schönemann 1966).
    """
    if loadings.shape != reference.shape:
        raise ValueError("Procrustes 정렬에는 같은 모양의 적재행렬이 필요합니다.")
    if loadings.shape[1] < 1:
        return loadings.copy(), np.eye(0)
    u, _, vt = np.linalg.svd(loadings.T @ reference)
    t = u @ vt
    return loadings @ t, t


def tucker_congruence(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """요인별 Tucker 일치계수 φ (k,) — 두 요인해가 '같은 요인'인지 재는 표준 지표.

    φ_j = Σ_i a_ij·b_ij / sqrt(Σ a_ij² · Σ b_ij²)  — 적재 벡터 사이 코사인 유사도.
    피어슨 상관과 달리 평균을 빼지 않으므로 적재의 **크기와 부호 패턴**을 함께 본다.

    해석(Lorenzo-Seva & ten Berge 2006): |φ| ≥ .95 = 사실상 동일한 요인,
    .85~.94 = 상당히 유사(공정), < .85 = 다른 요인으로 봐야 한다.
    한쪽 요인의 적재가 전부 0이면 정의 불가(NaN).
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if a.shape != b.shape:
        raise ValueError("Tucker 일치계수에는 같은 모양의 적재행렬이 필요합니다.")
    num = (a * b).sum(axis=0)
    den = np.sqrt((a ** 2).sum(axis=0) * (b ** 2).sum(axis=0))
    return np.divide(num, den, out=np.full(num.shape, np.nan), where=den > 1e-12)


def match_factors(loadings: np.ndarray, target: np.ndarray) -> tuple:
    """경험 요인을 가설(목표) 요인에 1:1 대응시킨다. 반환 (perm, congruence).

    perm[j] = 가설 요인 j에 대응하는 경험 요인의 열 번호.
    요인 번호는 추출 순서(고유값 크기)로 매겨질 뿐 의미가 없어서, 가설의 '수면의 질'이
    경험해의 F1일 수도 F2일 수도 있다. |Tucker φ| 합이 최대가 되는 대응을 고른다.

    열 수가 7 이하이면 모든 순열을 훑어 최적해를 보장하고, 그보다 크면(실무에서 거의 없다)
    탐욕적으로 큰 φ부터 짝지어 근사한다.
    """
    import itertools
    k = loadings.shape[1]
    m = target.shape[1]
    if k != m:
        raise ValueError("요인 대응에는 열 수가 같은 두 행렬이 필요합니다.")
    # phi[j, c] = 가설 요인 j 와 경험 요인 c 의 일치계수
    phi = np.zeros((m, k))
    for j in range(m):
        col_t = np.repeat(target[:, [j]], k, axis=1)
        phi[j] = tucker_congruence(loadings, col_t)
    score = np.nan_to_num(np.abs(phi), nan=0.0)

    if k <= 7:
        best, best_val = None, -np.inf
        for perm in itertools.permutations(range(k)):
            val = float(sum(score[j, perm[j]] for j in range(m)))
            if val > best_val:
                best_val, best = val, perm
        perm = list(best)
    else:
        # 탐욕법은 유효한 순열을 주지만 최적이 아니다 — 무작위 시험에서 k=8 이면 91%,
        # k=12 면 98%가 최적해를 놓쳤고 손실이 최대 30%였다. 잘못된 대응은 matched_factor·
        # agreement·mismatches·target_congruence를 한꺼번에 오염시키므로 정확히 푼다.
        perm = _hungarian_max(score)
    return perm, np.array([phi[j, perm[j]] for j in range(m)])


def _hungarian_max(score: np.ndarray) -> List[int]:
    """할당문제를 정확히 푼다: Σ score[j, perm[j]] 를 최대화하는 순열(Jonker-Volgenant).

    O(k³) 이며 scipy 없이 동작한다. 최대화는 비용 = −score 로 바꿔 최소화로 푼다.
    """
    cost = -np.asarray(score, dtype=float)
    n_rows, n_cols = cost.shape
    if n_rows != n_cols:
        raise ValueError("할당문제는 정방행렬이어야 합니다.")
    n = n_rows
    INF = float("inf")
    u = np.zeros(n + 1)
    v = np.zeros(n + 1)
    p = np.zeros(n + 1, dtype=int)      # p[j] = j열에 배정된 행
    way = np.zeros(n + 1, dtype=int)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = np.full(n + 1, INF)
        used = np.zeros(n + 1, dtype=bool)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta, j1 = INF, -1
            for j in range(1, n + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1, j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            if j1 < 0:                   # 남은 열이 없음(도달 불가하지만 방어)
                break
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    perm = [0] * n
    for j in range(1, n + 1):
        if p[j]:
            perm[p[j] - 1] = j - 1
    return perm


def congruence_null_reference(x: np.ndarray, sizes: Sequence[int], k: int,
                              reference: np.ndarray, extraction: str = "pca",
                              rotation: str = "varimax", n_rep: int = 200,
                              seed: int = 42, conf: float = 0.95) -> Optional[Dict]:
    """**같은 모집단**에서 이 크기로 집단을 나눴을 때 기대되는 최소 Tucker φ의 분포.

    이게 왜 필요한가: φ의 표집분포는 표본 크기만이 아니라 **공통성 크기와 요인당 문항 수**에
    함께 좌우된다. 그래서 '문항당 3명' 같은 고정 관문으로는 오경보율을 통제할 수 없다 —
    모의실험에서 동일 모집단인데도 적재가 낮은 자료(λ≈.55~.60)에서는 문항당 3명에서
    오경보가 75~89%까지 올라갔다. 어떤 고정 임계값도 이 의존성을 담을 수 없다.

    그래서 임계값을 **자기 자료에서 직접 만든다**: 집단 라벨을 무작위로 섞어(=진짜로 같은
    모집단에서 온) 같은 크기로 다시 나누고, 전체 해에 정렬해 최소 φ를 구하기를 n_rep번
    반복한다. 그 분포의 하위 백분위가 "표본 크기·자료 성질만으로 기대되는 하한"이다.
    관측된 최소 φ가 그 하한보다 낮을 때만 '구조가 다르다'고 말할 수 있다.

    반환 {"p_low": 하위백분위, "median": 중앙값, "n_ok": 성공 반복수, "n_rep": 요청수,
          "level": 하위백분위 수준} 또는 계산 불가 시 None.
    """
    x = np.asarray(x, dtype=float)
    sizes = [int(s) for s in sizes]
    n, p = x.shape
    if len(sizes) < 2 or sum(sizes) > n or k < 1:
        return None
    rng = np.random.default_rng(seed)
    mins: List[float] = []
    for _ in range(int(n_rep)):
        order = rng.permutation(n)
        start = 0
        fits: List[np.ndarray] = []
        ok = True
        for s in sizes:
            idx = order[start:start + s]
            start += s
            xg = x[idx]
            with np.errstate(over="ignore", invalid="ignore"):
                sd = xg.std(axis=0)
            if not np.all(np.isfinite(sd)) or np.any(sd <= 1e-12):
                ok = False
                break
            try:
                rg = correlation_matrix(xg)
                if not np.all(np.isfinite(rg)):
                    ok = False
                    break
                raw_g, conv_g, _ = extract_with_flags(rg, k, extraction)
                if not conv_g:
                    ok = False
                    break
                rot_g, _ = rotate_loadings(raw_g, rotation)
                aligned, _ = procrustes_align(rot_g, reference)
            except (np.linalg.LinAlgError, ValueError, FloatingPointError):
                ok = False
                break
            fits.append(aligned)
        if not ok or len(fits) < 2:
            continue
        vals: List[float] = []
        for a in fits:
            vals.extend(v for v in tucker_congruence(a, reference) if np.isfinite(v))
        for i in range(len(fits)):
            for j in range(i + 1, len(fits)):
                vals.extend(v for v in tucker_congruence(fits[i], fits[j]) if np.isfinite(v))
        if vals:
            mins.append(float(np.min(vals)))
    if len(mins) < 20:          # 분포를 그릴 만큼 성공하지 못함
        return None
    level = (1.0 - conf) * 100.0
    return {"p_low": float(np.percentile(mins, level)),
            "median": float(np.median(mins)),
            "n_ok": len(mins), "n_rep": int(n_rep), "level": float(level)}


@dataclass
class BootstrapResult:
    n_boot: int                     # 요청한 재표본 수
    n_ok: int                       # 실제로 계산에 성공한 재표본 수
    lo: np.ndarray                  # (p, k) 적재 하한
    hi: np.ndarray                  # (p, k) 적재 상한
    mean: np.ndarray                # (p, k) 재표본 평균 적재
    pa_agreement: Optional[float]   # 평행분석이 본해의 k를 지지한 재표본 비율
    k_counts: Dict[int, int]        # 평행분석이 고른 요인 수의 분포
    n_nonconverged: int             # PAF/ML 비수렴으로 제외한 재표본 수
    n_heywood: int                  # Heywood 케이스였던(포함된) 재표본 수
    alpha_ci: List[Optional[tuple]] # 요인별 Cronbach α의 (하한, 상한)
    omega_ci: List[Optional[tuple]] # 요인별 ω의 (하한, 상한)
    conf: float                     # 신뢰수준(예: 0.95)


def bootstrap_stability(x: np.ndarray, k: int, reference: np.ndarray,
                        n_boot: int = 500, seed: int = 42,
                        extraction: str = "pca", rotation: str = "varimax",
                        pa_reference: Optional[np.ndarray] = None,
                        correlation: str = "pearson",
                        conf: float = 0.95,
                        groups: Optional[Sequence[int]] = None) -> BootstrapResult:
    """비모수 부트스트랩으로 요인해의 **재현 가능성**을 추정한다.

    보고서의 다른 모든 숫자(적재·α·ω·요인 수)는 점추정이라 "표본을 다시 뽑아도 같을까"에
    답하지 못한다. 척도 개발 논문에서 리뷰어가 가장 자주 찌르는 지점이자, n<200인 임상
    표본에서 실제로 가장 자주 무너지는 지점이다. 응답자를 복원추출로 재표집해 전 과정을
    다시 돌리고, 다음을 낸다:

    - 문항×요인 적재의 백분위 신뢰구간(직교 Procrustes로 본해에 정렬한 뒤 집계)
    - 요인 수 안정성: 각 재표본에서 평행분석이 고른 k의 분포와 본해 k의 지지율
    - 요인별 Cronbach α · ω 의 백분위 신뢰구간(문항→요인 배정은 본해로 고정)

    요인→문항 배정(groups)을 재표본마다 다시 argmax하지 않고 **본해로 고정**하는 것이
    중요하다. 재표본마다 배정이 흔들리면 α·ω가 서로 다른 문항조합을 재게 되어 구간의
    의미가 사라진다(같은 하위척도의 신뢰도 변동을 재는 것이 목적).

    건너뛰는 재표본: 분산 0 문항, 특이/비유한 상관행렬, 수치 오류, 그리고 **PAF/ML이
    수렴하지 않은 경우**(예외를 던지지 않으므로 명시적으로 걸러낸다). 성공 개수는 n_ok로
    보고한다. Heywood 케이스는 '실패'가 아니라 경계해이므로 **제외하지 않고 포함**하되
    개수(n_heywood)를 함께 돌려주어 해석에 반영할 수 있게 한다.
    반환 구간은 백분위법이며, 편향보정(BCa)은 하지 않는다.
    """
    x = np.asarray(x, dtype=float)
    reference = np.asarray(reference, dtype=float)
    n, p = x.shape
    if reference.shape != (p, k):
        raise ValueError(f"reference 적재행렬의 모양이 ({p}, {k})이 아닙니다: {reference.shape}")
    if n_boot < 1:
        raise ValueError("n_boot는 1 이상이어야 합니다.")
    if not (0.0 < conf < 1.0):
        raise ValueError("conf는 0과 1 사이여야 합니다.")

    if groups is None:
        groups = np.argmax(np.abs(reference), axis=1)
    groups = np.asarray(list(groups), dtype=int)

    rng = np.random.default_rng(seed)
    collected: List[np.ndarray] = []
    alphas: List[List[float]] = []
    omegas: List[List[float]] = []
    k_counts: Dict[int, int] = {}
    n_nonconverged = 0
    n_heywood = 0

    for _ in range(int(n_boot)):
        idx = rng.integers(0, n, n)
        xb = x[idx]
        # 재표본에서 상수가 된 문항이 있으면 상관행렬이 NaN이 된다 → 그 재표본은 버린다.
        with np.errstate(over="ignore", invalid="ignore"):
            sd = xb.std(axis=0)
        if not np.all(np.isfinite(sd)) or np.any(sd <= 1e-12):
            continue
        try:
            if correlation == "polychoric":
                from . import polychoric as _poly
                rb = _poly.polychoric_matrix(xb)
            else:
                rb = correlation_matrix(xb)
            if not np.all(np.isfinite(rb)):
                continue
            raw_b, conv_b, hey_b = extract_with_flags(rb, k, extraction)
            rot_b, phi_b = rotate_loadings(raw_b, rotation)
            aligned, t = procrustes_align(rot_b, reference)
        except (np.linalg.LinAlgError, ValueError, FloatingPointError):
            continue
        if not np.all(np.isfinite(aligned)):
            continue
        # PAF/ML이 수렴하지 않은 재표본의 적재는 '최적화가 도중에 멈춘 값'이라 신뢰구간에
        # 넣을 수 없다. 예외가 나지 않아 조용히 섞여 들어가므로 여기서 명시적으로 뺀다.
        if not conv_b:
            n_nonconverged += 1
            continue
        # Heywood(공통성이 경계에 붙음)는 '실패'가 아니라 경계해라서 버리면 오히려 구간이
        # 한쪽으로 치우친다. 포함하되 몇 개였는지는 보고해 해석에 반영할 수 있게 한다.
        if hey_b:
            n_heywood += 1

        if pa_reference is not None:
            try:
                eig_b = np.clip(np.linalg.eigvalsh(rb)[::-1], 0.0, None)
                kb = retained_by_parallel(eig_b, np.asarray(pa_reference))
            except np.linalg.LinAlgError:
                kb = -1
            if kb >= 0:
                k_counts[kb] = k_counts.get(kb, 0) + 1

        collected.append(aligned)
        # α: 재표본 응답에서 직접 계산(본해 배정 고정).
        ab = alpha_by_group(xb, groups, k)
        alphas.append([np.nan if ab.get(f) is None else float(ab[f]) for f in range(k)])
        # ω: 구조행렬 기준(사교회전이면 Φ도 같은 T로 회전시켜 정렬 상태를 맞춘다).
        if phi_b is not None:
            phi_al = t.T @ phi_b @ t
            struct_b = aligned @ phi_al
        else:
            struct_b = aligned
        ob = omega_by_group(struct_b, groups)
        omegas.append([np.nan if ob.get(f) is None else float(ob[f]) for f in range(k)])

    n_ok = len(collected)
    alpha_q = (1.0 - conf) / 2.0 * 100.0
    lo_q, hi_q = alpha_q, 100.0 - alpha_q
    if n_ok >= 2:
        stack = np.stack(collected)                     # (n_ok, p, k)
        lo = np.percentile(stack, lo_q, axis=0)
        hi = np.percentile(stack, hi_q, axis=0)
        mean = stack.mean(axis=0)
    else:
        lo = hi = mean = np.full((p, k), np.nan)

    def _ci_list(rows: List[List[float]]) -> List[Optional[tuple]]:
        if n_ok < 2:
            return [None] * k
        arr = np.asarray(rows, dtype=float)             # (n_ok, k)
        out: List[Optional[tuple]] = []
        for f in range(k):
            col = arr[:, f]
            col = col[np.isfinite(col)]
            # 재표본의 절반 미만에서만 정의된 계수는 구간이 왜곡되므로 내지 않는다.
            if col.size < max(2, n_ok // 2):
                out.append(None)
                continue
            out.append((float(np.percentile(col, lo_q)), float(np.percentile(col, hi_q))))
        return out

    total_pa = sum(k_counts.values())
    pa_agreement = (k_counts.get(k, 0) / total_pa) if total_pa else None

    return BootstrapResult(
        n_boot=int(n_boot), n_ok=n_ok, lo=lo, hi=hi, mean=mean,
        pa_agreement=pa_agreement, k_counts=k_counts,
        n_nonconverged=n_nonconverged, n_heywood=n_heywood,
        alpha_ci=_ci_list(alphas), omega_ci=_ci_list(omegas), conf=float(conf),
    )


def omega_by_group(loadings: np.ndarray, groups: Sequence[int]) -> dict:
    """요인별 McDonald's ω(합성신뢰도) 근사값을 적재량으로부터 계산.

    ω_f = (Σλ_i)² / [(Σλ_i)² + Σ(1 − λ_i²)],  i ∈ 요인 f 에 주적재된 문항.
    λ_i 는 문항 i의 자기 요인(f) 적재. 분자·분모를 모두 자기 요인 적재로 맞춰
    (단일차원 congeneric 가정) 계수를 일관되게 둔다.

    주의: 여기서 λ는 '주성분(PCA)' 적재이므로 이 ω는 근사값이며, PCA가 공통성을
    다소 크게 추정하는 탓에 공통요인(PAF/ML) 기반 ω나 Cronbach α보다 **높게(낙관적)**
    나오는 경향이 있다(RMSR과는 반대 방향). 요인 내 문항이 2개 미만이면 정의 불가(None).
    """
    groups = np.asarray(list(groups))
    k = loadings.shape[1]
    out: Dict[int, Optional[float]] = {}
    for f in range(k):
        idx = np.where(groups == f)[0]
        if idx.size < 2:   # 1문항 이하 요인은 합성신뢰도 정의 불가
            out[f] = None
            continue
        lam = loadings[idx, f]
        sum_lam_sq = float(lam.sum()) ** 2
        resid_var = float(np.sum(1.0 - lam ** 2))  # 자기 요인 기준 오차분산
        denom = sum_lam_sq + resid_var
        out[f] = float(sum_lam_sq / denom) if denom > 0 else None
    return out
