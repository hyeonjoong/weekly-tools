"""설문 분석 핵심 로직: 문항 기술통계 · 결측 요약 · 역문항 처리 ·
하위척도 점수 · Cronbach α · 수정된 문항-총점 상관 · 문항 제거 시 α.

결과는 평범한 dict로 반환하여 report.py가 텍스트/JSON으로 렌더링한다.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from . import compare, factor, paired, quality, stats
from .config import SurveyConfig, band_index, band_label
from .dataio import SurveyData


def _reverse_value(v: float, scale_min: float, scale_max: float) -> float:
    """역문항 재코딩: x' = (min+max) - x."""
    return (scale_min + scale_max) - v


def item_descriptives(data: SurveyData, item: str) -> Dict[str, object]:
    """한 문항의 기술통계 + 결측 요약(원자료 raw 기준)."""
    raw = data.rows_value(item)
    present = [v for v in raw if v is not None]
    n_missing = len(raw) - len(present)
    return {
        "item": item,
        "n": len(present),
        "n_missing": n_missing,
        "missing_pct": round(100.0 * n_missing / len(raw), 1) if raw else 0.0,
        "mean": stats.mean(present),
        "sd": stats.stdev(present),
        "median": stats.median(present),
        "min": min(present) if present else None,
        "max": max(present) if present else None,
        "q1": stats.quantile(present, 0.25),
        "q3": stats.quantile(present, 0.75),
        "skew": stats.skewness(present),
        "kurtosis": stats.kurtosis(present),
    }


def _recoded_matrix(
    data: SurveyData, items: List[str], cfg: SurveyConfig
) -> List[List[Optional[float]]]:
    """응답자 x 문항 행렬(역문항 재코딩 적용). 결측은 None 유지."""
    rev = cfg.reverse_set()
    matrix: List[List[Optional[float]]] = []
    for row in data.rows:
        vals: List[Optional[float]] = []
        for it in items:
            v = row.get(it)
            if v is not None and it in rev:
                v = _reverse_value(v, cfg.scale_min, cfg.scale_max)
            vals.append(v)
        matrix.append(vals)
    return matrix


def _complete_rows(matrix: List[List[Optional[float]]]) -> List[List[float]]:
    """결측이 하나도 없는 응답자 행만 추린다(listwise)."""
    return [row for row in matrix if all(v is not None for v in row)]  # type: ignore[misc]


def subscale_scores(
    data: SurveyData, items: List[str], cfg: SurveyConfig
) -> List[Optional[float]]:
    """응답자별 하위척도 점수(역문항 적용 후).

    score_method="mean": 가용 문항의 평균.
    score_method="sum" : 비례배분 총합(가용문항 평균 × 문항수). 완전응답이면 실제 합과 동일.

    응답 문항 비율이 min_valid_ratio 미만이면 None(점수 없음).
    """
    matrix = _recoded_matrix(data, items, cfg)
    k = len(items)
    scores: List[Optional[float]] = []
    for row in matrix:
        present = [v for v in row if v is not None]
        if k == 0 or (len(present) / k) < cfg.min_valid_ratio or not present:
            scores.append(None)
        else:
            avg = sum(present) / len(present)
            scores.append(avg * k if cfg.score_method == "sum" else avg)
    return scores


def _inter_item_corrs(columns: List[List[float]]) -> List[float]:
    """완전응답자 기준, 서로 다른 문항 쌍의 피어슨 상관 목록(분산 0 쌍은 제외)."""
    k = len(columns)
    out: List[float] = []
    for i in range(k):
        for j in range(i + 1, k):
            r = stats.pearson(columns[i], columns[j])
            if r is not None:
                out.append(r)
    return out


