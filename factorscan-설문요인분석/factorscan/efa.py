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
from typing import List, Optional

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
    total = w.sum()
    prop = w / total if total > 0 else np.zeros_like(w)
    return Eigen(
        values=w,
        prop_variance=prop,
        cum_variance=np.cumsum(prop),
        kaiser_k=int(np.sum(w > 1.0)),
    )


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


def apply_sign_convention(loadings: np.ndarray) -> np.ndarray:
    """요인 부호를 관례에 맞게 정렬: 각 요인에서 절댓값이 가장 큰 적재가 양수가 되도록.

    (요인 부호는 수학적으로 임의이므로 해석 편의를 위해 정렬한다.)
    """
    out = loadings.copy()
    for j in range(out.shape[1]):
        col = out[:, j]
        lead = col[np.argmax(np.abs(col))]
        if lead < 0:
            out[:, j] = -col
    return out


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
