"""비선형 HRV 지표 — Poincaré 플롯 (SD1, SD2), 표본 엔트로피(SampEn),
그리고 상세변동분석(DFA, Detrended Fluctuation Analysis) α1/α2.

Poincaré 플롯은 연속한 NN 쌍 (NN_n, NN_{n+1})을 산점도로 그린 것입니다.
항등선(y=x)에 수직/평행 방향의 산포를 각각 SD1, SD2로 정의합니다.

여기서는 대수 공식(SD1²=0.5·SDSD² 등)에 의존하지 않고, 정의 그대로
투영값의 표준편차로 직접 계산하여 손 검산이 쉽도록 했습니다.
  - 수직 투영 = (NN_{n+1} - NN_n)/√2  →  표준편차 = SD1
  - 평행 투영 = (NN_{n+1} + NN_n)/√2  →  표준편차 = SD2

DFA는 Peng et al. (1995) 를 따르되 **양방향(bidirectional) 구간 분할**을 씁니다:
평균 제거 후 누적적분한 프로파일을 크기 n의 겹치지 않는 구간으로 나눠 각 구간에서
최소제곱 선형추세를 제거하고, 잔차의 제곱평균제곱근 F(n)을 구합니다.
log F(n) ~ log n 의 기울기가 스케일링 지수 α입니다.
α1(단기, 기본 4–16박동), α2(장기, 기본 16–64박동).

Peng 원논문은 N이 n으로 나눠떨어지지 않을 때 남는 꼬리를 **버립니다**. 여기서는
앞→뒤와 뒤→앞 양방향으로 구간을 잡아 모든 표본을 쓰는 Kantelhardt(MF-DFA) 방식을
택했습니다 — 짧은 HRV 기록에서 데이터를 버리지 않기 위함입니다. N이 n의 배수면
두 방식은 동일하고, 아니면 α1이 ~1–2% 수준으로 달라질 수 있습니다(예: 300박동
합성 시계열에서 양방향 0.9497 대 전방향 0.9659). 다른 도구와 비교할 때 유의하세요.

백색잡음 α≈0.5, 적분(브라운) 잡음 α≈1.5로 손 검산할 수 있습니다.
"""

from __future__ import annotations

import math
import statistics
from typing import Dict, List, Optional, Sequence, Tuple


def poincare(nn: Sequence[float]) -> Dict[str, float]:
    """Poincaré 지표(SD1, SD2, 비율, 타원 면적)를 계산.

    SD1: 단기 변동성(부교감 활동과 상관, RMSSD와 비례).
    SD2: 장기 변동성.
    반환 키:
      sd1, sd2         : ms
      sd1_sd2_ratio    : SD1/SD2 (작을수록 장기 변동 대비 단기 변동이 작음)
      ellipse_area     : π·SD1·SD2 (ms²)
    """
    nn = [float(x) for x in nn]
    if len(nn) < 3:
        raise ValueError("Poincaré 지표는 최소 3개의 박동이 필요합니다.")

    sqrt2 = math.sqrt(2.0)
    perp = [(nn[i + 1] - nn[i]) / sqrt2 for i in range(len(nn) - 1)]
    para = [(nn[i + 1] + nn[i]) / sqrt2 for i in range(len(nn) - 1)]

    sd1 = statistics.stdev(perp)  # ddof=1
    sd2 = statistics.stdev(para)

    ratio = sd1 / sd2 if sd2 > 0 else float("inf")
    return {
        "sd1": sd1,
        "sd2": sd2,
        "sd1_sd2_ratio": ratio,
        "ellipse_area": math.pi * sd1 * sd2,
    }


def sample_entropy(series: Sequence[float], m: int = 2,
                   r: Optional[float] = None) -> float:
    """표본 엔트로피(SampEn) — Richman & Moorman (2000).

    규칙성/복잡성 지표. 값이 클수록 불규칙(예측하기 어려움)합니다.
    m: 임베딩 차원(기본 2). r: 허용 오차(기본 0.2·SDNN).

    정의: 길이 m·(m+1) 템플릿을 같은 인덱스 범위(0..N-m-1)에서 만들고,
    자기 자신을 제외한 쌍 중 Chebyshev 거리 ≤ r 인 쌍의 수 B, A를 세어
    SampEn = -ln(A / B). 매칭이 없으면 NaN.
    """
    u = [float(x) for x in series]
    n = len(u)
    if n < m + 2:
        return float("nan")
    if r is None:
        sd = statistics.stdev(u) if n > 1 else 0.0
        r = 0.2 * sd
    if r <= 0:
        # 분산이 0이면 모든 값이 동일 → 완전 규칙적, 엔트로피 0.
        return 0.0

    def _count(mm: int) -> int:
        # 두 길이(m, m+1) 모두 같은 개수의 템플릿(0..N-m-1)에서 비교해 편향 제거.
        last = n - m  # 템플릿 시작 인덱스 개수 = N-m (i = 0..N-m-1)
        tmpl = [u[i:i + mm] for i in range(last)]
        count = 0
        for i in range(last):
            ti = tmpl[i]
            for j in range(i + 1, last):
                tj = tmpl[j]
                dist = 0.0
                for a, b in zip(ti, tj):
                    d = a - b if a >= b else b - a
                    if d > dist:
                        dist = d
                    if dist > r:
                        break
                if dist <= r:
                    count += 1
        return count

    b = _count(m)
    a = _count(m + 1)
    if b == 0 or a == 0:
        return float("nan")
    return -math.log(a / b)