def analyze_subscale(
    data: SurveyData, name: str, items: List[str], cfg: SurveyConfig,
    conf: float = 0.95,
) -> Dict[str, object]:
    """하위척도 1개에 대한 신뢰도 분석."""
    matrix = _recoded_matrix(data, items, cfg)
    complete = _complete_rows(matrix)
    k = len(items)
    n_complete = len(complete)

    alpha = None
    alpha_ci = None
    alpha_std = None
    omega = None
    omega_loadings: Dict[str, Optional[float]] = {}
    omega_heywood = False
    sem = None
    mdc95 = None
    sd_total_complete = None
    mean_inter_item = min_inter_item = max_inter_item = None
    item_total: Dict[str, Optional[float]] = {}
    alpha_if_deleted: Dict[str, Optional[float]] = {}

    if k >= 2 and n_complete >= 2:
        columns = [[complete[r][i] for r in range(n_complete)] for i in range(k)]
        alpha = stats.cronbach_alpha(columns)
        alpha_ci = stats.cronbach_alpha_ci(alpha, n_complete, k, conf)
        # 측정의 표준오차: 완전응답자 총점(점수 지표) SD × sqrt(1-α)
        complete_totals = [
            (sum(columns[i][r] for i in range(k)) if cfg.score_method == "sum"
             else sum(columns[i][r] for i in range(k)) / k)
            for r in range(n_complete)
        ]
        sd_total_complete = stats.stdev(complete_totals)
        sem = stats.sem_from_alpha(sd_total_complete, alpha)
        # MDC95 (최소검출가능변화, 95%): 1.96·√2·SEM — 개인 점수의 실질적 변화 판정용.
        if sem is not None:
            mdc95 = 1.959963984540054 * math.sqrt(2.0) * sem
        # McDonald's ω_total (단일요인 congeneric 모형). α의 타우동등성 가정을 완화한
        # 신뢰도 추정치 — 문항 3개 이상·수렴 시에만 보고(실패 시 조용히 None).
        om = factor.omega_total(columns)
        if om is not None:
            omega = om["omega"]
            omega_heywood = bool(om["heywood"])
            omega_loadings = {
                it: ld for it, ld in zip(items, om["loadings"])  # type: ignore[arg-type]
            }
        iics = _inter_item_corrs(columns)
        if iics:
            mean_inter_item = stats.mean(iics)
            min_inter_item = min(iics)
            max_inter_item = max(iics)
            # 표준화 α = k·r̄ / (1+(k-1)·r̄) — 문항 SD가 다를 때 참고.
            # α가 계산불가(총점 분산 0)이거나 평균 문항간 상관이 양수가 아니면
            # 신뢰도로서 의미가 없고(음수 r̄은 분모를 폭발시킴), 표기하지 않는다.
            rbar = mean_inter_item
            if alpha is not None and rbar > 0:
                alpha_std = (k * rbar) / (1.0 + (k - 1) * rbar)
        for i, it in enumerate(items):
            # 수정된 문항-총점 상관: 문항 i vs 나머지 문항 합
            this_item = columns[i]
            rest_total = [
                sum(columns[j][r] for j in range(k) if j != i) for r in range(n_complete)
            ]
            item_total[it] = stats.pearson(this_item, rest_total)
            # 문항 i 제거 시 alpha
            if k - 1 >= 2:
                remaining = [columns[j] for j in range(k) if j != i]
                alpha_if_deleted[it] = stats.cronbach_alpha(remaining)
            else:
                alpha_if_deleted[it] = None

    scores = subscale_scores(data, items, cfg)
    valid_scores = [s for s in scores if s is not None]
    score_ci = stats.t_ci_mean(valid_scores, conf) if len(valid_scores) >= 2 else None

    # 바닥/천장 효과: 척도 범위가 선언된 경우, 가능한 최소/최대 점수에 몰린 비율.
    possible_min = possible_max = None
    floor = ceiling = None
    if cfg.scale_min is not None and cfg.scale_max is not None and k > 0:
        if cfg.score_method == "sum":
            possible_min = k * cfg.scale_min
            possible_max = k * cfg.scale_max
        else:
            possible_min = cfg.scale_min
            possible_max = cfg.scale_max
        if valid_scores:
            tol = 1e-9
            n_floor = sum(1 for s in valid_scores if abs(s - possible_min) <= tol)
            n_ceil = sum(1 for s in valid_scores if abs(s - possible_max) <= tol)
            ns = len(valid_scores)
            floor = {"n": n_floor, "pct": round(100.0 * n_floor / ns, 1)}
            ceiling = {"n": n_ceil, "pct": round(100.0 * n_ceil / ns, 1)}

    # 임상 심각도 구간(config 의 severity_bands). 점수 단위(mean/sum)에 그대로 적용한다.
    bands_spec = cfg.severity_bands.get(name) or []
    band_scores: List[Optional[str]] = []
    bands_table: List[Dict[str, object]] = []
    n_unbanded = 0
    bands_out_of_range = False
    bands_range_unknown = False
    # 점수는 났지만 문항 일부만 답해 '비례배분(prorate)'된 응답자 수. 임상 구간 기준값은
    # 보통 전 문항 응답을 전제로 만들어졌으므로, 분포표에 몇 명이 추정치인지 밝힌다.
    n_prorated = sum(
        1
        for row, sc in zip(_recoded_matrix(data, items, cfg), scores)
        if sc is not None and any(v is None for v in row)
    )
    if bands_spec:
        band_scores = [band_label(s, bands_spec) for s in scores]
        n_scored_b = len(valid_scores)
        # 구간 **인덱스** 로 센다. 라벨로 세면 서로 다른 두 구간이 같은 라벨을 쓸 때
        # (예: [0,2,"낮음"], [6,8,"낮음"]) 두 행이 합산값을 각각 찍어 합계가 100%를 넘는다.
        counts = [0] * len(bands_spec)
        idx_of = band_index(scores, bands_spec)
        for s, bi in zip(scores, idx_of):
            if s is None:
                continue
            if bi is None:
                n_unbanded += 1
            else:
                counts[bi] += 1
        for (lo, hi, lab), cnt in zip(bands_spec, counts):
            bands_table.append(
                {
                    "label": lab,
                    "min": lo,
                    "max": hi,
                    "n": cnt,
                    "pct": round(100.0 * cnt / n_scored_b, 1) if n_scored_b else 0.0,
                }
            )
        # 구간이 '가능한 점수 범위' 밖까지 뻗어 있으면 score_method 불일치가 거의 확실하다
        # (예: ISI 총점 기준 0~28 구간을 mean 점수 0~4 에 적용). 조용히 오분류되면
        # 심각도 표 전체가 틀리므로 플래그로 노출한다.
        if possible_min is not None and possible_max is not None:
            tol = 1e-9
            lo_min = min(b[0] for b in bands_spec)
            hi_max = max(b[1] for b in bands_spec)
            bands_out_of_range = (
                lo_min < possible_min - tol or hi_max > possible_max + tol
            )
        else:
            # scale_min/scale_max 가 없으면 '가능한 점수 범위'를 몰라 위 점검을 할 수 없다.
            # 이때 mean/sum 단위를 잘못 적어도 전원이 최하위 구간에 몰릴 뿐 아무 신호가
            # 없으므로, 점검 불가 자체를 표면화한다.
            bands_range_unknown = True

    # 응답이 하나도 없는(전부 결측) 문항 — 이런 문항은 점수에 기여하지 못하므로
    # 하위척도 점수가 사실상 더 적은 문항으로 계산된다(오해 방지용 경고).
    items_no_data = [it for it in items if len(data.present_values(it)) == 0]

    return {
        "name": name,
        "items": items,
        "n_items": k,
        "items_no_data": items_no_data,
        "n_complete": n_complete,
        "n_excluded_listwise": data.n_respondents - n_complete,
        "alpha": alpha,
        "alpha_ci": list(alpha_ci) if alpha_ci else None,
        "alpha_std": alpha_std,
        "omega": omega,
        "omega_heywood": omega_heywood,
        "omega_loadings": omega_loadings,
        "sem": sem,
        "mdc95": mdc95,
        "sd_total_complete": sd_total_complete,
        "mean_inter_item_r": mean_inter_item,
        "min_inter_item_r": min_inter_item,
        "max_inter_item_r": max_inter_item,
        "item_total_corr": item_total,
        "alpha_if_deleted": alpha_if_deleted,
        "score_method": cfg.score_method,
        "score_mean": stats.mean(valid_scores),
        "score_sd": stats.stdev(valid_scores),
        "score_ci": list(score_ci) if score_ci else None,
        "possible_min": possible_min,
        "possible_max": possible_max,
        "floor": floor,
        "ceiling": ceiling,
        "bands": bands_table,
        "n_unbanded": n_unbanded,
        "n_prorated": n_prorated,
        "bands_out_of_range": bands_out_of_range,
        "bands_range_unknown": bands_range_unknown,
        "n_scored": len(valid_scores),
        "scores": scores,
        "band_scores": band_scores,
    }


