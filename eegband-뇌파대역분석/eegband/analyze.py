"""Orchestration: turn a single-channel EEG signal into a band-power report.

Given the samples and a sampling rate, this computes a Welch PSD and derives the
standard clinical/sleep features: absolute & relative band power (delta/theta/alpha/
beta/gamma), slow-wave activity (SWA = delta power, the key sleep endpoint),
spectral edge frequency, peak frequency, total power, and band ratios. It also
separates the broadband **aperiodic (1/f) background** from genuine oscillations, so
band powers can be reported with that background removed and its exponent tracked as
an endpoint in its own right. Optionally it windows the signal into fixed epochs and
reports per-epoch features plus a summary — including an SWA density (fraction of
epochs where delta dominates), autocorrelation-adjusted CIs, and a Mann–Kendall /
Theil–Sen trend across epochs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import aperiodic as ap
from . import spectral, stats
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
    # Aperiodic-adjusted ("oscillatory") quantities — None when no 1/f fit was made.
    osc_absolute: Optional[float] = None   # ∫ max(psd − 1/f fit, 0) over the band, µV²
    osc_relative: Optional[float] = None   # fraction of total oscillatory power
    adj_peak_freq: Optional[float] = None  # peak of the flattened spectrum (Hz)
    adj_peak_height: Optional[float] = None  # its height above background (log10)
    adj_peak_prominent: bool = False       # True if that flattened peak is genuine


@dataclass
class Spectrum:
    band_powers: List[BandPower]
    total_power: float       # integrated over [band_lo, band_hi], µV²
    peak_freq: Optional[float]
    sef: Optional[float]
    sef_frac: float
    ratios: Dict[str, float]
    dominant: Optional[str]  # name of the band with the most absolute power
    swa_abs: float           # slow-wave (delta) absolute power (µV²)
    swa_rel: float           # slow-wave (delta) relative power (0..1)
    band_lo: float
    band_hi: float
    swa_lo: Optional[float] = None   # actual SWA band edges used (None = undefined)
    swa_hi: Optional[float] = None
    swa_source: str = "delta"        # "delta" | "swa_band" | "undefined"
    rel_sum: float = 1.0     # Σ band relatives; <1 gap / >1 overlap in custom bands
    dominant_tie: bool = False  # top-two bands within 1% -> dominance is ambiguous
    entropy: Optional[float] = None  # normalized Shannon spectral entropy (0..1)
    aperiodic: Optional[ap.AperiodicFit] = None  # fitted 1/f background
    osc_total: Optional[float] = None  # total oscillatory power over the fit range, µV²
    # exponents of the lower/upper half of the fit range — a knee makes them differ
    aperiodic_halves: Optional[Tuple[float, float]] = None

    def power_by_name(self) -> Dict[str, float]:
        return {bp.name: bp.absolute for bp in self.band_powers}


@dataclass
class EpochResult:
    index: int
    start_sec: float
    end_sec: float
    spectrum: Spectrum
    peak_amp: float = 0.0             # max |amplitude| in the epoch (µV)
    max_grad: float = 0.0             # max |Δamplitude| between adjacent samples (µV)
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
    n_flat: int                  # samples inside a flat run at/over the run threshold
    frac_flat: float
    flags: List[str] = field(default_factory=list)
    quant_step: Optional[float] = None  # smallest non-zero |Δ| between samples (µV)
    n_levels: Optional[float] = None    # ptp / quant_step: amplitude resolution
    flat_run_min: int = 3               # run length that counted as "flat"
    longest_flat_run: int = 0


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
    max_grad: Optional[float] = None      # gradient artifact-rejection threshold (µV)
    n_epochs_kept: int = 0                # epochs used in the SWA summary
    n_epochs_rejected: int = 0            # epochs excluded as artifact
    aperiodic_mode: Optional[str] = None  # "robust"/"ols", or None when disabled
    fit_range: Optional[Tuple[float, float]] = None  # requested 1/f fit range (Hz)
    swa_band: Optional[Tuple[float, float]] = None   # explicit SWA band, if given
    n_seg: int = 0                        # Welch segments averaged (effective d.o.f.)
    qc_pass: bool = True                  # False when every epoch failed rejection
    start_offset_sec: float = 0.0         # --start offset of sample 0 in the source
    # The analysed samples, kept only so --psd-csv can re-derive the spectrum with the
    # exact parameters this result reports. Not serialised anywhere.
    samples: Optional[List[float]] = field(default=None, repr=False)
    # Per-endpoint summaries over the epochs that entered the summary (kept epochs).
    epoch_summary: Dict[str, Dict[str, float]] = field(default_factory=dict)
    epoch_trends: Dict[str, "stats.TrendResult"] = field(default_factory=dict)
    n_summary: int = 0                    # epochs behind epoch_summary/epoch_trends
    label: Optional[str] = None           # channel/series label (multi-channel input)

    def summary_epochs(self) -> List[EpochResult]:
        """Epochs behind the summary: kept ones, or all if every epoch was rejected."""
        kept = [ep for ep in self.epochs if not ep.rejected]
        return kept if kept else list(self.epochs)


def signal_quality(values: Sequence[float], n_interpolated: int = 0,
                   fs: Optional[float] = None) -> SignalQuality:
    """Compute time-domain quality/artifact metrics for a raw signal.

    Detects two common clinical-recording problems that spectral summaries hide:
      * clipping/saturation — samples pinned at the recording's min or max rail,
      * flat-lining — long runs of identical samples (disconnected lead / dropout).
    Plus amplitude range, RMS, the amplitude quantisation step and the fraction of
    samples that had to be interpolated. Purely descriptive; it never alters the
    analysis.

    The flat-run threshold scales with ``fs`` (0.1 s worth of samples, minimum 3): a
    **coarsely quantised** but perfectly healthy trace — e.g. an EDF whose physical
    range gives 1 µV steps, or an integer-rounded CSV export — necessarily repeats
    values for a few samples near every turning point, and a fixed 3-sample rule
    reports that as a 60% "lead disconnection". A real dropout lasts far longer than
    0.1 s, so the scaled threshold keeps the alarm meaningful.
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

    # Amplitude quantisation: the smallest non-zero step between adjacent samples.
    step: Optional[float] = None
    for i in range(1, n):
        d = abs(vals[i] - vals[i - 1])
        if d > 0 and (step is None or d < step):
            step = d
    n_levels = (ptp / step) if (step and step > 0) else None

    # Flat runs: maximal runs of identical consecutive values at/over the threshold.
    run_min = 3
    if fs and fs > 0:
        run_min = max(3, int(round(0.1 * fs)))
    n_flat = 0
    longest = 0
    i = 0
    while i < n:
        j = i + 1
        while j < n and vals[j] == vals[i]:
            j += 1
        run = j - i
        if run > longest:
            longest = run
        if run >= run_min:
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
            f"평탄 구간(≥{run_min}표본 연속 동일값)이 {frac_flat * 100:.1f}% — "
            "리드 탈락/드롭아웃 가능 / flat-lining.")
    if n_levels is not None and 0 < n_levels < 64:
        flags.append(
            f"진폭 해상도가 낮습니다: 양자화 간격 {step:.4g}, 전체 범위의 "
            f"{n_levels:.0f}단계 / coarse amplitude quantisation — high-frequency "
            "power is dominated by quantisation noise.")
    return SignalQuality(
        n_samples=n, n_interpolated=n_interpolated, frac_interpolated=frac_interp,
        v_min=v_min, v_max=v_max, mean=mean, rms=rms, ptp=ptp,
        n_clipped=n_clipped, frac_clipped=frac_clipped,
        n_flat=n_flat, frac_flat=frac_flat, flags=flags, quant_step=step,
        n_levels=n_levels, flat_run_min=run_min, longest_flat_run=longest)


