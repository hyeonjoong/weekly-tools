"""Human-readable (Korean + English), JSON, and CSV rendering of an AnalysisResult."""

from __future__ import annotations

import csv
import io
import math
from typing import Any, Dict, List, Optional

from . import __version__
from .analyze import AnalysisResult, Spectrum
from . import linenoise as _ln
from .stats import TrendResult

__all__ = ["render_text", "to_dict", "render_csv", "render_csv_batch",
           "render_comparison", "render_csv_summary", "render_psd_csv",
           "json_safe"]


# Epoch-wise endpoints, in report order: key -> (label, per-second→display scale,
# unit of the *value*, unit of the trend slope). The scale converts the stored
# fraction/µV² value to what is printed (relative power is shown in %).
_ENDPOINT_META = {
    "swa_relative": ("relative SWA", 100.0, "%", "%"),
    "swa_absolute_uv2": ("SWA absolute", 1.0, "µV²", "µV²"),
    "swa_absolute_log10": ("SWA absolute (log10)", 1.0, "log10 µV²", "log10 µV²"),
    "total_power_uv2": ("total power", 1.0, "µV²", "µV²"),
    "aperiodic_exponent": ("aperiodic exponent", 1.0, "", ""),
    "sef_hz": ("SEF", 1.0, "Hz", "Hz"),
    "spectral_entropy": ("spectral entropy", 1.0, "", ""),
}
# The text report keeps the four headline endpoints; every endpoint (including one per
# band) is in --json and --csv-summary, where a wall of numbers is what you want.
_TEXT_ENDPOINTS = ("swa_relative", "swa_absolute_uv2", "swa_absolute_log10",
                   "total_power_uv2", "aperiodic_exponent")


def _num(x: Optional[float], d: int = 3) -> str:
    """Fixed-point with ``d`` decimals, switching to scientific notation when that
    would print a wall of digits (1e12 µV²) or round a real value to 0.000 (1e-12)."""
    if x is None:
        return "n/a"
    if isinstance(x, float) and math.isnan(x):
        return "NaN"
    if isinstance(x, float) and math.isinf(x):
        return "inf" if x > 0 else "-inf"
    ax = abs(x)
    if ax != 0.0 and (ax >= 1e7 or ax < 10.0 ** -(d + 1)):
        return f"{x:.{max(2, d)}e}"
    return f"{x:.{d}f}"


def _ratio(x: Optional[float]) -> str:
    """A peak/background ratio, kept short. Round-off can make it astronomically big."""
    if x is None or not math.isfinite(x):
        return "n/a"
    if x >= 1e5:
        return f"{x:.1e}×"
    return f"{x:,.0f}×" if x >= 1000 else f"{x:.1f}×"


def _pval(x: Optional[float]) -> str:
    """Format a p/q value: 4 decimals normally, scientific below 1e-4.

    ``_num(p, 4)`` prints a p of 3e-5 as "0.0000", which reads as an exact zero;
    a p-value is never exactly zero and the reader needs its order of magnitude.
    """
    if x is None:
        return "n/a"
    if not math.isfinite(x):
        return "NaN"
    if x <= 0.0:
        return "<1e-300"
    if x < 1e-4:
        return f"{x:.2e}"
    return f"{x:.4f}"


def _hz(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float) and not math.isfinite(x):
        return "n/a"
    return f"{x:.2f} Hz"


def _hz2(x: Optional[float]) -> str:
    """Bare 2-dp Hz value (no unit) for compact table cells."""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "n/a"
    return f"{x:.2f}"


def _range(lo: float, hi: float) -> str:
    def g(v: float) -> str:
        return f"{v:g}"
    return f"{g(lo)}–{g(hi)}"


def _range_safe(lo: float, hi: float) -> str:
    """Like _range but never prints a reversed — or fabricated — span.

    When every band edge is above Nyquist the analysis span collapses (hi < lo). There
    is no valid range then, so say ``n/a`` rather than inventing a zero-width one.
    """
    if hi < lo:
        return "n/a"
    return _range(lo, hi)


def render_text(res: AnalysisResult) -> str:
    lines: List[str] = []
    L = lines.append
    bar = "=" * 74

    L(bar)
    L("  eegband — EEG 대역파워 분석 리포트 / EEG band-power report")
    L(bar)

    L("")
    L("[정보 / Info]")
    L(f"    eegband v{__version__}"
      + (f",  input = {res.source_file}" if res.source_file else ""))
    if res.label:
        L(f"    channel / series = {res.label}")
    L(f"    fs = {res.fs:.4g} Hz ({res.fs_source}),  N = {res.n_samples} samples,"
      f"  duration = {res.duration_sec:.2f} s")
    L(f"    Welch: nperseg = {res.nperseg}, noverlap = {res.noverlap}, "
      f"nfft = {res.nfft}, window = Hann(periodic), scaling = density (µV²/Hz)")
    L(f"    detrend = {res.detrend}, average = {res.average}, "
      f"segments averaged = {res.n_seg}")
    if res.start_offset_sec:
        L(f"    analysis window starts {res.start_offset_sec:g} s into the recording "
          "(epoch times below are relative to this window)")
    if res.warnings:
        L(f"    ⚠ {len(res.warnings)} warning(s) — see [!] at the end of this report")

    _render_quality(L, res)

    _render_spectrum(L, res.overall, "[1] 대역파워 / Band power", res.fs)

    # SWA highlight. The band is whatever actually defines SWA for this run: the
    # 'delta' band (whose edges --bands may have changed) or an explicit --swa-band.
    spec = res.overall
    L("")
    if spec.swa_source == "undefined":
        L("[2] 슬로우파 활동 / Slow-wave activity (SWA) — key sleep endpoint")
        L("    SWA = n/a — 이 분석의 대역 정의에 'delta' 대역이 없습니다 / no band "
          "named 'delta' in this band set; pass --swa-band LO-HI to define SWA.")
    else:
        src = " (--swa-band)" if spec.swa_source == "swa_band" else ""
        L(f"[2] 슬로우파 활동 / Slow-wave activity (SWA = "
          f"{_range(spec.swa_lo, spec.swa_hi)} Hz{src}) — key sleep endpoint")
        L(f"    SWA absolute  = {_num(spec.swa_abs)} µV²")
        L(f"    SWA relative  = {_num(spec.swa_rel * 100.0, 1)} %")
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
    L(f"    SEF{res.overall.sef_frac * 100:g} (spectral edge freq) = "
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

    _render_aperiodic(L, res)

    # Ratios
    L("")
    L("[5] 대역비 / Band ratios")
    L(f"    theta/alpha                        = {_num(res.overall.ratios.get('theta/alpha'))}")
    L(f"    delta/beta                         = {_num(res.overall.ratios.get('delta/beta'))}")
    L(f"    (delta+theta)/(alpha+beta) [slowing] = "
      f"{_num(res.overall.ratios.get('(delta+theta)/(alpha+beta)'))}")

    # Epochs
    if res.epochs:
        _render_epoch_table(L, res)
        _render_epoch_summary(L, res)
        _render_baseline(L, res)

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
      f"flat-run(≥{q.flat_run_min}) = {q.n_flat} ({q.frac_flat * 100:.1f}%, "
      f"longest {q.longest_flat_run})")
    if q.quant_step is not None:
        L(f"    quantisation step = {_num(q.quant_step, 4)} µV "
          f"({'n/a' if q.n_levels is None else format(q.n_levels, '.0f')} levels "
          "across the range)")
    for f in q.flags:
        L(f"    ⚠ {f}")
    _render_line_noise(L, res)


