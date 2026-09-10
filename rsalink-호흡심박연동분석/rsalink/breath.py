"""rsalink.breath — 호흡 파형에서 호흡(흡기 시작→다음 흡기 시작) 검출.

절차
1. 선형 디트렌드 → 이동 중앙값(창 = 2×max_breath_s, 기본 30 s) 기준선 제거 →
   smooth_s(기본 0.5 s) 이동평균으로 고주파 잡음 억제.
2. 골(local minimum) = 흡기 시작 후보. 돌출(prominence, 표준 정의) = 골 양쪽으로
   골보다 낮은 값이 나올 때까지의 최대값 중 작은 쪽 − 골 값.
   임계 = max(k·MAD_noise, 0.15·(p95−p5)). **MAD_noise 는 신호가 아니라 잡음의 척도**:
   평활 잔차(원 파형 − smooth_s 이동평균)의 MAD × 1.4826 (정규 잡음 σ 추정). k 기본 3.0.
   (라운드 0 은 신호 자체의 MAD × 1.0 이었다 — 호기 말 정지가 긴 파형에서는 샘플 대부분이
   정지 수준에 몰려 MAD 가 붕괴하고 잡음 골을 호흡으로 세었다. 라운드 1 A2.)
   (p95−p5) 는 **60 s 창별**(10 s 간격 블록)로 계산해 골마다 그 자리의 바닥을 쓴다 — 전역 값이면
   진폭 1.0 조건 뒤에 오는 진폭 0.12 조건(≈8:1)의 골이 전부 기각된다(라운드 2 #3).
   돌출 ≥ 임계이고 직전 골과의 간격 ≥ --min-breath-s (기본 1.5 s) 인 골만 채택.
   간격이 짧으면 더 깊은 골을 남긴다.
3. 호흡 = 골 i → 골 i+1. 사이의 최대값 위치 = 흡기 끝/호기 시작.
   **흡기 시작 보정**(라운드 2 #4): 봉우리에서 골 쪽으로 내려오며 누적 최소를 추적하다 신호가 누적
   최소보다 tol = max(3·σ_smooth, 0.10·흡기 진폭) 이상 되오르면 멈추고, 누적 최소 자리를 흡기 시작으로
   쓴다(σ_smooth = MAD_noise/√평활 창). 매끈한 파형은 되오름이 골을 지나야 생기므로 골 그대로이고,
   호기 말 정지가 평탄한 파형에서는 정지 구간 안의 임의 잡음 최저점 대신 상승 발치 근처가 잡힌다
   (정지형 6/분·σ 0.10·20 seed: 호흡수 6.2–6.9 → 6.0–6.3, 주기 CV 19–35% → 13–26%).
   흡기 창 [onset, peak), 호기 창 [peak, next onset).
4. 아티팩트 플래그: 주기 < min_breath_s 또는 > max_breath_s(기본 15 s),
   이 호흡의 흡기 진폭(peak−onset) < 임계, 호기 창 길이 0.
   플래그된 호흡은 호흡수/RSA 통계에서 제외하고 비율을 자백한다.
5. 호흡수 안정성: 유효 호흡수의 CV > RATE_CV_FLAG(40%) 면 "호흡 검출 불안정" 플래그,
   CV > RATE_CV_GATE(60%) 또는 |중앙값−평균|/중앙값 > RATE_SKEW_GATE(25%) 면 exit 3.
   (임계는 문헌 근거 없는 이 툴의 관례.)
검증(테스트): 진폭 1 사인 + 가우스 잡음 σ 0.3 까지 6/15/24 회/분 ±0.15 복원;
정지형 파형(빠른 흡기 + 지수 호기 + 정지) 6/분·σ 0.10 → 호흡수 6.0–6.3, 유효 ≥ 90%.
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

DEFAULT_K_MAD = 3.0
PROM_FLOOR_FRAC = 0.15          # 임계 바닥 = 0.15 × (p95 − p5)
PROM_FLOOR_WIN_S = 60.0         # 바닥은 60 s 창별로(라운드 2 #3) — 전역이면 큰 호흡 조건이 작은 호흡 조건을 지운다
PROM_FLOOR_HOP_S = 10.0         # 창 중심 간격
ONSET_REFINE_K = 3.0            # 흡기 시작 보정: 누적 최소보다 k·σ_smooth 이상 되오르면 정지(라운드 2 #4)
ONSET_REFINE_AMP_FRAC = 0.10    # 되오름 허용치의 바닥 = 흡기 진폭의 10%
MAD_TO_SIGMA = 1.4826
RATE_CV_FLAG = 0.40             # 호흡수 CV > 40% → "호흡 검출 불안정" 플래그
RATE_CV_GATE = 0.60             # CV > 60% → exit 3
RATE_SKEW_GATE = 0.25           # |중앙값 − 평균| / 중앙값 > 25% → exit 3
SPIKE_Z = 10.0                  # 디트렌드 전 |z| > 10·MAD·1.4826 클리핑


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
    mad: float                      # 잡음 σ 추정(평활 잔차 MAD × 1.4826)
    k_mad: float
    min_breath_s: float
    max_breath_s: float
    source: str                     # "waveform" | "events"
    peak_assumed: bool = False      # 이벤트 입력이면 True
    prom_floor: float = float("nan")    # 0.15 × (p95 − p5) 의 60 s 창별 값 중앙값(대표값)
    prom_thr: float = float("nan")      # 대표 임계 = max(k·mad, prom_floor) — 실제 적용은 골마다 그 자리의 바닥
    prom_floor_lo: float = float("nan")     # 창별 바닥 최소
    prom_floor_hi: float = float("nan")     # 창별 바닥 최대
    smooth_s: float = float("nan")
    baseline_win_s: float = float("nan")
    n_clipped: int = 0                  # 스파이크 클리핑 샘플 수

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


def _percentile(xs: Sequence[float], p: float) -> float:
    s = sorted(xs)
    if not s:
        return float("nan")
    pos = p * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def rolling_range_floor(d: Sequence[float], fs: float, win_s: float = PROM_FLOOR_WIN_S,
                        hop_s: float = PROM_FLOOR_HOP_S, frac: float = PROM_FLOOR_FRAC) -> List[float]:
    """샘플별 임계 바닥 = frac × (p95 − p5), 길이 win_s 창(중심 간격 hop_s 블록, ~4 Hz 로 솎아 계산).
    기록이 win_s 보다 짧으면 전역 값과 같다(라운드 2 #3)."""
    n = len(d)
    if n == 0:
        return []
    step = max(1, int(round(fs / 4.0)))
    coarse = d[::step]
    m = len(coarse)
    half = max(1, int(round(win_s * fs / step / 2.0)))
    hop = max(1, int(round(hop_s * fs / step)))
    n_blocks = (m + hop - 1) // hop
    block_val = []
    for b in range(n_blocks):
        c = b * hop + hop // 2
        seg = coarse[max(0, c - half):min(m, c + half + 1)]
        block_val.append(frac * (_percentile(seg, 0.95) - _percentile(seg, 0.05)))
    return [block_val[min(n_blocks - 1, (i // step) // hop)] for i in range(n)]


def _refine_onset(d: Sequence[float], i0: int, ip: int, tol: float) -> int:
    """봉우리 ip 에서 골 i0 쪽으로 내려오며 누적 최소를 추적하고, 신호가 누적 최소보다 tol 이상 되오르면
    멈춘다 → 누적 최소 위치(i0 ≤ 결과 ≤ ip). 매끈한 파형은 되오름이 골을 지나야 생기므로 골 그대로(라운드 2 #4)."""
    m, jm = d[ip], ip
    j = ip
    while j > i0:
        j -= 1
        if d[j] < m:
            m, jm = d[j], j
        elif d[j] > m + tol:
            break
    return jm


def detect_breaths(resp, min_breath_s: float = 1.5, max_breath_s: float = 15.0,
                   k_mad: float = DEFAULT_K_MAD, invert: bool = False, smooth_s: float = 0.5) -> BreathResult:
    if isinstance(resp, RespEvents):
        return _breaths_from_events(resp, min_breath_s, max_breath_s)
    if not isinstance(resp, RespWaveform):
        raise TypeError("resp must be RespWaveform or RespEvents")
    t = resp.t
    fs = resp.fs_est
    v = [(-x if invert else x) for x in resp.v]
    # 단일 스파이크: 디트렌드 전에 |z| > SPIKE_Z × MAD·1.4826 를 클리핑하고 건수를 자백(라운드 1 D12)
    v_med = _median(v)
    v_sig = MAD_TO_SIGMA * mad(v)
    n_clipped = 0
    if v_sig > 0:
        lo_c, hi_c = v_med - SPIKE_Z * v_sig, v_med + SPIKE_Z * v_sig
        clipped = []
        for x in v:
            if x > hi_c:
                clipped.append(hi_c)
                n_clipped += 1
            elif x < lo_c:
                clipped.append(lo_c)
                n_clipped += 1
            else:
                clipped.append(x)
        v = clipped
    d = linear_detrend(t, v)
    base_win_s = 2.0 * max_breath_s
    base = baseline_moving_median(d, fs, base_win_s)
    d = [a - b for a, b in zip(d, base)]
    # smooth_s 이동평균으로 고주파 잡음만 줄인다(1 Hz 성분 감쇠 ≈0.64, 0.5 Hz ≈0.9)
    d_raw = d
    win = max(1, int(round(smooth_s * fs)) | 1)
    d = moving_average(d, win)
    # 잡음 척도 = 평활 잔차의 MAD × 1.4826 (신호 자체의 MAD 는 정지 파형에서 붕괴)
    resid = [a - b for a, b in zip(d_raw, d)]
    sig_mad = MAD_TO_SIGMA * mad(resid)
    sig_smooth = sig_mad / math.sqrt(win)           # 평활 뒤 잡음 σ(이동평균 win 샘플)
    # 임계 바닥은 60 s 창별 (p95−p5) — 골마다 그 자리의 바닥(라운드 2 #3). 리포트에는 중앙값·범위를 적는다.
    floor_at = rolling_range_floor(d, fs)
    floor = _median(floor_at)
    thr = max(k_mad * sig_mad, floor, 1e-12)

    def thr_at(i: int) -> float:
        return max(k_mad * sig_mad, floor_at[i], 1e-12)

    mins = _local_minima(d)
    span = int(round(max_breath_s * fs))
    cand = [(i, _prominence_at(d, i, span)) for i in mins]
    cand = [(i, p) for i, p in cand if p >= thr_at(i)]
    # 최소 간격: 가까운 골 쌍은 더 깊은(값이 작은) 쪽만
    kept: List[Tuple[int, float]] = []
    for i, p in cand:
        if kept and t[i] - t[kept[-1][0]] < min_breath_s:
            if d[i] < d[kept[-1][0]]:
                kept[-1] = (i, p)
            continue
        kept.append((i, p))
    # 봉우리(흡기 끝) 와 보정된 흡기 시작(라운드 2 #4). 마지막 골은 다음 봉우리가 없어 그대로.
    peaks: List[int] = []
    onsets: List[int] = []
    for n in range(len(kept) - 1):
        i0, i1 = kept[n][0], kept[n + 1][0]
        seg = d[i0:i1 + 1]
        ip = i0 + max(range(len(seg)), key=seg.__getitem__)
        tol = max(ONSET_REFINE_K * sig_smooth, ONSET_REFINE_AMP_FRAC * (d[ip] - d[i0]))
        peaks.append(ip)
        onsets.append(_refine_onset(d, i0, ip, tol))
    if kept:
        onsets.append(kept[-1][0])
    breaths: List[Breath] = []
    for n in range(len(kept) - 1):
        i0, p0 = kept[n]
        j0, ip, j1 = onsets[n], peaks[n], onsets[n + 1]
        b = Breath(idx=n, t_onset=t[j0], t_peak=t[ip], t_end=t[j1], prominence=p0)
        if b.period_s < min_breath_s:
            b.flag = "short"
        elif b.period_s > max_breath_s:
            b.flag = "long"
        elif (d[ip] - d[i0]) < thr_at(i0):
            b.flag = "low_prom"      # 이 호흡 자체의 흡기 진폭(봉우리 − 골)이 임계 미만(얕은 호흡/잡음)
        elif ip <= j0 or ip >= j1:
            b.flag = "no_exp"
        breaths.append(b)
    return BreathResult(breaths=breaths, detrended=d, t=list(t), mad=sig_mad, k_mad=k_mad,
                        min_breath_s=min_breath_s, max_breath_s=max_breath_s, source="waveform",
                        prom_floor=floor, prom_thr=thr, smooth_s=smooth_s, baseline_win_s=base_win_s,
                        n_clipped=n_clipped, prom_floor_lo=min(floor_at), prom_floor_hi=max(floor_at))


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
        return {"n": 0, "mean": float("nan"), "sd": float("nan"), "cv": float("nan"), "median": float("nan"),
                "skew": float("nan")}
    m = sum(rates) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in rates) / (n - 1)) if n > 1 else 0.0
    med = _median(rates)
    return {"n": n, "mean": m, "sd": sd, "cv": sd / m if m else float("nan"), "median": med,
            "skew": abs(med - m) / med if med else float("nan")}


def rate_stability(rates: Sequence[float]) -> Tuple[bool, List[str]]:
    """(불안정 플래그, exit-3 사유 목록). 라운드 1 A2 — 임계는 이 툴의 관례(문헌 근거 없음)."""
    s = rate_summary(rates)
    if s["n"] < 2:
        return False, []
    flag = s["cv"] > RATE_CV_FLAG
    reasons = []
    if s["cv"] > RATE_CV_GATE:
        reasons.append(f"호흡 검출 불안정: 호흡수 CV {100 * s['cv']:.0f}% > {RATE_CV_GATE * 100:.0f}%")
    if s["skew"] > RATE_SKEW_GATE:
        reasons.append(f"호흡 검출 불안정: 호흡수 |중앙값−평균|/중앙값 {100 * s['skew']:.0f}% > "
                       f"{RATE_SKEW_GATE * 100:.0f}%")
    return flag, reasons
