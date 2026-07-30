"""전체 분석 오케스트레이션: 전처리된 데이터 -> 결과 딕셔너리."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np

from . import efa, polychoric
from .dataio import NA_STRINGS, Prepared, listwise_bias_check, missing_report

# 진단 임계값(관례적 기준)
MSA_POOR = 0.5          # 문항별 MSA 하한
# 낮은 공통성 기준 — 추출 방식에 따라 다르게 잡는다.
# PCA는 관측분산 전체를 쓰므로 h²가 크게 나오지만, PAF/ML은 **공통분산만** 모형화해 같은
# 자료에서도 h²가 체계적으로 낮다. 0.3을 그대로 적용하면 진적재 .50짜리(게재에 아무 문제
# 없는) 문항까지 PAF/ML에서 대부분 '제거 검토'로 표시된다(모의자료 실측: 24문항 중 21개).
COMMUNALITY_LOW = 0.3           # 주성분(PCA) 기준 — 전통적 관례
COMMUNALITY_LOW_COMMON = 0.2    # 공통요인(PAF/ML) 기준
ITEM_TOTAL_LOW = 0.3    # 낮은 문항-총점 상관
DROP_PROP_WARN = 0.10   # listwise 삭제 비율 경고선(10%)
ITEM_MISS_WARN = 0.05   # 문항별 결측 비율 경고선(5%)
MCAR_D_WARN = 0.2       # listwise 편향 점검의 |Cohen's d| 경고선(작은 효과)
ALPHA_GOOD = 0.70       # 척도 신뢰도 관례 기준(Nunnally)
CONGRUENCE_EQUAL = 0.95     # Tucker φ: 사실상 동일한 요인
CONGRUENCE_FAIR = 0.85      # Tucker φ: 상당히 유사(그 아래는 다른 요인)


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

    # 판정은 d의 크기가 아니라 **다중비교 보정 신뢰구간이 0을 배제하는가**로 한다.
    # 고정 임계값(|d|≥0.2)은 완전 무작위 결측에서도 사실상 100% 발화했다(모의 실측).
    biased = [b for b in rep["bias_check"] if b.get("flagged")]
    if biased:
        detail = ", ".join(
            f"{b['item']}(d={b['d']:+.2f}, 95% CI [{b['ci_lo']:+.2f}, {b['ci_hi']:+.2f}])"
            for b in biased)
        n_tested = biased[0].get("n_tested", len(rep["bias_check"]))
        result["notes"].append(
            f"결측 제거 편향 신호: 완전응답자와 삭제된 응답자의 응답 분포가 다릅니다 — {detail}. "
            f"결측이 무작위(MCAR)가 아닐 수 있어 요인구조가 특정 집단으로 치우쳤을 가능성이 "
            f"있으니 결측 사유를 확인하세요(문항 {n_tested}개를 검정하고 다중비교를 보정했습니다).")


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
# 한 열의 SD/범위가 나머지 문항 중앙값의 이 배수를 넘으면 '문항이 아닌 열'로 의심한다.
# 1~5 리커트(SD≈1.2)에 섞인 나이(SD≈16)는 배수 13, 0~100 슬라이더도 8배 정도가 된다.
SCALE_OUTLIER_RATIO = 4.0
# 범위가 넓어도 이 정도 범주 수를 넘지 않으면 '문항'으로 본다(0~10 NRS = 11개).
SCALE_OUTLIER_MIN_CATEGORIES = 15
# 응답 범주 진단 임계값(efa 쪽 정의를 그대로 재사용).
RARE_CATEGORY_PROP = efa.RARE_CATEGORY_PROP
# ω와 α가 이만큼 벌어지면 역문항 미처리·이질 척도 혼입·배정 붕괴 중 하나다.
OMEGA_ALPHA_GAP = 0.20
# 백분위 신뢰구간을 인쇄하기 위한 최소 '성공' 재표본 수. 이보다 적으면 구간이
# 오히려 좁고 확신에 차 보여(점추정을 벗어나기도 한다) 논문에 그대로 실릴 위험이 크다.
BOOTSTRAP_MIN_OK = 50


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

    # 척도가 크게 다른 열(나이·BMI·방문차수·총점) 탐지.
    # 임상 CSV에서 가장 흔한 사고다: 자동선택은 '숫자이고 상수가 아니면' 문항으로 삼키는데,
    # SD가 10배 큰 공변량 하나가 하위척도 총점을 지배해 **멀쩡한 문항 전부를**
    # '문항-총점 낮음'으로 뒤집어 놓고 α를 음수로 무너뜨린다. 원인은 문항이 아니라 그 열이다.
    if len(desc) >= 3:
        sds = np.array([d["sd"] for d in desc], dtype=float)
        rngs = np.array([d["max"] - d["min"] for d in desc], dtype=float)
        med_sd = float(np.median(sds))
        med_rng = float(np.median(rngs))
        med_cat = float(np.median([d["n_categories"] for d in desc]))
        odd = []
        for i, d in enumerate(desc):
            wide = ((med_sd > 1e-12 and sds[i] >= SCALE_OUTLIER_RATIO * med_sd)
                    or (med_rng > 1e-12 and rngs[i] >= SCALE_OUTLIER_RATIO * med_rng))
            # 범위만 넓다고 문항이 아닌 건 아니다 — 이분문항 사이의 0~4 중증도 문항이나
            # 리커트 사이의 0~100 VAS는 **정상적인 혼합 배터리**다. 공변량(나이·BMI·총점)을
            # 가르는 진짜 표지는 '고유값 개수도 함께 많다'는 점이다.
            many = (d["n_categories"] > SCALE_OUTLIER_MIN_CATEGORIES
                    and (med_cat <= 1e-12 or d["n_categories"] >= SCALE_OUTLIER_RATIO * med_cat))
            if wide and many:
                odd.append((d["item"], d["min"], d["max"], d["sd"]))
        if odd:
            detail = ", ".join(f"{nm}(범위 {lo:g}~{hi:g}, SD {sd:.1f})" for nm, lo, hi, sd in odd)
            result["warnings"].append(
                f"다른 문항과 척도가 크게 다른 열이 있습니다: {detail} — 나머지 문항은 SD 중앙값 "
                f"{med_sd:.2f}, 범위 중앙값 {med_rng:g}입니다. 문항이 아니라 공변량(나이·BMI·"
                f"방문차수)이나 총점 열일 가능성이 큽니다 — 문항이 아니면 "
                f"`--id-col {odd[0][0]}` 로 제외하고 다시 돌리세요. 그대로 두면 이 열이 "
                f"하위척도 총점을 지배해 나머지 문항이 전부 '문항-총점 낮음'으로 잘못 표시됩니다.")

    # 응답 범주 분포 — 죽은 범주(아무도 안 고른 선택지)는 요인분석이 절대 알려 주지 않는다.
    cf = efa.category_frequencies(x, names, scale_min, scale_max)
    result["category_frequencies"] = cf
    if cf:
        dead = [(r["item"], r["unused"]) for r in cf["items"] if r["unused"]]
        rare = [(r["item"], r["rare"]) for r in cf["items"] if r["rare"]]
        if dead and cf["declared_range"]:
            # 척도 범위를 선언했을 때만 '아무도 안 고름'이 확실하다(선언이 없으면 관측
            # 범주만 세우므로 빈 칸이 애초에 생기지 않는다).
            detail = ", ".join(f"{nm}({', '.join(map(str, u))}번)" for nm, u in dead[:6])
            result["notes"].append(
                f"아무도 선택하지 않은 응답 범주가 있습니다: {detail}"
                f"{' 외' if len(dead) > 6 else ''}. 그 문항은 사실상 더 짧은 척도로 작동하므로 "
                f"범주를 합치거나(collapse) 문구를 다듬는 것을 검토하세요.")
        if rare:
            detail = ", ".join(f"{nm}({', '.join(map(str, u))}번)" for nm, u in rare[:6])
            result["notes"].append(
                f"선택률이 {RARE_CATEGORY_PROP*100:.0f}% 미만인 응답 범주: {detail}"
                f"{' 외' if len(rare) > 6 else ''}. 응답자가 거의 쓰지 않는 선택지는 변별에 "
                f"기여하지 못하니 범주 축소를 검토하세요.")
        outside = [r["item"] for r in cf["items"] if r["outside_range"]]
        if outside and cf["declared_range"]:
            result["warnings"].append(
                f"선언한 척도범위를 벗어난 응답이 있는 문항: {', '.join(outside[:8])}. "
                f"--scale-min/--scale-max 설정이나 자료 입력을 확인하세요.")

    nonnormal = [d["item"] for d in desc
                 if abs(d["skew"]) > SKEW_WARN or abs(d["kurtosis"]) > KURT_WARN]
    if nonnormal and correlation == "pearson":
        extra = ("ML 적합도지수(χ²·RMSEA 등)도 다변량 정규성을 가정하므로 함께 왜곡됩니다. "
                 if extraction == "ml" else "")
        result["notes"].append(
            f"분포가 심하게 치우친 문항(|왜도|>{SKEW_WARN:g} 또는 |첨도|>{KURT_WARN:g}): "
            f"{', '.join(nonnormal)}. 피어슨 상관은 정규성에서 벗어난 순서형 응답의 상관을 "
            f"과소추정합니다 — {extra}순서형(리커트)이라면 --correlation polychoric 를 검토하세요.")


# 집단별 재적합에 필요한 최소 응답자 수(이보다 작으면 상관행렬이 의미를 잃는다).
GROUP_MIN_N = 20
# 집단의 φ를 '판정'에 쓰기 위한 최소 문항당 응답자 수. 모의실험에서 동일 모집단으로부터
# 뽑은 집단들도 문항당 1~2명 수준에서는 최소 φ가 .75~.83까지 떨어져 거의 100% '다름'으로
# 오판됐고, 문항당 3명을 넘기면 오경보가 사라졌다. 그 경계를 그대로 쓴다.
GROUP_RATIO_MIN = 3.0
# 널 기준선(같은 모집단 무작위 분할) 반복 수. 200회면 5백분위가 충분히 안정적이고,
# 피어슨 상관에서는 집단 3개 기준 0.3초 안팎이다.
GROUP_NULL_REPS = 200
# 집단 수가 이보다 많으면 범주형 그룹 변수가 아니라 연속형을 잘못 지정한 것으로 본다.
GROUP_MAX_LEVELS = 12


def _normalize_group_label(v) -> str:
    """집단 라벨 하나를 정규화한다(제어문자 제거 · 공백 정리 · 결측 토큰 → "")."""
    if v is None:
        return ""
    if isinstance(v, float) and not np.isfinite(v):
        return ""
    s = str(v)
    # 줄바꿈·탭 등 제어문자는 공백으로 바꿔 표 정렬이 깨지지 않게 한다.
    s = "".join(" " if (ch < " " or ch == "\x7f") else ch for ch in s)
    s = " ".join(s.split())         # 연속 공백 축약 + 앞뒤 제거
    if s.lower() in NA_STRINGS:
        return ""
    return s


def _group_replicability(x: np.ndarray, names: List[str], labels: Sequence,
                         k: int, reference: np.ndarray, extraction: str,
                         rotation: str, correlation: str, result: Dict,
                         groups: np.ndarray, seed: int = 42) -> None:
    """집단(사이트·성별·투여군)별로 요인해를 다시 적합해 **구조가 재현되는지** 본다.

    다기관 임상시험이나 성별·연령대가 섞인 표본에서 "전체 표본에서 나온 2요인 구조가
    각 집단에서도 같은 구조인가"는 척도 논문의 필수 질문이다. 완전한 측정동일성 검정은
    확인적 요인분석(CFA)의 영역이지만, 그 전에 **탐색 단계에서 반드시 하는 선별 검사**가
    집단별 EFA + Tucker 일치계수(φ) 비교다(Lorenzo-Seva & ten Berge 2006).

    각 집단에서 같은 k·같은 추출/회전으로 다시 적합한 뒤 전체 해에 직교 Procrustes로
    정렬하고, 요인별 φ를 낸다. φ<.85인 요인은 그 집단에서 **다른 것을 재고 있다**는 뜻이라
    집단을 합쳐 총점을 비교하면 안 된다는 실질적 경고가 된다.

    문항→요인 배정은 전체 해로 고정해 집단별 α가 같은 하위척도를 재게 한다.
    """
    p = x.shape[1]
    # 집단 라벨 정규화. 세 가지를 함께 처리한다:
    #  (1) 제어문자(줄바꿈·탭)를 공백으로 — 엑셀에서 내보낸 자유기입 사이트명에 흔하고,
    #      그대로 두면 보고서 표가 줄바꿈으로 깨진다.
    #  (2) 앞뒤 공백 제거 — ' A '와 'A'는 같은 집단이다.
    #  (3) 결측 토큰(NA·N/A·nan·none·null·.·-·missing)을 결측으로 — 같은 문자열이
    #      문항 열에서는 결측인데 집단 열에서는 'NA라는 이름의 사이트'가 되어, 존재하지도
    #      않는 집단끼리 φ·KMO·α를 계산하고 통합 경고까지 내던 사고를 막는다.
    labels = np.asarray(
        [_normalize_group_label(v) for v in labels], dtype=object)
    if labels.shape[0] != x.shape[0]:
        raise ValueError("집단 라벨 수가 응답자 수와 다릅니다.")

    blank = int(np.sum(labels == ""))
    valid = labels != ""
    levels = sorted({str(v) for v in labels[valid]})
    info: Dict = {"column": None, "n_blank": blank, "levels": levels,
                  "n_levels": len(levels), "groups": [],
                  "min_congruence": None, "pairwise": None}
    result["group_replicability"] = info

    if blank:
        result["notes"].append(
            f"집단 열의 값이 비어 있는 응답자 {blank}명은 집단별 비교에서 제외했습니다"
            f"(전체 요인분석에는 그대로 포함됩니다).")
    if len(levels) < 2:
        result["warnings"].append(
            f"집단별 비교 생략: 유효한 집단이 {len(levels)}개뿐입니다(2개 이상 필요).")
        return
    # 대소문자만 다른 집단값('여'/'여 '는 이미 strip으로 합쳐지지만 'F'/'f'는 남는다)은
    # 임상 CSV의 전형적 입력 오류다. 말없이 두 집단으로 쪼개면 각 집단이 표본 부족으로
    # 건너뛰어져 '비교 불가'가 되는데, 사용자는 왜인지 알 수 없다.
    folded: Dict[str, List[str]] = {}
    for lv in levels:
        folded.setdefault(lv.casefold(), []).append(lv)
    dup = [v for v in folded.values() if len(v) > 1]
    if dup:
        detail = "; ".join(" / ".join(v) for v in dup)
        result["warnings"].append(
            f"집단 열에 대소문자만 다른 값이 있어 서로 다른 집단으로 처리했습니다: {detail}. "
            f"같은 집단이라면 입력을 통일한 뒤 다시 실행하세요.")
    if len(levels) > GROUP_MAX_LEVELS:
        # 값 목록을 지운다. 집단이 이렇게 많다는 건 대개 환자ID·주민번호·생년월일 같은
        # **식별자 열을 잘못 지정한** 경우인데, 그대로 두면 분석은 거절해 놓고 정작 그
        # 식별자 열 전체를 JSON에 복사해 내보내게 된다(협력자에게 그대로 전달된다).
        info["levels"] = None
        result["warnings"].append(
            f"집단별 비교 생략: 집단이 {len(levels)}개로 너무 많습니다(상한 {GROUP_MAX_LEVELS}). "
            f"연속형 변수(나이·점수)나 식별자(환자ID 등)를 지정하지 않았는지 확인하세요 — "
            f"범주로 묶어서 다시 주세요. (식별정보가 결과 파일에 새지 않도록 집단값 목록은 "
            f"출력하지 않았습니다.)")
        return

    fitted: Dict[str, np.ndarray] = {}
    for lv in levels:
        sel = labels == lv
        xg = x[sel]
        ng = int(xg.shape[0])
        row: Dict = {"level": lv, "n": ng, "congruence": None, "alpha": None,
                     "kmo": None, "skipped": None, "provisional": None}
        # 문항 수보다 응답자가 적으면 그 집단의 상관행렬은 특이해서 요인해가 잡음이다.
        # 전체 분석은 n<p를 '결과 신뢰 불가'로 막으면서 집단별 재적합만 무방비였다.
        hard_min = max(GROUP_MIN_N, k + 1, p + 1)
        if ng < hard_min:
            reason = (f"표본 부족(n={ng} < {hard_min}"
                      + (f"; 문항 수 p={p}보다 적어 상관행렬이 특이)" if ng <= p else ")"))
            row["skipped"] = reason
            info["groups"].append(row)
            continue
        with np.errstate(over="ignore", invalid="ignore"):
            sd = xg.std(axis=0)
        const = [names[i] for i in range(p) if not np.isfinite(sd[i]) or sd[i] <= 1e-12]
        if const:
            row["skipped"] = f"이 집단에서 값이 모두 동일한 문항: {', '.join(const)}"
            info["groups"].append(row)
            continue
        try:
            rg = (polychoric.polychoric_matrix(xg) if correlation == "polychoric"
                  else efa.correlation_matrix(xg))
            if not np.all(np.isfinite(rg)):
                raise ValueError("상관행렬에 계산 불가 값이 있습니다")
            raw_g, conv_g, hey_g = efa.extract_with_flags(rg, k, extraction)
            rot_g, _ = efa.rotate_loadings(raw_g, rotation)
            aligned, _ = efa.procrustes_align(rot_g, reference)
        except (ValueError, np.linalg.LinAlgError) as exc:
            row["skipped"] = f"요인해 계산 실패: {exc}"
            info["groups"].append(row)
            continue
        # PAF/ML은 수렴 실패해도 예외 없이 마지막 반복값을 돌려준다. 그 값으로 φ를 내면
        # '최적화가 도중에 멈춘 상태'를 구조 차이로 오독해, 멀쩡한 다기관 자료에
        # "사이트를 합치지 마세요"라는 무거운 경고를 발사한다.
        if not conv_g:
            row["skipped"] = (f"요인해 수렴 실패({extraction.upper()} 추출) — "
                              f"이 집단의 적재는 해가 아니라 중간 상태라 비교할 수 없습니다")
            info["groups"].append(row)
            continue

        phi_vec = efa.tucker_congruence(aligned, reference)
        row["congruence"] = [None if not np.isfinite(v) else float(v) for v in phi_vec]
        row["loadings"] = aligned.tolist()
        ag = efa.alpha_by_group(xg, groups, k)
        row["alpha"] = [ag.get(f) for f in range(k)]
        if efa.is_positive_definite(rg):
            try:
                row["kmo"] = float(efa.kmo(rg).overall)
            except np.linalg.LinAlgError:
                row["kmo"] = None
        # '판정 자격' 심사. 표본이 작으면 **같은 모집단에서 뽑아도** φ가 뚝 떨어진다
        # (모의실험: 문항당 1.2명이면 동일 모집단에서도 최소 φ 중앙값 .75, 오경보 100%).
        # 그런 집단의 낮은 φ는 '구조가 다르다'가 아니라 '표본이 작다'는 뜻이므로,
        # 값은 보여 주되 판정과 최상위 경고에서는 뺀다.
        reasons: List[str] = []
        if hey_g:
            reasons.append("Heywood 케이스(공통성이 경계에 도달)")
        if ng < GROUP_RATIO_MIN * p:
            reasons.append(f"문항당 {ng / p:.1f}명(권장 ≥{GROUP_RATIO_MIN})")
        if row["kmo"] is None:
            reasons.append("상관행렬이 특이해 KMO 계산 불가")
        elif row["kmo"] < 0.5:
            reasons.append(f"KMO={row['kmo']:.2f}(<0.50, 요인분석 부적합)")
        if reasons:
            row["provisional"] = " · ".join(reasons)
        elif ng < 5 * p:
            row["note"] = f"표본이 작아(문항당 {ng / p:.1f}명) 이 집단의 해는 불안정할 수 있습니다."
        fitted[lv] = aligned
        info["groups"].append(row)

    # bool([None, None]) 은 True다 — φ가 전부 정의 불가인 집단이 '비교 완료'로 세어져
    # "비교 가능한 집단이 2개 미만" 경고를 조용히 삼키던 버그. 실제 값이 있는지로 판정한다.
    usable = [r for r in info["groups"]
              if any(v is not None for v in (r.get("congruence") or []))]
    # 판정에 쓸 수 있는(자격을 갖춘) 집단.
    judged = [r for r in usable if not r.get("provisional")]
    info["n_usable"] = len(usable)
    info["n_judged"] = len(judged)
    if len(usable) < 2:
        result["warnings"].append(
            "집단별 비교를 완료한 집단이 2개 미만이라 구조 재현성을 판단할 수 없습니다"
            "(각 집단의 사유는 보고서의 집단별 표를 보세요).")
        return

    # 집단 쌍끼리 직접 비교. 두 집단이 각각 전체 해와는 그럭저럭 닮았는데 서로는 크게
    # 다른 경우가 있어(전체 해가 둘의 평균이므로), 전체 대비 φ만 보면 놓친다.
    pair_rows: List[Dict] = []
    ok_levels = {r["level"] for r in judged}
    lv_ok = [r["level"] for r in usable]
    for i in range(len(lv_ok)):
        for j in range(i + 1, len(lv_ok)):
            a, b = fitted[lv_ok[i]], fitted[lv_ok[j]]
            # 두 집단해는 이미 같은 기준(전체 해)에 정렬돼 있어 요인 대응이 맞다.
            v = efa.tucker_congruence(a, b)
            pair_rows.append({
                "a": lv_ok[i], "b": lv_ok[j],
                "congruence": [None if not np.isfinite(t) else float(t) for t in v],
                # 한쪽이라도 판정 자격이 없으면 이 쌍도 판정에 쓰지 않는다.
                "provisional": not (lv_ok[i] in ok_levels and lv_ok[j] in ok_levels),
            })
    info["pairwise"] = pair_rows

    # 최소 φ는 **판정 자격을 갖춘** 집단·쌍만으로 계산한다. 표본이 작아 잡음이 큰 집단이
    # 최솟값을 끌어내려 무거운 경고를 발사하던 것이 오경보의 주원인이었다.
    all_phi = [v for r in judged for v in r["congruence"] if v is not None]
    all_phi += [v for r in pair_rows if not r["provisional"]
                for v in r["congruence"] if v is not None]
    if all_phi:
        info["min_congruence"] = float(min(all_phi))

    provisional = [r["level"] for r in usable if r.get("provisional")]
    if provisional:
        result["notes"].append(
            f"판정 보류 집단: {', '.join(map(str, provisional))}. 표본이 작거나(문항당 "
            f"{GROUP_RATIO_MIN}명 미만) 그 집단의 상관행렬이 요인분석에 부적합해, φ가 낮게 나와도 "
            f"'구조가 다르다'는 근거로 쓸 수 없습니다(같은 모집단에서 뽑아도 이 표본 크기에서는 "
            f"φ가 떨어집니다). 값은 참고로만 표시하고 판정·경고에서는 제외했습니다.")
    if len(judged) < 2:
        # 판정을 안 했으면 판정용 숫자도 남기지 않는다 — JSON 소비자가 보고서는 거부한
        # 값을 '결론'으로 읽는다.
        info["min_congruence"] = None
        result["notes"].append(
            "판정 자격을 갖춘 집단이 2개 미만이라 구조 재현성을 확정적으로 판단하지 않았습니다 "
            "— 집단별 표본을 더 모은 뒤 다시 확인하세요.")
        return

    # --- 자기 자료에서 만든 널 기준선 ---
    # 고정 임계값(.85)만으로는 오경보를 통제할 수 없다. 같은 모집단을 이 크기로 나눴을 때
    # 기대되는 최소 φ를 직접 구해, 관측값이 그보다 낮을 때만 '다르다'고 말한다.
    info["null_reference"] = None
    judged_sizes = [r["n"] for r in judged]
    if correlation != "polychoric" and len(judged_sizes) >= 2:
        try:
            pooled = x[np.isin(labels, [r["level"] for r in judged])]
            info["null_reference"] = efa.congruence_null_reference(
                pooled, judged_sizes, k, reference, extraction=extraction,
                rotation=rotation, n_rep=GROUP_NULL_REPS, seed=seed)
        except (ValueError, np.linalg.LinAlgError):
            info["null_reference"] = None

    worst = info["min_congruence"]
    nullref = info.get("null_reference")
    if worst is not None and nullref is not None:
        # 널 기준선이 있으면 그것이 판정 기준이다(고정 .85보다 자료에 맞다).
        floor = nullref["p_low"]
        if worst < floor:
            bad = [f"{r['level']}·F{j+1}(φ={v:.2f})"
                   for r in judged for j, v in enumerate(r["congruence"])
                   if v is not None and v < floor]
            bad += [f"{r['a']}↔{r['b']}·F{j+1}(φ={v:.2f})"
                    for r in pair_rows if not r["provisional"]
                    for j, v in enumerate(r["congruence"])
                    if v is not None and v < floor]
            result["warnings"].append(
                f"요인구조가 집단 간에 재현되지 않습니다: 관측된 최소 Tucker φ={worst:.2f} 가 "
                f"**같은 모집단을 이 크기로 나눴을 때 기대되는 하한**(φ={floor:.2f}, 중앙값 "
                f"{nullref['median']:.2f})보다 낮습니다 — 표본 크기만으로는 설명되지 않는 차이입니다"
                + (f": {', '.join(bad[:6])}{' 외' if len(bad) > 6 else ''}" if bad else "") +
                f". 해당 집단에서는 같은 문항이 다른 것을 재고 있을 수 있어, 집단을 합친 총점 "
                f"비교나 공통 규준 사용은 위험합니다 — 집단별로 따로 보고하거나 확인적 "
                f"요인분석(CFA)으로 측정동일성을 검증하세요. (판정 기준은 하위 5백분위라, "
                f"구조가 완전히 같은 자료도 약 20번에 1번은 여기 걸립니다.)")
        else:
            result["notes"].append(
                f"집단 간 요인구조 차이는 표본 크기로 설명되는 범위 안입니다"
                f"(관측 최소 φ={worst:.2f} ≥ 같은 모집단 기대 하한 {floor:.2f}, "
                f"중앙값 {nullref['median']:.2f}, 무작위 분할 {nullref['n_ok']}회). "
                f"고정 기준(.85)만 보면 낮아 보여도 이 크기에서는 정상 범위입니다.")
        return

    if worst is not None and worst < CONGRUENCE_FAIR:
        bad = [f"{r['level']}·F{j+1}(φ={v:.2f})"
               for r in judged for j, v in enumerate(r["congruence"])
               if v is not None and v < CONGRUENCE_FAIR]
        bad += [f"{r['a']}↔{r['b']}·F{j+1}(φ={v:.2f})"
                for r in pair_rows if not r["provisional"]
                for j, v in enumerate(r["congruence"])
                if v is not None and v < CONGRUENCE_FAIR]
        result["warnings"].append(
            f"요인구조가 집단 간에 재현되지 않습니다(Tucker φ<{CONGRUENCE_FAIR:.2f}): "
            f"{', '.join(bad[:6])}{' 외' if len(bad) > 6 else ''}. 해당 집단에서는 같은 문항이 "
            f"다른 것을 재고 있을 수 있어, 집단을 합친 총점 비교나 공통 규준 사용은 위험합니다 — "
            f"집단별로 따로 보고하거나 확인적 요인분석(CFA)으로 측정동일성을 검증하세요.")
    elif worst is not None and worst < CONGRUENCE_EQUAL:
        result["notes"].append(
            f"집단 간 요인구조가 대체로 유사하지만 완전히 같지는 않습니다"
            f"(최소 Tucker φ={worst:.2f}, {CONGRUENCE_FAIR:.2f}≤φ<{CONGRUENCE_EQUAL:.2f} = '공정한 유사'). "
            f"집단 비교를 논문의 핵심 주장으로 쓸 계획이면 측정동일성 검증을 권합니다.")


def _hypothesis_check(x: np.ndarray, names: List[str], structure: Dict[str, List[str]],
                      k: int, rotated: np.ndarray, groups: np.ndarray,
                      result: Dict, min_loading: float = 0.40) -> None:
    """연구자가 **미리 정한** 하위척도 구조와 실제 요인해를 정면으로 대조한다.

    척도 개발·번안 논문은 언제나 가설을 가지고 시작한다("Q1–Q4는 수면의 질, Q5–Q8은 주간
    기능"). 그런데 EFA는 그 가설을 모른 채 요인을 뽑고, 요인 번호는 고유값 크기 순일 뿐이라
    연구자가 표를 눈으로 대조해야 했다. 여기서 자동으로 답한다:

    - **문항 배정 일치율**: 각 문항이 자기가 속하기로 한 하위척도에 실제로 최대 적재됐는가.
      어긋난 문항은 '어디로 갔는지'까지 이름으로 알려 준다(논문에서 삭제/재배치를 정당화할 근거).
    - **가설 하위척도별 α**(+ 신뢰구간): 요인해의 argmax 배정이 아니라 **연구자가 정한 문항
      묶음** 그대로 계산한다. 배정이 어긋난 문항이 있어도 "원래 의도한 하위척도의 신뢰도"를
      알 수 있어야 하기 때문이다.
    - **목표 일치계수 φ**: 각 경험 요인이 이상적 단순구조(해당 문항 1, 나머지 0)에 얼마나
      가까운지. 가설 요인과 경험 요인의 대응은 |φ| 합 최대로 자동 결정한다(요인 번호는 임의).

    가설 요인 수와 적용된 요인 수가 다르면 그 자체가 중요한 결과이므로 경고로 알린다.
    """
    p = len(names)
    idx = {nm: i for i, nm in enumerate(names)}
    labels = list(structure.keys())
    m = len(labels)

    info: Dict = {"labels": labels, "n_hypothesized": m, "n_applied": k,
                  "counts": [len(structure[lab]) for lab in labels],
                  "agreement": None, "agreement_strict": None,
                  "items": [], "mismatches": [], "weak": [],
                  "alpha": [], "alpha_ci": [],
                  "target_congruence": None, "target_congruence_flipped": None,
                  "matched_factor": None, "min_loading": float(min_loading),
                  "uncovered_items": [], "n_items_checked": 0}
    result["hypothesis"] = info

    # 배정표 만들기(가설에 없는 문항·중복 문항은 명확히 거절/보고).
    member: Dict[str, int] = {}
    for j, lab in enumerate(labels):
        for nm in structure[lab]:
            if nm not in idx:
                raise ValueError(
                    f"설정의 structure에 있는 문항 '{nm}'을 분석 문항에서 찾을 수 없습니다"
                    f"(분석 문항: {', '.join(names)}). 이름 오타나 --items 지정을 확인하세요.")
            if nm in member:
                raise ValueError(
                    f"설정의 structure에서 문항 '{nm}'이 둘 이상의 하위척도에 들어 있습니다 "
                    f"— 각 문항은 하나의 하위척도에만 배정하세요.")
            member[nm] = j
    info["n_items_checked"] = len(member)
    uncovered = [nm for nm in names if nm not in member]
    info["uncovered_items"] = uncovered
    if uncovered:
        result["notes"].append(
            f"가설 구조에 포함되지 않은 문항 {len(uncovered)}개는 배정 대조에서 제외했습니다: "
            f"{', '.join(uncovered)}.")

    # 가설 하위척도별 α — 요인해와 무관하게 '연구자가 정한 문항 묶음'으로 직접 계산.
    for lab in labels:
        cols = [idx[nm] for nm in structure[lab]]
        a = efa.cronbach_alpha(x[:, cols]) if len(cols) >= 2 else None
        info["alpha"].append(a)
        ci = efa.alpha_ci_feldt(a, x.shape[0], len(cols)) if a is not None else None
        info["alpha_ci"].append(list(ci) if ci else None)

    if m != k:
        result["warnings"].append(
            f"가설 요인 수({m}개: {', '.join(labels)})와 실제 적용된 요인 수({k}개)가 다릅니다. "
            f"자료가 가설과 다른 차원 구조를 지지한다는 뜻이며, 이는 그 자체로 보고할 결과입니다 "
            f"— 문항 배정 대조는 요인 수가 같을 때만 가능하니 `--n-factors {m}` 으로 가설 구조를 "
            f"강제해 비교해 보세요(가설 하위척도별 α는 위 표에 그대로 제공됩니다).")
        return

    # 목표행렬은 **가설이 다루는 문항만**으로 만든다. 가설 밖 문항을 남겨 두면 그 적재가
    # 분모에는 들어가고 분자에는 안 들어가 φ를 기계적으로 끌어내린다(완벽한 24문항 3요인
    # 구조라도 가설 밖 문항 6개가 있으면 φ가 .87까지 떨어져 '유사'로 잘못 읽힌다).
    covered = np.array([i for i, nm in enumerate(names) if nm in member], dtype=int)
    target = np.zeros((covered.size, m))
    for pos, i in enumerate(covered):
        target[pos, member[names[i]]] = 1.0
    perm, phi = efa.match_factors(rotated[covered], target)
    info["matched_factor"] = [int(c) + 1 for c in perm]      # 1-based 표시용
    # 부호는 요인 부호 관례(절댓값 최대 적재를 양수로)에 좌우돼 임의다 — 같은 자료가
    # +0.48도 −0.48도 될 수 있으므로 크기만 보고하고, 부호가 뒤집힌 경우는 따로 알린다.
    info["target_congruence"] = [None if not np.isfinite(v) else float(abs(v)) for v in phi]
    info["target_congruence_flipped"] = [bool(np.isfinite(v) and v < 0) for v in phi]
    info["min_loading"] = float(min_loading)

    # 경험 요인 → 가설 요인 역매핑으로 문항 배정을 대조한다.
    factor_to_hypo = {int(c): j for j, c in enumerate(perm)}
    hit = 0
    strict_hit = 0
    rows: List[Dict] = []
    for nm in names:
        if nm not in member:
            continue
        j = member[nm]
        i = idx[nm]
        top = int(groups[i])
        landed = factor_to_hypo.get(top)
        lam_hyp = float(rotated[i, int(perm[j])])
        lam_top = float(rotated[i, top])
        matched = landed == j
        # 배정만 맞고 적재가 기준 미만이면 '실렸다'고 말할 수 없다. argmax만 보면
        # 순수 잡음 문항(λ=0.20)도 '일치'로 세어져 '가설 구조가 재현되었다'가 찍힌다.
        strong = matched and abs(lam_hyp) >= min_loading
        hit += int(matched)
        strict_hit += int(strong)
        rows.append({
            "item": nm,
            "hypothesized": labels[j],
            "landed_on": labels[landed] if landed is not None else f"F{top+1}",
            "loading": lam_top,
            "loading_on_hypothesized": lam_hyp,
            "status": "ok" if strong else ("weak" if matched else "moved"),
        })
    # 요인 이름을 가설 하위척도명으로 자동 부여한다. 요인 번호(F1/F2)는 고유값 크기 순일
    # 뿐이라 보고서 전체에서 의미를 읽을 수 없었는데, 대응은 이미 계산해 놓고 있었다.
    factor_names = [None] * k
    for j, c in enumerate(perm):
        factor_names[int(c)] = labels[j]
    result["factor_names"] = factor_names
    info["items"] = rows
    info["mismatches"] = [r for r in rows if r["status"] == "moved"]
    info["weak"] = [r for r in rows if r["status"] == "weak"]
    n_it = len(rows)
    info["agreement"] = hit / n_it if n_it else None
    info["agreement_strict"] = strict_hit / n_it if n_it else None

    if info["mismatches"]:
        detail = ", ".join(f"{d['item']}({d['hypothesized']}→{d['landed_on']}, "
                           f"λ={d['loading']:+.2f})" for d in info["mismatches"][:6])
        result["warnings"].append(
            f"가설과 다른 하위척도에 실린 문항이 {len(info['mismatches'])}개 있습니다"
            f"({info['agreement']*100:.0f}% 일치): {detail}"
            f"{' 외' if len(info['mismatches']) > 6 else ''}. 문항 문구가 두 개념을 함께 건드리는지 "
            f"확인하고, 재배치·수정·삭제 중 무엇을 할지 근거와 함께 보고하세요.")
    if info["weak"]:
        detail = ", ".join(f"{d['item']}(λ={d['loading_on_hypothesized']:+.2f})"
                           for d in info["weak"][:6])
        result["warnings"].append(
            f"가설한 하위척도에 배정되기는 했지만 적재가 기준(|λ|≥{min_loading:.2f})에 못 미치는 "
            f"문항이 {len(info['weak'])}개 있습니다: {detail}"
            f"{' 외' if len(info['weak']) > 6 else ''}. '가설 구조가 재현되었다'고 보고하기 전에 "
            f"이 문항들을 검토하세요 — 배정은 최대적재(argmax)만 보므로 적재가 0.2인 잡음 문항도 "
            f"어딘가에는 배정됩니다.")
    if not info["mismatches"] and not info["weak"]:
        result["notes"].append(
            f"모든 문항({n_it}개)이 가설한 하위척도에 |λ|≥{min_loading:.2f}로 실렸습니다 — "
            f"가설 요인구조가 자료에서 재현되었습니다.")


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
            bootstrap: int = 0,
            group_labels: Optional[Sequence] = None,
            group_name: Optional[str] = None,
            structure: Optional[Dict[str, List[str]]] = None) -> Dict:
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
    # det==0(완전 특이 = 다중공선성의 최악)이 조건에서 빠지면 안 된다 — 가장 심한 경우에
    # 경고가 오히려 꺼지는 역전이 생긴다. 원인 열까지 SMC로 지목해 준다.
    if det_r < 1e-5:
        culprit = ""
        try:
            smc = efa.squared_multiple_correlations(r)
            worst = [names[i] for i in np.argsort(-smc)[:3] if smc[i] > 0.95]
            if worst:
                culprit = (f" 다른 문항들로 거의 완전히 설명되는 열: "
                           f"{', '.join(worst)} (총점·합계·중복 문항이 아닌지 확인하세요).")
        except (np.linalg.LinAlgError, ValueError):
            pass
        result["warnings"].append(
            f"상관행렬 행렬식이 매우 작습니다(det={det_r:.2e}). 문항 간 다중공선성(거의 중복되는 "
            f"문항)이 의심되니 중복 문항을 확인하세요.{culprit}")

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
            result["notes"].append("Velicer MAP 생략: 편상관 계산에 실패했습니다(특이행렬).")
    else:
        # 조용히 사라지면 사용자는 '세 기준을 나란히 본다'는 약속이 깨진 걸 눈치채지 못한다.
        why = ("상관행렬이 양의 정부호가 아니어서" if not pos_def
               else f"문항이 {p}개뿐이라(3개 이상 필요)")
        result["notes"].append(
            f"Velicer MAP 기준 생략: {why} 편상관을 계산할 수 없습니다 — 요인 수 판단은 "
            f"고유값·평행분석으로만 했습니다.")

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
    struct_mat = rotated @ phi if phi is not None else rotated

    # 공통성·설명분산: 직교회전은 적재제곱합, 사교(promax)회전은 구조행렬 S=PΦ 사용.
    if applied_rotation == "promax" and phi is not None:
        comm = np.einsum("ij,ij->i", rotated, struct_mat)  # diag(P Φ Pᵀ)
        ss_loadings = (struct_mat ** 2).sum(axis=0)
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
    # 직교회전이면 struct_mat==loadings 이므로 값이 동일하다.
    omega = efa.omega_by_group(struct_mat, groups)
    result["omega"] = [omega.get(f) for f in range(k)]
    # 요인별 Cronbach's α — 표본 원점수 기반(적재 낙관적 ω와 나란히 보고).
    alpha = efa.alpha_by_group(x, groups, k)
    result["alpha"] = [alpha.get(f) for f in range(k)]
    # α의 Feldt 95% 신뢰구간 — APA/COSMIN 보고 지침이 요구하는 값. 점추정 .80이
    # "실제로는 .70을 못 넘을 수도" 있다는 사실은 구간을 봐야만 드러난다.
    n_by_factor = [int(np.sum(groups == f)) for f in range(k)]
    result["alpha_ci"] = [
        (list(ci) if (ci := efa.alpha_ci_feldt(alpha.get(f), n, n_by_factor[f])) else None)
        for f in range(k)
    ]
    # ω(적재 기반)와 α(응답분산 기반)가 크게 어긋나면 계수 자체보다 **자료에 문제가 있다**는
    # 신호다. 두 값을 나란히 인쇄만 하고 침묵하면, 사용자는 높은 쪽(ω)을 보고 넘어간다.
    # 임계값은 추출 방식과 하위척도 길이에 맞춰 조정한다. PCA의 ω는 정의상 α보다 낙관적이고
    # 그 격차는 문항이 적을수록 커진다(Spearman-Brown) — 고정 0.20을 쓰면 2문항 하위척도가
    # 완전히 정상인데도 100% 발화했다(모의 실측: λ=.60, 2문항, PCA에서 20/20).
    def _gap_threshold(f: int) -> float:
        base = OMEGA_ALPHA_GAP
        if extraction == "pca":
            base += 0.15                    # PCA 낙관 보정
            n_items = n_by_factor[f] if f < len(n_by_factor) else 0
            if n_items <= 3:                # 짧은 하위척도일수록 격차가 커진다
                base += 0.15
        return base

    gap = [(f, omega.get(f), alpha.get(f)) for f in range(k)
           if omega.get(f) is not None and alpha.get(f) is not None
           and abs(omega[f] - alpha[f]) >= _gap_threshold(f)]
    if gap:
        detail = ", ".join(f"F{f+1}(ω={o:.2f} vs α={a:.2f}, 차이 {abs(o-a):.2f})"
                           for f, o, a in gap)
        result["warnings"].append(
            f"ω와 Cronbach α가 크게 다른 요인이 있습니다: {detail}. ω는 적재량에서, α는 실제 "
            f"응답분산에서 계산하므로 이만큼 벌어지면 ⑴ 역문항 미처리, ⑵ 척도 범위가 다른 열"
            f"(나이·총점 등)의 혼입, ⑶ 요인 배정 오류 중 하나입니다 — α가 0 근처이거나 음수면 "
            f"그 하위척도로 총점을 만들지 마세요.")

    weak = [f for f in range(k)
            if alpha.get(f) is not None and alpha[f] >= ALPHA_GOOD
            and result["alpha_ci"][f] is not None and result["alpha_ci"][f][0] < ALPHA_GOOD]
    if weak:
        detail = ", ".join(
            f"F{f+1}(α={alpha[f]:.2f}, 95% CI 하한 {result['alpha_ci'][f][0]:.2f})" for f in weak)
        result["notes"].append(
            f"신뢰도 점추정은 {ALPHA_GOOD:.2f}를 넘지만 신뢰구간 하한은 그 아래인 요인: {detail}. "
            f"표본이 작아 'α≥{ALPHA_GOOD:.2f} 달성'이라고 단정할 수 없습니다 — 논문에는 "
            f"점추정과 구간을 함께 보고하세요.")
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
            extraction=extraction, rotation=applied_rotation, pa_reference=pa_ref,
            correlation=correlation, groups=groups)
        result["bootstrap"] = {
            "n_boot": bs.n_boot,
            "n_ok": bs.n_ok,
            "conf": bs.conf,
            "loading_lo": bs.lo.tolist(),
            "loading_hi": bs.hi.tolist(),
            "loading_mean": bs.mean.tolist(),
            "alpha_ci": [list(c) if c else None for c in bs.alpha_ci],
            "omega_ci": [list(c) if c else None for c in bs.omega_ci],
            "pa_agreement": bs.pa_agreement,
            "k_counts": {str(kk): vv for kk, vv in sorted(bs.k_counts.items())},
            "n_nonconverged": bs.n_nonconverged,
            "n_heywood": bs.n_heywood,
        }
        if bs.n_nonconverged:
            result["warnings"].append(
                f"부트스트랩 재표본 {bs.n_nonconverged}개는 {extraction.upper()} 추출이 "
                f"수렴하지 않아 제외했습니다(성공 {bs.n_ok}/{bs.n_boot}).")
        if bs.n_heywood:
            result["notes"].append(
                f"부트스트랩 재표본 {bs.n_heywood}/{bs.n_ok}개에서 Heywood 케이스(공통성이 경계에 "
                f"도달)가 발생했습니다. 경계해도 유효한 해라 구간에 포함했지만, 비율이 높으면"
                f"(대략 10% 이상) 요인 수가 과다하거나 표본이 부족하다는 신호이니 구간을 "
                f"확정적으로 해석하지 마세요.")
        # 유효 재표본이 적으면 '95% 신뢰구간'이라는 라벨 자체가 거짓말이 된다. 3개짜리
        # 백분위 구간은 폭이 0.02로 나와 건강한 실행보다 오히려 더 확신에 차 보이고,
        # 점추정을 구간 밖에 두기도 한다 — 그대로 논문 표에 복사되면 최악이다.
        result["bootstrap"]["reliable"] = bs.n_ok >= BOOTSTRAP_MIN_OK
        if bs.n_ok < BOOTSTRAP_MIN_OK:
            # 원인을 정확히 짚는다(비수렴인데 '분산 0 문항'을 찾아 헤매게 하지 않는다).
            causes = []
            if bs.n_nonconverged:
                causes.append(f"{extraction.upper()} 비수렴 {bs.n_nonconverged}개")
            other = bs.n_boot - bs.n_ok - bs.n_nonconverged
            if other:
                causes.append(f"분산 0 문항·특이행렬 등 {other}개")
            why = ("(" + ", ".join(causes) + ")") if causes else ""
            result["warnings"].append(
                f"부트스트랩 신뢰구간을 만들지 않았습니다: 유효 재표본이 {bs.n_ok}개뿐이라"
                f"(요청 {bs.n_boot}개{why}) 백분위 구간이 의미를 갖지 못합니다"
                f"(최소 {BOOTSTRAP_MIN_OK}개 필요). 요인 수를 줄이거나 추출 방식을 바꿔 보세요.")
        other_fail = bs.n_boot - bs.n_ok - bs.n_nonconverged
        if other_fail > 0:
            result["warnings"].append(
                f"부트스트랩 재표본 {bs.n_boot}개 중 {other_fail}개가 계산 불가로 "
                f"제외됐습니다(재표본에서 분산 0 문항이나 특이행렬 발생) — 구간이 낙관적일 수 있습니다.")
        if bs.pa_agreement is not None and bs.pa_agreement < 0.7:
            result["notes"].append(
                f"요인 수가 불안정합니다: 평행분석이 재표본의 {bs.pa_agreement*100:.0f}%에서만 "
                f"{k}개 요인을 지지했습니다(분포: "
                f"{', '.join(f'{kk}요인 {vv}회' for kk, vv in sorted(bs.k_counts.items()))}). "
                f"요인 수를 확정적으로 보고하지 말고 표본을 늘리는 것을 검토하세요.")

    # --- 가설(a priori) 요인구조 대조 ---
    result["hypothesis"] = None
    result["factor_names"] = None
    if structure:
        _hypothesis_check(x, names, structure, k, rotated, groups, result, min_loading)

    # --- 집단별 구조 재현성(Tucker 일치계수) ---
    # 다기관·성별·투여군에서 "같은 요인구조인가"를 탐색 단계에서 선별한다.
    result["group_replicability"] = None
    if group_labels is not None:
        _group_replicability(x, names, group_labels, k, rotated, extraction,
                             applied_rotation, correlation, result, groups, seed)
        if result.get("group_replicability") is not None:
            result["group_replicability"]["column"] = group_name

    # --- 문항별 진단 플래그 ---
    desc = result["item_descriptives"]
    msa = result["kmo"]["per_item"] if result.get("kmo") else [None] * p
    comm_low = (COMMUNALITY_LOW_COMMON if extraction in ("paf", "ml")
                else COMMUNALITY_LOW)
    result["communality_threshold"] = comm_low
    flags: List[Dict] = []
    for i, name in enumerate(names):
        row = rotated[i]
        abs_row = np.abs(row)
        top = int(groups[i])
        loads = np.where(abs_row >= min_loading)[0]
        problems = []
        if msa[i] is not None and msa[i] < MSA_POOR:
            problems.append(f"MSA<{MSA_POOR}")
        if comm[i] < comm_low:
            problems.append(f"공통성<{comm_low:g}")
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