def _render_line_noise(L, res: AnalysisResult) -> None:
    """Mains 50/60 Hz assessment, inside the signal-quality section."""
    lnr = res.overall.line_noise
    if lnr is None:
        if res.line_freq_mode is None:
            return
        # Either no candidate window fits below Nyquist, or the spectrum carries no
        # measurable background at all (a constant/zero-power channel).
        df_bin = res.fs / res.nfft if res.nfft else float("nan")
        if not _ln.windows_fit(res.fs, res.line_bw):
            why = (f"창(±{res.line_bw:g} Hz)이 (0, {res.fs / 2.0:g}) Hz 안에 들어가는 "
                   f"고조파가 없음 / no mains harmonic window of ±{res.line_bw:g} Hz "
                   f"fits below Nyquist ({res.fs / 2.0:g} Hz)")
        elif res.line_bw < df_bin:
            why = (f"창(±{res.line_bw:g} Hz)이 주파수 해상도({df_bin:.3g} Hz)보다 좁음 / "
                   f"the ±{res.line_bw:g} Hz window is narrower than the "
                   f"{df_bin:.3g} Hz bin spacing")
        elif res.fs / 2.0 <= 51.0:
            why = ("Nyquist 가 너무 낮아 50/60 Hz 창이 들어가지 않음 / no 50/60 Hz "
                   f"window fits below this recording's Nyquist ({res.fs / 2.0:g} Hz)")
        else:
            why = ("스펙트럼에 측정 가능한 배경이 없음(파워 0/상수 신호) / no measurable "
                   "background in this spectrum (zero-power or constant signal)")
        L(f"    전원잡음 / mains line noise: 확인 불가 — {why}.")
        return
    how = "auto-detected" if lnr.source == "auto" else "specified"
    suspects = lnr.suspect_aliases()
    if not lnr.detected:
        # "≤ max_ratio" would contradict itself when a loud but *aliased* harmonic was
        # deliberately not flagged, so quote the ratio of the harmonics that were
        # actually eligible and name the suspects separately.
        eligible = [p.ratio for p in lnr.peaks
                    if p.ratio is not None and not (p.aliased and lnr.source == "auto")]
        if eligible:
            how_much = (f"peak/background ≤ {_num(max(eligible), 1)}× "
                        f"(threshold {lnr.threshold:g}×)")
        elif all(p.aliased for p in lnr.peaks):
            # Every harmonic is an alias — there is no in-band fundamental to quote a
            # ratio for, and "≤ n/a×" would read as a measurement.
            how_much = (f"이 fs 에서는 대역 내 기본파가 없습니다 / no in-band "
                        f"fundamental at fs={2 * lnr.nyquist_hz:g} Hz")
        else:
            # In-band harmonics exist but none could be measured — the usual cause is
            # a --line-bw narrower than the bin spacing, leaving too few shoulder bins.
            df_bin = res.fs / res.nfft if res.nfft else float("nan")
            how_much = (f"측정 불가 — 창(±{lnr.bandwidth:g} Hz)이 주파수 해상도"
                        f"({df_bin:.3g} Hz)에 비해 좁아 배경을 추정할 수 없습니다 / "
                        f"not measurable: the ±{lnr.bandwidth:g} Hz window is too "
                        f"narrow at a {df_bin:.3g} Hz bin spacing")
        L(f"    전원잡음 / mains line noise: none detected at {lnr.f0:g} Hz ({how}); "
          + how_much)
        _render_suspects(L, lnr, suspects)
        return
    state = ("제거됨 / REMOVED by spectral interpolation" if lnr.removed
             else "검출됨 (제거 안 함) / detected, NOT removed — add --notch")
    L(f"    전원잡음 / mains line noise @ {lnr.f0:g} Hz ({how}) — {state}")
    L(f"      {'harmonic':<12}{'freq(Hz)':>10}{'peak/bg':>10}{'excess(µV²)':>14}"
      f"   {'bands affected'}")
    band_names = [(n, lo, hi) for n, lo, hi in res.bands]
    for p in lnr.peaks:
        tag = f"×{p.order}" + (" (aliased)" if p.aliased else "")
        hit = [n for n, lo, hi in band_names
               if min(hi, p.freq_hz + lnr.bandwidth) - max(lo, p.freq_hz -
                                                           lnr.bandwidth) > 0]
        if not p.detected:
            note = "-"
        elif hit:
            note = "← " + ", ".join(hit)
        else:
            note = "← (모든 대역 밖 / outside every reported band)"
        L(f"      {tag:<12}{p.freq_hz:>10.4g}{_num(p.ratio, 1):>10}"
          f"{_num(p.excess_uv2, 3):>14}   {note}")
    # How much of each band is electrical rather than neural.
    contam = []
    for name, lo, hi in band_names:
        bp = next((b for b in res.overall.band_powers if b.name == name), None)
        if bp is None:
            continue
        exc = lnr.excess_in(lo, hi)
        if exc <= 0:
            continue
        # After removal the reported power no longer contains the excess, so express
        # it against what the band WOULD have been.
        base = bp.absolute + exc if lnr.removed else bp.absolute
        if base > 0:
            contam.append(f"{name} {min(exc / base, 1.0) * 100:.1f}%")
    if contam:
        verb = "제거된 비율 / share removed" if lnr.removed else \
            "전기잡음 비율 / share that is electrical"
        L(f"      {verb}: {', '.join(contam)}")
    if any(p.aliased and p.detected for p in lnr.peaks):
        L(f"      ⚠ {lnr.f0:g} Hz > Nyquist {lnr.nyquist_hz:g} Hz — the peak is an "
          "ALIAS folded down from the mains. At that frequency an alias and a real "
          "rhythm are the same measurement, so removing it also removes any genuine "
          "activity there.")
    _render_suspects(L, lnr, suspects)


def _render_suspects(L, lnr, suspects) -> None:
    """Loud aliased harmonics that auto-detection refused to flag (never notched)."""
    if not suspects:
        return
    where = ", ".join(f"{p.freq_hz:.4g} Hz ({_ratio(p.ratio)}, {p.nominal_hz:g} Hz "
                      "접힘)" for p in suspects)
    L(f"      ⓘ 에일리어싱 의심 / suspected mains alias at {where} — 자동 판정에서 "
      "제외했습니다(그 자리에서는 전원 접힘과 진짜 리듬이 같은 측정값). 제거하려면 "
      f"--line-freq {lnr.f0:g} 를 명시하세요.")


