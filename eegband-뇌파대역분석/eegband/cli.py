"""Command-line interface for eegband.

Examples
--------
Value-only CSV, sampling rate given:
    eegband signal.csv --fs 128

Value + time column (fs inferred and cross-checked against --fs):
    eegband signal.csv --time time_s --value eeg_uv

Per-epoch analysis + JSON:
    eegband signal.csv --fs 128 --epoch 30 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional, Sequence, Tuple

from . import __version__
from .analyze import analyze, resolve_fs
from .dataio import load_signal
from .report import render_csv, render_text, to_dict
from .spectral import DEFAULT_BANDS


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


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eegband",
        description="단일채널 EEG 대역파워 분석기 — Welch PSD로 delta/theta/alpha/"
                    "beta/gamma 절대·상대 파워, 슬로우파(SWA), SEF95, 피크주파수, "
                    "대역비를 계산합니다 (표준 라이브러리만).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("csv", help="입력 CSV 파일 경로 (값 열, 선택적으로 시간 열)")
    p.add_argument("--value", help="값(µV) 열 이름 (미지정 시 자동 감지)")
    p.add_argument("--time", help="시간(초) 열 이름 (있으면 fs를 추정·교차검증)")
    p.add_argument("--fs", type=float, default=128.0,
                   help="표본화율 Hz (기본 128). 시간 열이 있으면 추정값을 우선 사용")
    p.add_argument("--epoch", type=float,
                   help="에폭 길이(초). 지정 시 에폭별 + 요약 리포트")
    p.add_argument("--nperseg", type=int,
                   help="Welch 세그먼트 길이(표본). 기본 ~4초, 신호 길이로 상한")
    p.add_argument("--noverlap", type=int,
                   help="Welch 세그먼트 겹침(표본). 기본 nperseg//2 (50%%)")
    p.add_argument("--bands",
                   help="대역 재정의, 예: 'delta:0.5-4,theta:4-8,alpha:8-13,"
                        "beta:13-30,gamma:30-45'")
    p.add_argument("--sef", type=float, default=95.0,
                   help="스펙트럼 에지 주파수 백분위 (기본 95 → SEF95)")
    p.add_argument("--json", action="store_true", help="사람용 리포트 대신 JSON 출력")
    p.add_argument("--csv", dest="csv_out", action="store_true",
                   help="에폭별(없으면 전체) 대역파워 표를 CSV로 출력 (통계 SW용)")
    p.add_argument("--version", action="version", version=f"eegband {__version__}")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.json and args.csv_out:  # checked early, before any computation
        print("입력 오류: --json 과 --csv 는 함께 쓸 수 없습니다.", file=sys.stderr)
        return 2

    # load + fs resolution: any input/IO problem becomes a clean exit-2 message.
    # OSError covers FileNotFoundError, IsADirectoryError and PermissionError;
    # resolve_fs raises ValueError on a non-increasing/degenerate time column.
    try:
        sig = load_signal(args.csv, value_col=args.value, time_col=args.time)
        fs, fs_source, fs_warns = resolve_fs(args.fs, sig.times)
    except (ValueError, OSError) as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2
    if fs <= 0:
        print("입력 오류: 표본화율(fs)이 0 이하입니다.", file=sys.stderr)
        return 2

    try:
        bands = _parse_bands(args.bands) if args.bands else list(DEFAULT_BANDS)
    except ValueError as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2
    sef_frac = args.sef / 100.0
    if not (0.0 < sef_frac < 1.0):
        print("입력 오류: --sef 는 0~100 사이여야 합니다.", file=sys.stderr)
        return 2

    warnings = list(sig.warnings) + list(fs_warns)

    try:
        result = analyze(sig.values, fs=fs, bands=bands, nperseg=args.nperseg,
                         noverlap=args.noverlap, sef_frac=sef_frac,
                         epoch_sec=args.epoch, times=sig.times,
                         fs_source=fs_source, warnings=warnings)
    except ValueError as exc:
        print(f"분석 오류: {exc}", file=sys.stderr)
        return 2

    result.source_file = args.csv
    result.n_filled = sig.n_filled
    result.input_encoding = sig.encoding
    if args.csv_out:
        sys.stdout.write(render_csv(result))
    elif args.json:
        print(json.dumps(to_dict(result), ensure_ascii=False, indent=2))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
