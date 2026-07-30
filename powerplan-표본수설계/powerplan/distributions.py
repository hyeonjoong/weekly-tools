"""검정력 계산에 필요한 분포함수 — 중심/비중심 t, F, 그리고 그 분위수.

핵심은 **비중심 t 분포**(:func:`nct_cdf`)다. t-검정 계열의 정확한 검정력은
비중심 t의 꼬리확률이며, 흔히 쓰이는 정규근사는 표본이 작을 때 검정력을
과대추정한다. 여기서는 조건부 표현

    P(T' ≤ t) = E_x[ Φ(t·x/√df − δ) ],   x ~ χ_df (카이 분포)

를 Gauss–Legendre 복합구적으로 적분한다. χ 밀도는 **최빈값 기준 로그차이**로
계산한 뒤 적분값으로 자기정규화(self-normalizing)하므로, df가 매우 커도
lgamma의 큰 수 상쇄 오차가 결과에 실리지 않는다.

정확도(mpmath 40자리 대조): 실용 구간에서 상대오차 ~1e-11 이하. 단 **자유도 2 이하 +
임계값이 매우 큰 조합**(예: df=1, t=100)에서는 피적분함수의 0→1 전이가 한 패널 안에
들어가 절대오차가 1e-4까지 커질 수 있다. 이 영역의 검정력은 사실상 0이라 표본수
결과는 바뀌지 않지만, 값을 직접 쓸 때는 알고 있어야 한다.
"""

from __future__ import annotations

import math
from functools import lru_cache

from .special import betainc, bisect_increasing, gauss_legendre, norm_cdf

__all__ = [
    "t_cdf",
    "t_sf",
    "t_ppf",
    "f_cdf",
    "f_ppf",
    "nct_cdf",
    "nct_sf",
    "ncf_sf",
    "chi_expectation",
    "chi_expect",
    "nct_ncp_ci",
]

# 구적 설정: 20점 × 24패널 (위 docstring의 정확도 주석 참고).
_GL_N = 20
_GL_PANELS = 24
_GL_NODES, _GL_WEIGHTS = gauss_legendre(_GL_N)
# 적분 범위: χ 분포의 평균 ± _CHI_SIGMAS·표준편차 (바깥 질량 < 1e-30)
_CHI_SIGMAS = 18.0


#: 이보다 큰 자유도는 로그 계산의 상쇄오차로 신뢰할 수 없다 (실제 연구에선 도달 불가)
MAX_DF = 1e12
#: 비중심 F 급수의 최대 항 수 — 이보다 길어지면 답이 0/1로 포화된 무의미한 계산이다
MAX_NCF_TERMS = 200_000


def _check_df(df: float, name: str = "df") -> float:
    df = float(df)
    if not math.isfinite(df) or df <= 0.0:
        raise ValueError(f"{name}는 0보다 큰 유한한 값이어야 합니다 (받은 값: {df})")
    if df > MAX_DF:
        raise ValueError(
            f"{name}가 너무 큽니다 ({df:.3g} > {MAX_DF:.0g}) — 표본수를 줄이세요. "
            "이 범위에서는 로그 계산의 상쇄오차로 결과를 신뢰할 수 없습니다"
        )
    return df


def t_cdf(t: float, df: float) -> float:
    """중심 t 분포함수."""
    df = _check_df(df)
    if t != t:
        return float("nan")
    if t == float("inf"):
        return 1.0
    if t == float("-inf"):
        return 0.0
    denom = df + t * t
    # x와 1−x를 같은 분모에서 만들어 넘긴다 (df가 클 때 1−x의 정밀도 유지)
    tail = 0.5 * betainc(0.5 * df, 0.5, df / denom, (t * t) / denom)  # = P(T > |t|)
    return 1.0 - tail if t > 0 else tail


def t_sf(t: float, df: float) -> float:
    """중심 t 상측확률 P(T > t)."""
    return t_cdf(-t, df)


@lru_cache(maxsize=4096)
def t_ppf(p: float, df: float) -> float:
    """중심 t 분위수. 이분법이라 df·p 전 구간에서 안정적이다."""
    df = _check_df(df)
    if not (0.0 < p < 1.0):
        if p <= 0.0:
            return float("-inf")
        if p >= 1.0:
            return float("inf")
        return float("nan")
    if p == 0.5:
        return 0.0
    hi = 2.0
    while t_cdf(hi, df) < p and hi < 1e12:
        hi *= 4.0
    lo = -2.0
    while t_cdf(lo, df) > p and lo > -1e12:
        lo *= 4.0
    return bisect_increasing(lambda t: t_cdf(t, df), p, lo, hi)


