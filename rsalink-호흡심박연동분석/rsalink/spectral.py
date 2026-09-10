"""rsalink.spectral — 자체 FFT · Welch PSD · 대역 파워 · 결맞음 (stdlib 전용).

- RR(ms, 박동 시각 기준)을 4 Hz 등간격 격자에 선형 보간(양끝은 홀드).
- Welch: Hann 창, 50% 겹침, 세그먼트 길이 nperseg(기본 256 샘플 = 64 s, Δf ≈ 0.0156 Hz;
  샘플이 2·nperseg 미만이면 2의 거듭제곱으로 줄여 최소 64). 세그먼트마다 평균 제거.
  단측 PSD 스케일: |X|²·2 / (fs·Σw²) → ms²/Hz. 대역 파워 = Σ PSD·Δf (ms²).
- 대역: LF 0.04–0.15, HF 고정 0.15–0.40 (Task Force 1996), 호흡중심 = 평균 호흡주파수
  ± half_width(기본 0.04 Hz).
- 결맞음: 같은 Welch 세그먼트로 교차스펙트럼 Pxy 를 평균, MSC = |Pxy|²/(Pxx·Pyy).
  호흡주파수에 가장 가까운 빈의 값을 보고. 독립 신호에서도 기대값 ≈ 1/L (L = 세그먼트 수)
  이므로 L 과 1/L 을 함께 표시한다.
- 교차상관 오프셋 제안: 4 Hz 격자에서 ±max_lag_s 범위 상관 최대 지연 — 표시만, 적용 안 함.
"""
from __future__ import annotations

import bisect
import cmath
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

FS = 4.0
LF_BAND = (0.04, 0.15)
HF_BAND = (0.15, 0.40)
RESP_HALF_WIDTH = 0.04
DEFAULT_NPERSEG = 256
MIN_NPERSEG = 64


# ---------------------------------------------------------------- 보간


def resample_linear(t: Sequence[float], x: Sequence[float], t0: float, t1: float,
                    fs: float = FS) -> Tuple[List[float], List[float]]:
    """[t0, t1) 를 1/fs 간격으로 선형 보간. 범위 밖은 양끝 값 홀드."""
    n = int(math.floor((t1 - t0) * fs))
    grid = [t0 + k / fs for k in range(n)]
    out = []
    for g in grid:
        j = bisect.bisect_right(t, g)
        if j <= 0:
            out.append(x[0])
        elif j >= len(t):
            out.append(x[-1])
        else:
            ta, tb = t[j - 1], t[j]
            xa, xb = x[j - 1], x[j]
            f = (g - ta) / (tb - ta) if tb > ta else 0.0
            out.append(xa + (xb - xa) * f)
    return grid, out


# ---------------------------------------------------------------- FFT


def fft(x: Sequence[complex]) -> List[complex]:
    """반복형 radix-2 FFT. 길이는 2의 거듭제곱이어야 한다."""
    n = len(x)
    if n & (n - 1):
        raise ValueError("fft length must be a power of two")
    a = [complex(v) for v in x]
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    length = 2
    while length <= n:
        ang = -2.0 * math.pi / length
        wl = complex(math.cos(ang), math.sin(ang))
        for i in range(0, n, length):
            w = 1 + 0j
            half = length // 2
            for k in range(half):
                u = a[i + k]
                v = a[i + k + half] * w
                a[i + k] = u + v
                a[i + k + half] = u - v
                w *= wl
        length <<= 1
    return a


def hann(n: int) -> List[float]:
    return [0.5 - 0.5 * math.cos(2 * math.pi * k / n) for k in range(n)]


def choose_nperseg(n: int, want: int = DEFAULT_NPERSEG) -> int:
    seg = want
    while seg > MIN_NPERSEG and n < 2 * seg:
        seg //= 2
    return seg


@dataclass
class Spectrum:
    freqs: List[float]
    pxx: List[float]              # ms²/Hz
    nperseg: int
    n_segments: int
    df: float
    pyy: Optional[List[float]] = None
    coherence: Optional[List[float]] = None


