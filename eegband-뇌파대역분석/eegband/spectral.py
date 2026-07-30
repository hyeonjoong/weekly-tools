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
    "peak_frequency_prominent",
    "spectral_edge_frequency",
    "spectral_entropy",
    "band_ratios",
    "median_bias",
]

# Standard EEG bands (name, low_hz, high_hz). Delta's lower edge is 0.5 Hz (SWA).
DEFAULT_BANDS: List[Tuple[str, float, float]] = [
    ("delta", 0.5, 4.0),
    ("theta", 4.0, 8.0),
    ("alpha", 8.0, 13.0),
    ("beta", 13.0, 30.0),
    ("gamma", 30.0, 45.0),
]


_DETREND_MODES = ("constant", "linear", "none")


def median_bias(n: int) -> float:
    """Bias-correction divisor for the median of ``n`` periodograms.

    The median of ``n`` iid exponential (χ²₂/2) periodogram estimates is a biased
    estimator of the mean; dividing by this factor makes it unbiased. Matches
    ``scipy.signal._spectral_py._median_bias`` exactly so ``average='median'`` agrees
    with SciPy.
    """
    if n <= 0:
        raise ValueError("n must be >= 1")
    bias = 1.0
    ii_2 = 2  # 2*1
    for _ in range(1, (n - 1) // 2 + 1):
        bias += 1.0 / (ii_2 + 1) - 1.0 / ii_2
        ii_2 += 2
    return bias


def _detrend_segment(seg: List[float], mode: str) -> List[float]:
    """Remove a per-segment trend: 'constant' (mean), 'linear' (least-squares line),
    or 'none'. Linear detrend matches scipy.signal.detrend(type='linear')."""
    if mode == "none":
        return seg
    m = len(seg)
    if mode == "constant" or m < 2:
        mean = math.fsum(seg) / m
        return [v - mean for v in seg]
    # Linear least-squares fit y = a + b·t over t = 0..m-1, then subtract it.
    # Closed form using integer sums of t and t²; robust and dependency-free.
    t_mean = (m - 1) / 2.0
    sxx = math.fsum((t - t_mean) ** 2 for t in range(m))
    y_mean = math.fsum(seg) / m
    sxy = math.fsum((t - t_mean) * (seg[t] - y_mean) for t in range(m))
    b = sxy / sxx if sxx > 0 else 0.0
    a = y_mean - b * t_mean
    return [seg[t] - (a + b * t) for t in range(m)]


def welch_psd(x: Sequence[float], fs: float, nperseg: Optional[int] = None,
              noverlap: Optional[int] = None, detrend: str = "constant",
              average: str = "mean",
              ) -> Tuple[List[float], List[float], Dict[str, int]]:
    """Estimate the one-sided PSD (µV²/Hz) of ``x`` via Welch's method.

    Returns (freqs, psd, meta) where meta has the resolved nperseg/noverlap/nfft/n_seg.
    Matches scipy.signal.welch(x, fs, window='hann', nperseg, noverlap, nfft=next_pow2,
    detrend=detrend, scaling='density', average=average).

    detrend : 'constant' (per-segment mean removal, default), 'linear' (per-segment
        least-squares line removal — strips slow drift so it does not leak into
        delta), or 'none'.
    average : 'mean' (default) or 'median' (robust to transient artifacts; a
        bias-correction divisor keeps it unbiased for the true PSD).
    """
    xs = [float(v) for v in x]
    n = len(xs)
    if n == 0:
        raise ValueError("cannot compute a spectrum of an empty signal")
    if fs <= 0:
        raise ValueError("sampling rate fs must be positive")
    if detrend not in _DETREND_MODES:
        raise ValueError(f"detrend must be one of {_DETREND_MODES}, got {detrend!r}")
    if average not in ("mean", "median"):
        raise ValueError(f"average must be 'mean' or 'median', got {average!r}")

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

    scale = 1.0 / (fs * u_pow)
    freqs = [k * fs / nfft for k in range(n_out)]
    nyq = (nfft % 2 == 0)

    # Per-segment one-sided scaled periodograms, so 'median' averaging can operate
    # on the fully-scaled estimates exactly as SciPy does. For 'mean' only a running
    # sum is needed, so the per-segment rows are NOT retained — with a large overlap
    # (noverlap = nperseg-1 gives n_seg ≈ N) keeping them all would cost O(N·nfft)
    # floats and exhaust memory on a long recording.
    keep_rows = (average == "median")
    segs_psd: List[List[float]] = []
    acc = [0.0] * n_out
    n_seg = 0
    for s in starts:
        seg = xs[s:s + nperseg]
        seg = _detrend_segment(seg, detrend)
        wseg = [seg[i] * win[i] for i in range(nperseg)]
        padded = [complex(v, 0.0) for v in wseg] + [0j] * (nfft - nperseg)
        spec = fft(padded)
        row = [0.0] * n_out
        for k in range(n_out):
            re = spec[k].real
            im = spec[k].imag
            val = (re * re + im * im) * scale
            if k != 0 and not (nyq and k == n_out - 1):
                val *= 2.0
            row[k] = val
        n_seg += 1
        if keep_rows:
            segs_psd.append(row)
        else:
            for k in range(n_out):
                acc[k] += row[k]

    psd: List[float] = [0.0] * n_out
    if not keep_rows:
        psd = [v / n_seg for v in acc]
    else:  # median with bias correction
        bias = median_bias(n_seg)
        for k in range(n_out):
            col = sorted(row[k] for row in segs_psd)
            mid = len(col) // 2
            med = col[mid] if len(col) % 2 else 0.5 * (col[mid - 1] + col[mid])
            psd[k] = med / bias

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


# A genuine oscillatory peak must rise this many times above the in-band median PSD.
# Empirically real EEG peaks (alpha rhythm, sleep spindle) sit 20–2500× the band
# median, while the argmax of a peakless noisy band stays < 2×; 3× separates them.
_MIN_PROMINENCE_RATIO = 3.0


def peak_frequency_prominent(freqs: Sequence[float], psd: Sequence[float],
                             lo: float, hi: float,
                             min_ratio: float = _MIN_PROMINENCE_RATIO,
                             ) -> Tuple[Optional[float], bool]:
    """Return (argmax frequency in [lo, hi], is_prominent).

    ``is_prominent`` is True only when the in-band maximum is a genuine spectral
    hump rather than the argmax of noise on a 1/f slope. Three conditions: the peak
    is strictly interior to the band (not pinned at an edge), it exceeds the PSD at
    *both* band edges, and it rises at least ``min_ratio``× above the in-band median
    PSD. This distinguishes a real Individual Alpha Frequency / spindle from a
    peakless band. Callers suppress a spurious "peak" when this is False.
    """
    idx = [i for i, f in enumerate(freqs) if lo <= f <= hi]
    if not idx:
        return None, False
    best = max(idx, key=lambda i: psd[i])
    peak_f = freqs[best]
    first, last = idx[0], idx[-1]
    interior = first < best < last
    band_vals = sorted(psd[i] for i in idx)
    m = len(band_vals)
    median = (band_vals[m // 2] if m % 2
              else 0.5 * (band_vals[m // 2 - 1] + band_vals[m // 2]))
    ratio_ok = median > 0 and psd[best] >= min_ratio * median
    prominent = (interior and psd[best] > psd[first] and psd[best] > psd[last]
                 and psd[best] > 0.0 and ratio_ok)
    return peak_f, prominent


def spectral_edge_frequency(freqs: Sequence[float], psd: Sequence[float],
                            lo: float, hi: float, frac: float = 0.95,
                            ) -> Optional[float]:
    """Spectral edge frequency: the frequency below which ``frac`` of the power in
    [lo, hi] accumulates (SEF95 for frac=0.95). None if the band carries no power.

    The crossing is solved *exactly* within the trapezoid segment that contains it:
    the PSD is piecewise-linear, so cumulative power is piecewise-quadratic and the
    edge is the root of that quadratic (not a linear-in-cumulative approximation).
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
            # Solve for u in [0, w] with cumulative-power(u) = remaining, where the
            # PSD over this segment is y0 + (m)·u:  0.5·m·u² + y0·u = remaining.
            w = xs[i] - xs[i - 1]
            if w <= 0:
                return xs[i]
            y0, y1 = ys[i - 1], ys[i]
            remaining = target - cum[i - 1]
            if remaining <= 0:
                return xs[i - 1]
            m = (y1 - y0) / w
            if abs(m) < 1e-15:                     # flat segment -> linear
                u = remaining / y0 if y0 > 0 else w
            else:
                disc = y0 * y0 + 2.0 * m * remaining
                u = (-y0 + math.sqrt(disc if disc > 0 else 0.0)) / m
            if u < 0.0:
                u = 0.0
            elif u > w:
                u = w
            return xs[i - 1] + u
    return xs[-1]


def spectral_entropy(freqs: Sequence[float], psd: Sequence[float],
                     lo: float, hi: float, normalize: bool = True
                     ) -> Optional[float]:
    """Shannon spectral entropy of the PSD bins in [lo, hi].

    The PSD bins inside the band are normalized to a probability distribution
    ``p_k = psd_k / Σ psd`` and H = −Σ p_k ln p_k is returned (empty bins contribute
    0, since p·ln p → 0). With ``normalize=True`` (default) H is divided by ln(M)
    where M is the *total* number of frequency bins in [lo, hi], giving a value in
    [0, 1]: ~1 for a flat (white) spectrum, near 0 for a single dominant rhythm. A
    spectral flatness / complexity index. Returns None when the band spans < 2 bins
    or carries no power.
    """
    in_band = [p for f, p in zip(freqs, psd) if lo <= f <= hi]
    m = len(in_band)              # total bins in band (denominator uses ln(m))
    if m < 2:
        return None
    ps = [p for p in in_band if p > 0.0]
    total = math.fsum(ps)
    if total <= 0 or not math.isfinite(total):
        # An overflowed (inf) total would make every q underflow to 0 and log(0) raise.
        return None
    h = 0.0
    for p in ps:
        q = p / total
        if q <= 0.0:          # underflow for an astronomically dominant bin
            continue
        h -= q * math.log(q)
    if normalize:
        denom = math.log(m)
        return h / denom if denom > 0 else 0.0
    return h


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
