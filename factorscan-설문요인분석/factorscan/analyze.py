"""전체 분석 오케스트레이션: 전처리된 데이터 -> 결과 딕셔너리."""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from . import efa, polychoric
from .dataio import Prepared, listwise_bias_check, missing_report

# 진단 임계값(관례적 기준)
MSA_POOR = 0.5          # 문항별 MSA 하한
COMMUNALITY_LOW = 0.3   # 낮은 공통성
ITEM_TOTAL_LOW = 0.3    # 낮은 문항-총점 상관
DROP_PROP_WARN = 0.10   # listwise 삭제 비율 경고선(10%)
ITEM_MISS_WARN = 0.05   # 문항별 결측 비율 경고선(5%)
MCAR_D_WARN = 0.2       # listwise 편향 점검의 |Cohen's d| 경고선(작은 효과)


def _missing_diagnostics(prep: Prepared, names: List[str], result: Dict) -> None:
    """결측 구조를 진단해 result에 싣고, 손실이 크거나 편향 신호가 있으면 경고한다.

    listwise 삭제는 '한 문항만 빠져도' 응답자를 통째로 버린다. 임상 설문에서는 이 손실이
    조용히 표본의 절반을 날리기도 하므로, 얼마나·어느 문항 때문에 잃었는지와 그 삭제가
    분포를 바꾸는지(MCAR 위배 신호)를 함께 보고한다.
    """
    raw = getattr(prep, "raw", None)
    if raw is None or raw.size == 0:
        result["missing"] = None
        return
    rep = missing_report(raw, names)
    rep["bias_check"] = listwise_bias_check(raw, names)
    result["missing"] = rep

    n_total = prep.n_total
    if n_total > 0 and prep.n_dropped > 0:
        prop = prep.n_dropped / n_total
        if prop >= DROP_PROP_WARN:
            worst = rep.get("worst_item")
            hint = (f" 결측이 가장 많은 문항은 '{worst}'입니다 — 이 문항을 빼고(--items) 다시 "
                    f"돌리면 표본이 늘 수 있습니다." if worst else "")
            result["warnings"].append(
                f"결측 제거로 응답자의 {prop*100:.0f}%({prep.n_dropped}/{n_total}명)를 잃었습니다."
                + hint)

    high = [(names[i], v) for i, v in enumerate(rep["per_item_prop"]) if v >= ITEM_MISS_WARN]
    if high:
        detail = ", ".join(f"{nm}({v*100:.0f}%)" for nm, v in high)
        result["notes"].append(f"결측률이 높은 문항({ITEM_MISS_WARN*100:.0f}% 이상): {detail}.")

    biased = [b for b in rep["bias_check"] if abs(b["d"]) >= MCAR_D_WARN]
    if biased:
        detail = ", ".join(f"{b['item']}(d={b['d']:+.2f})" for b in biased)
        result["notes"].append(
            f"결측 제거 편향 신호: 완전응답자와 삭제된 응답자의 응답 분포가 다릅니다 — {detail}. "
            f"결측이 무작위(MCAR)가 아닐 수 있어 요인구조가 특정 집단으로 치우쳤을 가능성이 "
            f"있으니 결측 사유를 확인하세요.")


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


def _ml_fit_scan(r: np.ndarray, n: int, p: int,
                 null_chi: Optional[float]) -> List[Dict]:
    """k=1..(식별 가능한 최대)까지 ML을 반복 적합해 적합도지수 표를 만든다.

    ML EFA에서 요인 수를 고르는 실제 관행: χ²가 비유의해지는(=모형이 기각되지 않는) 최소 k,
    또는 RMSEA/BIC가 최소가 되는 k를 근거로 삼는다. 고유값·평행분석·MAP과 독립적인 근거다.
    수렴 실패나 수치 오류가 난 k는 조용히 건너뛰지 않고 error 필드로 남긴다.
    """
    kmax = min(efa.ml_max_factors(p), p - 1)
    rows: List[Dict] = []
    for kk in range(1, kmax + 1):
        row: Dict = {"k": kk}
        try:
            m = efa.ml_factor_analysis(r, kk)
            row.update(efa.fit_indices(m.criterion, n, p, kk, null_chi_square=null_chi))
            row["converged"] = m.converged
            row["heywood"] = m.heywood
        except (ValueError, np.linalg.LinAlgError) as exc:
            row["error"] = str(exc)
        rows.append(row)
    return rows