def welch(x: Sequence[float], fs: float = FS, nperseg: Optional[int] = None,
          y: Optional[Sequence[float]] = None) -> Spectrum:
    n = len(x)
    seg = nperseg or choose_nperseg(n)
    if n < seg:
        raise ValueError(f"too few samples for Welch ({n} < {seg})")
    step = seg // 2
    w = hann(seg)
    wsum2 = sum(v * v for v in w)
    nb = seg // 2 + 1
    pxx = [0.0] * nb
    pyy = [0.0] * nb if y is not None else None
    pxy = [0j] * nb if y is not None else None
    L = 0
    start = 0
    while start + seg <= n:
        sx = x[start:start + seg]
        mx = sum(sx) / seg
        X = fft([(v - mx) * wk for v, wk in zip(sx, w)])
        if y is not None:
            sy = y[start:start + seg]
            my = sum(sy) / seg
            Y = fft([(v - my) * wk for v, wk in zip(sy, w)])
        for k in range(nb):
            pxx[k] += abs(X[k]) ** 2
            if y is not None:
                pyy[k] += abs(Y[k]) ** 2
                pxy[k] += X[k] * Y[k].conjugate()
        L += 1
        start += step
    scale = 2.0 / (fs * wsum2)
    freqs = [k * fs / seg for k in range(nb)]
    pxx = [v / L * scale for v in pxx]
    pxx[0] /= 2.0
    pxx[-1] /= 2.0
    coh = None
    if y is not None:
        pyy_s = [v / L * scale for v in pyy]
        coh = []
        for k in range(nb):
            # MSC = |Σ XY*|² / (Σ|X|² · Σ|Y|²) — 스케일·L 은 약분되므로 원시 합으로 계산
            den = (pxx[k] / scale * L) * pyy[k]
            num = abs(pxy[k]) ** 2
            coh.append(num / den if den > 0 else 0.0)
        pyy = pyy_s
    return Spectrum(freqs=freqs, pxx=pxx, nperseg=seg, n_segments=L, df=fs / seg, pyy=pyy, coherence=coh)


def band_power(sp: Spectrum, lo: float, hi: float) -> float:
    """[lo, hi) 구간 PSD 적분(ms²)."""
    return sum(p for f, p in zip(sp.freqs, sp.pxx) if lo <= f < hi) * sp.df


def band_bin_count(df: float, lo: float, hi: float) -> int:
    """[lo, hi) 에 들어가는 Welch 빈 수(band_power 와 같은 규칙: f = k·df, lo ≤ f < hi).
    nperseg 256(Δf 1/64) 의 HF 0.15–0.40 은 k = 10..25 → 16개; 128 → 8개; 64 → 4개. 잡음 마진(segments.py)에 쓴다."""
    if df <= 0:
        return 0
    return sum(1 for k in range(int(hi / df) + 2) if lo <= k * df < hi)


def value_at(freqs: Sequence[float], vals: Sequence[float], f: float) -> float:
    k = min(range(len(freqs)), key=lambda i: abs(freqs[i] - f))
    return vals[k]


def peak_freq(sp: Spectrum, lo: float, hi: float) -> float:
    best, bf = -1.0, float("nan")
    for f, p in zip(sp.freqs, sp.pxx):
        if lo <= f < hi and p > best:
            best, bf = p, f
    return bf


# ---------------------------------------------------------------- 상위 API


@dataclass
class BandResult:
    lf: float
    hf_fixed: float
    resp_centered: float
    resp_band: Tuple[float, float]
    resp_freq: float
    coherence_at_resp: Optional[float]
    n_segments: int
    nperseg: int
    df: float
    duration_s: float
    rr_peak_freq: float           # RR 스펙트럼 0.04–0.40 최대 빈
    coherence_bias: Optional[float]   # 1/L
    hf_n_bins: int = 0            # HF 고정 대역 안의 Welch 빈 수(잡음 마진용; 0 이면 df 로 계산)

    @property
    def resp_over_hf(self) -> float:
        return self.resp_centered / self.hf_fixed if self.hf_fixed > 0 else float("inf")

    @property
    def coherence_null95(self) -> Optional[float]:
        """독립 신호 MSC 의 95% 분위 근사 1 − 0.05^(1/(L−1)) (L 세그먼트 Welch, 단일 빈) — 라운드 1 D6."""
        if self.coherence_at_resp is None or self.n_segments < 2:
            return None
        return 1.0 - 0.05 ** (1.0 / (self.n_segments - 1))


