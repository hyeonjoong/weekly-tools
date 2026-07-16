"""자립형(scipy 불필요) 통계 함수.

- 카이제곱 상측꼬리확률(Bartlett 구형성 검정·ML 적합도 검정의 p값)
- 비중심 카이제곱 CDF와 그 비중심모수 역산(RMSEA 신뢰구간)

정규화 불완전감마 함수는 Numerical Recipes의 급수/연분수 전개를 따른다.
scipy에 의존하지 않기 위함.
"""
from __future__ import annotations

import math

_MAXIT = 1000
_EPS = 1e-15
_FPMIN = 1e-300


def _gser(a: float, x: float) -> float:
    """정규화 하측 불완전감마 P(a, x)를 급수 전개로 계산 (x < a+1 에서 수렴 빠름)."""
    if x <= 0.0:
        return 0.0
    gln = math.lgamma(a)
    ap = a
    total = 1.0 / a
    delta = total
    for _ in range(_MAXIT):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * _EPS:
            break
    return total * math.exp(-x + a * math.log(x) - gln)


def _gcf(a: float, x: float) -> float:
    """정규화 상측 불완전감마 Q(a, x)를 연분수로 계산 (x >= a+1 에서 수렴 빠름)."""
    gln = math.lgamma(a)
    b = x + 1.0 - a
    c = 1.0 / _FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, _MAXIT):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = b + an / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def gammq(a: float, x: float) -> float:
    """정규화 상측 불완전감마 Q(a, x) = 1 - P(a, x)."""
    if x < 0.0 or a <= 0.0:
        raise ValueError("gammq: a>0, x>=0 이어야 합니다")
    if x == 0.0:
        return 1.0
    if x < a + 1.0:
        return 1.0 - _gser(a, x)
    return _gcf(a, x)


def chi2_sf(x: float, df: float) -> float:
    """자유도 df인 카이제곱 분포의 상측꼬리확률 P(X > x)."""
    if df <= 0:
        raise ValueError("chi2_sf: 자유도는 양수여야 합니다")
    if x <= 0.0:
        return 1.0
    return gammq(df / 2.0, x / 2.0)


def chi2_cdf(x: float, df: float) -> float:
    """자유도 df인 카이제곱 분포의 누적확률 P(X <= x)."""
    return 1.0 - chi2_sf(x, df)


# 비중심 카이제곱 급수의 최대 항 수. λ가 커지면 포아송 가중치의 질량이 j≈λ/2 부근에
# 몰리므로, 아래 ncx2_cdf는 그 최빈항에서 양방향으로 전개해 항 수를 절약한다.
_NCX2_MAXIT = 10000


def ncx2_cdf(x: float, df: float, nc: float) -> float:
    """비중심 카이제곱 CDF P(X <= x; df, 비중심모수 nc).

    포아송 혼합 표현을 쓴다:  P(X<=x) = Σ_j e^{-nc/2}(nc/2)^j / j! · P_central(x; df+2j).
    질량 중심(j≈nc/2)에서 시작해 양방향으로 더해, nc가 커도 항 수가 폭발하지 않는다.
    nc=0이면 중심 카이제곱과 정확히 일치한다.
    """
    if df <= 0:
        raise ValueError("ncx2_cdf: 자유도는 양수여야 합니다")
    if nc < 0:
        raise ValueError("ncx2_cdf: 비중심모수는 0 이상이어야 합니다")
    if x <= 0.0:
        return 0.0
    if nc == 0.0:
        return chi2_cdf(x, df)

    half = nc / 2.0
    j0 = int(half)  # 포아송 최빈항
    # log 공간에서 가중치를 계산해 큰 nc에서의 언더/오버플로를 피한다.
    def _term(j: int) -> float:
        logw = -half + j * math.log(half) - math.lgamma(j + 1.0)
        if logw < -745.0:      # exp 언더플로 하한
            return 0.0
        return math.exp(logw) * chi2_cdf(x, df + 2.0 * j)

    total = _term(j0)
    # 위쪽으로 전개
    for j in range(j0 + 1, j0 + _NCX2_MAXIT):
        t = _term(j)
        total += t
        if t < _EPS * max(total, 1e-300):
            break
    # 아래쪽으로 전개
    for j in range(j0 - 1, -1, -1):
        t = _term(j)
        total += t
        if t < _EPS * max(total, 1e-300):
            break
    return min(max(total, 0.0), 1.0)


def ncx2_sf(x: float, df: float, nc: float) -> float:
    """비중심 카이제곱 상측꼬리확률 P(X > x; df, nc).

    `1 - ncx2_cdf(...)`로 구하면 CDF가 1에 붙는 순간 유효숫자가 전부 사라져(파국적 상쇄)
    아주 작은 꼬리확률이 1e-15 수준의 부동소수 잡음으로 뭉개진다. 그래서 CDF를 빼지 않고
    **상측꼬리를 직접** 포아송 혼합으로 합산한다:
        P(X>x) = Σ_j e^{-nc/2}(nc/2)^j / j! · Q_central(x; df+2j)
    chi2_sf(=gammq)는 꼬리에서 연분수 전개를 쓰므로 각 항이 작은 값에서도 정확하고,
    항들이 모두 양수라 합산에도 상쇄가 없다(PCLOSE가 1e-100 영역에서도 유효).
    """
    if df <= 0:
        raise ValueError("ncx2_sf: 자유도는 양수여야 합니다")
    if nc < 0:
        raise ValueError("ncx2_sf: 비중심모수는 0 이상이어야 합니다")
    if x <= 0.0:
        return 1.0
    if nc == 0.0:
        return chi2_sf(x, df)

    half = nc / 2.0
    j0 = int(half)

    def _term(j: int) -> float:
        logw = -half + j * math.log(half) - math.lgamma(j + 1.0)
        if logw < -745.0:
            return 0.0
        return math.exp(logw) * chi2_sf(x, df + 2.0 * j)

    total = _term(j0)
    for j in range(j0 + 1, j0 + _NCX2_MAXIT):
        t = _term(j)
        total += t
        # 상측꼬리 항은 j가 커질수록 1로 다가가므로, 포아송 가중치가 말라야 멈춘다.
        if t < _EPS * max(total, 1e-300) and j > half:
            break
    for j in range(j0 - 1, -1, -1):
        t = _term(j)
        total += t
        if t < _EPS * max(total, 1e-300):
            break
    return min(max(total, 0.0), 1.0)


def ncx2_nc_for_quantile(x: float, df: float, prob: float,
                         upper: float = 1e7, tol: float = 1e-8) -> float:
    """P(X <= x; df, nc) = prob 을 만족하는 비중심모수 nc를 이분법으로 찾는다.

    RMSEA 신뢰구간의 표준 절차(Steiger). CDF는 nc에 대해 단조감소하므로 이분법이 안전하다.
    해가 존재하지 않으면(즉 nc=0에서 이미 CDF < prob) 0.0을 돌려준다.
    """
    if not (0.0 < prob < 1.0):
        raise ValueError("ncx2_nc_for_quantile: prob는 0과 1 사이여야 합니다")
    if ncx2_cdf(x, df, 0.0) < prob:
        return 0.0                      # 아무리 nc를 줄여도 prob에 못 미침 → 하한 0
    lo, hi = 0.0, 1.0
    while ncx2_cdf(x, df, hi) > prob:   # CDF가 prob 밑으로 내려갈 때까지 상한 확장
        hi *= 2.0
        if hi > upper:
            return float(upper)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if ncx2_cdf(x, df, mid) > prob:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol * max(1.0, hi):
            break
    return 0.5 * (lo + hi)