SKEW_WARN = 2.0             # |왜도| 한계(Curran, West & Finch 1996)
KURT_WARN = 7.0             # |초과첨도| 한계


def _descriptive_diagnostics(x: np.ndarray, names: List[str], result: Dict,
                             scale_min: Optional[float], scale_max: Optional[float],
                             correlation: str, extraction: str) -> None:
    """문항 기술통계와 그로부터 나오는 분포 경고(바닥/천장·정규성)를 싣는다."""
    desc = efa.item_descriptives(x, scale_min, scale_max)
    for d, nm in zip(desc, names):
        d["item"] = nm
    result["item_descriptives"] = desc

    fc = [(d["item"], d["floor_prop"], d["ceiling_prop"], d["extreme_threshold"])
          for d in desc
          if max(d["floor_prop"], d["ceiling_prop"]) > d["extreme_threshold"]]
    if fc:
        detail = ", ".join(
            f"{nm}({'바닥' if f >= c else '천장'} {max(f, c)*100:.0f}%>기준{t*100:.0f}%)"
            for nm, f, c, t in fc)
        result["notes"].append(
            f"바닥/천장 효과가 큰 문항: {detail}. 응답이 척도 끝에 몰려 변별력이 낮습니다 — "
            f"적재량이 높아도 재검토하세요(기준은 균등응답 기대치에 맞춰 범주 수별로 조정)"
            + ("(척도범위 미지정: 관측 최솟값/최댓값 기준)." if scale_min is None else "."))

    nonnormal = [d["item"] for d in desc
                 if abs(d["skew"]) > SKEW_WARN or abs(d["kurtosis"]) > KURT_WARN]
    if nonnormal and correlation == "pearson":
        extra = ("ML 적합도지수(χ²·RMSEA 등)도 다변량 정규성을 가정하므로 함께 왜곡됩니다. "
                 if extraction == "ml" else "")
        result["notes"].append(
            f"분포가 심하게 치우친 문항(|왜도|>{SKEW_WARN:g} 또는 |첨도|>{KURT_WARN:g}): "
            f"{', '.join(nonnormal)}. 피어슨 상관은 정규성에서 벗어난 순서형 응답의 상관을 "
            f"과소추정합니다 — {extra}순서형(리커트)이라면 --correlation polychoric 를 검토하세요.")


