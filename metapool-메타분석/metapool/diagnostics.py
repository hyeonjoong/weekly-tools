"""민감도 분석과 출판편향(소규모연구 효과) 진단."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .distributions import t_sf
from .effects import Study
from .meta import Pooled, random_effects

__all__ = ["EggerResult", "LeaveOneOut", "egger_test", "leave_one_out"]


@dataclass
class EggerResult:
    intercept: float
    se: float
    t: float
    df: int
    p: float
    slope: float
    k: int


@dataclass
class LeaveOneOut:
    omitted: str
    estimate: float
    ci_low: float
    ci_high: float
    tau2: float
    p: float


def egger_test(studies: Sequence[Study]) -> Optional[EggerResult]:
    """Egger의 회귀 비대칭 검정.

    표준정규편차 ``y_i/se_i`` 를 정밀도 ``1/se_i`` 에 대해 단순회귀하고,
    절편이 0인지 자유도 k-2의 t 검정으로 확인한다. 절편이 0에서 멀수록
    깔때기그림(funnel plot) 비대칭 = 소규모연구 효과가 의심된다.

    연구가 3편 미만이면 None. (Egger 등은 **10편 이상**을 권고하며,
    그 미만에서는 검정력이 매우 낮다 — 보고 시 반드시 함께 밝힐 것.)
    """
    k = len(studies)
    if k < 3:
        return None
    xs = [1.0 / s.sei for s in studies]
    ys = [s.yi / s.sei for s in studies]
    n = float(k)
    mx = math.fsum(xs) / n
    my = math.fsum(ys) / n
    sxx = math.fsum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None  # 모든 연구의 정밀도가 동일 → 기울기/절편 식별 불가
    sxy = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    df = k - 2
    sse = math.fsum(r * r for r in resid)
    if df <= 0:
        return None
    mse = sse / df
    se_int = math.sqrt(mse * (1.0 / n + mx * mx / sxx)) if mse > 0 else 0.0
    if se_int <= 0:
        return None  # 잔차가 0 (완전적합) → t 통계량 정의 불가
    t = intercept / se_int
    return EggerResult(
        intercept=intercept,
        se=se_int,
        t=t,
        df=df,
        p=2.0 * t_sf(abs(t), float(df)),
        slope=slope,
        k=k,
    )


def leave_one_out(
    studies: Sequence[Study],
    conf: float = 0.95,
    tau2_method: str = "DL",
    knapp_hartung: bool = True,
) -> List[LeaveOneOut]:
    """연구를 하나씩 빼면서 변량효과 합성을 다시 해 결과의 안정성을 본다."""
    k = len(studies)
    if k < 3:
        return []
    out: List[LeaveOneOut] = []
    for i, s in enumerate(studies):
        rest = [x for j, x in enumerate(studies) if j != i]
        p: Pooled = random_effects(
            rest, conf=conf, tau2_method=tau2_method, knapp_hartung=knapp_hartung
        )
        out.append(
            LeaveOneOut(
                omitted=s.label,
                estimate=p.estimate,
                ci_low=p.ci_low,
                ci_high=p.ci_high,
                tau2=p.tau2,
                p=p.p,
            )
        )
    return out