def pair_keys(
    data: SurveyData, pair_id_columns: Optional[List[str]] = None
) -> List[str]:
    """응답자 행별 짝짓기 키(ID 값 조합). 값이 하나도 없으면 빈 문자열.

    ID 컬럼이 여러 개면(예: 기관+환자번호) 조합을 하나의 식별자로 본다.
    --pair-id 로 일부만 골라 쓸 수도 있다(예: ID 는 방문마다 달라지고 환자번호만 같은 경우).
    """
    cols = [c for c in (pair_id_columns or data.id_columns) if c in data.id_columns]
    out: List[str] = []
    for ids in data.id_values:
        parts = [str(ids.get(c, "")).strip() for c in cols]
        out.append(" / ".join(parts) if any(parts) else "")
    if len(out) < data.n_respondents:
        out += [""] * (data.n_respondents - len(out))
    return out


def _group_alphas(
    data: SurveyData, cfg: SurveyConfig, items: List[str], group_values: List[str],
) -> Dict[str, Optional[float]]:
    """집단별 Cronbach α(그 집단의 완전응답자만).

    같은 척도라도 집단마다 신뢰도가 크게 다르면(예: 한쪽만 α<.6) 점수 비교의 전제가
    흔들린다 — 측정불변성의 완전한 검증은 아니지만 값싸고 유용한 점검이다.
    """
    matrix = _recoded_matrix(data, items, cfg)
    k = len(items)
    out: Dict[str, Optional[float]] = {}
    buckets: Dict[str, List[List[float]]] = {}
    for row, gv in zip(matrix, group_values):
        lab = (gv or "").strip()
        if not lab:
            continue
        if all(v is not None for v in row):
            buckets.setdefault(lab, []).append([float(v) for v in row])  # type: ignore[arg-type]
        else:
            buckets.setdefault(lab, [])
    for lab, rowsg in buckets.items():
        n = len(rowsg)
        if k < 2 or n < 2:
            out[lab] = None
            continue
        columns = [[rowsg[r][i] for r in range(n)] for i in range(k)]
        out[lab] = stats.cronbach_alpha(columns)
    return out