def render_comparison(results: List[AnalysisResult]) -> str:
    """One-line-per-series comparison table (multi-channel or multi-file runs).

    The point of a batch run is the comparison, so the endpoints that matter — total
    power, every band's relative power, the dominant band, SEF, the aperiodic exponent
    and (with ``--epoch``) SWA density — are put side by side instead of leaving the
    reader to diff long reports.
    """
    lines: List[str] = []
    L = lines.append
    if not results:
        return ""
    first = results[0]
    band_names = [bp.name for bp in first.overall.band_powers]
    ap_on = any(r.aperiodic_mode is not None for r in results)
    ep_on = any(r.swa_density is not None for r in results)
    multi_file = len({r.source_file for r in results}) > 1

    names = [(r.label or (r.source_file or "series")) for r in results]
    # Width follows the longest label (capped) so real IDs like
    # 'SUBJ-014_visit2_C3-A2' stay distinguishable instead of colliding at 16 chars.
    w = max(8, min(34, max(len(n) for n in names)))
    bar_len = max(74, w + 12 * len(band_names) + 60)
    L("=" * bar_len)
    L(f"  [비교 / Series comparison]  n = {len(results)}")
    L("=" * bar_len)
    head = f"    {'series':<{w}}{'fs':>8}{'fs_src':>10}{'dur(s)':>9}{'total(µV²)':>13}"
    head += "".join(f"{n[:5] + '%':>8}" for n in band_names)
    head += f"{'dominant':>10}{'SEF':>8}"
    if ap_on:
        head += f"{'expo':>7}{'R²':>6}"
    if ep_on:
        head += f"{'δ-dom':>7}{'kept':>8}"
    L(head)
    for res, name in zip(results, names):
        src = res.fs_source.split(" ")[0][:9]
        row = (f"    {name[:w]:<{w}}{res.fs:>8.4g}{src:>10}"
               f"{res.duration_sec:>9.1f}{res.overall.total_power:>13.3f}")
        for bp in res.overall.band_powers:
            if _band_unavailable(bp, res.fs):
                row += f"{'n/a':>8}"          # above Nyquist: never measured
            else:
                row += f"{bp.relative * 100:>7.1f}%"
        dom = (res.overall.dominant or "n/a") + ("*" if res.overall.dominant_tie
                                                 else "")
        row += f"{dom:>10}"
        row += (f"{res.overall.sef:>8.2f}" if res.overall.sef is not None
                else f"{'n/a':>8}")
        if ap_on:
            fit = res.overall.aperiodic
            if fit is None:
                row += f"{'n/a':>7}{'n/a':>6}"
            else:
                row += f"{fit.exponent:>7.2f}{fit.r2:>6.2f}"
        if ep_on:
            row += (f"{res.swa_density * 100:>6.0f}%"
                    if res.swa_density is not None else f"{'n/a':>7}")
            if res.epochs:
                flag = "" if res.qc_pass else "!"
                row += f"{res.n_epochs_kept}/{len(res.epochs)}{flag:>2}"
            else:
                row += f"{'n/a':>8}"
        L(row)
    if any(_band_unavailable(bp, r.fs)
           for r in results for bp in r.overall.band_powers):
        L("    (n/a = 그 채널의 Nyquist 위 대역 — 측정 불가(0이 아님) / above that "
          "channel's Nyquist: not measured, NOT zero)")
    if ep_on and any(not r.qc_pass for r in results):
        L("    (kept 열의 '!' = 전 에폭 아티팩트 제외 → 요약 없음 / QC failure)")
    if multi_file:
        L("")
        L("    파일 / files:")
        for res, name in zip(results, names):
            L(f"      {name[:w]:<{w}} {res.source_file}")
    L("")
    return "\n".join(lines)


def _render_aperiodic(L, res: AnalysisResult) -> None:
    """Section [4]: the fitted 1/f background and background-removed band power."""
    if res.aperiodic_mode is None:
        return
    spec = res.overall
    fit = spec.aperiodic
    L("")
    L("[4] 비주기(1/f) 배경 + 진동성 파워 / Aperiodic background & oscillatory power")
    if fit is None:
        L("    1/f 적합 불가 (유효 빈 < 3 또는 파워 0) / no aperiodic fit available")
        return
    req = (f" (요청 / requested {res.fit_range[0]:g}–{res.fit_range[1]:g})"
           if res.fit_range else "")
    L(f"    fit = {fit.fit_lo:.2f}–{fit.fit_hi:.2f} Hz{req}, mode = {fit.mode}, "
      f"bins used = {fit.n_used}/{fit.n_total}")
    L("    model: PSD(f) = 10^offset · f^(−exponent)   (exponent > 0 = falling with f)")
    L(f"    exponent (χ) = {_num(fit.exponent)} ± {_num(fit.exponent_se)} (SE, "
      "낙관적 하한 / optimistic: neighbouring Welch bins are not independent)")
    L(f"    offset (log10 µV²/Hz @1 Hz) = {_num(fit.offset)}"
      + ("   ⚠ extrapolated: the fit starts at "
         f"{fit.fit_lo:.3g} Hz" if fit.fit_lo > 2.0 else ""))
    L(f"    fit R² = {_num(fit.r2)} (적합에 쓴 빈 / fitted bins), "
      f"{_num(fit.r2_full)} (전체 빈, 진동 포함 / all bins)")
    if spec.aperiodic_halves is not None:
        lo_e, hi_e = spec.aperiodic_halves
        L(f"    slope by half-range: {_num(lo_e, 2)} (lower half) vs "
          f"{_num(hi_e, 2)} (upper half)"
          + ("   ⚠ knee/bend — a single exponent is an average"
             if abs(hi_e - lo_e) > 0.75 else ""))
    L(f"    {'band':<8}{'osc(µV²)':>14}{'osc(%osc)':>11}{'peak(Hz)':>10}"
      f"{'height':>9}")
    for bp in spec.band_powers:
        osc = "n/a" if bp.osc_absolute is None else _num(bp.osc_absolute)
        orel = ("n/a" if bp.osc_relative is None
                else _num(bp.osc_relative * 100.0, 1))
        if bp.adj_peak_prominent:
            pk = _hz2(bp.adj_peak_freq)
            ht = _num(bp.adj_peak_height, 2)
        else:
            pk = ht = "n/a"
        L(f"    {bp.name:<8}{osc:>14}{orel:>11}{pk:>10}{ht:>9}")
    tot = "n/a" if spec.osc_total is None else _num(spec.osc_total)
    L(f"    {'total':<8}{tot:>14}")
    L("    (osc = 배경 제거 후 진동성 파워 ∫max(PSD−1/f,0);  osc(%osc) = 전체 진동성"
      " 파워 대비 비율(총파워 아님);  height = 배경 대비 log10 상승)")
    L("    (n/a = 적합 범위가 그 대역을 완전히 덮지 않음 / band not fully inside the "
      "fit range)")


def _render_epoch_table(L, res: AnalysisResult) -> None:
    """Section [6]: one row per epoch."""
    L("")
    L(f"[6] 에폭별 / Per-epoch  (epoch = {res.epoch_sec:g} s, "
      f"n_epochs = {len(res.epochs)})")
    amp_on = res.max_amp is not None
    grad_on = res.max_grad is not None
    rej_on = amp_on or grad_on
    ap_on = res.aperiodic_mode is not None
    head = (f"    {'ep':>3}{'t0(s)':>8}{'t1(s)':>8}"
            + "".join(f"{name[:5]:>8}" for name, _, _ in res.bands)
            + f"{'peak':>8}{'SEF':>8}")
    if ap_on:
        head += f"{'expo':>7}"
    head += "  dominant"
    if amp_on:
        head += "  |amp|"
    if grad_on:
        head += "   |Δ|"
    if rej_on:
        head += "  rej"
    L(head)
    for ep in res.epochs:
        sp = ep.spectrum
        rels = "".join(f"{sp_rel(sp, name) * 100:>7.1f}%"
                       for name, _, _ in res.bands)
        dom_cell = (sp.dominant or "n/a") + ("*" if sp.dominant_tie else "")
        pk = f"{sp.peak_freq:>8.2f}" if sp.peak_freq is not None else f"{'n/a':>8}"
        se = f"{sp.sef:>8.2f}" if sp.sef is not None else f"{'n/a':>8}"
        row = (f"    {ep.index:>3}{ep.start_sec:>8.1f}{ep.end_sec:>8.1f}{rels}"
               f"{pk}{se}")
        if ap_on:
            row += (f"{sp.aperiodic.exponent:>7.2f}" if sp.aperiodic is not None
                    else f"{'n/a':>7}")
        row += f"  {dom_cell}"
        if amp_on:
            row += f"  {ep.peak_amp:>6.1f}"
        if grad_on:
            row += f"  {ep.max_grad:>6.1f}"
        if rej_on and ep.rejected:
            row += "  ✗REJ"
        L(row)
    L("    (대역 값은 상대파워 % / band cells are relative power %"
      + (", expo = 1/f 지수" if ap_on else "") + ")")
    if rej_on:
        crit = []
        if amp_on:
            crit.append(f"|amp| > {res.max_amp:g} µV")
        if grad_on:
            crit.append(f"|Δamp| > {res.max_grad:g} µV/sample")
        L(f"    artifact rejection ({' or '.join(crit)}): "
          f"kept {res.n_epochs_kept}/{len(res.epochs)}, "
          f"rejected {res.n_epochs_rejected}  "
          "→ 요약 통계는 채택 에폭만 사용 / summary uses kept epochs only")


