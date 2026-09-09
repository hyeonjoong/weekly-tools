"""rsalink.breath — 호흡 파형에서 호흡(흡기 시작→다음 흡기 시작) 검출.

절차
1. 선형 디트렌드 → 이동 중앙값(창 = 2×max_breath_s, 기본 30 s) 기준선 제거 →
   smooth_s(기본 0.5 s) 이동평균으로 고주파 잡음 억제.
2. 골(local minimum) = 흡기 시작 후보. 돌출(prominence, 표준 정의) = 골 양쪽으로
   골보다 낮은 값이 나올 때까지의 최대값 중 작은 쪽 − 골 값. 돌출 ≥ k·MAD(신호)
   (k 기본 1.0) 이고 직전 골과의 간격 ≥ --min-breath-s (기본 1.5 s) 인 골만 채택.
   간격이 짧으면 더 깊은 골을 남긴다.
3. 호흡 = 골 i → 골 i+1. 사이의 최대값 위치 = 흡기 끝/호기 시작.
   흡기 창 [onset, peak), 호기 창 [peak, next onset).
4. 아티팩트 플래그: 주기 < min_breath_s 또는 > max_breath_s(기본 15 s),
   이 호흡의 흡기 진폭(peak−onset) < k·MAD, 호기 창 길이 0.
   플래그된 호흡은 호흡수/RSA 통계에서 제외하고 비율을 자백한다.
검증(테스트): 진폭 1 사인 + 가우스 잡음 σ 0.3 까지 6/15/24 회/분 ±0.15 복원.
σ 0.5(SNR ≈ 6 dB) 에서 느린 호흡(6/분)은 골 근처의 잡음 봉우리를 호흡으로 더 셀 수
있다 — 검출률 자체를 리포트에 찍는 이유.

부호 규약: 파형 값이 클수록 흉곽 확장(흡기)이라고 가정한다. 반대 부호면 --invert.
호흡 이벤트 입력(RespEvents)은 골 검출 없이 이벤트 시각을 onset 으로 쓰고, 피크는
onset + 0.4×주기 로 가정(흡기:호기 ≈ 2:3, 가정임을 자백).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .parse import RespEvents, RespWaveform, _median


@dataclass
class Breath:
    idx: int
    t_onset: float          # 흡기 시작(초)
    t_peak: float           # 흡기 끝 / 호기 시작(초)
    t_end: float            # 다음 흡기 시작(초)
    prominence: float
    flag: str = ""          # "" | "short" | "long" | "low_prom" | "no_exp"

    @property
    def period_s(self) -> float:
        return self.t_end - self.t_onset

    @property
    def rate_bpm(self) -> float:
        return 60.0 / self.period_s if self.period_s > 0 else float("nan")

    @property
    def valid(self) -> bool:
        return self.flag == ""


@dataclass
class BreathResult:
    breaths: List[Breath]
    detrended: List[float]          # 기준선 제거된 파형(결맞음용)
    t: List[float]
    mad: float
    k_mad: float
    min_breath_s: float
    max_breath_s: float
    source: str                     # "waveform" | "events"
    peak_assumed: bool = False      # 이벤트 입력이면 True

    @property
    def valid_breaths(self) -> List[Breath]:
        return [b for b in self.breaths if b.valid]

    @property
    def n(self) -> int:
        return len(self.breaths)

    @property
    def artifact_frac(self) -> float:
        return 0.0 if not self.breaths else 1.0 - len(self.valid_breaths) / len(self.breaths)

    def rates(self, t0: Optional[float] = None, t1: Optional[float] = None) -> List[float]:
        out = []
        for b in self.valid_breaths:
            if t0 is not None and b.t_onset < t0:
                continue
            if t1 is not None and b.t_onset >= t1:
                continue
            out.append(b.rate_bpm)
        return out

    def mean_rate(self, t0=None, t1=None) -> float:
        r = self.rates(t0, t1)
        return sum(r) / len(r) if r else float("nan")

    def mean_freq_hz(self, t0=None, t1=None) -> float:
        """평균 호흡 주파수 = 평균 호흡수 / 60 (주기 평균의 역수가 아니라 rate 평균)."""
        m = self.mean_rate(t0, t1)
        return m / 60.0 if m == m else float("nan")


# ---------------------------------------------------------------- 기본 통계


def mad(xs: Sequence[float]) -> float:
    m = _median(xs)
    return _median([abs(x - m) for x in xs])


def linear_detrend(t: Sequence[float], v: Sequence[float]) -> List[float]:
    n = len(v)
    if n < 2:
        return list(v)
    mt = sum(t) / n
    mv = sum(v) / n
    sxx = sum((a - mt) ** 2 for a in t)
    if sxx == 0:
        return [x - mv for x in v]
    sxy = sum((a - mt) * (b - mv) for a, b in zip(t, v))
    slope = sxy / sxx
    return [b - (mv + slope * (a - mt)) for a, b in zip(t, v)]


def moving_median(v: Sequence[float], win: int) -> List[float]:
    """홀수 창 이동 중앙값(가장자리는 축소 창). O(n·w log w) — 호흡 파형 크기엔 충분."""
    n = len(v)
    half = max(1, win // 2)
    out = [0.0] * n
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out[i] = _median(v[lo:hi])
    return out


# ---------------------------------------------------------------- 검출


def _local_minima(v: Sequence[float]) -> List[int]:
    n = len(v)
    idx = []
    i = 1
    while i < n - 1:
        if v[i] < v[i - 1] and v[i] <= v[i + 1]:
            # 평탄 구간이면 중앙 인덱스
            j = i
            while j + 1 < n and v[j + 1] == v[i]:
                j += 1
            if j + 1 < n and v[j + 1] > v[i]:
                idx.append((i + j) // 2)
            i = j + 1
        else:
            i += 1
    return idx


def _prominence_at(v: Sequence[float], i: int, max_span: int) -> float:
    """골 i 의 돌출(표준 정의): 좌우로 v[i] 보다 낮은 값이 나올 때까지(또는 max_span)
    훑어 그 사이 최대값을 각각 취하고, 둘 중 작은 쪽 − v[i]."""
    n = len(v)
    base = v[i]
    left = base
    j = i - 1
    lim = max(0, i - max_span)
    while j >= lim and v[j] >= base:
        if v[j] > left:
            left = v[j]
        j -= 1
    right = base
    j = i + 1
    lim = min(n - 1, i + max_span)
    while j <= lim and v[j] >= base:
        if v[j] > right:
            right = v[j]
        j += 1
    return min(left, right) - base


def moving_average(v: Sequence[float], win: int) -> List[float]:
    n = len(v)
    if win <= 1 or n < win:
        return list(v)
    half = win // 2
    out = [0.0] * n
    csum = [0.0] * (n + 1)
    for i, x in enumerate(v):
        csum[i + 1] = csum[i] + x
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out[i] = (csum[hi] - csum[lo]) / (hi - lo)
    return out


def baseline_moving_median(d: Sequence[float], fs: float, win_s: float) -> List[float]:
    """긴 창(win_s) 이동 중앙값 기준선 — 속도를 위해 ~2 Hz 로 블록 평균 후 계산하고
    선형 보간으로 되돌린다."""
    n = len(d)
    step = max(1, int(round(fs / 2.0)))
    coarse = [sum(d[i:i + step]) / len(d[i:i + step]) for i in range(0, n, step)]
    win = int(round(win_s * fs / step)) | 1
    cm = moving_median(coarse, max(3, win))
    out = [0.0] * n
    for i in range(n):
        pos = i / step
        k = int(pos)
        if k + 1 < len(cm):
            f = pos - k
            out[i] = cm[k] * (1 - f) + cm[k + 1] * f
        else:
            out[i] = cm[-1]
    return out


def detect_breaths(resp, min_breath_s: float = 1.5, max_breath_s: float = 15.0,
                   k_mad: float = 1.0, invert: bool = False, smooth_s: float = 0.5) -> BreathResult:
    if isinstance(resp, RespEvents):
        return _breaths_from_events(resp, min_breath_s, max_breath_s)
    if not isinstance(resp, RespWaveform):
        raise TypeError("resp must be RespWaveform or RespEvents")
    t = resp.t
    fs = resp.fs_est
    v = [(-x if invert else x) for x in resp.v]
    d = linear_detrend(t, v)
    base = baseline_moving_median(d, fs, 2.0 * max_breath_s)
    d = [a - b for a, b in zip(d, base)]
    # smooth_s 이동평균으로 고주파 잡음만 줄인다(1 Hz 성분 감쇠 ≈0.64, 0.5 Hz ≈0.9)
    d = moving_average(d, max(1, int(round(smooth_s * fs)) | 1))
    sig_mad = mad(d) or 1e-12
    thr = k_mad * sig_mad
    mins = _local_minima(d)
    span = int(round(max_breath_s * fs))
    cand = [(i, _prominence_at(d, i, span)) for i in mins]
    cand = [(i, p) for i, p in cand if p >= thr]
    # 최소 간격: 가까운 골 쌍은 더 깊은(값이 작은) 쪽만
    kept: List[Tuple[int, float]] = []
    for i, p in cand:
        if kept and t[i] - t[kept[-1][0]] < min_breath_s:
            if d[i] < d[kept[-1][0]]:
                kept[-1] = (i, p)
            continue
        kept.append((i, p))
    breaths: List[Breath] = []
    for n in range(len(kept) - 1):
        i0, p0 = kept[n]
        i1, _ = kept[n + 1]
        seg = d[i0:i1 + 1]
        ip = i0 + max(range(len(seg)), key=seg.__getitem__)
        b = Breath(idx=n, t_onset=t[i0], t_peak=t[ip], t_end=t[i1], prominence=p0)
        if b.period_s < min_breath_s:
            b.flag = "short"
        elif b.period_s > max_breath_s:
            b.flag = "long"
        elif (d[ip] - d[i0]) < thr:
            b.flag = "low_prom"      # 이 호흡 자체의 흡기 진폭이 k·MAD 미만(얕은 호흡/잡음)
        elif ip <= i0 or ip >= i1:
            b.flag = "no_exp"
        breaths.append(b)
    return BreathResult(breaths=breaths, detrended=d, t=list(t), mad=sig_mad, k_mad=k_mad,
                        min_breath_s=min_breath_s, max_breath_s=max_breath_s, source="waveform")


def _breaths_from_events(ev: RespEvents, min_breath_s: float, max_breath_s: float) -> BreathResult:
    breaths = []
    for n in range(len(ev.t) - 1):
        t0, t1 = ev.t[n], ev.t[n + 1]
        b = Breath(idx=n, t_onset=t0, t_peak=t0 + 0.4 * (t1 - t0), t_end=t1, prominence=float("nan"))
        if b.period_s < min_breath_s:
            b.flag = "short"
        elif b.period_s > max_breath_s:
            b.flag = "long"
        breaths.append(b)
    return BreathResult(breaths=breaths, detrended=[], t=list(ev.t), mad=float("nan"), k_mad=float("nan"),
                        min_breath_s=min_breath_s, max_breath_s=max_breath_s, source="events",
                        peak_assumed=True)


# ---------------------------------------------------------------- 요약


def rate_summary(rates: Sequence[float]) -> dict:
    n = len(rates)
    if n == 0:
        return {"n": 0, "mean": float("nan"), "sd": float("nan"), "cv": float("nan"), "median": float("nan")}
    m = sum(rates) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in rates) / (n - 1)) if n > 1 else 0.0
    return {"n": n, "mean": m, "sd": sd, "cv": sd / m if m else float("nan"), "median": _median(rates)}
