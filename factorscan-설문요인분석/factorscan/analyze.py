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
        "extraction": "principal_component",  # 추출 방식(SPSS 기본과 동일)
        "warnings": [],
        "notes": [],
    }

    # 숫자로 못 읽어 결측처리된 값이 있으면(오타·문자 혼입·이상한 구분자) 알린다.
    coercion = getattr(prep, "coercion", {}) or {}
    if coercion:
        detail = ", ".join(f"{k}({v}개)" for k, v in coercion.items())
        result["warnings"].append(
            f"숫자로 해석할 수 없어 결측처리된 값이 있습니다: {detail}. "
            f"오타나 잘못된 구분자가 아닌지 확인하세요.")

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

    # 상관행렬 행렬식(다중공선성 진단): 0에 가까우면 문항 간 과도한 중복/완전상관 신호.
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        sign_r, logdet_r = np.linalg.slogdet(r)
    det_r = float(np.exp(logdet_r)) if sign_r > 0 else 0.0
    result["r_determinant"] = det_r
    if 0.0 < det_r < 1e-5:
        result["warnings"].append(
            f"상관행렬 행렬식이 매우 작습니다(det={det_r:.2e}). 문항 간 다중공선성(거의 중복되는 "
            f"문항)이 의심되니 중복 문항을 확인하세요.")

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

    # 평행분석 관련 키는 생략/불가 시에도 항상 존재(null)하도록 초기화 — JSON 스키마 안정성.
    pa_k = None
    result["parallel_eigenvalues"] = None
    result["parallel_k"] = None
    if parallel_iter and parallel_iter > 0:
        if n <= p:
            # n<=p 이면 무작위 상관행렬도 특이(0/음수 고유값 포함)해 기준선이 왜곡된다.
            result["warnings"].append(
                f"평행분석 생략: 응답자 수(n={n})가 문항 수(p={p}) 이하여서 "
                f"무작위 기준선이 왜곡됩니다.")
        else:
            pa = efa.parallel_analysis(n, p, iters=parallel_iter, seed=seed)
            result["parallel_eigenvalues"] = pa.tolist()
            # 첫 교차(관측<=무작위)에서 멈추는 표준 규칙으로 선행 요인 수만 센다.
            pa_k = efa.retained_by_parallel(eig.values, pa)
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
    phi = None                      # 요인 상관행렬(사교회전/추정)
    if k >= 2 and rotation == "varimax":
        rotated = efa.varimax(raw)
        applied_rotation = "varimax"
    elif k >= 2 and rotation == "promax":
        rotated, phi = efa.promax(raw)
        applied_rotation = "promax"
    else:
        rotated = raw
        applied_rotation = "none"

    # 부호 정렬(관례). 사교회전이면 요인 상관행렬 phi의 부호도 함께 뒤집는다.
    signs = efa.sign_convention_signs(rotated)
    rotated = rotated * signs
    if phi is not None:
        phi = phi * np.outer(signs, signs)

    result["rotation"] = applied_rotation
    if applied_rotation == "none" and k >= 2:
        result["notes"].append(
            "비회전(rotation=none) 상태에서는 첫 성분에 문항이 몰려 교차적재 플래그·요인별 ω·"
            "요인총점이 왜곡될 수 있습니다. 문항의 요인 소속 해석에는 Varimax 회전을 권장합니다.")
    result["loadings"] = rotated.tolist()

    # 구조행렬 S = P Φ (직교회전이면 Φ=I 이므로 S=P). 요인-문항 상관이며 |성분|≤1.
    structure = rotated @ phi if phi is not None else rotated

    # 공통성·설명분산: 직교회전은 적재제곱합, 사교(promax)회전은 구조행렬 S=PΦ 사용.
    if applied_rotation == "promax" and phi is not None:
        comm = np.einsum("ij,ij->i", rotated, structure)  # diag(P Φ Pᵀ)
        ss_loadings = (structure ** 2).sum(axis=0)
        result["notes"].append(
            "사교(promax)회전이 적용되었습니다. 요인이 서로 상관되어 요인별 설명분산이 겹치므로 "
            "합이 총분산과 일치하지 않을 수 있습니다(구조행렬 기준).")
    else:
        comm = efa.communalities(rotated)
        ss_loadings = (rotated ** 2).sum(axis=0)
    result["communalities"] = comm.tolist()
    result["ss_loadings"] = ss_loadings.tolist()
    result["ss_prop_variance"] = (ss_loadings / p).tolist()

    # 요인 상관 진단: 어떤 회전이든 promax로 요인 상관을 추정해 사교회전 필요성을 안내.
    if k >= 2:
        if phi is not None:
            phi_est = phi
        else:
            est_pattern, est_phi = efa.promax(raw)
            est_phi = est_phi * np.outer(efa.sign_convention_signs(est_pattern),
                                         efa.sign_convention_signs(est_pattern))
            phi_est = est_phi
        result["factor_correlation"] = np.asarray(phi_est).tolist()
        off = phi_est[np.triu_indices_from(phi_est, k=1)]
        max_abs = float(np.max(np.abs(off))) if off.size else 0.0
        result["factor_correlation_max"] = max_abs
        if max_abs >= 0.32 and applied_rotation != "promax":
            result["notes"].append(
                f"요인 간 상관 추정치가 큽니다(|r|최대={max_abs:.2f}, promax 기준). 요인들이 서로 "
                f"상관되어 있을 수 있으니 사교회전(--rotation promax)도 함께 검토하세요.")
    else:
        result["factor_correlation"] = None
        result["factor_correlation_max"] = None

    # 각 문항의 주적재 요인(0-based) — 하위척도 그룹핑에 사용
    groups = np.argmax(np.abs(rotated), axis=1)

    # --- 수정된 문항-총점 상관: 전체 척도 기준 + 소속 요인(하위척도) 기준 ---
    it_overall = efa.corrected_item_total(x)
    it_factor = efa.corrected_item_total_by_group(x, groups)
    result["item_total_overall"] = it_overall.tolist()   # 전체 문항 합 기준
    result["item_total_by_factor"] = it_factor.tolist()  # 소속 요인 내 합 기준(다차원 권장)
    # 다차원 척도에서는 소속 요인 기준을 진단·표시에 쓴다(k=1이면 둘이 동일).
    it = it_factor

    # --- 모형 적합도(재현 상관행렬 잔차) + 요인별 신뢰도(McDonald ω) ---
    # 사교(promax) 회전이면 R̂ = P Φ Pᵀ 로 재현해야 RMSR이 올바르다.
    resid_phi = phi if applied_rotation == "promax" else None
    result["residual"] = efa.residual_stats(r, rotated, phi=resid_phi)
    # ω는 구조행렬(요인-문항 상관, |값|≤1)로 계산 → 사교회전에서도 [0,1] 유계 보장.
    # 직교회전이면 structure==loadings 이므로 값이 동일하다.
    omega = efa.omega_by_group(structure, groups)
    result["omega"] = [omega.get(f) for f in range(k)]

    # --- 문항별 진단 플래그 ---
    msa = result["kmo"]["per_item"] if result.get("kmo") else [None] * p
    flags: List[Dict] = []
    for i, name in enumerate(names):
        row = rotated[i]
        abs_row = np.abs(row)
        top = int(groups[i])
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
            "msa": float(msa[i]) if msa[i] is not None else None,
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