def _fmt_ci(lo: float, hi: float, d: int = 3) -> str:
    return f"[{_num(lo, d)}, {_num(hi, d)}]"


def _render_endpoint(L, key: str, st: Dict[str, float]) -> None:
    """Render one endpoint's descriptive block (mean/SD/SEM/CI, median/IQR, ρ-adj)."""
    label, scale, unit, _ = _ENDPOINT_META[key]
    u = f" {unit}" if unit else ""
    d = 1 if scale == 100.0 else 3
    n = int(st["n"])
    L(f"    {label} across epochs = {_num(st['mean'] * scale, d)} "
      f"± {_num(st['sd'] * scale, d)}{u} (SD, n-1),  "
      f"SEM {_num(st['sem'] * scale, d)}{u},  "
      f"95% CI {_fmt_ci(st['ci_lo'] * scale, st['ci_hi'] * scale, d)}{u}  (n={n})")
    L(f"      median {_num(st['median'] * scale, d)}{u}, "
      f"IQR {_fmt_ci(st['q1'] * scale, st['q3'] * scale, d)}, "
      f"range {_fmt_ci(st['min'] * scale, st['max'] * scale, d)}")
    rho = st.get("rho1", float("nan"))
    if n > 2 and isinstance(rho, float) and math.isfinite(rho):
        L(f"      자기상관 보정 / autocorr-adjusted: ρ₁ = {_num(rho, 2)}, "
          f"n_eff = {_num(st['n_eff'], 1)}, "
          f"95% CI {_fmt_ci(st['ci_lo_adj'] * scale, st['ci_hi_adj'] * scale, d)}{u}")


def _render_trends(L, res: AnalysisResult) -> None:
    """Mann–Kendall + Theil–Sen trend table across epochs (slopes per hour)."""
    trends = [(k, res.epoch_trends[k]) for k in _TEXT_ENDPOINTS
              if k in res.epoch_trends]
    if not trends:
        return
    # Report the slope over a time unit the recording actually spans: quoting a
    # per-hour drift for a 40 s recording extrapolates 90-fold and reads as nonsense.
    summ = res.summary_epochs()
    span = (summ[-1].start_sec - summ[0].start_sec) if len(summ) > 1 else 0.0
    if span >= 1800.0:
        t_scale, t_name = 3600.0, "h"
    elif span >= 60.0:
        t_scale, t_name = 60.0, "min"
    else:
        t_scale, t_name = 1.0, "s"
    L(f"    시간 추세 / Trend across epochs (Mann–Kendall 검정 + Theil–Sen 기울기, "
      f"per {t_name})")
    L(f"      {'endpoint':<20}{'slope/' + t_name:>12}"
      f"{'95% CI (slope/' + t_name + ')':>26}{'tau':>8}{'p':>10}")
    for key, tr in trends:
        label, scale, unit, _ = _ENDPOINT_META[key]
        f = t_scale * scale
        u = f"{unit}/{t_name}" if unit else f"1/{t_name}"
        L(f"      {label:<20}{_num(tr.slope * f, 3):>12}"
          f"{_fmt_ci(tr.slope_lo * f, tr.slope_hi * f):>26}"
          f"{_num(tr.tau, 3):>8}{_num(tr.p, 4):>10}   {u}")
    L("      (Mann–Kendall: 순위기반 단조추세 검정 / nonparametric monotone-trend "
      "test; p는 정규근사. JSON은 항상 초당 기울기 / JSON slopes are per second)")


def _render_epoch_summary(L, res: AnalysisResult) -> None:
    """SWA density, per-endpoint descriptive blocks, and the trend table."""
    if not res.qc_pass:
        L("    ⚠ QC 실패: 모든 에폭이 아티팩트로 제외되어 요약/추세를 계산하지 "
          "않았습니다 / every epoch was rejected — no summary is reported.")
        return
    if res.swa_density is None:
        return
    summ = res.summary_epochs()
    n_ep = len(summ)
    delta_dom = sum(1 for ep in summ if ep.spectrum.dominant == "delta")
    # NOT "slow-wave density" in the sleep-medicine sense (events per minute): this is
    # the fraction of epochs in which delta happens to be the strongest band, which
    # saturates at 100% on any normal 1/f background. Named accordingly.
    L(f"    delta-dominant epochs = {delta_dom}/{n_ep}"
      f"  ({res.swa_density * 100:.0f} %)   (a.k.a. 'SWA density'; NOT slow-wave "
      "events per minute)")
    for key in _TEXT_ENDPOINTS:
        st = res.epoch_summary.get(key)
        if st is not None:
            _render_endpoint(L, key, st)
    # Honesty: epochs from one recording are autocorrelated, so this spread is a
    # within-recording distribution, NOT a between-subject inferential CI.
    L("      (에폭은 자기상관 — 기록 내 분포이며 피험자간 추론 CI 아님 / "
      "within-recording spread, not a between-subject CI)")
    _render_trends(L, res)


def _endpoint_meta(key: str):
    """(label, display scale, unit) for any endpoint key, including per-band ones.

    ``_ENDPOINT_META`` only names the core endpoints; the per-band ``alpha_relative`` /
    ``alpha_absolute_uv2`` keys are generated from whatever ``--bands`` were used, so
    their units are derived from the suffix rather than tabulated.
    """
    meta = _ENDPOINT_META.get(key)
    if meta:
        return meta[0], meta[1], meta[2]
    if key.endswith("_relative"):
        return f"{key[:-len('_relative')]} relative", 100.0, "%"
    if key.endswith("_absolute_uv2"):
        return f"{key[:-len('_absolute_uv2')]} absolute", 1.0, "µV²"
    return key, 1.0, ""


def _render_baseline(L, res: AnalysisResult) -> None:
    """Section [7]: each endpoint's change from this recording's own baseline."""
    if res.baseline_sec is None or not res.baseline_contrasts:
        return
    L("")
    L(f"[7] 기저 대비 변화 / Change from baseline  (baseline = 0–"
      f"{res.baseline_sec:g} s)")
    L(f"    baseline epochs = {res.n_baseline},  post epochs = {res.n_post},  "
      f"BH-FDR family m = {res.baseline_family_size}")
    L(f"      {'endpoint':<20}{'baseline':>12}{'post':>12}{'Δ':>12}{'Δ%':>9}"
      f"{'95% CI (Δ)':>26}{'g':>9}{'n_eff':>12}{'df':>7}{'p':>12}{'q(FDR)':>12}")
    keys = [k for k in _TEXT_ENDPOINTS if k in res.baseline_contrasts]
    keys += [k for k in res.baseline_contrasts if k not in keys]
    any_adj = False
    for key in keys:
        cr = res.baseline_contrasts[key]
        label, scale, unit = _endpoint_meta(key)
        any_adj = any_adj or cr.adjusted
        star = "*" if (math.isfinite(cr.q) and cr.q < 0.05) else " "
        # Δ% is undefined (not zero, not NaN-worthy) when the baseline mean is not a
        # positive quantity — an exponent that averages -0.01 has no meaningful
        # "percent change".
        pct = _num(cr.pct_change, 1) if math.isfinite(cr.pct_change) else "n/a"
        L(f"      {label:<20}{_num(cr.mean_a * scale, 3):>12}"
          f"{_num(cr.mean_b * scale, 3):>12}{_num(cr.diff * scale, 3):>12}"
          f"{pct:>9}"
          f"{_fmt_ci(cr.ci_lo * scale, cr.ci_hi * scale):>26}"
          f"{_num(cr.hedges_g, 2):>9}"
          f"{f'{cr.n_eff_a:.1f}/{cr.n_eff_b:.1f}':>12}{_num(cr.df, 1):>7}"
          f"{_pval(cr.p):>12}{_pval(cr.q):>12}{star}"
          + (f"  {unit}" if unit else ""))
    L("      (Welch t-검정, Hedges' g 효과크기, BH-FDR 보정 q; * = q<0.05 / Welch "
      "t-test, Hedges' g, Benjamini–Hochberg q over the m endpoints above)")
    L("      (g 는 이 기록의 에폭간 변동으로 표준화한 값입니다 — 문헌의 피험자간 g 와 "
      "직접 비교하지 마세요 / g is standardised against within-recording epoch "
      "variability, NOT comparable to a between-subject g)")
    L("      (n_eff = 자기상관 보정 유효표본수(기저/이후). n_eff = n 이면 보정이 "
      "일어나지 않은 것입니다 — ρ̂≤0 이거나 창이 짧아 하한 2에 걸린 경우 / n_eff = n "
      "means NO adjustment was applied)")
    if any_adj:
        L("      (연속 에폭은 독립이 아니므로 AR(1) 유효표본수로 각 군의 분산·SE·CI와 "
          "자유도를 보정했습니다. 보정 폭은 ρ̂ 와 에폭 수에 따라 달라지며, 창이 짧으면 "
          "거의 0일 수 있습니다 / the AR(1) effective n widens each group's SE and CI "
          "and lowers the d.o.f.; the size of that adjustment depends on ρ̂ and can be "
          "negligible for short windows)")
    L("      (이는 한 기록 내 전·후 비교입니다. 위약 대조·피험자간 추론이 아닙니다 / "
      "a within-recording before/after contrast, NOT a placebo-controlled or "
      "between-subject inference)")


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


