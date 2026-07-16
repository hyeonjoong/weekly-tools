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
    chi = -((n - 1) - (2 * p + 5) / 6.0) * logdet
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


def paf_loadings(r: np.ndarray, k: int, max_iter: int = 100,
                 tol: float = 1e-6) -> PAFResult:
    """주축분해(반복 Principal Axis Factoring)로 공통요인 적재를 추출한다.

    주성분(PCA)이 관측분산 전체를 쓰는 것과 달리, PAF는 대각을 공통성 추정치로 바꾼
    '축소상관행렬'을 분해해 **공통분산만** 모형화한다(SPSS/R의 principal-axis와 동일 계열).
    절차: 대각 ← SMC → 상위 k 고유분해 → 공통성 갱신 → 수렴까지 반복.

    반환 PAFResult. Heywood 케이스(공통성>1)는 1로 절단하고 flag로 알린다.
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


def item_descriptives(x: np.ndarray, scale_min: Optional[float] = None,
                      scale_max: Optional[float] = None) -> List[Dict]:
    """문항별 기술통계: 평균·SD·왜도·첨도·최솟값/최댓값·바닥/천장 비율.

    척도 타당화 논문의 'Table 1'이자, 이 도구의 다른 선택을 결정해 주는 근거다:
    - **바닥/천장 효과**: 응답이 척도 양 끝에 몰리면(관례상 >15%) 그 문항은 변별력이
      거의 없다. COSMIN이 별도 측정속성으로 다루며, 요인분석은 이를 알려주지 않는다
      (분산이 줄어든 문항도 적재는 높게 나올 수 있다).
    - **왜도/첨도**: |왜도|>2 또는 |첨도|>7 이면(Curran, West & Finch 1996) 다변량
      정규성 가정이 깨져 ML·피어슨이 부적절해진다 → 폴리코릭 전환의 객관적 근거.

    왜도·첨도는 적률 기반(g1, g2 = 초과첨도)이며 표본분산은 ddof=1을 쓴다.
    scale_min/max가 주어지면 그 값을, 없으면 관측 최솟값/최댓값을 바닥/천장 기준으로 쓴다.
    """
    n, p = x.shape
    out: List[Dict] = []
    for i in range(p):
        col = x[:, i]
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
        })
    return out


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
