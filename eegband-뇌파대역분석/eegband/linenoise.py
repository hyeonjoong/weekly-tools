"""Mains (50/60 Hz) line-noise detection and PSD-domain removal.

Every clinical EEG recording sits in a room full of mains wiring, and the resulting
narrow peak at 50 Hz (EU/KR/JP-west) or 60 Hz (US/KR-grid 60 Hz/JP-east) — plus its
harmonics — sits right where a wide gamma band is measured. A band-power report that
silently adds that peak to "gamma" is reporting the building, not the brain.

Note that the DEFAULT bands stop at 45 Hz, so 50/60 Hz falls outside every reported
band and a notch changes none of them; the peak is still worth reporting as a quality
signal. It contaminates band power only once gamma is widened (``--bands
'...,gamma:30-90'``) or the mains aliases down (see below).

Two things happen here:

* **Detection** (always on by default): the height of the peak in a ±``bw`` Hz window
  around each harmonic is compared with the *local* background (the median PSD in the
  surrounding shoulders). A ratio of ≥ :data:`RATIO_THRESHOLD` is called line noise.
  Nothing is altered — the user is simply told, with the excess power quantified in
  µV² so they can see how much of a band it accounts for.
* **Removal** (``--notch``): the offending bins are replaced by a log-linear
  interpolation between the bins just outside the window — the standard "spectral
  interpolation" notch. This is done in the frequency domain, on the PSD, so it costs
  nothing in the time domain (no filter ringing, no phase distortion, no edge effects)
  and it is exactly the operation a band-power report needs.

**Aliasing.** If the recording's Nyquist frequency is below the mains frequency, the
mains peak does not vanish — it folds back to ``|f0 − k·fs|`` and contaminates a
*lower* frequency. A 60 Hz mains on a 100 Hz recording appears at 40 Hz, i.e. inside
gamma, and on a 128 Hz recording at 60 Hz it is simply gone (above Nyquist=64? no —
60 < 64, so it is still there). :func:`alias_freq` folds a frequency into
[0, fs/2] so the harmonic is examined where it actually is, and the report says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .spectral import integrate_psd

__all__ = [
    "DEFAULT_BW",
    "RATIO_THRESHOLD",
    "CANDIDATES",
    "MAX_ORDER",
    "HarmonicPeak",
    "LineNoiseReport",
    "alias_freq",
    "local_background",
    "measure_peak",
    "analyse_line_noise",
    "notch_psd",
    "windows_fit",
    "MIN_EXCESS_SHARE",
]

DEFAULT_BW = 1.0          # half-width (Hz) of the notch / measurement window
RATIO_THRESHOLD = 3.0     # peak/background ratio that counts as line noise
# A ratio test alone is scale-free, so on a numerically pure signal (a synthetic sine,
# a calibration channel) floating-point round-off at 50 Hz sits millions of times above
# a round-off background and is declared mains contamination. Real mains noise is never
# a negligible share of the spectrum, so also require the excess to be at least this
# fraction of the total power in the analysed spectrum.
MIN_EXCESS_SHARE = 1e-6
CANDIDATES: Tuple[float, ...] = (50.0, 60.0)
MAX_ORDER = 3             # fundamental + 2 harmonics


@dataclass
class HarmonicPeak:
    """One mains harmonic as it appears in this spectrum."""
    order: int             # 1 = fundamental, 2 = first harmonic, ...
    nominal_hz: float      # order * f0, before any aliasing
    freq_hz: float         # where it actually sits in [0, Nyquist] (aliased if needed)
    aliased: bool
    ratio: Optional[float]      # peak PSD / local background (None = not measurable)
    background: Optional[float]  # local background PSD (µV²/Hz)
    peak_psd: Optional[float]
    excess_uv2: Optional[float]  # power in the window above the background, µV²
    detected: bool = False       # flagged as line noise (drives --notch)
    # ratio >= threshold AND the excess is a non-negligible share of the spectrum.
    # `detected` additionally requires that the harmonic is not an unguarded alias.
    significant: bool = False


@dataclass
class LineNoiseReport:
    f0: float                       # mains fundamental used (Hz)
    source: str                     # "auto" (detected) or "user"
    bandwidth: float                # half-width of each window (Hz)
    nyquist_hz: float
    peaks: List[HarmonicPeak] = field(default_factory=list)
    removed: bool = False           # notch actually applied to the PSD
    threshold: float = RATIO_THRESHOLD

    @property
    def detected(self) -> bool:
        return any(p.detected for p in self.peaks)

    def suspect_aliases(self) -> List[HarmonicPeak]:
        """Loud aliased harmonics that were deliberately NOT flagged (auto mode).

        These are reported so the user can decide, but never notched automatically:
        at that frequency an alias of the mains and a genuine rhythm are the same
        measurement.
        """
        return [p for p in self.peaks
                if p.aliased and not p.detected and p.significant]

    @property
    def max_ratio(self) -> Optional[float]:
        rs = [p.ratio for p in self.peaks if p.ratio is not None]
        return max(rs) if rs else None

    def detected_peaks(self) -> List[HarmonicPeak]:
        return [p for p in self.peaks if p.detected]

    def targets(self) -> List[float]:
        """Frequencies whose windows a notch would replace (detected harmonics)."""
        return [p.freq_hz for p in self.peaks if p.detected]

    def excess_in(self, lo: float, hi: float) -> float:
        """Excess (line-noise) power, µV², attributable to the band [lo, hi].

        A harmonic is charged to the band that CONTAINS ITS CENTRE FREQUENCY, in full.
        Prorating it across the ±bw window instead would be badly wrong whenever a band
        edge cuts through the window: mains power is concentrated in the one or two
        bins at the peak, not spread evenly over ±bw, so a band that merely clips the
        skirt of the window would be told that most of its power was electrical when in
        fact none of it was removed from that band.

        A peak sitting exactly on a band edge is charged to the upper band, matching
        the half-open [lo, hi) convention used everywhere else.
        """
        total = 0.0
        for p in self.peaks:
            if not p.detected or p.excess_uv2 is None:
                continue
            if lo <= p.freq_hz < hi or (p.freq_hz == hi and hi == self.nyquist_hz):
                total += p.excess_uv2
        return total


def alias_freq(f: float, fs: float) -> Tuple[float, bool]:
    """Fold ``f`` into [0, fs/2]; returns (folded frequency, was_aliased).

    Sampling at ``fs`` maps every frequency to its image in the first Nyquist zone.
    A 60 Hz mains component in a signal sampled at 100 Hz is indistinguishable from
    a 40 Hz component, so that is where it must be looked for.
    """
    if fs <= 0:
        raise ValueError("fs must be positive")
    nyq = fs / 2.0
    if 0 <= f <= nyq:
        return f, False
    m = math.fmod(abs(f), fs)
    if m > nyq:
        m = fs - m
    return m, True


def local_background(freqs: Sequence[float], psd: Sequence[float], f: float,
                     bw: float, shoulder: float = 4.0) -> Optional[float]:
    """Median PSD in the shoulders around ``f``: ``bw < |g − f| <= shoulder·bw``.

    The median (not the mean) so that a *second* narrow peak in the shoulder — the
    neighbouring harmonic of the other mains standard, say — cannot inflate the
    background and hide a real one. Returns None when fewer than 3 shoulder bins
    exist (too little context to judge a peak).
    """
    if bw <= 0:
        raise ValueError("bw must be positive")
    vals = [p for g, p in zip(freqs, psd)
            if bw < abs(g - f) <= shoulder * bw and math.isfinite(p)]
    if len(vals) < 3:
        return None
    vals.sort()
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else 0.5 * (vals[mid - 1] + vals[mid])


def measure_peak(freqs: Sequence[float], psd: Sequence[float], f: float,
                 bw: float) -> Tuple[Optional[float], Optional[float],
                                     Optional[float], Optional[float]]:
    """Measure the peak at ``f``: (ratio, background, peak_psd, excess_uv2).

    ``excess_uv2`` is the integrated power in [f−bw, f+bw] minus what a flat local
    background would contribute over the same span — i.e. the µV² that a notch
    would remove.
    """
    bg = local_background(freqs, psd, f, bw)
    window = [p for g, p in zip(freqs, psd) if abs(g - f) <= bw and math.isfinite(p)]
    if not window:
        return None, bg, None, None
    peak = max(window)
    if bg is None or bg <= 0 or not math.isfinite(bg):
        # No usable background (a zero-power or single-tone spectrum): the ratio is
        # undefined rather than infinite, and no excess can be attributed.
        return None, bg, peak, None
    ratio = peak / bg
    total = integrate_psd(freqs, psd, f - bw, f + bw)
    span = min(f + bw, freqs[-1]) - max(f - bw, freqs[0])
    excess = max(total - bg * max(span, 0.0), 0.0)
    return ratio, bg, peak, excess


def _harmonics(f0: float, fs: float, bw: float,
               max_order: int = MAX_ORDER) -> List[Tuple[int, float, float, bool]]:
    """(order, nominal_hz, freq_hz, aliased) for the harmonics worth examining."""
    nyq = fs / 2.0
    out: List[Tuple[int, float, float, bool]] = []
    seen: List[float] = []
    for k in range(1, max_order + 1):
        nominal = k * f0
        f, aliased = alias_freq(nominal, fs)
        # A window that runs off either end of the spectrum cannot be measured
        # against a background, and one sitting on DC would swallow delta.
        if f - bw <= 0 or f + bw >= nyq:
            continue
        if any(abs(f - s) < bw for s in seen):
            continue          # aliasing folded two harmonics onto the same place
        seen.append(f)
        out.append((k, nominal, f, aliased))
    return out


def windows_fit(fs: float, bw: float,
                candidates: Sequence[float] = CANDIDATES) -> bool:
    """True when at least one candidate FUNDAMENTAL window fits inside (0, fs/2).

    This is exactly the predicate :func:`analyse_line_noise` uses to pick ``f0`` (in
    band first, then its alias), so it explains *why* nothing was measured instead of
    guessing. A ±``bw`` window needs both edges strictly inside the spectrum, so a wide
    ``--line-bw`` on a low ``fs`` leaves no measurable fundamental even though a higher
    harmonic might still fit.
    """
    if fs <= 0 or bw <= 0:
        return False
    nyq = fs / 2.0
    for cand in candidates:
        f, _ = alias_freq(cand, fs)
        if f - bw > 0 and f + bw < nyq:
            return True
    return False


def analyse_line_noise(freqs: Sequence[float], psd: Sequence[float], fs: float,
                       f0: Optional[float] = None, bw: float = DEFAULT_BW,
                       threshold: float = RATIO_THRESHOLD,
                       candidates: Sequence[float] = CANDIDATES,
                       source: Optional[str] = None,
                       ) -> Optional[LineNoiseReport]:
    """Detect mains line noise in a PSD.

    ``source`` overrides how the result is labelled and, with it, whether the aliased-
    harmonic guard applies. Epochs inherit the *recording's* fundamental as a number
    but must keep the recording's ``"auto"`` semantics, or an epoch would flag (and
    claim to have removed) an aliased harmonic that the recording deliberately spared.

    ``f0=None`` auto-selects between ``candidates`` (50/60 Hz) by whichever
    fundamental has the larger peak/background ratio; the winner is reported even
    when it is below ``threshold`` (``report.detected`` is then False), so the
    report can always state what was checked. Returns None when no candidate could
    be measured at all (e.g. Nyquist too low for any window).
    """
    if fs <= 0:
        raise ValueError("fs must be positive")
    if bw <= 0:
        raise ValueError("bw must be positive")
    nyq = fs / 2.0
    if f0 is not None:
        chosen: Optional[float] = float(f0)
        if chosen <= 0:
            raise ValueError("line frequency must be positive")
        source = "user" if source is None else source
    else:
        source = "auto" if source is None else source
        chosen = None
        best = -1.0
        for cand in candidates:
            # Auto-selection FLAGS only a fundamental that is genuinely IN the recorded
            # band. Above Nyquist the mains folds to an essentially arbitrary frequency
            # that can coincide with a real rhythm (50 Hz sampled at 80 Hz puts its 3rd
            # harmonic on 10 Hz — exactly where alpha lives), and calling that "line
            # noise" on the tool's own initiative would be a false positive with a
            # --notch that deletes the rhythm.
            if cand > nyq or cand - bw <= 0 or cand + bw >= nyq:
                continue
            ratio, _, _, _ = measure_peak(freqs, psd, cand, bw)
            if ratio is not None and ratio > best:
                best = ratio
                chosen = float(cand)
        if chosen is None:
            # No candidate is in band — the common case for fs <= 102 Hz, which is an
            # ordinary ambulatory/clinical rate. Returning None here would be the worst
            # possible answer: at fs=100 a 60 Hz mains folds to 40 Hz, dead centre of
            # gamma, and saying nothing reports it as brain activity. So still build a
            # report over the ALIASED positions, with nothing flagged — that is what
            # feeds the "suspected mains alias" warning.
            best_alias = None
            best_ratio = -1.0
            for cand in candidates:
                f, _ = alias_freq(cand, fs)
                if f - bw <= 0 or f + bw >= nyq:
                    continue
                ratio, _, _, _ = measure_peak(freqs, psd, f, bw)
                if ratio is not None and ratio > best_ratio:
                    best_ratio = ratio
                    best_alias = float(cand)
            if best_alias is None:
                return None
            chosen = best_alias

    # Total power in the analysed spectrum, for the "is this a meaningful share?" floor.
    total = integrate_psd(freqs, psd, freqs[0], freqs[-1]) if len(freqs) > 1 else 0.0

    peaks: List[HarmonicPeak] = []
    for order, nominal, f, aliased in _harmonics(chosen, fs, bw):
        ratio, bg, peak, excess = measure_peak(freqs, psd, f, bw)
        loud = ratio is not None and ratio >= threshold
        if loud and total > 0 and (excess is None
                                   or excess < MIN_EXCESS_SHARE * total):
            loud = False        # a ratio spike that carries no actual power
        # An ALIASED harmonic is never flagged on the tool's own initiative: its
        # position carries no information that separates mains from brain activity at
        # that frequency. Only an explicit --line-freq (the user asserting what the
        # mains frequency is) lets one be flagged — and the report says so loudly.
        peaks.append(HarmonicPeak(
            order=order, nominal_hz=nominal, freq_hz=f, aliased=aliased,
            ratio=ratio, background=bg, peak_psd=peak, excess_uv2=excess,
            significant=loud,
            detected=bool(loud and (source == "user" or not aliased))))
    if not peaks:
        return None
    return LineNoiseReport(f0=chosen, source=source, bandwidth=bw,
                           nyquist_hz=fs / 2.0, peaks=peaks, threshold=threshold)


def notch_psd(freqs: Sequence[float], psd: Sequence[float],
              targets: Sequence[float], bw: float) -> Tuple[List[float], int]:
    """Replace the bins within ±``bw`` of each target by interpolated background.

    Interpolation is linear in log10(PSD) between the nearest bin outside the window
    on each side — a spectrum falls off roughly as a power law, so a straight line in
    log-log/log space is a far better stand-in than a straight line in linear power.
    Non-positive anchors fall back to linear interpolation. Returns the cleaned PSD
    and the number of bins replaced.
    """
    out = [float(p) for p in psd]
    n = len(out)
    if n == 0 or not targets:
        return out, 0
    if bw <= 0:
        raise ValueError("bw must be positive")
    replaced = 0
    for f in targets:
        idx = [i for i in range(n) if abs(freqs[i] - f) <= bw]
        if not idx:
            continue
        i0, i1 = idx[0], idx[-1]
        left = i0 - 1
        right = i1 + 1
        if left < 0 and right >= n:
            continue                      # window covers the whole spectrum
        if left < 0:
            for i in idx:
                out[i] = out[right]
            replaced += len(idx)
            continue
        if right >= n:
            for i in idx:
                out[i] = out[left]
            replaced += len(idx)
            continue
        a, b = out[left], out[right]
        fa, fb = freqs[left], freqs[right]
        use_log = a > 0 and b > 0
        la, lb = (math.log10(a), math.log10(b)) if use_log else (a, b)
        for i in idx:
            t = 0.0 if fb == fa else (freqs[i] - fa) / (fb - fa)
            v = la + (lb - la) * t
            out[i] = (10.0 ** v) if use_log else v
        replaced += len(idx)
    return out, replaced
