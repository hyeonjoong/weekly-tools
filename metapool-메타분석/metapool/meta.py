"""효과크기 합성(pooling)·이질성·하위군 분석.

- 고정효과(fixed-effect): 역분산 가중
- 변량효과(random-effects): DerSimonian–Laird 또는 Paule–Mandel로 tau^2 추정
- Hartung–Knapp(–Sidik–Jonkman) 보정 신뢰구간 (연구 수가 적을 때 1종 오류 억제)
- 이질성: Q, I^2, H^2, tau^2, 95% 예측구간
- 하위군: 하위군별 변량효과 + Q_between (혼합효과 Wald 검정)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .distributions import chi2_sf, normal_ppf, normal_sf, t_ppf, t_sf
from .effects import Study

__all__ = [
    "Pooled",
    "Heterogeneity",
    "SubgroupResult",
    "fixed_effect",
    "random_effects",
    "heterogeneity",
    "tau2_dersimonian_laird",
    "tau2_paule_mandel",
    "prediction_interval",
    "subgroup_analysis",
    "MetaError",
]

TAU2_METHODS = ("DL", "PM")


class MetaError(ValueError):
    """합성 자체가 불가능한 상황(연구 0개 등)."""


@dataclass
class Pooled:
    """합성 결과 한 덩어리."""

    model: str  # 'fixed' | 'random'
    estimate: float
    se: float  # 보고용 표준오차 (HK 보정을 켜면 HK 표준오차)
    ci_low: float
    ci_high: float
    stat: float  # z 또는 t
    p: float
    k: int
    #: 모형기반 표준오차 1/sqrt(sum w). HK 보정과 무관하게 항상 채워지며,
    #: 예측구간·하위군 간 검정처럼 "모형 분산"이 필요한 곳에서 이 값을 쓴다.
    se_model: float = 0.0
    weights: List[float] = field(default_factory=list)  # 정규화 전 가중치
    tau2: float = 0.0
    tau2_method: Optional[str] = None
    ci_method: str = "z"  # 'z' | 'HK'
    df: Optional[float] = None
    conf: float = 0.95
    #: HK 보정을 요청했지만 연구 간 분산이 0이라 z 구간으로 되돌린 경우
    hk_degenerate: bool = False

    @property
    def weight_percent(self) -> List[float]:
        total = math.fsum(self.weights)
        if total <= 0:
            return [0.0 for _ in self.weights]
        return [100.0 * w / total for w in self.weights]


@dataclass
class Heterogeneity:
    q: float
    df: int
    p: float
    i2: float  # 백분율
    h2: float
    tau2: float
    tau: float
    tau2_method: str


@dataclass
class SubgroupResult:
    name: str
    pooled: Pooled
    het: Optional[Heterogeneity]
    k: int


def _require(studies: Sequence[Study]) -> None:
    if not studies:
        raise MetaError("합성할 연구가 없습니다 (유효한 행이 0개입니다).")
    for s in studies:
        if s.vi <= 0 or not math.isfinite(s.vi):
            raise MetaError("연구 '%s'의 분산이 0 이하입니다." % s.label)


def _pool_with_weights(studies, weights):
    total_w = math.fsum(weights)
    est = math.fsum(w * s.yi for w, s in zip(weights, studies)) / total_w
    se = math.sqrt(1.0 / total_w)
    return est, se, total_w


def _weighted_ss(studies, weights, center):
    """sum w_i (y_i - center)^2 — 편차를 먼저 빼서 자리수 소실을 피한다."""
    return math.fsum(w * (s.yi - center) ** 2 for w, s in zip(weights, studies))


def fixed_effect(studies: Sequence[Study], conf: float = 0.95) -> Pooled:
    """역분산 가중 고정효과 모형."""
    _require(studies)
    weights = [1.0 / s.vi for s in studies]
    est, se, _ = _pool_with_weights(studies, weights)
    z = normal_ppf(0.5 + conf / 2.0)
    stat = est / se
    return Pooled(
        model="fixed",
        estimate=est,
        se=se,
        se_model=se,
        ci_low=est - z * se,
        ci_high=est + z * se,
        stat=stat,
        p=2.0 * normal_sf(abs(stat)),
        k=len(studies),
        weights=weights,
        conf=conf,
    )


def _q_statistic(studies: Sequence[Study]) -> "tuple[float, float, float]":
    """Cochran Q와 고정효과 가중치 합/제곱합을 함께 반환.

    Q = sum(w*y^2) - (sum w*y)^2/sum w 는 대수적으로는 맞지만 효과크기의
    절대값이 흩어짐보다 훨씬 클 때(예: y ~ 1e6, 차이 ~0.1) 자리수 소실로
    Q가 0이 되어 "이질성 없음"을 잘못 보고한다. 편차 형태로 계산한다.
    """
    w = [1.0 / s.vi for s in studies]
    sw = math.fsum(w)
    sw2 = math.fsum(wi * wi for wi in w)
    mu = math.fsum(wi * s.yi for wi, s in zip(w, studies)) / sw
    q = _weighted_ss(studies, w, mu)
    return (max(q, 0.0) if math.isfinite(q) else 0.0), sw, sw2


def tau2_dersimonian_laird(studies: Sequence[Study]) -> float:
    """DerSimonian–Laird tau^2 = max(0, (Q - df) / C)."""
    _require(studies)
    k = len(studies)
    if k < 2:
        return 0.0
    q, sw, sw2 = _q_statistic(studies)
    c = sw - sw2 / sw
    if c <= 0:
        return 0.0
    return max(0.0, (q - (k - 1)) / c)


def tau2_paule_mandel(studies: Sequence[Study], tol: float = 1e-10, max_iter: int = 200) -> float:
    """Paule–Mandel tau^2: sum w_i(tau^2)*(y_i - mu(tau^2))^2 = k-1 을 만족하는 tau^2.

    DL보다 편향이 작다고 알려져 있어 연구 수가 적을 때 특히 권장된다.
    해가 존재하지 않으면(=이질성이 통계적으로 없으면) 0을 반환한다.
    """
    _require(studies)
    k = len(studies)
    if k < 2:
        return 0.0

    def gen_q(t2: float) -> float:
        w = [1.0 / (s.vi + t2) for s in studies]
        sw = math.fsum(w)
        mu = math.fsum(wi * s.yi for wi, s in zip(w, studies)) / sw
        return math.fsum(wi * (s.yi - mu) ** 2 for wi, s in zip(w, studies))

    target = float(k - 1)
    if gen_q(0.0) <= target:
        return 0.0
    lo, hi = 0.0, max(tau2_dersimonian_laird(studies), 1e-8)
    for _ in range(100):  # 상한 확장
        if gen_q(hi) <= target:
            break
        hi *= 2.0
    else:  # pragma: no cover - 수치적으로 도달하기 어려운 경로
        return hi
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        if gen_q(mid) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol * max(1.0, hi):
            break
    return 0.5 * (lo + hi)


def _tau2(studies, method: str) -> float:
    method = method.upper()
    if method == "DL":
        return tau2_dersimonian_laird(studies)
    if method == "PM":
        return tau2_paule_mandel(studies)
    raise MetaError("알 수 없는 tau^2 추정법: %r (가능: %s)" % (method, ", ".join(TAU2_METHODS)))


def random_effects(
    studies: Sequence[Study],
    conf: float = 0.95,
    tau2_method: str = "DL",
    knapp_hartung: bool = True,
) -> Pooled:
    """변량효과 모형. ``knapp_hartung``이면 t 분포 기반 HK 보정 CI를 쓴다."""
    _require(studies)
    k = len(studies)
    tau2 = _tau2(studies, tau2_method)
    weights = [1.0 / (s.vi + tau2) for s in studies]
    est, se, total_w = _pool_with_weights(studies, weights)

    if knapp_hartung and k >= 2:
        # HK 분산: sum w_i (y_i - mu)^2 / ((k-1) * sum w_i)
        num = _weighted_ss(studies, weights, est)
        se_hk = math.sqrt(num / ((k - 1) * total_w)) if math.isfinite(num) else 0.0
        if se_hk <= 0 or not math.isfinite(se_hk):
            # 모든 연구의 효과크기가 완전히 같으면 HK 분산이 0이 되어
            # 폭 0의 신뢰구간과 p=0 을 만들어낸다. 모형기반 z 구간으로 되돌린다.
            z = normal_ppf(0.5 + conf / 2.0)
            stat = est / se
            return Pooled(
                model="random", estimate=est, se=se, se_model=se,
                ci_low=est - z * se, ci_high=est + z * se, stat=stat,
                p=2.0 * normal_sf(abs(stat)), k=k, weights=weights, tau2=tau2,
                tau2_method=tau2_method.upper(), ci_method="z", conf=conf,
                hk_degenerate=True,
            )
        df = float(k - 1)
        t_crit = t_ppf(0.5 + conf / 2.0, df)
        stat = est / se_hk
        p = 2.0 * t_sf(abs(stat), df)
        return Pooled(
            model="random",
            estimate=est,
            se=se_hk,
            se_model=se,
            ci_low=est - t_crit * se_hk,
            ci_high=est + t_crit * se_hk,
            stat=stat,
            p=p,
            k=k,
            weights=weights,
            tau2=tau2,
            tau2_method=tau2_method.upper(),
            ci_method="HK",
            df=df,
            conf=conf,
        )

    z = normal_ppf(0.5 + conf / 2.0)
    stat = est / se
    return Pooled(
        model="random",
        estimate=est,
        se=se,
        se_model=se,
        ci_low=est - z * se,
        ci_high=est + z * se,
        stat=stat,
        p=2.0 * normal_sf(abs(stat)),
        k=k,
        weights=weights,
        tau2=tau2,
        tau2_method=tau2_method.upper(),
        ci_method="z",
        conf=conf,
    )


def heterogeneity(studies: Sequence[Study], tau2_method: str = "DL") -> Heterogeneity:
    """Cochran Q, I^2, H^2, tau^2."""
    _require(studies)
    k = len(studies)
    df = k - 1
    if df < 1:
        return Heterogeneity(0.0, 0, 1.0, 0.0, 1.0, 0.0, 0.0, tau2_method.upper())
    q, _, _ = _q_statistic(studies)
    p = chi2_sf(q, df) if q > 0 else 1.0
    i2 = max(0.0, (q - df) / q) * 100.0 if q > 0 else 0.0
    h2 = q / df if df > 0 else 1.0
    tau2 = _tau2(studies, tau2_method)
    return Heterogeneity(q, df, p, i2, max(h2, 1.0), tau2, math.sqrt(tau2), tau2_method.upper())


def prediction_interval(pooled: Pooled, conf: float = 0.95):
    """향후 유사한 연구 1편의 참효과가 놓일 95% 예측구간.

    mu +- t(k-2) * sqrt(tau^2 + SE(mu)^2). 연구 3편 미만이면 정의하지 않는다.
    SE(mu)는 HK 보정 여부와 무관하게 **모형기반 표준오차**를 쓴다
    (HK 표준오차는 예측구간의 근거가 되는 분산 분해와 맞지 않는다).
    """
    k = pooled.k
    if k < 3:
        return None
    df = float(k - 2)
    t_crit = t_ppf(0.5 + conf / 2.0, df)
    se_m = pooled.se_model or pooled.se
    half = t_crit * math.sqrt(pooled.tau2 + se_m ** 2)
    return (pooled.estimate - half, pooled.estimate + half)


def subgroup_analysis(
    studies: Sequence[Study],
    conf: float = 0.95,
    tau2_method: str = "DL",
    knapp_hartung: bool = True,
    min_k: int = 1,
):
    """하위군별 변량효과 합성 + 하위군 간 차이 검정.

    각 하위군에서 tau^2를 따로 추정하고(하위군별 변량효과),
    Q_between = sum_g (mu_g - mu_bar)^2 / SE_g^2 를 자유도 G-1의 카이제곱으로 검정한다.
    하위군이 1개뿐이거나 유효 하위군이 2개 미만이면 검정은 None.
    """
    groups: Dict[str, List[Study]] = {}
    for s in studies:
        groups.setdefault(s.subgroup or "(미지정)", []).append(s)

    results: List[SubgroupResult] = []
    for name in sorted(groups):
        members = groups[name]
        if len(members) < min_k:
            continue
        # HK 보정은 연구 2편 이상일 때만 의미가 있다.
        pooled = random_effects(
            members, conf=conf, tau2_method=tau2_method, knapp_hartung=knapp_hartung and len(members) >= 2
        )
        het = heterogeneity(members, tau2_method=tau2_method) if len(members) >= 2 else None
        results.append(SubgroupResult(name=name, pooled=pooled, het=het, k=len(members)))

    # Q_between에는 HK 보정 표준오차가 아니라 모형기반 표준오차를 써야 한다.
    testable = [
        r for r in results if r.pooled.se_model > 0 and math.isfinite(r.pooled.se_model)
    ]
    test = None
    if len(testable) >= 2:
        w = [1.0 / (r.pooled.se_model ** 2) for r in testable]
        sw = math.fsum(w)
        mu_bar = math.fsum(wi * r.pooled.estimate for wi, r in zip(w, testable)) / sw
        q_bet = math.fsum(wi * (r.pooled.estimate - mu_bar) ** 2 for wi, r in zip(w, testable))
        df_bet = len(testable) - 1
        test = {
            "q_between": q_bet,
            "df": df_bet,
            "p": chi2_sf(q_bet, df_bet) if q_bet > 0 else 1.0,
            "groups": [r.name for r in testable],
        }
    return results, test
