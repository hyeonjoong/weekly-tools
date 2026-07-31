"""시간영역 HRV 지표.

모든 정의는 Task Force (1996) / Shaffer & Ginsberg (2017) 표준을 따릅니다.
표본표준편차(ddof=1)를 사용하며 손 계산으로 검증 가능합니다.
"""

from __future__ import annotations

import math
import statistics
from typing import Dict, List, Sequence

# 기하학적 지표용 히스토그램 빈 폭 — Task Force(1996) 표준: 1/128 초 ≈ 7.8125 ms.
_HIST_BIN_MS = 1000.0 / 128.0

# 히스토그램 빈 개수 상한. 빈 폭이 7.8125 ms 이므로 2^20 빈 ≈ 8190 초(2.3시간)의
# NN 폭까지 덮습니다 — 생리적으로 도달할 수 없는 범위입니다. 상한이 없으면
# `--clean none` 으로 통과한 비생리적 값(예: 9e99) 하나가 `range(max_idx+1)` 을
# 무한정 키워 프로세스가 OOM 으로 되돌아올 수 없이 멈춥니다.
_MAX_HIST_BINS = 1 << 20


def _histogram(nn: Sequence[float], bin_width: float
               ) -> "tuple[List[float], List[int]]":
    """NN 히스토그램 (빈 중심(ms) 리스트, 카운트 리스트)를 반환."""
    lo = min(nn)
    # 빈 인덱스 = floor((v - lo)/bin_width). 중심 = lo + (idx+0.5)*bin_width.
    counts: Dict[int, int] = {}
    for v in nn:
        idx = int(math.floor((v - lo) / bin_width))
        counts[idx] = counts.get(idx, 0) + 1
    max_idx = max(counts)
    if max_idx + 1 > _MAX_HIST_BINS:
        raise ValueError(
            f"NN 값의 폭이 너무 커 히스토그램 빈이 {max_idx + 1:.3g}개 필요합니다 "
            f"(상한 {_MAX_HIST_BINS}). 비생리적 값이 섞여 있습니다 — "
            "--max-rr 로 범위를 제한하거나 --clean interpolate 를 쓰세요.")
    centers = [lo + (i + 0.5) * bin_width for i in range(max_idx + 1)]
    hist = [counts.get(i, 0) for i in range(max_idx + 1)]
    return centers, hist


def geometric_indices(nn: Sequence[float],
                      bin_width: float = _HIST_BIN_MS) -> Dict[str, float]:
    """기하학적 HRV 지표 — HRV 삼각지수(HTI)와 TINN.

    HTI  = 전체 NN 개수 / 히스토그램 최빈 빈의 높이 (무차원). 클수록 변동성 큼.
    TINN = NN 히스토그램을 삼각형으로 보간했을 때 밑변 폭(ms).

    둘 다 이상값(artifact)에 강건해 임상에서 널리 쓰입니다(Task Force 1996).
    삼각형 정점은 최빈 빈에 고정하고, 좌/우 밑변 지점 N, M을 각각 잔차
    제곱합이 최소가 되도록 독립적으로 탐색합니다(정점 좌우로 오차가 분리됨).
    """
    nn = [float(x) for x in nn]
    n = len(nn)
    if n < 2:
        return {"hti": float("nan"), "tinn": float("nan")}

    try:
        centers, hist = _histogram(nn, bin_width)
    except ValueError:
        # 기하학적 지표만 포기하고 나머지 시간영역 지표는 그대로 냅니다.
        return {"hti": float("nan"), "tinn": float("nan")}
    peak = max(hist)
    if peak <= 0:
        return {"hti": float("nan"), "tinn": float("nan")}

    hti = n / peak

    # 삼각형 밑변(N, M)은 데이터 범위 **밖**에 놓일 수 있어야 합니다. centers 는
    # min(nn)~max(nn) 만 덮으므로, 패딩 없이 탐색하면 밑변이 관측 범위에 갇혀 TINN이
    # 체계적으로 과소평가됩니다 — 꼬리 없는 완전한 삼각형 히스토그램(참값 156.25 ms)
    # 에서 140.63 ms(정확히 2빈 부족)가 나왔습니다. 양쪽에 0 카운트 빈을 덧대
    # 최적점이 내부에 오도록 합니다(실측 NN은 꼬리가 있어 대개 영향 없음).
    pad = min(len(hist), 256)
    centers = ([centers[0] - (pad - i) * bin_width for i in range(pad)] +
               centers +
               [centers[-1] + (i + 1) * bin_width for i in range(pad)])
    hist = [0] * pad + hist + [0] * pad

    # 여러 최빈 빈이 있으면 첫 번째를 정점으로.
    x_idx = hist.index(peak)
    x = centers[x_idx]
    y = float(peak)

    def _left_error(n_idx: int) -> float:
        """정점 왼쪽 밑변을 centers[n_idx]에 둘 때 좌측 잔차 제곱합."""
        xn = centers[n_idx]
        err = 0.0
        for t in range(0, x_idx):
            ct = centers[t]
            q = 0.0
            if ct > xn:
                q = y * (ct - xn) / (x - xn) if x > xn else 0.0
            err += (hist[t] - q) ** 2
        return err

    def _right_error(m_idx: int) -> float:
        xm = centers[m_idx]
        err = 0.0
        for t in range(x_idx + 1, len(centers)):
            ct = centers[t]
            q = 0.0
            if ct < xm:
                q = y * (xm - ct) / (xm - x) if xm > x else 0.0
            err += (hist[t] - q) ** 2
        return err

    # N은 정점 왼쪽(정점 포함 왼쪽 끝), M은 정점 오른쪽에서 탐색.
    best_n = x_idx
    best_ne = _left_error(x_idx)
    for cand in range(0, x_idx):
        e = _left_error(cand)
        if e < best_ne:
            best_ne, best_n = e, cand

    best_m = x_idx
    best_me = _right_error(x_idx)
    for cand in range(x_idx + 1, len(centers)):
        e = _right_error(cand)
        if e < best_me:
            best_me, best_m = e, cand

    tinn = centers[best_m] - centers[best_n]
    return {"hti": hti, "tinn": tinn}


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
    if any(not math.isfinite(x) or x <= 0 for x in nn):
        raise ValueError("NN 간격은 유한한 양수여야 합니다 "
                         "(0/음수/NaN이 정제 후에도 남아 있습니다).")

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

    # 강건(로버스트) 통계 — 이상값에 덜 민감. 임상 실측 RR에 유용.
    median_nn = statistics.median(nn)
    mad_nn = statistics.median([abs(v - median_nn) for v in nn])

    geom = geometric_indices(nn)

    return {
        "n_beats": n,
        "mean_nn": mean_nn,
        "median_nn": median_nn,
        "mad_nn": mad_nn,
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
        "hti": geom["hti"],
        "tinn": geom["tinn"],
    }