def analyze(prep: Prepared,
            n_factors: Optional[int] = None,
            rotation: str = "varimax",
            parallel_iter: int = 100,
            seed: int = 42,
            min_loading: float = 0.40,
            extraction: str = "pca",
            correlation: str = "pearson",
            fit_scan: bool = False,
            scale_min: Optional[float] = None,
            scale_max: Optional[float] = None,
            bootstrap: int = 0) -> Dict:
    """전처리된 데이터에 EFA/타당도 진단을 수행하고 결과 딕셔너리를 반환.

    extraction: "pca"(주성분, SPSS 기본) · "paf"(주축분해) · "ml"(최대우도, 적합도지수 제공).
    correlation: "pearson"(기본) 또는 "polychoric"(순서형 리커트용 잠재상관).
    fit_scan: ML 추출에서 k=1..최대까지 적합도지수를 훑어 요인 수 선택 근거를 제공.
    """
    if extraction not in ("pca", "paf", "ml"):
        raise ValueError("extraction은 'pca', 'paf', 'ml' 중 하나여야 합니다.")
    if correlation not in ("pearson", "polychoric"):
        raise ValueError("correlation은 'pearson' 또는 'polychoric'이어야 합니다.")
    names = prep.names
    x = prep.matrix
    n, p = x.shape

    result: Dict = {
        "items": names,
        "n_items": p,
        "n_total": prep.n_total,
        "n_used": prep.n_used,
        "n_dropped": prep.n_dropped,
        # 추출 방식: pca=주성분(SPSS 기본), paf=주축분해(공통요인), ml=최대우도
        "extraction": {"pca": "principal_component", "paf": "principal_axis",
                       "ml": "maximum_likelihood"}[extraction],
        "correlation": correlation,
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

    # 자동선택에서 통째로 빠진 열을 알린다. 조용히 빠지면 사용자는 문항 하나가
    # 분석에서 사라진 걸 눈치채지 못한다(자릿수구분 쉼표 "1,234", 캐시 없는 수식 셀,
    # 상수 열 등). --items 로 명시하면 오류로 잡히지만 자동선택에서는 여기서만 드러난다.
    dropped = getattr(prep, "dropped", {}) or {}
    result["dropped_columns"] = dict(dropped)
    if dropped:
        detail = ", ".join(f"{k}({v})" for k, v in dropped.items())
        result["warnings"].append(
            f"분석에서 자동 제외된 열이 있습니다: {detail}. 문항이라면 --items 로 명시해 "
            f"원인을 확인하세요(ID·날짜·메모 열이면 정상입니다).")

    # 결측 구조 진단(제거 전 원자료 기준) — 손실 규모·유발 문항·MCAR 위배 신호.
    _missing_diagnostics(prep, names, result)

    if p < 2:
        raise ValueError("요인분석에는 최소 2개 이상의 문항(열)이 필요합니다.")
    if n < 3:
        # 표본이 처음부터 적은 것과 'listwise 삭제가 다 지워버린 것'은 원인도 해법도 다르다.
        # 후자에 "표본이 적다"고만 말하면 사용자는 엉뚱한 곳을 고치게 된다.
        if prep.n_dropped > 0 and prep.n_total >= 3:
            miss = result.get("missing") or {}
            worst = miss.get("worst_item")
            hint = (f" 결측이 가장 많은 문항은 '{worst}'입니다 — 이 문항을 빼고(--items) "
                    f"다시 돌려 보세요." if worst else "")
            raise ValueError(
                f"결측 제거 후 남은 응답자가 {n}명뿐입니다(전체 {prep.n_total}명 중 "
                f"{prep.n_dropped}명이 한 문항 이상 결측이라 삭제됨).{hint}")

        raise ValueError(
            f"응답자 수가 너무 적습니다(n={n}). 요인분석에는 최소 3명 이상이 필요하며, "
            f"실제로는 문항당 5~10명 이상을 권장합니다.")

    # 결측 제거 후 분산이 0이 된 열은 상관행렬을 NaN으로 오염시키므로 명확히 막는다.
    # 극단값(1e308 등)은 분산 계산에서 오버플로해 상관행렬을 NaN으로 만들고, numpy가
    # 영문 RuntimeWarning을 뿜은 뒤 "Eigenvalues did not converge" 같은 해석 불가한
    # 오류로 끝난다. 분산을 쓰는 첫 계산보다 먼저 원인을 짚어 막는다.
    with np.errstate(over="ignore", invalid="ignore"):
        col_var = np.var(x, axis=0)
    bad_scale = [names[i] for i in range(p) if not np.isfinite(col_var[i])]
    if bad_scale:
        raise ValueError(
            f"값이 너무 커서 분산을 계산할 수 없는 문항이 있습니다: {', '.join(bad_scale)}. "
            f"입력 오류(자릿수·단위)가 아닌지 확인하세요 — 리커트 응답이라면 보통 1~7 범위입니다.")

    zero_var = [names[i] for i in range(p) if np.std(x[:, i]) == 0]
    if zero_var:
        raise ValueError(
            f"결측 제거 후 값이 모두 동일해진 문항이 있습니다: {', '.join(zero_var)}. "
            f"해당 문항을 제외하거나 결측 패턴을 확인하세요.")

    # 표본 크기 경고.
    # '문항당 N명' 규칙만으로는 부족하다 — MacCallum, Widaman, Zhang & Hong(1999)은
    # 요인해의 안정성이 문항당 인원비가 아니라 '절대 표본 수 · 공통성 크기'에 달렸음을
    # 보였다. n=60·p=10이면 문항당 6명이라 비율 규칙은 통과하지만 실제로는 위험하다.
    # 그래서 비율과 절대 수를 함께 본다.
    if n < p:
        result["warnings"].append(
            f"응답자 수(n={n})가 문항 수(p={p})보다 적어 상관행렬이 특이합니다 — 결과 신뢰 불가.")
    elif n < 5 * p:
        result["warnings"].append(
            f"표본이 작습니다(n={n}, 문항당 {n / p:.1f}명). 문항당 5~10명 이상을 권장합니다.")
    if p <= n < 150:
        result["warnings"].append(
            f"절대 표본 수가 작습니다(n={n}). 공통성이 낮거나(<.5) 요인당 문항이 적으면 "
            f"n<150에서는 요인해가 표본마다 크게 흔들립니다 — 적재량·요인 수를 확정적으로 "
            f"해석하지 마세요(문항당 인원비만으로는 충분하지 않습니다).")

    # 문항 기술통계(평균·SD·왜도·첨도·바닥/천장) — 척도 논문 Table 1이자
    # polychoric/ML 전환 판단의 근거.
    _descriptive_diagnostics(x, names, result, scale_min, scale_max, correlation, extraction)

    if correlation == "polychoric":
        # 순서형 적정성 진단: 범주가 너무 많으면(연속형에 가까움) 폴리코릭이 부적절·과도.
        max_cat = polychoric.max_categories(x)
        non_int = int(np.sum(np.abs(x - np.rint(x)) > 1e-8))
        if non_int > 0:
            result["warnings"].append(
                f"polychoric 상관은 정수 코드 순서형(리커트) 문항을 가정합니다 — 정수가 아닌 값 "
                f"{non_int}개를 반올림해 범주로 처리했습니다. 연속형이면 --correlation pearson을 쓰세요.")
        if max_cat > 15:
            result["warnings"].append(
                f"문항 범주 수가 많습니다(최대 {max_cat}개). 폴리코릭은 소수 범주(대개 ≤7) 순서형에 적합하며 "
                f"범주가 많으면 느리고 이득이 적습니다 — 연속형이면 --correlation pearson을 권장합니다.")
        r = polychoric.polychoric_matrix(x)
    else:
        r = efa.correlation_matrix(x)
    result["correlation_matrix"] = r
    pos_def = efa.is_positive_definite(r)
    if correlation == "polychoric" and not pos_def:
        result["warnings"].append(
            "폴리코릭 상관행렬이 양의 정부호가 아닙니다(쌍별 추정의 흔한 특성) — KMO/Bartlett가 생략될 수 "
            "있습니다. 표본을 키우거나 문항 수를 줄여 보세요.")

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

    # --- Velicer MAP(최소평균편상관): Kaiser·평행분석과 독립적인 제3의 유지 근거 ---
    # 편상관 기반이라 양의 정부호 상관행렬에서만 신뢰 가능(특이행렬이면 생략).
    result["map_k"] = None
    result["map_values"] = None
    if pos_def and p >= 3:
        try:
            mp = efa.velicer_map(r)
            result["map_k"] = mp["k"]
            result["map_values"] = mp["values"]
        except np.linalg.LinAlgError:
            pass

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
    result["fit"] = None
    result["fit_scan"] = None
    if extraction == "ml":
        # 독립모형(모든 상관=0) χ²는 Bartlett 통계량과 동일 — TLI/CFI의 기준선으로 재사용.
        null_chi = result["bartlett"]["chi_square"] if result.get("bartlett") else None
        ml = efa.ml_factor_analysis(r, k)
        raw = ml.loadings
        if not ml.converged:
            result["warnings"].append(
                f"최대우도(ML) 최적화가 {ml.n_iter}회 반복에서 수렴하지 않았습니다 — "
                f"적합도지수를 신뢰하기 어렵습니다. 요인 수를 줄여 보세요.")
        if ml.heywood:
            result["warnings"].append(
                "최대우도(ML)에서 고유분산이 하한(0.005)에 도달하는 Heywood 케이스가 발생했습니다 — "
                "요인 수 과다·표본 부족·문항 다중공선성의 신호이며 해가 불안정할 수 있습니다.")
        fit = efa.fit_indices(ml.criterion, n, p, k, null_chi_square=null_chi)
        result["fit"] = fit
        if not fit.get("identified"):
            result["warnings"].append(
                f"요인 수 k={k}에서 모형 자유도가 {fit['df']}(≤0)이라 적합도 검정이 불가합니다 — "
                f"문항 수(p={p}) 대비 요인이 너무 많습니다(최대 k={efa.ml_max_factors(p)}).")
        if correlation == "polychoric":
            result["notes"].append(
                "폴리코릭 상관에 ML 적합도지수를 적용했습니다. χ²/RMSEA/TLI/CFI는 원자료의 다변량 "
                "정규성을 가정한 값이라 폴리코릭 입력에서는 근사이며, 참고용으로만 보고하세요.")
        if fit_scan:
            scan = _ml_fit_scan(r, n, p, null_chi)
            result["fit_scan"] = scan
            if not scan:
                # p가 작으면 df>0인 k가 하나도 없다 → 표가 통째로 비는데, 아무 말도 없으면
                # 옵션이 무시된 건지 자료가 문제인지 알 수 없다.
                result["warnings"].append(
                    f"--fit-scan: 문항 수(p={p})가 적어 자유도가 양수인 요인 수(k)가 없습니다 — "
                    f"적합도 스캔을 만들 수 없습니다(문항이 최소 4~5개는 필요합니다).")
    elif extraction == "paf":
        paf = efa.paf_loadings(r, k)
        raw = paf.loadings
        if not paf.converged:
            result["warnings"].append(
                f"주축분해(PAF) 공통성이 {paf.n_iter}회 반복에서 수렴하지 않았습니다 — "
                f"요인 수(--n-factors)를 줄이거나 표본을 확인하세요.")
        if paf.heywood:
            result["warnings"].append(
                "주축분해(PAF) 중 공통성이 1에 도달/초과하는 Heywood 케이스가 발생해 1로 절단했습니다 — "
                "요인 수 과다·표본 부족·문항 다중공선성의 신호일 수 있습니다.")
        # 요인 수가 추출 가능한 공통요인 수를 넘으면 0(빈) 요인 열이 생긴다 → 명확히 안내.
        col_norm = np.sqrt((raw ** 2).sum(axis=0))
        n_good = int(np.sum(col_norm >= 1e-8))
        if n_good < k:
            result["warnings"].append(
                f"요인 수(k={k})가 주축분해로 추출 가능한 공통요인 수(≈{n_good})를 초과해 "
                f"빈(0 적재) 요인이 생겼습니다 — --n-factors를 {max(1, n_good)} 이하로 줄이세요.")
    else:
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

    # --- 역문항 미처리 탐지 ---
    # 주적재가 '음수'인 문항은 같은 요인의 다른 문항과 반대 방향으로 재는 문항이다.
    # 거의 항상 역문항(reverse-worded)을 --reverse 로 선언하지 않은 것이 원인이며,
    # 이때 합산점수는 그 문항을 거꾸로 더해 조용히 오염된다(α가 음수로 무너지기도 한다).
    # 문항 자체는 멀쩡한데 '문항-총점 낮음'으로 플래그돼 좋은 문항을 지우게 되므로,
    # 증상이 아니라 원인을 짚어 준다.
    neg = [i for i in range(p)
           if rotated[i][groups[i]] < 0 and abs(rotated[i][groups[i]]) >= min_loading]
    result["negative_loading_items"] = [names[i] for i in neg]
    if neg:
        detail = ", ".join(f"{names[i]}(F{groups[i]+1}={rotated[i][groups[i]]:+.2f})" for i in neg)
        result["warnings"].append(
            f"주적재가 음수인 문항이 있습니다: {detail}. 역문항(reverse-worded)을 재점수화하지 "
            f"않았을 가능성이 큽니다 — 그대로 두면 합산점수·Cronbach α가 왜곡됩니다. "
            f"역문항이라면 `--reverse {','.join(names[i] for i in neg)} "
            f"--scale-min 1 --scale-max 5`(실제 척도범위로) 로 다시 실행하세요. "
            f"역문항이 아니라면 문항 방향(문구)을 확인하세요.")

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
    # 요인별 Cronbach's α — 표본 원점수 기반(적재 낙관적 ω와 나란히 보고).
    alpha = efa.alpha_by_group(x, groups, k)
    result["alpha"] = [alpha.get(f) for f in range(k)]
    # 문항을 뺐을 때의 α — "이 문항을 지우면 신뢰도가 오르나?"에 직접 답한다.
    aid = efa.alpha_if_deleted(x, groups, k)
    result["alpha_if_deleted"] = aid.tolist()

    # --- 부트스트랩 안정성: 적재 신뢰구간 + 요인 수 합의율 ---
    # 이 보고서의 다른 모든 숫자는 점추정이다. "표본을 다시 뽑아도 이 적재·이 요인 수가
    # 버티는가"에 답할 수 있는 유일한 지표라, 작은 표본에서 특히 중요하다.
    result["bootstrap"] = None
    if bootstrap and bootstrap > 0:
        pa_ref = np.asarray(result["parallel_eigenvalues"]) \
            if result.get("parallel_eigenvalues") else None
        bs = efa.bootstrap_stability(
            x, k, reference=rotated, n_boot=int(bootstrap), seed=seed,
            extraction=extraction, rotation=applied_rotation, pa_reference=pa_ref)
        result["bootstrap"] = {
            "n_boot": bs.n_boot,
            "n_ok": bs.n_ok,
            "loading_lo": bs.lo.tolist(),
            "loading_hi": bs.hi.tolist(),
            "pa_agreement": bs.pa_agreement,
            "k_counts": {str(kk): vv for kk, vv in sorted(bs.k_counts.items())},
        }
        if bs.n_ok < bs.n_boot:
            result["warnings"].append(
                f"부트스트랩 재표본 {bs.n_boot}개 중 {bs.n_boot - bs.n_ok}개가 계산 불가로 "
                f"제외됐습니다(재표본에서 분산 0 문항이나 특이행렬 발생) — 구간이 낙관적일 수 있습니다.")
        if bs.pa_agreement is not None and bs.pa_agreement < 0.7:
            result["notes"].append(
                f"요인 수가 불안정합니다: 평행분석이 재표본의 {bs.pa_agreement*100:.0f}%에서만 "
                f"{k}개 요인을 지지했습니다(분포: "
                f"{', '.join(f'{kk}요인 {vv}회' for kk, vv in sorted(bs.k_counts.items()))}). "
                f"요인 수를 확정적으로 보고하지 말고 표본을 늘리는 것을 검토하세요.")

    # --- 문항별 진단 플래그 ---
    desc = result["item_descriptives"]
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
        # 그 문항을 빼면 소속 요인의 α가 눈에 띄게 오른다 = 척도를 갉아먹는 문항.
        cur_a = alpha.get(int(groups[i]))
        if (cur_a is not None and not np.isnan(aid[i]) and aid[i] > cur_a + 0.02):
            problems.append(f"제거시 α↑({cur_a:.2f}→{aid[i]:.2f})")
        d = desc[i]
        if max(d["floor_prop"], d["ceiling_prop"]) > d["extreme_threshold"]:
            side = "바닥" if d["floor_prop"] >= d["ceiling_prop"] else "천장"
            problems.append(f"{side}효과{max(d['floor_prop'], d['ceiling_prop'])*100:.0f}%")
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