def _next_pow2_len(nperseg: int) -> int:
    """The nfft welch_psd will use for this segment length (next power of two)."""
    from .fft import next_pow2
    return next_pow2(nperseg)


def _default_nperseg(fs: float, n: int) -> int:
    """Default Welch segment: ~4 s of data, capped at the signal length, >= 2."""
    target = int(round(4.0 * fs))
    if target < 2:
        target = 2
    return max(2, min(target, n))


def _apply_reject(ep: "EpochResult", seg: Sequence[float],
                  max_amp: Optional[float],
                  max_grad: Optional[float] = None) -> None:
    """Record an epoch's artifact metrics and mark it rejected when a threshold is
    exceeded, so it is dropped from the SWA endpoint summary.

    Two independent criteria, both standard in clinical EEG screening:
      * ``max_amp`` — peak |amplitude| (µV): movement/electrode-pop artifacts,
      * ``max_grad`` — largest step between adjacent samples (µV): sharp transients
        and digital glitches that a peak-amplitude test can miss when the offending
        spike stays inside the amplitude limit.
    """
    vals = [abs(float(v)) for v in seg]
    ep.peak_amp = max(vals, default=0.0)
    grad = 0.0
    for i in range(1, len(seg)):
        d = abs(float(seg[i]) - float(seg[i - 1]))
        if d > grad:
            grad = d
    ep.max_grad = grad
    reasons: List[str] = []
    if max_amp is not None and ep.peak_amp > max_amp:
        reasons.append(f"|amp| {ep.peak_amp:.1f} > {max_amp:g} µV")
    if max_grad is not None and grad > max_grad:
        reasons.append(f"|Δamp| {grad:.1f} > {max_grad:g} µV/sample")
    if reasons:
        ep.rejected = True
        ep.reject_reason = "; ".join(reasons)


