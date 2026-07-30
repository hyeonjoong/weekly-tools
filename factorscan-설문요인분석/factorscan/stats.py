"""자립형(scipy 불필요) 통계 함수.

- 카이제곱 상측꼬리확률(Bartlett 구형성 검정·ML 적합도 검정의 p값)
- 비중심 카이제곱 CDF와 그 비중심모수 역산(RMSEA 신뢰구간)
- 정규화 불완전베타와 F 분포 CDF/분위수(Cronbach α 신뢰구간)

정규화 불완전감마·불완전베타 함수는 Numerical Recipes의 급수/연분수 전개를 따른다.
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
    """자유도 df인 카이제곱 분포의 누적확률 P(X <= x).

    `1 - chi2_sf(x, df)` 로 구하면 안 된다. chi2_sf는 x < a+1 구간에서 `1 - _gser(a,x)`를
    돌려주므로, 그 결과를 다시 1에서 빼면 `1 - (1 - t)` 왕복이 되어 t의 유효숫자가 통째로
    날아간다(파국적 상쇄). 실제로 P(X≤1; df=100) = 1.79e-80 이 정확히 0.0으로 뭉개졌다.
    하측꼬리는 _gser가 이미 정확한 값을 갖고 있으므로 그것을 직접 쓴다.
    """
    if df <= 0:
        raise ValueError("chi2_cdf: 자유도는 양수여야 합니다")
    a, xx = df / 2.0, x / 2.0
    if xx <= 0.0:
        return 0.0
    if xx < a + 1.0:
        return _gser(a, xx)
    return 1.0 - _gcf(a, xx)


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
    # 아래쪽으로 전개. 포아송 가중치는 줄지만 chi2_cdf(x, df+2j)는 j가 작아질수록
    # **커지므로** 항이 단조감소하지 않는다 — 첫 항 하나로 끊으면 최빈항이 언더플로한
    # 구간에서 급수가 통째로 잘려 CDF가 0으로 붕괴한다. 연속으로 무시할 만할 때만 멈춘다.
    # 게다가 최빈항 자체가 언더플로해 total==0 인 상태로 시작할 수 있다(x가 df에 비해
    # 아주 작을 때). 그때 '작은 항'으로 끊으면 실제 질량이 있는 j≈0 구간에 닿기도 전에
    # 급수가 끝나 CDF가 0으로 붕괴한다 — total이 아직 0이면 멈추지 않는다.
    small = 0
    for j in range(j0 - 1, -1, -1):
        # 최빈항 아래에서는 포아송 가중치가 j가 줄수록 **단조감소**한다. 그 가중치가
        # 표현 가능한 범위 아래로 내려가면 F_central ≤ 1 이므로 남은 항은 어떤 경우에도
        # 기여할 수 없다 — 여기서 끊어야 nc가 클 때 500만 번을 도는 일이 없다.
        logw = -half + j * math.log(half) - math.lgamma(j + 1.0)
        if logw < -745.0:
            break
        t = _term(j)
        total += t
        if total > 0.0 and t < _EPS * total:
            small += 1
            if small >= 3:
                break
        else:
            small = 0
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


def _betacf(a: float, b: float, x: float) -> float:
    """정규화 불완전베타의 연분수 전개(Lentz 알고리즘, Numerical Recipes 6.4)."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, _MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def betai(a: float, b: float, x: float) -> float:
    """정규화 불완전베타 I_x(a, b) = B(x; a,b) / B(a,b).  0 ≤ x ≤ 1."""
    if a <= 0.0 or b <= 0.0:
        raise ValueError("betai: a>0, b>0 이어야 합니다")
    if x < 0.0 or x > 1.0:
        raise ValueError("betai: x는 0..1 범위여야 합니다")
    if x == 0.0:
        return 0.0
    if x == 1.0:
        return 1.0
    ln_bt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log1p(-x))
    bt = math.exp(ln_bt)
    # 연분수는 x < (a+1)/(a+b+2) 에서 빠르게 수렴한다. 반대쪽은 대칭관계로 뒤집는다.
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def f_cdf(x: float, d1: float, d2: float) -> float:
    """F(d1, d2) 분포의 누적확률 P(X ≤ x) = I_{d1·x/(d1·x+d2)}(d1/2, d2/2).

    x가 커지면 d1·x/(d1·x+d2)가 부동소수에서 정확히 1.0으로 반올림돼 CDF가 1.0에 붙고,
    그 위쪽 분위수를 역산할 수 없게 된다. 그래서 그 구간에서는 대칭관계
    I_z(a,b) = 1 − I_{1−z}(b,a) 를 써서 **작은 인수** d2/(d1x+d2)로 계산한다.
    """
    if d1 <= 0 or d2 <= 0:
        raise ValueError("f_cdf: 자유도는 양수여야 합니다")
    if x <= 0.0:
        return 0.0
    if math.isinf(x):
        return 1.0
    denom = d1 * x + d2
    if not math.isfinite(denom) or denom <= 0.0:
        return 1.0
    z = d1 * x / denom
    # z가 부동소수에서 정확히 1.0이 되면 betai(·, z)로는 값을 잃는다 — 그때만 대칭식.
    if z >= 1.0:
        return 1.0 - betai(d2 / 2.0, d1 / 2.0, d2 / denom)
    return betai(d1 / 2.0, d2 / 2.0, z)


