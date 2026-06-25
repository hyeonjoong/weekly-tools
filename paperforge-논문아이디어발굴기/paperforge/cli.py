"""Command-line interface for paperforge."""
from __future__ import annotations

import argparse
import sys

from .engine import evaluate
from .manifest import ManifestError, load_manifest
from .report import render_csv, render_markdown


def _positive_int(value: str) -> int:
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"정수가 아닙니다: {value!r}")
    if ivalue < 1:
        raise argparse.ArgumentTypeError("--top 은 1 이상이어야 합니다.")
    return ivalue


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paperforge",
        description=(
            "보유 데이터 매니페스트(JSON)를 입력받아 멀티모달 논문 아이디어 "
            "매트릭스를 생성합니다 (가설·변수·분석법·저널·실현가능성)."
        ),
    )
    p.add_argument("manifest", help="데이터셋 매니페스트 JSON 경로")
    p.add_argument("--out", help="Markdown 리포트 저장 경로 (미지정 시 stdout)")
    p.add_argument("--csv", help="CSV 매트릭스 저장 경로 (선택)")
    p.add_argument(
        "--alpha", type=float, default=0.05,
        help="유의수준 (기본 0.05; 지원: 0.05/0.01/0.10)",
    )
    p.add_argument(
        "--power", type=float, default=0.80,
        help="검정력 (기본 0.80; 지원: 0.80/0.90/0.95)",
    )
    p.add_argument(
        "--top", type=_positive_int, default=None,
        help="상위 N개만 출력 (1 이상; 기본: 전체)",
    )
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
    except FileNotFoundError:
        print(f"오류: 파일을 찾을 수 없습니다: {args.manifest}", file=sys.stderr)
        return 2
    except ManifestError as exc:
        print(f"매니페스트 오류: {exc}", file=sys.stderr)
        return 2

    try:
        results = evaluate(manifest, alpha=args.alpha, power=args.power)
    except ValueError as exc:
        print(f"분석 오류: {exc}", file=sys.stderr)
        return 2

    if args.top is not None:
        results = results[: args.top]

    report = render_markdown(manifest, results, args.alpha, args.power)

    # Write requested files FIRST so a bad path fails cleanly (exit 2) before we
    # print anything to stdout — avoids the confusing "report printed, then
    # crashed on the CSV write" half-success.
    try:
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(report + "\n")
        if args.csv:
            with open(args.csv, "w", encoding="utf-8", newline="") as fh:
                fh.write(render_csv(results))
    except OSError as exc:
        print(f"출력 파일 쓰기 오류: {exc}", file=sys.stderr)
        return 2

    if args.out:
        print(f"리포트 저장: {args.out}")
    else:
        print(report)
    if args.csv:
        print(f"CSV 저장: {args.csv}", file=sys.stderr)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