def analyze_segment(rr_t: Sequence[float], rr_ms: Sequence[float], t0: float, t1: float,
                    resp_freq: float, resp_t: Optional[Sequence[float]] = None,
                    resp_v: Optional[Sequence[float]] = None, resp_offset: float = 0.0,
                    half_width: float = RESP_HALF_WIDTH, nperseg: Optional[int] = None) -> BandResult:
    """구간 [t0,t1) 의 대역 파워와(호흡 파형이 있으면) 결맞음. 격자 상한은 RR 데이터 끝(끝값 홀드 금지)."""
    t1 = min(t1, rr_t[-1]) if rr_t else t1
    if t1 <= t0:
        raise ValueError("구간이 RR 데이터 범위 밖")
    grid, x = resample_linear(rr_t, rr_ms, t0, t1)
    y = None
    if resp_t is not None and resp_v is not None and len(resp_t) > 1:
        # 호흡 시각 + offset = RR 시각 → RR 격자 g 에 대응하는 호흡 시각은 g − offset
        _, y = resample_linear(resp_t, resp_v, t0 - resp_offset, t1 - resp_offset)
        y = y[:len(x)]
    sp = welch(x, FS, nperseg, y)
    lo, hi = max(0.0, resp_freq - half_width), resp_freq + half_width
    coh = value_at(sp.freqs, sp.coherence, resp_freq) if sp.coherence is not None else None
    return BandResult(
        lf=band_power(sp, *LF_BAND), hf_fixed=band_power(sp, *HF_BAND),
        resp_centered=band_power(sp, lo, hi), resp_band=(lo, hi), resp_freq=resp_freq,
        coherence_at_resp=coh, n_segments=sp.n_segments, nperseg=sp.nperseg, df=sp.df,
        duration_s=len(x) / FS, rr_peak_freq=peak_freq(sp, 0.04, 0.40),
        coherence_bias=(1.0 / sp.n_segments) if sp.coherence is not None else None,
        hf_n_bins=sum(1 for f in sp.freqs if HF_BAND[0] <= f < HF_BAND[1]))


def suggest_offset(rr_t: Sequence[float], rr_ms: Sequence[float], resp_t: Sequence[float],
                   resp_v: Sequence[float], t0: float, t1: float, max_lag_s: float = 5.0) -> Tuple[float, float]:
    """교차상관이 최대(절대값)인 지연(초)과 그 상관계수. (표시만 — 적용은 --resp-offset 으로.)
    양수 = 호흡 시각에 이만큼 더해야 RR 과 맞는다는 뜻."""
    _, x = resample_linear(rr_t, rr_ms, t0, t1)
    _, y = resample_linear(resp_t, resp_v, t0, t1)
    n = min(len(x), len(y))
    x, y = x[:n], y[:n]
    mx, my = sum(x) / n, sum(y) / n
    x = [v - mx for v in x]
    y = [v - my for v in y]
    sx = math.sqrt(sum(v * v for v in x)) or 1.0
    sy = math.sqrt(sum(v * v for v in y)) or 1.0
    best_lag, best_r = 0.0, 0.0
    max_k = int(max_lag_s * FS)
    for k in range(-max_k, max_k + 1):
        # y 를 k 샘플 뒤로 밀었을 때 (resp shifted by +k/FS) 의 상관
        if k >= 0:
            s = sum(x[i + k] * y[i] for i in range(n - k))
        else:
            s = sum(x[i] * y[i - k] for i in range(n + k))
        r = s / (sx * sy)
        if abs(r) > abs(best_r):
            best_r, best_lag = r, k / FS
    return best_lag, best_r