def f_sf(x: float, d1: float, d2: float) -> float:
    """F(d1, d2) 분포의 상측꼬리확률 P(X > x). 1−CDF의 상쇄를 피해 직접 계산."""
    if d1 <= 0 or d2 <= 0:
        raise ValueError("f_sf: 자유도는 양수여야 합니다")
    if x <= 0.0:
        return 1.0
    if math.isinf(x):
        return 0.0
    denom = d1 * x + d2
    if not math.isfinite(denom) or denom <= 0.0:
        return 0.0
    z = d1 * x / denom
    w = d2 / denom
    # 상측꼬리는 대칭식이 '작은 값을 직접' 주므로 기본으로 쓰고, w가 1.0으로
    # 반올림되는 반대쪽 극단에서만 1−betai 로 돌아간다.
    if w <= 0.0:
        return 0.0
    if w < 1.0:
        return betai(d2 / 2.0, d1 / 2.0, w)
    return 1.0 - betai(d1 / 2.0, d2 / 2.0, z)


def f_ppf(p: float, d1: float, d2: float, tol: float = 1e-12) -> float:
    """F(d1, d2) 분포의 분위수 F⁻¹(p). CDF가 단조증가하므로 이분법으로 역산한다.

    scipy 없이 Cronbach α의 Feldt 신뢰구간을 내기 위해 필요하다.
    수렴 판정은 **상대** 오차로 한다 — `hi - lo < tol * max(1.0, hi)` 처럼 1을 바닥으로
    깔면 1보다 훨씬 작은 분위수(예: F⁻¹(1e-5) ≈ 1.6e-10)에서 절대해상도 1e-10에 갇혀
    유효숫자가 남지 않는다.
    """
    if not (0.0 < p < 1.0):
        raise ValueError("f_ppf: p는 0과 1 사이여야 합니다")
    if d1 <= 0 or d2 <= 0:
        raise ValueError("f_ppf: 자유도는 양수여야 합니다")
    # 목표를 '작은 쪽 확률'로 잡아야 상대정밀도가 산다. p<0.5 면 CDF로, 아니면 상측꼬리로
    # 비교한다 — 1e-15 분위수를 1−p=1−1e-15 와 견주면 부동소수 해상도가 남지 않는다.
    lower = p < 0.5
    target = p if lower else 1.0 - p

    def _tail(v: float) -> float:
        return f_cdf(v, d1, d2) if lower else f_sf(v, d1, d2)

    # _tail 은 lower면 증가, 아니면 감소 — 부호를 맞춰 단조증가 함수로 취급한다.
    def _mono(v: float) -> float:
        return _tail(v) if lower else -_tail(v)

    goal = target if lower else -target
    lo, hi = 0.0, 1.0
    for _ in range(4000):
        if _mono(hi) >= goal:
            break
        lo = hi
        hi *= 2.0
        if not math.isfinite(hi):
            return math.inf
    else:                                   # 상한을 못 찾음 → 사실상 무한대
        return hi
    for _ in range(400):
        mid = 0.5 * (lo + hi)
        if _mono(mid) < goal:
            lo = mid
        else:
            hi = mid
        if hi - lo <= tol * max(hi, 1e-300):
            break
    return 0.5 * (lo + hi)


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
    # 상한에서도 CDF가 prob를 넘으면 해는 상한 밖이다. 미리 확인하면 χ²가 1e6 근처일 때
    # 2배씩 20번 확장하며 매번 급수를 도는 비용(수십 초)을 건너뛴다.
    if ncx2_cdf(x, df, upper) > prob:
        return float(upper)
    # 상한을 1에서 2배씩 올리면 χ²가 10⁶ 규모일 때 확장 단계에서만 급수를 20번 돈다.
    # 비중심모수는 대략 x 규모를 넘지 않으므로(평균 = df + nc) 거기서 시작한다.
    lo, hi = 0.0, min(float(upper), max(1.0, 2.0 * x + 10.0 * df))
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