def _band_unavailable(bp, fs: Optional[float]) -> bool:
    """True when a band lies entirely above Nyquist, i.e. it was never measured.

    Such a band integrates to exactly 0, which is indistinguishable from "measured and
    empty" unless it is marked — and in a channel-comparison table those two readings
    lead to opposite conclusions.
    """
    return fs is not None and fs > 0 and bp.lo >= fs / 2.0


def _render_spectrum(L, spec: Spectrum, title: str,
                     fs: Optional[float] = None) -> None:
    L("")
    L(title + "  (absolute µV², relative %, prominent in-band peak Hz)")
    L(f"    {'band':<8}{'range(Hz)':<12}{'abs(µV²)':>14}{'rel(%)':>10}{'peak(Hz)':>10}")
    swa_name = None
    if spec.swa_source == "delta":
        swa_name = "delta"
    for bp in spec.band_powers:
        tag = "  ← SWA" if bp.name == swa_name else ""
        if _band_unavailable(bp, fs):
            L(f"    {bp.name:<8}{_range(bp.lo, bp.hi):<12}{'n/a':>14}{'n/a':>10}"
              f"{'n/a':>10}   (> Nyquist {fs / 2:g} Hz — not measured)")
            continue
        # only report the peak when it is a genuine hump, not a 1/f-slope argmax.
        peak = _hz2(bp.peak_freq) if bp.peak_prominent else "n/a"
        L(f"    {bp.name:<8}{_range(bp.lo, bp.hi):<12}{_num(bp.absolute):>14}"
          f"{_num(bp.relative * 100.0, 1):>10}{peak:>10}{tag}")
    L(f"    {'total':<8}{_range_safe(spec.band_lo, spec.band_hi):<12}"
      f"{_num(spec.total_power):>14}{_num(spec.rel_sum * 100.0, 1):>10}")


def _aperiodic_dict(spec: Spectrum) -> Optional[Dict[str, Any]]:
    fit = spec.aperiodic
    if fit is None:
        return None
    return {
        "exponent": fit.exponent,
        "exponent_se": fit.exponent_se,
        "offset_log10_uv2_hz_at_1hz": fit.offset,
        "r2": fit.r2,
        "r2_all_bins": fit.r2_full,
        "fit_lo_hz": fit.fit_lo,
        "fit_hi_hz": fit.fit_hi,
        "n_bins_used": fit.n_used,
        "n_bins_total": fit.n_total,
        "mode": fit.mode,
        "n_trim_iterations": fit.n_trim_iter,
        "model": "psd(f) = 10^offset * f^(-exponent)",
    }


def _line_noise_dict(spec: Spectrum, bands) -> Optional[Dict[str, Any]]:
    lnr = spec.line_noise
    if lnr is None:
        return None
    return {
        "fundamental_hz": lnr.f0,
        "source": lnr.source,
        "bandwidth_hz": lnr.bandwidth,
        "ratio_threshold": lnr.threshold,
        "detected": lnr.detected,
        "removed": lnr.removed,
        "max_ratio": lnr.max_ratio,
        "harmonics": [
            {"order": p.order, "nominal_hz": p.nominal_hz, "freq_hz": p.freq_hz,
             "aliased": p.aliased, "ratio": p.ratio, "background_uv2_per_hz":
             p.background, "peak_uv2_per_hz": p.peak_psd,
             "excess_uv2": p.excess_uv2, "detected": p.detected}
            for p in lnr.peaks
        ],
        "excess_uv2_by_band": {n: lnr.excess_in(lo, hi) for n, lo, hi in bands},
        "note": ("excess_uv2 is the power inside +-bandwidth of the harmonic above the "
                 "local background; when removed=true it has already been subtracted "
                 "from every band power reported here"),
    }


def _contrast_dict(cr) -> Dict[str, Any]:
    return {
        "n_baseline": cr.n_a, "n_post": cr.n_b,
        "mean_baseline": cr.mean_a, "mean_post": cr.mean_b,
        "sd_baseline": cr.sd_a, "sd_post": cr.sd_b,
        "diff": cr.diff, "pct_change": cr.pct_change,
        "se": cr.se, "df": cr.df, "t": cr.t, "p_two_sided": cr.p,
        "ci_lo": cr.ci_lo, "ci_hi": cr.ci_hi,
        "hedges_g": cr.hedges_g,
        "n_eff_baseline": cr.n_eff_a, "n_eff_post": cr.n_eff_b,
        "autocorr_adjusted": cr.adjusted,
        "q_bh_fdr": cr.q,
    }


def _trend_dict(tr: TrendResult) -> Dict[str, Any]:
    return {
        "n": tr.n, "mann_kendall_s": tr.s, "var_s": tr.var_s, "z": tr.z,
        "p_two_sided": tr.p, "kendall_tau_b": tr.tau,
        "theil_sen_slope_per_sec": tr.slope,
        "theil_sen_slope_ci_per_sec": [tr.slope_lo, tr.slope_hi],
        "x_unit": tr.x_unit,
    }


