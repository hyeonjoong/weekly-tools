"""정확 이항(Clopper–Pearson) 구간과 개인 내 임계차.

근거 문헌 (2026-09-11 PubMed 원문 대조):
  Thornton AR, Raffin MJ. Speech-discrimination scores modeled as a binomial
  variable. J Speech Hear Res. 1978;21(3):507-518. doi:10.1044/jshr.2103.507
  — 말지각 점수를 이항변수로 모형화하고, 두 점수가 다르다고 볼 수 있는
  '임계차(critical difference)' 표를 만든 원 논문.
  Raffin MJ, Schafer D. J Speech Hear Res. 1980;23(3):570-575.
  doi:10.1044/jshr.2303.570 — NU-6 100단어 자료로 위 모형을 재확인.

이 모듈은 **그 논문의 표 값을 복제하지 않는다.** 같은 이항 가정 위에서
Clopper–Pearson 정확 구간을 직접 계산하며, 두 방식의 값이 소수점까지
같다고 주장하지 않는다(리포트에 그대로 자백한다).
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

_EPS = 3.0e-16
_FPMIN = 1e-300


def log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _betacf(a: float, b: float, x: float) -> float:
    """연분수 전개 (Lentz 변형). 0 < x < (a+1)/(a+b+2) 영역에서 수렴이 빠르다."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, 300):
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


def betainc_reg(x: float, a: float, b: float) -> float:
    """정규화 불완전 베타 함수 I_x(a, b)."""
    if not (0.0 <= x <= 1.0):
        raise ValueError("x 는 0..1 이어야 합니다: %r" % (x,))
    if a <= 0 or b <= 0:
        raise ValueError("a, b 는 양수여야 합니다")
    if x == 0.0:
        return 0.0
    if x == 1.0:
        return 1.0
    front = math.exp(a * math.log(x) + b * math.log1p(-x) - log_beta(a, b))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(
        b * math.log1p(-x) + a * math.log(x) - log_beta(b, a)
    ) * _betacf(b, a, 1.0 - x) / b


def betainv_reg(p: float, a: float, b: float, tol: float = 1e-15) -> float:
    """I_x(a, b) = p 를 만족하는 x (이분법). 단조함수라 이분법이 안전하다."""
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(400):
        mid = 0.5 * (lo + hi)
        if betainc_reg(mid, a, b) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> Tuple[float, float]:
    """성공 k / 시행 n 의 정확 이항 (1-alpha) 양측 신뢰구간.

    k=0 이면 하한 0, k=n 이면 상한 1 (닫힌 형태와 일치한다).
    """
    if n <= 0:
        raise ValueError("n 은 1 이상이어야 합니다")
    if not (0 <= k <= n):
        raise ValueError("k 는 0..n 이어야 합니다")
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha 는 0과 1 사이여야 합니다")
    lo = 0.0 if k == 0 else betainv_reg(alpha / 2.0, k, n - k + 1)
    hi = 1.0 if k == n else betainv_reg(1.0 - alpha / 2.0, k + 1, n - k)
    return lo, hi


def score_interval(k: int, n: int, alpha: float = 0.05) -> Tuple[int, int]:
    """사전 점수 k/n 과 '검사 오차 범위 안'이라고 볼 수 있는 정답수 구간 [lo, hi].

    Clopper–Pearson 비율 구간을 정답수 눈금으로 되돌린다. 경계는 **안쪽으로**
    잡지 않고 바깥쪽(내림·올림)으로 잡는다 — 경계에 걸친 값을 '벗어남'이라고
    말하지 않기 위해서다(보수적).
    """
    lo_p, hi_p = clopper_pearson(k, n, alpha)
    lo = max(0, math.floor(lo_p * n + 1e-12))
    hi = min(n, math.ceil(hi_p * n - 1e-12))
    return lo, hi


def critical_difference_pp(k: int, n: int, alpha: float = 0.05):
    """'오차 범위 밖'이 되려면 필요한 최소 변화폭을 **방향별로** 돌려준다.

    반환값은 ``(하락쪽 %p, 상승쪽 %p)`` 이고, 그 방향으로는 구간을 벗어날 수 없을
    때(바닥·천장에 붙어 있을 때) 그 쪽은 ``None`` 이다.

    한 숫자로 뭉뚱그리면 거짓말이 된다: 18/18 인 피험자에게 "임계차 5.6%p" 라고
    적어 두면 1문항만 떨어져도 오차 범위를 벗어난다는 뜻이 되는데, 실제로는
    5문항(27.8%p) 이 떨어져야 한다. 반대 방향은 아예 불가능하다.
    """
    lo, hi = score_interval(k, n, alpha)
    down = (k - lo + 1) * 100.0 / n if lo > 0 else None
    up = (hi - k + 1) * 100.0 / n if hi < n else None
    return down, up


def format_critical_difference(k: int, n: int, alpha: float = 0.05) -> str:
    """리포트·CSV 에 넣을 사람이 읽는 형태. 불가능한 방향은 '—' 로 적는다."""
    down, up = critical_difference_pp(k, n, alpha)
    parts = []
    parts.append("↓%.1f" % down if down is not None else "↓—")
    parts.append("↑%.1f" % up if up is not None else "↑—")
    return " ".join(parts)


def verdict(pre_k: Optional[int], post_k: Optional[int], n: int,
            alpha: float = 0.05) -> str:
    """사전→사후 변화 판정. 반환값은 세 가지뿐이다.

    ``"초과"``   개인 내 변화가 검사 재검사 오차 범위를 벗어남
    ``"미달"``   오차 범위 안
    ``"판정불가"`` 사전 또는 사후가 없음 / 문항수가 다름
    """
    if pre_k is None or post_k is None or n is None or n <= 0:
        return "판정불가"
    if not (0 <= pre_k <= n and 0 <= post_k <= n):
        return "판정불가"
    lo, hi = score_interval(pre_k, n, alpha)
    return "초과" if (post_k < lo or post_k > hi) else "미달"
