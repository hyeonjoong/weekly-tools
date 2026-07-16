"""Command-line interface for statwise.

Examples
--------
Long format (a value column and a group column):
    statwise data.csv --value score --group arm

Wide format (each column is a group):
    statwise data.csv --wide
    statwise data.csv --wide --columns control,treatment

Paired / repeated-measures (pre vs post on the same subjects):
    statwise data.csv --paired --value score --group time --id subject
    statwise wide_pairs.csv --paired --wide --columns pre,post

Machine-readable output:
    statwise data.csv --wide --format json
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional, Sequence, Tuple

from . import __version__
from .analyze import analyze, analyze_paired
from .dataio import (load_long, load_paired_long, load_paired_wide, load_wide)
from .report import render_json, render_text


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="statwise",
        description="그룹 비교 통계 자동 선택기 — 정규성/등분산 점검 후 t-검정·"
                    "Welch·Mann-Whitney·ANOVA·Welch-ANOVA·Kruskal-Wallis를 골라 "
                    "실행하고(+대응표본 paired), 효과크기와 논문용 문장까지 출력합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("csv", help="입력 CSV 파일 경로")
    p.add_argument("--value", help="(long 형식) 수치가 담긴 열 이름")
    p.add_argument("--group", help="(long 형식) 그룹 라벨이 담긴 열 이름")
    p.add_argument("--wide", action="store_true",
                   help="wide 형식(각 열이 하나의 그룹)")
    p.add_argument("--columns",
                   help="(wide 형식) 사용할 열들, 쉼표로 구분 (미지정 시 전체 열)")
    p.add_argument("--paired", action="store_true",
                   help="대응 표본(paired) 분석: 같은 대상의 두 조건(예: 전/후) 비교. "
                        "long이면 --id 필요, wide면 2개 열(--columns)")
    p.add_argument("--id",
                   help="(paired long) 대상 식별자(subject id) 열 이름")
    p.add_argument("--baseline",
                   help="(paired) 기준(reference) 조건 이름. 차이 = (다른 조건 − 기준). "
                        "지정하면 CSV 행 순서와 무관하게 부호가 고정됩니다.")
    p.add_argument("--alpha", type=float, default=0.05,
                   help="유의수준 (기본 0.05)")
    p.add_argument("--alpha-norm", type=float, default=0.05,
                   help="정규성/등분산 판정용 유의수준 (기본 0.05)")
    p.add_argument("--correction", choices=["holm", "bh"], default="holm",
                   help="사후검정 다중비교 보정: holm(기본) 또는 bh(Benjamini-Hochberg FDR)")
    p.add_argument("--no-posthoc", action="store_true",
                   help="3그룹 이상에서 사후검정(post-hoc)을 하지 않음")
    p.add_argument("--format", choices=["text", "json"], default="text",
                   help="출력 형식: text(기본) 또는 json")
    p.add_argument("--delimiter",
                   help="CSV 구분자 강제 지정 (예: ';' 또는 '\\t'). 미지정 시 자동 감지")
    p.add_argument("--version", action="version",
                   version=f"statwise {__version__}")
    return p


def _resolve_delimiter(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    return {"\\t": "\t", "tab": "\t", "\\\\t": "\t"}.get(raw, raw)


def _split_columns(spec: Optional[str]) -> Optional[List[str]]:
    if not spec:
        return None
    return [c.strip() for c in spec.split(",") if c.strip()]


def _run_paired(args: argparse.Namespace, delim: Optional[str],
                notes: List[str]) -> int:
    if args.wide or (not args.value and not args.group):
        cols = _split_columns(args.columns)
        if args.baseline and cols and args.baseline in cols:
            # put the baseline second so diff = (other − baseline)
            cols = [c for c in cols if c != args.baseline] + [args.baseline]
        cond_a, cond_b = load_paired_wide(args.csv, cols, delim, notes)
    else:
        if not args.value or not args.group or not args.id:
            raise ValueError("paired long 형식에는 --value, --group, --id 를 모두 "
                             "지정해야 합니다. (또는 --paired --wide --columns a,b)")
        cond_a, cond_b = load_paired_long(
            args.csv, args.value, args.group, args.id, delim, notes,
            baseline=args.baseline)

    result = analyze_paired(cond_a, cond_b, alpha=args.alpha,
                            alpha_norm=args.alpha_norm)
    for n in notes:
        result.warnings.append(n)
    _emit(result, args.format)
    return 0


def _load(args: argparse.Namespace, delim: Optional[str], notes: List[str]
          ) -> List[Tuple[str, List[float]]]:
    if args.wide or (not args.value and not args.group):
        return load_wide(args.csv, _split_columns(args.columns), delim, notes)
    if not args.value or not args.group:
        raise SystemExit("long 형식에는 --value 와 --group 을 모두 지정해야 합니다. "
                         "(또는 --wide 사용)")
    return load_long(args.csv, args.value, args.group, delim, notes)


def _emit(result, fmt: str) -> None:
    if fmt == "json":
        print(render_json(result))
    else:
        print(render_text(result))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    delim = _resolve_delimiter(args.delimiter)
    notes: List[str] = []

    if args.paired:
        try:
            return _run_paired(args, delim, notes)
        except (ValueError, FileNotFoundError) as exc:
            print(f"입력 오류: {exc}", file=sys.stderr)
            return 2

    try:
        named = _load(args, delim, notes)
    except (ValueError, FileNotFoundError) as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2

    # drop groups too small to analyze, but warn
    usable = [(g, v) for g, v in named if len(v) >= 2]
    dropped = [g for g, v in named if len(v) < 2]
    if len(usable) < 2:
        hint = ""
        if not args.wide and not args.value and not args.group:
            hint = ("\n힌트: 값 열과 그룹 열이 따로 있는 long 형식이라면 "
                    "'--value 값열 --group 그룹열' 을 지정하세요. "
                    "지금은 wide(각 열=그룹)로 해석했습니다.")
        print("오류: 분석 가능한 그룹이 2개 미만입니다 "
              f"(각 그룹 최소 2개 관측치 필요). 발견된 그룹: "
              f"{[(g, len(v)) for g, v in named]}{hint}", file=sys.stderr)
        return 2

    try:
        result = analyze(usable, alpha=args.alpha, alpha_norm=args.alpha_norm,
                         posthoc=not args.no_posthoc, correction=args.correction)
    except ValueError as exc:
        print(f"분석 오류: {exc}", file=sys.stderr)
        return 2

    if dropped:
        result.warnings.append(
            "관측치 2개 미만으로 제외된 그룹: " + ", ".join(dropped))
    for n in notes:
        result.warnings.append(n)
    _emit(result, args.format)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
