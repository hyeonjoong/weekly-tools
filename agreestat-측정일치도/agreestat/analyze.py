"""Orchestration: run the full agreement analysis and assemble a result object.

Given paired measurements (method A vs method B on the same subjects, optionally
with a subject id), :func:`analyze` computes Bland-Altman, ICC(2,1) & ICC(3,1),
Lin's CCC, repeatability (when replicates exist), and Pearson r / paired t for
context, and collects interpretive warnings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from . import agreement
from .agreement import (
    BlandAltmanResult,
    CCCResult,
    ICCResult,
    PairedTResult,
    PearsonResult,
    RepeatabilityResult,
)

__all__ = ["AnalysisResult", "analyze"]


@dataclass
class AnalysisResult:
    name_a: str
    name_b: str
    n: int
    dropped: int
    alpha: float
    mode: str
    # descriptives
    mean_a: float
    sd_a: float
    mean_b: float
    sd_b: float
    # blocks
    ba: BlandAltmanResult
    icc21: ICCResult
    icc31: ICCResult
    ccc: CCCResult
    repeat: RepeatabilityResult
    pearson: PearsonResult
    paired: PairedTResult
    reported_icc: str = "ICC(2,1)"  # which model to headline
    warnings: List[str] = field(default_factory=list)


def _constant(x: Sequence[float]) -> bool:
    return all(v == x[0] for v in x)


def analyze(a: Sequence[float], b: Sequence[float],
            subjects: Optional[Sequence[str]] = None,
            name_a: str = "A", name_b: str = "B",
            alpha: float = 0.05, mode: str = "absolute",
            dropped: int = 0) -> AnalysisResult:
    """Run every agreement statistic and return an :class:`AnalysisResult`."""
    a = [float(v) for v in a]
    b = [float(v) for v in b]
    n = len(a)
    if len(b) != n:
        raise ValueError("method A and method B must have the same length")
    if n < 2:
        raise ValueError("need at least 2 paired observations")

    warnings: List[str] = []
    a_const = _constant(a)
    b_const = _constant(b)
    if a_const:
        warnings.append(f"'{name_a}' 값이 모두 동일합니다(분산 0). "
                        "ICC/CCC/Pearson이 정의되지 않을 수 있습니다.")
    if b_const:
        warnings.append(f"'{name_b}' 값이 모두 동일합니다(분산 0). "
                        "ICC/CCC/Pearson이 정의되지 않을 수 있습니다.")
    if n < 10:
        warnings.append(f"표본이 작습니다(n={n}). 신뢰구간이 넓고 "
                        "LoA/ICC/CCC 추정이 불안정할 수 있습니다.")

    ba = agreement.bland_altman(a, b, alpha=alpha, mode=mode)
    if ba.prop_bias:
        warnings.append(
            "비례 편향(proportional bias)이 감지되었습니다: 차이가 측정값 크기에 "
            f"따라 변합니다 (기울기={ba.prop_slope:.4f}, p={_p(ba.prop_pvalue)}). "
            "단일 bias/LoA 해석에 주의하고, 백분율(--percent) 또는 회귀 기반 "
            "LoA를 고려하세요.")

    rows = [[x, y] for x, y in zip(a, b)]
    try:
        icc21, icc31, _ms = agreement.icc(rows, alpha=alpha)
    except ValueError as exc:
        warnings.append(f"ICC 계산 불가: {exc}")
        nan = float("nan")
        icc21 = ICCResult("ICC(2,1)", "", nan, nan, nan, nan, nan, nan, nan,
                          "판정 불가 / undefined")
        icc31 = ICCResult("ICC(3,1)", "", nan, nan, nan, nan, nan, nan, nan,
                          "판정 불가 / undefined")

    try:
        ccc_res = agreement.ccc(a, b, alpha=alpha)
    except ValueError as exc:
        warnings.append(f"CCC 계산 불가: {exc}")
        nan = float("nan")
        ccc_res = CCCResult(nan, nan, nan, nan, nan, "판정 불가 / undefined", alpha)

    repeat = agreement.repeatability(a, b, subjects)
    if repeat.available:
        warnings.append(
            "반복측정 데이터가 감지되었습니다. 위의 Bland-Altman/ICC는 각 행을 "
            "독립으로 가정합니다 — 개인당 반복이 있으면 LoA가 좁게 나올 수 있으니 "
            "반복측정용 방법(Bland & Altman 2007)도 함께 고려하세요.")

    pear = agreement.pearson(a, b, alpha=alpha)
    paired = agreement.paired_t(a, b)

    return AnalysisResult(
        name_a=name_a, name_b=name_b, n=n, dropped=dropped, alpha=alpha,
        mode=mode,
        mean_a=agreement.mean(a),
        sd_a=math.sqrt(agreement.variance(a)) if not a_const else 0.0,
        mean_b=agreement.mean(b),
        sd_b=math.sqrt(agreement.variance(b)) if not b_const else 0.0,
        ba=ba, icc21=icc21, icc31=icc31, ccc=ccc_res, repeat=repeat,
        pearson=pear, paired=paired, warnings=warnings)


def _p(p: float) -> str:
    if p != p:
        return "NaN"
    return "<0.001" if p < 0.001 else f"{p:.3f}"