def response_frequencies(
    data: SurveyData, items: List[str], scale_min: float, scale_max: float
) -> Optional[Dict[str, object]]:
    """문항별 응답 선택지 빈도표.

    scale_min/scale_max 가 정수이고 선택지 수가 21개 이하일 때만 계산한다.
    각 문항에 대해 각 정수 수준의 응답 수를 세고, 수준에 해당하지 않는(비정수/범위밖)
    응답은 '기타'로 묶는다. 반환 None이면 빈도표를 낼 수 없는 경우.
    """
    if scale_min is None or scale_max is None:
        return None
    if not (float(scale_min).is_integer() and float(scale_max).is_integer()):
        return None
    lo, hi = int(scale_min), int(scale_max)
    # 선택지 개수를 정수 산술로 먼저 확인한다. range(...) 를 리스트로 물질화하기 전에
    # 걸러야 거대한 척도 범위(예: scale_max=1e11)에서 OOM/행이 발생하지 않는다.
    n_levels = hi - lo + 1
    if not (1 <= n_levels <= 21):
        return None
    levels = list(range(lo, hi + 1))
    level_set = set(float(v) for v in levels)
    rows: List[Dict[str, object]] = []
    for it in items:
        present = data.present_values(it)
        counts = {v: 0 for v in levels}
        other = 0
        for val in present:
            if val in level_set:
                counts[int(val)] += 1
            else:
                other += 1
        rows.append({"item": it, "counts": counts, "other": other, "n": len(present)})
    return {"levels": levels, "items": rows}


