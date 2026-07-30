"""수치 특수함수 — 표준 라이브러리(math)만 사용.

검정력/표본수 계산에 필요한 최소한의 수치 도구만 담는다.

- :func:`norm_cdf` / :func:`norm_ppf` : 표준정규 분포함수와 분위수
- :func:`betainc`                    : 정규화 불완전 베타함수 I_x(a, b)
- :func:`gauss_legendre`             : Gauss–Legendre 구적 노드/가중치
- :func:`bisect_increasing`          : 단조증가 함수의 역함수(이분법)

scipy를 쓰지 않는 이유: 이 툴은 임상 프로토콜을 쓰는 자리에서 바로 돌아야 하고,
설치 실패가 곧 사용 불가로 이어지기 때문이다. 정확도는 tests/에서 mpmath 40자리로
미리 계산해 하드코딩한 기준값과 대조한다 (완전 오프라인, scipy 불필요).
"""

from __future__ import annotations

import math

__all__ = [
    "norm_cdf",
    "norm_pdf",
    "norm_ppf",
    "betainc",
    "log_beta",
    "gauss_legendre",
    "bisect_increasing",
]

_SQRT2 = math.sqrt(2.0)
_SQRT2PI = math.sqrt(2.0 * math.pi)


def norm_pdf(z: float) -> float:
    """표준정규 확률밀도."""
    return math.exp(-0.5 * z * z) / _SQRT2PI


def norm_cdf(z: float) -> float:
    """표준정규 분포함수 Φ(z). erfc 기반이라 양쪽 꼬리에서 상대오차가 유지된다."""
    if z != z:  # NaN
        return float("nan")
    return 0.5 * math.erfc(-z / _SQRT2)


# Acklam(2000) 유리함수 근사 계수 — 이후 Halley 보정으로 배정도까지 끌어올린다.
_ACK_A = (
    -3.969683028665376e01,
    2.209460984245205e02,
    -2.759285104469687e02,
    1.383577518672690e02,
    -3.066479806614716e01,
    2.506628277459239e00,
)
_ACK_B = (
    -5.447609879822406e01,
    1.615858368580409e02,
    -1.556989798598866e02,
    6.680131188771972e01,
    -1.328068155288572e01,
)
_ACK_C = (
    -7.784894002430293e-03,
    -3.223964580411365e-01,
    -2.400758277161838e00,
    -2.549732539343734e00,
    4.374664141464968e00,
    2.938163982698783e00,
)
_ACK_D = (
    7.784695709041462e-03,
    3.224671290700398e-01,
    2.445134137142996e00,
    3.754408661907416e00,
)
_ACK_PLOW = 0.02425


def norm_ppf(p: float) -> float:
    """표준정규 분위수 Φ⁻¹(p) — Acklam 근사 + Halley 보정 2회.

    상용 구간(p ∈ [1e-6, 1−1e-6])에서 절대오차 < 1e-14. 극단 꼬리에서는
    p 자체의 배정도 표현 한계 때문에 상대오차가 1e-9까지 커질 수 있다
    (Φ⁻¹의 조건수가 1/φ(x)로 발산하므로 어떤 구현도 마찬가지다).
    """
    if p != p:
        return float("nan")
    if p <= 0.0:
        return float("-inf")
    if p >= 1.0:
        return float("inf")

    if p < _ACK_PLOW:
        q = math.sqrt(-2.0 * math.log(p))
        x = (
            ((((_ACK_C[0] * q + _ACK_C[1]) * q + _ACK_C[2]) * q + _ACK_C[3]) * q + _ACK_C[4]) * q
            + _ACK_C[5]
        ) / ((((_ACK_D[0] * q + _ACK_D[1]) * q + _ACK_D[2]) * q + _ACK_D[3]) * q + 1.0)
    elif p <= 1.0 - _ACK_PLOW:
        q = p - 0.5
        r = q * q
        x = (
            (((((_ACK_A[0] * r + _ACK_A[1]) * r + _ACK_A[2]) * r + _ACK_A[3]) * r + _ACK_A[4]) * r
             + _ACK_A[5])
            * q
        ) / (((((_ACK_B[0] * r + _ACK_B[1]) * r + _ACK_B[2]) * r + _ACK_B[3]) * r + _ACK_B[4]) * r
             + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log1p(-p))
        x = -(
            ((((_ACK_C[0] * q + _ACK_C[1]) * q + _ACK_C[2]) * q + _ACK_C[3]) * q + _ACK_C[4]) * q
            + _ACK_C[5]
        ) / ((((_ACK_D[0] * q + _ACK_D[1]) * q + _ACK_D[2]) * q + _ACK_D[3]) * q + 1.0)

    # Halley 보정 (꼬리에서 exp(x²/2)가 발산하므로 안전하게 감싼다)
    for _ in range(2):
        try:
            err = norm_cdf(x) - p
            u = err * _SQRT2PI * math.exp(0.5 * x * x)
        except OverflowError:
            break
        if not math.isfinite(u):
            break
        x -= u / (1.0 + 0.5 * x * u)
    return x


