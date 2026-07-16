"""Command-line interface for agreestat.

Examples
--------
Two measurement columns (auto-detected if only two numeric columns exist)::

    agreestat data.csv --method-a sensor --method-b band

Percentage Bland-Altman + subject id for repeated measures::

    agreestat data.csv -a watch -b ecg --subject id --percent

Machine-readable output::

    agreestat data.csv -a sensor -b band --json
"""

from __future__ import annotations

import argparse
import math
import sys
from typing import Optional, Sequence

from . import __version__
from .analyze import analyze
from .dataio import load_pairs
from .report import (
    render_json,
    render_markdown,
    render_plot_data,
    render_svg,
    render_text,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agreestat",
        description="측정 방법 일치도(agreement) 분석기 — 두 방법(A vs B)의 "
                    "짝지은 측정값으로 Bland–Altman, ICC(2,1)/ICC(3,1), Lin's CCC, "
                    "반복성, 상관/차이 검정을 계산하고 논문용 문장까지 출력합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("csv", help="입력 CSV 파일 경로")
    p.add_argument("-a", "--method-a", dest="method_a",
                   help="방법 A(측정1) 열 이름 (미지정 시 자동 탐지)")
    p.add_argument("-b", "--method-b", dest="method_b",
                   help="방법 B(측정2/기준) 열 이름 (미지정 시 자동 탐지)")
    p.add_argument("-s", "--subject", dest="subject",
                   help="(선택) 피험자 ID 열 이름 — 반복측정 지표 계산에 사용")
    p.add_argument("--name-a", dest="name_a",
                   help="리포트에 표시할 방법 A 이름 (기본: 열 이름)")
    p.add_argument("--name-b", dest="name_b",
                   help="리포트에 표시할 방법 B 이름 (기본: 열 이름)")
    p.add_argument("--percent", action="store_true",
                   help="백분율 Bland–Altman (diff%% = 100·(A−B)/mean) — "
                        "비례오차가 있는 데이터에 적합")
    p.add_argument("--alpha", type=float, default=0.05,
                   help="유의수준 / 신뢰구간 폭 (기본 0.05 → 95%% CI)")
    p.add_argument("--accept", type=float, metavar="DELTA",
                   help="사전 설정한 임상 허용한계 ±DELTA. 95%% LoA가 "
                        "[−DELTA, +DELTA] 안이면 '교환가능' 판정을 출력")
    p.add_argument("--accept-lower", dest="accept_lower", type=float,
                   help="비대칭 허용한계의 하한 (--accept-upper와 함께 사용)")
    p.add_argument("--accept-upper", dest="accept_upper", type=float,
                   help="비대칭 허용한계의 상한 (--accept-lower와 함께 사용)")
    p.add_argument("--encoding",
                   help="입력 CSV 인코딩 (기본: 자동 감지 utf-8/utf-16/cp949/euc-kr)")
    p.add_argument("--target-loa-hw", dest="target_loa_hw", type=float,
                   metavar="H",
                   help="목표 LoA CI 반너비 H. 그 정밀도 달성에 필요한 표본수 n을 계산")
    p.add_argument("--json", action="store_true", help="JSON으로 출력")
    p.add_argument("--markdown", nargs="?", const="-", metavar="PATH",
                   help="결과를 마크다운 표로 출력(경로 생략 시 표준출력)")
    p.add_argument("--plot-data", dest="plot_data", metavar="PATH",
                   help="Bland–Altman 플롯 데이터(mean,diff)를 CSV로 저장")
    p.add_argument("--svg", metavar="PATH",
                   help="Bland–Altman 플롯을 SVG 파일로 저장")
    p.add_argument("--version", action="version",
                   version=f"agreestat {__version__}")
    return p


def _resolve_accept(args):
    """Turn --accept / --accept-lower / --accept-upper into (lo, hi) or None.

    Returns the string ``"error"`` on an invalid/incomplete specification
    (mixing forms, non-positive/non-finite delta, incomplete or lo>=hi pair).
    """
    lo, hi = args.accept_lower, args.accept_upper
    if args.accept is not None:
        if lo is not None or hi is not None:
            return "error"  # can't mix symmetric and asymmetric forms
        d = abs(args.accept)
        if d == 0.0 or not math.isfinite(d):
            return "error"
        return (-d, d)
    if lo is None and hi is None:
        return None
    if lo is None or hi is None:
        return "error"
    if not (math.isfinite(lo) and math.isfinite(hi)) or lo >= hi:
        return "error"
    return (lo, hi)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if not 0.0 < args.alpha < 1.0:
        print("입력 오류: --alpha 는 0과 1 사이여야 합니다.", file=sys.stderr)
        return 2

    if args.target_loa_hw is not None and not (
            math.isfinite(args.target_loa_hw) and args.target_loa_hw > 0.0):
        print("입력 오류: --target-loa-hw 는 0보다 큰 유한한 값이어야 합니다.",
              file=sys.stderr)
        return 2

    accept = _resolve_accept(args)
    if accept == "error":
        print("입력 오류: 허용한계는 --accept DELTA 또는 "
              "--accept-lower/--accept-upper 쌍으로 지정하세요 (하한 < 상한).",
              file=sys.stderr)
        return 2

    try:
        data = load_pairs(args.csv, args.method_a, args.method_b, args.subject,
                          encoding=args.encoding)
    except (ValueError, OSError) as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2

    if data.n < 2:
        print(f"오류: 분석에 최소 2쌍이 필요합니다 (발견: {data.n}쌍).",
              file=sys.stderr)
        return 2

    try:
        result = analyze(
            data.a, data.b, subjects=data.subjects,
            name_a=args.name_a or data.name_a,
            name_b=args.name_b or data.name_b,
            alpha=args.alpha,
            mode="percent" if args.percent else "absolute",
            dropped=data.dropped,
            accept=accept,
            nonfinite=data.nonfinite,
            extra_warnings=data.notes,
            target_loa_hw=args.target_loa_hw,
        )
    except (ValueError, OverflowError) as exc:
        print(f"분석 오류: {exc}", file=sys.stderr)
        return 2

    # Side-car file outputs (plot data / SVG); these don't replace the report.
    try:
        if args.plot_data:
            with open(args.plot_data, "w", encoding="utf-8") as fh:
                fh.write(render_plot_data(result))
            print(f"플롯 데이터를 저장했습니다: {args.plot_data}", file=sys.stderr)
        if args.svg:
            with open(args.svg, "w", encoding="utf-8") as fh:
                fh.write(render_svg(result))
            print(f"SVG 플롯을 저장했습니다: {args.svg}", file=sys.stderr)
    except OSError as exc:
        print(f"출력 오류: {exc}", file=sys.stderr)
        return 2

    if args.markdown:
        md = render_markdown(result)
        if args.markdown == "-":
            print(md)
        else:
            try:
                with open(args.markdown, "w", encoding="utf-8") as fh:
                    fh.write(md)
                print(f"마크다운 표를 저장했습니다: {args.markdown}", file=sys.stderr)
            except OSError as exc:
                print(f"출력 오류: {exc}", file=sys.stderr)
                return 2
    elif args.json:
        print(render_json(result))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
