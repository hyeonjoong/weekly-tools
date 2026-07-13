"""RR 간격의 이상박동(artifact/ectopic) 탐지 및 보정.

실제 웨어러블/PPG 데이터는 놓친 박동(missed beat), 여분 박동(extra beat),
움직임 잡음 때문에 물리적으로 불가능한 RR 값을 종종 포함합니다. HRV 지표,
특히 RMSSD/pNN50/HF 파워는 이런 이상값에 매우 민감하므로 반드시 먼저
정제해야 합니다.

두 가지 규칙을 사용합니다 (둘 다 투명하고 손으로 검산 가능):
  1) 생리적 범위 필터: [min_rr, max_rr] (기본 300~2000 ms, 30~200 bpm) 밖의 값.
  2) 상대적 급변 필터(Malik류): 국소 중앙값 대비 상대편차가 rel_thresh(기본 0.2)를
     넘는 값. 국소 중앙값은 해당 지점 주변 window개 박동의 중앙값입니다.
"""

from __future__ import annotations

import statistics
from typing import List, Sequence, Tuple


def _local_median(rr: Sequence[float], i: int, window: int,
                  in_range: Sequence[bool] = None) -> float:
    """i번째 박동 주변(자신 제외) window개 박동의 중앙값.

    in_range가 주어지면 생리적 범위를 벗어난 이웃(gross artifact)은 기준선
    계산에서 제외합니다 — 한 개의 극단값이 인접 정상 박동까지 이상으로
    오탐하는 것을 막습니다. 범위 내 이웃이 하나도 없으면 원시 이웃으로 폴백.
    """
    half = max(1, window // 2)
    lo = max(0, i - half)
    hi = min(len(rr), i + half + 1)
    raw = [rr[j] for j in range(lo, hi) if j != i]
    if in_range is not None:
        clean = [rr[j] for j in range(lo, hi) if j != i and in_range[j]]
        if clean:
            return statistics.median(clean)
    if not raw:
        return rr[i]
    return statistics.median(raw)


def detect_artifacts(
    rr: Sequence[float],
    min_rr: float = 300.0,
    max_rr: float = 2000.0,
    rel_thresh: float = 0.2,
    window: int = 5,
) -> List[bool]:
    """각 RR이 이상박동인지 여부를 담은 bool 리스트를 반환.

    True = 이상(제거/보정 대상). 범위 위반 또는 국소 중앙값 대비 상대편차가
    rel_thresh를 초과하면 이상으로 표시합니다. 국소 중앙값은 생리적 범위를
    벗어난 이웃을 제외하고 계산해, 극단적 이상박동이 인접 정상 박동을
    오탐하지 않도록 합니다.
    """
    in_range = [min_rr <= float(v) <= max_rr for v in rr]
    flags: List[bool] = []
    for i, value in enumerate(rr):
        bad = False
        if not in_range[i]:
            bad = True
        else:
            med = _local_median(rr, i, window, in_range)
            if med > 0 and abs(value - med) / med > rel_thresh:
                bad = True
        flags.append(bad)
    return flags


def clean_rr(
    rr: Sequence[float],
    method: str = "interpolate",
    min_rr: float = 300.0,
    max_rr: float = 2000.0,
    rel_thresh: float = 0.2,
    window: int = 5,
) -> Tuple[List[float], List[bool]]:
    """이상박동을 탐지하고 보정한 RR 시계열을 반환.

    method:
      - "interpolate": 이상값을 양옆 정상값의 선형보간으로 대체(길이 유지).
        주파수영역 분석에 권장.
      - "remove": 이상값을 제거(길이 감소).
      - "none": 탐지만 하고 원본 유지.

    반환: (정제된 RR 리스트, 원본 길이의 이상 플래그 리스트)
    """
    rr = [float(x) for x in rr]
    flags = detect_artifacts(rr, min_rr, max_rr, rel_thresh, window)

    if method == "none":
        return list(rr), flags

    if method == "remove":
        cleaned = [v for v, bad in zip(rr, flags) if not bad]
        return cleaned, flags

    if method == "interpolate":
        cleaned = list(rr)
        n = len(rr)
        for i in range(n):
            if not flags[i]:
                continue
            # 왼쪽/오른쪽에서 가장 가까운 정상값을 찾아 선형보간
            left = i - 1
            while left >= 0 and flags[left]:
                left -= 1
            right = i + 1
            while right < n and flags[right]:
                right += 1
            if left >= 0 and right < n:
                span = right - left
                cleaned[i] = rr[left] + (rr[right] - rr[left]) * (i - left) / span
            elif left >= 0:
                cleaned[i] = cleaned[left]
            elif right < n:
                cleaned[i] = rr[right]
            # 전부 이상이면 그대로 둠(원본 값)
        return cleaned, flags

    raise ValueError(f"알 수 없는 보정 방법: {method!r} (interpolate/remove/none 중 하나)")
