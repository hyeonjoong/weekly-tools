"""Power spectral density (Welch) and band-power features — pure standard library.

The PSD follows the exact conventions of ``scipy.signal.welch`` with
``scaling='density'`` so that, assuming the input is in microvolts (µV), the PSD is
in µV²/Hz and integrating it over frequency recovers the signal variance
(Parseval). This lets the tool be validated against SciPy where available and, more
importantly, hand-checked against an analytic sinusoid.

Welch's method (구현 내용):
  1) 신호를 길이 nperseg 세그먼트로 자르고 50% 겹침(기본).
  2) 세그먼트마다 평균 제거(detrend='constant') 후 Hann 창(주기형) 적용.
  3) 창 세그먼트를 다음 2의 거듭제곱(nfft)까지 0-패딩하여 자체 FFT로 변환.
  4) 주기도를 |X|²/(fs·Σw²) 로 정규화, 단측화(×2, DC·Nyquist 제외), 세그먼트 평균.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from .fft import fft, hann_window, next_pow2

__all__ = [
    "DEFAULT_BANDS",
    "welch_psd",
    "integrate_psd",
    "band_powers",
    "total_power",
    "peak_frequency",
    "spectral_edge_frequency",
    "band_ratios",
]

# Standard EEG bands (name, low_hz, high_hz). Delta's lower edge is 0.5 Hz (SWA).
DEFAULT_BANDS: List[Tuple[str, float, float]] = [
    ("delta", 0.5, 4.0),
    ("theta", 4.0, 8.0),
    ("alpha", 8.0, 13.0),
    ("beta", 13.0, 30.0),
    ("gamma", 30.0, 45.0),
]


def welch_psd(x: Sequence[float], fs: float, nperseg: Optional[int] = None,
              noverlap: Optional[int] = None, detrend: str = "constant",
              ) -> Tuple[List[float], List[float], Dict[str, int]]:
    """Estimate the one-sided PSD (µV²/Hz) of ``x`` via Welch's method.

    Returns (freqs, psd, meta) where meta has the resolved nperseg/noverlap/nfft/n_seg.
    Matches scipy.signal.welch(x, fs, window='hann', nperseg, noverlap, nfft=next_pow2,
    detrend='constant', scaling='density', average='mean').
    """
    xs = [float(v) for v in x]
    n = len(xs)
    if n == 0:
        raise ValueError("cannot compute a spectrum of an empty signal")
    if fs <= 0:
        raise ValueError("sampling rate fs must be positive")

    if nperseg is None:
        nperseg = n
    nperseg = int(nperseg)
    if nperseg < 2:
        raise ValueError("nperseg must be >= 2")
    if nperseg > n:
        nperseg = n
    if nperseg < 2:
        raise ValueError("signal has fewer than 2 samples")

    if noverlap is None:
        noverlap = nperseg // 2
    noverlap = int(noverlap)
    if noverlap < 0 or noverlap >= nperseg:
        noverlap = nperseg // 2
    step = nperseg - noverlap

    win = hann_window(nperseg, periodic=True)
    u_pow = math.fsum(w * w for w in win)  # window power = Σ w²
    nfft = next_pow2(nperseg)
    n_out = nfft // 2 + 1

    starts = list(range(0, n - nperseg + 1, step))
    if not starts:
        starts = [0]

    acc = [0.0] * n_out
    for s in starts:
        seg = xs[s:s + nperseg]
        if detrend == "constant":
            m = math.fsum(seg) / len(seg)
            seg = [v - m for v in seg]
        wseg = [seg[i] * win[i] for i in range(nperseg)]
        padded = [complex(v, 0.0) for v in wseg] + [0j] * (nfft - nperseg)
        spec = fft(padded)
        for k in range(n_out):
            re = spec[k].real
            im = spec[k].imag
            acc[k] += re * re + im * im

    n_seg = len(starts)
    scale = 1.0 / (fs * u_pow)
    freqs = [k * fs / nfft for k in range(n_out)]
    psd: List[float] = []
    nyq = (nfft % 2 == 0)
    for k in range(n_out):
        val = (acc[k] / n_seg) * scale
        if k != 0 and not (nyq and k == n_out - 1):
            val *= 2.0
        psd.append(val)

    meta = {"nperseg": nperseg, "noverlap": noverlap, "nfft": nfft, "n_seg": n_seg}
    return freqs, psd, meta


def _interp_at(freqs: Sequence[float], psd: Sequence[float], f: float) -> float:
    """Linear interpolation of the PSD at an arbitrary frequency f."""
    if f <= freqs[0]:
        return psd[0]
    if f >= freqs[-1]:
        return psd[-1]
    lo, hi = 0, len(freqs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if freqs[mid] <= f:
            lo = mid
        else:
            hi = mid
    f0, f1 = freqs[lo], freqs[hi]
    if f1 == f0:
        return psd[lo]
    t = (f - f0) / (f1 - f0)
    return psd[lo] + (psd[hi] - psd[lo]) * t


def integrate_psd(freqs: Sequence[float], psd: Sequence[float],
                  lo: float, hi: float) -> float:
    """Trapezoidal integral of the PSD over [lo, hi], interpolating the band edges."""
    if not freqs:
        return 0.0
    lo = max(lo, freqs[0])
    hi = min(hi, freqs[-1])
    if hi <= lo:
        return 0.0
    xs = [lo]
    ys = [_interp_at(freqs, psd, lo)]
    for f, p in zip(freqs, psd):
        if lo < f < hi:
            xs.append(f)
            ys.append(p)
    xs.append(hi)
    ys.append(_interp_at(freqs, psd, hi))
    total = 0.0
    for i in range(1, len(xs)):
        total += 0.5 * (ys[i] + ys[i - 1]) * (xs[i] - xs[i - 1])
    return total


def band_powers(freqs: Sequence[float], psd: Sequence[float],
                bands: Sequence[Tuple[str, float, float]] = DEFAULT_BANDS,
                ) -> List[Tuple[str, float, float, float]]:
    """Absolute band powers as [(name, lo, hi, absolute_power_µV²), ...]."""
    return [(name, lo, hi, integrate_psd(freqs, psd, lo, hi))
            for name, lo, hi in bands]


def total_power(freqs: Sequence[float], psd: Sequence[float],
                lo: float, hi: float) -> float:
    """Integrated power over the whole analysis band [lo, hi]."""
    return integrate_psd(freqs, psd, lo, hi)


def peak_frequency(freqs: Sequence[float], psd: Sequence[float],
                   lo: float, hi: float) -> Optional[float]:
    """Frequency of maximum PSD within [lo, hi] (bin resolution). None if no bins."""
    best_f: Optional[float] = None
    best_p = -1.0
    for f, p in zip(freqs, psd):
        if lo <= f <= hi and p > best_p:
            best_p = p
            best_f = f
    return best_f


def spectral_edge_frequency(freqs: Sequence[float], psd: Sequence[float],
                            lo: float, hi: float, frac: float = 0.95,
                            ) -> Optional[float]:
    """Spectral edge frequency: the frequency below which ``frac`` of the power in
    [lo, hi] accumulates (SEF95 for frac=0.95). Linearly interpolated. None if the
    band carries no power.
    """
    lo = max(lo, freqs[0])
    hi = min(hi, freqs[-1])
    if hi <= lo:
        return None
    xs = [lo]
    ys = [_interp_at(freqs, psd, lo)]
    for f, p in zip(freqs, psd):
        if lo < f < hi:
            xs.append(f)
            ys.append(p)
    xs.append(hi)
    ys.append(_interp_at(freqs, psd, hi))

    cum = [0.0]
    for i in range(1, len(xs)):
        cum.append(cum[-1] + 0.5 * (ys[i] + ys[i - 1]) * (xs[i] - xs[i - 1]))
    total = cum[-1]
    if total <= 0:
        return None
    target = frac * total
    for i in range(1, len(cum)):
        if cum[i] >= target:
            # interpolate within segment [i-1, i]
            seg = cum[i] - cum[i - 1]
            if seg <= 0:
                return xs[i]
            t = (target - cum[i - 1]) / seg
            return xs[i - 1] + t * (xs[i] - xs[i - 1])
    return xs[-1]


def band_ratios(power_by_name: Dict[str, float]) -> Dict[str, float]:
    """Common clinical band ratios. Denominator of zero -> inf (or nan if 0/0)."""
    d = power_by_name.get("delta", 0.0)
    t = power_by_name.get("theta", 0.0)
    a = power_by_name.get("alpha", 0.0)
    b = power_by_name.get("beta", 0.0)

    def ratio(num: float, den: float) -> float:
        if den == 0:
            return float("nan") if num == 0 else float("inf")
        return num / den

    return {
        "theta/alpha": ratio(t, a),
        "delta/beta": ratio(d, b),
        "(delta+theta)/(alpha+beta)": ratio(d + t, a + b),  # slowing index
    }
