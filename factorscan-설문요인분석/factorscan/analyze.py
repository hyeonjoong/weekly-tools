"""전체 분석 오케스트레이션: 전처리된 데이터 -> 결과 딕셔너리."""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from . import efa
from .dataio import Prepared

# 진단 임계값(관례적 기준)
MSA_POOR = 0.5          # 문항별 MSA 하한
COMMUNALITY_LOW = 0.3   # 낮은 공통성
ITEM_TOTAL_LOW = 0.3    # 낮은 문항-총점 상관


def _factorability_flags(kmo_overall: Optional[float], bartlett_p: Optional[float]) -> List[str]:
    notes = []
    if kmo_overall is not None:
        if kmo_overall < 0.5:
            notes.append("KMO<0.50: 요인분석에 부적합(수용 불가) 수준입니다.")
        elif kmo_overall < 0.6:
            notes.append("KMO 0.50~0.60: 요인분석 적합성이 낮습니다(주의).")
    if bartlett_p is not None and bartlett_p >= 0.05:
        notes.append("Bartlett p>=0.05: 상관행렬이 단위행렬과 다르지 않아 요인분석 근거가 약합니다.")
    return notes


def analyze(prep: Prepared,
            n_factors: Optional[int] = None,
            rotation: str = "varimax",
            parallel_iter: int = 100,
            seed: int = 42,
            min_loading: float = 0.40) -> Dict:
    """전처리된 데이터에 EFA/타당도 진단을 수행하고 결과 딕셔너리를 반환."""
    names = prep.names
    x = prep.matrix
    n, p = x.shape

    result: Dict = {
        "items": names,
        "n_items": p,
        "n_total": prep.n_total,
        "n_used": prep.n_used,
        "n_dropped": prep.n_dropped,
        "warnings": [],
        "notes": [],
    }

    if p < 2:
        raise ValueError("요인분석에는 최소 2개 이상의 문항(열)이 필요합니다.")
    if n < 3:
        raise ValueError(f"응답자 수가 너무 적습니다(n={n}). 최소 몇 배수의 표본이 필요합니다.")

    # 결측 제거 후 분산이 0이 된 열은 상관행렬을 NaN으로 오염시키므로 명확히 막는다.
    zero_var = [names[i] for i in range(p) if np.std(x[:, i]) == 0]
    if zero_var:
        raise ValueError(
            f"결측 제거 후 값이 모두 동일해진 문항이 있습니다: {', '.join(zero_var)}. "
            f"해당 문항을 제외하거나 결측 패턴을 확인하세요.")

    # 표본 크기 경고(관례: 문항당 5~10명, 최소 표본 등)
    if n < p:
        result["warnings"].append(
            f"응답자 수(n={n})가 문항 수(p={p})보다 적어 상관행렬이 특이합니다 — 결과 신뢰 불가.")
    elif n < 5 * p:
        result["warnings"].append(
            f"표본이 작습니다(n={n}, 문항당 {n / p:.1f}명). 문항당 5~10명 이상을 권장합니다.")

    r = efa.correlation_matrix(x)
    result["correlation_matrix"] = r
    pos_def = efa.is_positive_definite(r)

    # --- 요인분석 적합성: Bartlett & KMO (역행렬/행렬식 필요) ---
    if pos_def:
        try:
            b = efa.bartlett_sphericity(r, n)
            result["bartlett"] = {"chi_square": b.chi_square, "df": b.df, "p_value": b.p_value}
        except (ValueError, np.linalg.LinAlgError) as exc:
            result["bartlett"] = None
            result["warnings"].append(f"Bartlett 검정 생략: {exc}")
        try:
            k = efa.kmo(r)
            result["kmo"] = {"overall": k.overall, "per_item": k.per_item.tolist()}
        except np.linalg.LinAlgError:
            result["kmo"] = None
            result["warnings"].append("KMO 계산 생략: 상관행렬 역행렬을 구할 수 없습니다.")
    else:
        result["bartlett"] = None
        result["kmo"] = None
        result["warnings"].append(
            "상관행렬이 양의 정부호가 아닙니다(특이행렬) — KMO/Bartlett 생략. "
            "표본이 문항 수보다 충분히 큰지, 중복/완전상관 문항이 없는지 확인하세요.")

    # --- 고유값 / 설명분산 / Kaiser / 평행분석 ---
    eig = efa.eigen_summary(r)
    result["eigenvalues"] = eig.values.tolist()
    result["prop_variance"] = eig.prop_variance.tolist()
    result["cum_variance"] = eig.cum_variance.tolist()
    result["kaiser_k"] = eig.kaiser_k

    pa_k = None
    if parallel_iter and parallel_iter > 0:
        pa = efa.parallel_analysis(n, p, iters=parallel_iter, seed=seed)
        result["parallel_eigenvalues"] = pa.tolist()
        pa_k = int(np.sum(eig.values > pa))
        result["parallel_k"] = pa_k

    # --- 유지 요인 수 결정 ---
    # 기본값: 평행분석(Horn)을 우선한다. Kaiser 기준(고유값>1)은 요인을 과대추정하는
    # 경향이 강하므로, 평행분석이 있으면 그 결과를, 없으면 Kaiser를 사용한다.
    if n_factors is not None:
        if n_factors < 1 or n_factors > p:
            raise ValueError(f"n_factors는 1..{p} 범위여야 합니다.")
        k = n_factors
        result["k_source"] = "user"
    elif pa_k is not None:
        k = max(1, min(pa_k, p - 1))
        result["k_source"] = "parallel"
    else:
        k = max(1, min(eig.kaiser_k, p - 1))
        result["k_source"] = "kaiser"
    result["n_factors"] = k
    result["min_loading"] = min_loading

    # 자동 결정일 때만, Kaiser와 평행분석이 어긋나면 사용자에게 알린다(과대추정 위험).
    if n_factors is None and pa_k is not None and pa_k != eig.kaiser_k:
        result["notes"].append(
            f"요인 수 판정 불일치: Kaiser={eig.kaiser_k}개 vs 평행분석={pa_k}개. "
            f"Kaiser는 과대추정 경향이 있어 평행분석을 우선하여 {k}개를 적용했습니다"
            + ("(최소 1개 유지)." if pa_k < 1 else ".")
            + " 필요하면 --n-factors 로 직접 지정하세요.")

    # --- 적재량 / 회전 / 공통성 ---
    raw = efa.component_loadings(r, k)
    rotated = raw
    applied_rotation = "none"
    if rotation == "varimax" and k >= 2:
        rotated = efa.varimax(raw)
        applied_rotation = "varimax"
    rotated = efa.apply_sign_convention(rotated)
    result["rotation"] = applied_rotation
    result["loadings"] = rotated.tolist()
    comm = efa.communalities(rotated)
    result["communalities"] = comm.tolist()

    # 회전 후 요인별 설명분산(적재제곱합)
    ss_loadings = (rotated ** 2).sum(axis=0)
    result["ss_loadings"] = ss_loadings.tolist()
    result["ss_prop_variance"] = (ss_loadings / p).tolist()

    # --- 수정된 문항-총점 상관 ---
    it = efa.corrected_item_total(x)
    result["item_total"] = it.tolist()

    # --- 문항별 진단 플래그 ---
    msa = result["kmo"]["per_item"] if result.get("kmo") else [None] * p
    flags: List[Dict] = []
    for i, name in enumerate(names):
        row = rotated[i]
        abs_row = np.abs(row)
        top = int(np.argmax(abs_row))
        loads = np.where(abs_row >= min_loading)[0]
        problems = []
        if msa[i] is not None and msa[i] < MSA_POOR:
            problems.append(f"MSA<{MSA_POOR}")
        if comm[i] < COMMUNALITY_LOW:
            problems.append(f"공통성<{COMMUNALITY_LOW}")
        if not np.isnan(it[i]) and it[i] < ITEM_TOTAL_LOW:
            problems.append(f"문항-총점<{ITEM_TOTAL_LOW}")
        if abs_row.max() < min_loading:
            problems.append(f"주적재<{min_loading}")
        elif len(loads) >= 2:
            problems.append("교차적재")
        flags.append({
            "item": name,
            "primary_factor": top + 1,
            "primary_loading": float(row[top]),
            "problems": problems,
        })
    result["item_flags"] = flags

    result["notes"].extend(
        _factorability_flags(
            result["kmo"]["overall"] if result.get("kmo") else None,
            result["bartlett"]["p_value"] if result.get("bartlett") else None,
        )
    )
    return result
