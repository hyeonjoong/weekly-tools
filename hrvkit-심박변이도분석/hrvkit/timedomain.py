"""시간영역 HRV 지표.

모든 정의는 Task Force (1996) / Shaffer & Ginsberg (2017) 표준을 따릅니다.
표본표준편차(ddof=1)를 사용하며 손 계산으로 검증 가능합니다.
"""

from __future__ import annotations

import math
import statistics
from typing import Dict, Sequence


def time_domain(nn: Sequence[float]) -> Dict[str, float]:
    """정제된 NN(정상-정상) 간격(ms)으로부터 시간영역 지표를 계산.

    반환 키:
      n_beats   : 박동 수
      mean_nn   : 평균 NN (ms)
      sdnn      : NN 표준편차 (ms) — 전체 변동성
      rmssd     : 연속차 제곱평균제곱근 (ms) — 단기(부교감) 변동성
      sdsd      : 연속차 표준편차 (ms)
      nn50/pnn50: 연속차 > 50 ms 개수 / 비율(%)
      nn20/pnn20: 연속차 > 20 ms 개수 / 비율(%)
      mean_hr   : 평균 순간 심박수 (bpm)
      std_hr    : 순간 심박수 표준편차 (bpm)
      min_hr/max_hr : 최소/최대 순간 심박수 (bpm)
      cvnn      : 변이계수 = sdnn/mean_nn (무차원)
    """
    nn = [float(x) for x in nn]
    n = len(nn)
    if n < 2:
        raise ValueError("시간영역 지표는 최소 2개의 박동이 필요합니다.")

    diffs = [nn[i + 1] - nn[i] for i in range(n - 1)]

    mean_nn = statistics.fmean(nn)
    sdnn = statistics.stdev(nn)  # ddof=1
    rmssd = math.sqrt(statistics.fmean([d * d for d in diffs]))
    sdsd = statistics.stdev(diffs) if len(diffs) >= 2 else 0.0

    nn50 = sum(1 for d in diffs if abs(d) > 50.0)
    nn20 = sum(1 for d in diffs if abs(d) > 20.0)
    pnn50 = 100.0 * nn50 / len(diffs)
    pnn20 = 100.0 * nn20 / len(diffs)

    inst_hr = [60000.0 / x for x in nn]
    mean_hr = statistics.fmean(inst_hr)
    std_hr = statistics.stdev(inst_hr)

    return {
        "n_beats": n,
        "mean_nn": mean_nn,
        "sdnn": sdnn,
        "rmssd": rmssd,
        "sdsd": sdsd,
        "nn50": nn50,
        "pnn50": pnn50,
        "nn20": nn20,
        "pnn20": pnn20,
        "mean_hr": mean_hr,
        "std_hr": std_hr,
        "min_hr": min(inst_hr),
        "max_hr": max(inst_hr),
        "cvnn": sdnn / mean_nn,
    }
