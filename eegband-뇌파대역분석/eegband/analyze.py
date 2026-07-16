"""Orchestration: turn a single-channel EEG signal into a band-power report.

Given the samples and a sampling rate, this computes a Welch PSD and derives the
standard clinical/sleep features: absolute & relative band power (delta/theta/alpha/
beta/gamma), slow-wave activity (SWA = delta power, the key sleep endpoint),
spectral edge frequency, peak frequency, total power, and band ratios. Optionally it
windows the signal into fixed epochs and reports per-epoch features plus a summary,
including an SWA density (fraction of epochs where delta dominates).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import spectral
from .dataio import infer_fs

__all__ = [
    "BandPower",
    "Spectrum",
    "EpochResult",
    "SignalQuality",
    "AnalysisResult",
    "analyze",
    "signal_quality",
]


@dataclass
class BandPower:
    name: str
    lo: float
    hi: float
    absolute: float          # µV²
    relative: float          # fraction of analysis-band total power (0..1)
    peak_freq: Optional[float] = None  # frequency of max PSD within this band (Hz)
    peak_prominent: bool = False       # True if that peak is a genuine in-band hump


@dataclass
class Spectrum:
    band_powers: List[BandPower]
    total_power: float       # integrated over [band_lo, band_hi], µV²
    peak_freq: Optional[float]
    sef: Optional[float]
    sef_frac: float
    ratios: Dict[str, float]
    dominant: Optional[str]  # name of the band with the most absolute power
    swa_abs: float           # delta absolute power (µV²)
    swa_rel: float           # delta relative power (0..1)
    band_lo: float
    band_hi: float
    rel_sum: float = 1.0     # Σ band relatives; <1 gap / >1 overlap in custom bands
    dominant_tie: bool = False  # top-two bands within 1% -> dominance is ambiguous
    entropy: Optional[float] = None  # normalized Shannon spectral entropy (0..1)

    def power_by_name(self) -> Dict[str, float]:
        return {bp.name: bp.absolute for bp in self.band_powers}


@dataclass
class EpochResult:
    index: int
    start_sec: float
    end_sec: float
    spectrum: Spectrum
    peak_amp: float = 0.0             # max |amplitude| in the epoch (µV)
    rejected: bool = False            # excluded from the SWA summary as artifact
    reject_reason: Optional[str] = None


@dataclass
class SignalQuality:
    """Time-domain quality/artifact metrics of the raw signal (µV)."""
    n_samples: int
    n_interpolated: int          # non-finite/blank cells that were interpolated
    frac_interpolated: float     # n_interpolated / n_samples
    v_min: float
    v_max: float
    mean: float
    rms: float                   # root-mean-square amplitude (µV)
    ptp: float                   # peak-to-peak (v_max - v_min)
    n_clipped: int               # samples pinned at the global min or max (saturation)
    frac_clipped: float
    n_flat: int                  # samples inside a run of >= 3 identical values
    frac_flat: float
    flags: List[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    fs: float
    fs_source: str           # "user", "inferred", or "inferred (user mismatch)"
    n_samples: int
    duration_sec: float
    nperseg: int
    noverlap: int
    nfft: int
    bands: List[Tuple[str, float, float]]
    overall: Spectrum
    epoch_sec: Optional[float] = None
    epochs: List[EpochResult] = field(default_factory=list)
    swa_density: Optional[float] = None   # fraction of epochs that are delta-dominant
    warnings: List[str] = field(default_factory=list)
    source_file: Optional[str] = None     # input path, echoed for provenance
    sef_frac: float = 0.95                # SEF percentile used (for provenance)
    n_filled: int = 0                     # interpolated samples in the value column
    input_encoding: Optional[str] = None  # codec the CSV was decoded with
    detrend: str = "constant"             # per-segment detrend mode used
    average: str = "mean"                 # Welch segment averaging (mean/median)
    quality: Optional[SignalQuality] = None  # time-domain artifact metrics
    max_amp: Optional[float] = None       # amplitude artifact-rejection threshold (µV)
    n_epochs_kept: int = 0                # epochs used in the SWA summary
    n_epochs_rejected: int = 0            # epochs excluded as artifact


def signal_quality(values: Sequence[float], n_interpolated: int = 0,
                   ) -> SignalQuality:
    """Compute time-domain quality/artifact metrics for a raw signal.

    Detects two common clinical-recording problems that spectral summaries hide:
      * clipping/saturation — samples pinned at the recording's min or max rail,
      * flat-lining — runs of >= 3 identical samples (disconnected lead / dropout).
    Plus amplitude range, RMS and the fraction of samples that had to be
    interpolated. Purely descriptive; it never alters the analysis.
    """
    vals = [float(v) for v in values]
    n = len(vals)
    if n == 0:
        raise ValueError("cannot assess quality of an empty signal")
    v_min = min(vals)
    v_max = max(vals)
    mean = math.fsum(vals) / n
    rms = math.sqrt(math.fsum(v * v for v in vals) / n)
    ptp = v_max - v_min

    # Clipping/saturation: samples pinned at a rail. Real clipping means the rail
    # value REPEATS (an ADC railed for several samples); the lone global min and max
    # of any continuous trace are not clipping, so a rail counts only when it occurs
    # >= 2 times. A constant signal is flat, not clipped.
    if ptp > 0:
        n_min = sum(1 for v in vals if v == v_min)
        n_max = sum(1 for v in vals if v == v_max)
        n_clipped = (n_min if n_min >= 2 else 0) + (n_max if n_max >= 2 else 0)
    else:
        n_clipped = 0

    # Flat runs: maximal runs of identical consecutive values with length >= 3.
    n_flat = 0
    i = 0
    while i < n:
        j = i + 1
        while j < n and vals[j] == vals[i]:
            j += 1
        run = j - i
        if run >= 3:
            n_flat += run
        i = j

    flags: List[str] = []
    frac_interp = n_interpolated / n
    frac_clipped = n_clipped / n
    frac_flat = n_flat / n
    if ptp == 0:
        flags.append("신호가 완전히 상수(분산 0)입니다 / signal is constant.")
    if frac_interp > 0.10:
        flags.append(
            f"보간된 표본이 {frac_interp * 100:.1f}%로 많습니다 "
            "(>10%); 결과 신뢰도에 주의 / heavy interpolation.")
    if ptp > 0 and frac_clipped > 0.02:
        flags.append(
            f"레일(min/max)에 고정된 표본이 {frac_clipped * 100:.1f}% — "
            "ADC 포화/클리핑 가능 / possible clipping.")
    if frac_flat > 0.05:
        flags.append(
            f"평탄 구간(연속 동일값)이 {frac_flat * 100:.1f}% — "
            "리드 탈락/드롭아웃 가능 / flat-lining.")
    return SignalQuality(
        n_samples=n, n_interpolated=n_interpolated, frac_interpolated=frac_interp,
        v_min=v_min, v_max=v_max, mean=mean, rms=rms, ptp=ptp,
        n_clipped=n_clipped, frac_clipped=frac_clipped,
        n_flat=n_flat, frac_flat=frac_flat, flags=flags)


def _default_nperseg(fs: float, n: int) -> int:
    """Default Welch segment: ~4 s of data, capped at the signal length, >= 2."""
    target = int(round(4.0 * fs))
    if target < 2:
        target = 2
    return max(2, min(target, n))


def _apply_reject(ep: "EpochResult", seg: Sequence[float],
                  max_amp: Optional[float]) -> None:
    """Record an epoch's peak |amplitude| and, if ``max_amp`` is set and exceeded,
    mark it rejected so it is dropped from the SWA endpoint summary."""
    peak = max((abs(float(v)) for v in seg), default=0.0)
    ep.peak_amp = peak
    if max_amp is not None and peak > max_amp:
        ep.rejected = True
        ep.reject_reason = f"|amp| {peak:.1f} > {max_amp:g} µV"


def _spectrum(values: Sequence[float], fs: float,
              bands: Sequence[Tuple[str, float, float]],
              nperseg: int, noverlap: Optional[int], sef_frac: float,
              detrend: str = "constant", average: str = "mean",
              ) -> Tuple[Spectrum, Dict[str, int]]:
    freqs, psd, meta = spectral.welch_psd(values, fs, nperseg=nperseg,
                                          noverlap=noverlap, detrend=detrend,
                                          average=average)
    band_lo = min(b[1] for b in bands)
    band_hi = min(max(b[2] for b in bands), freqs[-1])
    total = spectral.total_power(freqs, psd, band_lo, band_hi)

    bps: List[BandPower] = []
    for name, lo, hi in bands:
        absv = spectral.integrate_psd(freqs, psd, lo, hi)
        rel = (absv / total) if total > 0 else 0.0
        # per-band peak (e.g. alpha peak = Individual Alpha Frequency) with a
        # prominence flag so a 1/f slope's argmax is not mistaken for a rhythm.
        if absv > 0:
            bpeak, bprom = spectral.peak_frequency_prominent(freqs, psd, lo, hi)
        else:
            bpeak, bprom = None, False
        bps.append(BandPower(name, lo, hi, absv, rel, peak_freq=bpeak,
                             peak_prominent=bprom))

    power_by_name = {bp.name: bp.absolute for bp in bps}
    dominant = None
    dominant_tie = False
    if bps and total > 0:
        ranked = sorted(bps, key=lambda bp: bp.absolute, reverse=True)
        dominant = ranked[0].name
        if len(ranked) > 1:
            top, second = ranked[0].absolute, ranked[1].absolute
            # ambiguous when the runner-up is within 1% of the leader
            dominant_tie = top > 0 and (top - second) <= 0.01 * top

    # peak/SEF are meaningless when the band carries no power (constant signal).
    peak = (spectral.peak_frequency(freqs, psd, band_lo, band_hi)
            if total > 0 else None)
    sef = spectral.spectral_edge_frequency(freqs, psd, band_lo, band_hi, sef_frac)
    ratios = spectral.band_ratios(power_by_name)

    swa_abs = power_by_name.get("delta", 0.0)
    swa_rel = (swa_abs / total) if total > 0 else 0.0
    rel_sum = math.fsum(bp.relative for bp in bps)
    entropy = (spectral.spectral_entropy(freqs, psd, band_lo, band_hi)
               if total > 0 else None)

    spec = Spectrum(band_powers=bps, total_power=total, peak_freq=peak, sef=sef,
                    sef_frac=sef_frac, ratios=ratios, dominant=dominant,
                    swa_abs=swa_abs, swa_rel=swa_rel, band_lo=band_lo,
                    band_hi=band_hi, rel_sum=rel_sum, dominant_tie=dominant_tie,
                    entropy=entropy)
    return spec, meta


def analyze(values: Sequence[float], fs: float = 128.0,
            bands: Optional[Sequence[Tuple[str, float, float]]] = None,
            nperseg: Optional[int] = None, noverlap: Optional[int] = None,
            sef_frac: float = 0.95, epoch_sec: Optional[float] = None,
            times: Optional[Sequence[float]] = None,
            fs_source: str = "user", warnings: Optional[List[str]] = None,
            detrend: str = "constant", average: str = "mean",
            n_filled: int = 0, max_amp: Optional[float] = None,
            ) -> AnalysisResult:
    """Run the full band-power analysis. See module docstring for what it computes."""
    vals = [float(v) for v in values]
    n = len(vals)
    warns: List[str] = list(warnings) if warnings else []

    if n == 0:
        raise ValueError("signal is empty")
    if not all(math.isfinite(v) for v in vals):
        # load_signal interpolates NaN/blank cells; a direct library caller that
        # passes non-finite values would otherwise get a silently all-NaN spectrum.
        raise ValueError(
            "signal contains non-finite (NaN/inf) values; clean them first "
            "(load_signal interpolates blank/NA cells) before calling analyze().")
    if fs <= 0:
        raise ValueError("fs must be positive")
    if detrend not in spectral._DETREND_MODES:
        raise ValueError(f"detrend must be one of {spectral._DETREND_MODES}")
    if average not in ("mean", "median"):
        raise ValueError("average must be 'mean' or 'median'")
    band_list = list(bands) if bands else list(spectral.DEFAULT_BANDS)

    # Validate band edges against Nyquist.
    nyq = fs / 2.0
    max_edge = max(b[2] for b in band_list)
    if max_edge > nyq:
        warns.append(
            f"top band edge {max_edge:g} Hz exceeds Nyquist {nyq:g} Hz "
            f"(fs={fs:g}); frequencies above Nyquist are unavailable.")

    if n < 2:
        raise ValueError("need at least 2 samples to compute a spectrum")

    # Constant / zero-variance guard.
    if max(vals) == min(vals):
        warns.append("signal is constant (zero variance) — all band powers are 0.")

    resolved_nperseg = int(nperseg) if nperseg else _default_nperseg(fs, n)
    resolved_nperseg = max(2, min(resolved_nperseg, n))

    if detrend == "linear" and resolved_nperseg <= 2:
        warns.append(
            f"linear detrend with nperseg={resolved_nperseg} removes all degrees of "
            "freedom (a 2-point segment fits a line exactly) — the PSD will be ~0. "
            "Use a longer segment or detrend='constant'.")

    overall, meta = _spectrum(vals, fs, band_list, resolved_nperseg, noverlap,
                              sef_frac, detrend, average)

    # Relative powers only sum to 100% when the bands tile the analysis span with
    # no gaps or overlaps (true for the defaults). Flag custom bands that don't.
    if overall.total_power > 0 and abs(overall.rel_sum - 1.0) > 0.01:
        if overall.rel_sum < 1.0:
            warns.append(
                f"bands leave gaps: they cover {overall.rel_sum * 100:.1f}% of the "
                f"{overall.band_lo:g}–{overall.band_hi:g} Hz analysis span, so band "
                "relatives do not sum to 100%.")
        else:
            warns.append(
                f"bands overlap: they sum to {overall.rel_sum * 100:.1f}% of the "
                f"{overall.band_lo:g}–{overall.band_hi:g} Hz analysis span "
                "(relatives exceed 100%).")

    result = AnalysisResult(
        fs=fs, fs_source=fs_source, n_samples=n, duration_sec=n / fs,
        nperseg=meta["nperseg"], noverlap=meta["noverlap"], nfft=meta["nfft"],
        bands=band_list, overall=overall, warnings=warns, sef_frac=sef_frac,
        detrend=detrend, average=average, n_filled=n_filled,
        quality=signal_quality(vals, n_interpolated=n_filled),
        max_amp=max_amp)

    if epoch_sec is not None:
        result.epoch_sec = epoch_sec
        epoch_len = int(round(epoch_sec * fs))
        if epoch_len < 2:
            warns.append(f"epoch {epoch_sec:g}s is too short at fs={fs:g}; "
                         "skipping per-epoch analysis.")
        elif epoch_len >= n:
            warns.append(
                f"epoch {epoch_sec:g}s ({epoch_len} samples) is >= signal length "
                f"({n} samples); using the whole signal as a single epoch.")
            spec, _ = _spectrum(vals, fs, band_list,
                                min(resolved_nperseg, n), noverlap, sef_frac,
                                detrend, average)
            ep = EpochResult(0, 0.0, n / fs, spec)
            _apply_reject(ep, vals, max_amp)
            result.epochs = [ep]
        else:
            n_epochs = n // epoch_len
            leftover = n - n_epochs * epoch_len
            if leftover:
                warns.append(
                    f"{leftover} trailing sample(s) ({leftover / fs:.2f}s) do not "
                    f"fill a {epoch_sec:g}s epoch and were dropped from per-epoch "
                    "analysis.")
            epochs: List[EpochResult] = []
            ep_nperseg = max(2, min(resolved_nperseg, epoch_len))
            for e in range(n_epochs):
                seg = vals[e * epoch_len:(e + 1) * epoch_len]
                spec, _ = _spectrum(seg, fs, band_list, ep_nperseg, noverlap,
                                    sef_frac, detrend, average)
                ep = EpochResult(
                    e, e * epoch_len / fs, (e + 1) * epoch_len / fs, spec)
                _apply_reject(ep, seg, max_amp)
                epochs.append(ep)
            result.epochs = epochs

        if result.epochs:
            kept = [ep for ep in result.epochs if not ep.rejected]
            result.n_epochs_kept = len(kept)
            result.n_epochs_rejected = len(result.epochs) - len(kept)
            if max_amp is not None and result.n_epochs_rejected:
                warns.append(
                    f"artifact rejection: {result.n_epochs_rejected} of "
                    f"{len(result.epochs)} epochs exceeded |amp| > {max_amp:g} µV "
                    "and were excluded from the SWA summary.")
            # SWA density and the endpoint summary use KEPT epochs only.
            summ = kept if kept else result.epochs
            if not kept and max_amp is not None:
                warns.append(
                    "all epochs were rejected as artifact; SWA summary falls back "
                    "to all epochs — loosen --max-amp.")
            delta_dom = sum(1 for ep in summ if ep.spectrum.dominant == "delta")
            result.swa_density = delta_dom / len(summ) if summ else None

    result.warnings = warns
    return result


def resolve_fs(user_fs: float, times: Optional[Sequence[float]]
               ) -> Tuple[float, str, List[str]]:
    """Decide which sampling rate to use given --fs and an optional time column.

    Returns (fs, source, warnings). When a time column is present its inferred fs is
    used, and a warning is emitted if it disagrees with --fs by > 1%.
    """
    warns: List[str] = []
    if not times or len(times) < 2:
        return user_fs, "user", warns
    fs_inf, regular, _ = infer_fs(list(times))
    if not regular:
        warns.append(
            f"time column is irregularly sampled; inferred mean fs={fs_inf:.4g} Hz "
            "but Welch assumes uniform sampling — interpret with care.")
    source = "inferred"
    if user_fs and abs(fs_inf - user_fs) / user_fs > 0.01:
        warns.append(
            f"--fs {user_fs:g} Hz disagrees with fs inferred from the time column "
            f"({fs_inf:.4g} Hz); using the inferred value.")
        source = "inferred (user mismatch)"
    return fs_inf, source, warns