def f_cdf(x: float, df1: float, df2: float) -> float:
    """중심 F 분포함수."""
    df1, df2 = _check_df(df1, "df1"), _check_df(df2, "df2")
    if x != x:
        return float("nan")
    if x <= 0.0:
        return 0.0
    if x == float("inf"):
        return 1.0
    denom = df1 * x + df2
    return betainc(0.5 * df1, 0.5 * df2, (df1 * x) / denom, df2 / denom)


@lru_cache(maxsize=4096)
def f_ppf(p: float, df1: float, df2: float) -> float:
    """중심 F 분위수."""
    df1, df2 = _check_df(df1, "df1"), _check_df(df2, "df2")
    if not (0.0 < p < 1.0):
        if p <= 0.0:
            return 0.0
        if p >= 1.0:
            return float("inf")
        return float("nan")
    hi = 2.0
    while f_cdf(hi, df1, df2) < p and hi < 1e14:
        hi *= 4.0
    return bisect_increasing(lambda x: f_cdf(x, df1, df2), p, 0.0, hi)


@lru_cache(maxsize=512)
def chi_expect(df: float) -> tuple[float, float]:
    """χ_df 분포의 (평균, 표준편차). df가 크면 안정적인 근사식으로 넘어간다."""
    df = _check_df(df)
    if df > 300.0:
        # E[χ] = √(df − 1/2 + 1/(8df) + ...) — 상대오차 < 1e-12
        mean = math.sqrt(df - 0.5 + 0.125 / df)
    else:
        mean = math.sqrt(2.0) * math.exp(math.lgamma(0.5 * (df + 1.0)) - math.lgamma(0.5 * df))
    var = max(df - mean * mean, 1e-300)
    return mean, math.sqrt(var)


@lru_cache(maxsize=512)
def _chi_quadrature(df: float) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """χ_df 밀도에 대한 (노드, 정규화된 가중치). 가중치 합 = 1."""
    df = _check_df(df)
    mean, sd = chi_expect(df)
    lo = max(1e-12, mean - _CHI_SIGMAS * sd)
    hi = mean + _CHI_SIGMAS * sd
    step = (hi - lo) / _GL_PANELS
    half = 0.5 * step
    log_mode = math.log(math.sqrt(max(df - 1.0, 1e-12))) if df > 1.0 else math.log(mean)
    mode = math.exp(log_mode)
    nodes: list[float] = []
    raw: list[float] = []
    for panel in range(_GL_PANELS):
        centre = lo + step * panel + half
        for node, weight in zip(_GL_NODES, _GL_WEIGHTS):
            x = centre + half * node
            if x <= 0.0:
                continue
            # 최빈값 기준 로그차이 → 큰 df에서도 상쇄오차 없음
            log_shape = (df - 1.0) * (math.log(x) - log_mode) - 0.5 * (x * x - mode * mode)
            if log_shape < -745.0:
                continue
            nodes.append(x)
            raw.append(weight * half * math.exp(log_shape))
    total = math.fsum(raw)
    if total <= 0.0:  # 방어적: 수치적으로 불가능하나 0 나눗셈은 막는다
        raise ValueError(f"χ 구적 실패 (df={df})")
    return tuple(nodes), tuple(w / total for w in raw)


def chi_expectation(df: float, fn) -> float:
    """E[fn(x)], x ~ χ_df. 비중심 t·TOST 검정력이 모두 이 기대값 형태다.

    fn은 x > 0에서 유계인 매끄러운 함수여야 한다(확률을 돌려주는 함수).
    """
    nodes, weights = _chi_quadrature(df)
    acc = 0.0
    for x, w in zip(nodes, weights):
        acc += w * fn(x)
    return acc


def nct_cdf(t: float, df: float, ncp: float) -> float:
    """비중심 t 분포함수 P(T' ≤ t | df, ncp)."""
    df = _check_df(df)
    ncp = float(ncp)
    if t != t or ncp != ncp:
        return float("nan")
    if ncp == 0.0:
        return t_cdf(t, df)
    if t == float("inf"):
        return 1.0
    if t == float("-inf"):
        return 0.0
    nodes, weights = _chi_quadrature(df)
    scale = t / math.sqrt(df)
    acc = 0.0
    for x, w in zip(nodes, weights):
        acc += w * norm_cdf(scale * x - ncp)
    return min(1.0, max(0.0, acc))