def json_safe(obj: Any) -> Any:
    """Recursively replace non-finite floats with None so the result is valid JSON.

    ``json.dumps`` happily writes bare ``NaN``/``Infinity``, which RFC 8259 does not
    allow and which R's ``jsonlite``, JavaScript's ``JSON.parse`` and ``jq`` all
    refuse — precisely on the degenerate recordings a user most wants to inspect
    (n<3 epochs give a NaN ρ̂; a constant channel gives NaN ratios).
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return obj


def to_dict(res: AnalysisResult) -> Dict[str, Any]:
    """JSON-serialisable dict (used by --json).

    All non-finite floats are converted to ``null`` (see :func:`json_safe`), so the
    output always parses with a strict JSON reader.
    """
    def spec_dict(spec: Spectrum) -> Dict[str, Any]:
        return {
            "band_power": [
                {"name": bp.name, "low_hz": bp.lo, "high_hz": bp.hi,
                 "absolute_uv2": bp.absolute, "relative": bp.relative,
                 "peak_freq_hz": bp.peak_freq,
                 "peak_prominent": bp.peak_prominent,
                 "oscillatory_uv2": bp.osc_absolute,
                 "oscillatory_relative": bp.osc_relative,
                 "adjusted_peak_freq_hz": bp.adj_peak_freq,
                 "adjusted_peak_height_log10": bp.adj_peak_height,
                 "adjusted_peak_prominent": bp.adj_peak_prominent}
                for bp in spec.band_powers
            ],
            "aperiodic": _aperiodic_dict(spec),
            "oscillatory_total_uv2": spec.osc_total,
            "total_power_uv2": spec.total_power,
            "relative_sum": spec.rel_sum,
            "peak_freq_hz": spec.peak_freq,
            "spectral_entropy": spec.entropy,
            "sef_frac": spec.sef_frac,
            "sef_hz": spec.sef,
            "dominant_band": spec.dominant,
            "dominant_tie": spec.dominant_tie,
            "swa": {
                "absolute_uv2": (spec.swa_abs if spec.swa_source != "undefined"
                                 else None),
                "relative": (spec.swa_rel if spec.swa_source != "undefined"
                             else None),
                "band_hz": ([spec.swa_lo, spec.swa_hi]
                            if spec.swa_lo is not None else None),
                "source": spec.swa_source,
            },
            "ratios": {k: (None if (isinstance(v, float) and not math.isfinite(v))
                           else v)
                       for k, v in spec.ratios.items()},
            "band_range_hz": ([spec.band_lo, spec.band_hi]
                              if spec.band_hi >= spec.band_lo else None),
            "aperiodic_half_range_exponents": (
                list(spec.aperiodic_halves) if spec.aperiodic_halves else None),
            "line_noise": _line_noise_dict(spec, res.bands),
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
                  "average": res.average, "n_segments": res.n_seg,
                  "freq_resolution_hz": (res.fs / res.nfft) if res.nfft else None},
        "bands": [{"name": n, "low_hz": lo, "high_hz": hi}
                  for n, lo, hi in res.bands],
        "signal_quality": _quality_dict(res.quality),
        "provenance": {
            "sef_percent": res.sef_frac * 100.0,
            "n_interpolated_samples": res.n_filled,
            "input_encoding": res.input_encoding,
            "aperiodic_mode": res.aperiodic_mode,
            "aperiodic_fit_range_hz": (list(res.fit_range) if res.fit_range
                                       else None),
            "swa_band_hz": list(res.swa_band) if res.swa_band else None,
            "line_freq_mode": res.line_freq_mode,
            "line_bandwidth_hz": res.line_bw,
            "notch_applied": res.notch,
            "analysis_start_sec": res.start_offset_sec,
            "analysed_duration_sec": res.duration_sec,
            "nyquist_hz": res.fs / 2.0,
            "units": ("relative/oscillatory_relative are FRACTIONS (0-1); "
                      "absolute powers are uV^2; slopes are per second"),
        },
        "qc": {
            "pass": res.qc_pass,
            "n_epochs": len(res.epochs),
            "n_kept": res.n_epochs_kept,
            "n_rejected": res.n_epochs_rejected,
            "max_amp_uv": res.max_amp,
            "max_gradient_uv": res.max_grad,
        },
        "overall": spec_dict(res.overall),
        "warnings": res.warnings,
    }
    if res.label:
        out["label"] = res.label
    if res.epochs:
        out["epoch_sec"] = res.epoch_sec
        out["swa_density"] = res.swa_density
        out["epochs"] = [
            {"index": ep.index, "start_sec": ep.start_sec, "end_sec": ep.end_sec,
             "peak_amp_uv": ep.peak_amp, "max_gradient_uv": ep.max_grad,
             "rejected": ep.rejected, "reject_reason": ep.reject_reason,
             **spec_dict(ep.spectrum)}
            for ep in res.epochs
        ]
        if res.max_amp is not None or res.max_grad is not None:
            out["artifact_rejection"] = {
                "max_amp_uv": res.max_amp,
                "max_gradient_uv": res.max_grad,
                "n_kept": res.n_epochs_kept,
                "n_rejected": res.n_epochs_rejected,
            }
        if res.epoch_summary:
            summary: Dict[str, Any] = {
                "n": res.n_summary,
                "note": ("epochs are autocorrelated; this is a within-recording "
                         "distribution, not a between-subject inferential CI. The "
                         "*_adj fields widen the CI using the AR(1) effective "
                         "sample size n_eff = n(1-rho)/(1+rho)."),
            }
            summary.update({k: dict(v) for k, v in res.epoch_summary.items()})
            summary["units_note"] = (
                "*_relative endpoints are fractions (0-1), *_uv2 are uV^2, "
                "*_log10 are log10(uV^2); 'adjusted' is 1 when the "
                "autocorrelation correction was actually applied")
            if res.epoch_trends:
                summary["trends"] = {
                    k: _trend_dict(tr) for k, tr in res.epoch_trends.items()}
                summary["trend_note"] = (
                    "Mann-Kendall rank trend test (tie- and continuity-corrected "
                    "normal approximation) with a Theil-Sen slope per second; x is "
                    "the epoch start time in seconds.")
            out["epoch_summary"] = summary
        if res.baseline_sec is not None:
            out["baseline_contrast"] = {
                "baseline_sec": res.baseline_sec,
                "n_baseline": res.n_baseline,
                "n_post": res.n_post,
                "bh_fdr_family_size": res.baseline_family_size,
                "endpoints": {k: _contrast_dict(c)
                              for k, c in res.baseline_contrasts.items()},
                "note": ("post-baseline vs baseline epochs of the SAME recording: "
                         "Welch t-test on AR(1) effective sample sizes (consecutive "
                         "epochs are not independent), Hedges' g, and Benjamini-"
                         "Hochberg q over bh_fdr_family_size endpoints (structural "
                         "duplicates such as swa_* vs delta_* are counted once and "
                         "share a q). Band-power endpoints are strongly correlated, "
                         "so BH is approximate here. This is a "
                         "within-recording before/after contrast, not a placebo-"
                         "controlled or between-subject inference."),
            }
    # Non-finite floats (a NaN rho for n<3, NaN ratios for a constant channel) are
    # converted to null here, so json.dumps(..., allow_nan=False) always succeeds and
    # strict readers (R jsonlite, jq, JS) can load the file.
    return json_safe(out)


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
    band contributes ``<band>_abs_uv2``/``<band>_rel``/``<band>_peak_hz`` columns —
    plus ``<band>_osc_uv2``/``<band>_adj_peak_hz`` (aperiodic-corrected) unless the
    1/f fit was disabled; the three clinical ratios, the aperiodic exponent/offset/R²
    and (when ``--max-amp``/``--max-grad`` are set) per-epoch artifact metrics + a
    ``rejected`` flag are included. NaN/inf render as empty cells. With
    ``comment=True`` (default) a leading ``#`` provenance line makes the file
    self-describing; pass ``comment=False`` (CLI ``--no-comment``) for a clean
    rectangle that base-R ``read.csv``/SAS ``PROC IMPORT`` parse without options.
    """
    return render_csv_batch([res], comment=comment)


def _cell(x: Optional[float]) -> str:
    """CSV cell for a float: empty for None/NaN/inf, full ``repr`` precision else."""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return ""
    return repr(x)


# Leading characters that make Excel/LibreOffice treat a cell as a formula.
_FORMULA_LEAD = ("=", "+", "@", "\t", "\r")


def _text_cell(s: Optional[str]) -> str:
    """CSV cell for free text (channel label, file name, band name, dominant band).

    Neutralises spreadsheet formula injection: a channel label like
    ``=WEBSERVICE("http://...")`` — which an attacker-supplied or simply corrupt EDF can
    carry — would otherwise execute when the exported table is opened in Excel. A
    leading apostrophe makes it literal text; numeric cells go through :func:`_cell`
    and are untouched, so a negative number is never mangled.
    """
    if not s:
        return ""
    txt = s.replace("\r", " ").replace("\n", " ")
    if txt[:1] in _FORMULA_LEAD:
        return "'" + txt
    return txt


