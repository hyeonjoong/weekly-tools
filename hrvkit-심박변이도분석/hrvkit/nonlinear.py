"""비선형 HRV 지표 — Poincaré 플롯 (SD1, SD2) 과 표본 엔트로피(SampEn).

Poincaré 플롯은 연속한 NN 쌍 (NN_n, NN_{n+1})을 산점도로 그린 것입니다.
항등선(y=x)에 수직/평행 방향의 산포를 각각 SD1, SD2로 정의합니다.

여기서는 대수 공식(SD1²=0.5·SDSD² 등)에 의존하지 않고, 정의 그대로
투영값의 표준편차로 직접 계산하여 손 검산이 쉽도록 했습니다.
  - 수직 투영 = (NN_{n+1} - NN_n)/√2  →  표준편차 = SD1
  - 평행 투영 = (NN_{n+1} + NN_n)/√2  →  표준편차 = SD2
"""

from __future__ import annotations

import math
import statistics
from typing import Dict, Optional, Sequence


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
