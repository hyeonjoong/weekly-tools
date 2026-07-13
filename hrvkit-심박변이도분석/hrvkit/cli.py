"""hrvkit 명령줄 인터페이스.

예시
----
단일 열 RR(ms):
    hrvkit examples/resting.csv

시간+값 형식에서 값 열 지정, JSON 출력:
    hrvkit examples/slow_breathing.csv --col rr_ms --json

순간 HR(bpm) 입력을 직접 지정:
    hrvkit my_hr.csv --unit bpm
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from . import __version__
from .analyze import analyze_rr
from .dataio import load_series
from .report import render_text


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hrvkit",
        description="심박변이도(HRV) 분석기 — RR/IBI(ms) 또는 순간 HR(bpm) CSV로부터 "
                    "이상박동 보정 후 시간영역·주파수영역(Welch/FFT)·비선형 지표를 "
                    "계산합니다 (표준 라이브러리만).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("csv", help="입력 CSV 파일 경로")
    p.add_argument("--col", default=None,
                   help="값 열 이름(또는 0-based 인덱스). 미지정 시 자동 추정.")
    p.add_argument("--unit", default="auto", choices=["auto", "ms", "s", "bpm"],
                   help="입력 단위 (기본 auto: 값의 중앙값으로 감지)")
    p.add_argument("--clean", default="interpolate",
                   choices=["interpolate", "remove", "none"],
                   help="이상박동 보정 방법 (기본 interpolate)")
    p.add_argument("--min-rr", type=float, default=300.0,
                   help="생리적 하한 RR(ms) (기본 300)")
    p.add_argument("--max-rr", type=float, default=2000.0,
                   help="생리적 상한 RR(ms) (기본 2000)")
    p.add_argument("--rel-thresh", type=float, default=0.2,
                   help="국소 중앙값 대비 상대 급변 임계값 (기본 0.2 = 20%%)")
    p.add_argument("--fs", type=float, default=4.0,
                   help="주파수영역 리샘플 주파수 Hz (기본 4)")
    p.add_argument("--no-sampen", action="store_true",
                   help="표본 엔트로피(SampEn) 계산 생략")
    p.add_argument("--json", action="store_true",
                   help="사람이 읽는 리포트 대신 JSON 출력")
    p.add_argument("--version", action="version", version=f"hrvkit {__version__}")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        rr_ms, meta = load_series(args.csv, col=args.col, unit=args.unit)
    except (ValueError, FileNotFoundError, OSError) as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2

    try:
        res = analyze_rr(
            rr_ms,
            source=args.csv,
            unit=meta["unit"],
            clean_method=args.clean,
            fs=args.fs,
            min_rr=args.min_rr,
            max_rr=args.max_rr,
            rel_thresh=args.rel_thresh,
            do_sampen=not args.no_sampen,
        )
    except ValueError as exc:
        print(f"분석 오류: {exc}", file=sys.stderr)
        return 2

    if meta["n_dropped"]:
        res.warnings.append(
            f"{meta['n_dropped']}개 셀이 비수치/빈칸으로 무시되었습니다.")

    if args.json:
        out = res.to_dict()
        out["input_meta"] = meta
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(render_text(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
