"""Command-line entry point for rocdx."""

from __future__ import annotations

import argparse
import csv
import math
import sys
from typing import List, Optional, Sequence

from .analyze import add_comparison, analyze, load_dataset
from .loader import LoadError, read_table
from .report import format_report, markdown_report, points_csv_rows

__all__ = ["main", "build_parser"]

_EPILOG = """\
예시 / examples:
  rocdx examples/sepsis_biomarker.csv --score crp_mg_L --truth sepsis
  rocdx data.csv --score crp --truth sepsis --min-spec 0.90 --bootstrap 2000
  rocdx data.csv --score crp --truth 진단 --positive-label 양성 --cutoff 10 --cutoff 20
  rocdx data.csv --score cognition --truth dementia --direction lower
  rocdx data.csv --score new_marker --truth sepsis --compare crp_mg_L
  rocdx data.csv --list-columns
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rocdx",
        description="진단정확도(ROC) 분석 — AUC·민감도·특이도·PPV/NPV·우도비를 "
                    "신뢰구간과 함께 계산합니다. 외부 라이브러리 없이 동작합니다.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("csv", help="입력 CSV/TSV 파일")
    p.add_argument("--score", "-s", help="검사값(연속형 지표) 열 이름 또는 #번호")
    p.add_argument("--truth", "-t", help="기준 진단(정답) 열 이름 또는 #번호")
    p.add_argument("--positive-label", help="기준 진단 열에서 '질환 있음'을 뜻하는 값 (이 값이 아닌 나머지는 "
                        "모두 비질환군이 됩니다 — 판정보류를 빼려면 --negative-label 도 지정)")
    p.add_argument("--negative-label", help="'질환 없음'을 뜻하는 값 (--positive-label 과 함께 지정해야 하며, "
                        "두 값 중 어느 쪽도 아닌 행은 분석에서 제외)")
    p.add_argument("--direction", choices=["auto", "higher", "lower"], default="auto",
                   help="검사값이 높을수록(higher)/낮을수록(lower) 질환. 기본 auto")
    p.add_argument("--min-spec", type=float, metavar="P",
                   help="특이도 하한 (예: 0.90) — 이를 만족하는 가장 민감한 절단점")
    p.add_argument("--min-sens", type=float, metavar="P",
                   help="민감도 하한 (예: 0.95) — 이를 만족하는 가장 특이한 절단점")
    p.add_argument("--cutoff", type=float, action="append", default=[], metavar="X",
                   help="미리 정해진 절단점 (여러 번 지정 가능)")
    p.add_argument("--prevalence", type=float, metavar="P",
                   help="대상 인구집단의 유병률 — PPV/NPV를 이 값 기준으로 재계산")
    p.add_argument("--compare", action="append", default=[], metavar="COL",
                   help="같은 대상에서 측정한 다른 검사 열 — DeLong 짝지은 AUC 비교")
    p.add_argument("--bootstrap", type=int, default=0, metavar="N",
                   help="절단점 선택까지 포함한 부트스트랩 반복 수 (예: 2000). 기본 0=생략")
    p.add_argument("--seed", type=int, default=20260806, help="부트스트랩 난수 seed")
    p.add_argument("--alpha", type=float, default=0.05, help="유의수준 (기본 0.05 = 95%% CI)")
    p.add_argument("--ci-method", choices=["logit", "wald"], default="logit",
                   help="AUC 신뢰구간 계산 방식 (기본 logit)")
    p.add_argument("--sep", help="구분자 강제 지정 (기본: 자동 판별)")
    p.add_argument("--encoding", help="인코딩 강제 지정 (기본: 자동 판별)")
    p.add_argument("--markdown", action="store_true", help="마크다운 표로 출력")
    p.add_argument("--no-curve", action="store_true", help="ASCII ROC 곡선 생략")
    p.add_argument("--points-csv", metavar="FILE",
                   help="모든 절단점(TP/FP/민감도/특이도)을 CSV로 저장")
    p.add_argument("--list-columns", action="store_true", help="열 이름만 출력하고 종료")
    p.add_argument("--show-samples", action="store_true",
                   help="--list-columns 에서 값 미리보기도 표시 (8자까지만)")
    return p


def _list_columns(table, show_samples: bool = False) -> int:
    """Print the column names (and, only if asked, a short masked sample).

    Sample cells are withheld by default: this command is what a confused user
    runs first, and a clinical export's first rows are whole patient records.
    """
    print(f"파일: {table.path}")
    print(f"인코딩: {table.encoding}   구분자: {table.delimiter!r}   행: {len(table.rows)}")
    print("열 목록:")
    for i, h in enumerate(table.headers, 1):
        if not show_samples:
            print(f"  #{i:<3} {h}")
            continue
        sample = [r[i - 1] for r in table.rows[:2] if len(r) >= i and r[i - 1].strip()]
        masked = [c if len(c) <= 8 else c[:8] + "…" for c in sample]
        print(f"  #{i:<3} {h}   예: {', '.join(masked) or '(비어 있음)'}")
    if not show_samples:
        print("  (값 미리보기는 --show-samples 를 붙이면 8자까지만 표시합니다 — "
              "환자정보가 화면·로그에 남지 않도록 기본은 숨김입니다)")
    return 0


def _make_output_safe() -> None:
    """Never die on a terminal whose encoding cannot show — / ∞ / Korean.

    A hospital workstation with ``LC_ALL=C`` would otherwise raise
    UnicodeEncodeError halfway through printing the report.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):  # pragma: no cover - stream already closed
            pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    _make_output_safe()
    try:
        table = read_table(args.csv, encoding=args.encoding, delimiter=args.sep)
        if args.list_columns:
            return _list_columns(table, show_samples=args.show_samples)
        if not args.score or not args.truth:
            print("오류: --score 와 --truth 를 모두 지정해야 합니다.\n"
                  "      열 이름을 모르면 --list-columns 를 먼저 실행하세요.", file=sys.stderr)
            return 2
        for name, val in (("--min-spec", args.min_spec), ("--min-sens", args.min_sens)):
            if val is not None and not 0.0 <= val <= 1.0:
                print(f"오류: {name} 은(는) 0과 1 사이의 비율이어야 합니다 (예: 0.90).",
                      file=sys.stderr)
                return 2
        if args.bootstrap < 0:
            print("오류: --bootstrap 은 0 이상이어야 합니다.", file=sys.stderr)
            return 2
        for c in args.cutoff:
            if not math.isfinite(c):
                print("오류: --cutoff 는 유한한 숫자여야 합니다 (nan/inf 불가).",
                      file=sys.stderr)
                return 2

        dataset = load_dataset(
            table, args.score, args.truth,
            positive_label=args.positive_label, negative_label=args.negative_label,
            compare_cols=args.compare,
        )
        analysis = analyze(
            dataset, direction=args.direction, alpha=args.alpha,
            prevalence=args.prevalence, min_spec=args.min_spec, min_sens=args.min_sens,
            cutoffs=args.cutoff, n_boot=args.bootstrap, seed=args.seed,
            ci_method=args.ci_method,
        )
        for comp in dataset.extra:
            add_comparison(analysis, comp, direction=args.direction, alpha=args.alpha)
    except LoadError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    if args.markdown:
        print(markdown_report(analysis))
    else:
        print(format_report(analysis, show_curve=not args.no_curve))

    if args.points_csv:
        try:
            with open(args.points_csv, "w", newline="", encoding="utf-8-sig") as fh:
                csv.writer(fh).writerows(points_csv_rows(analysis))
        except OSError as exc:
            print(f"오류: 절단점 CSV를 쓸 수 없습니다: {exc}", file=sys.stderr)
            return 2
        print(f"[저장] 모든 절단점 → {args.points_csv}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