def _spectrum(values: Sequence[float], fs: float,
              bands: Sequence[Tuple[str, float, float]],
              nperseg: int, noverlap: Optional[int], sef_frac: float,
              detrend: str = "constant", average: str = "mean",
              aperiodic_mode: Optional[str] = "robust",
              fit_range: Optional[Tuple[float, float]] = None,
              swa_band: Optional[Tuple[float, float]] = None,
              ) -> Tuple[Spectrum, Dict[str, int]]:
    freqs, psd, meta = spectral.welch_psd(values, fs, nperseg=nperseg,
                                          noverlap=noverlap, detrend=detrend,
                                          average=average)
    band_lo = min(b[1] for b in bands)
    band_hi = min(max(b[2] for b in bands), freqs[-1])
    total = spectral.total_power(freqs, psd, band_lo, band_hi)

    # ---- aperiodic (1/f) background -------------------------------------------
    fit = None
    res_f: List[float] = []
    res_p: List[float] = []
    flat_f: List[float] = []
    flat_v: List[float] = []
    halves = None
    if aperiodic_mode is not None and total > 0 and math.isfinite(total):
        f_lo, f_hi = (fit_range if fit_range is not None else (band_lo, band_hi))
        fit = ap.fit_aperiodic(freqs, psd, f_lo, f_hi, mode=aperiodic_mode)
        if fit is not None:
            res_f, res_p = ap.residual_psd(freqs, psd, fit)
            flat_f, flat_v = ap.flattened_log_spectrum(freqs, psd, fit)
            halves = ap.half_range_exponents(freqs, psd, fit, mode=aperiodic_mode)
    osc_total = None
    if fit is not None and res_f:
        osc_total = ap.oscillatory_power(res_f, res_p, res_f[0], res_f[-1])

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
        osc_abs = osc_rel = None
        apk = apk_h = None
        apk_prom = False
        if fit is not None:
            osc_abs = ap.oscillatory_power(res_f, res_p, lo, hi)
            if osc_abs is not None and osc_total is not None and osc_total > 0:
                osc_rel = osc_abs / osc_total
            apk, apk_h, apk_prom = ap.flattened_peak(flat_f, flat_v, lo, hi)
        bps.append(BandPower(name, lo, hi, absv, rel, peak_freq=bpeak,
                             peak_prominent=bprom, osc_absolute=osc_abs,
                             osc_relative=osc_rel, adj_peak_freq=apk,
                             adj_peak_height=apk_h, adj_peak_prominent=apk_prom))

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

    # Slow-wave activity: an explicit --swa-band wins; otherwise the band actually
    # named "delta" (whose edges may have been redefined by --bands); if neither
    # exists the endpoint is undefined and must be reported as such, never as 0.
    if swa_band is not None:
        swa_lo, swa_hi = swa_band
        swa_abs = spectral.integrate_psd(freqs, psd, swa_lo, swa_hi)
        swa_source = "swa_band"
    elif "delta" in power_by_name:
        delta_bp = next(bp for bp in bps if bp.name == "delta")
        swa_lo, swa_hi = delta_bp.lo, delta_bp.hi
        swa_abs = delta_bp.absolute
        swa_source = "delta"
    else:
        swa_lo = swa_hi = None
        swa_abs = 0.0
        swa_source = "undefined"
    swa_rel = (swa_abs / total) if total > 0 else 0.0
    rel_sum = math.fsum(bp.relative for bp in bps)
    entropy = (spectral.spectral_entropy(freqs, psd, band_lo, band_hi)
               if total > 0 else None)

    spec = Spectrum(band_powers=bps, total_power=total, peak_freq=peak, sef=sef,
                    sef_frac=sef_frac, ratios=ratios, dominant=dominant,
                    swa_abs=swa_abs, swa_rel=swa_rel, band_lo=band_lo,
                    band_hi=band_hi, rel_sum=rel_sum, dominant_tie=dominant_tie,
                    entropy=entropy, aperiodic=fit, osc_total=osc_total,
                    aperiodic_halves=halves,
                    swa_lo=swa_lo, swa_hi=swa_hi, swa_source=swa_source)
    return spec, meta


