"""효과크기 합성(pooling)·이질성·하위군 분석.

- 고정효과(fixed-effect): 역분산 가중
- 변량효과(random-effects): DerSimonian–Laird / Paule–Mandel / REML / Sidik–Jonkman
- Hartung–Knapp(–Sidik–Jonkman) 보정 신뢰구간 (연구 수가 적을 때 1종 오류 억제)
- 이질성: Q, I^2, H^2, tau^2, 95% 예측구간, tau^2·I^2 의 Q-profile 신뢰구간
- 하위군: 하위군별 변량효과 + Q_between (혼합효과 Wald 검정)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .distributions import chi2_ppf, chi2_sf, normal_ppf, normal_sf, t_ppf, t_sf
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
    "tau2_reml",
    "tau2_reml_converged",
    "_reml_score",
    "tau2_sidik_jonkman",
    "tau2_ci_qprofile",
    "typical_within_variance",
    "prediction_interval",
    "subgroup_analysis",
    "MetaError",
]

#: tau^2 추정법. DL=DerSimonian–Laird, PM=Paule–Mandel(=경험적 베이즈),
#: REML=제한최대가능도(metafor 기본값), SJ=Sidik–Jonkman.
TAU2_METHODS = ("DL", "PM", "REML", "SJ")


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
    #: tau^2 의 Q-profile(Viechtbauer 2007) 신뢰구간. 연구 2편 미만이면 None.
    tau2_ci: Optional["tuple[float, float]"] = None
    #: 위 tau^2 구간에서 유도한 I^2(백분율) 신뢰구간.
    i2_ci: Optional["tuple[float, float]"] = None
    #: 구간을 계산한 신뢰수준
    ci_conf: float = 0.95


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


def generalized_q(studies: Sequence[Study], tau2: float) -> float:
    """일반화 Q(tau^2) = sum 1/(v_i+tau^2) * (y_i - mu(tau^2))^2.

    tau^2 에 대해 단조감소한다. Paule–Mandel 추정과 Q-profile 신뢰구간이
    모두 이 함수의 역함수를 푸는 문제라서 한 곳에 모아 둔다.
    """
    w = [1.0 / (s.vi + tau2) for s in studies]
    sw = math.fsum(w)
    mu = math.fsum(wi * s.yi for wi, s in zip(w, studies)) / sw
    return math.fsum(wi * (s.yi - mu) ** 2 for wi, s in zip(w, studies))


def _solve_gen_q(studies: Sequence[Study], target: float, tol: float = 1e-10,
                 max_iter: int = 200) -> float:
    """generalized_q(tau^2) = target 을 만족하는 tau^2 (>=0). 없으면 0."""
    if generalized_q(studies, 0.0) <= target:
        return 0.0
    hi = max(tau2_dersimonian_laird(studies), 1e-8)
    for _ in range(200):  # 상한 확장
        if generalized_q(studies, hi) <= target:
            break
        hi *= 2.0
        if not math.isfinite(hi):  # pragma: no cover - 도달 불가
            return math.inf
    else:  # pragma: no cover - 수치적으로 도달하기 어려운 경로
        return hi
    lo = 0.0
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        if generalized_q(studies, mid) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol * max(1.0, hi):
            break
    return 0.5 * (lo + hi)


def tau2_paule_mandel(studies: Sequence[Study], tol: float = 1e-10, max_iter: int = 200) -> float:
    """Paule–Mandel tau^2: sum w_i(tau^2)*(y_i - mu(tau^2))^2 = k-1 을 만족하는 tau^2.

    DL보다 편향이 작다고 알려져 있어 연구 수가 적을 때 특히 권장된다.
    해가 존재하지 않으면(=이질성이 통계적으로 없으면) 0을 반환한다.
    """
    _require(studies)
    k = len(studies)
    if k < 2:
        return 0.0
    return _solve_gen_q(studies, float(k - 1), tol=tol, max_iter=max_iter)


#: 마지막 REML 호출이 수렴했는지. random_effects/heterogeneity 가 읽어 경고로 올린다.
#: (순수 함수 서명을 유지하면서 수렴 실패를 숨기지 않기 위한 최소한의 장치)
def _reml_score(studies: Sequence[Study], tau2: float) -> float:
    """REML 로그가능도의 tau^2 미분(양수 배수).

    l_R 를 미분하고 w_i = 1/(v_i+tau^2) 로 정리하면
        S(tau^2) = sum w_i^2 (y_i-mu)^2 - sum w_i + (sum w_i^2)/(sum w_i)
    가 된다 (sum w_i^2 v_i + tau^2 sum w_i^2 = sum w_i 를 이용). S 의 근이 REML 해다.
    """
    w = [1.0 / (s.vi + tau2) for s in studies]
    sw = math.fsum(w)
    sw2 = math.fsum(wi * wi for wi in w)
    mu = math.fsum(wi * s.yi for wi, s in zip(w, studies)) / sw
    return math.fsum(wi * wi * (s.yi - mu) ** 2 for wi, s in zip(w, studies)) - sw + sw2 / sw


def tau2_reml_converged(studies: Sequence[Study], tol: float = 1e-12,
                        max_iter: int = 200) -> "tuple[float, bool]":
    """REML tau^2 와 수렴 여부.

    고정점 반복(tau2 <- ...)은 스텝이 극단적으로 작아질 수 있어 (연구 하나만
    아주 정밀한 흔한 배치에서) 500회를 돌려도 해 근처에 못 간다 —
    그러면 "수렴하지 않은 값"이 그대로 통합 추정치·p값·논문 문장에 실린다.
    그래서 고정점 대신 **점수함수 S(tau^2)의 부호 변화를 이분법으로** 잡는다.
    S 는 tau^2 가 커지면 감소하므로 S(0) <= 0 이면 해는 0(경계)이다.
    """
    _require(studies)
    k = len(studies)
    if k < 2:
        return 0.0, True
    if _reml_score(studies, 0.0) <= 0.0:
        return 0.0, True
    hi = max(tau2_dersimonian_laird(studies), max(s.vi for s in studies), 1e-8)
    for _ in range(200):  # 부호가 바뀔 때까지 상한 확장
        if _reml_score(studies, hi) <= 0.0:
            break
        hi *= 2.0
        if not math.isfinite(hi):  # pragma: no cover - 도달 불가
            return math.inf, False
    else:  # pragma: no cover - 수치적으로 도달하기 어려운 경로
        return hi, False
    lo = 0.0
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        if _reml_score(studies, mid) > 0.0:
            lo = mid
        else:
            hi = mid
        if hi - lo <= tol * max(1.0, hi):
            return 0.5 * (lo + hi), True
    return 0.5 * (lo + hi), False  # pragma: no cover - 200회 이분법이면 항상 수렴


def tau2_reml(studies: Sequence[Study], tol: float = 1e-12, max_iter: int = 200) -> float:
    """제한최대가능도(REML) tau^2 — R ``metafor::rma`` 의 기본 추정법.

    REML 점수함수의 근을 이분법으로 구한다. 수렴 여부까지 필요하면
    :func:`tau2_reml_converged` 를 쓴다 (분석 파이프라인이 그쪽을 써서
    수렴 실패를 경고로 올린다).
    """
    return tau2_reml_converged(studies, tol=tol, max_iter=max_iter)[0]


def tau2_sidik_jonkman(studies: Sequence[Study]) -> float:
    """Sidik–Jonkman tau^2 (모형오차 분산 추정).

    비가중 분산으로 초기값을 잡고 r_i = (v_i + tau0^2)/tau0^2 로 재가중한다.
    구조상 항상 0보다 커서, DL이 0으로 절단되는 상황에서도 보수적인
    (=더 넓은) 신뢰구간을 준다.
    """
    _require(studies)
    k = len(studies)
    if k < 2:
        return 0.0
    ybar = math.fsum(s.yi for s in studies) / k
    tau0 = math.fsum((s.yi - ybar) ** 2 for s in studies) / k
    if tau0 <= 0:
        # 모든 효과크기가 같다 — Sidik–Jonkman 권고대로 아주 작은 값으로 대체
        tau0 = 0.01 * math.fsum(s.vi for s in studies) / k
        if tau0 <= 0:  # pragma: no cover - vi>0 이 보장되므로 도달 불가
            return 0.0
    r = [(s.vi + tau0) / tau0 for s in studies]
    inv = [1.0 / ri for ri in r]
    sinv = math.fsum(inv)
    mu = math.fsum(ii * s.yi for ii, s in zip(inv, studies)) / sinv
    return math.fsum(ii * (s.yi - mu) ** 2 for ii, s in zip(inv, studies)) / (k - 1)


def typical_within_variance(studies: Sequence[Study]) -> float:
    """Higgins–Thompson의 "전형적" 연구내 분산 s^2.

    s^2 = (k-1) * sum(w) / (sum(w)^2 - sum(w^2)),  w_i = 1/v_i.
    I^2 = tau^2 / (tau^2 + s^2) 관계로 tau^2 구간을 I^2 구간으로 옮길 때 쓴다.
    """
    _require(studies)
    k = len(studies)
    if k < 2:
        return 0.0
    w = [1.0 / s.vi for s in studies]
    sw = math.fsum(w)
    sw2 = math.fsum(wi * wi for wi in w)
    denom = sw * sw - sw2
    if denom <= 0:
        return 0.0
    return (k - 1) * sw / denom


def tau2_ci_qprofile(studies: Sequence[Study], conf: float = 0.95):
    """tau^2 의 Q-profile(일반화 Q 통계량) 신뢰구간 — Viechtbauer(2007).

    generalized_q(tau^2) 는 tau^2 에 대해 단조감소하고 귀무가설 아래
    자유도 k-1 의 카이제곱을 따른다. 그래서
        하한: generalized_q(tau^2) = chi2_{1-a/2}(k-1)
        상한: generalized_q(tau^2) = chi2_{a/2}(k-1)
    를 각각 풀면 된다. 어느 쪽이든 tau^2=0 에서 이미 임계값 아래면 0으로 둔다.
    연구 2편 미만이면 None.
    """
    _require(studies)
    k = len(studies)
    if k < 2:
        return None
    alpha = 1.0 - conf
    df = float(k - 1)
    q_hi_crit = chi2_ppf(1.0 - alpha / 2.0, df)
    q_lo_crit = chi2_ppf(alpha / 2.0, df)
    low = _solve_gen_q(studies, q_hi_crit)
    high = _solve_gen_q(studies, q_lo_crit)
    if high < low:  # pragma: no cover - 수치오차 방어
        low, high = high, low
    return (low, high)


def _tau2(studies, method: str) -> float:
    method = method.upper()
    if method == "DL":
        return tau2_dersimonian_laird(studies)
    if method == "PM":
        return tau2_paule_mandel(studies)
    if method == "REML":
        return tau2_reml(studies)
    if method == "SJ":
        return tau2_sidik_jonkman(studies)
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


def heterogeneity(studies: Sequence[Study], tau2_method: str = "DL",
                  conf: float = 0.95) -> Heterogeneity:
    """Cochran Q, I^2, H^2, tau^2 와 tau^2·I^2 의 Q-profile 신뢰구간."""
    _require(studies)
    k = len(studies)
    df = k - 1
    if df < 1:
        return Heterogeneity(0.0, 0, 1.0, 0.0, 1.0, 0.0, 0.0, tau2_method.upper(),
                             None, None, conf)
    q, _, _ = _q_statistic(studies)
    p = chi2_sf(q, df) if q > 0 else 1.0
    tau2 = _tau2(studies, tau2_method)
    # I^2·H^2 는 **선택한 tau^2 추정법과 같은 척도**로 계산해야 한다
    #   I^2 = tau^2 / (tau^2 + s^2),   H^2 = (tau^2 + s^2) / s^2
    # DL 에서는 이 식이 (Q-df)/Q, Q/df 와 대수적으로 동일하므로 기존 값이 그대로
    # 나오고, PM/REML/SJ 에서는 metafor 와 같은 값을 준다. (예전처럼 Q 기반으로
    # 두면 점추정이 자기 신뢰구간 밖으로 나가는 일이 실제로 생겼다.)
    s2 = typical_within_variance(studies)
    if s2 > 0:
        i2 = 100.0 * tau2 / (tau2 + s2)
        h2 = (tau2 + s2) / s2
    else:  # pragma: no cover - vi>0 이 보장되면 s2>0
        i2 = max(0.0, (q - df) / q) * 100.0 if q > 0 else 0.0
        h2 = q / df if df > 0 else 1.0
    tau2_ci = tau2_ci_qprofile(studies, conf=conf)
    i2_ci = None
    if tau2_ci is not None and s2 > 0:
        i2_ci = tuple(100.0 * t / (t + s2) for t in tau2_ci)
    return Heterogeneity(q, df, p, i2, max(h2, 1.0), tau2, math.sqrt(tau2),
                         tau2_method.upper(), tau2_ci, i2_ci, conf)


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
        het = heterogeneity(members, tau2_method=tau2_method, conf=conf) if len(members) >= 2 else None
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
