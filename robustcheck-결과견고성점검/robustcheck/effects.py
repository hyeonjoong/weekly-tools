"""효과크기와 그 등급.

이 툴은 시나리오마다 **검정을 바꾼다.** Mann–Whitney 의 rank-biserial 과
Welch 의 Hedges g 를 나란히 놓고 "등급이 바뀌었다"고 말하면 그건 거짓말이다
(단위가 다르다). 그래서 **비교용 효과크기는 검정과 무관하게 데이터 배치에서
계산한 d 계열(상관 설계는 r) 하나로 고정**하고, 검정 고유 효과크기는 따로
표에만 남긴다. 뒤집힘 판정 ③④ 는 오직 비교용 효과크기로만 한다.
"""

import math
from typing import Sequence

from .inference import mean, rankdata, variance

__all__ = [
    "hedges_g_two_group",
    "hedges_g_paired",
    "rank_biserial_two_group",
    "matched_rank_biserial",
    "effect_grade",
    "D_FAMILY",
    "R_FAMILY",
    "D_GRADE_BOUNDS",
    "R_GRADE_BOUNDS",
    "family_min_delta",
    "sign_flip_floor",
    "GRADE_LABELS",
]

D_FAMILY = "d"
R_FAMILY = "r"

# Cohen 관례. 경계는 "이상"이 다음 등급이다 (|g| = 0.5 → 中).
D_GRADE_BOUNDS = (0.2, 0.5, 0.8)
R_GRADE_BOUNDS = (0.1, 0.3, 0.5)
GRADE_LABELS = ("미미", "小", "中", "大")

# 등급 경계를 스치듯 넘는 변화(0.499 → 0.501)를 "뒤집힘"이라 부르면
# 이 툴은 매번 우는 체커가 된다. 등급 변화는 **이 폭 이상 움직였을 때만**
# 센다. 좁히는 방향의 규칙이고, 값은 리포트에 그대로 인쇄된다.
MIN_DELTA = {D_FAMILY: 0.10, R_FAMILY: 0.05}


def family_min_delta(family: str) -> float:
    return MIN_DELTA[family]


def sign_flip_floor(family: str) -> float:
    """부호 반전(③)을 셀 최소 크기 = 첫 등급 경계.

    효과크기가 +0.109 에서 −0.11 로 간 것을 "부호가 뒤집혔다"고 치명으로
    부르면, 사실상 0 인 효과를 두고 매번 우는 체커가 된다. 양쪽 모두
    적어도 '小' 이상일 때만 부호 반전으로 센다.
    """
    bounds = D_GRADE_BOUNDS if family == D_FAMILY else R_GRADE_BOUNDS
    return bounds[0]


def _hedges_correction(df: float) -> float:
    """Hedges 의 소표본 편향 보정 J = 1 − 3/(4·df − 1).

    df ≤ 1 에서는 근사가 무너진다 — J(1) = 0 (효과크기를 0 으로 만든다),
    J(0.5) = −2 (부호를 뒤집는다). 그 구간에서는 보정을 포기하고 1 을 쓴다.
    """
    if df <= 1.0:
        return 1.0
    return 1.0 - 3.0 / (4.0 * df - 1.0)


def hedges_g_two_group(a: Sequence[float], b: Sequence[float]) -> float:
    """독립 2군 Hedges g = J · (mean_a − mean_b) / s_pooled."""
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        raise ValueError("hedges_g_two_group: 각 군 n ≥ 2 가 필요합니다")
    df = n1 + n2 - 2
    sp2 = ((n1 - 1) * variance(a) + (n2 - 1) * variance(b)) / df
    if sp2 <= 0.0:
        raise ValueError("hedges_g_two_group: 합동분산이 0 입니다")
    return _hedges_correction(df) * (mean(a) - mean(b)) / math.sqrt(sp2)


def hedges_g_paired(pre: Sequence[float], post: Sequence[float]) -> float:
    """대응표본 Hedges g (차이점수 기준, d_z 계열)."""
    if len(pre) != len(post):
        raise ValueError("hedges_g_paired: 두 계열의 길이가 다릅니다")
    diffs = [q - p for p, q in zip(pre, post)]
    n = len(diffs)
    if n < 2:
        raise ValueError("hedges_g_paired: n ≥ 2 가 필요합니다")
    var = variance(diffs)
    if var <= 0.0:
        raise ValueError("hedges_g_paired: 차이의 분산이 0 입니다")
    return _hedges_correction(n - 1) * mean(diffs) / math.sqrt(var)


def rank_biserial_two_group(a: Sequence[float], b: Sequence[float]) -> float:
    """독립 2군 rank-biserial r = 2·U1/(n1·n2) − 1 (검정 고유 효과크기)."""
    n1, n2 = len(a), len(b)
    if n1 < 1 or n2 < 1:
        raise ValueError("rank_biserial_two_group: 각 군 n ≥ 1 이 필요합니다")
    ranks = rankdata(list(a) + list(b))
    r1 = math.fsum(ranks[:n1])
    u1 = r1 - n1 * (n1 + 1) / 2.0
    return 2.0 * u1 / (n1 * n2) - 1.0


def matched_rank_biserial(pre: Sequence[float], post: Sequence[float]) -> float:
    """대응표본 rank-biserial = (W+ − W−) / (W+ + W−). 0 차이는 제외."""
    diffs = [q - p for p, q in zip(pre, post)]
    nonzero = [d for d in diffs if d != 0.0]
    if not nonzero:
        raise ValueError("matched_rank_biserial: 0 이 아닌 차이가 없습니다")
    ranks = rankdata([abs(d) for d in nonzero])
    w_plus = math.fsum(r for d, r in zip(nonzero, ranks) if d > 0)
    w_minus = math.fsum(r for d, r in zip(nonzero, ranks) if d < 0)
    total = w_plus + w_minus
    if total <= 0.0:
        raise ValueError("matched_rank_biserial: 순위합이 0 입니다")
    return (w_plus - w_minus) / total


def effect_grade(value: float, family: str) -> str:
    """|효과크기| → 미미 / 小 / 中 / 大."""
    bounds = D_GRADE_BOUNDS if family == D_FAMILY else R_GRADE_BOUNDS
    magnitude = abs(value)
    if math.isnan(magnitude):
        return "판정불가"
    for label, bound in zip(GRADE_LABELS, bounds):
        if magnitude < bound:
            return label
    return GRADE_LABELS[-1]