def _betacf(a: float, b: float, x: float) -> float:
    """불완전 베타함수의 연분수 (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
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
        if abs(delta - 1.0) < 3e-16:
            break
    return h


def _log_gamma_ratio(a: float, b: float) -> float:
    """log(Γ(a) / Γ(a+b)). a가 크면 Stirling 전개로 큰 수 상쇄를 제거한다.

    lgamma(a) − lgamma(a+b)를 그대로 계산하면 두 항이 각각 O(a·log a)여서
    a ~ 5e5(=df/2, df가 100만인 t 검정)에서 절대오차가 1e-9까지 커진다.
    아래 형태는 모든 항이 O(b·log a)라 배정도 정밀도가 유지된다.
    """
    if a < 1e3:
        return math.lgamma(a) - math.lgamma(a + b)

    def stirling_tail(z: float) -> float:
        z2 = z * z
        return (1.0 / 12.0 - (1.0 / 360.0 - (1.0 / 1260.0 - 1.0 / (1680.0 * z2)) / z2) / z2) / z

    return (
        -b * math.log(a)
        - (a + b - 0.5) * math.log1p(b / a)
        + b
        + stirling_tail(a)
        - stirling_tail(a + b)
    )


def log_beta(a: float, b: float) -> float:
    """log B(a, b) — 큰 인수에서도 안정적."""
    if a >= b:
        return math.lgamma(b) + _log_gamma_ratio(a, b)
    return math.lgamma(a) + _log_gamma_ratio(b, a)


def betainc(a: float, b: float, x: float, x1m: float | None = None) -> float:
    """정규화 불완전 베타함수 I_x(a, b) = B(x; a, b) / B(a, b).

    `x1m`으로 1−x를 직접 넘길 수 있다. x가 1에 극히 가깝고 a가 크면
    (예: df=10⁶인 t 분포에서 a=5e5, 1−x≈9e-8) `1.0 - x`는 이미 1e-16의
    절대오차를 안고 있어 a·log(x)에서 그 오차가 a배로 증폭된다. 호출부가
    1−x를 정확히 알고 있을 때 넘겨주면 상대정확도가 유지된다.

    a + b가 매우 큰 경우(> ~1e3) 상대오차는 약 1e-13 수준이다.
    """
    if not (a > 0.0 and b > 0.0):
        raise ValueError(f"betainc: a, b는 양수여야 합니다 (a={a}, b={b})")
    if x != x or (x1m is not None and x1m != x1m):
        return float("nan")
    if x1m is None:
        x1m = 1.0 - x
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    if x1m >= 1.0:
        # x가 0에 극히 가까워 호출부가 넘긴 1−x가 정확히 1.0으로 반올림된 경우.
        # x 자체는 아직 정확하므로 여기서 되살린다 (t_ppf가 |t| > 1e8에서
        # 조용히 포화되던 문제).
        x1m = 1.0 - x
        if x1m >= 1.0:
            x1m = math.nextafter(1.0, 0.0)
    elif x1m <= 0.0:
        return 1.0
    log_x = math.log1p(-x1m) if x1m < 0.5 else math.log(x)
    log_x1m = math.log1p(-x) if x < 0.5 else math.log(x1m)
    log_front = a * log_x + b * log_x1m - log_beta(a, b)
    if log_front < -740.0:  # exp 언더플로 영역 → 꼬리 확률은 0/1로 수렴
        return 0.0 if x < (a + 1.0) / (a + b + 2.0) else 1.0
    front = math.exp(log_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return min(1.0, front * _betacf(a, b, x) / a)
    return max(0.0, 1.0 - front * _betacf(b, a, x1m) / b)


def gauss_legendre(n: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """[-1, 1] 구간의 n점 Gauss–Legendre 노드와 가중치 (Newton 반복으로 직접 계산)."""
    if n < 2:
        raise ValueError("gauss_legendre: n >= 2")
    nodes = [0.0] * n
    weights = [0.0] * n
    for i in range((n + 1) // 2):
        x = math.cos(math.pi * (i + 0.75) / (n + 0.5))
        dp = 1.0
        for _ in range(100):
            p_prev, p_cur = 1.0, x
            for k in range(2, n + 1):
                p_prev, p_cur = p_cur, ((2 * k - 1) * x * p_cur - (k - 1) * p_prev) / k
            dp = n * (x * p_cur - p_prev) / (x * x - 1.0)
            dx = -p_cur / dp
            x += dx
            if abs(dx) < 1e-16:
                break
        w = 2.0 / ((1.0 - x * x) * dp * dp)
        nodes[i], nodes[n - 1 - i] = -x, x
        weights[i], weights[n - 1 - i] = w, w
    if n % 2 == 1:
        nodes[n // 2] = 0.0
    return tuple(nodes), tuple(weights)


def bisect_increasing(f, target: float, lo: float, hi: float, tol: float = 1e-13,
                      max_iter: int = 200) -> float:
    """[lo, hi]에서 단조증가 f에 대해 f(x) = target을 만족하는 x (이분법).

    구간 밖이면 경계값을 돌려준다 (호출부에서 구간을 넓혀 쓴다).
    """
    flo, fhi = f(lo), f(hi)
    if flo >= target:
        return lo
    if fhi <= target:
        return hi
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        if mid == lo or mid == hi:
            break
        fm = f(mid)
        if fm < target:
            lo, flo = mid, fm
        else:
            hi, fhi = mid, fm
        if hi - lo <= tol * max(1.0, abs(hi)):
            break
    return 0.5 * (lo + hi)