def _csv_provenance(res: AnalysisResult) -> str:
    """Single-field ``#`` provenance line describing every analysis parameter.

    Artifact thresholds are recorded as applied: with no ``--epoch`` no epoch is
    screened, so writing the thresholds would assert screening that never happened.
    """
    bands_str = ";".join(f"{n}:{lo:g}-{hi:g}" for n, lo, hi in res.bands)
    fit_range = (f"{res.fit_range[0]:g}-{res.fit_range[1]:g}"
                 if res.fit_range else "band")
    swa = (f"{res.overall.swa_lo:g}-{res.overall.swa_hi:g}"
           if res.overall.swa_lo is not None else "undefined")
    rej_on = bool(res.epochs)
    amp = res.max_amp if (rej_on and res.max_amp is not None) else ""
    grad = res.max_grad if (rej_on and res.max_grad is not None) else ""
    window = (f"{res.start_offset_sec:g}+{res.duration_sec:g}s"
              if res.start_offset_sec else f"0+{res.duration_sec:g}s")
    # Every parameter that can change a number in the table must appear here, or two
    # exports produced under different settings are indistinguishable in an audit.
    epoch = f"{res.epoch_sec:g}s" if res.epoch_sec else "none"
    line = (f"{res.line_freq_mode}" if res.line_freq_mode else "off")
    notched = int(bool(res.overall.line_noise and res.overall.line_noise.removed))
    baseline = f"{res.baseline_sec:g}s" if res.baseline_sec is not None else "none"
    return (f"# eegband v{__version__} | fs_hz={res.fs:g} ({res.fs_source}) | "
            f"nperseg={res.nperseg} noverlap={res.noverlap} nfft={res.nfft} "
            f"n_seg={res.n_seg} | "
            f"detrend={res.detrend} average={res.average} | "
            f"sef={res.sef_frac * 100:g}% | bands={bands_str} | swa={swa} | "
            f"aperiodic={res.aperiodic_mode or 'off'} fit_range={fit_range} | "
            f"epoch={epoch} | "
            f"line_freq={line} line_bw={res.line_bw:g} notch={int(res.notch)} "
            f"notch_applied={notched} | baseline={baseline} | "
            f"max_amp={amp} max_grad={grad} qc_pass={int(res.qc_pass)} | "
            f"window={window} | n_interpolated={res.n_filled} | "
            f"rel_columns=fraction(0-1) | "
            f"encoding={res.input_encoding or 'utf-8-sig'} | "
            f"source={res.source_file or ''}")


def _write_provenance(w, results: List[AnalysisResult]) -> None:
    """Write one ``#`` provenance line per distinct parameter set (deduplicated).

    A 50-file cohort shares one parameter set, so 50 identical comment lines are pure
    noise for whoever has to skip them.
    """
    seen = set()
    for res in results:
        line = _csv_provenance(res)
        if line not in seen:
            seen.add(line)
            w.writerow([line])


def render_csv_batch(results: List[AnalysisResult], comment: bool = True,
                     with_series_cols: bool = True) -> str:
    """Tidy band-power table across one or more analyses (channels and/or files).

    One row per epoch per series (or one 'overall' row per series when ``--epoch``
    was not used). ``with_series_cols`` prepends ``series``/``source_file`` columns so
    a multi-channel or multi-file export is directly groupable in R/SAS. The column
    set is identical for every row, so the result is always a clean rectangle.
    """
    if not results:
        return ""
    first = results[0]
    band_names = [bp.name for bp in first.overall.band_powers]
    amp_on = any(r.max_amp is not None for r in results) and \
        any(r.epochs for r in results)
    grad_on = any(r.max_grad is not None for r in results) and \
        any(r.epochs for r in results)
    ap_on = any(r.aperiodic_mode is not None for r in results)

    header: List[str] = []
    if with_series_cols:
        header += ["series", "source_file"]
    header += ["epoch", "start_sec", "end_sec", "nyquist_hz"]
    for name in band_names:
        header += [f"{name}_abs_uv2", f"{name}_rel", f"{name}_peak_hz"]
        if ap_on:
            header += [f"{name}_osc_uv2", f"{name}_adj_peak_hz"]
    header = [_text_cell(h) for h in header]
    header += ["total_uv2", "peak_hz", "sef_hz", "entropy",
               "theta_alpha_ratio", "delta_beta_ratio", "slowing_ratio",
               "dominant", "dominant_tie"]
    if ap_on:
        header += ["ap_exponent", "ap_offset", "ap_r2", "osc_total_uv2"]
    if amp_on:
        header += ["peak_amp_uv"]
    if grad_on:
        header += ["max_grad_uv"]
    if amp_on or grad_on:
        header += ["rejected"]

    def _row(res: AnalysisResult, label: str, t0: float, t1: float, spec: Spectrum,
             peak_amp: Optional[float] = None, max_grad: Optional[float] = None,
             rejected: bool = False) -> List[str]:
        row: List[str] = []
        if with_series_cols:
            row += [_text_cell(res.label), _text_cell(res.source_file)]
        row += [label, _cell(t0), _cell(t1), _cell(res.fs / 2.0)]
        by = {bp.name: bp for bp in spec.band_powers}
        for name in band_names:
            bp = by.get(name)
            if bp is None:
                row += ["", "", ""] + (["", ""] if ap_on else [])
                continue
            if _band_unavailable(bp, res.fs):
                # above this channel's Nyquist: never measured, so blank (not 0)
                row += ["", "", ""] + (["", ""] if ap_on else [])
                continue
            row += [_cell(bp.absolute), _cell(bp.relative), _cell(bp.peak_freq)]
            if ap_on:
                adj = bp.adj_peak_freq if bp.adj_peak_prominent else None
                row += [_cell(bp.osc_absolute), _cell(adj)]
        row += [_cell(spec.total_power), _cell(spec.peak_freq), _cell(spec.sef),
                _cell(spec.entropy),
                _cell(spec.ratios.get("theta/alpha")),
                _cell(spec.ratios.get("delta/beta")),
                _cell(spec.ratios.get("(delta+theta)/(alpha+beta)")),
                _text_cell(spec.dominant), "1" if spec.dominant_tie else "0"]
        if ap_on:
            fit = spec.aperiodic
            if fit is None:
                row += ["", "", ""]
            else:
                row += [_cell(fit.exponent), _cell(fit.offset), _cell(fit.r2)]
            row += [_cell(spec.osc_total)]
        if amp_on:
            row += [_cell(peak_amp)]
        if grad_on:
            row += [_cell(max_grad)]
        if amp_on or grad_on:
            row += ["1" if rejected else "0"]
        return row

    buf = io.StringIO()
    w = csv.writer(buf)
    if comment:
        # Provenance as a SINGLE comment field (no internal commas) so a naive
        # csv.DictReader sees one bogus column, not several fake headers; the full
        # analysis parameters make an exported epoch table self-reproducible.
        _write_provenance(w, results)
    w.writerow(header)
    for res in results:
        if res.epochs:
            for ep in res.epochs:
                w.writerow(_row(res, str(ep.index), ep.start_sec, ep.end_sec,
                                ep.spectrum, ep.peak_amp, ep.max_grad, ep.rejected))
        else:
            w.writerow(_row(res, "overall", 0.0, res.duration_sec, res.overall))
    return buf.getvalue()


# Per-endpoint statistics exported in --csv-summary, in column order.
_SUMMARY_STAT_KEYS = ("mean", "sd", "sem", "ci_lo", "ci_hi", "median", "q1", "q3",
                      "min", "max", "rho1", "n_eff", "sem_adj", "ci_lo_adj",
                      "ci_hi_adj", "adjusted")
_SUMMARY_TREND_KEYS = ("theil_sen_slope_per_sec", "slope_ci_lo_per_sec",
                       "slope_ci_hi_per_sec", "kendall_tau_b", "mann_kendall_p")

