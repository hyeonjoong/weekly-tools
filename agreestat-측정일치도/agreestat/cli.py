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
import sys
from typing import Optional, Sequence

from . import __version__
from .analyze import analyze
from .dataio import load_pairs
from .report import render_json, render_text


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
    p.add_argument("--json", action="store_true", help="JSON으로 출력")
    p.add_argument("--version", action="version",
                   version=f"agreestat {__version__}")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if not 0.0 < args.alpha < 1.0:
        print("입력 오류: --alpha 는 0과 1 사이여야 합니다.", file=sys.stderr)
        return 2

    try:
        data = load_pairs(args.csv, args.method_a, args.method_b, args.subject)
    except (ValueError, FileNotFoundError) as exc:
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
        )
    except ValueError as exc:
        print(f"분석 오류: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(render_json(result))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