def analyze(values: Sequence[float], fs: float = 128.0,
            bands: Optional[Sequence[Tuple[str, float, float]]] = None,
            nperseg: Optional[int] = None, noverlap: Optional[int] = None,
            sef_frac: float = 0.95, epoch_sec: Optional[float] = None,
            times: Optional[Sequence[float]] = None,
            fs_source: str = "user", warnings: Optional[List[str]] = None,
            detrend: str = "constant", average: str = "mean",
            n_filled: int = 0, max_amp: Optional[float] = None,
            max_grad: Optional[float] = None,
            aperiodic_mode: Optional[str] = "robust",
            fit_range: Optional[Tuple[float, float]] = None,
            swa_band: Optional[Tuple[float, float]] = None,
            label: Optional[str] = None,
            ) -> AnalysisResult:
    """Run the full band-power analysis. See module docstring for what it computes.

    aperiodic_mode : ``"robust"`` (default) / ``"ols"`` fit the 1/f background and
        report background-corrected band power; ``None`` skips it entirely.
    fit_range : (lo, hi) Hz for that fit; defaults to the analysis band.
    swa_band : (lo, hi) Hz defining slow-wave activity explicitly. By default SWA is
        the band named ``delta``; with custom ``bands`` that have no such band the
        endpoint is reported as undefined rather than as 0.
    """
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
    if aperiodic_mode is not None and aperiodic_mode not in ap.FIT_MODES:
        raise ValueError(f"aperiodic_mode must be one of {ap.FIT_MODES} or None")
    if fit_range is not None:
        f_lo, f_hi = float(fit_range[0]), float(fit_range[1])
        if not (math.isfinite(f_lo) and math.isfinite(f_hi)):
            raise ValueError("fit_range edges must be finite")
        if f_lo <= 0:
            raise ValueError("fit_range lower edge must be > 0 Hz (log-log fit)")
        if f_hi <= f_lo:
            raise ValueError("fit_range upper edge must exceed the lower edge")
        fit_range = (f_lo, f_hi)
    if swa_band is not None:
        s_lo, s_hi = float(swa_band[0]), float(swa_band[1])
        if not (math.isfinite(s_lo) and math.isfinite(s_hi)):
            raise ValueError("swa_band edges must be finite")
        if s_lo < 0 or s_hi <= s_lo:
            raise ValueError("swa_band must be 0 <= lo < hi")
        swa_band = (s_lo, s_hi)
    band_list = list(bands) if bands else list(spectral.DEFAULT_BANDS)
    for name, lo, hi in band_list:
        if not (math.isfinite(lo) and math.isfinite(hi)):
            raise ValueError(f"band '{name}' has non-finite edges ({lo}, {hi})")
        if lo < 0 or hi <= lo:
            raise ValueError(f"band '{name}' must satisfy 0 <= lo < hi, got "
                             f"({lo}, {hi})")

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

    if nperseg is not None and int(nperseg) < 2:
        raise ValueError(
            f"nperseg must be >= 2 (got {int(nperseg)}); a 1- or 0-sample segment has "
            "no spectrum. Leave it unset for the ~4 s default.")
    requested_nperseg = int(nperseg) if nperseg else None
    resolved_nperseg = requested_nperseg if requested_nperseg else _default_nperseg(fs, n)
    resolved_nperseg = max(2, min(resolved_nperseg, n))
    if requested_nperseg is not None and resolved_nperseg != requested_nperseg:
        warns.append(
            f"nperseg={requested_nperseg} exceeds the signal length ({n} samples) and "
            f"was clamped to {resolved_nperseg}.")
    if noverlap is not None:
        nov = int(noverlap)
        if nov < 0 or nov >= resolved_nperseg:
            warns.append(
                f"noverlap={nov} is outside [0, nperseg-1={resolved_nperseg - 1}] and "
                f"was reset to the 50% default ({resolved_nperseg // 2}).")

    # Frequency resolution vs the narrowest band: a segment shorter than ~2 cycles of
    # the narrowest band cannot resolve it, and the band power is then dominated by
    # window leakage — silently, since the overall spectrum stays correct.
    df_seg = fs / _next_pow2_len(resolved_nperseg)
    narrowest = min(b[2] - b[1] for b in band_list)
    if df_seg > 0.5 * narrowest:
        warns.append(
            f"frequency resolution {df_seg:.3g} Hz (from nperseg={resolved_nperseg} at "
            f"fs={fs:g}) is coarser than half the narrowest band ({narrowest:g} Hz): "
            "band powers are severely biased by spectral leakage. Use a longer segment "
            "(or longer epochs).")

    if detrend == "linear" and resolved_nperseg <= 2:
        warns.append(
            f"linear detrend with nperseg={resolved_nperseg} removes all degrees of "
            "freedom (a 2-point segment fits a line exactly) — the PSD will be ~0. "
            "Use a longer segment or detrend='constant'.")

    overall, meta = _spectrum(vals, fs, band_list, resolved_nperseg, noverlap,
                              sef_frac, detrend, average, aperiodic_mode, fit_range,
                              swa_band)

    # A PSD that overflowed to inf/NaN (input amplitudes ≳1e155) would otherwise print
    # a confident "NaN µV²" with an empty warning list.
    if not math.isfinite(overall.total_power):
        warns.append(
            "the spectrum overflowed to inf/NaN — the input amplitudes are too large "
            "for double precision. Rescale the signal (e.g. work in mV) and note that "
            "band powers scale by the square of that factor.")

    # Bands that the 1/f fit range does not fully cover: their oscillatory (background
    # removed) power is not reported at all, rather than being computed over a
    # truncated span and labelled with the full band name.
    if aperiodic_mode is not None and overall.aperiodic is not None:
        uncovered = [bp.name for bp in overall.band_powers if bp.osc_absolute is None]
        if uncovered:
            f = overall.aperiodic
            warns.append(
                f"band(s) {', '.join(uncovered)} are not fully inside the 1/f fit "
                f"range ({f.fit_lo:.3g}–{f.fit_hi:.3g} Hz), so their oscillatory "
                "(background-removed) power is reported as n/a — widen --fit-range to "
                "get it. Raw band power is unaffected.")

    # Aperiodic-fit quality: an exponent from a badly-fitting 1/f model is not an
    # endpoint, so say so instead of printing a confident number.
    if aperiodic_mode is not None:
        fit_obj = overall.aperiodic
        if fit_obj is None and overall.total_power > 0:
            warns.append(
                "aperiodic 1/f fit unavailable: fewer than 3 usable frequency bins "
                "in the fit range (widen --fit-range or lengthen the segment).")
        elif fit_obj is not None:
            if fit_obj.n_total < 8:
                warns.append(
                    f"aperiodic 1/f fit uses only {fit_obj.n_total} frequency bins "
                    f"({fit_obj.fit_lo:.3g}–{fit_obj.fit_hi:.3g} Hz); the exponent is "
                    "poorly determined.")
            # A strong rhythm sitting at the bottom of the fit range anchors the
            # log-log line and inflates the exponent — the classic reason sleep
            # spectra need a fit range that starts above the slow oscillation.
            low_edge = fit_obj.fit_lo * 4.0   # lowest two octaves of the fit range
            if fit_obj.fit_hi > low_edge:
                anchored = [bp for bp in overall.band_powers
                            if bp.adj_peak_prominent and bp.adj_peak_freq is not None
                            and bp.adj_peak_freq <= low_edge]
                if anchored:
                    fr = ", ".join(f"{bp.adj_peak_freq:.3g} Hz" for bp in anchored)
                    warns.append(
                        f"a prominent oscillation ({fr}) sits in the lowest two "
                        f"octaves of the 1/f fit range ({fit_obj.fit_lo:.3g}–"
                        f"{fit_obj.fit_hi:.3g} Hz) and can inflate the exponent; for an "
                        "estimate comparable across recordings refit above it, e.g. "
                        "--fit-range 2-45 (or 30-45), and compare the two.")
            if fit_obj.r2 < 0.8:
                warns.append(
                    f"aperiodic 1/f fit is poor (R²={fit_obj.r2:.2f} over "
                    f"{fit_obj.fit_lo:.3g}–{fit_obj.fit_hi:.3g} Hz) — a single exponent "
                    "does not describe this spectrum (knee/line noise/filter roll-off"
                    "?); treat the exponent and the corrected band powers with care.")
            # A knee (bend in the log-log slope) can leave R² high — a textbook
            # 3rd-order knee still fits at R²≈0.92 — so compare the slope of the lower
            # and upper half of the fit range, which a bend separates.
            halves = overall.aperiodic_halves
            if halves is not None:
                lo_exp, hi_exp = halves
                if abs(hi_exp - lo_exp) > 0.75:
                    warns.append(
                        f"the 1/f slope is not constant across the fit range "
                        f"(χ={lo_exp:.2f} over the lower half vs {hi_exp:.2f} over the "
                        "upper half): the spectrum has a knee/bend, so the single "
                        "reported exponent is an average of the two and is not "
                        "comparable to a literature value fitted over a narrower "
                        "range. Refit with --fit-range on one side of the bend.")

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
        quality=signal_quality(vals, n_interpolated=n_filled, fs=fs),
        max_amp=max_amp, max_grad=max_grad, aperiodic_mode=aperiodic_mode,
        fit_range=fit_range, swa_band=swa_band, label=label,
        n_seg=meta["n_seg"], samples=vals)

    if epoch_sec is not None:
        if not math.isfinite(epoch_sec) or epoch_sec <= 0:
            raise ValueError(f"epoch_sec must be a positive finite number, got "
                             f"{epoch_sec!r}")
        result.epoch_sec = epoch_sec
        epoch_len = int(round(epoch_sec * fs))
        if epoch_len < 2:
            warns.append(f"epoch {epoch_sec:g}s is too short at fs={fs:g}; "
                         "skipping per-epoch analysis.")
        elif epoch_len >= n:
            warns.append(
                f"epoch {epoch_sec:g}s ({epoch_len:g} samples) is >= signal length "
                f"({n} samples); using the whole signal as a single epoch.")
            spec, _ = _spectrum(vals, fs, band_list,
                                min(resolved_nperseg, n), noverlap, sef_frac,
                                detrend, average, aperiodic_mode, fit_range,
                                swa_band)
            ep = EpochResult(0, 0.0, n / fs, spec)
            _apply_reject(ep, vals, max_amp, max_grad)
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
            df_ep = fs / _next_pow2_len(ep_nperseg)
            if df_ep > 0.5 * narrowest:
                warns.append(
                    f"per-epoch frequency resolution {df_ep:.3g} Hz (epoch "
                    f"{epoch_sec:g}s → nperseg={ep_nperseg}) is coarser than half the "
                    f"narrowest band ({narrowest:g} Hz): per-epoch band powers are "
                    "severely biased by leakage and will NOT match the whole-recording "
                    "values. Use longer epochs.")
            for e in range(n_epochs):
                seg = vals[e * epoch_len:(e + 1) * epoch_len]
                spec, _ = _spectrum(seg, fs, band_list, ep_nperseg, noverlap,
                                    sef_frac, detrend, average, aperiodic_mode,
                                    fit_range, swa_band)
                ep = EpochResult(
                    e, e * epoch_len / fs, (e + 1) * epoch_len / fs, spec)
                _apply_reject(ep, seg, max_amp, max_grad)
                epochs.append(ep)
            result.epochs = epochs

        if result.epochs:
            kept = [ep for ep in result.epochs if not ep.rejected]
            result.n_epochs_kept = len(kept)
            result.n_epochs_rejected = len(result.epochs) - len(kept)
            rejection_on = max_amp is not None or max_grad is not None
            if rejection_on and result.n_epochs_rejected:
                crit = []
                if max_amp is not None:
                    crit.append(f"|amp| > {max_amp:g} µV")
                if max_grad is not None:
                    crit.append(f"|Δamp| > {max_grad:g} µV/sample")
                warns.append(
                    f"artifact rejection: {result.n_epochs_rejected} of "
                    f"{len(result.epochs)} epochs exceeded {' or '.join(crit)} "
                    "and were excluded from the SWA summary.")
            # SWA density and the endpoint summary use KEPT epochs ONLY. When every
            # epoch is rejected there is nothing to summarise: falling back to all
            # epochs would make a recording that failed QC produce exactly the same
            # numbers as a clean one, silently.
            if not kept:
                warns.append(
                    f"QC FAILURE: all {len(result.epochs)} epochs were rejected as "
                    "artifact, so no endpoint summary, SWA density or trend is "
                    "reported. Loosen --max-amp/--max-grad or exclude this recording.")
                result.qc_pass = False
                result.swa_density = None
            else:
                delta_dom = sum(1 for ep in kept
                                if ep.spectrum.dominant == "delta")
                result.swa_density = delta_dom / len(kept)
                _summarize_epochs(result, kept, warns)

    result.warnings = warns
    return result


