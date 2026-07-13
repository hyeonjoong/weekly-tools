"""Human-readable (Korean + English) and JSON rendering of an AnalysisResult."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .analyze import AnalysisResult, Spectrum

__all__ = ["render_text", "to_dict"]


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


def _range(lo: float, hi: float) -> str:
    def g(v: float) -> str:
        return f"{v:g}"
    return f"{g(lo)}–{g(hi)}"


def render_text(res: AnalysisResult) -> str:
    lines: List[str] = []
    L = lines.append
    bar = "=" * 74

    L(bar)
    L("  eegband — 단일채널 EEG 대역파워 분석 / Single-channel EEG band-power report")
    L(bar)

    L("")
    L("[정보 / Info]")
    L(f"    fs = {res.fs:.4g} Hz ({res.fs_source}),  N = {res.n_samples} samples,"
      f"  duration = {res.duration_sec:.2f} s")
    L(f"    Welch: nperseg = {res.nperseg}, noverlap = {res.noverlap}, "
      f"nfft = {res.nfft}, window = Hann(periodic), scaling = density (µV²/Hz)")

    _render_spectrum(L, res.overall, "[1] 대역파워 / Band power")

    # SWA highlight
    L("")
    L("[2] 슬로우파 활동 / Slow-wave activity (SWA = delta 0.5–4 Hz) — key sleep endpoint")
    L(f"    SWA absolute  = {_num(res.overall.swa_abs)} µV²")
    L(f"    SWA relative  = {_num(res.overall.swa_rel * 100.0, 1)} %")
    dom = res.overall.dominant
    if dom is None:
        L("    dominant band = n/a  (no spectral power — constant/zero signal)")
    elif dom == "delta":
        L("    dominant band = delta   ← slow-wave/delta dominant")
    else:
        L(f"    dominant band = {dom}   (delta is not the strongest band)")

    # Spectral summary
    L("")
    L("[3] 스펙트럼 요약 / Spectral summary")
    L(f"    peak frequency          = {_hz(res.overall.peak_freq)}")
    L(f"    SEF{int(round(res.overall.sef_frac * 100))} (spectral edge freq) = "
      f"{_hz(res.overall.sef)}")
    L(f"    total power ({_range(res.overall.band_lo, res.overall.band_hi)} Hz) = "
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
        L(f"    {'ep':>3}{'t0(s)':>8}{'t1(s)':>8}"
          + "".join(f"{name[:5]:>8}" for name, _, _ in res.bands)
          + f"{'peak':>8}{'SEF':>8}  dominant")
        for ep in res.epochs:
            sp = ep.spectrum
            rels = "".join(f"{sp_rel(sp, name) * 100:>7.1f}%" for name, _, _ in res.bands)
            L(f"    {ep.index:>3}{ep.start_sec:>8.1f}{ep.end_sec:>8.1f}{rels}"
              f"{(sp.peak_freq or 0):>8.2f}{(sp.sef or 0):>8.2f}  {sp.dominant or 'n/a'}")
        L("    (대역 값은 상대파워 % / band cells are relative power %)")
        if res.swa_density is not None:
            delta_dom = sum(1 for ep in res.epochs if ep.spectrum.dominant == "delta")
            L(f"    SWA density (delta-dominant epochs) = {delta_dom}/{len(res.epochs)}"
              f"  ({res.swa_density * 100:.0f} %)")
            mean_rel_delta = sum(ep.spectrum.swa_rel for ep in res.epochs) / len(res.epochs)
            L(f"    mean relative delta across epochs   = {mean_rel_delta * 100:.1f} %")

    # Warnings
    if res.warnings:
        L("")
        L("[!] 주의 / Warnings")
        for w in res.warnings:
            L(f"    - {w}")

    L("")
    return "\n".join(lines)


def sp_rel(spec: Spectrum, name: str) -> float:
    for bp in spec.band_powers:
        if bp.name == name:
            return bp.relative
    return 0.0


def _render_spectrum(L, spec: Spectrum, title: str) -> None:
    L("")
    L(title + "  (absolute µV², relative %)")
    L(f"    {'band':<8}{'range(Hz)':<12}{'abs(µV²)':>14}{'rel(%)':>10}")
    for bp in spec.band_powers:
        tag = "  ← SWA" if bp.name == "delta" else ""
        L(f"    {bp.name:<8}{_range(bp.lo, bp.hi):<12}{_num(bp.absolute):>14}"
          f"{_num(bp.relative * 100.0, 1):>10}{tag}")
    L(f"    {'total':<8}{_range(spec.band_lo, spec.band_hi):<12}"
      f"{_num(spec.total_power):>14}{'100.0':>10}")


def to_dict(res: AnalysisResult) -> Dict[str, Any]:
    """JSON-serialisable dict (used by --json)."""
    def spec_dict(spec: Spectrum) -> Dict[str, Any]:
        return {
            "band_power": [
                {"name": bp.name, "low_hz": bp.lo, "high_hz": bp.hi,
                 "absolute_uv2": bp.absolute, "relative": bp.relative}
                for bp in spec.band_powers
            ],
            "total_power_uv2": spec.total_power,
            "peak_freq_hz": spec.peak_freq,
            "sef_frac": spec.sef_frac,
            "sef_hz": spec.sef,
            "dominant_band": spec.dominant,
            "swa": {"absolute_uv2": spec.swa_abs, "relative": spec.swa_rel},
            "ratios": {k: (None if (isinstance(v, float) and not math.isfinite(v))
                           else v)
                       for k, v in spec.ratios.items()},
            "band_range_hz": [spec.band_lo, spec.band_hi],
        }

    out: Dict[str, Any] = {
        "fs_hz": res.fs,
        "fs_source": res.fs_source,
        "n_samples": res.n_samples,
        "duration_sec": res.duration_sec,
        "welch": {"nperseg": res.nperseg, "noverlap": res.noverlap,
                  "nfft": res.nfft, "window": "hann-periodic",
                  "scaling": "density"},
        "bands": [{"name": n, "low_hz": lo, "high_hz": hi}
                  for n, lo, hi in res.bands],
        "overall": spec_dict(res.overall),
        "warnings": res.warnings,
    }
    if res.epochs:
        out["epoch_sec"] = res.epoch_sec
        out["swa_density"] = res.swa_density
        out["epochs"] = [
            {"index": ep.index, "start_sec": ep.start_sec, "end_sec": ep.end_sec,
             **spec_dict(ep.spectrum)}
            for ep in res.epochs
        ]
    return out
