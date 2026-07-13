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
    "AnalysisResult",
    "analyze",
]


@dataclass
class BandPower:
    name: str
    lo: float
    hi: float
    absolute: float          # µV²
    relative: float          # fraction of analysis-band total power (0..1)


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

    def power_by_name(self) -> Dict[str, float]:
        return {bp.name: bp.absolute for bp in self.band_powers}


@dataclass
class EpochResult:
    index: int
    start_sec: float
    end_sec: float
    spectrum: Spectrum


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


def _default_nperseg(fs: float, n: int) -> int:
    """Default Welch segment: ~4 s of data, capped at the signal length, >= 2."""
    target = int(round(4.0 * fs))
    if target < 2:
        target = 2
    return max(2, min(target, n))


def _spectrum(values: Sequence[float], fs: float,
              bands: Sequence[Tuple[str, float, float]],
              nperseg: int, noverlap: Optional[int], sef_frac: float,
              ) -> Tuple[Spectrum, Dict[str, int]]:
    freqs, psd, meta = spectral.welch_psd(values, fs, nperseg=nperseg,
                                          noverlap=noverlap)
    band_lo = min(b[1] for b in bands)
    band_hi = min(max(b[2] for b in bands), freqs[-1])
    total = spectral.total_power(freqs, psd, band_lo, band_hi)

    bps: List[BandPower] = []
    for name, lo, hi in bands:
        absv = spectral.integrate_psd(freqs, psd, lo, hi)
        rel = (absv / total) if total > 0 else 0.0
        bps.append(BandPower(name, lo, hi, absv, rel))

    power_by_name = {bp.name: bp.absolute for bp in bps}
    dominant = None
    if bps and total > 0:
        dominant = max(bps, key=lambda bp: bp.absolute).name

    peak = spectral.peak_frequency(freqs, psd, band_lo, band_hi)
    sef = spectral.spectral_edge_frequency(freqs, psd, band_lo, band_hi, sef_frac)
    ratios = spectral.band_ratios(power_by_name)

    swa_abs = power_by_name.get("delta", 0.0)
    swa_rel = (swa_abs / total) if total > 0 else 0.0

    spec = Spectrum(band_powers=bps, total_power=total, peak_freq=peak, sef=sef,
                    sef_frac=sef_frac, ratios=ratios, dominant=dominant,
                    swa_abs=swa_abs, swa_rel=swa_rel, band_lo=band_lo,
                    band_hi=band_hi)
    return spec, meta


def analyze(values: Sequence[float], fs: float = 128.0,
            bands: Optional[Sequence[Tuple[str, float, float]]] = None,
            nperseg: Optional[int] = None, noverlap: Optional[int] = None,
            sef_frac: float = 0.95, epoch_sec: Optional[float] = None,
            times: Optional[Sequence[float]] = None,
            fs_source: str = "user", warnings: Optional[List[str]] = None,
            ) -> AnalysisResult:
    """Run the full band-power analysis. See module docstring for what it computes."""
    vals = [float(v) for v in values]
    n = len(vals)
    warns: List[str] = list(warnings) if warnings else []

    if n == 0:
        raise ValueError("signal is empty")
    if fs <= 0:
        raise ValueError("fs must be positive")
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

    overall, meta = _spectrum(vals, fs, band_list, resolved_nperseg, noverlap,
                              sef_frac)

    result = AnalysisResult(
        fs=fs, fs_source=fs_source, n_samples=n, duration_sec=n / fs,
        nperseg=meta["nperseg"], noverlap=meta["noverlap"], nfft=meta["nfft"],
        bands=band_list, overall=overall, warnings=warns)

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
                                min(resolved_nperseg, n), noverlap, sef_frac)
            result.epochs = [EpochResult(0, 0.0, n / fs, spec)]
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
                                    sef_frac)
                epochs.append(EpochResult(
                    e, e * epoch_len / fs, (e + 1) * epoch_len / fs, spec))
            result.epochs = epochs

        if result.epochs:
            delta_dom = sum(1 for ep in result.epochs
                            if ep.spectrum.dominant == "delta")
            result.swa_density = delta_dom / len(result.epochs)

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