# Endpoints always summarised (and trend-tested) across epochs, in report order.
# Per-band absolute/relative power is appended to this list at runtime so a custom
# --bands set (e.g. a sigma/spindle band) gets the same treatment as delta.
_CORE_ENDPOINTS: List[Tuple[str, str]] = [
    ("swa_relative", "swa_rel"),
    ("swa_absolute_uv2", "swa_abs"),
    ("swa_absolute_log10", "_swa_log10"),
    ("total_power_uv2", "total_power"),
    ("aperiodic_exponent", "_exponent"),
    ("sef_hz", "sef"),
    ("spectral_entropy", "entropy"),
]


def _epoch_endpoints(bands: Sequence[Tuple[str, float, float]],
                     swa_defined: bool) -> List[Tuple[str, str]]:
    """The endpoint list for these bands: the core set plus per-band abs/rel power."""
    out = [(k, a) for k, a in _CORE_ENDPOINTS
           if swa_defined or not k.startswith("swa_")]
    for name, _, _ in bands:
        out.append((f"{name}_absolute_uv2", f"_band_abs:{name}"))
        out.append((f"{name}_relative", f"_band_rel:{name}"))
    return out


def _endpoint_value(spec: Spectrum, attr: str) -> Optional[float]:
    """One endpoint of one epoch's spectrum, or None when it does not exist."""
    if attr == "_exponent":
        fit = spec.aperiodic
        return None if fit is None else fit.exponent
    if attr == "_swa_log10":
        # log10 SWA: the scale sleep/pharma models are actually fitted on (band power
        # is strongly right-skewed). Undefined for zero power.
        return math.log10(spec.swa_abs) if spec.swa_abs > 0 else None
    if attr.startswith("_band_abs:"):
        name = attr.split(":", 1)[1]
        bp = next((b for b in spec.band_powers if b.name == name), None)
        return None if bp is None else bp.absolute
    if attr.startswith("_band_rel:"):
        name = attr.split(":", 1)[1]
        bp = next((b for b in spec.band_powers if b.name == name), None)
        return None if bp is None else bp.relative
    return getattr(spec, attr, None)


