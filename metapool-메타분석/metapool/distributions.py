"""표준정규 · t · 카이제곱 분포 함수 (표준 라이브러리만 사용).

메타분석에 필요한 최소한의 분포 함수를 직접 구현한다.
scipy 없이도 동작해야 하므로 정확도가 중요하며, 모든 함수는
알려진 참값(테스트 참조)과 1e-10 이내로 일치하도록 작성했다.
"""

from __future__ import annotations

import math

__all__ = [
    "normal_cdf",
    "normal_sf",
    "normal_ppf",
    "t_cdf",
    "t_sf",
    "t_ppf",
    "chi2_sf",
    "chi2_ppf",
]

_SQRT2 = math.sqrt(2.0)


def normal_cdf(z: float) -> float:
    """표준정규 누적분포 P(Z <= z)."""
    return 0.5 * math.erfc(-z / _SQRT2)


def normal_sf(z: float) -> float:
    """표준정규 상측확률 P(Z > z). 꼬리에서도 정확하도록 erfc를 직접 쓴다."""
    return 0.5 * math.erfc(z / _SQRT2)


def normal_ppf(p: float) -> float:
    """표준정규 분위수. erfc 기반 이분법 + 뉴턴 보정으로 기계정밀도까지 수렴."""
    if not (0.0 < p < 1.0):
        raise ValueError("normal_ppf: p는 0과 1 사이여야 합니다 (받은 값: %r)" % (p,))
    lo, hi = -40.0, 40.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if normal_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-15 * max(1.0, abs(mid)):
            break
    x = 0.5 * (lo + hi)
    # 뉴턴 보정 1회 (pdf > 0인 구간에서만)
    pdf = math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)
    if pdf > 1e-300:
        x -= (normal_cdf(x) - p) / pdf
    return x


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _betacf(a: float, b: float, x: float) -> float:
    """정규화 불완전베타의 연분수 (Lentz 알고리즘, Numerical Recipes 방식)."""
    tiny = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 301):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """정규화 불완전베타 I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(a * math.log(x) + b * math.log1p(-x) - _log_beta(a, b))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(
        b * math.log1p(-x) + a * math.log(x) - _log_beta(b, a)
    ) * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    """스튜던트 t 누적분포 P(T <= t)."""
    if df <= 0:
        raise ValueError("t_cdf: 자유도는 양수여야 합니다 (받은 값: %r)" % (df,))
    if math.isinf(t):
        return 1.0 if t > 0 else 0.0
    x = df / (df + t * t)
    tail = 0.5 * _betainc(0.5 * df, 0.5, x)
    return 1.0 - tail if t > 0 else tail


def t_sf(t: float, df: float) -> float:
    """스튜던트 t 상측확률 P(T > t)."""
    return t_cdf(-t, df)


def t_ppf(p: float, df: float) -> float:
    """스튜던트 t 분위수.

    고정 구간을 쓰면 자유도가 1이고 p가 극단적일 때(예: 99.999% 신뢰수준)
    분위수가 조용히 잘려 신뢰구간이 너무 좁아진다. 그래서 먼저 해를 포함하는
    구간을 찾을 때까지 넓힌 뒤 이분법으로 좁힌다.
    """
    if not (0.0 < p < 1.0):
        raise ValueError("t_ppf: p는 0과 1 사이여야 합니다 (받은 값: %r)" % (p,))
    if df <= 0:
        raise ValueError("t_ppf: 자유도는 양수여야 합니다 (받은 값: %r)" % (df,))
    if p == 0.5:
        return 0.0
    lo, hi = -1.0, 1.0
    for _ in range(200):  # 해를 포함할 때까지 구간 확장
        if t_cdf(lo, df) < p < t_cdf(hi, df):
            break
        lo *= 2.0
        hi *= 2.0
        if not (math.isfinite(lo) and math.isfinite(hi)):  # pragma: no cover
            break
    for _ in range(400):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-14 * max(1.0, abs(mid)):
            break
    return 0.5 * (lo + hi)


def _gamma_p_series(a: float, x: float) -> float:
    """하측 정규화 불완전감마 P(a, x) — 급수전개 (x < a+1)."""
    ap = a
    total = 1.0 / a
    delta = total
    for _ in range(1000):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * 1e-16:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gamma_q_cf(a: float, x: float) -> float:
    """상측 정규화 불완전감마 Q(a, x) — 연분수 (x >= a+1)."""
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 1001):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-16:
            break
    return h * math.exp(-x + a * math.log(x) - math.lgamma(a))


def chi2_sf(x: float, df: float) -> float:
    """카이제곱 상측확률 P(X > x). 이질성 Q 검정 등에 사용."""
    if df <= 0:
        raise ValueError("chi2_sf: 자유도는 양수여야 합니다 (받은 값: %r)" % (df,))
    if x <= 0:
        return 1.0
    a = 0.5 * df
    y = 0.5 * x
    if y < a + 1.0:
        return 1.0 - _gamma_p_series(a, y)
    return _gamma_q_cf(a, y)


def chi2_ppf(p: float, df: float) -> float:
    """카이제곱 분위수 (P(X <= x) = p 인 x). tau² 의 Q-profile 신뢰구간에 쓴다.

    상측확률 ``chi2_sf`` 가 단조감소라는 사실만 이용한 이분법이라 느리지만
    (수십 회 평가) 메타분석 규모에서는 문제가 되지 않으며, 급수/연분수의
    정확도를 그대로 물려받는다.
    """
    if not (0.0 < p < 1.0):
        raise ValueError("chi2_ppf: p는 0과 1 사이여야 합니다 (받은 값: %r)" % (p,))
    if df <= 0:
        raise ValueError("chi2_ppf: 자유도는 양수여야 합니다 (받은 값: %r)" % (df,))
    target = 1.0 - p  # 상측확률
    lo = 0.0
    hi = max(df, 1.0)
    for _ in range(200):  # 해를 포함할 때까지 상한 확장
        if chi2_sf(hi, df) <= target:
            break
        hi *= 2.0
        if not math.isfinite(hi):  # pragma: no cover - 도달 불가
            return math.inf
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if chi2_sf(mid, df) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12 * max(1.0, hi):
            break
    return 0.5 * (lo + hi)