# Per-endpoint baseline-vs-post columns (only emitted when --baseline was used).
_BASELINE_KEYS = ("mean", "post_mean", "delta", "pct_change", "ci_lo", "ci_hi",
                  "hedges_g", "p", "q_fdr")


def render_csv_summary(results: List[AnalysisResult], comment: bool = True,
                       with_series_cols: bool = True) -> str:
    """**One row per series** — the analysis dataset a study actually tabulates.

    ``--csv`` gives one row per epoch; this gives one row per channel/recording, with
    every endpoint's mean/SD/SEM/CI, median/IQR/range, the autocorrelation-adjusted CI
    (ρ̂₁, n_eff, widened limits) and the Mann–Kendall/Theil–Sen trend — the numbers that
    are otherwise trapped in the text report, and that nobody reimplements in base R.
    QC columns (``n_epochs``/``n_kept``/``n_rejected``/``qc_pass``) travel with the
    values so rows can never be pooled across recordings that were screened differently.

    Without ``--epoch`` there are no per-epoch statistics, so the row carries the
    whole-recording values (``*_mean`` only) and ``n_epochs = 0``.
    """
    if not results:
        return ""
    band_names = [bp.name for bp in results[0].overall.band_powers]
    # Union of endpoint keys across series, in the canonical order.
    keys: List[str] = [k for k in _ENDPOINT_META
                       if any(k in r.epoch_summary for r in results)]
    for name in band_names:
        for suffix in ("absolute_uv2", "relative"):
            k = f"{name}_{suffix}"
            # A band named like a core endpoint ('swa') would otherwise emit the same
            # column name twice; analyze._epoch_endpoints already drops the collision,
            # so keep this list in step with it.
            if k in keys:
                continue
            if any(k in r.epoch_summary for r in results):
                keys.append(k)

    header: List[str] = []
    if with_series_cols:
        header += ["series", "source_file"]
    header += ["fs_hz", "fs_source", "nyquist_hz", "n_samples", "duration_sec",
               "start_sec", "nperseg", "noverlap", "nfft", "n_seg",
               "freq_resolution_hz", "detrend", "average", "aperiodic_mode",
               "fit_lo_hz", "fit_hi_hz", "swa_lo_hz", "swa_hi_hz",
               "epoch_sec", "n_epochs", "n_kept", "n_rejected", "qc_pass",
               "delta_dominant_frac",
               # whole-recording (not per-epoch) values, for reference
               "overall_total_uv2", "overall_swa_uv2", "overall_swa_rel",
               "overall_sef_hz", "overall_entropy", "overall_ap_exponent",
               "overall_ap_r2",
               # mains line noise, so a row can never be pooled with one that was
               # cleaned differently
               "line_freq_hz", "line_detected", "line_notched", "line_max_ratio",
               "line_excess_uv2"]
    # Baseline-vs-post columns only exist when --baseline was used somewhere.
    with_baseline = any(r.baseline_contrasts for r in results)
    if with_baseline:
        header += ["baseline_sec", "n_baseline_epochs", "n_post_epochs"]
    for k in keys:
        for stat in _SUMMARY_STAT_KEYS:
            header.append(f"{k}_{stat}")
        for stat in _SUMMARY_TREND_KEYS:
            header.append(f"{k}_{stat}")
        if with_baseline:
            for stat in _BASELINE_KEYS:
                header.append(f"{k}_base_{stat}")
    header = [_text_cell(h) for h in header]

    buf = io.StringIO()
    w = csv.writer(buf)
    if comment:
        _write_provenance(w, results)
    w.writerow(header)
    for res in results:
        spec = res.overall
        fit = spec.aperiodic
        row: List[str] = []
        if with_series_cols:
            row += [_text_cell(res.label), _text_cell(res.source_file)]
        row += [
            _cell(res.fs), _text_cell(res.fs_source), _cell(res.fs / 2.0),
            str(res.n_samples), _cell(res.duration_sec),
            _cell(res.start_offset_sec), str(res.nperseg), str(res.noverlap),
            str(res.nfft), str(res.n_seg),
            _cell(res.fs / res.nfft if res.nfft else None),
            _text_cell(res.detrend), _text_cell(res.average),
            _text_cell(res.aperiodic_mode or "off"),
            _cell(fit.fit_lo if fit else None), _cell(fit.fit_hi if fit else None),
            _cell(spec.swa_lo), _cell(spec.swa_hi),
            _cell(res.epoch_sec), str(len(res.epochs)), str(res.n_epochs_kept),
            str(res.n_epochs_rejected), "1" if res.qc_pass else "0",
            _cell(res.swa_density),
            _cell(spec.total_power),
            _cell(spec.swa_abs if spec.swa_source != "undefined" else None),
            _cell(spec.swa_rel if spec.swa_source != "undefined" else None),
            _cell(spec.sef), _cell(spec.entropy),
            _cell(fit.exponent if fit else None), _cell(fit.r2 if fit else None),
        ]
        lnr = spec.line_noise
        row += [
            _cell(lnr.f0 if lnr else None),
            ("" if lnr is None else ("1" if lnr.detected else "0")),
            ("" if lnr is None else ("1" if lnr.removed else "0")),
            _cell(lnr.max_ratio if lnr else None),
            _cell(sum(lnr.excess_in(lo, hi) for _, lo, hi in res.bands)
                  if lnr else None),
        ]
        if with_baseline:
            row += [_cell(res.baseline_sec), str(res.n_baseline), str(res.n_post)]
        for k in keys:
            st = res.epoch_summary.get(k)
            for stat in _SUMMARY_STAT_KEYS:
                row.append(_cell(st.get(stat)) if st else "")
            tr = res.epoch_trends.get(k)
            if tr is None:
                row += [""] * len(_SUMMARY_TREND_KEYS)
            else:
                row += [_cell(tr.slope), _cell(tr.slope_lo), _cell(tr.slope_hi),
                        _cell(tr.tau), _cell(tr.p)]
            if with_baseline:
                cr = res.baseline_contrasts.get(k)
                if cr is None:
                    row += [""] * len(_BASELINE_KEYS)
                else:
                    row += [_cell(cr.mean_a), _cell(cr.mean_b), _cell(cr.diff),
                            _cell(cr.pct_change), _cell(cr.ci_lo), _cell(cr.ci_hi),
                            _cell(cr.hedges_g), _cell(cr.p), _cell(cr.q)]
        w.writerow(row)
    return buf.getvalue()


def render_psd_csv(results: List[AnalysisResult], psds: List[Any],
                   comment: bool = True) -> str:
    """Export the raw spectra: ``series,freq_hz,psd_uv2_per_hz,aperiodic_fit,residual``.

    Everything else in the tool summarises the PSD; this hands over the PSD itself so
    it can be plotted or re-analysed in R/Python/Prism without re-implementing Welch.
    ``psds`` is a list of ``(freqs, psd)`` pairs aligned with ``results``.
    """
    buf = io.StringIO()
    w = csv.writer(buf)
    if comment:
        _write_provenance(w, results)
    w.writerow(["series", "source_file", "freq_hz", "psd_uv2_per_hz",
                "aperiodic_fit_uv2_per_hz", "residual_uv2_per_hz"])
    for res, (freqs, psd) in zip(results, psds):
        fit = res.overall.aperiodic
        for f, p in zip(freqs, psd):
            if fit is not None and f > 0 and fit.fit_lo <= f <= fit.fit_hi:
                bg = fit.psd_at(f)
                resid = max(p - bg, 0.0)
                w.writerow([_text_cell(res.label), _text_cell(res.source_file),
                            _cell(f), _cell(p), _cell(bg), _cell(resid)])
            else:
                w.writerow([_text_cell(res.label), _text_cell(res.source_file),
                            _cell(f), _cell(p), "", ""])
    return buf.getvalue()
