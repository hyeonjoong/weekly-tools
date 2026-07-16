"""단일요인 분석(주축분해)과 McDonald's ω — 표준 라이브러리만 사용.

Cronbach α는 '본질적 타우동등성(essential tau-equivalence)' — 즉 모든 문항이 잠재
특성을 **동일한 크기로** 반영한다는 가정 — 위에서만 신뢰도의 불편추정치다. 현실의
설문에서 이 가정은 거의 성립하지 않으며, 그럴 때 α는 신뢰도를 **과소추정**한다.
McDonald's ω(congeneric 모형: 문항별 부하량이 달라도 됨)는 이 가정을 완화한 추정치로,
심리측정 문헌에서 α 대신/α와 함께 보고하도록 권고된다(Revelle & Zinbarg 2009;
Dunn, Baguley & Brunsden 2014; Hayes & Coutts 2020).

구현
- 문항 상관행렬 R 에 **반복 주축분해(iterated principal axis factoring, PAF)** 로
  단일요인을 적합한다: 대각을 공통성(communality) h² 로 바꾼 축소상관행렬의
  최대 고유쌍을 구해 부하량 λ = √λ₁·v 를 얻고, h² ← λ² 로 갱신하기를 수렴까지 반복.
- 최대 고유쌍은 **이동(shift) 멱승법**으로 구한다. 축소상관행렬은 음의 고유값을
  가질 수 있어 그대로 멱승법을 쓰면 '절대값이 가장 큰' 음의 고유값으로 수렴할 수
  있다. Gershgorin 하한으로 c 를 잡아 A+cI 를 준양정치로 만든 뒤 멱승법을 돌리고
  λ₁ = μ₁ − c 로 되돌린다(고유벡터는 이동에 불변).
- ω_total = (Σλ)² / ((Σλ)² + Σψ)  — 공분산 단위(α와 같은 단위)에서 계산한다.
    λ_i(공분산) = 표준화부하량_i × SD_i,   ψ_i = Var_i − λ_i²
  분모는 모형함의(model-implied) 총점 분산이다.

정확도/안전
- 고유쌍은 numpy.linalg.eigh 를 오라클로 대조한다(테스트에서만 numpy 사용).
- 수렴에 실패하거나 잔차 ‖Av−λv‖ 가 크면 **틀린 값을 내놓지 않고 None** 을 반환한다.
- Heywood case(h²>1: 모형 부적합 신호)는 클램프하고 플래그로 노출한다.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

from . import stats

# PAF 반복 상한/수렴 기준.
_PAF_MAXIT = 300
_PAF_TOL = 1e-9
# 멱승법 반복 상한/수렴 기준.
_POW_MAXIT = 20000
_POW_TOL = 1e-13
# Heywood case 방지용 공통성 상한(1.0 이면 ψ=0 이 되어 ω 가 1로 붙어버린다).
_H2_MAX = 0.998


def correlation_matrix(columns: Sequence[Sequence[float]]) -> Optional[List[List[float]]]:
    """문항 상관행렬. 분산 0 문항이 있거나 문항<2면 None."""
    k = len(columns)
    if k < 2:
        return None
    R = [[1.0] * k for _ in range(k)]
    for i in range(k):
        for j in range(i + 1, k):
            r = stats.pearson(columns[i], columns[j])
            if r is None:
                return None
            R[i][j] = R[j][i] = r
    return R


def _inverse(m: List[List[float]]) -> Optional[List[List[float]]]:
    """Gauss-Jordan 역행렬(부분 피벗팅). 특이행렬이면 None."""
    n = len(m)
    a = [list(row) + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(m)]
    for col in range(n):
        # 부분 피벗팅: 절대값이 가장 큰 행을 피벗으로.
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-12:
            return None
        a[col], a[piv] = a[piv], a[col]
        pv = a[col][col]
        a[col] = [x / pv for x in a[col]]
        for r in range(n):
            if r == col:
                continue
            f = a[r][col]
            if f == 0.0:
                continue
            a[r] = [x - f * y for x, y in zip(a[r], a[col])]
    return [row[n:] for row in a]


def _dominant_eig(A: List[List[float]]):
    """대칭행렬 A의 **대수적으로 가장 큰** 고유값과 고유벡터 (λ₁, v).

    축소상관행렬은 음의 고유값을 가질 수 있으므로, Gershgorin 하한으로 이동시켜
    준양정치로 만든 뒤 멱승법을 쓴다. 수렴 실패 시 None.
    """
    n = len(A)
    if n == 0:
        return None
    # Gershgorin: 모든 고유값 ≥ min_i (A_ii − Σ_{j≠i}|A_ij|)
    lo = min(
        A[i][i] - sum(abs(A[i][j]) for j in range(n) if j != i) for i in range(n)
    )
    c = max(0.0, -lo) + 1e-9  # A + cI 의 고유값이 모두 ≥ 0 이 되도록
    # 시작 벡터: 행합(요인 부하와 정렬되기 쉬움) — 0 이면 균등벡터로 대체.
    v = [sum(A[i]) for i in range(n)]
    nrm = math.sqrt(sum(x * x for x in v))
    if nrm < 1e-12:
        v = [1.0] * n
        nrm = math.sqrt(float(n))
    v = [x / nrm for x in v]

    mu_prev = None
    for _ in range(_POW_MAXIT):
        # w = (A + cI) v
        w = [sum(A[i][j] * v[j] for j in range(n)) + c * v[i] for i in range(n)]
        nrm = math.sqrt(sum(x * x for x in w))
        if not math.isfinite(nrm) or nrm < 1e-300:
            return None
        v = [x / nrm for x in w]
        # Rayleigh 몫으로 수렴 판정(고유값 기준이 벡터 부호진동에 강건).
        mu = nrm  # (A+cI) 가 준양정치이므로 ‖w‖ 가 곧 μ 로 수렴
        if mu_prev is not None and abs(mu - mu_prev) <= _POW_TOL * max(1.0, abs(mu)):
            lam = mu - c
            # 안전장치: 잔차가 크면 틀린 값을 내보내지 않는다.
            resid = math.sqrt(
                sum(
                    (sum(A[i][j] * v[j] for j in range(n)) - lam * v[i]) ** 2
                    for i in range(n)
                )
            )
            if resid > 1e-6 * max(1.0, abs(lam)):
                return None
            return lam, v
        mu_prev = mu
    return None


def _initial_communalities(R: List[List[float]]) -> List[float]:
    """초기 공통성 추정 = SMC(다중상관제곱). 역행렬이 없으면 최대 |r| 로 대체."""
    k = len(R)
    inv = _inverse(R)
    if inv is not None:
        h2 = []
        ok = True
        for i in range(k):
            d = inv[i][i]
            if d <= 0 or not math.isfinite(d):
                ok = False
                break
            h2.append(min(max(1.0 - 1.0 / d, 0.0), _H2_MAX))
        if ok:
            return h2
    return [
        min(max(max(abs(R[i][j]) for j in range(k) if j != i), 0.0), _H2_MAX)
        for i in range(k)
    ]


def single_factor_loadings(R: List[List[float]]) -> Optional[Dict[str, object]]:
    """반복 주축분해(PAF)로 단일요인 표준화 부하량을 구한다.

    반환 {"loadings": [...], "heywood": bool} 또는 수렴 실패 시 None.
    """
    k = len(R)
    if k < 3:
        # 단일요인 모형은 문항 3개 미만에서 식별되지 않는다(자유도 부족).
        return None
    h2 = _initial_communalities(R)
    heywood = False
    loadings: Optional[List[float]] = None
    converged = False
    for _ in range(_PAF_MAXIT):
        A = [list(row) for row in R]
        for i in range(k):
            A[i][i] = h2[i]
        eig = _dominant_eig(A)
        if eig is None:
            return None
        lam1, v = eig
        if lam1 <= 0 or not math.isfinite(lam1):
            # 공통분산이 없다 — 단일요인 모형이 성립하지 않음.
            return None
        s = math.sqrt(lam1)
        loadings = [s * x for x in v]
        # 고유벡터의 부호는 임의 — 부하량 합이 양수가 되도록 고정.
        if sum(loadings) < 0:
            loadings = [-x for x in loadings]
        new_h2 = []
        for x in loadings:
            hv = x * x
            if hv > 1.0:
                heywood = True
            new_h2.append(min(hv, _H2_MAX))
        delta = max(abs(a - b) for a, b in zip(h2, new_h2))
        h2 = new_h2
        if delta < _PAF_TOL:
            converged = True
            break
    if loadings is None or not converged:
        return None
    if not all(math.isfinite(x) for x in loadings):
        return None
    return {"loadings": loadings, "heywood": heywood}


def omega_total(columns: Sequence[Sequence[float]]) -> Optional[Dict[str, object]]:
    """McDonald's ω_total (단일요인 congeneric 모형, 공분산 단위).

    columns[i] = 문항 i 의 응답(완전응답자만, 응답자 순서 동일, 역문항 재코딩 후).

    ω = (Σλ)² / ((Σλ)² + Σψ),  λ_i = 표준화부하량_i × SD_i,  ψ_i = Var_i − λ_i²

    문항 3개 미만·응답자 3명 미만·분산 0 문항·수렴 실패 시 None(틀린 값 대신 무보고).
    """
    k = len(columns)
    if k < 3:
        return None
    n = len(columns[0])
    if n < 3 or any(len(c) != n for c in columns):
        return None
    sds = []
    for col in columns:
        sd = stats.stdev(col)
        if sd is None or sd <= 0 or not math.isfinite(sd):
            return None
        sds.append(sd)
    R = correlation_matrix(columns)
    if R is None:
        return None
    fit = single_factor_loadings(R)
    if fit is None:
        return None
    std_loadings: List[float] = fit["loadings"]  # type: ignore[assignment]
    heywood = bool(fit["heywood"])

    lam_cov = [l * sd for l, sd in zip(std_loadings, sds)]
    sum_lam = sum(lam_cov)
    psi_sum = 0.0
    for lc, sd in zip(lam_cov, sds):
        psi = sd * sd - lc * lc
        if psi < 0.0:
            # Heywood: 모형함의 공통분산이 관측분산을 넘음 → 0 으로 클램프.
            heywood = True
            psi = 0.0
        psi_sum += psi
    denom = sum_lam * sum_lam + psi_sum
    if denom <= 0 or not math.isfinite(denom):
        return None
    omega = (sum_lam * sum_lam) / denom
    if not math.isfinite(omega):
        return None
    omega = min(max(omega, 0.0), 1.0)
    return {
        "omega": omega,
        "loadings": std_loadings,
        "heywood": heywood,
        "n_complete": n,
        "n_items": k,
    }