def analyze(
    data: SurveyData,
    cfg: SurveyConfig,
    conf: float = 0.95,
    item_freq: bool = False,
    quality_check: bool = False,
    longstring_min: Optional[int] = None,
    use_nonparam: bool = False,
    time_pre: Optional[str] = None,
    time_post: Optional[str] = None,
    pair_id_columns: Optional[List[str]] = None,
) -> Dict[str, object]:
    """전체 분석 실행. config에 명시된 모든 문항/하위척도를 검증·분석한다.

    conf: 신뢰구간(α CI, 점수 평균 CI)의 신뢰수준(기본 0.95).
    item_freq: True면 문항별 응답 선택지 빈도표를 함께 계산(척도 범위 정수일 때).
    quality_check: True면 응답자별 부주의응답 선별 지표(longstring·IRV·결측)를 계산.
    longstring_min: longstring 플래그 기준(None이면 max(3, ceil(k/2)) 휴리스틱).
    use_nonparam: True면 순위 기반 검정(Mann-Whitney/Kruskal-Wallis/Wilcoxon)을 함께 계산.
    time_pre/time_post: 사전-사후 비교에 쓸 시점 라벨(시점이 3개 이상이면 필수).
    pair_id_columns: 사전-사후 짝짓기에 쓸 ID 컬럼(없으면 --id-col 전체를 조합해 사용).
    """
    # config 문항이 데이터에 실제로 있는지 확인
    missing_cols = [it for it in cfg.all_items() if it not in data.columns]
    if missing_cols:
        # CSV에는 있는데 --id-col/--group-col 로 분석에서 뺀 컬럼이면 '없다'고만 말하면
        # 사용자가 헤더를 아무리 봐도 원인을 못 찾는다. 두 경우를 구분해 안내한다.
        header = set(getattr(data, "source_columns", []) or [])
        excluded = [c for c in missing_cols if c in header]
        truly_missing = [c for c in missing_cols if c not in header]
        parts = []
        if truly_missing:
            parts.append("CSV에 없습니다: " + ", ".join(truly_missing))
        if excluded:
            parts.append(
                "--id-col/--group-col 로 분석에서 제외되었습니다: "
                + ", ".join(excluded)
                + " (그 옵션에서 빼거나 config에서 지우세요)"
            )
        raise ValueError("config에 적힌 문항이 " + " / ".join(parts))

    items_all = cfg.all_items()

    # 척도 범위가 선언된 경우, 범위를 벗어난 값(입력 오류 가능)을 점검한다.
    out_of_range: List[Dict[str, object]] = []
    if cfg.scale_min is not None and cfg.scale_max is not None:
        for it in items_all:
            bad = [v for v in data.present_values(it)
                   if v < cfg.scale_min or v > cfg.scale_max]
            if bad:
                # 정수면 정수로 저장 -> 텍스트/JSON 출력이 일치(9.0 대신 9).
                examples = [int(v) if float(v).is_integer() else v
                            for v in sorted(set(bad))[:5]]
                out_of_range.append({"item": it, "count": len(bad),
                                     "examples": examples})

    # 값이 있는데 숫자로 못 읽은 셀 — 조용히 '결측'으로 묻으면 N·결측률·α가 틀어진다.
    # (텍스트 라벨 '매우그렇다', 소수점 콤마 '3,5', '3점', 제로폭공백, 엑셀 아포스트로피 등)
    unreadable = [
        {"item": it, "count": int(data.unreadable[it]["count"]),
         "examples": list(data.unreadable[it]["examples"])}
        for it in items_all
        if it in data.unreadable
    ]

    # 분석 문항에 응답이 하나도 없는 행 — Qualtrics/구글폼이 헤더 아래 남기는
    # 문항문구·ImportId 메타데이터 행이 '응답자'로 잡히면 N과 결측률이 부풀려진다.
    empty_rows = [
        (data.source_lines[i] if i < len(data.source_lines) else i + 2)
        for i, row in enumerate(data.rows)
        if all(row.get(it) is None for it in items_all)
    ]

    descriptives = [item_descriptives(data, it) for it in items_all]
    subscales = [
        analyze_subscale(data, name, items, cfg, conf)
        for name, items in cfg.subscales.items()
    ]

    # 집단 비교(--group-col 지정 시). 하위척도 점수를 집단별로 나눠 Welch 검정·효과크기.
    group_compare = None
    group_column = getattr(data, "group_column", None)
    if group_column:
        gvals = list(getattr(data, "group_values", []) or [])
        # 길이 방어: 어떤 이유로든 라벨이 모자라면 빈 문자열(미분류)로 채운다.
        if len(gvals) < data.n_respondents:
            gvals += [""] * (data.n_respondents - len(gvals))
        galphas = {
            str(s["name"]): _group_alphas(data, cfg, list(s["items"]), gvals)  # type: ignore[arg-type]
            for s in subscales
        }
        group_compare = compare.compare_subscales(
            subscales, gvals, group_column, conf, galphas, use_nonparam
        )

    # 사전-사후(반복측정) 비교(--time-col 지정 시).
    prepost = None
    time_column = getattr(data, "time_column", None)
    if time_column:
        tvals = list(getattr(data, "time_values", []) or [])
        if len(tvals) < data.n_respondents:
            tvals += [""] * (data.n_respondents - len(tvals))
        keys = pair_keys(data, pair_id_columns)
        gvals_p = list(getattr(data, "group_values", []) or [])
        if gvals_p and len(gvals_p) < data.n_respondents:
            gvals_p += [""] * (data.n_respondents - len(gvals_p))
        prepost = paired.compare_prepost(
            subscales,
            keys,
            tvals,
            time_column,
            pre=time_pre,
            post=time_post,
            conf=conf,
            mcid=dict(cfg.mcid),
            group_values=gvals_p if group_column else None,
            group_column=group_column,
            use_nonparam=use_nonparam,
            id_label=" / ".join(pair_id_columns or data.id_columns),
        )
        # 시점별 α — 같은 척도가 두 시점에서 비슷한 신뢰도를 보이는지(측정 안정성) 점검.
        if prepost.get("usable"):
            # α는 **짝지어진 행만**으로 계산한다. 시점의 모든 행을 쓰면 짝짓기에서 뺀 행
            # (중복 입력·한 시점만 있는 ID)까지 섞여, 표에 적힌 N 과 다른 표본의 α가
            # 나란히 찍힌다(리뷰 지적 D3).
            info = paired.build_pairs(
                keys, tvals, str(prepost["pre"]), str(prepost["post"])
            )
            used = set()
            for _key, i_pre, i_post in info["pairs"]:
                used.add(i_pre)
                used.add(i_post)
            tvals = [t if i in used else "" for i, t in enumerate(tvals)]
            talphas = {
                str(s["name"]): _group_alphas(data, cfg, list(s["items"]), tvals)  # type: ignore[arg-type]
                for s in subscales
            }
            for row in prepost["subscales"]:
                a = talphas.get(str(row["name"]), {})
                row["alpha_pre"] = a.get(str(prepost.get("pre")))
                row["alpha_post"] = a.get(str(prepost.get("post")))

    total_cells = data.n_respondents * len(items_all)
    missing_cells = sum(d["n_missing"] for d in descriptives)
    complete_resp = sum(
        1
        for row in data.rows
        if all(row.get(it) is not None for it in items_all)
    )

    return {
        "n_respondents": data.n_respondents,
        "n_items": len(items_all),
        "reverse_items": list(cfg.reverse_items),
        "scale_min": cfg.scale_min,
        "scale_max": cfg.scale_max,
        "score_method": cfg.score_method,
        "conf_level": conf,
        "item_freq": (
            response_frequencies(data, items_all, cfg.scale_min, cfg.scale_max)
            if item_freq else None
        ),
        "out_of_range": out_of_range,
        "unreadable": unreadable,
        "empty_rows": empty_rows,
        "skipped_blank_lines": list(getattr(data, "skipped_blank_lines", [])),
        "descriptives": descriptives,
        "subscales": subscales,
        # 하위척도 간 상관(변별타당도). 하위척도가 1개면 None.
        "subscale_corr": quality.subscale_correlations(subscales),
        "group_column": group_column,
        "group_compare": group_compare,
        "time_column": time_column,
        "prepost": prepost,
        "encoding_used": getattr(data, "encoding_used", "utf-8-sig"),
        "encoding_forced": bool(getattr(data, "encoding_forced", False)),
        "duplicate_ids": quality.duplicate_ids(
            data,
            list(getattr(data, "time_values", []) or []) if time_column else None,
        ),
        "quality": (
            quality.respondent_quality(data, items_all, longstring_min)
            if quality_check else None
        ),
        "missing": {
            "total_cells": total_cells,
            "missing_cells": missing_cells,
            "missing_pct": round(100.0 * missing_cells / total_cells, 1)
            if total_cells
            else 0.0,
            "complete_respondents": complete_resp,
            "complete_pct": round(100.0 * complete_resp / data.n_respondents, 1)
            if data.n_respondents
            else 0.0,
        },
    }