# --------------------------------------------------------------------------- #
# DFA — Detrended Fluctuation Analysis (Peng et al. 1995)
# --------------------------------------------------------------------------- #
def _detrend_rms_sq(seg: Sequence[float]) -> float:
    """구간 seg에 최소제곱 직선을 맞추고 잔차 제곱의 합을 반환.

    인덱스 t=0..n-1 에 대해 y=a+b·t 를 적합. Σ(y-ŷ)² 를 돌려줍니다.
    """
    n = len(seg)
    if n < 2:
        return 0.0
    mean_t = (n - 1) / 2.0
    mean_y = statistics.fmean(seg)
    sxx = 0.0
    sxy = 0.0
    for t in range(n):
        dt = t - mean_t
        sxx += dt * dt
        sxy += dt * (seg[t] - mean_y)
    slope = sxy / sxx if sxx > 0 else 0.0
    intercept = mean_y - slope * mean_t
    ss = 0.0
    for t in range(n):
        resid = seg[t] - (intercept + slope * t)
        ss += resid * resid
    return ss


def dfa_fluctuations(series: Sequence[float],
                     scales: Sequence[int]) -> List[Tuple[int, float]]:
    """각 구간 크기 n에 대한 DFA 요동함수 F(n)을 계산.

    반환: [(n, F(n)), ...] — 유효한(구간이 1개 이상 만들어지는) n만 포함.
    F(n) = sqrt( (겹치지 않는 모든 구간의 잔차 제곱합) / (사용된 점 수) ).
    """
    x = [float(v) for v in series]
    N = len(x)
    if N < 2:
        return []
    mean_x = statistics.fmean(x)
    # 누적적분 프로파일 y(k) = Σ_{i<=k} (x_i - mean)
    profile: List[float] = []
    acc = 0.0
    for v in x:
        acc += v - mean_x
        profile.append(acc)

    out: List[Tuple[int, float]] = []
    seen = set()
    for n in scales:
        if n < 2 or n > N or n in seen:
            continue
        seen.add(n)
        n_boxes = N // n
        if n_boxes < 1:
            continue
        ss_total = 0.0
        used = 0
        # 앞에서부터 겹치지 않는 구간. N이 n의 배수가 아니면 남는 꼬리를
        # 버리지 않도록 뒤에서부터도 한 번 더 나눠(양방향) 모든 점을 사용.
        for b in range(n_boxes):
            seg = profile[b * n:(b + 1) * n]
            ss_total += _detrend_rms_sq(seg)
            used += n
        if N % n != 0:
            for b in range(n_boxes):
                seg = profile[N - (b + 1) * n:N - b * n]
                ss_total += _detrend_rms_sq(seg)
                used += n
        if used == 0:
            continue
        f_n = math.sqrt(ss_total / used)
        out.append((n, f_n))
    return out


def _loglog_slope(points: Sequence[Tuple[int, float]]) -> float:
    """(n, F) 점들의 log-log 최소제곱 기울기 = 스케일링 지수 α.

    F(n)==0 인 점(완전 평탄 구간 등)은 제외. 유효점 2개 미만이면 NaN.
    """
    xs, ys = [], []
    for n, f in points:
        if f > 0 and n > 0:
            xs.append(math.log(n))
            ys.append(math.log(f))
    k = len(xs)
    if k < 2:
        return float("nan")
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return float("nan")
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(k))
    return sxy / sxx


def dfa_alpha(series: Sequence[float], scale_min: int = 4,
              scale_max: int = 16) -> float:
    """구간 크기 [scale_min, scale_max]에 대한 DFA 스케일링 지수 α.

    유효한 스케일이 2개 미만이면 NaN. α1은 기본 4–16, α2는 16–64를 씁니다.
    """
    if scale_min < 2:
        scale_min = 2
    if scale_max < scale_min:
        return float("nan")
    scales = list(range(scale_min, scale_max + 1))
    fl = dfa_fluctuations(series, scales)
    if len(fl) < 2:
        return float("nan")
    return _loglog_slope(fl)


def dfa(series: Sequence[float], *, short_range: Tuple[int, int] = (4, 16),
        long_range: Tuple[int, int] = (16, 64)) -> Dict[str, float]:
    """DFA α1(단기)·α2(장기)를 한 번에 계산.

    반환 키:
      dfa_alpha1 : 단기 스케일링 지수(기본 4–16 박동). 건강한 성인 안정 시 ≈1.0.
      dfa_alpha2 : 장기 스케일링 지수(기본 16–64 박동). 데이터가 짧으면 NaN.
    데이터가 α2 계산에 충분치 않으면(가장 큰 구간이 2개 미만) α2=NaN.
    """
    n = len(series)
    a1 = dfa_alpha(series, short_range[0], short_range[1])
    # α2는 가장 큰 구간이 최소 2개 만들어질 만큼 데이터가 있어야 신뢰할 수 있음.
    a2 = float("nan")
    if n >= 2 * long_range[1]:
        a2 = dfa_alpha(series, long_range[0], long_range[1])
    return {"dfa_alpha1": a1, "dfa_alpha2": a2}