def _endpoint_series(epochs: Sequence[EpochResult], attr: str
                     ) -> Tuple[Optional[List[float]], int]:
    """Pull one endpoint out of each epoch.

    Returns ``(series, n_missing)``; ``series`` is None when any epoch lacks a finite
    value, because a summary over a subset of epochs would not match the reported n.
    """
    out: List[float] = []
    missing = 0
    for ep in epochs:
        v = _endpoint_value(ep.spectrum, attr)
        if v is None or not math.isfinite(v):
            missing += 1
        else:
            out.append(v)
    if missing:
        return None, missing
    return out, 0


def _summarize_epochs(result: AnalysisResult, summ: Sequence[EpochResult],
                      warns: List[str]) -> None:
    """Fill ``epoch_summary``/``epoch_trends`` from the epochs entering the summary.

    Descriptive statistics (incl. an autocorrelation-adjusted CI) and a Mann–Kendall
    /Theil–Sen trend are computed **once here**, so the text, JSON and CSV renderings
    can never disagree with each other. Trend x-values are epoch start times in
    seconds, so the Theil–Sen slope is per second (the text report rescales it to a
    unit the recording actually spans; JSON always keeps per-second).
    """
    result.n_summary = len(summ)
    if not summ:
        return
    xs = [ep.start_sec for ep in summ]
    if len(summ) > stats.MAX_EXACT_TREND_N:
        warns.append(
            f"{len(summ)} epochs exceed the {stats.MAX_EXACT_TREND_N}-epoch limit for "
            "the O(n²) Mann–Kendall/Theil–Sen trend test; trends were not computed "
            "(use longer epochs). Descriptive summaries are unaffected.")
    swa_defined = result.overall.swa_source != "undefined"
    dropped: List[str] = []
    for key, attr in _epoch_endpoints(result.bands, swa_defined):
        series, missing = _endpoint_series(summ, attr)
        if series is None:
            dropped.append(f"{key} ({missing}/{len(summ)} epochs lack it)")
            continue
        result.epoch_summary[key] = stats.summary_stats(series)
        tr = stats.trend(series, xs, x_unit="sec")
        if tr is not None and tr.exact:
            result.epoch_trends[key] = tr
    if dropped:
        warns.append(
            "endpoint(s) omitted from the epoch summary because some epochs have no "
            "finite value for them: " + "; ".join(dropped) + ".")


