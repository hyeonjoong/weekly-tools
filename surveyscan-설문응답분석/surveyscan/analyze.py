"""설문 분석 핵심 로직: 문항 기술통계 · 결측 요약 · 역문항 처리 ·
하위척도 점수 · Cronbach α · 수정된 문항-총점 상관 · 문항 제거 시 α.

결과는 평범한 dict로 반환하여 report.py가 텍스트/JSON으로 렌더링한다.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from . import stats
from .config import SurveyConfig
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
        "n_scored": len(valid_scores),
        "scores": scores,
    }


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
    data: SurveyData, cfg: SurveyConfig, conf: float = 0.95, item_freq: bool = False
) -> Dict[str, object]:
    """전체 분석 실행. config에 명시된 모든 문항/하위척도를 검증·분석한다.

    conf: 신뢰구간(α CI, 점수 평균 CI)의 신뢰수준(기본 0.95).
    item_freq: True면 문항별 응답 선택지 빈도표를 함께 계산(척도 범위 정수일 때).
    """
    # config 문항이 데이터에 실제로 있는지 확인
    missing_cols = [it for it in cfg.all_items() if it not in data.columns]
    if missing_cols:
        raise ValueError(
            "config에 적힌 문항이 CSV에 없습니다: " + ", ".join(missing_cols)
        )

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

    descriptives = [item_descriptives(data, it) for it in items_all]
    subscales = [
        analyze_subscale(data, name, items, cfg, conf)
        for name, items in cfg.subscales.items()
    ]

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
        "descriptives": descriptives,
        "subscales": subscales,
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
