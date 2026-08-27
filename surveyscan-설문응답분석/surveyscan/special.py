"""표준 라이브러리만으로 구현한 특수함수 · 분포 분위수.

신뢰구간(CI)을 계산하려면 t 분포와 F 분포의 역누적분포(분위수)가 필요하다.
외부 의존성(scipy) 없이 동작해야 하므로 정규화 불완전 베타함수(regularized
incomplete beta)를 직접 구현하고, 그 위에 t·F 분포의 CDF/PPF를 올린다.

정확도: scipy.special.betainc / scipy.stats.t / scipy.stats.f 와 대조해
상대오차 ~1e-12 수준에서 일치함을 테스트로 확인한다(테스트에서만 scipy 사용).

규약
- 모든 함수는 결측이 없는 유한한 float를 받는다.
- 분위수(PPF)는 0<p<1 에 대해서만 정의. 경계는 ±inf 를 반환하지 않고 호출부에서
  걸러야 한다(여기서는 p 를 (eps, 1-eps)로 클램프해 유한값을 보장).
"""
from __future__ import annotations

import math

# 연속분수 반복의 상한/정밀도.
_MAXIT = 500
_EPS = 3.0e-16
_FPMIN = 1.0e-300


def _betacf(a: float, b: float, x: float) -> float:
    """정규화 불완전 베타함수의 연속분수(Lentz 알고리즘). Numerical Recipes 참고."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
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


def betainc(a: float, b: float, x: float) -> float:
    """정규화 불완전 베타함수 I_x(a, b). x∈[0,1], a>0, b>0. 반환 ∈[0,1]."""
    if a <= 0 or b <= 0:
        raise ValueError("betainc: a, b는 양수여야 합니다.")
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    # 로그감마로 접두인자 계산(오버플로 방지).
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    # 수렴이 빠른 쪽을 고른다(대칭식 사용).
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def betaincinv(a: float, b: float, p: float) -> float:
    """I_x(a, b) = p 를 만족하는 x (역 정규화 불완전 베타). p∈[0,1]."""
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if betainc(a, b, mid) < p:
            lo = mid
        else:
            hi = mid
        # 절대·상대 기준을 함께 사용해 x가 0에 매우 가까운 극단 꼬리에서도
        # 상대정밀도를 확보한다(일반적인 설문 CI 범위에는 영향 없음).
        if hi - lo <= 1e-15 + 4e-16 * abs(hi):
            break
    return 0.5 * (lo + hi)


def t_cdf(x: float, df: float) -> float:
    """스튜던트 t 분포의 CDF P(T ≤ x). df>0."""
    if df <= 0:
        raise ValueError("t_cdf: df는 양수여야 합니다.")
    if x == 0.0:
        return 0.5
    xb = df / (df + x * x)
    tail = 0.5 * betainc(df / 2.0, 0.5, xb)  # P(T ≤ -|x|)
    return tail if x < 0 else 1.0 - tail


def t_ppf(p: float, df: float) -> float:
    """스튜던트 t 분포의 분위수(역 CDF). 0<p<1, df>0. 대칭성으로 안정적으로 계산."""
    if df <= 0:
        raise ValueError("t_ppf: df는 양수여야 합니다.")
    p = min(max(p, 1e-12), 1.0 - 1e-12)
    if p == 0.5:
        return 0.0
    # |T| 의 꼬리확률 q 에서 x = df/(df+t^2) 를 역베타로 구한다.
    lower = p < 0.5
    q = 2.0 * (p if lower else 1.0 - p)  # 양측 꼬리확률
    xb = betaincinv(df / 2.0, 0.5, q)
    t = math.sqrt(df * (1.0 - xb) / xb) if xb > 0 else float("inf")
    return -t if lower else t


def f_cdf(x: float, dfn: float, dfd: float) -> float:
    """F 분포의 CDF P(F ≤ x). dfn,dfd>0, x≥0."""
    if dfn <= 0 or dfd <= 0:
        raise ValueError("f_cdf: 자유도는 양수여야 합니다.")
    if x <= 0.0:
        return 0.0
    xb = dfn * x / (dfn * x + dfd)
    return betainc(dfn / 2.0, dfd / 2.0, xb)


def f_ppf(p: float, dfn: float, dfd: float) -> float:
    """F 분포의 분위수(역 CDF). 0<p<1, dfn,dfd>0."""
    if dfn <= 0 or dfd <= 0:
        raise ValueError("f_ppf: 자유도는 양수여야 합니다.")
    p = min(max(p, 1e-12), 1.0 - 1e-12)
    # xb = betaincinv(dfn/2, dfd/2, p) = dfn*x/(dfn*x+dfd) 를 x에 대해 역산.
    xb = betaincinv(dfn / 2.0, dfd / 2.0, p)
    if xb >= 1.0:
        return float("inf")
    return (xb * dfd) / (dfn * (1.0 - xb))


def t_sf_two_sided(t: float, df: float) -> float:
    """양측 t 검정의 p = P(|T| ≥ |t|). df>0.

    `2*(1 - t_cdf(|t|, df))` 로 계산하면 꼬리확률이 ~1e-16 아래로 내려가는 순간
    1.0 과의 뺄셈에서 유효숫자가 통째로 사라져(catastrophic cancellation) p 가 **정확히
    0.0** 이 된다(|t|≳9). p=0 은 존재할 수 없는 값이고 JSON 산출물로도 나가므로,
    꼬리를 정규화 불완전 베타로 **직접** 계산한다:

        P(|T| ≥ |t|) = I_{df/(df+t²)}(df/2, 1/2)

    (t_cdf 도 같은 항등식을 쓰지만 거기서는 1에서 빼기 때문에 손실이 생긴다.)
    """
    if df <= 0:
        raise ValueError("t_sf_two_sided: df는 양수여야 합니다.")
    x = df / (df + t * t)
    return betainc(df / 2.0, 0.5, x)


def f_sf(f: float, dfn: float, dfd: float) -> float:
    """F 분포의 상측 꼬리확률 P(F ≥ f). dfn,dfd>0.

    `1 - f_cdf` 의 자리수 손실을 피하려고 상측 꼬리를 직접 계산한다:
        P(F ≥ f) = I_{dfd/(dfn·f+dfd)}(dfd/2, dfn/2)
    """
    if dfn <= 0 or dfd <= 0:
        raise ValueError("f_sf: 자유도는 양수여야 합니다.")
    if f <= 0.0:
        return 1.0
    x = dfd / (dfn * f + dfd)
    return betainc(dfd / 2.0, dfn / 2.0, x)


def norm_cdf(x: float) -> float:
    """표준정규 CDF Φ(x). math.erf 기반이라 정확도는 배정밀도 수준."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """표준정규 분위수 Φ⁻¹(p). 0<p<1.

    효과크기(Hedges' g)의 대표본 신뢰구간에 쓴다. 별도 유리함수 근사를 두지 않고
    Φ(x)=p 를 이분법으로 푼다 — Φ 자체가 erf 로 정확하므로 결과도 정확하고,
    호출 횟수가 적어(하위척도당 2회) 속도는 문제되지 않는다.
    """
    p = min(max(p, 1e-15), 1.0 - 1e-15)
    if p == 0.5:
        return 0.0
    lo, hi = -40.0, 40.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-14 * max(1.0, abs(hi)):
            break
    return 0.5 * (lo + hi)


def norm_sf(x: float) -> float:
    """표준정규 상측 꼬리확률 P(Z ≥ x).

    `1 - norm_cdf(x)` 로 계산하면 x≳8 에서 자리수 손실로 **정확히 0** 이 되어
    p=0 이라는 존재할 수 없는 값이 리포트·JSON에 실린다. erfc 를 직접 써서
    아주 작은 꼬리(~7e-323, 배정밀도 비정규수 한계)까지 유지한다. 그보다 더 작은
    꼬리(|x|≳38.5)는 double 로 표현할 수 없어 0.0 이 된다 — 이는 구현의 한계가 아니라
    부동소수의 한계다(scipy 는 |x|≳38 에서 이미 0.0 을 낸다).
    """
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def _gamma_p_series(a: float, x: float) -> float:
    """하측 정규화 불완전 감마 P(a,x) 의 급수전개(x < a+1 에서 빠르게 수렴)."""
    ap = a
    total = 1.0 / a
    delta = total
    # x < a+1 구간에서 급수는 대략 2a 항이 필요하다. 고정 상한(_MAXIT)만 쓰면 a 가 큰
    # 경우(카이제곱 df 가 수천 이상) 수렴하지 않은 채 조용히 빠져나와 값이 틀어진다.
    max_it = max(_MAXIT, int(4.0 * a) + 100)
    for _ in range(max_it):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * _EPS:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gamma_q_cf(a: float, x: float) -> float:
    """상측 정규화 불완전 감마 Q(a,x) 의 연속분수(x ≥ a+1 에서 빠르게 수렴, Lentz)."""
    b = x + 1.0 - a
    c = 1.0 / _FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, _MAXIT + 1):
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
    return h * math.exp(-x + a * math.log(x) - math.lgamma(a))


def gammainc_upper_reg(a: float, x: float) -> float:
    """상측 정규화 불완전 감마 Q(a,x) = Γ(a,x)/Γ(a). a>0, x≥0. 반환 ∈[0,1]."""
    if a <= 0:
        raise ValueError("gammainc_upper_reg: a는 양수여야 합니다.")
    if x < 0:
        raise ValueError("gammainc_upper_reg: x는 음수일 수 없습니다.")
    if x == 0.0:
        return 1.0
    if x < a + 1.0:
        return min(max(1.0 - _gamma_p_series(a, x), 0.0), 1.0)
    return min(max(_gamma_q_cf(a, x), 0.0), 1.0)


def chi2_sf(x: float, df: float) -> float:
    """카이제곱 분포의 상측 꼬리확률 P(χ²_df ≥ x) = Q(df/2, x/2). df>0.

    Kruskal-Wallis 검정의 p 값에 쓴다. `1-CDF` 가 아니라 상측 꼬리를 직접 계산해
    큰 H 값(강한 집단차)에서도 p 가 0.0 으로 뭉개지지 않게 한다.
    """
    if df <= 0:
        raise ValueError("chi2_sf: df는 양수여야 합니다.")
    if x <= 0.0:
        return 1.0
    return gammainc_upper_reg(df / 2.0, x / 2.0)
