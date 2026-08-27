"""단일 성분 24h 코사이너(cosinor) — 최소제곱 선형화 적합.

모형 (Cornelissen 2014, "Cosinor-based rhythmometry",
Theor Biol Med Model 11:16, 식 (1)–(3)):

    y(t) = M + A·cos(2πt/τ − φ) + e(t)
         = M + β·cos(ωt) + γ·sin(ωt) + e(t),   ω = 2π/τ

    β = A·cosφ,  γ = A·sinφ  →  A = √(β²+γ²),  φ = atan2(γ, β)

t 를 '하루 중 시각(시간, 0–24)'으로 두면 τ=24 에서 정점위상 φ/ω 가 곧
정점 '시계 시각'이 된다(예: φ/ω = 15.4 → 15:24). atan2 를 쓰므로 위상
사분면 부호 함정(acrophase 고전 함정)이 없다 — 검증은 기지 위상 3개
(03:00·15:00·21:30)를 심은 합성파 테스트로 한다.

zero-amplitude 검정 (Cornelissen 2014, 식 (8) 취지):
    H0: A = 0.  F = ((RSS0 − RSS)/2) / (RSS/(n−3)) ~ F(2, n−3)
분자 자유도가 2로 고정이므로 F 분포 생존함수는 폐형식이 있다:
    P(F(2, d2) > f) = (d2 / (d2 + 2f))^(d2/2)
(F(2,d2)는 Beta(1, d2/2) 변환 — 불완전베타가 멱함수로 닫힌다.)
scipy 대조값은 tests/test_cosinor.py 에 하드코딩되어 있다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence


@dataclass
class CosinorFit:
    n: int
    mesor: float
    amplitude: float
    acrophase_hours: float        # 정점 시각(시간, 0–24)
    r2: Optional[float]           # 결정계수 (분산 0이면 None)
    f_stat: Optional[float]
    p_value: Optional[float]      # zero-amplitude 검정
    period: float = 24.0

    @property
    def acrophase_clock(self) -> str:
        return hours_to_clock(self.acrophase_hours)

    @property
    def bathyphase_hours(self) -> float:
        return (self.acrophase_hours + self.period / 2.0) % self.period


def hours_to_clock(h: float) -> str:
    """15.4 → '15:24'. 반올림이 24:00 이 되면 00:00 으로.

    NaN/inf 방어(라운드 1 M4): 극단 입력이 적합을 오염시켜 위상이 NaN 이
    되어도 크래시 대신 '—' 를 낸다(값 검증은 parse 층에서 이미 막는다).
    """
    if not math.isfinite(h):
        return "—"
    total_min = int(round((h % 24.0) * 60.0)) % (24 * 60)
    return f"{total_min // 60:02d}:{total_min % 60:02d}"


def _solve3(a, b):
    """3×3 연립 일차방정식 — 부분 피벗 가우스 소거."""
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(3):
            if r != col:
                f = m[r][col] / m[col][col]
                for c in range(col, 4):
                    m[r][c] -= f * m[col][c]
    return [m[i][3] / m[i][i] for i in range(3)]


def f2_sf(f: float, d2: int) -> float:
    """P(F(2, d2) > f) — 폐형식. f<0 방어."""
    if f <= 0:
        return 1.0
    return (d2 / (d2 + 2.0 * f)) ** (d2 / 2.0)


def fit_cosinor(times_hours: Sequence[float], values: Sequence[float],
                period: float = 24.0) -> Optional[CosinorFit]:
    """t(시간 단위)와 y로 코사이너 적합. 표본 < 8개면 None."""
    n = len(values)
    if n < 8 or n != len(times_hours):
        return None
    w = 2.0 * math.pi / period
    # 정규방정식 X'X b = X'y,  X = [1, cos(wt), sin(wt)]
    sc = ss = scc = sss = scs = sy = syc = sys_ = 0.0
    for t, y in zip(times_hours, values):
        c = math.cos(w * t)
        s = math.sin(w * t)
        sc += c
        ss += s
        scc += c * c
        sss += s * s
        scs += c * s
        sy += y
        syc += y * c
        sys_ += y * s
    xtx = [[float(n), sc, ss],
           [sc, scc, scs],
           [ss, scs, sss]]
    sol = _solve3(xtx, [sy, syc, sys_])
    if sol is None:
        return None   # 시각이 전부 같은 위상 등 특이 설계
    mesor, beta, gamma = sol
    amplitude = math.hypot(beta, gamma)
    acro = (math.atan2(gamma, beta) / w) % period

    mean_y = sy / n
    tss = sum((y - mean_y) ** 2 for y in values)     # = RSS0 (H0: y=M)
    rss = 0.0
    for t, y in zip(times_hours, values):
        pred = mesor + beta * math.cos(w * t) + gamma * math.sin(w * t)
        rss += (y - pred) ** 2
    if tss <= 1e-12:
        # 상수 시계열 — 리듬 자체가 정의되지 않음
        return CosinorFit(n, mesor, 0.0, 0.0, None, None, None, period)
    r2 = 1.0 - rss / tss
    d2 = n - 3
    if d2 <= 0 or rss <= 1e-12:
        # 완전 적합(무잡음 합성파): F 무한대 취급, p → 0
        return CosinorFit(n, mesor, amplitude, acro, r2, None, 0.0, period)
    f_stat = ((tss - rss) / 2.0) / (rss / d2)
    return CosinorFit(n, mesor, amplitude, acro, r2, f_stat,
                      f2_sf(f_stat, d2), period)