def nct_sf(t: float, df: float, ncp: float) -> float:
    """비중심 t 상측확률 P(T' > t). 대칭성 P(T'>t|δ) = P(T'<-t|-δ) 이용."""
    df = _check_df(df)
    ncp = float(ncp)
    if ncp == 0.0:
        return t_sf(t, df)
    nodes, weights = _chi_quadrature(df)
    scale = -t / math.sqrt(df)
    acc = 0.0
    for x, w in zip(nodes, weights):
        acc += w * norm_cdf(scale * x + ncp)
    return min(1.0, max(0.0, acc))


def ncf_sf(x: float, df1: float, df2: float, ncp: float) -> float:
    """비중심 F 상측확률 P(F' > x | df1, df2, ncp).

    Poisson 가중 불완전베타 급수. 절단오차는 남은 Poisson 질량으로 상한이
    보장된다(I_y ≤ 1). ncp가 커도 최빈값 주변만 계산해 항 수를 억제한다.
    """
    df1, df2 = _check_df(df1, "df1"), _check_df(df2, "df2")
    ncp = float(ncp)
    if ncp < 0.0:
        raise ValueError(f"ncf_sf: ncp >= 0 이어야 합니다 (받은 값: {ncp})")
    if x != x:
        return float("nan")
    if x <= 0.0:
        return 1.0
    if x == float("inf"):
        return 0.0
    if ncp == 0.0:
        return 1.0 - f_cdf(x, df1, df2)

    half = 0.5 * ncp
    denom = df1 * x + df2
    y, y1m = (df1 * x) / denom, df2 / denom
    a0, b = 0.5 * df1, 0.5 * df2
    sd = math.sqrt(half)
    j_lo = max(0, int(half - 12.0 * sd - 20.0))
    j_hi = int(half + 12.0 * sd + 60.0)
    if j_hi - j_lo > MAX_NCF_TERMS:
        raise ValueError(
            f"ncf_sf: 비중심모수 λ={ncp:.3g}가 너무 큽니다 — 효과크기(--f)나 표본수(--n)를 "
            "확인하세요 (이 범위의 검정력은 사실상 100%입니다)"
        )
    log_half = math.log(half)
    acc = 0.0
    mass = 0.0
    for j in range(j_lo, j_hi + 1):
        log_w = -half + j * log_half - math.lgamma(j + 1.0)
        if log_w < -745.0:
            continue
        w = math.exp(log_w)
        mass += w
        acc += w * (1.0 - betainc(a0 + j, b, y, y1m))
    # 남은 질량이 있으면(극단적 ncp) 위쪽 항을 더 채운다.
    # 임계값 1e-7: ±12σ 바깥의 진짜 절단오차는 1e-30 미만이고, 이보다 작은 결손은
    # 로그 가중치의 상쇄 노이즈(λ·1e-16)일 뿐이라 계산을 늘려도 정확도가 오르지 않는다.
    if mass < 1.0 - 1e-7 and j_hi < 10_000_000:
        j = j_hi + 1
        while mass < 1.0 - 1e-12 and j < j_hi + 2_000_000:
            log_w = -half + j * log_half - math.lgamma(j + 1.0)
            if log_w < -745.0:
                break
            w = math.exp(log_w)
            mass += w
            acc += w * (1.0 - betainc(a0 + j, b, y, y1m))
            j += 1
    return min(1.0, max(0.0, acc))


def nct_ncp_ci(t_obs: float, df: float, conf: float = 0.95) -> tuple[float, float]:
    """관측 t값으로부터 비중심모수(ncp)의 정확 신뢰구간.

    P(T' ≤ t_obs | ncp) 가 ncp에 대해 감소함을 이용한 pivot 방식(Steiger & Fouladi).
    효과크기 d의 정확 신뢰구간을 만들 때 쓴다.
    """
    df = _check_df(df)
    if not (0.0 < conf < 1.0):
        raise ValueError(f"conf는 0과 1 사이여야 합니다 (받은 값: {conf})")
    alpha = 1.0 - conf

    def solve(target: float) -> float:
        # ncp가 커지면 cdf가 작아지므로 -cdf는 증가함수
        lo, hi = t_obs - 10.0 - abs(t_obs), t_obs + 10.0 + abs(t_obs)
        guard = 0
        while nct_cdf(t_obs, df, lo) < target and guard < 60:
            lo -= 10.0 + abs(lo)
            guard += 1
        guard = 0
        while nct_cdf(t_obs, df, hi) > target and guard < 60:
            hi += 10.0 + abs(hi)
            guard += 1
        return bisect_increasing(lambda ncp: -nct_cdf(t_obs, df, ncp), -target, lo, hi)

    upper = solve(alpha / 2.0)        # cdf = α/2 → 큰 ncp
    lower = solve(1.0 - alpha / 2.0)  # cdf = 1−α/2 → 작은 ncp
    return lower, upper
