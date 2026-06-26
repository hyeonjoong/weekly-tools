"""설문 분석 핵심 로직: 문항 기술통계 · 결측 요약 · 역문항 처리 ·
하위척도 점수 · Cronbach α · 수정된 문항-총점 상관 · 문항 제거 시 α.

결과는 평범한 dict로 반환하여 report.py가 텍스트/JSON으로 렌더링한다.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import stats
from .config import SurveyConfig
from .dataio import SurveyData


def _reverse_value(v: float, scale_min: float, scale_max: float) -> float:
    """역문항 재코딩: x' = (min+max) - x."""
    return (scale_min + scale_max) - v


def item_descriptives(data: SurveyData, item: str) -> Dict[str, object]:
    """한 문항의 기술통계 + 결측 요약."""
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
    """응답자별 하위척도 점수(역문항 적용 후 가용 문항 평균).

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
            scores.append(sum(present) / len(present))
    return scores


def analyze_subscale(
    data: SurveyData, name: str, items: List[str], cfg: SurveyConfig
) -> Dict[str, object]:
    """하위척도 1개에 대한 신뢰도 분석."""
    matrix = _recoded_matrix(data, items, cfg)
    complete = _complete_rows(matrix)
    k = len(items)
    n_complete = len(complete)

    alpha = None
    item_total: Dict[str, Optional[float]] = {}
    alpha_if_deleted: Dict[str, Optional[float]] = {}

    if k >= 2 and n_complete >= 2:
        columns = [[complete[r][i] for r in range(n_complete)] for i in range(k)]
        alpha = stats.cronbach_alpha(columns)
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
        "item_total_corr": item_total,
        "alpha_if_deleted": alpha_if_deleted,
        "score_mean": stats.mean(valid_scores),
        "score_sd": stats.stdev(valid_scores),
        "n_scored": len(valid_scores),
        "scores": scores,
    }


def analyze(data: SurveyData, cfg: SurveyConfig) -> Dict[str, object]:
    """전체 분석 실행. config에 명시된 모든 문항/하위척도를 검증·분석한다."""
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
        analyze_subscale(data, name, items, cfg)
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