DEFAULT_FS = 128.0


def resolve_fs(user_fs: Optional[float], times: Optional[Sequence[float]]
               ) -> Tuple[float, str, List[str]]:
    """Decide which sampling rate to use given --fs and an optional time column.

    Returns (fs, source, warnings). When a time column is present its inferred fs is
    used, and a warning is emitted if it disagrees with an *explicitly given* --fs by
    > 1%. ``user_fs=None`` means "not specified": with a time column the inferred rate
    is simply used (no spurious mismatch warning against a default), and without one
    the documented default of 128 Hz is assumed — and said out loud, since a wrong
    sampling rate rescales every frequency and every band power.
    """
    warns: List[str] = []

    def _default() -> Tuple[float, str, List[str]]:
        warns.append(
            f"표본화율(--fs)이 지정되지 않았고 사용 가능한 시간 열도 없어 기본 "
            f"{DEFAULT_FS:g} Hz로 가정했습니다 / no --fs and no usable time column; "
            f"assumed {DEFAULT_FS:g} Hz — if that is wrong, every frequency and "
            "band power is wrong.")
        return DEFAULT_FS, "default", warns

    if not times or len(times) < 2:
        if user_fs is None:
            return _default()
        return user_fs, "user", warns
    try:
        fs_inf, regular, _ = infer_fs(list(times))
    except ValueError as exc:
        # A non-increasing / constant time column is unusable, but the value column is
        # fine: fall back instead of refusing to analyse the recording.
        warns.append(
            f"time column is unusable ({exc}); ignoring it and using "
            + (f"--fs {user_fs:g} Hz." if user_fs else "the default rate."))
        if user_fs is None:
            return _default()
        return user_fs, "user (time column ignored)", warns
    if not regular:
        warns.append(
            f"time column is irregularly sampled; inferred mean fs={fs_inf:.4g} Hz "
            "but Welch assumes uniform sampling — interpret with care.")
    # Snap a rate that is a hair off a round value (a 6-dp time column gives e.g.
    # 127.999998 Hz), which otherwise leaks into every reported frequency as
    # 9.999999874975583 Hz and into epoch end times as 40.00000050009768 s.
    snapped = round(fs_inf)
    if snapped > 0 and abs(fs_inf - snapped) <= 1e-4 * snapped and fs_inf != snapped:
        warns.append(
            f"fs inferred from the time column is {fs_inf:.10g} Hz, within 0.01% of "
            f"{snapped:g} Hz; snapped to {snapped:g} Hz to keep reported frequencies "
            "clean.")
        fs_inf = float(snapped)
    source = "inferred"
    if user_fs is not None and user_fs > 0 and abs(fs_inf - user_fs) / user_fs > 0.01:
        # An explicitly given --fs is a statement of fact about the recording; a rate
        # guessed from a column whose unit we cannot verify (Unix ms timestamps, a
        # counter) is not. Prefer the user's value and say so loudly.
        warns.append(
            f"--fs {user_fs:g} Hz disagrees with fs inferred from the time column "
            f"({fs_inf:.4g} Hz) by more than 1%; USING --fs {user_fs:g} Hz and "
            "ignoring the time column. Drop --fs to use the inferred rate instead, "
            "and check the time column's unit (seconds are assumed).")
        return user_fs, "user (time column disagreed)", warns
    return fs_inf, source, warns
