"""Aperiodic (1/f) spectral parameterization — pure standard library.

Why this matters clinically. A raw band power (e.g. delta = SWA) mixes two very
different things: a broadband **aperiodic** background whose shape is
``PSD(f) ≈ 10^offset / f^exponent`` and, on top of it, genuine narrowband
**oscillations** (alpha rhythm, sleep spindles, ...). Many drugs shift the aperiodic
background alone — propofol/ketamine/anaesthetics steepen it — which inflates or
deflates every band power without any change in the underlying rhythm. Reporting the
aperiodic exponent separately, and reporting band power *after* removing that
background, keeps the two effects from being confused.

구현 (what this module does)::

    1) 로그-로그 공간에서 log10 PSD ≈ offset − exponent·log10 f 을 최소제곱 적합.
    2) robust 모드: 잔차가 +k·σ 를 넘는 빈(=진동 피크)을 반복적으로 제외하고 재적합
       (수렴할 때까지, 최대 max_iter회). 1/f 배경만 남긴 안정적인 기울기를 얻는다.
    3) 선형 공간 잔차 max(PSD − fit, 0) 를 적분 → **진동성(배경 보정) 대역파워** (µV²).
    4) 로그 공간 잔차(=flattened spectrum)에서 대역별 피크를 찾고, 잔차 SD의 배수
       문턱으로 잡음 argmax를 억제 → 1/f 위에서도 신뢰할 수 있는 IAF/스핀들 피크.

Deliberately NOT modelled: a spectral *knee* (a bend in the 1/f slope, common when the
fit range spans a very wide band). With a knee present a single exponent is a summary
of the average slope over the fit range — the reported R² makes a poor fit visible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .spectral import integrate_psd

__all__ = [
    "AperiodicFit",
    "FIT_MODES",
    "fit_aperiodic",
    "residual_psd",
    "flattened_log_spectrum",
    "oscillatory_power",
    "flattened_peak",
    "half_range_exponents",
]

FIT_MODES = ("robust", "ols")

# robust 모드에서 "피크"로 보고 제외하는 잔차 문턱 (robust SD = MAD×1.4826 의 배수).
# MAD 기반이라 반복해도 척도가 수축하지 않아 절단이 폭주하지 않는다.
_TRIM_SIGMA = 2.0
# 재적합에 남겨야 하는 최소 빈 수 / 비율 (과도한 절단 방지).
_MIN_KEEP = 3
_MIN_KEEP_FRAC = 0.25
_MAD_TO_SD = 1.4826  # consistency constant: MAD × this ≈ SD for Gaussian noise
# Residual scale below which the spectrum is an exact power law up to rounding.
_EXACT_FIT_SD = 1e-12
# flattened(log) 스펙트럼에서 피크로 인정하는 문턱 (robust 잔차 SD의 배수).
# 빈이 100~200개면 백색잡음의 최대치가 ~3σ 이므로 3σ 미만은 잡음과 구분되지 않는다.
_PEAK_SIGMA = 3.0
# 그리고 배경 대비 최소 상승폭 (log10 파워). 0.1 ≈ 배경의 1.26배.
_PEAK_MIN_HEIGHT = 0.1


@dataclass
class AperiodicFit:
    """A fitted 1/f background: ``psd(f) = 10^offset · f^(-exponent)``."""

    exponent: float            # χ (양수 = 고주파로 갈수록 감소; 급할수록 큼)
    offset: float              # log10 PSD at 1 Hz (µV²/Hz)
    exponent_se: float         # OLS standard error of the exponent (근사)
    r2: float                  # R² over the bins used in the final fit
    r2_full: float             # R² over ALL in-range bins (peaks included)
    fit_lo: float              # actual lowest frequency used (Hz)
    fit_hi: float              # actual highest frequency used (Hz)
    n_used: int                # bins in the final fit
    n_total: int               # in-range bins with psd > 0
    mode: str                  # "robust" | "ols"
    n_trim_iter: int = 0       # robust 재적합 반복 횟수

    def psd_at(self, f: float) -> float:
        """The fitted aperiodic PSD (µV²/Hz) at frequency ``f`` (f > 0)."""
        if f <= 0:
            return float("nan")
        return 10.0 ** (self.offset - self.exponent * math.log10(f))

    def log_psd_at(self, f: float) -> float:
        """log10 of the fitted aperiodic PSD at ``f``."""
        if f <= 0:
            return float("nan")
        return self.offset - self.exponent * math.log10(f)


def _median(vals: Sequence[float]) -> float:
    """Median of a non-empty sequence (does not mutate the input)."""
    s = sorted(vals)
    m = len(s)
    mid = m // 2
    return s[mid] if m % 2 else 0.5 * (s[mid - 1] + s[mid])


def _ols(xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, float, float]:
    """Least-squares fit ``y = a + b·x``. Returns (b, a, sxx).

    ``sxx`` = Σ(x−x̄)² is handed back so the caller can derive the slope's standard
    error without recomputing it.
    """
    n = len(xs)
    x_mean = math.fsum(xs) / n
    y_mean = math.fsum(ys) / n
    sxx = math.fsum((x - x_mean) ** 2 for x in xs)
    if sxx <= 0:
        return 0.0, y_mean, 0.0
    sxy = math.fsum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    b = sxy / sxx
    return b, y_mean - b * x_mean, sxx


def _r2(xs: Sequence[float], ys: Sequence[float], b: float, a: float) -> float:
    """Coefficient of determination of ``y = a + b·x`` on the given points."""
    n = len(ys)
    if n == 0:
        return float("nan")
    y_mean = math.fsum(ys) / n
    ss_tot = math.fsum((y - y_mean) ** 2 for y in ys)
    ss_res = math.fsum((ys[i] - (a + b * xs[i])) ** 2 for i in range(n))
    if ss_tot <= 0:
        # A perfectly flat y: the fit is exact iff it has no residual.
        return 1.0 if ss_res <= 0 else 0.0
    return 1.0 - ss_res / ss_tot


def fit_aperiodic(freqs: Sequence[float], psd: Sequence[float],
                  fit_lo: float, fit_hi: float, mode: str = "robust",
                  max_iter: int = 10) -> Optional[AperiodicFit]:
    """Fit the aperiodic (1/f) background of a PSD over [fit_lo, fit_hi].

    Only strictly positive frequencies with strictly positive PSD enter the fit (a
    log-log fit is undefined otherwise). Returns ``None`` when fewer than 3 such bins
    exist (e.g. a constant/zero signal, or a fit range narrower than 3 bins), so
    callers can report "n/a" rather than a fabricated exponent.

    mode : ``"robust"`` (default) iteratively drops bins whose log-power sits more
        than ``_TRIM_SIGMA`` (= 2) robust SDs *above* the current fit — those are
        oscillatory peaks — and refits until the kept set stops changing. The scale is
        a MAD-based SD of the residuals over **all** in-range bins, so it neither
        shrinks with trimming nor is inflated by the peaks themselves. ``"ols"`` is a
        single plain least-squares fit over every in-range bin (peaks included), which
        biases the exponent toward the peaks.
    """
    if mode not in FIT_MODES:
        raise ValueError(f"mode must be one of {FIT_MODES}, got {mode!r}")
    if max_iter < 0:
        raise ValueError("max_iter must be >= 0")
    if fit_hi < fit_lo:
        fit_lo, fit_hi = fit_hi, fit_lo

    # Relative tolerance on the range edges: when fs is *inferred* from a time column
    # (e.g. 127.99997 Hz) the bin that should sit exactly on the requested edge lands
    # a few 1e-8 below it, and a strict comparison would silently drop it.
    lo_edge = fit_lo * (1.0 - 1e-9)
    hi_edge = fit_hi * (1.0 + 1e-9)

    xs: List[float] = []
    ys: List[float] = []
    used_f: List[float] = []
    for f, p in zip(freqs, psd):
        if f > 0.0 and lo_edge <= f <= hi_edge and p > 0.0 and math.isfinite(p):
            xs.append(math.log10(f))
            ys.append(math.log10(p))
            used_f.append(f)
    n_total = len(xs)
    if n_total < _MIN_KEEP:
        return None

    b, a, _ = _ols(xs, ys)
    keep = list(range(n_total))
    n_iter = 0
    if mode == "robust":
        for _ in range(max_iter):
            # Robust scale from ALL in-range bins: the median/MAD are unaffected by
            # the oscillatory peaks we want to trim, and (unlike the plain SD of the
            # kept subset) they do not shrink each iteration, so the trimming
            # converges instead of eating the spectrum.
            resid = [ys[i] - (a + b * xs[i]) for i in range(n_total)]
            centre = _median(resid)
            mad = _median([abs(r - centre) for r in resid])
            sd_r = _MAD_TO_SD * mad
            # An exact power law leaves only floating-point rounding as "residual";
            # trimming on that noise would drop a bin for no reason.
            if sd_r <= _EXACT_FIT_SD:
                break
            thresh = centre + _TRIM_SIGMA * sd_r
            cand = [i for i in range(n_total) if resid[i] <= thresh]
            if len(cand) < max(_MIN_KEEP, int(_MIN_KEEP_FRAC * n_total)):
                break
            if cand == keep:
                break
            keep = cand
            b, a, _ = _ols([xs[i] for i in keep], [ys[i] for i in keep])
            n_iter += 1

    kx = [xs[i] for i in keep]
    ky = [ys[i] for i in keep]
    b, a, sxx = _ols(kx, ky)
    r2_kept = _r2(kx, ky, b, a)
    r2_all = _r2(xs, ys, b, a)

    # Standard error of the slope: sqrt( SS_res / (n−2) / Sxx ). Approximate here
    # because neighbouring Welch bins are not independent (window main lobe +
    # overlapping segments), so treat it as a lower bound on the true uncertainty.
    n_k = len(kx)
    if n_k > 2 and sxx > 0:
        ss_res = math.fsum((ky[i] - (a + b * kx[i])) ** 2 for i in range(n_k))
        se = math.sqrt(ss_res / (n_k - 2) / sxx)
    else:
        se = float("nan")

    return AperiodicFit(
        exponent=-b, offset=a, exponent_se=se, r2=r2_kept, r2_full=r2_all,
        fit_lo=min(used_f), fit_hi=max(used_f), n_used=n_k, n_total=n_total,
        mode=mode, n_trim_iter=n_iter)


def residual_psd(freqs: Sequence[float], psd: Sequence[float],
                 fit: AperiodicFit) -> Tuple[List[float], List[float]]:
    """Oscillatory (background-removed) PSD: ``max(psd − aperiodic_fit, 0)``.

    Restricted to the bins the fit covers (``[fit.fit_lo, fit.fit_hi]``) since the
    extrapolated background outside that range is not supported by data. Returns
    (freqs_used, residual_psd) — integrate it to get oscillatory band power in µV².
    """
    out_f: List[float] = []
    out_p: List[float] = []
    for f, p in zip(freqs, psd):
        if f <= 0 or not (fit.fit_lo <= f <= fit.fit_hi):
            continue
        out_f.append(f)
        r = p - fit.psd_at(f)
        out_p.append(r if r > 0.0 else 0.0)
    return out_f, out_p


def flattened_log_spectrum(freqs: Sequence[float], psd: Sequence[float],
                           fit: AperiodicFit) -> Tuple[List[float], List[float]]:
    """Flattened spectrum in log10 power: ``log10(psd) − log10(aperiodic_fit)``.

    Scale-free, so a small alpha bump at 10 Hz is as visible as a huge delta bump at
    1 Hz. Used for peak detection. Bins with psd <= 0 are dropped.
    """
    out_f: List[float] = []
    out_v: List[float] = []
    for f, p in zip(freqs, psd):
        if f <= 0 or not (fit.fit_lo <= f <= fit.fit_hi) or p <= 0:
            continue
        out_f.append(f)
        out_v.append(math.log10(p) - fit.log_psd_at(f))
    return out_f, out_v


def oscillatory_power(res_f: Sequence[float], res_p: Sequence[float],
                      lo: float, hi: float, require_full: bool = True,
                      ) -> Optional[float]:
    """Integrate the residual PSD over [lo, hi] → oscillatory µV², or ``None``.

    With ``require_full=True`` (the default, and what the analysis uses) the band must
    lie **entirely inside the fitted range**; otherwise ``None`` is returned. That is
    deliberate: integrating only the covered part but labelling the row with the full
    band name understates the band by an arbitrary factor. Concretely, with
    ``--fit-range 2-45`` the delta row would carry the 2–4 Hz power under the label
    ``delta 0.5–4``, which on a slow-wave recording is ~50× smaller — a difference a
    reader would attribute to the drug, not to the fit range.

    With ``require_full=False`` the intersection is integrated instead (``None`` only
    when the overlap is empty); callers that do that must label the actual span.
    """
    if not res_f:
        return None
    # A relative tolerance absorbs bin-grid rounding (an inferred fs can put the edge
    # bin a few 1e-8 inside/outside the requested range).
    tol = 1e-9
    if require_full:
        if lo < res_f[0] * (1.0 - tol) or hi > res_f[-1] * (1.0 + tol):
            return None
    a = max(lo, res_f[0])
    b = min(hi, res_f[-1])
    if b <= a:
        return None
    return integrate_psd(res_f, res_p, a, b)


def half_range_exponents(freqs: Sequence[float], psd: Sequence[float],
                         fit: AperiodicFit, mode: str = "robust",
                         ) -> Optional[Tuple[float, float]]:
    """Exponents fitted separately to the lower and upper half of the fit range.

    A single exponent describes a spectrum only if the log-log slope is constant. When
    the spectrum has a **knee** (a bend, common over wide ranges) the overall R² can
    still look excellent — a textbook 3rd-order knee gives R² ≈ 0.92 — so R² alone
    does not expose it. Comparing the two half-range slopes does: they differ by
    roughly the size of the bend. Returns ``None`` when either half has too few bins.
    """
    mid = math.sqrt(fit.fit_lo * fit.fit_hi)          # geometric midpoint in log f
    lo_fit = fit_aperiodic(freqs, psd, fit.fit_lo, mid, mode=mode)
    hi_fit = fit_aperiodic(freqs, psd, mid, fit.fit_hi, mode=mode)
    if lo_fit is None or hi_fit is None:
        return None
    return lo_fit.exponent, hi_fit.exponent


def flattened_peak(flat_f: Sequence[float], flat_v: Sequence[float],
                   lo: float, hi: float, sigma: float = _PEAK_SIGMA,
                   min_height: float = _PEAK_MIN_HEIGHT,
                   ) -> Tuple[Optional[float], Optional[float], bool]:
    """Find a band peak in the flattened (log-power) spectrum.

    Returns (freq, height_log10, prominent). ``height_log10`` is how far the peak
    rises above the aperiodic background in log10 µV²/Hz (0.30 ≈ a doubling of
    power, 1.0 ≈ ten-fold).

    ``prominent`` is True only when every one of these holds, which together keep the
    argmax of a noisy flat band from being reported as a rhythm:

      * the peak is interior to the band and above the flattened value at both band
        edges (a shoulder pinned at an edge is not a peak),
      * it rises at least ``min_height`` in log10 power above the background, and at
        least ``sigma``× a *robust* (MAD-based) SD of the flattened spectrum — with
        100–200 bins the largest white-noise excursion is ≈3σ, hence the 3σ default,
      * it has width: the two immediate neighbours together reach half the peak
        height and the stronger one alone reaches 40% of it. A real rhythm spans
        several bins (the periodic-Hann main lobe alone is ≈4 bins wide) while a
        one-bin spike is noise or a mains/artifact line. The pair is scored jointly
        because a rhythm whose frequency falls between two bins splits asymmetrically
        across them. (Measured on synthetic 1/f Welch spectra: false-positive rate
        ≈0.4% per band, and a 10 Hz rhythm is caught reliably once it lifts the band
        clearly above the 1/f background.)
    """
    idx = [i for i, f in enumerate(flat_f) if lo <= f <= hi]
    if not idx:
        return None, None, False
    best = max(idx, key=lambda i: flat_v[i])
    peak_f = flat_f[best]
    height = flat_v[best]
    first, last = idx[0], idx[-1]
    if not (first < best < last):
        return peak_f, height, False

    # Robust noise scale of the flattened spectrum over the whole fit range: the MAD
    # ignores the peaks themselves, so a strong rhythm does not raise its own gate.
    centre = _median(flat_v)
    sd = _MAD_TO_SD * _median([abs(v - centre) for v in flat_v])

    if best - 1 >= 0 and best + 1 < len(flat_v):
        left, right = flat_v[best - 1], flat_v[best + 1]
        has_width = (left + right >= 0.5 * height
                     and max(left, right) >= 0.4 * height)
    else:
        has_width = False
    # With a perfectly clean background (MAD = 0) any positive bump is infinitely many
    # SDs above the noise, so the σ gate is vacuously satisfied — refusing to report
    # the peak in that case would be the wrong way round.
    sigma_ok = (sd <= 0.0) or (height >= sigma * sd)
    prominent = (
        height > flat_v[first]
        and height > flat_v[last]
        and height >= min_height
        and sigma_ok
        and has_width
    )
    return peak_f, height, prominent
