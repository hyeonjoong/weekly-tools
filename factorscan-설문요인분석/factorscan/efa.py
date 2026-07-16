"""탐색적 요인분석(EFA)과 요인분석 적합성 진단의 수치 엔진.

- Bartlett 구형성 검정 / KMO(전체 + 문항별 MSA)
- 상관행렬 고유값, 설명분산, Kaiser 기준, 평행분석(Horn)
- 주성분 추출 적재량 + Varimax(Kaiser 정규화) 회전, 공통성
- 수정된 문항-총점 상관(corrected item-total correlation)

추출 방식은 주성분(principal component)으로, SPSS의 기본 추출 방식과 동일하다.
모든 입력은 결측 제거가 끝난 (관측자 x 문항) 실수 행렬을 가정한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np

from .stats import chi2_sf


def correlation_matrix(x: np.ndarray) -> np.ndarray:
    """열(문항) 간 피어슨 상관행렬. x: (n, p)."""
    return np.corrcoef(x, rowvar=False)


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
    inv = np.linalg.inv(coef.T @ coef)
    coef = coef @ np.diag(np.sqrt(np.diag(inv)))
    pattern = (vn @ coef) * h[:, None]           # 정규화 복원
    cinv = np.linalg.inv(coef)
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
