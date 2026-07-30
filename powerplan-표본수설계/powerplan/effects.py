"""효과크기 변환과 해석 라벨.

프로토콜에는 "d = 0.5" 대신 "ISI 3점 차이(SD 6점) = d 0.50 (중간 효과)"처럼
**원래 단위 → 표준화 효과크기 → 관례적 해석**이 함께 적혀야 심사자가 납득한다.
그 세 가지를 이어주는 최소한의 도구만 둔다.
"""

from __future__ import annotations

import math

from .validate import PowerPlanError, as_float, positive

__all__ = [
    "cohen_d",
    "cohen_f_from_means",
    "fisher_z",
    "hedges_correction",
    "label_d",
    "label_f",
    "label_r",
    "pooled_sd",
]

# Cohen(1988) 관례적 기준. "관례"일 뿐 임상적 중요성과 같지 않다.
_D_BANDS = ((0.2, "매우 작음/very small"), (0.5, "작음/small"), (0.8, "중간/medium"))
_F_BANDS = ((0.10, "매우 작음/very small"), (0.25, "작음/small"), (0.40, "중간/medium"))
_R_BANDS = ((0.10, "매우 작음/very small"), (0.30, "작음/small"), (0.50, "중간/medium"))


def _label(value: float, bands) -> str:
    v = abs(value)
    for edge, name in bands:
        if v < edge:
            return name
    return "큼/large"


def label_d(d: float) -> str:
    """Cohen's d 관례적 해석 (0.2/0.5/0.8 기준)."""
    return _label(d, _D_BANDS)


def label_f(f: float) -> str:
    """Cohen's f 관례적 해석 (0.10/0.25/0.40 기준)."""
    return _label(f, _F_BANDS)


def label_r(r: float) -> str:
    """상관계수 r 관례적 해석 (0.10/0.30/0.50 기준)."""
    return _label(r, _R_BANDS)


def cohen_d(mean1: float, mean2: float, sd: float) -> float:
    """두 평균과 공통 SD에서 Cohen's d = (m1 − m2) / sd."""
    sd = positive("--sd", sd)
    return (as_float("--mean1", mean1) - as_float("--mean2", mean2)) / sd


def pooled_sd(sd1: float, n1: int, sd2: float, n2: int) -> float:
    """합동 표준편차 √[((n1−1)s1² + (n2−1)s2²) / (n1+n2−2)]."""
    sd1, sd2 = positive("sd1", sd1), positive("sd2", sd2)
    if n1 < 2 or n2 < 2:
        raise PowerPlanError("합동 SD를 구하려면 두 군 모두 n ≥ 2가 필요합니다")
    num = (n1 - 1) * sd1 * sd1 + (n2 - 1) * sd2 * sd2
    return math.sqrt(num / (n1 + n2 - 2))


def cohen_f_from_means(means, sd: float) -> float:
    """여러 군 평균과 군내 SD에서 Cohen's f = σ_m / σ.

    σ_m = √(Σ(m_i − m̄)² / k) — Cohen(1988)의 정의(k로 나눔, k−1이 아님).
    """
    vals = [as_float("--means", m) for m in means]
    if len(vals) < 2:
        raise PowerPlanError("--means: 군 평균이 2개 이상 필요합니다")
    sd = positive("--sd", sd)
    grand = math.fsum(vals) / len(vals)
    sigma_m = math.sqrt(math.fsum((v - grand) ** 2 for v in vals) / len(vals))
    return sigma_m / sd


def fisher_z(r: float) -> float:
    """Fisher z 변환 atanh(r)."""
    r = as_float("r", r)
    if not (-1.0 < r < 1.0):
        raise PowerPlanError(f"r: -1과 1 사이여야 합니다 (받은 값: {r:g})")
    return math.atanh(r)


def hedges_correction(df: float) -> float:
    """Hedges의 소표본 편향 보정계수 J = Γ(df/2) / (√(df/2)·Γ((df−1)/2)).

    d에 곱하면 g가 된다. 흔히 쓰는 근사 1 − 3/(4df − 1) 대신 정확식을 쓴다.
    """
    if df <= 1.0:
        raise PowerPlanError(f"Hedges 보정: df > 1 이어야 합니다 (받은 값: {df:g})")
    log_j = math.lgamma(df / 2.0) - 0.5 * math.log(df / 2.0) - math.lgamma((df - 1.0) / 2.0)
    return math.exp(log_j)
