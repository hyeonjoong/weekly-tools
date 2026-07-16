"""Human-readable (Korean + English), JSON, and CSV rendering of an AnalysisResult."""

from __future__ import annotations

import csv
import io
import math
import statistics
from typing import Any, Dict, List, Optional

from . import __version__
from .analyze import AnalysisResult, Spectrum

__all__ = ["render_text", "to_dict", "render_csv"]


# Two-sided 97.5% Student-t critical values for df = 1..30; z (1.95996) beyond.
_T975 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080,
    22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
    29: 2.045, 30: 2.042,
}


_Z975 = 1.959963984540054  # standard-normal 0.975 quantile


def _t_crit(df: int) -> float:
    """Two-sided 95% (0.975 one-tail) Student-t critical value for ``df`` d.o.f.

    Exact 3-dp table for df<=30; for df>30 a Cornish–Fisher expansion of the t
    quantile around the normal (accurate to <1e-3 for df>30), so there is no
    discontinuity at df=31 and CIs for many-epoch (full-night) recordings stay
    correct rather than collapsing to the normal 1.96.
    """
    if df <= 0:
        return float("nan")
    if df <= 30:
        return _T975[df]
    z = _Z975
    g1 = (z ** 3 + z) / 4.0
    g2 = (5 * z ** 5 + 16 * z ** 3 + 3 * z) / 96.0
    g3 = (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / 384.0
    g4 = (79 * z ** 9 + 776 * z ** 7 + 1482 * z ** 5 - 1920 * z ** 3 - 945 * z) / 92160.0
    return z + g1 / df + g2 / df ** 2 + g3 / df ** 3 + g4 / df ** 4


def _quantile(sorted_vals: List[float], q: float) -> float:
    """Linear-interpolation quantile (type-7, matches numpy.quantile default)."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def _summary_stats(vals: List[float]) -> Dict[str, float]:
    """Descriptive stats for an endpoint across epochs: mean, sample SD (n-1), SEM,
    t-based 95% CI on the mean, median, quartiles and range."""
    n = len(vals)
    mean = statistics.fmean(vals)
    sd = statistics.stdev(vals) if n > 1 else 0.0
    sem = sd / math.sqrt(n) if n > 1 else 0.0
    half = _t_crit(n - 1) * sem if n > 1 else 0.0
    s = sorted(vals)
    return {
        "mean": mean, "sd": sd, "sem": sem,
        "ci_lo": mean - half, "ci_hi": mean + half,
        "median": _quantile(s, 0.5), "q1": _quantile(s, 0.25),
        "q3": _quantile(s, 0.75), "min": s[0], "max": s[-1],
    }


def _num(x: Optional[float], d: int = 3) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float) and math.isnan(x):
        return "NaN"
    if isinstance(x, float) and math.isinf(x):
        return "inf" if x > 0 else "-inf"
    return f"{x:.{d}f}"


def _hz(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:.2f} Hz"


def _hz2(x: Optional[float]) -> str:
    """Bare 2-dp Hz value (no unit) for compact table cells."""
    return "n/a" if x is None else f"{x:.2f}"


def _range(lo: float, hi: float) -> str:
    def g(v: float) -> str:
        return f"{v:g}"
    return f"{g(lo)}–{g(hi)}"


def _range_safe(lo: float, hi: float) -> str:
    """Like _range but never prints a reversed span (e.g. when the analysis band
    collapses because every band edge is above Nyquist -> hi < lo)."""
    if hi < lo:
        return f"{lo:g}–{lo:g}"
    return _range(lo, hi)


def render_text(res: AnalysisResult) -> str:
    lines: List[str] = []
    L = lines.append
    bar = "=" * 74

    L(bar)
    L("  eegband — 단일채널 EEG 대역파워 분석 / Single-channel EEG band-power report")
    L(bar)

    L("")
    L("[정보 / Info]")
    L(f"    eegband v{__version__}"
      + (f",  input = {res.source_file}" if res.source_file else ""))
    L(f"    fs = {res.fs:.4g} Hz ({res.fs_source}),  N = {res.n_samples} samples,"
      f"  duration = {res.duration_sec:.2f} s")
    L(f"    Welch: nperseg = {res.nperseg}, noverlap = {res.noverlap}, "
      f"nfft = {res.nfft}, window = Hann(periodic), scaling = density (µV²/Hz)")
    L(f"    detrend = {res.detrend}, average = {res.average}")

    _render_quality(L, res)

    _render_spectrum(L, res.overall, "[1] 대역파워 / Band power")

    # SWA highlight
    L("")
    L("[2] 슬로우파 활동 / Slow-wave activity (SWA = delta 0.5–4 Hz) — key sleep endpoint")
    L(f"    SWA absolute  = {_num(res.overall.swa_abs)} µV²")
    L(f"    SWA relative  = {_num(res.overall.swa_rel * 100.0, 1)} %")
    dom = res.overall.dominant
    tie = "  (⚠ near-tie: runner-up within 1%)" if res.overall.dominant_tie else ""
    if dom is None:
        L("    dominant band = n/a  (no spectral power — constant/zero signal)")
    elif dom == "delta":
        L(f"    dominant band = delta   ← slow-wave/delta dominant{tie}")
    else:
        L(f"    dominant band = {dom}   (delta is not the strongest band){tie}")

    # Spectral summary
    L("")
    L("[3] 스펙트럼 요약 / Spectral summary")
    L(f"    peak frequency          = {_hz(res.overall.peak_freq)}")
    L(f"    SEF{int(round(res.overall.sef_frac * 100))} (spectral edge freq) = "
      f"{_hz(res.overall.sef)}")
    L(f"    spectral entropy (norm) = {_num(res.overall.entropy)}"
      "   (1=flat/white, 0=single rhythm)")
    iaf, iaf_prom = _band_peak(res.overall, "alpha")
    if iaf_prom:
        L(f"    alpha peak (IAF)        = {_hz(iaf)}")
    else:
        L("    alpha peak (IAF)        = n/a  (no distinct alpha peak)")
    L(f"    total power ({_range_safe(res.overall.band_lo, res.overall.band_hi)} Hz) = "
      f"{_num(res.overall.total_power)} µV²")

    # Ratios
    L("")
    L("[4] 대역비 / Band ratios")
    L(f"    theta/alpha                        = {_num(res.overall.ratios.get('theta/alpha'))}")
    L(f"    delta/beta                         = {_num(res.overall.ratios.get('delta/beta'))}")
    L(f"    (delta+theta)/(alpha+beta) [slowing] = "
      f"{_num(res.overall.ratios.get('(delta+theta)/(alpha+beta)'))}")

    # Epochs
    if res.epochs:
        L("")
        L(f"[5] 에폭별 / Per-epoch  (epoch = {res.epoch_sec:g} s, "
          f"n_epochs = {len(res.epochs)})")
        rej_on = res.max_amp is not None
        L(f"    {'ep':>3}{'t0(s)':>8}{'t1(s)':>8}"
          + "".join(f"{name[:5]:>8}" for name, _, _ in res.bands)
          + f"{'peak':>8}{'SEF':>8}  dominant" + ("  |amp|/rej" if rej_on else ""))
        for ep in res.epochs:
            sp = ep.spectrum
            rels = "".join(f"{sp_rel(sp, name) * 100:>7.1f}%" for name, _, _ in res.bands)
            dom_cell = (sp.dominant or "n/a") + ("*" if sp.dominant_tie else "")
            pk = f"{sp.peak_freq:>8.2f}" if sp.peak_freq is not None else f"{'n/a':>8}"
            se = f"{sp.sef:>8.2f}" if sp.sef is not None else f"{'n/a':>8}"
            rej = ""
            if rej_on:
                rej = f"  {ep.peak_amp:>6.1f}" + ("  ✗REJ" if ep.rejected else "")
            L(f"    {ep.index:>3}{ep.start_sec:>8.1f}{ep.end_sec:>8.1f}{rels}"
              f"{pk}{se}  {dom_cell}{rej}")
        L("    (대역 값은 상대파워 % / band cells are relative power %)")
        if rej_on:
            L(f"    artifact rejection (|amp| > {res.max_amp:g} µV): "
              f"kept {res.n_epochs_kept}/{len(res.epochs)}, "
              f"rejected {res.n_epochs_rejected}  "
              "→ 요약 통계는 채택 에폭만 사용 / summary uses kept epochs only")
        if res.swa_density is not None:
            kept = [ep for ep in res.epochs if not ep.rejected]
            summ = kept if kept else res.epochs
            n_ep = len(summ)
            delta_dom = sum(1 for ep in summ if ep.spectrum.dominant == "delta")
            L(f"    SWA density (delta-dominant epochs) = {delta_dom}/{n_ep}"
              f"  ({res.swa_density * 100:.0f} %)")
            rel_deltas = [ep.spectrum.swa_rel for ep in summ]
            abs_swas = [ep.spectrum.swa_abs for ep in summ]
            r = _summary_stats(rel_deltas)
            a = _summary_stats(abs_swas)
            # SD is the sample SD (n-1); SEM = SD/sqrt(n) for the endpoint mean;
            # 95% CI = mean ± t(0.975, n-1)·SEM.
            L(f"    relative delta across epochs = {r['mean'] * 100:.1f} "
              f"± {r['sd'] * 100:.1f} % (SD, n-1),  SEM {r['sem'] * 100:.1f} %,  "
              f"95% CI [{r['ci_lo'] * 100:.1f}, {r['ci_hi'] * 100:.1f}] %  (n={n_ep})")
            L(f"      median {r['median'] * 100:.1f} %, "
              f"IQR [{r['q1'] * 100:.1f}, {r['q3'] * 100:.1f}] %, "
              f"range [{r['min'] * 100:.1f}, {r['max'] * 100:.1f}] %")
            L(f"    SWA absolute across epochs   = {_num(a['mean'])} "
              f"± {_num(a['sd'])} µV² (SD, n-1),  SEM {_num(a['sem'])} µV²,  "
              f"95% CI [{_num(a['ci_lo'])}, {_num(a['ci_hi'])}] µV²")
            L(f"      median {_num(a['median'])}, "
              f"IQR [{_num(a['q1'])}, {_num(a['q3'])}], "
              f"range [{_num(a['min'])}, {_num(a['max'])}] µV²")
            # Honesty: epochs from one recording are autocorrelated, so this spread
            # is a within-recording distribution, NOT a between-subject inferential CI.
            L("      (에폭은 자기상관 — 기록 내 분포이며 피험자간 추론 CI 아님 / "
              "within-recording spread, not a between-subject CI)")

    # Warnings
    if res.warnings:
        L("")
        L("[!] 주의 / Warnings")
        for w in res.warnings:
            L(f"    - {w}")

    L("")
    return "\n".join(lines)


def _render_quality(L, res: AnalysisResult) -> None:
    q = res.quality
    if q is None:
        return
    L("")
    L("[0] 신호 품질 / Signal quality")
    L(f"    amplitude: min = {_num(q.v_min)}  max = {_num(q.v_max)}  "
      f"ptp = {_num(q.ptp)}  mean = {_num(q.mean)}  RMS = {_num(q.rms)} µV")
    L(f"    interpolated = {q.n_interpolated}/{q.n_samples} "
      f"({q.frac_interpolated * 100:.1f}%),  "
      f"clipped(rail) = {q.n_clipped} ({q.frac_clipped * 100:.1f}%),  "
      f"flat-run = {q.n_flat} ({q.frac_flat * 100:.1f}%)")
    for f in q.flags:
        L(f"    ⚠ {f}")


def sp_rel(spec: Spectrum, name: str) -> float:
    for bp in spec.band_powers:
        if bp.name == name:
            return bp.relative
    return 0.0


def _band_peak(spec: Spectrum, name: str):
    """Return (peak_freq, is_prominent) for band ``name`` (None, False if absent)."""
    for bp in spec.band_powers:
        if bp.name == name:
            return bp.peak_freq, bp.peak_prominent
    return None, False


def _render_spectrum(L, spec: Spectrum, title: str) -> None:
    L("")
    L(title + "  (absolute µV², relative %, prominent in-band peak Hz)")
    L(f"    {'band':<8}{'range(Hz)':<12}{'abs(µV²)':>14}{'rel(%)':>10}{'peak(Hz)':>10}")
    for bp in spec.band_powers:
        tag = "  ← SWA" if bp.name == "delta" else ""
        # only report the peak when it is a genuine hump, not a 1/f-slope argmax.
        peak = _hz2(bp.peak_freq) if bp.peak_prominent else "n/a"
        L(f"    {bp.name:<8}{_range(bp.lo, bp.hi):<12}{_num(bp.absolute):>14}"
          f"{_num(bp.relative * 100.0, 1):>10}{peak:>10}{tag}")
    L(f"    {'total':<8}{_range_safe(spec.band_lo, spec.band_hi):<12}"
      f"{_num(spec.total_power):>14}{_num(spec.rel_sum * 100.0, 1):>10}")


def to_dict(res: AnalysisResult) -> Dict[str, Any]:
    """JSON-serialisable dict (used by --json)."""
    def spec_dict(spec: Spectrum) -> Dict[str, Any]:
        return {
            "band_power": [
                {"name": bp.name, "low_hz": bp.lo, "high_hz": bp.hi,
                 "absolute_uv2": bp.absolute, "relative": bp.relative,
                 "peak_freq_hz": bp.peak_freq,
                 "peak_prominent": bp.peak_prominent}
                for bp in spec.band_powers
            ],
            "total_power_uv2": spec.total_power,
            "relative_sum": spec.rel_sum,
            "peak_freq_hz": spec.peak_freq,
            "spectral_entropy": spec.entropy,
            "sef_frac": spec.sef_frac,
            "sef_hz": spec.sef,
            "dominant_band": spec.dominant,
            "dominant_tie": spec.dominant_tie,
            "swa": {"absolute_uv2": spec.swa_abs, "relative": spec.swa_rel},
            "ratios": {k: (None if (isinstance(v, float) and not math.isfinite(v))
                           else v)
                       for k, v in spec.ratios.items()},
            "band_range_hz": [spec.band_lo, spec.band_hi],
        }

    out: Dict[str, Any] = {
        "tool": "eegband",
        "version": __version__,
        "source_file": res.source_file,
        "fs_hz": res.fs,
        "fs_source": res.fs_source,
        "n_samples": res.n_samples,
        "duration_sec": res.duration_sec,
        "welch": {"nperseg": res.nperseg, "noverlap": res.noverlap,
                  "nfft": res.nfft, "window": "hann-periodic",
                  "scaling": "density", "detrend": res.detrend,
                  "average": res.average},
        "bands": [{"name": n, "low_hz": lo, "high_hz": hi}
                  for n, lo, hi in res.bands],
        "signal_quality": _quality_dict(res.quality),
        "provenance": {
            "sef_percent": res.sef_frac * 100.0,
            "n_interpolated_samples": res.n_filled,
            "input_encoding": res.input_encoding,
        },
        "overall": spec_dict(res.overall),
        "warnings": res.warnings,
    }
    if res.epochs:
        out["epoch_sec"] = res.epoch_sec
        out["swa_density"] = res.swa_density
        out["epochs"] = [
            {"index": ep.index, "start_sec": ep.start_sec, "end_sec": ep.end_sec,
             "peak_amp_uv": ep.peak_amp, "rejected": ep.rejected,
             "reject_reason": ep.reject_reason,
             **spec_dict(ep.spectrum)}
            for ep in res.epochs
        ]
        if res.max_amp is not None:
            out["artifact_rejection"] = {
                "max_amp_uv": res.max_amp,
                "n_kept": res.n_epochs_kept,
                "n_rejected": res.n_epochs_rejected,
            }
        if res.swa_density is not None:
            # summary uses KEPT epochs only (falls back to all if none kept).
            kept = [ep for ep in res.epochs if not ep.rejected]
            summ = kept if kept else res.epochs
            out["epoch_summary"] = {
                "n": len(summ),
                "note": ("epochs are autocorrelated; this is a within-recording "
                         "distribution, not a between-subject inferential CI"),
                "swa_relative": _summary_stats([ep.spectrum.swa_rel for ep in summ]),
                "swa_absolute_uv2": _summary_stats(
                    [ep.spectrum.swa_abs for ep in summ]),
            }
    return out


def _quality_dict(q) -> Optional[Dict[str, Any]]:
    if q is None:
        return None
    return {
        "n_samples": q.n_samples,
        "n_interpolated": q.n_interpolated,
        "frac_interpolated": q.frac_interpolated,
        "amplitude_min_uv": q.v_min, "amplitude_max_uv": q.v_max,
        "amplitude_ptp_uv": q.ptp, "mean_uv": q.mean, "rms_uv": q.rms,
        "n_clipped": q.n_clipped, "frac_clipped": q.frac_clipped,
        "n_flat": q.n_flat, "frac_flat": q.frac_flat,
        "flags": list(q.flags),
    }


def render_csv(res: AnalysisResult, comment: bool = True) -> str:
    """Tidy per-epoch (or single overall) band-power table as CSV, for stats tools.

    One row per epoch when ``--epoch`` was used, otherwise one 'overall' row. Each
    band contributes ``<band>_abs_uv2``/``<band>_rel``/``<band>_peak_hz`` columns;
    the three clinical ratios and (when ``--max-amp`` is set) per-epoch peak
    amplitude + a ``rejected`` flag are included. NaN/inf render as empty cells.
    With ``comment=True`` (default) a leading ``#`` provenance line makes the file
    self-describing; pass ``comment=False`` (CLI ``--no-comment``) for a clean
    rectangle that base-R ``read.csv``/SAS ``PROC IMPORT`` parse without options.
    """
    band_names = [bp.name for bp in res.overall.band_powers]
    # per-epoch amplitude/rejection columns only make sense when epoching is on.
    rej_on = res.max_amp is not None and bool(res.epochs)
    header = ["epoch", "start_sec", "end_sec"]
    for name in band_names:
        header += [f"{name}_abs_uv2", f"{name}_rel", f"{name}_peak_hz"]
    header += ["total_uv2", "peak_hz", "sef_hz", "entropy",
               "theta_alpha_ratio", "delta_beta_ratio", "slowing_ratio",
               "dominant", "dominant_tie"]
    if rej_on:
        header += ["peak_amp_uv", "rejected"]

    def _cell(x: Optional[float]) -> str:
        if x is None or (isinstance(x, float) and not math.isfinite(x)):
            return ""
        return repr(x)

    def _row(label: str, t0: float, t1: float, spec: Spectrum,
             peak_amp: Optional[float] = None, rejected: bool = False) -> List[str]:
        row = [label, _cell(t0), _cell(t1)]
        by = {bp.name: (bp.absolute, bp.relative, bp.peak_freq)
              for bp in spec.band_powers}
        for name in band_names:
            absv, rel, bpeak = by.get(name, (0.0, 0.0, None))
            row += [_cell(absv), _cell(rel), _cell(bpeak)]
        row += [_cell(spec.total_power), _cell(spec.peak_freq), _cell(spec.sef),
                _cell(spec.entropy),
                _cell(spec.ratios.get("theta/alpha")),
                _cell(spec.ratios.get("delta/beta")),
                _cell(spec.ratios.get("(delta+theta)/(alpha+beta)")),
                spec.dominant or "", "1" if spec.dominant_tie else "0"]
        if rej_on:
            row += [_cell(peak_amp), "1" if rejected else "0"]
        return row

    # Provenance as a SINGLE comment field (no internal commas) so a naive
    # csv.DictReader sees one bogus column, not four fake headers; the full
    # analysis parameters make an exported epoch table self-reproducible.
    bands_str = ";".join(f"{n}:{lo:g}-{hi:g}" for n, lo, hi in res.bands)
    prov = (f"# eegband v{__version__} | fs_hz={res.fs:g} ({res.fs_source}) | "
            f"nperseg={res.nperseg} noverlap={res.noverlap} nfft={res.nfft} | "
            f"detrend={res.detrend} average={res.average} | "
            f"sef={res.sef_frac * 100:g}% | bands={bands_str} | "
            f"max_amp={res.max_amp if res.max_amp is not None else ''} | "
            f"n_interpolated={res.n_filled} | "
            f"encoding={res.input_encoding or 'utf-8-sig'} | "
            f"source={res.source_file or ''}")
    buf = io.StringIO()
    w = csv.writer(buf)
    if comment:
        w.writerow([prov])
    w.writerow(header)
    if res.epochs:
        for ep in res.epochs:
            w.writerow(_row(str(ep.index), ep.start_sec, ep.end_sec, ep.spectrum,
                            ep.peak_amp, ep.rejected))
    else:
        w.writerow(_row("overall", 0.0, res.duration_sec, res.overall))
    return buf.getvalue()
