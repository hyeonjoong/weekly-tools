"""민감도 분석과 출판편향(소규모연구 효과) 진단.

- Egger 회귀 비대칭 검정
- Begg–Mazumdar 순위상관 검정 (비모수 — 이분형 지표에서 Egger의 위양성을 견제)
- Duval–Tweedie trim-and-fill (누락 연구를 채워 넣은 보정 추정치)
- leave-one-out 민감도 + 영향력 진단(표준화 잔차)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .distributions import normal_sf, t_sf
from .effects import Study
from .meta import Pooled, heterogeneity, random_effects

__all__ = [
    "EggerResult",
    "BeggResult",
    "TrimFillResult",
    "LeaveOneOut",
    "egger_test",
    "begg_test",
    "trim_and_fill",
    "leave_one_out",
]


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
class BeggResult:
    """Begg–Mazumdar 순위상관 검정 결과."""

    tau: float  # Kendall tau_a
    z: float
    p: float
    k: int
    #: 'exact' = 순열 정확검정, 'normal' = 정규근사(동점이 있거나 k가 클 때)
    method: str = "normal"
    score: int = 0  # Kendall S = 일치쌍 - 불일치쌍


@dataclass
class TrimFillResult:
    """Duval–Tweedie trim-and-fill 결과."""

    k0: int  # 채워 넣은(=누락으로 추정된) 연구 수
    side: str  # 'left' | 'right' — 누락된 것으로 추정되는 쪽
    estimator: str  # 'L0' | 'R0'
    adjusted: Pooled  # 채운 뒤 다시 합성한 변량효과 결과
    imputed: List[float] = field(default_factory=list)  # 채운 연구들의 효과크기(분석 척도)
    converged: bool = True


@dataclass
class LeaveOneOut:
    omitted: str
    estimate: float
    ci_low: float
    ci_high: float
    tau2: float
    p: float
    #: 그 연구를 뺐을 때의 I²(%) — 이질성이 어느 연구에서 오는지 보여 준다.
    i2: Optional[float] = None
    #: 표준화 잔차 (y_i - mu_(-i)) / sqrt(v_i + tau2_(-i) + se_(-i)^2).
    #: |값| > 약 2 면 그 연구가 나머지와 잘 맞지 않는다는 신호(이상치 후보).
    std_resid: Optional[float] = None


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


#: 정확검정을 쓸 수 있는 최대 연구 수 (그 이상은 정규근사로도 충분히 정확하다)
_BEGG_EXACT_MAX_K = 20


def _inversion_counts(n: int) -> List[int]:
    """길이 n 순열의 역위(inversion) 수 분포 — Mahonian 수열.

    c[d] = 역위가 정확히 d 개인 순열의 수. 파이썬 정수라 오차가 없다.
    """
    counts = [1]
    for size in range(2, n + 1):
        max_inv = len(counts) - 1 + size - 1
        prefix = [0] * (len(counts) + 1)
        for i, value in enumerate(counts):
            prefix[i + 1] = prefix[i] + value
        new = [0] * (max_inv + 1)
        for d in range(max_inv + 1):
            lo = max(0, d - size + 1)
            hi = min(d, len(counts) - 1)
            if hi >= lo:
                new[d] = prefix[hi + 1] - prefix[lo]
        counts = new
    return counts


def _kendall_exact_p(k: int, score: int) -> float:
    """Kendall S 의 양측 정확 p값 (동점이 없을 때).

    S = M - 2D (M = k(k-1)/2, D = 불일치쌍 수)이고 귀무가설 아래 D 는 역위 분포를
    따른다. 분포가 0 을 중심으로 대칭이므로 P(|S| >= |s|) = 2·P(D <= (M-|s|)/2).

    k=4 에서 얻을 수 있는 가장 작은 p 는 1/12 = 0.083 이다 — 정규근사는 같은
    자료에 0.042 를 주어 "출판편향 유의"라는 불가능한 결론을 만든다.
    """
    m = k * (k - 1) // 2
    s = abs(int(score))
    if s == 0:
        return 1.0
    counts = _inversion_counts(k)
    total = sum(counts)
    d_max = (m - s) // 2
    if d_max < 0:
        return 0.0  # pragma: no cover - |S| <= M 이므로 도달 불가
    tail = sum(counts[: d_max + 1])
    return min(1.0, 2.0 * tail / total)


def begg_test(studies: Sequence[Study]) -> Optional[BeggResult]:
    """Begg–Mazumdar 순위상관 검정 (비모수 깔때기그림 비대칭 검정).

    표준화 효과크기 ``t_i = (y_i - mu_FE) / sqrt(v_i - 1/sum(1/v))`` 와 분산
    ``v_i`` 사이의 Kendall 순위상관을 검정한다. Egger 회귀보다 검정력은 낮지만
    이분형 지표(OR/RR)에서 나타나는 Egger의 구조적 위양성에 덜 취약해,
    둘을 **함께** 보고하는 것이 관행이다.

    p값은 동점이 없고 연구가 20편 이하면 **순열 정확검정**으로 구한다.
    정규근사는 연구 수가 적을 때 심하게 비보수적이어서(k=4·완전 일치에서
    정확 p = 0.083 인데 근사는 0.042), 그대로 쓰면 나올 수 없는 유의성이 나온다.
    동점이 있거나 k가 크면 정규근사로 돌아가며 ``method`` 에 그 사실을 남긴다.

    연구 3편 미만이면 None.
    """
    k = len(studies)
    if k < 3:
        return None
    inv = [1.0 / s.vi for s in studies]
    sinv = math.fsum(inv)
    mu = math.fsum(wi * s.yi for wi, s in zip(inv, studies)) / sinv
    ts: List[float] = []
    vs: List[float] = []
    for s in studies:
        var_star = s.vi - 1.0 / sinv
        if var_star <= 0:
            return None  # 한 연구가 전체 정밀도를 다 갖고 있음 — 표준화 불가
        ts.append((s.yi - mu) / math.sqrt(var_star))
        vs.append(s.vi)
    score = 0
    ties = False
    for i in range(k):
        for j in range(i + 1, k):
            a = ts[j] - ts[i]
            b = vs[j] - vs[i]
            if a == 0 or b == 0:
                ties = True
                continue
            score += 1 if (a > 0) == (b > 0) else -1
    var = k * (k - 1) * (2 * k + 5) / 18.0
    if var <= 0:  # pragma: no cover - k>=3 이면 항상 양수
        return None
    z = score / math.sqrt(var)
    tau = 2.0 * score / (k * (k - 1))
    if not ties and k <= _BEGG_EXACT_MAX_K:
        return BeggResult(tau=tau, z=z, p=_kendall_exact_p(k, score), k=k,
                          method="exact", score=score)
    return BeggResult(tau=tau, z=z, p=2.0 * normal_sf(abs(z)), k=k,
                      method="normal", score=score)


def _linear_slope(xs: Sequence[float], ys: Sequence[float], weights: Sequence[float]) -> float:
    """가중 단순회귀의 기울기 (분모가 0이면 0)."""
    sw = math.fsum(weights)
    mx = math.fsum(w * x for w, x in zip(weights, xs)) / sw
    my = math.fsum(w * y for w, y in zip(weights, ys)) / sw
    sxx = math.fsum(w * (x - mx) ** 2 for w, x in zip(weights, xs))
    if sxx <= 0:
        return 0.0
    return math.fsum(w * (x - mx) * (y - my) for w, x, y in zip(weights, xs, ys)) / sxx


def _l0(ys: Sequence[float], center: float) -> float:
    """Duval–Tweedie L0 추정량 (오른쪽에서 잘려나갔다고 가정한 척도)."""
    n = len(ys)
    dev = [y - center for y in ys]
    order = sorted(range(n), key=lambda i: abs(dev[i]))
    rank = [0] * n
    for r, i in enumerate(order, start=1):
        rank[i] = r
    tn = math.fsum(rank[i] for i in range(n) if dev[i] > 0)
    return (4.0 * tn - n * (n + 1.0)) / (2.0 * n - 1.0)


def _r0(ys: Sequence[float], center: float) -> float:
    """Duval–Tweedie R0 추정량 — 오른쪽 끝의 연속된 양의 편차 길이 기반."""
    n = len(ys)
    dev = [y - center for y in ys]
    order = sorted(range(n), key=lambda i: abs(dev[i]))
    run = 0
    for i in reversed(order):
        if dev[i] > 0:
            run += 1
        else:
            break
    gamma = run  # 가장 오른쪽 연속 구간 길이
    return float(max(0, gamma - 1))


def trim_and_fill(
    studies: Sequence[Study],
    conf: float = 0.95,
    tau2_method: str = "DL",
    knapp_hartung: bool = True,
    estimator: str = "L0",
    side: Optional[str] = None,
    max_iter: int = 100,
) -> Optional[TrimFillResult]:
    """Duval–Tweedie trim-and-fill — 누락된 것으로 보이는 연구를 대칭으로 채운다.

    깔때기그림이 한쪽으로 치우쳐 있으면, 반대쪽에 있었어야 할 연구가
    출판되지 않았다고 보고 통합값 기준 거울상으로 ``k0`` 편을 채워 넣은 뒤
    다시 합성한다. **보정 추정치는 "출판편향이 있었다면 이 정도"라는 민감도
    분석**이지 참값 추정이 아니다 — 이질성이 큰 자료에서는 k0을 과대추정한다.

    연구 3편 미만이면 None. ``side`` 를 주지 않으면 정밀도-효과 회귀의
    기울기 부호로 자동 판정한다('left' = 왼쪽(작은 효과)이 누락).
    """
    k = len(studies)
    if k < 3:
        return None
    estimator = estimator.upper()
    if estimator not in ("L0", "R0"):
        raise ValueError("trim_and_fill: estimator 는 'L0' 또는 'R0' 여야 합니다 (받은 값: %r)" % estimator)

    if side is None:
        slope = _linear_slope([s.sei for s in studies], [s.yi for s in studies],
                              [1.0 / s.vi for s in studies])
        side = "left" if slope > 0 else "right"
    if side not in ("left", "right"):
        raise ValueError("trim_and_fill: side 는 'left' 또는 'right' 여야 합니다 (받은 값: %r)" % side)

    flip = -1.0 if side == "right" else 1.0
    # 항상 "오른쪽이 남고 왼쪽이 잘렸다"는 표준형으로 바꿔 계산한다.
    ys = [flip * s.yi for s in studies]
    vs = [s.vi for s in studies]
    order = sorted(range(k), key=lambda i: ys[i])  # 오름차순 (오른쪽 = 큰 값)

    fn = _l0 if estimator == "L0" else _r0
    k0 = 0
    converged = False
    for _ in range(max_iter):
        keep = order[: k - k0] if k0 > 0 else order
        sub = [Study(label="t%d" % i, yi=ys[i], vi=vs[i]) for i in keep]
        mu = random_effects(sub, conf=conf, tau2_method=tau2_method,
                            knapp_hartung=False).estimate
        new_k0 = int(max(0, math.floor(fn([ys[i] for i in order], mu) + 0.5)))
        new_k0 = min(new_k0, k - 1)
        if new_k0 == k0:
            converged = True
            break
        k0 = new_k0
    if k0 <= 0:
        adjusted = random_effects(list(studies), conf=conf, tau2_method=tau2_method,
                                  knapp_hartung=knapp_hartung)
        return TrimFillResult(k0=0, side=side, estimator=estimator, adjusted=adjusted,
                              imputed=[], converged=converged)

    # 최종 중심을 기준으로 가장 오른쪽 k0 편의 거울상을 채운다.
    keep = order[: k - k0]
    sub = [Study(label="t%d" % i, yi=ys[i], vi=vs[i]) for i in keep]
    mu = random_effects(sub, conf=conf, tau2_method=tau2_method, knapp_hartung=False).estimate
    filled = list(studies)
    imputed: List[float] = []
    for rank, i in enumerate(order[k - k0:], start=1):
        y_new = flip * (2.0 * mu - ys[i])
        imputed.append(y_new)
        filled.append(Study(label="(채워 넣은 연구 %d)" % rank, yi=y_new, vi=vs[i]))
    adjusted = random_effects(filled, conf=conf, tau2_method=tau2_method,
                              knapp_hartung=knapp_hartung)
    return TrimFillResult(k0=k0, side=side, estimator=estimator, adjusted=adjusted,
                          imputed=imputed, converged=converged)


def leave_one_out(
    studies: Sequence[Study],
    conf: float = 0.95,
    tau2_method: str = "DL",
    knapp_hartung: bool = True,
) -> List[LeaveOneOut]:
    """연구를 하나씩 빼면서 변량효과 합성을 다시 해 결과의 안정성을 본다.

    함께 계산하는 값
    - ``i2``        : 그 연구를 뺐을 때 남는 이질성 (I² 급감 = 그 연구가 이질성의 원인)
    - ``std_resid`` : 나머지 연구로 만든 예측과 그 연구가 얼마나 어긋나는지
    """
    k = len(studies)
    if k < 3:
        return []
    out: List[LeaveOneOut] = []
    for i, s in enumerate(studies):
        rest = [x for j, x in enumerate(studies) if j != i]
        p: Pooled = random_effects(
            rest, conf=conf, tau2_method=tau2_method, knapp_hartung=knapp_hartung
        )
        het = heterogeneity(rest, tau2_method=tau2_method, conf=conf) if len(rest) >= 2 else None
        denom = s.vi + p.tau2 + p.se_model ** 2
        resid = (s.yi - p.estimate) / math.sqrt(denom) if denom > 0 else None
        out.append(
            LeaveOneOut(
                omitted=s.label,
                estimate=p.estimate,
                ci_low=p.ci_low,
                ci_high=p.ci_high,
                tau2=p.tau2,
                p=p.p,
                i2=het.i2 if het else None,
                std_resid=resid,
            )
        )
    return out
