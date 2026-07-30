"""Command-line interface for eegband.

Examples
--------
Value-only CSV, sampling rate given:
    eegband signal.csv --fs 128

Value + time column (fs inferred and cross-checked against --fs):
    eegband signal.csv --time time_s --value eeg_uv

Per-epoch analysis + JSON:
    eegband signal.csv --fs 128 --epoch 30 --json

Every channel of a wide CSV, or of an EDF/BDF recording, as a tidy table:
    eegband study.csv --channels all --fs 256 --csv > bands.csv
    eegband night.edf --channels all --epoch 30 --csv > bands.csv

A cohort of files in one call (one row per file per epoch):
    eegband subj*.csv --fs 128 --epoch 30 --csv > cohort.csv
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import List, Optional, Sequence, Tuple

from . import __version__
from .analyze import AnalysisResult, analyze, resolve_fs
from .dataio import SignalData, list_columns, load_signal, load_signals
from .edf import is_edf_path, looks_like_edf, read_edf_channel, read_edf_info
from .report import (
    json_safe,
    render_comparison,
    render_csv_batch,
    render_csv_summary,
    render_psd_csv,
    render_text,
    to_dict,
)
from .spectral import DEFAULT_BANDS, welch_psd


def _parse_bands(text: str) -> List[Tuple[str, float, float]]:
    """Parse '--bands' like 'delta:0.5-4,theta:4-8,alpha:8-13'."""
    bands: List[Tuple[str, float, float]] = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            name, rng = chunk.split(":")
            lo, hi = rng.split("-")
            lo_f, hi_f = float(lo), float(hi)
        except ValueError:
            raise ValueError(
                f"잘못된 --bands 항목: '{chunk}'. 형식: name:lo-hi (예: delta:0.5-4)")
        if hi_f <= lo_f:
            raise ValueError(f"--bands '{name}': 상한({hi_f})이 하한({lo_f})보다 커야 합니다.")
        bands.append((name.strip(), lo_f, hi_f))
    if not bands:
        raise ValueError("--bands 에서 유효한 대역을 찾지 못했습니다.")
    return bands


def _parse_range(text: str, opt: str) -> Tuple[float, float]:
    """Parse 'LO-HI' (e.g. '--fit-range 2-45') into a validated (lo, hi) pair."""
    parts = text.split("-")
    if len(parts) != 2:
        raise ValueError(f"{opt} 형식은 LO-HI 입니다 (예: 2-45), 받은 값: '{text}'")
    try:
        lo, hi = float(parts[0]), float(parts[1])
    except ValueError:
        raise ValueError(f"{opt} 의 숫자를 해석할 수 없습니다: '{text}'")
    if lo <= 0:
        raise ValueError(f"{opt} 하한은 0보다 커야 합니다 (로그-로그 적합): {lo}")
    if hi <= lo:
        raise ValueError(f"{opt} 상한({hi})이 하한({lo})보다 커야 합니다.")
    return lo, hi


def _parse_channels(text: Optional[str]) -> Optional[List[str]]:
    """'--channels all' -> [] (meaning every channel); 'A,B' -> ['A','B']; None -> None."""
    if text is None:
        return None
    if text.strip().lower() == "all":
        return []
    names = [c.strip() for c in text.split(",") if c.strip()]
    if not names:
        raise ValueError("--channels 에서 유효한 채널 이름을 찾지 못했습니다.")
    return names


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eegband",
        description="단일/다채널 EEG 대역파워 분석기 — Welch PSD로 delta/theta/alpha/"
                    "beta/gamma 절대·상대 파워, 슬로우파(SWA), SEF95, 피크주파수, "
                    "대역비, 1/f 비주기 성분(지수·배경보정 파워)을 계산합니다 "
                    "(표준 라이브러리만; CSV/TSV·EDF/BDF 입력).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("inputs", nargs="+", metavar="INPUT",
                   help="입력 파일: CSV/TSV(값 열, 선택적 시간 열) 또는 EDF/EDF+/BDF. "
                        "여러 개를 주면 한 번에 일괄 분석")
    p.add_argument("--value", help="값(µV) 열 이름 (미지정 시 자동 감지)")
    p.add_argument("--time", help="시간(초) 열 이름 (있으면 fs를 추정·교차검증)")
    p.add_argument("--channels", metavar="SPEC",
                   help="여러 채널 분석: 'all' 또는 'Fp1,Cz' (CSV 열 이름 / EDF 채널 이름)")
    p.add_argument("--list-channels", action="store_true", dest="list_channels",
                   help="입력의 채널(열) 목록만 출력하고 종료")
    p.add_argument("--fs", type=float, default=None,
                   help="표본화율 Hz (미지정 시 128 가정). 시간 열/EDF 헤더가 있으면 "
                        "그 값을 우선 사용하며, 명시한 --fs와 1%% 이상 다르면 경고")
    p.add_argument("--start", type=float, default=None, metavar="SEC",
                   help="분석 시작 시점(초). 앞부분을 건너뜀 (EDF는 필요한 레코드만 읽음)")
    p.add_argument("--duration", type=float, default=None, metavar="SEC",
                   help="분석 길이(초). 긴 기록의 일부만 볼 때")
    p.add_argument("--epoch", type=float,
                   help="에폭 길이(초). 지정 시 에폭별 + 요약 + 시간추세 리포트")
    p.add_argument("--max-amp", type=float, dest="max_amp",
                   help="아티팩트 제거: 에폭 최대 |진폭|(µV)이 이 값을 넘으면 SWA 요약에서 "
                        "제외 (예: 150). 에폭 표에는 표시되지만 통계에서 빠짐")
    p.add_argument("--max-grad", type=float, dest="max_grad",
                   help="아티팩트 제거: 인접 표본 간 최대 |변화량|(µV)이 이 값을 넘는 에폭 제외 "
                        "(예: 50). 진폭 한계 안에 숨은 급격한 스파이크를 잡음")
    p.add_argument("--nperseg", type=int,
                   help="Welch 세그먼트 길이(표본). 기본 ~4초, 신호 길이로 상한")
    p.add_argument("--noverlap", type=int,
                   help="Welch 세그먼트 겹침(표본). 기본 nperseg//2 (50%%)")
    p.add_argument("--detrend", choices=("constant", "linear", "none"),
                   default="constant",
                   help="세그먼트 디트렌드: constant(평균 제거, 기본), "
                        "linear(선형 추세 제거 — 드리프트가 delta로 새는 것 방지), none")
    p.add_argument("--average", choices=("mean", "median"), default="mean",
                   help="Welch 세그먼트 평균: mean(기본) 또는 median"
                        "(일시적 아티팩트에 강건, 편향보정 포함)")
    p.add_argument("--aperiodic", choices=("robust", "ols", "off"), default="robust",
                   help="1/f 비주기 배경 적합: robust(피크 제외 반복 적합, 기본), "
                        "ols(전 구간 최소제곱), off(끄기)")
    p.add_argument("--fit-range", dest="fit_range", metavar="LO-HI",
                   help="1/f 적합 주파수 범위 Hz (기본: 분석 대역 전체). 느린 진동이 지수를 "
                        "부풀리면 예: 2-45 또는 30-45")
    p.add_argument("--bands",
                   help="대역 재정의, 예: 'delta:0.5-4,theta:4-8,alpha:8-13,"
                        "beta:13-30,gamma:30-45'")
    p.add_argument("--sef", type=float, default=95.0,
                   help="스펙트럼 에지 주파수 백분위 (기본 95 → SEF95)")
    p.add_argument("--swa-band", dest="swa_band", metavar="LO-HI",
                   help="슬로우파(SWA) 대역을 명시 (기본: 'delta' 대역). 예: 0.5-4 "
                        "— 커스텀 --bands에 delta가 없을 때 필요")
    p.add_argument("--json", action="store_true", help="사람용 리포트 대신 JSON 출력")
    p.add_argument("--csv", dest="csv_out", action="store_true",
                   help="에폭별(없으면 전체) 대역파워 표를 CSV로 출력 (통계 SW용)")
    p.add_argument("--csv-summary", dest="csv_summary", action="store_true",
                   help="계열(채널/파일)당 한 행 요약 CSV — 종말점별 mean/SD/CI/"
                        "자기상관보정CI/중앙값·IQR + 추세(기울기·p·tau) + QC 열. "
                        "논문 표에 바로 쓰는 분석 데이터셋")
    p.add_argument("--psd-csv", dest="psd_csv", action="store_true",
                   help="스펙트럼 자체를 CSV로 출력 (freq_hz, psd, 1/f 적합, 잔차) — "
                        "다른 도구에서 그림 그릴 때")
    p.add_argument("--no-comment", dest="csv_comment", action="store_false",
                   help="CSV 출력에서 맨 앞 '#' 프로버넌스 주석 행을 생략 "
                        "(base-R read.csv/SAS PROC IMPORT 호환)")
    p.add_argument("--version", action="version", version=f"eegband {__version__}")
    return p


def _list_channels(paths: Sequence[str]) -> int:
    """Print the channels/columns of each input (``--list-channels``)."""
    n_failed = 0
    for path in paths:
        print(f"# {path}")
        try:
            if is_edf_path(path) or looks_like_edf(path):
                info = read_edf_info(path)
                print(f"    format = {info.kind}, duration = {info.duration_sec:g} s, "
                      f"records = {info.n_records} × {info.record_duration:g} s"
                      + ("" if info.continuous else ", EDF+D (discontinuous)"))
                for s in info.signals:
                    tag = "  [annotation — 분석 불가]" if s.is_annotation else ""
                    print(f"    - {s.label:<20} fs = {s.fs:>8.4g} Hz  unit = "
                          f"{s.unit or '?':<6}{tag}")
                print("    (환자 식별정보 필드는 읽지 않습니다 / patient fields are "
                      "never read)")
            else:
                cols, tcol, delim, enc = list_columns(path)
                dname = {",": "comma", ";": "semicolon", "\t": "tab",
                         "|": "pipe"}.get(delim, delim)
                print(f"    format = text ({dname}-separated, {enc})")
                if tcol:
                    print(f"    time column  : {tcol}")
                for c in cols:
                    print(f"    - {c}")
        except (ValueError, OSError) as exc:
            print(f"입력 오류 [{path}]: {exc}", file=sys.stderr)
            n_failed += 1
    # Same convention as an analysis run: 1 = some inputs failed, 2 = all of them.
    if n_failed == 0:
        return 0
    return 2 if n_failed == len(paths) else 1


def _crop(sig: SignalData, fs: float, start: Optional[float],
          duration: Optional[float]) -> None:
    """Crop a loaded series in place to [start, start+duration) seconds."""
    if start is None and duration is None:
        return
    n = len(sig.values)
    i0 = 0 if start is None else int(round(start * fs))
    if i0 >= n:
        raise ValueError(
            f"--start {start:g}s 는 신호 길이({n / fs:.2f}s)를 넘습니다.")
    i1 = n if duration is None else min(n, i0 + int(round(duration * fs)))
    if i1 - i0 < 2:
        raise ValueError(
            "--start/--duration 로 남은 구간이 2 표본 미만입니다 (구간을 늘리세요).")
    sig.values = sig.values[i0:i1]
    if sig.times is not None:
        sig.times = sig.times[i0:i1]
    if i0 or i1 < n:
        sig.warnings.append(
            f"analysed samples {i0}–{i1 - 1} of {n} "
            f"({i0 / fs:.2f}–{i1 / fs:.2f} s) as requested by --start/--duration.")


def _load_edf_series(path: str, channels: Optional[List[str]], args
                     ) -> List[Tuple[SignalData, float, str]]:
    """Load the requested EDF/BDF channels as (signal, fs_from_header, source).

    Channels are addressed by **signal index**, not by label, so a file that repeats a
    label (routine in clinical exports) still yields every distinct signal instead of
    reading the first match twice. A channel that cannot be read (0 samples/record, a
    corrupt entry) is skipped with a warning rather than aborting the whole file.
    """
    info = read_edf_info(path)
    extra: List[str] = []
    if channels is None:
        idxs = [info.data_signals[0].index] if info.data_signals else []
        if len(info.data_signals) > 1:
            others = ", ".join(s.label for s in info.data_signals[1:])
            extra.append(
                f"only the first signal channel was analysed; this file also has: "
                f"{others}. Use --channels all (or --channels NAME) for the others.")
    elif channels == []:
        idxs = [s.index for s in info.data_signals]
    else:
        idxs = []
        for name in channels:
            found = info.find(name)
            if found is None:
                names = ", ".join(s.label for s in info.signals)
                raise ValueError(f"channel '{name}' not found in '{path}'. "
                                 f"Available: {names}")
            idxs.append(found.index)
    if not idxs:
        raise ValueError(f"'{path}' 에 분석 가능한 신호 채널이 없습니다.")
    out: List[Tuple[SignalData, float, str]] = []
    skipped: List[str] = []
    first_error: Optional[ValueError] = None
    for i in idxs:
        try:
            sig, fs, _ = read_edf_channel(path, start_sec=args.start or 0.0,
                                          duration_sec=args.duration, info=info,
                                          index=i)
        except ValueError as exc:
            first_error = first_error or exc
            skipped.append(f"{info.signals[i].label} ({exc})")
            continue
        # "edf": --start/--duration were already applied while reading the records,
        # so the caller must NOT crop again. Record the window for provenance.
        if args.start or args.duration is not None:
            sig.warnings.append(
                f"analysed {args.start or 0:g}–"
                f"{(args.start or 0) + len(sig.values) / fs:g} s of a "
                f"{info.duration_sec:g} s recording as requested by "
                "--start/--duration.")
        sig.warnings.extend(extra)
        out.append((sig, fs, "edf"))
    if not out:
        raise first_error if first_error else ValueError(
            f"no readable channel in '{path}'.")
    if skipped:
        note = "skipped unreadable channel(s): " + "; ".join(skipped)
        for sig, _, _ in out:
            sig.warnings.append(note)
    return out


def _load_csv_series(path: str, channels: Optional[List[str]], args
                     ) -> List[Tuple[SignalData, Optional[float], str]]:
    """Load the requested CSV/TSV columns as (signal, fs_or_None, source)."""
    if channels is None:
        sigs = [load_signal(path, value_col=args.value, time_col=args.time)]
    else:
        sigs = load_signals(path, value_cols=(channels or None),
                            time_col=args.time)
    return [(s, None, "csv") for s in sigs]


def _analyse_input(path: str, args, bands, sef_frac: float,
                   channels: Optional[List[str]],
                   ap_mode: Optional[str],
                   fit_range: Optional[Tuple[float, float]],
                   swa_band: Optional[Tuple[float, float]],
                   multi: bool) -> List[AnalysisResult]:
    """Load one input file and analyse every selected channel of it."""
    is_edf = is_edf_path(path) or looks_like_edf(path)
    extra_warns: List[str] = []
    if is_edf:
        loaded = _load_edf_series(path, channels, args)
        if args.value or args.time:
            extra_warns.append(
                "--value/--time apply to CSV input only and were ignored for this "
                "EDF/BDF file (use --channels to pick channels).")
    else:
        loaded = _load_csv_series(path, channels, args)

    # With several input files the channel name alone is ambiguous ('eeg_uv' twice),
    # so prefix the series label with the file stem.
    stem = os.path.splitext(os.path.basename(path))[0]

    results: List[AnalysisResult] = []
    for sig, fs_hint, kind in loaded:
        if fs_hint is not None:
            fs, fs_source, fs_warns = fs_hint, "edf header", []
            if args.fs is not None and args.fs > 0 and \
                    abs(fs_hint - args.fs) / args.fs > 0.01:
                fs_warns = [f"--fs {args.fs:g} Hz disagrees with the EDF header "
                            f"({fs_hint:.6g} Hz); using the header value."]
        else:
            fs, fs_source, fs_warns = resolve_fs(args.fs, sig.times)
        if fs <= 0:
            raise ValueError("표본화율(fs)이 0 이하입니다.")
        if kind != "edf":
            _crop(sig, fs, args.start, args.duration)
        warnings = list(sig.warnings) + list(fs_warns) + extra_warns
        n_series = len(loaded)
        if multi and n_series > 1:
            label = f"{stem}:{sig.value_col}"
        elif multi:
            label = stem
        else:
            # Always label the series (never blank): a single-series export must still
            # say which column/channel it came from when several are appended.
            label = sig.value_col
        if (args.max_amp is not None or args.max_grad is not None) \
                and args.epoch is None:
            warnings.append(
                "--max-amp/--max-grad screen whole epochs, so they do nothing without "
                "--epoch; no artifact rejection was applied.")
        res = analyze(sig.values, fs=fs, bands=bands, nperseg=args.nperseg,
                      noverlap=args.noverlap, sef_frac=sef_frac,
                      epoch_sec=args.epoch, times=sig.times,
                      fs_source=fs_source, warnings=warnings,
                      detrend=args.detrend, average=args.average,
                      n_filled=sig.n_filled, max_amp=args.max_amp,
                      max_grad=args.max_grad, aperiodic_mode=ap_mode,
                      fit_range=fit_range, swa_band=swa_band, label=label)
        res.source_file = path
        res.input_encoding = sig.encoding
        res.start_offset_sec = float(args.start or 0.0)
        results.append(res)
    return results


_NUMERIC_OPTS = ("fs", "start", "duration", "epoch", "sef", "max_amp", "max_grad")


def _recompute_psd(res: AnalysisResult):
    """Re-derive (freqs, psd) for --psd-csv from the recorded Welch parameters.

    The analysis keeps only summaries (a full PSD per epoch would be large), so the
    overall spectrum is recomputed here with exactly the parameters the report states.
    """
    if res.samples is None:
        return [], []
    freqs, psd, _ = welch_psd(res.samples, res.fs, nperseg=res.nperseg,
                              noverlap=res.noverlap, detrend=res.detrend,
                              average=res.average)
    return freqs, psd


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    modes = [m for m, on in (("--json", args.json), ("--csv", args.csv_out),
                             ("--csv-summary", args.csv_summary),
                             ("--psd-csv", args.psd_csv)) if on]
    if len(modes) > 1:   # checked early, before any computation
        print(f"입력 오류: {' 과 '.join(modes)} 는 함께 쓸 수 없습니다 "
              "(출력 형식은 하나만).", file=sys.stderr)
        return 2

    if args.list_channels:
        return _list_channels(args.inputs)

    # ---- option validation (all before any file is read) ----------------------
    # argparse's float type accepts 'inf'/'nan'; those reach int() conversions deep in
    # the analysis and raise OverflowError/ValueError as a traceback. Reject up front.
    for name in _NUMERIC_OPTS:
        val = getattr(args, name, None)
        if val is not None and not math.isfinite(val):
            print(f"입력 오류: --{name.replace('_', '-')} 는 유한한 수여야 합니다 "
                  f"(받은 값: {val}).", file=sys.stderr)
            return 2
    try:
        bands = _parse_bands(args.bands) if args.bands else list(DEFAULT_BANDS)
        channels = _parse_channels(args.channels)
        fit_range = (_parse_range(args.fit_range, "--fit-range")
                     if args.fit_range else None)
        swa_band = (_parse_range(args.swa_band, "--swa-band")
                    if args.swa_band else None)
    except ValueError as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2
    sef_frac = args.sef / 100.0
    if not (0.0 < sef_frac < 1.0):
        print("입력 오류: --sef 는 0~100 사이여야 합니다.", file=sys.stderr)
        return 2
    if args.start is not None and args.start < 0:
        print("입력 오류: --start 는 0 이상이어야 합니다.", file=sys.stderr)
        return 2
    if args.duration is not None and args.duration <= 0:
        print("입력 오류: --duration 은 0보다 커야 합니다.", file=sys.stderr)
        return 2
    if args.epoch is not None and args.epoch <= 0:
        print("입력 오류: --epoch 은 0보다 커야 합니다.", file=sys.stderr)
        return 2
    if args.nperseg is not None and args.nperseg < 2:
        print("입력 오류: --nperseg 는 2 이상이어야 합니다.", file=sys.stderr)
        return 2
    ap_mode = None if args.aperiodic == "off" else args.aperiodic
    if fit_range is not None and ap_mode is None:
        print("입력 오류: --fit-range 는 --aperiodic off 와 함께 쓸 수 없습니다.",
              file=sys.stderr)
        return 2

    # ---- load + analyse every input ------------------------------------------
    multi_input = len(args.inputs) > 1
    results: List[AnalysisResult] = []
    n_failed = 0
    for path in args.inputs:
        try:
            results.extend(_analyse_input(path, args, bands, sef_frac, channels,
                                          ap_mode, fit_range, swa_band,
                                          multi_input))
        except (ValueError, OSError) as exc:
            prefix = f"입력 오류 [{path}]" if multi_input else "입력 오류"
            print(f"{prefix}: {exc}", file=sys.stderr)
            n_failed += 1
    if not results:
        return 2

    # ---- render ---------------------------------------------------------------
    if args.csv_out:
        sys.stdout.write(render_csv_batch(results, comment=args.csv_comment))
    elif args.csv_summary:
        sys.stdout.write(render_csv_summary(results, comment=args.csv_comment))
    elif args.psd_csv:
        psds = [_recompute_psd(r) for r in results]
        sys.stdout.write(render_psd_csv(results, psds,
                                        comment=args.csv_comment))
    elif args.json:
        if len(results) == 1:
            payload = to_dict(results[0])
        else:
            payload = {"tool": "eegband", "version": __version__,
                       "n_series": len(results),
                       "series": [to_dict(r) for r in results]}
        # allow_nan=False so a future non-finite leak fails loudly instead of writing
        # bare NaN/Infinity, which strict JSON readers (R jsonlite, jq) reject.
        print(json.dumps(json_safe(payload), ensure_ascii=False, indent=2,
                         allow_nan=False))
    else:
        for i, res in enumerate(results):
            if i:
                print()
            print(render_text(res))
        if len(results) > 1:
            print(render_comparison(results))

    # Warnings must reach the user in EVERY mode: in --csv/--csv-summary/--psd-csv the
    # report they would otherwise appear in is not printed at all, and "all epochs
    # rejected" or "aperiodic fit poor" must never be silently dropped.
    if args.csv_out or args.csv_summary or args.psd_csv:
        for res in results:
            if not res.warnings:
                continue
            who = f"[{res.label}] " if res.label else ""
            for w in res.warnings:
                print(f"주의 {who}{w}", file=sys.stderr)
    # Partial failure in a batch is reported but the successful series are kept.
    return 1 if n_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
